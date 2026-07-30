"""Tests for CDM control-level consolidation (issue 722).

``consolidate_proposals`` (heuristic phase) and ``recompute_proposals_llm``
(LLM phase) are exercised against scripted fake sessions that dispatch on
statement type/entity rather than call order, since the pass interleaves
selects, upserts, link-updates and per-group commits.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, List
from uuid import UUID, uuid4

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["ENABLE_CDM"] = "true"

from sqlalchemy import Select, Update  # noqa: E402

from models import AuditLog, CDMControlProposal  # noqa: E402
from services import cdm_consolidation, cdm_intent  # noqa: E402
from services.cdm_consolidation import (  # noqa: E402
    citations_fingerprint,
    consolidate_proposals,
    derive_proposal_status,
    recompute_proposals_llm,
)

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
CONTROL_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1")
CONTROL_B = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa2")
DOC_ID = UUID("11111111-1111-1111-1111-111111111111")
DOC_SHA = "d" * 64
KB = "kb-test-1"
SCF_ID = "MON99"  # deliberately hyphen-free; realism of the code is irrelevant here
CONTROL_NAME = "Analyze Traffic for Covert Exfiltration"


def _mapping_row(
    control_id: UUID,
    doc_id: UUID = DOC_ID,
    *,
    status: str = "proposed",
    score: float = 0.5,
    start: int = 0,
    end: int = 100,
    proposal_id: UUID | None = None,
    doc_sha: str | None = DOC_SHA,
) -> tuple:
    """Shape of the pass's big mapping select (9 columns)."""
    return (
        uuid4(), control_id, doc_id, status, score, start, end, proposal_id, doc_sha
    )


class _FakeConsolSession:
    """Dispatches on statement entity, not call order."""

    def __init__(self, mapping_rows: List[tuple], existing: List[CDMControlProposal] | None = None):
        self.mapping_rows = list(mapping_rows)
        self.proposals: dict[tuple, CDMControlProposal] = {}
        for proposal in existing or []:
            key = (proposal.organization_id, proposal.scoped_control_id, proposal.cdm_document_id)
            self.proposals[key] = proposal
        self.added: List[Any] = []
        self.link_updates: List[Any] = []
        self.proposal_updates: List[Any] = []
        self.commits = 0
        self.rollbacks = 0

    # -- helpers -------------------------------------------------------
    @staticmethod
    def _params(stmt) -> dict:
        return dict(stmt.compile().params)

    def _proposal_for_stmt(self, stmt) -> CDMControlProposal | None:
        values = set()
        for value in self._params(stmt).values():
            if isinstance(value, UUID):
                values.add(value)
        for (org, control, doc), proposal in self.proposals.items():
            if {org, control, doc} <= values:
                return proposal
        return None

    # -- session protocol ---------------------------------------------
    def execute(self, stmt):
        if isinstance(stmt, Update):
            table_name = stmt.table.name
            rowcount = 1
            for value in self._params(stmt).values():
                if isinstance(value, (list, tuple)):
                    rowcount = len(value)
            if table_name == "cdm_mappings":
                self.link_updates.append(stmt)
            else:
                self.proposal_updates.append(stmt)
                # Apply the update's values to the in-memory proposal so
                # later groups and assertions observe them.
                target = None
                for value in self._params(stmt).values():
                    if isinstance(value, UUID):
                        for proposal in self.proposals.values():
                            if proposal.id == value:
                                target = proposal
                if target is not None:
                    for key, value in stmt.compile().params.items():
                        base = key.rstrip("0123456789").rstrip("_")
                        if hasattr(target, base) and base != "id":
                            setattr(target, base, value)

            class _UpdateResult:
                pass

            result = _UpdateResult()
            result.rowcount = rowcount
            return result

        assert isinstance(stmt, Select)
        descriptions = stmt.column_descriptions
        first = descriptions[0]
        entity = first.get("entity")
        if entity is CDMControlProposal and first.get("name") != "status":
            proposal = self._proposal_for_stmt(stmt)

            class _ScalarResult:
                def scalar_one_or_none(self_inner):
                    return proposal

                def scalar_one(self_inner):
                    assert proposal is not None
                    return proposal

            return _ScalarResult()

        rows = self.mapping_rows

        class _RowsResult:
            def all(self_inner):
                return list(rows)

        return _RowsResult()

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def flush(self) -> None:
        for obj in self.added:
            if isinstance(obj, CDMControlProposal):
                if obj.id is None:
                    obj.id = uuid4()
                key = (obj.organization_id, obj.scoped_control_id, obj.cdm_document_id)
                self.proposals.setdefault(key, obj)

    def commit(self) -> None:
        self.flush()
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def _existing_proposal(
    control_id: UUID,
    *,
    status: str = "proposed",
    fingerprint: str,
    score: float = 0.5,
    rationale: str | None = None,
    provider: str | None = None,
) -> CDMControlProposal:
    return CDMControlProposal(
        id=uuid4(),
        organization_id=ORG_ID,
        scoped_control_id=control_id,
        cdm_document_id=DOC_ID,
        status=status,
        consolidated_score=score,
        rationale=rationale,
        citation_count=1,
        citations_fingerprint=fingerprint,
        recompute_provider=provider,
        recompute_model_id="m" if provider else None,
        kb_revision=KB,
    )


