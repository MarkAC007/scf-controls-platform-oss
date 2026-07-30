from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
from uuid import UUID

import pytest

from scripts.cdm_eval import variants
from scripts.cdm_eval.ground_truth import (
    GROUND_TRUTH,
    OUTCOME_CORRECT,
    OUTCOME_CORRECT_ABSTAIN,
    OUTCOME_MISSED_ABSTAIN,
    OUTCOME_UNEXPECTED_ABSTAIN,
    OUTCOME_WRONG,
    OUTCOMES,
    abstain_domains,
    covered_domains,
    expected_substring,
    judge,
)
from scripts.cdm_eval.variants import (
    VARIANTS,
    BaselineGate,
    CachedIntentGate,
    ControlContext,
    DocumentContext,
    SmokeTitleGate,
    get_variant,
    rank_key,
)


def _control(domain: str) -> ControlContext:
    return ControlContext(
        scf_id=f"{domain}-01",
        domain=domain,
        control_name="Example control",
        control_question="Is the example control implemented?",
        objectives=("Example objective",),
    )


def _document(identifier: str, filename: str) -> DocumentContext:
    return DocumentContext(
        cdm_document_id=UUID(identifier),
        filename=filename,
        word_count=100,
        headings=("Overview",),
        first_chunk_body="Example policy content.",
    )


def _write_intent_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    provider: str = "claude",
    documents: dict[str, dict[str, object]] | None = None,
    sha256s: tuple[str, ...] = ("aaaaaaaa", "bbbbbbbb"),
    cache_fingerprint: str | None = None,
) -> Path:
    fingerprint = hashlib.sha256("\n".join(sorted(sha256s)).encode("utf-8")).hexdigest()
    intent_documents = documents or {
        "Identity and Access Policy.docx": {
            "primary_domains": ["IAC"],
            "rationale": "Identity controls.",
        },
        "Encryption Policy.docx": {
            "primary_domains": ["DCH"],
            "rationale": "Encryption controls.",
        },
    }
    manifest_documents = [
        {
            "filename": f"Policy {index}.docx",
            "sha256": sha256,
            "size_bytes": 100,
        }
        for index, sha256 in enumerate(sha256s, start=1)
    ]

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "generated_at": "2026-07-29T10:00:00Z",
                "source": "test",
                "documents": manifest_documents,
            }
        ),
        encoding="utf-8",
    )

    cache_path = tmp_path / f"intents_{provider}.json"
    cache_path.write_text(
        json.dumps(
            {
                "prompt_version": "1",
                "provider": provider,
                "model": "test-model",
                "corpus_fingerprint": cache_fingerprint or fingerprint,
                "classified_at": "2026-07-29T10:00:00Z",
                "documents": intent_documents,
            }
        ),
        encoding="utf-8",
    )

    def fake_intents_path(requested_provider: str) -> Path:
        return tmp_path / f"intents_{requested_provider}.json"

    monkeypatch.setattr(variants, "_FIXTURES_LOCAL", tmp_path)
    monkeypatch.setattr(variants, "_intents_path", fake_intents_path)
    return cache_path


def test_ground_truth_has_exactly_33_domains() -> None:
    assert len(GROUND_TRUTH) == 33


def test_domain_sets_partition_ground_truth() -> None:
    covered = covered_domains()
    abstain = abstain_domains()

    assert len(covered) == 12
    assert len(abstain) == 21
    assert covered.isdisjoint(abstain)
    assert covered | abstain == frozenset(GROUND_TRUTH)


def test_outcomes_are_named_for_aggregation() -> None:
    assert OUTCOMES == {
        OUTCOME_CORRECT,
        OUTCOME_WRONG,
        OUTCOME_UNEXPECTED_ABSTAIN,
        OUTCOME_CORRECT_ABSTAIN,
        OUTCOME_MISSED_ABSTAIN,
    }


def test_expected_substring_returns_value_none_or_loud_unknown() -> None:
    assert expected_substring("IAC") == "Identity and Access"
    assert expected_substring("GOV") is None

    with pytest.raises(KeyError, match="ZZZ"):
        expected_substring("ZZZ")


def test_judge_truth_table() -> None:
    assert judge("IAC", "identity and access management policy.docx") == OUTCOME_CORRECT
    assert judge("IAC", "Acceptable Use Policy.docx") == OUTCOME_WRONG
    assert judge("IAC", None) == OUTCOME_UNEXPECTED_ABSTAIN
    assert judge("GOV", None) == OUTCOME_CORRECT_ABSTAIN
    assert judge("GOV", "Governance Policy.docx") == OUTCOME_MISSED_ABSTAIN


def test_judge_raises_for_unknown_domain() -> None:
    with pytest.raises(KeyError, match="ZZZ"):
        judge("ZZZ", None)


def test_rank_key_orders_top_candidate_deterministically() -> None:
    candidates = [
        (0.42, "Encryption Policy.docx", 0, "lower score"),
        (0.92, "Vendor Information Security Policy.docx", 3, "filename later"),
        (0.92, "Asset Management Policy.docx", 5, "ordinal later"),
        (0.92, "Asset Management Policy.docx", 2, "ordinal earlier"),
        (0.80, "Business Continuity Policy.docx", 1, "middle score"),
    ]

    ordered = sorted(candidates, key=lambda candidate: rank_key(candidate[0], candidate[1], candidate[2]))

    assert [candidate[3] for candidate in ordered] == [
        "ordinal earlier",
        "ordinal later",
        "filename later",
        "middle score",
        "lower score",
    ]


