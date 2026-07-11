"""
SCF Catalog Seeder - Populates catalog tables from JSON files on application startup.

This module handles seeding the SCF catalog reference data from JSON files.
Catalog data is READ-ONLY reference data and is NOT included in backup/restore operations.

Tables seeded:
- scf_catalog_controls: 1,451 SCF control definitions
- scf_catalog_domains: 33 SCF domain definitions
- scf_catalog_evidence: Evidence Request List (ERL) entries
- scf_catalog_assessment_objectives: ~5,736 assessment objectives
"""
import json
import os
import logging
from pathlib import Path
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert

from database import AsyncSessionLocal
from catalog_models import (
    SCFCatalogControl,
    SCFCatalogDomain,
    SCFCatalogEvidence,
    SCFCatalogAssessmentObjective,
    CapabilityTheme,
    CapabilityThemeMapping,
    SystemCatalogTemplate,
    SystemCatalogRecipe,
)
from services.system_catalog_validation import validate_vendor_file, validate_fallbacks_file

logger = logging.getLogger(__name__)

# Default catalog version
CATALOG_VERSION = "2025.4"

# Path to JSON data files
# In Docker: mounted at /app/data/json (see docker-compose.yml)
# In development: relative to project root
_DOCKER_DATA_PATH = Path("/app/data/json")
_LOCAL_DATA_PATH = Path(__file__).parent.parent / "webclient" / "public" / "data"

# Use Docker path if it exists, otherwise fall back to local development path
DATA_DIR = _DOCKER_DATA_PATH if _DOCKER_DATA_PATH.exists() else _LOCAL_DATA_PATH

# Systems knowledge catalog seed files (per-vendor JSON + _fallbacks.json).
# Lives inside the backend source tree, so it ships in the image via
# `COPY backend/ .` with no Dockerfile changes.
SYSTEM_CATALOG_DIR = Path(__file__).parent / "data" / "system_catalog"


def _resolve_catalog_version() -> str:
    """
    Resolve the catalog version from generated metadata, environment, or fallback.
    """
    meta_path = DATA_DIR / "catalog_meta.json"
    if meta_path.exists():
        try:
            with open(meta_path, "r") as f:
                meta = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(f"Could not read catalog metadata from {meta_path}: {exc}")
        else:
            if isinstance(meta, dict) and meta.get("catalog_version"):
                return str(meta["catalog_version"])

    return os.environ.get("SCF_CATALOG_VERSION") or CATALOG_VERSION


async def seed_catalog_if_empty() -> dict:
    """
    Main entry point for catalog seeding.
    Seeds all catalog tables if they are empty.

    Returns:
        dict: Summary of seeding operations with counts
    """
    async with AsyncSessionLocal() as session:
        results = {
            "controls": await seed_controls_if_empty(session),
            "domains": await seed_domains_if_empty(session),
            "evidence": await seed_evidence_if_empty(session),
            "assessment_objectives": await seed_assessment_objectives_if_empty(session),
            "capability_themes": await seed_capability_themes_if_empty(session),
            "system_catalog": await seed_system_catalog(session),
        }
        return results


def load_system_catalog_files() -> tuple:
    """
    Load and validate all system catalog seed files.

    Returns (vendors, fallbacks, fallbacks_version, errors):
    - vendors: list of validated vendor dicts
    - fallbacks: {system_type: {level: recipe}} from _fallbacks.json
    - fallbacks_version: the _fallbacks.json content version (drives reseeding)
    - errors: one string per unreadable/invalid file (never raises)
    """
    vendors = []
    fallbacks = {}
    fallbacks_version = "1.0"
    errors = []

    if not SYSTEM_CATALOG_DIR.is_dir():
        return vendors, fallbacks, fallbacks_version, errors

    for path in sorted(SYSTEM_CATALOG_DIR.glob("*.json")):
        try:
            with open(path, "r") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{path.name}: {exc}")
            continue

        if path.name == "_fallbacks.json":
            file_errors = validate_fallbacks_file(data)
            if file_errors:
                errors.append(f"{path.name}: " + "; ".join(file_errors))
            else:
                fallbacks = data["fallbacks"]
                fallbacks_version = data["version"]
        else:
            file_errors = validate_vendor_file(data)
            if file_errors:
                errors.append(f"{path.name}: " + "; ".join(file_errors))
            else:
                vendors.append(data)

    return vendors, fallbacks, fallbacks_version, errors


