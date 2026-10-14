"""Organisation compensating controls (PIG R9).

Compensating controls with no vendor, recorded against the organisation's
controls; see ``services/compensating_controls.py`` for when one needs an
accepted risk. Reading needs viewer; recording, updating and deleting need
editor. Every write is in the audit log. Vendor compensating controls stay
under ``/vendors/{vendor_id}/compensating-controls``.
"""
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import OrgMembership, require_org_role
from database import get_db
from models import ScopedControl, VendorCompensatingControl
from schemas import (
    CompensatingControlCreate,
    CompensatingControlResponse,
    CompensatingControlUpdate,
    SuccessResponse,
)
from services.audit_service import (
    detect_action_source,
    get_client_ip,
    get_request_id,
    get_user_agent,
    log_entity_changes,
)
from services.compensating_controls import (
    COMPENSATING_TRACKED_FIELDS,
    ENTITY_TYPE,
    is_org_level,
    justification_problem,
    standing,
    tracked_values,
)
from services.deficiencies import domain_of

router = APIRouter(tags=["compensating-controls"])

#: Fields a PATCH may not set to null: the columns are NOT NULL.
_NOT_CLEARABLE = ("gap_description", "compensating_control", "effectiveness_rating")


def _caller_id(membership: OrgMembership) -> Optional[UUID]:
    return UUID(membership.user.db_id) if membership.user and membership.user.db_id else None


def _risk_code(value: Optional[str]) -> Optional[str]:
    return value.strip() if value and value.strip() else None


