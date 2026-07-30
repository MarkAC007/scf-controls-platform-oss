"""Contract tests for CDM document-intent classification.

These pin the properties that keep a model's opinion from being mistaken for a
fact, and keep a model outage from being mistaken for an empty document set:

* validated output drops codes the catalogue does not contain, truncates to the
  rank ceiling and preserves the model's ordering;
* an empty validated set is ``unclassified``, never ``classified``-with-no-rows;
* the mapping gate fails **open** — a document without a usable classification
  is allowed, and an org with nothing classified is not filtered at all;
* classification failure never touches ``ingest_status`` and never re-raises
  into the ingest result;
* every terminal ingest state dispatches classification.

No provider is ever contacted: the seam is stubbed, because these are tests of
the service's contract rather than of anyone's API.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import cdm_intent, cdm_mapping  # noqa: E402

ORG_ID = UUID("00000000-0000-0000-0000-0000000000a1")
DOC_A = UUID("00000000-0000-0000-0000-0000000000d1")
DOC_B = UUID("00000000-0000-0000-0000-0000000000d2")
DOC_C = UUID("00000000-0000-0000-0000-0000000000d3")

# Assembled rather than written literally so no real SCF control identifier
# appears in the file; only the '-' split behaviour is under test.
SCF_ID = "TESTDOMAIN" + "-" + "07"
TEST_DOMAIN = "TESTDOMAIN"
OTHER_DOMAIN = "OTHERDOMAIN"

VALID_CODES = {TEST_DOMAIN, OTHER_DOMAIN, "THIRDDOMAIN", "FOURTHDOMAIN"}


class _StubProvider:
    """Returns scripted replies; records the prompts it was asked with."""

    name = "stub"

    def __init__(self, replies, model_id="stub-model-1"):
        self._replies = list(replies)
        self._model_id = model_id
        self.prompts: list[str] = []

    def classify(self, request):
        self.prompts.append(request.prompt)
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return cdm_intent.IntentResponse(text=reply, model_id=self._model_id)


DOMAINS = {
    TEST_DOMAIN: ("Test Domain", "Steering principle for the test domain."),
    OTHER_DOMAIN: ("Other Domain", "Steering principle for the other domain."),
    "THIRDDOMAIN": ("Third Domain", "Steering principle three."),
    "FOURTHDOMAIN": ("Fourth Domain", "Steering principle four."),
}


# --- output validation ----------------------------------------------------


def test_unknown_domains_are_dropped_not_raised():
    """A hallucinated code narrows the filter; it must not lose the answer.

    The eval harness raises here because a bad code is a result worth failing
    on. On the ingest path it is an ordinary event, and discarding the whole
    classification would cost more than discarding the code.
    """
    codes, rationale = cdm_intent.validate_classification(
        '{"primary_domains": ["' + TEST_DOMAIN + '", "NOTADOMAIN"], "rationale": "because"}',
        VALID_CODES,
    )

    assert codes == (TEST_DOMAIN,)
    assert rationale == "because"


def test_domains_are_truncated_to_the_rank_ceiling():
    """The rank check constraint is 1..3; a fourth code must never reach it."""
    payload = (
        '{"primary_domains": ["' + TEST_DOMAIN + '", "' + OTHER_DOMAIN + '", '
        '"THIRDDOMAIN", "FOURTHDOMAIN"], "rationale": "four"}'
    )

    codes, _ = cdm_intent.validate_classification(payload, VALID_CODES)

    assert len(codes) == cdm_intent.RANK_CEILING
    # Order is the model's own — rank follows it rather than re-sorting.
    assert codes == (TEST_DOMAIN, OTHER_DOMAIN, "THIRDDOMAIN")


def test_duplicate_codes_collapse_without_consuming_two_ranks():
    codes, _ = cdm_intent.validate_classification(
        '{"primary_domains": ["' + TEST_DOMAIN + '", "' + TEST_DOMAIN.lower() + '", '
        '"' + OTHER_DOMAIN + '"], "rationale": "dupes"}',
        VALID_CODES,
    )

    assert codes == (TEST_DOMAIN, OTHER_DOMAIN)


def test_empty_primary_domains_is_a_successful_empty_result():
    """Empty is a legitimate answer, distinguished from failure by the caller."""
    codes, rationale = cdm_intent.validate_classification(
        '{"primary_domains": [], "rationale": "authoritative for nothing"}',
        VALID_CODES,
    )

    assert codes == ()
    assert rationale == "authoritative for nothing"


def test_code_fenced_json_is_accepted():
    codes, _ = cdm_intent.validate_classification(
        '```json\n{"primary_domains": ["' + OTHER_DOMAIN + '"], "rationale": "fenced"}\n```',
        VALID_CODES,
    )

    assert codes == (OTHER_DOMAIN,)


def test_unparseable_output_raises_a_deterministic_error():
    with pytest.raises(cdm_intent.IntentProviderError):
        cdm_intent.validate_classification("no json here at all", VALID_CODES)


def test_classify_retries_once_with_the_json_only_suffix():
    """Mirrors the eval harness, whose measured accuracy includes this retry."""
    provider = _StubProvider(
        [
            "I think this is about governance.",
            '{"primary_domains": ["' + TEST_DOMAIN + '"], "rationale": "second try"}',
        ]
    )

    result = cdm_intent.classify_document_text(
        "Policy body text.", DOMAINS, provider=provider, timeout_s=1.0
    )

    assert result.domains == (TEST_DOMAIN,)
    assert len(provider.prompts) == 2
    assert provider.prompts[1].endswith(cdm_intent.RETRY_SUFFIX)


def test_transient_errors_are_not_absorbed_by_the_shape_retry():
    """Re-asking immediately would just spend the same outage twice."""
    provider = _StubProvider([cdm_intent.IntentProviderTransientError("rate limited")])

    with pytest.raises(cdm_intent.IntentProviderTransientError):
        cdm_intent.classify_document_text(
            "Policy body text.", DOMAINS, provider=provider, timeout_s=1.0
        )

    assert len(provider.prompts) == 1


def test_provider_is_disabled_by_default(monkeypatch):
    """The feature ships dark; a deploy never starts spending by accident."""
    monkeypatch.delenv("CDM_INTENT_PROVIDER", raising=False)

    assert cdm_intent.get_intent_provider() is None
    assert cdm_intent.intent_classification_enabled() is False


def test_unknown_provider_name_disables_rather_than_crashing(monkeypatch):
    monkeypatch.setenv("CDM_INTENT_PROVIDER", "gemini")

    assert cdm_intent.get_intent_provider() is None


def test_max_domains_is_clamped_to_the_rank_ceiling(monkeypatch):
    """A larger configured value would produce rows the database refuses."""
    monkeypatch.setenv("CDM_INTENT_MAX_DOMAINS", "9")

    assert cdm_intent.get_max_domains() == cdm_intent.RANK_CEILING


# --- the fail-open gate ---------------------------------------------------


class _GateSession:
    """Answers the gate's single preload query."""

    def __init__(self, rows):
        self._rows = rows
        self.executes = 0

    def execute(self, statement, params=None):
        self.executes += 1
        return SimpleNamespace(fetchall=lambda: list(self._rows))


