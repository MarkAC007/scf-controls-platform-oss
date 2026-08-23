"""The freshness rule the dashboard states must be the one the backend applies.

`_calculate_status` in `api/evidence_health.py` grades an item green / amber /
red against its own threshold with a 1.5x grace band. The dashboard now prints
that rule to the user (`webclient/src/data/freshnessRule.ts`). Two copies of one
constant in two languages drift silently: the backend widens the band, the UI
keeps promising the old one, and the promise is wrong in the direction that
makes an overdue item look acceptable.

Nothing else crosses this boundary, so nothing else would catch it.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from api import evidence_health

FRONTEND_RULE = (
    Path(__file__).resolve().parents[2]
    / "webclient"
    / "src"
    / "data"
    / "freshnessRule.ts"
)


def _backend_multiplier() -> float:
    """The literal multiplier in the amber branch of _calculate_status."""
    source = inspect.getsource(evidence_health._calculate_status)
    match = re.search(r"threshold_days\s*\*\s*([0-9]*\.?[0-9]+)", source)
    assert match, f"no grace multiplier found in:\n{source}"
    return float(match.group(1))


def _frontend_multiplier() -> float:
    text = FRONTEND_RULE.read_text()
    match = re.search(
        r"AMBER_GRACE_MULTIPLIER\s*=\s*([0-9]*\.?[0-9]+)",
        text,
    )
    assert match, "AMBER_GRACE_MULTIPLIER not found in freshnessRule.ts"
    return float(match.group(1))


@pytest.mark.skipif(
    not FRONTEND_RULE.exists(),
    reason="webclient not present (backend-only checkout)",
)
def test_grace_multiplier_matches_across_the_stack():
    assert _frontend_multiplier() == _backend_multiplier()


def test_backend_still_grades_against_a_multiple_of_the_threshold():
    """Guards the parity test above from passing because it found nothing.

    If _calculate_status is rewritten to use a fixed number of days instead of a
    multiple, the regex stops matching and the assertion in _backend_multiplier
    fires here rather than leaving the parity test vacuously green.
    """
    assert _backend_multiplier() > 1.0
