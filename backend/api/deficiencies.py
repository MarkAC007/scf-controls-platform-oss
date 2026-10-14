"""Control deficiency record (PIG R8).

Deficiencies are the organisation's action items with no vendor; see
``services/deficiencies.py`` for what raises one and how one closes. Reading
needs viewer; raising and updating need editor. A deficiency is never
deleted: one raised in error is cancelled, and the record stays.
"""
from datetime import date
from typing import Annotated, List, Optional, Tuple
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import OrgMembership, assert_user_in_org, require_org_role
from database import get_db
from models import RiskAssessment, ScopedControl, VendorActionItem
from schemas import (
    ACTION_ITEM_STATUSES,
    DeficiencyCreate,
    DeficiencyResponse,
    DeficiencySummary,
    DeficiencyUpdate,
)
from services.audit_service import (
    detect_action_source,
    get_client_ip,
    get_request_id,
    get_user_agent,
    log_entity_changes,
)
from services.capability_tests import restored_finding_problem
from services.deficiencies import (
    CANCELLED,
    CLOSED_STATUSES,
    DEFICIENCY_TRACKED_FIELDS,
    ENTITY_TYPE,
    apply_status,
    blank_to_none,
    closure_problem,
    domain_of,
    is_deficiency,
    is_open,
    open_counts,
    tracked_values,
)

router = APIRouter(tags=["deficiencies"])


def _caller_id(membership: OrgMembership) -> Optional[UUID]:
    return UUID(membership.user.db_id) if membership.user and membership.user.db_id else None


