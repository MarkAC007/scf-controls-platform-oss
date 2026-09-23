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

Control succession (plan §4.2.3) is DECLARED, never guessed. A deprecated
control's successor comes from the workbook and nowhere else: the ``Legacy
SCF #`` crosswalk first, the READ THIS sheet's merge list second. There is no
name-similarity scorer any more. It produced 1-2 extra candidates on 377 of the
801 deprecations in 2026.2->2026.3 and decided nothing, while making a
publisher declaration and a string-distance guess look like the same kind of
claim in the same list — which is exactly the confusion an operator signing off
a four-figure renumbering cannot afford. Frameworks still derive their
succession (see ``framework_succession``), because no publisher crosswalk
exists for focal documents.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from catalog_models import (
    CatalogFrameworkRegistry,
    SCFCatalogAssessmentObjective,
    SCFCatalogControl,
    SCFCatalogDomain,
    SCFCatalogEvidence,
)
from services.framework_succession import (
    TIER_DECLARED,
    TIER_DECLARED_STEM,
    match_framework_successions,
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
    IdReuse,
    PublisherChanges,
    PublisherChangesSummary,
    ResurrectedEntity,
    SanityCheck,
    SanityReport,
    SupersededSuggestion,
)

if TYPE_CHECKING:  # pragma: no cover - import-cycle-free type reference
    from services.framework_registry import LiveRegistryStatus

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Version guard (plan §4.2.2)
# ---------------------------------------------------------------------------

VERSION_RE = re.compile(r"^(\d{4})\.(\d+)$")

# Sanity-gate threshold: an unclassified control-count drop beyond this
# fraction of the live active catalog blocks the run (plan §4.2.2).
CONTROL_COUNT_DROP_THRESHOLD = 0.05

# Churn gate: a live control leaving the workbook is "explained" when the
# workbook's Legacy SCF # crosswalk names its successor. Unexplained
# retirements beyond this fraction of the live active catalog block the run.
# The absolute floor keeps small catalogs and fixtures out of scope - the
# failure this gate exists for is a four-figure mass retirement, not a
# handful of genuine ones.
CONTROL_CHURN_UNEXPLAINED_THRESHOLD = 0.05
CONTROL_CHURN_MIN_ROWS = 50

