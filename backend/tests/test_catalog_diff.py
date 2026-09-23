"""Tests for backend/services/catalog_diff.py (WP1a, plan §4.2.2-3, §4.7).

Covers, without a live database (the repo's unit-test pattern — DB access is
faked at the session boundary):

- all five change classes (added / changed / deprecated / resurrected /
  unchanged), producing contract-valid ``DiffDetail`` / ``DiffSummary``
  instances (imported from ``schemas_catalog_upgrade``);
- every sanity gate tripping on its crafted fixture;
- the version guard (same-version / downgrade / unparseable / force);
- declared control succession: the Legacy SCF # crosswalk, the READ THIS merge
  list, their precedence, and the absence of any generated suggestion;
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
from dataclasses import fields
from functools import lru_cache
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
from test_scf_extractor import AICPA_HEADER, build_workbook  # noqa: E402


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
        "control_churn",
        "zero_rows",
        "framework_names",
        "framework_churn",
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
# No generated successor proposals (plan §4.2.3 — the workbook is the authority)
# ---------------------------------------------------------------------------


def test_the_name_similarity_scorer_is_gone():
    """Not a style point: a proposal in the suggestions list is a liability.

    The scorer used to emit up to three same-domain candidates above 0.6 name
    similarity. On 2026.2->2026.3 that added 1-2 extra chips to 377 of the 801
    deprecations, decided nothing, and sat in the same list as the publisher's
    own declaration. Asserting its absence by name is what stops it coming
    back through a helpful refactor.
    """
    assert not hasattr(cd, "suggest_successors")
    assert not hasattr(cd, "SUGGESTION_SIMILARITY_THRESHOLD")
    assert not hasattr(cd, "SUGGESTION_TOP_N")
    assert "suggestion_candidates" not in cd.compute_entity_diff.__code__.co_varnames


def test_deprecated_controls_get_no_suggestion_without_a_declaration():
    """A near-identical name is not a succession claim and must produce nothing.

    "Governance Program Review Board" scores 0.87 against "Governance Program
    Reviews" and shares its domain, which is precisely what the old scorer
    would have surfaced. The workbook declares no crosswalk here, so the
    deprecation must arrive bare.
    """
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
    assert deprecated[0].suggestions == []
    assert deprecated[0].superseded_by is None
    assert deprecated[0].superseded_source is None
    counts = cd.summarize_diff(detail).entities[CatalogEntityType.CONTROLS]
    assert counts.renamed == 0


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
    def __init__(self, rows, scalar=None):
        self._rows = rows
        self._scalar = scalar

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._scalar


class FakeSession:
    """Answers the four load_live_catalog selects in order:
    controls, domains, evidence, assessment objectives.

    load_live_catalog also resolves the live catalogue version and reads that
    version's framework registry row (fwreg001). Those three selects are
    answered by table name rather than from the ordered queue, so they cannot
    consume an entity result. Default: no ledger run, no stamped version and no
    registry row — which is the pre-fwreg001 install whose live focal-document
    identifiers come from the JSON artifact.
    """

    def __init__(
        self,
        controls=(),
        domains=(),
        evidence=(),
        objectives=(),
        registry=None,
        live_version=None,
        registry_source="apply",
    ):
        self._results = [list(controls), list(domains), list(evidence), list(objectives)]
        self._live_version = live_version
        self._registry_row = (
            SimpleNamespace(
                catalog_version=live_version,
                registry=registry,
                source=registry_source,
            )
            if registry is not None
            else None
        )

    async def execute(self, stmt):
        sql = str(stmt)
        if "catalog_import_runs" in sql:
            return _FakeResult([])  # no applied run; falls through to max()
        if "max(scf_catalog_controls.catalog_version)" in sql:
            return _FakeResult([], scalar=self._live_version)
        if "catalog_framework_registries" in sql:
            return _FakeResult([self._registry_row] if self._registry_row else [])
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


# The two frameworks the synthetic workbook's controls map to, with the
# publisher identifiers a post-fwreg001 install stores for them. Staging now
# gates on this row existing and carrying identifiers, so the fake live
# catalogue has to look like an install that applied an upgrade normally.
# These are the identifiers the synthetic workbook itself declares, so the live
# catalogue looks like the result of applying that workbook: the AICPA column
# carries an FDI and the GDPR column does not.
_LIVE_REGISTRY = {
    "aicpa_tsc_2017_2022_used_for_soc_2": {
        "name": (
            "American Institute of Certified Public Accountants (AICPA) "
            "Trust Services Criteria (2017)"
        ),
        "focal_document_id": "general-aicpa-tsc-2017",
        "geography": "General",
    },
    "gdpr_eu_general_data_protection_regulation": {
        "name": "GDPR EU General Data Protection Regulation",
        "focal_document_id": None,
        "geography": None,
    },
}


def _matching_live_session():
    """A fake live catalog matching the synthetic workbook's rows, plus one
    active control absent from the workbook (⇒ deprecated)."""
    return FakeSession(
        registry=_LIVE_REGISTRY,
        live_version="2026.1",
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
async def test_staging_seeds_no_pairings_onto_the_run(tmp_path):
    """The declaration lives in the diff; the pairings column is overrides only.

    Staging used to copy every declared successor into
    ``run.superseded_pairings``. That made the admin's override list and the
    publisher's declaration the same list, so the first partial save through an
    editor that pages at 200 rows deleted the other 601 declarations. Apply now
    reads declarations from the stored diff, so nothing needs seeding, and the
    attribute that carried them must be gone rather than merely unused.
    """
    workbook = build_workbook(tmp_path / "scf.xlsx", version="2026.2", era="focal_documents")
    staged = await cd.stage_catalog_diff(
        _matching_live_session(), workbook, "2026.1"
    )
    assert not hasattr(staged, "suggested_pairings")
    assert "suggested_pairings" not in {f.name for f in fields(cd.StagedDiff)}


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


# ---------------------------------------------------------------------------
# Legacy SCF # crosswalk (2026.3 renumbering)
# ---------------------------------------------------------------------------


def _wb_control(scf_id: str, name: str, legacy: list | None = None) -> dict:
    ctrl = {
        "scf_id": scf_id,
        "scf_domain": "Governance",
        "control_name": name,
        "control_description": "Mechanisms exist.",
    }
    if legacy is not None:
        ctrl["legacy_scf_ids"] = legacy
    return ctrl


def test_crosswalk_maps_predecessor_to_successor():
    extracted = _extracted(controls=[_wb_control("GOV-02", "Program", ["GOV-01"])])
    assert cd.build_legacy_crosswalk(extracted) == {"GOV-01": "GOV-02"}


def test_crosswalk_omits_self_referential_entries():
    """A control that kept its id is not a rename."""
    extracted = _extracted(controls=[_wb_control("GOV-01", "Program", ["GOV-01"])])
    assert cd.build_legacy_crosswalk(extracted) == {}


def test_crosswalk_records_a_merge_as_two_predecessors():
    """Two retired controls collapsing into one both point at the survivor."""
    extracted = _extracted(
        controls=[_wb_control("AST-22", "Assets", ["AST-09", "TDA-11.2"])]
    )
    assert cd.build_legacy_crosswalk(extracted) == {
        "AST-09": "AST-22",
        "TDA-11.2": "AST-22",
    }


def test_crosswalk_empty_for_workbooks_without_the_column():
    """Pre-2026.3 workbooks carry no crosswalk; behaviour is unchanged."""
    extracted = _extracted(controls=[_wb_control("GOV-01", "Program")])
    assert cd.build_legacy_crosswalk(extracted) == {}


def test_deprecation_takes_superseded_by_from_the_crosswalk():
    workbook = {"GOV-02": _control_cols("Program")}
    live = {"GOV-01": _live_control("GOV-01", "Program")}
    diff = cd.compute_entity_diff(
        workbook,
        live,
        cd.CONTROL_COMPARED_FIELDS,
        name_field="control_name",
        legacy_crosswalk={"GOV-01": "GOV-02"},
    )
    assert len(diff.deprecated) == 1
    assert diff.deprecated[0].superseded_by == "GOV-02"
    assert diff.deprecated[0].superseded_source == "workbook_crosswalk"


def test_the_declared_successor_is_the_only_suggestion():
    """One entry, full score, the source named, the name read off the workbook.

    The name matters: the console renders the chip, and a deprecation whose
    successor was renamed in the same release ("Totally Different Name") must
    show the NEW name, not the retiring control's.
    """
    workbook = {
        "GOV-02": _control_cols("Totally Different Name"),
        "GOV-08": _control_cols("Near Namesake Program"),
    }
    live = {"GOV-01": _live_control("GOV-01", "Namesake Program")}
    diff = cd.compute_entity_diff(
        workbook,
        live,
        cd.CONTROL_COMPARED_FIELDS,
        name_field="control_name",
        legacy_crosswalk={"GOV-01": "GOV-02"},
    )
    suggestions = diff.deprecated[0].suggestions
    assert len(suggestions) == 1
    assert suggestions[0].scf_id == "GOV-02"
    assert suggestions[0].name == "Totally Different Name"
    assert suggestions[0].score == 1.0
    assert suggestions[0].signals == ["workbook_crosswalk"]


def test_the_declaration_outranks_a_pre_existing_live_pairing():
    """The workbook is the authority, so a stale row value cannot shadow it.

    This inverts the earlier rule. The stored diff is the only carrier of the
    declaration into apply, so a live row that already names a successor must
    not be allowed to hide it: the channel for overriding a declaration is this
    run's pairings list, which apply consults per key.
    """
    workbook = {"GOV-02": _control_cols("Program"), "GOV-09": _control_cols("Other")}
    live = {"GOV-01": _live_control("GOV-01", "Program", superseded_by="GOV-09")}
    diff = cd.compute_entity_diff(
        workbook,
        live,
        cd.CONTROL_COMPARED_FIELDS,
        name_field="control_name",
        legacy_crosswalk={"GOV-01": "GOV-02"},
    )
    assert diff.deprecated[0].superseded_by == "GOV-02"
    assert diff.deprecated[0].superseded_source == "workbook_crosswalk"


def test_a_pre_existing_live_pairing_survives_where_nothing_is_declared():
    """Still a fallback, and still not this run's claim.

    A retirement the workbook says nothing about keeps whatever an admin
    recorded earlier, and ``superseded_source`` stays None so the summary does
    not count it as a rename this release performed.
    """
    workbook = {"GOV-02": _control_cols("Program"), "GOV-09": _control_cols("Other")}
    live = {"GOV-01": _live_control("GOV-01", "Program", superseded_by="GOV-09")}
    diff = cd.compute_entity_diff(
        workbook,
        live,
        cd.CONTROL_COMPARED_FIELDS,
        name_field="control_name",
        legacy_crosswalk={},
    )
    assert diff.deprecated[0].superseded_by == "GOV-09"
    assert diff.deprecated[0].superseded_source is None
    assert diff.deprecated[0].suggestions == []


# ---------------------------------------------------------------------------
# READ THIS merge list: the second declared source, and the id-reuse flag
# ---------------------------------------------------------------------------


def _merges(*entries) -> dict:
    """publisher_changes-shaped dict holding just the merge list."""
    return {
        "controls": {
            "merged": [
                {
                    "legacy_scf_id": legacy,
                    "legacy_name": legacy_name,
                    "merged_into": survivor,
                }
                for legacy, legacy_name, survivor in entries
            ]
        }
    }


def test_build_publisher_merges_reads_the_read_this_block():
    merges = cd.build_publisher_merges(
        _extracted_with_publisher(
            _merges(
                ("OLD-A1", "Component Disposal", "GOV-02"),
                ("OLD-A2", None, "GOV-03"),
                ("OLD-A3", "Self merge", "OLD-A3"),  # nonsense, dropped
                ("", "No id", "GOV-04"),  # nonsense, dropped
                ("OLD-A4", "No survivor", ""),  # nonsense, dropped
                ("OLD-A1", "Second mention", "GOV-99"),  # first mention wins
            )
        )
    )
    assert merges == {
        "OLD-A1": ("GOV-02", "Component Disposal"),
        "OLD-A2": ("GOV-03", None),
    }


def test_build_publisher_merges_empty_before_2026_3():
    assert cd.build_publisher_merges(_extracted(controls=[_wb_control("GOV-01", "P")])) == {}


def test_deprecation_takes_superseded_by_from_the_merge_list():
    """A control the crosswalk is silent about but the merge list names."""
    workbook = {"GOV-02": _control_cols("Survivor")}
    live = {"GOV-01": _live_control("GOV-01", "Retiree")}
    diff = cd.compute_entity_diff(
        workbook,
        live,
        cd.CONTROL_COMPARED_FIELDS,
        name_field="control_name",
        legacy_crosswalk={},
        publisher_merges={"GOV-01": ("GOV-02", "Retiree")},
    )
    dep = diff.deprecated[0]
    assert dep.superseded_by == "GOV-02"
    assert dep.superseded_source == "publisher_merged"
    assert [(s.scf_id, s.score, s.signals) for s in dep.suggestions] == [
        ("GOV-02", 1.0, ["publisher_merged"])
    ]


def test_the_crosswalk_outranks_the_merge_list_where_both_speak():
    workbook = {"GOV-02": _control_cols("From crosswalk"), "GOV-03": _control_cols("From prose")}
    live = {"GOV-01": _live_control("GOV-01", "Retiree")}
    diff = cd.compute_entity_diff(
        workbook,
        live,
        cd.CONTROL_COMPARED_FIELDS,
        name_field="control_name",
        legacy_crosswalk={"GOV-01": "GOV-02"},
        publisher_merges={"GOV-01": ("GOV-03", "Retiree")},
    )
    assert diff.deprecated[0].superseded_by == "GOV-02"
    assert diff.deprecated[0].superseded_source == "workbook_crosswalk"


def test_a_merge_successor_absent_from_the_workbook_is_dropped():
    """Same rule as the crosswalk: a declaration pointing outside is stale."""
    workbook = {"GOV-02": _control_cols("Survivor")}
    live = {"GOV-01": _live_control("GOV-01", "Retiree")}
    diff = cd.compute_entity_diff(
        workbook,
        live,
        cd.CONTROL_COMPARED_FIELDS,
        name_field="control_name",
        legacy_crosswalk={},
        publisher_merges={"GOV-01": ("SOMEWHERE-ELSE", "Retiree")},
    )
    assert diff.deprecated[0].superseded_by is None
    assert diff.deprecated[0].superseded_source is None
    assert diff.deprecated[0].suggestions == []


def test_a_merged_away_id_still_in_the_workbook_is_flagged_as_reused():
    """The END-03 shape: merged into CHG-04.2, yet END-03 is still a key.

    The row can only be ``changed`` — the key is on both sides — so the flag is
    the only thing telling an operator that the wholesale rewrite of name and
    description is an id changing hands, not a control being edited.
    """
    workbook = {"GOV-01": _control_cols("Endpoint Protection Mechanisms")}
    live = {"GOV-01": _live_control("GOV-01", "Prohibit Installation")}
    diff = cd.compute_entity_diff(
        workbook,
        live,
        cd.CONTROL_COMPARED_FIELDS,
        name_field="control_name",
        legacy_crosswalk={},
        publisher_merges={"GOV-01": ("GOV-09", "Prohibit Installation")},
    )
    assert diff.deprecated == []
    assert [c.key for c in diff.changed] == ["GOV-01"]
    assert diff.changed[0].id_reused.merged_into == "GOV-09"
    assert diff.changed[0].id_reused.legacy_name == "Prohibit Installation"


def test_a_changed_control_the_publisher_did_not_merge_is_not_flagged():
    workbook = {"GOV-01": _control_cols("Renamed")}
    live = {"GOV-01": _live_control("GOV-01", "Original")}
    diff = cd.compute_entity_diff(
        workbook,
        live,
        cd.CONTROL_COMPARED_FIELDS,
        name_field="control_name",
        publisher_merges={"GOV-77": ("GOV-09", "Something else")},
    )
    assert diff.changed[0].id_reused is None


def test_a_reused_id_never_vetoes_a_pairing_that_points_at_it():
    """The TDA-02.6 -> TDA-11.2 shape. Eight rows like it on 2026.3.

    TDA-11.2 was merged into AST-22 AND its id was handed to a new control, so
    the id carries a reuse flag. A different retiring control legitimately
    renumbers INTO that id, and the workbook says so. The reuse flag sits on the
    changed row and describes the id's history; it is not a veto, and the
    pairing must survive untouched.
    """
    workbook = {
        "GOV-02": _control_cols("Insecure Ports, Protocols & Services"),
        "GOV-09": _control_cols("Asset Disposal"),
    }
    live = {
        "GOV-02": _live_control("GOV-02", "Component Disposal"),
        "GOV-01": _live_control("GOV-01", "Ports In Use"),
    }
    diff = cd.compute_entity_diff(
        workbook,
        live,
        cd.CONTROL_COMPARED_FIELDS,
        name_field="control_name",
        legacy_crosswalk={"GOV-01": "GOV-02"},
        publisher_merges={"GOV-02": ("GOV-09", "Component Disposal")},
    )
    # The reused id is flagged on its own changed row...
    assert diff.changed[0].key == "GOV-02"
    assert diff.changed[0].id_reused.merged_into == "GOV-09"
    # ...and the unrelated deprecation still pairs INTO it.
    assert diff.deprecated[0].key == "GOV-01"
    assert diff.deprecated[0].superseded_by == "GOV-02"
    assert diff.deprecated[0].superseded_source == "workbook_crosswalk"


def test_crosswalk_successor_absent_from_workbook_is_ignored():
    """A crosswalk pointing outside this catalog is stale, not authoritative."""
    workbook = {"GOV-02": _control_cols("Program")}
    live = {"GOV-01": _live_control("GOV-01", "Program")}
    diff = cd.compute_entity_diff(
        workbook,
        live,
        cd.CONTROL_COMPARED_FIELDS,
        name_field="control_name",
        legacy_crosswalk={"GOV-01": "SOMEWHERE-ELSE"},
    )
    assert diff.deprecated[0].superseded_by is None
    assert diff.deprecated[0].superseded_source is None


def _churn_catalogs(n: int, explained: bool):
    """n live controls all renumbered; crosswalk present only if `explained`."""
    live = {
        f"OLD-{i}": _live_control(f"OLD-{i}", f"Control {i}") for i in range(n)
    }
    controls = [
        _wb_control(f"NEW-{i}", f"Control {i}", [f"OLD-{i}"] if explained else [])
        for i in range(n)
    ]
    return _extracted(version="2026.3", controls=controls), _live_catalog(live)


def test_churn_gate_passes_when_the_crosswalk_explains_the_renumbering():
    extracted, live = _churn_catalogs(200, explained=True)
    report = cd.run_sanity_checks(extracted, live)
    check = _check(report, "control_churn")
    assert check.passed is True
    assert "200 explained" in check.detail


def test_churn_gate_fails_on_unexplained_mass_retirement():
    """The 2026.3 shape: net count barely moves, the whole catalog is replaced."""
    extracted, live = _churn_catalogs(200, explained=False)
    report = cd.run_sanity_checks(extracted, live)
    assert _check(report, "control_churn").passed is False
    assert report.passed is False
    # The net-count gate cannot see it: 200 out, 200 in.
    assert _check(report, "control_count_drop").passed is True


def test_churn_gate_ignores_a_handful_of_genuine_retirements():
    live = {f"OLD-{i}": _live_control(f"OLD-{i}", f"Control {i}") for i in range(10)}
    extracted = _extracted(
        version="2026.3",
        controls=[_wb_control(f"OLD-{i}", f"Control {i}") for i in range(5)],
    )
    assert _check(cd.run_sanity_checks(extracted, _live_catalog(live)), "control_churn").passed


def test_count_check_names_a_rise_as_a_rise():
    """A signed percentage beside the word 'drop' rendered a rise as a drop."""
    live = _live_catalog(
        {f"OLD-{i}": _live_control(f"OLD-{i}", f"Control {i}") for i in range(100)}
    )
    extracted = _extracted(
        controls=[_wb_control(f"OLD-{i}", f"Control {i}") for i in range(104)]
    )
    detail = _check(cd.run_sanity_checks(extracted, live), "control_count_drop").detail
    assert "rise" in detail
    assert "drop" not in detail


def test_count_check_still_names_a_drop_as_a_drop():
    live = _live_catalog(
        {f"OLD-{i}": _live_control(f"OLD-{i}", f"Control {i}") for i in range(100)}
    )
    extracted = _extracted(
        controls=[_wb_control(f"OLD-{i}", f"Control {i}") for i in range(96)]
    )
    detail = _check(cd.run_sanity_checks(extracted, live), "control_count_drop").detail
    assert "fewer" in detail and "drop" in detail


@pytest.mark.asyncio
async def test_load_live_catalog_decorates_frameworks_from_the_stored_registry(
    tmp_path, monkeypatch
):
    """load_live_catalog reads the registry row, not the mounted JSON artifact.

    The whole seam the framework_churn gate depends on: the live focal-document
    identifiers must come from the row that committed with the catalogue rows,
    so a stale or foreign DATA_DIR cannot decide whether an upgrade is blocked.
    """
    import catalog_seeder

    import extract_scf_data

    aicpa = extract_scf_data.normalize_framework_id(AICPA_HEADER)
    (tmp_path / "framework_registry.json").write_text(
        '{"%s": {"name": "STALE", "focal_document_id": "stale-identifier"}}' % aicpa
    )
    monkeypatch.setattr(catalog_seeder, "DATA_DIR", tmp_path)

    control = _orm_control(
        "GOV-A1", "Governance", framework_mappings={aicpa: ["CC1.1"]}
    )

    from_file = await cd.load_live_catalog(FakeSession(controls=[control]))
    assert from_file.frameworks[aicpa].fields["focal_document_id"] == "stale-identifier"

    from_db = await cd.load_live_catalog(
        FakeSession(
            controls=[control],
            live_version="2026.1",
            registry={
                aicpa: {
                    "name": "AICPA Trust Services Criteria",
                    "focal_document_id": "general-aicpa-tsc-2017",
                }
            },
        )
    )
    assert from_db.frameworks[aicpa].fields["focal_document_id"] == (
        "general-aicpa-tsc-2017"
    )
    assert from_db.frameworks[aicpa].name == "AICPA Trust Services Criteria"


# ---------------------------------------------------------------------------
# Publisher changes: workbook -> DiffDetail -> DiffSummary
# ---------------------------------------------------------------------------

_PUBLISHER_RAW = {
    "summary": "This release renumbers the catalogue.",
    "frameworks": {
        "added": [{"fdi": "usa-federal-cmmc-3-0", "name": "CMMC 3.0"}],
        "removed": [
            {"fdi": "emea-deu-c5-2020", "name": "Germany C5 (2020)"},
            {"fdi": "general-sparta", "name": "SPARTA"},
        ],
        "mapping_errata": [
            {
                "fdi": "general-iso-27002-2022",
                "name": "ISO 27002:2022",
                "note": "FDE #: 8.10, 8.12",
            }
        ],
    },
    "controls": {
        "counts": {"renumbered": 1457, "new_control": 80, "merged": 22},
        "merged": [
            {
                "legacy_scf_id": "OLD-Z9",
                "legacy_name": "Old Thing",
                "merged_into": "GOV-A1",
            }
        ],
        "tags": {"GOV-A1": ["renumbered", "wordsmithed"]},
    },
}

_PUBLISHER_EMPTY = {
    "summary": None,
    "frameworks": {"added": [], "removed": [], "mapping_errata": []},
    "controls": {"counts": {}, "merged": [], "tags": {}},
}


def test_extract_workbook_reads_publisher_changes_from_the_extractor_artifact(
    tmp_path, monkeypatch
):
    """``publisher_changes.json`` is a new artifact and must be picked up.

    The extractor writes it beside frameworks.json and framework_registry.json.
    Reading it here is what carries the publisher's declarations into the diff;
    without this the errata sheet is parsed and then thrown away.
    """
    workbook = build_workbook(
        tmp_path / "scf.xlsx", version="2026.2", era="focal_documents"
    )
    real_extract_to_dir = cd._load_extractor().extract_to_dir

    def _also_write_publisher_changes(path, out_dir):
        meta = real_extract_to_dir(path, out_dir)
        (Path(out_dir) / "publisher_changes.json").write_text(
            json.dumps(_PUBLISHER_RAW)
        )
        return meta

    extractor = cd._load_extractor()
    monkeypatch.setattr(
        extractor, "extract_to_dir", _also_write_publisher_changes
    )

    extracted = cd.extract_workbook(workbook)

    assert extracted.publisher_changes["summary"] == (
        "This release renumbers the catalogue."
    )
    assert len(extracted.publisher_changes["frameworks"]["removed"]) == 2


def test_a_pre_2026_3_workbook_extracts_to_the_empty_publisher_shape(tmp_path):
    """The artifact is always written; for a pre-2026.3 workbook it is empty.

    Which is deliberate: a missing file and a file saying "no change sheets"
    would otherwise be indistinguishable from a failed extraction. The contract
    boundary treats both as "no panel", so ``build_publisher_changes`` returns
    None either way.
    """
    workbook = build_workbook(
        tmp_path / "scf.xlsx", version="2026.2", era="focal_documents"
    )
    extracted = cd.extract_workbook(workbook)

    assert extracted.publisher_changes == _PUBLISHER_EMPTY
    assert cd.publisher_changes_are_empty(extracted.publisher_changes) is True
    assert cd.build_publisher_changes(extracted) is None
    # And an extraction that wrote no artifact at all reads the same way.
    assert cd.build_publisher_changes(_extracted_with_publisher({})) is None


def _extracted_with_publisher(raw):
    return cd.ExtractedCatalog(
        catalog_version="2026.3",
        controls=[{"scf_id": "GOV-A1", "control_name": "Governance"}],
        domains=[{"identifier": "GOV"}],
        evidence={},
        assessment_objectives=[],
        framework_names={"fw_a": "Framework A"},
        publisher_changes=raw,
    )


def test_publisher_changes_reach_the_diff_detail_and_the_summary():
    """The frontend reads ``diff_summary.publisher_changes``.

    The detail carries the lists (it is the stored blob) and the summary carries
    only counts (it is a database column read on every runs-list request), so
    both halves have to be populated and neither may carry the other's payload.
    """
    detail = cd.compute_catalog_diff(
        _extracted_with_publisher(_PUBLISHER_RAW), cd.LiveCatalog(), "2026.2"
    )

    assert detail.publisher_changes is not None
    assert [f.fdi for f in detail.publisher_changes.frameworks.removed] == [
        "emea-deu-c5-2020",
        "general-sparta",
    ]
    assert detail.publisher_changes.controls.merged[0].merged_into == "GOV-A1"

    summary = cd.summarize_diff(detail)
    assert summary.publisher_changes is not None
    assert summary.publisher_changes.frameworks_added == 1
    assert summary.publisher_changes.frameworks_removed == 2
    assert summary.publisher_changes.mapping_errata == 1
    assert summary.publisher_changes.controls["renumbered"] == 1457
    assert summary.publisher_changes.summary == (
        "This release renumbers the catalogue."
    )
    # The summary is a column, not a blob: it must not carry the lists.
    assert not hasattr(summary.publisher_changes, "frameworks")


def test_a_release_that_published_nothing_leaves_both_halves_none():
    """None, not a row of zeros.

    "The publisher shipped no change sheets" and "the publisher shipped them
    and changed nothing" are different facts. Rendering the second for the first
    would tell an operator the 2026.2 release renamed nothing, which the
    workbook never said.
    """
    detail = cd.compute_catalog_diff(
        _extracted_with_publisher(_PUBLISHER_EMPTY), cd.LiveCatalog(), "2026.1"
    )
    assert detail.publisher_changes is None
    assert cd.summarize_diff(detail).publisher_changes is None


def test_publisher_changes_survive_a_json_round_trip():
    """The detail is stored as JSON in object storage and read back."""
    detail = cd.compute_catalog_diff(
        _extracted_with_publisher(_PUBLISHER_RAW), cd.LiveCatalog(), "2026.2"
    )
    revived = type(detail).model_validate(detail.model_dump(mode="json"))
    assert revived.publisher_changes.controls.tags["GOV-A1"] == [
        "renumbered",
        "wordsmithed",
    ]
    assert revived.publisher_changes.frameworks.mapping_errata[0].note == (
        "FDE #: 8.10, 8.12"
    )


def test_publisher_changes_are_empty_treats_a_narrative_alone_as_content():
    """A release whose only declaration is its paragraph still published one."""
    assert cd.publisher_changes_are_empty(None) is True
    assert cd.publisher_changes_are_empty({}) is True
    assert cd.publisher_changes_are_empty(_PUBLISHER_EMPTY) is True
    narrative_only = dict(_PUBLISHER_EMPTY, summary="Read this first.")
    assert cd.publisher_changes_are_empty(narrative_only) is False


# ---------------------------------------------------------------------------
# Declared succession end-to-end through compute_catalog_diff / summarize_diff
# ---------------------------------------------------------------------------


def _extracted_2026_3(controls: list, merged: list) -> cd.ExtractedCatalog:
    """A 2026.3-era ExtractedCatalog: crosswalk column AND a READ THIS block."""
    return cd.ExtractedCatalog(
        catalog_version="2026.3",
        controls=controls,
        domains=[{"identifier": "GOV", "order": 1, "name": "Governance"}],
        evidence={},
        assessment_objectives=[],
        framework_names={"fw_a": "Framework A"},
        publisher_changes={
            "summary": "This release renumbers and merges.",
            "controls": {"counts": {"merged": len(merged)}, "merged": merged, "tags": {}},
        },
    )


def test_both_declared_sources_reach_the_full_diff_and_the_summary():
    """One deprecation per source, one reused id, counted in the summary.

    ``renamed`` counts deprecations this run gave a successor; ``id_reused``
    counts changed rows whose key changed hands. They are different populations
    and a row is never in both: the reused id is not deprecated at all.
    """
    live = _live_catalog(
        {
            "GOV-01": _live_control("GOV-01", "Renumbered away"),
            "GOV-04": _live_control("GOV-04", "Merged away"),
            "GOV-07": _live_control("GOV-07", "Original owner of an id"),
        }
    )
    extracted = _extracted_2026_3(
        controls=[
            _wb_control("GOV-02", "Survivor of the renumber", legacy=["GOV-01"]),
            _wb_control("GOV-03", "Survivor of the merge"),
            _wb_control("GOV-07", "A completely different control now"),
        ],
        merged=[
            {
                "legacy_scf_id": "GOV-04",
                "legacy_name": "Merged away",
                "merged_into": "GOV-03",
            },
            {
                "legacy_scf_id": "GOV-07",
                "legacy_name": "Original owner of an id",
                "merged_into": "GOV-03",
            },
        ],
    )
    detail = cd.compute_catalog_diff(extracted, live, "2026.2")
    controls = detail.entities[CatalogEntityType.CONTROLS]

    by_key = {d.key: d for d in controls.deprecated}
    assert by_key["GOV-01"].superseded_source == "workbook_crosswalk"
    assert by_key["GOV-01"].superseded_by == "GOV-02"
    assert by_key["GOV-04"].superseded_source == "publisher_merged"
    assert by_key["GOV-04"].superseded_by == "GOV-03"
    assert all(len(d.suggestions) == 1 for d in controls.deprecated)

    assert [c.key for c in controls.changed] == ["GOV-07"]
    assert controls.changed[0].id_reused.merged_into == "GOV-03"

    counts = cd.summarize_diff(detail).entities[CatalogEntityType.CONTROLS]
    assert counts.deprecated == 2
    assert counts.renamed == 2
    assert counts.changed == 1
    assert counts.id_reused == 1


def test_id_reused_survives_a_json_round_trip():
    """The diff detail is stored as JSON and read back at apply/preview time."""
    live = _live_catalog({"GOV-07": _live_control("GOV-07", "Old meaning")})
    extracted = _extracted_2026_3(
        controls=[_wb_control("GOV-07", "New meaning")],
        merged=[
            {
                "legacy_scf_id": "GOV-07",
                "legacy_name": "Old meaning",
                "merged_into": "GOV-09",
            }
        ],
    )
    detail = cd.compute_catalog_diff(extracted, live, "2026.2")
    revived = DiffDetail.model_validate(json.loads(detail.model_dump_json()))
    reused = revived.entities[CatalogEntityType.CONTROLS].changed[0].id_reused
    assert (reused.merged_into, reused.legacy_name) == ("GOV-09", "Old meaning")


def test_a_stored_diff_without_the_new_fields_still_validates():
    """Diffs staged before this change are read back at revert time."""
    old = {
        "from_version": "2026.1",
        "to_version": "2026.2",
        "entities": {
            "controls": {
                "changed": [{"key": "GOV-01", "fields": {}}],
                "deprecated": [{"key": "GOV-02", "superseded_by": "GOV-01"}],
            }
        },
    }
    detail = DiffDetail.model_validate(old)
    controls = detail.entities[CatalogEntityType.CONTROLS]
    assert controls.changed[0].id_reused is None
    assert controls.deprecated[0].superseded_source is None
    assert cd.summarize_diff(detail).entities[CatalogEntityType.CONTROLS].id_reused == 0
    revived = DiffSummary.model_validate(
        {"from_version": "2026.1", "to_version": "2026.2", "entities": {"controls": {}}}
    )
    assert revived.entities[CatalogEntityType.CONTROLS].id_reused == 0


# ---------------------------------------------------------------------------
# The real 2026.2 -> 2026.3 release — skipped where the workbooks are absent
# ---------------------------------------------------------------------------

_CATALOG_SOURCE = Path(__file__).resolve().parents[2] / "catalog-source"
_WORKBOOK_2026_2 = _CATALOG_SOURCE / "scf-2026-2.xlsx"
_WORKBOOK_2026_3 = _CATALOG_SOURCE / "scf-2026-3.xlsx"
_WORKBOOKS_MISSING = (
    f"{_WORKBOOK_2026_2} / {_WORKBOOK_2026_3} not present. The real SCF "
    "workbooks are gitignored (.gitignore: catalog-source/*.xlsx), so this "
    "assertion cannot run in a fresh clone or in CI; the synthetic fixtures "
    "above pin every rule it exercises."
)


@lru_cache(maxsize=1)
def _real_controls_diff():
    """2026.2 -> 2026.3 controls diff, 2026.2 standing in for the live catalog.

    Cached: extracting both workbooks costs about six seconds and all three
    assertions below read the same diff.

    A live DB snapshot is not available to a unit test, and the previous release
    IS the live catalog on a platform that has applied every release: 1,534
    active controls, which is what dev held when these numbers were measured.
    """
    old = cd.extract_workbook(_WORKBOOK_2026_2)
    new = cd.extract_workbook(_WORKBOOK_2026_3)
    old_rows = cd._workbook_rows(old)[CatalogEntityType.CONTROLS]
    new_rows = cd._workbook_rows(new)[CatalogEntityType.CONTROLS]
    live = {
        key: cd.LiveEntityRow(
            key=key,
            status="active",
            fields={f: cols.get(f) for f in cd.CONTROL_COMPARED_FIELDS},
            name=cols.get("control_name"),
        )
        for key, cols in old_rows.items()
    }
    diff = cd.compute_entity_diff(
        new_rows,
        live,
        cd.CONTROL_COMPARED_FIELDS,
        name_field="control_name",
        legacy_crosswalk=cd.build_legacy_crosswalk(new),
        publisher_merges=cd.build_publisher_merges(new),
    )
    return new, new_rows, live, diff


@pytest.mark.skipif(
    not (_WORKBOOK_2026_2.exists() and _WORKBOOK_2026_3.exists()),
    reason=_WORKBOOKS_MISSING,
)
def test_real_2026_3_declares_a_successor_for_every_deprecation():
    """801 of 801, and not one generated candidate among them.

    These are the numbers the change was built against. The old behaviour
    produced the same 801 declarations plus 612 similarity candidates spread
    over 377 rows; the assertion that the suggestion list is exactly one entry
    long per row is what pins the removal on real data rather than a fixture.
    """
    _, _, live, diff = _real_controls_diff()
    assert len(live) == 1534

    assert len(diff.deprecated) == 801
    assert sum(1 for d in diff.deprecated if d.superseded_by) == 801
    assert all(d.superseded_source == "workbook_crosswalk" for d in diff.deprecated)
    assert all(len(d.suggestions) == 1 for d in diff.deprecated)
    assert all(s.score == 1.0 for d in diff.deprecated for s in d.suggestions)
    assert {tuple(s.signals) for d in diff.deprecated for s in d.suggestions} == {
        ("workbook_crosswalk",)
    }
    # Every one of the 801 is the crosswalk's doing, so the merge list adds no
    # deprecation of its own on THIS release. It is not dead code: nine of the
    # 23 merged controls left the workbook and the crosswalk happens to name
    # them too, which is the agreement documented on the source constants.
    assert not [d for d in diff.deprecated if d.superseded_source == "publisher_merged"]


@pytest.mark.skipif(
    not (_WORKBOOK_2026_2.exists() and _WORKBOOK_2026_3.exists()),
    reason=_WORKBOOKS_MISSING,
)
def test_real_2026_3_flags_fourteen_reused_ids():
    """14 of the 23 merged-away ids were handed to a different control."""
    new, new_rows, _, diff = _real_controls_diff()

    merged = new.publisher_changes["controls"]["merged"]
    assert len(merged) == 23
    survivors = {m["legacy_scf_id"] for m in merged if m["legacy_scf_id"] in new_rows}
    assert len(survivors) == 14

    flagged = {c.key: c.id_reused for c in diff.changed if c.id_reused is not None}
    assert set(flagged) == survivors
    assert len(flagged) == 14
    # No reused id slipped into `unchanged`, where there is no row to carry the
    # flag. If a future release does that, this is where it surfaces.
    assert not survivors & set(diff.unchanged)

    # And the count the console's summary card reads.
    summary = cd.summarize_diff(
        DiffDetail(
            from_version="2026.2",
            to_version="2026.3",
            entities={CatalogEntityType.CONTROLS: diff},
        )
    )
    assert summary.entities[CatalogEntityType.CONTROLS].id_reused == 14
    assert summary.entities[CatalogEntityType.CONTROLS].renamed == 801


@pytest.mark.skipif(
    not (_WORKBOOK_2026_2.exists() and _WORKBOOK_2026_3.exists()),
    reason=_WORKBOOKS_MISSING,
)
def test_real_2026_3_pairs_eight_deprecations_into_reused_ids():
    """The eight rows where both facts are true at once, and both are correct.

    A reused id is still a live control with a name and mappings, so a retiring
    control renumbering into it is a valid pairing. Eight of the 801 do exactly
    that. If the reuse flag were ever allowed to veto a pairing, these eight
    orgs' controls would silently retire instead of migrating.
    """
    _, _, _, diff = _real_controls_diff()
    reused = {c.key for c in diff.changed if c.id_reused is not None}
    paired_into_reused = {
        d.key: d.superseded_by for d in diff.deprecated if d.superseded_by in reused
    }
    assert len(paired_into_reused) == 8
    assert all(
        d.superseded_source == "workbook_crosswalk"
        for d in diff.deprecated
        if d.key in paired_into_reused
    )