# ───────────────────────── derive_proposal_status ─────────────────────────


def test_derive_status_any_accepted_wins():
    assert derive_proposal_status(["dismissed", "accepted", "proposed"]) == "accepted"


def test_derive_status_stale_beats_dismissed_and_proposed():
    assert derive_proposal_status(["stale", "dismissed", "proposed"]) == "stale"


def test_derive_status_all_dismissed():
    assert derive_proposal_status(["dismissed", "dismissed"]) == "dismissed"


def test_derive_status_default_proposed():
    assert derive_proposal_status(["proposed", "dismissed"]) == "proposed"
    assert derive_proposal_status([]) == "proposed"


# ───────────────────────── fingerprint ─────────────────────────


def test_fingerprint_includes_document_sha():
    """Same offsets, different extracted-text sha must differ: re-extraction
    can rewrite text at identical offsets."""
    offsets = [(0, 10), (20, 30)]
    assert citations_fingerprint("a" * 64, offsets) != citations_fingerprint("b" * 64, offsets)


def test_fingerprint_order_independent():
    assert citations_fingerprint(DOC_SHA, [(0, 10), (20, 30)]) == citations_fingerprint(
        DOC_SHA, [(20, 30), (0, 10)]
    )


# ───────────────────────── heuristic pass ─────────────────────────


def test_grouping_creates_one_proposal_per_control_document_pair():
    """Three citations for control A + one for B produce two proposals."""
    rows = [
        _mapping_row(CONTROL_A, score=0.5),
        _mapping_row(CONTROL_A, score=0.7, start=200, end=300),
        _mapping_row(CONTROL_A, score=0.6, start=400, end=500),
        _mapping_row(CONTROL_B, score=0.4, start=600, end=700),
    ]
    session = _FakeConsolSession(rows)

    summary = consolidate_proposals(session, ORG_ID, kb_revision=KB)

    assert summary.groups_seen == 2
    assert summary.proposals_created == 2
    created = [o for o in session.added if isinstance(o, CDMControlProposal)]
    assert len(created) == 2
    by_control = {p.scoped_control_id: p for p in created}
    # Heuristic score is the max citation score
    assert by_control[CONTROL_A].consolidated_score == 0.7
    assert by_control[CONTROL_A].citation_count == 3
    assert by_control[CONTROL_B].citation_count == 1
    # No LLM ran: provider stamp stays clear
    assert by_control[CONTROL_A].recompute_provider is None
    # Per-group commits
    assert session.commits == 2


def test_grouping_spans_all_statuses_and_derives_status():
    """Partial review must not fragment the group or churn the fingerprint."""
    rows = [
        _mapping_row(CONTROL_A, status="accepted", score=0.7),
        _mapping_row(CONTROL_A, status="proposed", score=0.5, start=200, end=300),
    ]
    session = _FakeConsolSession(rows)

    summary = consolidate_proposals(session, ORG_ID, kb_revision=KB)

    assert summary.proposals_created == 1
    created = [o for o in session.added if isinstance(o, CDMControlProposal)][0]
    assert created.citation_count == 2
    assert created.status == "accepted"


