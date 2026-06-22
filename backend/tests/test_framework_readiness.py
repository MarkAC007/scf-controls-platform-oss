"""
Tests for Framework Readiness API endpoint.

The readiness calculation uses the formula:
Readiness = (40% × Implementation Score) + (60% × Evidence Score)

Where:
- Implementation Score = % of selected controls that are implemented
- Evidence Score = % of required evidence that is tracked
"""
import pytest
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.capabilities import calculate_readiness_grade, IMPLEMENTATION_WEIGHT, EVIDENCE_WEIGHT


class TestReadinessGradeCalculation:
    """Unit tests for the readiness grade calculation function."""

    def test_excellent_grade_at_90(self):
        """Score of 90 should yield 'excellent' grade."""
        assert calculate_readiness_grade(90.0) == "excellent"

    def test_excellent_grade_at_100(self):
        """Score of 100 should yield 'excellent' grade."""
        assert calculate_readiness_grade(100.0) == "excellent"

    def test_excellent_grade_at_95(self):
        """Score of 95 should yield 'excellent' grade."""
        assert calculate_readiness_grade(95.0) == "excellent"

    def test_good_grade_at_70(self):
        """Score of 70 should yield 'good' grade."""
        assert calculate_readiness_grade(70.0) == "good"

    def test_good_grade_at_89(self):
        """Score of 89 should yield 'good' grade (just under excellent)."""
        assert calculate_readiness_grade(89.0) == "good"

    def test_good_grade_at_75(self):
        """Score of 75 should yield 'good' grade."""
        assert calculate_readiness_grade(75.0) == "good"

    def test_fair_grade_at_50(self):
        """Score of 50 should yield 'fair' grade."""
        assert calculate_readiness_grade(50.0) == "fair"

    def test_fair_grade_at_69(self):
        """Score of 69 should yield 'fair' grade (just under good)."""
        assert calculate_readiness_grade(69.0) == "fair"

    def test_fair_grade_at_55(self):
        """Score of 55 should yield 'fair' grade."""
        assert calculate_readiness_grade(55.0) == "fair"

    def test_needs_work_grade_at_49(self):
        """Score of 49 should yield 'needs-work' grade."""
        assert calculate_readiness_grade(49.0) == "needs-work"

    def test_needs_work_grade_at_0(self):
        """Score of 0 should yield 'needs-work' grade."""
        assert calculate_readiness_grade(0.0) == "needs-work"

    def test_needs_work_grade_at_25(self):
        """Score of 25 should yield 'needs-work' grade."""
        assert calculate_readiness_grade(25.0) == "needs-work"


class TestReadinessWeights:
    """Tests for the readiness calculation weights."""

    def test_weights_sum_to_one(self):
        """Implementation and evidence weights should sum to 1.0."""
        assert IMPLEMENTATION_WEIGHT + EVIDENCE_WEIGHT == 1.0

    def test_implementation_weight_is_40_percent(self):
        """Implementation weight should be 0.4 (40%)."""
        assert IMPLEMENTATION_WEIGHT == 0.4

    def test_evidence_weight_is_60_percent(self):
        """Evidence weight should be 0.6 (60%)."""
        assert EVIDENCE_WEIGHT == 0.6


