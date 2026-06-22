"""
Evidence AI Assessment API endpoints.

Provides on-demand AI-powered content assessment of evidence files,
evaluating whether uploaded content satisfies mapped control requirements.

Endpoints:
  POST /organizations/{org_id}/evidence/{evidence_id}/files/{file_id}/assess  — Trigger assessment
  GET  /organizations/{org_id}/evidence/{evidence_id}/files/{file_id}/assessment — Get result
  POST /organizations/{org_id}/evidence/assess-bulk — Bulk assess files
  GET  /organizations/{org_id}/evidence/assessment/summary — Dashboard metrics
"""
import logging
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, and_, func, case
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_org_role, OrgMembership
from database import get_db
from models import EvidenceFile, EvidenceAssessment
from tasks_assessment import assess_evidence_task
from schemas import (
    EvidenceAssessmentResponse,
    EvidenceAssessmentRequest,
    EvidenceAssessmentBulkRequest,
    EvidenceAssessmentSummary,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["evidence-assessment"])


# ---------------------------------------------------------------------------
# POST trigger assessment
# ---------------------------------------------------------------------------

@router.post(
    "/organizations/{org_id}/evidence/{evidence_id}/files/{file_id}/assess",
    response_model=EvidenceAssessmentResponse,
    status_code=202,
    summary="Trigger AI assessment of an evidence file",
    description="""
    Queue an AI-powered content assessment for a specific evidence file.
    The assessment evaluates whether the evidence content satisfies
    the mapped control requirements.

    Returns 202 Accepted with the assessment record in 'pending' or
    'processing' state. Poll the GET endpoint for the result.

    Assessment is advisory only — it never changes the review_status.
    """,
)
async def trigger_assessment(
    org_id: UUID,
    evidence_id: str,
    file_id: UUID,
    body: EvidenceAssessmentRequest,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """
    Trigger AI assessment of an evidence file.
    Requires: editor role or higher.
    """
    # Look up the evidence file
    result = await db.execute(
        select(EvidenceFile).where(
            and_(
                EvidenceFile.id == file_id,
                EvidenceFile.organization_id == org_id,
                EvidenceFile.evidence_id == evidence_id,
                EvidenceFile.is_deleted == False,
            )
        )
    )
    evidence_file = result.scalar_one_or_none()

    if not evidence_file:
        raise HTTPException(status_code=404, detail="Evidence file not found")

    # Create or update assessment record to "pending"
    existing = await db.execute(
        select(EvidenceAssessment).where(
            EvidenceAssessment.evidence_file_id == file_id
        )
    )
    assessment = existing.scalar_one_or_none()

    if not assessment:
        assessment = EvidenceAssessment(
            evidence_file_id=file_id,
            organization_id=org_id,
            evidence_id=evidence_id,
            status="pending",
            assessment_source=body.assessment_source,
            requested_by_user_id=UUID(membership.user.db_id),
        )
        db.add(assessment)
    else:
        assessment.status = "pending"
        assessment.assessment_source = body.assessment_source
        assessment.requested_by_user_id = UUID(membership.user.db_id)

    await db.commit()
    await db.refresh(assessment)

    # Dispatch Celery task (runs in worker, not web server)
    assess_evidence_task.delay(
        str(file_id),
        str(org_id),
        str(membership.user.db_id),
        body.assessment_source,
    )

    return assessment


# ---------------------------------------------------------------------------
# GET assessment result
# ---------------------------------------------------------------------------

@router.get(
    "/organizations/{org_id}/evidence/{evidence_id}/files/{file_id}/assessment",
    response_model=EvidenceAssessmentResponse,
    summary="Get AI assessment result for a file",
    description="Retrieve the AI assessment result for a specific evidence file.",
)
async def get_assessment(
    org_id: UUID,
    evidence_id: str,
    file_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """
    Get the AI assessment result for a specific evidence file.
    Requires: viewer role or higher.
    """
    result = await db.execute(
        select(EvidenceAssessment).where(
            and_(
                EvidenceAssessment.evidence_file_id == file_id,
                EvidenceAssessment.organization_id == org_id,
            )
        )
    )
    assessment = result.scalar_one_or_none()

    if not assessment:
        raise HTTPException(status_code=404, detail="No assessment found for this file")

    return assessment


# ---------------------------------------------------------------------------
# POST bulk assess
# ---------------------------------------------------------------------------

@router.post(
    "/organizations/{org_id}/evidence/assess-bulk",
    status_code=202,
    summary="Bulk assess evidence files",
    description="""
    Queue AI assessments for multiple evidence files. Specify either
    an evidence_id (assess all files for that evidence item) or a list
    of specific file_ids.

    Returns the count of assessments queued.
    """,
)
async def bulk_assess(
    org_id: UUID,
    body: EvidenceAssessmentBulkRequest,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """
    Queue AI assessments for multiple evidence files.
    Requires: editor role or higher.
    """
    if not body.evidence_id and not body.file_ids and not body.assess_unassessed:
        raise HTTPException(
            status_code=422,
            detail="Provide either evidence_id, file_ids, or assess_unassessed",
        )

    # Build query for target files
    query = select(EvidenceFile).where(
        and_(
            EvidenceFile.organization_id == org_id,
            EvidenceFile.is_deleted == False,
        )
    )

    if body.assess_unassessed:
        # Find files that have no existing assessment
        assessed_ids = select(EvidenceAssessment.evidence_file_id).where(
            EvidenceAssessment.organization_id == org_id
        )
        query = query.where(EvidenceFile.id.notin_(assessed_ids))
    elif body.evidence_id:
        query = query.where(EvidenceFile.evidence_id == body.evidence_id)
    elif body.file_ids:
        query = query.where(EvidenceFile.id.in_(body.file_ids))

    result = await db.execute(query)
    files = result.scalars().all()

    if not files:
        raise HTTPException(status_code=404, detail="No evidence files found matching criteria")

    # Cap at 50 files per bulk request to prevent overloading
    capped_files = files[:50]

    # Create pending assessment records and dispatch Celery tasks
    user_id = str(membership.user.db_id)
    for f in capped_files:
        # Create assessment record if not exists
        existing = await db.execute(
            select(EvidenceAssessment).where(
                EvidenceAssessment.evidence_file_id == f.id
            )
        )
        if not existing.scalar_one_or_none():
            assessment = EvidenceAssessment(
                evidence_file_id=f.id,
                organization_id=org_id,
                evidence_id=f.evidence_id,
                status="pending",
                assessment_source="bulk",
                requested_by_user_id=UUID(user_id),
            )
            db.add(assessment)

    await db.commit()

    # Dispatch Celery tasks (runs in workers, not web server)
    from celery import group
    tasks = [
        assess_evidence_task.s(str(f.id), str(org_id), user_id, "bulk")
        for f in capped_files
    ]
    group(tasks).apply_async()

    queued = len(capped_files)
    return {"queued": queued, "message": f"Queued {queued} assessments"}


# ---------------------------------------------------------------------------
# GET assessment summary
# ---------------------------------------------------------------------------

@router.get(
    "/organizations/{org_id}/evidence/assessment/summary",
    response_model=EvidenceAssessmentSummary,
    summary="Get AI assessment summary metrics",
    description="Aggregate AI assessment metrics for the organisation dashboard.",
)
async def get_assessment_summary(
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """
    Aggregate AI assessment metrics for the dashboard.
    Requires: viewer role or higher.
    """
    result = await db.execute(
        select(
            func.count(EvidenceAssessment.id).label("total"),
            func.count(case((EvidenceAssessment.status == "sufficient", 1))).label("sufficient"),
            func.count(case((EvidenceAssessment.status == "partial", 1))).label("partial"),
            func.count(case((EvidenceAssessment.status == "insufficient", 1))).label("insufficient"),
            func.count(case((EvidenceAssessment.status == "pending", 1))).label("pending"),
            func.count(case((EvidenceAssessment.status == "error", 1))).label("error"),
            func.avg(EvidenceAssessment.relevance_score).label("avg_score"),
            func.sum(EvidenceAssessment.cost_cents).label("total_cost"),
        ).where(
            EvidenceAssessment.organization_id == org_id
        )
    )
    row = result.one()

    # Count files that have no assessment yet
    assessed_ids = select(EvidenceAssessment.evidence_file_id).where(
        EvidenceAssessment.organization_id == org_id
    )
    unassessed_result = await db.execute(
        select(func.count(EvidenceFile.id)).where(
            and_(
                EvidenceFile.organization_id == org_id,
                EvidenceFile.is_deleted == False,
                EvidenceFile.id.notin_(assessed_ids),
            )
        )
    )
    unassessed_count = unassessed_result.scalar() or 0

    return EvidenceAssessmentSummary(
        total_assessed=row.total or 0,
        sufficient_count=row.sufficient or 0,
        partial_count=row.partial or 0,
        insufficient_count=row.insufficient or 0,
        pending_count=row.pending or 0,
        error_count=row.error or 0,
        unassessed_count=unassessed_count,
        average_relevance_score=round(float(row.avg_score), 2) if row.avg_score else None,
        total_cost_cents=round(float(row.total_cost), 4) if row.total_cost else None,
    )