def test_baseline_gate_allows_all_documents_by_returning_none() -> None:
    documents = [_document("00000000-0000-0000-0000-000000000001", "Identity and Access Policy.docx")]

    assert BaselineGate().allowed_documents(_control("IAC"), documents) is None


def test_smoke_title_gate_abstains_for_abstain_domain() -> None:
    documents = [_document("00000000-0000-0000-0000-000000000001", "Identity and Access Policy.docx")]

    assert SmokeTitleGate().allowed_documents(_control("GOV"), documents) == set()


def test_smoke_title_gate_returns_matching_document_ids_for_covered_domain() -> None:
    matching_primary = _document("00000000-0000-0000-0000-000000000001", "Identity and Access Policy.docx")
    non_matching = _document("00000000-0000-0000-0000-000000000002", "Encryption Policy.docx")
    matching_case = _document("00000000-0000-0000-0000-000000000003", "identity and access standard.docx")

    allowed = SmokeTitleGate().allowed_documents(_control("IAC"), [matching_primary, non_matching, matching_case])

    assert allowed == {matching_primary.cdm_document_id, matching_case.cdm_document_id}


def test_get_variant_resolves_registered_variants_and_rejects_unknown_names() -> None:
    assert get_variant("baseline") is VARIANTS["baseline"]
    assert get_variant("smoke") is VARIANTS["smoke"]

    with pytest.raises(KeyError, match="known variants: baseline, intent-claude, intent-gpt, smoke"):
        get_variant("experimental")


def test_cached_intent_gate_allows_only_documents_whose_cached_domains_contain_control_domain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_intent_fixture(tmp_path, monkeypatch)
    matching = _document("00000000-0000-0000-0000-000000000001", "Identity and Access Policy.docx")
    non_matching = _document("00000000-0000-0000-0000-000000000002", "Encryption Policy.docx")

    allowed = CachedIntentGate("claude").allowed_documents(_control("IAC"), [matching, non_matching])

    assert allowed == {matching.cdm_document_id}


def test_cached_intent_gate_returns_empty_set_when_no_cached_document_covers_control_domain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_intent_fixture(tmp_path, monkeypatch)
    identity = _document("00000000-0000-0000-0000-000000000001", "Identity and Access Policy.docx")
    encryption = _document("00000000-0000-0000-0000-000000000002", "Encryption Policy.docx")

    allowed = CachedIntentGate("claude").allowed_documents(_control("GOV"), [identity, encryption])

    assert allowed == set()


def test_cached_intent_gate_raises_when_cache_fingerprint_differs_from_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_intent_fixture(tmp_path, monkeypatch, cache_fingerprint="stale-fingerprint")
    identity = _document("00000000-0000-0000-0000-000000000001", "Identity and Access Policy.docx")

    with pytest.raises(RuntimeError, match="corpus_fingerprint .* --force"):
        CachedIntentGate("claude").allowed_documents(_control("IAC"), [identity])


def test_cached_intent_gate_missing_cache_error_names_classification_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_intents_path(provider: str) -> Path:
        return tmp_path / f"intents_{provider}.json"

    monkeypatch.setattr(variants, "_intents_path", fake_intents_path)

    with pytest.raises(RuntimeError, match="classify_intents.py"):
        CachedIntentGate("claude").allowed_documents(_control("IAC"), [])


def test_cached_intent_gate_raises_when_document_is_absent_from_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_intent_fixture(tmp_path, monkeypatch)
    absent = _document("00000000-0000-0000-0000-000000000003", "Business Continuity Policy.docx")

    with pytest.raises(RuntimeError, match="Business Continuity Policy\\.docx.*--force"):
        CachedIntentGate("claude").allowed_documents(_control("BCD"), [absent])


def test_intent_variants_resolve_and_cached_gate_construction_does_not_touch_filesystem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_intents_path(provider: str) -> Path:
        raise AssertionError(f"unexpected cache path access for {provider}")

    def fail_manifest_fingerprint() -> str:
        raise AssertionError("unexpected manifest fingerprint access")

    monkeypatch.setattr(variants, "_intents_path", fail_intents_path)
    monkeypatch.setattr(variants, "_manifest_fingerprint", fail_manifest_fingerprint)

    assert get_variant("intent-claude") is VARIANTS["intent-claude"]
    assert get_variant("intent-gpt") is VARIANTS["intent-gpt"]
    assert CachedIntentGate("claude").name == "intent-claude"


def test_cached_intent_gate_source_does_not_reference_ground_truth() -> None:
    assert "ground_truth" not in inspect.getsource(CachedIntentGate)


def test_manifest_fingerprint_matches_setup_fixture_corpus_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("sqlalchemy")
    from scripts.cdm_eval.setup_fixture import corpus_fingerprint

    sha256s = ("bbbbbbbb", "aaaaaaaa", "cccccccc")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "generated_at": "2026-07-29T10:00:00Z",
                "source": "test",
                "documents": [
                    {
                        "filename": f"Policy {index}.docx",
                        "sha256": sha256,
                        "size_bytes": 100,
                    }
                    for index, sha256 in enumerate(sha256s, start=1)
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(variants, "_FIXTURES_LOCAL", tmp_path)

    assert variants._manifest_fingerprint() == corpus_fingerprint(sha256s)
