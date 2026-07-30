"""Contract tests for the #712 within-domain precision filters.

Two additive filters in ``compute_mappings_v2`` — a per-control proposal cap
and a within-control relative score cutoff — plus fail-closed per-document
intent eligibility on ``DocumentIntentGate`` when a provider is enabled.

Each test guards a specific way the review queue was silently flooded:

* 789 above-floor proposals from one document is structural volume, not a
  scoring bug — the cap bounds reviewer effort per control regardless of
  document length;
* the cutoff prunes the long tail relative to each control's own best hit,
  so it degrades gracefully where a raised absolute floor would not;
* documents whose classification never arrived (``pending``/``failed`` under
  an enabled provider) must produce **no** proposals, never ungated ones —
  fail-open under an enabled provider turned a visible classification failure
  into 34,704 silently ungated proposals.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import cdm_mapping, cdm_retrieval, cdm_scoring  # noqa: E402

ORG_ID = UUID("00000000-0000-0000-0000-0000000000a1")
CONTROL_ID = UUID("00000000-0000-0000-0000-0000000000c1")
DOC_ID = UUID("00000000-0000-0000-0000-0000000000d1")
DOC_PENDING_ID = UUID("00000000-0000-0000-0000-0000000000d2")
SCF_ID = "TESTCONTROL"

# _domain_for_scf_id("TESTCONTROL") — the prefix before the first hyphen.
TEST_DOMAIN = "TESTCONTROL"
OTHER_DOMAIN = "OTHERDOMAIN"

DOCUMENT_TEXT = (
    "Supplier Management Policy\n\n"
    "All third-party suppliers must complete a security risk assessment "
    "before any contract is signed. Procurement retains the completed "
    "assessment for six years.\n\n"
    "Supplier access credentials are revoked within   24  hours of "
    "termination.\n"
)
CHUNK_BODY = (
    "All third-party suppliers must complete a security risk assessment "
    "before any contract is signed. Procurement retains the completed "
    "assessment for six years."
)
CHUNK_START = DOCUMENT_TEXT.index(CHUNK_BODY)

OBJECTIVES = (
    "A risk assessment is conducted prior to acquiring third-party services.",
    "Completed supplier assessments are retained for a defined period.",
)


def _chunk(**overrides) -> cdm_retrieval.RetrievedChunk:
    kwargs = {
        "chunk_id": uuid4(),
        "cdm_document_id": DOC_ID,
        "ordinal": 0,
        "heading": "4.2 Supplier Onboarding",
        "body": CHUNK_BODY,
        "body_norm": " ".join(CHUNK_BODY.split()).lower(),
        "char_start": CHUNK_START,
        "char_end": CHUNK_START + len(CHUNK_BODY),
        "ts_rank": 0.4,
        "matched_objectives": OBJECTIVES,
    }
    kwargs.update(overrides)
    return cdm_retrieval.RetrievedChunk(**kwargs)


class _StubBackend:
    name = "stub_fts"
    can_produce_mappings = True

    def __init__(self, chunks=None):
        self._chunks = list(chunks if chunks is not None else [_chunk()])

    def search(self, session, org_id, query, *, limit):
        return list(self._chunks), len(self._chunks)


class _FakeSession:
    """Records added rows; answers the queries the helper issues."""

    def __init__(self, *, control_rows=None):
        self._control_rows = (
            control_rows
            if control_rows is not None
            else [(CONTROL_ID, SCF_ID, "Third-Party Risk", "Does the org assess suppliers?")]
        )
        self.added: list = []
        self.updates: list = []
        self.commits = 0
        self.documents = {
            DOC_ID: SimpleNamespace(id=DOC_ID, organization_id=ORG_ID),
            DOC_PENDING_ID: SimpleNamespace(id=DOC_PENDING_ID, organization_id=ORG_ID),
        }

    def execute(self, statement, params=None):
        compiled = str(statement)
        if "scoped_controls" in compiled and "cdm_mappings" not in compiled:
            return SimpleNamespace(all=lambda: list(self._control_rows))
        if "UPDATE cdm_mappings" in compiled:
            self.updates.append(statement)
            return SimpleNamespace()
        return SimpleNamespace(first=lambda: None)

    def get(self, model, pk):
        return self.documents.get(pk)

    def add(self, row):
        self.added.append(row)

    def commit(self):
        self.commits += 1


class _GateSession:
    """Answers the gate's single preload query with scripted rows."""

    def __init__(self, rows):
        self._rows = rows

    def execute(self, statement, params=None):
        return SimpleNamespace(fetchall=lambda: list(self._rows))


