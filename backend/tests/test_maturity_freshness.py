"""Maturity freshness judged against the configured cadence (#789 audit lane).

The freshness ladder used to be a flat 7 / 30 / 90 days for every cadence, which
silently assumed monthly collection everywhere. An organisation running an
annual control annually was scored "very stale" four months after collecting it,
and quarterly evidence at 60 days scored "stale" while the frequency vocabulary
gives quarterly 95 days before it is stale at all.

These tests pin the fix: the bands are multiples of *that cadence's own*
staleness threshold, and the two frequency-shaped edge cases — no cadence
configured, and a cadence configured but unrecognised — are kept distinct.
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.frequency_vocabulary import (  # noqa: E402
    CANONICAL_FREQUENCIES,
    DEFAULT_STALENESS_DAYS,
    staleness_days,
)
from services.maturity import (  # noqa: E402
    FRESHNESS_BAND_MULTIPLIERS,
    FRESHNESS_VERY_STALE,
    MaturityInput,
    calculate_maturity,
)


def freshness_of(frequency, days_ago, **kwargs):
    """The freshness factor for evidence last collected ``days_ago`` days ago."""
    result = calculate_maturity(MaturityInput(
        is_tracked=True,
        collection_method=kwargs.pop("collection_method", "manual"),
        capability_status=kwargs.pop("capability_status", "active"),
        frequency=frequency,
        last_collection_date=date.today() - timedelta(days=days_ago),
        **kwargs,
    ))
    return result.factors.get("freshness")


class TestTheCadenceSetsTheBands:

    @pytest.mark.parametrize("frequency", sorted(CANONICAL_FREQUENCIES))
    def test_every_canonical_cadence_is_scored_at_its_own_threshold(self, frequency):
        """One day inside the cadence is never worse than neutral, for any cadence."""
        threshold = staleness_days(frequency)
        assert threshold is not None, f"{frequency} has no staleness threshold"
        factor = freshness_of(frequency, threshold - 1)
        assert factor["modifier"] >= 0, (
            f"{frequency} penalised at {threshold - 1} days, inside its own "
            f"{threshold}-day threshold"
        )

    @pytest.mark.parametrize("frequency", sorted(CANONICAL_FREQUENCIES))
    def test_every_canonical_cadence_reports_the_threshold_it_was_judged_on(self, frequency):
        factor = freshness_of(frequency, 1)
        assert factor["threshold_days"] == staleness_days(frequency)

    def test_annual_evidence_four_months_old_is_not_stale(self):
        """The headline case. Under the old flat ladder this scored -2."""
        factor = freshness_of("annual", 120)
        assert factor["modifier"] == 0

    def test_quarterly_evidence_at_sixty_days_is_not_stale(self):
        """Quarterly gets 95 days from the vocabulary. The old ladder gave it 30."""
        factor = freshness_of("quarterly", 60)
        assert factor["modifier"] == 0

    def test_daily_evidence_a_fortnight_old_is_very_stale(self):
        """The fix cuts both ways: a tight cadence is now judged tightly.

        Two weeks is seven times a daily cadence's two-day threshold. The old
        flat ladder called it merely "stale" (-1) — the same verdict it gave
        annual evidence at the same age, which is the incoherence being fixed.
        """
        factor = freshness_of("daily", 14)
        assert factor["modifier"] == FRESHNESS_VERY_STALE

    def test_monthly_barely_moves_because_the_old_literals_were_tuned_for_it(self):
        """Monthly is the common case; the multipliers were chosen to preserve it.

        Old ladder: +1 at <=7, 0 at <=30, -1 at <=90. Monthly's threshold is 35,
        so the new bands are <=8, <=35, <=105.
        """
        assert freshness_of("monthly", 7)["modifier"] == 1
        assert freshness_of("monthly", 30)["modifier"] == 0
        assert freshness_of("monthly", 90)["modifier"] == -1
        assert freshness_of("monthly", 200)["modifier"] == FRESHNESS_VERY_STALE


class TestTheTwoAbsentFrequencyCases:
    """"No cadence configured" and "cadence configured, not understood" are
    different situations, and only one of them is fine."""

    def test_no_cadence_at_all_falls_back_to_the_documented_default(self):
        factor = freshness_of(None, 1)
        assert factor["threshold_days"] == DEFAULT_STALENESS_DAYS
        assert "unscored_reason" not in factor

    def test_an_unrecognised_cadence_is_recorded_but_not_scored(self):
        """A typo must not masquerade as monthly (#783).

        Substituting the default here would score freshness off a number nobody
        chose. The days are still reported — a reader can see the evidence is
        four years old — but the modifier stays neutral and says why.
        """
        factor = freshness_of("whenever we remember", 1500)
        assert factor["modifier"] == 0
        assert "unrecognised frequency" in factor["unscored_reason"]
        assert factor["days_since_collection"] == 1500
        assert "threshold_days" not in factor

    def test_an_unrecognised_cadence_does_not_silently_borrow_the_default(self):
        recognised = freshness_of("monthly", 1500)
        unrecognised = freshness_of("whenever we remember", 1500)
        assert recognised["modifier"] == FRESHNESS_VERY_STALE
        assert unrecognised["modifier"] == 0, (
            "an unrecognised cadence was scored as if it were the default"
        )

    def test_no_collection_date_produces_no_freshness_factor_at_all(self):
        result = calculate_maturity(MaturityInput(
            is_tracked=True, collection_method="manual",
            capability_status="active", frequency="monthly",
            last_collection_date=None,
        ))
        assert "freshness" not in result.factors


class TestTheBandTableItself:

    def test_the_bands_are_ordered_and_strictly_decreasing(self):
        """A misordered table would make an earlier band shadow a later one."""
        multipliers = [m for m, _ in FRESHNESS_BAND_MULTIPLIERS]
        modifiers = [mod for _, mod in FRESHNESS_BAND_MULTIPLIERS]
        assert multipliers == sorted(multipliers)
        assert modifiers == sorted(modifiers, reverse=True)
        assert modifiers[-1] > FRESHNESS_VERY_STALE

    def test_collection_on_the_threshold_itself_is_still_neutral_not_stale(self):
        """Boundary: "due today" is not "overdue"."""
        threshold = staleness_days("quarterly")
        assert freshness_of("quarterly", threshold)["modifier"] == 0
        assert freshness_of("quarterly", threshold + 1)["modifier"] == -1
