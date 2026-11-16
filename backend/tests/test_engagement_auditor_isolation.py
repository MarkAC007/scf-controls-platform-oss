"""Side doors an auditor could have used to reach what the engagement routes
withhold. Each of these was found by review, not by the routes' own tests:

- an internal note's text in the organisation's audit log, which every member
  can read, including a member who holds an internal-auditor grant;
- a signed download link that outlived the grant it was minted under;
- a member's email address in a notification an external auditor can read.

No database: the statements are inspected and the reads are scripted.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import catalog_models  # noqa: E402,F401  (mapper configuration needs both modules)
from api import audit_log as al  # noqa: E402
from api import engagement_comments as ec  # noqa: E402
from api import evidence_files as ef  # noqa: E402
from models import Comment, EngagementAuditorStatus  # noqa: E402
from services import notifications as notif  # noqa: E402

ORG_ID = uuid4()
SECRET = "do not tell the auditor about the Q2 gap"


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


class _Result:
    def __init__(self, value=None, rows=()):
        self._value, self._rows = value, list(rows)

    def scalar(self):
        return self._value

    def scalar_one_or_none(self):
        return self._value

    def all(self):
        return list(self._rows)

    def fetchall(self):
        return list(self._rows)

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._rows))


class _Session:
    def __init__(self, scripted=()):
        self.scripted = list(scripted)
        self.statements = []

    async def execute(self, stmt, *_a, **_k):
        self.statements.append(stmt)
        if not self.scripted:
            raise AssertionError(f"ran out of scripted results at {stmt}")
        return self.scripted.pop(0)


# =============================================================================
# 1. Internal notes and the organisation's audit log
# =============================================================================

def _comment(visibility, content=SECRET):
    return Comment(
        id=uuid4(), content=content, visibility=visibility,
        is_edited=False, is_deleted=False,
    )


@pytest.fixture
def audit_calls(monkeypatch):
    calls = []

    async def _log(**kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr(ec, "log_entity_changes", _log)
    monkeypatch.setattr(ec, "detect_action_source", lambda _request: "ui")
    monkeypatch.setattr(ec, "get_request_id", lambda _request: None)
    return calls


def test_snapshot_of_an_internal_note_holds_no_text():
    snapshot = ec._snapshot(_comment(Comment.VISIBILITY_INTERNAL))
    assert "content" not in snapshot
    assert SECRET not in repr(snapshot)
    assert set(snapshot) == {"is_edited", "is_deleted"}


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["create", "update", "delete"])
async def test_audit_write_for_an_internal_note_never_carries_its_text(audit_calls, action):
    before = _comment(Comment.VISIBILITY_INTERNAL)
    after = _comment(Comment.VISIBILITY_INTERNAL, content=SECRET + " (edited)")
    after.is_edited = True
    old_values = {} if action == "create" else ec._snapshot(before)
    new_values = {} if action == "delete" else ec._snapshot(after)

    await ec._audit(None, None, ORG_ID, after, action, uuid4(), old_values, new_values)

    [call] = audit_calls
    assert "content" not in call["tracked_fields"]
    assert "content" not in call["old_values"] and "content" not in call["new_values"]
    assert SECRET not in repr(call)
    assert call["entity_type"] == "comment" and call["action"] == action


@pytest.mark.asyncio
async def test_audit_write_for_a_shared_comment_still_records_its_text(audit_calls):
    comment = _comment(Comment.VISIBILITY_SHARED, content="Please see the attached export")
    await ec._audit(None, None, ORG_ID, comment, "create", uuid4(), {}, ec._snapshot(comment))
    [call] = audit_calls
    assert "content" in call["tracked_fields"]
    assert call["new_values"]["content"] == "Please see the attached export"


def test_no_caller_id_means_no_filter_to_build():
    assert al._internal_notes_hidden_from(None) is None


def test_hidden_rows_are_internal_notes_on_engagements_the_caller_actively_audits():
    caller = uuid4()
    stmt = al._internal_notes_hidden_from(caller)
    sql = _sql(stmt)
    params = stmt.compile(dialect=postgresql.dialect()).params

    assert "FROM comments" in sql
    assert "JOIN engagement_evidence_reviews" in sql
    assert "JOIN engagement_auditors" in sql
    assert "engagement_auditors.engagement_id = engagement_evidence_reviews.engagement_id" in sql
    assert "comments.visibility" in sql and "engagement_auditors.status" in sql
    assert caller in params.values()
    assert Comment.VISIBILITY_INTERNAL in params.values()
    assert EngagementAuditorStatus.ACTIVE.value in params.values()
    assert "engagement_evidence_review" in params.values()


async def _list_audit_log(db, user):
    return await al.list_audit_log(
        ORG_ID, membership=SimpleNamespace(user=user), db=db,
        entity_type="comment", entity_id=None, scf_id=None, action=None,
        changed_by_user_id=None, action_source=None, request_id=None,
        date_from=None, date_to=None, actor_id=None, search_text=SECRET,
        limit=50, offset=0,
    )


@pytest.mark.asyncio
async def test_audit_log_hides_those_rows_from_the_page_and_from_the_count():
    """Both statements carry the filter: a hidden row must not be countable."""
    db = _Session([_Result(value=0), _Result(rows=[])])
    response = await _list_audit_log(db, SimpleNamespace(db_id=str(uuid4())))

    assert response.total == 0 and response.entries == []
    count_sql, page_sql = _sql(db.statements[0]), _sql(db.statements[1])
    for sql in (count_sql, page_sql):
        assert "audit_log.entity_type !=" in sql
        assert "NOT IN" in sql
        assert "engagement_auditors" in sql and "comments.visibility" in sql


@pytest.mark.asyncio
async def test_audit_log_without_a_user_id_is_unchanged():
    """A caller with no user row (the platform key) holds no grant to hide from."""
    db = _Session([_Result(value=0), _Result(rows=[])])
    await _list_audit_log(db, SimpleNamespace(db_id=None))
    assert all("engagement_auditors" not in _sql(st) for st in db.statements)


# =============================================================================
# 2. A signed download link dies with the grant
# =============================================================================

ACTIVE = EngagementAuditorStatus.ACTIVE.value
REVOKED = EngagementAuditorStatus.REVOKED.value


@pytest.fixture
def org_role(monkeypatch):
    holder = SimpleNamespace(role=None, calls=0)

    async def _role(_db, _user_id, _org_id):
        holder.calls += 1
        return holder.role

    monkeypatch.setattr(ef, "_current_org_role", _role)
    return holder


@pytest.mark.asyncio
async def test_link_for_someone_who_was_never_an_auditor_is_unchanged(org_role):
    db = _Session([_Result(rows=[])])
    assert await ef._auditor_link_still_valid(db, ORG_ID, "E-IAC-07", str(uuid4())) is True
    assert org_role.calls == 0 and len(db.statements) == 1


@pytest.mark.asyncio
async def test_link_for_a_member_who_also_audits_is_unchanged(org_role):
    org_role.role = "viewer"
    db = _Session([_Result(rows=[(uuid4(), REVOKED)])])
    assert await ef._auditor_link_still_valid(db, ORG_ID, "E-IAC-07", str(uuid4())) is True


@pytest.mark.asyncio
async def test_link_stops_working_the_moment_the_grant_is_revoked(org_role):
    db = _Session([_Result(rows=[(uuid4(), REVOKED)])])
    assert await ef._auditor_link_still_valid(db, ORG_ID, "E-IAC-07", str(uuid4())) is False
    assert len(db.statements) == 1   # no need to look for a review


@pytest.mark.asyncio
async def test_active_grant_on_another_engagement_does_not_open_this_evidence(org_role):
    db = _Session([_Result(rows=[(uuid4(), ACTIVE)]), _Result(value=None)])
    assert await ef._auditor_link_still_valid(db, ORG_ID, "E-IAC-07", str(uuid4())) is False
    review_sql = _sql(db.statements[1])
    assert "engagement_evidence_reviews.evidence_id" in review_sql
    assert "engagement_evidence_reviews.engagement_id IN" in review_sql


@pytest.mark.asyncio
async def test_active_grant_that_reviews_this_evidence_keeps_the_link(org_role):
    db = _Session([_Result(rows=[(uuid4(), REVOKED), (uuid4(), ACTIVE)]), _Result(value=uuid4())])
    assert await ef._auditor_link_still_valid(db, ORG_ID, "E-IAC-07", str(uuid4())) is True


@pytest.mark.asyncio
async def test_grant_lookup_is_confined_to_this_organisation(org_role):
    db = _Session([_Result(rows=[])])
    await ef._auditor_link_still_valid(db, ORG_ID, "E-IAC-07", str(uuid4()))
    sql = _sql(db.statements[0])
    assert "JOIN audit_engagements" in sql and "audit_engagements.organization_id" in sql


# =============================================================================
# 3. No email address in a notification an auditor can read
# =============================================================================

@pytest.mark.asyncio
async def test_display_name_never_falls_back_to_the_email_address():
    user = SimpleNamespace(display_name=None, email="jane.doe@client.example")
    db = _Session([_Result(value=user)])
    assert await notif._get_display_name(db, uuid4()) == "Someone"

    named = SimpleNamespace(display_name="Jane Doe", email="jane.doe@client.example")
    db = _Session([_Result(value=named)])
    assert await notif._get_display_name(db, uuid4()) == "Jane Doe"


def test_engagement_emitters_use_the_display_name_only():
    """The four emitters that name a person must not call the helper that
    falls back to an email. Checked on the source so a new emitter that copies
    an older one is caught."""
    import inspect

    for name in (
        "create_engagement_evidence_submitted_notifications",
        "create_engagement_evidence_reassigned_notifications",
        "create_engagement_evidence_query_raised_notifications",
        "create_engagement_query_answered_notifications",
        "create_engagement_mention_notifications",
        "create_engagement_evidence_action_required_notifications",
    ):
        source = inspect.getsource(getattr(notif, name))
        assert "_get_user_name(" not in source, name