def _row(document_id, intent_status, domain=None):
    return SimpleNamespace(
        document_id=document_id, intent_status=intent_status, domain=domain
    )


def _gate(rows, *, require_defined_intent):
    return cdm_mapping.DocumentIntentGate(
        _GateSession(rows), ORG_ID, require_defined_intent=require_defined_intent
    )


def _run(session, backend, **kwargs):
    defaults = dict(
        extracted_text_loader=lambda doc: DOCUMENT_TEXT,
        backend=backend,
        objectives_loader=lambda ids: {SCF_ID: list(OBJECTIVES)},
        kb_revision="test-rev",
    )
    defaults.update(kwargs)
    return cdm_mapping.compute_mappings_v2(session, ORG_ID, **defaults)


def _score_is_ts_rank(monkeypatch):
    """Make the composed score equal ts_rank exactly.

    Cap and cutoff order candidates by *composed* score; pinning the weights
    to ts_rank-only makes each test's intended ordering explicit instead of
    an artifact of term overlap against the fixture text.
    """
    monkeypatch.setenv("CDM_SCORE_WEIGHT_TS_RANK", "1")
    monkeypatch.setenv("CDM_SCORE_WEIGHT_OBJECTIVE_COVERAGE", "0")
    monkeypatch.setenv("CDM_SCORE_WEIGHT_TERM_OVERLAP", "0")


# --- env plumbing ---------------------------------------------------------


def test_cap_default_and_invalid_values(monkeypatch):
    monkeypatch.delenv("CDM_MAX_PROPOSALS_PER_CONTROL", raising=False)
    assert cdm_scoring.get_max_proposals_per_control() == 3

    for bad in ("abc", "0", "-2"):
        monkeypatch.setenv("CDM_MAX_PROPOSALS_PER_CONTROL", bad)
        assert cdm_scoring.get_max_proposals_per_control() == 3

    monkeypatch.setenv("CDM_MAX_PROPOSALS_PER_CONTROL", "7")
    assert cdm_scoring.get_max_proposals_per_control() == 7


def test_cutoff_default_and_out_of_range_values(monkeypatch):
    monkeypatch.delenv("CDM_RELATIVE_SCORE_CUTOFF", raising=False)
    assert cdm_scoring.get_relative_score_cutoff() == 0.6

    for bad in ("1.5", "-0.1", "nonsense"):
        monkeypatch.setenv("CDM_RELATIVE_SCORE_CUTOFF", bad)
        assert cdm_scoring.get_relative_score_cutoff() == 0.6

    monkeypatch.setenv("CDM_RELATIVE_SCORE_CUTOFF", "0.4")
    assert cdm_scoring.get_relative_score_cutoff() == 0.4


# --- per-control cap ------------------------------------------------------


def test_cap_keeps_top_n_by_composed_score(monkeypatch):
    _score_is_ts_rank(monkeypatch)
    monkeypatch.setenv("CDM_MAX_PROPOSALS_PER_CONTROL", "3")
    monkeypatch.setenv("CDM_RELATIVE_SCORE_CUTOFF", "0")

    session = _FakeSession()
    summary = _run(
        session,
        _StubBackend(
            chunks=[_chunk(ts_rank=r) for r in (0.5, 0.9, 0.6, 0.8, 0.7)]
        ),
    )

    assert summary.mappings_created == 3
    assert summary.mappings_skipped_by_cap == 2
    kept_scores = sorted((row.relevance_score for row in session.added), reverse=True)
    assert kept_scores == pytest.approx([0.9, 0.8, 0.7])


