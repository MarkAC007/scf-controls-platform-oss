"""Unit tests for the frequency-health detection service (M4 PR 1, #574).

Pure-function coverage for the cadence-detection algorithm. These tests
exercise ``_observe_one`` directly — no DB, no async fixtures. The
async ``compute_for_org`` entrypoint is integration-tested in PR 2 via
the new ``GET /evidence/frequency-health`` endpoint test file.

Spec reference: ``/tmp/m4-design-spec.md`` ISC-6..9.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from typing import List

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.frequency_health_service import (  # noqa: E402
    FrequencyObservation,
    _bucket_median_to_frequency,
    _classify_confidence,
    _compute_iqr_cv,
    _gaps_days,
    _observe_one,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seq(start: datetime, gap_days: float, n: int) -> List[datetime]:
    """Generate ``n`` evenly spaced timestamps starting at ``start``."""
    return [start + timedelta(days=gap_days * i) for i in range(n)]


_BASE = datetime(2026, 4, 1, 12, 0, 0)


# ---------------------------------------------------------------------------
# _gaps_days
# ---------------------------------------------------------------------------


class TestGapsDays:
    def test_empty_returns_empty(self):
        assert _gaps_days([]) == []

    def test_single_returns_empty(self):
        assert _gaps_days([_BASE]) == []

    def test_unsorted_input_is_sorted(self):
        ts = [_BASE + timedelta(days=2), _BASE, _BASE + timedelta(days=5)]
        gaps = _gaps_days(ts)
        # After sorting: 0, 2, 5 → gaps 2, 3
        assert gaps == pytest.approx([2.0, 3.0])

    def test_daily_pattern(self):
        ts = _seq(_BASE, 1.0, 5)
        gaps = _gaps_days(ts)
        assert gaps == pytest.approx([1.0, 1.0, 1.0, 1.0])


# ---------------------------------------------------------------------------
# _compute_iqr_cv
# ---------------------------------------------------------------------------


class TestComputeIqrCv:
    def test_empty_gaps(self):
        median, cv = _compute_iqr_cv([])
        assert median == 0.0
        assert cv == 0.0

    def test_two_gaps_returns_cv_zero(self):
        # N<4 → cv=0 per spec
        median, cv = _compute_iqr_cv([1.0, 1.0])
        assert median == 1.0
        assert cv == 0.0

    def test_perfect_daily_gaps_cv_zero(self):
        median, cv = _compute_iqr_cv([1.0] * 10)
        assert median == 1.0
        assert cv == pytest.approx(0.0)

    def test_zero_median_returns_inf_cv(self):
        # Burst of identical timestamps — degenerate
        median, cv = _compute_iqr_cv([0.0, 0.0, 0.0, 0.0])
        assert cv == float("inf")

    def test_high_variance_high_cv(self):
        # Mix of fast + slow gaps — IQR/median should be large
        gaps = [0.01, 0.02, 0.03, 0.04, 7.0, 14.0, 21.0, 30.0]
        median, cv = _compute_iqr_cv(gaps)
        assert cv > 1.0


# ---------------------------------------------------------------------------
# _classify_confidence
# ---------------------------------------------------------------------------


class TestClassifyConfidence:
    def test_high_confidence(self):
        assert _classify_confidence(8, 0.10) == "high"
        assert _classify_confidence(20, 0.30) == "high"

    def test_medium_when_lower_n_or_higher_cv(self):
        assert _classify_confidence(4, 0.40) == "medium"
        assert _classify_confidence(10, 0.45) == "medium"

    def test_low_when_minimal_n(self):
        assert _classify_confidence(2, 0.50) == "low"
        assert _classify_confidence(3, 1.00) == "low"

    def test_insufficient_when_cv_too_high(self):
        assert _classify_confidence(20, 1.5) == "insufficient"

    def test_insufficient_when_n_below_2(self):
        assert _classify_confidence(0, 0.0) == "insufficient"
        assert _classify_confidence(1, 0.0) == "insufficient"


# ---------------------------------------------------------------------------
# _bucket_median_to_frequency
# ---------------------------------------------------------------------------


class TestBucketMedianToFrequency:
    def test_real_time_under_quarter_day(self):
        assert _bucket_median_to_frequency(0.05) == "real_time"
        assert _bucket_median_to_frequency(0.24) == "real_time"

    def test_daily_quarter_to_one_and_half(self):
        assert _bucket_median_to_frequency(0.25) == "daily"
        assert _bucket_median_to_frequency(1.0) == "daily"
        assert _bucket_median_to_frequency(1.49) == "daily"

    def test_weekly_one_and_half_to_twelve(self):
        assert _bucket_median_to_frequency(1.5) == "weekly"
        assert _bucket_median_to_frequency(7.0) == "weekly"
        assert _bucket_median_to_frequency(11.99) == "weekly"

    def test_monthly_twelve_to_fifty(self):
        assert _bucket_median_to_frequency(12.0) == "monthly"
        assert _bucket_median_to_frequency(30.0) == "monthly"
        assert _bucket_median_to_frequency(49.99) == "monthly"

    def test_quarterly_fifty_to_two_hundred(self):
        assert _bucket_median_to_frequency(50.0) == "quarterly"
        assert _bucket_median_to_frequency(90.0) == "quarterly"
        assert _bucket_median_to_frequency(199.99) == "quarterly"

    def test_annual_two_hundred_and_above(self):
        assert _bucket_median_to_frequency(200.0) == "annual"
        assert _bucket_median_to_frequency(365.0) == "annual"


# ---------------------------------------------------------------------------
# _observe_one — full algorithm coverage (spec ISC-9 edge cases)
# ---------------------------------------------------------------------------


class TestObserveOne:
    def test_empty_returns_no_files(self):
        obs = _observe_one("E-1", "daily", [])
        assert obs.reason == "no_files"
        assert obs.confidence == "insufficient"
        assert obs.file_count == 0
        assert obs.misaligned is False
        assert obs.observed_cadence_days is None
        assert obs.suggested_frequency is None

    def test_single_file_returns_single_file(self):
        obs = _observe_one("E-1", "daily", [_BASE])
        assert obs.reason == "single_file"
        assert obs.confidence == "insufficient"
        assert obs.file_count == 1
        assert obs.misaligned is False
        assert obs.observed_cadence_days is None

    def test_perfectly_daily_n10_aligned(self):
        # 10 daily uploads, declared daily — aligned at high confidence
        ts = _seq(_BASE, 1.0, 10)
        obs = _observe_one("E-2", "daily", ts)
        assert obs.confidence == "high"
        assert obs.suggested_frequency == "daily"
        assert obs.observed_cadence_days == pytest.approx(1.0)
        assert obs.misaligned is False
        assert obs.reason == "aligned"

    def test_perfectly_weekly_n10_aligned(self):
        ts = _seq(_BASE, 7.0, 10)
        obs = _observe_one("E-3", "weekly", ts)
        assert obs.confidence == "high"
        assert obs.suggested_frequency == "weekly"
        assert obs.observed_cadence_days == pytest.approx(7.0)
        assert obs.misaligned is False
        assert obs.reason == "aligned"

    def test_bursty_collector_irregular_cadence(self):
        # 5 files in 1h then 3w gap → high cv → insufficient confidence
        burst_start = _BASE
        # 5 files, 15 minutes apart = 0.0104 days each
        burst = [burst_start + timedelta(minutes=15 * i) for i in range(5)]
        # Then a single file 3 weeks later
        burst.append(burst_start + timedelta(days=21))
        obs = _observe_one("E-4", "daily", burst)
        assert obs.confidence == "insufficient"
        assert obs.reason == "irregular_cadence"
        assert obs.misaligned is False
        # observed_cadence_days should still report the median for diagnostic purposes
        # (some median was computed) — but suggested_frequency is None
        assert obs.suggested_frequency is None

    def test_switched_cadence_declared_monthly_observed_daily_misaligned(self):
        # Declared monthly, but ran daily for last 30 days → flag as misaligned
        ts = _seq(_BASE, 1.0, 30)
        obs = _observe_one("E-5", "monthly", ts)
        assert obs.confidence == "high"
        assert obs.suggested_frequency == "daily"
        assert obs.misaligned is True
        assert obs.reason == "misaligned"
        assert obs.declared_frequency == "monthly"

    def test_no_declared_frequency_with_signal_flagged(self):
        # No tracking.frequency set, but observed cadence is clear weekly
        ts = _seq(_BASE, 7.0, 10)
        obs = _observe_one("E-6", None, ts)
        assert obs.confidence == "high"
        assert obs.suggested_frequency == "weekly"
        assert obs.declared_frequency is None
        assert obs.misaligned is True
        assert obs.reason == "no_frequency_set"

    def test_no_declared_frequency_low_confidence_not_flagged(self):
        # No frequency declared, only 2 files — low confidence, do not flag
        ts = _seq(_BASE, 7.0, 2)
        obs = _observe_one("E-6b", None, ts)
        assert obs.confidence == "low"
        assert obs.misaligned is False
        assert obs.reason == "no_frequency_set"
        assert obs.suggested_frequency == "weekly"

    def test_low_confidence_n2_weekly_not_flagged(self):
        # 2 files, perfectly weekly, but file_count < 4 → low confidence,
        # NOT flagged as misalignment even if declared differs (per ISC-8 step 6).
        ts = _seq(_BASE, 7.0, 2)
        obs = _observe_one("E-7", "monthly", ts)
        assert obs.confidence == "low"
        assert obs.misaligned is False
        assert obs.suggested_frequency == "weekly"
        # reason is 'aligned' because misaligned=False at low confidence even
        # when declared != suggested
        assert obs.reason == "aligned"

    def test_daily_with_one_outlier_still_classified_daily(self):
        # 30 daily uploads with one missed run (2 day gap) — median absorbs
        # outlier → still suggests daily. Tests robustness claim from ISC-9.
        ts = _seq(_BASE, 1.0, 30)
        # Insert a 2-day gap by removing one element and shifting back
        ts = ts[:15] + [t + timedelta(days=1) for t in ts[15:]]
        obs = _observe_one("E-8", "daily", ts)
        assert obs.suggested_frequency == "daily"
        assert obs.misaligned is False
        # Confidence should still be high — IQR is small, one outlier shouldn't
        # blow the cv past 0.30
        assert obs.confidence in {"high", "medium"}

    def test_declared_frequency_uppercase_normalized(self):
        # Spec/legacy data may have uppercased frequency strings — make sure
        # comparison is case-insensitive.
        ts = _seq(_BASE, 1.0, 10)
        obs = _observe_one("E-9", "DAILY", ts)
        assert obs.misaligned is False
        assert obs.reason == "aligned"
        assert obs.declared_frequency == "daily"  # normalized

    def test_observation_dataclass_shape(self):
        # Lock down the fields exposed — tests that downstream PR 2 schema
        # mapping won't silently drift.
        obs = _observe_one("E-10", "daily", _seq(_BASE, 1.0, 10))
        assert isinstance(obs, FrequencyObservation)
        for fld in [
            "evidence_id",
            "declared_frequency",
            "observed_cadence_days",
            "suggested_frequency",
            "confidence",
            "file_count",
            "misaligned",
            "reason",
        ]:
            assert hasattr(obs, fld), f"FrequencyObservation missing field {fld}"