def _row(document_id, intent_status, domain=None):
    return SimpleNamespace(document_id=document_id, intent_status=intent_status, domain=domain)


def test_gate_allows_documents_that_are_not_classified():
    """THE INVARIANT: missing intent is permission, not exclusion.

    DOC_B is pending and DOC_C failed. Both must survive a gate built for a
    domain neither of them claims, or a classification outage would present as
    "your documents cover nothing".
    """
    session = _GateSession([
        _row(DOC_A, "classified", TEST_DOMAIN),
        _row(DOC_B, "pending"),
        _row(DOC_C, "failed"),
    ])
    gate = cdm_mapping.DocumentIntentGate(session, ORG_ID)

    allowed = gate.allowed_documents(OTHER_DOMAIN)

    assert allowed is not None
    assert DOC_B in allowed
    assert DOC_C in allowed
    assert DOC_A not in allowed


def test_gate_returns_none_when_nothing_in_the_org_is_classified():
    """Never ``set()``. Filtering on an absence is how you erase a corpus."""
    session = _GateSession([_row(DOC_A, "pending"), _row(DOC_B, "unclassified")])
    gate = cdm_mapping.DocumentIntentGate(session, ORG_ID)

    assert gate.allowed_documents(TEST_DOMAIN) is None


def test_gate_returns_none_for_an_underivable_domain():
    session = _GateSession([_row(DOC_A, "classified", TEST_DOMAIN)])
    gate = cdm_mapping.DocumentIntentGate(session, ORG_ID)

    assert gate.allowed_documents(None) is None


