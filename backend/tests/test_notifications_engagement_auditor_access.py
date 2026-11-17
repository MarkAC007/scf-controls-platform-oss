# SPDX-License-Identifier: AGPL-3.0-only
"""External auditors can read their engagement's notifications, and only those.

An external auditor is not an org member, so the #852 org gate alone hides
every notification from them. engrev001 widens the gate: a row stamped with an
``engagement_id`` is readable by its recipient while they hold an ACTIVE
``EngagementAuditor`` grant on that engagement. The grant is checked live, in
SQL for the list and read-all and per row for mark-read, so a revoke takes
effect on the next request.

Fake-session style (mirrors tests/test_notifications_tenancy.py): the list and
read-all filters run in SQL, so those are pinned by the statement that reaches
the database; mark-read decides in Python, so it is tested by outcome.
"""
import os
import sys
from collections import namedtuple
from datetime import datetime
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException  # noqa: E402
from sqlalchemy.dialects import postgresql  # noqa: E402

import catalog_models  # noqa: E402,F401 — registers mappers referenced by models


class FakeResult:
    def __init__(self, value):
        self._value = value

    def first(self):
        return self._value

    def scalar(self):
        return self._value

    def scalar_one_or_none(self):
        return self._value

    def all(self):
        return list(self._value or [])

    def scalars(self):
        value = self._value

        class _Scalars:
            def all(self_inner):
                return list(value or [])

            def first(self_inner):
                if isinstance(value, list):
                    return value[0] if value else None
                return value

        return _Scalars()


class FakeSession:
    def __init__(self, results=None):
        self._results = list(results or [])
        self.statements = []
        self.committed = False

    async def execute(self, statement, params=None):
        self.statements.append(statement)
        value = self._results.pop(0) if self._results else None
        return FakeResult(value)

    async def commit(self):
        self.committed = True

    async def refresh(self, obj):
        pass


def bind_values(statement):
    compiled = statement.compile(dialect=postgresql.dialect())
    values = set()
    for value in compiled.params.values():
        if isinstance(value, (list, tuple)):
            values.update(str(item) for item in value)
        else:
            values.add(str(value))
    return values


def sql_text(statement):
    return " ".join(str(statement.compile(dialect=postgresql.dialect())).split())


def accessible_orgs(*org_ids):
    async def _accessible(user, db):
        return list(org_ids)
    return _accessible


async def no_reference_keys(db, notifications):
    return {}


OrgRow = namedtuple("OrgRow", ["id", "name"])


@pytest.fixture
def auditor():
    user = MagicMock()
    user.db_id = str(uuid4())
    user.auth_method = "oidc"
    return user


def make_notification(user_id, organization_id, engagement_id=None):
    n = MagicMock()
    n.id = uuid4()
    n.user_id = user_id if isinstance(user_id, UUID) else UUID(str(user_id))
    n.organization_id = organization_id
    n.engagement_id = engagement_id
    n.type = "engagement_evidence_submitted"
    n.reference_type = "engagement_evidence_review"
    n.reference_id = uuid4()
    n.message = "Org editor submitted evidence E-IAC-07 for review"
    n.is_read = False
    n.read_at = None
    n.created_at = datetime(2026, 11, 16, 9, 0, 0)
    return n


def _assert_engagement_arm(statement, user_id):
    """The grant arm: correlated to the row's own engagement, ACTIVE only,
    for this caller, and never matching an unstamped row."""
    text = sql_text(statement)
    assert "engagement_auditors.engagement_id = notifications.engagement_id" in text
    assert "notifications.engagement_id IS NOT NULL" in text
    assert "EXISTS" in text
    values = bind_values(statement)
    assert "active" in values
    assert str(user_id) in values


# ---------------------------------------------------------------------------
# GET /api/notifications
# ---------------------------------------------------------------------------

