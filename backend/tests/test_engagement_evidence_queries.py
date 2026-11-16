"""Queries that target an evidence object, internal-auditor grants, and review
materialisation at engagement creation (api/audit_engagements.py).

Route functions are called directly against a scripted fake session, in the
no-real-Postgres style of test_engagement_auditor_access.py. Notification
creators, the audit writer and materialise_reviews are monkeypatched.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api.audit_engagements as ae  # noqa: E402
from models import AuditEngagement, Comment, EngagementAuditor, EngagementQuery  # noqa: E402
from schemas import (  # noqa: E402
    AuditEngagementCreate,
    EngagementAuditorCreate,
    EngagementQueryCreate,
    EngagementQueryResponseCreate,
    EngagementQueryStatusUpdate,
)

ORG_ID = uuid4()
ENGAGEMENT_ID = uuid4()
USER_ID = uuid4()


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


class _Result:
    def __init__(self, value=None, rows=None, first=None):
        self._value, self._rows, self._first = value, rows or [], first

    def scalar_one_or_none(self):
        return self._value

    def scalar_one(self):
        return self._value

    def fetchall(self):
        return list(self._rows)

    def first(self):
        return self._first

    def all(self):
        return list(self._rows)


class _Session:
    """Pops scripted results; records statements and added objects."""

    def __init__(self, scripted=()):
        self.scripted = list(scripted)
        self.statements, self.added = [], []
        self.commits = 0

    async def execute(self, stmt, *_a, **_k):
        self.statements.append(stmt)
        if not self.scripted:
            raise AssertionError(f"ran out of scripted results at {stmt}")
        return self.scripted.pop(0)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid4()

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        now = datetime(2026, 11, 16, 12, 0)
        for attr in ("created_at", "updated_at", "invited_at"):
            if hasattr(obj, attr) and getattr(obj, attr, None) is None:
                setattr(obj, attr, now)


def _access(is_auditor=True):
    return ae.EngagementAccess(
        user=SimpleNamespace(db_id=str(USER_ID), email="auditor@example.com"),
        organization_id=ORG_ID, engagement_id=ENGAGEMENT_ID,
        is_auditor=is_auditor, role="auditor" if is_auditor else "editor",
    )


@pytest.fixture
def calls(monkeypatch):
    """Record notifications and audit writes instead of performing them."""
    rec = SimpleNamespace(evidence=[], control=[], answered=[], audit=[], materialise=[])

    async def _evidence(db, **kw):
        rec.evidence.append(kw)
        return 1

    async def _control(db, **kw):
        rec.control.append(kw)
        return 1

    async def _answered(db, **kw):
        rec.answered.append(kw)
        return 1

    async def _audit(**kw):
        rec.audit.append(kw)
        return []

    monkeypatch.setattr(ae, "create_engagement_evidence_query_raised_notifications", _evidence)
    monkeypatch.setattr(ae, "create_engagement_query_raised_notifications", _control)
    monkeypatch.setattr(ae, "create_engagement_query_answered_notifications", _answered)
    monkeypatch.setattr(ae, "log_entity_changes", _audit)
    monkeypatch.setattr(ae, "detect_action_source", lambda request: "api")
    monkeypatch.setattr(ae, "get_request_id", lambda request: None)
    return rec


# =============================================================================
# Target validation
# =============================================================================

def test_exactly_one_target():
    with pytest.raises(ValidationError):
        EngagementQueryCreate(title="t", body="b")
    with pytest.raises(ValidationError):
        EngagementQueryCreate(title="t", body="b", scf_id="IAC-01", evidence_id="E-IAC-07")
    assert EngagementQueryCreate(title="t", body="b", scf_id="IAC-01").evidence_id is None
    assert EngagementQueryCreate(title="t", body="b", evidence_id="E-IAC-07").scf_id is None


@pytest.mark.asyncio
async def test_unknown_evidence_id_is_404(calls):
    db = _Session([_Result(value=None)])  # no review row for that evidence id here
    with pytest.raises(HTTPException) as exc:
        await ae.create_engagement_query(
            ORG_ID, ENGAGEMENT_ID,
            EngagementQueryCreate(title="t", body="b", evidence_id="E-NOPE-01"),
            request=None, access=_access(), db=db,
        )
    assert exc.value.status_code == 404
    assert db.added == [] and db.commits == 0
    sql = _sql(db.statements[0])
    assert "engagement_evidence_reviews.evidence_id = 'E-NOPE-01'" in sql
    assert str(ENGAGEMENT_ID) in sql and str(ORG_ID) in sql
    assert calls.evidence == [] and calls.control == []


# =============================================================================
# Raising
# =============================================================================

@pytest.mark.asyncio
async def test_evidence_query_routes_to_evidence_creator_and_leaves_review_alone(calls):
    # the review exists; then the raiser's display name
    db = _Session([_Result(value=uuid4()), _Result(rows=[(USER_ID, "Ada Auditor")])])
    item = await ae.create_engagement_query(
        ORG_ID, ENGAGEMENT_ID,
        EngagementQueryCreate(title="Need v2", body="Out of window", evidence_id="E-IAC-07"),
        request=None, access=_access(), db=db,
    )
    assert item.evidence_id == "E-IAC-07" and item.scf_id is None and item.status == "open"
    assert item.raised_by_name == "Ada Auditor"

    # The card does not move: a SELECT for the review and one for the name,
    # nothing else touched.
    assert len(db.statements) == 2
    assert all(_sql(st).lstrip().upper().startswith("SELECT") for st in db.statements)
    assert "engagement_evidence_reviews" in _sql(db.statements[0])
    assert "FROM users" in _sql(db.statements[1])
    assert [type(o) for o in db.added] == [EngagementQuery]

    assert calls.control == []
    assert calls.evidence == [{
        "organization_id": ORG_ID, "engagement_id": ENGAGEMENT_ID, "query_id": item.id,
        "evidence_id": "E-IAC-07", "raised_by_user_id": USER_ID,
    }]
    assert calls.audit[0]["new_values"]["evidence_id"] == "E-IAC-07"


@pytest.mark.asyncio
async def test_unknown_control_id_is_422(calls):
    db = _Session([_Result(value=None)])  # not a control of this engagement's scope
    with pytest.raises(HTTPException) as exc:
        await ae.create_engagement_query(
            ORG_ID, ENGAGEMENT_ID, EngagementQueryCreate(title="t", body="b", scf_id="IAC-99"),
            request=None, access=_access(), db=db,
        )
    assert exc.value.status_code == 422
    assert exc.value.detail == "Control IAC-99 is not in this engagement's scope."
    assert db.added == [] and db.commits == 0
    assert calls.control == [] and calls.evidence == []
    # The board's source: the frozen scope, in scope, not retired.
    sql = _sql(db.statements[0])
    assert "engagement_control_scope" in sql
    assert "engagement_control_scope.scf_id = 'IAC-99'" in sql
    assert str(ENGAGEMENT_ID) in sql
    assert "'in_scope'" in sql
    assert "deprecated" in sql


@pytest.mark.asyncio
async def test_control_query_keeps_existing_creator(calls):
    # the control is in scope; then the raiser's display name
    db = _Session([_Result(value="IAC-01"), _Result(rows=[])])
    item = await ae.create_engagement_query(
        ORG_ID, ENGAGEMENT_ID, EngagementQueryCreate(title="t", body="b", scf_id="IAC-01"),
        request=None, access=_access(), db=db,
    )
    assert item.scf_id == "IAC-01" and item.evidence_id is None
    assert "engagement_control_scope" in _sql(db.statements[0])
    assert calls.evidence == []
    assert calls.control == [{
        "organization_id": ORG_ID, "query_id": item.id, "scf_id": "IAC-01", "raised_by_user_id": USER_ID,
    }]


# =============================================================================
# Listing and reading
# =============================================================================

@pytest.mark.asyncio
async def test_list_filters_by_evidence_id():
    q = EngagementQuery(id=uuid4(), engagement_id=ENGAGEMENT_ID, evidence_id="E-IAC-07", title="t",
                        body="b", status="open", created_at=datetime(2026, 11, 16))
    db = _Session([_Result(rows=[(q, "a@example.com")]), _Result(rows=[])])
    items = await ae.list_engagement_queries(
        ORG_ID, ENGAGEMENT_ID, scf_id=None, evidence_id="E-IAC-07", status=None, access=_access(), db=db)
    assert [i.evidence_id for i in items] == ["E-IAC-07"]
    assert "engagement_queries.evidence_id = 'E-IAC-07'" in _sql(db.statements[0])
    # response counts never include a non-shared row
    assert "comments.visibility = 'shared'" in _sql(db.statements[1])


@pytest.mark.asyncio
async def test_single_query_thread_reads_shared_responses_only():
    q = EngagementQuery(id=uuid4(), engagement_id=ENGAGEMENT_ID, evidence_id="E-IAC-07", title="t",
                        body="b", status="open", created_at=datetime(2026, 11, 16))
    db = _Session([_Result(value=q), _Result(rows=[])])
    item = await ae.get_engagement_query(ORG_ID, ENGAGEMENT_ID, q.id, access=_access(), db=db)
    assert item.responses == []
    assert "comments.visibility = 'shared'" in _sql(db.statements[1])


@pytest.mark.asyncio
async def test_presentation_query_map_skips_evidence_targeted_rows():
    qid = uuid4()
    db = _Session([_Result(rows=[("IAC-01", qid, "t", "open")])])
    out = await ae._fetch_queries_by_scf(db, ENGAGEMENT_ID)
    assert out == {"IAC-01": [{"id": str(qid), "title": "t", "status": "open"}]}
    assert "engagement_queries.scf_id IS NOT NULL" in _sql(db.statements[0])


# =============================================================================
# Answer / close / reopen on an evidence query
# =============================================================================

@pytest.mark.asyncio
async def test_answer_close_reopen_evidence_query(calls):
    q = EngagementQuery(id=uuid4(), engagement_id=ENGAGEMENT_ID, scf_id=None, evidence_id="E-IAC-07",
                        title="t", body="b", status="open", created_at=datetime(2026, 11, 16))

    db = _Session([_Result(value=q), _Result(rows=[(q.id, 1)])])
    answered = await ae.respond_to_engagement_query(
        ORG_ID, ENGAGEMENT_ID, q.id, EngagementQueryResponseCreate(content="Attached v2"),
        request=None, access=_access(is_auditor=False), db=db)
    assert answered.status == "answered" and answered.evidence_id == "E-IAC-07"
    response = next(o for o in db.added if isinstance(o, Comment))
    assert response.commentable_type == EngagementQuery.COMMENTABLE_TYPE
    assert response.visibility == Comment.VISIBILITY_SHARED

    # the query, the caller's active grant, the response count
    db = _Session([_Result(value=q), _Result(value="external"), _Result(rows=[])])
    closed = await ae.update_engagement_query_status(
        ORG_ID, ENGAGEMENT_ID, q.id, EngagementQueryStatusUpdate(status="closed"),
        request=None, access=_access(), db=db)
    assert closed.status == "closed" and closed.closed_at is not None

    db = _Session([_Result(value=q), _Result(value="external"), _Result(rows=[])])
    reopened = await ae.update_engagement_query_status(
        ORG_ID, ENGAGEMENT_ID, q.id, EngagementQueryStatusUpdate(status="open"),
        request=None, access=_access(), db=db)
    assert reopened.status == "open" and reopened.closed_at is None


# =============================================================================
# Internal-auditor grants
# =============================================================================

def _admin():
    return SimpleNamespace(user=SimpleNamespace(db_id=str(uuid4())), role="admin")


@pytest.mark.asyncio
async def test_internal_grant_for_non_member_is_422(calls):
    target = uuid4()
    db = _Session([
        _Result(value=SimpleNamespace(id=ENGAGEMENT_ID)),   # engagement under org
        _Result(first=(target, "outsider@example.com")),    # user exists
        _Result(value=None),                                # not an org member
    ])
    with pytest.raises(HTTPException) as exc:
        await ae.grant_engagement_auditor(
            ORG_ID, ENGAGEMENT_ID, EngagementAuditorCreate(user_id=target, kind="internal"),
            request=None, membership=_admin(), db=db)
    assert exc.value.status_code == 422
    assert db.added == [] and db.commits == 0


@pytest.mark.asyncio
async def test_internal_grant_for_member_carries_kind(calls):
    target = uuid4()
    db = _Session([
        _Result(value=SimpleNamespace(id=ENGAGEMENT_ID)),
        _Result(first=(target, "member@example.com")),
        _Result(value=uuid4()),       # is a member
        _Result(value=None),          # no existing grant
    ])
    resp = await ae.grant_engagement_auditor(
        ORG_ID, ENGAGEMENT_ID, EngagementAuditorCreate(user_id=target, kind="internal"),
        request=None, membership=_admin(), db=db)
    assert resp.kind == "internal"
    grant = next(o for o in db.added if isinstance(o, EngagementAuditor))
    assert grant.kind == EngagementAuditor.KIND_INTERNAL
    assert calls.audit[0]["new_values"]["kind"] == "internal"


@pytest.mark.asyncio
async def test_default_grant_is_external_and_skips_membership_check(calls):
    target = uuid4()
    db = _Session([
        _Result(value=SimpleNamespace(id=ENGAGEMENT_ID)),
        _Result(first=(target, "auditor@firm.example")),
        _Result(value=None),          # no existing grant
    ])
    resp = await ae.grant_engagement_auditor(
        ORG_ID, ENGAGEMENT_ID, EngagementAuditorCreate(user_id=target),
        request=None, membership=_admin(), db=db)
    assert resp.kind == "external"
    with pytest.raises(ValidationError):
        EngagementAuditorCreate(user_id=target, kind="consultant")


# =============================================================================
# Materialise reviews at creation
# =============================================================================

@pytest.mark.asyncio
async def test_create_engagement_materialises_reviews_in_same_transaction(monkeypatch):
    """The session runs with autoflush off, so the scope rows must be flushed
    before the review materialiser reads engagement_control_scope. Without that
    flush the read sees no scope and a new engagement gets zero cards."""
    order = []

    class _LoggingSession(_Session):
        async def flush(self):
            order.append("flush")
            await super().flush()

    async def _scope(db, engagement_id, org_id, frameworks):
        db.add(SimpleNamespace(id=None, kind="scope_row"))   # pending, unflushed
        order.append("scope")
        return 3

    async def _reviews(db, engagement_id, organization_id):
        order.append(("reviews", engagement_id, organization_id, db.commits))
        return 5

    monkeypatch.setattr(ae, "_materialise_scope", _scope)
    monkeypatch.setattr(ae, "materialise_reviews", _reviews)

    db = _LoggingSession()
    membership = SimpleNamespace(user=SimpleNamespace(db_id=None))  # platform key: no audit row
    resp = await ae.create_engagement(
        ORG_ID, AuditEngagementCreate(name="ISO 2026", frameworks=["iso27001"]),
        request=None, membership=membership, db=db)

    engagement = next(o for o in db.added if isinstance(o, AuditEngagement))
    reviews_at = next(i for i, o in enumerate(order) if isinstance(o, tuple))
    assert order[reviews_at] == ("reviews", engagement.id, ORG_ID, 0)   # before commit
    scope_at = order.index("scope")
    # A flush between the scope rows being added and the reviews read.
    assert "flush" in order[scope_at + 1:reviews_at], order
    assert db.commits == 1
    assert resp.scope_count == 3


# =============================================================================
# Names on, emails off for an external auditor
# =============================================================================

def _item_with_response(raiser_id, responder_id):
    from schemas import EngagementQueryItem, QueryResponseItem
    now = datetime(2026, 11, 16, 12, 0)
    return EngagementQueryItem(
        id=uuid4(), engagement_id=ENGAGEMENT_ID, evidence_id="E-IAC-07",
        raised_by_user_id=raiser_id, raised_by_email="auditor@example.com",
        title="t", body="b", status="answered", created_at=now, response_count=1,
        responses=[QueryResponseItem(
            id=uuid4(), user_id=responder_id, email="jane.doe@client.example",
            content="Here it is", created_at=now,
        )],
    )


@pytest.mark.asyncio
async def test_external_auditor_gets_names_and_no_email_addresses():
    raiser, responder = uuid4(), uuid4()
    db = _Session([_Result(rows=[(raiser, "Ada Auditor"), (responder, None)])])
    [item] = await ae._finish_query_items(db, _access(is_auditor=True), [_item_with_response(raiser, responder)])

    assert item.raised_by_name == "Ada Auditor"
    assert item.raised_by_email is None
    assert item.responses[0].name == "Unnamed user"   # no display name: never the email
    assert item.responses[0].email is None
    assert "client.example" not in item.model_dump_json()


@pytest.mark.asyncio
async def test_org_member_still_sees_emails_and_gets_names():
    raiser, responder = uuid4(), uuid4()
    db = _Session([_Result(rows=[(raiser, "Ada Auditor"), (responder, "Jane Doe")])])
    [item] = await ae._finish_query_items(db, _access(is_auditor=False), [_item_with_response(raiser, responder)])

    assert item.raised_by_email == "auditor@example.com"
    assert item.responses[0].email == "jane.doe@client.example"
    assert item.responses[0].name == "Jane Doe"


# =============================================================================
# Who closes and reopens: org members answer, auditors decide
# =============================================================================

RAISER_ID = uuid4()


def _query(status, scf_id="IAC-01", evidence_id=None):
    return EngagementQuery(
        id=uuid4(), engagement_id=ENGAGEMENT_ID, scf_id=scf_id, evidence_id=evidence_id,
        raised_by_user_id=None, title="t", body="b", status=status,
        created_at=datetime(2026, 11, 16),
    )


async def _patch(q, status, access, scripted):
    db = _Session([_Result(value=q), *scripted])
    item = await ae.update_engagement_query_status(
        ORG_ID, ENGAGEMENT_ID, q.id, EngagementQueryStatusUpdate(status=status),
        request=None, access=access, db=db)
    return item, db


@pytest.mark.asyncio
@pytest.mark.parametrize("start,target", [("open", "closed"), ("answered", "closed"), ("closed", "open")])
async def test_org_member_without_grant_cannot_close_or_reopen(calls, start, target):
    q = _query(start)
    with pytest.raises(HTTPException) as exc:
        await _patch(q, target, _access(is_auditor=False), [_Result(value=None)])  # no grant
    assert exc.value.status_code == 403
    assert exc.value.detail == "Only the engagement's auditor can close or reopen a query."
    assert q.status == start and calls.audit == []


@pytest.mark.asyncio
async def test_platform_key_without_user_cannot_close(calls):
    q = _query("answered")
    access = ae.EngagementAccess(
        user=SimpleNamespace(db_id=None, email=None), organization_id=ORG_ID,
        engagement_id=ENGAGEMENT_ID, is_auditor=False, role="admin",
    )
    with pytest.raises(HTTPException) as exc:
        await _patch(q, "closed", access, [])   # no user: no grant lookup at all
    assert exc.value.status_code == 403
    assert q.status == "answered"


@pytest.mark.asyncio
async def test_org_member_can_send_answered_back_to_open(calls):
    q = _query("answered")
    item, db = await _patch(q, "open", _access(is_auditor=False), [_Result(rows=[])])
    assert item.status == "open"
    assert len(db.statements) == 2   # the query and the count: no grant lookup


@pytest.mark.asyncio
async def test_external_auditor_closes_a_control_query_they_answered(calls):
    """Segregation of duties binds internal auditors only."""
    q = _query("answered")
    item, db = await _patch(q, "closed", _access(), [_Result(value="external"), _Result(rows=[])])
    assert item.status == "closed"
    assert not any("ORDER BY comments.created_at DESC" in _sql(st) for st in db.statements)


@pytest.mark.asyncio
async def test_internal_auditor_closes_and_reopens(calls):
    q = _query("answered")
    item, _ = await _patch(q, "closed", _access(is_auditor=False), [
        _Result(value="internal"),
        _Result(value=uuid4()),          # someone else wrote the latest response
        _Result(rows=[]),
    ])
    assert item.status == "closed"
    item, _ = await _patch(q, "open", _access(is_auditor=False), [_Result(value="internal"), _Result(rows=[])])
    assert item.status == "open" and item.closed_at is None


@pytest.mark.asyncio
async def test_internal_auditor_cannot_close_a_control_query_they_answered_last(calls):
    q = _query("answered")
    with pytest.raises(HTTPException) as exc:
        await _patch(q, "closed", _access(is_auditor=False), [
            _Result(value="internal"),
            _Result(value=USER_ID),          # the caller wrote the latest response
        ])
    assert exc.value.status_code == 403
    assert "Segregation of duties" in exc.value.detail
    assert q.status == "answered" and calls.audit == []


@pytest.mark.asyncio
async def test_latest_response_lookup_reads_the_newest_shared_response():
    qid = uuid4()
    db = _Session([_Result(value=USER_ID)])
    assert await ae._latest_response_author(db, qid) == USER_ID
    sql = _sql(db.statements[0])
    assert "comments.visibility = 'shared'" in sql
    assert "ORDER BY comments.created_at DESC" in sql
    assert str(qid) in sql


# =============================================================================
# Notify the raiser when their query is answered
# =============================================================================

@pytest.mark.asyncio
async def test_response_notifies_the_raiser_after_commit(calls, monkeypatch):
    q = _query("open")
    q.raised_by_user_id = RAISER_ID
    db = _Session([_Result(value=q), _Result(rows=[(q.id, 1)]), _Result(rows=[])])

    seen_commits = []

    async def _answered(_db, **kw):
        seen_commits.append(_db.commits)
        calls.answered.append(kw)
        return 1

    monkeypatch.setattr(ae, "create_engagement_query_answered_notifications", _answered)
    await ae.respond_to_engagement_query(
        ORG_ID, ENGAGEMENT_ID, q.id, EngagementQueryResponseCreate(content="Here"),
        request=None, access=_access(is_auditor=False), db=db)

    assert seen_commits == [1]
    assert calls.answered == [{
        "organization_id": ORG_ID, "engagement_id": ENGAGEMENT_ID, "query_id": q.id,
        "raised_by_user_id": RAISER_ID, "responder_user_id": USER_ID,
        "scf_id": "IAC-01", "evidence_id": None,
    }]
