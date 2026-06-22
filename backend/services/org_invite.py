"""
Organisation Invite Service - Business logic for org member invitations.

Handles:
- Creating invitations with domain validation and subscription checks
- Accepting invitations (token-based, email-verified)
- Previewing invitations (public, no auth)
- Listing and cancelling invitations
"""
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, List
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from models import (
    OrganizationInvite,
    OrgInviteStatus,
    Organization,
    OrganizationMember,
    User as DBUser,
    ConsultantProfile,
    ConsultantClientRelationship,
)
from services.domain_validation import validate_invite_domain, is_public_domain
from services.subscription import get_user_subscription, can_invite_member

logger = logging.getLogger(__name__)

INVITE_EXPIRY_DAYS = 7


async def get_org_owner_user_id(org_id: UUID, db: AsyncSession) -> Optional[UUID]:
    """Get the organisation owner's user ID (first admin by joined_at)."""
    result = await db.execute(
        select(OrganizationMember.user_id)
        .where(OrganizationMember.organization_id == org_id)
        .where(OrganizationMember.role == "admin")
        .order_by(OrganizationMember.joined_at.asc())
        .limit(1)
    )
    row = result.first()
    return row[0] if row else None


async def create_invite(
    org_id: UUID,
    inviter_user_id: UUID,
    inviter_email: str,
    email: str,
    role: str,
    message: Optional[str],
    db: AsyncSession,
) -> OrganizationInvite:
    """
    Create an organisation member invitation.

    Validates domain rules, checks for duplicates, enforces subscription limits.

    Raises:
        ValueError: For validation failures (domain, duplicates, existing member)
        PermissionError: For subscription limit exceeded
    """
    # Validate role
    if role not in ("admin", "editor", "viewer"):
        raise ValueError(f"Invalid role '{role}'. Must be admin, editor, or viewer.")

    # Check if inviter is a consultant for this organisation (cross-domain allowed)
    consultant_rel = await db.execute(
        select(ConsultantClientRelationship).join(
            ConsultantProfile,
            ConsultantClientRelationship.consultant_id == ConsultantProfile.id
        ).where(
            ConsultantProfile.user_id == inviter_user_id,
            ConsultantClientRelationship.organization_id == org_id,
            ConsultantClientRelationship.status == "active",
        )
    )
    is_consultant_for_org = consultant_rel.scalar_one_or_none() is not None

    if is_consultant_for_org:
        # Consultants can invite cross-domain, but invitee must not use a public email
        if is_public_domain(email):
            raise ValueError(
                "Invited users must use a corporate email address. "
                "Public email providers (e.g. Gmail, Outlook) are not supported."
            )
        logger.info(f"Consultant cross-domain invite: {inviter_email} -> {email} for org {org_id}")
    else:
        # Standard domain validation for non-consultant invites
        is_valid, error_msg = validate_invite_domain(inviter_email, email)
        if not is_valid:
            raise ValueError(error_msg)

    # Check for duplicate pending invite
    result = await db.execute(
        select(OrganizationInvite).where(
            OrganizationInvite.organization_id == org_id,
            OrganizationInvite.email == email.strip().lower(),
            OrganizationInvite.status == OrgInviteStatus.PENDING.value,
        )
    )
    existing_invite = result.scalar_one_or_none()
    if existing_invite:
        raise ValueError(
            "A pending invitation already exists for this email. "
            "Cancel the existing invitation first."
        )

    # Check if user is already a member
    result = await db.execute(
        select(DBUser).where(DBUser.email == email.strip().lower())
    )
    existing_user = result.scalar_one_or_none()
    if existing_user:
        result = await db.execute(
            select(OrganizationMember).where(
                OrganizationMember.organization_id == org_id,
                OrganizationMember.user_id == existing_user.id,
            )
        )
        if result.scalar_one_or_none():
            raise ValueError("This user is already a member of the organisation.")

    # Check subscription limits (members + pending invites count toward limit)
    owner_user_id = await get_org_owner_user_id(org_id, db)
    if owner_user_id:
        subscription = await get_user_subscription(owner_user_id, db)

        # Count current members
        member_count_result = await db.execute(
            select(func.count(OrganizationMember.id))
            .where(OrganizationMember.organization_id == org_id)
        )
        current_members = member_count_result.scalar() or 0

        # Count pending invites
        pending_invite_result = await db.execute(
            select(func.count(OrganizationInvite.id))
            .where(
                OrganizationInvite.organization_id == org_id,
                OrganizationInvite.status == OrgInviteStatus.PENDING.value,
            )
        )
        pending_invites = pending_invite_result.scalar() or 0

        total_count = current_members + pending_invites
        if not can_invite_member(subscription, total_count):
            raise PermissionError(
                f"Team member limit reached ({total_count}/{subscription.max_team_members}). "
                "Upgrade your subscription to invite more members."
            )

    # Generate secure token and create invite
    invite = OrganizationInvite(
        organization_id=org_id,
        invited_by_user_id=inviter_user_id,
        email=email.strip().lower(),
        role=role,
        invite_token=secrets.token_urlsafe(32),
        status=OrgInviteStatus.PENDING.value,
        custom_message=message,
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=INVITE_EXPIRY_DAYS),
    )
    db.add(invite)
    await db.commit()
    await db.refresh(invite)

    logger.info(f"Created org invite: org={org_id}, email={email}, role={role}")
    return invite


