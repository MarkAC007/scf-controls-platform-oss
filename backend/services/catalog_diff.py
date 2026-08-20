"""Catalog diff engine for the SCF catalog upgrade feature (WP1a, plan §4.2.2-3).

Stages a newly uploaded SCF workbook against the live catalog:

1. Extracts the workbook via ``scripts/extract_scf_data.py`` into a per-run
   temp directory (``tempfile.mkdtemp`` — never the shared ``DATA_DIR``).
2. Runs the sanity gates (any failure ⇒ the caller marks the run ``blocked``).
3. Enforces the version guard (refuse downgrade/same-version unless the caller
   passes ``force``, which is surfaced on the result so it can be recorded).
4. Computes the per-entity live-DB diff in the frozen contract shapes
   (``DiffDetail`` / ``DiffSummary`` / ``SanityReport`` from
   ``schemas_catalog_upgrade`` — imported, never redefined).

Diff semantics per entity key:
- ``added``        — in the workbook, not in the live catalog.
- ``changed``      — in both, live row active, field-level differences
                     (old AND new stored: the diff is the platform revert
                     anchor, plan §4.1 M4).
- ``deprecated``   — active in the live catalog, absent from the workbook.
- ``resurrected``  — deprecated in the live catalog, present in the workbook
                     (may carry field changes).
- ``unchanged``    — keys only; includes rows already deprecated in the live
                     catalog and still absent from the workbook (no change).

Compared fields are exactly the catalog model columns the seeder writes
(mirroring ``catalog_seeder.py``), excluding by construction:
``required_artifact_types`` (+``_extracted_at``), ``created_at``/``updated_at``,
``catalog_version``, and the lifecycle columns ``status`` /
``retired_in_version`` / ``superseded_by``.

Entity coverage notes (contract ``CatalogEntityType``):
- ``capability_themes`` is emitted as an empty diff: themes are not
  workbook-sourced and are re-derived wholesale at apply (plan §4.1 M2, §4.2.4).
- ``framework_mappings`` is an informational per-control view: ``changed``
  rows break a control's mapping-set change down per framework slug, and
  ``unchanged`` lists in-both controls with identical mappings. The REVERT
  AUTHORITY for the ``framework_mappings`` column stays with the ``controls``
  entity, whose changed-field set includes ``framework_mappings``; apply/revert
  (WP1b) must consume the controls entity only.

The superseded_by suggestion scorer (controls only, plan §4.2.3): candidates
share the deprecated control's domain prefix, name similarity >= 0.6
(``difflib``), top 3, display-only — the admin pairs manually.
"""
from __future__ import annotations

import difflib
import json
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from catalog_models import (
    SCFCatalogAssessmentObjective,
    SCFCatalogControl,
    SCFCatalogDomain,
    SCFCatalogEvidence,
)
from schemas_catalog_upgrade import (
    AddedEntity,
    CatalogEntityType,
    ChangedEntity,
    DeprecatedEntity,
    DiffDetail,
    DiffSummary,
    EntityDiff,
    EntityDiffCounts,
    FieldChange,
    ResurrectedEntity,
    SanityCheck,
    SanityReport,
    SupersededSuggestion,
)

# ---------------------------------------------------------------------------
# Version guard (plan §4.2.2)
# ---------------------------------------------------------------------------

VERSION_RE = re.compile(r"^(\d{4})\.(\d+)$")

# Sanity-gate threshold: an unclassified control-count drop beyond this
# fraction of the live active catalog blocks the run (plan §4.2.2).
CONTROL_COUNT_DROP_THRESHOLD = 0.05

SUGGESTION_SIMILARITY_THRESHOLD = 0.6
SUGGESTION_TOP_N = 3


class CatalogDiffError(Exception):
    """Base error for the catalog diff engine."""


