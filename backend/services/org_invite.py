"""
Organisation Invite Service - Business logic for org member invitations.

Handles:
- Creating invitations with domain validation and subscription checks
- Provisioning the invitee's identity when the platform runs the bundled
  Keycloak (#984) -- see :func:`create_invite`
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
from services.org_utils import MEMBER_TYPES
from services.invite_tokens import hash_invite_token
from services.audit_service import (
    create_audit_entry,
    log_entity_changes,
    ORG_MEMBER_TRACKED_FIELDS,
)
from services.subscription import get_user_subscription, can_invite_member
from services import keycloak_admin

logger = logging.getLogger(__name__)

INVITE_EXPIRY_DAYS = 7


class IdpProvisioningError(Exception):
    """The invitee's identity could not be provisioned, named by the step.

    ``step`` is one of ``token``, ``lookup``, ``create``, ``set_password`` --
    :class:`services.keycloak_admin.KeycloakAdminError`'s vocabulary, carried
    through unchanged so the API can tell an operator *which* half of the round
    trip broke. The message deliberately carries no response body, token or
    password: it reaches an HTTP response.

    Distinct from ``ValueError`` (a 400: the caller asked for something invalid)
    because this is a 502 -- the request was fine, a dependency was not.
    """

    def __init__(self, *, step: str, message: str) -> None:
        super().__init__(f"Identity provider {step} failed: {message}")
        self.step = step
        self.message = message


def _as_uuid(value: Optional[str]) -> Optional[UUID]:
    """A Keycloak user id as a UUID, or None when it is not one.

    Keycloak mints UUIDs, but the column holds an opaque string from another
    system, so nothing here may assume the shape. ``audit_log.entity_id`` is a
    UUID column; a non-UUID id falls back to the invite's own id rather than
    costing us the audit row.
    """
    if not value:
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


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
    member_type: str = "internal",
) -> tuple[OrganizationInvite, Optional[str]]:
    """
    Create an organisation member invitation.

    Validates domain rules, checks for duplicates, enforces subscription limits.

    Returns ``(invite, temporary_password)``. The password is not None only when
    this call created a *new* identity in the bundled Keycloak: it is handed to
    the inviter once, put in the invitation email once, and never stored. The
    tuple exists so it can never be read back off the invite row -- there is
    nowhere to read it from.

    *member_type* is the employment type the membership will carry once the
    invite is accepted (#822 phase 2). Defaulted so that every existing caller
    -- and any caller that simply does not care -- produces an internal member,
    which is what the column default would have given them anyway. It is a
    label and grants nothing; `role` remains the only thing authorisation
    consults.

    Raises:
        ValueError: For validation failures (domain, duplicates, existing member)
        PermissionError: For subscription limit exceeded
        IdpProvisioningError: The bundled Keycloak rejected the provisioning
            round trip. The invite is NOT created -- a row promising a login
            that does not exist is the defect #984 was raised over.
    """
    # Validate role
    if role not in ("admin", "editor", "viewer"):
        raise ValueError(f"Invalid role '{role}'. Must be admin, editor, or viewer.")

    # Validate member_type here as well as in the request schema: this function
    # is called directly by the CLI and by tests, which never see the Pydantic
    # pattern, and the CHECK constraint's rejection would surface as a 500.
    if member_type not in MEMBER_TYPES:
        raise ValueError(
            f"Invalid member_type '{member_type}'. "
            f"Must be one of: {', '.join(sorted(MEMBER_TYPES))}."
        )

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

    # --- Identity provisioning (#984) -------------------------------------
    #
    # Placed after every validation and the seat check, and before the invite is
    # committed. Provisioning earlier would mint identities for invites that are
    # about to be rejected; committing first would leave a row promising a login
    # that does not exist, which is exactly the defect being fixed.
    #
    # `is_enabled()` is false on a bring-your-own-OIDC install and this block
    # then makes no HTTP call whatsoever. That gate is the whole guarantee that
    # the platform never writes to a directory it does not own, so it must stay
    # the only way in.
    provision: Optional[keycloak_admin.ProvisionResult] = None
    if keycloak_admin.is_enabled():
        try:
            provision = await keycloak_admin.provision_user(email.strip().lower())
        except keycloak_admin.KeycloakAdminError as exc:
            # Deliberately fatal. A database-only invite would look successful
            # to the admin and fail days later in front of the invitee, who can
            # do nothing about it. An identity that already existed is not a
            # failure -- `provision_user` returns it with created=False.
            raise IdpProvisioningError(step=exc.step, message=exc.message) from exc

    # Generate secure token and create invite. The hash is the lookup key —
    # invite_token itself is encrypted at rest and cannot be matched by value.
    token = secrets.token_urlsafe(32)
    invite = OrganizationInvite(
        organization_id=org_id,
        invited_by_user_id=inviter_user_id,
        email=email.strip().lower(),
        role=role,
        member_type=member_type,
        invite_token=token,
        invite_token_hash=hash_invite_token(token),
        status=OrgInviteStatus.PENDING.value,
        custom_message=message,
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=INVITE_EXPIRY_DAYS),
    )
    # Only an identity this call *created* is recorded as ours. An account that
    # already existed belongs to a person who may be using it in another
    # organisation: we do not own it, we did not touch it, and cancelling this
    # invite must not delete it. Null columns are what say so.
    if provision is not None and provision.created:
        invite.idp_user_id = provision.user_id
        invite.idp_provisioned_at = datetime.now(timezone.utc)

    db.add(invite)
    try:
        if provision is not None and provision.created:
            # `invite.id` is generated at INSERT, and the audit row needs a real
            # entity id, so flush first. One transaction: a failure below takes
            # the audit row with it rather than leaving a record of a
            # provisioning that has been undone.
            await db.flush()
            await create_audit_entry(
                db=db,
                organization_id=org_id,
                entity_type="idp_user",
                entity_id=_as_uuid(provision.user_id) or invite.id,
                action="create",
                changed_by_user_id=inviter_user_id,
                field_name="email",
                new_value=invite.email,
            )
        await db.commit()
        await db.refresh(invite)
    except Exception:
        # The account exists and the invite does not. Leaving it stranded would
        # also poison the retry: the next invite for this address would find an
        # existing identity, leave it alone, and issue no password at all.
        if provision is not None and provision.created:
            try:
                await keycloak_admin.delete_user(provision.user_id)
            except Exception as cleanup_error:  # pragma: no cover - defensive
                # Best effort, and never the password: this log line is the
                # operator's only trace of an orphaned account.
                logger.warning(
                    "Could not undo the Keycloak user created for a failed "
                    "invite to %s (idp_user_id=%s): %s",
                    invite.email,
                    provision.user_id,
                    cleanup_error,
                )
        raise

    logger.info(
        f"Created org invite: org={org_id}, email={email}, role={role}, "
        f"member_type={member_type}, idp_provisioned={bool(invite.idp_user_id)}"
    )
    return invite, (provision.temporary_password if provision and provision.created else None)


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

    **Cross-table invariant.** The invite's ``member_type`` is copied verbatim
    onto the membership below, which means
    ``ck_organization_invites_member_type`` and
    ``ck_organization_members_member_type`` must accept exactly the same set of
    values. If they ever drift, the failure is nasty and delayed: an invite
    validates and sends fine, then blows up at acceptance -- in front of the
    invitee, who can do nothing about it, and after the admin who sent it has
    stopped watching. Widen or narrow one only by widening or narrowing the
    other in the same migration, and keep :data:`services.org_utils.MEMBER_TYPES`
    in step with both.

    Returns:
        Tuple of (invite, organization)

    Raises:
        ValueError: For invalid token, expired, wrong email, or already used
    """
    # Find invite by token
    result = await db.execute(
        select(OrganizationInvite).where(
            OrganizationInvite.invite_token_hash == hash_invite_token(token)
        )
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
    #
    # member_type is carried from the invite, not defaulted. If this line is
    # dropped the invite modal's employment-type selector becomes a control
    # that silently does nothing -- exactly the defect #822 was raised over.
    member = OrganizationMember(
        organization_id=invite.organization_id,
        user_id=user_id,
        role=invite.role,
        member_type=invite.member_type,
    )
    db.add(member)
    # Flush, not commit: `member.id` is generated client-side at INSERT, and
    # the audit entry below needs it as `entity_id`. Flushing keeps the
    # membership and its audit rows in ONE transaction, so a later failure
    # rolls back both rather than leaving a membership nobody can account for.
    await db.flush()

    # Audit the membership creation (#822 invariant 6: every membership
    # mutation writes to audit_log). Accepting an invite creates a membership,
    # so it is one -- and this is the arrival path for contractors, which is
    # exactly what `member_type` exists to make visible during an audit. A
    # contractor accountable for a control with no record of how they got
    # there defeats the point of the field.
    #
    # Driven off ORG_MEMBER_TRACKED_FIELDS rather than naming member_type, so
    # this is a membership-creation audit rather than a member_type bolt-on
    # and grows automatically if the tracked set does.
    #
    # The actor is the accepting user: they are the one taking the action. The
    # other half of the story -- who invited them -- is on the invite row as
    # `invited_by_user_id`, so both are reconstructible from the two records.
    #
    # No action_source or request_id: this service takes no Request, and
    # threading one through the signature purely to populate two nullable
    # columns would ripple into every caller. Both are nullable by design.
    await log_entity_changes(
        db=db,
        organization_id=invite.organization_id,
        entity_type='org_member',
        entity_id=member.id,
        action='create',
        changed_by_user_id=UUID(str(user_id)) if user_id else None,
        old_values={},
        new_values={
            f: getattr(member, f)
            for f in ORG_MEMBER_TRACKED_FIELDS
            if hasattr(member, f)
        },
        tracked_fields=ORG_MEMBER_TRACKED_FIELDS,
    )

    # Mark invite as accepted
    invite.status = OrgInviteStatus.ACCEPTED.value
    await db.commit()

    # Load organisation
    result = await db.execute(
        select(Organization).where(Organization.id == invite.organization_id)
    )
    org = result.scalar_one()

    logger.info(
        f"Invite accepted: user={user_id}, org={invite.organization_id}, "
        f"role={invite.role}, member_type={invite.member_type}"
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
        select(OrganizationInvite).where(
            OrganizationInvite.invite_token_hash == hash_invite_token(token)
        )
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
    cancelled_by_user_id: Optional[UUID] = None,
) -> OrganizationInvite:
    """
    Cancel a pending invitation.

    When the invite provisioned an identity in the bundled Keycloak (#984) and
    nobody has logged in with it, the identity is removed too -- otherwise
    cancelling would leave an account with a live temporary password and no
    invitation behind it. Both halves of that condition matter: ``idp_user_id``
    means *we* created it, and the absence of a ``users`` row means it has never
    been used. Either one missing and the account is left completely alone.

    *cancelled_by_user_id* is the actor for the audit row; it falls back to the
    original inviter, because an audit entry needs a user and the API has one
    more readily than this service does.

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

    if invite.idp_user_id and keycloak_admin.is_enabled():
        user_row = await db.execute(
            select(DBUser).where(DBUser.email == invite.email)
        )
        if user_row.scalar_one_or_none() is None:
            deleted = False
            try:
                await keycloak_admin.delete_user(invite.idp_user_id)
                deleted = True
            except keycloak_admin.KeycloakAdminError as exc:
                # The cancellation itself still stands. Refusing to cancel
                # because a directory call failed would leave the admin with an
                # invite they cannot withdraw; the operator gets the account.
                logger.warning(
                    "Invite %s cancelled but its Keycloak user %s could not be "
                    "removed at the %s step: %s",
                    invite_id,
                    invite.idp_user_id,
                    exc.step,
                    exc.message,
                )
            if deleted:
                await create_audit_entry(
                    db=db,
                    organization_id=org_id,
                    entity_type="idp_user",
                    entity_id=_as_uuid(invite.idp_user_id) or invite.id,
                    action="delete",
                    changed_by_user_id=cancelled_by_user_id or invite.invited_by_user_id,
                    field_name="email",
                    old_value=invite.email,
                )

    invite.status = OrgInviteStatus.CANCELLED.value
    await db.commit()
    await db.refresh(invite)

    logger.info(f"Invite cancelled: id={invite_id}, org={org_id}")
    return invite


# ---------------------------------------------------------------------------
# Identity-provider status for pending invites (#984)
# ---------------------------------------------------------------------------

#: What an invite's identity looks like from the platform's side.
#:   ``provisioned``  — an account exists for this address in the realm
#:   ``not_in_idp``   — bundled Keycloak is on and the address has no account
#:   ``external``     — provisioning is off; the customer's IdP owns the account
IDP_STATUS_PROVISIONED = "provisioned"
IDP_STATUS_NOT_IN_IDP = "not_in_idp"
IDP_STATUS_EXTERNAL = "external"


async def idp_status_for(invite: OrganizationInvite, db: AsyncSession) -> str:
    """The :data:`IDP_STATUS_PROVISIONED`-family status for one invite.

    A null ``idp_user_id`` on a bundled install is not the same as "no account":
    the address may have had one before it was ever invited here. That case is
    exactly why the lookup happens rather than reading the column alone -- an
    admin told "not in the IdP" about someone who *is* would go and create a
    duplicate.

    *db* is unused today and kept in the signature deliberately: it is the shape
    every other reader of this module expects, and a future status that needs a
    row (say, "already a member elsewhere") should not churn every call site.

    A lookup failure reports ``not_in_idp`` rather than raising. This feeds a
    list endpoint, and a directory hiccup must not take the invitations page
    down; the warning names the step for whoever has to look.
    """
    if not keycloak_admin.is_enabled():
        return IDP_STATUS_EXTERNAL
    if invite.idp_user_id:
        return IDP_STATUS_PROVISIONED
    try:
        found = await keycloak_admin.find_user_id(invite.email)
    except keycloak_admin.KeycloakAdminError as exc:
        logger.warning(
            "Could not resolve idp_status for invite %s at the %s step: %s",
            invite.id,
            exc.step,
            exc.message,
        )
        return IDP_STATUS_NOT_IN_IDP
    return IDP_STATUS_PROVISIONED if found else IDP_STATUS_NOT_IN_IDP


async def idp_statuses_for(
    invites: List[OrganizationInvite], db: AsyncSession
) -> dict:
    """Statuses for a whole list, keyed by invite id, cheaply.

    The list endpoint renders every invite an organisation has ever sent, so the
    naive version is one Keycloak round trip per row. Three things keep it
    small: a disabled install makes none at all; a row that already carries an
    ``idp_user_id`` needs none; and results are memoised per address, because a
    cancelled and a re-sent invite share one.

    Only *pending* invites are looked up. A cancelled or expired invite's
    identity is not actionable, and paying for a directory call to decorate it
    would make the page slower for no decision anyone can act on.
    """
    if not keycloak_admin.is_enabled():
        return {invite.id: IDP_STATUS_EXTERNAL for invite in invites}

    statuses: dict = {}
    by_email: dict = {}
    for invite in invites:
        if invite.idp_user_id:
            statuses[invite.id] = IDP_STATUS_PROVISIONED
            continue
        if invite.status != OrgInviteStatus.PENDING.value:
            statuses[invite.id] = IDP_STATUS_NOT_IN_IDP
            continue
        email = (invite.email or "").strip().lower()
        if email not in by_email:
            by_email[email] = await idp_status_for(invite, db)
        statuses[invite.id] = by_email[email]
    return statuses
