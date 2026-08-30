"""
Cross-tenant isolation tests for the Notifications API.

Notifications used to be scoped by `user_id` alone. That is not a tenancy
boundary: a consultant who works across several client orgs, or an ex-member
whose membership was revoked, kept receiving — and could keep reading — the
notification stream of every org that had ever generated one for them. The
bell commingled orgs on one page and survived offboarding.

The fix puts `organization_id` on `Notification` and gates every route on
`get_accessible_org_ids`. These tests pin that gate: the SQL that reaches the
database carries the caller's accessible orgs, an empty accessible set never
touches the notifications table at all, and the item routes refuse rows from
orgs the caller cannot see — as a 404, matching PR #851's "empty, not 403"
doctrine, so the response cannot be used to prove a notification exists.

Uses unittest.mock — no database required (mirrors
tests/test_evidence_tasks_tenancy.py).
"""
import pytest
from unittest.mock import MagicMock, patch
from uuid import uuid4, UUID
from collections import namedtuple
from datetime import datetime

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException  # noqa: E402
from sqlalchemy.dialects import postgresql  # noqa: E402

import catalog_models  # noqa: E402,F401 — registers mappers referenced by models


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeResult:
    """Stand-in for a SQLAlchemy Result over a single scripted value."""

    def __init__(self, value):
        self._value = value

    def first(self):
        return self._value

    def scalar(self):
        return self._value

    def scalar_one(self):
        return self._value

    def scalar_one_or_none(self):
        return self._value

    def all(self):
        return list(self._value or [])

    def fetchall(self):
        return list(self._value or [])

    def scalars(self):
        rows = self._value

        class _Scalars:
            def all(self_inner):
                return list(rows or [])

        return _Scalars()


class FakeSession:
    """Async session stub replaying scripted results and recording statements."""

    def __init__(self, results=None):
        self._results = list(results or [])
        self.statements = []
        self.added = []
        self.committed = False

    async def execute(self, statement, params=None):
        self.statements.append(statement)
        value = self._results.pop(0) if self._results else None
        return FakeResult(value)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True

    async def flush(self):
        pass

    async def refresh(self, obj):
        pass


def bind_values(statement):
    """Every bound parameter value in a statement, as strings.

    IN-clause parameters bind as a list, so those are flattened out.
    """
    compiled = statement.compile(dialect=postgresql.dialect())
    values = set()
    for value in compiled.params.values():
        if isinstance(value, (list, tuple)):
            values.update(str(item) for item in value)
        else:
            values.add(str(value))
    return values


def sql_text(statement):
    return str(statement.compile(dialect=postgresql.dialect()))


def accessible_orgs(*org_ids):
    """An async get_accessible_org_ids stub."""
    async def _accessible(user, db):
        return list(org_ids)

    return _accessible


async def no_reference_keys(db, notifications):
    """A resolve_reference_keys stub — deep-link resolution is not under test."""
    return {}


#: Row shape returned by the batched organization-name lookup. A namedtuple so
#: it works whether the endpoint reads `row.id` / `row.name` or unpacks pairs.
OrgRow = namedtuple("OrgRow", ["id", "name"])


def success_message(result):
    """The message from a SuccessResponse model or an equivalent dict."""
    if isinstance(result, dict):
        return result.get("message")
    return getattr(result, "message", None)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def org_a():
    return uuid4()


@pytest.fixture
def org_b():
    return uuid4()


@pytest.fixture
def caller():
    """Authenticated user; org membership is decided per-test by the stub."""
    user = MagicMock()
    user.db_id = str(uuid4())
    user.email = "consultant@example.com"
    user.auth_method = "oidc"
    return user


def make_notification(user_id, organization_id, is_read=False):
    """A notification row with every attribute the serializer touches."""
    n = MagicMock()
    n.id = uuid4()
    n.user_id = user_id if isinstance(user_id, UUID) else UUID(str(user_id))
    n.organization_id = organization_id
    n.type = "task_overdue"
    n.reference_type = "task"
    n.reference_id = uuid4()
    n.message = "Evidence collection task for E-HRS-16 is overdue"
    n.is_read = is_read
    n.read_at = None
    n.created_at = datetime(2026, 1, 1, 9, 0, 0)
    return n


# ---------------------------------------------------------------------------
# GET /api/notifications
# ---------------------------------------------------------------------------