def _fallback_template_dict(system_type: str, recipes: dict, version: str) -> dict:
    """Synthesize a vendor-dict-shaped entry for a per-type generic fallback."""
    label = system_type.replace("_", " ").title()
    return {
        "slug": f"fallback-{system_type.replace('_', '-')}",
        "name": f"Generic {label}",
        "vendor": "Generic",
        "system_type": system_type,
        "category": None,
        "description": f"Generic collection guidance for {label.lower()} systems.",
        "website": None,
        "aliases": [],
        "logo_hint": None,
        "version": version,
        "recipes": recipes,
    }


async def seed_system_catalog(session: AsyncSession) -> dict:
    """
    Seed/refresh the systems knowledge catalog from backend/data/system_catalog/.

    Upsert keyed on slug, global templates only (organization_id IS NULL):
    - missing slug -> insert template + recipes
    - existing slug with a different file version -> update fields, recreate recipes
    - org-private (AI-generated) templates are never touched

    Invalid files are skipped with a logged warning; this never raises.
    """
    vendors, fallbacks, fallbacks_version, errors = load_system_catalog_files()
    for err in errors:
        logger.warning(f"System catalog seed file skipped: {err}")

    entries = [(v, False) for v in vendors] + [
        (_fallback_template_dict(st, recipes, fallbacks_version), True)
        for st, recipes in fallbacks.items()
    ]
    if not entries:
        return {"status": "skipped", "inserted": 0, "updated": 0, "errors": errors}

    existing_result = await session.execute(
        select(SystemCatalogTemplate).where(SystemCatalogTemplate.organization_id.is_(None))
    )
    existing = {t.slug: t for t in existing_result.scalars().all()}

    inserted = updated = skipped = 0
    for entry, is_fallback in entries:
        template = existing.get(entry["slug"])

        if template is None:
            template = SystemCatalogTemplate(
                slug=entry["slug"],
                name=entry["name"],
                vendor=entry["vendor"],
                system_type=entry["system_type"],
                category=entry.get("category"),
                description=entry.get("description"),
                website=entry.get("website"),
                aliases=entry.get("aliases", []),
                logo_hint=entry.get("logo_hint"),
                is_fallback=is_fallback,
                version=entry["version"],
            )
            session.add(template)
            await session.flush()
            inserted += 1
        elif template.version != entry["version"]:
            template.name = entry["name"]
            template.vendor = entry["vendor"]
            template.system_type = entry["system_type"]
            template.category = entry.get("category")
            template.description = entry.get("description")
            template.website = entry.get("website")
            template.aliases = entry.get("aliases", [])
            template.logo_hint = entry.get("logo_hint")
            template.is_fallback = is_fallback
            template.version = entry["version"]
            await session.execute(
                SystemCatalogRecipe.__table__.delete().where(
                    SystemCatalogRecipe.template_id == template.id
                )
            )
            updated += 1
        else:
            skipped += 1
            continue

        for level, recipe in entry["recipes"].items():
            session.add(SystemCatalogRecipe(
                template_id=template.id,
                maturity_level=level,
                title=recipe["title"],
                estimated_time=recipe.get("estimated_time"),
                frequency=recipe.get("frequency"),
                steps=recipe.get("steps", []),
                source="curated",
                version=entry["version"],
            ))

    await session.commit()
    logger.info(
        f"System catalog seeded: {inserted} inserted, {updated} updated, "
        f"{skipped} unchanged, {len(errors)} files skipped"
    )
    return {
        "status": "seeded",
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
    }