async def accept_invite(
    token: str,
    user_id: UUID,
    user_email: str,
    db: AsyncSession,
) -> tuple[OrganizationInvite, Organization]:
    """
    Accept an organisation invitation by token.

    Verifies the token, checks expiry, validates email match,
    creates the membership, and marks the invite as accepted.

    Returns:
        Tuple of (invite, organization)

    Raises:
        ValueError: For invalid token, expired, wrong email, or already used
    """
    # Find invite by token
    result = await db.execute(
        select(OrganizationInvite).where(OrganizationInvite.invite_token == token)
    )
    invite = result.scalar_one_or_none()
    if not invite:
        raise ValueError("Invalid invitation link.")

    # Check status
    if invite.status != OrgInviteStatus.PENDING.value:
        raise ValueError(f"This invitation has already been {invite.status}.")

    # Check expiry
    if invite.is_expired():
        invite.status = OrgInviteStatus.EXPIRED.value
        await db.commit()
        raise ValueError("This invitation has expired. Please ask the admin to send a new one.")

    # Verify email match
    if user_email.strip().lower() != invite.email.strip().lower():
        raise ValueError(
            "This invitation was sent to a different email address. "
            "Please sign in with the correct account."
        )

    # Check if already a member (race condition guard)
    result = await db.execute(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == invite.organization_id,
            OrganizationMember.user_id == user_id,
        )
    )
    if result.scalar_one_or_none():
        invite.status = OrgInviteStatus.ACCEPTED.value
        await db.commit()
        raise ValueError("You are already a member of this organisation.")

    # Create membership
    member = OrganizationMember(
        organization_id=invite.organization_id,
        user_id=user_id,
        role=invite.role,
    )
    db.add(member)

    # Mark invite as accepted
    invite.status = OrgInviteStatus.ACCEPTED.value
    await db.commit()

    # Load organisation
    result = await db.execute(
        select(Organization).where(Organization.id == invite.organization_id)
    )
    org = result.scalar_one()

    logger.info(
        f"Invite accepted: user={user_id}, org={invite.organization_id}, role={invite.role}"
    )
    return invite, org


async def get_invite_preview(token: str, db: AsyncSession) -> dict:
    """
    Get a public preview of an invitation (no auth required).

    Returns dict with org name, inviter info, role, expiry, status.

    Raises:
        ValueError: If token is invalid
    """
    result = await db.execute(
        select(OrganizationInvite).where(OrganizationInvite.invite_token == token)
    )
    invite = result.scalar_one_or_none()
    if not invite:
        raise ValueError("Invalid invitation link.")

    # Load organisation name
    result = await db.execute(
        select(Organization).where(Organization.id == invite.organization_id)
    )
    org = result.scalar_one_or_none()

    # Load inviter info
    inviter_name = None
    inviter_email = None
    if invite.invited_by_user_id:
        result = await db.execute(
            select(DBUser).where(DBUser.id == invite.invited_by_user_id)
        )
        inviter = result.scalar_one_or_none()
        if inviter:
            inviter_name = inviter.display_name
            inviter_email = inviter.email

    is_expired = invite.is_expired()
    status = invite.status
    if status == OrgInviteStatus.PENDING.value and is_expired:
        status = OrgInviteStatus.EXPIRED.value

    # PII masking for public preview: mask email to show first char + domain
    masked_email = None
    if inviter_email:
        local, domain = inviter_email.split("@", 1) if "@" in inviter_email else (inviter_email, "")
        masked_email = f"{local[0]}***@{domain}" if local else inviter_email

    return {
        "organization_name": org.name if org else "Unknown Organisation",
        "inviter_name": inviter_name,
        "inviter_email": masked_email,
        "role": invite.role,
        "expires_at": invite.expires_at,
        "is_expired": is_expired,
        "status": status,
    }


async def list_org_invites(
    org_id: UUID,
    status_filter: Optional[str],
    db: AsyncSession,
) -> List[OrganizationInvite]:
    """List invitations for an organisation, optionally filtered by status."""
    query = (
        select(OrganizationInvite)
        .where(OrganizationInvite.organization_id == org_id)
        .order_by(OrganizationInvite.created_at.desc())
    )
    if status_filter:
        query = query.where(OrganizationInvite.status == status_filter)

    result = await db.execute(query)
    return list(result.scalars().all())


async def cancel_invite(
    invite_id: UUID,
    org_id: UUID,
    db: AsyncSession,
) -> OrganizationInvite:
    """
    Cancel a pending invitation.

    Raises:
        ValueError: If invite not found or not pending
    """
    result = await db.execute(
        select(OrganizationInvite).where(
            OrganizationInvite.id == invite_id,
            OrganizationInvite.organization_id == org_id,
        )
    )
    invite = result.scalar_one_or_none()
    if not invite:
        raise ValueError("Invitation not found.")

    if invite.status != OrgInviteStatus.PENDING.value:
        raise ValueError(f"Cannot cancel an invitation that is already {invite.status}.")

    invite.status = OrgInviteStatus.CANCELLED.value
    await db.commit()
    await db.refresh(invite)

    logger.info(f"Invite cancelled: id={invite_id}, org={org_id}")
    return invite
