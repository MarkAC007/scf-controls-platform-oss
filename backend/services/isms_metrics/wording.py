# SPDX-License-Identifier: AGPL-3.0-only
"""Plain-English wording for ISMS metrics: pure functions, no database.

Used for the starters, the wording endpoint and every metric save, so the
evaluation text and the target in words always say the same thing as the
numbers stored beside them.
"""
from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Any, Dict, Optional

DASH = "—"


def _number(value: Any) -> str:
    """``90`` for whole numbers, ``92.5`` otherwise."""
    number = Decimal(str(value))
    if number == number.to_integral_value():
        return str(int(number))
    return format(number.normalize(), "f")


def _with_unit(unit: Optional[str], value: Any) -> str:
    return f"{_number(value)}%" if unit == "percent" else _number(value)


def _side(number: Decimal, threshold: Decimal) -> int:
    return (number > threshold) - (number < threshold)


def _shown(value: Any, places: tuple, thresholds: tuple) -> Decimal:
    """``value`` rounded to the fewest of ``places`` decimals that keeps it on
    its own side of every threshold.

    A whole number can read better than the facts: 99.5 against "at least
    100" prints as 100 beside "At risk". So the rounded figure must compare
    with the target and the warning level exactly as the real one does. If
    half-up rounding cannot do that at the finest place, round away from the
    threshold instead.
    """
    true = Decimal(str(value))
    marks = [Decimal(str(t)) for t in thresholds if t is not None]

    def faithful(shown: Decimal) -> bool:
        return all(_side(shown, t) == _side(true, t) for t in marks)

    for count in places:
        shown = true.quantize(Decimal(1).scaleb(-count), rounding=ROUND_HALF_UP)
        if faithful(shown):
            return shown
    finest = Decimal(1).scaleb(-places[-1])
    for rounding in (ROUND_FLOOR, ROUND_CEILING):
        shown = true.quantize(finest, rounding=rounding)
        if faithful(shown):
            return shown
    return true.quantize(finest, rounding=ROUND_HALF_UP)


def value_text(
    unit: Optional[str], value: Any, kind: Optional[str] = None, target: Any = None, warn: Any = None,
) -> str:
    """``"14%"`` (whole number), ``"16"``, ``"Done"`` / ``"Not done"``; ``"—"`` when there is no value.

    With a ``target`` or ``warn`` level, a percentage gains one or two decimal
    places only when the whole number would sit on the other side of that
    level (``"99.5%"`` against at least 100%).
    """
    if value is None:
        return DASH
    if kind == "milestone":
        return "Done" if float(value) >= 1 else "Not done"
    if unit == "percent":
        return f"{_number(_shown(value, (0, 1, 2), (target, warn)))}%"
    return _number(_shown(value, (2, 3, 4), (target, warn)))


def pace_texts(unit: Optional[str], value: Any, expected: Any) -> tuple:
    """Today's value and the expected value, printed together for the pace line.

    Both carry the same number of decimals: the fewest of 0, 1, 2 at which the
    two printed numbers compare the way the true ones do (less, equal, greater),
    so "Behind pace" never sits beside two equal numbers. At 2 decimals it
    stops, whatever the result. Percent sign for a percentage, none for a count.
    """
    true_value, true_expected = Decimal(str(value)), Decimal(str(expected))
    order = _side(true_value, true_expected)
    for count in (0, 1, 2):
        step = Decimal(1).scaleb(-count)
        shown = [n.quantize(step, rounding=ROUND_HALF_UP) for n in (true_value, true_expected)]
        if _side(*shown) == order:
            break
    suffix = "%" if unit == "percent" else ""
    return tuple(f"{n:.{count}f}{suffix}" for n in shown)


def target_text(unit: Optional[str], direction: Optional[str], target: Any, warn: Any = None) -> str:
    """``"at least 90%"`` / ``"at most 0"``; ``""`` with no target."""
    if target is None:
        return ""
    word = "at most" if direction == "at_most" else "at least"
    return f"{word} {_with_unit(unit, target)}"


def _warning(unit: Optional[str], direction: Optional[str], warn: Any) -> str:
    side = "above" if direction == "at_most" else "below"
    return f"warning {side} {_with_unit(unit, warn)}"


