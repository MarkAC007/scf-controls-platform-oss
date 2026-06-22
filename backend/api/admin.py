"""
Platform Admin API endpoints.

Provides REST API access for platform-level administrative operations.
All endpoints require platform admin privileges (is_platform_admin=true).

SECURITY:
- All endpoints protected by require_platform_admin dependency
- API key authentication also grants platform admin access
- Actions are logged for audit purposes
"""
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import Optional
from uuid import UUID
from datetime import datetime, timedelta
import logging

from database import get_db
from models import (
    User as DBUser,
    Organization,
    OrganizationMember,
    ScopedControl,
    EvidenceTracking,
    ConsultantProfile,
)
from schemas import (
    PlatformUserResponse,
    PlatformUserListResponse,
    PlatformOrganizationResponse,
    PlatformOrganizationListResponse,
    GrantPlatformAdminRequest,
    RevokePlatformAdminRequest,
    PlatformAdminActionResponse,
    DeleteUserRequest,
    DeleteOrganizationRequest,
    PlatformStatsResponse,
    SuccessResponse,
    GrantConsultantRequest,
    ConsultantAdminActionResponse,
)
from auth import require_platform_admin, User

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin",
    tags=["platform-admin"]
)


# =============================================================================
# User Management
# =============================================================================

@router.get("/users", response_model=PlatformUserListResponse)
async def list_all_users(
    request: Request,
    skip: int = 0,
    limit: int = 100,
    admins_only: bool = False,
    search: Optional[str] = None,
    user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    List all users in the platform.

    SECURITY: Requires platform admin privileges.

    Args:
        skip: Number of records to skip (pagination)
        limit: Maximum records to return (max 100)
        admins_only: Only return platform admins
        search: Search by email or display name
    """
    logger.info(f"Platform admin {user.email} listing users (skip={skip}, limit={limit})")

    query = select(DBUser)

    # Apply filters
    if admins_only:
        query = query.where(DBUser.is_platform_admin == True)  # noqa: E712

    if search:
        search_filter = f"%{search}%"
        query = query.where(
            (DBUser.email.ilike(search_filter)) |
            (DBUser.display_name.ilike(search_filter))
        )

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Apply pagination and ordering
    query = query.order_by(DBUser.created_at.desc()).offset(skip).limit(min(limit, 100))
    result = await db.execute(query)
    users = result.scalars().all()

    # Build response with organisation counts
    user_responses = []
    for db_user in users:
        # Count organisation memberships
        org_count_result = await db.execute(
            select(func.count(OrganizationMember.id)).where(
                OrganizationMember.user_id == db_user.id
            )
        )
        org_count = org_count_result.scalar() or 0

        user_responses.append(PlatformUserResponse(
            id=db_user.id,
            email=db_user.email,
            display_name=db_user.display_name,
            google_sub=db_user.google_sub,
            is_platform_admin=db_user.is_platform_admin,
            created_at=db_user.created_at,
            last_login_at=db_user.last_login_at,
            email_notifications_enabled=db_user.email_notifications_enabled,
            notification_frequency=db_user.notification_frequency,
            organization_count=org_count
        ))

    return PlatformUserListResponse(total=total, users=user_responses)


@router.get("/users/{user_id}", response_model=PlatformUserResponse)
async def get_user(
    request: Request,
    user_id: UUID,
    user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    Get a specific user by ID.

    SECURITY: Requires platform admin privileges.
    """
    result = await db.execute(select(DBUser).where(DBUser.id == user_id))
    db_user = result.scalar_one_or_none()

    if not db_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    # Count organisation memberships
    org_count_result = await db.execute(
        select(func.count(OrganizationMember.id)).where(
            OrganizationMember.user_id == db_user.id
        )
    )
    org_count = org_count_result.scalar() or 0

    return PlatformUserResponse(
        id=db_user.id,
        email=db_user.email,
        display_name=db_user.display_name,
        google_sub=db_user.google_sub,
        is_platform_admin=db_user.is_platform_admin,
        created_at=db_user.created_at,
        last_login_at=db_user.last_login_at,
        email_notifications_enabled=db_user.email_notifications_enabled,
        notification_frequency=db_user.notification_frequency,
        organization_count=org_count
    )


@router.post("/users/grant-admin", response_model=PlatformAdminActionResponse)
async def grant_platform_admin(
    request: Request,
    grant_request: GrantPlatformAdminRequest,
    user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    Grant platform admin privileges to a user.

    SECURITY: Requires platform admin privileges.
    """
    result = await db.execute(select(DBUser).where(DBUser.id == grant_request.user_id))
    target_user = result.scalar_one_or_none()

    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    if target_user.is_platform_admin:
        return PlatformAdminActionResponse(
            success=True,
            message=f"User {target_user.email} is already a platform admin",
            user_id=target_user.id,
            is_platform_admin=True
        )

    target_user.is_platform_admin = True
    await db.commit()

    logger.info(f"Platform admin {user.email} granted admin to {target_user.email}")

    return PlatformAdminActionResponse(
        success=True,
        message=f"Successfully granted platform admin to {target_user.email}",
        user_id=target_user.id,
        is_platform_admin=True
    )


@router.post("/users/revoke-admin", response_model=PlatformAdminActionResponse)
async def revoke_platform_admin(
    request: Request,
    revoke_request: RevokePlatformAdminRequest,
    user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    Revoke platform admin privileges from a user.

    SECURITY: Requires platform admin privileges.

    Note: Users cannot revoke their own admin privileges via API
    (use CLI for self-demotion to prevent accidental lockout).
    """
    result = await db.execute(select(DBUser).where(DBUser.id == revoke_request.user_id))
    target_user = result.scalar_one_or_none()

    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    # Prevent self-demotion via API
    if user.db_id and str(revoke_request.user_id) == user.db_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot revoke your own platform admin privileges via API. Use CLI for self-demotion."
        )

    if not target_user.is_platform_admin:
        return PlatformAdminActionResponse(
            success=True,
            message=f"User {target_user.email} is not a platform admin",
            user_id=target_user.id,
            is_platform_admin=False
        )

    target_user.is_platform_admin = False
    await db.commit()

    logger.info(f"Platform admin {user.email} revoked admin from {target_user.email}")

    return PlatformAdminActionResponse(
        success=True,
        message=f"Successfully revoked platform admin from {target_user.email}",
        user_id=target_user.id,
        is_platform_admin=False
    )


@router.delete("/users/{user_id}", response_model=SuccessResponse)
async def delete_user(
    request: Request,
    user_id: UUID,
    confirm: bool = False,
    user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    Delete a user and all their associated data.

    SECURITY: Requires platform admin privileges and explicit confirmation.

    Warning: This action is irreversible. All user data including
    memberships, assignments, comments, and notifications will be deleted.
    """
    if not confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Deletion requires confirm=true parameter"
        )

    result = await db.execute(select(DBUser).where(DBUser.id == user_id))
    target_user = result.scalar_one_or_none()

    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    # Prevent self-deletion
    if user.db_id and str(user_id) == user.db_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account"
        )

    email = target_user.email
    await db.delete(target_user)
    await db.commit()

    logger.warning(f"Platform admin {user.email} deleted user {email}")

    return SuccessResponse(message=f"Successfully deleted user {email}")


# =============================================================================
# Consultant Management
# =============================================================================

@router.post("/users/{user_id}/grant-consultant", response_model=ConsultantAdminActionResponse)
async def grant_consultant(
    request: Request,
    user_id: UUID,
    body: Optional[GrantConsultantRequest] = None,
    user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    Grant consultant access to a user by creating or activating their ConsultantProfile.

    SECURITY: Requires platform admin privileges.

    Args:
        user_id: Target user's UUID
        body: Optional company_name and max_clients
    """
    result = await db.execute(select(DBUser).where(DBUser.id == user_id))
    target_user = result.scalar_one_or_none()

    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    # Check for existing consultant profile
    profile_result = await db.execute(
        select(ConsultantProfile).where(ConsultantProfile.user_id == user_id)
    )
    profile = profile_result.scalar_one_or_none()

    company_name = body.company_name if body else None
    max_clients = body.max_clients if body else 5

    if profile:
        if profile.is_active:
            return ConsultantAdminActionResponse(
                success=True,
                message=f"User {target_user.email} is already an active consultant",
                user_id=target_user.id,
                is_consultant=True
            )
        # Re-activate
        profile.is_active = True
        profile.max_clients = max_clients
        if company_name:
            profile.company_name = company_name
        message = f"Re-activated consultant profile for {target_user.email}"
    else:
        # Create new profile
        profile = ConsultantProfile(
            user_id=user_id,
            is_active=True,
            max_clients=max_clients,
            company_name=company_name,
        )
        db.add(profile)
        message = f"Created consultant profile for {target_user.email}"

    await db.commit()
    logger.info(f"Platform admin {user.email} granted consultant to {target_user.email}")

    return ConsultantAdminActionResponse(
        success=True,
        message=message,
        user_id=target_user.id,
        is_consultant=True
    )


@router.post("/users/{user_id}/revoke-consultant", response_model=ConsultantAdminActionResponse)
async def revoke_consultant(
    request: Request,
    user_id: UUID,
    user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    Revoke consultant access from a user by deactivating their ConsultantProfile.

    SECURITY: Requires platform admin privileges.

    Note: This deactivates the profile rather than deleting it,
    preserving client relationship history.
    """
    result = await db.execute(select(DBUser).where(DBUser.id == user_id))
    target_user = result.scalar_one_or_none()

    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    profile_result = await db.execute(
        select(ConsultantProfile).where(ConsultantProfile.user_id == user_id)
    )
    profile = profile_result.scalar_one_or_none()

    if not profile or not profile.is_active:
        return ConsultantAdminActionResponse(
            success=True,
            message=f"User {target_user.email} does not have an active consultant profile",
            user_id=target_user.id,
            is_consultant=False
        )

    profile.is_active = False
    await db.commit()

    logger.info(f"Platform admin {user.email} revoked consultant from {target_user.email}")

    return ConsultantAdminActionResponse(
        success=True,
        message=f"Deactivated consultant profile for {target_user.email}",
        user_id=target_user.id,
        is_consultant=False
    )


# =============================================================================
# Organisation Management
# =============================================================================

@router.get("/organizations", response_model=PlatformOrganizationListResponse)
async def list_all_organizations(
    request: Request,
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    List all organisations in the platform.

    SECURITY: Requires platform admin privileges.
    """
    logger.info(f"Platform admin {user.email} listing organisations")

    query = select(Organization)

    if search:
        search_filter = f"%{search}%"
        query = query.where(
            (Organization.name.ilike(search_filter)) |
            (Organization.slug.ilike(search_filter))
        )

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Apply pagination
    query = query.order_by(Organization.created_at.desc()).offset(skip).limit(min(limit, 100))
    result = await db.execute(query)
    orgs = result.scalars().all()

    # Build response with counts
    org_responses = []
    for org in orgs:
        # Count members
        member_count_result = await db.execute(
            select(func.count(OrganizationMember.id)).where(
                OrganizationMember.organization_id == org.id
            )
        )
        member_count = member_count_result.scalar() or 0

        # Count controls
        control_count_result = await db.execute(
            select(func.count(ScopedControl.id)).where(
                ScopedControl.organization_id == org.id
            )
        )
        control_count = control_count_result.scalar() or 0

        org_responses.append(PlatformOrganizationResponse(
            id=org.id,
            name=org.name,
            slug=org.slug,
            created_at=org.created_at,
            updated_at=org.updated_at,
            member_count=member_count,
            control_count=control_count
        ))

    return PlatformOrganizationListResponse(total=total, organizations=org_responses)


@router.get("/organizations/{org_id}", response_model=PlatformOrganizationResponse)
async def get_organization(
    request: Request,
    org_id: UUID,
    user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    Get a specific organisation by ID.

    SECURITY: Requires platform admin privileges.
    """
    result = await db.execute(select(Organization).where(Organization.id == org_id))
    org = result.scalar_one_or_none()

    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organisation not found"
        )

    # Count members
    member_count_result = await db.execute(
        select(func.count(OrganizationMember.id)).where(
            OrganizationMember.organization_id == org.id
        )
    )
    member_count = member_count_result.scalar() or 0

    # Count controls
    control_count_result = await db.execute(
        select(func.count(ScopedControl.id)).where(
            ScopedControl.organization_id == org.id
        )
    )
    control_count = control_count_result.scalar() or 0

    return PlatformOrganizationResponse(
        id=org.id,
        name=org.name,
        slug=org.slug,
        created_at=org.created_at,
        updated_at=org.updated_at,
        member_count=member_count,
        control_count=control_count
    )


@router.delete("/organizations/{org_id}", response_model=SuccessResponse)
async def delete_organization(
    request: Request,
    org_id: UUID,
    confirm: bool = False,
    user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    Delete an organisation and all its associated data.

    SECURITY: Requires platform admin privileges and explicit confirmation.

    Warning: This action is irreversible. All organisation data including
    controls, evidence tracking, memberships, and related records will be deleted.
    """
    if not confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Deletion requires confirm=true parameter"
        )

    result = await db.execute(select(Organization).where(Organization.id == org_id))
    org = result.scalar_one_or_none()

    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organisation not found"
        )

    name = org.name
    await db.delete(org)
    await db.commit()

    logger.warning(f"Platform admin {user.email} deleted organisation {name}")

    return SuccessResponse(message=f"Successfully deleted organisation {name}")


# =============================================================================
# Platform Statistics
# =============================================================================

@router.get("/stats", response_model=PlatformStatsResponse)
async def get_platform_stats(
    request: Request,
    user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    Get platform-wide statistics.

    SECURITY: Requires platform admin privileges.
    """
    logger.info(f"Platform admin {user.email} retrieving platform stats")

    # Total users
    total_users_result = await db.execute(select(func.count(DBUser.id)))
    total_users = total_users_result.scalar() or 0

    # Platform admins
    platform_admins_result = await db.execute(
        select(func.count(DBUser.id)).where(DBUser.is_platform_admin == True)  # noqa: E712
    )
    platform_admins = platform_admins_result.scalar() or 0

    # Total organisations
    total_orgs_result = await db.execute(select(func.count(Organization.id)))
    total_orgs = total_orgs_result.scalar() or 0

    # Total controls
    total_controls_result = await db.execute(select(func.count(ScopedControl.id)))
    total_controls = total_controls_result.scalar() or 0

    # Total evidence
    total_evidence_result = await db.execute(select(func.count(EvidenceTracking.id)))
    total_evidence = total_evidence_result.scalar() or 0

    # Users active in last 30 days
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    active_users_result = await db.execute(
        select(func.count(DBUser.id)).where(DBUser.last_login_at >= thirty_days_ago)
    )
    users_last_30_days = active_users_result.scalar() or 0

    # Orgs created in last 30 days
    new_orgs_result = await db.execute(
        select(func.count(Organization.id)).where(Organization.created_at >= thirty_days_ago)
    )
    orgs_last_30_days = new_orgs_result.scalar() or 0

    return PlatformStatsResponse(
        total_users=total_users,
        platform_admins=platform_admins,
        total_organizations=total_orgs,
        total_controls=total_controls,
        total_evidence=total_evidence,
        users_last_30_days=users_last_30_days,
        orgs_last_30_days=orgs_last_30_days
    )
