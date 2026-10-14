"""Due-care pack (PIG R10): control validation and posture snapshots.

* ``POST …/scoped-controls/{scf_id}/validation`` records that an in-scope
  control was validated (editor). Its cadence then gives the next date.
* ``GET …/control-validations`` is the validation schedule (viewer).
* ``GET/POST …/posture-snapshots`` reads the monthly posture snapshots and
  takes this month's when the schedule has not (viewer / editor).

The Governance Oversight Report is a document generator
(``governance-report``), run through the documents API like the others.
Every write here is in the audit log.
"""
from datetime import date, datetime
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import OrgMembership, require_org_role
from database import get_db
from models import PostureSnapshot, ScopedControl
from schemas import (
    ControlValidationListResponse,
    ControlValidationRequest,
    ControlValidationResponse,
    PostureSnapshotResponse,
)
from services.audit_service import (
    SCOPED_CONTROL_TRACKED_FIELDS,
    detect_action_source,
    get_client_ip,
    get_request_id,
    get_user_agent,
    log_entity_changes,
)
from services.due_care import (
    SNAPSHOT_ENTITY_TYPE,
    TRIGGER_MANUAL,
    VALIDATION_STATES,
    SnapshotExists,
    snapshot_values,
    take_snapshot,
    validation_counts,
    validation_record,
    validation_rows_stmt,
    validation_summary,
)

router = APIRouter(tags=["due-care"])

VALIDATION_FIELDS = ("last_validated_at", "last_validated_by", "last_validation_note")


def _caller_id(membership: OrgMembership) -> Optional[UUID]:
    return UUID(membership.user.db_id) if membership.user and membership.user.db_id else None


def _validation_values(control: ScopedControl) -> dict:
    return {f: getattr(control, f) for f in VALIDATION_FIELDS}


@router.post(
    "/organizations/{org_id}/scoped-controls/{scf_id}/validation",
    response_model=ControlValidationResponse,
)
async def record_control_validation(
    org_id: UUID,
    scf_id: str,
    body: ControlValidationRequest,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """Record that an in-scope control has been validated. Requires editor.

    The note is the record: what was checked and what was found. The control's
    validation cadence then gives the date the next validation is due.
    """
    wanted = scf_id.strip().upper()
    control = (await db.execute(
        select(ScopedControl).where(
            ScopedControl.organization_id == org_id,
            ScopedControl.scf_id == wanted,
            ScopedControl.selected.is_(True),
        )
    )).scalar_one_or_none()
    if control is None:
        raise HTTPException(status_code=422, detail=f"{wanted} is not a control this organisation has in scope")
    user_id = _caller_id(membership)
    old_values = _validation_values(control)
    validation_record(control, body.note, user_id, datetime.utcnow())
    await db.flush()
    await log_entity_changes(
        db=db,
        organization_id=org_id,
        entity_type="scoped_control",
        entity_id=control.id,
        action="update",
        changed_by_user_id=user_id,
        old_values=old_values,
        new_values=_validation_values(control),
        scf_id=control.scf_id,
        tracked_fields=SCOPED_CONTROL_TRACKED_FIELDS,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )
    await db.commit()
    row = (await db.execute(
        validation_rows_stmt(org_id, ScopedControl.id == control.id)
    )).one()
    return ControlValidationResponse(**validation_summary([row], date.today())[0])


@router.get(
    "/organizations/{org_id}/control-validations",
    response_model=ControlValidationListResponse,
)
async def list_control_validations(
    org_id: UUID,
    state: Optional[str] = Query(
        None, description="current | due_soon | overdue | never | unknown_cadence"
    ),
    domain: Optional[str] = Query(None, description="Controls in this SCF domain code"),
    scf_id: Optional[str] = Query(None),
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """The validation schedule: each in-scope control's last validation and next date.

    ``counts`` covers every in-scope control the domain and scf_id filters
    select, before the state filter, so it reads as the whole picture.
    Requires viewer.
    """
    if state is not None and state not in VALIDATION_STATES:
        raise HTTPException(
            status_code=422, detail=f"state must be one of {', '.join(VALIDATION_STATES)}"
        )
    extra = []
    if scf_id:
        extra.append(ScopedControl.scf_id == scf_id.strip().upper())
    if domain:
        extra.append(ScopedControl.scf_id.istartswith(f"{domain.strip()}-", autoescape=True))
    rows = (await db.execute(validation_rows_stmt(org_id, *extra))).all()
    today = date.today()
    controls = validation_summary(rows, today)
    if state is not None:
        controls = [c for c in controls if c["validation_state"] == state]
    return ControlValidationListResponse(
        counts=validation_counts(rows, today),
        controls=[ControlValidationResponse(**c) for c in controls],
    )


@router.get(
    "/organizations/{org_id}/posture-snapshots",
    response_model=List[PostureSnapshotResponse],
)
async def list_posture_snapshots(
    org_id: UUID,
    limit: int = Query(24, ge=1, le=120, description="Most recent months first"),
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """The organisation's monthly posture snapshots, newest first. Requires viewer."""
    rows = (await db.execute(
        select(PostureSnapshot)
        .where(PostureSnapshot.organization_id == org_id)
        .order_by(PostureSnapshot.period.desc())
        .limit(limit)
    )).scalars().all()
    return rows


@router.get(
    "/organizations/{org_id}/posture-snapshots/{period}",
    response_model=PostureSnapshotResponse,
)
async def get_posture_snapshot(
    org_id: UUID,
    period: str,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """One month's snapshot; ``period`` is ``YYYY-MM``. Requires viewer."""
    try:
        month = datetime.strptime(period, "%Y-%m").date()
    except ValueError:
        raise HTTPException(status_code=422, detail="period must be YYYY-MM")
    row = (await db.execute(
        select(PostureSnapshot).where(
            and_(PostureSnapshot.organization_id == org_id, PostureSnapshot.period == month)
        )
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"No posture snapshot for {period}")
    return row


@router.post(
    "/organizations/{org_id}/posture-snapshots",
    response_model=PostureSnapshotResponse,
    status_code=201,
)
async def create_posture_snapshot(
    org_id: UUID,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """Take this month's posture snapshot now. Requires editor.

    The monthly schedule takes one on the first of the month; this is for an
    organisation that needs it sooner. A month has one snapshot, so a second
    is refused (409) rather than replacing the record.
    """
    user_id = _caller_id(membership)
    try:
        snapshot = await take_snapshot(db, org_id, TRIGGER_MANUAL, user_id)
    except SnapshotExists as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await log_entity_changes(
        db=db,
        organization_id=org_id,
        entity_type=SNAPSHOT_ENTITY_TYPE,
        entity_id=snapshot.id,
        action="create",
        changed_by_user_id=user_id,
        old_values={},
        new_values=snapshot_values(snapshot),
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )
    await db.commit()
    await db.refresh(snapshot)
    return snapshot