def change_text(unit: Optional[str], delta: float, days: int) -> str:
    """``"up 2 points in the last 60 days, to the last stored measurement"`` (percent), the same
    without "points" for a count, or no change.

    It runs between stored results, never to today's live figure, and says so.
    One decimal place at most, rounded half up; a change that rounds to nothing reads as no change.
    """
    ends = f"in the last {days} days, to the last stored measurement"
    shown = Decimal(str(abs(delta))).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    if shown == 0:
        return f"no change {ends}"
    word = "up" if delta > 0 else "down"
    points = " points" if unit == "percent" else ""
    if unit == "percent" and shown == 1:
        points = " point"
    return f"{word} {_number(shown)}{points} {ends}"


def target_label(unit: Optional[str], direction: Optional[str], target: Any, warn: Any = None) -> str:
    """``"at least 90% · warning below 75%"`` / ``"at most 0 · warning above 3"``; ``""`` with no target."""
    text = target_text(unit, direction, target)
    if not text:
        return ""
    if warn is not None:
        text += f" · {_warning(unit, direction, warn)}"
    return text


def _target_sentence(unit, direction, target, warn) -> str:
    text = target_text(unit, direction, target)
    if not text:
        return "Target: not set yet."
    if warn is not None:
        text += f"; {_warning(unit, direction, warn)}"
    return f"Target: {text}."


def formula_text(measure: Any, params: Optional[Dict[str, Any]] = None) -> str:
    """The measure's rule in words with its settings filled in."""
    if measure is None:
        return ""
    from services.isms_metrics.library import resolve_params
    return measure.formula.format(**resolve_params(measure, params))


def metric_name(measure: Any, params: Optional[Dict[str, Any]] = None, unit: Optional[str] = None) -> str:
    """``"Risks within appetite (%)"``; the measure's name with its settings and unit."""
    from services.isms_metrics.library import resolve_params
    name = measure.name.format(**resolve_params(measure, params))
    suffix = "%" if (unit or measure.unit) == "percent" else "count"
    return f"{name} ({suffix})"


def evaluation_text(
    measure: Any,
    params: Optional[Dict[str, Any]],
    kind: str,
    unit: Optional[str],
    direction: Optional[str],
    target: Any,
    warn: Any,
    frequency: Optional[str],
    name: Optional[str] = None,
) -> str:
    """How results are evaluated, in one short paragraph."""
    if kind == "milestone":
        return "Done or not done by the due date."
    target_part = _target_sentence(unit, direction, target, warn)
    if kind == "live" and measure is not None:
        return (
            f"{formula_text(measure, params)} {target_part} "
            f"Measured nightly by the platform from {measure.source_label}."
        )
    label = (name or "The metric").strip() or "The metric"
    return f"{label}, entered {frequency or 'regularly'} by the responsible team. {target_part}"


def metric_description(measure: Any, kind: str, frequency: Optional[str]) -> str:
    """``"Live from the Risk Register, measured nightly."`` and the like."""
    if kind == "live" and measure is not None:
        return f"Live from the {measure.source_label}, measured nightly."
    if kind == "logged":
        return f"Logged {frequency or 'regularly'} by the responsible team."
    if kind == "milestone":
        return "Done or not done by the due date."
    return ""


def wording(
    measure: Any,
    kind: str,
    params: Optional[Dict[str, Any]] = None,
    name: Optional[str] = None,
    unit: Optional[str] = None,
    direction: Optional[str] = None,
    target: Any = None,
    warn: Any = None,
    frequency: Optional[str] = None,
) -> Dict[str, str]:
    """Everything the form fills in for a metric choice (the ``Wording`` shape)."""
    if kind == "live" and measure is not None:
        unit = measure.unit
        direction = direction or measure.direction
        frequency = "nightly"
        title = metric_name(measure, params, unit)
    elif kind == "milestone":
        unit, direction, target, warn, frequency = None, None, None, None, None
        title = (name or "").strip() or "Milestone"
    else:
        direction = direction or "at_least"
        title = (name or "").strip()
    return {
        "evaluation_method": evaluation_text(measure, params, kind, unit, direction, target, warn, frequency, title),
        "metric_name": title,
        "metric_description": metric_description(measure, kind, frequency),
        "target_text": target_text(unit, direction, target),
        "target_label": target_label(unit, direction, target, warn),
    }
