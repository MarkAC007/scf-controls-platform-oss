"""Pure tests for services.engagement_rollup — the board -> dashboard roll-up."""
from __future__ import annotations

import os
import sys
from datetime import date, datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.engagement_rollup import (  # noqa: E402
    STATUSES,
    build_rollup,
    clause_group,
    rollup_status,
)

TODAY = date(2026, 11, 16)


# ---------------------------------------------------------------------------
# rollup_status — every branch
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("statuses, expected", [
    ([], "open"),
    (["open"], "open"),
    (["open", "open"], "open"),
    (["accepted", "action_required"], "action_required"),
    (["open", "action_required", "submitted"], "action_required"),
    (["accepted"], "accepted"),
    (["accepted", "accepted"], "accepted"),
    (["submitted", "accepted"], "submitted"),
    (["submitted"], "submitted"),
    (["open", "accepted"], "in_progress"),
    (["open", "in_progress"], "in_progress"),
    (["open", "submitted"], "in_progress"),
    (["in_progress", "accepted"], "in_progress"),
])
def test_rollup_status_branches(statuses, expected):
    assert rollup_status(statuses) == expected


def test_rollup_status_accepts_generators():
    assert rollup_status(s for s in ["accepted", "accepted"]) == "accepted"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _card(eid, status, controls, queries=0):
    return {"evidence_id": eid, "title": f"Title {eid}", "status": status,
            "controls": controls, "open_query_count": queries}


def _ctl(scf, clauses):
    return {"scf_id": scf, "title": f"Control {scf}", "clauses": clauses}


CONTROLS = [
    _ctl("IAC-01", ["A.5.15", "A.5.16"]),
    _ctl("IAC-02", ["A.5.16"]),
    _ctl("GOV-01", ["5.2"]),
    _ctl("HRS-01", ["A.6.1"]),   # no evidence object
]
CLAUSES = ["A.5.15", "A.5.16", "5.2", "A.6.1"]


def _roll(cards, **kw):
    kw.setdefault("today", TODAY)
    return build_rollup(cards=cards, controls=CONTROLS, clause_ids=CLAUSES, **kw)


def _clause(result, clause):
    return next(r for r in result["clause_rollup"] if r["clause"] == clause)


# ---------------------------------------------------------------------------
# Roll-up rules
# ---------------------------------------------------------------------------

def test_control_accepted_only_when_all_its_evidence_accepted():
    part = _roll([
        _card("E-1", "accepted", ["IAC-01"]),
        _card("E-2", "submitted", ["IAC-01"]),
    ])
    assert part["controls"]["by_status"]["accepted"] == 0
    assert _clause(part, "A.5.15")["status"] == "submitted"

    full = _roll([
        _card("E-1", "accepted", ["IAC-01"]),
        _card("E-2", "accepted", ["IAC-01"]),
    ])
    assert _clause(full, "A.5.15")["status"] == "accepted"


def test_one_action_required_holds_its_control_and_every_clause():
    result = _roll([
        _card("E-1", "action_required", ["IAC-01"], queries=2),
        _card("E-2", "accepted", ["IAC-01", "IAC-02"]),
        _card("E-3", "accepted", ["GOV-01"]),
    ])
    assert _clause(result, "A.5.15")["status"] == "action_required"
    assert _clause(result, "A.5.16")["status"] == "action_required"
    assert _clause(result, "5.2")["status"] == "accepted"

    [held] = result["action_required"]
    assert held["evidence_id"] == "E-1"
    assert held["held_controls"] == ["IAC-01"]
    assert held["held_clauses"] == ["A.5.15", "A.5.16"]
    assert held["open_query_count"] == 2


