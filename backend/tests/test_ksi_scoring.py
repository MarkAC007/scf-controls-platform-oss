"""
Unit tests for the KSI multi-axis scoring helpers (issue #549, Phase 1).

Pure-function tests — no DB. Each axis formula and band threshold is exercised
at boundary values, including the null-handling and weight-redistribution edge
cases called out in the source design (§4 of the methodology analysis).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.ksi_scoring import (
    KPS_DEFAULT_WEIGHTS,
    band_for_axis,
    compute_ec,
    compute_eq,
    compute_eq_warning,
    compute_ic,
    compute_kps,
    compute_maturity,
)


class TestImplementationCoverage:
    """IC = (monitored + implemented + 0.5·rfr + 0.25·in_progress) / (scoped - N/A)."""

    def test_zero_denominator_returns_none(self):
        assert compute_ic(0, 0, 0, 0, scoped=0, not_applicable=0) is None

    def test_all_n_a_returns_none(self):
        assert compute_ic(0, 0, 0, 0, scoped=5, not_applicable=5) is None

    def test_full_implemented_returns_one(self):
        assert compute_ic(0, 10, 0, 0, scoped=10, not_applicable=0) == 1.0

    def test_full_monitored_returns_one(self):
        assert compute_ic(10, 0, 0, 0, scoped=10, not_applicable=0) == 1.0

    def test_all_not_started_returns_zero(self):
        # 0 numerator over 10 controls = 0.0
        assert compute_ic(0, 0, 0, 0, scoped=10, not_applicable=0) == 0.0

    def test_ready_for_review_half_credit(self):
        # 4 RFR over 10 → 4·0.5 / 10 = 0.20
        assert compute_ic(0, 0, 4, 0, scoped=10, not_applicable=0) == pytest.approx(0.20)

    def test_in_progress_quarter_credit(self):
        # 4 in_progress over 10 → 4·0.25 / 10 = 0.10
        assert compute_ic(0, 0, 0, 4, scoped=10, not_applicable=0) == pytest.approx(0.10)

    def test_not_applicable_excluded_from_denominator(self):
        # 5 implemented + 5 N/A out of 10 scoped = 5/(10-5) = 1.0
        assert compute_ic(0, 5, 0, 0, scoped=10, not_applicable=5) == 1.0


class TestMaturity:
    """M = AVG(L-level numeric) with sample-size guard (n<3 → null)."""

    def test_sample_size_below_floor_returns_none(self):
        assert compute_maturity([3, 4]) is None

    def test_empty_list_returns_none(self):
        assert compute_maturity([]) is None

    def test_at_floor_returns_average(self):
        assert compute_maturity([2, 3, 4]) == pytest.approx(3.0)

    def test_all_l5_returns_five(self):
        assert compute_maturity([5, 5, 5, 5]) == 5.0

    def test_all_l0_returns_zero(self):
        assert compute_maturity([0, 0, 0]) == 0.0


class TestEvidenceCoverage:
    """EC = controls_with_evidence / (scoped - N/A)."""

    def test_zero_denominator_returns_none(self):
        assert compute_ec(0, scoped=0, not_applicable=0) is None

    def test_all_covered_returns_one(self):
        assert compute_ec(controls_with_evidence=8, scoped=10, not_applicable=2) == 1.0

    def test_none_covered_returns_zero(self):
        assert compute_ec(0, scoped=10, not_applicable=0) == 0.0

    def test_partial_coverage(self):
        assert compute_ec(controls_with_evidence=3, scoped=10, not_applicable=0) == pytest.approx(0.30)


class TestEvidenceQuality:
    """EQ = (1·sufficient + 0.5·partial + 0·insufficient) / total_assessed × (relevance/100)."""

    def test_no_assessed_returns_none(self):
        assert compute_eq(sufficient=0, partial=0, insufficient=0, avg_relevance_0_100=80) is None

    def test_all_sufficient_full_relevance(self):
        # 5 sufficient / 5 = 1.0 quality × 1.0 relevance = 1.0
        assert compute_eq(5, 0, 0, avg_relevance_0_100=100) == 1.0

    def test_partial_half_credit(self):
        # 4 partial / 4 = 0.5 quality × 1.0 relevance = 0.5
        assert compute_eq(0, 4, 0, avg_relevance_0_100=100) == pytest.approx(0.5)

    def test_insufficient_zero_credit(self):
        assert compute_eq(0, 0, 4, avg_relevance_0_100=100) == 0.0

    def test_null_relevance_treated_as_neutral_half(self):
        # Quality 1.0 × neutral 0.5 = 0.5
        assert compute_eq(5, 0, 0, avg_relevance_0_100=None) == pytest.approx(0.5)

    def test_relevance_scales_quality(self):
        # 4 sufficient + 4 partial = 8; quality = (4 + 2) / 8 = 0.75; × 0.6 = 0.45
        assert compute_eq(4, 4, 0, avg_relevance_0_100=60) == pytest.approx(0.45)

    def test_insufficient_sample_half_credit(self):
        # M1a: 4 insufficient_sample / 4 = 0.5 quality × 1.0 relevance = 0.5
        # Coverage-gap status is scored the same as 'partial' — content is ok,
        # the missing sample is the problem.
        assert compute_eq(0, 0, 0, avg_relevance_0_100=100, insufficient_sample=4) == pytest.approx(0.5)

    def test_insufficient_sample_combined_with_other_buckets(self):
        # 2 sufficient + 2 partial + 2 insufficient + 2 insufficient_sample = 8 total
        # quality = (2 + 1 + 0 + 1) / 8 = 0.5; × 0.8 = 0.4
        assert compute_eq(
            sufficient=2, partial=2, insufficient=2,
            avg_relevance_0_100=80, insufficient_sample=2,
        ) == pytest.approx(0.4)

    def test_insufficient_sample_default_zero_preserves_legacy_contract(self):
        # Without supplying insufficient_sample, behaviour matches pre-M1a callers.
        assert compute_eq(5, 0, 0, avg_relevance_0_100=100) == 1.0


class TestEvidenceQualityWarning:
    """Warning raised when unassessed_ratio > 0.30."""

    def test_no_files_returns_none(self):
        assert compute_eq_warning(0, 0) is None

    def test_below_threshold_returns_none(self):
        # 3 unassessed of 10 = 0.30 — NOT > 0.30
        assert compute_eq_warning(3, 10) is None

    def test_above_threshold_returns_warning(self):
        # 4 unassessed of 10 = 0.40
        assert compute_eq_warning(4, 10) == "low_ai_coverage"

    def test_all_unassessed_returns_warning(self):
        assert compute_eq_warning(10, 10) == "low_ai_coverage"


class TestComposite:
    """KPS = weighted sum with null-axis weight redistribution."""

    def test_all_axes_populated_uses_default_weights(self):
        # 0.35·0.8 + 0.20·1.0 + 0.20·0.6 + 0.25·0.4 = 0.28 + 0.20 + 0.12 + 0.10 = 0.70
        assert compute_kps(0.8, 1.0, 0.6, 0.4) == pytest.approx(0.70)

    def test_all_axes_none_returns_none(self):
        assert compute_kps(None, None, None, None) is None

    def test_single_null_axis_redistributes(self):
        # M None → IC/EC/EQ weights renormalised to 0.35+0.20+0.25 = 0.80
        # Composite = 0.35/0.80·0.8 + 0.20/0.80·0.6 + 0.25/0.80·0.4 = 0.35 + 0.15 + 0.125 = 0.625
        assert compute_kps(0.8, None, 0.6, 0.4) == pytest.approx(0.625)

    def test_only_one_axis_populated_returns_that_value(self):
        assert compute_kps(0.7, None, None, None) == pytest.approx(0.7)


class TestBands:
    """Per-axis Strong/Moderate/Developing thresholds — §4.4."""

    def test_ic_strong_at_threshold(self):
        assert band_for_axis("IC", 0.75) == "Strong"

    def test_ic_moderate_just_below_strong(self):
        assert band_for_axis("IC", 0.74) == "Moderate"

    def test_ic_moderate_at_lower_bound(self):
        assert band_for_axis("IC", 0.40) == "Moderate"

    def test_ic_developing_just_below_moderate(self):
        assert band_for_axis("IC", 0.39) == "Developing"

    def test_m_strong_at_three(self):
        assert band_for_axis("M", 3.0) == "Strong"

    def test_m_moderate_at_two(self):
        assert band_for_axis("M", 2.0) == "Moderate"

    def test_m_null_treated_as_developing(self):
        assert band_for_axis("M", None) == "Developing"

    def test_ec_strong_at_threshold(self):
        assert band_for_axis("EC", 0.70) == "Strong"

    def test_eq_developing_just_below_threshold(self):
        assert band_for_axis("EQ", 0.39) == "Developing"

    def test_kps_null_treated_as_developing(self):
        assert band_for_axis("KPS", None) == "Developing"

    def test_unknown_axis_raises(self):
        with pytest.raises(ValueError):
            band_for_axis("ZZ", 0.5)


class TestWeights:
    def test_default_weights_sum_to_one(self):
        assert sum(KPS_DEFAULT_WEIGHTS) == pytest.approx(1.0)