class TestList:

    @pytest.mark.asyncio
    async def test_non_member_with_active_grant_reads_their_engagement_notification(
        self, auditor,
    ):
        from api.notifications import list_notifications

        org, eng = uuid4(), uuid4()
        row = make_notification(auditor.db_id, org, engagement_id=eng)
        db = FakeSession([uuid4(), 1, [row], [OrgRow(org, "Client Ltd")]])

        with patch("api.notifications.get_accessible_org_ids", accessible_orgs()):
            with patch("api.notifications.resolve_reference_keys", no_reference_keys):
                result = await list_notifications(
                    unread_only=False, limit=10, db=db, current_user=auditor,
                )

        assert result["unread_count"] == 1
        item = result["notifications"][0]
        assert item["id"] == row.id
        assert item["engagement_id"] == eng
        assert item["organization_id"] == org

        grant_check, count_stmt, list_stmt = db.statements[:3]
        assert "engagement_auditors" in sql_text(grant_check)
        assert "notifications" not in sql_text(grant_check)
        for stmt in (count_stmt, list_stmt):
            _assert_engagement_arm(stmt, auditor.db_id)
            # No org arm at all for a non-member.
            assert "notifications.organization_id IN" not in sql_text(stmt)

    @pytest.mark.asyncio
    async def test_revoked_grant_stops_the_read_without_touching_notifications(
        self, auditor,
    ):
        """No ACTIVE grant (revoked) and no orgs: empty, and the notifications
        table is never queried."""
        from api.notifications import list_notifications

        db = FakeSession([None])

        with patch("api.notifications.get_accessible_org_ids", accessible_orgs()):
            with patch("api.notifications.resolve_reference_keys", no_reference_keys):
                result = await list_notifications(
                    unread_only=False, limit=10, db=db, current_user=auditor,
                )

        assert result == {"unread_count": 0, "notifications": []}
        assert len(db.statements) == 1
        text = sql_text(db.statements[0])
        assert "FROM engagement_auditors" in text
        assert "notifications" not in text
        assert "active" in bind_values(db.statements[0])

    @pytest.mark.asyncio
    async def test_grant_on_engagement_a_cannot_reach_a_row_stamped_b(self, auditor):
        """The grant is matched to the row's own engagement_id (correlated),
        not to "any engagement the caller is on"."""
        from api.notifications import list_notifications

        db = FakeSession([uuid4(), 0, []])

        with patch("api.notifications.get_accessible_org_ids", accessible_orgs()):
            with patch("api.notifications.resolve_reference_keys", no_reference_keys):
                await list_notifications(
                    unread_only=False, limit=10, db=db, current_user=auditor,
                )

        for stmt in db.statements[1:3]:
            text = sql_text(stmt)
            assert "engagement_auditors.engagement_id = notifications.engagement_id" in text
            assert "notifications.user_id =" in text

    @pytest.mark.asyncio
    async def test_unstamped_org_notification_stays_invisible_to_a_non_member(
        self, auditor,
    ):
        from api.notifications import list_notifications

        db = FakeSession([uuid4(), 0, []])

        with patch("api.notifications.get_accessible_org_ids", accessible_orgs()):
            with patch("api.notifications.resolve_reference_keys", no_reference_keys):
                await list_notifications(
                    unread_only=False, limit=10, db=db, current_user=auditor,
                )

        # The only arm a non-member has requires a stamp.
        for stmt in db.statements[1:3]:
            assert "notifications.engagement_id IS NOT NULL" in sql_text(stmt)

    @pytest.mark.asyncio
    async def test_org_member_behaviour_unchanged(self, auditor):
        """Member: org arm still present, same three statements, no grant pre-check."""
        from api.notifications import list_notifications

        org = uuid4()
        row = make_notification(auditor.db_id, org)
        db = FakeSession([1, [row], [OrgRow(org, "Org A")]])

        with patch("api.notifications.get_accessible_org_ids", accessible_orgs(org)):
            with patch("api.notifications.resolve_reference_keys", no_reference_keys):
                result = await list_notifications(
                    unread_only=False, limit=10, db=db, current_user=auditor,
                )

        assert len(db.statements) == 3
        assert result["notifications"][0]["organization_name"] == "Org A"
        for stmt in db.statements[:2]:
            assert "notifications.organization_id IN" in sql_text(stmt)
            assert str(org) in bind_values(stmt)


# ---------------------------------------------------------------------------
# PATCH /api/notifications/{id}/read
# ---------------------------------------------------------------------------

