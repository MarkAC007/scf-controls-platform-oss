"""Contract tests for the document-map aggregate endpoint.

The map's whole value is that it distinguishes "a model thinks this document is
about a domain" from "a human accepted a mapping into it". These pin that:

* the four-value domain state is derived server-side and covers all four cases;
* ``coverage_summary.covered`` counts confirmed domains only — claimed is its
  own number and is never folded into a coverage figure nobody earned;
* the full catalogue skeleton is always returned, so an empty domain renders as
  empty rather than as absent;
* no response model declares provider, model, prompt or rationale bookkeeping.

The endpoint is invoked directly rather than through the ASGI stack: the auth
dependency is a factory that returns a fresh function per call, so overriding it
by identity would test the override rather than the endpoint.
"""
from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace
from uuid import UUID

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import cdm_document_map  # noqa: E402

ORG_ID = UUID("00000000-0000-0000-0000-0000000000a1")
DOC_A = UUID("00000000-0000-0000-0000-0000000000d1")
DOC_B = UUID("00000000-0000-0000-0000-0000000000d2")
DOC_C = UUID("00000000-0000-0000-0000-0000000000d3")

COVERED_DOMAIN = "ONEDOMAIN"
CLAIMED_DOMAIN = "TWODOMAIN"
GAP_DOMAIN = "THREEDOMAIN"
OOS_DOMAIN = "FOURDOMAIN"

CATALOG = [
    SimpleNamespace(identifier=COVERED_DOMAIN, name="One Domain", order=1),
    SimpleNamespace(identifier=CLAIMED_DOMAIN, name="Two Domain", order=2),
    SimpleNamespace(identifier=GAP_DOMAIN, name="Three Domain", order=3),
    SimpleNamespace(identifier=OOS_DOMAIN, name="Four Domain", order=4),
]


class _FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def __iter__(self):
        return iter(self._rows)

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._rows))


class _FakeAsyncSession:
    """Answers each of the endpoint's grouped queries by shape.

    Counting the calls is deliberate: an N+1 regression would show up here as a
    call count that scales with the catalogue.
    """

    def __init__(self, *, control_counts, documents, intents, mappings, control_hits):
        self.control_counts = control_counts
        self.documents = documents
        self.intents = intents
        self.mappings = mappings
        self.control_hits = control_hits
        self.calls = 0

    async def execute(self, statement, params=None):
        self.calls += 1
        compiled = str(statement)
        if "scf_catalog_domains" in compiled:
            return _FakeResult(CATALOG)
        if "FROM scoped_controls" in compiled:
            return _FakeResult(self.control_counts)
        if "FROM cdm_documents" in compiled:
            return _FakeResult(self.documents)
        if "FROM cdm_document_intents" in compiled:
            return _FakeResult(self.intents)
        if "count(DISTINCT m.scoped_control_id)" in compiled:
            return _FakeResult(self.control_hits)
        if "FROM cdm_mappings" in compiled:
            return _FakeResult(self.mappings)
        raise AssertionError(f"unexpected query: {compiled}")


def _counts(domain, total, selected):
    return SimpleNamespace(domain=domain, total=total, selected=selected)


def _document(document_id, filename, ingest_status="indexed", intent_status="classified"):
    return SimpleNamespace(
        id=document_id,
        original_filename=filename,
        ingest_status=ingest_status,
        intent_status=intent_status,
    )


def _intent(document_id, domain, rank):
    return SimpleNamespace(cdm_document_id=document_id, domain=domain, rank=rank)


def _mapping(domain, document_id, status, count):
    return SimpleNamespace(
        domain=domain, cdm_document_id=document_id, status=status, mapping_count=count
    )


def _control_hit(domain, status, count):
    return SimpleNamespace(domain=domain, status=status, control_count=count)