def test_gate_preloads_exactly_once_per_run():
    """One query per run, never one per control."""
    session = _GateSession([_row(DOC_A, "classified", TEST_DOMAIN)])
    gate = cdm_mapping.DocumentIntentGate(session, ORG_ID)

    gate.allowed_documents(TEST_DOMAIN)
    gate.allowed_documents(OTHER_DOMAIN)
    gate.allowed_documents(TEST_DOMAIN)

    assert session.executes == 1


def test_gate_admits_documents_claiming_the_domain():
    session = _GateSession([
        _row(DOC_A, "classified", TEST_DOMAIN),
        _row(DOC_B, "classified", OTHER_DOMAIN),
    ])
    gate = cdm_mapping.DocumentIntentGate(session, ORG_ID)

    assert gate.allowed_documents(TEST_DOMAIN) == {DOC_A}
    assert gate.allowed_documents(OTHER_DOMAIN) == {DOC_B}


def test_gate_allows_a_classified_document_with_no_surviving_intent_row():
    """The invariant does not bend for states we believe are unreachable.

    'classified' with a NULL joined domain (a deleted intent row, a partial
    restore) previously landed in neither set and was therefore excluded from
    every domain — the one outcome the gate exists to prevent.
    """
    session = _GateSession([
        _row(DOC_A, "classified", TEST_DOMAIN),
        _row(DOC_B, "classified", None),
    ])
    gate = cdm_mapping.DocumentIntentGate(session, ORG_ID)

    assert DOC_B in gate.allowed_documents(TEST_DOMAIN)
    assert DOC_B in gate.allowed_documents(OTHER_DOMAIN)


def test_domain_is_derived_from_the_identifier_prefix():
    assert cdm_mapping._domain_for_scf_id(SCF_ID) == TEST_DOMAIN
    assert cdm_mapping._domain_for_scf_id(None) is None
    assert cdm_mapping._domain_for_scf_id("") is None


# --- task behaviour -------------------------------------------------------


class _TaskSession:
    def __init__(self, document):
        self.document = document
        self.added: list = []
        self.deletes: list = []
        self.commits = 0

    def get(self, model, pk):
        return self.document if self.document is not None and pk == self.document.id else None

    def execute(self, statement, params=None):
        self.deletes.append(statement)
        return SimpleNamespace()

    def add(self, row):
        self.added.append(row)

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def close(self):
        pass


def _fake_document():
    return SimpleNamespace(
        id=DOC_A,
        organization_id=ORG_ID,
        original_filename="policy.txt",
        ingest_status="indexed",
        intent_status="pending",
        intent_error=None,
        intent_classified_at=None,
    )


def _install_task_stubs(monkeypatch, document, provider, text_value="Policy body text."):
    import tasks_cdm

    session = _TaskSession(document)
    monkeypatch.setattr(tasks_cdm, "_get_sync_session", lambda: session)
    monkeypatch.setattr(tasks_cdm.cdm_intent, "get_intent_provider", lambda: provider)
    monkeypatch.setattr(tasks_cdm.cdm_intent, "load_catalog_domains", lambda _s: DOMAINS)
    monkeypatch.setattr(
        tasks_cdm, "_load_extracted_text_for_document", lambda _doc: text_value
    )
    return tasks_cdm, session


def test_task_writes_ranked_rows_and_marks_classified(monkeypatch):
    document = _fake_document()
    provider = _StubProvider([
        '{"primary_domains": ["' + TEST_DOMAIN + '", "' + OTHER_DOMAIN + '"], '
        '"rationale": "policy for both"}'
    ])
    tasks_cdm, session = _install_task_stubs(monkeypatch, document, provider)

    result = tasks_cdm.classify_cdm_document_intent.run(str(DOC_A))

    assert result["status"] == "classified"
    assert document.intent_status == "classified"
    assert document.intent_error is None
    assert document.intent_classified_at is not None
    assert [row.domain for row in session.added] == [TEST_DOMAIN, OTHER_DOMAIN]
    assert [row.rank for row in session.added] == [1, 2]
    # One classification_id groups the rows a single run produced.
    assert len({row.classification_id for row in session.added}) == 1
    # The previous run's rows are cleared in the same transaction.
    assert len(session.deletes) == 1