class TestMarkRead:

    @pytest.mark.asyncio
    async def test_non_member_with_active_grant_marks_own_row_read(self, auditor):
        from api.notifications import mark_notification_read

        eng = uuid4()
        row = make_notification(auditor.db_id, uuid4(), engagement_id=eng)
        db = FakeSession([row, uuid4()])  # row, then the grant lookup hits

        with patch("api.notifications.get_accessible_org_ids", accessible_orgs()):
            with patch("api.notifications.resolve_reference_keys", no_reference_keys):
                result = await mark_notification_read(
                    notification_id=row.id, db=db, current_user=auditor,
                )

        assert db.committed is True
        assert row.is_read is True
        assert result["id"] == row.id
        grant_stmt = db.statements[1]
        values = bind_values(grant_stmt)
        assert str(eng) in values and str(auditor.db_id) in values and "active" in values

    @pytest.mark.asyncio
    async def test_row_stamped_with_another_engagement_is_404(self, auditor):
        """The grant lookup is keyed on the row's engagement; no grant there -> 404."""
        from api.notifications import mark_notification_read

        other_eng = uuid4()
        row = make_notification(auditor.db_id, uuid4(), engagement_id=other_eng)
        db = FakeSession([row, None])

        with patch("api.notifications.get_accessible_org_ids", accessible_orgs()):
            with pytest.raises(HTTPException) as exc:
                await mark_notification_read(
                    notification_id=row.id, db=db, current_user=auditor,
                )

        assert exc.value.status_code == 404
        assert db.committed is False
        assert str(other_eng) in bind_values(db.statements[1])

    @pytest.mark.asyncio
    async def test_revoked_grant_is_404(self, auditor):
        from api.notifications import mark_notification_read

        row = make_notification(auditor.db_id, uuid4(), engagement_id=uuid4())
        db = FakeSession([row, None])  # only ACTIVE grants match; revoked -> none

        with patch("api.notifications.get_accessible_org_ids", accessible_orgs()):
            with pytest.raises(HTTPException) as exc:
                await mark_notification_read(
                    notification_id=row.id, db=db, current_user=auditor,
                )

        assert exc.value.status_code == 404
        assert row.is_read is False

    @pytest.mark.asyncio
    async def test_unstamped_org_row_is_404_without_a_grant_lookup(self, auditor):
        from api.notifications import mark_notification_read

        row = make_notification(auditor.db_id, uuid4(), engagement_id=None)
        db = FakeSession([row])

        with patch("api.notifications.get_accessible_org_ids", accessible_orgs()):
            with pytest.raises(HTTPException) as exc:
                await mark_notification_read(
                    notification_id=row.id, db=db, current_user=auditor,
                )

        assert exc.value.status_code == 404
        assert len(db.statements) == 1

    @pytest.mark.asyncio
    async def test_cannot_mark_another_users_engagement_row_read(self, auditor):
        """Another user's row on the same engagement: 404 (not an oracle), no write."""
        from api.notifications import mark_notification_read

        row = make_notification(uuid4(), uuid4(), engagement_id=uuid4())
        db = FakeSession([row, uuid4()])

        with patch("api.notifications.get_accessible_org_ids", accessible_orgs()):
            with pytest.raises(HTTPException) as exc:
                await mark_notification_read(
                    notification_id=row.id, db=db, current_user=auditor,
                )

        assert exc.value.status_code == 404
        assert db.committed is False
        assert row.is_read is False

    @pytest.mark.asyncio
    async def test_member_other_users_row_still_403(self, auditor):
        from api.notifications import mark_notification_read

        org = uuid4()
        row = make_notification(uuid4(), org)
        db = FakeSession([row])

        with patch("api.notifications.get_accessible_org_ids", accessible_orgs(org)):
            with pytest.raises(HTTPException) as exc:
                await mark_notification_read(
                    notification_id=row.id, db=db, current_user=auditor,
                )

        assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# PATCH /api/notifications/read-all
# ---------------------------------------------------------------------------

class TestReadAll:

    @pytest.mark.asyncio
    async def test_non_member_with_grant_updates_through_the_engagement_arm(self, auditor):
        from api.notifications import mark_all_notifications_read

        db = FakeSession([uuid4(), None])

        with patch("api.notifications.get_accessible_org_ids", accessible_orgs()):
            await mark_all_notifications_read(db=db, current_user=auditor)

        update_stmt = db.statements[1]
        assert sql_text(update_stmt).startswith("UPDATE notifications")
        _assert_engagement_arm(update_stmt, auditor.db_id)
        assert "notifications.organization_id IN" not in sql_text(update_stmt)

    @pytest.mark.asyncio
    async def test_non_member_without_grant_issues_no_update(self, auditor):
        from api.notifications import mark_all_notifications_read

        db = FakeSession([None])

        with patch("api.notifications.get_accessible_org_ids", accessible_orgs()):
            await mark_all_notifications_read(db=db, current_user=auditor)

        assert len(db.statements) == 1
        assert "UPDATE" not in sql_text(db.statements[0])
        assert db.committed is False

    @pytest.mark.asyncio
    async def test_member_update_keeps_org_arm(self, auditor):
        from api.notifications import mark_all_notifications_read

        org = uuid4()
        db = FakeSession([None])

        with patch("api.notifications.get_accessible_org_ids", accessible_orgs(org)):
            await mark_all_notifications_read(db=db, current_user=auditor)

        assert len(db.statements) == 1
        text = sql_text(db.statements[0])
        assert "notifications.organization_id IN" in text
        assert str(org) in bind_values(db.statements[0])