async def seed_controls_if_empty(session: AsyncSession) -> dict:
    """
    Seed SCF catalog controls from control_guidance.json if table is empty.
    """
    catalog_version = _resolve_catalog_version()

    # Check if table has data
    count_result = await session.execute(
        select(func.count()).select_from(SCFCatalogControl)
    )
    existing_count = count_result.scalar()

    if existing_count > 0:
        logger.info(f"SCF catalog controls already seeded ({existing_count} records)")
        return {"status": "skipped", "existing": existing_count}

    # Load JSON data
    json_path = DATA_DIR / "control_guidance.json"
    if not json_path.exists():
        logger.error(f"Control guidance JSON not found: {json_path}")
        return {"status": "error", "message": f"File not found: {json_path}"}

    with open(json_path, "r") as f:
        data = json.load(f)

    controls = data.get("controls", [])
    logger.info(f"Seeding {len(controls)} SCF catalog controls...")

    # Transform and insert controls
    for ctrl in controls:
        # Extract nested objects (use 'or {}' to handle explicit None values)
        pptdf = ctrl.get("pptdf_applicability") or {}
        cmm = ctrl.get("cmm_maturity") or {}
        biz = ctrl.get("business_size_guidance") or {}
        scrm = ctrl.get("scrm_focus") or {}
        risk_threat = ctrl.get("risk_threat_mapping") or {}

        control_obj = SCFCatalogControl(
            scf_id=ctrl["scf_id"],
            scf_domain=ctrl["scf_domain"],
            control_name=ctrl["control_name"],
            control_description=ctrl["control_description"],
            control_question=ctrl.get("control_question"),
            validation_cadence=ctrl.get("validation_cadence"),
            control_weighting=ctrl.get("control_weighting"),
            nist_csf_function=ctrl.get("nist_csf_function"),
            # PPTDF
            pptdf_people=pptdf.get("people", False),
            pptdf_process=pptdf.get("process", False),
            pptdf_technology=pptdf.get("technology", False),
            pptdf_data=pptdf.get("data", False),
            pptdf_facility=pptdf.get("facility", False),
            # JSONB arrays
            evidence_requests=ctrl.get("evidence_requests", []),
            framework_mappings=ctrl.get("framework_mappings", {}),
            # CMM Maturity levels
            cmm_level_0=cmm.get("level_0"),
            cmm_level_1=cmm.get("level_1"),
            cmm_level_2=cmm.get("level_2"),
            cmm_level_3=cmm.get("level_3"),
            cmm_level_4=cmm.get("level_4"),
            cmm_level_5=cmm.get("level_5"),
            # Business size guidance
            biz_micro_small=biz.get("micro_small"),
            biz_small=biz.get("small"),
            biz_medium=biz.get("medium"),
            biz_large=biz.get("large"),
            biz_enterprise=biz.get("enterprise"),
            # SCRM focus tiers
            scrm_tier1_strategic=scrm.get("tier1_strategic", False),
            scrm_tier2_operational=scrm.get("tier2_operational", False),
            scrm_tier3_tactical=scrm.get("tier3_tactical", False),
            # Risk/Threat codes
            risk_codes=risk_threat.get("risk_codes", []),
            threat_codes=risk_threat.get("threat_codes", []),
            # Metadata
            catalog_version=catalog_version,
        )
        session.add(control_obj)

    await session.commit()
    logger.info(f"Successfully seeded {len(controls)} SCF catalog controls")
    return {"status": "seeded", "count": len(controls)}


async def seed_domains_if_empty(session: AsyncSession) -> dict:
    """
    Seed SCF catalog domains from domains.json if table is empty.
    """
    catalog_version = _resolve_catalog_version()

    # Check if table has data
    count_result = await session.execute(
        select(func.count()).select_from(SCFCatalogDomain)
    )
    existing_count = count_result.scalar()

    if existing_count > 0:
        logger.info(f"SCF catalog domains already seeded ({existing_count} records)")
        return {"status": "skipped", "existing": existing_count}

    # Load JSON data
    json_path = DATA_DIR / "domains.json"
    if not json_path.exists():
        logger.error(f"Domains JSON not found: {json_path}")
        return {"status": "error", "message": f"File not found: {json_path}"}

    with open(json_path, "r") as f:
        domains = json.load(f)  # Direct array, not wrapped

    logger.info(f"Seeding {len(domains)} SCF catalog domains...")

    for domain in domains:
        domain_obj = SCFCatalogDomain(
            identifier=domain["identifier"],
            order=domain["order"],
            name=domain["name"],
            principle=domain["principle"],
            principle_intent=domain.get("principle_intent"),
            catalog_version=catalog_version,
        )
        session.add(domain_obj)

    await session.commit()
    logger.info(f"Successfully seeded {len(domains)} SCF catalog domains")
    return {"status": "seeded", "count": len(domains)}


