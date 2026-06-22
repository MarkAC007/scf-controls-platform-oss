"""
Users API endpoints - manage organization members.

SUBSCRIPTION: Team member limits
- Member invitations are governed by the organisation owner's subscription
- Owner is defined as the first admin member (by joined_at timestamp)
- Limits include: owner (1) + active members + pending invitations
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import List, Optional
from uuid import UUID
import logging

from sqlalchemy.orm import joinedload

from database import get_db
from auth import require_auth, require_org_role, OrgMembership, User
from models import User as DBUser, OrganizationMember, Organization, OrganizationInvite, ApiKey
from schemas import (
    OrganizationMemberCreate,
    OrganizationMemberResponse,
    UserResponse,
    UserSubscriptionInfo,
    SuccessResponse,
    InviteUserRequest,
    InviteUserResponse,
    OrgInviteCreate,
    OrgInviteResponse,
    OrgInvitePreviewResponse,
    AcceptOrgInviteResponse,
    OrgInviteListResponse,
)
from services.audit_service import log_entity_changes, detect_action_source, get_request_id, ORG_MEMBER_TRACKED_FIELDS
from services.email_service import send_invitation_email
from services.subscription import get_user_subscription, can_invite_member
from services import org_invite as org_invite_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["users"])


@router.get("/api/organizations/{org_id}/members", response_model=List[OrganizationMemberResponse])
async def list_organization_members(
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db)
):
    """
    List all members of an organization.
    Requires: viewer role or higher.
    """
    # Organization existence verified by require_org_role

    # Get all members with user data in a single query (N+1 fix)
    result = await db.execute(
        select(OrganizationMember)
        .where(OrganizationMember.organization_id == org_id)
        .options(joinedload(OrganizationMember.user))
        .order_by(OrganizationMember.joined_at.desc())
    )
    members = result.unique().scalars().all()

    member_list = []
    for member in members:
        user = member.user
        member_dict = {
            "id": member.id,
            "organization_id": member.organization_id,
            "user_id": member.user_id,
            "role": member.role,
            "joined_at": member.joined_at,
            "user": {
                "id": user.id,
                "email": user.email,
                "display_name": user.display_name
            } if user else None
        }
        member_list.append(member_dict)

    return member_list


@router.post("/api/organizations/{org_id}/members", response_model=OrganizationMemberResponse, status_code=status.HTTP_201_CREATED)
async def add_organization_member(
    org_id: UUID,
    member_data: OrganizationMemberCreate,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db)
):
    """
    Add a member to an organization.
    Requires: admin role.
    """
    # Organization existence verified by require_org_role

    # Verify user exists
    result = await db.execute(select(DBUser).where(DBUser.id == member_data.user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if already a member
    result = await db.execute(
        select(OrganizationMember).where(
            (OrganizationMember.organization_id == org_id) &
            (OrganizationMember.user_id == member_data.user_id)
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="User is already a member of this organization")

    # Create membership
    member = OrganizationMember(
        organization_id=org_id,
        user_id=member_data.user_id,
        role=member_data.role
    )
    db.add(member)
    await db.commit()
    await db.refresh(member)

    # Return with user data
    return {
        "id": member.id,
        "organization_id": member.organization_id,
        "user_id": member.user_id,
        "role": member.role,
        "joined_at": member.joined_at,
        "user": {
            "id": user.id,
            "email": user.email,
            "display_name": user.display_name
        }
    }


@router.patch("/api/organizations/{org_id}/members/{user_id}", response_model=OrganizationMemberResponse)
async def update_member_role(
    org_id: UUID,
    user_id: UUID,
    role: str,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db)
):
    """
    Update a member's role in an organization.
    Requires: admin role.
    """
    # Validate role
    if role not in ['admin', 'editor', 'viewer']:
        raise HTTPException(status_code=400, detail="Invalid role. Must be admin, editor, or viewer")

    # Get membership
    result = await db.execute(
        select(OrganizationMember).where(
            (OrganizationMember.organization_id == org_id) &
            (OrganizationMember.user_id == user_id)
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    # Capture old values for audit trail
    old_values = {f: getattr(member, f) for f in ORG_MEMBER_TRACKED_FIELDS if hasattr(member, f)}

    # Update role
    member.role = role

    # Capture new values and log changes
    new_values = {f: getattr(member, f) for f in ORG_MEMBER_TRACKED_FIELDS if hasattr(member, f)}
    await log_entity_changes(
        db=db, organization_id=org_id, entity_type='org_member',
        entity_id=member.id, action='update',
        changed_by_user_id=UUID(membership.user.db_id) if membership.user and membership.user.db_id else None,
        old_values=old_values, new_values=new_values,
        tracked_fields=ORG_MEMBER_TRACKED_FIELDS,
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )

    await db.commit()
    await db.refresh(member)

    # Get user data
    user_result = await db.execute(select(DBUser).where(DBUser.id == user_id))
    user = user_result.scalar_one_or_none()

    return {
        "id": member.id,
        "organization_id": member.organization_id,
        "user_id": member.user_id,
        "role": member.role,
        "joined_at": member.joined_at,
        "user": {
            "id": user.id,
            "email": user.email,
            "display_name": user.display_name
        } if user else None
    }


@router.delete("/api/organizations/{org_id}/members/{user_id}", response_model=SuccessResponse)
async def remove_organization_member(
    org_id: UUID,
    user_id: UUID,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db)
):
    """
    Remove a member from an organization.
    Requires: admin role.
    """
    # Get membership
    result = await db.execute(
        select(OrganizationMember).where(
            (OrganizationMember.organization_id == org_id) &
            (OrganizationMember.user_id == user_id)
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    # Capture old values for audit trail
    old_values = {f: getattr(member, f) for f in ORG_MEMBER_TRACKED_FIELDS if hasattr(member, f)}

    # Deactivate any API keys the removed member had for this org
    api_key_result = await db.execute(
        select(ApiKey).where(
            (ApiKey.user_id == user_id) &
            (ApiKey.organization_id == org_id) &
            (ApiKey.is_active == True)  # noqa: E712
        )
    )
    for key in api_key_result.scalars().all():
        key.is_active = False

    # Log deletion before removing
    await log_entity_changes(
        db=db, organization_id=org_id, entity_type='org_member',
        entity_id=member.id, action='delete',
        changed_by_user_id=UUID(membership.user.db_id) if membership.user and membership.user.db_id else None,
        old_values=old_values, new_values={},
        tracked_fields=ORG_MEMBER_TRACKED_FIELDS,
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )

    await db.delete(member)
    await db.commit()

    return SuccessResponse(message="Member removed successfully")


@router.get("/api/users/me", response_model=UserResponse)
async def get_current_user(
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """Get current authenticated user's details."""
    if current_user.auth_method == "api_key":
        # API key users have no subscription model — return null subscription
        return {
            "id": "00000000-0000-0000-0000-000000000000",
            "google_sub": "api_user",
            "email": current_user.email,
            "display_name": current_user.name,
            "created_at": None,
            "last_login_at": None,
            "email_notifications_enabled": False,
            "notification_frequency": "immediate",
            "subscription": None,
        }

    # Get user from database
    if current_user.db_id:
        result = await db.execute(select(DBUser).where(DBUser.id == current_user.db_id))
        db_user = result.scalar_one_or_none()
        if db_user:
            # Load subscription (auto-creates free tier if none exists)
            sub = await get_user_subscription(db_user.id, db)
            return {
                "id": db_user.id,
                "google_sub": db_user.google_sub,
                "email": db_user.email,
                "display_name": db_user.display_name,
                "created_at": db_user.created_at,
                "last_login_at": db_user.last_login_at,
                "email_notifications_enabled": db_user.email_notifications_enabled,
                "notification_frequency": db_user.notification_frequency,
                "is_platform_admin": db_user.is_platform_admin,
                "subscription": UserSubscriptionInfo(
                    tier=sub.tier,
                    max_organisations=sub.max_organisations,
                    max_team_members=sub.max_team_members,
                    is_active=sub.is_active,
                ),
            }

    raise HTTPException(status_code=404, detail="User not found in database")


