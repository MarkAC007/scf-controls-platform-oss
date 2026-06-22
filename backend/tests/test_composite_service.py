"""Unit tests for the ControlAssessmentComposite rollup service (M3, #575, PR 1).

Covers ``_compute_composite`` (ISC-7..11), the dispatcher's terminal-status
whitelist (ISC-13), and the idempotency-key short-circuit (ISC-14). DB-bound
SQL helpers are monkeypatched so the suite stays fast and dependency-free.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import composite_service
from services.composite_service import (
    CURRENT_COMPUTATION_VERSION,
    TERMINAL_WINDOW_STATUSES,
    _after_commit_handler,
    _before_flush_handler,
    _compute_composite,
    _stash_pending,
    _worst_of,
)


# ---------------------------------------------------------------------------
# Helpers — build EvidenceWindowAssessment-like fakes
# ---------------------------------------------------------------------------

def _window(
    evidence_id: str,
    status: str,
    relevance: Optional[float] = None,
    artifact_type_coverage: Optional[Dict[str, Any]] = None,
    window_end: Optional[datetime] = None,
    frequency_used: str = "monthly",
    review_status: Optional[str] = None,
) -> SimpleNamespace:
    """Lightweight stand-in for EvidenceWindowAssessment ORM rows."""
    return SimpleNamespace(
        id=uuid4(),
        evidence_id=evidence_id,
        status=status,
        relevance_score=Decimal(str(relevance)) if relevance is not None else None,
        artifact_type_coverage=artifact_type_coverage or {},
        window_end=window_end or datetime(2026, 5, 1, 12, 0, 0),
        assessed_at=datetime(2026, 5, 1, 12, 0, 0),
        frequency_used=frequency_used,
        review_status=review_status,
    )


@pytest.fixture
def patch_db_helpers(monkeypatch):
    """Replace the four DB query helpers with monkeypatchable returns.

    Returns a setter dict the test populates with the desired return values.
    """
    state: Dict[str, Any] = {
        "evidence_ids": [],
        "required_artifact_types": [],
        "latest_per_ev": {},
        "latest_file_per_ev": {},
    }

    def _evidence_ids_for_control(session, scf_id):
        return list(state["evidence_ids"])

    def _required_artifact_types_for_control(session, scf_id):
        return list(state["required_artifact_types"])

    def _latest_window_per_evidence(session, organization_id, evidence_ids):
        return dict(state["latest_per_ev"])

    def _latest_file_at_per_evidence(session, organization_id, evidence_ids):
        return dict(state["latest_file_per_ev"])

    monkeypatch.setattr(
        composite_service, "_evidence_ids_for_control",
        _evidence_ids_for_control,
    )
    monkeypatch.setattr(
        composite_service, "_required_artifact_types_for_control",
        _required_artifact_types_for_control,
    )
    monkeypatch.setattr(
        composite_service, "_latest_window_per_evidence",
        _latest_window_per_evidence,
    )
    monkeypatch.setattr(
        composite_service, "_latest_file_at_per_evidence",
        _latest_file_at_per_evidence,
    )
    return state


# ---------------------------------------------------------------------------
# _worst_of helper — ISC-7 ordering
# ---------------------------------------------------------------------------

class TestWorstOf:
    def test_pure_sufficient(self):
        assert _worst_of(["sufficient", "sufficient"]) == "sufficient"

    def test_partial_beats_sufficient(self):
        assert _worst_of(["sufficient", "partial"]) == "partial"

    def test_insufficient_dominates(self):
        assert _worst_of(["sufficient", "partial", "insufficient"]) == "insufficient"

    def test_insufficient_dominates_reverse_order(self):
        assert _worst_of(["insufficient", "partial", "sufficient"]) == "insufficient"

    def test_unknown_status_ignored(self):
        # error / pending should not affect rollup ordering
        assert _worst_of(["sufficient", "error"]) == "sufficient"

    def test_only_unknown_falls_back_to_insufficient(self):
        assert _worst_of(["error"]) == "insufficient"


# ---------------------------------------------------------------------------
# Worst-of status — 8 permutations covering each ISC-4 enum value
# ---------------------------------------------------------------------------

ORG = uuid4()


class TestComputeStatusPermutations:
    def test_no_evidence_when_no_mapping(self, patch_db_helpers):
        # ev_ids empty -> no_evidence per ISC-7 step 1
        out = _compute_composite(MagicMock(), ORG, "BCD-11")
        assert out["composite_status"] == "no_evidence"
        assert out["composite_score"] is None
        assert out["included_evidence_ids"] == []

    def test_no_evidence_when_mapping_present_but_no_windows(self, patch_db_helpers):
        # ev_ids present but no windows -> no_evidence with missing_window gaps
        patch_db_helpers["evidence_ids"] = ["E-1", "E-2", "E-3"]
        patch_db_helpers["latest_per_ev"] = {}
        out = _compute_composite(MagicMock(), ORG, "BCD-11")
        assert out["composite_status"] == "no_evidence"
        gap_reasons = {g["reason"] for g in out["mandatory_gaps"]}
        assert gap_reasons == {"missing_window"}

    def test_all_sufficient(self, patch_db_helpers):
        patch_db_helpers["evidence_ids"] = ["E-1", "E-2", "E-3"]
        patch_db_helpers["latest_per_ev"] = {
            "E-1": _window("E-1", "sufficient", 90.0),
            "E-2": _window("E-2", "sufficient", 80.0),
            "E-3": _window("E-3", "sufficient", 100.0),
        }
        out = _compute_composite(MagicMock(), ORG, "BCD-11")
        assert out["composite_status"] == "sufficient"
        assert out["composite_score"] == Decimal("90.00")

    def test_mixed_partial_sufficient(self, patch_db_helpers):
        patch_db_helpers["evidence_ids"] = ["E-1", "E-2"]
        patch_db_helpers["latest_per_ev"] = {
            "E-1": _window("E-1", "sufficient", 90.0),
            "E-2": _window("E-2", "partial", 50.0),
        }
        out = _compute_composite(MagicMock(), ORG, "BCD-11")
        assert out["composite_status"] == "partial"
        assert out["composite_score"] == Decimal("70.00")

    def test_one_insufficient_dominates(self, patch_db_helpers):
        # ISC-7 + ISC-9: one insufficient window forces composite insufficient.
        patch_db_helpers["evidence_ids"] = ["E-1", "E-2", "E-3"]
        patch_db_helpers["latest_per_ev"] = {
            "E-1": _window("E-1", "sufficient", 90.0),
            "E-2": _window("E-2", "sufficient", 95.0),
            "E-3": _window("E-3", "insufficient", 10.0),
        }
        out = _compute_composite(MagicMock(), ORG, "BCD-11")
        assert out["composite_status"] == "insufficient"
        # window_insufficient gap is recorded
        reasons = [g["reason"] for g in out["mandatory_gaps"]]
        assert "window_insufficient" in reasons

    def test_mixed_with_missing_window(self, patch_db_helpers):
        # E-3 is mapped but has no window -> missing_window gap, status forced insufficient.
        patch_db_helpers["evidence_ids"] = ["E-1", "E-2", "E-3"]
        patch_db_helpers["latest_per_ev"] = {
            "E-1": _window("E-1", "sufficient", 90.0),
            "E-2": _window("E-2", "sufficient", 90.0),
            # E-3 absent
        }
        out = _compute_composite(MagicMock(), ORG, "BCD-11")
        assert out["composite_status"] == "insufficient"
        gap_reasons = {(g["evidence_id"], g["reason"]) for g in out["mandatory_gaps"]}
        assert ("E-3", "missing_window") in gap_reasons

    def test_all_insufficient(self, patch_db_helpers):
        patch_db_helpers["evidence_ids"] = ["E-1", "E-2"]
        patch_db_helpers["latest_per_ev"] = {
            "E-1": _window("E-1", "insufficient", 5.0),
            "E-2": _window("E-2", "insufficient", 10.0),
        }
        out = _compute_composite(MagicMock(), ORG, "BCD-11")
        assert out["composite_status"] == "insufficient"

    def test_insufficient_sample_status_in_window_treated_as_insufficient(self, patch_db_helpers):
        # ISC-11: composite-level guard, but per-window insufficient_sample
        # contribution maps to insufficient at worst-of time.
        patch_db_helpers["evidence_ids"] = ["E-1", "E-2"]
        patch_db_helpers["latest_per_ev"] = {
            "E-1": _window("E-1", "sufficient", 80.0),
            "E-2": _window("E-2", "insufficient_sample", None),
        }
        out = _compute_composite(MagicMock(), ORG, "BCD-11")
        assert out["composite_status"] == "insufficient"


# ---------------------------------------------------------------------------
# Score formula — ISC-8
# ---------------------------------------------------------------------------

class TestScoreFormula:
    def test_uniform_weight_average(self, patch_db_helpers):
        patch_db_helpers["evidence_ids"] = ["E-1", "E-2", "E-3"]
        patch_db_helpers["latest_per_ev"] = {
            "E-1": _window("E-1", "sufficient", 60.0),
            "E-2": _window("E-2", "sufficient", 80.0),
            "E-3": _window("E-3", "sufficient", 100.0),
        }
        out = _compute_composite(MagicMock(), ORG, "BCD-11")
        # (60 + 80 + 100) / 3 = 80.0
        assert out["composite_score"] == Decimal("80.00")

    def test_error_window_excluded_from_numerator_and_denominator(self, patch_db_helpers):
        # Three evidence: two scoring sufficient, one in error.
        # error excluded -> score = (80 + 100) / 2 = 90.0
        patch_db_helpers["evidence_ids"] = ["E-1", "E-2", "E-3"]
        # Note _latest_window_per_evidence filters out errors in production,
        # but our patch returns whatever we put in; the function still has
        # logic to handle "error" status defensively.
        patch_db_helpers["latest_per_ev"] = {
            "E-1": _window("E-1", "sufficient", 80.0),
            "E-2": _window("E-2", "sufficient", 100.0),
            "E-3": _window("E-3", "error", None),
        }
        out = _compute_composite(MagicMock(), ORG, "BCD-11")
        # error is excluded from worst-of (so doesn't dominate) and excluded
        # from numerator/denominator
        assert out["composite_score"] == Decimal("90.00")

    def test_relevance_none_contributes_zero(self, patch_db_helpers):
        patch_db_helpers["evidence_ids"] = ["E-1", "E-2"]
        patch_db_helpers["latest_per_ev"] = {
            "E-1": _window("E-1", "sufficient", 100.0),
            "E-2": _window("E-2", "partial", None),  # no relevance
        }
        out = _compute_composite(MagicMock(), ORG, "BCD-11")
        # (100 + 0) / 2 = 50.0
        assert out["composite_score"] == Decimal("50.00")


# ---------------------------------------------------------------------------
# Mandatory artifact gaps — ISC-9
# ---------------------------------------------------------------------------

class TestMandatoryGapsDominance:
    def test_missing_mandatory_artifact_forces_insufficient(self, patch_db_helpers):
        patch_db_helpers["evidence_ids"] = ["E-1"]
        patch_db_helpers["required_artifact_types"] = [
            {"type": "restore_test_result", "mandatory": True, "weight": "high"},
        ]
        patch_db_helpers["latest_per_ev"] = {
            "E-1": _window(
                "E-1", "sufficient", 95.0,
                artifact_type_coverage={
                    "restore_test_result": {"present": False},
                },
            ),
        }
        out = _compute_composite(MagicMock(), ORG, "BCD-11")
        # Even though window is sufficient with high score, missing mandatory
        # artifact type forces insufficient.
        assert out["composite_status"] == "insufficient"
        gap = [g for g in out["mandatory_gaps"] if g.get("artifact_type") == "restore_test_result"]
        assert len(gap) == 1
        assert gap[0]["reason"] == "missing"
        assert gap[0]["evidence_id"] == "E-1"

    def test_present_mandatory_artifact_does_not_force_insufficient(self, patch_db_helpers):
        patch_db_helpers["evidence_ids"] = ["E-1"]
        patch_db_helpers["required_artifact_types"] = [
            {"type": "restore_test_result", "mandatory": True},
        ]
        patch_db_helpers["latest_per_ev"] = {
            "E-1": _window(
                "E-1", "sufficient", 95.0,
                artifact_type_coverage={
                    "restore_test_result": {"present": True},
                },
            ),
        }
        out = _compute_composite(MagicMock(), ORG, "BCD-11")
        assert out["composite_status"] == "sufficient"
        # No artifact-type gap entries
        artifact_gaps = [g for g in out["mandatory_gaps"] if g.get("artifact_type")]
        assert artifact_gaps == []

    def test_non_mandatory_missing_does_not_force_insufficient(self, patch_db_helpers):
        patch_db_helpers["evidence_ids"] = ["E-1"]
        patch_db_helpers["required_artifact_types"] = [
            {"type": "policy_doc", "mandatory": False},
        ]
        patch_db_helpers["latest_per_ev"] = {
            "E-1": _window(
                "E-1", "sufficient", 80.0,
                artifact_type_coverage={"policy_doc": {"present": False}},
            ),
        }
        out = _compute_composite(MagicMock(), ORG, "BCD-11")
        # Non-mandatory missing -> stays sufficient
        assert out["composite_status"] == "sufficient"


# ---------------------------------------------------------------------------
# Sample-size guard — ISC-11
# ---------------------------------------------------------------------------

class TestSampleSizeGuard:
    def test_below_floor_triggers_insufficient_sample(self, patch_db_helpers):
        # 6 mapped evidence, only 1 has a window; floor = ceil(6/3) = 2
        patch_db_helpers["evidence_ids"] = [f"E-{i}" for i in range(1, 7)]
        patch_db_helpers["latest_per_ev"] = {
            "E-1": _window("E-1", "sufficient", 90.0),
        }
        out = _compute_composite(MagicMock(), ORG, "BCD-11")
        assert out["composite_status"] == "insufficient_sample"
        assert out["composite_score"] is None

    def test_at_floor_proceeds_to_normal_rollup(self, patch_db_helpers):
        # 3 mapped evidence, 1 with window; floor = ceil(3/3) = 1
        patch_db_helpers["evidence_ids"] = ["E-1", "E-2", "E-3"]
        patch_db_helpers["latest_per_ev"] = {
            "E-1": _window("E-1", "sufficient", 90.0),
        }
        out = _compute_composite(MagicMock(), ORG, "BCD-11")
        # Above floor of 1 — but missing windows for E-2, E-3 force insufficient
        assert out["composite_status"] == "insufficient"

    def test_single_evidence_floor_is_one(self, patch_db_helpers):
        # One mapped evidence with one window — floor = 1, score = 100
        patch_db_helpers["evidence_ids"] = ["E-1"]
        patch_db_helpers["latest_per_ev"] = {
            "E-1": _window("E-1", "sufficient", 100.0),
        }
        out = _compute_composite(MagicMock(), ORG, "BCD-11")
        assert out["composite_status"] == "sufficient"
        assert out["composite_score"] == Decimal("100.00")


# ---------------------------------------------------------------------------
# Stale window handling — ISC-10
# ---------------------------------------------------------------------------

class TestStaleWindow:
    def test_stale_window_halves_relevance_and_adds_gap(self, patch_db_helpers):
        # monthly cadence -> staleness 35 days -> 2x = 70 days. File from 80
        # days ago is stale.
        patch_db_helpers["evidence_ids"] = ["E-1"]
        patch_db_helpers["latest_per_ev"] = {
            "E-1": _window("E-1", "sufficient", 100.0, frequency_used="monthly"),
        }
        patch_db_helpers["latest_file_per_ev"] = {
            "E-1": datetime.utcnow() - timedelta(days=80),
        }
        out = _compute_composite(MagicMock(), ORG, "BCD-11")
        # 100 * 0.5 = 50
        assert out["composite_score"] == Decimal("50.00")
        stale_gaps = [g for g in out["mandatory_gaps"] if g["reason"] == "stale"]
        assert len(stale_gaps) == 1
        assert stale_gaps[0]["evidence_id"] == "E-1"

    def test_fresh_window_full_relevance(self, patch_db_helpers):
        patch_db_helpers["evidence_ids"] = ["E-1"]
        patch_db_helpers["latest_per_ev"] = {
            "E-1": _window("E-1", "sufficient", 100.0, frequency_used="monthly"),
        }
        patch_db_helpers["latest_file_per_ev"] = {
            "E-1": datetime.utcnow() - timedelta(days=10),
        }
        out = _compute_composite(MagicMock(), ORG, "BCD-11")
        assert out["composite_score"] == Decimal("100.00")
        assert not [g for g in out["mandatory_gaps"] if g["reason"] == "stale"]


# ---------------------------------------------------------------------------
# Missing window contribution — ISC-10
# ---------------------------------------------------------------------------

class TestMissingWindow:
    def test_missing_window_marks_insufficient_and_records_gap(self, patch_db_helpers):
        patch_db_helpers["evidence_ids"] = ["E-1", "E-2"]
        patch_db_helpers["latest_per_ev"] = {
            "E-1": _window("E-1", "sufficient", 90.0),
            # E-2 missing
        }
        out = _compute_composite(MagicMock(), ORG, "BCD-11")
        assert out["composite_status"] == "insufficient"
        gaps = [g for g in out["mandatory_gaps"] if g["reason"] == "missing_window"]
        assert any(g["evidence_id"] == "E-2" for g in gaps)


# ---------------------------------------------------------------------------
# Idempotency-key short-circuit — ISC-14
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_short_circuit_when_claim_fails(self, monkeypatch):
        """When _claim_idempotency returns False, the task returns 'deduplicated' without touching DB."""
        monkeypatch.setattr(composite_service, "_claim_idempotency", lambda *a, **k: False)

        called_session = {"opened": False}

        def _fake_get_session():
            called_session["opened"] = True
            return MagicMock()

        monkeypatch.setattr(composite_service, "_get_sync_session", _fake_get_session)

        # Celery's shared_task with bind=True wraps the function in a Task
        # subclass; .run() on the task does NOT auto-inject self. We invoke
        # the bound runtime via .apply(args=...) which routes through Celery's
        # eager execution path and sets up self.request properly.
        result = composite_service.recompute_control_composite_task.apply(
            kwargs={
                "organization_id": str(uuid4()),
                "scf_id": "BCD-11",
                "trigger_event": "after_commit",
            },
        ).get()
        assert result["status"] == "deduplicated"
        assert called_session["opened"] is False, "session should not open on dedup hit"


# ---------------------------------------------------------------------------
# Dispatcher — terminal-status whitelist (ISC-13)
# ---------------------------------------------------------------------------

class TestDispatcherWhitelist:
    def _make_session_with_window(self, *, new_status, prior_status=None, in_dirty=False):
        """Build a fake Session-like object with a fake EvidenceWindowAssessment in new/dirty."""
        from models import EvidenceWindowAssessment

        ewa = EvidenceWindowAssessment(
            organization_id=uuid4(),
            evidence_id="E-1",
            status=new_status,
            window_start=datetime(2026, 1, 1),
            window_end=datetime(2026, 2, 1),
            frequency_used="monthly",
        )

        session = MagicMock()
        session.info = {}
        if in_dirty:
            session.new = []
            session.dirty = [ewa]
        else:
            session.new = [ewa]
            session.dirty = []
        return session, ewa, prior_status

    def test_insert_with_terminal_status_fires(self):
        session, ewa, _ = self._make_session_with_window(new_status="sufficient")
        _before_flush_handler(session, None, None)
        pending = session.info["_composite_pending"]
        assert len(pending) == 1
        assert pending[0][1] == "E-1"
        assert pending[0][2] == "sufficient"

    def test_insert_with_pending_status_does_not_fire(self):
        session, _, _ = self._make_session_with_window(new_status="pending")
        _before_flush_handler(session, None, None)
        pending = session.info.get("_composite_pending", [])
        assert pending == []

    def test_insert_with_processing_status_does_not_fire(self):
        # ISC-13: pending->processing transitions never fire
        session, _, _ = self._make_session_with_window(new_status="processing")
        _before_flush_handler(session, None, None)
        pending = session.info.get("_composite_pending", [])
        assert pending == []

    def test_insert_with_error_status_does_not_fire(self):
        # ISC-13: error transitions don't inform composites
        session, _, _ = self._make_session_with_window(new_status="error")
        _before_flush_handler(session, None, None)
        pending = session.info.get("_composite_pending", [])
        assert pending == []

    def test_update_pending_to_terminal_fires(self, monkeypatch):
        # Update path uses get_history to discover prior status.
        from sqlalchemy.orm import attributes as orm_attributes

        class _FakeHistory:
            def __init__(self, prior):
                self.deleted = [prior] if prior is not None else []

        session, ewa, _ = self._make_session_with_window(
            new_status="sufficient", in_dirty=True,
        )
        # M4 PR 3: dispatcher now also reads review_status history. Stub returns
        # an empty history for that attr so only the status-transition path fires.
        def _by_attr(obj, attr):
            return _FakeHistory("pending") if attr == "status" else _FakeHistory(None)
        monkeypatch.setattr(orm_attributes, "get_history", _by_attr)
        _before_flush_handler(session, None, None)
        pending = session.info["_composite_pending"]
        assert len(pending) == 1

    def test_update_error_to_terminal_does_not_fire(self, monkeypatch):
        # ISC-13: error -> any does NOT fire
        from sqlalchemy.orm import attributes as orm_attributes

        class _FakeHistory:
            def __init__(self, prior):
                self.deleted = [prior] if prior is not None else []

        session, ewa, _ = self._make_session_with_window(
            new_status="sufficient", in_dirty=True,
        )
        def _by_attr(obj, attr):
            return _FakeHistory("error") if attr == "status" else _FakeHistory(None)
        monkeypatch.setattr(orm_attributes, "get_history", _by_attr)
        _before_flush_handler(session, None, None)
        pending = session.info.get("_composite_pending", [])
        assert pending == []

    def test_update_terminal_to_terminal_does_not_re_fire(self, monkeypatch):
        # Avoid recompute storms: flipping between terminals doesn't re-fire.
        from sqlalchemy.orm import attributes as orm_attributes

        class _FakeHistory:
            def __init__(self, prior):
                self.deleted = [prior] if prior is not None else []

        session, _, _ = self._make_session_with_window(
            new_status="insufficient", in_dirty=True,
        )
        def _by_attr(obj, attr):
            return _FakeHistory("sufficient") if attr == "status" else _FakeHistory(None)
        monkeypatch.setattr(orm_attributes, "get_history", _by_attr)
        _before_flush_handler(session, None, None)
        pending = session.info.get("_composite_pending", [])
        assert pending == []

    def test_update_with_no_status_change_does_not_fire(self, monkeypatch):
        # Dirty row but status itself didn't change — get_history returns empty deleted.
        from sqlalchemy.orm import attributes as orm_attributes

        class _FakeHistory:
            def __init__(self, deleted=None):
                self.deleted = deleted or []

        session, _, _ = self._make_session_with_window(
            new_status="sufficient", in_dirty=True,
        )
        monkeypatch.setattr(
            orm_attributes, "get_history",
            lambda obj, attr: _FakeHistory(),
        )
        _before_flush_handler(session, None, None)
        pending = session.info.get("_composite_pending", [])
        assert pending == []

    def test_after_commit_clears_pending_even_when_circuit_open(self, monkeypatch):
        # Defensive: stale entries should not leak into next transaction.
        session = MagicMock()
        session.info = {"_composite_pending": [(uuid4(), "E-1", "sufficient")]}
        monkeypatch.setattr(composite_service, "_is_circuit_open", lambda: True)
        _after_commit_handler(session)
        assert session.info["_composite_pending"] == []


# ---------------------------------------------------------------------------
# Review-status gating (M4 PR 3) — D1, D2, D4
# ---------------------------------------------------------------------------


class TestReviewStatusGating:
    """Per-window reviewer disposition overrides AI signal in composite math.

    Decisions:
      D1 — rejected forces insufficient + zero relevance + gap "review_rejected"
      D2 — needs_revision forces insufficient + zero relevance + gap "pending_revision"
      D4 — approved is baseline-equivalent to not_reviewed / None
    """

    def test_rejected_window_forces_insufficient_with_zero_relevance(self, patch_db_helpers):
        patch_db_helpers["evidence_ids"] = ["E-1"]
        patch_db_helpers["latest_per_ev"] = {
            "E-1": _window("E-1", "sufficient", 90.0, review_status="rejected"),
        }
        out = _compute_composite(MagicMock(), ORG, "BCD-11")
        # AI said sufficient with 90; reviewer override pulls down to insufficient.
        assert out["composite_status"] == "insufficient"
        # Score is the weighted average of zero contribution / non-zero denom.
        assert out["composite_score"] == Decimal("0.00")
        # Window still in provenance for audit — D1 keeps the row visible.
        assert len(out["included_window_ids"]) == 1
        # Gap reason is the review-specific one, not missing/stale.
        reasons = [g.get("reason") for g in out["mandatory_gaps"]]
        assert "review_rejected" in reasons

    def test_needs_revision_window_forces_insufficient_with_zero_relevance(self, patch_db_helpers):
        patch_db_helpers["evidence_ids"] = ["E-1"]
        patch_db_helpers["latest_per_ev"] = {
            "E-1": _window("E-1", "sufficient", 85.0, review_status="needs_revision"),
        }
        out = _compute_composite(MagicMock(), ORG, "BCD-11")
        assert out["composite_status"] == "insufficient"
        assert out["composite_score"] == Decimal("0.00")
        assert len(out["included_window_ids"]) == 1
        reasons = [g.get("reason") for g in out["mandatory_gaps"]]
        # Distinct from rejected so UI can tell "AI wrong" from "wants re-run".
        assert "pending_revision" in reasons
        assert "review_rejected" not in reasons

    def test_approved_window_baseline_equivalent_to_not_reviewed(self, patch_db_helpers):
        patch_db_helpers["evidence_ids"] = ["E-1"]
        patch_db_helpers["latest_per_ev"] = {
            "E-1": _window("E-1", "sufficient", 90.0, review_status="approved"),
        }
        out_approved = _compute_composite(MagicMock(), ORG, "BCD-11")

        patch_db_helpers["latest_per_ev"] = {
            "E-1": _window("E-1", "sufficient", 90.0, review_status=None),
        }
        out_unreviewed = _compute_composite(MagicMock(), ORG, "BCD-11")

        # D4: approved == not_reviewed for composite math.
        assert out_approved["composite_status"] == out_unreviewed["composite_status"]
        assert out_approved["composite_score"] == out_unreviewed["composite_score"]
        # Neither emits a review-driven gap reason.
        assert all(
            g.get("reason") not in {"review_rejected", "pending_revision"}
            for g in out_approved["mandatory_gaps"]
        )

    def test_not_reviewed_explicit_string_behaves_like_none(self, patch_db_helpers):
        patch_db_helpers["evidence_ids"] = ["E-1"]
        patch_db_helpers["latest_per_ev"] = {
            "E-1": _window("E-1", "sufficient", 80.0, review_status="not_reviewed"),
        }
        out = _compute_composite(MagicMock(), ORG, "BCD-11")
        # No special handling for "not_reviewed" string — falls through.
        assert out["composite_status"] == "sufficient"
        assert all(
            g.get("reason") not in {"review_rejected", "pending_revision"}
            for g in out["mandatory_gaps"]
        )

    def test_rejected_overrides_high_score_to_zero(self, patch_db_helpers):
        # Even relevance=100 with sufficient status is fully zeroed by rejection.
        patch_db_helpers["evidence_ids"] = ["E-1"]
        patch_db_helpers["latest_per_ev"] = {
            "E-1": _window("E-1", "sufficient", 100.0, review_status="rejected"),
        }
        out = _compute_composite(MagicMock(), ORG, "BCD-11")
        assert out["composite_score"] == Decimal("0.00")


# ---------------------------------------------------------------------------
# Dispatcher review_status watching (M4 PR 3)
# ---------------------------------------------------------------------------


class TestDispatcherReviewStatus:
    """``_before_flush_handler`` must fire recompute on review_status changes
    in addition to its existing status-transition firing.
    """

    def _make_dirty_session(self, *, status="sufficient"):
        from models import EvidenceWindowAssessment

        ewa = EvidenceWindowAssessment(
            organization_id=uuid4(),
            evidence_id="E-1",
            status=status,
            window_start=datetime(2026, 1, 1),
            window_end=datetime(2026, 2, 1),
            frequency_used="monthly",
            review_status="approved",
        )
        session = MagicMock()
        session.info = {}
        session.new = []
        session.dirty = [ewa]
        return session, ewa

    def test_review_status_change_fires_recompute(self, monkeypatch):
        from sqlalchemy.orm import attributes as orm_attributes

        class _FakeHistory:
            def __init__(self, deleted):
                self.deleted = deleted

        session, _ = self._make_dirty_session(status="sufficient")

        def _fake_get_history(obj, attr):
            if attr == "status":
                # status not changed in this commit
                return _FakeHistory([])
            if attr == "review_status":
                # review_status flipped from not_reviewed -> approved
                return _FakeHistory(["not_reviewed"])
            return _FakeHistory([])

        monkeypatch.setattr(orm_attributes, "get_history", _fake_get_history)
        _before_flush_handler(session, None, None)
        pending = session.info.get("_composite_pending", [])
        # Should fire even though status didn't change — review_status alone is enough.
        assert len(pending) == 1
        assert pending[0][1] == "E-1"

    def test_no_review_change_does_not_fire(self, monkeypatch):
        from sqlalchemy.orm import attributes as orm_attributes

        class _FakeHistory:
            def __init__(self, deleted):
                self.deleted = deleted

        session, _ = self._make_dirty_session(status="sufficient")
        monkeypatch.setattr(
            orm_attributes, "get_history",
            lambda obj, attr: _FakeHistory([]),
        )
        _before_flush_handler(session, None, None)
        pending = session.info.get("_composite_pending", [])
        assert pending == []

    def test_status_and_review_status_both_change_dedupes_downstream(self, monkeypatch):
        # Both changing in one commit: pending may contain duplicate (org, ev)
        # entries — by_evidence dedupes in _after_commit_handler. We assert
        # both fired (defensive — the dedupe is downstream).
        from sqlalchemy.orm import attributes as orm_attributes

        class _FakeHistory:
            def __init__(self, deleted):
                self.deleted = deleted

        session, _ = self._make_dirty_session(status="sufficient")

        def _fake_get_history(obj, attr):
            if attr == "status":
                return _FakeHistory(["pending"])
            if attr == "review_status":
                return _FakeHistory(["not_reviewed"])
            return _FakeHistory([])

        monkeypatch.setattr(orm_attributes, "get_history", _fake_get_history)
        _before_flush_handler(session, None, None)
        pending = session.info.get("_composite_pending", [])
        # Two entries fired — one for status transition, one for review_status.
        # _after_commit_handler will collapse via by_evidence dict.
        assert len(pending) == 2