async def seed_evidence_if_empty(session: AsyncSession) -> dict:
    """
    Seed SCF catalog evidence from erl.json if table is empty.
    """
    catalog_version = _resolve_catalog_version()

    # Check if table has data
    count_result = await session.execute(
        select(func.count()).select_from(SCFCatalogEvidence)
    )
    existing_count = count_result.scalar()

    if existing_count > 0:
        logger.info(f"SCF catalog evidence already seeded ({existing_count} records)")
        return {"status": "skipped", "existing": existing_count}

    # Load JSON data
    json_path = DATA_DIR / "erl.json"
    if not json_path.exists():
        logger.error(f"Evidence (ERL) JSON not found: {json_path}")
        return {"status": "error", "message": f"File not found: {json_path}"}

    with open(json_path, "r") as f:
        erl_data = json.load(f)  # Dict keyed by evidence_id

    logger.info(f"Seeding {len(erl_data)} SCF catalog evidence entries...")

    for evidence_id, evidence in erl_data.items():
        evidence_obj = SCFCatalogEvidence(
            evidence_id=evidence.get("evidence_id", evidence_id),
            area_of_focus=evidence["area_of_focus"],
            artifact_title=evidence["artifact_title"],
            artifact_description=evidence.get("artifact_description"),
            control_mappings=evidence.get("control_mappings", []),
            catalog_version=catalog_version,
        )
        session.add(evidence_obj)

    await session.commit()
    logger.info(f"Successfully seeded {len(erl_data)} SCF catalog evidence entries")
    return {"status": "seeded", "count": len(erl_data)}


async def seed_assessment_objectives_if_empty(session: AsyncSession) -> dict:
    """
    Seed SCF catalog assessment objectives from assessment_objectives.json if table is empty.
    """
    catalog_version = _resolve_catalog_version()

    # Check if table has data
    count_result = await session.execute(
        select(func.count()).select_from(SCFCatalogAssessmentObjective)
    )
    existing_count = count_result.scalar()

    if existing_count > 0:
        logger.info(f"SCF catalog assessment objectives already seeded ({existing_count} records)")
        return {"status": "skipped", "existing": existing_count}

    # Load JSON data
    json_path = DATA_DIR / "assessment_objectives.json"
    if not json_path.exists():
        logger.error(f"Assessment objectives JSON not found: {json_path}")
        return {"status": "error", "message": f"File not found: {json_path}"}

    with open(json_path, "r") as f:
        data = json.load(f)

    objectives = data.get("objectives", [])
    logger.info(f"Seeding {len(objectives)} SCF catalog assessment objectives...")

    # Batch insert for better performance (5736 records)
    batch_size = 500
    for i in range(0, len(objectives), batch_size):
        batch = objectives[i:i + batch_size]

        for ao in batch:
            # Extract nested PPTDF applicability (use 'or {}' to handle explicit None)
            pptdf = ao.get("pptdf_applicability") or {}

            ao_obj = SCFCatalogAssessmentObjective(
                ao_id=ao["ao_id"],
                scf_id=ao["scf_id"],
                objective_text=ao["objective_text"],
                # PPTDF
                pptdf_people=pptdf.get("people", False),
                pptdf_process=pptdf.get("process", False),
                pptdf_technology=pptdf.get("technology", False),
                pptdf_data=pptdf.get("data", False),
                pptdf_facility=pptdf.get("facility", False),
                # Assessment metadata
                ao_origins=ao.get("ao_origins"),
                notes=ao.get("notes"),
                assessment_rigor=ao.get("assessment_rigor"),
                scf_defined_parameters=ao.get("scf_defined_parameters"),
                org_defined_parameters=ao.get("org_defined_parameters"),
                # Framework-specific mappings
                cmmc_level1_ao=ao.get("cmmc_level1_ao"),
                dhs_ztcf_ao=ao.get("dhs_ztcf_ao"),
                nist_800_53a=ao.get("nist_800_53a"),
                nist_800_171a=ao.get("nist_800_171a"),
                nist_800_171a_r3=ao.get("nist_800_171a_r3"),
                nist_800_172a=ao.get("nist_800_172a"),
                # Assessment execution
                asset_type=ao.get("asset_type"),
                assessment_procedure=ao.get("assessment_procedure"),
                expected_results=ao.get("expected_results"),
                # Metadata
                catalog_version=catalog_version,
            )
            session.add(ao_obj)

        # Commit each batch
        await session.commit()
        logger.debug(f"Seeded assessment objectives batch {i//batch_size + 1}")

    logger.info(f"Successfully seeded {len(objectives)} SCF catalog assessment objectives")
    return {"status": "seeded", "count": len(objectives)}


