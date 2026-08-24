"""
GRC Dashboard API endpoints - work queue and operational overview.
"""
import logging
from typing import List, Optional
from uuid import UUID
from datetime import date
from datetime import date as DateType

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from database import get_db
from auth import require_org_viewer, OrgMembership
from models import EvidenceCollectionTask, ScopedControl, EvidenceTracking
from services.responsibility import my_item_filter, my_task_filter
from services.team_assignments import (
    CONTROL_ASSIGNMENT_SPEC,
    EVIDENCE_ASSIGNMENT_SPEC,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class OverdueEvidenceItem(BaseModel):
    task_id: str
    evidence_id: str
    #: The parent evidence item's UUID (#822 phase 4). ``evidence_id`` beside
    #: it is the human-facing reference, which is not a key -- so without this
    #: a client holding an overdue row cannot ask who owns it. A task that
    #: inherits its team (``owning_team_id IS NULL``, the common case) resolves
    #: ownership entirely through the parent, and this is the only handle on it.
    evidence_tracking_id: str
    title: Optional[str] = None
    due_date: DateType
    days_overdue: int
    priority: Optional[str] = None


class BlockingControlItem(BaseModel):
    scf_id: str
    implementation_status: str
    days_stale: int


class StaleCollectionItem(BaseModel):
    evidence_id: str
    next_collection_date: DateType
    days_overdue: int


class WorkQueueResponse(BaseModel):
    overdue_evidence: List[OverdueEvidenceItem]
    blocking_controls: List[BlockingControlItem]
    stale_collections: List[StaleCollectionItem]
    total_items: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/organizations/{org_id}/dashboard/work-queue",
    response_model=WorkQueueResponse,
)
async def get_work_queue(
    org_id: UUID,
    assigned_to_me: bool = False,
    membership: OrgMembership = Depends(require_org_viewer),
    db: AsyncSession = Depends(get_db),
):
    """
    Return the consolidated GRC work queue for the organisation.

    Aggregates three categories of actionable items:
    1. Overdue evidence collection tasks
    2. Blocking (not_started / at_risk) scoped controls
    3. Stale evidence collection schedules past their next collection date
       for which no overdue collection task exists

    Categories 1 and 3 are two readings of one underlying fact and used to
    overlap completely (#809). ``evidence_tracking.next_collection_date`` is
    written in exactly one place -- ``task_generator.generate_task_for_tracking``
    -- to the same value as the ``due_date`` of the task it creates on the same
    line, so every lapsed schedule with a live task was reported twice, once per
    heading, with identical ids in identical order.

    They are kept as separate sections but their membership is now disjoint,
    and the split carries a meaning a reader can act on:

    * **Overdue evidence** -- the collection window has lapsed and there IS an
      open task to work. The unit of work is the task; complete it.
    * **Stale collections** -- the window has lapsed and there is NO open task.
      Nothing is queued to fix it, so the unit of work is to find out why
      (an unrecognised frequency, a tracking row nobody generated against) and
      get a task onto it.

    An evidence item can satisfy only one of those by construction, which is
    what makes ``total_items`` below a real count rather than a sum of
    overlapping sets.

    With ``assigned_to_me=true``, every category is limited to the calling
    user: tasks to those assigned to them, and controls and evidence schedules
    to those where they are the owner or the assignee.

    All three narrow together on purpose. The caller is one checkbox in the UI
    with one label; a category that ignores it silently mixes other people's
    work into a list the user believes is theirs.

    #822 phase 4 widens what "the calling user" resolves to, without widening
    it silently. "Mine" is now the ownership chain the notifier uses: the
    explicit owner or assignee, and -- **only when there is no explicit owner
    or assignee at all** -- the accountable team's primary and delegate. An
    item that still names a person stays that person's alone, so an
    organisation that has created no teams sees precisely the queue it sees
    today, and marking a team accountable never quietly adds an already-owned
    item to two more people's lists.

    Tier 3 is deliberately absent from every branch. Falling a *queue* through
    to "every org admin" would put every unowned item in the organisation on
    the list of everyone able to fix that, which is the unfiltered view with
    extra steps. Tier 3 stays the last resort for telling somebody an item has
    no owner, which is a notification, not a queue.
    """
    today = date.today()

    caller_id: Optional[UUID] = (
        UUID(membership.user.db_id)
        if assigned_to_me and membership.user.db_id
        else None
    )

    # ------------------------------------------------------------------
    # 1. Overdue evidence collection tasks
    # ------------------------------------------------------------------
    overdue_query = (
        select(
            EvidenceCollectionTask.id,
            EvidenceCollectionTask.title,
            EvidenceCollectionTask.due_date,
            EvidenceCollectionTask.priority,
            EvidenceTracking.evidence_id,
            EvidenceTracking.id.label("evidence_tracking_id"),
        )
        .join(
            EvidenceTracking,
            EvidenceCollectionTask.evidence_tracking_id == EvidenceTracking.id,
        )
        .where(
            and_(
                EvidenceTracking.organization_id == org_id,
                EvidenceCollectionTask.due_date < today,
                EvidenceCollectionTask.status != "completed",
            )
        )
        .order_by(EvidenceCollectionTask.due_date.asc())
        .limit(20)
    )
    if caller_id is not None:
        overdue_query = overdue_query.where(my_task_filter(caller_id))

    overdue_result = await db.execute(overdue_query)
    overdue_rows = overdue_result.all()

    overdue_evidence = [
        OverdueEvidenceItem(
            task_id=str(row.id),
            evidence_id=row.evidence_id,
            evidence_tracking_id=str(row.evidence_tracking_id),
            title=row.title,
            due_date=row.due_date,
            days_overdue=(today - row.due_date).days,
            priority=row.priority,
        )
        for row in overdue_rows
    ]

    # ------------------------------------------------------------------
    # 2. Blocking controls (not_started or at_risk)
    # ------------------------------------------------------------------
    blocking_query = (
        select(
            ScopedControl.scf_id,
            ScopedControl.implementation_status,
            ScopedControl.updated_at,
        )
        .where(
            and_(
                ScopedControl.organization_id == org_id,
                ScopedControl.selected == True,  # noqa: E712
                ScopedControl.implementation_status.in_(["not_started", "at_risk"]),
            )
        )
        .order_by(ScopedControl.updated_at.asc())
        .limit(20)
    )
    if caller_id is not None:
        blocking_query = blocking_query.where(
            my_item_filter(
                CONTROL_ASSIGNMENT_SPEC,
                ScopedControl.id,
                organization_id=org_id,
                user_id=caller_id,
                owner_column=ScopedControl.owner_user_id,
                assignee_column=ScopedControl.assigned_user_id,
            )
        )

    blocking_result = await db.execute(blocking_query)
    blocking_rows = blocking_result.all()

    blocking_controls = [
        BlockingControlItem(
            scf_id=row.scf_id,
            implementation_status=row.implementation_status,
            days_stale=(today - row.updated_at.date()).days if row.updated_at else 0,
        )
        for row in blocking_rows
    ]

    # ------------------------------------------------------------------
    # 3. Stale evidence collections
    # ------------------------------------------------------------------
    # The exact population of section 1, expressed as a correlated subquery:
    # "this tracking row already has an open collection task that is past due".
    # Excluding it here is what makes the two evidence sections disjoint (#809).
    #
    # It is a NOT EXISTS rather than a post-filter against the evidence_ids
    # fetched above because section 1 is capped at 20 rows. Filtering against
    # that page would let overdue item 21 onwards reappear under the other
    # heading -- the same double count, just further down the list.
    overdue_task_exists = select(EvidenceCollectionTask.id).where(
        and_(
            EvidenceCollectionTask.evidence_tracking_id == EvidenceTracking.id,
            EvidenceCollectionTask.due_date < today,
            EvidenceCollectionTask.status != "completed",
        )
    )
    if caller_id is not None:
        # Narrow the exclusion the same way section 1 is narrowed. Without this,
        # a row the caller owns whose overdue task is assigned to somebody else
        # would be hidden from both sections instead of shown in exactly one.
        overdue_task_exists = overdue_task_exists.where(my_task_filter(caller_id))

    stale_query = (
        select(
            EvidenceTracking.evidence_id,
            EvidenceTracking.next_collection_date,
        )
        .where(
            and_(
                EvidenceTracking.organization_id == org_id,
                EvidenceTracking.next_collection_date < today,
                EvidenceTracking.is_tracked == True,  # noqa: E712
                ~overdue_task_exists.correlate(EvidenceTracking).exists(),
            )
        )
        .order_by(EvidenceTracking.next_collection_date.asc())
        .limit(20)
    )

    if caller_id is not None:
        # Same ownership test as the blocking-controls branch above. "Mine" has
        # to mean one thing across the whole queue, and evidence_tracking
        # carries the same owner/assignee pair that scoped_controls does.
        stale_query = stale_query.where(
            my_item_filter(
                EVIDENCE_ASSIGNMENT_SPEC,
                EvidenceTracking.id,
                organization_id=org_id,
                user_id=caller_id,
                owner_column=EvidenceTracking.owner_user_id,
                assignee_column=EvidenceTracking.assigned_user_id,
            )
        )

    stale_result = await db.execute(stale_query)
    stale_rows = stale_result.all()

    stale_collections = [
        StaleCollectionItem(
            evidence_id=row.evidence_id,
            next_collection_date=row.next_collection_date,
            days_overdue=(today - row.next_collection_date).days,
        )
        for row in stale_rows
    ]

    # ------------------------------------------------------------------
    # Build response
    # ------------------------------------------------------------------
    # Distinct union of the item identities on show, NOT a sum of section
    # lengths (#809). A sum is only ever correct while every section is
    # provably disjoint from every other, which is a property no future edit is
    # obliged to preserve; counting identities is correct either way. Keys are
    # namespaced because a control id and an evidence id are different things
    # that happen to be strings.
    distinct_items = (
        {("evidence", item.evidence_id) for item in overdue_evidence}
        | {("control", item.scf_id) for item in blocking_controls}
        | {("evidence", item.evidence_id) for item in stale_collections}
    )
    total_items = len(distinct_items)

    return WorkQueueResponse(
        overdue_evidence=overdue_evidence,
        blocking_controls=blocking_controls,
        stale_collections=stale_collections,
        total_items=total_items,
    )
