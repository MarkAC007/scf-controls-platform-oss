"""
System Evidence Capability API endpoints.
Handles CRUD operations for system-evidence capability mappings.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from sqlalchemy.orm import selectinload
from typing import List, Optional
from uuid import UUID

from database import get_db
from models import System, SystemEvidenceCapability, Organization, User, EvidenceTracking, ScopedControl, RecipeFeedback
from schemas import (
    SystemEvidenceCapabilityResponse,
    SystemEvidenceCapabilityCreate,
    SystemEvidenceCapabilityUpdate,
    SuccessResponse,
    EvidenceSuggestionsResponse,
    CapableSystemInfo,
    EvidenceRecommendation,
    EvidenceGapsResponse,
    EvidenceGapItem,
    FrameworkReadinessRequest,
    FrameworkReadinessResponse,
    FrameworkReadinessItem,
    CollectionGuidanceSchema,
    CollectionRecipeSchema,
    RecipeStepSchema,
    RecipeFeedbackCreate,
    RecipeFeedbackResponse,
    SystemRecipesResponse,
    SystemCatalogRecipeResponse,
)
from auth import require_org_role, OrgMembership, get_current_user
from services.system_catalog_resolution import resolve_recipes_for_system
from services.system_catalog_validation import RECIPE_LEVELS
from api.system_catalog import template_summary

import json
import os
from functools import lru_cache
from pathlib import Path

router = APIRouter(tags=["capabilities"])


# ============================================================================
# Recipe Resolution Helpers
# ============================================================================

def _find_data_file(filename: str) -> Optional[Path]:
    """Find a data JSON file, checking backend/data/json first then webclient/public/data."""
    candidates = [
        Path(__file__).parent.parent / "data" / "json" / filename,  # backend/data/json/ (Docker)
        Path(__file__).parent.parent.parent / "webclient" / "public" / "data" / filename,  # local dev
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


@lru_cache(maxsize=1)
def _load_collection_interfaces() -> dict:
    """Load and cache collection interfaces from JSON file."""
    ci_path = _find_data_file("collection_interfaces.json")
    if not ci_path:
        return {}
    with open(ci_path, "r") as f:
        return json.load(f)


MATURITY_ORDER = ["L0", "L1", "L2", "L3", "L4", "L5"]

# Catalog recipes exist for RECIPE_LEVELS (L1-L4, owned by the catalog
# validation module); requests at the extremes clamp inward.
RECIPE_LEVEL_ORDER = list(RECIPE_LEVELS)


def clamp_recipe_level(level) -> str:
    """Clamp a maturity level to the range recipes exist for (L1-L4)."""
    if level == "L0":
        return "L1"
    if level == "L5":
        return "L4"
    return level if level in RECIPE_LEVEL_ORDER else "L1"


def matched_via_to_confidence(matched_via: str) -> str:
    """Map catalog resolution provenance onto the recipe_confidence vocabulary
    the frontend renders. An explicit template link is system-specific; a
    heuristic alias match is honest about being vendor-level guidance."""
    if matched_via == "template":
        return "system_specific"
    if matched_via == "alias":
        return "vendor_generic"
    return "type_generic"


def _recipe_schema_from_row(recipe_row) -> CollectionRecipeSchema:
    """Convert a SystemCatalogRecipe row to the guidance recipe schema."""
    return CollectionRecipeSchema(
        title=recipe_row.title,
        estimated_time=recipe_row.estimated_time,
        frequency=recipe_row.frequency,
        steps=[RecipeStepSchema(**step) for step in (recipe_row.steps or [])],
        source=recipe_row.source or "curated",
    )


def _get_maturity_appropriate_methods(system_type: str, maturity_level: str) -> list[dict]:
    """Get collection interfaces appropriate for the given maturity level and system type."""
    ci_data = _load_collection_interfaces()
    level_idx = MATURITY_ORDER.index(maturity_level) if maturity_level in MATURITY_ORDER else 0
    methods = []

    for ci_id, ci in ci_data.items():
        # Check system type compatibility
        ci_types = ci.get("system_types", [])
        if system_type not in ci_types and "manual" not in ci_types:
            continue

        # Check maturity range
        maturity_range = ci.get("maturity_range", {})
        min_level = maturity_range.get("min", "L0")
        max_level = maturity_range.get("max", "L5")
        min_idx = MATURITY_ORDER.index(min_level) if min_level in MATURITY_ORDER else 0
        max_idx = MATURITY_ORDER.index(max_level) if max_level in MATURITY_ORDER else 5

        if min_idx <= level_idx <= max_idx:
            methods.append({
                "id": ci_id,
                "title": ci.get("title", ""),
                "collection_method": ci.get("collection_method", ""),
            })

    return methods


# ============================================================================
# CAPABILITY ENDPOINTS (NESTED UNDER SYSTEMS)
# ============================================================================

@router.get(
    "/organizations/{org_id}/systems/{system_id}/capabilities",
    response_model=List[SystemEvidenceCapabilityResponse]
)
async def list_system_capabilities(
    org_id: UUID,
    system_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    capability_status: Optional[str] = Query(None, description="Filter by capability status"),
    db: AsyncSession = Depends(get_db)
):
    """
    Get all evidence capabilities for a specific system.
    Requires: viewer role or higher.
    Optionally filter by capability_status (potential, configured, active).
    """
    # Verify system exists and belongs to org
    system_result = await db.execute(
        select(System).where(
            and_(
                System.id == system_id,
                System.organization_id == org_id
            )
        )
    )
    if not system_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="System not found")

    # Build query
    query = select(SystemEvidenceCapability).where(
        SystemEvidenceCapability.system_id == system_id
    ).options(
        selectinload(SystemEvidenceCapability.system),
        selectinload(SystemEvidenceCapability.created_by),
        selectinload(SystemEvidenceCapability.updated_by)
    )

    if capability_status:
        query = query.where(SystemEvidenceCapability.capability_status == capability_status)

    query = query.order_by(SystemEvidenceCapability.evidence_id)

    result = await db.execute(query)
    capabilities = result.scalars().all()
    return capabilities


@router.get(
    "/organizations/{org_id}/systems/{system_id}/capabilities/{capability_id}",
    response_model=SystemEvidenceCapabilityResponse
)
async def get_capability(
    org_id: UUID,
    system_id: UUID,
    capability_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db)
):
    """
    Get a specific capability by ID.
    Requires: viewer role or higher.
    """
    # Verify system exists and belongs to org
    system_result = await db.execute(
        select(System).where(
            and_(
                System.id == system_id,
                System.organization_id == org_id
            )
        )
    )
    if not system_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="System not found")

    query = select(SystemEvidenceCapability).where(
        and_(
            SystemEvidenceCapability.id == capability_id,
            SystemEvidenceCapability.system_id == system_id
        )
    ).options(
        selectinload(SystemEvidenceCapability.system),
        selectinload(SystemEvidenceCapability.created_by),
        selectinload(SystemEvidenceCapability.updated_by)
    )

    result = await db.execute(query)
    capability = result.scalar_one_or_none()

    if not capability:
        raise HTTPException(status_code=404, detail="Capability not found")

    return capability


@router.post(
    "/organizations/{org_id}/systems/{system_id}/capabilities",
    response_model=SystemEvidenceCapabilityResponse,
    status_code=201
)
async def create_capability(
    org_id: UUID,
    system_id: UUID,
    capability_data: SystemEvidenceCapabilityCreate,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db)
):
    """
    Add a new evidence capability to a system.
    Requires: editor role or higher.
    Each system can only have one capability entry per evidence_id.
    """
    current_user = membership.user
    # Verify system exists and belongs to org
    system_result = await db.execute(
        select(System).where(
            and_(
                System.id == system_id,
                System.organization_id == org_id
            )
        )
    )
    if not system_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="System not found")

    # Check if capability for this evidence_id already exists
    existing = await db.execute(
        select(SystemEvidenceCapability).where(
            and_(
                SystemEvidenceCapability.system_id == system_id,
                SystemEvidenceCapability.evidence_id == capability_data.evidence_id
            )
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail=f"Capability for evidence '{capability_data.evidence_id}' already exists for this system"
        )

    # Create new capability
    new_capability = SystemEvidenceCapability(
        system_id=system_id,
        created_by_user_id=UUID(current_user.db_id) if current_user and current_user.db_id else None,
        **capability_data.model_dump()
    )
    db.add(new_capability)
    await db.commit()

    # Reload with relationships
    query = select(SystemEvidenceCapability).where(
        SystemEvidenceCapability.id == new_capability.id
    ).options(
        selectinload(SystemEvidenceCapability.system),
        selectinload(SystemEvidenceCapability.created_by),
        selectinload(SystemEvidenceCapability.updated_by)
    )
    result = await db.execute(query)
    return result.scalar_one()


@router.patch(
    "/organizations/{org_id}/systems/{system_id}/capabilities/{capability_id}",
    response_model=SystemEvidenceCapabilityResponse
)
async def update_capability(
    org_id: UUID,
    system_id: UUID,
    capability_id: UUID,
    capability_update: SystemEvidenceCapabilityUpdate,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db)
):
    """
    Partially update a capability.
    Requires: editor role or higher.
    Only provided fields will be updated.
    """
    current_user = membership.user
    # Verify system exists and belongs to org
    system_result = await db.execute(
        select(System).where(
            and_(
                System.id == system_id,
                System.organization_id == org_id
            )
        )
    )
    if not system_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="System not found")

    # Find capability
    result = await db.execute(
        select(SystemEvidenceCapability).where(
            and_(
                SystemEvidenceCapability.id == capability_id,
                SystemEvidenceCapability.system_id == system_id
            )
        )
    )
    capability = result.scalar_one_or_none()

    if not capability:
        raise HTTPException(status_code=404, detail="Capability not found")

    # Update fields
    update_data = capability_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(capability, key, value)

    # Track who updated
    if current_user:
        capability.updated_by_user_id = UUID(current_user.db_id) if current_user.db_id else None

    await db.commit()

    # Reload with relationships
    query = select(SystemEvidenceCapability).where(
        SystemEvidenceCapability.id == capability.id
    ).options(
        selectinload(SystemEvidenceCapability.system),
        selectinload(SystemEvidenceCapability.created_by),
        selectinload(SystemEvidenceCapability.updated_by)
    )
    result = await db.execute(query)
    return result.scalar_one()


@router.delete(
    "/organizations/{org_id}/systems/{system_id}/capabilities/{capability_id}",
    response_model=SuccessResponse
)
async def delete_capability(
    org_id: UUID,
    system_id: UUID,
    capability_id: UUID,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db)
):
    """
    Remove a capability from a system.
    Requires: editor role or higher.
    """
    # Verify system exists and belongs to org
    system_result = await db.execute(
        select(System).where(
            and_(
                System.id == system_id,
                System.organization_id == org_id
            )
        )
    )
    if not system_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="System not found")

    # Find capability
    result = await db.execute(
        select(SystemEvidenceCapability).where(
            and_(
                SystemEvidenceCapability.id == capability_id,
                SystemEvidenceCapability.system_id == system_id
            )
        )
    )
    capability = result.scalar_one_or_none()

    if not capability:
        raise HTTPException(status_code=404, detail="Capability not found")

    evidence_id = capability.evidence_id
    await db.delete(capability)
    await db.commit()

    return SuccessResponse(message=f"Capability for evidence '{evidence_id}' removed successfully")


# ============================================================================
# PER-SYSTEM RECIPE RESOLUTION
# ============================================================================

async def _get_org_system(db: AsyncSession, org_id: UUID, system_id: UUID) -> System:
    """Load an org-scoped system or raise 404."""
    result = await db.execute(
        select(System).where(
            and_(
                System.id == system_id,
                System.organization_id == org_id
            )
        )
    )
    system = result.scalar_one_or_none()
    if not system:
        raise HTTPException(status_code=404, detail="System not found")
    return system


@router.get(
    "/organizations/{org_id}/systems/{system_id}/recipes",
    response_model=SystemRecipesResponse
)
async def get_system_recipes(
    org_id: UUID,
    system_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db)
):
    """
    Resolve collection recipes for a system from the systems knowledge catalog.
    Requires: viewer role or higher.
    Resolution order: explicit template link -> alias match -> per-type fallback.
    """
    system = await _get_org_system(db, org_id, system_id)

    resolution = await resolve_recipes_for_system(db, system)

    return SystemRecipesResponse(
        system_id=system.id,
        matched_via=resolution.matched_via,
        template=template_summary(resolution.template) if resolution.template else None,
        recipes=[SystemCatalogRecipeResponse.model_validate(r) for r in resolution.recipes],
    )


@router.post(
    "/organizations/{org_id}/systems/{system_id}/generate-recipes",
    status_code=202
)
async def generate_system_recipes(
    org_id: UUID,
    system_id: UUID,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db)
):
    """
    Queue AI generation of collection recipes for a system.
    Requires: editor role or higher.
    Output is stored as an org-private catalog template with
    source='ai_generated' recipes; poll the status endpoint or re-fetch
    the recipes endpoint to see results. Runs in mock mode without an
    ANTHROPIC_API_KEY (returns clearly marked sample recipes).
    """
    await _get_org_system(db, org_id, system_id)

    from tasks_recipe_generation import run_recipe_generation, recipegen_status_key, RECIPEGEN_STATUS_TTL
    from redis_client import get_redis_client

    try:
        r = await get_redis_client()
        await r.setex(
            recipegen_status_key(str(system_id)),
            RECIPEGEN_STATUS_TTL,
            json.dumps({"status": "queued"}),
        )
    except Exception:
        # Status tracking is best-effort; the task itself re-writes it.
        pass

    run_recipe_generation.delay(str(org_id), str(system_id))
    return {"status": "queued"}


@router.get(
    "/organizations/{org_id}/systems/{system_id}/generate-recipes/status"
)
async def get_recipe_generation_status(
    org_id: UUID,
    system_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db)
):
    """
    Read the AI recipe generation status for a system.
    Requires: viewer role or higher.
    Returns {"status": "idle"} when no generation has been requested recently.
    """
    await _get_org_system(db, org_id, system_id)

    from tasks_recipe_generation import recipegen_status_key
    from redis_client import get_redis_client

    try:
        r = await get_redis_client()
        raw = await r.get(recipegen_status_key(str(system_id)))
    except Exception:
        raw = None

    if not raw:
        return {"status": "idle"}
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {"status": "idle"}


# ============================================================================
# EVIDENCE-CENTRIC QUERIES
# ============================================================================

@router.get(
    "/organizations/{org_id}/evidence-capabilities/{evidence_id}",
    response_model=List[SystemEvidenceCapabilityResponse]
)
async def get_systems_for_evidence(
    org_id: UUID,
    evidence_id: str,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    capability_status: Optional[str] = Query(None, description="Filter by capability status"),
    db: AsyncSession = Depends(get_db)
):
    """
    Find all systems that can provide a specific evidence type.
    Requires: viewer role or higher.
    This is the inverse query - given an evidence_id, find capable systems.
    """
    # Organization existence verified by require_org_role

    # Query capabilities for this evidence_id across all systems in org
    query = select(SystemEvidenceCapability).join(
        System, SystemEvidenceCapability.system_id == System.id
    ).where(
        and_(
            System.organization_id == org_id,
            SystemEvidenceCapability.evidence_id == evidence_id
        )
    ).options(
        selectinload(SystemEvidenceCapability.system),
        selectinload(SystemEvidenceCapability.created_by),
        selectinload(SystemEvidenceCapability.updated_by)
    )

    if capability_status:
        query = query.where(SystemEvidenceCapability.capability_status == capability_status)

    result = await db.execute(query)
    capabilities = result.scalars().all()
    return capabilities


@router.get(
    "/organizations/{org_id}/evidence-capabilities",
    response_model=List[SystemEvidenceCapabilityResponse]
)
async def list_all_capabilities(
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    capability_status: Optional[str] = Query(None, description="Filter by capability status"),
    system_type: Optional[str] = Query(None, description="Filter by system type"),
    db: AsyncSession = Depends(get_db)
):
    """
    List all evidence capabilities across all systems in an organization.
    Requires: viewer role or higher.
    Useful for building a complete capability matrix.
    """
    # Organization existence verified by require_org_role

    # Query all capabilities
    query = select(SystemEvidenceCapability).join(
        System, SystemEvidenceCapability.system_id == System.id
    ).where(
        System.organization_id == org_id
    ).options(
        selectinload(SystemEvidenceCapability.system),
        selectinload(SystemEvidenceCapability.created_by),
        selectinload(SystemEvidenceCapability.updated_by)
    )

    if capability_status:
        query = query.where(SystemEvidenceCapability.capability_status == capability_status)
    if system_type:
        query = query.where(System.system_type == system_type)

    query = query.order_by(SystemEvidenceCapability.evidence_id, System.name)

    result = await db.execute(query)
    capabilities = result.scalars().all()
    return capabilities


# ============================================================================
# EVIDENCE COLLECTION SUGGESTIONS
# ============================================================================

@router.get(
    "/organizations/{org_id}/evidence/{evidence_id}/suggestions",
    response_model=EvidenceSuggestionsResponse
)
async def get_evidence_suggestions(
    org_id: UUID,
    evidence_id: str,
    system_id: Optional[UUID] = Query(None, description="System ID to get collection guidance for"),
    maturity_level: Optional[str] = Query(None, description="Current maturity level (L0-L5)"),
    include_alternatives: bool = Query(False, description="Include alternative collection methods"),
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db)
):
    """
    Get collection suggestions for a specific evidence type.
    Requires: viewer role or higher.
    Returns systems capable of providing this evidence with recommendations.

    When system_id is provided, also returns collection_guidance with
    step-by-step recipes filtered by maturity level.
    """
    # Organization existence verified by require_org_role

    # Get current tracking status for this evidence
    tracking_result = await db.execute(
        select(EvidenceTracking).where(
            and_(
                EvidenceTracking.organization_id == org_id,
                EvidenceTracking.evidence_id == evidence_id
            )
        )
    )
    tracking = tracking_result.scalar_one_or_none()

    currently_tracking = None
    current_system_id = None
    if tracking:
        currently_tracking = tracking.collecting_system
        current_system_id = tracking.system_id

    # Get all systems capable of providing this evidence
    query = select(SystemEvidenceCapability).join(
        System, SystemEvidenceCapability.system_id == System.id
    ).where(
        and_(
            System.organization_id == org_id,
            SystemEvidenceCapability.evidence_id == evidence_id,
            System.status == "active"  # Only active systems
        )
    ).options(
        selectinload(SystemEvidenceCapability.system)
    ).order_by(
        # Order by capability status (active first, then configured, then potential)
        SystemEvidenceCapability.capability_status.desc(),
        System.name
    )

    result = await db.execute(query)
    capabilities = result.scalars().all()

    # Build capable systems list
    capable_systems = []
    for cap in capabilities:
        system = cap.system
        capable_systems.append(CapableSystemInfo(
            system_id=system.id,
            name=system.name,
            system_type=system.system_type,
            vendor=system.vendor,
            capability_status=cap.capability_status,
            collection_method=cap.collection_method,
            confidence_level=cap.confidence_level,
            notes=cap.notes
        ))

    # Determine recommendation
    recommendation = None
    if capable_systems:
        # Priority: active > configured > potential
        # Secondary: high confidence > medium > low
        status_priority = {"active": 3, "configured": 2, "potential": 1}
        confidence_priority = {"high": 3, "medium": 2, "low": 1}

        def score_system(sys: CapableSystemInfo) -> int:
            return (
                status_priority.get(sys.capability_status, 0) * 10 +
                confidence_priority.get(sys.confidence_level, 0)
            )

        best = max(capable_systems, key=score_system)

        # Determine reason
        if best.capability_status == "active":
            reason = "Already actively collecting this evidence"
        elif best.capability_status == "configured":
            reason = "Configured and ready to collect this evidence"
        else:
            reason = f"Can provide this evidence ({best.confidence_level} confidence)"

        recommendation = EvidenceRecommendation(
            system_id=best.system_id,
            system_name=best.name,
            reason=reason
        )

    # Build collection guidance if system_id is provided
    collection_guidance = None
    if system_id:
        # Look up the system
        system_result = await db.execute(
            select(System).where(
                and_(
                    System.id == system_id,
                    System.organization_id == org_id,
                )
            )
        )
        target_system = system_result.scalar_one_or_none()

        if target_system:
            effective_maturity = maturity_level or "L1"  # Default to L1 if not specified
            recipe_level = clamp_recipe_level(effective_maturity)

            # Resolve recipes from the systems knowledge catalog
            resolution = await resolve_recipes_for_system(db, target_system)
            recipes_by_level = {r.maturity_level: r for r in resolution.recipes}

            recipe_schema = None
            recipe_row = recipes_by_level.get(recipe_level)
            if recipe_row:
                recipe_schema = _recipe_schema_from_row(recipe_row)

            next_recipe_schema = None
            level_idx = RECIPE_LEVEL_ORDER.index(recipe_level)
            if level_idx + 1 < len(RECIPE_LEVEL_ORDER):
                next_row = recipes_by_level.get(RECIPE_LEVEL_ORDER[level_idx + 1])
                if next_row:
                    next_recipe_schema = _recipe_schema_from_row(next_row)

            # Get maturity-appropriate methods
            appropriate_methods = _get_maturity_appropriate_methods(
                system_type=target_system.system_type,
                maturity_level=effective_maturity,
            )

            # Count alternatives (other systems that can provide this evidence)
            alternatives = len([s for s in capable_systems if str(s.system_id) != str(system_id)])

            collection_guidance = CollectionGuidanceSchema(
                system_id=target_system.id,
                system_name=target_system.name,
                system_type=target_system.system_type,
                vendor=target_system.vendor,
                current_maturity=effective_maturity,
                recipe=recipe_schema,
                recipe_confidence=matched_via_to_confidence(resolution.matched_via),
                matched_via=resolution.matched_via,
                maturity_appropriate_methods=appropriate_methods,
                next_level_preview=next_recipe_schema,
                alternatives_count=alternatives,
            )

    return EvidenceSuggestionsResponse(
        evidence_id=evidence_id,
        currently_tracking=currently_tracking,
        current_system_id=current_system_id,
        capable_systems=capable_systems,
        recommendation=recommendation,
        has_suggestions=len(capable_systems) > 0,
        collection_guidance=collection_guidance,
    )


# ============================================================================
# RECIPE FEEDBACK
# ============================================================================

@router.post(
    "/organizations/{org_id}/evidence/{evidence_id}/recipe-feedback",
    response_model=RecipeFeedbackResponse
)
async def submit_recipe_feedback(
    org_id: UUID,
    evidence_id: str,
    feedback: RecipeFeedbackCreate,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db)
):
    """
    Submit feedback on a collection recipe.
    Requires: viewer role or higher.
    Stores feedback for recipe maintenance and improvement.
    """
    fb = RecipeFeedback(
        organization_id=org_id,
        evidence_id=evidence_id,
        system_type=feedback.system_type,
        vendor=feedback.vendor,
        feedback_type=feedback.feedback_type,
        maturity_level=feedback.maturity_level,
        created_by_user_id=UUID(membership.user.db_id) if membership.user.db_id else None,
    )
    db.add(fb)
    await db.commit()
    await db.refresh(fb)

    return RecipeFeedbackResponse(
        id=fb.id,
        feedback_type=fb.feedback_type,
        maturity_level=fb.maturity_level,
        created_at=fb.created_at,
    )


# ============================================================================
# EVIDENCE GAP ANALYSIS
# ============================================================================

@router.get(
    "/organizations/{org_id}/evidence-gaps",
    response_model=EvidenceGapsResponse
)
async def get_evidence_gaps(
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db)
):
    """
    Analyze evidence collection gaps.
    Requires: viewer role or higher.
    Returns evidence items that aren't being collected but have capable systems.
    """
    # Organization existence verified by require_org_role

    # Get all evidence tracking entries for this org
    tracking_result = await db.execute(
        select(EvidenceTracking).where(
            EvidenceTracking.organization_id == org_id
        )
    )
    tracking_entries = {t.evidence_id: t for t in tracking_result.scalars().all()}

    # Get all system capabilities for this org
    capabilities_result = await db.execute(
        select(SystemEvidenceCapability).join(
            System, SystemEvidenceCapability.system_id == System.id
        ).where(
            and_(
                System.organization_id == org_id,
                System.status == "active"
            )
        ).options(
            selectinload(SystemEvidenceCapability.system)
        )
    )
    all_capabilities = capabilities_result.scalars().all()

    # Group capabilities by evidence_id
    capabilities_by_evidence = {}
    for cap in all_capabilities:
        if cap.evidence_id not in capabilities_by_evidence:
            capabilities_by_evidence[cap.evidence_id] = []
        capabilities_by_evidence[cap.evidence_id].append(cap)

    # Identify gaps: evidence that has capabilities but isn't being tracked
    gaps = []
    total_tracked = 0
    all_evidence_ids = set(tracking_entries.keys()) | set(capabilities_by_evidence.keys())

    for evidence_id in all_evidence_ids:
        tracking = tracking_entries.get(evidence_id)
        capabilities = capabilities_by_evidence.get(evidence_id, [])

        # Count as tracked if is_tracked=True and has a collecting_system
        is_actively_tracked = (
            tracking is not None and
            tracking.is_tracked and
            tracking.collecting_system
        )

        if is_actively_tracked:
            total_tracked += 1
            continue

        # If there are capable systems but not being tracked, it's a gap
        if capabilities:
            system_names = [cap.system.name for cap in capabilities if cap.system]
            system_ids = [cap.system.id for cap in capabilities if cap.system]

            # Find best system for recommendation
            best_cap = max(
                capabilities,
                key=lambda c: (
                    {"active": 3, "configured": 2, "potential": 1}.get(c.capability_status, 0),
                    {"high": 3, "medium": 2, "low": 1}.get(c.confidence_level, 0)
                )
            )
            best_system_name = best_cap.system.name if best_cap.system else "Unknown"

            recommended_action = f"Configure {best_system_name} to collect this evidence"
            if best_cap.capability_status == "active":
                recommended_action = f"{best_system_name} is already active - link it to tracking"
            elif best_cap.capability_status == "configured":
                recommended_action = f"Activate collection in {best_system_name}"

            gaps.append(EvidenceGapItem(
                evidence_id=evidence_id,
                evidence_title=None,  # Could be enriched from ERL data if available
                required_by_controls=[],  # Would need to cross-reference with scoped controls
                capable_systems=system_names,
                capable_system_ids=system_ids,
                recommended_action=recommended_action
            ))

    # Calculate coverage
    total_evidence = len(all_evidence_ids)
    coverage_percentage = (total_tracked / total_evidence * 100) if total_evidence > 0 else 100.0

    # Sort gaps by number of capable systems (more options = easier to fix)
    gaps.sort(key=lambda g: len(g.capable_systems), reverse=True)

    return EvidenceGapsResponse(
        total_gaps=len(gaps),
        total_tracked=total_tracked,
        total_evidence=total_evidence,
        coverage_percentage=round(coverage_percentage, 1),
        gaps=gaps
    )


# ============================================================================
# FRAMEWORK READINESS CALCULATION
# ============================================================================

# Readiness calculation weights
IMPLEMENTATION_WEIGHT = 0.4
EVIDENCE_WEIGHT = 0.6


def calculate_readiness_grade(score: float) -> str:
    """Convert readiness score to grade."""
    if score >= 90:
        return "excellent"
    elif score >= 70:
        return "good"
    elif score >= 50:
        return "fair"
    else:
        return "needs-work"


@router.post(
    "/organizations/{org_id}/framework-readiness",
    response_model=FrameworkReadinessResponse
)
async def calculate_framework_readiness(
    org_id: UUID,
    request: FrameworkReadinessRequest,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db)
):
    """
    Calculate framework readiness scores using the formula:
    Requires: viewer role or higher.
    Readiness = (40% × Implementation Score) + (60% × Evidence Score)

    The frontend sends framework-control-evidence mappings from the catalog,
    and this endpoint calculates readiness using the organization's database state.

    Implementation Score = % of selected controls that are implemented
    Evidence Score = % of required evidence that is tracked
    """
    # Organization existence verified by require_org_role

    # Get all scoped controls for this org
    controls_result = await db.execute(
        select(ScopedControl).where(ScopedControl.organization_id == org_id)
    )
    all_scoped_controls = {c.scf_id: c for c in controls_result.scalars().all()}

    # Get all evidence tracking for this org
    tracking_result = await db.execute(
        select(EvidenceTracking).where(EvidenceTracking.organization_id == org_id)
    )
    all_evidence_tracking = {t.evidence_id: t for t in tracking_result.scalars().all()}

    # Calculate readiness for each framework
    framework_results = []

    for framework_name, mapping in request.frameworks.items():
        # Control statistics
        total_controls = len(mapping.controls)
        selected_controls = 0
        implemented_controls = 0
        in_progress_controls = 0
        at_risk_controls = 0
        not_started_controls = 0

        for scf_id in mapping.controls:
            scoped = all_scoped_controls.get(scf_id)
            if scoped and scoped.selected:
                selected_controls += 1
                status = scoped.implementation_status or "not_started"
                if status == "implemented":
                    implemented_controls += 1
                elif status == "in_progress":
                    in_progress_controls += 1
                elif status == "at_risk":
                    at_risk_controls += 1
                else:  # not_started, not_applicable, deferred, or unknown
                    not_started_controls += 1

        # Evidence statistics
        total_evidence = len(mapping.evidence)
        tracked_evidence = 0

        for evidence_id in mapping.evidence:
            tracking = all_evidence_tracking.get(evidence_id)
            if tracking and tracking.is_tracked:
                tracked_evidence += 1

        # Calculate scores
        # Implementation score: % of selected controls that are implemented
        implementation_score = (
            (implemented_controls / selected_controls * 100)
            if selected_controls > 0
            else 0.0
        )

        # Evidence score: % of required evidence that is tracked
        evidence_score = (
            (tracked_evidence / total_evidence * 100)
            if total_evidence > 0
            else 0.0
        )

        # Combined readiness score
        readiness_score = (
            IMPLEMENTATION_WEIGHT * implementation_score +
            EVIDENCE_WEIGHT * evidence_score
        )

        framework_results.append(FrameworkReadinessItem(
            framework_name=framework_name,
            total_controls=total_controls,
            selected_controls=selected_controls,
            implemented_controls=implemented_controls,
            in_progress_controls=in_progress_controls,
            at_risk_controls=at_risk_controls,
            not_started_controls=not_started_controls,
            total_evidence=total_evidence,
            tracked_evidence=tracked_evidence,
            implementation_score=round(implementation_score, 1),
            evidence_score=round(evidence_score, 1),
            readiness_score=round(readiness_score, 1),
            readiness_grade=calculate_readiness_grade(readiness_score)
        ))

    # Sort by total controls (most comprehensive frameworks first)
    framework_results.sort(key=lambda f: f.total_controls, reverse=True)

    return FrameworkReadinessResponse(
        organization_id=org_id,
        calculation_weights={
            "implementation": IMPLEMENTATION_WEIGHT,
            "evidence": EVIDENCE_WEIGHT
        },
        frameworks=framework_results
    )