# Framework churn. The registry is an order of magnitude smaller than the
# control set (254 in 2026.2 against 1534 controls), so the control floor of 50
# rows would swallow the entire framework population and never fire. The floor
# here is the number of unexplained removals below which churn is treated as
# ordinary editorial tidying rather than a mass retirement.
FRAMEWORK_CHURN_UNEXPLAINED_THRESHOLD = 0.05
# Deliberately 10, not control_churn's 50. The floor exists so a small
# catalogue does not block on one or two retirements, where a single row in
# twenty is already over the ratio. Ten against SCF's ~250 frameworks forgives
# at most 9 removals = 3.6%, which the 5% ratio would have passed anyway - so
# on a full catalogue the floor never decides anything and cannot be used to
# walk a large unexplained churn past this check. Raising it would break that.
FRAMEWORK_CHURN_MIN_ROWS = 10


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
    # id -> {"name", "focal_document_id", "geography"}. The focal-document id is
    # the publisher's stable identity for a framework and is what makes
    # succession a DECLARED fact rather than a guess. Absent before SCF 2026.1.
    framework_registry: Dict[str, dict] = field(default_factory=dict)
    # What the publisher SAYS it changed, from the 2026.3+ change sheets
    # (``extract_scf_data.extract_publisher_changes``). Empty for every earlier
    # release, which is a fact about the release and not a missing extraction.
    publisher_changes: Dict[str, Any] = field(default_factory=dict)
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
        registry_path = tmp / "framework_registry.json"
        framework_registry = {}
        if registry_path.exists():
            with open(registry_path) as f:
                framework_registry = json.load(f)
        # Optional twice over: absent from a workbook that predates the change
        # sheets, and absent from an extraction that predates this file.
        publisher_path = tmp / "publisher_changes.json"
        publisher_changes = {}
        if publisher_path.exists():
            with open(publisher_path) as f:
                publisher_changes = json.load(f) or {}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return ExtractedCatalog(
        catalog_version=str(meta.get("catalog_version", "")),
        controls=controls,
        domains=domains,
        evidence=evidence,
        assessment_objectives=assessment_objectives,
        framework_names=framework_names,
        framework_registry=framework_registry,
        publisher_changes=publisher_changes,
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
    # The framework REGISTRY the live catalogue currently offers, id -> row
    # whose ``name`` is the focal-document display name. Loaded from the
    # applied seeder artifact rather than a table (see ``load_live_frameworks``).
    frameworks: Dict[str, LiveEntityRow] = field(default_factory=dict)

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

    live.frameworks = derive_live_frameworks(
        live.controls, await load_live_framework_registry(session)
    )

    return live


async def resolve_live_catalog_version(session: AsyncSession) -> Optional[str]:
    """The catalogue version the live rows belong to.

    Ledger first (latest applied import run), else the max version stamped on
    the control rows — the pre-first-upgrade bootstrap. ONE implementation:
    ``tasks_catalog._resolve_from_version`` and the admin backfill CLI both
    call this, so a diff's live side and the version a registry row is written
    against can never disagree.
    """
    from services.catalog_apply import get_current_catalog_version

    version = await get_current_catalog_version(session)
    if version:
        return version
    result = await session.execute(select(func.max(SCFCatalogControl.catalog_version)))
    return result.scalar()


async def load_live_framework_registry(session: AsyncSession) -> Optional[dict]:
    """The stored framework registry for the live catalogue version, if any.

    None on installs seeded before ``fwreg001`` (or before the row was
    backfilled), which sends ``derive_live_frameworks`` to the file fallback.
    """
    version = await resolve_live_catalog_version(session)
    if not version:
        return None
    result = await session.execute(
        select(CatalogFrameworkRegistry).where(
            CatalogFrameworkRegistry.catalog_version == version
        )
    )
    row = result.scalars().first()
    return row.registry if row is not None else None


def derive_live_frameworks(
    controls: Dict[str, LiveEntityRow],
    registry: Optional[dict] = None,
) -> Dict[str, LiveEntityRow]:
    """The framework registry the live catalogue currently offers.

    Frameworks have no catalogue table — only ``organization_framework_selections``
    records which ids an org chose — so "live" has to be derived. Two sources,
    with distinct jobs:

    * **The catalogue rows are authoritative for membership.** A framework is
      live iff some active control maps to it. Taken from the already-loaded
      ``controls`` rather than a second query, so it is by construction the
      same snapshot the rest of the diff compares — and it is the exact set
      ``bulk_scope_frameworks`` scopes from, so a framework that is "live" here
      is one a tenant can actually hold.
    * **The stored registry decorates it.** ``catalog_framework_registries``
      holds the display NAME and the publisher's focal-document identifier for
      the live catalogue version, and both are succession signals — without the
      identifier the DECLARED succession tier can never fire and the
      ``framework_churn`` gate blocks every real upgrade. ``registry`` is that
      row, passed in by ``load_live_catalog``. When it is absent — an install
      seeded before ``fwreg001``, or one whose row has not been backfilled —
      we fall back to reading ``DATA_DIR/framework_registry.json`` (or the
      older ``frameworks.json``).

    Deriving membership from the artifact instead was wrong in a way worth
    recording: the artifact is a file on a mounted volume with no transactional
    relationship to the session, so a stale or foreign DATA_DIR reported
    hundreds of phantom retirements and blocked the upgrade.

    Ids the ingestion now classifies as non-frameworks are excluded from the
    live side. A platform seeded before the column partition existed carries
    ``risk_r_1`` / ``errata_2026_2`` in its framework mappings; those were never
    frameworks, so their absence from a clean extraction is a correction and
    must not be counted as a retirement.

    The JSON artifact is a frontend cache derived from the same extraction;
    ``catalog_framework_registries`` is the transactional record.
    """
    extractor = _load_extractor()
    non_framework = getattr(extractor, "non_framework_id_reason", lambda _k: None)

    live_ids: set = set()
    for row in controls.values():
        if row.status != "active":
            continue
        for key in (row.fields or {}).get("framework_mappings") or {}:
            if not non_framework(key):
                live_ids.add(key)

    if registry:
        return _decorated_frameworks(live_ids, _registry_decoration(registry))

    decoration: Dict[str, dict] = {}
    try:
        from catalog_seeder import DATA_DIR  # local import: optional dependency

        registry = Path(DATA_DIR) / "framework_registry.json"
        if registry.exists():
            with open(registry) as f:
                entries = json.load(f)
            if isinstance(entries, dict):
                decoration = {
                    k: {
                        "name": (v or {}).get("name"),
                        "focal_document_id": (v or {}).get("focal_document_id"),
                    }
                    for k, v in entries.items()
                }
        else:
            artifact = Path(DATA_DIR) / "frameworks.json"
            if artifact.exists():
                with open(artifact) as f:
                    names = json.load(f)
                if isinstance(names, dict):
                    decoration = {
                        k: {"name": v, "focal_document_id": None}
                        for k, v in names.items()
                    }
    except Exception:  # pragma: no cover - ids alone still diff correctly
        logger.warning(
            "framework registry artifact unreadable; diffing on ids alone "
            "(display names and focal-document ids unavailable)",
            exc_info=True,
        )

    return _decorated_frameworks(live_ids, decoration)


def _registry_decoration(registry: dict) -> Dict[str, dict]:
    """Registry rows -> the two fields the frameworks diff compares."""
    return {
        key: {
            "name": (value or {}).get("name"),
            "focal_document_id": (value or {}).get("focal_document_id"),
        }
        for key, value in registry.items()
    }


def _decorated_frameworks(
    live_ids: set, decoration: Dict[str, dict]
) -> Dict[str, LiveEntityRow]:
    return {
        key: LiveEntityRow(
            key=key,
            status="active",
            fields={
                "focal_document_id": decoration.get(key, {}).get("focal_document_id")
            },
            name=decoration.get(key, {}).get("name"),
        )
        for key in sorted(live_ids)
    }


# ---------------------------------------------------------------------------
# Publisher declarations (SCF 2026.3+ change sheets)
# ---------------------------------------------------------------------------


def publisher_retired_focal_document_ids(extracted: ExtractedCatalog) -> set:
    """Focal-document identifiers the publisher declares removed this release.

    Read off the STRM Errata sheet ("removed in 2026.3"). Identifiers, not our
    framework ids: the errata sheet never mentions a mapping column header, and
    the FDI is the only key that survives a rename in either direction.
    """
    frameworks = (extracted.publisher_changes or {}).get("frameworks") or {}
    return {
        str((entry or {}).get("fdi")).strip()
        for entry in (frameworks.get("removed") or [])
        if (entry or {}).get("fdi")
    }


def _live_focal_document_id(
    live_frameworks: Dict[str, LiveEntityRow], key: str
) -> Optional[str]:
    row = live_frameworks.get(key)
    if row is None:
        return None
    value = (row.fields or {}).get("focal_document_id")
    return str(value).strip() if value else None


def publisher_changes_are_empty(publisher_changes: Optional[dict]) -> bool:
    """Whether a workbook shipped no publisher change sheets at all.

    Distinguished from "shipped them and reported nothing" so the console can
    hide the panel for a pre-2026.3 workbook instead of showing an empty one.
    """
    if not publisher_changes:
        return True
    if publisher_changes.get("summary"):
        return False
    frameworks = publisher_changes.get("frameworks") or {}
    if any(frameworks.get(k) for k in ("added", "removed", "mapping_errata")):
        return False
    controls = publisher_changes.get("controls") or {}
    return not any(controls.get(k) for k in ("counts", "merged", "tags"))


# ---------------------------------------------------------------------------
# Sanity gates (plan §4.2.2 — any failure ⇒ run 'blocked')
# ---------------------------------------------------------------------------


def run_sanity_checks(
    extracted: ExtractedCatalog,
    live: LiveCatalog,
    *,
    live_registry: Optional["LiveRegistryStatus"] = None,
) -> SanityReport:
    """Run every staging gate.

    ``live_registry`` is the outcome of the stage-time registry self-heal
    (``services.framework_registry.ensure_live_framework_registry``). It is
    optional so the many callers that construct a diff directly - tests, and any
    future non-staging consumer - keep their existing behaviour: when it is None
    the ``live_framework_registry`` check is not emitted at all, rather than
    emitted with a fabricated verdict about a registry nobody looked at.
    """
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
        # Name the direction explicitly. A signed percentage next to the word
        # "drop" reads a 57-control RISE as "(-3.7% drop)", which is how a
        # renumbering release slipped past a human reading this line.
        delta = workbook_count - live_active
        if delta > 0:
            direction = f"{delta} more ({abs(drop):.1%} rise)"
        elif delta < 0:
            direction = f"{abs(delta)} fewer ({abs(drop):.1%} drop)"
        else:
            direction = "no net change"
        drop_detail = (
            f"live active controls: {live_active}, workbook controls: "
            f"{workbook_count} - {direction}"
        )
    else:
        # Empty live catalog: nothing to compare a drop against.
        drop_ok = True
        drop_detail = f"live catalog empty; workbook controls: {workbook_count}"
    checks.append(
        SanityCheck(check="control_count_drop", passed=drop_ok, detail=drop_detail)
    )

    # Net counts cannot see churn: 801 retirements offset by 858 additions is a
    # +3.7% rise on the check above and a catalog-wide renumbering underneath.
    # This gate counts the retirements themselves and asks the workbook to
    # account for them.
    crosswalk = build_legacy_crosswalk(extracted)
    workbook_keys = {
        key
        for key in (str(c.get("scf_id") or "").strip() for c in extracted.controls)
        if key
    }
    retiring = {
        key
        for key, row in live.controls.items()
        if row.status == "active" and key not in workbook_keys
    }
    unexplained = {
        key for key in retiring if crosswalk.get(key) not in workbook_keys
    }
    if retiring and live_active > 0:
        ratio = len(unexplained) / live_active
        churn_ok = (
            len(unexplained) < CONTROL_CHURN_MIN_ROWS
            or ratio <= CONTROL_CHURN_UNEXPLAINED_THRESHOLD
        )
        explained = len(retiring) - len(unexplained)
        churn_detail = (
            f"{len(retiring)} live controls absent from the workbook; "
            f"{explained} explained by the Legacy SCF # crosswalk, "
            f"{len(unexplained)} unexplained ({ratio:.1%} of live active)"
        )
        if not churn_ok:
            sample = ", ".join(sorted(unexplained)[:5])
            churn_detail += (
                f" - refusing a mass retirement the workbook does not account "
                f"for (e.g. {sample})"
            )
    else:
        churn_ok = True
        churn_detail = "no live controls are absent from the workbook"
    checks.append(
        SanityCheck(check="control_churn", passed=churn_ok, detail=churn_detail)
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

    # The seam the framework_churn gate below depends on. Ordered immediately
    # before it because a framework_churn failure is UNREADABLE without this
    # line: "0 carry the workbook's own focal-document identifier" describes a
    # release that dropped 73 documents and a platform that has no identifiers
    # to compare against identically, and production hit the second one.
    if live_registry is not None:
        registry_version = live_registry.catalog_version or "unknown"
        if live_registry.usable:
            if live_registry.recovered_from_run_id:
                provenance = (
                    f"recovered from the {registry_version} upgrade workbook"
                )
            else:
                provenance = str(live_registry.source)
            registry_detail = (
                f"registry for {registry_version}: {live_registry.entries} "
                f"frameworks, {live_registry.with_focal_document_id} carrying a "
                f"focal-document identifier (source: {provenance})"
            )
        else:
            registry_detail = (
                f"no framework registry with focal-document identifiers is stored "
                f"for the live catalog {registry_version} and none could be "
                f"recovered from a stored upgrade workbook "
                f"({live_registry.reason or 'no reason recorded'}). Register the "
                f"{registry_version} workbook on Platform → Catalog "
                f'("Register your current catalog workbook"), discard this run and '
                f"upload the new workbook again."
            )
        checks.append(
            SanityCheck(
                check="live_framework_registry",
                passed=live_registry.usable,
                detail=registry_detail,
            )
        )

    # ``framework_names`` only asks whether the map came back non-empty. It
    # passed at "extracted 383 framework display names" on a release that
    # silently dropped 75 of them, because a count says nothing about identity.
    # This gate names the removals and asks the succession matcher to account
    # for them, the same shape as control_churn - except that where controls
    # have the workbook's own Legacy SCF # crosswalk, frameworks have only a
    # derived heuristic, so what "explained" means here is weaker and the
    # detail string says so.
    live_frameworks = live.frameworks or {}
    live_fw_active = {
        key for key, row in live_frameworks.items() if row.status == "active"
    }
    workbook_frameworks = extracted.framework_names or {}
    fw_removed = {
        key: live_frameworks[key].name
        for key in sorted(live_fw_active - set(workbook_frameworks))
    }
    if fw_removed and live_fw_active:
        fw_added = {
            k: v for k, v in workbook_frameworks.items() if k not in live_fw_active
        }
        fw_retained = {
            k: v for k, v in workbook_frameworks.items() if k in live_fw_active
        }
        proposals = match_framework_successions(
            fw_removed,
            fw_added,
            fw_retained,
            focal_document_ids=framework_focal_document_ids(extracted, live),
            control_sets=framework_control_sets(extracted, live),
        )
        # Only a DECLARED pairing counts as an explanation here, and that is
        # the whole design of the gate. control_churn is unblocked by the
        # vendor's own Legacy SCF # column - an assertion SCF publishes and
        # stands behind - not by our confidence in our own guess. A gate that
        # accepted derived matches would have exactly one lever: widen the
        # matcher until the number falls. Every extra match decrements
        # `unexplained` whether it is right or wrong, so the cheapest way to
        # unblock a release would be to make the matcher less careful - and the
        # error it would be loosened into is the silent one that rebinds a
        # tenant's scope to the wrong document. Derived proposals still reach
        # the reviewer; they just cannot let a release past this check.
        #
        # There are three kinds of declaration, and the detail names them
        # separately because "73 unexplained" and "73 accounted for" read the
        # same to an operator who is only shown a total:
        #
        #  * renamed       - same focal-document identifier on both sides. The
        #                    publisher moved the column header; the document is
        #                    the document (TIER_DECLARED).
        #  * new edition   - the identifier differs only by its edition token, so
        #                    it is the 2026 revision of the 2020 document
        #                    (TIER_DECLARED_STEM).
        #  * retired by    - the publisher's own STRM Errata sheet lists the live
        #    the publisher   document's identifier as "removed in <version>".
        #                    Nothing succeeds it; it is gone on purpose.
        #
        # The third is new in 2026.3 and is the only one that can explain a
        # removal with no successor at all. It is still a DECLARATION - read off
        # the publisher's sheet, matched on the FDI - so it cannot be widened
        # into the failure mode above. A derived matcher tier still explains
        # nothing, whatever it proposes.
        fw_renamed = sorted(
            k for k, p in proposals.items()
            if p.bound_successor and p.best.tier == TIER_DECLARED
        )
        fw_new_edition = sorted(
            k for k, p in proposals.items()
            if p.bound_successor and p.best.tier == TIER_DECLARED_STEM
        )
        publisher_retired_fdis = publisher_retired_focal_document_ids(extracted)
        already = set(fw_renamed) | set(fw_new_edition)
        fw_publisher_retired = sorted(
            key for key in fw_removed
            if key not in already
            and _live_focal_document_id(live_frameworks, key) in publisher_retired_fdis
        )
        fw_explained = sorted(already | set(fw_publisher_retired))
        fw_unexplained = sorted(set(fw_removed) - set(fw_explained))
        fw_ratio = len(fw_unexplained) / len(live_fw_active)
        fw_churn_ok = (
            len(fw_unexplained) < FRAMEWORK_CHURN_MIN_ROWS
            or fw_ratio <= FRAMEWORK_CHURN_UNEXPLAINED_THRESHOLD
        )
        fw_detail = (
            f"{len(fw_removed)} live frameworks absent from the workbook: "
            f"{len(fw_renamed)} renamed (same focal document), "
            f"{len(fw_new_edition)} superseded by a new edition, "
            f"{len(fw_publisher_retired)} retired by the publisher, "
            f"{len(fw_unexplained)} unexplained "
            f"({fw_ratio:.1%} of {len(live_fw_active)} live active) - blocks "
            f"above {FRAMEWORK_CHURN_UNEXPLAINED_THRESHOLD:.0%} unless under "
            f"the {FRAMEWORK_CHURN_MIN_ROWS}-row floor"
        )
        if not fw_churn_ok:
            sample = ", ".join(sorted(fw_unexplained)[:5])
            fw_detail += (
                f" - refusing a framework retirement nothing accounts for "
                f"(e.g. {sample})"
            )
    else:
        fw_churn_ok = True
        fw_detail = (
            "no live frameworks are absent from the workbook"
            if live_fw_active
            else "no live framework registry to compare against"
        )
    checks.append(
        SanityCheck(
            check="framework_churn", passed=fw_churn_ok, detail=fw_detail
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
# Declared control succession (controls only, plan §4.2.3)
# ---------------------------------------------------------------------------


def build_legacy_crosswalk(extracted: ExtractedCatalog) -> Dict[str, str]:
    """Map each predecessor SCF id to its successor, from the workbook itself.

    SCF ships the ``Legacy SCF #`` column from 2026.3 onward; it is the
    authoritative record of a renumbering, and it is what makes a mass
    renumber distinguishable from a mass retirement. Without it a release
    that renames 1,457 controls looks identical to one that retires them and
    adds 1,457 unrelated replacements.

    Controls that kept their id contribute nothing (they are not renames).
    Pre-2026.3 workbooks carry no such column, so the crosswalk is empty and
    every caller falls back to its prior behaviour.

    A predecessor claimed by more than one successor would be a split, which
    the format does not express unambiguously; first-in-workbook-order wins
    and the ambiguity surfaces as a sanity-check detail rather than a silent
    pick.
    """
    crosswalk: Dict[str, str] = {}
    for ctrl in extracted.controls:
        successor = str(ctrl.get("scf_id") or "").strip()
        if not successor:
            continue
        for legacy in ctrl.get("legacy_scf_ids") or []:
            legacy_key = str(legacy).strip()
            if not legacy_key or legacy_key == successor:
                continue
            crosswalk.setdefault(legacy_key, successor)
    return crosswalk


SUCCESSION_SOURCE_WORKBOOK_CROSSWALK = "workbook_crosswalk"
# The READ THIS sheet's deprecation block, which names the survivor each merged
# control was folded into. Second in precedence behind the Legacy SCF # column:
# the crosswalk is a per-row machine-readable field the publisher maintains for
# renumbering, while the merge list is prose about a release. Where both speak
# they have agreed so far, and where they disagree the structured field wins.
SUCCESSION_SOURCE_PUBLISHER_MERGED = "publisher_merged"


def build_publisher_merges(
    extracted: ExtractedCatalog,
) -> Dict[str, Tuple[str, Optional[str]]]:
    """Legacy control id -> (survivor id, the legacy control's own name).

    From the READ THIS sheet's deprecation block (2026.3+), which is the only
    place the workbook states which retired control was merged into which
    survivor. Empty for every earlier release.

    Two jobs, and both need the same map. A merged control that LEFT the
    workbook is a deprecation whose successor the publisher has declared. A
    merged control whose id is still in the workbook has had its id handed to an
    unrelated control, and the changed row carries an ``IdReuse`` flag instead.
    """
    raw = ((extracted.publisher_changes or {}).get("controls") or {}).get("merged")
    merges: Dict[str, Tuple[str, Optional[str]]] = {}
    for entry in raw or []:
        legacy = str((entry or {}).get("legacy_scf_id") or "").strip()
        survivor = str((entry or {}).get("merged_into") or "").strip()
        if not legacy or not survivor or legacy == survivor:
            continue
        legacy_name = (entry or {}).get("legacy_name")
        legacy_name = str(legacy_name).strip() or None if legacy_name else None
        # First mention wins, matching build_legacy_crosswalk.
        merges.setdefault(legacy, (survivor, legacy_name))
    return merges


def declared_successor(
    key: str,
    workbook_rows: Dict[str, dict],
    legacy_crosswalk: Optional[Dict[str, str]],
    publisher_merges: Optional[Dict[str, Tuple[str, Optional[str]]]],
) -> Tuple[Optional[str], Optional[str]]:
    """(successor, source) the workbook declares for a departing control.

    ``(None, None)`` when the workbook declares nothing. A declared successor
    that is not itself a row in the new workbook is DROPPED rather than
    carried: a crosswalk or merge note pointing outside this catalog is stale,
    and pairing to a key that does not exist would fail apply-time validation
    for every org at once.
    """
    crosswalk_successor = (legacy_crosswalk or {}).get(key)
    if crosswalk_successor in workbook_rows:
        return crosswalk_successor, SUCCESSION_SOURCE_WORKBOOK_CROSSWALK
    merged_into = ((publisher_merges or {}).get(key) or (None, None))[0]
    if merged_into in workbook_rows:
        return merged_into, SUCCESSION_SOURCE_PUBLISHER_MERGED
    return None, None


# ---------------------------------------------------------------------------
# Per-entity diff
# ---------------------------------------------------------------------------


def compute_entity_diff(
    workbook_rows: Dict[str, dict],
    live_rows: Dict[str, LiveEntityRow],
    compared: tuple,
    name_field: Optional[str] = None,
    legacy_crosswalk: Optional[Dict[str, str]] = None,
    publisher_merges: Optional[Dict[str, Tuple[str, Optional[str]]]] = None,
) -> EntityDiff:
    """Classify one entity's keys into the five change classes.

    ``legacy_crosswalk`` and ``publisher_merges`` (controls only) are the two
    things the workbook says about control succession, in that precedence. They
    are the ONLY sources of a successor on a deprecated row: nothing here
    proposes one. ``publisher_merges`` does double duty and also flags a changed
    row whose key the publisher declared merged away while handing the id to an
    unrelated control.
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
            # The publisher declared this key merged away, yet here the key is,
            # still in the workbook. The id has been reused for something else.
            merge = (publisher_merges or {}).get(key)
            diff.changed.append(
                ChangedEntity(
                    key=key,
                    name=name or live.name,
                    fields=changes,
                    id_reused=(
                        IdReuse(merged_into=merge[0], legacy_name=merge[1])
                        if merge
                        else None
                    ),
                )
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
        successor, source = declared_successor(
            key, workbook_rows, legacy_crosswalk, publisher_merges
        )
        suggestions = (
            [
                SupersededSuggestion(
                    scf_id=successor,
                    name=(
                        workbook_rows[successor].get(name_field)
                        if name_field
                        else None
                    )
                    or successor,
                    score=1.0,
                    # Not "which heuristics fired" — which AUTHORITY said so.
                    signals=[source],
                )
            ]
            if successor is not None
            else []
        )
        diff.deprecated.append(
            DeprecatedEntity(
                key=key,
                name=live.name,
                # The workbook is the authority on succession, so a declaration
                # wins. A pre-existing value on the live row is only a fallback
                # for a retirement the workbook says nothing about; the channel
                # for overriding a declaration is this run's pairings list,
                # which apply consults (see catalog_apply._apply_successors).
                superseded_by=successor or live.superseded_by,
                superseded_source=source,
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


SUCCESSION_SOURCE_DERIVED = "derived_succession"
# A match the workbook itself declares, via the Focal Documents sheet's Focal
# Document Identifier. Kept distinct from the derived source so a reviewer can
# see at a glance which pairings are the publisher's word and which are ours.
SUCCESSION_SOURCE_FOCAL_DOCUMENT = "workbook_focal_document"
# A retirement the publisher declares outright on its STRM Errata sheet
# ("removed in 2026.3"). There is no successor and none is implied: this records
# WHY the framework left, which is the difference between a deliberate
# retirement and a document that fell out of the workbook unnoticed.
SUCCESSION_SOURCE_PUBLISHER_DECLARED = "publisher_declared"

_DECLARED_TIERS = {TIER_DECLARED, TIER_DECLARED_STEM}


def framework_control_sets(
    extracted: ExtractedCatalog, live: LiveCatalog
) -> Dict[str, set]:
    """framework id -> the set of control ids mapping to it, in ONE id space.

    Both sides are expressed in the WORKBOOK's control-id space: the live rows
    are pushed forward through the workbook's own ``Legacy SCF #`` crosswalk
    first. Skipping that step makes the signal worse than useless - 2026.3
    renumbered nearly every control, so the same true framework pairs score a
    median overlap of 0.075 raw against 1.000 remapped.

    Where a framework id appears on both sides the workbook's set wins, since
    that is the coverage being proposed.
    """
    crosswalk = build_legacy_crosswalk(extracted)
    sets: Dict[str, set] = {}
    for key, row in (live.controls or {}).items():
        if row.status != "active":
            continue
        forward = crosswalk.get(key, key)
        for fw in (row.fields or {}).get("framework_mappings") or {}:
            sets.setdefault(fw, set()).add(forward)
    workbook_sets: Dict[str, set] = {}
    for ctrl in extracted.controls or []:
        key = str(ctrl.get("scf_id") or "").strip()
        if not key:
            continue
        for fw in (ctrl.get("framework_mappings") or {}):
            workbook_sets.setdefault(fw, set()).add(key)
    sets.update(workbook_sets)
    return sets


def framework_focal_document_ids(
    extracted: ExtractedCatalog, live: LiveCatalog
) -> Dict[str, Optional[str]]:
    """id -> focal-document identifier, across both sides of the diff.

    The live side's value comes from the applied registry artifact; the
    workbook side's from this extraction. Ids present on both sides take the
    workbook's value, which is the one being proposed.
    """
    ids: Dict[str, Optional[str]] = {}
    for key, row in (live.frameworks or {}).items():
        value = (row.fields or {}).get("focal_document_id")
        if value:
            ids[key] = value
    for key, entry in (extracted.framework_registry or {}).items():
        value = (entry or {}).get("focal_document_id")
        if value:
            ids[key] = value
    return ids


def compute_frameworks_diff(
    extracted: ExtractedCatalog, live: LiveCatalog
) -> EntityDiff:
    """Diff the framework REGISTRY: which focal documents the catalogue offers.

    Distinct from ``compute_framework_mappings_diff``, which reports how each
    control's mapping set moved. A framework can leave the registry while every
    surviving control keeps mappings, and a control's mappings can churn
    wholesale without the registry changing, so neither is derivable from the
    other.

    ``deprecated`` rows carry a DERIVED successor where the matcher is
    confident. That is a weaker claim than a control's ``superseded_by``, which
    the publisher declares in the workbook, and the contract keeps the two
    distinguishable: ``superseded_source`` is ``workbook_crosswalk`` for a
    declared control rename and ``derived_succession`` here. Every proposal is
    also repeated in ``suggestions`` with its score, its signals and an
    ambiguity flag, so nothing downstream has to take the bound value on faith.
    """
    workbook = {k: v for k, v in (extracted.framework_names or {}).items()}
    live_rows = live.frameworks or {}

    added_keys = sorted(k for k in workbook if k not in live_rows)
    # A live row that is already deprecated and back in the workbook is
    # RESURRECTED, not changed or unchanged: the five classes stay disjoint,
    # exactly as _compute_entity_diff keeps them for the other entities.
    common_keys = sorted(
        k for k in workbook
        if k in live_rows and live_rows[k].status == "active"
    )
    removed_keys = sorted(
        k for k, row in live_rows.items()
        if row.status == "active" and k not in workbook
    )
    resurrected_keys = sorted(
        k for k, row in live_rows.items()
        if row.status != "active" and k in workbook
    )

    removed = {k: live_rows[k].name for k in removed_keys}
    added = {k: workbook[k] for k in added_keys}
    retained = {k: workbook[k] for k in common_keys}
    proposals = match_framework_successions(
        removed,
        added,
        retained,
        focal_document_ids=framework_focal_document_ids(extracted, live),
        control_sets=framework_control_sets(extracted, live),
    )

    changed, unchanged = [], []
    for key in common_keys:
        old_name, new_name = live_rows[key].name, workbook[key]
        # A live registry derived from framework_mappings keys has no names; a
        # None old name is "unknown", never "changed to".
        if old_name is not None and _norm(old_name) != _norm(new_name):
            changed.append(
                ChangedEntity(
                    key=key,
                    name=new_name,
                    fields={"display_name": FieldChange(old=old_name, new=new_name)},
                )
            )
        else:
            unchanged.append(key)

    deprecated = []
    publisher_retired_fdis = publisher_retired_focal_document_ids(extracted)
    for key in removed_keys:
        proposal = proposals.get(key)
        bound = proposal.bound_successor if proposal else None
        suggestions = [
            SupersededSuggestion(
                scf_id=c.successor_id,
                name=c.successor_name,
                score=c.score,
                signals=list(c.signals),
                ambiguous=c.ambiguous,
                control_overlap=c.control_overlap,
            )
            for c in (proposal.candidates if proposal else [])
        ]
        source = None
        if bound:
            source = (
                SUCCESSION_SOURCE_FOCAL_DOCUMENT
                if proposal.best.tier in _DECLARED_TIERS
                else SUCCESSION_SOURCE_DERIVED
            )
        elif _live_focal_document_id(live_rows, key) in publisher_retired_fdis:
            # No successor, but not unaccounted for. Recorded on the row so the
            # console can say "retired by the publisher" instead of leaving the
            # reviewer to guess at a blank.
            source = SUCCESSION_SOURCE_PUBLISHER_DECLARED
        deprecated.append(
            DeprecatedEntity(
                key=key,
                name=live_rows[key].name,
                superseded_by=bound,
                superseded_source=source,
                suggestions=suggestions,
            )
        )

    return EntityDiff(
        added=[
            AddedEntity(key=k, name=workbook[k], data={"display_name": workbook[k]})
            for k in added_keys
        ],
        changed=changed,
        deprecated=deprecated,
        resurrected=[
            ResurrectedEntity(key=k, name=workbook[k]) for k in resurrected_keys
        ],
        unchanged=unchanged,
    )


def build_publisher_changes(
    extracted: ExtractedCatalog,
) -> Optional[PublisherChanges]:
    """The publisher's own change narrative, contract-shaped, or None.

    None means the workbook shipped no change sheets — every release up to
    2026.2 — and the console hides the panel rather than rendering a row of
    zeros that would read as "the publisher changed nothing".
    """
    raw = extracted.publisher_changes or {}
    if publisher_changes_are_empty(raw):
        return None
    return PublisherChanges.model_validate(raw)


def summarize_publisher_changes(
    publisher: Optional[PublisherChanges],
) -> Optional[PublisherChangesSummary]:
    """Counts only. The lists stay in the diff detail, which is the blob."""
    if publisher is None:
        return None
    return PublisherChangesSummary(
        summary=publisher.summary,
        frameworks_added=len(publisher.frameworks.added),
        frameworks_removed=len(publisher.frameworks.removed),
        mapping_errata=len(publisher.frameworks.mapping_errata),
        controls=dict(publisher.controls.counts),
    )


def compute_catalog_diff(
    extracted: ExtractedCatalog, live: LiveCatalog, from_version: str
) -> DiffDetail:
    """Full live-DB diff in the frozen ``DiffDetail`` contract shape."""
    workbook = _workbook_rows(extracted)
    live_by_entity = live.by_entity()

    workbook_controls = workbook[CatalogEntityType.CONTROLS]
    legacy_crosswalk = build_legacy_crosswalk(extracted)
    publisher_merges = build_publisher_merges(extracted)

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
            legacy_crosswalk=(
                legacy_crosswalk
                if entity_type is CatalogEntityType.CONTROLS
                else None
            ),
            publisher_merges=(
                publisher_merges
                if entity_type is CatalogEntityType.CONTROLS
                else None
            ),
        )

    entities[CatalogEntityType.FRAMEWORKS] = compute_frameworks_diff(
        extracted, live
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
        # The apply transaction persists this against to_version; the workbook
        # is gone by then, so the diff is the only carrier.
        framework_registry=extracted.framework_registry or {},
        publisher_changes=build_publisher_changes(extracted),
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
                # ``renamed`` is deprecations this RUN attributed a successor
                # to, whatever the source — the workbook's own crosswalk for
                # controls, a derived match for frameworks. A None source means
                # the value predates this run as an admin pairing and is not
                # this run's claim. Controls only ever set 'workbook_crosswalk',
                # so their count is unchanged by the generalisation.
                # ``superseded_by`` is required as well as a source, because a
                # source is now also set for a retirement the PUBLISHER declared
                # outright, which has no successor. A rename with nothing to
                # rename to is not a rename.
                renamed=sum(
                    1
                    for d in diff.deprecated
                    if d.superseded_source is not None and d.superseded_by
                ),
                # Changed rows whose key the publisher declared merged away and
                # then reused. Never a subset of ``renamed``: the reused id is
                # not deprecated by this run, so it has no successor to name.
                id_reused=sum(1 for c in diff.changed if c.id_reused is not None),
            )
            for entity_type, diff in detail.entities.items()
        },
        publisher_changes=summarize_publisher_changes(detail.publisher_changes),
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

    # BEFORE load_live_catalog, deliberately: this may WRITE the live version's
    # framework registry row (recovered from the applied run's own workbook), and
    # load_live_framework_registry a line later has to read what it wrote. The
    # other order leaves the diff comparing against a registry that exists in the
    # database but not in this snapshot, which is the silent-stale-read shape
    # that made the file-based registry unusable in the first place.
    from services.framework_registry import ensure_live_framework_registry

    live_registry = await ensure_live_framework_registry(session)
    live = await load_live_catalog(session)

    sanity = run_sanity_checks(extracted, live, live_registry=live_registry)
    if not sanity.passed:
        return StagedDiff(
            to_version=extracted.catalog_version,
            sanity_report=sanity,
            forced=force,
        )

    guard_version(from_version, extracted.catalog_version, force=force)

    detail = compute_catalog_diff(extracted, live, from_version)
    # No pairings are seeded onto the run. The declared successor lives in the
    # stored diff and apply reads it from there; ``superseded_pairings`` is the
    # admin's OVERRIDE list and must stay empty until an admin puts something in
    # it. Seeding it was what made "apply without editing" lose every
    # declaration the moment an admin saved a partial list of 801 rows.
    return StagedDiff(
        to_version=extracted.catalog_version,
        sanity_report=sanity,
        diff_detail=detail,
        diff_summary=summarize_diff(detail),
        forced=force,
    )
