"""Confirmation weighting on the evidence-quality axis (#881 WS3, seam 4).

An AI verdict nobody has looked at is not worth the same as one a person has
confirmed, and the quality axis has to say so. These tests hold the arithmetic
(``apply_confirmation_weight``), the shape of the SQL that feeds it, and the
promise that an organisation on an untouched tier scores exactly what it scored
before this change.

Pure functions and scripted SQL, no DB — the style of
``test_attested_ksi_sql.py`` and ``test_ksi_composite.py``.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.ksi_scoring import (  # noqa: E402
    EQ_UNCONFIRMED_WEIGHT,
    apply_confirmation_weight,
    compute_eq,
)


class TestApplyConfirmationWeight:
    def test_unconfirmed_weight_is_a_half(self):
        """Pinned deliberately: the number is a product decision, not a detail."""
        assert EQ_UNCONFIRMED_WEIGHT == 0.5

    def test_all_confirmed_counts_in_full(self):
        assert apply_confirmation_weight(8, 8) == 8.0

    def test_none_confirmed_counts_at_the_unconfirmed_weight(self):
        assert apply_confirmation_weight(8, 0) == 4.0

    def test_mixed_splits_between_the_two_rates(self):
        # 3 confirmed at 1.0 + 5 unconfirmed at 0.5
        assert apply_confirmation_weight(8, 3) == 5.5

    def test_a_null_confirmed_count_leaves_the_total_untouched(self):
        """SQL variants that do not emit the column must not be silently halved.

        Three of the eight metric tiers are unchanged by this work. They hand
        back ``None`` for the confirmed count, and that has to mean "this tier
        does not model confirmation", not "nothing here is confirmed" — the
        second reading would quietly halve the quality axis for every org on a
        windowed or composite tier without anyone asking for it.
        """
        assert apply_confirmation_weight(8, None) == 8.0

    def test_zero_total_is_zero(self):
        assert apply_confirmation_weight(0, 0) == 0.0

    def test_confirmed_above_total_is_clamped(self):
        """Defensive: a count that exceeds its own population cannot earn a bonus."""
        assert apply_confirmation_weight(4, 9) == 4.0

    def test_weight_is_overridable_for_callers_that_need_to_model_it(self):
        assert apply_confirmation_weight(10, 0, weight=0.25) == 2.5


class TestWeightingReachesEQ:
    def test_confirmed_evidence_scores_higher_than_the_same_unconfirmed_evidence(self):
        confirmed = compute_eq(
            sufficient=apply_confirmation_weight(4, 4),
            partial=0, insufficient=0, avg_relevance_0_100=100.0,
            total_assessed=4,
        )
        unconfirmed = compute_eq(
            sufficient=apply_confirmation_weight(4, 0),
            partial=0, insufficient=0, avg_relevance_0_100=100.0,
            total_assessed=4,
        )
        assert confirmed > unconfirmed
        assert confirmed == pytest.approx(1.0)
        # Half the sufficient weight over an unchanged denominator.
        assert unconfirmed == pytest.approx(0.5)

    def test_weighting_is_inert_without_an_explicit_denominator(self):
        """The trap this contract exists to close.

        Weighting the buckets and then deriving the denominator from those same
        weighted buckets cancels exactly: four unconfirmed sufficient files
        would score 1.0, the same as four confirmed ones, and the whole
        mechanism would be a no-op nobody noticed.
        """
        cancelled = compute_eq(
            sufficient=apply_confirmation_weight(4, 0),
            partial=0, insufficient=0, avg_relevance_0_100=100.0,
        )
        assert cancelled == pytest.approx(1.0)

    def test_the_denominator_is_the_unweighted_population(self):
        eq = compute_eq(
            sufficient=apply_confirmation_weight(2, 0),
            partial=apply_confirmation_weight(2, 2),
            insufficient=0,
            avg_relevance_0_100=100.0,
            total_assessed=4,
        )
        # (1.0*1.0 + 0.5*2.0) / 4.0
        assert eq == pytest.approx(0.5)

    def test_unweighted_callers_are_unaffected(self):
        """Every existing call site passes plain counts and no denominator."""
        assert compute_eq(2, 1, 1, 100.0) == pytest.approx(2.5 / 4.0)


class TestPerFileSQLEmitsConfirmationCounts:
    @pytest.mark.parametrize("attested_only", [False, True])
    def test_confirmed_buckets_are_selected(self, attested_only):
        from api.capability_themes import _build_per_file_sql

        sql = str(_build_per_file_sql(attested_only=attested_only))
        for column in (
            "sufficient_confirmed_count",
            "partial_confirmed_count",
            "insufficient_confirmed_count",
        ):
            assert column in sql

    @pytest.mark.parametrize("attested_only", [False, True])
    def test_confirmation_is_read_off_the_denormalized_column(self, attested_only):
        """No new join: review_decision already lives on evidence_assessments."""
        from api.capability_themes import _build_per_file_sql

        sql = str(_build_per_file_sql(attested_only=attested_only))
        assert "ea.review_decision IS NOT NULL" in sql
        assert "assessment_confirmed" in sql
        assert "evidence_assessment_versions" not in sql

    @pytest.mark.parametrize("attested_only", [False, True])
    def test_confirmed_buckets_are_subsets_of_their_status_buckets(self, attested_only):
        from api.capability_themes import _build_per_file_sql

        sql = str(_build_per_file_sql(attested_only=attested_only))
        assert (
            "FILTER (WHERE oe.assessment_status = 'sufficient' AND oe.assessment_confirmed)"
            in sql
        )

    def test_builder_still_takes_no_string_parameters(self):
        """The nosemgrep suppression rests on this — keep it true."""
        import inspect
        from api.capability_themes import _build_per_file_sql

        for param in inspect.signature(_build_per_file_sql).parameters.values():
            assert param.annotation in (bool, "bool"), param


class TestUnassessableReachesTheAxis:
    """Wave 1 taught compute_eq about unassessable; nothing was feeding it."""

    @pytest.mark.parametrize(
        "builder,kwargs",
        [
            ("_build_per_file_sql", {}),
            ("_build_window_aware_sql", {}),
            ("_build_composite_aware_sql", {"window_enabled": False}),
        ],
    )
    def test_unassessable_count_is_emitted(self, builder, kwargs):
        import api.capability_themes as ct

        sql = str(getattr(ct, builder)(**kwargs))
        assert "unassessable_count" in sql

    def test_axis_bundle_passes_unassessable_through(self):
        """The column existing is not the same as the score using it."""
        import inspect
        from api.capability_themes import _compute_axis_bundle

        source = inspect.getsource(_compute_axis_bundle)
        assert "unassessable" in source
        assert "unassessable=unassessable" in source

    def test_axis_bundle_uses_confirmation_weighting(self):
        import inspect
        from api.capability_themes import _compute_axis_bundle

        source = inspect.getsource(_compute_axis_bundle)
        assert "apply_confirmation_weight" in source
        # getattr-with-None so untouched SQL variants keep full weight.
        assert "sufficient_confirmed_count" in source
        assert "None" in source
