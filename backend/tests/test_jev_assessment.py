"""Unit tests for services.jev_assessment — the Jev evidence-assessment engine.

Pure functions throughout; the one call to the API goes through an injected
``ask``. What is pinned here:

- the engine setting resolves tolerantly and defaults to the LLM,
- the choice criteria ARE the designation vocabulary, one question per AO,
- the state is cut to the budget and says so,
- a verdict is refused, not repaired, when an answer is outside the contract,
- the comparison reports both the plain and the confident-subset agreement,
- a low-confidence designation is kept and named, never downgraded.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import jev_assessment as ja  # noqa: E402
from services import jev_client as jc  # noqa: E402
from services.assessment_prompts import AO_DESIGNATIONS, MAX_ASSESSMENT_OBJECTIVES  # noqa: E402


AO_ONE, AO_TWO = "AO0001", "AO0002"
CONTROL = "AAA-01"


def _objective(ao_id, scf_id=CONTROL):
    return {"ao_id": ao_id, "scf_id": scf_id, "objective_text": "Is it done?", "expected_results": "A record."}


def _context(**overrides):
    fields = {
        "evidence_id": "ERL-001",
        "artifact_title": "Access Control Policy",
        "artifact_description": "The policy",
        "area_of_focus": "AAA",
        "controls": [{"scf_id": CONTROL, "control_name": "Identity", "control_description": "Manage access."}],
        "context_hash": "c" * 64,
        "framework_version": "2026.1",
        "objectives": [_objective(AO_ONE), _objective(AO_TWO)],
        "objectives_capped": False,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _answer(qid, type_, value, confidence=0.95, probabilities=None):
    return jc.JevAnswer(
        question_id=qid, type=type_, value=value, confidence=confidence,
        probabilities=probabilities or {}, raw={},
    )


def _response(answers, model="jev-1.13.0"):
    return jc.JevResponse(model=model, answers=answers, input_tokens=500, output_tokens=0, latency_ms=120)


def _good_answers(**per_ao):
    answers = {}
    for ao_id in (AO_ONE, AO_TWO):
        designation, confidence = per_ao.get(ao_id, ("appears_satisfied", 0.95))
        answers[ao_id] = _answer(ao_id, "choice", designation, confidence, {designation: confidence})
    answers[ja.RELEVANCE_QUESTION_ID] = _answer(ja.RELEVANCE_QUESTION_ID, "score", 3.0, 0.8)
    return answers


# ---------------------------------------------------------------------------
# Engine resolution
# ---------------------------------------------------------------------------

class TestResolveEngine:
    @pytest.mark.parametrize("settings", [None, {}, [], "jev", {"evidence_assessment_engine": "gpt"},
                                          {"evidence_assessment_engine": 7}])
    def test_anything_unreadable_is_the_llm(self, settings):
        assert ja.resolve_engine(settings) == ja.ENGINE_LLM

    @pytest.mark.parametrize("engine", ja.ENGINES)
    def test_each_engine_round_trips(self, engine):
        assert ja.resolve_engine({ja.ENGINE_SETTING_KEY: engine}) == engine

    def test_default_is_llm(self):
        assert ja.DEFAULT_ENGINE == ja.ENGINE_LLM

    def test_cutoff_env(self, monkeypatch):
        monkeypatch.delenv(ja.CONFIDENCE_CUTOFF_ENV, raising=False)
        assert ja.confidence_cutoff() == ja.DEFAULT_CONFIDENCE_CUTOFF
        monkeypatch.setenv(ja.CONFIDENCE_CUTOFF_ENV, "0.7")
        assert ja.confidence_cutoff() == pytest.approx(0.7)
        monkeypatch.setenv(ja.CONFIDENCE_CUTOFF_ENV, "nope")
        assert ja.confidence_cutoff() == ja.DEFAULT_CONFIDENCE_CUTOFF
        monkeypatch.setenv(ja.CONFIDENCE_CUTOFF_ENV, "5")
        assert ja.confidence_cutoff() == 1.0


# ---------------------------------------------------------------------------
# Questions and state
# ---------------------------------------------------------------------------

class TestQuestions:
    def test_one_choice_per_objective_plus_relevance(self):
        q = ja.build_questions(_context())
        assert list(q) == [AO_ONE, AO_TWO, ja.RELEVANCE_QUESTION_ID]
        for ao_id in (AO_ONE, AO_TWO):
            assert q[ao_id]["type"] == "choice"
            # The criteria keys are the stored designation values — no mapping.
            assert tuple(q[ao_id]["criteria"]) == AO_DESIGNATIONS
            assert isinstance(q[ao_id]["criteria"], dict)
            assert ao_id in q[ao_id]["instructions"]
            assert CONTROL in q[ao_id]["instructions"]
            assert "Identity" in q[ao_id]["instructions"]
            assert "A record." in q[ao_id]["instructions"]

    def test_relevance_is_an_ordered_score_list(self):
        q = ja.build_questions(_context())[ja.RELEVANCE_QUESTION_ID]
        assert q["type"] == "score"
        assert isinstance(q["criteria"], list)
        assert 2 <= len(q["criteria"]) <= 10

    def test_criteria_descriptions_fit_the_api_limit(self):
        for text in ja.DESIGNATION_CRITERIA.values():
            assert len(text) <= 255
        for text in ja.RELEVANCE_LEVELS:
            assert len(text) <= 255

    def test_objectives_are_capped_like_the_llm_path(self):
        ctx = _context(objectives=[_objective(f"AO{i:04d}") for i in range(MAX_ASSESSMENT_OBJECTIVES + 5)])
        q = ja.build_questions(ctx)
        assert len(q) == MAX_ASSESSMENT_OBJECTIVES + 1


class TestState:
    def test_state_is_an_object_with_the_document_last(self):
        state, cut = ja.build_state(_context(), "the body", "policy.pdf", "application/pdf", "2026-09-29")
        assert cut is False
        assert list(state) == ["evidence", "mapped_controls", "notes", "document_text"]
        assert state["evidence"]["file"] == "policy.pdf"
        assert state["evidence"]["assessment_date"] == "2026-09-29"
        assert state["mapped_controls"][0]["scf_id"] == CONTROL
        assert state["document_text"] == "the body"
        assert state["notes"] == []

    def test_state_is_cut_to_budget_and_says_so(self, monkeypatch):
        monkeypatch.setenv(ja.STATE_CHAR_BUDGET_ENV, "1000")
        state, cut = ja.build_state(_context(), "x" * 5000, "f.txt", "text/plain")
        assert cut is True
        assert state["document_text"].startswith("x" * 1000)
        assert "truncated" in state["document_text"]
        assert any("cut further" in n for n in state["notes"])

    def test_extractor_truncation_is_disclosed(self):
        state, _ = ja.build_state(_context(), "x", "f.txt", "text/plain", truncated=True)
        assert any("head of the document" in n for n in state["notes"])

    def test_request_hash_is_stable_and_sensitive(self):
        ctx = _context()
        q = ja.build_questions(ctx)
        s1, _ = ja.build_state(ctx, "a", "f", "t")
        s2, _ = ja.build_state(ctx, "b", "f", "t")
        assert ja.hash_request(s1, q) == ja.hash_request(s1, q)
        assert ja.hash_request(s1, q) != ja.hash_request(s2, q)
        assert len(ja.hash_request(s1, q)) == 64


# ---------------------------------------------------------------------------
# Verdict parsing
# ---------------------------------------------------------------------------

def _parse(answers, cutoff=0.85):
    return ja.parse_verdict(
        _response(answers), [AO_ONE, AO_TWO],
        request_hash="r" * 64, state_truncated=False, cutoff=cutoff,
    )


class TestParseVerdict:
    def test_designations_confidence_and_relevance(self):
        v = _parse(_good_answers())
        assert v.designations == ["appears_satisfied", "appears_satisfied"]
        assert v.status == "sufficient"
        assert v.unassessable_reason is None
        assert v.ao_findings[0]["confidence"] == pytest.approx(0.95)
        assert set(v.ao_findings[0]["probabilities"]) == set(AO_DESIGNATIONS)
        # score 3 of levels 0..4 -> 75
        assert v.relevance_score == pytest.approx(75.0)
        assert v.model_id == "jev-1.13.0"
        assert v.input_tokens == 500
        assert v.cost_cents == pytest.approx(500 * 0.042 / 1_000_000 * 100)
        assert v.low_confidence_count == 0

    def test_status_is_derived_from_designations(self):
        v = _parse(_good_answers(**{AO_TWO: ("gap_identified", 0.9)}))
        assert v.status == "partial"
        assert v.gap_count == 1
        v = _parse(_good_answers(**{AO_ONE: ("cannot_assess", 0.9), AO_TWO: ("cannot_assess", 0.9)}))
        assert v.status == "unassessable"
        assert v.cannot_assess_count == 2
        assert v.unassessable_reason

    def test_low_confidence_is_kept_and_counted_not_downgraded(self):
        v = _parse(_good_answers(**{AO_TWO: ("gap_identified", 0.4)}))
        assert v.designations[1] == "gap_identified"
        assert v.low_confidence_count == 1
        assert v.low_confidence[0]["ao_id"] == AO_TWO

    def test_unknown_designation_is_refused(self):
        answers = _good_answers()
        answers[AO_ONE] = _answer(AO_ONE, "choice", "satisfied")
        with pytest.raises(ja.JevVerdictError, match="not one of"):
            _parse(answers)

    def test_missing_objective_is_refused(self):
        answers = _good_answers()
        del answers[AO_TWO]
        with pytest.raises(ja.JevVerdictError, match="no answer for objective 'AO0002'"):
            _parse(answers)

    def test_unasked_objective_is_refused(self):
        answers = _good_answers()
        answers["AO9999"] = _answer("AO9999", "choice", "appears_satisfied")
        with pytest.raises(ja.JevVerdictError, match="not asked"):
            _parse(answers)

    def test_relevance_is_optional_and_clamped(self):
        answers = _good_answers()
        del answers[ja.RELEVANCE_QUESTION_ID]
        assert _parse(answers).relevance_score is None
        answers[ja.RELEVANCE_QUESTION_ID] = _answer(ja.RELEVANCE_QUESTION_ID, "score", 9.0)
        assert _parse(answers).relevance_score == 100.0

    def test_no_objectives_records_partial(self):
        v = ja.parse_verdict(
            _response({ja.RELEVANCE_QUESTION_ID: _answer(ja.RELEVANCE_QUESTION_ID, "score", 2.0)}), [],
            request_hash="r", state_truncated=False, cutoff=0.85,
        )
        assert v.status == "partial"


class TestAssess:
    def test_assess_wires_state_questions_and_model(self, monkeypatch):
        seen = {}

        def ask(model, state, questions):
            seen.update(model=model, state=state, questions=questions)
            return _response(_good_answers())

        v = ja.assess(_context(), "body", "f.pdf", "application/pdf", "2026-09-29", ask=ask)
        assert seen["model"] == "jev-1.13.0"
        assert seen["state"]["document_text"] == "body"
        assert list(seen["questions"]) == [AO_ONE, AO_TWO, ja.RELEVANCE_QUESTION_ID]
        assert v.request_hash == ja.hash_request(seen["state"], seen["questions"])
        assert v.confidence_cutoff == ja.confidence_cutoff()

    def test_unavailable_propagates(self):
        def ask(*a, **k):
            raise jc.JevUnavailableError("no key")

        with pytest.raises(jc.JevUnavailableError):
            ja.assess(_context(), "body", "f", "t", ask=ask)


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

class TestCompare:
    def test_plain_and_confident_rates(self):
        v = _parse(_good_answers(**{AO_ONE: ("appears_satisfied", 0.95), AO_TWO: ("gap_identified", 0.5)}))
        llm = [
            {"ao_id": AO_ONE, "suggested_designation": "appears_satisfied"},
            {"ao_id": AO_TWO, "suggested_designation": "appears_satisfied"},
        ]
        c = ja.compare(llm, "sufficient", v)
        assert c["compared"] == 2 and c["agreed"] == 1
        assert c["agreement_rate"] == 0.5
        # Only AO_ONE cleared the cutoff, and it agreed.
        assert c["confident_total"] == 1 and c["confident_agreed"] == 1
        assert c["confident_agreement_rate"] == 1.0
        assert c["llm_status"] == "sufficient" and c["jev_status"] == "partial"
        assert c["status_agrees"] is False
        assert c["disagreements"] == [
            {"ao_id": AO_TWO, "llm": "appears_satisfied", "jev": "gap_identified", "confidence": 0.5},
        ]

    def test_objectives_the_llm_did_not_answer_are_not_compared(self):
        v = _parse(_good_answers())
        c = ja.compare([{"ao_id": AO_ONE, "suggested_designation": "appears_satisfied"}], "sufficient", v)
        assert c["compared"] == 1 and c["agreed"] == 1

    def test_nothing_compared_is_none_not_zero(self):
        v = _parse(_good_answers())
        c = ja.compare([], None, v)
        assert c["agreement_rate"] is None
        assert c["confident_agreement_rate"] is None
        assert c["status_agrees"] is None


# ---------------------------------------------------------------------------
# Primary-mode terminal fields
# ---------------------------------------------------------------------------

class TestTerminalFields:
    def test_ao_findings_carry_confidence_in_place_of_rationale(self):
        v = _parse(_good_answers(**{AO_TWO: ("gap_identified", 0.4)}))
        out = ja.terminal_ao_findings(v)
        assert [f["ao_id"] for f in out] == [AO_ONE, AO_TWO]
        assert out[0]["suggested_designation"] == "appears_satisfied"
        assert "0.95" in out[0]["rationale"]
        assert out[0]["suggestion"] == ""
        assert out[0]["confidence"] == pytest.approx(0.95)
        assert "below the cutoff" in out[1]["suggestion"]

    def test_findings_name_the_engine_and_the_low_confidence_objectives(self):
        v = _parse(_good_answers(**{AO_TWO: ("gap_identified", 0.4)}))
        findings = ja.terminal_findings(v, _context())
        assert "Jev" in findings[0]["message"]
        assert any(AO_TWO in f["message"] and "0.85" in f["message"] for f in findings)

    def test_no_published_objectives_is_disclosed_like_the_llm_path(self):
        # Only the relevance question was asked; the 'partial' status is a
        # stand-in and the reviewer is told so, as the LLM path tells them.
        answers = {
            ja.RELEVANCE_QUESTION_ID: jc.JevAnswer(
                question_id=ja.RELEVANCE_QUESTION_ID, type="score", value=2.0, confidence=0.8,
            ),
        }
        v = ja.parse_verdict(
            _response(answers), [], request_hash="r", state_truncated=False, cutoff=0.85,
        )
        assert v.ao_findings == []
        assert v.status == "partial"
        findings = ja.terminal_findings(v, _context(objectives=[]))
        assert any("publish no SCF assessment objectives" in f["message"] for f in findings)

    def test_findings_disclose_cap_and_state_truncation(self):
        v = ja.parse_verdict(
            _response(_good_answers()), [AO_ONE, AO_TWO],
            request_hash="r", state_truncated=True, cutoff=0.85,
        )
        findings = ja.terminal_findings(v, _context(objectives_capped=True))
        messages = " ".join(f["message"] for f in findings)
        assert "cut to fit" in messages
        assert str(MAX_ASSESSMENT_OBJECTIVES) in messages

    def test_summary_counts(self):
        v = _parse(_good_answers(**{AO_TWO: ("gap_identified", 0.4)}))
        s = ja.terminal_summary(v)
        assert "2 objective" in s and "1 appear satisfied" in s and "1 with gaps" in s and "1 below" in s
