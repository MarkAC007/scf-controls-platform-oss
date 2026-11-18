# SPDX-License-Identifier: AGPL-3.0-only
"""Who counts in the KSI evidence-metrics SQL.

Two rules, checked on the statement text of all eight variants (no database,
same style as ``test_attested_ksi_sql.py``):

1. Evidence counts toward a theme only through controls the organisation has
   in scope. The coverage axis divides by the scoped controls, so a control
   outside scope must not add to the numerator ("177 of 108 controls").
2. In the composite-aware variants, "this control has evidence" is read from
   the organisation's side of the LEFT JOIN. The catalogue's own evidence id
   is never NULL, and reading that one reported every control with a
   catalogue evidence item as covered: evidence coverage 100% on every theme.
"""
from __future__ import annotations

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.capability_themes import (  # noqa: E402
    _EVIDENCE_METRICS_COMPOSITE_AWARE_SQL,
    _EVIDENCE_METRICS_COMPOSITE_AWARE_SQL_ATTESTED,
    _EVIDENCE_METRICS_COMPOSITE_AWARE_WINDOW_SQL,
    _EVIDENCE_METRICS_COMPOSITE_AWARE_WINDOW_SQL_ATTESTED,
    _EVIDENCE_METRICS_SQL,
    _EVIDENCE_METRICS_SQL_ATTESTED,
    _EVIDENCE_METRICS_WINDOW_AWARE_SQL,
    _EVIDENCE_METRICS_WINDOW_AWARE_SQL_ATTESTED,
)

COMPOSITE = {
    "composite": _EVIDENCE_METRICS_COMPOSITE_AWARE_SQL,
    "composite_attested": _EVIDENCE_METRICS_COMPOSITE_AWARE_SQL_ATTESTED,
    "composite_window": _EVIDENCE_METRICS_COMPOSITE_AWARE_WINDOW_SQL,
    "composite_window_attested": _EVIDENCE_METRICS_COMPOSITE_AWARE_WINDOW_SQL_ATTESTED,
}
ALL = {
    "per_file": _EVIDENCE_METRICS_SQL,
    "per_file_attested": _EVIDENCE_METRICS_SQL_ATTESTED,
    "window": _EVIDENCE_METRICS_WINDOW_AWARE_SQL,
    "window_attested": _EVIDENCE_METRICS_WINDOW_AWARE_SQL_ATTESTED,
    **COMPOSITE,
}

SCOPED_JOIN = re.compile(
    r"JOIN scoped_controls sc ON sc\.scf_id = (\w+)\.scf_id\s+"
    r"AND sc\.organization_id = :org_id AND sc\.selected = true"
)


def _cte(sql, name: str) -> str:
    """The body of one named CTE: from `name AS (` to its closing bracket."""
    text = str(sql)
    start = text.index(f"{name} AS (")
    depth = 0
    for pos in range(text.index("(", start), len(text)):
        if text[pos] == "(":
            depth += 1
        elif text[pos] == ")":
            depth -= 1
            if depth == 0:
                return text[start:pos]
    raise AssertionError(f"{name}: no closing bracket")


@pytest.mark.parametrize("variant", sorted(ALL))
def test_evidence_reaches_a_theme_only_through_scoped_controls(variant):
    body = _cte(ALL[variant], "evidence_theme_map")
    match = SCOPED_JOIN.search(body)
    assert match, f"{variant}: evidence_theme_map does not join the scoped controls"
    assert match.group(1) == "ecm"


@pytest.mark.parametrize("variant", sorted(COMPOSITE))
def test_a_composite_counts_only_for_a_scoped_control(variant):
    body = _cte(COMPOSITE[variant], "composite_theme")
    match = SCOPED_JOIN.search(body)
    assert match, f"{variant}: composite_theme does not join the scoped controls"
    assert match.group(1) == "cs"


@pytest.mark.parametrize("variant", sorted(COMPOSITE))
def test_the_fallback_signal_is_read_from_the_organisations_side(variant):
    body = _cte(COMPOSITE[variant], "fallback_theme")
    columns = body[: body.index("FROM evidence_theme_map")]
    assert "fu.evidence_id" in columns
    assert "etm.evidence_id" not in columns
    # and the outer query still derives the signal from that column
    assert "(evidence_id IS NOT NULL) AS has_signal" in str(COMPOSITE[variant])


@pytest.mark.parametrize("variant", sorted(ALL))
def test_the_organisation_stays_a_bind_parameter(variant):
    text = str(ALL[variant])
    assert ":org_id" in text
    assert "sc.organization_id = :org_id" in text