# =============================================================================
# Organisation Member Invitation Endpoints
# =============================================================================


@router.post("/api/organizations/{org_id}/invite", response_model=OrgInviteResponse)
async def invite_user_to_organization(
    org_id: UUID,
    invite_data: OrgInviteCreate,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db)
):
    """
    Invite a user to join the organization via email.
    Requires: admin role.

    Creates a tracked invitation with a secure token and sends an email.
    Enforces domain validation and subscription limits.
    """
    current_user = membership.user
    logger.info(f"Invite request: org={org_id}, email={invite_data.email}, role={invite_data.role}")

    # Load organisation
    result = await db.execute(select(Organization).where(Organization.id == org_id))
    org = result.scalar_one_or_none()

    try:
        invite = await org_invite_service.create_invite(
            org_id=org_id,
            inviter_user_id=current_user.db_id,
            inviter_email=current_user.email,
            email=invite_data.email,
            role=invite_data.role,
            message=invite_data.message,
            db=db,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except PermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "message": str(e),
                "upgrade_url": "/subscription/upgrade"
            }
        )

    # Send invitation email
    inviter_name = current_user.name or current_user.email or "A team member"
    try:
        await send_invitation_email(
            to_email=invite_data.email,
            organization_name=org.name,
            inviter_name=inviter_name,
            invite_token=invite.invite_token,
            custom_message=invite_data.message,
            invite_type="org",
        )
    except Exception as e:
        logger.error(f"Email send failed for invite {invite.id}: {e}")
        # Invite is created even if email fails — admin can share link manually

    return OrgInviteResponse(
        id=invite.id,
        organization_id=invite.organization_id,
        organization_name=org.name,
        email=invite.email,
        role=invite.role,
        status=invite.status,
        invite_token=invite.invite_token,
        expires_at=invite.expires_at,
        created_at=invite.created_at,
    )