def _response(row: VendorCompensatingControl, standing_now: dict) -> CompensatingControlResponse:
    material, state, justified = standing_now[row.id]
    return CompensatingControlResponse(
        id=row.id,
        organization_id=row.organization_id,
        scoped_control_id=row.scoped_control_id,
        scf_id=row.scf_id,
        domain=domain_of(row.scf_id),
        gap_description=row.gap_description,
        compensating_control=row.compensating_control,
        effectiveness_rating=row.effectiveness_rating,
        risk_reduction_notes=row.risk_reduction_notes,
        risk_code=row.risk_code,
        risk_acceptance_state=state,
        material=material,
        justified=justified,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _one_response(db: AsyncSession, row: VendorCompensatingControl) -> CompensatingControlResponse:
    return _response(row, await standing(db, row.organization_id, [row]))


async def _get_or_404(db: AsyncSession, org_id: UUID, cc_id: UUID) -> VendorCompensatingControl:
    row = (await db.execute(
        select(VendorCompensatingControl).where(
            VendorCompensatingControl.id == cc_id,
            VendorCompensatingControl.organization_id == org_id,
            is_org_level(),
        )
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Compensating control not found")
    return row


async def _audit(db, request, org_id, row, action, user_id, old_values, new_values):
    await log_entity_changes(
        db=db,
        organization_id=org_id,
        entity_type=ENTITY_TYPE,
        entity_id=row.id,
        action=action,
        changed_by_user_id=user_id,
        old_values=old_values,
        new_values=new_values,
        scf_id=row.scf_id,
        tracked_fields=COMPENSATING_TRACKED_FIELDS,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )


@router.get(
    "/organizations/{org_id}/compensating-controls",
    response_model=List[CompensatingControlResponse],
)
async def list_compensating_controls(
    org_id: UUID,
    scf_id: Optional[str] = Query(None, description="Compensating controls for this control"),
    domain: Optional[str] = Query(None, description="Compensating controls for controls in this SCF domain code"),
    risk_code: Optional[str] = Query(None),
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """The organisation's compensating controls, newest first. Requires viewer."""
    query = select(VendorCompensatingControl).where(
        VendorCompensatingControl.organization_id == org_id, is_org_level(),
    )
    if scf_id:
        query = query.where(VendorCompensatingControl.scf_id == scf_id.strip().upper())
    if domain:
        query = query.where(
            VendorCompensatingControl.scf_id.istartswith(f"{domain.strip()}-", autoescape=True)
        )
    if risk_code:
        query = query.where(VendorCompensatingControl.risk_code == risk_code.strip())
    query = query.order_by(VendorCompensatingControl.created_at.desc(), VendorCompensatingControl.id)
    rows = (await db.execute(query)).scalars().all()
    standing_now = await standing(db, org_id, rows)
    return [_response(row, standing_now) for row in rows]


@router.get(
    "/organizations/{org_id}/compensating-controls/{cc_id}",
    response_model=CompensatingControlResponse,
)
async def get_compensating_control(
    org_id: UUID,
    cc_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    return await _one_response(db, await _get_or_404(db, org_id, cc_id))


@router.post(
    "/organizations/{org_id}/compensating-controls",
    response_model=CompensatingControlResponse,
    status_code=201,
)
async def create_compensating_control(
    org_id: UUID,
    data: CompensatingControlCreate,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """Record a compensating control for an in-scope control. Requires editor.

    On a material control it needs ``risk_code``: an accepted risk linked to
    the control, with an approval in force (409 otherwise).
    """
    scf_id = data.scf_id.strip().upper()
    control_id = await db.scalar(
        select(ScopedControl.id).where(
            ScopedControl.organization_id == org_id,
            ScopedControl.scf_id == scf_id,
            ScopedControl.selected.is_(True),
        )
    )
    if control_id is None:
        raise HTTPException(status_code=422, detail=f"{scf_id} is not a control this organisation has in scope")
    risk_code = _risk_code(data.risk_code)
    problem = await justification_problem(db, org_id, scf_id, risk_code)
    if problem:
        raise HTTPException(status_code=409, detail=problem)

    row = VendorCompensatingControl(
        organization_id=org_id,
        vendor_id=None,
        scoped_control_id=control_id,
        scf_id=scf_id,
        risk_code=risk_code,
        gap_description=data.gap_description,
        compensating_control=data.compensating_control,
        effectiveness_rating=data.effectiveness_rating,
        risk_reduction_notes=data.risk_reduction_notes,
    )
    db.add(row)
    await db.flush()
    await _audit(db, request, org_id, row, "create", _caller_id(membership), {}, tracked_values(row))
    await db.commit()
    await db.refresh(row)
    return await _one_response(db, row)


@router.patch(
    "/organizations/{org_id}/compensating-controls/{cc_id}",
    response_model=CompensatingControlResponse,
)
async def update_compensating_control(
    org_id: UUID,
    cc_id: UUID,
    data: CompensatingControlUpdate,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """Update a compensating control. A changed risk_code is re-checked as on create. Requires editor."""
    row = await _get_or_404(db, org_id, cc_id)
    changes = data.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="Nothing to update")
    for field in _NOT_CLEARABLE:
        if field in changes and changes[field] is None:
            raise HTTPException(status_code=422, detail=f"{field} cannot be cleared")
    if "risk_code" in changes:
        changes["risk_code"] = _risk_code(changes["risk_code"])
        if changes["risk_code"] != row.risk_code:
            problem = await justification_problem(db, org_id, row.scf_id, changes["risk_code"])
            if problem:
                raise HTTPException(status_code=409, detail=problem)

    old_values = tracked_values(row)
    for field, value in changes.items():
        setattr(row, field, value)
    await _audit(db, request, org_id, row, "update", _caller_id(membership), old_values, tracked_values(row))
    await db.commit()
    await db.refresh(row)
    return await _one_response(db, row)


@router.delete(
    "/organizations/{org_id}/compensating-controls/{cc_id}",
    response_model=SuccessResponse,
)
async def delete_compensating_control(
    org_id: UUID,
    cc_id: UUID,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """Withdraw a compensating control. The audit log keeps what it said. Requires editor."""
    row = await _get_or_404(db, org_id, cc_id)
    await _audit(db, request, org_id, row, "delete", _caller_id(membership), tracked_values(row), {})
    await db.delete(row)
    await db.commit()
    return SuccessResponse(message="Compensating control deleted successfully")
