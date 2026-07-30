"""Contract tests for ``cdm_mapping.compute_mappings_v2`` (CDM v2, epic #709).

These pin the properties that make the mappings table auditable, expressed
against the typed ``RetrievalBackend`` contract rather than a LightRAG
response blob. Each one guards a specific way v1 was silently wrong:

* the score is composed from persisted components, so it can be recomputed
  and argued with — v1's was list position wearing a decimal point;
* a backend that cannot return offsets cannot write a mapping at all;
* an offset that no longer resolves produces no row, not a row pointing at
  the wrong text;
* re-running updates rather than duplicating.

The retrieval backend is a stub because these are tests of the *mapping*
logic. The real SQL is exercised against Postgres by the FTS backend tests
and by the live end-to-end run.
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
SCF_ID = "TESTCONTROL"

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
    """Minimal backend honouring the typed contract."""

    name = "stub_fts"
    can_produce_mappings = True

    def __init__(self, chunks=None, total=None, raises=None):
        self._chunks = list(chunks if chunks is not None else [_chunk()])
        self._total = total if total is not None else len(self._chunks)
        self._raises = raises
        self.calls: list[cdm_retrieval.ControlQuery] = []

    def search(self, session, org_id, query, *, limit):
        self.calls.append(query)
        if self._raises is not None:
            raise self._raises
        return list(self._chunks), self._total


class _OffsetlessBackend:
    name = "offsetless"
    can_produce_mappings = False

    def search(self, session, org_id, query, *, limit):  # pragma: no cover
        raise AssertionError("must never be reached")


class _FakeSession:
    """Records added rows; answers the two queries the helper issues."""

    def __init__(self, *, control_rows=None, existing_mapping_id=None):
        self._control_rows = (
            control_rows
            if control_rows is not None
            else [(CONTROL_ID, SCF_ID, "Third-Party Risk", "Does the org assess suppliers?")]
        )
        self._existing_mapping_id = existing_mapping_id
        self.added: list = []
        self.updates: list = []
        self.commits = 0
        self.documents = {
            DOC_ID: SimpleNamespace(id=DOC_ID, organization_id=ORG_ID)
        }

    def execute(self, statement, params=None):
        compiled = str(statement)
        if "scoped_controls" in compiled and "cdm_mappings" not in compiled:
            return SimpleNamespace(all=lambda: list(self._control_rows))
        if "UPDATE cdm_mappings" in compiled:
            self.updates.append(statement)
            return SimpleNamespace()
        # Duplicate probe against cdm_mappings.
        found = (
            (self._existing_mapping_id,) if self._existing_mapping_id else None
        )
        return SimpleNamespace(first=lambda: found)

    def get(self, model, pk):
        return self.documents.get(pk)

    def add(self, row):
        self.added.append(row)

    def commit(self):
        self.commits += 1


def _run(session, backend, **kwargs):
    defaults = dict(
        extracted_text_loader=lambda doc: DOCUMENT_TEXT,
        backend=backend,
        objectives_loader=lambda ids: {SCF_ID: list(OBJECTIVES)},
        kb_revision="test-rev",
    )
    defaults.update(kwargs)
    return cdm_mapping.compute_mappings_v2(session, ORG_ID, **defaults)


# --- the offset rule ------------------------------------------------------


def test_backend_without_offsets_is_refused_before_any_work():
    """The refusal is structural, not a documented convention.

    A backend that cannot prove where a passage lives must not be able to
    write an audit citation, no matter how confident its relevance score is.
    """
    session = _FakeSession()

    with pytest.raises(ValueError, match="cannot return verifiable offsets"):
        _run(session, _OffsetlessBackend())

    assert session.added == []


def test_retrieved_chunk_rejects_null_or_inverted_offsets():
    """Construction-time validation, so a bad row cannot reach the writer."""
    with pytest.raises((ValueError, TypeError)):
        _chunk(char_start=None)
    with pytest.raises(ValueError):
        _chunk(char_start=100, char_end=100)
    with pytest.raises(ValueError):
        _chunk(char_start=-1, char_end=10)


def test_unresolvable_offset_produces_no_row():
    """Extractor drift must cost a mapping, never move one silently.

    The loader returns text that no longer contains the chunk body — exactly
    what a PyMuPDF upgrade or a re-extraction can produce. The correct output
    is nothing, counted, not a citation pointing at whatever now sits at
    those coordinates.
    """
    session = _FakeSession()
    backend = _StubBackend()

    summary = _run(
        session,
        backend,
        extracted_text_loader=lambda doc: "Completely different document text.",
    )

    assert session.added == []
    assert summary.mappings_created == 0
    assert summary.mappings_skipped_unresolved_offset == 1


def test_missing_extracted_text_produces_no_row():
    session = _FakeSession()
    summary = _run(session, _StubBackend(), extracted_text_loader=lambda doc: None)

    assert session.added == []
    assert summary.mappings_skipped_unresolved_offset == 1


def test_cross_tenant_document_is_refused():
    """Tenant isolation is re-checked at write time, not assumed from the query."""
    session = _FakeSession()
    session.documents[DOC_ID] = SimpleNamespace(id=DOC_ID, organization_id=uuid4())

    summary = _run(session, _StubBackend())

    assert session.added == []
    assert summary.mappings_skipped_unresolved_offset == 1


# --- score interrogability ------------------------------------------------


def test_score_is_recomputable_from_persisted_components():
    session = _FakeSession()
    _run(session, _StubBackend())

    assert len(session.added) == 1
    row = session.added[0]
    weights = row.score_weights

    recomputed = (
        weights["ts_rank"] * row.ts_rank_component
        + weights["objective_coverage"] * row.objective_coverage_component
        + weights["term_overlap"] * row.term_overlap_component
    )
    assert recomputed == pytest.approx(row.relevance_score, abs=1e-9)
    # Sum the three weight keys by name: since #712 the persisted JSON also
    # records the active cap/cutoff for provenance, alongside the weights.
    weight_sum = sum(
        weights[k] for k in ("ts_rank", "objective_coverage", "term_overlap")
    )
    assert weight_sum == pytest.approx(1.0, abs=1e-9)


def test_score_is_not_derived_from_result_position():
    """v1's defining defect: rank 0 scored 1.0, rank 1 scored 0.95, forever.

    Three chunks with distinct ts_ranks must produce three scores that track
    the evidence, not the ordering.
    """
    chunks = [
        _chunk(ordinal=0, ts_rank=0.40),
        _chunk(ordinal=1, ts_rank=0.25),
        _chunk(ordinal=2, ts_rank=0.10),
    ]
    session = _FakeSession()
    _run(session, _StubBackend(chunks=chunks))

    scores = [row.relevance_score for row in session.added]
    assert len(scores) == 3
    assert len(set(scores)) == 3
    assert scores == sorted(scores, reverse=True)
    assert not any(s == pytest.approx(1.0) for s in scores)
    assert not any(
        s == pytest.approx(1.0 - 0.05 * i) for i, s in enumerate(scores)
    )


def test_default_threshold_is_reachable():
    """v1's 0.7 default was unreachable past rank 6, so the knob was a lie.

    A strong hit must clear the shipped default without the operator having
    to discover and lower it.
    """
    session = _FakeSession()
    _run(session, _StubBackend(chunks=[_chunk(ts_rank=0.9)]))

    assert session.added, "a strong hit must clear the shipped default threshold"
    assert session.added[0].relevance_score >= cdm_scoring.get_score_threshold()


def test_below_threshold_hits_are_counted_not_written():
    session = _FakeSession()
    summary = _run(
        session,
        _StubBackend(chunks=[_chunk(ts_rank=0.0, matched_objectives=())]),
        score_threshold=0.99,
    )

    assert session.added == []
    assert summary.mappings_skipped_below_threshold == 1
    assert summary.hits_evaluated == 1


# --- provenance fields ----------------------------------------------------


def test_mapping_records_its_full_provenance():
    session = _FakeSession()
    backend = _StubBackend()
    chunk = backend._chunks[0]

    _run(session, backend)

    row = session.added[0]
    assert row.retrieval_tier == "stub_fts"
    assert row.cdm_document_chunk_id == chunk.chunk_id
    assert row.match_type == "exact"
    assert row.matched_objective_text == OBJECTIVES[0]
    assert row.section == "4.2 Supplier Onboarding"
    assert row.kb_revision == "test-rev"
    assert row.status == "proposed"
    # The excerpt must be the text at the recorded offsets, not a paraphrase.
    assert DOCUMENT_TEXT[row.byte_offset_start : row.byte_offset_end] == row.excerpt


def test_citation_narrows_to_the_answering_sentence():
    """A 1800-char chunk is not a citation; a reviewer should not have to hunt."""
    session = _FakeSession()
    _run(session, _StubBackend())

    row = session.added[0]
    assert len(row.excerpt) < len(CHUNK_BODY)
    assert "security risk assessment" in row.excerpt
    # Still inside the chunk it came from.
    assert row.byte_offset_start >= CHUNK_START
    assert row.byte_offset_end <= CHUNK_START + len(CHUNK_BODY)


def test_heading_absent_falls_back_to_derived_section():
    session = _FakeSession()
    _run(session, _StubBackend(chunks=[_chunk(heading=None)]))

    row = session.added[0]
    assert row.section != "4.2 Supplier Onboarding"


# --- objectives and queries ----------------------------------------------


def test_query_is_built_from_assessment_objectives():
    session = _FakeSession()
    backend = _StubBackend()
    _run(session, backend)

    assert backend.calls[0].query_texts() == list(OBJECTIVES)


def test_control_without_objectives_falls_back_to_name_and_question():
    session = _FakeSession()
    backend = _StubBackend()
    _run(session, backend, objectives_loader=lambda ids: {})

    texts = backend.calls[0].query_texts()
    assert texts, "a control with no objectives must still be searchable"
    assert any("Third-Party Risk" in t for t in texts)


def test_objective_coverage_is_zero_when_control_has_no_objectives():
    """Not 1.0. Zero objectives is missing information, not perfect coverage."""
    assert cdm_retrieval.compute_objective_coverage((), ()) == 0.0


def test_control_with_no_query_text_is_skipped_without_error():
    session = _FakeSession(
        control_rows=[(CONTROL_ID, None, None, None)]
    )
    backend = _StubBackend()

    summary = _run(session, backend, objectives_loader=lambda ids: {})

    assert backend.calls == []
    assert session.added == []
    assert summary.controls_processed == 1


# --- idempotency and resilience ------------------------------------------


def test_existing_mapping_is_updated_not_duplicated():
    session = _FakeSession(existing_mapping_id=uuid4())

    summary = _run(session, _StubBackend())

    assert session.added == []
    assert summary.mappings_skipped_duplicate == 1
    assert len(session.updates) == 1


def test_backend_failure_for_one_control_does_not_abort_the_batch():
    """One bad control must not deny every other control its mappings."""
    other_control = UUID("00000000-0000-0000-0000-0000000000c2")
    session = _FakeSession(
        control_rows=[
            (CONTROL_ID, SCF_ID, "Third-Party Risk", "Does the org assess suppliers?"),
            (other_control, SCF_ID, "Third-Party Risk", "Does the org assess suppliers?"),
        ]
    )

    class _FlakyBackend(_StubBackend):
        def search(self, session_, org_id, query, *, limit):
            self.calls.append(query)
            if len(self.calls) == 1:
                raise RuntimeError("transient index error")
            return [_chunk()], 1

    summary = _run(session, _FlakyBackend())

    assert summary.controls_processed == 2
    assert len(session.added) == 1


def test_summary_counts_reconcile_with_rows_written():
    chunks = [
        _chunk(ordinal=0, ts_rank=0.40),
        _chunk(ordinal=1, ts_rank=0.25),
    ]
    session = _FakeSession()
    summary = _run(session, _StubBackend(chunks=chunks))

    assert summary.hits_evaluated == 2
    assert summary.mappings_created == len(session.added) == 2
    assert summary.controls_processed == 1
