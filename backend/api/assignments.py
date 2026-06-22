"""
Assignments API endpoints - manage user assignments to controls and evidence.
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from typing import List, Optional
from uuid import UUID

from database import get_db
from auth import require_auth, get_accessible_org_ids, User
from models import Assignment, User as DBUser, ScopedControl, EvidenceTracking, EvidenceCollectionTask
from schemas import (
    AssignmentCreate,
    AssignmentResponse,
    SuccessResponse
)
from services.notifications import create_assignment_notification
from services.audit_service import log_entity_changes, detect_action_source, get_request_id, ASSIGNMENT_TRACKED_FIELDS

router = APIRouter(
    tags=["assignments"],
    dependencies=[Depends(require_auth)]
)


@router.post("/api/assignments", response_model=AssignmentResponse, status_code=status.HTTP_201_CREATED)
async def create_assignment(
    request: Request,
    assignment_data: AssignmentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """Assign a user to a control or evidence item."""
    # Get user's accessible organisation IDs for tenant isolation
    accessible_org_ids = await get_accessible_org_ids(current_user, db)

    # Verify user exists
    result = await db.execute(select(DBUser).where(DBUser.id == assignment_data.user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Verify assignable exists AND belongs to user's accessible organisations
    # Return 404 (not 403) to avoid leaking existence of entities in other orgs
    if assignment_data.assignable_type == "control":
        result = await db.execute(
            select(ScopedControl)
            .where(ScopedControl.id == assignment_data.assignable_id)
            .where(ScopedControl.organization_id.in_(accessible_org_ids))
        )
        assignable = result.scalar_one_or_none()
    elif assignment_data.assignable_type == "evidence":
        result = await db.execute(
            select(EvidenceTracking)
            .where(EvidenceTracking.id == assignment_data.assignable_id)
            .where(EvidenceTracking.organization_id.in_(accessible_org_ids))
        )
        assignable = result.scalar_one_or_none()
    elif assignment_data.assignable_type == "task":
        # Tasks inherit org access from their parent EvidenceTracking
        result = await db.execute(
            select(EvidenceCollectionTask)
            .join(EvidenceTracking, EvidenceCollectionTask.evidence_tracking_id == EvidenceTracking.id)
            .where(EvidenceCollectionTask.id == assignment_data.assignable_id)
            .where(EvidenceTracking.organization_id.in_(accessible_org_ids))
        )
        assignable = result.scalar_one_or_none()
    else:
        raise HTTPException(status_code=400, detail="Invalid assignable_type. Must be 'control', 'evidence', or 'task'")

    if not assignable:
        raise HTTPException(status_code=404, detail=f"{assignment_data.assignable_type.capitalize()} not found")

    # Check if assignment already exists
    result = await db.execute(
        select(Assignment).where(
            and_(
                Assignment.assignable_type == assignment_data.assignable_type,
                Assignment.assignable_id == assignment_data.assignable_id,
                Assignment.user_id == assignment_data.user_id
            )
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="User is already assigned to this item")

    # Create assignment
    assignment = Assignment(
        assignable_type=assignment_data.assignable_type,
        assignable_id=assignment_data.assignable_id,
        user_id=assignment_data.user_id,
        role=assignment_data.role,
        assigned_by_user_id=UUID(current_user.db_id) if current_user.db_id else None
    )
    db.add(assignment)
    await db.flush()

    # Derive org_id from polymorphic parent for audit logging
    if assignment_data.assignable_type == 'task':
        et_result = await db.execute(
            select(EvidenceTracking).where(EvidenceTracking.id == assignable.evidence_tracking_id)
        )
        et = et_result.scalar_one_or_none()
        audit_org_id = et.organization_id if et else None
    elif hasattr(assignable, 'organization_id'):
        audit_org_id = assignable.organization_id
    else:
        audit_org_id = None

    if audit_org_id is not None:
        new_values = {f: getattr(assignment, f) for f in ASSIGNMENT_TRACKED_FIELDS if hasattr(assignment, f)}
        await log_entity_changes(
            db=db,
            organization_id=audit_org_id,
            entity_type='assignment',
            entity_id=assignment.id,
            action='create',
            changed_by_user_id=UUID(current_user.db_id) if current_user.db_id else None,
            old_values={},
            new_values=new_values,
            tracked_fields=ASSIGNMENT_TRACKED_FIELDS,
            action_source=detect_action_source(request),
            request_id=get_request_id(request),
        )

    await db.commit()
    await db.refresh(assignment)

    # Create notification (includes email if enabled)
    await create_assignment_notification(
        db=db,
        user_id=assignment_data.user_id,
        assignable_type=assignment_data.assignable_type,
        assignable_id=assignment_data.assignable_id,
        assigned_by_user_id=UUID(current_user.db_id) if current_user.db_id else None
    )

    # Return with user data
    return {
        "id": assignment.id,
        "assignable_type": assignment.assignable_type,
        "assignable_id": assignment.assignable_id,
        "user_id": assignment.user_id,
        "role": assignment.role,
        "assigned_at": assignment.assigned_at,
        "assigned_by_user_id": assignment.assigned_by_user_id,
        "user": {
            "id": user.id,
            "email": user.email,
            "display_name": user.display_name
        }
    }


async def _verify_assignment_access(
    assignment: Assignment,
    accessible_org_ids: list[UUID],
    db: AsyncSession
) -> bool:
    """Verify that an assignment's target entity belongs to user's accessible organisations."""
    if assignment.assignable_type == "control":
        result = await db.execute(
            select(ScopedControl)
            .where(ScopedControl.id == assignment.assignable_id)
            .where(ScopedControl.organization_id.in_(accessible_org_ids))
        )
        return result.scalar_one_or_none() is not None
    elif assignment.assignable_type == "evidence":
        result = await db.execute(
            select(EvidenceTracking)
            .where(EvidenceTracking.id == assignment.assignable_id)
            .where(EvidenceTracking.organization_id.in_(accessible_org_ids))
        )
        return result.scalar_one_or_none() is not None
    elif assignment.assignable_type == "task":
        result = await db.execute(
            select(EvidenceCollectionTask)
            .join(EvidenceTracking, EvidenceCollectionTask.evidence_tracking_id == EvidenceTracking.id)
            .where(EvidenceCollectionTask.id == assignment.assignable_id)
            .where(EvidenceTracking.organization_id.in_(accessible_org_ids))
        )
        return result.scalar_one_or_none() is not None
    return False


@router.get("/api/assignments", response_model=List[AssignmentResponse])
async def list_assignments(
    assignable_type: Optional[str] = Query(None, regex="^(control|evidence|task)$"),
    assignable_id: Optional[UUID] = None,
    user_id: Optional[UUID] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """List assignments with optional filters."""
    # Get user's accessible organisation IDs for tenant isolation
    accessible_org_ids = await get_accessible_org_ids(current_user, db)

    query = select(Assignment)

    filters = []
    if assignable_type:
        filters.append(Assignment.assignable_type == assignable_type)
    if assignable_id:
        filters.append(Assignment.assignable_id == assignable_id)
    if user_id:
        filters.append(Assignment.user_id == user_id)

    if filters:
        query = query.where(and_(*filters))

    query = query.order_by(Assignment.assigned_at.desc())

    result = await db.execute(query)
    assignments = result.scalars().all()

    # Filter assignments to only include those where target entity is in accessible orgs
    # and eagerly load user data
    assignment_list = []
    for assignment in assignments:
        # Verify the assignment's target entity belongs to user's accessible organisations
        if not await _verify_assignment_access(assignment, accessible_org_ids, db):
            continue

        user_result = await db.execute(
            select(DBUser).where(DBUser.id == assignment.user_id)
        )
        user = user_result.scalar_one_or_none()

        assignment_dict = {
            "id": assignment.id,
            "assignable_type": assignment.assignable_type,
            "assignable_id": assignment.assignable_id,
            "user_id": assignment.user_id,
            "role": assignment.role,
            "assigned_at": assignment.assigned_at,
            "assigned_by_user_id": assignment.assigned_by_user_id,
            "user": {
                "id": user.id,
                "email": user.email,
                "display_name": user.display_name
            } if user else None
        }
        assignment_list.append(assignment_dict)

    return assignment_list


@router.get("/api/users/me/assignments", response_model=List[AssignmentResponse])
async def get_my_assignments(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """Get current user's assignments."""
    if not current_user.db_id:
        return []

    # Get user's accessible organisation IDs for tenant isolation
    accessible_org_ids = await get_accessible_org_ids(current_user, db)

    result = await db.execute(
        select(Assignment)
        .where(Assignment.user_id == UUID(current_user.db_id))
        .order_by(Assignment.assigned_at.desc())
    )
    assignments = result.scalars().all()

    # Filter to only include assignments where target entity is in accessible orgs
    # and eagerly load user data
    assignment_list = []
    for assignment in assignments:
        # Verify the assignment's target entity belongs to user's accessible organisations
        if not await _verify_assignment_access(assignment, accessible_org_ids, db):
            continue

        user_result = await db.execute(
            select(DBUser).where(DBUser.id == assignment.user_id)
        )
        user = user_result.scalar_one_or_none()

        assignment_dict = {
            "id": assignment.id,
            "assignable_type": assignment.assignable_type,
            "assignable_id": assignment.assignable_id,
            "user_id": assignment.user_id,
            "role": assignment.role,
            "assigned_at": assignment.assigned_at,
            "assigned_by_user_id": assignment.assigned_by_user_id,
            "user": {
                "id": user.id,
                "email": user.email,
                "display_name": user.display_name
            } if user else None
        }
        assignment_list.append(assignment_dict)

    return assignment_list


