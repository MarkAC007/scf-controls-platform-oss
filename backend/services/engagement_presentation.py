"""Framework-native presentation for audit engagements.

Increment 2 of the framework-native audit-engagement design
(docs/superpowers/specs/2026-08-19-audit-engagements-design.md).

Re-sequences an engagement's frozen control set under the requested framework's
own clause / Annex A identifiers. The clause outline is *derived on read* from
the catalog framework_mappings (design decision D3) — there is no authored
canonical outline — so only clauses that at least one in-scope control maps to
appear. Evidence is shown live and flagged in/out of the audit window using the
upload-date proxy (D4); nothing is hidden.

This module is deliberately pure (no DB access) so the ordering and tree-assembly
logic is unit-testable in isolation. The API layer fetches the inputs and passes
them in.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

_TOKEN_RE = re.compile(r"\d+|[^\d.]+")


def natural_clause_sort_key(clause_id: str) -> tuple:
    """Sort key giving natural, audit-friendly ordering of framework clause ids.

    Numeric segments compare as integers (so ``5.2`` precedes ``5.10``), and
    alphabetic segments sort after numeric ones at the same position (so the
    numbered management clauses come before the ``A.*`` Annex A controls).
    """
    key: List[Tuple[int, Any]] = []
    for token in _TOKEN_RE.findall(clause_id or ""):
        if token.isdigit():
            key.append((0, int(token)))
        else:
            key.append((1, token))
    return tuple(key)


def _in_window(uploaded_at: Optional[datetime], window: Tuple[Optional[date], Optional[date]]) -> bool:
    """Whether an artifact's upload date sits within the engagement window.

    An unset window bound is treated as open-ended, so an engagement with no
    dates counts every artifact as in-window.
    """
    start, end = window
    if uploaded_at is None:
        return False
    upload_date = uploaded_at.date() if isinstance(uploaded_at, datetime) else uploaded_at
    if start is not None and upload_date < start:
        return False
    if end is not None and upload_date > end:
        return False
    return True


def _build_control_node(
    row: Dict[str, Any],
    live_by_scf: Dict[str, Dict[str, Any]],
    evidence_by_scf: Dict[str, List[Dict[str, Any]]],
    window: Tuple[Optional[date], Optional[date]],
    queries_by_scf: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Any]:
    scf_id = row["scf_id"]
    live = live_by_scf.get(scf_id) or {}

    evidence: List[Dict[str, Any]] = []
    in_window_count = 0
    for art in evidence_by_scf.get(scf_id, []):
        flagged = _in_window(art.get("uploaded_at"), window)
        if flagged:
            in_window_count += 1
        evidence.append({
            "id": art.get("id"),
            "filename": art.get("filename"),
            "uploaded_at": art.get("uploaded_at"),
            "review_status": art.get("review_status"),
            "in_window": flagged,
        })

    return {
        "scf_id": scf_id,
        "control_name": row.get("control_name"),
        "scope_status": row.get("scope_status"),
        "out_of_scope_justification": row.get("out_of_scope_justification"),
        "source_frameworks": row.get("source_frameworks") or [],
        "scoped_control_id": row.get("scoped_control_id"),
        "implementation_status": live.get("implementation_status"),
        "maturity_level": live.get("maturity_level"),
        "owner": live.get("owner"),
        "evidence": evidence,
        "evidence_in_window_count": in_window_count,
        "queries": queries_by_scf.get(scf_id, []),
    }


def build_framework_presentation(
    framework: str,
    scope_rows: List[Dict[str, Any]],
    mappings_by_scf: Dict[str, Dict[str, List[str]]],
    live_by_scf: Dict[str, Dict[str, Any]],
    evidence_by_scf: Dict[str, List[Dict[str, Any]]],
    window: Tuple[Optional[date], Optional[date]],
    queries_by_scf: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    """Assemble the clause-ordered presentation tree for one framework.

    Only controls that map to ``framework`` appear (a control pulled into the
    engagement solely by another framework is excluded from this view). A control
    mapping to several clauses appears under each of them.
    """
    queries_by_scf = queries_by_scf or {}
    # clause_id -> list of control nodes
    clauses: Dict[str, List[Dict[str, Any]]] = {}

    for row in scope_rows:
        scf_id = row["scf_id"]
        clause_ids = (mappings_by_scf.get(scf_id) or {}).get(framework) or []
        if not clause_ids:
            continue  # not part of this framework's view
        node = _build_control_node(row, live_by_scf, evidence_by_scf, window, queries_by_scf)
        for clause_id in clause_ids:
            clauses.setdefault(clause_id, []).append(node)

    ordered_clauses = [
        {
            "clause_id": clause_id,
            "controls": sorted(clauses[clause_id], key=lambda c: c["scf_id"]),
        }
        for clause_id in sorted(clauses.keys(), key=natural_clause_sort_key)
    ]

    return {
        "framework": framework,
        "clauses": ordered_clauses,
    }
