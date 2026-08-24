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
    def test_per_file_success_path_writes_it(self):
        source = inspect.getsource(tasks_assessment)
        assert "prompt_version = :prompt_version" in source
        assert '"prompt_version": PROMPT_VERSION' in source

    def test_per_file_error_path_writes_it_only_alongside_a_hash(self):
        # The version travels with the hash: stamping a version onto a row
        # whose prompt_hash was left untouched would claim provenance for
        # a prompt this run never recorded.
        source = inspect.getsource(tasks_assessment._update_assessment_error)
        assert "prompt_version = CASE WHEN :prompt_hash IS NULL" in source

    def test_no_llm_result_path_does_not_claim_a_prompt(self):
        # _update_assessment_result records a verdict reached without
        # calling a model. There was no prompt, so there is no version.
        source = inspect.getsource(tasks_assessment._update_assessment_result)
        assert "prompt_version" not in source

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