@router.delete("/api/assignments/{assignment_id}", response_model=SuccessResponse)
async def delete_assignment(
    request: Request,
    assignment_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """Remove an assignment."""
    # Get user's accessible organisation IDs for tenant isolation
    accessible_org_ids = await get_accessible_org_ids(current_user, db)

    result = await db.execute(
        select(Assignment).where(Assignment.id == assignment_id)
    )
    assignment = result.scalar_one_or_none()

    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    # Verify the assignment's target entity belongs to user's accessible organisations
    # Return 404 (not 403) to avoid leaking existence of assignments in other orgs
    if not await _verify_assignment_access(assignment, accessible_org_ids, db):
        raise HTTPException(status_code=404, detail="Assignment not found")

    # Capture old values for audit logging before deletion
    old_values = {f: getattr(assignment, f) for f in ASSIGNMENT_TRACKED_FIELDS if hasattr(assignment, f)}

    # Derive org_id from polymorphic parent for audit logging
    if assignment.assignable_type == 'task':
        task_result = await db.execute(
            select(EvidenceCollectionTask).where(EvidenceCollectionTask.id == assignment.assignable_id)
        )
        task_entity = task_result.scalar_one_or_none()
        if task_entity:
            et_result = await db.execute(
                select(EvidenceTracking).where(EvidenceTracking.id == task_entity.evidence_tracking_id)
            )
            et = et_result.scalar_one_or_none()
            audit_org_id = et.organization_id if et else None
        else:
            audit_org_id = None
    elif assignment.assignable_type == 'control':
        sc_result = await db.execute(
            select(ScopedControl).where(ScopedControl.id == assignment.assignable_id)
        )
        sc = sc_result.scalar_one_or_none()
        audit_org_id = sc.organization_id if sc else None
    elif assignment.assignable_type == 'evidence':
        ev_result = await db.execute(
            select(EvidenceTracking).where(EvidenceTracking.id == assignment.assignable_id)
        )
        ev = ev_result.scalar_one_or_none()
        audit_org_id = ev.organization_id if ev else None
    else:
        audit_org_id = None

    if audit_org_id is not None:
        await log_entity_changes(
            db=db,
            organization_id=audit_org_id,
            entity_type='assignment',
            entity_id=assignment.id,
            action='delete',
            changed_by_user_id=UUID(current_user.db_id) if current_user.db_id else None,
            old_values=old_values,
            new_values={},
            tracked_fields=ASSIGNMENT_TRACKED_FIELDS,
            action_source=detect_action_source(request),
            request_id=get_request_id(request),
        )

    await db.delete(assignment)
    await db.commit()

    return SuccessResponse(message="Assignment removed successfully")
