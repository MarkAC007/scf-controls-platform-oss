# SPDX-License-Identifier: AGPL-3.0-only
"""Performance status rules for security objectives (pure, no database)."""
from datetime import date
from types import SimpleNamespace

import pytest

from services.isms_metrics.status import derive_status, period_start

TODAY = date(2026, 11, 18)


def _metric(**kw):
    base = dict(kind="live", unit="percent", direction="at_least", target_value=90, warn_value=75,
                min_population=5, frequency="nightly")
    base.update(kw)
    return SimpleNamespace(**base)


def _status(metric, value=None, denominator=None, lifecycle="active", objective_type="maintain",
            due_date=None, period=None, has_logged=False, today=TODAY):
    return derive_status(lifecycle, objective_type, due_date, metric, value, denominator, period, has_logged, today)


def test_no_metric_is_not_measured():
    assert _status(None) == "not_measured"


def test_unset_metric_is_not_measured_even_when_proposed():
    assert _status(_metric(kind="unset"), lifecycle="proposed") == "not_measured"


def test_proposed_shows_no_judgement():
    assert _status(_metric(), value=10, denominator=50, lifecycle="proposed") == "proposed"
    assert _status(_metric(), value=100, denominator=50, lifecycle="proposed") == "proposed"


@pytest.mark.parametrize("value,due,expected", [
    (1, date(2026, 12, 1), "on_track"),
    (1, date(2026, 1, 1), "on_track"),
    (0, date(2026, 1, 1), "off_track"),
    (None, date(2026, 1, 1), "off_track"),
    (0, date(2026, 12, 1), "in_progress"),
    (None, None, "in_progress"),
])
def test_milestone(value, due, expected):
    metric = _metric(kind="milestone", unit=None, direction=None, target_value=None, warn_value=None, frequency=None)
    assert _status(metric, value=value, due_date=due, has_logged=value is not None) == expected


def test_logged_with_no_entry_is_first_measurement_due():
    metric = _metric(kind="logged", frequency="quarterly", min_population=None)
    assert _status(metric, has_logged=False) == "first_measurement_due"


def test_logged_stale_entry_is_overdue():
    metric = _metric(kind="logged", frequency="quarterly", min_population=None)
    assert _status(metric, value=99, period=date(2026, 7, 1), has_logged=True) == "measurement_overdue"
    assert _status(metric, value=99, period=date(2026, 10, 1), has_logged=True) == "on_track"


def test_logged_percent_without_population_is_judged():
    """A logged percentage has no population; rule 6 is for live readings."""
    metric = _metric(kind="logged", frequency="monthly", min_population=None)
    assert _status(metric, value=80, period=date(2026, 11, 1), has_logged=True) == "at_risk"


def test_value_none_is_insufficient_data():
    assert _status(_metric(), value=None, denominator=10) == "insufficient_data"


def test_percent_on_zero_population_never_reads_on_track():
    assert _status(_metric(), value=100, denominator=0) == "insufficient_data"
    assert _status(_metric(), value=100, denominator=None) == "insufficient_data"


def test_percent_below_min_population_is_insufficient():
    assert _status(_metric(min_population=5), value=100, denominator=4) == "insufficient_data"
    assert _status(_metric(min_population=5), value=100, denominator=5) == "on_track"


def test_count_at_zero_is_a_real_reading():
    metric = _metric(unit="count", direction="at_most", target_value=0, warn_value=3, min_population=0)
    assert _status(metric, value=0, denominator=0) == "on_track"
    assert _status(metric, value=2, denominator=10) == "at_risk"
    assert _status(metric, value=4, denominator=10) == "off_track"


def test_no_target_is_not_measured():
    assert _status(_metric(target_value=None, warn_value=None), value=50, denominator=10) == "not_measured"


def test_achieve_before_due_date_is_in_progress():
    assert _status(_metric(), value=10, denominator=10, objective_type="achieve",
                   due_date=date(2026, 12, 31)) == "in_progress"
    assert _status(_metric(), value=10, denominator=10, objective_type="achieve", due_date=TODAY) == "in_progress"


