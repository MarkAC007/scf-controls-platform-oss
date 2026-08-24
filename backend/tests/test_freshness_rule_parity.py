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


# ---------------------------------------------------------------------------
# The anchor, not just the band
# ---------------------------------------------------------------------------
#
# The multiplier was only half of what the legend promises. The other half is
# what the days are counted FROM, and #57 changed it from the upload date to the
# coverage date. The legend went on saying "last upload" — describing, with the
# platform's full authority, a rule the backend had stopped applying.


def _rule_text() -> str:
    """Just the user-facing strings, not the file's own explanatory comments."""
    text = FRONTEND_RULE.read_text()
    return text[text.index("export const FRESHNESS_RULE"):]


@pytest.mark.skipif(
    not FRONTEND_RULE.exists(),
    reason="webclient not present (backend-only checkout)",
)
def test_the_legend_does_not_promise_an_anchor_the_backend_abandoned():
    rule = _rule_text().lower()

    # "upload" may appear only where the copy is describing the fallback, which
    # it has to name as a fallback for the sentence to be true.
    for line in rule.splitlines():
        if "upload" in line:
            assert "fall" in line or "asserted" in line, (
                f"freshness copy still anchors on the upload date: {line.strip()}"
            )


@pytest.mark.skipif(
    not FRONTEND_RULE.exists(),
    reason="webclient not present (backend-only checkout)",
)
def test_the_legend_names_the_anchor_the_backend_actually_uses():
    """The always-visible legend, specifically.

    Checking the whole file would pass on the per-status tooltips alone, and
    those are only read on hover. The legend is the one string every user of the
    dashboard sees, so it is the one that has to carry the disclosure: what the
    days are counted from, and that the upload date is a fallback rather than
    the rule.
    """
    text = _rule_text()
    legend = text[text.index("FRESHNESS_LEGEND"):].lower()

    assert "cover" in legend, "the legend never says what the age is counted from"
    assert "fall" in legend, "the legend never discloses the upload-date fallback"


def test_the_backend_really_does_anchor_on_coverage():
    """Guards the two above from passing against a backend that reverted."""
    source = inspect.getsource(evidence_health)

    assert "days_since_coverage, threshold_days" in source
    assert "effective_period_end" in source
