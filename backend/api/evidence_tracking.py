"""
Evidence Tracking API endpoints.
Handles CRUD operations for evidence tracking.
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, update
from sqlalchemy.orm import selectinload
from typing import List, Optional
from uuid import UUID

from database import get_db
from models import EvidenceTracking, EvidenceCollectionTask, EvidenceFile, Organization, System
from catalog_models import SCFCatalogEvidence
from schemas import (
    EvidenceTrackingResponse,
    EvidenceTrackingCreate,
    EvidenceTrackingUpdate,
    BatchEvidenceTrackingRequest,
    BatchEvidenceTrackingResponse,
)
from schemas_catalog_upgrade import CatalogLifecycleBadge
from auth import require_org_role, OrgMembership, assert_user_in_org
from services.audit_service import (
    log_entity_changes,
    get_request_id,
    detect_action_source,
    EVIDENCE_TRACKING_TRACKED_FIELDS,
)
from services.task_generator import generate_task_for_tracking
from services.team_assignments import (
    EVIDENCE_ASSIGNMENT_SPEC,
    accountable_owner_filter,
    team_assignment_filter,
)
from services.org_utils import MEMBER_TYPES, invalid_member_type_detail

logger = logging.getLogger(__name__)

router = APIRouter(tags=["evidence_tracking"])


class EvidenceTrackingBadgedResponse(EvidenceTrackingResponse, CatalogLifecycleBadge):
    """Tracking record + catalog lifecycle badge (plan §4.4 consumer 10).

    Existing tracked rows keep resolving after their ERL entry is retired;
    NEW tracking of a deprecated evidence id is refused at the write path.
    """


async def _catalog_lifecycle_by_evidence_id(db: AsyncSession, evidence_ids):
    """Bulk map evidence_id -> (status, retired_in_version, superseded_by)."""
    if not evidence_ids:
        return {}
    result = await db.execute(
        select(
            SCFCatalogEvidence.evidence_id,
            SCFCatalogEvidence.status,
            SCFCatalogEvidence.retired_in_version,
            SCFCatalogEvidence.superseded_by,
        ).where(SCFCatalogEvidence.evidence_id.in_(list(evidence_ids)))
    )
    return {row.evidence_id: row for row in result.all()}


def _apply_badge(tracking, meta) -> None:
    """Stamp badge attributes on the ORM row for from_attributes serialization."""
    tracking.catalog_status = meta.status if meta is not None else None
    tracking.retired_in_version = meta.retired_in_version if meta is not None else None
    tracking.superseded_by = meta.superseded_by if meta is not None else None


async def _validate_assignees(
    update_data: dict,
    org_id: UUID,
    db: AsyncSession,
    existing=None,
) -> None:
    """Reject NEW assignee/owner user ids that do not belong to ``org_id`` (#781).

    ``assigned_user_id`` and ``owner_user_id`` arrive straight from the request
    body and become the assignee on every task the generator creates. Without
    this check an editor could name a user from another tenant, who would then
    receive due-date notifications and work-queue rows carrying this org's
    evidence IDs. Existence of the user is not sufficient — membership is.

    Three cases are deliberately let through without a lookup:

    * the key is absent — a PATCH that says nothing about assignment
    * the value is ``null`` — an explicit unassign, which is always allowed and
      is the escape hatch out of the case below
    * the value is **unchanged** from what is already stored

    That last one is load-bearing, not an optimisation. The web client re-sends
    the entire tracking object on every single field edit, so a strict check
    would re-validate the stored assignee on every keystroke-debounced save. The
    moment that user left the organisation, editing the row's comments would
    start failing on a field the operator never touched, and the row would be
    un-editable until somebody worked out why. Validating only what *changes*
    means a stale assignee is inert rather than obstructive, and a genuine
    cross-tenant assignment is still refused.
    """
    for field in ("assigned_user_id", "owner_user_id"):
        if field not in update_data:
            continue
        user_id = update_data[field]
        if user_id is None:
            continue
        if existing is not None and getattr(existing, field, None) == user_id:
            continue
        await assert_user_in_org(user_id, org_id, db)


_ASSIGNMENT_KEYS = ("assigned_user_id", "owner_user_id")


async def _propagate_assignee_to_open_tasks(tracking, db: AsyncSession) -> int:
    """Give already-generated open tasks the assignee their evidence just gained.

    ``task_generator`` stamps a task's assignee once, at creation, and its
    duplicate-window check means it never revisits a task it already made. So
    without this, assigning evidence would only ever help the *next* collection
    period: an org with eighty tracked items would set assignees today and still
    see an empty work queue, because the eighty open tasks it already has stay
    NULL until they fall out of their window. That is the symptom #781 exists to
    remove, surviving the fix.

    Only rows where ``assigned_user_id IS NULL`` are touched. A per-task assignee
    set deliberately by a person is never overwritten by an evidence-level edit,
    and completed tasks are left alone as the historical record.

    Returns the number of tasks updated, for the log.
    """
    assignee = tracking.assigned_user_id or tracking.owner_user_id
    if assignee is None:
        return 0

    result = await db.execute(
        update(EvidenceCollectionTask)
        .where(
            EvidenceCollectionTask.evidence_tracking_id == tracking.id,
            EvidenceCollectionTask.assigned_user_id.is_(None),
            EvidenceCollectionTask.status != "completed",
        )
        .values(assigned_user_id=assignee)
    )
    return result.rowcount or 0


async def _generate_first_task(tracking, db: AsyncSession) -> None:
    """Give a newly-eligible tracking row the task it is owed, now.

    ``_propagate_assignee_to_open_tasks`` above fixed assignment for tasks that
    already exist. This closes the other half: an item that has just become
    tracked has no task at all, and until now got none until the nightly sweep
    ran at 01:00 UTC — up to twenty-four hours of an assignment that produced
    nothing visible, with nothing in the product disclosing the wait (#789).

    The eligibility rule is not restated here. `generate_task_for_tracking` is
    the single declaration, shared with the sweep, so the two cannot drift the
    way the frequency vocabulary did before #783.

    ``flush`` first: a row created in this request has no ``id`` yet, and the
    generator's duplicate check joins on it — without the id every call would
    match nothing and create another task.

    Failures are swallowed deliberately. Saving a tracking record is the thing
    the operator asked for; scheduling its first collection is a consequence. A
    consequence that fails must not take the request down with it, and the
    sweep will pick the row up tonight regardless.
    """
    try:
        await db.flush()
        outcome = await generate_task_for_tracking(db, tracking)
    except Exception:
        logger.exception(
            "First-task generation failed for %s; the nightly sweep will retry",
            tracking.evidence_id,
        )
        return

    if outcome.created:
        logger.info(
            "Generated first collection task for %s due %s on write",
            tracking.evidence_id, outcome.due_date,
        )


async def _refuse_new_tracking_of_deprecated(db: AsyncSession, evidence_id: str) -> None:
    """Raise 409 when a NEW tracking row targets a deprecated ERL entry."""
    row = (
        await db.execute(
            select(SCFCatalogEvidence.status, SCFCatalogEvidence.superseded_by).where(
                SCFCatalogEvidence.evidence_id == evidence_id
            )
        )
    ).first()
    if row is not None and row[0] == "deprecated":
        hint = f" Consider its successor '{row[1]}'." if row[1] else ""
        raise HTTPException(
            status_code=409,
            detail=(
                f"Evidence '{evidence_id}' is deprecated in the SCF catalog "
                f"and cannot be newly tracked.{hint}"
            ),
        )


@router.get(
    "/organizations/{org_id}/evidence-tracking",
    response_model=List[EvidenceTrackingBadgedResponse]
)
async def list_evidence_tracking(
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    system_id: Optional[UUID] = Query(None, description="Filter by collecting system"),
    team_id: Optional[UUID] = Query(None, description="Filter to evidence this team is assigned to (accountable or consulted)"),
    function_id: Optional[UUID] = Query(None, description="Filter to evidence assigned to any team aligned to this function"),
    accountable_owner_type: Optional[str] = Query(None, description="Filter to items whose accountable team's primary owner has this member_type: internal or external_contractor"),
    db: AsyncSession = Depends(get_db)
):
    """
    Get all evidence tracking records for an organization.
    Requires: viewer role or higher.
    Optionally filter by system_id to find evidence collected by a specific system,
    and by team_id / function_id to find evidence a team or function is assigned to.
    """
    # Organization existence verified by require_org_role

    # Build query with eager loading for system relationship
    query = select(EvidenceTracking).options(
        selectinload(EvidenceTracking.system),
        selectinload(EvidenceTracking.assigned_user),
        selectinload(EvidenceTracking.owner_user),
    ).where(EvidenceTracking.organization_id == org_id)

    # Apply optional system_id filter
    if system_id is not None:
        query = query.where(EvidenceTracking.system_id == system_id)

    # Apply team / function filters (#822 phase 3)
    #
    # Same EXISTS, same "any assigned team" semantics as the controls list --
    # one helper serves both so the two lists cannot answer the same question
    # differently.
    #
    # This endpoint is NOT paginated: it returns every row for the org. So the
    # argument that a client-side filter would silently filter one page does
    # not apply here. Filtering server-side anyway, for two reasons. The two
    # lists must agree on what "assigned to GRC" means, and one shared helper
    # is how that stays true. And an unpaginated list is exactly the one you
    # least want to ship whole to a browser to have most of it discarded --
    # this pushes the discarding into an indexed semi-join.
    assignment_filter = team_assignment_filter(
        EVIDENCE_ASSIGNMENT_SPEC,
        EvidenceTracking.id,
        organization_id=org_id,
        team_id=team_id,
        function_id=function_id,
    )
    if assignment_filter is not None:
        query = query.where(assignment_filter)

    # Apply the accountable-owner filter (#822 phase 2)
    #
    # The same helper the controls list uses, so "evidence owned by a
    # contractor" and "controls owned by a contractor" mean the same chain:
    # accountable team -> that team's primary -> that person's member_type.
    if accountable_owner_type is not None and accountable_owner_type not in MEMBER_TYPES:
        raise HTTPException(
            status_code=400,
            detail=invalid_member_type_detail("accountable_owner_type"),
        )

    owner_filter = accountable_owner_filter(
        EVIDENCE_ASSIGNMENT_SPEC,
        EvidenceTracking.id,
        organization_id=org_id,
        accountable_owner_type=accountable_owner_type,
    )
    if owner_filter is not None:
        query = query.where(owner_filter)

    result = await db.execute(query)
    tracking = result.scalars().all()

    # Compute file counts for all evidence items in one query
    if tracking:
        evidence_ids = [t.evidence_id for t in tracking]
        count_result = await db.execute(
            select(
                EvidenceFile.evidence_id,
                func.count(EvidenceFile.id).label("file_count"),
            )
            .where(
                and_(
                    EvidenceFile.organization_id == org_id,
                    EvidenceFile.evidence_id.in_(evidence_ids),
                    EvidenceFile.is_deleted == False,
                )
            )
            .group_by(EvidenceFile.evidence_id)
        )
        counts = {row.evidence_id: row.file_count for row in count_result}

        # Attach file_count to each tracking record for serialization
        for t in tracking:
            t.file_count = counts.get(t.evidence_id, 0)
    else:
        for t in tracking:
            t.file_count = 0

    # Attach catalog lifecycle badges — deprecated ERL entries keep resolving
    # for existing tracked rows (plan §4.4 consumer 10).
    lifecycle = await _catalog_lifecycle_by_evidence_id(
        db, {t.evidence_id for t in tracking}
    )
    for t in tracking:
        _apply_badge(t, lifecycle.get(t.evidence_id))

    return tracking


@router.get(
    "/organizations/{org_id}/evidence-tracking/{evidence_id}",
    response_model=EvidenceTrackingBadgedResponse
)
async def get_evidence_tracking(
    org_id: UUID,
    evidence_id: str,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db)
):
    """
    Get a single evidence tracking record by evidence ID.
    Requires: viewer role or higher.
    """
    result = await db.execute(
        select(EvidenceTracking).options(
            selectinload(EvidenceTracking.system),
            selectinload(EvidenceTracking.assigned_user),
            selectinload(EvidenceTracking.owner_user),
        ).where(
            and_(
                EvidenceTracking.organization_id == org_id,
                EvidenceTracking.evidence_id == evidence_id
            )
        )
    )
    tracking = result.scalar_one_or_none()

    if not tracking:
        raise HTTPException(status_code=404, detail="Evidence tracking record not found")

    # Compute file count
    count_result = await db.execute(
        select(func.count(EvidenceFile.id)).where(
            and_(
                EvidenceFile.organization_id == org_id,
                EvidenceFile.evidence_id == evidence_id,
                EvidenceFile.is_deleted == False,
            )
        )
    )
    tracking.file_count = count_result.scalar() or 0

    lifecycle = await _catalog_lifecycle_by_evidence_id(db, {evidence_id})
    _apply_badge(tracking, lifecycle.get(evidence_id))

    return tracking


@router.post(
    "/organizations/{org_id}/evidence-tracking",
    response_model=EvidenceTrackingResponse,
    status_code=201
)
async def create_or_update_evidence_tracking(
    org_id: UUID,
    tracking_data: EvidenceTrackingCreate,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db)
):
    """
    Create or update evidence tracking (upsert).
    Requires: editor role or higher.
    If a record with the same evidence_id exists, it will be updated.
    Otherwise, a new record will be created.
    """
    # Organization existence verified by require_org_role

    # Validate system_id if provided
    if tracking_data.system_id is not None:
        system_result = await db.execute(
            select(System).where(
                and_(
                    System.id == tracking_data.system_id,
                    System.organization_id == org_id
                )
            )
        )
        if not system_result.scalar_one_or_none():
            raise HTTPException(
                status_code=400,
                detail="Invalid system_id: System not found or belongs to different organization"
            )

    # Check if tracking record already exists
    result = await db.execute(
        select(EvidenceTracking).where(
            and_(
                EvidenceTracking.organization_id == org_id,
                EvidenceTracking.evidence_id == tracking_data.evidence_id
            )
        )
    )
    existing_tracking = result.scalar_one_or_none()

    # Assignment targets must be members of THIS organisation (#781). Checked
    # against the stored row so an unchanged, already-stale assignee does not
    # block edits to unrelated fields — see _validate_assignees.
    await _validate_assignees(
        tracking_data.model_dump(exclude_unset=True), org_id, db, existing_tracking
    )

    if not existing_tracking:
        # NEW tracking is active-catalog-only (plan §4.4 consumer 10);
        # updates to an existing row remain allowed.
        await _refuse_new_tracking_of_deprecated(db, tracking_data.evidence_id)

    if existing_tracking:
        # Update existing record
        update_fields = tracking_data.model_dump(exclude_unset=True)
        for key, value in update_fields.items():
            setattr(existing_tracking, key, value)
        if any(k in update_fields for k in _ASSIGNMENT_KEYS):
            stamped = await _propagate_assignee_to_open_tasks(existing_tracking, db)
            if stamped:
                logger.info(
                    "Assignment on %s propagated to %s open task(s)",
                    existing_tracking.evidence_id, stamped,
                )
        await _generate_first_task(existing_tracking, db)
        await db.commit()
        # Reload with system relationship
        result = await db.execute(
            select(EvidenceTracking).options(
                selectinload(EvidenceTracking.system),
                selectinload(EvidenceTracking.assigned_user),
                selectinload(EvidenceTracking.owner_user),
            ).where(EvidenceTracking.id == existing_tracking.id)
        )
        return result.scalar_one()
    else:
        # Create new record
        new_tracking = EvidenceTracking(
            organization_id=org_id,
            **tracking_data.model_dump()
        )
        db.add(new_tracking)
        await _generate_first_task(new_tracking, db)
        await db.commit()
        # Reload with system relationship
        result = await db.execute(
            select(EvidenceTracking).options(
                selectinload(EvidenceTracking.system),
                selectinload(EvidenceTracking.assigned_user),
                selectinload(EvidenceTracking.owner_user),
            ).where(EvidenceTracking.id == new_tracking.id)
        )
        return result.scalar_one()


@router.post(
    "/organizations/{org_id}/evidence-tracking/batch",
    response_model=BatchEvidenceTrackingResponse,
    status_code=200
)
async def batch_update_evidence_tracking(
    org_id: UUID,
    request: BatchEvidenceTrackingRequest,
    http_request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """
    Batch create/update evidence tracking records in a single transaction.
    Requires: editor role or higher.
    Max 500 operations per request.

    For each operation:
    - If a tracking record with the same evidence_id exists, it is updated.
    - Otherwise, a new record is created.
    """
    user_id = UUID(membership.user.db_id)
    updated_count = 0
    created_count = 0
    failed_count = 0
    errors: List[str] = []
    result_evidence: List[EvidenceTracking] = []

    for op in request.operations:
        try:
            # Validate system_id if provided
            update_data = op.model_dump(exclude={'evidence_id'}, exclude_unset=True)
            if "system_id" in update_data and update_data["system_id"] is not None:
                system_result = await db.execute(
                    select(System).where(
                        and_(
                            System.id == update_data["system_id"],
                            System.organization_id == org_id
                        )
                    )
                )
                if not system_result.scalar_one_or_none():
                    raise ValueError(
                        "Invalid system_id: System not found or belongs to different organization"
                    )

            # Check if tracking record already exists
            result = await db.execute(
                select(EvidenceTracking).where(
                    and_(
                        EvidenceTracking.organization_id == org_id,
                        EvidenceTracking.evidence_id == op.evidence_id
                    )
                )
            )
            existing = result.scalar_one_or_none()

            # Assignment targets must be members of THIS organisation (#781).
            # Surfaced as a per-op ValueError, so one bad user id reports itself
            # instead of failing the other 499 rows in the same transaction.
            try:
                await _validate_assignees(update_data, org_id, db, existing)
            except HTTPException as http_exc:
                raise ValueError(http_exc.detail)

            if existing:
                # Capture old values for audit
                old_values = {f: getattr(existing, f) for f in EVIDENCE_TRACKING_TRACKED_FIELDS}

                # Apply updates
                for field_name, value in update_data.items():
                    setattr(existing, field_name, value)

                # Audit log
                new_values = {f: getattr(existing, f) for f in EVIDENCE_TRACKING_TRACKED_FIELDS}
                await log_entity_changes(
                    db=db, organization_id=org_id, entity_type='evidence_tracking',
                    entity_id=existing.id, action='update', changed_by_user_id=user_id,
                    old_values=old_values, new_values=new_values,
                    tracked_fields=EVIDENCE_TRACKING_TRACKED_FIELDS,
                    action_source=detect_action_source(http_request),
                    request_id=get_request_id(http_request),
                )

                if any(k in update_data for k in _ASSIGNMENT_KEYS):
                    await _propagate_assignee_to_open_tasks(existing, db)

                await _generate_first_task(existing, db)

                result_evidence.append(existing)
                updated_count += 1
            else:
                # NEW tracking is active-catalog-only (plan §4.4 consumer 10):
                # surface the refusal as a per-op error, not a batch failure.
                try:
                    await _refuse_new_tracking_of_deprecated(db, op.evidence_id)
                except HTTPException as http_exc:
                    raise ValueError(http_exc.detail)

                # Create new record
                create_data = op.model_dump(exclude={'evidence_id'}, exclude_unset=True)
                new_tracking = EvidenceTracking(
                    organization_id=org_id,
                    evidence_id=op.evidence_id,
                    **create_data,
                )
                db.add(new_tracking)
                await db.flush()

                # Audit log
                new_values = {f: getattr(new_tracking, f) for f in EVIDENCE_TRACKING_TRACKED_FIELDS}
                await log_entity_changes(
                    db=db, organization_id=org_id, entity_type='evidence_tracking',
                    entity_id=new_tracking.id, action='create', changed_by_user_id=user_id,
                    old_values={}, new_values=new_values,
                    tracked_fields=EVIDENCE_TRACKING_TRACKED_FIELDS,
                    action_source=detect_action_source(http_request),
                    request_id=get_request_id(http_request),
                )

                await _generate_first_task(new_tracking, db)

                result_evidence.append(new_tracking)
                created_count += 1
        except Exception as e:
            failed_count += 1
            errors.append(f"{op.evidence_id}: {str(e)}")
            logger.error(f"Batch evidence operation failed for {op.evidence_id}: {e}")

    await db.commit()

    # Re-select with the nested relationships eagerly loaded. A bare
    # db.refresh() reloads column attributes only, so serialising `system`,
    # `assigned_user` or `owner_user` off these instances would fall through to
    # a lazy load and raise MissingGreenlet under the async session (#781).
    if result_evidence:
        reload_result = await db.execute(
            select(EvidenceTracking)
            .options(
                selectinload(EvidenceTracking.system),
                selectinload(EvidenceTracking.assigned_user),
                selectinload(EvidenceTracking.owner_user),
            )
            .where(EvidenceTracking.id.in_([t.id for t in result_evidence]))
        )
        by_id = {t.id: t for t in reload_result.scalars().all()}
        result_evidence = [by_id.get(t.id, t) for t in result_evidence]

    logger.info(
        f"Batch evidence tracking: org={org_id}, updated={updated_count}, "
        f"created={created_count}, failed={failed_count}"
    )

    return BatchEvidenceTrackingResponse(
        updated=updated_count,
        created=created_count,
        failed=failed_count,
        errors=errors,
        evidence=result_evidence,
    )


@router.patch(
    "/organizations/{org_id}/evidence-tracking/{evidence_id}",
    response_model=EvidenceTrackingResponse
)
async def update_evidence_tracking(
    org_id: UUID,
    evidence_id: str,
    tracking_update: EvidenceTrackingUpdate,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db)
):
    """
    Partially update evidence tracking.
    Requires: editor role or higher.
    Only provided fields will be updated.
    """
    result = await db.execute(
        select(EvidenceTracking).where(
            and_(
                EvidenceTracking.organization_id == org_id,
                EvidenceTracking.evidence_id == evidence_id
            )
        )
    )
    tracking = result.scalar_one_or_none()

    if not tracking:
        raise HTTPException(status_code=404, detail="Evidence tracking record not found")

    update_data = tracking_update.model_dump(exclude_unset=True)

    # Assignment targets must be members of THIS organisation (#781)
    await _validate_assignees(update_data, org_id, db, tracking)

    # Validate system_id if provided
    if "system_id" in update_data and update_data["system_id"] is not None:
        system_result = await db.execute(
            select(System).where(
                and_(
                    System.id == update_data["system_id"],
                    System.organization_id == org_id
                )
            )
        )
        if not system_result.scalar_one_or_none():
            raise HTTPException(
                status_code=400,
                detail="Invalid system_id: System not found or belongs to different organization"
            )

    # Update only provided fields
    for key, value in update_data.items():
        setattr(tracking, key, value)

    # A newly assigned evidence item has to reach the tasks that already exist,
    # not just the ones a future generator run would create — see the helper.
    if any(k in update_data for k in _ASSIGNMENT_KEYS):
        stamped = await _propagate_assignee_to_open_tasks(tracking, db)
        if stamped:
            logger.info(
                "Assignment on %s propagated to %s open task(s)",
                tracking.evidence_id, stamped,
            )

    await _generate_first_task(tracking, db)

    await db.commit()
    # Reload with system relationship
    result = await db.execute(
        select(EvidenceTracking).options(
            selectinload(EvidenceTracking.system),
            selectinload(EvidenceTracking.assigned_user),
            selectinload(EvidenceTracking.owner_user),
        ).where(EvidenceTracking.id == tracking.id)
    )
    return result.scalar_one()
