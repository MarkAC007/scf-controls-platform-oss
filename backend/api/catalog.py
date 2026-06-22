"""
SCF Catalog API endpoints.

Provides read-only access to SCF catalog reference data:
- Controls with full metadata (CMM maturity, business size guidance, etc.)
- Domains
- Evidence Request List (ERL)
- Assessment Objectives
- Frameworks (with control counts)

This data is seeded from SCF 2025.4 JSON files on application startup.
"""
import logging
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_, text
from sqlalchemy.orm import load_only

from database import get_db
from auth import require_auth, User
from catalog_models import (
    SCFCatalogControl,
    SCFCatalogDomain,
    SCFCatalogEvidence,
    SCFCatalogAssessmentObjective,
)

import json
import os
from pathlib import Path

# Load framework display names from SCF export (355 frameworks)
# This JSON maps framework IDs to their display names
# In Docker: mounted at /app/data/json/frameworks.json
# In dev: relative path from backend/api to webclient/public/data
_docker_path = Path("/app/data/json/frameworks.json")
_dev_path = Path(__file__).parent.parent.parent / "webclient" / "public" / "data" / "frameworks.json"
_frameworks_json_path = _docker_path if _docker_path.exists() else _dev_path

try:
    FRAMEWORK_DISPLAY_NAMES = json.loads(_frameworks_json_path.read_text())
except FileNotFoundError:
    # Fallback if file not found (shouldn't happen in normal operation)
    FRAMEWORK_DISPLAY_NAMES = {}
    logging.warning(f"frameworks.json not found at {_frameworks_json_path}")


def format_framework_name(key: str) -> str:
    """Format a framework key into a readable name."""
    if key in FRAMEWORK_DISPLAY_NAMES:
        return FRAMEWORK_DISPLAY_NAMES[key]
    # Default: replace underscores with spaces and title case
    return key.replace('_', ' ').title()

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/catalog", tags=["catalog"])


# =============================================================================
# BULK EXPORT ENDPOINTS (for frontend initial load)
# =============================================================================

@router.get("/bulk/controls")
async def bulk_export_controls(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_auth),
):
    """
    Export all controls with full metadata in a single request.

    This endpoint is optimized for frontend initial data loading,
    returning all 1,451 controls with complete details including
    CMM maturity, business size guidance, SCRM focus, and risk/threat mappings.

    Returns the same format as control_guidance.json for compatibility.
    """
    result = await db.execute(
        select(SCFCatalogControl).order_by(SCFCatalogControl.scf_id)
    )
    controls = result.scalars().all()

    return {
        "total": len(controls),
        "controls": [
            {
                "scf_id": c.scf_id,
                "scf_domain": c.scf_domain,
                "control_name": c.control_name,
                "control_description": c.control_description,
                "control_question": c.control_question,
                "validation_cadence": c.validation_cadence,
                "control_weighting": c.control_weighting,
                "nist_csf_function": c.nist_csf_function,
                "pptdf_applicability": {
                    "people": c.pptdf_people,
                    "process": c.pptdf_process,
                    "technology": c.pptdf_technology,
                    "data": c.pptdf_data,
                    "facility": c.pptdf_facility,
                },
                "evidence_requests": c.evidence_requests or [],
                "framework_mappings": c.framework_mappings or {},
                "cmm_maturity": {
                    "level_0": c.cmm_level_0,
                    "level_1": c.cmm_level_1,
                    "level_2": c.cmm_level_2,
                    "level_3": c.cmm_level_3,
                    "level_4": c.cmm_level_4,
                    "level_5": c.cmm_level_5,
                },
                "business_size_guidance": {
                    "micro_small": c.biz_micro_small,
                    "small": c.biz_small,
                    "medium": c.biz_medium,
                    "large": c.biz_large,
                    "enterprise": c.biz_enterprise,
                },
                "scrm_focus": {
                    "tier1_strategic": c.scrm_tier1_strategic,
                    "tier2_operational": c.scrm_tier2_operational,
                    "tier3_tactical": c.scrm_tier3_tactical,
                },
                "risk_threat_mapping": {
                    "risk_codes": c.risk_codes or [],
                    "threat_codes": c.threat_codes or [],
                },
            }
            for c in controls
        ],
    }


