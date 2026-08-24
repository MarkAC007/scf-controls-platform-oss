"""Tests for the framework-native presentation builder.

Increment 2 of the framework-native audit-engagement design
(docs/superpowers/specs/2026-08-19-audit-engagements-design.md).

The presentation re-sequences an engagement's frozen control set under the
requested framework's own clause / Annex A identifiers (derived on read from the
catalog framework_mappings). These tests exercise the pure logic — natural clause
ordering and tree assembly — without a database.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.engagement_presentation import (  # noqa: E402
    natural_clause_sort_key,
    build_framework_presentation,
)


# --- natural clause ordering ------------------------------------------------

def test_numeric_clauses_sort_naturally_not_lexically():
    clauses = ["5.10", "5.2", "5.1", "10.1", "8.1"]
    ordered = sorted(clauses, key=natural_clause_sort_key)
    assert ordered == ["5.1", "5.2", "5.10", "8.1", "10.1"]


def test_annex_a_clauses_sort_after_numbered_management_clauses():
    clauses = ["A.5.1", "8.1", "A.5.10", "A.5.2", "5.1"]
    ordered = sorted(clauses, key=natural_clause_sort_key)
    assert ordered == ["5.1", "8.1", "A.5.1", "A.5.2", "A.5.10"]


# --- tree assembly ----------------------------------------------------------

def _scope_row(scf_id, status="in_scope", justification=None, scoped_control_id="x", name=None):
    return {
        "scf_id": scf_id,
        "scope_status": status,
        "out_of_scope_justification": justification,
        "source_frameworks": ["iso_27001_2022"],
        "scoped_control_id": scoped_control_id,
        "control_name": name or f"{scf_id} name",
    }


def test_controls_grouped_and_ordered_under_framework_clauses():
    scope_rows = [_scope_row("GOV-02"), _scope_row("GOV-01")]
    mappings_by_scf = {
        "GOV-01": {"iso_27001_2022": ["5.1"]},
        "GOV-02": {"iso_27001_2022": ["5.10", "5.2"]},
    }
    tree = build_framework_presentation(
        framework="iso_27001_2022",
        scope_rows=scope_rows,
        mappings_by_scf=mappings_by_scf,
        live_by_scf={},
        evidence_by_scf={},
        window=(None, None),
    )
    clause_ids = [c["clause_id"] for c in tree["clauses"]]
    assert clause_ids == ["5.1", "5.2", "5.10"]
    # GOV-02 maps to two clauses -> appears under both (multi-clause fan-out).
    assert tree["clauses"][0]["controls"][0]["scf_id"] == "GOV-01"
    assert [c["controls"][0]["scf_id"] for c in tree["clauses"][1:]] == ["GOV-02", "GOV-02"]


def test_controls_not_mapped_to_requested_framework_are_excluded():
    # GOV-09 was pulled into the engagement by SOC 2, not ISO — it must not appear
    # in the ISO presentation.
    scope_rows = [_scope_row("GOV-01"), _scope_row("GOV-09")]
    mappings_by_scf = {
        "GOV-01": {"iso_27001_2022": ["5.1"]},
        "GOV-09": {"soc2": ["CC1.1"]},
    }
    tree = build_framework_presentation(
        framework="iso_27001_2022",
        scope_rows=scope_rows,
        mappings_by_scf=mappings_by_scf,
        live_by_scf={},
        evidence_by_scf={},
        window=(None, None),
    )
    all_scf = [ctrl["scf_id"] for c in tree["clauses"] for ctrl in c["controls"]]
    assert all_scf == ["GOV-01"]


def test_excluded_control_carries_justification_and_live_fields_absent():
    scope_rows = [
        _scope_row("GOV-01", status="excluded",
                   justification="Covered by parent ISMS.", scoped_control_id="sc1"),
    ]
    tree = build_framework_presentation(
        framework="iso_27001_2022",
        scope_rows=scope_rows,
        mappings_by_scf={"GOV-01": {"iso_27001_2022": ["5.1"]}},
        live_by_scf={"GOV-01": {"implementation_status": "not_applicable", "maturity_level": None, "owner": "Jane"}},
        evidence_by_scf={},
        window=(None, None),
    )
    ctrl = tree["clauses"][0]["controls"][0]
    assert ctrl["scope_status"] == "excluded"
    assert ctrl["out_of_scope_justification"] == "Covered by parent ISMS."
    assert ctrl["implementation_status"] == "not_applicable"
    assert ctrl["owner"] == "Jane"
    assert ctrl["queries"] == []  # increment 4 placeholder


def test_evidence_flagged_in_or_out_of_window_by_upload_date():
    window = (date(2026, 1, 1), date(2026, 12, 31))
    evidence_by_scf = {
        "GOV-01": [
            {"id": "e1", "filename": "in.pdf", "uploaded_at": datetime(2026, 6, 1), "review_status": "approved"},
            {"id": "e2", "filename": "old.pdf", "uploaded_at": datetime(2025, 6, 1), "review_status": "approved"},
        ]
    }
    tree = build_framework_presentation(
        framework="iso_27001_2022",
        scope_rows=[_scope_row("GOV-01")],
        mappings_by_scf={"GOV-01": {"iso_27001_2022": ["5.1"]}},
        live_by_scf={},
        evidence_by_scf=evidence_by_scf,
        window=window,
    )
    ctrl = tree["clauses"][0]["controls"][0]
    ev = {e["filename"]: e for e in ctrl["evidence"]}
    assert ev["in.pdf"]["in_window"] is True
    assert ev["old.pdf"]["in_window"] is False
    assert ctrl["evidence_in_window_count"] == 1  # artifacts are flagged, not hidden
    assert len(ctrl["evidence"]) == 2


def test_open_window_treats_all_evidence_as_in_window():
    evidence_by_scf = {
        "GOV-01": [{"id": "e1", "filename": "a.pdf", "uploaded_at": datetime(2020, 1, 1), "review_status": "approved"}]
    }
    tree = build_framework_presentation(
        framework="iso_27001_2022",
        scope_rows=[_scope_row("GOV-01")],
        mappings_by_scf={"GOV-01": {"iso_27001_2022": ["5.1"]}},
        live_by_scf={},
        evidence_by_scf=evidence_by_scf,
        window=(None, None),
    )
    ctrl = tree["clauses"][0]["controls"][0]
    assert ctrl["evidence"][0]["in_window"] is True
    assert ctrl["evidence_in_window_count"] == 1


# --- the asserted effective period as the window anchor (#786) ---------------
#
# The upload date was always a proxy: it says when a file arrived, not what
# period it describes. Where the preparer has asserted an effective period, that
# is the only statement in the system about what the evidence actually covers,
# and it displaces the proxy. These tests are mostly about the cases where the
# two disagree, because those are the ones the proxy has been getting wrong.

def _tree_with(artifacts, window):
    tree = build_framework_presentation(
        framework="iso_27001_2022",
        scope_rows=[_scope_row("GOV-01")],
        mappings_by_scf={"GOV-01": {"iso_27001_2022": ["5.1"]}},
        live_by_scf={},
        evidence_by_scf={"GOV-01": artifacts},
        window=window,
    )
    ctrl = tree["clauses"][0]["controls"][0]
    return ctrl, {e["filename"]: e for e in ctrl["evidence"]}


Q1 = (date(2026, 1, 1), date(2026, 3, 31))


def test_an_asserted_period_beats_the_upload_date_that_contradicts_it():
    """The case the proxy has always got wrong.

    A quarterly access review covering Q1 is exported and uploaded on 2 April.
    The proxy puts it in Q2 and rules it out of a Q1 engagement — which is the
    opposite of the truth. The preparer said what it covers; believe them.
    """
    _, ev = _tree_with([{
        "id": "e1", "filename": "q1-access-review.csv",
        "uploaded_at": datetime(2026, 4, 2, 9, 0),
        "review_status": "approved",
        "effective_period_start": date(2026, 1, 1),
        "effective_period_end": date(2026, 3, 31),
    }], Q1)
    assert ev["q1-access-review.csv"]["in_window"] is True
    assert ev["q1-access-review.csv"]["in_window_basis"] == "asserted_period"


def test_an_in_window_upload_of_out_of_window_evidence_is_ruled_out():
    """The mirror image, and the more dangerous one.

    Someone uploads last year's report during the engagement. The upload proxy
    counts it; the asserted period says it describes 2025 and cannot support a
    2026 opinion.
    """
    _, ev = _tree_with([{
        "id": "e1", "filename": "last-year.pdf",
        "uploaded_at": datetime(2026, 2, 1),
        "review_status": "approved",
        "effective_period_start": date(2025, 1, 1),
        "effective_period_end": date(2025, 12, 31),
    }], Q1)
    assert ev["last-year.pdf"]["in_window"] is False
    assert ev["last-year.pdf"]["in_window_basis"] == "asserted_period"


def test_an_annual_report_supports_a_quarterly_engagement():
    """Overlap, not containment.

    A period covering the whole year is not contained by Q1, but it plainly says
    something about Q1. Requiring containment would throw away exactly the
    evidence auditors most often rely on.
    """
    _, ev = _tree_with([{
        "id": "e1", "filename": "annual.pdf",
        "uploaded_at": datetime(2027, 1, 15),
        "review_status": "approved",
        "effective_period_start": date(2026, 1, 1),
        "effective_period_end": date(2026, 12, 31),
    }], Q1)
    assert ev["annual.pdf"]["in_window"] is True


def test_a_period_touching_the_window_by_one_day_is_in():
    _, ev = _tree_with([
        {"id": "a", "filename": "ends-on-day-one.pdf", "uploaded_at": datetime(2026, 5, 1),
         "review_status": "approved",
         "effective_period_start": date(2025, 10, 1), "effective_period_end": date(2026, 1, 1)},
        {"id": "b", "filename": "starts-on-last-day.pdf", "uploaded_at": datetime(2026, 5, 1),
         "review_status": "approved",
         "effective_period_start": date(2026, 3, 31), "effective_period_end": date(2026, 6, 30)},
    ], Q1)
    assert ev["ends-on-day-one.pdf"]["in_window"] is True
    assert ev["starts-on-last-day.pdf"]["in_window"] is True


def test_a_period_missing_the_window_by_one_day_is_out():
    _, ev = _tree_with([
        {"id": "a", "filename": "ends-the-day-before.pdf", "uploaded_at": datetime(2026, 2, 1),
         "review_status": "approved",
         "effective_period_start": date(2025, 10, 1), "effective_period_end": date(2025, 12, 31)},
        {"id": "b", "filename": "starts-the-day-after.pdf", "uploaded_at": datetime(2026, 2, 1),
         "review_status": "approved",
         "effective_period_start": date(2026, 4, 1), "effective_period_end": date(2026, 6, 30)},
    ], Q1)
    assert ev["ends-the-day-before.pdf"]["in_window"] is False
    assert ev["starts-the-day-after.pdf"]["in_window"] is False


def test_an_unasserted_artifact_still_uses_the_upload_proxy():
    """No behaviour change for the files already in every tenant."""
    ctrl, ev = _tree_with([
        {"id": "a", "filename": "in.pdf", "uploaded_at": datetime(2026, 2, 1), "review_status": "approved"},
        {"id": "b", "filename": "out.pdf", "uploaded_at": datetime(2025, 2, 1), "review_status": "approved"},
    ], Q1)
    assert ev["in.pdf"]["in_window"] is True
    assert ev["in.pdf"]["in_window_basis"] == "upload_date"
    assert ev["out.pdf"]["in_window"] is False
    assert ctrl["evidence_in_window_count"] == 1


def test_a_half_asserted_period_falls_back_to_the_proxy_rather_than_guessing():
    """Half a period is not a window. The confirm endpoint refuses one, but a
    row written before these columns existed can still hold one, and inventing
    the missing end would be the one thing these columns exist to prevent."""
    _, ev = _tree_with([
        {"id": "a", "filename": "start-only.pdf", "uploaded_at": datetime(2026, 2, 1),
         "review_status": "approved", "effective_period_start": date(2020, 1, 1),
         "effective_period_end": None},
        {"id": "b", "filename": "end-only.pdf", "uploaded_at": datetime(2026, 2, 1),
         "review_status": "approved", "effective_period_start": None,
         "effective_period_end": date(2020, 12, 31)},
    ], Q1)
    # Both would be ruled OUT on their stated 2020 dates; both are ruled IN on
    # the upload proxy. The basis is what makes that legible.
    for name in ("start-only.pdf", "end-only.pdf"):
        assert ev[name]["in_window"] is True
        assert ev[name]["in_window_basis"] == "upload_date"


def test_an_open_window_takes_an_asserted_period_too():
    ctrl, ev = _tree_with([{
        "id": "e1", "filename": "ancient.pdf",
        "uploaded_at": datetime(2020, 1, 1), "review_status": "approved",
        "effective_period_start": date(2019, 1, 1), "effective_period_end": date(2019, 12, 31),
    }], (None, None))
    assert ev["ancient.pdf"]["in_window"] is True
    assert ev["ancient.pdf"]["in_window_basis"] == "asserted_period"
    assert ctrl["evidence_in_window_count"] == 1


def test_a_half_open_window_is_bounded_on_the_side_it_has():
    _, ev = _tree_with([
        {"id": "a", "filename": "before.pdf", "uploaded_at": datetime(2026, 5, 1),
         "review_status": "approved",
         "effective_period_start": date(2024, 1, 1), "effective_period_end": date(2024, 12, 31)},
        {"id": "b", "filename": "after.pdf", "uploaded_at": datetime(2026, 5, 1),
         "review_status": "approved",
         "effective_period_start": date(2026, 6, 1), "effective_period_end": date(2026, 6, 30)},
    ], (date(2026, 1, 1), None))
    assert ev["before.pdf"]["in_window"] is False
    assert ev["after.pdf"]["in_window"] is True


def test_the_asserted_period_is_carried_on_the_item_not_only_used_for_the_ruling():
    """A reader who disagrees with the ruling has to be able to see the dates."""
    _, ev = _tree_with([{
        "id": "e1", "filename": "q1.csv", "uploaded_at": datetime(2026, 4, 2),
        "review_status": "approved",
        "effective_period_start": date(2026, 1, 1), "effective_period_end": date(2026, 3, 31),
    }], Q1)
    assert ev["q1.csv"]["effective_period_start"] == date(2026, 1, 1)
    assert ev["q1.csv"]["effective_period_end"] == date(2026, 3, 31)


def test_the_in_window_count_follows_the_asserted_ruling_not_the_proxy():
    ctrl, _ = _tree_with([
        # In on assertion, out on the proxy.
        {"id": "a", "filename": "q1-late-upload.csv", "uploaded_at": datetime(2026, 4, 2),
         "review_status": "approved",
         "effective_period_start": date(2026, 1, 1), "effective_period_end": date(2026, 3, 31)},
        # Out on assertion, in on the proxy.
        {"id": "b", "filename": "stale-but-recent.pdf", "uploaded_at": datetime(2026, 2, 1),
         "review_status": "approved",
         "effective_period_start": date(2024, 1, 1), "effective_period_end": date(2024, 12, 31)},
    ], Q1)
    # The proxy would also have said 1 — for the wrong file.
    assert ctrl["evidence_in_window_count"] == 1
    flags = {e["filename"]: e["in_window"] for e in ctrl["evidence"]}
    assert flags == {"q1-late-upload.csv": True, "stale-but-recent.pdf": False}


def test_an_artifact_with_neither_a_period_nor_an_upload_date_is_out():
    _, ev = _tree_with([
        {"id": "a", "filename": "orphan.pdf", "uploaded_at": None, "review_status": "approved"},
    ], Q1)
    assert ev["orphan.pdf"]["in_window"] is False
    assert ev["orphan.pdf"]["in_window_basis"] == "upload_date"
