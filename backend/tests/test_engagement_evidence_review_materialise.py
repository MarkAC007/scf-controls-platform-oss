# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for materialising evidence reviews and recording review events.

Scripted fake async session, no real Postgres, in the style of
test_engagement_auditor_access.py. The fake applies ON CONFLICT DO NOTHING
itself so idempotency can be asserted end to end through the service.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects import postgresql

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api.audit_engagements as ae  # noqa: E402
import services.engagement_evidence_review as svc  # noqa: E402
from models import EngagementEvidenceReviewEvent  # noqa: E402


ORG_ID = uuid4()
ENGAGEMENT_ID = uuid4()


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchall(self):
        return list(self._rows)


class _FakeSession:
    """Answers the service's statements from an in-memory review table.

    * SELECT on engagement_evidence_reviews -> existing evidence ids
    * SELECT on engagement_control_scope    -> scripted scope scf_ids
    * INSERT (ON CONFLICT DO NOTHING)       -> inserts only absent ids, RETURNING id
    """

    def __init__(self, existing=(), scope_scf_ids=()):
        self.existing = set(existing)
        self.scope_scf_ids = list(scope_scf_ids)
        self.statements = []
        self.inserted_rows = []
        self.added = []
        self.commits = 0

    async def execute(self, stmt, *_args, **_kwargs):
        self.statements.append(stmt)
        if stmt.is_insert:
            compiled = stmt.compile(dialect=postgresql.dialect())
            sql = str(compiled)
            assert "ON CONFLICT (engagement_id, evidence_id) DO NOTHING" in sql
            rows = _rows_from_insert(compiled.params)
            returned = []
            for row in rows:
                if row["evidence_id"] in self.existing:
                    continue  # ON CONFLICT DO NOTHING
                self.existing.add(row["evidence_id"])
                self.inserted_rows.append(row)
                returned.append((uuid4(),))
            return _Result(returned)
        if stmt.is_select:
            sql = str(stmt.compile(dialect=postgresql.dialect()))
            if "FROM engagement_evidence_reviews" in sql:
                return _Result([(eid,) for eid in sorted(self.existing)])
            if "FROM engagement_control_scope" in sql:
                return _Result([(scf,) for scf in self.scope_scf_ids])
        raise AssertionError(f"FakeSession: unexpected statement {stmt}")

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.statements.append("flush")

    async def commit(self):
        self.commits += 1


def _rows_from_insert(params: dict) -> list[dict]:
    """Rebuild per-row dicts from multi-VALUES bind params (``col_m0``, ``col_m1``...)."""
    rows: dict[int, dict] = {}
    for key, value in params.items():
        col, _, idx = key.rpartition("_m")
        if not col or not idx.isdigit():
            continue
        rows.setdefault(int(idx), {})[col] = value
    return [rows[i] for i in sorted(rows)]


@pytest.fixture
def in_scope(monkeypatch):
    """Monkeypatch evidence_ids_in_scope to a mutable set."""
    ids = set()

    async def _fake(db, engagement_id, organization_id):
        assert engagement_id == ENGAGEMENT_ID
        assert organization_id == ORG_ID
        return set(ids)

    monkeypatch.setattr(svc, "evidence_ids_in_scope", _fake)
    return ids


def _no_update_or_delete(session):
    return all(
        not s.is_update and not s.is_delete for s in session.statements if s != "flush"
    )


# ---------------------------------------------------------------------------
# materialise_reviews
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_materialise_inserts_only_missing_ids(in_scope):
    in_scope.update({"E-IAC-07", "E-GOV-01", "E-AST-02"})
    session = _FakeSession(existing={"E-GOV-01"})

    count = await svc.materialise_reviews(session, ENGAGEMENT_ID, ORG_ID)

    assert count == 2
    assert sorted(r["evidence_id"] for r in session.inserted_rows) == ["E-AST-02", "E-IAC-07"]
    for row in session.inserted_rows:
        assert row["status"] == "open"
        assert row["organization_id"] == ORG_ID
        assert row["engagement_id"] == ENGAGEMENT_ID
    assert _no_update_or_delete(session)
    assert session.commits == 0


@pytest.mark.asyncio
async def test_materialise_is_idempotent(in_scope):
    in_scope.update({"E-IAC-07", "E-GOV-01"})
    session = _FakeSession()

    first = await svc.materialise_reviews(session, ENGAGEMENT_ID, ORG_ID)
    second = await svc.materialise_reviews(session, ENGAGEMENT_ID, ORG_ID)

    assert first == 2
    assert second == 0
    assert len(session.inserted_rows) == 2
    assert _no_update_or_delete(session)


