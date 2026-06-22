"""
Provisioning API endpoints for subscription management.

This API provides:
- GET /subscription: Current user's subscription details
- GET /usage: Current usage vs subscription limits
- POST /sync: Webhook for marketing site to sync subscription changes

SECURITY:
- GET endpoints require JWT authentication (current user)
- POST /sync requires API key authentication only
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from uuid import UUID

from database import get_db
from auth import require_auth, User
from rate_limiting import rate_limit_provisioning_sync
from models import (
    User as DBUser,
    Organization,
    OrganizationMember,
    UserSubscription,
    SubscriptionTier,
    ScopedControl,
    ConsultantProfile,
    ConsultantInvite,
    ConsultantInviteStatus,
    ConsultantClientRelationship,
    ConsultantClientStatus,
    EvidenceTracking,
    System,
    Vendor,
)
from services.subscription import get_user_subscription, get_tier_limits
from services.org_utils import generate_unique_slug
from schemas import (
    SubscriptionResponse,
    UsageResponse,
    UsageLimit,
    SyncRequest,
    SyncResponse,
    AccountDeletionRequest,
    DeleteUserResponse,
    DeletionPreviewResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/provisioning",
    tags=["provisioning"]
)


# =============================================================================
# GET /subscription - Current user's subscription
# =============================================================================

@router.get("/subscription", response_model=SubscriptionResponse)
async def get_subscription(
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """
    Get the current user's subscription details.

    Returns the subscription tier, limits, and Stripe references.
    If the user has no subscription, a free tier is auto-created.
    """
    subscription = await get_user_subscription(UUID(user.db_id), db)

    return SubscriptionResponse(
        tier=subscription.tier,
        max_organisations=subscription.max_organisations,
        max_team_members=subscription.max_team_members,
        is_active=subscription.is_active,
        stripe_customer_id=subscription.stripe_customer_id,
        stripe_subscription_id=subscription.stripe_subscription_id,
        created_at=subscription.created_at,
        updated_at=subscription.updated_at,
    )


# =============================================================================
# GET /usage - Current usage vs limits
# =============================================================================

@router.get("/usage", response_model=UsageResponse)
async def get_usage(
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """
    Get current usage statistics compared to subscription limits.

    Returns counts of:
    - Organisations: Where user is an admin (owner)
    - Team members: Total across all organisations user owns
    """
    user_uuid = UUID(user.db_id)

    # Get subscription
    subscription = await get_user_subscription(user_uuid, db)

    # Count organisations where user is admin
    org_count_result = await db.execute(
        select(func.count(OrganizationMember.id))
        .where(OrganizationMember.user_id == user_uuid)
        .where(OrganizationMember.role == "admin")
    )
    org_count = org_count_result.scalar() or 0

    # Get all organisations where user is admin
    org_ids_result = await db.execute(
        select(OrganizationMember.organization_id)
        .where(OrganizationMember.user_id == user_uuid)
        .where(OrganizationMember.role == "admin")
    )
    org_ids = [row[0] for row in org_ids_result.all()]

    # Count total team members across all owned organisations
    team_member_count = 0
    if org_ids:
        member_count_result = await db.execute(
            select(func.count(OrganizationMember.id))
            .where(OrganizationMember.organization_id.in_(org_ids))
        )
        team_member_count = member_count_result.scalar() or 0

    return UsageResponse(
        organisations=UsageLimit(current=org_count, max=subscription.max_organisations),
        team_members=UsageLimit(current=team_member_count, max=subscription.max_team_members),
        tier=subscription.tier,
    )


# =============================================================================
# POST /sync - Webhook from marketing site
# =============================================================================

@router.post("/sync", response_model=SyncResponse)
@rate_limit_provisioning_sync
async def sync_subscription(
    request: Request,
    response: Response,
    sync_data: SyncRequest,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """
    Sync subscription changes from the marketing site.

    SECURITY: This endpoint requires API key authentication only.
    It's designed to be called by the marketing site webhook when a user
    upgrades/downgrades their subscription.

    If the user doesn't have a subscription, one is created.
    If the user already has a subscription, it's updated.
    """
    # Verify API key authentication
    if user.auth_method != "api_key":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This endpoint requires API key authentication"
        )

    # Find user by email
    result = await db.execute(
        select(DBUser).where(DBUser.email == sync_data.user_email)
    )
    db_user = result.scalar_one_or_none()

    # If user doesn't exist, create them (new signup from marketing site)
    if not db_user:
        # Create a new user with the sync data
        # They'll link to Google OAuth when they first log into the platform
        display_name = sync_data.name or sync_data.user_email.split('@')[0]
        db_user = DBUser(
            email=sync_data.user_email,
            display_name=display_name,
            # google_sub will be populated when user first logs in via OAuth
            google_sub=f"pending:{sync_data.user_email}",
        )
        db.add(db_user)
        await db.flush()  # Get the ID without committing
        logger.info(f"Created new user {db_user.id} from marketing site sync: {sync_data.user_email}")

    # Get tier limits
    try:
        tier_limits = get_tier_limits(sync_data.tier)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

    # Check for existing subscription
    result = await db.execute(
        select(UserSubscription).where(UserSubscription.user_id == db_user.id)
    )
    subscription = result.scalar_one_or_none()

    if subscription:
        # Update existing subscription
        subscription.tier = sync_data.tier
        subscription.max_organisations = tier_limits["max_organisations"]
        subscription.max_team_members = tier_limits["max_team_members"]
        subscription.stripe_customer_id = sync_data.stripe_customer_id
        subscription.stripe_subscription_id = sync_data.stripe_subscription_id
        subscription.is_active = sync_data.status != 'canceled'
        message = "Subscription updated"
        logger.info(f"Updated subscription for user {db_user.id}: tier={sync_data.tier}")
    else:
        # Create new subscription
        subscription = UserSubscription(
            user_id=db_user.id,
            tier=sync_data.tier,
            max_organisations=tier_limits["max_organisations"],
            max_team_members=tier_limits["max_team_members"],
            is_active=sync_data.status != 'canceled',
            stripe_customer_id=sync_data.stripe_customer_id,
            stripe_subscription_id=sync_data.stripe_subscription_id,
        )
        db.add(subscription)
        message = "Subscription created"
        logger.info(f"Created subscription for user {db_user.id}: tier={sync_data.tier}")

    # Auto-provision/deactivate consultant profile based on tier
    existing_profile_result = await db.execute(
        select(ConsultantProfile).where(ConsultantProfile.user_id == db_user.id)
    )
    consultant_profile = existing_profile_result.scalar_one_or_none()

    if sync_data.tier == "consultant":
        if not consultant_profile:
            consultant_profile = ConsultantProfile(
                user_id=db_user.id,
                is_active=True,
                max_clients=sync_data.max_clients or 5,
            )
            db.add(consultant_profile)
            logger.info(f"Auto-created consultant profile for user {db_user.id}")
        elif not consultant_profile.is_active:
            consultant_profile.is_active = True
            consultant_profile.max_clients = sync_data.max_clients or 5
            logger.info(f"Re-activated consultant profile for user {db_user.id}")
    else:
        # Downgrade: deactivate consultant profile if it exists and is active
        if consultant_profile and consultant_profile.is_active:
            consultant_profile.is_active = False
            logger.info(f"Deactivated consultant profile for user {db_user.id} (tier changed to {sync_data.tier})")

            # Phase 3: Cascade - cancel pending invites for this consultant
            pending_invites_result = await db.execute(
                select(ConsultantInvite)
                .where(ConsultantInvite.consultant_id == consultant_profile.id)
                .where(ConsultantInvite.status == ConsultantInviteStatus.PENDING.value)
            )
            pending_invites = pending_invites_result.scalars().all()
            cancelled_count = 0
            for invite in pending_invites:
                invite.status = ConsultantInviteStatus.CANCELLED.value
                cancelled_count += 1
            if cancelled_count:
                logger.info(f"Cancelled {cancelled_count} pending invites for consultant {consultant_profile.id}")

            # Archive vacant orgs (awaiting_admin=True, created by this consultant)
            vacant_orgs_result = await db.execute(
                select(Organization)
                .where(Organization.awaiting_admin == True)
                .where(Organization.created_by_consultant_id == consultant_profile.id)
            )
            vacant_orgs = vacant_orgs_result.scalars().all()
            archived_count = 0
            for org in vacant_orgs:
                org.awaiting_admin = False  # Clear the flag
                archived_count += 1
            if archived_count:
                logger.info(f"Archived {archived_count} vacant orgs for consultant {consultant_profile.id}")

            # Suspend active relationships
            active_rels_result = await db.execute(
                select(ConsultantClientRelationship)
                .where(ConsultantClientRelationship.consultant_id == consultant_profile.id)
                .where(ConsultantClientRelationship.status == ConsultantClientStatus.ACTIVE.value)
            )
            active_rels = active_rels_result.scalars().all()
            suspended_count = 0
            for rel in active_rels:
                rel.status = ConsultantClientStatus.SUSPENDED.value
                suspended_count += 1
            if suspended_count:
                logger.info(f"Suspended {suspended_count} active relationships for consultant {consultant_profile.id}")

    # Validate evidence storage is configured before provisioning org
    from services.storage_service import is_configured as storage_configured, get_backend
    if not storage_configured():
        logger.error(
            "Evidence storage not configured — cannot provision org for user %s. "
            "Set EVIDENCE_BUCKET (AWS) or AZURE_STORAGE_ACCOUNT_NAME + "
            "AZURE_STORAGE_ACCOUNT_KEY (Azure).",
            db_user.id,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "message": "Evidence storage is not configured on this platform instance. "
                           "Please contact the platform administrator.",
                "storage_backend": get_backend(),
            },
        )

    # Phase 1: Auto-provision organisation for all non-enterprise tiers
    if sync_data.tier in ("free", "pro", "professional", "consultant"):
        # Check if user already has an admin membership (idempotency guard)
        existing_admin_result = await db.execute(
            select(OrganizationMember.id)
            .where(OrganizationMember.user_id == db_user.id)
            .where(OrganizationMember.role == "admin")
            .limit(1)
        )
        if not existing_admin_result.scalar_one_or_none():
            # Generate org name from email domain (e.g. "compliancegenie.io" -> "Compliancegenie")
            # Falls back to "My Organisation" for generic domains
            email_domain = db_user.email.split('@')[-1].split('.')[0]
            generic_domains = {"gmail", "yahoo", "hotmail", "outlook", "icloud", "protonmail", "aol", "live"}
            if email_domain.lower() in generic_domains:
                org_name = "My Organisation"
            else:
                org_name = email_domain.replace('-', ' ').replace('_', ' ').title()
            slug = await generate_unique_slug(org_name, db)

            org = Organization(
                name=org_name,
                slug=slug,
            )
            db.add(org)
            await db.flush()

            membership = OrganizationMember(
                organization_id=org.id,
                user_id=db_user.id,
                role="admin",
            )
            db.add(membership)
            logger.info(f"Auto-provisioned org '{org_name}' (slug={slug}) for user {db_user.id}")

    await db.commit()

    return SyncResponse(
        status="synced",
        user_id=str(db_user.id),
        tier=sync_data.tier,
        message=message,
    )


# =============================================================================
# GET /deletion-preview - Preview account deletion impact
# =============================================================================

@router.get("/deletion-preview", response_model=DeletionPreviewResponse)
async def deletion_preview(
    email: str = Query(..., min_length=5, max_length=255, description="Email of user to preview deletion for"),
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Preview the impact of deleting a user account.

    SECURITY: This endpoint requires API key authentication only.
    Returns a breakdown of organisations, controls, evidence, systems, and
    vendors that would be affected by deleting the user.
    """
    if user.auth_method != "api_key":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This endpoint requires API key authentication",
        )

    # Find user by email
    result = await db.execute(
        select(DBUser).where(DBUser.email == email.lower().strip())
    )
    db_user = result.scalar_one_or_none()
    if not db_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User not found: {email}",
        )

    # Find all orgs where user is admin
    admin_memberships_result = await db.execute(
        select(OrganizationMember)
        .where(OrganizationMember.user_id == db_user.id)
        .where(OrganizationMember.role == "admin")
    )
    admin_memberships = admin_memberships_result.scalars().all()

    sole_admin_orgs = []
    shared_orgs = []

    for membership in admin_memberships:
        org_id = membership.organization_id

        # Get org details
        org_result = await db.execute(
            select(Organization).where(Organization.id == org_id)
        )
        org = org_result.scalar_one_or_none()
        if not org:
            continue

        # Check if there are other admins
        other_admins_result = await db.execute(
            select(func.count(OrganizationMember.id))
            .where(OrganizationMember.organization_id == org_id)
            .where(OrganizationMember.role == "admin")
            .where(OrganizationMember.user_id != db_user.id)
        )
        other_admin_count = other_admins_result.scalar() or 0

        org_info = {"id": str(org.id), "name": org.name}

        if other_admin_count == 0:
            sole_admin_orgs.append(org_info)
        else:
            shared_orgs.append(org_info)

    # Also find non-admin memberships (will just be removed)
    non_admin_memberships_result = await db.execute(
        select(OrganizationMember)
        .where(OrganizationMember.user_id == db_user.id)
        .where(OrganizationMember.role != "admin")
    )
    non_admin_memberships = non_admin_memberships_result.scalars().all()
    for membership in non_admin_memberships:
        org_result = await db.execute(
            select(Organization).where(Organization.id == membership.organization_id)
        )
        org = org_result.scalar_one_or_none()
        if org:
            shared_orgs.append({"id": str(org.id), "name": org.name})

    # Count resources across sole-admin orgs
    sole_admin_org_ids = [UUID(o["id"]) for o in sole_admin_orgs]

    total_scoped_controls = 0
    total_evidence = 0
    total_systems = 0
    total_vendors = 0

    if sole_admin_org_ids:
        controls_result = await db.execute(
            select(func.count(ScopedControl.id))
            .where(ScopedControl.organization_id.in_(sole_admin_org_ids))
        )
        total_scoped_controls = controls_result.scalar() or 0

        evidence_result = await db.execute(
            select(func.count(EvidenceTracking.id))
            .where(EvidenceTracking.organization_id.in_(sole_admin_org_ids))
        )
        total_evidence = evidence_result.scalar() or 0

        systems_result = await db.execute(
            select(func.count(System.id))
            .where(System.organization_id.in_(sole_admin_org_ids))
        )
        total_systems = systems_result.scalar() or 0

        vendors_result = await db.execute(
            select(func.count(Vendor.id))
            .where(Vendor.organization_id.in_(sole_admin_org_ids))
        )
        total_vendors = vendors_result.scalar() or 0

    return DeletionPreviewResponse(
        email=db_user.email,
        sole_admin_orgs=sole_admin_orgs,
        shared_orgs=shared_orgs,
        total_scoped_controls=total_scoped_controls,
        total_evidence=total_evidence,
        total_systems=total_systems,
        total_vendors=total_vendors,
    )


