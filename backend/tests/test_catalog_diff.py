"""Tests for backend/services/catalog_diff.py (WP1a, plan §4.2.2-3, §4.7).

Covers, without a live database (the repo's unit-test pattern — DB access is
faked at the session boundary):

- all five change classes (added / changed / deprecated / resurrected /
  unchanged), producing contract-valid ``DiffDetail`` / ``DiffSummary``
  instances (imported from ``schemas_catalog_upgrade``);
- every sanity gate tripping on its crafted fixture;
- the version guard (same-version / downgrade / unparseable / force);
- the superseded_by suggestion scorer;
- the staging entry point end-to-end over a synthetic workbook with a faked
  DB session, including per-run temp-dir hygiene.

Fixture identifiers use a letter after the hyphen (``GOV-A1``) — opaque to the
code under test; literal control-ID-shaped tokens cannot be written to this
repo (ContainmentGuard).
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from services import catalog_diff as cd  # noqa: E402
from schemas_catalog_upgrade import (  # noqa: E402
    CatalogEntityType,
    DiffDetail,
    DiffSummary,
    SanityReport,
    SupersededSuggestion,
)
from test_scf_extractor import build_workbook  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _control_cols(name: str, **over) -> dict:
    cols = {f: None for f in cd.CONTROL_COMPARED_FIELDS}
    cols.update(
        scf_domain="Cybersecurity & Data Protection Governance",
        control_name=name,
        control_description=f"Mechanisms exist for {name}.",
        pptdf_people=False,
        pptdf_process=True,
        pptdf_technology=False,
        pptdf_data=False,
        pptdf_facility=False,
        evidence_requests=[],
        framework_mappings={},
        scrm_tier1_strategic=False,
        scrm_tier2_operational=False,
        scrm_tier3_tactical=False,
        risk_codes=[],
        threat_codes=[],
    )
    cols.update(over)
    return cols


def _live_control(
    key: str,
    name: str,
    status: str = "active",
    superseded_by: str | None = None,
    **field_over,
) -> cd.LiveEntityRow:
    return cd.LiveEntityRow(
        key=key,
        status=status,
        fields=_control_cols(name, **field_over),
        name=name,
        superseded_by=superseded_by,
    )


def _live_catalog(controls: dict | None = None) -> cd.LiveCatalog:
    return cd.LiveCatalog(controls=controls or {})


def _extracted(
    version: str = "2026.2",
    controls: list | None = None,
    domains: list | None = None,
    evidence: dict | None = None,
    objectives: list | None = None,
    framework_names: dict | None = None,
) -> cd.ExtractedCatalog:
    """Raw-extractor-shaped ExtractedCatalog with sane non-empty defaults."""
    if controls is None:
        controls = [
            {
                "scf_id": "GOV-A1",
                "scf_domain": "Governance",
                "control_name": "Governance Program",
                "control_description": "Mechanisms exist.",
            }
        ]
    if domains is None:
        domains = [
            {
                "identifier": "GOV",
                "order": 1,
                "name": "Governance",
                "principle": "Execute a program.",
                "principle_intent": "Intent.",
            }
        ]
    if evidence is None:
        evidence = {
            "E-GOV-A1": {
                "evidence_id": "E-GOV-A1",
                "area_of_focus": "Governance",
                "artifact_title": "Charter",
                "artifact_description": "",
                "control_mappings": ["GOV-A1"],
            }
        }
    if objectives is None:
        objectives = [
            {
                "ao_id": "GOV-A1.1",
                "scf_id": "GOV-A1",
                "objective_text": "the program exists.",
            }
        ]
    if framework_names is None:
        framework_names = {"aicpa_tsc": "AICPA Trust Services Criteria"}
    return cd.ExtractedCatalog(
        catalog_version=version,
        controls=controls,
        domains=domains,
        evidence=evidence,
        assessment_objectives=objectives,
        framework_names=framework_names,
        meta={"catalog_version": version},
    )


# ---------------------------------------------------------------------------
# Version guard
# ---------------------------------------------------------------------------


def test_guard_allows_upgrade():
    cd.guard_version("2026.1", "2026.2")  # no raise
    cd.guard_version("2025.4", "2026.1")  # year rollover


def test_guard_refuses_same_version():
    with pytest.raises(cd.VersionGuardError) as exc:
        cd.guard_version("2026.2", "2026.2")
    assert exc.value.code == "same_version"


def test_guard_refuses_downgrade():
    with pytest.raises(cd.VersionGuardError) as exc:
        cd.guard_version("2026.2", "2026.1")
    assert exc.value.code == "downgrade"


def test_guard_force_allows_same_and_downgrade():
    cd.guard_version("2026.2", "2026.2", force=True)
    cd.guard_version("2026.2", "2025.4", force=True)


def test_guard_unparseable_always_raises():
    with pytest.raises(cd.VersionGuardError) as exc:
        cd.guard_version("2026.1", "latest", force=True)
    assert exc.value.code == "unparseable"
    with pytest.raises(cd.VersionGuardError):
        cd.guard_version("unknown", "2026.2", force=True)


def test_parse_version_orders_numerically():
    # 2026.10 > 2026.2 — numeric minor, not lexicographic.
    assert cd.parse_version("2026.10") > cd.parse_version("2026.2")
    assert cd.parse_version("not-a-version") is None


# ---------------------------------------------------------------------------
# Sanity gates — each trips on its crafted fixture (plan §4.2.2)
# ---------------------------------------------------------------------------


def _check(report: SanityReport, name: str):
    return next(c for c in report.checks if c.check == name)


def test_sanity_all_pass():
    live = _live_catalog({"GOV-A1": _live_control("GOV-A1", "Governance Program")})
    report = cd.run_sanity_checks(_extracted(), live)
    assert isinstance(report, SanityReport)
    assert report.passed is True
    assert {c.check for c in report.checks} == {
        "version_parseable",
        "control_count_drop",
        "zero_rows",
        "framework_names",
    }


def test_sanity_unparseable_version_trips():
    report = cd.run_sanity_checks(_extracted(version="banana"), _live_catalog())
    assert report.passed is False
    assert _check(report, "version_parseable").passed is False


def test_sanity_control_count_drop_trips_beyond_5_percent():
    live = _live_catalog(
        {f"GOV-A{i}": _live_control(f"GOV-A{i}", f"Control {i}") for i in range(100)}
    )
    wb_94 = _extracted(
        controls=[{"scf_id": f"GOV-A{i}", "control_name": f"Control {i}"} for i in range(94)]
    )
    report = cd.run_sanity_checks(wb_94, live)
    assert _check(report, "control_count_drop").passed is False
    assert report.passed is False


def test_sanity_control_count_drop_boundary_passes():
    """A drop of exactly 5% does not trip (>5% is the gate)."""
    live = _live_catalog(
        {f"GOV-A{i}": _live_control(f"GOV-A{i}", f"Control {i}") for i in range(100)}
    )
    wb_95 = _extracted(
        controls=[{"scf_id": f"GOV-A{i}", "control_name": f"Control {i}"} for i in range(95)]
    )
    assert _check(cd.run_sanity_checks(wb_95, live), "control_count_drop").passed is True


def test_sanity_count_drop_ignores_deprecated_live_rows():
    """The drop baseline is the ACTIVE live catalog only."""
    controls = {f"GOV-A{i}": _live_control(f"GOV-A{i}", f"Control {i}") for i in range(50)}
    controls.update(
        {
            f"GOV-B{i}": _live_control(f"GOV-B{i}", f"Old {i}", status="deprecated")
            for i in range(50)
        }
    )
    wb_50 = _extracted(
        controls=[{"scf_id": f"GOV-A{i}", "control_name": f"Control {i}"} for i in range(50)]
    )
    assert (
        _check(cd.run_sanity_checks(wb_50, _live_catalog(controls)), "control_count_drop").passed
        is True
    )


def test_sanity_zero_rows_trips():
    report = cd.run_sanity_checks(_extracted(domains=[]), _live_catalog())
    check = _check(report, "zero_rows")
    assert check.passed is False
    assert "domains" in (check.detail or "")
    assert report.passed is False


def test_sanity_empty_framework_names_trips():
    report = cd.run_sanity_checks(_extracted(framework_names={}), _live_catalog())
    assert _check(report, "framework_names").passed is False
    assert report.passed is False


# ---------------------------------------------------------------------------
# Change classes
# ---------------------------------------------------------------------------


def test_added_entity():
    live = {"GOV-A1": _live_control("GOV-A1", "Governance Program")}
    workbook = {
        "GOV-A1": _control_cols("Governance Program"),
        "GOV-A2": _control_cols("Documentation"),
    }
    diff = cd.compute_entity_diff(workbook, live, cd.CONTROL_COMPARED_FIELDS, "control_name")
    assert [a.key for a in diff.added] == ["GOV-A2"]
    assert diff.added[0].name == "Documentation"
    assert diff.added[0].data == workbook["GOV-A2"]
    assert diff.unchanged == ["GOV-A1"]
    assert not diff.changed and not diff.deprecated and not diff.resurrected


def test_changed_entity_field_level_old_and_new():
    live = {
        "GOV-A1": _live_control(
            "GOV-A1", "Governance Program", control_description="Old text.", control_weighting=8
        )
    }
    workbook = {
        "GOV-A1": _control_cols(
            "Governance Program", control_description="New text.", control_weighting=10
        )
    }
    diff = cd.compute_entity_diff(workbook, live, cd.CONTROL_COMPARED_FIELDS, "control_name")
    assert [c.key for c in diff.changed] == ["GOV-A1"]
    fields = diff.changed[0].fields
    assert set(fields) == {"control_description", "control_weighting"}
    assert fields["control_description"].old == "Old text."
    assert fields["control_description"].new == "New text."
    assert fields["control_weighting"].old == 8
    assert fields["control_weighting"].new == 10


def test_deprecated_entity_active_in_db_absent_from_workbook():
    live = {
        "GOV-A1": _live_control("GOV-A1", "Governance Program"),
        "GOV-A2": _live_control("GOV-A2", "Documentation", superseded_by="GOV-A9"),
    }
    workbook = {"GOV-A1": _control_cols("Governance Program")}
    diff = cd.compute_entity_diff(workbook, live, cd.CONTROL_COMPARED_FIELDS, "control_name")
    assert [d.key for d in diff.deprecated] == ["GOV-A2"]
    assert diff.deprecated[0].name == "Documentation"
    assert diff.deprecated[0].superseded_by == "GOV-A9"


def test_resurrected_entity_with_field_changes():
    live = {
        "GOV-A2": _live_control(
            "GOV-A2", "Documentation", status="deprecated", control_description="Old."
        )
    }
    workbook = {"GOV-A2": _control_cols("Documentation", control_description="Back again.")}
    diff = cd.compute_entity_diff(workbook, live, cd.CONTROL_COMPARED_FIELDS, "control_name")
    assert [r.key for r in diff.resurrected] == ["GOV-A2"]
    assert diff.resurrected[0].fields["control_description"].old == "Old."
    assert diff.resurrected[0].fields["control_description"].new == "Back again."
    assert not diff.deprecated and not diff.changed


def test_already_deprecated_and_still_absent_is_unchanged():
    live = {"GOV-A9": _live_control("GOV-A9", "Retired long ago", status="deprecated")}
    diff = cd.compute_entity_diff({}, live, cd.CONTROL_COMPARED_FIELDS, "control_name")
    assert diff.unchanged == ["GOV-A9"]
    assert not diff.deprecated


def test_normalisation_blank_equals_none_and_list_order_ignored():
    live = {
        "GOV-A1": _live_control(
            "GOV-A1",
            "Governance Program",
            control_question=None,
            evidence_requests=["E-GOV-A2", "E-GOV-A1"],
        )
    }
    workbook = {
        "GOV-A1": _control_cols(
            "Governance Program",
            control_question="",
            evidence_requests=["E-GOV-A1", "E-GOV-A2"],
        )
    }
    diff = cd.compute_entity_diff(workbook, live, cd.CONTROL_COMPARED_FIELDS, "control_name")
    assert diff.unchanged == ["GOV-A1"]
    assert not diff.changed


# ---------------------------------------------------------------------------
# superseded_by suggestion scorer (plan §4.2.3)
# ---------------------------------------------------------------------------


def test_scorer_same_domain_prefix_only():
    suggestions = cd.suggest_successors(
        "GOV-A1",
        "Governance Program",
        {
            "GOV-A7": "Governance Program",  # same prefix, identical name
            "IAC-A1": "Governance Program",  # different prefix — excluded
        },
    )
    assert [s.scf_id for s in suggestions] == ["GOV-A7"]
    assert suggestions[0].score == 1.0


def test_scorer_threshold_top3_and_ordering():
    candidates = {
        "GOV-A2": "Governance Program Reviews",
        "GOV-A3": "Governance Program",
        "GOV-A4": "Governance Program Oversight",
        "GOV-A5": "Governance Programme Management",
        "GOV-A6": "Completely Unrelated Widget Painting",
    }
    suggestions = cd.suggest_successors("GOV-A1", "Governance Program", candidates)
    assert len(suggestions) == 3
    assert suggestions[0].scf_id == "GOV-A3"  # exact match first
    scores = [s.score for s in suggestions]
    assert scores == sorted(scores, reverse=True)
    assert all(s.score >= 0.6 for s in suggestions)
    assert all(isinstance(s, SupersededSuggestion) for s in suggestions)
    assert "GOV-A6" not in {s.scf_id for s in suggestions}


def test_scorer_excludes_self_and_handles_missing_name():
    assert cd.suggest_successors("GOV-A1", None, {"GOV-A2": "Anything"}) == []
    suggestions = cd.suggest_successors(
        "GOV-A1", "Governance Program", {"GOV-A1": "Governance Program"}
    )
    assert suggestions == []


def test_deprecated_controls_get_scored_suggestions_in_full_diff():
    live = _live_catalog(
        {
            "GOV-A1": _live_control("GOV-A1", "Governance Program"),
            "GOV-A2": _live_control("GOV-A2", "Governance Program Reviews"),
        }
    )
    extracted = _extracted(
        controls=[
            {
                "scf_id": "GOV-A1",
                "scf_domain": "Governance",
                "control_name": "Governance Program",
                "control_description": "Mechanisms exist.",
            },
            {
                "scf_id": "GOV-A7",
                "scf_domain": "Governance",
                "control_name": "Governance Program Review Board",
                "control_description": "Mechanisms exist.",
            },
        ]
    )
    detail = cd.compute_catalog_diff(extracted, live, "2026.1")
    deprecated = detail.entities[CatalogEntityType.CONTROLS].deprecated
    assert [d.key for d in deprecated] == ["GOV-A2"]
    # Both workbook controls clear the 0.6 similarity bar against
    # "Governance Program Reviews"; the closer name ranks first.
    suggestion_ids = [s.scf_id for s in deprecated[0].suggestions]
    assert suggestion_ids[0] == "GOV-A7"
    assert set(suggestion_ids) == {"GOV-A7", "GOV-A1"}


# ---------------------------------------------------------------------------
# framework_mappings entity view + capability_themes
# ---------------------------------------------------------------------------


def test_framework_mappings_diff_per_slug():
    live = {
        "GOV-A1": _live_control(
            "GOV-A1", "Governance Program", framework_mappings={"aicpa": ["CC1.1"]}
        ),
        "GOV-A2": _live_control(
            "GOV-A2", "Documentation", framework_mappings={"aicpa": ["CC5.3"]}
        ),
    }
    workbook = {
        "GOV-A1": _control_cols(
            "Governance Program",
            framework_mappings={"aicpa": ["CC1.1", "CC1.2"], "gdpr": ["Art 32"]},
        ),
        "GOV-A2": _control_cols("Documentation", framework_mappings={"aicpa": ["CC5.3"]}),
    }
    diff = cd.compute_framework_mappings_diff(workbook, live)
    assert [c.key for c in diff.changed] == ["GOV-A1"]
    fields = diff.changed[0].fields
    assert set(fields) == {"aicpa", "gdpr"}
    assert fields["aicpa"].old == ["CC1.1"]
    assert fields["aicpa"].new == ["CC1.1", "CC1.2"]
    assert fields["gdpr"].old is None
    assert fields["gdpr"].new == ["Art 32"]
    assert diff.unchanged == ["GOV-A2"]


def test_capability_themes_entity_is_empty_placeholder():
    detail = cd.compute_catalog_diff(_extracted(), _live_catalog(), "2026.1")
    themes = detail.entities[CatalogEntityType.CAPABILITY_THEMES]
    assert not any(
        (themes.added, themes.changed, themes.deprecated, themes.resurrected, themes.unchanged)
    )


# ---------------------------------------------------------------------------
# Full diff + summary are contract-valid and consistent
# ---------------------------------------------------------------------------


def test_full_diff_all_five_classes_and_summary_counts():
    live = _live_catalog(
        {
            "GOV-A1": _live_control("GOV-A1", "Governance Program"),  # unchanged
            "GOV-A2": _live_control(
                "GOV-A2", "Documentation", control_description="Old."
            ),  # changed
            "GOV-A3": _live_control("GOV-A3", "Reviews"),  # deprecated
            "GOV-A4": _live_control("GOV-A4", "Steering", status="deprecated"),  # resurrected
        }
    )

    def raw(scf_id, name, description="Mechanisms exist."):
        return {
            "scf_id": scf_id,
            "scf_domain": "Cybersecurity & Data Protection Governance",
            "control_name": name,
            "control_description": description,
            "pptdf_applicability": {"process": True},
        }

    extracted = _extracted(
        controls=[
            raw("GOV-A1", "Governance Program", "Mechanisms exist for Governance Program."),
            raw("GOV-A2", "Documentation", "New."),
            raw("GOV-A4", "Steering", "Mechanisms exist for Steering."),
            raw("GOV-A5", "Metrics"),  # added
        ]
    )
    detail = cd.compute_catalog_diff(extracted, live, "2026.1")
    assert isinstance(detail, DiffDetail)
    assert detail.from_version == "2026.1"
    assert detail.to_version == "2026.2"
    assert set(detail.entities) == set(CatalogEntityType)

    controls = detail.entities[CatalogEntityType.CONTROLS]
    assert [a.key for a in controls.added] == ["GOV-A5"]
    assert [c.key for c in controls.changed] == ["GOV-A2"]
    assert [d.key for d in controls.deprecated] == ["GOV-A3"]
    assert [r.key for r in controls.resurrected] == ["GOV-A4"]
    assert controls.unchanged == ["GOV-A1"]

    summary = cd.summarize_diff(detail)
    assert isinstance(summary, DiffSummary)
    counts = summary.entities[CatalogEntityType.CONTROLS]
    assert (counts.added, counts.changed, counts.deprecated, counts.resurrected, counts.unchanged) == (
        1,
        1,
        1,
        1,
        1,
    )

    # The stored diff must be JSON-serialisable (JSONB / object storage).
    json.loads(detail.model_dump_json())
    json.loads(summary.model_dump_json())


def test_controls_entity_owns_framework_mappings_revert_anchor():
    """The controls changed-field set includes framework_mappings — the revert
    authority for that column (the framework_mappings entity is display-only)."""
    live = _live_catalog(
        {
            "GOV-A1": _live_control(
                "GOV-A1", "Governance Program", framework_mappings={"aicpa": ["CC1.1"]}
            )
        }
    )
    extracted = _extracted(
        controls=[
            {
                "scf_id": "GOV-A1",
                "scf_domain": "Governance",
                "control_name": "Governance Program",
                "control_description": "Mechanisms exist.",
                "framework_mappings": {"aicpa": ["CC1.1", "CC1.2"]},
            }
        ]
    )
    detail = cd.compute_catalog_diff(extracted, live, "2026.1")
    changed = detail.entities[CatalogEntityType.CONTROLS].changed
    changed_fields = {f for c in changed for f in c.fields}
    assert "framework_mappings" in changed_fields
    fw_view = detail.entities[CatalogEntityType.FRAMEWORK_MAPPINGS]
    assert [c.key for c in fw_view.changed] == ["GOV-A1"]


# ---------------------------------------------------------------------------
# Staging entry point over a real synthetic workbook + faked session
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class FakeSession:
    """Answers the four load_live_catalog selects in order:
    controls, domains, evidence, assessment objectives."""

    def __init__(self, controls=(), domains=(), evidence=(), objectives=()):
        self._results = [list(controls), list(domains), list(evidence), list(objectives)]

    async def execute(self, _stmt):
        return _FakeResult(self._results.pop(0))


def _orm_control(scf_id, name, status="active", **field_over):
    attrs = _control_cols(name, **field_over)
    return SimpleNamespace(
        scf_id=scf_id, status=status, superseded_by=None, **attrs
    )


def _orm_domain(identifier, **over):
    attrs = {
        "identifier": identifier,
        "status": "active",
        "superseded_by": None,
        "order": 1,
        "name": "Cybersecurity & Data Protection Governance",
        "principle": "Execute a documented, risk-based program.",
        "principle_intent": "Organizations specify the development of a program.",
    }
    attrs.update(over)
    return SimpleNamespace(**attrs)


def _orm_evidence(evidence_id, title, **over):
    attrs = {
        "evidence_id": evidence_id,
        "status": "active",
        "superseded_by": None,
        "area_of_focus": "Governance",
        "artifact_title": title,
        "artifact_description": None,
        "control_mappings": [],
    }
    attrs.update(over)
    return SimpleNamespace(**attrs)


def _orm_ao(ao_id, scf_id, **over):
    attrs = {f: None for f in cd.AO_COMPARED_FIELDS}
    attrs.update(
        ao_id=ao_id,
        status="active",
        superseded_by=None,
        scf_id=scf_id,
        objective_text="the organization facilitates a governance program.",
        pptdf_people=False,
        pptdf_process=True,
        pptdf_technology=False,
        pptdf_data=False,
        pptdf_facility=False,
        ao_origins="SCF",
        assessment_rigor=3,
        assessment_procedure="Examine the program charter.",
        expected_results="A charter exists and is approved.",
    )
    attrs.update(over)
    return SimpleNamespace(**attrs)


def _matching_live_session():
    """A fake live catalog matching the synthetic workbook's rows, plus one
    active control absent from the workbook (⇒ deprecated)."""
    return FakeSession(
        controls=[
            _orm_control(
                "GOV-A1",
                "Cybersecurity & Data Protection Governance Program",
                control_description=(
                    "Mechanisms exist to facilitate the implementation of a governance program."
                ),
                control_question="Does the organization facilitate a governance program?",
                validation_cadence="Annual",
                control_weighting=10,
                nist_csf_function="Govern",
                evidence_requests=["E-GOV-A1", "E-GOV-A2"],
                framework_mappings={
                    "aicpa_tsc_2017_2022_used_for_soc_2": ["CC1.1", "CC1.2"],
                    "gdpr_eu_general_data_protection_regulation": ["Art 32"],
                },
                cmm_level_0="Practices are non-existent.",
            ),
            _orm_control("GOV-A9", "Governance Program Metrics"),
        ],
        domains=[_orm_domain("GOV")],
        evidence=[
            _orm_evidence(
                "E-GOV-A1",
                "Cybersecurity Program Charter",
                artifact_description="Charter for the cybersecurity program.",
                control_mappings=["GOV-A1", "GOV-A2"],
            ),
            _orm_evidence(
                "E-GOV-A2",
                "Steering Committee Minutes",
                artifact_description="Minutes evidencing oversight.",
                control_mappings=["GOV-A1"],
            ),
        ],
        objectives=[_orm_ao("GOV-A1.1", "GOV-A1")],
    )


@pytest.mark.asyncio
async def test_stage_catalog_diff_end_to_end(tmp_path):
    workbook = build_workbook(tmp_path / "scf.xlsx", version="2026.2", era="focal_documents")
    staged = await cd.stage_catalog_diff(
        _matching_live_session(), workbook, "2026.1"
    )

    assert staged.to_version == "2026.2"
    assert staged.forced is False
    assert staged.sanity_report.passed is True
    assert isinstance(staged.diff_detail, DiffDetail)
    assert isinstance(staged.diff_summary, DiffSummary)

    controls = staged.diff_detail.entities[CatalogEntityType.CONTROLS]
    assert "GOV-A1" in controls.unchanged
    assert [a.key for a in controls.added] == ["GOV-A2"]
    assert [d.key for d in controls.deprecated] == ["GOV-A9"]

    aos = staged.diff_detail.entities[CatalogEntityType.ASSESSMENT_OBJECTIVES]
    assert aos.unchanged == ["GOV-A1.1"]


@pytest.mark.asyncio
async def test_stage_refuses_same_version_unless_forced(tmp_path):
    workbook = build_workbook(tmp_path / "scf.xlsx", version="2026.2", era="focal_documents")
    with pytest.raises(cd.VersionGuardError) as exc:
        await cd.stage_catalog_diff(_matching_live_session(), workbook, "2026.2")
    assert exc.value.code == "same_version"

    staged = await cd.stage_catalog_diff(
        _matching_live_session(), workbook, "2026.2", force=True
    )
    assert staged.forced is True
    assert staged.diff_detail is not None


@pytest.mark.asyncio
async def test_stage_blocks_on_sanity_failure_without_diff(tmp_path):
    workbook = build_workbook(tmp_path / "scf.xlsx", version="2026.2", era="focal_documents")
    # 100 active live controls vs 2 in the workbook ⇒ count-drop gate trips.
    session = FakeSession(
        controls=[_orm_control(f"GOV-A{i}", f"Control {i}") for i in range(100)],
        domains=[_orm_domain("GOV")],
        evidence=[],
        objectives=[],
    )
    staged = await cd.stage_catalog_diff(session, workbook, "2026.1")
    assert staged.sanity_report.passed is False
    assert staged.diff_detail is None
    assert staged.diff_summary is None
    failed = {c.check for c in staged.sanity_report.checks if not c.passed}
    assert "control_count_drop" in failed


@pytest.mark.asyncio
async def test_extraction_uses_private_temp_dir_and_cleans_up(tmp_path, monkeypatch):
    workbook = build_workbook(tmp_path / "scf.xlsx", version="2026.2", era="focal_documents")
    created = []
    real_mkdtemp = tempfile.mkdtemp

    def spy_mkdtemp(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        created.append(path)
        return path

    monkeypatch.setattr(cd.tempfile, "mkdtemp", spy_mkdtemp)
    extracted = cd.extract_workbook(workbook)

    assert extracted.catalog_version == "2026.2"
    assert len(created) == 1
    run_dir = Path(created[0])
    assert run_dir.name.startswith("catalog-upgrade-")
    # Private per-run dir under the system temp root — never the shared DATA_DIR
    # (which lives under /app/data or the webclient tree).
    assert str(run_dir).startswith(tempfile.gettempdir())
    # And it is removed before extract_workbook returns.
    assert not run_dir.exists()


@pytest.mark.asyncio
async def test_stage_raises_for_unrecognisable_workbook(tmp_path):
    workbook = build_workbook(
        tmp_path / "bad.xlsx", version="2026.2", controls_sheet_title="SCF Controls"
    )
    with pytest.raises(ValueError, match="catalog version"):
        await cd.stage_catalog_diff(FakeSession(), workbook, "2026.1")