class TestListNotifications:

    @pytest.mark.asyncio
    async def test_queries_are_scoped_to_accessible_orgs(self, caller, org_a):
        """Both the count and the listing must filter on the caller's orgs.

        Scoping only the listing would leave the bell's unread badge counting
        notifications from orgs the caller can no longer open.
        """
        from api.notifications import list_notifications

        db = FakeSession([0, [], []])

        with patch("api.notifications.get_accessible_org_ids", accessible_orgs(org_a)):
            with patch("api.notifications.resolve_reference_keys", no_reference_keys):
                await list_notifications(
                    unread_only=False,
                    limit=50,
                    db=db,
                    current_user=caller,
                )

        assert len(db.statements) >= 2
        count_statement, list_statement = db.statements[0], db.statements[1]

        # The unread badge is a COUNT, not len() of a fully materialised page.
        assert "count(" in sql_text(count_statement).lower()

        for statement in (count_statement, list_statement):
            assert "organization_id" in sql_text(statement)
            assert str(org_a) in bind_values(statement)
            assert str(caller.db_id) in bind_values(statement)

    @pytest.mark.asyncio
    async def test_ex_member_cannot_see_former_org_notifications(
        self, caller, org_a, org_b
    ):
        """A user removed from org A keeps the rows but loses the org.

        The caller's accessible set is org B only; the SQL that reaches the
        database binds org B and never org A, so org A's notifications are
        unreachable — the endpoint returns an empty bell rather than the
        stale stream it used to serve.
        """
        from api.notifications import list_notifications

        db = FakeSession([0, [], []])

        with patch("api.notifications.get_accessible_org_ids", accessible_orgs(org_b)):
            with patch("api.notifications.resolve_reference_keys", no_reference_keys):
                result = await list_notifications(
                    unread_only=False,
                    limit=50,
                    db=db,
                    current_user=caller,
                )

        assert result["notifications"] == []
        assert result["unread_count"] == 0

        for statement in db.statements[:2]:
            values = bind_values(statement)
            assert str(org_b) in values
            assert str(org_a) not in values

    @pytest.mark.asyncio
    async def test_no_accessible_orgs_returns_empty_without_querying(self, caller):
        """A caller with no memberships must not reach the notifications table.

        Returning the empty payload before any query is what keeps a
        de-provisioned account from probing the table at all.
        """
        from api.notifications import list_notifications

        db = FakeSession()

        with patch("api.notifications.get_accessible_org_ids", accessible_orgs()):
            with patch("api.notifications.resolve_reference_keys", no_reference_keys):
                result = await list_notifications(
                    unread_only=False,
                    limit=50,
                    db=db,
                    current_user=caller,
                )

        assert result == {"unread_count": 0, "notifications": []}
        assert db.statements == []

    @pytest.mark.asyncio
    async def test_member_sees_own_org_rows_with_org_identity(self, caller, org_a):
        """Legitimate rows still come back — and say which org they came from.

        Without organization_id and organization_name on each item, a
        multi-org consultant cannot tell whose notification they are reading.
        """
        from api.notifications import list_notifications

        notification = make_notification(caller.db_id, org_a)
        db = FakeSession([1, [notification], [OrgRow(org_a, "Org A Ltd")]])

        with patch("api.notifications.get_accessible_org_ids", accessible_orgs(org_a)):
            with patch("api.notifications.resolve_reference_keys", no_reference_keys):
                result = await list_notifications(
                    unread_only=False,
                    limit=50,
                    db=db,
                    current_user=caller,
                )

        assert result["unread_count"] == 1
        assert len(result["notifications"]) == 1

        item = result["notifications"][0]
        assert item["id"] == notification.id
        assert item["organization_id"] == org_a
        assert item["organization_name"] == "Org A Ltd"

    @pytest.mark.asyncio
    async def test_org_names_are_one_batched_query(self, caller, org_a, org_b):
        """Org names for a page of notifications cost one query, not N.

        The bell polls this endpoint; a per-row name lookup would put the
        polling interval on the wrong side of the database.
        """
        from api.notifications import list_notifications

        rows = [
            make_notification(caller.db_id, org_a),
            make_notification(caller.db_id, org_b),
            make_notification(caller.db_id, org_a),
        ]
        db = FakeSession([
            3,
            rows,
            [OrgRow(org_a, "Org A Ltd"), OrgRow(org_b, "Org B Ltd")],
        ])

        with patch(
            "api.notifications.get_accessible_org_ids", accessible_orgs(org_a, org_b)
        ):
            with patch("api.notifications.resolve_reference_keys", no_reference_keys):
                result = await list_notifications(
                    unread_only=False,
                    limit=50,
                    db=db,
                    current_user=caller,
                )

        # count + list + one name lookup for three rows across two orgs.
        assert len(db.statements) == 3
        names = [item["organization_name"] for item in result["notifications"]]
        assert names == ["Org A Ltd", "Org B Ltd", "Org A Ltd"]

    @pytest.mark.asyncio
    async def test_user_without_db_id_gets_empty_payload(self, caller):
        """An identity with no database user has no notifications and no query."""
        from api.notifications import list_notifications

        caller.db_id = None
        db = FakeSession()

        with patch("api.notifications.get_accessible_org_ids", accessible_orgs()):
            with patch("api.notifications.resolve_reference_keys", no_reference_keys):
                result = await list_notifications(
                    unread_only=False,
                    limit=50,
                    db=db,
                    current_user=caller,
                )

        assert result == {"unread_count": 0, "notifications": []}
        assert db.statements == []