def test_control_with_no_evidence_stays_open_and_blocks_its_clause():
    result = _roll([
        _card("E-1", "accepted", ["IAC-01", "IAC-02"]),
        _card("E-3", "accepted", ["GOV-01"]),
    ], open_control_queries={"HRS-01": 1})
    assert _clause(result, "A.6.1")["status"] == "open"
    assert result["controls"]["by_status"]["open"] == 1
    assert result["clauses"]["by_status"]["accepted"] == 3
    assert [c["scf_id"] for c in result["no_evidence_controls"]] == ["HRS-01"]
    assert result["no_evidence_controls"][0]["open_query_count"] == 1
    assert result["no_evidence_controls"][0]["clauses"] == ["A.6.1"]
    # Not every clause accepted -> milestone not done.
    milestone = {t["key"]: t for t in result["timeline"]}
    assert milestone["all_clauses_accepted"]["done"] is False


_ALL_EVIDENCE_ACCEPTED = [
    ("E-1", "accepted", ["IAC-01", "IAC-02"]),
    ("E-3", "accepted", ["GOV-01"]),
]


def _accepted_board(**kw):
    return _roll([_card(*c) for c in _ALL_EVIDENCE_ACCEPTED], **kw)


def _control_status(result, scf):
    # HRS-01 is the only control on clause A.6.1.
    assert scf == "HRS-01"
    return _clause(result, "A.6.1")["status"]


def test_no_evidence_control_with_only_closed_queries_is_accepted():
    result = _accepted_board(closed_control_queries={"HRS-01": 1})
    assert _control_status(result, "HRS-01") == "accepted"
    assert result["controls"]["by_status"]["accepted"] == 4
    assert result["clauses"]["by_status"]["accepted"] == 4
    milestone = {t["key"]: t for t in result["timeline"]}
    assert milestone["all_clauses_accepted"]["done"] is True


@pytest.mark.parametrize("kw", [
    {"closed_control_queries": {"HRS-01": 2}, "open_control_queries": {"HRS-01": 1}},
    {"closed_control_queries": {"HRS-01": 2}, "answered_control_queries": {"HRS-01": 1}},
    {},
    {"closed_control_queries": {"OTHER": 3}},
])
def test_no_evidence_control_stays_open_unless_every_query_is_closed(kw):
    result = _accepted_board(**kw)
    assert _control_status(result, "HRS-01") == "open"
    assert result["controls"]["by_status"]["open"] == 1


def test_control_with_evidence_ignores_its_control_level_queries():
    result = _roll(
        [_card("E-1", "in_progress", ["IAC-01", "IAC-02"]), _card("E-3", "accepted", ["GOV-01"])],
        closed_control_queries={"IAC-01": 3, "IAC-02": 1, "GOV-01": 1},
    )
    assert _clause(result, "A.5.15")["status"] == "in_progress"
    assert _clause(result, "5.2")["status"] == "accepted"

    result = _roll(
        [_card("E-1", "accepted", ["IAC-01", "IAC-02"]), _card("E-3", "accepted", ["GOV-01"])],
        open_control_queries={"GOV-01": 2}, answered_control_queries={"IAC-01": 1},
    )
    assert _clause(result, "5.2")["status"] == "accepted"
    assert _clause(result, "A.5.15")["status"] == "accepted"


def test_counts_add_up_to_totals():
    result = _roll([
        _card("E-1", "open", ["IAC-01"]),
        _card("E-2", "in_progress", ["IAC-02"]),
        _card("E-3", "submitted", ["GOV-01"]),
    ])
    for section in ("evidence", "controls", "clauses"):
        block = result[section]
        assert sum(block["by_status"].values()) == block["total"]
        assert set(block["by_status"]) == set(STATUSES)
    assert result["evidence"]["total"] == 3
    assert result["controls"]["total"] == 4
    assert result["clauses"]["total"] == 4


def test_clause_rollup_counts_and_natural_order():
    result = _roll([
        _card("E-1", "open", ["IAC-01", "IAC-02"]),
        _card("E-2", "open", ["IAC-02"]),
    ])
    assert [r["clause"] for r in result["clause_rollup"]] == ["5.2", "A.5.15", "A.5.16", "A.6.1"]
    a516 = _clause(result, "A.5.16")
    assert a516["control_count"] == 2
    assert a516["evidence_count"] == 2
    assert a516["group"] == "A"