def test_achieve_past_due_date_is_judged_against_target():
    assert _status(_metric(), value=10, denominator=10, objective_type="achieve",
                   due_date=date(2026, 11, 17)) == "off_track"
    assert _status(_metric(), value=95, denominator=10, objective_type="achieve",
                   due_date=date(2026, 11, 17)) == "on_track"


@pytest.mark.parametrize("value,expected", [(90, "on_track"), (89.9, "at_risk"), (75, "at_risk"), (74, "off_track")])
def test_at_least(value, expected):
    assert _status(_metric(), value=value, denominator=10) == expected


def test_at_least_without_warning():
    assert _status(_metric(warn_value=None), value=89, denominator=10) == "off_track"


def test_at_most_without_warning():
    metric = _metric(unit="count", direction="at_most", target_value=0, warn_value=None, min_population=0)
    assert _status(metric, value=1, denominator=1) == "off_track"


def test_rule_order_proposed_before_insufficient():
    assert _status(_metric(), value=None, denominator=0, lifecycle="proposed") == "proposed"


def test_metric_as_dict():
    assert _status({"kind": "live", "unit": "count", "direction": "at_least", "target_value": 2,
                    "warn_value": 1, "min_population": 0}, value=2, denominator=2) == "on_track"


@pytest.mark.parametrize("frequency,day,expected", [
    ("monthly", date(2026, 11, 18), date(2026, 11, 1)),
    ("quarterly", date(2026, 11, 18), date(2026, 10, 1)),
    ("quarterly", date(2026, 3, 31), date(2026, 1, 1)),
    ("annually", date(2026, 11, 18), date(2026, 1, 1)),
    ("nightly", date(2026, 11, 18), None),
])
def test_period_start(frequency, day, expected):
    assert period_start(frequency, day) == expected


# ---------------------------------------------------------------------------
# Closed objectives: the final state, no judgement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lifecycle", ["achieved", "missed"])
def test_a_closed_objective_shows_its_final_state_whatever_the_value(lifecycle):
    """Marked missed at 95% against 90% it must not read On track; marked achieved at 10% not Off track."""
    assert _status(_metric(), value=95, denominator=50, lifecycle=lifecycle) == lifecycle
    assert _status(_metric(), value=10, denominator=50, lifecycle=lifecycle) == lifecycle
    assert _status(_metric(), value=None, lifecycle=lifecycle) == lifecycle


@pytest.mark.parametrize("lifecycle", ["achieved", "missed"])
def test_a_closed_objective_is_final_for_every_kind_of_metric(lifecycle):
    assert _status(None, lifecycle=lifecycle) == lifecycle
    assert _status(_metric(kind="unset"), lifecycle=lifecycle) == lifecycle
    logged = _metric(kind="logged", frequency="quarterly", min_population=None)
    assert _status(logged, has_logged=False, lifecycle=lifecycle) == lifecycle
    assert _status(logged, value=99, period=date(2026, 1, 1), has_logged=True, lifecycle=lifecycle) == lifecycle
    milestone = _metric(kind="milestone", unit=None, direction=None, target_value=None, warn_value=None,
                        frequency=None)
    assert _status(milestone, value=0, due_date=date(2026, 1, 1), has_logged=True, lifecycle=lifecycle) == lifecycle


def test_the_final_states_are_reading_statuses_only():
    """The stored-status CHECK holds the nine judged statuses; a closed state is never stored."""
    from services.isms_metrics import status as rules

    assert rules.CLOSED == ("achieved", "missed")
    assert not set(rules.CLOSED) & set(rules.STATUSES)
    assert rules.READING_STATUSES == rules.STATUSES + rules.CLOSED
    assert not set(rules.CLOSED) & set(rules.NEEDS_ATTENTION) and not set(rules.CLOSED) & set(rules.OVERDUE)
    assert rules.STATUS_LABELS["achieved"] == "Achieved" and rules.STATUS_LABELS["missed"] == "Missed"
    assert set(rules.STATUS_LABELS) == set(rules.READING_STATUSES)