class VersionGuardError(CatalogDiffError):
    """Upgrade refused by the version guard.

    ``code`` is one of ``unparseable``, ``same_version``, ``downgrade``.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def parse_version(version: Optional[str]) -> Optional[tuple]:
    """Parse an SCF catalog version like ``2026.2`` into ``(2026, 2)``."""
    if not version:
        return None
    match = VERSION_RE.match(str(version).strip())
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)))


def guard_version(from_version: str, to_version: str, force: bool = False) -> None:
    """Refuse downgrade / same-version staging unless ``force`` (plan §4.2.2).

    Unparseable versions always raise — ``force`` cannot bypass a version we
    cannot compare (the ``version_parseable`` sanity gate blocks first anyway).
    """
    parsed_to = parse_version(to_version)
    if parsed_to is None:
        raise VersionGuardError(
            "unparseable", f"workbook catalog version {to_version!r} is not parseable"
        )
    parsed_from = parse_version(from_version)
    if parsed_from is None:
        raise VersionGuardError(
            "unparseable", f"current catalog version {from_version!r} is not parseable"
        )
    if parsed_to == parsed_from and not force:
        raise VersionGuardError(
            "same_version",
            f"workbook version {to_version} equals the current catalog version",
        )
    if parsed_to < parsed_from and not force:
        raise VersionGuardError(
            "downgrade",
            f"workbook version {to_version} is older than the current "
            f"catalog version {from_version}",
        )


# ---------------------------------------------------------------------------
# Workbook extraction (per-run temp dir — never the shared DATA_DIR)
# ---------------------------------------------------------------------------

# The extractor ships at /app/scripts in the backend image (Dockerfile.backend)
# and at <repo>/scripts in a source checkout.
_EXTRACTOR_DIR_CANDIDATES = (
    "/app/scripts",
    str(Path(__file__).resolve().parents[2] / "scripts"),
)


def _load_extractor():
    try:
        import extract_scf_data
    except ImportError:
        for candidate in _EXTRACTOR_DIR_CANDIDATES:
            if candidate not in sys.path and Path(candidate).is_dir():
                sys.path.insert(0, candidate)
        import extract_scf_data
    return extract_scf_data


@dataclass
class ExtractedCatalog:
    """In-memory image of one workbook extraction."""

    catalog_version: str
    controls: List[dict]  # raw extractor dicts (control_guidance.json shape)
    domains: List[dict]
    evidence: Dict[str, dict]  # keyed by evidence_id (erl.json shape)
    assessment_objectives: List[dict]
    framework_names: Dict[str, str]
    meta: dict = field(default_factory=dict)


def extract_workbook(workbook_path) -> ExtractedCatalog:
    """Extract an SCF workbook into a fresh temp dir and load the JSON output.

    The temp dir is private to this run and removed before returning; the
    shared seeder ``DATA_DIR`` is never touched (plan §4.2.2).
    Raises ``ValueError`` (from the extractor) for unrecognisable workbooks.
    """
    extractor = _load_extractor()
    tmp_dir = tempfile.mkdtemp(prefix="catalog-upgrade-")
    try:
        meta = extractor.extract_to_dir(workbook_path, tmp_dir)
        tmp = Path(tmp_dir)
        with open(tmp / "control_guidance.json") as f:
            controls = json.load(f).get("controls", [])
        with open(tmp / "domains.json") as f:
            domains = json.load(f)
        with open(tmp / "erl.json") as f:
            evidence = json.load(f)
        with open(tmp / "assessment_objectives.json") as f:
            assessment_objectives = json.load(f).get("objectives", [])
        with open(tmp / "frameworks.json") as f:
            framework_names = json.load(f)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return ExtractedCatalog(
        catalog_version=str(meta.get("catalog_version", "")),
        controls=controls,
        domains=domains,
        evidence=evidence,
        assessment_objectives=assessment_objectives,
        framework_names=framework_names,
        meta=meta,
    )


# ---------------------------------------------------------------------------
# Workbook row -> model-column dicts (mirrors catalog_seeder.py exactly)
# ---------------------------------------------------------------------------

CONTROL_COMPARED_FIELDS = (
    "scf_domain",
    "control_name",
    "control_description",
    "control_question",
    "validation_cadence",
    "control_weighting",
    "nist_csf_function",
    "pptdf_people",
    "pptdf_process",
    "pptdf_technology",
    "pptdf_data",
    "pptdf_facility",
    "evidence_requests",
    "framework_mappings",
    "cmm_level_0",
    "cmm_level_1",
    "cmm_level_2",
    "cmm_level_3",
    "cmm_level_4",
    "cmm_level_5",
    "biz_micro_small",
    "biz_small",
    "biz_medium",
    "biz_large",
    "biz_enterprise",
    "scrm_tier1_strategic",
    "scrm_tier2_operational",
    "scrm_tier3_tactical",
    "risk_codes",
    "threat_codes",
)

DOMAIN_COMPARED_FIELDS = ("order", "name", "principle", "principle_intent")

EVIDENCE_COMPARED_FIELDS = (
    "area_of_focus",
    "artifact_title",
    "artifact_description",
    "control_mappings",
)

AO_COMPARED_FIELDS = (
    "scf_id",
    "objective_text",
    "pptdf_people",
    "pptdf_process",
    "pptdf_technology",
    "pptdf_data",
    "pptdf_facility",
    "ao_origins",
    "notes",
    "assessment_rigor",
    "scf_defined_parameters",
    "org_defined_parameters",
    "cmmc_level1_ao",
    "dhs_ztcf_ao",
    "nist_800_53a",
    "nist_800_171a",
    "nist_800_171a_r3",
    "nist_800_172a",
    "asset_type",
    "assessment_procedure",
    "expected_results",
)


def control_to_columns(ctrl: dict) -> dict:
    pptdf = ctrl.get("pptdf_applicability") or {}
    cmm = ctrl.get("cmm_maturity") or {}
    biz = ctrl.get("business_size_guidance") or {}
    scrm = ctrl.get("scrm_focus") or {}
    risk_threat = ctrl.get("risk_threat_mapping") or {}
    return {
        "scf_domain": ctrl.get("scf_domain"),
        "control_name": ctrl.get("control_name"),
        "control_description": ctrl.get("control_description"),
        "control_question": ctrl.get("control_question"),
        "validation_cadence": ctrl.get("validation_cadence"),
        "control_weighting": ctrl.get("control_weighting"),
        "nist_csf_function": ctrl.get("nist_csf_function"),
        "pptdf_people": pptdf.get("people", False),
        "pptdf_process": pptdf.get("process", False),
        "pptdf_technology": pptdf.get("technology", False),
        "pptdf_data": pptdf.get("data", False),
        "pptdf_facility": pptdf.get("facility", False),
        "evidence_requests": ctrl.get("evidence_requests", []),
        "framework_mappings": ctrl.get("framework_mappings", {}),
        "cmm_level_0": cmm.get("level_0"),
        "cmm_level_1": cmm.get("level_1"),
        "cmm_level_2": cmm.get("level_2"),
        "cmm_level_3": cmm.get("level_3"),
        "cmm_level_4": cmm.get("level_4"),
        "cmm_level_5": cmm.get("level_5"),
        "biz_micro_small": biz.get("micro_small"),
        "biz_small": biz.get("small"),
        "biz_medium": biz.get("medium"),
        "biz_large": biz.get("large"),
        "biz_enterprise": biz.get("enterprise"),
        "scrm_tier1_strategic": scrm.get("tier1_strategic", False),
        "scrm_tier2_operational": scrm.get("tier2_operational", False),
        "scrm_tier3_tactical": scrm.get("tier3_tactical", False),
        "risk_codes": risk_threat.get("risk_codes", []),
        "threat_codes": risk_threat.get("threat_codes", []),
    }


def domain_to_columns(domain: dict) -> dict:
    return {
        "order": domain.get("order"),
        "name": domain.get("name"),
        "principle": domain.get("principle"),
        "principle_intent": domain.get("principle_intent"),
    }


def evidence_to_columns(evidence: dict) -> dict:
    return {
        "area_of_focus": evidence.get("area_of_focus"),
        "artifact_title": evidence.get("artifact_title"),
        "artifact_description": evidence.get("artifact_description"),
        "control_mappings": evidence.get("control_mappings", []),
    }


def ao_to_columns(ao: dict) -> dict:
    pptdf = ao.get("pptdf_applicability") or {}
    return {
        "scf_id": ao.get("scf_id"),
        "objective_text": ao.get("objective_text"),
        "pptdf_people": pptdf.get("people", False),
        "pptdf_process": pptdf.get("process", False),
        "pptdf_technology": pptdf.get("technology", False),
        "pptdf_data": pptdf.get("data", False),
        "pptdf_facility": pptdf.get("facility", False),
        "ao_origins": ao.get("ao_origins"),
        "notes": ao.get("notes"),
        "assessment_rigor": ao.get("assessment_rigor"),
        "scf_defined_parameters": ao.get("scf_defined_parameters"),
        "org_defined_parameters": ao.get("org_defined_parameters"),
        "cmmc_level1_ao": ao.get("cmmc_level1_ao"),
        "dhs_ztcf_ao": ao.get("dhs_ztcf_ao"),
        "nist_800_53a": ao.get("nist_800_53a"),
        "nist_800_171a": ao.get("nist_800_171a"),
        "nist_800_171a_r3": ao.get("nist_800_171a_r3"),
        "nist_800_172a": ao.get("nist_800_172a"),
        "asset_type": ao.get("asset_type"),
        "assessment_procedure": ao.get("assessment_procedure"),
        "expected_results": ao.get("expected_results"),
    }


def _workbook_rows(extracted: ExtractedCatalog) -> Dict[CatalogEntityType, Dict[str, dict]]:
    """Key -> model-column dict per diffable entity."""
    controls: Dict[str, dict] = {}
    for ctrl in extracted.controls:
        key = str(ctrl.get("scf_id") or "").strip()
        if key:
            controls[key] = control_to_columns(ctrl)

    domains: Dict[str, dict] = {}
    for domain in extracted.domains:
        key = str(domain.get("identifier") or "").strip()
        if key:
            domains[key] = domain_to_columns(domain)

    evidence: Dict[str, dict] = {}
    for evidence_id, item in extracted.evidence.items():
        key = str(item.get("evidence_id") or evidence_id).strip()
        if key:
            evidence[key] = evidence_to_columns(item)

    objectives: Dict[str, dict] = {}
    for ao in extracted.assessment_objectives:
        key = str(ao.get("ao_id") or "").strip()
        if key:
            objectives[key] = ao_to_columns(ao)

    return {
        CatalogEntityType.CONTROLS: controls,
        CatalogEntityType.DOMAINS: domains,
        CatalogEntityType.EVIDENCE: evidence,
        CatalogEntityType.ASSESSMENT_OBJECTIVES: objectives,
    }


# ---------------------------------------------------------------------------
# Live catalog loading
# ---------------------------------------------------------------------------


@dataclass
class LiveEntityRow:
    """One live catalog row reduced to key, lifecycle status, and compared fields."""

    key: str
    status: str
    fields: Dict[str, Any]
    name: Optional[str] = None
    superseded_by: Optional[str] = None


@dataclass
class LiveCatalog:
    controls: Dict[str, LiveEntityRow] = field(default_factory=dict)
    domains: Dict[str, LiveEntityRow] = field(default_factory=dict)
    evidence: Dict[str, LiveEntityRow] = field(default_factory=dict)
    assessment_objectives: Dict[str, LiveEntityRow] = field(default_factory=dict)

    @property
    def active_control_count(self) -> int:
        return sum(1 for row in self.controls.values() if row.status == "active")

    def by_entity(self) -> Dict[CatalogEntityType, Dict[str, LiveEntityRow]]:
        return {
            CatalogEntityType.CONTROLS: self.controls,
            CatalogEntityType.DOMAINS: self.domains,
            CatalogEntityType.EVIDENCE: self.evidence,
            CatalogEntityType.ASSESSMENT_OBJECTIVES: self.assessment_objectives,
        }


def _live_row(orm_row, key_attr: str, compared: tuple, name_attr: Optional[str]) -> LiveEntityRow:
    return LiveEntityRow(
        key=getattr(orm_row, key_attr),
        status=getattr(orm_row, "status", None) or "active",
        fields={f: getattr(orm_row, f) for f in compared},
        name=getattr(orm_row, name_attr) if name_attr else None,
        superseded_by=getattr(orm_row, "superseded_by", None),
    )


async def load_live_catalog(session: AsyncSession) -> LiveCatalog:
    """Load the four catalog entity tables into plain diffable rows."""
    live = LiveCatalog()

    result = await session.execute(select(SCFCatalogControl))
    for row in result.scalars().all():
        live.controls[row.scf_id] = _live_row(
            row, "scf_id", CONTROL_COMPARED_FIELDS, "control_name"
        )

    result = await session.execute(select(SCFCatalogDomain))
    for row in result.scalars().all():
        live.domains[row.identifier] = _live_row(
            row, "identifier", DOMAIN_COMPARED_FIELDS, "name"
        )

    result = await session.execute(select(SCFCatalogEvidence))
    for row in result.scalars().all():
        live.evidence[row.evidence_id] = _live_row(
            row, "evidence_id", EVIDENCE_COMPARED_FIELDS, "artifact_title"
        )

    result = await session.execute(select(SCFCatalogAssessmentObjective))
    for row in result.scalars().all():
        live.assessment_objectives[row.ao_id] = _live_row(
            row, "ao_id", AO_COMPARED_FIELDS, None
        )

    return live


# ---------------------------------------------------------------------------
# Sanity gates (plan §4.2.2 — any failure ⇒ run 'blocked')
# ---------------------------------------------------------------------------


def run_sanity_checks(extracted: ExtractedCatalog, live: LiveCatalog) -> SanityReport:
    checks: List[SanityCheck] = []

    version_ok = parse_version(extracted.catalog_version) is not None
    checks.append(
        SanityCheck(
            check="version_parseable",
            passed=version_ok,
            detail=(
                f"workbook catalog version: {extracted.catalog_version!r}"
                if version_ok
                else f"unparseable workbook catalog version: {extracted.catalog_version!r}"
            ),
        )
    )

    live_active = live.active_control_count
    workbook_count = len(extracted.controls)
    if live_active > 0:
        drop = (live_active - workbook_count) / live_active
        drop_ok = drop <= CONTROL_COUNT_DROP_THRESHOLD
        drop_detail = (
            f"live active controls: {live_active}, workbook controls: "
            f"{workbook_count} ({drop:+.1%} drop)"
        )
    else:
        # Empty live catalog: nothing to compare a drop against.
        drop_ok = True
        drop_detail = f"live catalog empty; workbook controls: {workbook_count}"
    checks.append(
        SanityCheck(check="control_count_drop", passed=drop_ok, detail=drop_detail)
    )

    entity_counts = {
        "controls": len(extracted.controls),
        "domains": len(extracted.domains),
        "evidence": len(extracted.evidence),
        "assessment_objectives": len(extracted.assessment_objectives),
    }
    empty_entities = sorted(name for name, count in entity_counts.items() if count == 0)
    checks.append(
        SanityCheck(
            check="zero_rows",
            passed=not empty_entities,
            detail=(
                f"entities with zero extracted rows: {', '.join(empty_entities)}"
                if empty_entities
                else f"row counts: {entity_counts}"
            ),
        )
    )

    fw_count = len(extracted.framework_names)
    checks.append(
        SanityCheck(
            check="framework_names",
            passed=fw_count > 0,
            detail=(
                f"extracted {fw_count} framework display names"
                if fw_count
                else "framework-name extraction produced no entries"
            ),
        )
    )

    return SanityReport(passed=all(c.passed for c in checks), checks=checks)


# ---------------------------------------------------------------------------
# Field comparison
# ---------------------------------------------------------------------------


def _norm(value: Any) -> Any:
    """Normalise a field value for change detection.

    Empty strings equal None (the extractor emits '' where the seeder stores
    NULL), and list order is ignored for the reference-list columns (evidence
    refs, risk/threat codes, framework refs) so re-ordered exports do not
    register as changes. Stored FieldChange values stay raw.
    """
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else None
    if isinstance(value, (list, tuple)):
        return tuple(sorted(str(_norm(v)) for v in value if _norm(v) is not None))
    if isinstance(value, dict):
        return tuple(sorted((str(k), _norm(v)) for k, v in value.items()))
    return value


def _field_changes(
    live_fields: Dict[str, Any], workbook_fields: Dict[str, Any], compared: tuple
) -> Dict[str, FieldChange]:
    changes: Dict[str, FieldChange] = {}
    for name in compared:
        old = live_fields.get(name)
        new = workbook_fields.get(name)
        if _norm(old) != _norm(new):
            changes[name] = FieldChange(old=old, new=new)
    return changes


# ---------------------------------------------------------------------------
# superseded_by suggestion scorer (controls only, plan §4.2.3 — display-only)
# ---------------------------------------------------------------------------


def _domain_prefix(scf_id: str) -> str:
    return scf_id.split("-", 1)[0]


def suggest_successors(
    deprecated_key: str,
    deprecated_name: Optional[str],
    candidates: Dict[str, Optional[str]],
    *,
    threshold: float = SUGGESTION_SIMILARITY_THRESHOLD,
    top_n: int = SUGGESTION_TOP_N,
) -> List[SupersededSuggestion]:
    """Rank successor candidates for a planned deprecation.

    Candidates (key -> name) must share the deprecated control's domain prefix
    and reach name similarity >= ``threshold``; top ``top_n`` by score.
    """
    if not deprecated_name:
        return []
    prefix = _domain_prefix(deprecated_key)
    target = deprecated_name.strip().lower()
    scored = []
    for key, name in candidates.items():
        if key == deprecated_key or _domain_prefix(key) != prefix or not name:
            continue
        score = difflib.SequenceMatcher(None, target, name.strip().lower()).ratio()
        if score >= threshold:
            scored.append(SupersededSuggestion(scf_id=key, name=name, score=round(score, 4)))
    scored.sort(key=lambda s: (-s.score, s.scf_id))
    return scored[:top_n]


# ---------------------------------------------------------------------------
# Per-entity diff
# ---------------------------------------------------------------------------


def compute_entity_diff(
    workbook_rows: Dict[str, dict],
    live_rows: Dict[str, LiveEntityRow],
    compared: tuple,
    name_field: Optional[str] = None,
    suggestion_candidates: Optional[Dict[str, Optional[str]]] = None,
) -> EntityDiff:
    """Classify one entity's keys into the five change classes.

    ``suggestion_candidates`` (controls only) enables the superseded_by scorer
    on deprecated rows.
    """
    diff = EntityDiff()

    for key in sorted(workbook_rows):
        wb_fields = workbook_rows[key]
        name = wb_fields.get(name_field) if name_field else None
        live = live_rows.get(key)
        if live is None:
            diff.added.append(AddedEntity(key=key, name=name, data=wb_fields))
            continue
        changes = _field_changes(live.fields, wb_fields, compared)
        if live.status == "deprecated":
            diff.resurrected.append(
                ResurrectedEntity(key=key, name=name or live.name, fields=changes)
            )
        elif changes:
            diff.changed.append(
                ChangedEntity(key=key, name=name or live.name, fields=changes)
            )
        else:
            diff.unchanged.append(key)

    for key in sorted(live_rows):
        if key in workbook_rows:
            continue
        live = live_rows[key]
        if live.status == "deprecated":
            # Already deprecated and still absent: nothing changes.
            diff.unchanged.append(key)
            continue
        suggestions = (
            suggest_successors(key, live.name, suggestion_candidates)
            if suggestion_candidates is not None
            else []
        )
        diff.deprecated.append(
            DeprecatedEntity(
                key=key,
                name=live.name,
                superseded_by=live.superseded_by,
                suggestions=suggestions,
            )
        )

    return diff


def compute_framework_mappings_diff(
    workbook_controls: Dict[str, dict], live_controls: Dict[str, LiveEntityRow]
) -> EntityDiff:
    """Informational per-control framework-mapping view (see module docstring).

    Only controls present in both catalogs are reported: ``changed`` rows carry
    one FieldChange per framework slug whose reference list differs;
    ``unchanged`` lists in-both controls with identical mapping sets. The
    controls entity remains the revert authority for the column.
    """
    diff = EntityDiff()
    for key in sorted(workbook_controls):
        live = live_controls.get(key)
        if live is None:
            continue
        old_map = live.fields.get("framework_mappings") or {}
        new_map = workbook_controls[key].get("framework_mappings") or {}
        changes: Dict[str, FieldChange] = {}
        for slug in sorted(set(old_map) | set(new_map)):
            old_refs = old_map.get(slug)
            new_refs = new_map.get(slug)
            if _norm(old_refs) != _norm(new_refs):
                changes[slug] = FieldChange(old=old_refs, new=new_refs)
        if changes:
            diff.changed.append(
                ChangedEntity(
                    key=key,
                    name=workbook_controls[key].get("control_name") or live.name,
                    fields=changes,
                )
            )
        else:
            diff.unchanged.append(key)
    return diff


def compute_catalog_diff(
    extracted: ExtractedCatalog, live: LiveCatalog, from_version: str
) -> DiffDetail:
    """Full live-DB diff in the frozen ``DiffDetail`` contract shape."""
    workbook = _workbook_rows(extracted)
    live_by_entity = live.by_entity()

    workbook_controls = workbook[CatalogEntityType.CONTROLS]
    suggestion_candidates = {
        key: fields.get("control_name") for key, fields in workbook_controls.items()
    }

    compared_by_entity = {
        CatalogEntityType.CONTROLS: CONTROL_COMPARED_FIELDS,
        CatalogEntityType.DOMAINS: DOMAIN_COMPARED_FIELDS,
        CatalogEntityType.EVIDENCE: EVIDENCE_COMPARED_FIELDS,
        CatalogEntityType.ASSESSMENT_OBJECTIVES: AO_COMPARED_FIELDS,
    }
    name_field_by_entity = {
        CatalogEntityType.CONTROLS: "control_name",
        CatalogEntityType.DOMAINS: "name",
        CatalogEntityType.EVIDENCE: "artifact_title",
        CatalogEntityType.ASSESSMENT_OBJECTIVES: None,
    }

    entities: Dict[CatalogEntityType, EntityDiff] = {}
    for entity_type, compared in compared_by_entity.items():
        entities[entity_type] = compute_entity_diff(
            workbook[entity_type],
            live_by_entity[entity_type],
            compared,
            name_field=name_field_by_entity[entity_type],
            suggestion_candidates=(
                suggestion_candidates
                if entity_type is CatalogEntityType.CONTROLS
                else None
            ),
        )

    entities[CatalogEntityType.FRAMEWORK_MAPPINGS] = compute_framework_mappings_diff(
        workbook_controls, live.controls
    )
    # Themes are not workbook-sourced; re-derived wholesale at apply (§4.1 M2).
    entities[CatalogEntityType.CAPABILITY_THEMES] = EntityDiff()

    return DiffDetail(
        from_version=from_version,
        to_version=extracted.catalog_version,
        entities=entities,
    )


def summarize_diff(detail: DiffDetail) -> DiffSummary:
    """Count-only mirror of a ``DiffDetail`` (the ``diff_summary`` JSONB shape)."""
    return DiffSummary(
        from_version=detail.from_version,
        to_version=detail.to_version,
        entities={
            entity_type: EntityDiffCounts(
                added=len(diff.added),
                changed=len(diff.changed),
                deprecated=len(diff.deprecated),
                resurrected=len(diff.resurrected),
                unchanged=len(diff.unchanged),
            )
            for entity_type, diff in detail.entities.items()
        },
    )


# ---------------------------------------------------------------------------
# Staging entry point (plan §4.2 step 2)
# ---------------------------------------------------------------------------


@dataclass
class StagedDiff:
    """Result of staging one workbook against the live catalog.

    When ``sanity_report.passed`` is False the run must be marked ``blocked``
    and ``diff_detail``/``diff_summary`` are None. ``forced`` echoes the
    caller's force flag so a forced same-version/downgrade stage is recorded
    on the run (plan §4.2.2).
    """

    to_version: str
    sanity_report: SanityReport
    diff_detail: Optional[DiffDetail] = None
    diff_summary: Optional[DiffSummary] = None
    forced: bool = False


async def stage_catalog_diff(
    session: AsyncSession,
    workbook_path,
    from_version: str,
    *,
    force: bool = False,
) -> StagedDiff:
    """Extract, sanity-check, version-guard, and diff a workbook.

    Raises ``ValueError`` for an unrecognisable workbook (extractor) and
    ``VersionGuardError`` for a refused same-version/downgrade stage.
    """
    extracted = extract_workbook(workbook_path)
    live = await load_live_catalog(session)

    sanity = run_sanity_checks(extracted, live)
    if not sanity.passed:
        return StagedDiff(
            to_version=extracted.catalog_version,
            sanity_report=sanity,
            forced=force,
        )

    guard_version(from_version, extracted.catalog_version, force=force)

    detail = compute_catalog_diff(extracted, live, from_version)
    return StagedDiff(
        to_version=extracted.catalog_version,
        sanity_report=sanity,
        diff_detail=detail,
        diff_summary=summarize_diff(detail),
        forced=force,
    )
