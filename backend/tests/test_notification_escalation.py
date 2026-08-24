"""Escalation fires on crossings, not on state (#822 phase 4).

The defect this guards against is not subtle and it is not hypothetical: the
scheduler runs daily and asks "is this overdue?", which is a question about
*state*, so a task that escalated on Monday escalated again on Tuesday and
every day after. Thirty days of one stalled task is thirty notifications, and a
team that has muted the platform.

No database. :func:`~services.notification_escalation.escalation_threshold` is
pure — dates and the dates of what has already been sent go in, a threshold or
``None`` comes out — so every branch is a two-line test, including the ones a
DB-backed test would never reach (a scheduler that was switched off for a
month, an item imported with a historic due date).

The headline case #822 asks for is
:func:`test_five_days_overdue_produces_exactly_one_escalation`, which runs the
real function once per simulated day rather than asserting a single call.
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.notification_escalation import (  # noqa: E402
    ESCALATION_THRESHOLD_DAYS,
    escalation_threshold,
)

DUE = date(2026, 8, 1)


def _run_daily(days: int, due: date = DUE):
    """Run the scheduler once a day for ``days`` days after the due date.

    Returns the list of ``(day, threshold)`` pairs on which an escalation
    fired, recording each fire as a notification dated that day — which is
    exactly what the real scheduler does when it writes the row.
    """
    sent: list = []
    fired: list = []
    for offset in range(1, days + 1):
        today = due + timedelta(days=offset)
        threshold = escalation_threshold(
            due_date=due, today=today, prior_notification_dates=sent,
        )
        if threshold is not None:
            fired.append((offset, threshold))
            sent.append(today)
    return fired


# ---------------------------------------------------------------------------
# The acceptance criterion, stated as #822 states it
# ---------------------------------------------------------------------------

def test_five_days_overdue_produces_exactly_one_escalation():
    """#822: "a test asserts a task overdue for 5 days produced exactly one".

    Five scheduler runs, one notification. Before this change the same five
    runs produced five.
    """
    fired = _run_daily(5)

    assert len(fired) == 1, f"expected one escalation in five days, got {fired}"
    assert fired[0] == (1, 0), "it should fire on the day it became overdue"


def test_thirty_days_produces_three_escalations_not_thirty():
    """The 0 / +7 / +30 ladder, over a month of daily runs."""
    fired = _run_daily(30)

    assert [day for day, _ in fired] == [1, 7, 30]
    assert [threshold for _, threshold in fired] == [0, 7, 30]


def test_no_further_escalation_after_the_last_threshold():
    """Past +30 the item stops escalating rather than resuming daily."""
    fired = _run_daily(90)

    assert len(fired) == 3, f"expected three escalations in ninety days, got {fired}"


# ---------------------------------------------------------------------------
# The boundaries
# ---------------------------------------------------------------------------

def test_not_overdue_on_the_due_date_itself():
    """Due today is not overdue. The approach-to-deadline warning is a
    different notification with a different cadence."""
    assert escalation_threshold(
        due_date=DUE, today=DUE, prior_notification_dates=[],
    ) is None


def test_not_overdue_before_the_due_date():
    assert escalation_threshold(
        due_date=DUE, today=DUE - timedelta(days=3), prior_notification_dates=[],
    ) is None


def test_first_run_after_the_due_date_fires_the_zero_threshold():
    assert escalation_threshold(
        due_date=DUE, today=DUE + timedelta(days=1), prior_notification_dates=[],
    ) == 0


@pytest.mark.parametrize("day", [2, 3, 4, 5, 6])
def test_days_two_to_six_are_suppressed_by_day_ones_notification(day):
    assert escalation_threshold(
        due_date=DUE,
        today=DUE + timedelta(days=day),
        prior_notification_dates=[DUE + timedelta(days=1)],
    ) is None


def test_day_seven_fires_even_though_day_one_already_did():
    """A notification dated day 1 is *before* the +7 trigger date, so it
    cannot suppress it. This is the whole derivation in one assertion."""
    assert escalation_threshold(
        due_date=DUE,
        today=DUE + timedelta(days=7),
        prior_notification_dates=[DUE + timedelta(days=1)],
    ) == 7


def test_day_seven_is_suppressed_once_it_has_fired():
    assert escalation_threshold(
        due_date=DUE,
        today=DUE + timedelta(days=7),
        prior_notification_dates=[DUE + timedelta(days=1), DUE + timedelta(days=7)],
    ) is None


# ---------------------------------------------------------------------------
# The cases a database-backed test would not think to produce
# ---------------------------------------------------------------------------

def test_a_long_dormant_item_escalates_once_not_three_times():
    """A scheduler switched off for six weeks, or an item imported with a
    historic due date, must not emit a backlog of three notifications for
    deadlines that all passed before anybody could act on them."""
    fired = escalation_threshold(
        due_date=DUE, today=DUE + timedelta(days=40), prior_notification_dates=[],
    )

    assert fired == 30, "the highest threshold reached, not every threshold"


def test_the_run_after_a_dormant_catch_up_is_quiet():
    assert escalation_threshold(
        due_date=DUE,
        today=DUE + timedelta(days=41),
        prior_notification_dates=[DUE + timedelta(days=40)],
    ) is None


def test_a_lost_run_is_late_by_a_day_not_lost():
    """A run that died before writing anything leaves no row, so tomorrow's
    run fires the same threshold. Self-repairing, which a stored counter
    would not be."""
    assert escalation_threshold(
        due_date=DUE, today=DUE + timedelta(days=2), prior_notification_dates=[],
    ) == 0


def test_none_dates_are_ignored_rather_than_crashing():
    """``created_at`` has a server default but is nullable in the model, and a
    scheduler must not die on one odd row."""
    assert escalation_threshold(
        due_date=DUE,
        today=DUE + timedelta(days=1),
        prior_notification_dates=[None],
    ) == 0


def test_thresholds_are_the_documented_ladder():
    """Pinned so the ladder cannot be widened without the tests above being
    reconsidered — they encode 0 / 7 / 30 in their expected values."""
    assert tuple(ESCALATION_THRESHOLD_DAYS) == (0, 7, 30)