@router.get("/api/organizations/{org_id}/invites", response_model=OrgInviteListResponse)
async def list_organization_invites(
    org_id: UUID,
    status_filter: Optional[str] = Query(None, alias="status"),
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db)
):
    """List all invitations for an organisation. Requires: admin role."""
    result = await db.execute(select(Organization).where(Organization.id == org_id))
    org = result.scalar_one_or_none()

    invites = await org_invite_service.list_org_invites(org_id, status_filter, db)

    invite_responses = [
        OrgInviteResponse(
            id=inv.id,
            organization_id=inv.organization_id,
            organization_name=org.name if org else "Unknown",
            email=inv.email,
            role=inv.role,
            status=inv.status,
            invite_token=None,  # Don't expose tokens in list view
            expires_at=inv.expires_at,
            created_at=inv.created_at,
        )
        for inv in invites
    ]

    return OrgInviteListResponse(invites=invite_responses, total=len(invite_responses))


@router.delete("/api/organizations/{org_id}/invites/{invite_id}", response_model=SuccessResponse)
async def cancel_organization_invite(
    org_id: UUID,
    invite_id: UUID,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db)
):
    """Cancel a pending invitation. Requires: admin role."""
    try:
        await org_invite_service.cancel_invite(invite_id, org_id, db)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return SuccessResponse(message="Invitation cancelled successfully")


@router.get("/api/org-invites/{token}/preview", response_model=OrgInvitePreviewResponse)
async def preview_org_invite(
    token: str,
    db: AsyncSession = Depends(get_db)
):
    """Public endpoint — preview an organisation invitation without authentication."""
    try:
        preview = await org_invite_service.get_invite_preview(token, db)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return OrgInvitePreviewResponse(**preview)


@router.post("/api/org-invites/{token}/accept", response_model=AcceptOrgInviteResponse)
async def accept_org_invite(
    token: str,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """Accept an organisation invitation. Requires: authenticated user."""
    try:
        invite, org = await org_invite_service.accept_invite(
            token=token,
            user_id=current_user.db_id,
            user_email=current_user.email,
            db=db,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return AcceptOrgInviteResponse(
        success=True,
        message=f"You have joined {org.name} as {invite.role}.",
        organization={
            "id": org.id,
            "name": org.name,
            "slug": org.slug,
            "created_at": org.created_at,
            "updated_at": org.updated_at,
        },
    )
