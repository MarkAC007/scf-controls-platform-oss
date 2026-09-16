"""Lazy artifact-type extraction on first window assessment.

Self-hosted installs ship the catalog with `required_artifact_types` empty.
The window assessor fills it on demand: the first assessment that touches a
never-attempted control extracts once and the result is cached on the
control row. Everything here is fail-open — an extraction failure must never
break an assessment.
"""
import os
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import services.window_assessment_service as svc  # noqa: E402
from services.artifact_type_extraction_service import ExtractionResult  # noqa: E402


def _ctrl(scf_id="BCD-11", types=None, extracted_at=None):
    return SimpleNamespace(
        scf_id=scf_id,
        required_artifact_types=types,
        required_artifact_types_extracted_at=extracted_at,
    )


def _ok(scf_id, types):
    return ExtractionResult(
        scf_id=scf_id, artifact_types=types, model_id="m", input_tokens=1,
        output_tokens=1, cost_cents=0.0,
    )


@pytest.fixture
def calls(monkeypatch):
    """Record extract_for_control invocations; return a preset result."""
    seen = []
    state = {"result": None, "raise": None}

    def fake(session, scf_id, force=False):
        seen.append(scf_id)
        if state["raise"] is not None:
            raise state["raise"]
        return state["result"] or _ok(scf_id, [])

    monkeypatch.setattr(svc, "extract_for_control", fake)
    monkeypatch.delenv("ARTIFACT_TYPE_LAZY_EXTRACTION", raising=False)
    return SimpleNamespace(seen=seen, state=state)


class TestArtifactTypesForControl:
    def test_never_attempted_control_is_extracted_and_result_used(self, calls):
        calls.state["result"] = _ok("BCD-11", [{"type": "restore_test", "weight": "high", "mandatory": True}])
        out = svc._artifact_types_for_control(object(), _ctrl())
        assert calls.seen == ["BCD-11"]
        assert out == [{"type": "restore_test", "weight": "high", "mandatory": True}]

    def test_populated_control_is_not_re_extracted(self, calls):
        existing = [{"type": "policy", "weight": "medium", "mandatory": False}]
        out = svc._artifact_types_for_control(object(), _ctrl(types=existing))
        assert calls.seen == []
        assert out == existing

    def test_attempted_but_empty_control_is_not_retried(self, calls):
        """A stamped empty list means 'extraction ran and found nothing' —
        the assessor must not spend an LLM call on it every run."""
        out = svc._artifact_types_for_control(
            object(), _ctrl(types=[], extracted_at=datetime(2026, 9, 1)),
        )
        assert calls.seen == []
        assert out == []

    def test_env_opt_out_skips_extraction(self, calls, monkeypatch):
        monkeypatch.setenv("ARTIFACT_TYPE_LAZY_EXTRACTION", "false")
        out = svc._artifact_types_for_control(object(), _ctrl())
        assert calls.seen == []
        assert out == []

    @pytest.mark.parametrize("value", ["true", "1", "yes", "ON", " True "])
    def test_env_truthy_spellings_enable(self, calls, monkeypatch, value):
        monkeypatch.setenv("ARTIFACT_TYPE_LAZY_EXTRACTION", value)
        svc._artifact_types_for_control(object(), _ctrl())
        assert calls.seen == ["BCD-11"]

    def test_extraction_exception_is_swallowed(self, calls):
        calls.state["raise"] = RuntimeError("anthropic down")
        out = svc._artifact_types_for_control(object(), _ctrl())
        assert calls.seen == ["BCD-11"]
        assert out == []

    def test_extraction_error_result_yields_empty(self, calls):
        calls.state["result"] = ExtractionResult(
            scf_id="BCD-11", artifact_types=[], model_id="", input_tokens=0,
            output_tokens=0, cost_cents=0.0, error="LLM call failed",
        )
        out = svc._artifact_types_for_control(object(), _ctrl())
        assert out == []


class _FakeSession:
    """Answers the two selects _build_expected_artifact_types issues."""

    def __init__(self, catalog, ctrls):
        self._answers = [catalog, ctrls]

    def execute(self, stmt):
        answer = self._answers.pop(0)
        return SimpleNamespace(
            scalar_one_or_none=lambda: answer,
            scalars=lambda: SimpleNamespace(all=lambda: answer),
        )


class TestBuildExpectedArtifactTypesLazy:
    def test_only_never_attempted_controls_trigger_extraction(self, calls):
        calls.state["result"] = _ok("BCD-11", [{"type": "restore_test", "weight": "high", "mandatory": True}])
        catalog = SimpleNamespace(control_mappings=["BCD-11", "BCD-12", "BCD-13"])
        ctrls = [
            _ctrl("BCD-11"),                                               # never attempted → extract
            _ctrl("BCD-12", types=[{"type": "policy", "weight": "low"}]),  # populated → reuse
            _ctrl("BCD-13", types=[], extracted_at=datetime(2026, 9, 1)),  # attempted, empty → skip
        ]
        out = svc._build_expected_artifact_types(_FakeSession(catalog, ctrls), "E-0001")
        assert calls.seen == ["BCD-11"]
        assert sorted(e["type"] for e in out) == ["policy", "restore_test"]

    def test_failed_extraction_leaves_other_controls_intact(self, calls):
        calls.state["raise"] = RuntimeError("boom")
        catalog = SimpleNamespace(control_mappings=["BCD-11", "BCD-12"])
        ctrls = [
            _ctrl("BCD-11"),
            _ctrl("BCD-12", types=[{"type": "policy", "weight": "low"}]),
        ]
        out = svc._build_expected_artifact_types(_FakeSession(catalog, ctrls), "E-0001")
        assert [e["type"] for e in out] == ["policy"]

    def test_unmapped_evidence_never_extracts(self, calls):
        catalog = SimpleNamespace(control_mappings=[])
        out = svc._build_expected_artifact_types(_FakeSession(catalog, []), "E-0001")
        assert out == []
        assert calls.seen == []