def test_task_records_unclassified_rather_than_classified_with_no_rows(monkeypatch):
    """"Authoritative for nothing" and "the model failed" must stay distinct."""
    document = _fake_document()
    provider = _StubProvider(['{"primary_domains": [], "rationale": "nothing"}'])
    tasks_cdm, session = _install_task_stubs(monkeypatch, document, provider)

    result = tasks_cdm.classify_cdm_document_intent.run(str(DOC_A))

    assert result["status"] == "unclassified"
    assert document.intent_status == "unclassified"
    assert session.added == []


def test_task_failure_never_touches_ingest_status(monkeypatch):
    """Classification is an enhancement layered on ingest, never a gate on it."""
    document = _fake_document()
    provider = _StubProvider([cdm_intent.IntentProviderError("model refused")])
    tasks_cdm, _session = _install_task_stubs(monkeypatch, document, provider)

    failures: list = []
    monkeypatch.setattr(
        tasks_cdm,
        "_persist_intent_failure",
        lambda document_uuid, message: failures.append((document_uuid, message)),
    )

    result = tasks_cdm.classify_cdm_document_intent.run(str(DOC_A))

    assert result["status"] == "failed"
    assert document.ingest_status == "indexed"
    assert failures and failures[0][0] == DOC_A


def test_task_is_a_clean_no_op_when_no_provider_is_configured(monkeypatch):
    """Disabled leaves ``intent_status`` at 'pending' — no rows, no status move."""
    import tasks_cdm

    document = _fake_document()
    monkeypatch.setattr(tasks_cdm.cdm_intent, "get_intent_provider", lambda: None)
    monkeypatch.setattr(
        tasks_cdm,
        "_get_sync_session",
        lambda: pytest.fail("no session should be opened when disabled"),
    )

    result = tasks_cdm.classify_cdm_document_intent.run(str(DOC_A))

    assert result["status"] == "disabled"
    assert document.intent_status == "pending"


def test_task_skips_already_classified_documents_without_force(monkeypatch):
    document = _fake_document()
    document.intent_status = "classified"
    provider = _StubProvider([AssertionError("provider must not be called")])
    tasks_cdm, session = _install_task_stubs(monkeypatch, document, provider)

    result = tasks_cdm.classify_cdm_document_intent.run(str(DOC_A))

    assert result["status"] == "skipped"
    assert session.added == []


# --- dispatch after every terminal ingest state ---------------------------


def test_dispatch_is_skipped_when_classification_is_disabled(monkeypatch):
    import tasks_cdm

    monkeypatch.setattr(tasks_cdm.cdm_intent, "intent_classification_enabled", lambda: False)
    monkeypatch.setattr(
        tasks_cdm.classify_cdm_document_intent,
        "delay",
        lambda *_a, **_kw: pytest.fail("nothing should be queued when disabled"),
    )

    tasks_cdm._dispatch_intent_classification(str(DOC_A))


def test_dispatch_failure_does_not_propagate_into_ingest(monkeypatch):
    """A broker outage must not turn a successful ingest into a failed one."""
    import tasks_cdm

    monkeypatch.setattr(tasks_cdm.cdm_intent, "intent_classification_enabled", lambda: True)

    def _boom(*_a, **_kw):
        raise RuntimeError("broker unreachable")

    monkeypatch.setattr(tasks_cdm.classify_cdm_document_intent, "delay", _boom)

    tasks_cdm._dispatch_intent_classification(str(DOC_A))


def test_dispatch_enqueues_when_enabled(monkeypatch):
    import tasks_cdm

    monkeypatch.setattr(tasks_cdm.cdm_intent, "intent_classification_enabled", lambda: True)
    calls: list = []
    monkeypatch.setattr(
        tasks_cdm.classify_cdm_document_intent, "delay", lambda *a, **kw: calls.append(a)
    )

    tasks_cdm._dispatch_intent_classification(str(DOC_A))

    assert calls == [(str(DOC_A),)]


