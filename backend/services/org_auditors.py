# SPDX-License-Identifier: AGPL-3.0-only
"""External auditors: an organisation's roster of outside auditors.

An external auditor reviews one or more engagements and nothing else. They are
invited by email and are deliberately NOT organisation members: every org-scoped
permission check (``auth.verify_org_membership``, ``get_accessible_org_ids``,
member lists, seat counts) treats a membership row as "inside the
organisation". So this module never creates an ``OrganizationMember``.

What this means in practice:

* The organisation's email-domain rule (``validate_invite_domain``) applies to
  membership invites only. An auditor works for another firm, so their address
  is on another domain by definition. Public mail domains are still refused.
* An auditor does not take a subscription seat.
* Being on the roster grants nothing. Access is an ACTIVE ``EngagementAuditor``
  grant, assigned per engagement by an admin.

Account lifecycle: the invite provisions the bundled Keycloak account (when the
platform runs one) and ensures a ``users`` row exists -- an existing account is
reused, otherwise a ``pending:{email}`` placeholder is created, which the
existing pending-link path in ``auth._persist_oidc_user`` adopts at first
sign-in. :func:`activate_on_sign_in` then turns the roster entry and any
``invited`` grants on.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    AuditEngagement,
    EngagementAuditor,
    EngagementAuditorStatus,
    OrganizationAuditor,
    OrganizationAuditorStatus,
    OrganizationMember,
    User as DBUser,
    UserSubscription,
)
from services import keycloak_admin
from services.audit_service import create_audit_entry
from services.domain_validation import get_email_domain, is_public_domain
from services.email_service import _mask_email
from services.org_invite import IdpProvisioningError

logger = logging.getLogger(__name__)

ENTITY_TYPE = "organization_auditor"
GRANT_ENTITY_TYPE = "engagement_auditor"
_LIVE_GRANT_STATUSES = (
    EngagementAuditorStatus.INVITED.value,
    EngagementAuditorStatus.ACTIVE.value,
)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def normalise_email(email: str) -> str:
    """Lower-cased, trimmed, and shaped like an address -- else ValueError."""
    value = (email or "").strip().lower()
    local, _, domain = value.rpartition("@")
    if not local or "." not in domain or " " in value:
        raise ValueError("Enter a valid email address.")
    return value


async def invite_auditor(
    *,
    org_id: UUID,
    inviter_user_id: UUID,
    email: str,
    display_name: Optional[str],
    firm: Optional[str],
    db: AsyncSession,
) -> tuple[OrganizationAuditor, Optional[str]]:
    """Add an external auditor to the organisation's roster.

    Returns ``(auditor, temporary_password)``. The password is set only when
    this call created a new account in the bundled Keycloak; it is never stored.

    Raises:
        ValueError: invalid or public address, an existing member, or an auditor
            already on the roster (400).
        IdpProvisioningError: the bundled Keycloak refused (502). Nothing is
            written.
    """
    email = normalise_email(email)
    display_name = (display_name or "").strip() or None
    firm = (firm or "").strip() or None

    if is_public_domain(email):
        raise ValueError(
            "External auditors must use their firm's email address. "
            "Public email providers (e.g. Gmail, Outlook) are not supported."
        )

    user = (
        await db.execute(select(DBUser).where(DBUser.email == email))
    ).scalar_one_or_none()

    if user is not None:
        is_member = (
            await db.execute(
                select(OrganizationMember.id).where(
                    OrganizationMember.organization_id == org_id,
                    OrganizationMember.user_id == user.id,
                )
            )
        ).scalar_one_or_none()
        if is_member is not None:
            raise ValueError(
                "This person is already a member of the organisation. "
                "Assign them to an engagement as an internal auditor instead."
            )

    auditor = (
        await db.execute(
            select(OrganizationAuditor).where(
                OrganizationAuditor.organization_id == org_id,
                OrganizationAuditor.email == email,
            )
        )
    ).scalar_one_or_none()
    if auditor is not None and auditor.status != OrganizationAuditorStatus.DISABLED.value:
        raise ValueError("This auditor is already on your organisation's auditor list.")

    # Provision after every check and before anything is written, exactly as
    # membership invites do: a row promising a login that does not exist is
    # the defect #984 fixed.
    provision: Optional[keycloak_admin.ProvisionResult] = None
    if keycloak_admin.is_enabled():
        try:
            provision = await keycloak_admin.provision_user(email)
        except keycloak_admin.KeycloakAdminError as exc:
            raise IdpProvisioningError(step=exc.step, message=exc.message) from exc
    created_idp_user = provision.user_id if provision is not None and provision.created else None

    try:
        if user is None:
            # Same placeholder shape as `cli.admin add-member`: linked to the
            # real identity by the pending-link path at first sign-in.
            user = DBUser(
                google_sub=f"pending:{email}",
                email=email,
                display_name=display_name or email.split("@")[0].replace(".", " ").title(),
            )
            db.add(user)
            await db.flush()
            db.add(UserSubscription(user_id=user.id, tier="free", is_active=True))

        signed_in_before = user.last_login_at is not None and not str(user.google_sub).startswith("pending:")
        status = (
            OrganizationAuditorStatus.ACTIVE.value
            if signed_in_before
            else OrganizationAuditorStatus.INVITED.value
        )
        action = "create"
        if auditor is None:
            auditor = OrganizationAuditor(organization_id=org_id, email=email)
            db.add(auditor)
        else:
            action = "update"  # re-inviting a disabled auditor reuses the row
        auditor.user_id = user.id
        auditor.display_name = display_name
        auditor.firm = firm
        auditor.status = status
        auditor.invited_by_user_id = inviter_user_id
        auditor.idp_user_id = created_idp_user
        auditor.accepted_at = _now() if signed_in_before else None
        auditor.disabled_at = None
        await db.flush()

        # The domain exemption is the point of this invite, so the trail says
        # so explicitly rather than leaving a reviewer to infer it.
        await create_audit_entry(
            db=db, organization_id=org_id, entity_type=ENTITY_TYPE, entity_id=auditor.id,
            action=action, changed_by_user_id=inviter_user_id,
            field_name="email", new_value=email,
        )
        await create_audit_entry(
            db=db, organization_id=org_id, entity_type=ENTITY_TYPE, entity_id=auditor.id,
            action=action, changed_by_user_id=inviter_user_id,
            field_name="domain_rule",
            new_value=f"exempt: external auditor ({get_email_domain(email)})",
        )
        await create_audit_entry(
            db=db, organization_id=org_id, entity_type=ENTITY_TYPE, entity_id=auditor.id,
            action=action, changed_by_user_id=inviter_user_id,
            field_name="status", new_value=status,
        )
        await db.commit()
        await db.refresh(auditor)
    except Exception:
        await db.rollback()
        if created_idp_user:
            try:
                await keycloak_admin.delete_user(created_idp_user)
            except Exception as cleanup_error:  # pragma: no cover - defensive
                logger.warning(
                    "Could not undo the Keycloak user created for a failed auditor "
                    "invite to %s (idp_user_id=%s): %s",
                    _mask_email(email), created_idp_user, cleanup_error,
                )
        raise

    logger.info(
        "External auditor invited: org=%s email=%s status=%s idp_created=%s",
        org_id, _mask_email(email), auditor.status, bool(created_idp_user),
    )
    return auditor, (provision.temporary_password if created_idp_user else None)


@dataclass
class AuditorRow:
    auditor: OrganizationAuditor
    active_engagements: int


async def list_auditors(org_id: UUID, db: AsyncSession) -> list[AuditorRow]:
    """The roster, newest first, with each auditor's live grant count here."""
    grant_counts = (
        select(EngagementAuditor.user_id, func.count(EngagementAuditor.id).label("n"))
        .join(AuditEngagement, EngagementAuditor.engagement_id == AuditEngagement.id)
        .where(
            AuditEngagement.organization_id == org_id,
            EngagementAuditor.kind == EngagementAuditor.KIND_EXTERNAL,
            EngagementAuditor.status.in_(_LIVE_GRANT_STATUSES),
        )
        .group_by(EngagementAuditor.user_id)
        .subquery()
    )
    result = await db.execute(
        select(OrganizationAuditor, func.coalesce(grant_counts.c.n, 0))
        .outerjoin(grant_counts, grant_counts.c.user_id == OrganizationAuditor.user_id)
        .where(OrganizationAuditor.organization_id == org_id)
        .order_by(OrganizationAuditor.created_at.desc())
    )
    return [AuditorRow(auditor=a, active_engagements=int(n)) for a, n in result.all()]


