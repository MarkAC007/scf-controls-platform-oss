"""
Vendor Service - Business logic for vendor management.

This service handles:
- Vendor count limit checking per subscription tier
- Tier-based access control for vendor creation

Tier Limits:
    - FREE: 5 vendors per organisation
    - PROFESSIONAL: 100 vendors per organisation
    - ENTERPRISE: Unlimited (999)

Note: Limits are checked at creation time only, not on import/migration.
"""
import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from models import Vendor, UserSubscription, OrganizationMember, SubscriptionTier

logger = logging.getLogger(__name__)

# Vendor limits per subscription tier
VENDOR_TIER_LIMITS = {
    SubscriptionTier.FREE.value: 5,
    SubscriptionTier.PROFESSIONAL.value: 100,
    SubscriptionTier.ENTERPRISE.value: 999,
    # Website tier aliases
    "pro": 50,
    "consultant": 100,
    "custom": 999,
}


async def get_vendor_count(org_id: UUID, db: AsyncSession) -> int:
    """
    Get the current number of vendors in an organisation.

    Args:
        org_id: The organisation UUID
        db: Database session

    Returns:
        int: Number of vendors in the organisation
    """
    result = await db.execute(
        select(func.count(Vendor.id)).where(Vendor.organization_id == org_id)
    )
    return result.scalar() or 0


async def check_vendor_limit(org_id: UUID, db: AsyncSession) -> bool:
    """
    Check if the organisation can create another vendor based on the
    admin user's subscription tier.

    Looks up the admin member of the org, finds their subscription,
    and checks against tier limits.

    Args:
        org_id: The organisation UUID
        db: Database session

    Returns:
        bool: True if another vendor can be created, False if limit reached
    """
    # Self-hosted single-tenant deployments bypass subscription tier limits entirely (#657/#662).
    from services.single_tenant import is_single_tenant_active
    if is_single_tenant_active():
        return True

    # Find org admin's subscription
    admin_member = await db.execute(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == org_id,
            OrganizationMember.role == "admin"
        )
    )
    admin = admin_member.scalar_one_or_none()

    if not admin:
        logger.warning(f"No admin found for org {org_id} - denying vendor creation")
        return False

    # Get admin's subscription
    sub_result = await db.execute(
        select(UserSubscription).where(UserSubscription.user_id == admin.user_id)
    )
    subscription = sub_result.scalar_one_or_none()

    if not subscription:
        # No subscription = free tier limits
        tier = SubscriptionTier.FREE.value
    else:
        tier = subscription.tier

    # Get limit for this tier
    max_vendors = VENDOR_TIER_LIMITS.get(tier, VENDOR_TIER_LIMITS[SubscriptionTier.FREE.value])

    # Get current vendor count
    current_count = await get_vendor_count(org_id, db)

    allowed = current_count < max_vendors
    logger.debug(
        f"Vendor limit check: org={org_id}, current={current_count}, "
        f"max={max_vendors}, tier={tier}, allowed={allowed}"
    )
    return allowed
