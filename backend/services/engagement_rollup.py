"""Roll-up of evidence review status to controls and framework clauses.

The engagement review board's card is the evidence object. A control is
Accepted only when every evidence object it maps to is Accepted; one object in
Action required holds the control. A clause rolls up from its controls by the
same rule. A control with no evidence object is resolved by a control-level
query, not a card: it is Accepted when it has at least one closed
control-level query and none open or answered, and Open otherwise.

Pure: no I/O. The API loads the same data the card list uses and passes it in,
so the board and the dashboard can never disagree.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence

from services.engagement_presentation import natural_clause_sort_key

OPEN = "open"
IN_PROGRESS = "in_progress"
ACTION_REQUIRED = "action_required"
SUBMITTED = "submitted"
ACCEPTED = "accepted"

STATUSES: tuple = (OPEN, IN_PROGRESS, ACTION_REQUIRED, SUBMITTED, ACCEPTED)

_SUBMITTED_OR_ACCEPTED = frozenset({SUBMITTED, ACCEPTED})
_GROUP_SPLIT = re.compile(r"[.\-]")


def rollup_status(statuses: Iterable[str]) -> str:
    """The one roll-up rule (CONTRACTS.md), used for controls from evidence and
    for clauses from controls."""
    values = list(statuses)
    if not values:
        return OPEN
    if any(s == ACTION_REQUIRED for s in values):
        return ACTION_REQUIRED
    if all(s == ACCEPTED for s in values):
        return ACCEPTED
    if all(s in _SUBMITTED_OR_ACCEPTED for s in values):
        return SUBMITTED
    if any(s != OPEN for s in values):
        return IN_PROGRESS
    return OPEN


def empty_counts() -> Dict[str, int]:
    return {s: 0 for s in STATUSES}


def count_statuses(statuses: Iterable[str]) -> Dict[str, int]:
    counts = empty_counts()
    for s in statuses:
        if s in counts:
            counts[s] += 1
    return counts


def clause_group(clause_id: str) -> str:
    """The catalog holds clause ids only (``framework_mappings`` is
    ``{framework: [clause ids]}``), no clause titles or section names, so a
    clause is grouped by the leading segment of its id: the text before the
    first ``.`` or ``-``. So ``A.5.15`` -> ``A``, ``CC3.1-POF10`` -> ``CC3``,
    ``PR.AC-1`` -> ``PR`` and the Cyber Essentials ``3-BP4`` -> ``3`` (with
    only ``.`` as the separator every ``N-BPx`` id became a group of one).
    The segment is also the label; no theme names are invented."""
    clause_id = clause_id or ""
    return _GROUP_SPLIT.split(clause_id, 1)[0] if clause_id else ""


def _iso(value) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat()


def _as_date(value) -> Optional[date]:
    if value is None:
        return None
    return value.date() if isinstance(value, datetime) else value


def build_timeline(
    *,
    start_date: Optional[date],
    end_date: Optional[date],
    first_transition_at: Optional[datetime],
    last_transition_at: Optional[datetime],
    all_evidence_tested: bool,
    all_clauses_accepted: bool,
    today: date,
) -> List[Dict[str, Any]]:
    """Milestones from engagement dates and real progress. Unknown dates are None."""
    end_passed = end_date is not None and _as_date(end_date) < today
    start_passed = start_date is not None and _as_date(start_date) <= today
    return [
        {"key": "window_start", "label": "Audit window opens",
         "date": _iso(start_date), "done": start_passed},
        {"key": "testing_started", "label": "Testing started",
         "date": _iso(first_transition_at), "done": first_transition_at is not None},
        {"key": "all_evidence_tested", "label": "All evidence tested",
         "date": _iso(last_transition_at) if all_evidence_tested else None,
         "done": all_evidence_tested},
        {"key": "all_clauses_accepted", "label": "All clauses accepted",
         "date": _iso(last_transition_at) if all_clauses_accepted else None,
         "done": all_clauses_accepted},
        {"key": "window_end", "label": "Audit window closes",
         "date": _iso(end_date), "done": end_passed},
    ]


def build_rollup(
    *,
    cards: Sequence[Dict[str, Any]],
    controls: Sequence[Dict[str, Any]],
    clause_ids: Sequence[str],
    open_control_queries: Optional[Dict[str, int]] = None,
    closed_control_queries: Optional[Dict[str, int]] = None,
    answered_control_queries: Optional[Dict[str, int]] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    first_transition_at: Optional[datetime] = None,
    last_transition_at: Optional[datetime] = None,
    today: Optional[date] = None,
) -> Dict[str, Any]:
    """Everything the dashboard shows for one framework, from the board's data.

    ``cards``: the framework's cards, unfiltered — each
    ``{"evidence_id", "title", "status", "controls": [scf_id], "open_query_count"}``.
    ``controls``: every in-scope control with a clause in this framework —
    ``{"scf_id", "title", "clauses": [clause ids]}``.
    ``clause_ids``: the framework's clauses (any order; sorted here).
    ``open_control_queries``: ``{scf_id: open control-level query count}``.
    ``closed_control_queries`` / ``answered_control_queries``: the same for
    closed and answered queries. They only decide a no-evidence control.

    Returns the ``evidence``, ``controls``, ``clauses``, ``clause_rollup``,
    ``groups``, ``action_required``, ``no_evidence_controls``,
    ``open_queries`` and ``timeline`` sections. The caller adds ``engagement``
    and ``framework``.
    """
    open_control_queries = open_control_queries or {}
    closed_control_queries = closed_control_queries or {}
    answered_control_queries = answered_control_queries or {}
    today = today or date.today()

    status_by_evidence = {c["evidence_id"]: c["status"] for c in cards}
    control_ids = {c["scf_id"] for c in controls}

    # Control -> its evidence objects that are on this framework's board.
    evidence_by_control: Dict[str, List[str]] = {scf: [] for scf in control_ids}
    for card in cards:
        for scf in card.get("controls") or []:
            if scf in evidence_by_control:
                evidence_by_control[scf].append(card["evidence_id"])

    def _control_status(scf: str) -> str:
        evidence = evidence_by_control[scf]
        if evidence:
            # A control with evidence ignores its control-level queries.
            return rollup_status(status_by_evidence[e] for e in evidence)
        resolved = (
            closed_control_queries.get(scf, 0) > 0
            and open_control_queries.get(scf, 0) == 0
            and answered_control_queries.get(scf, 0) == 0
        )
        return ACCEPTED if resolved else OPEN

    control_status = {scf: _control_status(scf) for scf in control_ids}

    controls_by_clause: Dict[str, List[str]] = {cl: [] for cl in clause_ids}
    clauses_by_control: Dict[str, List[str]] = {}
    for ctl in controls:
        clauses_by_control[ctl["scf_id"]] = list(ctl.get("clauses") or [])
        for cl in ctl.get("clauses") or []:
            controls_by_clause.setdefault(cl, []).append(ctl["scf_id"])

    ordered_clauses = sorted(controls_by_clause, key=natural_clause_sort_key)
    clause_rollup = []
    for cl in ordered_clauses:
        scfs = controls_by_clause[cl]
        evidence_ids = {e for scf in scfs for e in evidence_by_control.get(scf, [])}
        clause_rollup.append({
            "clause": cl,
            "group": clause_group(cl),
            "status": rollup_status(control_status[scf] for scf in scfs),
            "control_count": len(scfs),
            "evidence_count": len(evidence_ids),
        })
    clause_status = {row["clause"]: row["status"] for row in clause_rollup}

    groups: Dict[str, Dict[str, Any]] = {}
    for row in clause_rollup:
        g = groups.setdefault(row["group"], {
            "key": row["group"], "label": row["group"],
            "clauses_total": 0, "clauses_accepted": 0,
        })
        g["clauses_total"] += 1
        if row["status"] == ACCEPTED:
            g["clauses_accepted"] += 1
    ordered_groups = [groups[k] for k in sorted(groups, key=natural_clause_sort_key)]

    action_required = []
    for card in sorted(cards, key=lambda c: c["evidence_id"]):
        if card["status"] != ACTION_REQUIRED:
            continue
        held_controls = sorted(s for s in (card.get("controls") or []) if s in control_ids)
        held_clauses = sorted(
            {cl for s in held_controls for cl in clauses_by_control.get(s, [])},
            key=natural_clause_sort_key,
        )
        action_required.append({
            "evidence_id": card["evidence_id"],
            "title": card.get("title"),
            "held_controls": held_controls,
            "held_clauses": held_clauses,
            "open_query_count": card.get("open_query_count", 0),
        })

    no_evidence_controls = [
        {
            "scf_id": ctl["scf_id"],
            "title": ctl.get("title"),
            "clauses": sorted(ctl.get("clauses") or [], key=natural_clause_sort_key),
            "open_query_count": open_control_queries.get(ctl["scf_id"], 0),
        }
        for ctl in sorted(controls, key=lambda c: c["scf_id"])
        if not evidence_by_control.get(ctl["scf_id"])
    ]

    evidence_queries = sum(c.get("open_query_count", 0) for c in cards)
    control_queries = sum(open_control_queries.get(scf, 0) for scf in control_ids)

    all_evidence_tested = all(
        c["status"] not in (OPEN, IN_PROGRESS) for c in cards
    ) and bool(cards)
    all_clauses_accepted = bool(clause_rollup) and all(
        s == ACCEPTED for s in clause_status.values()
    )

    return {
        "evidence": {
            "total": len(cards),
            "by_status": count_statuses(c["status"] for c in cards),
        },
        "controls": {
            "total": len(control_ids),
            "by_status": count_statuses(control_status.values()),
        },
        "clauses": {
            "total": len(clause_rollup),
            "by_status": count_statuses(clause_status.values()),
        },
        "clause_rollup": clause_rollup,
        "groups": ordered_groups,
        "action_required": action_required,
        "no_evidence_controls": no_evidence_controls,
        "open_queries": {
            "total": evidence_queries + control_queries,
            "evidence": evidence_queries,
            "control": control_queries,
        },
        "timeline": build_timeline(
            start_date=start_date,
            end_date=end_date,
            first_transition_at=first_transition_at,
            last_transition_at=last_transition_at,
            all_evidence_tested=all_evidence_tested,
            all_clauses_accepted=all_clauses_accepted,
            today=today,
        ),
    }