async def get_auditor(org_id: UUID, auditor_id: UUID, db: AsyncSession) -> Optional[OrganizationAuditor]:
    return (
        await db.execute(
            select(OrganizationAuditor).where(
                OrganizationAuditor.id == auditor_id,
                OrganizationAuditor.organization_id == org_id,
            )
        )
    ).scalar_one_or_none()


async def _revoke_grants(
    db: AsyncSession, *, org_id: UUID, grants: list[EngagementAuditor], actor_id: Optional[UUID], reason: str,
) -> int:
    now = _now()
    for grant in grants:
        old = grant.status
        grant.status = EngagementAuditorStatus.REVOKED.value
        grant.revoked_at = now
        await create_audit_entry(
            db=db, organization_id=org_id, entity_type=GRANT_ENTITY_TYPE, entity_id=grant.id,
            action="update", changed_by_user_id=actor_id,
            field_name="status", old_value=old, new_value=f"revoked ({reason})",
        )
    return len(grants)


async def disable_auditor(
    *, org_id: UUID, auditor: OrganizationAuditor, actor_id: UUID, db: AsyncSession,
) -> int:
    """Disable an auditor: every live external grant in this org is revoked.

    Returns the number of grants revoked. A Keycloak account this roster entry
    created is removed when the auditor never signed in and no other
    organisation still lists them.
    """
    if auditor.status == OrganizationAuditorStatus.DISABLED.value:
        return 0

    grants = list(
        (
            await db.execute(
                select(EngagementAuditor)
                .join(AuditEngagement, EngagementAuditor.engagement_id == AuditEngagement.id)
                .where(
                    AuditEngagement.organization_id == org_id,
                    EngagementAuditor.user_id == auditor.user_id,
                    EngagementAuditor.kind == EngagementAuditor.KIND_EXTERNAL,
                    EngagementAuditor.status.in_(_LIVE_GRANT_STATUSES),
                )
            )
        ).scalars().all()
    )
    revoked = await _revoke_grants(db, org_id=org_id, grants=grants, actor_id=actor_id, reason="auditor disabled")

    old_status = auditor.status
    auditor.status = OrganizationAuditorStatus.DISABLED.value
    auditor.disabled_at = _now()
    await create_audit_entry(
        db=db, organization_id=org_id, entity_type=ENTITY_TYPE, entity_id=auditor.id,
        action="update", changed_by_user_id=actor_id,
        field_name="status", old_value=old_status,
        new_value=OrganizationAuditorStatus.DISABLED.value,
    )

    remove_idp_user: Optional[str] = None
    if auditor.idp_user_id and auditor.accepted_at is None:
        still_listed = (
            await db.execute(
                select(func.count(OrganizationAuditor.id)).where(
                    OrganizationAuditor.user_id == auditor.user_id,
                    OrganizationAuditor.id != auditor.id,
                    OrganizationAuditor.status != OrganizationAuditorStatus.DISABLED.value,
                )
            )
        ).scalar() or 0
        if not still_listed:
            remove_idp_user = auditor.idp_user_id
            auditor.idp_user_id = None

    await db.commit()

    if remove_idp_user:
        try:
            await keycloak_admin.delete_user(remove_idp_user)
        except Exception as exc:  # the access is already gone; the account is cleanup
            logger.warning("Could not remove unused Keycloak account %s: %s", remove_idp_user, exc)

    logger.info("External auditor disabled: org=%s auditor=%s grants_revoked=%s", org_id, auditor.id, revoked)
    return revoked