def test_relative_cutoff_prunes_tail_of_best_hit(monkeypatch):
    _score_is_ts_rank(monkeypatch)
    monkeypatch.setenv("CDM_MAX_PROPOSALS_PER_CONTROL", "10")
    monkeypatch.setenv("CDM_RELATIVE_SCORE_CUTOFF", "0.6")

    session = _FakeSession()
    summary = _run(
        session,
        _StubBackend(chunks=[_chunk(ts_rank=r) for r in (0.9, 0.8, 0.5, 0.2)]),
    )

    # Best hit 0.9 → cutoff 0.54: 0.5 and 0.2 are tail, 0.8 survives.
    assert summary.mappings_created == 2
    assert summary.mappings_skipped_by_cap == 2


def test_best_scoring_excerpt_always_survives(monkeypatch):
    """AC #712: zero controls lose their best-scoring excerpt.

    Even the harshest legal configuration — cap 1, cutoff 1.0 — keeps the
    control's best hit; the filters can only ever remove the tail.
    """
    _score_is_ts_rank(monkeypatch)
    monkeypatch.setenv("CDM_MAX_PROPOSALS_PER_CONTROL", "1")
    monkeypatch.setenv("CDM_RELATIVE_SCORE_CUTOFF", "1.0")

    session = _FakeSession()
    summary = _run(
        session,
        _StubBackend(chunks=[_chunk(ts_rank=r) for r in (0.3, 0.9, 0.6)]),
    )

    assert summary.mappings_created == 1
    assert session.added[0].relevance_score == pytest.approx(0.9)
    assert summary.mappings_skipped_by_cap == 2


def test_summary_reconciles_every_dropped_hit(monkeypatch):
    """AC #712: hits_evaluated = created + all skip counters."""
    _score_is_ts_rank(monkeypatch)
    monkeypatch.setenv("CDM_MAX_PROPOSALS_PER_CONTROL", "2")
    monkeypatch.setenv("CDM_RELATIVE_SCORE_CUTOFF", "0")

    session = _FakeSession()
    summary = _run(
        session,
        _StubBackend(
            chunks=[_chunk(ts_rank=r) for r in (0.9, 0.8, 0.7, 0.25, 0.2)]
        ),
        score_threshold=0.3,
    )

    assert summary.hits_evaluated == 5
    assert summary.mappings_created == 2
    assert summary.mappings_skipped_below_threshold == 2
    assert summary.mappings_skipped_by_cap == 1
    assert summary.hits_evaluated == (
        summary.mappings_created
        + summary.mappings_skipped_below_threshold
        + summary.mappings_skipped_duplicate
        + summary.mappings_skipped_unresolved_offset
        + summary.mappings_skipped_by_intent_gate
        + summary.mappings_skipped_by_cap
    )


def test_score_weights_json_records_active_cap_and_cutoff(monkeypatch):
    """Provenance: a historical row must be interpretable after retuning."""
    _score_is_ts_rank(monkeypatch)
    monkeypatch.setenv("CDM_MAX_PROPOSALS_PER_CONTROL", "4")
    monkeypatch.setenv("CDM_RELATIVE_SCORE_CUTOFF", "0.5")

    session = _FakeSession()
    _run(session, _StubBackend())

    weights_json = session.added[0].score_weights
    assert weights_json["max_proposals_per_control"] == 4
    assert weights_json["relative_score_cutoff"] == 0.5


def test_summary_positional_construction_unchanged():
    """The six pre-#712 fields stay positional; new counters default to 0."""
    summary = cdm_mapping.ComputeMappingsSummary(0, 0, 0, 0, 0, 0)
    assert summary.mappings_skipped_by_intent_gate == 0
    assert summary.mappings_skipped_by_cap == 0
    assert summary.documents_excluded_awaiting_intent == 0


# --- fail-closed intent eligibility (gate semantics) ----------------------


def test_fail_closed_excludes_pending_and_failed_documents():
    doc_pending, doc_failed, doc_classified = uuid4(), uuid4(), uuid4()
    gate = _gate(
        [
            _row(doc_pending, "pending"),
            _row(doc_failed, "failed"),
            _row(doc_classified, "classified", TEST_DOMAIN),
        ],
        require_defined_intent=True,
    )

    allowed = gate.allowed_documents(TEST_DOMAIN)
    assert allowed == {doc_classified}
    assert gate.documents_excluded_awaiting_intent == 2


