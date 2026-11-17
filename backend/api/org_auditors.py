# SPDX-License-Identifier: AGPL-3.0-only
"""External auditor list for an organisation.

``/api/organizations/{org_id}/auditors`` -- invite, list and disable external
auditors. An external auditor is not an organisation member (see
``services.org_auditors``); these routes never touch membership.
"""
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import OrgMembership, require_org_role
from database import get_db
from models import Organization, OrganizationAuditor
from schemas import (
    OrgAuditorCreate,
    OrgAuditorInviteResponse,
    OrgAuditorResponse,
    OrgAuditorUpdate,
)
from services import org_auditors as service
from services.email_service import _mask_email, send_auditor_invitation_email
from services.org_invite import IdpProvisioningError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["external-auditors"])


def _response(auditor: OrganizationAuditor, active_engagements: int = 0) -> dict:
    return {
        "id": auditor.id,
        "organization_id": auditor.organization_id,
        "user_id": auditor.user_id,
        "email": auditor.email,
        "display_name": auditor.display_name,
        "firm": auditor.firm,
        "status": auditor.status,
        "active_engagements": active_engagements,
        "created_at": auditor.created_at,
        "accepted_at": auditor.accepted_at,
        "disabled_at": auditor.disabled_at,
    }


@router.get("/organizations/{org_id}/auditors", response_model=list[OrgAuditorResponse])
async def list_org_auditors(
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """The organisation's external auditors. Requires: viewer+."""
    rows = await service.list_auditors(org_id, db)
    return [_response(r.auditor, r.active_engagements) for r in rows]


@router.post(
    "/organizations/{org_id}/auditors",
    response_model=OrgAuditorInviteResponse,
    status_code=201,
)
async def invite_org_auditor(
    org_id: UUID,
    payload: OrgAuditorCreate,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """Invite an external auditor by email. Requires: admin.

    Not limited to the organisation's email domain: the auditor is not a
    member and can only see engagements an admin assigns to them.
    """
    current_user = membership.user
    try:
        auditor, temporary_password = await service.invite_auditor(
            org_id=org_id,
            inviter_user_id=UUID(current_user.db_id),
            email=payload.email,
            display_name=payload.display_name,
            firm=payload.firm,
            db=db,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except IdpProvisioningError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Identity provider {e.step} failed: {e.message}",
        )

    org = (await db.execute(select(Organization).where(Organization.id == org_id))).scalar_one()
    try:
        await send_auditor_invitation_email(
            to_email=auditor.email,
            organization_name=org.name,
            inviter_name=current_user.name or current_user.email or "An administrator",
            custom_message=payload.message,
            temporary_password=temporary_password,
        )
    except Exception as e:  # the auditor is on the list; the admin can follow up
        logger.error("Auditor invitation email to %s failed: %s", _mask_email(auditor.email), e)

    return {**_response(auditor), "idp_temporary_password": temporary_password}


@router.patch("/organizations/{org_id}/auditors/{auditor_id}", response_model=OrgAuditorResponse)
async def update_org_auditor(
    org_id: UUID,
    auditor_id: UUID,
    payload: OrgAuditorUpdate,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """Disable an external auditor, revoking all their engagement access here."""
    auditor = await service.get_auditor(org_id, auditor_id, db)
    if auditor is None:
        raise HTTPException(status_code=404, detail="Auditor not found")
    await service.disable_auditor(
        org_id=org_id, auditor=auditor, actor_id=UUID(membership.user.db_id), db=db,
    )
    await db.refresh(auditor)
    return _response(auditor, 0)