@router.get("/bulk/evidence")
async def bulk_export_evidence(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_auth),
):
    """
    Export all evidence entries in ERL format (keyed by evidence_id).

    Returns the same format as erl.json for compatibility.
    """
    result = await db.execute(
        select(SCFCatalogEvidence).order_by(SCFCatalogEvidence.evidence_id)
    )
    evidence_list = result.scalars().all()

    # Return as dictionary keyed by evidence_id (matches erl.json format)
    return {
        e.evidence_id: {
            "evidence_id": e.evidence_id,
            "area_of_focus": e.area_of_focus,
            "artifact_title": e.artifact_title,
            "artifact_description": e.artifact_description,
            "control_mappings": e.control_mappings or [],
        }
        for e in evidence_list
    }


# =============================================================================
# CONTROLS ENDPOINTS
# =============================================================================

@router.get("/controls")
async def list_controls(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_auth),
    domain: Optional[str] = Query(None, description="Filter by SCF domain identifier (e.g., GOV, IAC)"),
    csf_function: Optional[str] = Query(None, description="Filter by NIST CSF function"),
    control_weighting: Optional[int] = Query(None, ge=0, le=10, description="Filter by control weighting (0-10)"),
    search: Optional[str] = Query(None, description="Search control name/description"),
    limit: int = Query(100, ge=1, le=1000, description="Max results"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
):
    """
    List SCF catalog controls with optional filtering.

    Returns controls with core metadata. Use /controls/{scf_id} for full details.
    """
    query = select(SCFCatalogControl)

    # Apply filters
    if domain:
        # Match domain identifier in scf_id (e.g., "GOV" matches "GOV-01", "GOV-02")
        query = query.where(SCFCatalogControl.scf_id.like(f"{domain}-%"))

    if csf_function:
        query = query.where(SCFCatalogControl.nist_csf_function == csf_function)

    if control_weighting is not None:
        query = query.where(SCFCatalogControl.control_weighting == control_weighting)

    if search:
        search_term = f"%{search}%"
        query = query.where(
            or_(
                SCFCatalogControl.control_name.ilike(search_term),
                SCFCatalogControl.control_description.ilike(search_term),
                SCFCatalogControl.scf_id.ilike(search_term),
            )
        )

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_query)

    # Apply pagination and ordering
    query = query.order_by(SCFCatalogControl.scf_id).offset(offset).limit(limit)

    result = await db.execute(query)
    controls = result.scalars().all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "controls": [
            {
                "scf_id": c.scf_id,
                "scf_domain": c.scf_domain,
                "control_name": c.control_name,
                "control_description": c.control_description[:200] + "..." if len(c.control_description) > 200 else c.control_description,
                "nist_csf_function": c.nist_csf_function,
                "control_weighting": c.control_weighting,
                "validation_cadence": c.validation_cadence,
            }
            for c in controls
        ],
    }


@router.get("/controls/{scf_id}")
async def get_control(
    scf_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_auth),
):
    """
    Get full details for a specific SCF control.

    Includes all extended fields: CMM maturity, business size guidance,
    SCRM focus, risk/threat mappings, framework mappings, etc.
    """
    result = await db.execute(
        select(SCFCatalogControl).where(SCFCatalogControl.scf_id == scf_id.upper())
    )
    control = result.scalar_one_or_none()

    if not control:
        raise HTTPException(status_code=404, detail=f"Control {scf_id} not found")

    return {
        "scf_id": control.scf_id,
        "scf_domain": control.scf_domain,
        "control_name": control.control_name,
        "control_description": control.control_description,
        "control_question": control.control_question,
        "validation_cadence": control.validation_cadence,
        "control_weighting": control.control_weighting,
        "nist_csf_function": control.nist_csf_function,
        "pptdf_applicability": {
            "people": control.pptdf_people,
            "process": control.pptdf_process,
            "technology": control.pptdf_technology,
            "data": control.pptdf_data,
            "facility": control.pptdf_facility,
        },
        "evidence_requests": control.evidence_requests,
        "framework_mappings": control.framework_mappings,
        "cmm_maturity": {
            "level_0": control.cmm_level_0,
            "level_1": control.cmm_level_1,
            "level_2": control.cmm_level_2,
            "level_3": control.cmm_level_3,
            "level_4": control.cmm_level_4,
            "level_5": control.cmm_level_5,
        },
        "business_size_guidance": {
            "micro_small": control.biz_micro_small,
            "small": control.biz_small,
            "medium": control.biz_medium,
            "large": control.biz_large,
            "enterprise": control.biz_enterprise,
        },
        "scrm_focus": {
            "tier1_strategic": control.scrm_tier1_strategic,
            "tier2_operational": control.scrm_tier2_operational,
            "tier3_tactical": control.scrm_tier3_tactical,
        },
        "risk_threat_mapping": {
            "risk_codes": control.risk_codes,
            "threat_codes": control.threat_codes,
        },
        "required_artifact_types": control.required_artifact_types,
        "required_artifact_types_extracted_at": control.required_artifact_types_extracted_at,
        "catalog_version": control.catalog_version,
    }


