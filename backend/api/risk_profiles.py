"""
Risk Profile API endpoints.
Handles CRUD operations for per-organisation risk profile configuration.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from uuid import UUID

from database import get_db
from models import OrganizationRiskProfile
from schemas import RiskProfileResponse, RiskProfileUpdate
from auth import require_org_role, OrgMembership

router = APIRouter(tags=["risk-profiles"])

# Default values for reset
DEFAULTS = {
    "low_max": 4,
    "medium_max": 9,
    "high_max": 16,
    "acceptable_risk_level": "medium",
    "auto_escalate_above": "high",
    "required_vendor_certifications": "[]",
    "preferred_vendor_certifications": "[]",
    "vendor_auto_approve_max": 4,
    "vendor_auto_reject_min": 20,
}


async def _get_or_create_profile(
    org_id: UUID, db: AsyncSession
) -> OrganizationRiskProfile:
    """Get the risk profile for an org, auto-creating with defaults if missing."""
    result = await db.execute(
        select(OrganizationRiskProfile).where(
            OrganizationRiskProfile.organization_id == org_id
        )
    )
    profile = result.scalar_one_or_none()

    if not profile:
        profile = OrganizationRiskProfile(organization_id=org_id, **DEFAULTS)
        db.add(profile)
        await db.commit()
        await db.refresh(profile)

    return profile


@router.get(
    "/organizations/{org_id}/risk-profile",
    response_model=RiskProfileResponse,
)
async def get_risk_profile(
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """
    Get the organisation's risk profile.
    Auto-creates with defaults if one does not exist.
    Requires: viewer role or higher.
    """
    profile = await _get_or_create_profile(org_id, db)
    return profile


@router.put(
    "/organizations/{org_id}/risk-profile",
    response_model=RiskProfileResponse,
)
async def update_risk_profile(
    org_id: UUID,
    data: RiskProfileUpdate,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """
    Update the organisation's risk profile.
    Requires: admin role.
    Validates threshold ordering: low_max < medium_max < high_max < 25.
    """
    profile = await _get_or_create_profile(org_id, db)
    current_user = membership.user

    update_data = data.model_dump(exclude_unset=True)

    # Merge current values with updates for cross-field validation
    new_low = update_data.get("low_max", profile.low_max)
    new_med = update_data.get("medium_max", profile.medium_max)
    new_high = update_data.get("high_max", profile.high_max)

    if not (new_low < new_med < new_high < 25):
        raise HTTPException(
            status_code=422,
            detail="Thresholds must satisfy: low_max < medium_max < high_max < 25",
        )

    new_approve = update_data.get("vendor_auto_approve_max", profile.vendor_auto_approve_max)
    new_reject = update_data.get("vendor_auto_reject_min", profile.vendor_auto_reject_min)

    if new_approve >= new_reject:
        raise HTTPException(
            status_code=422,
            detail="vendor_auto_approve_max must be less than vendor_auto_reject_min",
        )

    for key, value in update_data.items():
        setattr(profile, key, value)

    if current_user and current_user.db_id:
        profile.updated_by_user_id = UUID(current_user.db_id)

    await db.commit()
    await db.refresh(profile)
    return profile


@router.post(
    "/organizations/{org_id}/risk-profile/reset",
    response_model=RiskProfileResponse,
)
async def reset_risk_profile(
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """
    Reset the organisation's risk profile to default values.
    Requires: admin role.
    """
    profile = await _get_or_create_profile(org_id, db)
    current_user = membership.user

    for key, value in DEFAULTS.items():
        setattr(profile, key, value)

    if current_user and current_user.db_id:
        profile.updated_by_user_id = UUID(current_user.db_id)

    await db.commit()
    await db.refresh(profile)
    return profile
