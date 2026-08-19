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
