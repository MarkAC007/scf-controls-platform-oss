"""Unit tests for tasks_assessment — foundation repair and AO grounding (#881).

Uses a recording fake sync session (no database), following the pattern in
test_tasks_vendor_assessment.py.

Wave 1 (foundation repair) behaviours:

- an extraction failure is 'unassessable', not 'insufficient' scored 0,
- model failures raise so the task's retry configuration actually fires,
  while a missing key or missing SDK terminates immediately,
- truncation is disclosed to the model and persisted on the row,
- an unchanged file + context + prompt version skips the model call.

Wave 2 (AO-grounded assessment) behaviours:

- assessment objectives enter the prompt and the context hash,
- a response that breaks the v2 contract produces an honest error, never a
  fabricated verdict,
- the recorded status is derived from the objective designations, not quoted
  from the model,
- every terminal verdict appends a version row and resets the review block;
  a cache hit and a transient failure append nothing.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tasks_assessment as ta  # noqa: E402
from services.assessment_prompts import (  # noqa: E402
    MAX_ASSESSMENT_OBJECTIVES,
    PROMPT_VERSION,
    build_assessment_prompt,
)
from services.assessment_verdict import (  # noqa: E402
    AssessmentParseError,
    derive_assessment_status,
    exceeds_max_age,
    parse_assessment_v2,
)
from services.text_extraction_service import ExtractedContent  # noqa: E402


FILE_ID = "11111111-1111-1111-1111-111111111111"
ORG_ID = "22222222-2222-2222-2222-222222222222"
USER_ID = "33333333-3333-3333-3333-333333333333"
ASSESSMENT_ID = "44444444-4444-4444-4444-444444444444"
CONTEXT_HASH = "c" * 64
FILE_SHA = "f" * 64

AO_ONE = "AO0001"
AO_TWO = "AO0002"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeSession:
    """Recording session that answers the task's SELECTs by statement shape."""

    def __init__(self, file_row=None, prior_row=None, current_version_number=0):
        self.file_row = file_row if file_row is not None else _file_row()
        self.prior_row = prior_row
        self.current_version_number = current_version_number
        self.updates: list[tuple[str, dict]] = []
        self.version_inserts: list[dict] = []
        self.committed = 0
        self.rolled_back = 0
        self.closed = False

    def execute(self, stmt, params=None):
        sql = str(stmt)
        params = params or {}
        result = MagicMock()
        if "INSERT INTO evidence_assessment_versions" in sql:
            self.version_inserts.append(params)
            return result
        if "UPDATE evidence_assessments" in sql:
            self.updates.append((sql, params))
            return result
        if "FROM evidence_files" in sql:
            result.mappings.return_value.first.return_value = self.file_row
        elif "FOR UPDATE" in sql:
            # The write protocol's row lock, which also reads the version the
            # next one has to follow.
            result.mappings.return_value.first.return_value = {
                "id": ASSESSMENT_ID,
                "version_number": self.current_version_number,
            }
        elif "FROM evidence_assessments" in sql:
            result.mappings.return_value.first.return_value = self.prior_row
        else:
            result.mappings.return_value.first.return_value = None
        return result

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1

    def close(self):
        self.closed = True

    # -- assertions helpers -------------------------------------------------

    @property
    def status_writes(self) -> list[str]:
        """Every status this task wrote, in order."""
        out = []
        for sql, params in self.updates:
            if "status" in params:
                out.append(params["status"])
            elif "SET status = 'processing'" in sql:
                out.append("processing")
            elif "status = 'error'" in sql:
                out.append("error")
        return out

    def last_update(self) -> dict:
        return self.updates[-1][1]

    def last_findings(self) -> list:
        return json.loads(self.last_update()["findings"])

    def last_version(self) -> dict:
        return self.version_inserts[-1]


def _file_row(**overrides):
    row = {
        "id": FILE_ID,
        "evidence_id": "ERL-001",
        "organization_id": ORG_ID,
        "filename": "policy.pdf",
        "content_type": "application/pdf",
        "s3_key": "org/evidence/policy.pdf",
        "sha256_hash": FILE_SHA,
        "computed_sha256": FILE_SHA,
    }
    row.update(overrides)
    return row


def _prior_row(**overrides):
    row = {
        "status": "sufficient",
        "prompt_hash": "p" * 64,
        "prompt_version": PROMPT_VERSION,
        "control_context_hash": CONTEXT_HASH,
        "assessed_file_sha256": FILE_SHA,
    }
    row.update(overrides)
    return row


def _objective(ao_id, scf_id="AAA-01", text="Verify the thing is done."):
    return {
        "ao_id": ao_id,
        "scf_id": scf_id,
        "objective_text": text,
        "expected_results": "",
    }


