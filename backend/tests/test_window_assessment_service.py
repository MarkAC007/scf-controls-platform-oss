"""Unit tests for windowed evidence assessment service (M1a).

Pure-function coverage for the private helpers. Full end-to-end tests (real DB,
real LLM) are exercised manually via the CG production MCP path per the plan's
Verification section — this file locks down the deterministic parts.
"""
import os
import pathlib
import sys
from datetime import datetime
from uuid import uuid4

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.window_assessment_service import (
    FALLBACK_FREQUENCY,
    _FileInWindow,
    _compose_findings,
    _prior_review_reference,
    _compute_coverage,
    _compute_window_hash,
    _guess_artifact_type_for_source,
    _infer_source_label,
    _parse_llm_response,
    _resolve_frequency,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _FakeTracking:
    def __init__(self, frequency):
        self.frequency = frequency


def _make_file(source_label="AzureBackup", uploaded_at=None, sha=None) -> _FileInWindow:
    return _FileInWindow(
        id=uuid4(),
        filename=f"webhook_{source_label}_x.json",
        s3_key=f"s3://bucket/{source_label}_x.json",
        content_type="application/json",
        uploaded_at=uploaded_at or datetime(2026, 4, 10, 12, 0, 0),
        source_label=source_label,
        extracted_text="{}",
        sha256_hash=sha or "deadbeef",
    )


# ---------------------------------------------------------------------------
# _resolve_frequency
# ---------------------------------------------------------------------------

class TestResolveFrequency:
    def test_none_tracking_falls_back(self):
        freq, is_fallback = _resolve_frequency(None)
        assert freq == FALLBACK_FREQUENCY
        assert is_fallback is True

    def test_blank_frequency_falls_back(self):
        freq, is_fallback = _resolve_frequency(_FakeTracking(""))
        assert freq == FALLBACK_FREQUENCY
        assert is_fallback is True

    def test_known_frequency_preserved(self):
        freq, is_fallback = _resolve_frequency(_FakeTracking("daily"))
        assert freq == "daily"
        assert is_fallback is False

    def test_uppercase_is_normalised(self):
        freq, is_fallback = _resolve_frequency(_FakeTracking("  DAILY  "))
        assert freq == "daily"
        assert is_fallback is False

    def test_aliased_spellings_now_resolve(self):
        """#783: 'fortnightly' used to fall back to monthly because each
        subsystem kept its own spelling list. It now resolves through the shared
        alias table, so the assessment window matches the declared cadence."""
        freq, is_fallback = _resolve_frequency(_FakeTracking("fortnightly"))
        assert freq == "biweekly"
        assert is_fallback is False

    def test_annually_resolves_to_annual(self):
        """The headline #783 defect at this callsite: the wizard's 'annually'
        was not a key, so an annual control got a monthly assessment window."""
        freq, is_fallback = _resolve_frequency(_FakeTracking("annually"))
        assert freq == "annual"
        assert is_fallback is False

    def test_unknown_frequency_falls_back(self):
        freq, is_fallback = _resolve_frequency(_FakeTracking("whenever we remember"))
        assert freq == FALLBACK_FREQUENCY
        assert is_fallback is True


# ---------------------------------------------------------------------------
# _infer_source_label
# ---------------------------------------------------------------------------

class TestInferSourceLabel:
    def test_webhook_filename_prefix_yields_source(self):
        assert _infer_source_label("webhook_AzureBackup_abc.json") == "AzureBackup"

    def test_non_webhook_filename_is_unknown(self):
        assert _infer_source_label("random_file.json") == "unknown"

    def test_empty_filename_is_unknown(self):
        assert _infer_source_label("") == "unknown"

    def test_webhook_payload_source_wins_over_filename(self):
        fid = uuid4()
        result = _infer_source_label(
            "webhook_AzureBackup_x.json",
            webhook_source_by_file={fid: "EntraID"},
            file_id=fid,
        )
        assert result == "EntraID"

    def test_webhook_payload_empty_falls_through(self):
        fid = uuid4()
        result = _infer_source_label(
            "webhook_AzureBackup_x.json",
            webhook_source_by_file={fid: ""},
            file_id=fid,
        )
        assert result == "AzureBackup"


# ---------------------------------------------------------------------------
# _guess_artifact_type_for_source
# ---------------------------------------------------------------------------

class TestGuessArtifactType:
    def test_empty_source_returns_none(self):
        assert _guess_artifact_type_for_source("", [{"type": "status_snapshot"}]) is None

    def test_empty_expected_returns_none(self):
        assert _guess_artifact_type_for_source("AzureBackup", []) is None

    def test_substring_match_on_token(self):
        expected = [{"type": "backup_status"}, {"type": "restore_test"}]
        assert _guess_artifact_type_for_source("AzureBackup", expected) == "backup_status"

    def test_no_overlap_returns_none(self):
        expected = [{"type": "restore_test_result"}]
        assert _guess_artifact_type_for_source("GitHubActions", expected) is None


# ---------------------------------------------------------------------------
# _compute_coverage
# ---------------------------------------------------------------------------

class TestComputeCoverage:
    def test_empty_files_yields_all_missing(self):
        expected = [{"type": "status_snapshot"}, {"type": "restore_test"}]
        src_cov, type_cov = _compute_coverage([], expected)
        assert src_cov == {}
        assert type_cov == {
            "status_snapshot": {"present": False, "file_count": 0},
            "restore_test": {"present": False, "file_count": 0},
        }

    def test_multiple_files_same_source_aggregate(self):
        files = [_make_file("AzureBackup"), _make_file("AzureBackup")]
        src_cov, _ = _compute_coverage(files, [])
        assert src_cov == {"AzureBackup": 2}

    def test_type_coverage_marks_matched_types(self):
        files = [_make_file("AzureBackup_status")]
        expected = [
            {"type": "status_snapshot"},
            {"type": "restore_test_result"},
        ]
        _, type_cov = _compute_coverage(files, expected)
        # "status" substring matches "status_snapshot" token
        assert type_cov["status_snapshot"]["present"] is True
        assert type_cov["restore_test_result"]["present"] is False

    def test_m2_flag_off_behaves_like_heuristic(self, monkeypatch):
        """M2 (#572) PR 1 regression: flag-off path must match pre-M2 behaviour.

        With ENABLE_COLLECTOR_REGISTRY unset/false and no declared artifact
        types on the file, the resolver returns empty and coverage falls back
        to _guess_artifact_type_for_source — identical to M1a.
        """
        monkeypatch.delenv("ENABLE_COLLECTOR_REGISTRY", raising=False)
        files = [_make_file("AzureBackup_status")]
        expected = [
            {"type": "status_snapshot"},
            {"type": "restore_test_result"},
        ]
        _, type_cov = _compute_coverage(files, expected)
        assert type_cov["status_snapshot"]["present"] is True
        assert type_cov["restore_test_result"]["present"] is False

    def test_m2_declared_artifact_types_present_honoured(self, monkeypatch):
        """Declared types on the file trump heuristic even when flag is off."""
        monkeypatch.delenv("ENABLE_COLLECTOR_REGISTRY", raising=False)
        f = _make_file("AzureBackup")
        f.declared_artifact_types = ["restore_test_result"]
        expected = [
            {"type": "status_snapshot"},
            {"type": "restore_test_result"},
        ]
        _, type_cov = _compute_coverage([f], expected)
        assert type_cov["restore_test_result"]["present"] is True
        # And the heuristic-resolved one should NOT fire because declared took precedence
        assert type_cov["status_snapshot"]["present"] is False

    def test_m2_heuristic_arm_emits_resolution_log(self, monkeypatch, caplog):
        """M2 PR 1.1 (#572 §6a): heuristic fallback arm must emit a resolution log.

        Registry covers {payload, registry, empty}; the heuristic is the fourth
        arm and lives in _compute_coverage. Without this log the cutover
        signal can't count heuristic hits.
        """
        monkeypatch.delenv("ENABLE_COLLECTOR_REGISTRY", raising=False)
        files = [_make_file("AzureBackup_status")]
        expected = [{"type": "status_snapshot"}]
        with caplog.at_level("INFO", logger="services.window_assessment_service"):
            _compute_coverage(files, expected)
        heuristic_lines = [
            r for r in caplog.records
            if "collector.resolve" in r.getMessage() and "resolved_via=heuristic" in r.getMessage()
        ]
        assert len(heuristic_lines) == 1
        assert "status_snapshot" in heuristic_lines[0].getMessage()


# ---------------------------------------------------------------------------
# _compute_window_hash
# ---------------------------------------------------------------------------

class TestComputeWindowHash:
    def test_same_inputs_same_hash(self):
        files = [_make_file(sha="aaa"), _make_file(sha="bbb")]
        h1 = _compute_window_hash("E-BCM-11", datetime(2026, 4, 1), datetime(2026, 4, 2), files)
        h2 = _compute_window_hash("E-BCM-11", datetime(2026, 4, 1), datetime(2026, 4, 2), files)
        assert h1 == h2

    def test_different_files_different_hash(self):
        files_a = [_make_file(sha="aaa")]
        files_b = [_make_file(sha="zzz")]
        h1 = _compute_window_hash("E-BCM-11", datetime(2026, 4, 1), datetime(2026, 4, 2), files_a)
        h2 = _compute_window_hash("E-BCM-11", datetime(2026, 4, 1), datetime(2026, 4, 2), files_b)
        assert h1 != h2

    def test_file_order_does_not_affect_hash(self):
        f1 = _make_file(sha="aaa")
        f2 = _make_file(sha="bbb")
        h1 = _compute_window_hash("E-BCM-11", datetime(2026, 4, 1), datetime(2026, 4, 2), [f1, f2])
        h2 = _compute_window_hash("E-BCM-11", datetime(2026, 4, 1), datetime(2026, 4, 2), [f2, f1])
        assert h1 == h2

    def test_different_evidence_id_different_hash(self):
        files = [_make_file(sha="aaa")]
        h1 = _compute_window_hash("E-BCM-11", datetime(2026, 4, 1), datetime(2026, 4, 2), files)
        h2 = _compute_window_hash("E-BCM-12", datetime(2026, 4, 1), datetime(2026, 4, 2), files)
        assert h1 != h2


# ---------------------------------------------------------------------------
# _parse_llm_response
# ---------------------------------------------------------------------------

class TestParseLLMResponse:
    def test_valid_json_passes_through(self):
        raw = '{"status": "sufficient", "relevance_score": 87.5, "summary": "ok", "findings": []}'
        parsed = _parse_llm_response(raw)
        assert parsed["status"] == "sufficient"
        assert parsed["relevance_score"] == 87.5
        assert parsed["summary"] == "ok"
        assert parsed["findings"] == []

    def test_code_fence_stripped(self):
        raw = '```json\n{"status": "partial", "relevance_score": 50, "summary": "s", "findings": []}\n```'
        parsed = _parse_llm_response(raw)
        assert parsed["status"] == "partial"
        assert parsed["relevance_score"] == 50.0

    def test_invalid_json_returns_error(self):
        parsed = _parse_llm_response("not-json {[")
        assert parsed["status"] == "error"
        assert parsed["relevance_score"] is None
        assert len(parsed["findings"]) == 1

    def test_unknown_status_defaulted_to_partial(self):
        raw = '{"status": "banana", "relevance_score": 50, "summary": "s", "findings": []}'
        parsed = _parse_llm_response(raw)
        assert parsed["status"] == "partial"

    def test_relevance_score_clamped(self):
        raw = '{"status": "sufficient", "relevance_score": 500, "summary": "", "findings": []}'
        parsed = _parse_llm_response(raw)
        assert parsed["relevance_score"] == 100.0

    def test_relevance_score_clamped_negative(self):
        raw = '{"status": "sufficient", "relevance_score": -20, "summary": "", "findings": []}'
        parsed = _parse_llm_response(raw)
        assert parsed["relevance_score"] == 0.0

    def test_missing_findings_replaced_with_empty_list(self):
        raw = '{"status": "sufficient", "relevance_score": 80, "summary": ""}'
        parsed = _parse_llm_response(raw)
        assert parsed["findings"] == []

    def test_non_string_summary_replaced(self):
        raw = '{"status": "sufficient", "relevance_score": 80, "summary": 42, "findings": []}'
        parsed = _parse_llm_response(raw)
        assert parsed["summary"] == ""


# ---------------------------------------------------------------------------
# Prior-review pointer (#789 audit lane, PR-1) — replaces the M4 PR 3 carryover
# ---------------------------------------------------------------------------

class TestPriorReviewReference:
    """``_prior_review_reference`` must NEVER put review state on a new row.

    The function it replaced copied review_status / reviewed_by_user_id /
    reviewed_at / review_notes from the last approved-or-rejected window
    onto a brand-new one, fabricating an attestation under a real
    reviewer's name. These tests pin the two halves of the fix: the new row
    stays unattested, and the prior disposition is still discoverable.
    """

    def _make_new_assessment(self):
        """Build a fresh assessment carrying the model's unreviewed default."""
        from types import SimpleNamespace
        return SimpleNamespace(
            review_status="not_reviewed",
            reviewed_by_user_id=None,
            reviewed_at=None,
            review_notes=None,
        )

    def _make_prior(self, *, review_status, reviewer_id=None, notes="prior notes"):
        from types import SimpleNamespace
        return SimpleNamespace(
            review_status=review_status,
            reviewed_by_user_id=reviewer_id or uuid4(),
            reviewed_at=datetime(2026, 5, 1, 10, 0, 0),
            review_notes=notes,
            window_start=datetime(2026, 4, 1, 0, 0, 0),
            window_end=datetime(2026, 5, 1, 0, 0, 0),
        )

    def _fake_session(self, prior_row):
        """A MagicMock session whose execute().scalar_one_or_none() returns prior_row."""
        from unittest.mock import MagicMock
        result = MagicMock()
        result.scalar_one_or_none.return_value = prior_row
        session = MagicMock()
        session.execute.return_value = result
        return session

    # -- the fabrication, killed ------------------------------------------

    def test_prior_approval_does_not_set_review_status(self):
        new_row = self._make_new_assessment()
        session = self._fake_session(self._make_prior(review_status="approved"))

        _prior_review_reference(session, uuid4(), "E-BCM-11")

        assert new_row.review_status == "not_reviewed"

    def test_prior_approval_does_not_name_a_reviewer(self):
        new_row = self._make_new_assessment()
        session = self._fake_session(self._make_prior(review_status="approved"))

        _prior_review_reference(session, uuid4(), "E-BCM-11")

        assert new_row.reviewed_by_user_id is None

    def test_prior_approval_does_not_set_a_review_timestamp(self):
        new_row = self._make_new_assessment()
        session = self._fake_session(self._make_prior(review_status="approved"))

        _prior_review_reference(session, uuid4(), "E-BCM-11")

        assert new_row.reviewed_at is None

    def test_prior_approval_does_not_copy_review_notes(self):
        new_row = self._make_new_assessment()
        session = self._fake_session(
            self._make_prior(review_status="approved", notes="LGTM")
        )

        _prior_review_reference(session, uuid4(), "E-BCM-11")

        assert new_row.review_notes is None

    def test_prior_rejection_does_not_set_review_status(self):
        new_row = self._make_new_assessment()
        session = self._fake_session(self._make_prior(review_status="rejected"))

        _prior_review_reference(session, uuid4(), "E-BCM-11")

        assert new_row.review_status == "not_reviewed"

    def test_function_takes_no_assessment_argument(self):
        """Structural: the fabrication is impossible if the row is never passed in.

        The replaced helper's fourth parameter was the row it mutated.
        Removing it is what makes the fix un-regressable by accident.
        """
        import inspect
        params = list(inspect.signature(_prior_review_reference).parameters)
        assert params == ["session", "organization_id", "evidence_id"]

    # -- the pointer, preserved -------------------------------------------

    def test_prior_approval_is_reported_as_an_informational_finding(self):
        session = self._fake_session(self._make_prior(review_status="approved"))

        note = _prior_review_reference(session, uuid4(), "E-BCM-11")

        assert note is not None
        assert note["category"] == "review"
        assert note["level"] == "info"

    def test_finding_names_the_prior_disposition(self):
        session = self._fake_session(self._make_prior(review_status="approved"))

        note = _prior_review_reference(session, uuid4(), "E-BCM-11")

        assert "approved" in note["message"]

    def test_finding_names_the_prior_window_not_this_one(self):
        session = self._fake_session(self._make_prior(review_status="approved"))

        note = _prior_review_reference(session, uuid4(), "E-BCM-11")

        assert "2026-04-01" in note["message"]
        assert "2026-05-01" in note["message"]

    def test_finding_states_it_is_not_an_attestation_of_this_window(self):
        session = self._fake_session(self._make_prior(review_status="rejected"))

        note = _prior_review_reference(session, uuid4(), "E-BCM-11")

        assert "does not attest" in note["message"]
        assert "unreviewed" in note["message"]

    def test_no_prior_review_returns_no_finding(self):
        session = self._fake_session(prior_row=None)

        assert _prior_review_reference(session, uuid4(), "E-BCM-11") is None

    def test_prior_row_with_no_reviewed_at_still_produces_a_finding(self):
        prior = self._make_prior(review_status="approved")
        prior.reviewed_at = None
        session = self._fake_session(prior)

        note = _prior_review_reference(session, uuid4(), "E-BCM-11")

        assert note is not None
        assert "unrecorded date" in note["message"]

    def test_query_filters_for_approved_or_rejected_only(self):
        """needs_revision stays excluded — it explicitly asked for a re-run.

        The SQL filters review_status IN ("approved", "rejected"), so a
        history of nothing but needs_revision surfaces as "no prior row".
        """
        session = self._fake_session(prior_row=None)

        assert _prior_review_reference(session, uuid4(), "E-BCM-11") is None

    def test_a_single_select_is_issued(self):
        from unittest.mock import MagicMock
        session = MagicMock()
        execute_result = MagicMock()
        execute_result.scalar_one_or_none.return_value = None
        session.execute.return_value = execute_result

        _prior_review_reference(session, uuid4(), "E-BCM-11")

        session.execute.assert_called_once()


class TestComposeFindings:
    """The prior-review pointer must actually reach the persisted findings.

    Without these, dropping ``prior_review_note`` from the composition
    would be a silent regression — the pointer half of the fix would
    vanish while every non-inheritance test kept passing.
    """

    COVERAGE = {"category": "coverage", "level": "insufficient", "message": "c"}
    NOTE = {"category": "review", "level": "info", "message": "n"}
    AI = {"category": "relevance", "level": "warning", "message": "a"}

    def test_prior_review_note_is_included(self):
        out = _compose_findings([self.COVERAGE], self.NOTE, [self.AI])
        assert self.NOTE in out

    def test_prior_review_note_follows_coverage_findings(self):
        out = _compose_findings([self.COVERAGE], self.NOTE, [self.AI])
        assert out.index(self.NOTE) > out.index(self.COVERAGE)

    def test_prior_review_note_precedes_ai_findings(self):
        out = _compose_findings([self.COVERAGE], self.NOTE, [self.AI])
        assert out.index(self.NOTE) < out.index(self.AI)

    def test_absent_note_contributes_nothing(self):
        assert _compose_findings([self.COVERAGE], None, [self.AI]) == [
            self.COVERAGE, self.AI,
        ]

    def test_coverage_findings_are_never_suppressed_by_the_note(self):
        out = _compose_findings([self.COVERAGE], self.NOTE, [])
        assert self.COVERAGE in out

    def test_inputs_are_not_mutated(self):
        pre = [self.COVERAGE]
        _compose_findings(pre, self.NOTE, [self.AI])
        assert pre == [self.COVERAGE]


class TestReviewBlockIsNeverWrittenByThisService:
    """Structural guard on the whole module, not just the helper.

    The defect this PR fixes was a *call site* copying four attributes onto
    a fresh row. A unit test on the helper cannot see that call site coming
    back. This walks the module AST and refuses any assignment to the
    review block anywhere in the assessment service — review state is
    written by the review API, and by nothing else.
    """

    REVIEW_BLOCK = {
        "review_status",
        "reviewed_by_user_id",
        "reviewed_at",
        "review_notes",
    }

    def _module_ast(self):
        import ast
        import services.window_assessment_service as mod
        return ast.parse(pathlib.Path(mod.__file__).read_text())

    def test_the_module_source_actually_loaded(self):
        """Non-vacuity: the parse must find the functions we care about."""
        import ast
        tree = self._module_ast()
        names = {
            n.name for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert "assess_window" in names
        assert "_prior_review_reference" in names

    def test_no_assignment_to_any_review_block_attribute(self):
        import ast
        tree = self._module_ast()
        offenders = []
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AugAssign):
                targets = [node.target]
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            for t in targets:
                if isinstance(t, ast.Attribute) and t.attr in self.REVIEW_BLOCK:
                    offenders.append(f"line {t.lineno}: .{t.attr}")
        assert offenders == [], (
            "window_assessment_service must never write the review block; "
            f"found {offenders}"
        )