# =============================================================================
# DOMAINS ENDPOINTS
# =============================================================================

@router.get("/domains")
async def list_domains(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_auth),
):
    """
    List all SCF domains in order.
    """
    result = await db.execute(
        select(SCFCatalogDomain).order_by(SCFCatalogDomain.order)
    )
    domains = result.scalars().all()

    return {
        "total": len(domains),
        "domains": [
            {
                "identifier": d.identifier,
                "order": d.order,
                "name": d.name,
                "principle": d.principle,
                "principle_intent": d.principle_intent,
            }
            for d in domains
        ],
    }


@router.get("/domains/{identifier}")
async def get_domain(
    identifier: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_auth),
):
    """
    Get a specific SCF domain with its controls.
    """
    # Get domain
    result = await db.execute(
        select(SCFCatalogDomain).where(SCFCatalogDomain.identifier == identifier.upper())
    )
    domain = result.scalar_one_or_none()

    if not domain:
        raise HTTPException(status_code=404, detail=f"Domain {identifier} not found")

    # Get controls in this domain
    controls_result = await db.execute(
        select(SCFCatalogControl)
        .where(SCFCatalogControl.scf_id.like(f"{identifier.upper()}-%"))
        .order_by(SCFCatalogControl.scf_id)
    )
    controls = controls_result.scalars().all()

    return {
        "identifier": domain.identifier,
        "order": domain.order,
        "name": domain.name,
        "principle": domain.principle,
        "principle_intent": domain.principle_intent,
        "control_count": len(controls),
        "controls": [
            {
                "scf_id": c.scf_id,
                "control_name": c.control_name,
                "nist_csf_function": c.nist_csf_function,
            }
            for c in controls
        ],
    }


# =============================================================================
# EVIDENCE ENDPOINTS
# =============================================================================

@router.get("/evidence")
async def list_evidence(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_auth),
    area: Optional[str] = Query(None, description="Filter by area of focus"),
    search: Optional[str] = Query(None, description="Search title/description"),
    limit: int = Query(100, ge=1, le=500, description="Max results"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
):
    """
    List SCF Evidence Request List (ERL) entries.
    """
    query = select(SCFCatalogEvidence)

    if area:
        query = query.where(SCFCatalogEvidence.area_of_focus.ilike(f"%{area}%"))

    if search:
        search_term = f"%{search}%"
        query = query.where(
            or_(
                SCFCatalogEvidence.artifact_title.ilike(search_term),
                SCFCatalogEvidence.artifact_description.ilike(search_term),
                SCFCatalogEvidence.evidence_id.ilike(search_term),
            )
        )

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_query)

    # Apply pagination
    query = query.order_by(SCFCatalogEvidence.evidence_id).offset(offset).limit(limit)

    result = await db.execute(query)
    evidence = result.scalars().all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "evidence": [
            {
                "evidence_id": e.evidence_id,
                "area_of_focus": e.area_of_focus,
                "artifact_title": e.artifact_title,
                "artifact_description": e.artifact_description,
                "control_mappings": e.control_mappings,
            }
            for e in evidence
        ],
    }


@router.get("/evidence/{evidence_id}")
async def get_evidence(
    evidence_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_auth),
):
    """
    Get a specific evidence entry with its mapped controls.
    """
    result = await db.execute(
        select(SCFCatalogEvidence).where(SCFCatalogEvidence.evidence_id == evidence_id.upper())
    )
    evidence = result.scalar_one_or_none()

    if not evidence:
        raise HTTPException(status_code=404, detail=f"Evidence {evidence_id} not found")

    return {
        "evidence_id": evidence.evidence_id,
        "area_of_focus": evidence.area_of_focus,
        "artifact_title": evidence.artifact_title,
        "artifact_description": evidence.artifact_description,
        "control_mappings": evidence.control_mappings,
        "catalog_version": evidence.catalog_version,
    }