def _default_session():
    return _FakeAsyncSession(
        control_counts=[
            _counts(COVERED_DOMAIN, 10, 8),
            _counts(CLAIMED_DOMAIN, 6, 6),
            _counts(GAP_DOMAIN, 4, 4),
            _counts(OOS_DOMAIN, 5, 0),
        ],
        documents=[
            _document(DOC_A, "information-security-policy.pdf"),
            _document(DOC_B, "board-charter.docx"),
            _document(DOC_C, "office-floor-plan.pdf", intent_status="unclassified"),
        ],
        intents=[
            _intent(DOC_A, COVERED_DOMAIN, 1),
            _intent(DOC_B, CLAIMED_DOMAIN, 1),
        ],
        mappings=[
            _mapping(COVERED_DOMAIN, DOC_A, "accepted", 7),
            _mapping(COVERED_DOMAIN, DOC_A, "proposed", 12),
            _mapping(CLAIMED_DOMAIN, DOC_B, "proposed", 4),
            _mapping("IGNOREDSTATUS", DOC_C, "superseded", 1),
            _mapping(GAP_DOMAIN, DOC_C, "dismissed", 3),
        ],
        control_hits=[
            _control_hit(COVERED_DOMAIN, "accepted", 7),
            _control_hit(COVERED_DOMAIN, "proposed", 19),
            _control_hit(CLAIMED_DOMAIN, "proposed", 4),
        ],
    )


def _run(session):
    return asyncio.run(
        cdm_document_map.get_document_map(ORG_ID, membership=None, db=session)
    )


# --- the state enum -------------------------------------------------------


def test_state_is_covered_only_when_a_human_accepted_something():
    assert cdm_document_map.derive_domain_state(
        selected_controls=8, accepted_mappings=1, proposed_mappings=0, model_intents=0
    ) == "covered"


def test_state_is_claimed_when_only_a_model_or_a_proposal_says_so():
    """The state where over-classification hides, so it gets its own name."""
    assert cdm_document_map.derive_domain_state(
        selected_controls=6, accepted_mappings=0, proposed_mappings=0, model_intents=1
    ) == "claimed"
    assert cdm_document_map.derive_domain_state(
        selected_controls=6, accepted_mappings=0, proposed_mappings=4, model_intents=0
    ) == "claimed"


def test_state_is_gap_when_controls_are_scoped_and_nothing_points_at_them():
    assert cdm_document_map.derive_domain_state(
        selected_controls=4, accepted_mappings=0, proposed_mappings=0, model_intents=0
    ) == "gap"


def test_state_is_out_of_scope_when_no_control_is_selected():
    """A scoping decision outranks a leftover edge into deselected controls."""
    assert cdm_document_map.derive_domain_state(
        selected_controls=0, accepted_mappings=3, proposed_mappings=9, model_intents=1
    ) == "out_of_scope"


# --- the aggregate --------------------------------------------------------


def test_all_four_states_appear_in_one_response():
    response = _run(_default_session())

    states = {entry.domain: entry.state for entry in response.domains}
    assert states == {
        COVERED_DOMAIN: "covered",
        CLAIMED_DOMAIN: "claimed",
        GAP_DOMAIN: "gap",
        OOS_DOMAIN: "out_of_scope",
    }


def test_coverage_summary_counts_confirmed_domains_only():
    """Claimed is reported alongside covered, never added into it."""
    response = _run(_default_session())

    assert response.coverage_summary.covered == 1
    assert response.coverage_summary.claimed == 1
    assert response.coverage_summary.gap == 1
    assert response.coverage_summary.total_domains == len(CATALOG)


def test_full_catalog_skeleton_is_returned_even_with_no_data():
    """An empty domain must render as empty, never as missing."""
    session = _FakeAsyncSession(
        control_counts=[], documents=[], intents=[], mappings=[], control_hits=[]
    )

    response = _run(session)

    assert [entry.domain for entry in response.domains] == [d.identifier for d in CATALOG]
    assert all(entry.documents == [] for entry in response.domains)
    assert response.coverage_summary.total_domains == len(CATALOG)


def test_intent_source_is_confirmed_only_with_an_accepted_mapping():
    response = _run(_default_session())

    by_domain = {entry.domain: entry for entry in response.domains}
    confirmed_doc = by_domain[COVERED_DOMAIN].documents[0]
    assert confirmed_doc.intent_source == "confirmed"
    assert confirmed_doc.claimed_by_model is True

    claimed_doc = by_domain[CLAIMED_DOMAIN].documents[0]
    assert claimed_doc.intent_source == "model"
    assert claimed_doc.claimed_by_model is True


def test_dismissed_only_document_is_orphaned():
    """A dismissal says the document does not belong there — that is not an edge."""
    response = _run(_default_session())

    orphans = {orphan.cdm_document_id for orphan in response.orphan_documents}
    assert orphans == {DOC_C}
    assert response.coverage_summary.documents_orphaned == 1
    assert response.coverage_summary.documents_total == 3