async def seed_capability_themes_if_empty(session: AsyncSession) -> dict:
    """
    Seed capability themes and SCF-to-theme mappings from capability_themes.json.

    Themes are seeded directly from JSON. Mappings are computed by cross-referencing
    each SCF control's NIST 800-53 R5 framework mappings against the NIST family
    to KSI theme mapping defined in the JSON.
    """
    catalog_version = _resolve_catalog_version()

    # Check if themes table has data
    count_result = await session.execute(
        select(func.count()).select_from(CapabilityTheme)
    )
    existing_count = count_result.scalar()

    if existing_count > 0:
        logger.info(f"Capability themes already seeded ({existing_count} records)")
        return {"status": "skipped", "existing": existing_count}

    # Load capability themes JSON
    json_path = DATA_DIR / "capability_themes.json"
    if not json_path.exists():
        logger.error(f"Capability themes JSON not found: {json_path}")
        return {"status": "error", "message": f"File not found: {json_path}"}

    with open(json_path, "r") as f:
        data = json.load(f)

    themes_data = data.get("themes", [])
    nist_family_mappings = data.get("nist_family_mappings", {})

    logger.info(f"Seeding {len(themes_data)} capability themes...")

    # Step 1: Insert themes
    theme_code_to_id = {}
    for theme in themes_data:
        theme_obj = CapabilityTheme(
            theme_code=theme["theme_code"],
            name=theme["name"],
            description=theme["description"],
            ksi_reference=theme.get("ksi_reference"),
            display_order=theme.get("display_order", 0),
            icon=theme.get("icon"),
            catalog_version=catalog_version,
        )
        session.add(theme_obj)

    await session.flush()  # Flush to get auto-generated IDs

    # Build theme_code -> id lookup
    theme_result = await session.execute(
        select(CapabilityTheme.id, CapabilityTheme.theme_code)
    )
    for row in theme_result:
        theme_code_to_id[row.theme_code] = row.id

    logger.info(f"Inserted {len(theme_code_to_id)} capability themes")

    # Step 2: Load control guidance to compute SCF-to-theme mappings
    controls_json_path = DATA_DIR / "control_guidance.json"
    if not controls_json_path.exists():
        logger.warning(f"Control guidance JSON not found, skipping theme mappings: {controls_json_path}")
        await session.commit()
        return {"status": "partial", "count": len(theme_code_to_id), "mappings": 0}

    with open(controls_json_path, "r") as f:
        controls_data = json.load(f)

    controls = controls_data.get("controls", [])
    mapping_count = 0
    seen_pairs = set()  # Track (theme_id, scf_id) to avoid duplicates

    for ctrl in controls:
        scf_id = ctrl.get("scf_id")
        if not scf_id:
            continue

        nist_controls = ctrl.get("framework_mappings", {}).get("nist_800_53_r5", [])
        if not nist_controls:
            continue

        # Extract unique NIST families from this control's mappings
        nist_families = set()
        for nist_ctrl in nist_controls:
            # NIST control format: "AC-1", "IA-2.1", "PM-1", etc.
            family = nist_ctrl.split("-")[0] if "-" in nist_ctrl else nist_ctrl
            nist_families.add(family)

        # Map each NIST family to capability themes
        for family in nist_families:
            family_mapping = nist_family_mappings.get(family)
            if not family_mapping:
                continue

            # Primary mapping
            primary_theme = family_mapping.get("primary")
            if primary_theme and primary_theme in theme_code_to_id:
                pair = (theme_code_to_id[primary_theme], scf_id)
                if pair not in seen_pairs:
                    seen_pairs.add(pair)
                    session.add(CapabilityThemeMapping(
                        theme_id=theme_code_to_id[primary_theme],
                        scf_id=scf_id,
                        relevance="primary",
                        catalog_version=catalog_version,
                    ))
                    mapping_count += 1

            # Supporting mappings
            for supporting_theme in family_mapping.get("supporting", []):
                if supporting_theme in theme_code_to_id:
                    pair = (theme_code_to_id[supporting_theme], scf_id)
                    if pair not in seen_pairs:
                        seen_pairs.add(pair)
                        session.add(CapabilityThemeMapping(
                            theme_id=theme_code_to_id[supporting_theme],
                            scf_id=scf_id,
                            relevance="supporting",
                            catalog_version=catalog_version,
                        ))
                        mapping_count += 1

    await session.commit()
    logger.info(f"Successfully seeded {len(theme_code_to_id)} capability themes and {mapping_count} mappings")
    return {"status": "seeded", "count": len(theme_code_to_id), "mappings": mapping_count}