# =============================================================================
# ASSESSMENT OBJECTIVES ENDPOINTS
# =============================================================================

@router.get("/assessment-objectives")
async def list_assessment_objectives(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_auth),
    scf_id: Optional[str] = Query(None, description="Filter by parent control ID"),
    rigor: Optional[int] = Query(None, ge=1, le=3, description="Filter by assessment rigor (1-3)"),
    search: Optional[str] = Query(None, description="Search objective text"),
    limit: int = Query(100, ge=1, le=500, description="Max results"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
):
    """
    List SCF Assessment Objectives with optional filtering.
    """
    query = select(SCFCatalogAssessmentObjective)

    if scf_id:
        query = query.where(SCFCatalogAssessmentObjective.scf_id == scf_id.upper())

    if rigor:
        query = query.where(SCFCatalogAssessmentObjective.assessment_rigor == rigor)

    if search:
        query = query.where(
            SCFCatalogAssessmentObjective.objective_text.ilike(f"%{search}%")
        )

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_query)

    # Apply pagination
    query = query.order_by(SCFCatalogAssessmentObjective.ao_id).offset(offset).limit(limit)

    result = await db.execute(query)
    objectives = result.scalars().all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "assessment_objectives": [
            {
                "ao_id": ao.ao_id,
                "scf_id": ao.scf_id,
                "objective_text": ao.objective_text[:200] + "..." if len(ao.objective_text) > 200 else ao.objective_text,
                "assessment_rigor": ao.assessment_rigor,
                "ao_origins": ao.ao_origins,
                "pptdf_applicability": {
                    "people": ao.pptdf_people,
                    "process": ao.pptdf_process,
                    "technology": ao.pptdf_technology,
                    "data": ao.pptdf_data,
                    "facility": ao.pptdf_facility,
                },
            }
            for ao in objectives
        ],
    }


@router.get("/assessment-objectives/{ao_id}")
async def get_assessment_objective(
    ao_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_auth),
):
    """
    Get full details for a specific assessment objective.
    """
    result = await db.execute(
        select(SCFCatalogAssessmentObjective).where(
            SCFCatalogAssessmentObjective.ao_id == ao_id.upper()
        )
    )
    ao = result.scalar_one_or_none()

    if not ao:
        raise HTTPException(status_code=404, detail=f"Assessment objective {ao_id} not found")

    return {
        "ao_id": ao.ao_id,
        "scf_id": ao.scf_id,
        "objective_text": ao.objective_text,
        "pptdf_applicability": {
            "people": ao.pptdf_people,
            "process": ao.pptdf_process,
            "technology": ao.pptdf_technology,
            "data": ao.pptdf_data,
            "facility": ao.pptdf_facility,
        },
        "ao_origins": ao.ao_origins,
        "notes": ao.notes,
        "assessment_rigor": ao.assessment_rigor,
        "scf_defined_parameters": ao.scf_defined_parameters,
        "org_defined_parameters": ao.org_defined_parameters,
        "framework_mappings": {
            "cmmc_level1_ao": ao.cmmc_level1_ao,
            "dhs_ztcf_ao": ao.dhs_ztcf_ao,
            "nist_800_53a": ao.nist_800_53a,
            "nist_800_171a": ao.nist_800_171a,
            "nist_800_171a_r3": ao.nist_800_171a_r3,
            "nist_800_172a": ao.nist_800_172a,
        },
        "assessment_execution": {
            "asset_type": ao.asset_type,
            "assessment_procedure": ao.assessment_procedure,
            "expected_results": ao.expected_results,
        },
        "catalog_version": ao.catalog_version,
    }


# =============================================================================
# CONTROL RELATIONSHIPS
# =============================================================================

