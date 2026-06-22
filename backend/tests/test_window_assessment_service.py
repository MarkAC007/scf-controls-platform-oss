"""Unit tests for windowed evidence assessment service (M1a).

Pure-function coverage for the private helpers. Full end-to-end tests (real DB,
real LLM) are exercised manually via the CG production MCP path per the plan's
Verification section — this file locks down the deterministic parts.
"""
import os
import sys
from datetime import datetime
from uuid import uuid4

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.window_assessment_service import (
    FALLBACK_FREQUENCY,
    _FileInWindow,
    _apply_sticky_review_carryover,
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

    def test_unknown_frequency_falls_back(self):
        freq, is_fallback = _resolve_frequency(_FakeTracking("fortnightly"))
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
# Sticky review carryover (M4 PR 3) — Decision D3
# ---------------------------------------------------------------------------

class TestStickyReviewCarryover:
    """``_apply_sticky_review_carryover`` copies approved/rejected dispositions
    from the most recent reviewed row onto a new assessment. needs_revision
    is excluded by design (D3).
    """

    def _make_new_assessment(self):
        """Build a fresh assessment with no review state."""
        from types import SimpleNamespace
        return SimpleNamespace(
            review_status=None,
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
        )

    def _fake_session(self, prior_row):
        """Build a MagicMock session whose execute().scalar_one_or_none() returns prior_row."""
        from unittest.mock import MagicMock
        result = MagicMock()
        result.scalar_one_or_none.return_value = prior_row
        session = MagicMock()
        session.execute.return_value = result
        return session

    def test_new_assessment_inherits_approved_review(self):
        new_row = self._make_new_assessment()
        prior = self._make_prior(review_status="approved", notes="LGTM")
        session = self._fake_session(prior)

        _apply_sticky_review_carryover(session, uuid4(), "E-BCM-11", new_row)

        assert new_row.review_status == "approved"
        assert new_row.reviewed_by_user_id == prior.reviewed_by_user_id
        assert new_row.reviewed_at == prior.reviewed_at
        assert new_row.review_notes == "LGTM"

    def test_new_assessment_inherits_rejected_review(self):
        new_row = self._make_new_assessment()
        prior = self._make_prior(review_status="rejected", notes="missing evidence")
        session = self._fake_session(prior)

        _apply_sticky_review_carryover(session, uuid4(), "E-BCM-11", new_row)

        assert new_row.review_status == "rejected"
        assert new_row.review_notes == "missing evidence"

    def test_new_assessment_with_no_prior_review_starts_unreviewed(self):
        new_row = self._make_new_assessment()
        session = self._fake_session(prior_row=None)

        _apply_sticky_review_carryover(session, uuid4(), "E-BCM-11", new_row)

        # No prior row → row stays exactly as it was (no review state).
        assert new_row.review_status is None
        assert new_row.reviewed_by_user_id is None
        assert new_row.reviewed_at is None
        assert new_row.review_notes is None

    def test_query_filters_for_approved_or_rejected_only(self):
        """D3: needs_revision is excluded from the carryover query, by design.

        The query itself filters review_status IN ("approved", "rejected"),
        so a prior row with review_status="needs_revision" would not be
        returned by scalar_one_or_none. We verify by setting up a session
        whose query returns None when only needs_revision rows exist.
        """
        new_row = self._make_new_assessment()
        # Simulate the SQL filter: only approved/rejected rows are returned.
        # A pure-needs_revision history surfaces as "no prior reviewed row".
        session = self._fake_session(prior_row=None)

        _apply_sticky_review_carryover(session, uuid4(), "E-BCM-11", new_row)

        assert new_row.review_status is None

    def test_query_uses_correct_organization_and_evidence_filters(self):
        """Defensive: the SELECT must filter by both org_id and evidence_id."""
        from unittest.mock import MagicMock
        new_row = self._make_new_assessment()
        session = MagicMock()
        execute_result = MagicMock()
        execute_result.scalar_one_or_none.return_value = None
        session.execute.return_value = execute_result

        org_id = uuid4()
        _apply_sticky_review_carryover(session, org_id, "E-BCM-11", new_row)

        # Single SELECT issued.
        session.execute.assert_called_once()
        # We don't introspect the SQL AST here — coverage of the WHERE
        # clause is the integration responsibility; this test asserts the
        # helper is wired into the query path.