async def reseed_catalog(force: bool = False) -> dict:
    """
    Reseed catalog tables. If force=True, drops existing data first.
    Use with caution - this deletes and recreates all catalog data.

    Args:
        force: If True, deletes existing data before reseeding

    Returns:
        dict: Summary of reseeding operations
    """
    if not force:
        logger.warning("Reseed called without force=True, skipping")
        return {"status": "skipped", "message": "Use force=True to reseed"}

    async with AsyncSessionLocal() as session:
        # Delete existing data (order matters for foreign keys)
        logger.info("Clearing existing catalog data...")
        await session.execute(CapabilityThemeMapping.__table__.delete())
        await session.execute(CapabilityTheme.__table__.delete())
        await session.execute(SCFCatalogAssessmentObjective.__table__.delete())
        await session.execute(SCFCatalogEvidence.__table__.delete())
        await session.execute(SCFCatalogControl.__table__.delete())
        await session.execute(SCFCatalogDomain.__table__.delete())
        await session.commit()
        logger.info("Existing catalog data cleared")

    # Reseed
    return await seed_catalog_if_empty()


async def get_catalog_stats() -> dict:
    """
    Get current record counts for all catalog tables.

    Returns:
        dict: Record counts for each catalog table
    """
    async with AsyncSessionLocal() as session:
        controls = await session.execute(
            select(func.count()).select_from(SCFCatalogControl)
        )
        domains = await session.execute(
            select(func.count()).select_from(SCFCatalogDomain)
        )
        evidence = await session.execute(
            select(func.count()).select_from(SCFCatalogEvidence)
        )
        aos = await session.execute(
            select(func.count()).select_from(SCFCatalogAssessmentObjective)
        )

        themes = await session.execute(
            select(func.count()).select_from(CapabilityTheme)
        )
        theme_mappings = await session.execute(
            select(func.count()).select_from(CapabilityThemeMapping)
        )

        return {
            "controls": controls.scalar(),
            "domains": domains.scalar(),
            "evidence": evidence.scalar(),
            "assessment_objectives": aos.scalar(),
            "capability_themes": themes.scalar(),
            "capability_theme_mappings": theme_mappings.scalar(),
            "catalog_version": _resolve_catalog_version(),
        }