@router.get("/controls/{scf_id}/assessment-objectives")
async def get_control_assessment_objectives(
    scf_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_auth),
):
    """
    Get all assessment objectives for a specific control.
    """
    # Verify control exists
    control_result = await db.execute(
        select(SCFCatalogControl.scf_id, SCFCatalogControl.control_name)
        .where(SCFCatalogControl.scf_id == scf_id.upper())
    )
    control = control_result.first()

    if not control:
        raise HTTPException(status_code=404, detail=f"Control {scf_id} not found")

    # Get assessment objectives
    ao_result = await db.execute(
        select(SCFCatalogAssessmentObjective)
        .where(SCFCatalogAssessmentObjective.scf_id == scf_id.upper())
        .order_by(SCFCatalogAssessmentObjective.ao_id)
    )
    objectives = ao_result.scalars().all()

    return {
        "scf_id": control.scf_id,
        "control_name": control.control_name,
        "assessment_objective_count": len(objectives),
        "assessment_objectives": [
            {
                "ao_id": ao.ao_id,
                "objective_text": ao.objective_text,
                "assessment_rigor": ao.assessment_rigor,
                "ao_origins": ao.ao_origins,
            }
            for ao in objectives
        ],
    }


@router.get("/controls/{scf_id}/evidence")
async def get_control_evidence(
    scf_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_auth),
):
    """
    Get all evidence requirements for a specific control.
    """
    # Get control with evidence_requests
    result = await db.execute(
        select(SCFCatalogControl).where(SCFCatalogControl.scf_id == scf_id.upper())
    )
    control = result.scalar_one_or_none()

    if not control:
        raise HTTPException(status_code=404, detail=f"Control {scf_id} not found")

    evidence_ids = control.evidence_requests or []

    # Get evidence details
    if evidence_ids:
        evidence_result = await db.execute(
            select(SCFCatalogEvidence)
            .where(SCFCatalogEvidence.evidence_id.in_(evidence_ids))
        )
        evidence = evidence_result.scalars().all()
    else:
        evidence = []

    return {
        "scf_id": control.scf_id,
        "control_name": control.control_name,
        "evidence_count": len(evidence),
        "evidence": [
            {
                "evidence_id": e.evidence_id,
                "area_of_focus": e.area_of_focus,
                "artifact_title": e.artifact_title,
                "artifact_description": e.artifact_description,
            }
            for e in evidence
        ],
    }


# =============================================================================
# FRAMEWORKS ENDPOINT
# =============================================================================

# Internal SCF mappings to exclude from compliance framework listings
# These are risk/threat codes and internal SCF metadata, not external frameworks
INTERNAL_MAPPING_PREFIXES = (
    'risk_',      # Risk mappings (R-GV-1, R-AC-1, etc.)
    'threat_',    # Threat mappings (NT-1, MT-1, etc.)
    'scf_core_',  # SCF core profiles
    'control_threat_summary',  # Summary field
    'risk_threat_summary',     # Summary field
    'minimum_security_requirements_mcr_dsr',  # Internal
    'identify_',   # MCR/DSR identification
    'errata_',     # Version errata
)


@router.get("/frameworks")
async def list_frameworks(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_auth),
    include_internal: bool = Query(
        False,
        description="Include internal SCF mappings (risk, threat, scf_core). Default: False"
    ),
):
    """
    List all frameworks with their control counts.

    Returns frameworks that have at least one control mapped to them,
    along with the count of mapped controls for each framework.
    Used by the "Scope by Framework" feature to show available frameworks.

    By default, excludes internal SCF mappings (risk codes, threat codes, etc.)
    that are not external compliance frameworks.
    """
    # Query to get distinct framework keys and their control counts
    # The framework_mappings column is JSONB with keys as framework IDs
    # We use jsonb_object_keys to extract all keys, then count controls per key
    query = text("""
        SELECT
            framework_key,
            COUNT(*) as control_count
        FROM
            scf_catalog_controls,
            jsonb_object_keys(framework_mappings) as framework_key
        GROUP BY
            framework_key
        ORDER BY
            control_count DESC, framework_key ASC
    """)

    result = await db.execute(query)
    rows = result.fetchall()

    frameworks = []
    for row in rows:
        framework_id = row[0]
        control_count = row[1]

        # Filter out internal mappings unless explicitly requested
        if not include_internal:
            if framework_id.startswith(INTERNAL_MAPPING_PREFIXES):
                continue

        frameworks.append({
            "id": framework_id,
            "name": format_framework_name(framework_id),
            "control_count": control_count,
        })

    return {
        "total": len(frameworks),
        "frameworks": frameworks,
    }
