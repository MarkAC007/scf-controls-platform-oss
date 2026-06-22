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
from auth import require_org_role, OrgMembership
from models import EvidenceCollectionTask, ScopedControl, EvidenceTracking

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class OverdueEvidenceItem(BaseModel):
    task_id: str
    evidence_id: str
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
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """
    Return the consolidated GRC work queue for the organisation.

    Aggregates three categories of actionable items:
    1. Overdue evidence collection tasks
    2. Blocking (not_started / at_risk) scoped controls
    3. Stale evidence collection schedules past their next collection date
    """
    today = date.today()

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

    overdue_result = await db.execute(overdue_query)
    overdue_rows = overdue_result.all()

    overdue_evidence = [
        OverdueEvidenceItem(
            task_id=str(row.id),
            evidence_id=row.evidence_id,
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
            )
        )
        .order_by(EvidenceTracking.next_collection_date.asc())
        .limit(20)
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
    total_items = len(overdue_evidence) + len(blocking_controls) + len(stale_collections)

    return WorkQueueResponse(
        overdue_evidence=overdue_evidence,
        blocking_controls=blocking_controls,
        stale_collections=stale_collections,
        total_items=total_items,
    )
