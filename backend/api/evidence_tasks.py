"""
Evidence Collection Tasks API endpoints - manage evidence collection tasks and dashboard.
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, text
from typing import List, Optional
from uuid import UUID
from datetime import date, datetime

from database import get_db
from auth import require_auth, User
from models import EvidenceCollectionTask, EvidenceTracking, User as DBUser
from schemas import (
    EvidenceCollectionTaskCreate,
    EvidenceCollectionTaskUpdate,
    EvidenceCollectionTaskResponse,
    SuccessResponse
)

router = APIRouter(
    tags=["evidence_tasks"],
    dependencies=[Depends(require_auth)]
)


@router.get("/api/evidence-tasks", response_model=List[dict])
async def list_evidence_tasks(
    status_filter: Optional[str] = Query(None, regex="^(not_started|in_progress|completed)$"),
    assigned_user_id: Optional[UUID] = None,
    overdue_only: bool = False,
    frameworks: Optional[List[str]] = Query(None, description="Filter by SCF framework mapping keys (OR logic)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """List evidence collection tasks with optional filters and evidence details."""
    query = select(EvidenceCollectionTask)

    # Framework filter: 2-step JSONB pre-query
    # Step 1: find scf_ids for controls in the requested frameworks
    # Step 2: find ERL IDs that appear in those controls' evidence_requests arrays
    # Then filter tasks whose EvidenceTracking.evidence_id is in that ERL set
    if frameworks:
        framework_conditions = " OR ".join(
            f"framework_mappings ? :fw_{i}" for i in range(len(frameworks))
        )
        params = {f"fw_{i}": fw for i, fw in enumerate(frameworks)}
        scf_result = await db.execute(
            text(f"SELECT scf_id FROM scf_catalog_controls WHERE {framework_conditions}"),
            params
        )
        framework_scf_ids = [row[0] for row in scf_result.fetchall()]

        if not framework_scf_ids:
            return []

        erl_result = await db.execute(
            text("""
                SELECT DISTINCT ev_id
                FROM scf_catalog_controls,
                     jsonb_array_elements_text(evidence_requests) AS ev_id
                WHERE scf_id = ANY(:scf_ids)
            """),
            {"scf_ids": framework_scf_ids}
        )
        framework_erl_ids = [row[0] for row in erl_result.fetchall()]

        if not framework_erl_ids:
            return []

        query = query.join(
            EvidenceTracking, EvidenceCollectionTask.evidence_tracking_id == EvidenceTracking.id
        ).where(EvidenceTracking.evidence_id.in_(framework_erl_ids))

    filters = []
    if status_filter:
        filters.append(EvidenceCollectionTask.status == status_filter)
    if assigned_user_id:
        filters.append(EvidenceCollectionTask.assigned_user_id == assigned_user_id)
    if overdue_only:
        filters.append(
            and_(
                EvidenceCollectionTask.due_date < date.today(),
                EvidenceCollectionTask.status != 'completed'
            )
        )

    if filters:
        query = query.where(and_(*filters))

    query = query.order_by(EvidenceCollectionTask.due_date.asc())

    result = await db.execute(query)
    tasks = result.scalars().all()

    # Eagerly load user data and evidence details
    task_list = []
    for task in tasks:
        user = None
        if task.assigned_user_id:
            user_result = await db.execute(
                select(DBUser).where(DBUser.id == task.assigned_user_id)
            )
            user = user_result.scalar_one_or_none()

        # Get evidence details
        evidence_result = await db.execute(
            select(EvidenceTracking).where(EvidenceTracking.id == task.evidence_tracking_id)
        )
        evidence = evidence_result.scalar_one_or_none()

        task_dict = {
            "id": task.id,
            "evidence_tracking_id": task.evidence_tracking_id,
            "evidence_id": evidence.evidence_id if evidence else None,
            "task_type": task.task_type,
            "title": task.title,
            "description": task.description,
            "priority": task.priority,
            "due_date": task.due_date,
            "status": task.status,
            "assigned_user_id": task.assigned_user_id,
            "completed_date": task.completed_date,
            "completion_notes": task.completion_notes,
            "dependencies": task.dependencies,
            "attachments": task.attachments,
            "auto_generated": task.auto_generated,
            "created_at": task.created_at,
            "frequency": evidence.frequency if evidence else None,
            "collecting_system": evidence.collecting_system if evidence else None,
            "method_of_collection": evidence.method_of_collection if evidence else None,
            "owner": evidence.owner if evidence else None,
            "assigned_user": {
                "id": user.id,
                "email": user.email,
                "display_name": user.display_name
            } if user else None
        }
        task_list.append(task_dict)

    return task_list


@router.post("/api/evidence-tasks", response_model=EvidenceCollectionTaskResponse, status_code=status.HTTP_201_CREATED)
async def create_evidence_task(
    task_data: EvidenceCollectionTaskCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """Create a manual evidence collection task."""
    # Verify evidence tracking exists
    result = await db.execute(
        select(EvidenceTracking).where(EvidenceTracking.id == task_data.evidence_tracking_id)
    )
    evidence = result.scalar_one_or_none()
    if not evidence:
        raise HTTPException(status_code=404, detail="Evidence tracking record not found")

    # Verify assigned user exists if provided
    user = None
    if task_data.assigned_user_id:
        result = await db.execute(
            select(DBUser).where(DBUser.id == task_data.assigned_user_id)
        )
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="Assigned user not found")

    # Create task with enhanced fields
    task = EvidenceCollectionTask(
        evidence_tracking_id=task_data.evidence_tracking_id,
        due_date=task_data.due_date,
        status=task_data.status,
        assigned_user_id=task_data.assigned_user_id,
        task_type=task_data.task_type,
        title=task_data.title,
        description=task_data.description,
        priority=task_data.priority,
        completion_notes=task_data.completion_notes,
        dependencies=task_data.dependencies or [],
        attachments=task_data.attachments or [],
        auto_generated=False
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    return {
        "id": task.id,
        "evidence_tracking_id": task.evidence_tracking_id,
        "due_date": task.due_date,
        "status": task.status,
        "assigned_user_id": task.assigned_user_id,
        "completed_date": task.completed_date,
        "completion_notes": task.completion_notes,
        "auto_generated": task.auto_generated,
        "created_at": task.created_at,
        "assigned_user": {
            "id": user.id,
            "email": user.email,
            "display_name": user.display_name
        } if user else None
    }


@router.patch("/api/evidence-tasks/{task_id}", response_model=EvidenceCollectionTaskResponse)
async def update_evidence_task(
    task_id: UUID,
    task_update: EvidenceCollectionTaskUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """Update an evidence collection task."""
    result = await db.execute(
        select(EvidenceCollectionTask).where(EvidenceCollectionTask.id == task_id)
    )
    task = result.scalar_one_or_none()

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Update fields
    if task_update.status is not None:
        task.status = task_update.status
    if task_update.task_type is not None:
        task.task_type = task_update.task_type
    if task_update.priority is not None:
        task.priority = task_update.priority
    if task_update.title is not None:
        task.title = task_update.title
    if task_update.description is not None:
        task.description = task_update.description
    if task_update.completion_notes is not None:
        task.completion_notes = task_update.completion_notes
    if task_update.completed_date is not None:
        task.completed_date = task_update.completed_date
    if task_update.assigned_user_id is not None:
        task.assigned_user_id = task_update.assigned_user_id
    if task_update.dependencies is not None:
        task.dependencies = task_update.dependencies
    if task_update.attachments is not None:
        task.attachments = task_update.attachments

    await db.commit()
    await db.refresh(task)

    # Get user data
    user = None
    if task.assigned_user_id:
        user_result = await db.execute(
            select(DBUser).where(DBUser.id == task.assigned_user_id)
        )
        user = user_result.scalar_one_or_none()

    return {
        "id": task.id,
        "evidence_tracking_id": task.evidence_tracking_id,
        "due_date": task.due_date,
        "status": task.status,
        "assigned_user_id": task.assigned_user_id,
        "completed_date": task.completed_date,
        "completion_notes": task.completion_notes,
        "auto_generated": task.auto_generated,
        "created_at": task.created_at,
        "assigned_user": {
            "id": user.id,
            "email": user.email,
            "display_name": user.display_name
        } if user else None
    }


@router.post("/api/evidence-tasks/{task_id}/complete", response_model=EvidenceCollectionTaskResponse)
async def complete_evidence_task(
    task_id: UUID,
    completion_notes: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """Mark an evidence collection task as completed."""
    result = await db.execute(
        select(EvidenceCollectionTask).where(EvidenceCollectionTask.id == task_id)
    )
    task = result.scalar_one_or_none()

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Update task
    task.status = 'completed'
    task.completed_date = date.today()
    if completion_notes:
        task.completion_notes = completion_notes

    # Update evidence tracking last_collection_date ONLY for 'collection' type tasks
    # Other task types (feasibility, setup, review, documentation, issue) should not
    # update the actual evidence collection date
    if task.task_type == 'collection':
        result = await db.execute(
            select(EvidenceTracking).where(EvidenceTracking.id == task.evidence_tracking_id)
        )
        evidence = result.scalar_one_or_none()
        if evidence:
            evidence.last_collection_date = date.today()

    await db.commit()
    await db.refresh(task)

    # Get user data
    user = None
    if task.assigned_user_id:
        user_result = await db.execute(
            select(DBUser).where(DBUser.id == task.assigned_user_id)
        )
        user = user_result.scalar_one_or_none()

    return {
        "id": task.id,
        "evidence_tracking_id": task.evidence_tracking_id,
        "due_date": task.due_date,
        "status": task.status,
        "assigned_user_id": task.assigned_user_id,
        "completed_date": task.completed_date,
        "completion_notes": task.completion_notes,
        "auto_generated": task.auto_generated,
        "created_at": task.created_at,
        "assigned_user": {
            "id": user.id,
            "email": user.email,
            "display_name": user.display_name
        } if user else None
    }


@router.get("/api/users/me/dashboard", response_model=dict)
async def get_my_dashboard(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """Get current user's task dashboard with counts and upcoming tasks."""
    if not current_user.db_id:
        return {
            "total_tasks": 0,
            "not_started": 0,
            "in_progress": 0,
            "completed": 0,
            "overdue": 0,
            "upcoming_tasks": []
        }

    user_id = UUID(current_user.db_id)

    # Get all tasks for user
    result = await db.execute(
        select(EvidenceCollectionTask)
        .where(EvidenceCollectionTask.assigned_user_id == user_id)
    )
    all_tasks = result.scalars().all()

    # Calculate counts
    total = len(all_tasks)
    not_started = sum(1 for t in all_tasks if t.status == 'not_started')
    in_progress = sum(1 for t in all_tasks if t.status == 'in_progress')
    completed = sum(1 for t in all_tasks if t.status == 'completed')
    overdue = sum(1 for t in all_tasks if t.due_date < date.today() and t.status != 'completed')

    # Get upcoming tasks (next 30 days, not completed)
    result = await db.execute(
        select(EvidenceCollectionTask)
        .where(
            and_(
                EvidenceCollectionTask.assigned_user_id == user_id,
                EvidenceCollectionTask.status != 'completed',
                EvidenceCollectionTask.due_date >= date.today()
            )
        )
        .order_by(EvidenceCollectionTask.due_date.asc())
        .limit(10)
    )
    upcoming = result.scalars().all()

    upcoming_list = []
    for task in upcoming:
        # Get evidence info with details
        evidence_result = await db.execute(
            select(EvidenceTracking).where(EvidenceTracking.id == task.evidence_tracking_id)
        )
        evidence = evidence_result.scalar_one_or_none()

        upcoming_list.append({
            "id": task.id,
            "evidence_tracking_id": task.evidence_tracking_id,
            "evidence_id": evidence.evidence_id if evidence else None,
            "task_type": task.task_type,
            "title": task.title,
            "description": task.description,
            "priority": task.priority,
            "due_date": task.due_date,
            "status": task.status,
            "days_until_due": (task.due_date - date.today()).days,
            "dependencies": task.dependencies,
            "attachments": task.attachments,
            "frequency": evidence.frequency if evidence else None,
            "collecting_system": evidence.collecting_system if evidence else None,
            "method_of_collection": evidence.method_of_collection if evidence else None,
            "owner": evidence.owner if evidence else None
        })

    return {
        "total_tasks": total,
        "not_started": not_started,
        "in_progress": in_progress,
        "completed": completed,
        "overdue": overdue,
        "upcoming_tasks": upcoming_list
    }
