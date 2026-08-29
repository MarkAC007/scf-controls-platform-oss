"""
Evidence Collection Tasks API endpoints - manage evidence collection tasks and dashboard.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, text
from sqlalchemy.orm import selectinload
from typing import List, Optional
from uuid import UUID
from datetime import date, datetime

from database import get_db
from user_display import user_label as _user_label
from auth import require_auth, get_accessible_org_ids, verify_org_membership, assert_user_in_org, User
from models import EvidenceCollectionTask, EvidenceTracking, Team, User as DBUser
from services.audit_service import (
    EVIDENCE_TASK_OWNERSHIP_TRACKED_FIELDS,
    detect_action_source,
    get_request_id,
    log_entity_changes,
)
from services.collection_date import advance_last_collection_date
from services.responsibility import my_task_filter
from schemas import (
    EvidenceCollectionTaskCreate,
    EvidenceCollectionTaskUpdate,
    EvidenceCollectionTaskResponse,
    SuccessResponse
)

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["evidence_tasks"],
    dependencies=[Depends(require_auth)]
)


# ---------------------------------------------------------------------------
# Tenancy helpers
#
# Evidence collection tasks carry `organization_id` since #822 phase 4, but it
# is denormalised from the parent EvidenceTracking row and the composite
# foreign key is what keeps the two in step. These helpers still authorise
# against the parent, so the authorisation decision reads the column the
# constraint is anchored on rather than a copy of it.
# ---------------------------------------------------------------------------

async def _resolve_task_access(
    task_id: UUID,
    current_user: User,
    db: AsyncSession,
    min_role: str = "viewer"
) -> EvidenceCollectionTask:
    """Load a task only if the caller may act on its owning organisation.

    A caller without viewer access gets the same 404 as a caller asking for a
    task that does not exist, so task IDs cannot be probed across tenants. A
    caller who *is* a member but lacks the required role gets a 403 — they can
    already see the task, so there is nothing left to leak.
    """
    result = await db.execute(
        select(EvidenceCollectionTask, EvidenceTracking.organization_id)
        .join(EvidenceTracking, EvidenceCollectionTask.evidence_tracking_id == EvidenceTracking.id)
        .where(EvidenceCollectionTask.id == task_id)
    )
    row = result.first()
    if row is None:
        raise HTTPException(status_code=404, detail="Task not found")

    task, organization_id = row

    try:
        membership = await verify_org_membership(organization_id, current_user, db, "viewer")
    except HTTPException:
        raise HTTPException(status_code=404, detail="Task not found")

    if not membership.has_role(min_role):
        raise HTTPException(
            status_code=403,
            detail=f"Access denied: requires '{min_role}' role on this organisation"
        )

    return task


async def _resolve_evidence_access(
    evidence_tracking_id: UUID,
    current_user: User,
    db: AsyncSession,
    min_role: str = "viewer"
) -> EvidenceTracking:
    """Load an evidence tracking record the caller may act on, else 404/403."""
    result = await db.execute(
        select(EvidenceTracking).where(EvidenceTracking.id == evidence_tracking_id)
    )
    evidence = result.scalar_one_or_none()
    if not evidence:
        raise HTTPException(status_code=404, detail="Evidence tracking record not found")

    try:
        membership = await verify_org_membership(evidence.organization_id, current_user, db, "viewer")
    except HTTPException:
        raise HTTPException(status_code=404, detail="Evidence tracking record not found")

    if not membership.has_role(min_role):
        raise HTTPException(
            status_code=403,
            detail=f"Access denied: requires '{min_role}' role on this organisation"
        )

    return evidence


@router.get("/api/evidence-tasks", response_model=List[dict])
async def list_evidence_tasks(
    status_filter: Optional[str] = Query(None, regex="^(not_started|in_progress|completed)$"),
    assigned_user_id: Optional[UUID] = None,
    overdue_only: bool = False,
    frameworks: Optional[List[str]] = Query(None, description="Filter by SCF framework mapping keys (OR logic)"),
    evidence_tracking_id: Optional[UUID] = Query(
        None, description="Only tasks belonging to this evidence tracking row"
    ),
    organization_id: Optional[UUID] = Query(
        None,
        description=(
            "Only tasks belonging to this organization. An organization the "
            "caller cannot access yields an empty list, indistinguishable "
            "from an org with no tasks."
        ),
    ),
    task_type: Optional[str] = Query(
        None,
        # `pattern`, not the `regex=` used above: that spelling is deprecated
        # and warns on every import.
        pattern="^(feasibility|setup|collection|review|documentation|issue)$",
        description="Only tasks of this type",
    ),
    assigned_to_me: bool = Query(
        False, description="Only tasks assigned to the caller"
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """List evidence collection tasks with optional filters and evidence details.

    Every filter is applied in SQL. Callers must not fetch the unfiltered list
    and narrow it in the client: the response is unpaginated, so that pattern
    grows with the whole organisation's task count rather than with what is
    displayed.
    """
    # Tasks inherit tenancy from their parent EvidenceTracking row
    accessible_org_ids = await get_accessible_org_ids(current_user, db)
    if not accessible_org_ids:
        return []

    # Org-scoped callers (the Tasks page) must not see other orgs' tasks
    # commingled: a consultant on N client orgs viewing org A's Tasks page
    # gets org A's tasks only. Cross-org aggregation remains available by
    # omitting the parameter (personal "my work" style consumers).
    if organization_id is not None:
        if organization_id not in accessible_org_ids:
            # The UI always sends an org the caller can access, so this is
            # either a probe or a stale client. The response stays an empty
            # list (indistinguishable from an org with no tasks); the log line
            # is what lets support tell "no tasks" from "access refused".
            logger.warning(
                "SECURITY: user %s requested evidence tasks for inaccessible org %s",
                current_user.db_id or current_user.email,
                organization_id,
            )
            return []
        org_scope = [organization_id]
    else:
        org_scope = accessible_org_ids

    query = (
        select(EvidenceCollectionTask)
        .join(EvidenceTracking, EvidenceCollectionTask.evidence_tracking_id == EvidenceTracking.id)
        .where(EvidenceTracking.organization_id.in_(org_scope))
    )

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

        query = query.where(EvidenceTracking.evidence_id.in_(framework_erl_ids))

    filters = []
    if status_filter:
        filters.append(EvidenceCollectionTask.status == status_filter)
    if assigned_user_id:
        filters.append(EvidenceCollectionTask.assigned_user_id == assigned_user_id)
    # Both of these were filtered client-side after fetching every task in the
    # org (#788): the evidence panel pulled the whole list to show one row's
    # tasks, and the tasks page pulled it again to filter by type in JS.
    if evidence_tracking_id:
        filters.append(EvidenceCollectionTask.evidence_tracking_id == evidence_tracking_id)
    if task_type:
        filters.append(EvidenceCollectionTask.task_type == task_type)
    # Server-resolved rather than an `assigned_user_id` the client supplies:
    # the frontend's auth context carries placeholder ids ('google_user') before
    # the profile fetch lands, and "my tasks" must never be one stale render away
    # from showing somebody else's queue (#788).
    if assigned_to_me:
        if not current_user.db_id:
            # Authenticated but not persisted (API-key service identity): no
            # task can be assigned to a row that does not exist.
            return []
        # Not `assigned_user_id == me` any more (#822 phase 4). A task whose
        # owner resolves only through a team must appear in that team's
        # people's queue -- otherwise `owning_team_id` is a column written and
        # never read, which is the defect #822 exists to fix. `my_task_filter`
        # is the notification resolution chain as SQL, tier 1 exclusivity
        # included: a task assigned to somebody stays theirs alone.
        filters.append(my_task_filter(UUID(current_user.db_id)))
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
            # owner_user is a relationship; a lazy load here raises
            # MissingGreenlet under async SQLAlchemy (#781).
            select(EvidenceTracking)
            .options(selectinload(EvidenceTracking.owner_user))
            .where(EvidenceTracking.id == task.evidence_tracking_id)
        )
        evidence = evidence_result.scalar_one_or_none()

        task_dict = {
            "id": task.id,
            "evidence_tracking_id": task.evidence_tracking_id,
            # Lets a caller reading the multi-org union (no organization_id
            # param) tell which org each row belongs to.
            "organization_id": task.organization_id,
            "evidence_id": evidence.evidence_id if evidence else None,
            "task_type": task.task_type,
            "title": task.title,
            "description": task.description,
            "priority": task.priority,
            "due_date": task.due_date,
            "status": task.status,
            "assigned_user_id": task.assigned_user_id,
            # NULL means "inherit the evidence item's accountable team", which
            # is not the same as "no team" -- the UI has to be able to tell
            # them apart to render the inherited badge.
            "owning_team_id": task.owning_team_id,
            "completed_date": task.completed_date,
            "completion_notes": task.completion_notes,
            "dependencies": task.dependencies,
            "attachments": task.attachments,
            "auto_generated": task.auto_generated,
            "created_at": task.created_at,
            "frequency": evidence.frequency if evidence else None,
            "collecting_system": evidence.collecting_system if evidence else None,
            "method_of_collection": evidence.method_of_collection if evidence else None,
            # Resolved accountable owner, not the old free-text label (#781).
            # The key name is kept so TaskDashboard keeps rendering; what it
            # carries is now a person, which is the point of the change.
            "owner": _user_label(evidence.owner_user) if evidence else None,
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
    # Verify evidence tracking exists and the caller may write to its organisation
    evidence = await _resolve_evidence_access(
        task_data.evidence_tracking_id, current_user, db, "editor"
    )

    # The assignee must be a member of the task's own organisation, not merely a
    # user that exists somewhere on the platform (#781). Existence alone let an
    # editor assign a task to another tenant's account, which then surfaced this
    # org's evidence IDs in that user's notifications and work queue.
    user = None
    if task_data.assigned_user_id:
        await assert_user_in_org(
            task_data.assigned_user_id, evidence.organization_id, db
        )
        result = await db.execute(
            select(DBUser).where(DBUser.id == task_data.assigned_user_id)
        )
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="Assigned user not found")

    # Create task with enhanced fields
    # #822 phase 4. The create schema accepts an owning team, so the create
    # path has to write it -- a field the API accepts and silently drops is
    # the same defect as a column nothing reads, just one layer up. Validated
    # exactly as the PATCH path validates it, and for the same reason: the
    # composite foreign key `fk_evidence_collection_tasks_team_org` already
    # makes a cross-tenant team unrepresentable, so this lookup buys a 400 the
    # caller can act on instead of a 500 from a constraint violation.
    if task_data.owning_team_id is not None:
        owning_team = (await db.execute(
            select(Team).where(
                and_(
                    Team.id == task_data.owning_team_id,
                    Team.organization_id == evidence.organization_id,
                )
            )
        )).scalar_one_or_none()
        if owning_team is None:
            raise HTTPException(
                status_code=400,
                detail="Owning team not found in this organisation",
            )
        if not owning_team.is_active:
            raise HTTPException(
                status_code=400,
                detail="Owning team is archived and cannot take new work",
            )

    task = EvidenceCollectionTask(
        evidence_tracking_id=task_data.evidence_tracking_id,
        # Denormalised from the parent, never from the caller (#822 §6). The
        # composite foreign key checks the two agree, so a request that named
        # another tenant's evidence item could not smuggle its own org in here
        # — but taking it from the request would still be asking the client a
        # question the parent already answers.
        organization_id=evidence.organization_id,
        due_date=task_data.due_date,
        status=task_data.status,
        assigned_user_id=task_data.assigned_user_id,
        # `None` means inherit the parent evidence item's accountable team.
        owning_team_id=task_data.owning_team_id,
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
        "owning_team_id": task.owning_team_id,
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
    # Defaulted rather than required. FastAPI injects this from the annotation
    # and ignores the default, so served requests always carry provenance; the
    # default exists because this endpoint is also called directly, and adding
    # a required positional argument would have been a breaking change to a
    # signature #822 says to extend additively.
    request: Request = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """Update an evidence collection task.

    #822 phase 4 extends this with ``owning_team_id`` — the task's team
    override. **Additive**: every existing field keeps the contract it had, and
    a caller that does not send ``owning_team_id`` leaves it exactly as it was.

    ``owning_team_id`` needs care that no other field on this model needs,
    because ``null`` is a *meaningful value* rather than an absence:

    * **omitted** — leave the override as it stands.
    * **null** — clear the override, so the task inherits its parent evidence
      item's accountable team. This is the common case and the default state.
    * **a team id** — override, for the setup / collection / review split,
      which on one evidence item is routinely three different functions.

    The ``if x is not None`` idiom the rest of this function uses cannot
    express that, so this one field is read from ``model_fields_set``. Doing it
    the other way round — a sentinel default — would change the OpenAPI schema
    every existing client is generated from.

    **Requires editor**, as it already did, which is #822's permission for
    assigning items to teams. Team membership is not consulted in the
    authorisation decision anywhere in this function: a team is an ownership
    label, and being somebody's primary must not grant a capability their org
    role denies.
    """
    task = await _resolve_task_access(task_id, current_user, db, "editor")
    actor_id = UUID(current_user.db_id) if current_user.db_id else None
    old_ownership = {
        f: getattr(task, f) for f in EVIDENCE_TASK_OWNERSHIP_TRACKED_FIELDS
    }

    # Reassignment had no validation at all before #781 — any UUID was accepted
    # and written straight to the column. Resolve the owning org from the parent
    # tracking row and require membership.
    #
    # Only a CHANGED assignee is validated. TaskEditModal re-sends
    # assigned_user_id with every save, so validating the stored value would make
    # a task un-editable the moment its assignee left the organisation — failing
    # on a field the operator never touched.
    if (
        task_update.assigned_user_id is not None
        and task_update.assigned_user_id != task.assigned_user_id
    ):
        org_result = await db.execute(
            select(EvidenceTracking.organization_id).where(
                EvidenceTracking.id == task.evidence_tracking_id
            )
        )
        task_org_id = org_result.scalar_one_or_none()
        if task_org_id is None:
            raise HTTPException(status_code=404, detail="Task not found")
        await assert_user_in_org(task_update.assigned_user_id, task_org_id, db)

    # `model_fields_set` distinguishes an omitted field from an explicit null;
    # see the docstring. Everything below this block keeps the `is not None`
    # contract it has always had.
    owning_team_supplied = 'owning_team_id' in task_update.model_fields_set
    if owning_team_supplied and task_update.owning_team_id is not None:
        # The composite foreign key `fk_evidence_collection_tasks_team_org`
        # already makes a cross-tenant team unrepresentable, so this lookup is
        # not the isolation control — it is the difference between a 400 the
        # caller can act on and a 500 from a constraint violation. A team from
        # another organisation is reported as not found rather than as
        # forbidden: its existence is not this caller's to learn.
        team = (await db.execute(
            select(Team).where(
                and_(
                    Team.id == task_update.owning_team_id,
                    Team.organization_id == task.organization_id,
                )
            )
        )).scalar_one_or_none()
        if team is None:
            raise HTTPException(
                status_code=400,
                detail="Owning team not found in this organisation",
            )
        if not team.is_active:
            # Archived teams keep their historical assignments (that is the
            # point of archiving rather than deleting) but must not collect
            # new ones, or an org tidying up its structure would silently
            # route fresh work to a team it has stood down.
            raise HTTPException(
                status_code=400,
                detail="Owning team is archived and cannot take new work",
            )

    # Update fields
    if owning_team_supplied:
        task.owning_team_id = task_update.owning_team_id
    if task_update.due_date is not None:
        task.due_date = task_update.due_date
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

    # Every assignment mutation is auditable (#822). Team membership is
    # mutable and unversioned, so without this the question "who owned this
    # task in March?" has no answer -- today's membership silently overwrites
    # March's. `log_entity_changes` emits nothing when nothing changed, so a
    # PATCH that only edits the title writes no ownership audit rows.
    await log_entity_changes(
        db=db,
        organization_id=task.organization_id,
        entity_type='evidence_collection_task',
        entity_id=task.id,
        action='update',
        changed_by_user_id=actor_id,
        old_values=old_ownership,
        new_values={
            f: getattr(task, f) for f in EVIDENCE_TASK_OWNERSHIP_TRACKED_FIELDS
        },
        tracked_fields=EVIDENCE_TASK_OWNERSHIP_TRACKED_FIELDS,
        action_source=detect_action_source(request) if request else None,
        request_id=get_request_id(request) if request else None,
    )

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
        "owning_team_id": task.owning_team_id,
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
    task = await _resolve_task_access(task_id, current_user, db, "editor")

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
        # Today, because completing a collection task IS a person saying they
        # collected it today — unlike an upload, which may be carrying old
        # paperwork. Routed through the shared helper for the monotonic guard
        # only: closing a task must never walk the column backwards behind a
        # more recent collection (#57).
        advance_last_collection_date(evidence, date.today())

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
        "owning_team_id": task.owning_team_id,
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
    """Get current user's task dashboard with counts and upcoming tasks.

    "Mine" here means the same thing it means to the notifier (#822 phase 4):
    assigned to me, or -- when nobody is assigned -- owned by a team I am the
    primary or delegate of. A person who is told about a task by email and
    then cannot find it on their own dashboard has been given a notification
    system, not an ownership model.
    """
    empty_dashboard = {
        "total_tasks": 0,
        "not_started": 0,
        "in_progress": 0,
        "completed": 0,
        "overdue": 0,
        "upcoming_tasks": []
    }

    if not current_user.db_id:
        return empty_dashboard

    # Assignments can outlive membership — scope to orgs the caller can still access
    accessible_org_ids = await get_accessible_org_ids(current_user, db)
    if not accessible_org_ids:
        return empty_dashboard

    user_id = UUID(current_user.db_id)

    # Get all tasks for user
    mine = my_task_filter(user_id)

    result = await db.execute(
        select(EvidenceCollectionTask)
        .join(EvidenceTracking, EvidenceCollectionTask.evidence_tracking_id == EvidenceTracking.id)
        .where(mine)
        .where(EvidenceTracking.organization_id.in_(accessible_org_ids))
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
        .join(EvidenceTracking, EvidenceCollectionTask.evidence_tracking_id == EvidenceTracking.id)
        .where(
            and_(
                mine,
                EvidenceCollectionTask.status != 'completed',
                EvidenceCollectionTask.due_date >= date.today(),
                EvidenceTracking.organization_id.in_(accessible_org_ids)
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
            # owner_user is a relationship; a lazy load here raises
            # MissingGreenlet under async SQLAlchemy (#781).
            select(EvidenceTracking)
            .options(selectinload(EvidenceTracking.owner_user))
            .where(EvidenceTracking.id == task.evidence_tracking_id)
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
            "owner": _user_label(evidence.owner_user) if evidence else None
        })

    return {
        "total_tasks": total,
        "not_started": not_started,
        "in_progress": in_progress,
        "completed": completed,
        "overdue": overdue,
        "upcoming_tasks": upcoming_list
    }
