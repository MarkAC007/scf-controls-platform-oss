"""prompt_version is recorded on every AI verdict (#787, ISC-74).

``prompt_hash`` already proved that *a* prompt produced a verdict. It could
not answer the question an auditor actually asks — which release of the
template was that, and which verdicts came from the one we have since
corrected — because a hash is an identity, not an ordered version.

These assertions are structural (columns, SQL text, writer call sites)
rather than behavioural: exercising the real writers means an LLM call and
a database, neither of which exists in CI.
"""
from __future__ import annotations

import inspect
import os
import re
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tasks_assessment  # noqa: E402
from models import EvidenceAssessment, EvidenceWindowAssessment  # noqa: E402
from schemas import (  # noqa: E402
    EvidenceAssessmentResponse,
    EvidenceWindowAssessmentResponse,
)
from services import window_assessment_service  # noqa: E402
from services.assessment_prompts import PROMPT_VERSION  # noqa: E402


class TestColumns:
    def test_per_file_assessment_has_the_column(self):
        assert "prompt_version" in EvidenceAssessment.__table__.columns

    def test_window_assessment_has_the_column(self):
        assert "prompt_version" in EvidenceWindowAssessment.__table__.columns

    @pytest.mark.parametrize(
        "model", [EvidenceAssessment, EvidenceWindowAssessment]
    )
    def test_column_is_nullable(self, model):
        # Rows written before this column existed came from an unknown
        # template release. Backfilling the current version onto them would
        # put a fact into the audit trail that nobody established.
        assert model.__table__.columns["prompt_version"].nullable is True

    @pytest.mark.parametrize(
        "model", [EvidenceAssessment, EvidenceWindowAssessment]
    )
    def test_column_is_wide_enough_for_the_current_version(self, model):
        length = model.__table__.columns["prompt_version"].type.length
        assert length >= len(PROMPT_VERSION)


class TestApiExposure:
    def test_per_file_response_exposes_it(self):
        assert "prompt_version" in EvidenceAssessmentResponse.model_fields

    def test_window_response_exposes_it(self):
        assert "prompt_version" in EvidenceWindowAssessmentResponse.model_fields

    def test_it_is_optional_so_historical_rows_still_serialise(self):
        field = EvidenceAssessmentResponse.model_fields["prompt_version"]
        assert field.is_required() is False


class TestWriters:
    """The per-file writer went through one function in #881 WS2.

    These were source-text assertions pinning a specific UPDATE statement.
    That statement no longer exists — every terminal verdict now goes through
    ``_write_terminal_verdict``, which writes the version row and the visible
    row together — so the same invariant is asserted against what the writer
    actually sends, which is stronger than pinning its text.
    """

    def _params(self, verdict) -> dict:
        """Capture the parameters one terminal write sends to the database."""
        sent: list[dict] = []

        class RecordingSession:
            def execute(self, stmt, params=None):
                if params is not None:
                    sent.append(params)
                result = MagicMock()
                result.mappings.return_value.first.return_value = {
                    "id": "a" * 32, "version_number": 0,
                }
                return result

            def commit(self):
                pass

            def rollback(self):  # pragma: no cover - not reached in these tests
                pass

        tasks_assessment._write_terminal_verdict(
            RecordingSession(), "file-id", "org-id", verdict,
        )
        return sent[-1]

    def test_per_file_success_path_writes_it(self):
        params = self._params(tasks_assessment.TerminalVerdict(
            status="sufficient", summary="s", findings=[], prompt_hash="h" * 64,
        ))
        assert params["prompt_version"] == PROMPT_VERSION

    def test_per_file_error_path_writes_it_only_alongside_a_hash(self):
        # The version travels with the hash: stamping a version onto a verdict
        # that recorded no prompt_hash would claim provenance for a prompt this
        # run never made.
        params = self._params(tasks_assessment.TerminalVerdict(
            status="error", summary="it broke", findings=[],
        ))
        assert params["prompt_hash"] is None
        assert params["prompt_version"] is None

    def test_no_llm_result_path_does_not_claim_a_prompt(self):
        # An extraction failure is a verdict reached without calling a model.
        # There was no prompt, so there is no template version.
        params = self._params(tasks_assessment.TerminalVerdict(
            status="unassessable", summary="cannot read it", findings=[],
            unassessable_reason="unsupported content type",
        ))
        assert params["prompt_version"] is None

    def test_the_statement_still_carries_the_column(self):
        """A verdict with a prompt must reach both rows, not just one."""
        source = inspect.getsource(tasks_assessment)
        assert "prompt_version = :prompt_version" in source
        assert ":model_id, :prompt_hash, :prompt_version" in source

    @pytest.mark.parametrize(
        "count_at_least,fragment",
        [(3, "assessment.prompt_version = PROMPT_VERSION")],
    )
    def test_window_service_writes_it_on_every_finalising_path(
        self, count_at_least, fragment
    ):
        source = inspect.getsource(window_assessment_service)
        assert source.count(fragment) >= count_at_least

    def test_every_window_prompt_hash_write_is_paired_with_a_version(self):
        source = inspect.getsource(window_assessment_service)
        hash_writes = len(
            re.findall(r"assessment\.prompt_hash = ", source)
        )
        version_writes = source.count("assessment.prompt_version = PROMPT_VERSION")
        assert version_writes >= hash_writes


class TestVersionValue:
    def test_current_version_is_a_dotted_release(self):
        assert re.fullmatch(r"\d+\.\d+\.\d+", PROMPT_VERSION)