def _pending_corpus_session():
    return _FakeAsyncSession(
        control_counts=[_counts(COVERED_DOMAIN, 2, 2)],
        documents=[
            _document(DOC_A, "a.pdf", intent_status="pending"),
            _document(DOC_B, "b.pdf", intent_status="failed"),
        ],
        intents=[],
        mappings=[],
        control_hits=[],
    )


def test_documents_awaiting_classification_counts_pending_only(monkeypatch):
    monkeypatch.setattr(
        cdm_document_map.cdm_intent, "intent_classification_enabled", lambda: True
    )

    response = _run(_pending_corpus_session())

    assert response.coverage_summary.documents_awaiting_classification == 1


def test_nothing_is_awaiting_classification_when_the_stage_is_switched_off(monkeypatch):
    """Absence renders as absent, never as in-flight.

    ``CDM_INTENT_PROVIDER`` defaults to disabled, so nothing is ever queued and
    every document sits at 'pending' forever. Reporting those as awaiting
    classification would tell every existing org its whole corpus was mid-flight
    in a stage that does not run.
    """
    monkeypatch.setattr(
        cdm_document_map.cdm_intent, "intent_classification_enabled", lambda: False
    )

    response = _run(_pending_corpus_session())

    assert response.coverage_summary.documents_awaiting_classification == 0
    # The documents are still reported, carrying their neutral intent state.
    assert response.coverage_summary.documents_total == 2
    assert {orphan.intent_state for orphan in response.orphan_documents} == {"pending", "failed"}


def test_awaiting_classification_is_zero_by_default(monkeypatch):
    """The shipped default must not manufacture an in-flight backlog."""
    monkeypatch.delenv("CDM_INTENT_PROVIDER", raising=False)

    response = _run(_pending_corpus_session())

    assert response.coverage_summary.documents_awaiting_classification == 0


def test_intent_for_a_deleted_document_does_not_derive_claimed():
    """A mid-request CASCADE must not leave a claimed domain with no documents.

    Deriving 'claimed' from an intent row whose document is already gone would
    render a suggested-coverage block with zero documents under it.
    """
    session = _FakeAsyncSession(
        control_counts=[_counts(CLAIMED_DOMAIN, 6, 6)],
        documents=[],  # DOC_B was deleted between queries
        intents=[_intent(DOC_B, CLAIMED_DOMAIN, 1)],
        mappings=[],
        control_hits=[],
    )

    response = _run(session)

    claimed = next(e for e in response.domains if e.domain == CLAIMED_DOMAIN)
    assert claimed.documents == []
    assert claimed.state == "gap"
    assert response.coverage_summary.claimed == 0


def test_query_count_does_not_scale_with_the_catalogue():
    """Grouped queries stitched in Python — never one per domain or document."""
    session = _default_session()

    _run(session)

    assert session.calls <= 6


def test_control_totals_come_from_the_distinct_control_query():
    response = _run(_default_session())

    covered = next(e for e in response.domains if e.domain == COVERED_DOMAIN)
    assert covered.totals.controls_with_accepted_mapping == 7
    assert covered.totals.controls_with_proposed_mapping == 19
    assert covered.totals.confirmed_documents == 1
    assert covered.scoped_control_counts.selected == 8
    assert covered.display_order == 1


# --- schema-level leak prevention -----------------------------------------


@pytest.mark.parametrize(
    "model",
    [
        cdm_document_map.DomainDocument,
        cdm_document_map.OrphanDocument,
        cdm_document_map.DomainEntry,
        cdm_document_map.DomainTotals,
        cdm_document_map.CoverageSummary,
        cdm_document_map.DocumentMapResponse,
    ],
)
def test_no_response_model_declares_classifier_bookkeeping(model):
    """Enforced at the schema boundary, not by remembering to omit it.

    Operators reach provider, model and prompt version through SQL and logs.
    Declaring them here would put them one ``.model_dump()`` away from the
    webclient forever.
    """
    forbidden = {"provider", "model_id", "prompt_version", "classification_id", "rationale"}

    assert forbidden.isdisjoint(model.model_fields)


def test_serialised_payload_contains_no_classifier_bookkeeping():
    response = _run(_default_session())

    payload = response.model_dump_json()

    for field in ("provider", "model_id", "prompt_version", "classification_id", "rationale"):
        assert field not in payload