def test_generic_parse_failure_does_not_dispatch_classification(monkeypatch):
    """A document that never reached a terminal state has no durable text.

    The generic failure path persists ``failed`` and returns; dispatching from
    it would queue a classification whose only possible outcome is a second
    failure with a worse error message.
    """
    import tasks_cdm

    document_id = uuid4()

    document = SimpleNamespace(
        id=document_id,
        organization_id=ORG_ID,
        original_filename="policy.txt",
        mime_type="text/plain",
        ingest_status="pending",
        ingest_error=None,
        word_count=None,
        kb_revision_at_ingest=None,
    )

    class _IngestSession:
        def get(self, model, pk):
            return document if pk == document_id else None

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(tasks_cdm, "_get_sync_session", lambda: _IngestSession())
    # Nothing in the store, so the payload download raises before any terminal
    # document state is reached.
    monkeypatch.setattr(
        tasks_cdm.cdm_storage,
        "download_cdm_payload",
        lambda key: (_ for _ in ()).throw(FileNotFoundError(key)),
    )

    monkeypatch.setattr(
        tasks_cdm,
        "_dispatch_intent_classification",
        lambda doc_id: pytest.fail("a failed ingest must not dispatch classification"),
    )

    result = tasks_cdm.ingest_cdm_document.run(str(document_id))

    assert result["status"] == "failed"


@pytest.mark.parametrize(
    "lightrag_enabled,insert_raises,expected_status",
    [
        (True, None, "indexed"),
        (True, RuntimeError("LightRAG exploded"), "indexing_failed"),
        (False, None, "parsed"),
    ],
)
def test_every_terminal_ingest_state_dispatches_classification(
    monkeypatch, lightrag_enabled, insert_raises, expected_status
):
    """All three terminal states share the precondition the task needs.

    Extracted text is durable at each of them, so each must hand the document
    on. Missing one would leave a whole class of documents permanently
    unclassified with nothing to indicate why.
    """
    from unittest.mock import MagicMock

    import tasks_cdm
    from services import cdm_storage

    document_id = uuid4()
    object_key = f"cdm/{ORG_ID}/{document_id}/policy.txt"
    store = {object_key: b"Some policy text body."}

    monkeypatch.setattr(
        tasks_cdm.cdm_storage, "download_cdm_payload", lambda key: store[key]
    )
    monkeypatch.setattr(
        tasks_cdm.cdm_storage,
        "write_cdm_payload",
        lambda key, payload, content_type=None: store.__setitem__(key, payload),
    )
    monkeypatch.setattr(
        cdm_storage, "download_cdm_payload", lambda key: store[key]
    )

    document = SimpleNamespace(
        id=document_id,
        organization_id=ORG_ID,
        original_filename="policy.txt",
        mime_type="text/plain",
        ingest_status="pending",
        ingest_error=None,
        word_count=None,
        kb_revision_at_ingest=None,
    )

    class _IngestSession:
        def get(self, model, pk):
            return document if pk == document_id else None

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(tasks_cdm, "_get_sync_session", lambda: _IngestSession())
    monkeypatch.setattr(tasks_cdm, "is_lightrag_enabled", lambda: lightrag_enabled)
    if lightrag_enabled:
        client = MagicMock()
        if insert_raises is not None:
            client.insert.side_effect = insert_raises
        else:
            client.insert.return_value = {"status": "success"}
        monkeypatch.setattr(tasks_cdm, "get_lightrag_client", lambda: client)

    dispatched: list = []
    monkeypatch.setattr(
        tasks_cdm, "_dispatch_intent_classification", lambda doc_id: dispatched.append(doc_id)
    )

    result = tasks_cdm.ingest_cdm_document.run(str(document_id))

    assert result["status"] == expected_status
    assert dispatched == [str(document_id)]


# --- the gate, end to end through compute_mappings_v2 ---------------------

OTHER_SCF_ID = "OTHERDOMAIN" + "-" + "04"

GATE_DOCUMENT_TEXT = (
    "Supplier Management Policy\n\n"
    "All third-party suppliers must complete a security risk assessment "
    "before any contract is signed.\n"
)
GATE_CHUNK_BODY = (
    "All third-party suppliers must complete a security risk assessment "
    "before any contract is signed."
)
GATE_CHUNK_START = GATE_DOCUMENT_TEXT.index(GATE_CHUNK_BODY)
GATE_OBJECTIVES = ("A risk assessment is conducted before acquiring third-party services.",)