def test_open_query_totals():
    result = _roll(
        [_card("E-1", "open", ["IAC-01"], queries=2), _card("E-2", "open", ["GOV-01"], queries=1)],
        open_control_queries={"HRS-01": 1, "IAC-01": 1, "NOT-IN-VIEW": 5},
    )
    assert result["open_queries"] == {"total": 5, "evidence": 3, "control": 2}


# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------

def test_clause_group_is_leading_segment():
    assert clause_group("A.5.15") == "A"
    assert clause_group("5.2") == "5"
    assert clause_group("CC3.1-POF10") == "CC3"
    # Cyber Essentials ids have no dot; the hyphen separates the section.
    assert clause_group("3-BP4") == "3"
    assert clause_group("3.0") == "3"
    assert clause_group("PR.AC-1") == "PR"
    assert clause_group("") == ""


def test_group_totals():
    result = _roll([
        _card("E-1", "accepted", ["IAC-01", "IAC-02"]),
        _card("E-3", "accepted", ["GOV-01"]),
    ])
    groups = {g["key"]: g for g in result["groups"]}
    assert [g["key"] for g in result["groups"]] == ["5", "A"]
    assert groups["A"] == {"key": "A", "label": "A", "clauses_total": 3, "clauses_accepted": 2}
    assert groups["5"] == {"key": "5", "label": "5", "clauses_total": 1, "clauses_accepted": 1}
    assert sum(g["clauses_total"] for g in result["groups"]) == result["clauses"]["total"]


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------

def _timeline(result):
    return {t["key"]: t for t in result["timeline"]}


def test_timeline_untouched_engagement():
    result = _roll(
        [_card("E-1", "open", ["IAC-01"])],
        start_date=date(2026, 12, 1), end_date=date(2027, 1, 31),
    )
    t = _timeline(result)
    assert [x["key"] for x in result["timeline"]] == [
        "window_start", "testing_started", "all_evidence_tested",
        "all_clauses_accepted", "window_end",
    ]
    assert t["window_start"] == {"key": "window_start", "label": t["window_start"]["label"],
                                 "date": "2026-12-01", "done": False}
    assert t["testing_started"]["done"] is False and t["testing_started"]["date"] is None
    assert t["all_evidence_tested"]["done"] is False and t["all_evidence_tested"]["date"] is None
    assert t["all_clauses_accepted"]["done"] is False
    assert t["window_end"]["date"] == "2027-01-31" and t["window_end"]["done"] is False


def test_timeline_part_done_engagement():
    first, last = datetime(2026, 11, 2, 9), datetime(2026, 11, 10, 15)
    result = _roll(
        [_card("E-1", "accepted", ["IAC-01"]), _card("E-2", "in_progress", ["IAC-02"])],
        start_date=date(2026, 11, 1), end_date=None,
        first_transition_at=first, last_transition_at=last,
    )
    t = _timeline(result)
    assert t["window_start"]["done"] is True
    assert t["testing_started"] == {**t["testing_started"], "date": first.isoformat(), "done": True}
    assert t["all_evidence_tested"]["done"] is False
    assert t["all_clauses_accepted"]["done"] is False
    assert t["window_end"]["date"] is None and t["window_end"]["done"] is False


def test_timeline_fully_accepted_engagement():
    first, last = datetime(2026, 10, 2, 9), datetime(2026, 10, 30, 15)
    controls = [_ctl("IAC-01", ["A.5.15"]), _ctl("GOV-01", ["5.2"])]
    result = build_rollup(
        cards=[_card("E-1", "accepted", ["IAC-01"]), _card("E-3", "accepted", ["GOV-01"])],
        controls=controls, clause_ids=["A.5.15", "5.2"],
        start_date=date(2026, 9, 1), end_date=date(2026, 10, 31),
        first_transition_at=first, last_transition_at=last, today=TODAY,
    )
    t = _timeline(result)
    assert all(x["done"] for x in result["timeline"])
    assert t["all_evidence_tested"]["date"] == last.isoformat()
    assert t["all_clauses_accepted"]["date"] == last.isoformat()
    assert result["clauses"]["by_status"]["accepted"] == 2
    assert result["action_required"] == []
    assert result["no_evidence_controls"] == []