# =============================================================================
# DELETE /user - Delete user account and cascade
# =============================================================================

@router.delete("/user", response_model=DeleteUserResponse)
async def delete_user(
    delete_data: AccountDeletionRequest,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Delete a user account and cascade to sole-admin organisations.

    SECURITY: This endpoint requires API key authentication only.

    For orgs where the user is the sole admin, the Organisation is deleted
    (SQLAlchemy CASCADE handles related data). For shared orgs, only the
    user's membership is removed. The user's subscription and user record
    are also deleted.
    """
    if user.auth_method != "api_key":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This endpoint requires API key authentication",
        )

    # Find user by email
    result = await db.execute(
        select(DBUser).where(DBUser.email == delete_data.email)
    )
    db_user = result.scalar_one_or_none()
    if not db_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User not found: {delete_data.email}",
        )

    try:
        orgs_deleted = 0
        memberships_removed = 0

        # Find all memberships
        memberships_result = await db.execute(
            select(OrganizationMember)
            .where(OrganizationMember.user_id == db_user.id)
        )
        memberships = memberships_result.scalars().all()

        for membership in memberships:
            org_id = membership.organization_id

            if membership.role == "admin":
                # Check if sole admin
                other_admins_result = await db.execute(
                    select(func.count(OrganizationMember.id))
                    .where(OrganizationMember.organization_id == org_id)
                    .where(OrganizationMember.role == "admin")
                    .where(OrganizationMember.user_id != db_user.id)
                )
                other_admin_count = other_admins_result.scalar() or 0

                if other_admin_count == 0:
                    # Sole admin — delete the organisation (cascade handles related data)
                    org_result = await db.execute(
                        select(Organization).where(Organization.id == org_id)
                    )
                    org = org_result.scalar_one_or_none()
                    if org:
                        await db.delete(org)
                        orgs_deleted += 1
                        logger.info(f"Deleted sole-admin org {org_id} for user deletion {db_user.email}")
                else:
                    # Shared org — remove membership only
                    await db.delete(membership)
                    memberships_removed += 1
                    logger.info(f"Removed admin membership from org {org_id} for user deletion {db_user.email}")
            else:
                # Non-admin — remove membership only
                await db.delete(membership)
                memberships_removed += 1
                logger.info(f"Removed membership from org {org_id} for user deletion {db_user.email}")

        # Delete user subscription
        sub_result = await db.execute(
            select(UserSubscription).where(UserSubscription.user_id == db_user.id)
        )
        subscription = sub_result.scalar_one_or_none()
        if subscription:
            await db.delete(subscription)

        # Delete user record
        await db.delete(db_user)

        await db.commit()

        logger.info(
            f"Account deletion complete for {db_user.email}: "
            f"{orgs_deleted} orgs deleted, {memberships_removed} memberships removed"
        )

        return DeleteUserResponse(
            status="deleted",
            orgs_deleted=orgs_deleted,
            memberships_removed=memberships_removed,
            message=f"Account {delete_data.email} deleted successfully",
        )

    except Exception as e:
        await db.rollback()
        logger.error(f"Account deletion failed for {delete_data.email}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Account deletion failed: {str(e)}",
        )