@pytest.mark.asyncio
async def test_materialise_adds_new_scope_without_touching_existing(in_scope):
    in_scope.update({"E-IAC-07"})
    session = _FakeSession()
    assert await svc.materialise_reviews(session, ENGAGEMENT_ID, ORG_ID) == 1

    in_scope.add("E-GOV-01")
    assert await svc.materialise_reviews(session, ENGAGEMENT_ID, ORG_ID) == 1
    assert [r["evidence_id"] for r in session.inserted_rows] == ["E-IAC-07", "E-GOV-01"]

    # An evidence object leaving scope is never deleted.
    in_scope.discard("E-IAC-07")
    assert await svc.materialise_reviews(session, ENGAGEMENT_ID, ORG_ID) == 0
    assert "E-IAC-07" in session.existing
    assert _no_update_or_delete(session)


@pytest.mark.asyncio
async def test_materialise_counts_rows_lost_to_a_concurrent_insert(in_scope, monkeypatch):
    # Another materialise inserts E-IAC-07 between our read and our insert:
    # ON CONFLICT drops it and the count reflects only what this call inserted.
    in_scope.update({"E-IAC-07", "E-GOV-01"})
    session = _FakeSession()
    real_execute = session.execute

    async def _racing_execute(stmt, *a, **k):
        if stmt.is_insert:
            session.existing.add("E-IAC-07")
        return await real_execute(stmt, *a, **k)

    monkeypatch.setattr(session, "execute", _racing_execute)
    assert await svc.materialise_reviews(session, ENGAGEMENT_ID, ORG_ID) == 1
    assert [r["evidence_id"] for r in session.inserted_rows] == ["E-GOV-01"]


@pytest.mark.asyncio
async def test_materialise_with_nothing_in_scope_issues_no_insert(in_scope):
    session = _FakeSession()
    assert await svc.materialise_reviews(session, ENGAGEMENT_ID, ORG_ID) == 0
    assert not any(s.is_insert for s in session.statements if s != "flush")


@pytest.mark.asyncio
async def test_materialise_flushes_before_reading_scope(monkeypatch):
    """Autoflush is off: a caller's pending scope rows must be flushed before
    the scope read, or a new engagement materialises zero cards."""
    session = _FakeSession(scope_scf_ids=[])
    seen = []

    async def _fake(db, engagement_id, organization_id):
        seen.append(list(db.statements))
        return set()

    monkeypatch.setattr(svc, "evidence_ids_in_scope", _fake)
    await svc.materialise_reviews(session, ENGAGEMENT_ID, ORG_ID)
    assert seen == [["flush"]]


# ---------------------------------------------------------------------------
# evidence_ids_in_scope goes through _fetch_evidence_by_scf
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_evidence_ids_in_scope_uses_fetch_evidence_by_scf(monkeypatch):
    calls = []

    async def _fake_fetch(db, org_id, scf_ids):
        calls.append((org_id, set(scf_ids)))
        return (
            {"IAC-01": [{"id": uuid4()}]},
            {
                "IAC-01": [
                    {"evidence_id": "E-IAC-07", "artifact_title": "t", "area_of_focus": "a"},
                    {"evidence_id": "E-IAC-08", "artifact_title": "t", "area_of_focus": "a"},
                ],
                "GOV-01": [{"evidence_id": "E-IAC-07", "artifact_title": "t", "area_of_focus": "a"}],
            },
        )

    monkeypatch.setattr(ae, "_fetch_evidence_by_scf", _fake_fetch)
    session = _FakeSession(scope_scf_ids=["IAC-01", "GOV-01"])

    ids = await svc.evidence_ids_in_scope(session, ENGAGEMENT_ID, ORG_ID)

    assert ids == {"E-IAC-07", "E-IAC-08"}
    assert calls == [(ORG_ID, {"IAC-01", "GOV-01"})]

    scope_sql = str(session.statements[0].compile(dialect=postgresql.dialect()))
    assert "engagement_control_scope.scope_status" in scope_sql
    assert "LEFT OUTER JOIN scf_catalog_controls" in scope_sql
    assert "scf_catalog_controls.status" in scope_sql  # _not_retired()
    assert "audit_engagements.organization_id" in scope_sql
    params = session.statements[0].compile(dialect=postgresql.dialect()).params
    assert "in_scope" in params.values()
    assert "deprecated" in params.values()


@pytest.mark.asyncio
async def test_evidence_ids_in_scope_empty_scope_skips_fetch(monkeypatch):
    async def _boom(*_a, **_k):
        raise AssertionError("fetch must not run for an empty scope")

    monkeypatch.setattr(ae, "_fetch_evidence_by_scf", _boom)
    session = _FakeSession(scope_scf_ids=[])
    assert await svc.evidence_ids_in_scope(session, ENGAGEMENT_ID, ORG_ID) == set()


# ---------------------------------------------------------------------------
# record_transition / record_reviewer_assignment
# ---------------------------------------------------------------------------

