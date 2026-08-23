"""Collection-frequency vocabulary — the single source of truth (#783).

Before this module, "collection frequency" was declared independently in four
places that disagreed with each other:

  * ``services/validation_service.STALENESS_THRESHOLDS``   (freshness)
  * ``services/composite_service.STALENESS_THRESHOLDS``    (a hand-copy of the above)
  * ``services/task_generator.FREQUENCY_DAYS``             (task generation)
  * ``webclient/.../CollectionWizardSteps.FREQUENCY_OPTIONS`` (what users can pick)

Two of the six values the wizard offered were broken, in two subsystems, in two
different ways: ``annually`` had no freshness key (so a 370-day control was
judged against 30 days) and ``real_time`` had no task-generation key (so the
record was silently skipped with a WARNING nobody could see).

Every consumer now imports from here. There is one canonical spelling per
concept, one alias table absorbing every historical spelling, and one place to
change when the vocabulary changes.

**Import weight matters.** ``composite_service`` previously inlined its copy of
the thresholds specifically to avoid pulling in ``validation_service``'s
transitive boto3/storage imports inside Celery workers. This module is
therefore **stdlib-only, by contract** — no sqlalchemy, no boto3, no models.
Adding a non-stdlib import here re-breaks that, and
``tests/test_frequency_vocabulary.py`` asserts it.

The frontend half lives at ``webclient/src/data/frequencyVocabulary.ts`` and is
held in lockstep by a parity test in the same test module.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

__all__ = [
    "CANONICAL_FREQUENCIES",
    "STALENESS_DAYS",
    "TASK_INTERVAL_DAYS",
    "ALIASES",
    "UI_OPTIONS",
    "DEFAULT_STALENESS_DAYS",
    "normalize",
    "staleness_days",
    "task_interval_days",
    "is_time_based",
]


# ---------------------------------------------------------------------------
# Canonical vocabulary
# ---------------------------------------------------------------------------
#: Every legal value of ``evidence_tracking.frequency``, in cadence order.
#: Order is load-bearing for the UI dropdown — do not sort alphabetically.
CANONICAL_FREQUENCIES: Tuple[str, ...] = (
    "real_time",
    "daily",
    "weekly",
    "biweekly",
    "monthly",
    "quarterly",
    "semi_annual",
    "annual",
    "on_demand",
)

#: Age in days beyond which a piece of evidence is stale for that cadence.
#: Each value is the nominal interval plus a grace period, so a collection that
#: runs on schedule never flickers amber on the day it is due.
#:
#: The values for real_time / daily / weekly / monthly / quarterly / annual /
#: on_demand are carried over BYTE-FOR-BYTE from the pre-#783
#: ``validation_service.STALENESS_THRESHOLDS`` — consolidating the maps must not
#: silently re-tune anyone's red/amber/green.
#:
#: ``biweekly`` and ``semi_annual`` are NEW keys. They previously had no entry
#: and fell through to a 30-day default, which was never intentional for a
#: six-monthly control. Adding them DOES change the RAG state for orgs using
#: those values — that is the point, but it is a real behaviour change.
STALENESS_DAYS: Dict[str, int] = {
    "real_time": 2,
    "daily": 2,
    "weekly": 9,
    "biweekly": 16,      # new key — was falling through to 30
    "monthly": 35,
    "quarterly": 95,
    "semi_annual": 185,  # new key — was falling through to 30
    "annual": 370,
    "on_demand": 35,     # treat like monthly
}

#: Days between generated collection tasks. ``None`` means *this cadence does
#: not produce scheduled tasks* — an explicit, non-error state.
#:
#: ``real_time`` is None by design: real-time collection means a collector
#: pushes continuously, so a daily "Collect Evidence" task for it is pure noise,
#: and its 2-day staleness threshold already detects a collector that has died.
#: Before #783 it produced the same outcome (no task) via a "Invalid frequency"
#: WARNING and a bare ``continue`` — the behaviour was right and the reporting
#: was a lie. This makes the intent explicit.
TASK_INTERVAL_DAYS: Dict[str, Optional[int]] = {
    "real_time": None,
    "daily": 1,
    "weekly": 7,
    "biweekly": 14,
    "monthly": 30,
    "quarterly": 90,
    "semi_annual": 180,
    "annual": 365,
    "on_demand": None,
}

#: Every historical spelling that has ever been written to the column, mapped to
#: its canonical value. Sources: the wizard dropdown (``annually``), the old
#: free-text placeholder (``Annual``), ``task_generator.FREQUENCY_DAYS`` legacy
#: keys, and ``task_generator.SKIP_FREQUENCIES``.
#:
#: Keys here are already lowercased/stripped — ``normalize()`` does that first.
ALIASES: Dict[str, str] = {
    # cadence spellings
    "realtime": "real_time",
    "real time": "real_time",
    "real-time": "real_time",
    "continuous": "real_time",
    "continuously": "real_time",
    "ongoing": "real_time",
    "day": "daily",
    "week": "weekly",
    "bi-weekly": "biweekly",
    "bi weekly": "biweekly",
    "fortnightly": "biweekly",
    "every 2 weeks": "biweekly",
    "month": "monthly",
    "quarter": "quarterly",
    "semi-annual": "semi_annual",
    "semi annual": "semi_annual",
    "semi-annually": "semi_annual",
    "semi annually": "semi_annual",
    "semiannual": "semi_annual",
    "semiannually": "semi_annual",
    "biannual": "semi_annual",
    "biannually": "semi_annual",
    "6 monthly": "semi_annual",
    "six monthly": "semi_annual",
    "annually": "annual",
    "yearly": "annual",
    "year": "annual",
    "per annum": "annual",
    # non-time-based spellings
    "on demand": "on_demand",
    "on-demand": "on_demand",
    "ondemand": "on_demand",
    "as required": "on_demand",
    "as needed": "on_demand",
    "ad hoc": "on_demand",
    "ad-hoc": "on_demand",
    "adhoc": "on_demand",
    "event driven": "on_demand",
    "event-driven": "on_demand",
}

#: Values offered in the UI dropdown, in display order. This is deliberately a
#: SUBSET of ``CANONICAL_FREQUENCIES`` — ``biweekly`` and ``semi_annual`` are
#: accepted on the write path (legacy rows and bulk imports use them) but are
#: not promoted to new users. Change the subset here, not in the frontend.
UI_OPTIONS: List[Dict[str, str]] = [
    {"value": "real_time", "label": "Real-time"},
    {"value": "daily", "label": "Daily"},
    {"value": "weekly", "label": "Weekly"},
    {"value": "monthly", "label": "Monthly"},
    {"value": "quarterly", "label": "Quarterly"},
    {"value": "semi_annual", "label": "Semi-annually"},
    {"value": "annual", "label": "Annually"},
    {"value": "on_demand", "label": "On demand"},
]

#: Fallback used only when no frequency is configured at all. A frequency that
#: IS configured but unrecognised must never land here silently — that is the
#: ``.get(...) or DEFAULT`` shape #783 exists to remove.
DEFAULT_STALENESS_DAYS: int = 30


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------
def normalize(raw: Optional[str]) -> Optional[str]:
    """Return the canonical frequency for ``raw``, or ``None`` if unrecognised.

    Handles the three shapes the column actually holds: canonical values written
    by the dropdown, historical spellings written by the old free-text input,
    and values written by the bulk-import reconciliation path.

    ``None`` means *unrecognised* — callers must decide what to do about it
    rather than substituting a default, so a typo cannot masquerade as monthly.
    """
    if raw is None:
        return None
    key = raw.strip().lower().rstrip(".").strip()
    if not key:
        return None
    # Try the value as written, then with underscores and spaces swapped both
    # ways, so "semi_annually", "semi annually" and "semi-annually" all land.
    for candidate in (key, key.replace("_", " "), key.replace(" ", "_"), key.replace("-", " ")):
        if candidate in STALENESS_DAYS:
            return candidate
        if candidate in ALIASES:
            return ALIASES[candidate]
    return None


def staleness_days(raw: Optional[str]) -> Optional[int]:
    """Days-until-stale for ``raw``, or ``None`` if the value is unrecognised."""
    canonical = normalize(raw)
    if canonical is None:
        return None
    return STALENESS_DAYS[canonical]


def task_interval_days(raw: Optional[str]) -> Optional[int]:
    """Days between generated tasks, or ``None`` for unrecognised OR non-time-based.

    Use :func:`is_time_based` to tell those two apart — the distinction is the
    difference between "we deliberately do not schedule this" and "somebody
    typed something we do not understand".
    """
    canonical = normalize(raw)
    if canonical is None:
        return None
    return TASK_INTERVAL_DAYS[canonical]


def is_time_based(raw: Optional[str]) -> bool:
    """True when ``raw`` is a recognised cadence that produces scheduled tasks."""
    canonical = normalize(raw)
    return canonical is not None and TASK_INTERVAL_DAYS[canonical] is not None
