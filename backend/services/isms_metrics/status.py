# SPDX-License-Identifier: AGPL-3.0-only
"""Performance status of a security objective: pure rules, no database.

``derive_status`` applies the rules in order and the first that matches wins:

0. An objective marked ``achieved`` or ``missed`` shows that final state and
   is no longer judged: ``achieved`` / ``missed``. These two are reading
   statuses only; no result row is ever stored with them.
1. No active metric, or a metric still ``unset`` (text carried over from the
   old list): ``not_measured``.
2. A ``proposed`` objective shows its value but is not judged: ``proposed``.
3. Milestone: done (latest logged value of 1) is ``on_track``; not done past
   the due date is ``off_track``; otherwise ``in_progress``.
4. Logged: no entry yet is ``first_measurement_due``; the newest entry for a
   period before the current one is ``measurement_overdue``.
5. No value: ``insufficient_data``.
6. A live percentage over no population, or one below the measure's minimum,
   is ``insufficient_data``. Counts are exempt: zero is a real reading.
7. No target: ``not_measured``.
8. An ``achieve`` objective before its due date is ``in_progress``; past the
   date it is judged like any other.
9. Against the target and the warning level: ``on_track``, ``at_risk`` or
   ``off_track``.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Optional

from services.isms_metrics import wording

ON_TRACK = "on_track"
AT_RISK = "at_risk"
OFF_TRACK = "off_track"
IN_PROGRESS = "in_progress"
PROPOSED = "proposed"
INSUFFICIENT_DATA = "insufficient_data"
FIRST_MEASUREMENT_DUE = "first_measurement_due"
MEASUREMENT_OVERDUE = "measurement_overdue"
NOT_MEASURED = "not_measured"
ACHIEVED = "achieved"
MISSED = "missed"

#: Every status a stored result row may carry (the database CHECK pins this set).
STATUSES = (
    ON_TRACK, AT_RISK, OFF_TRACK, IN_PROGRESS, PROPOSED,
    INSUFFICIENT_DATA, FIRST_MEASUREMENT_DUE, MEASUREMENT_OVERDUE, NOT_MEASURED,
)
#: The final state of a closed objective. It has the same name as the
#: lifecycle value, appears on a reading only, and is never stored on a result.
CLOSED = (ACHIEVED, MISSED)
#: Every status a reading may carry.
READING_STATUSES = STATUSES + CLOSED

#: Statuses counted under "Needs attention" on the dashboard.
NEEDS_ATTENTION = (PROPOSED, INSUFFICIENT_DATA, FIRST_MEASUREMENT_DUE, MEASUREMENT_OVERDUE, NOT_MEASURED)
#: Statuses counted under "overdue".
OVERDUE = (FIRST_MEASUREMENT_DUE, MEASUREMENT_OVERDUE)

STATUS_LABELS = {
    ON_TRACK: "On track",
    AT_RISK: "At risk",
    OFF_TRACK: "Off track",
    IN_PROGRESS: "In progress",
    PROPOSED: "Proposed",
    INSUFFICIENT_DATA: "Insufficient data",
    FIRST_MEASUREMENT_DUE: "First measurement due",
    MEASUREMENT_OVERDUE: "Measurement overdue",
    NOT_MEASURED: "Not measured",
    ACHIEVED: "Achieved",
    MISSED: "Missed",
}


def closed_status(lifecycle: Optional[str]) -> Optional[str]:
    """``achieved`` / ``missed`` for a closed objective, else None."""
    return lifecycle if lifecycle in CLOSED else None

PERIOD_FREQUENCIES = ("monthly", "quarterly", "annually")


def period_start(frequency: Optional[str], day: date) -> Optional[date]:
    """First day of the calendar period containing ``day``; None for other frequencies."""
    if frequency == "monthly":
        return day.replace(day=1)
    if frequency == "quarterly":
        return date(day.year, 3 * ((day.month - 1) // 3) + 1, 1)
    if frequency == "annually":
        return date(day.year, 1, 1)
    return None


_PERIOD_MONTHS = {"monthly": 1, "quarterly": 3, "annually": 12}


def period_end(frequency: Optional[str], day: date) -> Optional[date]:
    """Last day of the calendar period containing ``day``; None for other frequencies."""
    start = period_start(frequency, day)
    if start is None:
        return None
    years, month = divmod(start.month - 1 + _PERIOD_MONTHS[frequency], 12)
    return date(start.year + years, month + 1, 1) - timedelta(days=1)


def _get(metric: Any, name: str) -> Any:
    if isinstance(metric, dict):
        return metric.get(name)
    return getattr(metric, name, None)


def _num(value: Any) -> Optional[float]:
    return None if value is None else float(value)


def derive_status(
    lifecycle: str,
    objective_type: str,
    due_date: Optional[date],
    metric: Any,
    value: Optional[float],
    denominator: Optional[int],
    latest_logged_period: Optional[date],
    has_logged: bool,
    today: date,
) -> str:
    """The performance status; see the module docstring for the rules.

    ``metric`` is the active metric row (or anything with the same attribute
    or key names: kind, unit, direction, target_value, warn_value,
    min_population, frequency). For logged and milestone metrics ``value`` is
    the newest entry's value.
    """
    if lifecycle in CLOSED:
        return lifecycle
    kind = _get(metric, "kind") if metric is not None else None
    if metric is None or kind in (None, "unset"):
        return NOT_MEASURED
    if lifecycle == PROPOSED:
        return PROPOSED

    if kind == "milestone":
        if value is not None and float(value) >= 1:
            return ON_TRACK
        if due_date is not None and today > due_date:
            return OFF_TRACK
        return IN_PROGRESS

    if kind == "logged":
        if not has_logged:
            return FIRST_MEASUREMENT_DUE
        current = period_start(_get(metric, "frequency"), today)
        if current is not None and (latest_logged_period is None or latest_logged_period < current):
            return MEASUREMENT_OVERDUE

    if value is None:
        return INSUFFICIENT_DATA

    unit = _get(metric, "unit")
    if kind == "live" and unit == "percent":
        minimum = _get(metric, "min_population") or 0
        if not denominator or denominator < minimum:
            return INSUFFICIENT_DATA

    target = _num(_get(metric, "target_value"))
    if target is None:
        return NOT_MEASURED

    if objective_type == "achieve" and due_date is not None and today <= due_date:
        return IN_PROGRESS

    warn = _num(_get(metric, "warn_value"))
    reading = float(value)
    if _get(metric, "direction") == "at_most":
        if reading <= target:
            return ON_TRACK
        if warn is not None and reading <= warn:
            return AT_RISK
        return OFF_TRACK
    if reading >= target:
        return ON_TRACK
    if warn is not None and reading >= warn:
        return AT_RISK
    return OFF_TRACK


# ---------------------------------------------------------------------------
# Pace for an "achieve by a date" objective (phase 3, contract A2)
# ---------------------------------------------------------------------------

ON_PACE = "on_pace"
BEHIND = "behind"
PACE_LABELS = {ON_PACE: "On pace", BEHIND: "Behind pace"}


def pace(
    objective_type: Optional[str], lifecycle: Optional[str], archived: bool, due_date: Optional[date],
    metric: Any, value: Any, baseline_on: Optional[date], baseline_value: Any, today: date,
) -> Optional[dict]:
    """Where a straight line from the first stored result to the target says the value should be today.

    None unless every condition holds: an ``achieve`` objective, ``active`` and not
    archived, a due date not yet passed, a numeric target on a percent or count
    metric with a direction, a numeric value today, and a baseline dated before
    the due date. A value that already meets the target today is on pace;
    otherwise behind is strictly the wrong side of the expected value, and equal
    is on pace.

    Pace is information only: it never changes the reading's status.
    ``value_text`` and ``expected_text`` are the two numbers worded together
    (``wording.pace_texts``): the same decimals, never equal unless the true
    values are (up to 2 decimals).
    """
    target = _num(_get(metric, "target_value")) if metric is not None else None
    unit = _get(metric, "unit") if metric is not None else None
    direction = _get(metric, "direction") if metric is not None else None
    if (objective_type != "achieve" or lifecycle != "active" or archived or due_date is None
            or today > due_date or target is None or unit not in ("percent", "count")
            or direction not in ("at_least", "at_most") or value is None or baseline_on is None
            or baseline_value is None or baseline_on >= due_date):
        return None
    current, start = float(value), float(baseline_value)
    span = (due_date - baseline_on).days
    elapsed = min(max((today - baseline_on).days, 0), span)
    expected = start + (target - start) * elapsed / span
    meets_now = current >= target if direction == "at_least" else current <= target
    behind = (current < expected) if direction == "at_least" else (current > expected)
    state = ON_PACE if meets_now or not behind else BEHIND
    value_text, expected_text = wording.pace_texts(unit, current, expected)
    return {
        "state": state, "label": PACE_LABELS[state],
        "expected_value": round(expected, 4), "value_text": value_text, "expected_text": expected_text,
        "baseline_on": baseline_on, "baseline_value": start,
        "due_date": due_date, "target_value": target,
    }