@pytest.fixture
def audit_calls(monkeypatch):
    calls = []

    async def _fake_log(**kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr(svc, "log_entity_changes", _fake_log)
    return calls


def _review(status="submitted"):
    return SimpleNamespace(
        id=uuid4(),
        engagement_id=ENGAGEMENT_ID,
        organization_id=ORG_ID,
        evidence_id="E-IAC-07",
        status=status,
        assigned_reviewer_user_id=None,
        last_decision_by_user_id=None,
        last_decision_at=None,
        decision_note=None,
        updated_at=None,
    )


@pytest.mark.asyncio
async def test_record_transition_updates_review_and_adds_one_event(audit_calls):
    session = _FakeSession()
    review = _review("submitted")
    actor = uuid4()

    event = await svc.record_transition(
        session, review=review, to_status="action_required", actor_user_id=actor,
        actor_kind="auditor", note="  Need the Q3 export  ",
    )

    assert review.status == "action_required"
    assert review.last_decision_by_user_id == actor
    assert review.last_decision_at is not None and review.last_decision_at.tzinfo is None
    assert review.decision_note == "Need the Q3 export"
    assert review.updated_at == review.last_decision_at

    assert session.added == [event]
    assert isinstance(event, EngagementEvidenceReviewEvent)
    assert event.event_type == "transition"
    assert event.review_id == review.id
    assert event.engagement_id == ENGAGEMENT_ID
    assert event.organization_id == ORG_ID
    assert event.evidence_id == "E-IAC-07"
    assert event.from_status == "submitted"
    assert event.to_status == "action_required"
    assert event.actor_user_id == actor
    assert event.actor_kind == "auditor"
    assert event.note == "Need the Q3 export"

    assert session.statements == []  # no SQL issued; the ORM add is the write
    assert session.commits == 0

    assert len(audit_calls) == 1
    call = audit_calls[0]
    assert call["db"] is session
    assert call["organization_id"] == ORG_ID
    assert call["entity_type"] == "engagement_evidence_review"
    assert call["entity_id"] == review.id
    assert call["action"] == "update"
    assert call["changed_by_user_id"] == actor
    assert call["old_values"] == {"status": "submitted"}
    assert call["new_values"] == {"status": "action_required"}
    assert call["tracked_fields"] == {"status"}
    assert call["action_source"] is None
    assert call["request_id"] is None


@pytest.mark.asyncio
async def test_record_transition_blank_note_is_stored_as_none(audit_calls):
    session = _FakeSession()
    review = _review("open")
    event = await svc.record_transition(
        session, review=review, to_status="in_progress", actor_user_id=uuid4(),
        actor_kind="auditor", note="   ",
    )
    assert event.note is None
    assert review.decision_note is None


@pytest.mark.asyncio
async def test_record_transition_uses_request_context(audit_calls, monkeypatch):
    request_id = uuid4()
    request = SimpleNamespace(state=SimpleNamespace(audit_request_id=request_id))
    monkeypatch.setattr(svc, "detect_action_source", lambda req: "web" if req is request else None)

    await svc.record_transition(
        _FakeSession(), review=_review("action_required"), to_status="submitted",
        actor_user_id=uuid4(), actor_kind="org", note="Attached", request=request,
    )
    assert audit_calls[0]["action_source"] == "web"
    assert audit_calls[0]["request_id"] == request_id


@pytest.mark.asyncio
async def test_record_reviewer_assignment_adds_reviewer_assigned_event(audit_calls):
    session = _FakeSession()
    review = _review("in_progress")
    reviewer, actor = uuid4(), uuid4()

    event = await svc.record_reviewer_assignment(
        session, review=review, reviewer_user_id=reviewer, actor_user_id=actor, actor_kind="auditor",
    )

    assert review.assigned_reviewer_user_id == reviewer
    assert review.status == "in_progress"
    assert review.updated_at is not None
    assert session.added == [event]
    assert event.event_type == "reviewer_assigned"
    assert event.from_status == event.to_status == "in_progress"
    assert event.actor_user_id == actor
    assert event.actor_kind == "auditor"
    assert event.note is None
    assert session.commits == 0

    assert len(audit_calls) == 1
    call = audit_calls[0]
    assert call["entity_type"] == "engagement_evidence_review"
    assert call["action"] == "update"
    assert call["old_values"] == {"assigned_reviewer_user_id": None}
    assert call["new_values"] == {"assigned_reviewer_user_id": reviewer}
    assert call["changed_by_user_id"] == actor


@pytest.mark.asyncio
async def test_record_reviewer_unassignment(audit_calls):
    review = _review("submitted")
    previous = uuid4()
    review.assigned_reviewer_user_id = previous

    event = await svc.record_reviewer_assignment(
        _FakeSession(), review=review, reviewer_user_id=None, actor_user_id=uuid4(), actor_kind="org",
    )
    assert review.assigned_reviewer_user_id is None
    assert event.from_status == event.to_status == "submitted"
    assert audit_calls[0]["old_values"] == {"assigned_reviewer_user_id": previous}
    assert audit_calls[0]["new_values"] == {"assigned_reviewer_user_id": None}
    assert isinstance(audit_calls[0]["entity_id"], UUID)
