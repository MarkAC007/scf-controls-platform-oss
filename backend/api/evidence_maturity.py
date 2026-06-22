"""
Evidence Maturity Advisory API endpoints.

Provides maturity assessment for evidence collection processes:
- GET /api/evidence/{evidence_id}/maturity - Single evidence maturity
- GET /api/organizations/{org_id}/evidence-maturity-summary - Organisation-wide summary
- GET /api/evidence/{evidence_id}/upgrade-recommendations - Improvement recommendations

Maturity Levels (L0-L5):
- L0: Non-Existent - No defined process
- L1: Ad Hoc - Inconsistent, reactive, manual
- L2: Developing - Documented but manual execution
- L3: Defined - Standardised, semi-automated
- L4: Managed - Fully automated via API
- L5: Optimising - AI/ML-driven continuous improvement
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from sqlalchemy.orm import selectinload
from typing import List, Optional
from uuid import UUID

from database import get_db
from models import EvidenceTracking, SystemEvidenceCapability, System
from schemas import (
    EvidenceMaturityResponse,
    EvidenceUpgradeRecommendationsResponse,
    UpgradeRecommendationResponse,
    OrganisationMaturitySummaryResponse,
    MaturityLevelSummary,
)
from auth import require_org_role, OrgMembership
from services.maturity import (
    calculate_maturity,
    get_upgrade_recommendations,
    MaturityInput,
    MaturityLevel,
    MaturityDistribution,
    MATURITY_NAMES,
    MATURITY_DESCRIPTIONS,
)

router = APIRouter(tags=["evidence_maturity"])


async def _get_evidence_with_capability(
    org_id: UUID,
    evidence_id: str,
    db: AsyncSession,
) -> tuple[Optional[EvidenceTracking], Optional[SystemEvidenceCapability], Optional[System]]:
    """
    Helper to fetch evidence tracking and associated capability data.

    Returns:
        Tuple of (EvidenceTracking, SystemEvidenceCapability, System) - any may be None
    """
    # Get evidence tracking record
    tracking_result = await db.execute(
        select(EvidenceTracking)
        .options(selectinload(EvidenceTracking.system))
        .where(
            and_(
                EvidenceTracking.organization_id == org_id,
                EvidenceTracking.evidence_id == evidence_id
            )
        )
    )
    tracking = tracking_result.scalar_one_or_none()

    # Get best capability for this evidence (prefer active, then configured)
    capability = None
    system = None

    if tracking and tracking.system_id:
        # Use the linked system's capability
        cap_result = await db.execute(
            select(SystemEvidenceCapability)
            .options(selectinload(SystemEvidenceCapability.system))
            .where(
                and_(
                    SystemEvidenceCapability.system_id == tracking.system_id,
                    SystemEvidenceCapability.evidence_id == evidence_id
                )
            )
        )
        capability = cap_result.scalar_one_or_none()
        if capability:
            system = capability.system
    else:
        # Look for any capable system in the org
        cap_result = await db.execute(
            select(SystemEvidenceCapability)
            .join(System, SystemEvidenceCapability.system_id == System.id)
            .options(selectinload(SystemEvidenceCapability.system))
            .where(
                and_(
                    System.organization_id == org_id,
                    SystemEvidenceCapability.evidence_id == evidence_id,
                    System.status == "active"
                )
            )
            .order_by(
                # Prefer active > configured > potential
                SystemEvidenceCapability.capability_status.desc()
            )
            .limit(1)
        )
        capability = cap_result.scalar_one_or_none()
        if capability:
            system = capability.system

    return tracking, capability, system


def _build_maturity_input(
    tracking: Optional[EvidenceTracking],
    capability: Optional[SystemEvidenceCapability],
) -> MaturityInput:
    """Build MaturityInput from database records."""
    return MaturityInput(
        is_tracked=tracking.is_tracked if tracking else False,
        collection_method=capability.collection_method if capability else None,
        capability_status=capability.capability_status if capability else None,
        frequency=tracking.frequency if tracking else None,
        last_collection_date=tracking.last_collection_date if tracking else None,
        has_system_linked=tracking.system_id is not None if tracking else False,
        method_of_collection=tracking.method_of_collection if tracking else None,
    )


# ============================================================================
# SINGLE EVIDENCE MATURITY
# ============================================================================

@router.get(
    "/organizations/{org_id}/evidence/{evidence_id}/maturity",
    response_model=EvidenceMaturityResponse
)
async def get_evidence_maturity(
    org_id: UUID,
    evidence_id: str,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db)
):
    """
    Get maturity level for a single evidence item.

    Calculates maturity based on:
    - Collection method (manual, export, api, webhook, scheduled)
    - Capability status (potential, configured, active)
    - Collection frequency and data freshness
    - Whether a system is linked

    Requires: viewer role or higher.
    """
    # Fetch evidence and capability data
    tracking, capability, system = await _get_evidence_with_capability(
        org_id, evidence_id, db
    )

    # Build input and calculate maturity
    maturity_input = _build_maturity_input(tracking, capability)
    result = calculate_maturity(maturity_input)

    return EvidenceMaturityResponse(
        evidence_id=evidence_id,
        level=result.level.value,
        level_name=result.name,
        level_description=result.description,
        score=result.score,
        factors=result.factors,
        upgrade_potential=result.upgrade_potential.value if result.upgrade_potential else None,
        is_tracked=tracking.is_tracked if tracking else False,
        collection_method=capability.collection_method if capability else None,
        frequency=tracking.frequency if tracking else None,
        system_name=system.name if system else None,
    )


# ============================================================================
# UPGRADE RECOMMENDATIONS
# ============================================================================

@router.get(
    "/organizations/{org_id}/evidence/{evidence_id}/upgrade-recommendations",
    response_model=EvidenceUpgradeRecommendationsResponse
)
async def get_evidence_upgrade_recommendations(
    org_id: UUID,
    evidence_id: str,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db)
):
    """
    Get recommendations for improving evidence collection maturity.

    Returns prioritised, actionable steps to move to the next maturity level.
    Recommendations are sorted by impact (high first) then effort (low first).

    Requires: viewer role or higher.
    """
    # Fetch evidence and capability data
    tracking, capability, system = await _get_evidence_with_capability(
        org_id, evidence_id, db
    )

    # Calculate current maturity
    maturity_input = _build_maturity_input(tracking, capability)
    result = calculate_maturity(maturity_input)

    # Get recommendations
    recommendations = get_upgrade_recommendations(
        current_level=result.level,
        collection_method=capability.collection_method if capability else None,
        capability_status=capability.capability_status if capability else None,
    )

    return EvidenceUpgradeRecommendationsResponse(
        evidence_id=evidence_id,
        current_level=result.level.value,
        current_level_name=result.name,
        recommendations=[
            UpgradeRecommendationResponse(
                current_level=rec.current_level.value,
                target_level=rec.target_level.value,
                title=rec.title,
                description=rec.description,
                effort=rec.effort,
                impact=rec.impact,
                steps=rec.steps,
            )
            for rec in recommendations
        ],
    )


# ============================================================================
# ORGANISATION MATURITY SUMMARY
# ============================================================================

@router.get(
    "/organizations/{org_id}/evidence-maturity-summary",
    response_model=OrganisationMaturitySummaryResponse
)
async def get_organisation_maturity_summary(
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db)
):
    """
    Get aggregated maturity statistics for all evidence in an organisation.

    Returns:
    - Distribution of evidence across maturity levels (L0-L5)
    - Average maturity score
    - Automation percentage (L3+ evidence)
    - Top/bottom evidence items by maturity
    - Number of low-effort improvement opportunities

    Requires: viewer role or higher.
    """
    # Get all evidence tracking for this org
    tracking_result = await db.execute(
        select(EvidenceTracking)
        .options(selectinload(EvidenceTracking.system))
        .where(EvidenceTracking.organization_id == org_id)
    )
    all_tracking = {t.evidence_id: t for t in tracking_result.scalars().all()}

    # Get all system capabilities for this org
    cap_result = await db.execute(
        select(SystemEvidenceCapability)
        .join(System, SystemEvidenceCapability.system_id == System.id)
        .options(selectinload(SystemEvidenceCapability.system))
        .where(System.organization_id == org_id)
    )
    all_capabilities = cap_result.scalars().all()

    # Group capabilities by evidence_id (keep best one)
    capabilities_by_evidence = {}
    for cap in all_capabilities:
        evidence_id = cap.evidence_id
        if evidence_id not in capabilities_by_evidence:
            capabilities_by_evidence[evidence_id] = cap
        else:
            # Keep the one with better status
            status_priority = {"active": 3, "configured": 2, "potential": 1}
            current_priority = status_priority.get(capabilities_by_evidence[evidence_id].capability_status, 0)
            new_priority = status_priority.get(cap.capability_status, 0)
            if new_priority > current_priority:
                capabilities_by_evidence[evidence_id] = cap

    # Get all unique evidence IDs
    all_evidence_ids = set(all_tracking.keys()) | set(capabilities_by_evidence.keys())

    if not all_evidence_ids:
        # No evidence data at all
        return OrganisationMaturitySummaryResponse(
            organisation_id=org_id,
            total_evidence=0,
            tracked_evidence=0,
            average_maturity_score=0.0,
            automation_percentage=0.0,
            distribution=[
                MaturityLevelSummary(level=i, name=MATURITY_NAMES[MaturityLevel(i)], count=0, percentage=0.0)
                for i in range(6)
            ],
            lowest_maturity_evidence=[],
            highest_maturity_evidence=[],
            improvement_opportunities=0,
        )

    # Calculate maturity for each evidence item
    distribution = MaturityDistribution()
    evidence_maturity_list: List[tuple[str, MaturityLevel, bool]] = []  # (evidence_id, level, is_low_effort_upgrade)

    for evidence_id in all_evidence_ids:
        tracking = all_tracking.get(evidence_id)
        capability = capabilities_by_evidence.get(evidence_id)

        maturity_input = _build_maturity_input(tracking, capability)
        result = calculate_maturity(maturity_input)

        distribution.increment(result.level)

        # Check if this is a low-effort upgrade opportunity
        is_low_effort = False
        if result.level < MaturityLevel.L4_MANAGED:
            # Low effort if: capability exists but not fully utilised
            if capability and capability.capability_status in ("potential", "configured"):
                is_low_effort = True
            # Or: has tracking but no system linked (easy to link)
            if tracking and tracking.is_tracked and not tracking.system_id and capability:
                is_low_effort = True

        evidence_maturity_list.append((evidence_id, result.level, is_low_effort))

    # Sort by maturity level
    evidence_maturity_list.sort(key=lambda x: x[1])

    # Extract lowest and highest maturity evidence
    lowest_maturity = [e[0] for e in evidence_maturity_list[:5]]
    highest_maturity = [e[0] for e in evidence_maturity_list[-5:][::-1]]

    # Count low-effort improvement opportunities
    improvement_opportunities = sum(1 for e in evidence_maturity_list if e[2])

    # Count tracked evidence
    tracked_count = sum(1 for t in all_tracking.values() if t.is_tracked)

    # Build distribution response
    total = distribution.total
    distribution_response = [
        MaturityLevelSummary(
            level=0,
            name=MATURITY_NAMES[MaturityLevel.L0_NON_EXISTENT],
            count=distribution.l0_count,
            percentage=round(distribution.l0_count / total * 100, 1) if total > 0 else 0.0,
        ),
        MaturityLevelSummary(
            level=1,
            name=MATURITY_NAMES[MaturityLevel.L1_AD_HOC],
            count=distribution.l1_count,
            percentage=round(distribution.l1_count / total * 100, 1) if total > 0 else 0.0,
        ),
        MaturityLevelSummary(
            level=2,
            name=MATURITY_NAMES[MaturityLevel.L2_DEVELOPING],
            count=distribution.l2_count,
            percentage=round(distribution.l2_count / total * 100, 1) if total > 0 else 0.0,
        ),
        MaturityLevelSummary(
            level=3,
            name=MATURITY_NAMES[MaturityLevel.L3_DEFINED],
            count=distribution.l3_count,
            percentage=round(distribution.l3_count / total * 100, 1) if total > 0 else 0.0,
        ),
        MaturityLevelSummary(
            level=4,
            name=MATURITY_NAMES[MaturityLevel.L4_MANAGED],
            count=distribution.l4_count,
            percentage=round(distribution.l4_count / total * 100, 1) if total > 0 else 0.0,
        ),
        MaturityLevelSummary(
            level=5,
            name=MATURITY_NAMES[MaturityLevel.L5_OPTIMISING],
            count=distribution.l5_count,
            percentage=round(distribution.l5_count / total * 100, 1) if total > 0 else 0.0,
        ),
    ]

    return OrganisationMaturitySummaryResponse(
        organisation_id=org_id,
        total_evidence=total,
        tracked_evidence=tracked_count,
        average_maturity_score=distribution.average_score,
        automation_percentage=distribution.automation_percentage,
        distribution=distribution_response,
        lowest_maturity_evidence=lowest_maturity,
        highest_maturity_evidence=highest_maturity,
        improvement_opportunities=improvement_opportunities,
    )