class TestReadinessFormulaCalculation:
    """Tests for the combined readiness score calculation."""

    def test_perfect_implementation_no_evidence(self):
        """100% implementation, 0% evidence = 40% readiness."""
        implementation_score = 100.0
        evidence_score = 0.0
        readiness = IMPLEMENTATION_WEIGHT * implementation_score + EVIDENCE_WEIGHT * evidence_score
        assert readiness == 40.0

    def test_no_implementation_perfect_evidence(self):
        """0% implementation, 100% evidence = 60% readiness."""
        implementation_score = 0.0
        evidence_score = 100.0
        readiness = IMPLEMENTATION_WEIGHT * implementation_score + EVIDENCE_WEIGHT * evidence_score
        assert readiness == 60.0

    def test_perfect_both(self):
        """100% implementation, 100% evidence = 100% readiness."""
        implementation_score = 100.0
        evidence_score = 100.0
        readiness = IMPLEMENTATION_WEIGHT * implementation_score + EVIDENCE_WEIGHT * evidence_score
        assert readiness == 100.0

    def test_zero_both(self):
        """0% implementation, 0% evidence = 0% readiness."""
        implementation_score = 0.0
        evidence_score = 0.0
        readiness = IMPLEMENTATION_WEIGHT * implementation_score + EVIDENCE_WEIGHT * evidence_score
        assert readiness == 0.0

    def test_fifty_fifty(self):
        """50% implementation, 50% evidence = 50% readiness."""
        implementation_score = 50.0
        evidence_score = 50.0
        readiness = IMPLEMENTATION_WEIGHT * implementation_score + EVIDENCE_WEIGHT * evidence_score
        assert readiness == 50.0

    def test_excellent_threshold_requires_both(self):
        """
        To reach 'excellent' (90+), need high scores in both.
        e.g., 100% impl (40) + 84% evidence (50.4) = 90.4
        """
        implementation_score = 100.0
        evidence_score = 84.0  # Slightly above minimum for safety
        readiness = IMPLEMENTATION_WEIGHT * implementation_score + EVIDENCE_WEIGHT * evidence_score
        # 0.4 * 100 + 0.6 * 84 = 40 + 50.4 = 90.4
        assert readiness >= 90.0
        assert calculate_readiness_grade(readiness) == "excellent"

    def test_good_implementation_poor_evidence(self):
        """
        Good implementation but poor evidence collection results in fair score.
        80% impl (32) + 30% evidence (18) = 50 = fair
        """
        implementation_score = 80.0
        evidence_score = 30.0
        readiness = IMPLEMENTATION_WEIGHT * implementation_score + EVIDENCE_WEIGHT * evidence_score
        assert readiness == 50.0
        assert calculate_readiness_grade(readiness) == "fair"

    def test_evidence_heavy_weight_impact(self):
        """
        Evidence has 60% weight, so low evidence drags down overall score.
        100% impl (40) + 50% evidence (30) = 70 = good (but not excellent)
        """
        implementation_score = 100.0
        evidence_score = 50.0
        readiness = IMPLEMENTATION_WEIGHT * implementation_score + EVIDENCE_WEIGHT * evidence_score
        assert readiness == 70.0
        assert calculate_readiness_grade(readiness) == "good"


class TestImplementationScoreCalculation:
    """Tests for implementation score calculation logic."""

    def test_implementation_score_all_implemented(self):
        """All selected controls implemented = 100%."""
        selected_controls = 10
        implemented_controls = 10
        score = (implemented_controls / selected_controls * 100) if selected_controls > 0 else 0.0
        assert score == 100.0

    def test_implementation_score_none_implemented(self):
        """No controls implemented = 0%."""
        selected_controls = 10
        implemented_controls = 0
        score = (implemented_controls / selected_controls * 100) if selected_controls > 0 else 0.0
        assert score == 0.0

    def test_implementation_score_half_implemented(self):
        """Half controls implemented = 50%."""
        selected_controls = 10
        implemented_controls = 5
        score = (implemented_controls / selected_controls * 100) if selected_controls > 0 else 0.0
        assert score == 50.0

    def test_implementation_score_no_selected_controls(self):
        """No selected controls = 0% (edge case)."""
        selected_controls = 0
        implemented_controls = 0
        score = (implemented_controls / selected_controls * 100) if selected_controls > 0 else 0.0
        assert score == 0.0


class TestEvidenceScoreCalculation:
    """Tests for evidence score calculation logic."""

    def test_evidence_score_all_tracked(self):
        """All required evidence tracked = 100%."""
        total_evidence = 20
        tracked_evidence = 20
        score = (tracked_evidence / total_evidence * 100) if total_evidence > 0 else 0.0
        assert score == 100.0

    def test_evidence_score_none_tracked(self):
        """No evidence tracked = 0%."""
        total_evidence = 20
        tracked_evidence = 0
        score = (tracked_evidence / total_evidence * 100) if total_evidence > 0 else 0.0
        assert score == 0.0

    def test_evidence_score_partial_tracking(self):
        """Some evidence tracked = proportional %."""
        total_evidence = 20
        tracked_evidence = 15
        score = (tracked_evidence / total_evidence * 100) if total_evidence > 0 else 0.0
        assert score == 75.0

    def test_evidence_score_no_required_evidence(self):
        """No required evidence = 0% (edge case)."""
        total_evidence = 0
        tracked_evidence = 0
        score = (tracked_evidence / total_evidence * 100) if total_evidence > 0 else 0.0
        assert score == 0.0