def test_fail_closed_all_pending_corpus_allows_nothing():
    """The incident shape: enabled provider, classification never arrived.

    The fail-open branch returned None (no filtering) here and produced
    34,704 ungated proposals. Fail-closed must return an empty set — zero
    proposals — never None.
    """
    gate = _gate(
        [_row(uuid4(), "pending"), _row(uuid4(), "pending")],
        require_defined_intent=True,
    )

    assert gate.allowed_documents(TEST_DOMAIN) == set()
    assert gate.documents_excluded_awaiting_intent == 2


def test_fail_closed_unclassified_participates_everywhere():
    """The classifier abstaining is a defined outcome, not an undefined one."""
    doc_unclassified = uuid4()
    gate = _gate(
        [_row(doc_unclassified, "unclassified")], require_defined_intent=True
    )

    assert doc_unclassified in gate.allowed_documents(TEST_DOMAIN)
    assert doc_unclassified in gate.allowed_documents(OTHER_DOMAIN)


def test_fail_closed_stale_participates_via_existing_intents():
    """Some gate beats no gate: stale intents still scope by domain."""
    doc_stale = uuid4()
    gate = _gate(
        [_row(doc_stale, "stale", TEST_DOMAIN)], require_defined_intent=True
    )

    assert doc_stale in gate.allowed_documents(TEST_DOMAIN)
    assert doc_stale not in gate.allowed_documents(OTHER_DOMAIN)


def test_fail_closed_stale_without_intent_rows_is_allowed():
    """A domain-scoped status with no surviving intent row cannot be placed,
    so it is allowed everywhere, not excluded everywhere."""
    doc_stale = uuid4()
    gate = _gate([_row(doc_stale, "stale")], require_defined_intent=True)

    assert doc_stale in gate.allowed_documents(TEST_DOMAIN)


def test_fail_closed_domain_none_still_excludes_awaiting_documents():
    """Eligibility is per document, not per domain: a control with no
    parseable domain still must not see awaiting-intent documents."""
    doc_pending, doc_classified = uuid4(), uuid4()
    gate = _gate(
        [
            _row(doc_pending, "pending"),
            _row(doc_classified, "classified", TEST_DOMAIN),
        ],
        require_defined_intent=True,
    )

    allowed = gate.allowed_documents(None)
    assert allowed == {doc_classified}


def test_fail_open_default_path_is_unchanged():
    """With the provider disabled classification never arrives, so the
    original invariant stands: pending is allowed and an unclassified org
    gets no filtering at all."""
    doc_pending = uuid4()
    gate = _gate([_row(doc_pending, "pending")], require_defined_intent=False)

    assert gate.allowed_documents(TEST_DOMAIN) is None
    assert gate.documents_excluded_awaiting_intent == 0


# --- fail-closed intent eligibility (through compute) ---------------------


def test_all_pending_corpus_computes_zero_proposals(monkeypatch):
    """AC #712: the incident sequence yields 0 proposals and a summary that
    explicitly counts the excluded documents — never an ungated flood."""
    _score_is_ts_rank(monkeypatch)

    gate = _gate(
        [_row(DOC_ID, "pending"), _row(DOC_PENDING_ID, "pending")],
        require_defined_intent=True,
    )
    session = _FakeSession()
    summary = _run(session, _StubBackend(), intent_gate=gate)

    assert summary.mappings_created == 0
    assert session.added == []
    assert summary.mappings_skipped_by_intent_gate == 1
    assert summary.documents_excluded_awaiting_intent == 2


def test_mixed_corpus_computes_only_from_classified_subset(monkeypatch):
    """AC #712: some classified, some pending — proposals come only from the
    classified subset."""
    _score_is_ts_rank(monkeypatch)

    gate = _gate(
        [
            _row(DOC_ID, "classified", TEST_DOMAIN),
            _row(DOC_PENDING_ID, "pending"),
        ],
        require_defined_intent=True,
    )
    session = _FakeSession()
    summary = _run(
        session,
        _StubBackend(
            chunks=[
                _chunk(ts_rank=0.9),
                _chunk(ts_rank=0.8, cdm_document_id=DOC_PENDING_ID),
            ]
        ),
        intent_gate=gate,
    )

    assert summary.mappings_created == 1
    assert session.added[0].cdm_document_id == DOC_ID
    assert summary.mappings_skipped_by_intent_gate == 1
    assert summary.documents_excluded_awaiting_intent == 1