def test_identical_fingerprint_is_noop():
    """Sticky decisions — unchanged evidence never rewrites the proposal."""
    row = _mapping_row(CONTROL_A, status="proposed", score=0.5)
    fingerprint = citations_fingerprint(DOC_SHA, [(row[5], row[6])])
    existing = _existing_proposal(
        CONTROL_A, status="dismissed", fingerprint=fingerprint
    )
    session = _FakeConsolSession([row], existing=[existing])

    summary = consolidate_proposals(session, ORG_ID, kb_revision=KB)

    assert summary.proposals_unchanged == 1
    assert summary.proposals_updated == 0
    assert summary.proposals_resurrected == 0
    assert existing.status == "dismissed"
    assert [o for o in session.added if isinstance(o, AuditLog)] == []


def test_changed_fingerprint_resurrects_dismissed_with_audit():
    """New evidence reopens a dismissed decision, with an audit row."""
    row = _mapping_row(CONTROL_A, status="proposed", score=0.8, start=999, end=1200)
    existing = _existing_proposal(
        CONTROL_A, status="dismissed", fingerprint="0" * 64
    )
    existing.dismiss_reason = "not relevant"
    session = _FakeConsolSession([row], existing=[existing])

    summary = consolidate_proposals(session, ORG_ID, kb_revision=KB)

    assert summary.proposals_updated == 1
    assert summary.proposals_resurrected == 1
    audit_rows = [o for o in session.added if isinstance(o, AuditLog)]
    assert len(audit_rows) == 1
    audit = audit_rows[0]
    assert audit.entity_type == "cdm_control_proposal"
    assert audit.action == "resurrected"
    assert audit.old_value == "dismissed"
    body = json.loads(audit.new_value)
    assert body["status"] == "proposed"
    assert body["old_fingerprint"] == "0" * 64
    assert audit.action_source == "system"


def test_changed_fingerprint_clears_recompute_stamp_keeps_rationale():
    """Prior LLM rationale survives a refresh; the stamp clears so the
    recompute pass revisits the group."""
    row = _mapping_row(CONTROL_A, status="proposed", score=0.4, start=50, end=90)
    existing = _existing_proposal(
        CONTROL_A,
        status="proposed",
        fingerprint="0" * 64,
        rationale="old judgment",
        provider="claude",
    )
    session = _FakeConsolSession([row], existing=[existing])

    summary = consolidate_proposals(session, ORG_ID, kb_revision=KB)

    assert summary.proposals_updated == 1
    assert existing.recompute_provider is None
    assert existing.rationale == "old judgment"


def test_unlinked_citations_are_linked():
    """Provenance rows adopt their parent via the link update."""
    rows = [
        _mapping_row(CONTROL_A, score=0.5),
        _mapping_row(CONTROL_A, score=0.6, start=200, end=300),
    ]
    session = _FakeConsolSession(rows)

    summary = consolidate_proposals(session, ORG_ID, kb_revision=KB)

    assert summary.citations_linked == 2
    assert len(session.link_updates) == 1


# ───────────────────────── recompute (LLM) pass ─────────────────────────


class _FakeRecomputeSession:
    """Scripted for the recompute pass: pending select, citations selects,
    optimistic stamp updates."""

    def __init__(self, pending_rows: List[tuple], citations_by_proposal: dict):
        self.pending_rows = list(pending_rows)
        self.citations_by_proposal = dict(citations_by_proposal)
        self.update_stmts: List[Any] = []
        self.commits = 0
        self._pending_served = False

    def execute(self, stmt):
        if isinstance(stmt, Update):
            self.update_stmts.append(stmt)

            class _UpdateResult:
                rowcount = 1

            return _UpdateResult()

        if not self._pending_served:
            self._pending_served = True
            rows = self.pending_rows
        else:
            proposal_id = next(
                value
                for value in stmt.compile().params.values()
                if isinstance(value, UUID)
            )
            rows = self.citations_by_proposal.get(proposal_id, [])

        class _RowsResult:
            def all(self_inner):
                return list(rows)

        return _RowsResult()

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        pass


class _FakeProvider:
    name = "fake"

    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts: List[str] = []

    def classify(self, request):
        self.prompts.append(request.prompt)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return cdm_intent.IntentResponse(text=response, model_id="fake-model-1")


def _pending_row(proposal_id: UUID) -> tuple:
    return (
        proposal_id, CONTROL_A, DOC_ID, SCF_ID,
        CONTROL_NAME,
        "Does the organisation analyse traffic?", "policy.pdf",
    )