def _context(**overrides):
    fields = {
        "evidence_id": "ERL-001",
        "artifact_title": "Access Control Policy",
        "artifact_description": "The policy",
        "area_of_focus": "IAC",
        "controls": [],
        "context_hash": CONTEXT_HASH,
        "framework_version": "2026.1",
        "objectives": [_objective(AO_ONE), _objective(AO_TWO)],
        "objectives_capped": False,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _ao(ao_id, designation, rationale="because the document says so"):
    return {
        "ao_id": ao_id,
        "suggested_designation": designation,
        "rationale": rationale,
        "suggestion": "",
    }


def _llm_ok(status="sufficient", score=88, ao_findings=None, **body):
    """A well-formed v2 response answering both default objectives."""
    payload = {
        "status": status,
        "relevance_score": score,
        "summary": "Looks good",
        "evidence_effective_date": None,
        "effective_date_source": None,
        "ao_findings": ao_findings if ao_findings is not None else [
            _ao(AO_ONE, "appears_satisfied"),
            _ao(AO_TWO, "appears_satisfied"),
        ],
        "findings": [{"category": "relevance", "level": "sufficient", "message": "on point"}],
    }
    payload.update(body)
    return _llm_raw(json.dumps(payload))


def _llm_raw(content, stop_reason="end_turn"):
    return {
        "content": content,
        "model": "claude-sonnet-4-6",
        "input_tokens": 100,
        "output_tokens": 50,
        "stop_reason": stop_reason,
    }


def _assessment_row(**overrides):
    """A stand-in for an EvidenceAssessment ORM row, for response building."""
    fields = {
        "id": uuid4(), "evidence_file_id": uuid4(), "organization_id": uuid4(),
        "evidence_id": "ERL-001", "status": "sufficient", "relevance_score": 88.0,
        "findings": [], "ao_findings": [], "summary": "Looks good",
        "gap_count": 0, "cannot_assess_count": 0,
        "evidence_effective_date": None, "effective_date_source": None,
        "age_exceeds_12_months": None, "unassessable_reason": None,
        "truncated": False, "assessed_file_sha256": FILE_SHA,
        "version_number": 1, "current_version_id": None,
        "review_decision": None, "reviewed_by_user_id": None, "reviewed_at": None,
        "model_id": "claude-sonnet-4-6", "prompt_hash": "p" * 64,
        "prompt_version": PROMPT_VERSION, "control_context_hash": CONTEXT_HASH,
        "framework_version": "2026.1", "input_token_count": 100,
        "output_token_count": 50, "cost_cents": None, "processing_time_ms": 10,
        "assessment_source": "on_demand", "requested_by_user_id": None,
        "assessed_at": datetime.utcnow(), "created_at": datetime.utcnow(),
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _run(monkeypatch, session, extracted=None, llm=None, force=False, context=None):
    """Drive the task eagerly with all I/O stubbed."""
    monkeypatch.setattr(ta, "_get_sync_session", lambda: session)
    monkeypatch.setattr(
        ta, "assemble_control_context_sync",
        lambda s, eid: _context() if context is None else context,
    )
    monkeypatch.setattr(ta, "download_evidence_bytes", lambda key: b"bytes")
    monkeypatch.setattr(
        ta, "extract_text_from_bytes",
        lambda data, ct, fn: extracted or ExtractedContent(text="policy body", extraction_method="pdf"),
    )
    if llm is not None:
        monkeypatch.setattr(ta, "_call_llm", llm)
    return ta.assess_evidence_task.apply(
        args=(FILE_ID, ORG_ID, USER_ID, "on_demand", force),
        throw=False,
    )


# ---------------------------------------------------------------------------
# Extraction outcomes
# ---------------------------------------------------------------------------

class TestExtractionOutcomes:
    def test_extraction_error_is_unassessable_with_real_reason(self, monkeypatch):
        session = FakeSession()
        llm = MagicMock()
        outcome = _run(
            monkeypatch, session,
            extracted=ExtractedContent(
                text="",
                extraction_method="unsupported",
                error="Image files require vision model support (phase 2)",
            ),
            llm=llm,
        ).result

        assert outcome["status"] == "unassessable"
        llm.assert_not_called()

        update = session.last_update()
        assert update["status"] == "unassessable"
        # No score: nobody read this file, so there is nothing to score.
        assert update["relevance_score"] is None
        # The extractor's real reason survives, rather than being replaced by
        # "Evidence file contains no readable content".
        assert "vision model support" in update["summary"]
        assert "vision model support" in session.last_findings()[0]["message"]

    def test_empty_without_error_stays_insufficient(self, monkeypatch):
        session = FakeSession()
        outcome = _run(
            monkeypatch, session,
            extracted=ExtractedContent(text="   ", extraction_method="pdf"),
            llm=MagicMock(),
        ).result

        # Extraction worked and the document is genuinely blank — that is a
        # verdict on the evidence, not a pipeline limitation.
        assert outcome["status"] == "insufficient"
        assert session.last_update()["status"] == "insufficient"
        assert session.last_update()["relevance_score"] == 0

    def test_unassessable_never_scores_zero(self, monkeypatch):
        """The regression that made images drag down the quality axis."""
        session = FakeSession()
        _run(
            monkeypatch, session,
            extracted=ExtractedContent(
                text="", extraction_method="unsupported",
                error="Unsupported content type for text extraction: image/png",
            ),
            llm=MagicMock(),
        )
        assert session.last_update()["status"] != "insufficient"
        assert session.last_update()["relevance_score"] != 0


# ---------------------------------------------------------------------------
# Failure taxonomy
# ---------------------------------------------------------------------------

class TestLLMFailures:
    def test_call_llm_raises_on_api_error(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        fake_anthropic = MagicMock()
        fake_anthropic.Anthropic.return_value.messages.create.side_effect = RuntimeError("529 overloaded")
        with patch.dict(sys.modules, {"anthropic": fake_anthropic}):
            with pytest.raises(ta.LLMCallError) as excinfo:
                ta._call_llm("sys", "user")
        assert excinfo.value.cause_class == "RuntimeError"
        assert "529 overloaded" in excinfo.value.cause_message

    def test_call_llm_raises_unavailable_without_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with patch.dict(sys.modules, {"anthropic": MagicMock()}):
            with pytest.raises(ta.LLMUnavailableError):
                ta._call_llm("sys", "user")

    def test_model_failure_propagates_so_retry_can_fire(self, monkeypatch):
        """The whole point of the change: the task must not return normally."""
        session = FakeSession()
        result = _run(
            monkeypatch, session,
            llm=MagicMock(side_effect=ta.LLMCallError(RuntimeError("529 overloaded"))),
        )
        assert result.failed(), "task returned normally — autoretry_for can never fire"
        assert isinstance(result.result, ta.LLMCallError)

        # The error writer sets status as a SQL literal, so read the summary.
        update = session.last_update()
        assert session.status_writes[-1] == "error"
        assert "529 overloaded" in update["summary"]
        assert "LLMCallError" in session.last_findings()[0]["exception_class"]

    def test_failed_row_is_never_left_processing(self, monkeypatch):
        session = FakeSession()
        _run(monkeypatch, session, llm=MagicMock(side_effect=ta.LLMCallError(RuntimeError("boom"))))
        assert session.status_writes[-1] != "processing"

    def test_missing_key_is_terminal_not_retried(self, monkeypatch):
        session = FakeSession()
        result = _run(
            monkeypatch, session,
            llm=MagicMock(side_effect=ta.LLMUnavailableError("ANTHROPIC_API_KEY not set")),
        )
        # Returns rather than raising: backing off changes nothing about a
        # missing key, so it must not consume the retry budget.
        assert not result.failed()
        assert result.result["retryable"] is False
        assert session.last_findings()[0]["retryable"] is False
        assert "ANTHROPIC_API_KEY" in session.last_update()["summary"]


# ---------------------------------------------------------------------------
# Cache gate
# ---------------------------------------------------------------------------

class TestCacheGate:
    def test_hit_when_nothing_changed(self):
        assert ta.is_cache_hit(
            status="sufficient", prompt_hash="p" * 64,
            stored_context_hash=CONTEXT_HASH, stored_prompt_version=PROMPT_VERSION,
            file_sha256=FILE_SHA, current_context_hash=CONTEXT_HASH,
            stored_file_sha256=FILE_SHA,
        )

    @pytest.mark.parametrize("kwargs", [
        {"status": "pending"},
        {"status": "processing"},
        {"status": "error"},
        {"status": "unassessable"},
        {"prompt_hash": None},
        {"file_sha256": None},
        {"stored_context_hash": "d" * 64},
        {"stored_prompt_version": "0.9.0"},
        {"stored_prompt_version": None},
        # The hash comparison the presence check could not make.
        {"stored_file_sha256": "a" * 64},
        {"stored_file_sha256": None},
    ])
    def test_miss_conditions(self, kwargs):
        base = dict(
            status="sufficient", prompt_hash="p" * 64,
            stored_context_hash=CONTEXT_HASH, stored_prompt_version=PROMPT_VERSION,
            file_sha256=FILE_SHA, current_context_hash=CONTEXT_HASH,
            stored_file_sha256=FILE_SHA,
        )
        base.update(kwargs)
        assert not ta.is_cache_hit(**base)

    def test_different_bytes_are_never_reused(self):
        """A verdict about other bytes is not a verdict about these ones."""
        assert not ta.is_cache_hit(
            status="sufficient", prompt_hash="p" * 64,
            stored_context_hash=CONTEXT_HASH, stored_prompt_version=PROMPT_VERSION,
            file_sha256=FILE_SHA, current_context_hash=CONTEXT_HASH,
            stored_file_sha256="0" * 64,
        )

    def test_task_cache_hit_skips_model_call(self, monkeypatch):
        session = FakeSession(prior_row=_prior_row())
        llm = MagicMock()
        outcome = _run(monkeypatch, session, llm=llm).result

        assert outcome["cached"] is True
        assert outcome["status"] == "sufficient"
        llm.assert_not_called()
        # Nothing written at all — not even the 'processing' claim.
        assert session.updates == []

    def test_force_bypasses_cache(self, monkeypatch):
        session = FakeSession(prior_row=_prior_row())
        llm = MagicMock(return_value=_llm_ok())
        outcome = _run(monkeypatch, session, llm=llm, force=True).result

        assert outcome["cached"] is False
        llm.assert_called_once()
        assert session.last_update()["status"] == "sufficient"

    def test_changed_context_reassesses(self, monkeypatch):
        session = FakeSession(prior_row=_prior_row(control_context_hash="stale" + "0" * 59))
        llm = MagicMock(return_value=_llm_ok())
        _run(monkeypatch, session, llm=llm)
        llm.assert_called_once()

    def test_no_prior_row_reassesses(self, monkeypatch):
        session = FakeSession(prior_row=None)
        llm = MagicMock(return_value=_llm_ok())
        _run(monkeypatch, session, llm=llm)
        llm.assert_called_once()


# ---------------------------------------------------------------------------
# Truncation
# ---------------------------------------------------------------------------

class TestTruncation:
    def test_notice_appended_to_user_prompt(self):
        _, truncated_prompt = build_assessment_prompt(
            control_context=_context(), extracted_text="head of doc",
            filename="p.pdf", content_type="application/pdf",
            assessment_date="2026-09-01", truncated=True,
        )
        _, whole_prompt = build_assessment_prompt(
            control_context=_context(), extracted_text="head of doc",
            filename="p.pdf", content_type="application/pdf",
            assessment_date="2026-09-01", truncated=False,
        )
        assert "truncated" in truncated_prompt.lower()
        assert "truncated" not in whole_prompt.lower()

    def test_truncation_persisted_in_findings(self, monkeypatch):
        session = FakeSession()
        outcome = _run(
            monkeypatch, session,
            extracted=ExtractedContent(
                text="head of doc", extraction_method="pdf", truncated=True,
            ),
            llm=MagicMock(return_value=_llm_ok()),
        ).result

        assert outcome["truncated"] is True
        findings = session.last_findings()
        # The tombstone is resolved: truncation is a column now, on both the
        # visible row and the frozen version.
        assert session.last_update()["truncated"] is True
        assert session.last_version()["truncated"] is True
        # The exact figure is still carried in the disclosure, so clients never
        # hardcode the extractor's MAX_TEXT_LENGTH in their own copy.
        assert ta.assessment_truncated_chars(findings) == len("head of doc")
        # The model's own findings are preserved alongside the disclosure.
        assert len(findings) == 2

    def test_no_disclosure_when_not_truncated(self, monkeypatch):
        session = FakeSession()
        _run(monkeypatch, session, llm=MagicMock(return_value=_llm_ok()))
        assert session.last_update()["truncated"] is False
        assert ta.assessment_truncated_chars(session.last_findings()) is None

    def test_assessment_truncated_chars_tolerates_junk(self):
        for junk in (None, [], ["not a dict"], "nope"):
            assert ta.assessment_truncated_chars(junk) is None

    def test_disclosure_survives_response_serialization(self, monkeypatch):
        """The finding schema drops undeclared keys — the marker must not be one."""
        from schemas import EvidenceAssessmentResponse

        session = FakeSession()
        _run(
            monkeypatch, session,
            extracted=ExtractedContent(text="head of doc", extraction_method="pdf", truncated=True),
            llm=MagicMock(return_value=_llm_ok()),
        )
        update = session.last_update()
        row = _assessment_row(
            findings=json.loads(update["findings"]),
            ao_findings=json.loads(update["ao_findings"]),
            truncated=update["truncated"],
        )
        response = EvidenceAssessmentResponse.from_assessment(row)

        assert response.truncated is True
        assert response.truncated_at_chars == len("head of doc")
        disclosure = [f for f in response.findings if f.truncated]
        assert len(disclosure) == 1
        assert disclosure[0].truncated_at_chars == len("head of doc")

    def test_response_carries_the_ao_findings(self, monkeypatch):
        from schemas import EvidenceAssessmentResponse

        session = FakeSession()
        _run(monkeypatch, session, llm=MagicMock(return_value=_llm_ok()))
        update = session.last_update()
        response = EvidenceAssessmentResponse.from_assessment(
            _assessment_row(
                findings=json.loads(update["findings"]),
                ao_findings=json.loads(update["ao_findings"]),
                gap_count=update["gap_count"],
                cannot_assess_count=update["cannot_assess_count"],
                version_number=update["version_number"],
            )
        )
        assert [f.ao_id for f in response.ao_findings] == [AO_ONE, AO_TWO]
        assert response.version_number == 1
        assert response.review_decision is None


# ---------------------------------------------------------------------------
# v2 response parsing — refusal, coercion and clamping
# ---------------------------------------------------------------------------

def _v2_body(**overrides):
    body = {
        "status": "partial",
        "relevance_score": 50,
        "summary": "A summary",
        "evidence_effective_date": None,
        "effective_date_source": None,
        "ao_findings": [_ao(AO_ONE, "appears_satisfied"), _ao(AO_TWO, "gap_identified")],
        "findings": [],
    }
    body.update(overrides)
    return json.dumps(body)


def _parse(content, stop_reason="end_turn", ao_ids=(AO_ONE, AO_TWO)):
    return parse_assessment_v2(content, stop_reason, list(ao_ids))


class TestParseAssessmentV2:
    def test_happy_path(self):
        parsed = _parse(_v2_body())
        assert [f["ao_id"] for f in parsed.ao_findings] == [AO_ONE, AO_TWO]
        assert parsed.gap_count == 1
        assert parsed.cannot_assess_count == 0
        assert parsed.relevance_score == 50.0

    def test_markdown_fenced_json(self):
        parsed = _parse(f"```json\n{_v2_body()}\n```")
        assert parsed.model_status == "partial"

    def test_answers_are_reordered_to_match_the_prompt(self):
        """The row and the UI read in the order the objectives were asked."""
        parsed = _parse(_v2_body(ao_findings=[
            _ao(AO_TWO, "gap_identified"), _ao(AO_ONE, "appears_satisfied"),
        ]))
        assert [f["ao_id"] for f in parsed.ao_findings] == [AO_ONE, AO_TWO]

    # -- refusals: an honest error, never a fabricated verdict --------------

    def test_invalid_json_raises(self):
        with pytest.raises(AssessmentParseError) as excinfo:
            _parse("I'm afraid I can't do that")
        assert "not valid JSON" in excinfo.value.reason
        assert excinfo.value.retryable is True

    def test_json_that_is_not_an_object_raises(self):
        with pytest.raises(AssessmentParseError):
            _parse("[1, 2, 3]")

    def test_max_tokens_is_terminal_not_retryable(self):
        """Same prompt, same ceiling, same cut — retrying spends three calls."""
        with pytest.raises(AssessmentParseError) as excinfo:
            _parse(_v2_body(), stop_reason="max_tokens")
        assert excinfo.value.retryable is False
        assert "cut off" in excinfo.value.reason

    def test_missing_ao_id_raises(self):
        with pytest.raises(AssessmentParseError) as excinfo:
            _parse(_v2_body(ao_findings=[{"suggested_designation": "appears_satisfied"}]))
        assert "no usable ao_id" in excinfo.value.reason

    def test_unknown_ao_id_raises(self):
        with pytest.raises(AssessmentParseError) as excinfo:
            _parse(_v2_body(ao_findings=[
                _ao(AO_ONE, "appears_satisfied"), _ao("AO9999", "appears_satisfied"),
            ]))
        assert "AO9999" in excinfo.value.reason

    def test_unanswered_objective_raises(self):
        with pytest.raises(AssessmentParseError) as excinfo:
            _parse(_v2_body(ao_findings=[_ao(AO_ONE, "appears_satisfied")]))
        assert AO_TWO in excinfo.value.reason

    def test_duplicate_ao_id_raises(self):
        with pytest.raises(AssessmentParseError) as excinfo:
            _parse(_v2_body(ao_findings=[
                _ao(AO_ONE, "appears_satisfied"), _ao(AO_ONE, "gap_identified"),
            ]))
        assert "more than once" in excinfo.value.reason

    @pytest.mark.parametrize("designation", ["satisfied", "other_than_satisfied", "pass", "", None])
    def test_designation_outside_the_advisory_vocabulary_raises(self, designation):
        """CAP assessor terms are not this platform's vocabulary."""
        with pytest.raises(AssessmentParseError):
            _parse(_v2_body(ao_findings=[
                _ao(AO_ONE, designation), _ao(AO_TWO, "appears_satisfied"),
            ]))

    def test_ao_findings_not_an_array_raises(self):
        with pytest.raises(AssessmentParseError):
            _parse(_v2_body(ao_findings="oops"))

    # -- coercion: fields that degrade rather than fail ---------------------

    @pytest.mark.parametrize("raw,expected", [(150, 100.0), (-20, 0.0), (42, 42.0), (0, 0.0)])
    def test_score_clamped(self, raw, expected):
        assert _parse(_v2_body(relevance_score=raw)).relevance_score == expected

    def test_non_numeric_score_becomes_null(self):
        assert _parse(_v2_body(relevance_score="high")).relevance_score is None

    def test_missing_score_stays_null_not_zero(self):
        body = json.loads(_v2_body())
        del body["relevance_score"]
        assert _parse(json.dumps(body)).relevance_score is None

    def test_model_cannot_claim_unassessable(self):
        """'unassessable' is a pipeline determination, never a model verdict."""
        assert _parse(_v2_body(status="unassessable")).model_status is None

    def test_off_contract_status_is_dropped_not_believed(self):
        assert _parse(_v2_body(status="excellent")).model_status is None

    def test_findings_forced_to_list(self):
        assert _parse(_v2_body(findings="oops")).findings == []

    # -- effective date -----------------------------------------------------

    def test_effective_date_parsed(self):
        parsed = _parse(_v2_body(
            evidence_effective_date="2026-03-14",
            effective_date_source="Approved on page 1",
        ))
        assert parsed.evidence_effective_date == date(2026, 3, 14)
        assert parsed.effective_date_source == "Approved on page 1"

    def test_null_date_is_the_contract_not_an_error(self):
        parsed = _parse(_v2_body(evidence_effective_date=None))
        assert parsed.evidence_effective_date is None
        # Silent: not finding a date is the expected, correct answer.
        assert parsed.findings == []

    def test_malformed_date_degrades_and_is_disclosed(self):
        """One bad field must not throw away a whole set of objective findings."""
        parsed = _parse(_v2_body(evidence_effective_date="March 2026"))
        assert parsed.evidence_effective_date is None
        assert any("March 2026" in f["message"] for f in parsed.findings)

    def test_source_without_a_date_is_dropped(self):
        parsed = _parse(_v2_body(
            evidence_effective_date=None, effective_date_source="page 1",
        ))
        assert parsed.effective_date_source is None


class TestEvidenceAge:
    def test_null_when_no_date(self):
        assert exceeds_max_age(None) is None

    def test_within_twelve_months(self):
        assert exceeds_max_age(date(2026, 1, 1), as_of=date(2026, 9, 1)) is False

    def test_beyond_twelve_months(self):
        assert exceeds_max_age(date(2025, 1, 1), as_of=date(2026, 9, 1)) is True

    def test_the_boundary_is_365_days(self):
        assert exceeds_max_age(date(2025, 9, 1), as_of=date(2026, 9, 1)) is False
        assert exceeds_max_age(date(2025, 8, 31), as_of=date(2026, 9, 1)) is True


# ---------------------------------------------------------------------------
# Status derivation — the matrix
# ---------------------------------------------------------------------------

SAT = "appears_satisfied"
GAP = "gap_identified"
NA = "not_applicable"
CANT = "cannot_assess"


class TestDeriveAssessmentStatus:
    @pytest.mark.parametrize("designations,expected", [
        ([SAT], "sufficient"),
        ([SAT, SAT], "sufficient"),
        # not_applicable is excluded from the arithmetic entirely.
        ([SAT, NA], "sufficient"),
        ([SAT, CANT], "partial"),
        ([SAT, GAP], "partial"),
        ([SAT, GAP, CANT], "partial"),
        ([GAP], "insufficient"),
        ([GAP, CANT], "insufficient"),
        ([GAP, NA], "insufficient"),
        # Nothing shown and nothing found missing: a statement about fit.
        ([CANT], "unassessable"),
        ([CANT, CANT], "unassessable"),
        ([CANT, NA], "unassessable"),
        ([NA], "unassessable"),
        ([NA, NA], "unassessable"),
    ])
    def test_matrix(self, designations, expected):
        status, _ = derive_assessment_status(designations)
        assert status == expected

    def test_no_objectives_means_no_derivation(self):
        """None, so the caller falls back rather than inventing a verdict."""
        status, reason = derive_assessment_status([])
        assert status is None
        assert reason is None

    def test_all_not_applicable_records_why(self):
        _, reason = derive_assessment_status([NA, NA])
        assert "not applicable" in reason

    def test_all_cannot_assess_records_why(self):
        _, reason = derive_assessment_status([CANT])
        assert "could be evaluated" in reason

    def test_a_reason_is_only_recorded_for_unassessable(self):
        for designations in ([SAT], [SAT, GAP], [GAP]):
            _, reason = derive_assessment_status(designations)
            assert reason is None


# ---------------------------------------------------------------------------
# Assessment objectives reach the model and the cache key
# ---------------------------------------------------------------------------

class TestObjectivesInContext:
    def test_objectives_are_rendered_in_the_prompt(self):
        _, prompt = build_assessment_prompt(
            control_context=_context(), extracted_text="body",
            filename="p.pdf", content_type="application/pdf",
        )
        assert "## Assessment Objectives" in prompt
        assert AO_ONE in prompt and AO_TWO in prompt
        assert "2 to answer" in prompt

    def test_the_advisory_vocabulary_is_stated_and_assessor_terms_are_not(self):
        system, prompt = build_assessment_prompt(
            control_context=_context(), extracted_text="body",
            filename="p.pdf", content_type="application/pdf",
        )
        for designation in ("appears_satisfied", "gap_identified", "not_applicable", "cannot_assess"):
            assert designation in system or designation in prompt
        # The one place assessor vocabulary may appear is the instruction
        # forbidding it.
        assert "other than satisfied" in system
        assert "Never use assessor vocabulary" in system

    def test_no_objectives_is_stated_rather_than_left_blank(self):
        _, prompt = build_assessment_prompt(
            control_context=_context(objectives=[]), extracted_text="body",
            filename="p.pdf", content_type="application/pdf",
        )
        assert "No assessment objectives are published" in prompt
        assert "0 to answer" in prompt

    def test_cap_is_disclosed_in_the_prompt(self):
        _, prompt = build_assessment_prompt(
            control_context=_context(objectives_capped=True), extracted_text="body",
            filename="p.pdf", content_type="application/pdf",
        )
        assert str(MAX_ASSESSMENT_OBJECTIVES) in prompt

    def test_expected_results_are_included_when_present(self):
        objective = _objective(AO_ONE)
        objective["expected_results"] = "A signed approval record exists"
        _, prompt = build_assessment_prompt(
            control_context=_context(objectives=[objective]), extracted_text="body",
            filename="p.pdf", content_type="application/pdf",
        )
        assert "A signed approval record exists" in prompt

    def test_context_hash_moves_when_the_objectives_move(self):
        """A catalog change to the AOs must invalidate the cached verdict."""
        from services.assessment_prompts import _context_hash

        catalog = SimpleNamespace(
            artifact_title="T", artifact_description="D",
            area_of_focus="IAC", catalog_version="2026.1",
        )
        base = _context_hash("ERL-001", catalog, [], [_objective(AO_ONE)])
        reworded = _objective(AO_ONE, text="Verify the thing is done, quarterly.")
        assert base != _context_hash("ERL-001", catalog, [], [reworded])
        assert base != _context_hash("ERL-001", catalog, [], [_objective(AO_ONE), _objective(AO_TWO)])
        assert base != _context_hash("ERL-001", catalog, [], [])
        # And is stable for identical input, or the cache never hits at all.
        assert base == _context_hash("ERL-001", catalog, [], [_objective(AO_ONE)])


# ---------------------------------------------------------------------------
# AO-grounded verdicts through the task
# ---------------------------------------------------------------------------

class TestAOGroundedTask:
    def test_designations_are_stored_and_counted(self, monkeypatch):
        session = FakeSession()
        outcome = _run(monkeypatch, session, llm=MagicMock(return_value=_llm_ok(
            ao_findings=[_ao(AO_ONE, SAT), _ao(AO_TWO, GAP)],
        ))).result

        assert outcome["status"] == "partial"
        assert outcome["gap_count"] == 1
        update = session.last_update()
        assert json.loads(update["ao_findings"])[1]["suggested_designation"] == GAP
        assert update["gap_count"] == 1
        assert update["cannot_assess_count"] == 0

    def test_derived_status_beats_the_models_own(self, monkeypatch):
        """A positive impression cannot overrule the objectives it produced."""
        session = FakeSession()
        outcome = _run(monkeypatch, session, llm=MagicMock(return_value=_llm_ok(
            status="sufficient",
            ao_findings=[_ao(AO_ONE, GAP), _ao(AO_TWO, GAP)],
        ))).result

        assert outcome["status"] == "insufficient"
        # And the disagreement is on the row, not only in the logs.
        messages = [f["message"] for f in session.last_findings()]
        assert any("'sufficient'" in m and "'insufficient'" in m for m in messages)

    def test_agreement_records_no_coercion_finding(self, monkeypatch):
        session = FakeSession()
        _run(monkeypatch, session, llm=MagicMock(return_value=_llm_ok()))
        assert not any(
            "derived from its own per-objective" in f["message"]
            for f in session.last_findings()
        )

    def test_all_cannot_assess_is_unassessable_with_a_reason(self, monkeypatch):
        session = FakeSession()
        outcome = _run(monkeypatch, session, llm=MagicMock(return_value=_llm_ok(
            ao_findings=[_ao(AO_ONE, CANT), _ao(AO_TWO, CANT)],
        ))).result

        assert outcome["status"] == "unassessable"
        assert "could be evaluated" in session.last_update()["unassessable_reason"]

    def test_no_objectives_falls_back_and_says_so(self, monkeypatch):
        session = FakeSession()
        outcome = _run(
            monkeypatch, session, context=_context(objectives=[]),
            llm=MagicMock(return_value=_llm_ok(status="partial", ao_findings=[])),
        ).result

        assert outcome["status"] == "partial"
        assert any(
            "publish no SCF assessment objectives" in f["message"]
            for f in session.last_findings()
        )

    def test_capped_objectives_are_disclosed_on_the_row(self, monkeypatch):
        session = FakeSession()
        _run(
            monkeypatch, session, context=_context(objectives_capped=True),
            llm=MagicMock(return_value=_llm_ok()),
        )
        assert any(
            "Coverage of the remainder is unknown" in f["message"]
            for f in session.last_findings()
        )

    def test_effective_date_is_stored_and_aged(self, monkeypatch):
        session = FakeSession()
        _run(monkeypatch, session, llm=MagicMock(return_value=_llm_ok(
            evidence_effective_date="2020-01-01",
            effective_date_source="Approved on page 1",
        )))
        update = session.last_update()
        assert update["evidence_effective_date"] == date(2020, 1, 1)
        assert update["effective_date_source"] == "Approved on page 1"
        assert update["age_exceeds_12_months"] is True

    def test_no_effective_date_is_null_not_false(self, monkeypatch):
        session = FakeSession()
        _run(monkeypatch, session, llm=MagicMock(return_value=_llm_ok()))
        update = session.last_update()
        assert update["evidence_effective_date"] is None
        # Null, because "not old" is a claim nobody made about an unknown date.
        assert update["age_exceeds_12_months"] is None

    def test_unreadable_response_is_an_error_with_the_real_reason(self, monkeypatch):
        session = FakeSession()
        result = _run(monkeypatch, session, llm=MagicMock(return_value=_llm_raw("not json at all")))

        assert result.failed(), "a parse failure must raise so the retry can fire"
        assert isinstance(result.result, AssessmentParseError)
        update = session.last_update()
        assert "not valid JSON" in update["summary"]
        # No verdict was invented from a response nobody could read.
        assert update["status"] == "error"
        assert "relevance_score" not in update or update.get("relevance_score") is None

    def test_truncated_output_terminates_instead_of_retrying(self, monkeypatch):
        session = FakeSession()
        result = _run(
            monkeypatch, session,
            llm=MagicMock(return_value=_llm_raw(_v2_body()[:40], stop_reason="max_tokens")),
        )
        assert not result.failed(), "retrying an identical cut-off costs three calls"
        assert result.result["retryable"] is False
        assert "cut off" in result.result["message"]


# ---------------------------------------------------------------------------
# Version write protocol
# ---------------------------------------------------------------------------

class TestVersionWriteProtocol:
    def test_terminal_verdict_appends_a_version(self, monkeypatch):
        session = FakeSession()
        _run(monkeypatch, session, llm=MagicMock(return_value=_llm_ok()))

        assert len(session.version_inserts) == 1
        version = session.last_version()
        assert version["version_number"] == 1
        assert version["schema_version"] == 2
        assert version["status"] == "sufficient"
        # The frozen copy and the visible row say the same thing.
        assert version["findings"] == session.last_update()["findings"]
        assert version["ao_findings"] == session.last_update()["ao_findings"]

    def test_version_number_follows_the_previous_one(self, monkeypatch):
        session = FakeSession(current_version_number=4)
        _run(monkeypatch, session, llm=MagicMock(return_value=_llm_ok()))

        assert session.last_version()["version_number"] == 5
        assert session.last_update()["version_number"] == 5

    def test_new_verdict_resets_the_review_block(self, monkeypatch):
        """A reviewer's name must not appear against findings they never saw."""
        session = FakeSession()
        _run(monkeypatch, session, llm=MagicMock(return_value=_llm_ok()))

        sql = session.updates[-1][0]
        assert "review_decision = NULL" in sql
        assert "reviewed_by_user_id = NULL" in sql
        assert "reviewed_at = NULL" in sql

    def test_provenance_is_frozen_on_the_version(self, monkeypatch):
        session = FakeSession()
        _run(monkeypatch, session, llm=MagicMock(return_value=_llm_ok()))

        version = session.last_version()
        assert version["prompt_version"] == PROMPT_VERSION
        assert version["control_context_hash"] == CONTEXT_HASH
        assert version["model_id"] == "claude-sonnet-4-6"
        assert version["assessed_file_sha256"] == FILE_SHA
        assert version["input_token_count"] == 100

    def test_cache_hit_appends_nothing(self, monkeypatch):
        session = FakeSession(prior_row=_prior_row())
        _run(monkeypatch, session, llm=MagicMock())

        assert session.version_inserts == []
        assert session.updates == []

    def test_extraction_failure_still_appends_a_version(self, monkeypatch):
        """'unassessable' is a verdict about the file and belongs in history."""
        session = FakeSession()
        _run(
            monkeypatch, session,
            extracted=ExtractedContent(
                text="", extraction_method="unsupported",
                error="Unsupported content type for text extraction: image/png",
            ),
            llm=MagicMock(),
        )
        version = session.last_version()
        assert version["status"] == "unassessable"
        assert "image/png" in version["unassessable_reason"]
        assert version["assessed_file_sha256"] == FILE_SHA

    def test_retries_produce_one_version_not_three(self, monkeypatch):
        """Three attempts at the same file are one story, not three verdicts."""
        session = FakeSession()
        _run(monkeypatch, session, llm=MagicMock(side_effect=ta.LLMCallError(RuntimeError("529"))))

        # Every attempt writes the visible row so a dead worker cannot leave it
        # on 'processing'; only the one that exhausted the budget is history.
        assert session.status_writes.count("error") > 1
        assert len(session.version_inserts) == 1
        assert session.status_writes[-1] == "error"

    def test_terminal_failure_appends_a_version(self, monkeypatch):
        session = FakeSession()
        _run(monkeypatch, session, llm=MagicMock(
            side_effect=ta.LLMUnavailableError("ANTHROPIC_API_KEY not set"),
        ))

        assert len(session.version_inserts) == 1
        assert session.last_version()["status"] == "error"

    def test_missing_assessment_row_is_a_no_op_not_a_crash(self, monkeypatch):
        session = FakeSession()
        monkeypatch.setattr(
            session, "execute",
            _execute_without_lock_row(session),
        )
        written = ta._write_terminal_verdict(
            session, FILE_ID, ORG_ID,
            ta.TerminalVerdict(status="sufficient", summary="s", findings=[]),
        )
        assert written is None
        assert session.version_inserts == []
        assert session.rolled_back == 1


def _execute_without_lock_row(session):
    """Simulate the assessment row disappearing between trigger and worker."""
    original = session.execute

    def execute(stmt, params=None):
        if "FOR UPDATE" in str(stmt):
            result = MagicMock()
            result.mappings.return_value.first.return_value = None
            return result
        return original(stmt, params)

    return execute
