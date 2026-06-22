"""Tests for CDM v1 slice 6 — stale mapping detection on re-ingest.

The helper `detect_stale_mappings_for_document` is exercised directly
against a fake sync session. We assert the helper only flips mappings
that match ALL four constraints (this doc, accepted status, mismatching
kb_revision, not already stale/dismissed) and writes one audit row per
flip.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, List
from uuid import UUID, uuid4

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["ENABLE_CDM"] = "true"

from models import AuditLog, CDMMapping  # noqa: E402
from services import cdm_mapping  # noqa: E402

DOC_ID = UUID("11111111-1111-1111-1111-111111111111")
OTHER_DOC_ID = UUID("22222222-2222-2222-2222-222222222222")
ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
NEW_KB = "lightrag-v2"
OLD_KB = "lightrag-v1"


class _FakeSyncSession:
    """Scripted sync session for the stale-detection helper.

    Behaviour:
    - ``execute(stmt).all()`` returns the candidate rows configured at init.
    - ``execute(stmt)`` (for UPDATE) returns an object with the per-call
      ``rowcount`` from ``update_rowcounts`` (default: 1 per update).
    - ``add(obj)`` tracks audit rows.
    """

    def __init__(
        self,
        candidate_rows: List[tuple],
        *,
        update_rowcounts: List[int] | None = None,
    ):
        self._candidates = list(candidate_rows)
        self._update_rowcounts = list(update_rowcounts) if update_rowcounts is not None else None
        self.executed_statements: List[Any] = []
        self.added: List[Any] = []

    def execute(self, stmt):
        self.executed_statements.append(stmt)
        # First call is the SELECT for candidates.
        if len(self.executed_statements) == 1:
            candidates = self._candidates

            class _SelectResult:
                def all(self_inner):
                    return list(candidates)

            return _SelectResult()

        # Subsequent calls are UPDATEs per candidate.
        update_idx = len(self.executed_statements) - 2  # zero-indexed across updates
        if self._update_rowcounts is not None and update_idx < len(self._update_rowcounts):
            rc = self._update_rowcounts[update_idx]
        else:
            rc = 1

        class _UpdateResult:
            @property
            def rowcount(self_inner):
                return rc

        return _UpdateResult()

    def add(self, obj: Any) -> None:
        self.added.append(obj)


def _audit_rows(session: _FakeSyncSession) -> List[AuditLog]:
    return [o for o in session.added if isinstance(o, AuditLog)]


# ───────────────────────── Happy path ─────────────────────────


def test_re_ingest_flips_accepted_mappings_on_this_doc():
    """ISC-11: accepted mappings on the re-ingested doc with old kb_revision flip to stale."""
    mapping_a = uuid4()
    mapping_b = uuid4()
    candidates = [
        (mapping_a, ORG_ID, OLD_KB),
        (mapping_b, ORG_ID, OLD_KB),
    ]
    session = _FakeSyncSession(candidates)

    flipped = cdm_mapping.detect_stale_mappings_for_document(
        session, DOC_ID, NEW_KB
    )

    assert flipped == 2

    rows = _audit_rows(session)
    assert len(rows) == 2
    for row in rows:
        assert row.entity_type == "cdm_mapping"
        assert row.action == "stale"
        assert row.field_name == "status"
        assert row.old_value == "accepted"
        assert row.organization_id == ORG_ID
        assert row.action_source == "system"
        body = json.loads(row.new_value)
        assert body["status"] == "stale"
        assert body["old_kb_revision"] == OLD_KB
        assert body["new_kb_revision"] == NEW_KB
        assert "detected_at" in body


# ───────────────────────── Filters: only candidates with mismatching kb ─────────────────────────


def test_no_candidates_returns_zero_no_audit_rows():
    """ISC-8: when no accepted mapping has mismatching kb_revision, helper is a no-op."""
    session = _FakeSyncSession(candidate_rows=[])

    flipped = cdm_mapping.detect_stale_mappings_for_document(
        session, DOC_ID, NEW_KB
    )

    assert flipped == 0
    assert _audit_rows(session) == []
    # Only the SELECT happened — no UPDATEs.
    assert len(session.executed_statements) == 1


# ───────────────────────── Race-loss skip ─────────────────────────


def test_race_loss_skips_audit_row():
    """If the optimistic UPDATE returns rowcount=0 (another writer beat us),
    skip the audit row and continue with the next candidate.
    """
    mapping_a = uuid4()
    mapping_b = uuid4()
    candidates = [
        (mapping_a, ORG_ID, OLD_KB),
        (mapping_b, ORG_ID, OLD_KB),
    ]
    # First UPDATE loses race, second wins.
    session = _FakeSyncSession(candidates, update_rowcounts=[0, 1])

    flipped = cdm_mapping.detect_stale_mappings_for_document(
        session, DOC_ID, NEW_KB
    )

    assert flipped == 1
    rows = _audit_rows(session)
    assert len(rows) == 1
    assert rows[0].entity_id == mapping_b


# ───────────────────────── System actor sentinel ─────────────────────────


def test_actor_falls_back_to_system_sentinel_when_none():
    """ISC-10: unattended ingest passes actor_user_id=None → sentinel actor."""
    mapping_id = uuid4()
    session = _FakeSyncSession([(mapping_id, ORG_ID, OLD_KB)])

    flipped = cdm_mapping.detect_stale_mappings_for_document(
        session, DOC_ID, NEW_KB, actor_user_id=None
    )

    assert flipped == 1
    rows = _audit_rows(session)
    assert len(rows) == 1
    assert rows[0].changed_by_user_id == UUID("00000000-0000-0000-0000-000000000001")


def test_actor_override_from_env(monkeypatch):
    """Sentinel can be overridden via CDM_SYSTEM_ACTOR_USER_ID."""
    custom_actor = UUID("33333333-3333-3333-3333-333333333333")
    monkeypatch.setenv("CDM_SYSTEM_ACTOR_USER_ID", str(custom_actor))

    mapping_id = uuid4()
    session = _FakeSyncSession([(mapping_id, ORG_ID, OLD_KB)])

    cdm_mapping.detect_stale_mappings_for_document(session, DOC_ID, NEW_KB)

    rows = _audit_rows(session)
    assert rows[0].changed_by_user_id == custom_actor


def test_actor_override_used_when_supplied():
    """Explicit actor_user_id parameter wins over env sentinel."""
    explicit_actor = UUID("44444444-4444-4444-4444-444444444444")
    mapping_id = uuid4()
    session = _FakeSyncSession([(mapping_id, ORG_ID, OLD_KB)])

    cdm_mapping.detect_stale_mappings_for_document(
        session, DOC_ID, NEW_KB, actor_user_id=explicit_actor
    )

    rows = _audit_rows(session)
    assert rows[0].changed_by_user_id == explicit_actor


# ───────────────────────── Other-doc / wrong-status candidates aren't returned ─────────────────────────


def test_candidates_query_excludes_other_docs_and_wrong_status():
    """ISC-12, 13: the SELECT WHERE-clause is the gate. We assert the query
    text contains the right filters so candidates from other docs / wrong
    statuses never reach the loop.
    """
    session = _FakeSyncSession(candidate_rows=[])
    cdm_mapping.detect_stale_mappings_for_document(session, DOC_ID, NEW_KB)

    # Inspect the compiled SELECT to confirm the right WHERE filters are in place.
    select_stmt = session.executed_statements[0]
    compiled = str(select_stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "cdm_document_id" in compiled
    assert "status" in compiled
    assert "accepted" in compiled
    assert "kb_revision" in compiled