async def activate_on_sign_in(*, user_id: UUID, db: AsyncSession) -> int:
    """Turn an invited auditor's roster entries and grants on at sign-in.

    Bound on the user id the invite recorded, never on an email claim. Returns
    the number of roster entries activated. The caller commits.
    """
    now = _now()
    auditors = list(
        (
            await db.execute(
                select(OrganizationAuditor).where(
                    OrganizationAuditor.user_id == user_id,
                    OrganizationAuditor.status == OrganizationAuditorStatus.INVITED.value,
                )
            )
        ).scalars().all()
    )
    if not auditors:
        return 0

    for auditor in auditors:
        auditor.status = OrganizationAuditorStatus.ACTIVE.value
        auditor.accepted_at = now
        await create_audit_entry(
            db=db, organization_id=auditor.organization_id, entity_type=ENTITY_TYPE,
            entity_id=auditor.id, action="update", changed_by_user_id=user_id,
            field_name="status", old_value=OrganizationAuditorStatus.INVITED.value,
            new_value=OrganizationAuditorStatus.ACTIVE.value,
        )

    org_ids = [a.organization_id for a in auditors]
    grant_rows = (
        await db.execute(
            select(EngagementAuditor, AuditEngagement.organization_id)
            .join(AuditEngagement, EngagementAuditor.engagement_id == AuditEngagement.id)
            .where(
                EngagementAuditor.user_id == user_id,
                EngagementAuditor.status == EngagementAuditorStatus.INVITED.value,
                AuditEngagement.organization_id.in_(org_ids),
            )
        )
    ).all()
    for grant, org_id in grant_rows:
        grant.status = EngagementAuditorStatus.ACTIVE.value
        grant.accepted_at = now
        await create_audit_entry(
            db=db, organization_id=org_id, entity_type=GRANT_ENTITY_TYPE, entity_id=grant.id,
            action="update", changed_by_user_id=user_id,
            field_name="status", old_value=EngagementAuditorStatus.INVITED.value,
            new_value=EngagementAuditorStatus.ACTIVE.value,
        )
    return len(auditors)


async def revoke_expired_grants(db: AsyncSession, *, today: Optional[date] = None) -> int:
    """Revoke external grants on engagements whose end date has passed.

    Access ends the day after ``end_date``. Internal auditors are org members
    and keep their membership access, so only external grants are touched.
    """
    today = today or datetime.now(timezone.utc).date()
    rows = (
        await db.execute(
            select(EngagementAuditor, AuditEngagement.organization_id)
            .join(AuditEngagement, EngagementAuditor.engagement_id == AuditEngagement.id)
            .where(
                and_(
                    AuditEngagement.end_date.is_not(None),
                    AuditEngagement.end_date < today,
                    EngagementAuditor.kind == EngagementAuditor.KIND_EXTERNAL,
                    EngagementAuditor.status.in_(_LIVE_GRANT_STATUSES),
                )
            )
        )
    ).all()
    revoked = 0
    for grant, org_id in rows:
        revoked += await _revoke_grants(
            db, org_id=org_id, grants=[grant], actor_id=None, reason="engagement ended",
        )
    await db.commit()
    if revoked:
        logger.info("Revoked %s external auditor grant(s) on ended engagements", revoked)
    return revoked