def deficiency_response(item: VendorActionItem) -> DeficiencyResponse:
    return DeficiencyResponse(
        id=item.id,
        organization_id=item.organization_id,
        title=item.title,
        description=item.description,
        priority=item.priority,
        status=item.status,
        owner_name=item.owner_name,
        owner_user_id=item.owner_user_id,
        due_date=item.due_date,
        completed_date=item.completed_date,
        scoped_control_id=item.scoped_control_id,
        scf_id=item.scf_id,
        domain=domain_of(item.scf_id),
        risk_code=item.risk_code,
        auto_generated=bool(item.auto_generated),
        source=item.source,
        source_ref=item.source_ref,
        retest_note=item.retest_note,
        closed_by_user_id=item.closed_by_user_id,
        capability_test_id=item.capability_test_id,
        root_cause=item.root_cause,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


async def _get_or_404(db: AsyncSession, org_id: UUID, deficiency_id: UUID) -> VendorActionItem:
    item = (await db.execute(
        select(VendorActionItem).where(
            VendorActionItem.id == deficiency_id,
            VendorActionItem.organization_id == org_id,
            is_deficiency(),
        )
    )).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Deficiency not found")
    return item


async def check_owner(db: AsyncSession, org_id: UUID, owner_user_id: Optional[UUID]) -> None:
    """The owner may be given work in this organisation (404 otherwise, as elsewhere)."""
    if owner_user_id is not None:
        await assert_user_in_org(owner_user_id, org_id, db)


async def resolve_subject(
    db: AsyncSession, org_id: UUID, scf_raw: Optional[str], risk_raw: Optional[str],
) -> Tuple[Optional[str], Optional[UUID], Optional[str]]:
    """What a deficiency is raised against: ``(scf_id, scoped_control_id, risk_code)``.

    Either may be absent. A control named must be in the organisation's
    scope, and a risk named must be one it has assessed (422 otherwise).
    """
    scf_id = scf_raw.strip().upper() if scf_raw and scf_raw.strip() else None
    risk_code = risk_raw.strip() if risk_raw and risk_raw.strip() else None
    control_id = None
    if scf_id is not None:
        control_id = await db.scalar(
            select(ScopedControl.id).where(
                ScopedControl.organization_id == org_id,
                ScopedControl.scf_id == scf_id,
                ScopedControl.selected.is_(True),
            )
        )
        if control_id is None:
            raise HTTPException(status_code=422, detail=f"{scf_id} is not a control this organisation has in scope")
    if risk_code is not None:
        known = await db.scalar(
            select(RiskAssessment.id).where(
                RiskAssessment.organization_id == org_id, RiskAssessment.risk_code == risk_code,
            )
        )
        if known is None:
            raise HTTPException(status_code=422, detail=f"{risk_code} is not a risk this organisation has assessed")
    return scf_id, control_id, risk_code


async def audit_deficiency(db, request, org_id, item, action, user_id, old_values):
    await log_entity_changes(
        db=db,
        organization_id=org_id,
        entity_type=ENTITY_TYPE,
        entity_id=item.id,
        action=action,
        changed_by_user_id=user_id,
        old_values=old_values,
        new_values=tracked_values(item),
        scf_id=item.scf_id,
        tracked_fields=DEFICIENCY_TRACKED_FIELDS,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )


@router.get(
    "/organizations/{org_id}/deficiencies",
    response_model=List[DeficiencyResponse],
)
async def list_deficiencies(
    org_id: UUID,
    status: Optional[str] = Query(None, pattern=f"^({ACTION_ITEM_STATUSES})$"),
    open_only: bool = Query(False, description="Only open and in-progress deficiencies"),
    scf_id: Optional[str] = Query(None, description="Deficiencies against this control"),
    domain: Optional[str] = Query(None, description="Deficiencies against controls in this SCF domain code"),
    risk_code: Optional[str] = Query(None),
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
    capability_test_id: Annotated[
        Optional[UUID], Query(description="Findings of this capability test")
    ] = None,
):
    """The organisation's deficiencies, newest first. Requires viewer."""
    query = select(VendorActionItem).where(
        VendorActionItem.organization_id == org_id, is_deficiency(),
    )
    if status:
        query = query.where(VendorActionItem.status == status)
    if open_only:
        query = query.where(is_open())
    if scf_id:
        query = query.where(VendorActionItem.scf_id == scf_id.strip().upper())
    if domain:
        query = query.where(VendorActionItem.scf_id.istartswith(f"{domain.strip()}-", autoescape=True))
    if risk_code:
        query = query.where(VendorActionItem.risk_code == risk_code.strip())
    if capability_test_id:
        query = query.where(VendorActionItem.capability_test_id == capability_test_id)
    query = query.order_by(VendorActionItem.created_at.desc(), VendorActionItem.id)
    return [deficiency_response(item) for item in (await db.execute(query)).scalars().all()]


@router.get(
    "/organizations/{org_id}/deficiencies/summary",
    response_model=DeficiencySummary,
)
async def deficiency_summary(
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """Open deficiencies per control and per domain. Requires viewer."""
    return await open_counts(db, org_id)


@router.get(
    "/organizations/{org_id}/deficiencies/{deficiency_id}",
    response_model=DeficiencyResponse,
)
async def get_deficiency(
    org_id: UUID,
    deficiency_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    return deficiency_response(await _get_or_404(db, org_id, deficiency_id))


@router.post(
    "/organizations/{org_id}/deficiencies",
    response_model=DeficiencyResponse,
    status_code=201,
)
async def create_deficiency(
    org_id: UUID,
    data: DeficiencyCreate,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """Raise a deficiency against a control, a risk, or both. Requires editor."""
    if not (data.scf_id and data.scf_id.strip()) and not (data.risk_code and data.risk_code.strip()):
        raise HTTPException(
            status_code=422, detail="A deficiency is raised against a control (scf_id), a risk (risk_code), or both"
        )
    scf_id, control_id, risk_code = await resolve_subject(db, org_id, data.scf_id, data.risk_code)
    await check_owner(db, org_id, data.owner_user_id)

    item = VendorActionItem(
        organization_id=org_id,
        vendor_id=None,
        scoped_control_id=control_id,
        scf_id=scf_id,
        risk_code=risk_code,
        title=data.title,
        description=data.description,
        priority=data.priority,
        status="open",
        owner_name=data.owner_name,
        owner_user_id=data.owner_user_id,
        due_date=data.due_date,
        auto_generated=False,
        root_cause=blank_to_none(data.root_cause),
    )
    db.add(item)
    await db.flush()
    await audit_deficiency(db, request, org_id, item, "create", _caller_id(membership), {})
    await db.commit()
    await db.refresh(item)
    return deficiency_response(item)


@router.patch(
    "/organizations/{org_id}/deficiencies/{deficiency_id}",
    response_model=DeficiencyResponse,
)
async def update_deficiency(
    org_id: UUID,
    deficiency_id: UUID,
    data: DeficiencyUpdate,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """Update a deficiency. Completing one needs a retest note. Requires editor."""
    item = await _get_or_404(db, org_id, deficiency_id)
    changes = data.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="Nothing to update")
    for field in ("title", "priority", "status"):
        if field in changes and changes[field] is None:
            raise HTTPException(status_code=422, detail=f"{field} cannot be cleared")
    if "owner_user_id" in changes:
        await check_owner(db, org_id, changes["owner_user_id"])
    if "root_cause" in changes:
        changes["root_cause"] = blank_to_none(changes["root_cause"])

    # Settle the outcome before touching the row, so a refused update leaves
    # nothing changed in the session.
    new_status = changes.pop("status", item.status)
    note_given = "retest_note" in changes
    note = changes.pop("retest_note", None)
    note = note.strip() if note and note.strip() else None
    if item.status in CLOSED_STATUSES and new_status not in CLOSED_STATUSES:
        # Reopened: closing it again needs a fresh retest, so a note sent
        # with the reopening (a whole-object PATCH echoing the old one) is
        # not kept.
        note = None
    elif not note_given:
        note = item.retest_note
    problem = closure_problem(new_status, note)
    if not problem and item.capability_test_id and item.status == CANCELLED and new_status != CANCELLED:
        # A restored finding counts again, so its test must not read as passed.
        problem = await restored_finding_problem(db, org_id, item.capability_test_id)
    if problem:
        raise HTTPException(status_code=422, detail=problem)

    old_values = tracked_values(item)
    user_id = _caller_id(membership)
    for field, value in changes.items():
        setattr(item, field, value)
    if new_status != item.status:
        apply_status(item, new_status, user_id, date.today())
    item.retest_note = note
    await audit_deficiency(db, request, org_id, item, "update", user_id, old_values)
    await db.commit()
    await db.refresh(item)
    return deficiency_response(item)
