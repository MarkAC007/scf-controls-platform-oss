"""Framework-native presentation for audit engagements.

Increment 2 of the framework-native audit-engagement design
(docs/superpowers/specs/2026-08-19-audit-engagements-design.md).

Re-sequences an engagement's frozen control set under the requested framework's
own clause / Annex A identifiers. The clause outline is *derived on read* from
the catalog framework_mappings (design decision D3) — there is no authored
canonical outline — so only clauses that at least one in-scope control maps to
appear. Evidence is shown live and flagged in/out of the audit window; nothing is
hidden. The window test prefers the preparer's asserted effective period
(#786) and falls back to the upload-date proxy (D4) only where nothing was
asserted — each artifact reports which of the two it was judged on.

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


#: The artifact carries a preparer-asserted effective period and it was used.
WINDOW_BASIS_ASSERTED = "asserted_period"
#: Nothing was asserted, so the upload date stood in for the coverage period.
WINDOW_BASIS_UPLOAD = "upload_date"


def _as_date(value) -> Optional[date]:
    if value is None:
        return None
    return value.date() if isinstance(value, datetime) else value


def _asserted_period(artifact: Dict[str, Any]) -> Optional[Tuple[date, date]]:
    """The preparer-asserted period, or None if this artifact has no whole one.

    Both ends or nothing. The confirm endpoint refuses a half-asserted period,
    but a row written before those columns existed — or patched by hand — can
    still hold one, and half a period cannot be compared against a window.
    """
    start = _as_date(artifact.get("effective_period_start"))
    end = _as_date(artifact.get("effective_period_end"))
    if start is None or end is None:
        return None
    return start, end


def _in_window(
    artifact: Dict[str, Any],
    window: Tuple[Optional[date], Optional[date]],
) -> Tuple[bool, str]:
    """Whether an artifact belongs to the engagement window, and on what basis.

    The upload date was always a proxy (design decision D4): it says when a file
    arrived, not what period it describes. A quarterly access review exported on
    2 April covers Q1 and uploads in Q2, and the proxy puts it in the wrong one.

    Where the preparer has asserted an effective period, that is used instead —
    it is the only statement in the system about what the evidence actually
    covers, and it was made by a person who takes responsibility for it. The
    test is **overlap**, not containment: an annual report legitimately supports
    a quarterly engagement, and excluding it would be wrong. A period that does
    not touch the window at all is out.

    Where nothing was asserted the upload proxy still applies, unchanged, which
    is why the returned basis matters as much as the boolean. A reader has to be
    able to tell "the preparer said this covers the window" from "we guessed
    from the upload date" — those are very different grounds for relying on it.

    An unset window bound is open-ended, so an engagement with no dates counts
    every artifact as in-window.
    """
    start, end = window

    period = _asserted_period(artifact)
    if period is not None:
        p_start, p_end = period
        if end is not None and p_start > end:
            return False, WINDOW_BASIS_ASSERTED
        if start is not None and p_end < start:
            return False, WINDOW_BASIS_ASSERTED
        return True, WINDOW_BASIS_ASSERTED

    uploaded_at = artifact.get("uploaded_at")
    if uploaded_at is None:
        return False, WINDOW_BASIS_UPLOAD
    upload_date = _as_date(uploaded_at)
    if start is not None and upload_date < start:
        return False, WINDOW_BASIS_UPLOAD
    if end is not None and upload_date > end:
        return False, WINDOW_BASIS_UPLOAD
    return True, WINDOW_BASIS_UPLOAD


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
        flagged, basis = _in_window(art, window)
        if flagged:
            in_window_count += 1
        evidence.append({
            "id": art.get("id"),
            "filename": art.get("filename"),
            "uploaded_at": art.get("uploaded_at"),
            "review_status": art.get("review_status"),
            "in_window": flagged,
            # What the in/out ruling was actually based on. Without this the
            # reader cannot tell an asserted coverage period from the upload-date
            # proxy, and those carry very different weight in an audit file.
            "in_window_basis": basis,
            "effective_period_start": art.get("effective_period_start"),
            "effective_period_end": art.get("effective_period_end"),
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