# ---------------------------------------------------------------------------
# PATCH /api/notifications/{notification_id}/read
# ---------------------------------------------------------------------------

class TestMarkNotificationRead:

    @pytest.mark.asyncio
    async def test_notification_in_inaccessible_org_is_404(
        self, caller, org_a, org_b
    ):
        """An org the caller cannot see is indistinguishable from no row.

        404 rather than 403: a 403 would confirm the notification exists and
        turn the id space into an oracle. Same doctrine as PR #851.
        """
        from api.notifications import mark_notification_read

        notification = make_notification(caller.db_id, org_a)
        db = FakeSession([notification])

        with patch("api.notifications.get_accessible_org_ids", accessible_orgs(org_b)):
            with patch("api.notifications.resolve_reference_keys", no_reference_keys):
                with pytest.raises(HTTPException) as exc_info:
                    await mark_notification_read(
                        notification_id=notification.id,
                        db=db,
                        current_user=caller,
                    )

        assert exc_info.value.status_code == 404
        assert db.committed is False
        assert notification.is_read is False
        assert notification.read_at is None

    @pytest.mark.asyncio
    async def test_other_users_notification_in_same_org_is_403(self, caller, org_a):
        """The per-user check survives the new org gate, and runs after it.

        A colleague in the same org is a legitimate reader of the org, so the
        row is not hidden — but it is still not theirs to mark read.
        """
        from api.notifications import mark_notification_read

        notification = make_notification(uuid4(), org_a)
        db = FakeSession([notification])

        with patch("api.notifications.get_accessible_org_ids", accessible_orgs(org_a)):
            with patch("api.notifications.resolve_reference_keys", no_reference_keys):
                with pytest.raises(HTTPException) as exc_info:
                    await mark_notification_read(
                        notification_id=notification.id,
                        db=db,
                        current_user=caller,
                    )

        assert exc_info.value.status_code == 403
        assert db.committed is False
        assert notification.is_read is False

    @pytest.mark.asyncio
    async def test_missing_notification_is_404(self, caller, org_a):
        from api.notifications import mark_notification_read

        db = FakeSession([None])

        with patch("api.notifications.get_accessible_org_ids", accessible_orgs(org_a)):
            with patch("api.notifications.resolve_reference_keys", no_reference_keys):
                with pytest.raises(HTTPException) as exc_info:
                    await mark_notification_read(
                        notification_id=uuid4(),
                        db=db,
                        current_user=caller,
                    )

        assert exc_info.value.status_code == 404
        assert db.committed is False

    @pytest.mark.asyncio
    async def test_own_notification_in_accessible_org_is_marked_read(
        self, caller, org_a
    ):
        """The gate must not break the ordinary case: own row, current org."""
        from api.notifications import mark_notification_read

        notification = make_notification(caller.db_id, org_a)
        db = FakeSession([notification])

        with patch("api.notifications.get_accessible_org_ids", accessible_orgs(org_a)):
            with patch("api.notifications.resolve_reference_keys", no_reference_keys):
                result = await mark_notification_read(
                    notification_id=notification.id,
                    db=db,
                    current_user=caller,
                )

        assert db.committed is True
        assert notification.is_read is True
        assert notification.read_at is not None
        assert result["id"] == notification.id
        assert result["is_read"] is True


# ---------------------------------------------------------------------------
# PATCH /api/notifications/read-all
# ---------------------------------------------------------------------------

class TestMarkAllNotificationsRead:

    @pytest.mark.asyncio
    async def test_update_is_scoped_to_accessible_orgs(self, caller, org_a, org_b):
        """Marking all read must not silently clear another org's stream.

        Unscoped, this one statement wrote across every org that had ever
        notified the user — including orgs they had been removed from.
        """
        from api.notifications import mark_all_notifications_read

        db = FakeSession([None])

        with patch(
            "api.notifications.get_accessible_org_ids", accessible_orgs(org_a, org_b)
        ):
            result = await mark_all_notifications_read(db=db, current_user=caller)

        assert len(db.statements) == 1
        statement = db.statements[0]
        text = sql_text(statement)
        assert text.strip().upper().startswith("UPDATE")
        assert "organization_id" in text

        values = bind_values(statement)
        assert str(org_a) in values
        assert str(org_b) in values
        assert str(caller.db_id) in values
        assert success_message(result)

    @pytest.mark.asyncio
    async def test_no_accessible_orgs_executes_no_update(self, caller):
        """A caller with no memberships marks nothing read — and issues no UPDATE.

        An unbounded IN () is either a SQL error or, worse, a no-op filter;
        the endpoint must short-circuit before building the statement.
        """
        from api.notifications import mark_all_notifications_read

        db = FakeSession()

        with patch("api.notifications.get_accessible_org_ids", accessible_orgs()):
            result = await mark_all_notifications_read(db=db, current_user=caller)

        assert db.statements == []
        assert success_message(result)