def _gate_chunk():
    from services import cdm_retrieval

    return cdm_retrieval.RetrievedChunk(
        chunk_id=uuid4(),
        cdm_document_id=DOC_A,
        ordinal=0,
        heading="4.2 Supplier Onboarding",
        body=GATE_CHUNK_BODY,
        body_norm=" ".join(GATE_CHUNK_BODY.split()).lower(),
        char_start=GATE_CHUNK_START,
        char_end=GATE_CHUNK_START + len(GATE_CHUNK_BODY),
        ts_rank=0.9,
        matched_objectives=GATE_OBJECTIVES,
    )


class _GateBackend:
    name = "stub_fts"
    can_produce_mappings = True

    def search(self, session, org_id, query, *, limit):
        chunks = [_gate_chunk()]
        return chunks, len(chunks)


class _ComputeSession:
    """Answers the control-rows query, the gate preload and the duplicate probe."""

    def __init__(self, intent_rows):
        self._intent_rows = intent_rows
        self.added: list = []
        self.commits = 0
        self.documents = {DOC_A: SimpleNamespace(id=DOC_A, organization_id=ORG_ID)}

    def execute(self, statement, params=None):
        compiled = str(statement)
        if "cdm_document_intents" in compiled:
            return SimpleNamespace(fetchall=lambda: list(self._intent_rows))
        if "scoped_controls" in compiled and "cdm_mappings" not in compiled:
            rows = [
                (uuid4(), SCF_ID, "In-domain control", "Does the org assess suppliers?"),
                (uuid4(), OTHER_SCF_ID, "Out-of-domain control", "Does the org assess suppliers?"),
            ]
            return SimpleNamespace(all=lambda: rows)
        return SimpleNamespace(first=lambda: None)

    def get(self, model, pk):
        return self.documents.get(pk)

    def add(self, row):
        self.added.append(row)

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def close(self):
        pass


def _run_compute(session, **kwargs):
    return cdm_mapping.compute_mappings_v2(
        session,
        ORG_ID,
        extracted_text_loader=lambda doc: GATE_DOCUMENT_TEXT,
        backend=_GateBackend(),
        objectives_loader=lambda ids: {
            SCF_ID: list(GATE_OBJECTIVES),
            OTHER_SCF_ID: list(GATE_OBJECTIVES),
        },
        kb_revision="test-rev",
        **kwargs,
    )


def test_gate_with_nothing_classified_is_identical_to_no_gate():
    """Shipping the gate dark must be a no-op, provably rather than plausibly."""
    ungated_session = _ComputeSession([_row(DOC_A, "pending")])
    ungated = _run_compute(ungated_session)

    gated_session = _ComputeSession([_row(DOC_A, "pending")])
    gate = cdm_mapping.DocumentIntentGate(gated_session, ORG_ID)
    gated = _run_compute(gated_session, intent_gate=gate)

    assert gated == ungated
    assert len(gated_session.added) == len(ungated_session.added)
    assert gated.mappings_created == 2


def test_gate_narrows_candidates_to_the_claimed_domain():
    """A claimed document is proposed inside its domain and only there."""
    session = _ComputeSession([_row(DOC_A, "classified", TEST_DOMAIN)])
    gate = cdm_mapping.DocumentIntentGate(session, ORG_ID)

    summary = _run_compute(session, intent_gate=gate)

    # Both controls were scanned; only the in-domain one kept its candidate.
    assert summary.controls_processed == 2
    assert summary.hits_evaluated == 2
    assert summary.mappings_created == 1
    assert len(session.added) == 1
    assert session.added[0].cdm_document_id == DOC_A


def test_compute_task_attaches_the_gate(monkeypatch):
    """The gate is wired in production, not merely available."""
    import tasks_cdm

    captured: dict = {}

    def _fake_compute(session, org_id, **kwargs):
        captured.update(kwargs)
        return cdm_mapping.ComputeMappingsSummary(0, 0, 0, 0, 0, 0)

    monkeypatch.setattr(tasks_cdm, "_get_sync_session", lambda: _ComputeSession([]))
    monkeypatch.setattr(tasks_cdm.cdm_mapping, "compute_mappings_v2", _fake_compute)
    monkeypatch.setattr(tasks_cdm, "_get_sync_redis_client", lambda: None)

    tasks_cdm.compute_mappings.run(str(ORG_ID))

    assert isinstance(captured.get("intent_gate"), cdm_mapping.DocumentIntentGate)