def test_recompute_success_stamps_score_and_rationale():
    """One call per group; the prompt carries every citation together."""
    proposal_id = uuid4()
    session = _FakeRecomputeSession(
        [_pending_row(proposal_id)],
        {proposal_id: [("5.2 Monitoring", 0.57, "excerpt one"), ("1.0 Scope", 0.52, "excerpt two")]},
    )
    provider = _FakeProvider(
        ['{"relevance": 0.82, "rationale": "Sections 5.2 and 1.0 together evidence it."}']
    )

    summary = recompute_proposals_llm(
        session, ORG_ID, provider=provider, timeout_s=1.0, budget_s=60.0
    )

    assert summary.proposals_recomputed == 1
    assert summary.recompute_failures == 0
    assert summary.proposals_remaining == 0
    assert not summary.budget_exhausted
    # Prompt carried BOTH citations together — the core "context is king" claim
    assert "excerpt one" in provider.prompts[0]
    assert "excerpt two" in provider.prompts[0]
    assert CONTROL_NAME in provider.prompts[0]
    assert session.commits == 1
    assert len(session.update_stmts) == 1


def test_recompute_provider_failure_falls_back_and_continues():
    """A failed group keeps heuristic values; later groups still run."""
    failing_id, ok_id = uuid4(), uuid4()
    session = _FakeRecomputeSession(
        [_pending_row(failing_id), _pending_row(ok_id)],
        {
            failing_id: [("s", 0.5, "text")],
            ok_id: [("s", 0.6, "text")],
        },
    )
    provider = _FakeProvider(
        [
            cdm_intent.IntentProviderError("boom"),
            '{"relevance": 0.4, "rationale": "weak"}',
        ]
    )

    summary = recompute_proposals_llm(
        session, ORG_ID, provider=provider, timeout_s=1.0, budget_s=60.0
    )

    assert summary.proposals_recomputed == 1
    assert summary.recompute_failures == 1
    # Only the successful group was stamped
    assert len(session.update_stmts) == 1


def test_recompute_malformed_json_counts_as_failure():
    proposal_id = uuid4()
    session = _FakeRecomputeSession(
        [_pending_row(proposal_id)], {proposal_id: [("s", 0.5, "text")]}
    )
    provider = _FakeProvider(["not json at all"])

    summary = recompute_proposals_llm(
        session, ORG_ID, provider=provider, timeout_s=1.0, budget_s=60.0
    )

    assert summary.proposals_recomputed == 0
    assert summary.recompute_failures == 1
    assert session.update_stmts == []


def test_recompute_budget_exhaustion_reports_remaining():
    """The caller re-enqueues on budget_exhausted — remaining must be honest."""
    ids = [uuid4(), uuid4(), uuid4()]
    session = _FakeRecomputeSession(
        [_pending_row(i) for i in ids],
        {i: [("s", 0.5, "text")] for i in ids},
    )
    provider = _FakeProvider(['{"relevance": 0.5, "rationale": "r"}'] * 3)

    ticks = iter([0.0, 0.0, 100.0, 100.0, 100.0, 100.0])

    summary = recompute_proposals_llm(
        session,
        ORG_ID,
        provider=provider,
        timeout_s=1.0,
        budget_s=50.0,
        clock=lambda: next(ticks),
    )

    assert summary.budget_exhausted
    assert summary.proposals_recomputed == 1
    assert summary.proposals_remaining == 2


def test_recompute_disabled_provider_is_noop(monkeypatch):
    """Keyless deployments: the pass returns immediately, nothing raises."""
    monkeypatch.setattr(cdm_intent, "get_intent_provider", lambda name=None: None)
    session = _FakeRecomputeSession([], {})

    summary = recompute_proposals_llm(session, ORG_ID)

    assert summary.proposals_recomputed == 0
    assert summary.proposals_remaining == 0


def test_recompute_out_of_range_relevance_is_failure():
    proposal_id = uuid4()
    session = _FakeRecomputeSession(
        [_pending_row(proposal_id)], {proposal_id: [("s", 0.5, "text")]}
    )
    provider = _FakeProvider(['{"relevance": 1.7, "rationale": "r"}'])

    summary = recompute_proposals_llm(
        session, ORG_ID, provider=provider, timeout_s=1.0, budget_s=60.0
    )

    assert summary.recompute_failures == 1
    assert session.update_stmts == []