class TestRealisticScenarios:
    """
    Test realistic GRC scenarios to validate the formula meets business needs.
    """

    def test_new_framework_adoption(self):
        """
        New framework adoption: Some controls selected, none implemented, no evidence.
        Should yield needs-work grade.
        """
        impl_score = 0.0  # No controls implemented yet
        evid_score = 0.0  # No evidence collection started
        readiness = IMPLEMENTATION_WEIGHT * impl_score + EVIDENCE_WEIGHT * evid_score
        assert readiness == 0.0
        assert calculate_readiness_grade(readiness) == "needs-work"

    def test_controls_implemented_no_evidence(self):
        """
        All controls implemented but evidence collection not started.
        Common early-stage compliance scenario. Should be 'needs-work'.
        """
        impl_score = 100.0  # All controls implemented
        evid_score = 0.0    # But no evidence being tracked
        readiness = IMPLEMENTATION_WEIGHT * impl_score + EVIDENCE_WEIGHT * evid_score
        assert readiness == 40.0
        assert calculate_readiness_grade(readiness) == "needs-work"

    def test_evidence_first_approach(self):
        """
        Evidence collection started before formal implementation.
        Common in automation-first GRC approaches. Should still be 'fair'.
        """
        impl_score = 20.0   # Few controls formally implemented
        evid_score = 80.0   # But evidence collection already automated
        readiness = IMPLEMENTATION_WEIGHT * impl_score + EVIDENCE_WEIGHT * evid_score
        # 0.4 * 20 + 0.6 * 80 = 8 + 48 = 56
        assert readiness == 56.0
        assert calculate_readiness_grade(readiness) == "fair"

    def test_audit_ready_scenario(self):
        """
        Ready for audit: High implementation and evidence collection.
        Should yield 'excellent' grade.
        """
        impl_score = 95.0   # Almost all controls implemented
        evid_score = 90.0   # Most evidence being collected
        readiness = IMPLEMENTATION_WEIGHT * impl_score + EVIDENCE_WEIGHT * evid_score
        # 0.4 * 95 + 0.6 * 90 = 38 + 54 = 92
        assert readiness == 92.0
        assert calculate_readiness_grade(readiness) == "excellent"

    def test_mature_but_gaps_scenario(self):
        """
        Mature program with some gaps. Should yield 'good' grade.
        """
        impl_score = 80.0   # Good implementation coverage
        evid_score = 70.0   # Good evidence but some gaps
        readiness = IMPLEMENTATION_WEIGHT * impl_score + EVIDENCE_WEIGHT * evid_score
        # 0.4 * 80 + 0.6 * 70 = 32 + 42 = 74
        assert readiness == 74.0
        assert calculate_readiness_grade(readiness) == "good"

    def test_bare_minimum_good(self):
        """
        Minimum score to achieve 'good' grade is 70.
        """
        # Various combinations that hit exactly 70:
        # 100% impl (40) + 50% evidence (30) = 70
        impl_score = 100.0
        evid_score = 50.0
        readiness = IMPLEMENTATION_WEIGHT * impl_score + EVIDENCE_WEIGHT * evid_score
        assert readiness == 70.0
        assert calculate_readiness_grade(readiness) == "good"

    def test_bare_minimum_excellent(self):
        """
        Minimum score to achieve 'excellent' grade is 90.
        """
        # 100% impl (40) + 83.33% evidence (50) = 90
        impl_score = 100.0
        evid_score = (90 - 40) / EVIDENCE_WEIGHT  # = 83.333...
        readiness = IMPLEMENTATION_WEIGHT * impl_score + EVIDENCE_WEIGHT * evid_score
        assert readiness >= 90.0
        assert calculate_readiness_grade(readiness) == "excellent"
