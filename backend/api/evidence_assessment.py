"""
Evidence AI Assessment API endpoints.

Provides on-demand AI-powered content assessment of evidence files,
evaluating whether uploaded content satisfies mapped control requirements.

Endpoints:
  POST /organizations/{org_id}/evidence/{evidence_id}/files/{file_id}/assess  — Trigger assessment
  GET  /organizations/{org_id}/evidence/{evidence_id}/files/{file_id}/assessment — Get result
  POST /organizations/{org_id}/evidence/{evidence_id}/files/{file_id}/assessment/review — Confirm/override
  GET  /organizations/{org_id}/evidence/{evidence_id}/files/{file_id}/assessment/versions — History
  POST /organizations/{org_id}/evidence/assess-bulk — Bulk assess files
  GET  /organizations/{org_id}/evidence/assessment/review-queue — Files awaiting confirmation
  GET  /organizations/{org_id}/evidence/assessment/summary — Dashboard metrics
"""
import logging
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select, and_, asc, desc, func, case, nullslast
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_org_role, OrgMembership
from database import get_db
from models import EvidenceFile, EvidenceAssessment, EvidenceAssessmentVersion
from services.assessment_prompts import assemble_control_context
from services.assessment_verdict import derive_assessment_status
from services.assurance_policy import get_assurance_policy
from services.audit_service import (
    log_entity_changes,
    get_client_ip,
    get_user_agent,
    detect_action_source,
    get_request_id,
)
from services.review_workflow import SOD_REFUSAL_DETAIL, reviewer_is_sole_uploader
from tasks_assessment import assess_evidence_task, is_cache_hit
from schemas import (
    EvidenceAssessmentResponse,
    EvidenceAssessmentRequest,
    EvidenceAssessmentBulkRequest,
    EvidenceAssessmentSummary,
    EvidenceAssessmentReviewRequest,
    EvidenceAssessmentVersionResponse,
    AssessmentReviewQueueItem,
    AssessmentReviewQueueResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["evidence-assessment"])


#: Statuses a human can be asked to confirm. Everything else is either still in
#: flight (pending, processing) or a failure of the pipeline rather than a
#: judgement about the evidence (error) — asking someone to confirm either
#: would be asking them to endorse a verdict that does not exist yet.
REVIEWABLE_STATUSES = ("sufficient", "partial", "insufficient", "unassessable")

#: What the audit log follows across a confirmation. The review block plus
#: everything an override is allowed to move: a reviewer who changes a verdict
#: from insufficient to sufficient must leave that visible next to their name.
ASSESSMENT_REVIEW_TRACKED_FIELDS = {
    "status",
    "gap_count",
    "cannot_assess_count",
    "review_decision",
    "reviewed_by_user_id",
    "reviewed_at",
}


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

    If a valid assessment already exists — same file, same control context,
    same prompt version — no work is queued and the stored result is returned
    with 200 OK and `cached: true`. Send `force: true` to re-assess anyway.

    Assessment is advisory only — it never changes the review_status.
    """,
    responses={
        200: {"description": "Existing assessment reused; nothing queued (cached: true)"},
        202: {"description": "Assessment queued"},
    },
)
async def trigger_assessment(
    org_id: UUID,
    evidence_id: str,
    file_id: UUID,
    body: EvidenceAssessmentRequest,
    response: Response,
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

    # Cache gate — answer from the stored verdict when nothing that could
    # change it has moved. Checked here as well as in the worker so a repeat
    # click costs one catalog read instead of a queue round-trip, and so the
    # caller is told plainly that no new assessment was run.
    if assessment is not None and not body.force:
        control_context = await assemble_control_context(db, evidence_id)
        if control_context is not None and is_cache_hit(
            status=assessment.status,
            prompt_hash=assessment.prompt_hash,
            stored_context_hash=assessment.control_context_hash,
            stored_prompt_version=assessment.prompt_version,
            file_sha256=evidence_file.computed_sha256 or evidence_file.sha256_hash,
            current_context_hash=control_context.context_hash,
            stored_file_sha256=assessment.assessed_file_sha256,
        ):
            logger.info(
                "Assessment cache hit for file %s (status=%s) — not queueing",
                file_id, assessment.status,
            )
            response.status_code = 200
            return EvidenceAssessmentResponse.from_assessment(assessment, cached=True)

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
        body.force,
    )

    return EvidenceAssessmentResponse.from_assessment(assessment, cached=False)


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

    return EvidenceAssessmentResponse.from_assessment(assessment)


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
            # Without this bucket the others do not sum to total_assessed, and
            # the shortfall reads as a counting bug rather than as the real
            # population it is: files nothing could be read out of.
            func.count(case((EvidenceAssessment.status == "unassessable", 1))).label("unassessable"),
            func.count(
                case((EvidenceAssessment.status.in_(("pending", "processing")), 1))
            ).label("pending"),
            func.count(case((EvidenceAssessment.status == "error", 1))).label("error"),
            # The size of the confirmation queue: terminal verdicts no human
            # has yet stood behind.
            func.count(
                case((
                    and_(
                        EvidenceAssessment.status.in_(REVIEWABLE_STATUSES),
                        EvidenceAssessment.review_decision.is_(None),
                    ),
                    1,
                ))
            ).label("awaiting_review"),
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
        unassessable_count=row.unassessable or 0,
        pending_count=row.pending or 0,
        error_count=row.error or 0,
        unassessed_count=unassessed_count,
        awaiting_review_count=row.awaiting_review or 0,
        average_relevance_score=round(float(row.avg_score), 2) if row.avg_score else None,
        total_cost_cents=round(float(row.total_cost), 4) if row.total_cost else None,
    )


# ---------------------------------------------------------------------------
# Human confirmation of an AI verdict (#881 WS3)
#
# This is a different verb on a different object from the file review in
# api/evidence_files.py. That one answers "do we accept this document as
# evidence"; this one answers "was the machine right about it". A file can be
# approved while its assessment is still an unreviewed suggestion, and the two
# must never be presented as the same act.
# ---------------------------------------------------------------------------

def _effective_ao_findings(
    ao_findings: Optional[List[dict]],
    overrides_by_id: dict,
) -> List[dict]:
    """The AI's objective answers with the human's substituted where given.

    Objectives the reviewer did not touch keep the AI's designation: an
    override is a targeted disagreement, not a wholesale re-authoring, and
    silently blanking the rest would destroy findings nobody objected to.
    """
    effective = []
    for finding in (ao_findings or []):
        entry = dict(finding)
        override = overrides_by_id.get(entry.get("ao_id"))
        if override is not None:
            entry["suggested_designation"] = override.human_designation
            entry["overridden_by_human"] = True
            if override.note:
                entry["override_note"] = override.note
        effective.append(entry)
    return effective


@router.post(
    "/organizations/{org_id}/evidence/{evidence_id}/files/{file_id}/assessment/review",
    response_model=EvidenceAssessmentResponse,
    summary="Confirm or override an AI assessment",
    description="""
    Record a human decision on the current AI verdict for this file.

    `confirmed` means the verdict stands as the AI produced it. `overridden`
    replaces one or more per-objective designations with the reviewer's, and
    requires both a reason and at least one objective — the file's recorded
    status and gap counts are then re-derived from the resulting designations.

    The frozen version row keeps the AI's original answers either way. An
    override is recorded as a disagreement alongside them, never as an edit of
    them.

    One decision per version. Re-assessing a file produces a new version, which
    starts unreviewed again.
    """,
    responses={
        403: {"description": "Segregation of duties: the reviewer is the sole uploader"},
        404: {"description": "No assessment exists for this file"},
        409: {"description": "Verdict is not in a reviewable state, or already decided"},
        422: {"description": "Override names an objective this verdict does not contain"},
    },
)
async def review_assessment(
    org_id: UUID,
    evidence_id: str,
    file_id: UUID,
    body: EvidenceAssessmentReviewRequest,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """
    Confirm or override the AI assessment of an evidence file.
    Requires: editor role or higher.
    """
    result = await db.execute(
        select(EvidenceAssessment).where(
            and_(
                EvidenceAssessment.evidence_file_id == file_id,
                EvidenceAssessment.organization_id == org_id,
                EvidenceAssessment.evidence_id == evidence_id,
            )
        )
    )
    assessment = result.scalar_one_or_none()

    if not assessment:
        raise HTTPException(status_code=404, detail="No assessment found for this file")

    if assessment.status not in REVIEWABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"This assessment is '{assessment.status}' and has no verdict to confirm. "
                f"Reviewable states are: {', '.join(REVIEWABLE_STATUSES)}."
            ),
        )

    if assessment.current_version_id is None:
        # A terminal status with no frozen version means the history this
        # decision would attach to does not exist. Writing the decision onto
        # the mutable row alone would put a review in the record with nothing
        # underneath it saying what was reviewed.
        raise HTTPException(
            status_code=409,
            detail=(
                "This assessment has no recorded version to review. Re-run the "
                "assessment to produce one."
            ),
        )

    version_result = await db.execute(
        select(EvidenceAssessmentVersion).where(
            and_(
                EvidenceAssessmentVersion.id == assessment.current_version_id,
                EvidenceAssessmentVersion.organization_id == org_id,
            )
        )
    )
    version = version_result.scalar_one_or_none()
    if version is None:
        raise HTTPException(
            status_code=409,
            detail="This assessment's current version could not be loaded.",
        )

    if version.review_decision is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"This verdict was already {version.review_decision} and a version "
                "carries one decision. Re-assess the file to produce a new verdict "
                "to review."
            ),
        )

    reviewer_id = UUID(membership.user.db_id)

    # Segregation of duties, opt-in per org. Same helper as the file review
    # path so the two cannot drift on what independence means.
    policy = await get_assurance_policy(db, org_id)
    if policy.require_reviewer_independence:
        uploader_result = await db.execute(
            select(EvidenceFile.uploaded_by_user_id).where(EvidenceFile.id == file_id)
        )
        uploader_id = uploader_result.scalar_one_or_none()
        if reviewer_is_sole_uploader([uploader_id], reviewer_id):
            raise HTTPException(status_code=403, detail=SOD_REFUSAL_DETAIL)

    old_values = {f: getattr(assessment, f) for f in ASSESSMENT_REVIEW_TRACKED_FIELDS}
    decided_at = datetime.utcnow()

    ao_overrides_payload = None
    if body.decision == "overridden":
        known_ao_ids = {
            entry.get("ao_id") for entry in (version.ao_findings or [])
        }
        overrides_by_id = {}
        for item in body.ao_overrides:
            if item.ao_id not in known_ao_ids:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Objective '{item.ao_id}' is not one this assessment answered. "
                        "An override can only disagree with an objective the AI was asked about."
                    ),
                )
            if item.ao_id in overrides_by_id:
                raise HTTPException(
                    status_code=422,
                    detail=f"Objective '{item.ao_id}' is listed more than once.",
                )
            overrides_by_id[item.ao_id] = item

        ai_by_id = {
            entry.get("ao_id"): entry.get("suggested_designation")
            for entry in (version.ao_findings or [])
        }
        # ai_designation comes off the frozen row, never off the request: the
        # record of what the AI said must not be written by the person
        # disagreeing with it.
        ao_overrides_payload = [
            {
                "ao_id": item.ao_id,
                "ai_designation": ai_by_id.get(item.ao_id),
                "human_designation": item.human_designation,
                "note": item.note or "",
            }
            for item in body.ao_overrides
        ]

        effective = _effective_ao_findings(version.ao_findings, overrides_by_id)
        designations = [entry.get("suggested_designation") for entry in effective]
        derived_status, unassessable_reason = derive_assessment_status(designations)

        assessment.ao_findings = effective
        assessment.gap_count = designations.count("gap_identified")
        assessment.cannot_assess_count = designations.count("cannot_assess")
        if derived_status is not None:
            assessment.status = derived_status
            assessment.unassessable_reason = unassessable_reason

    version.review_decision = body.decision
    version.review_reason = body.reason
    version.reviewed_by_user_id = reviewer_id
    version.reviewed_at = decided_at
    version.ao_overrides = ao_overrides_payload

    assessment.review_decision = body.decision
    assessment.reviewed_by_user_id = reviewer_id
    assessment.reviewed_at = decided_at

    new_values = {f: getattr(assessment, f) for f in ASSESSMENT_REVIEW_TRACKED_FIELDS}

    await log_entity_changes(
        db=db,
        organization_id=org_id,
        entity_type="evidence_assessment",
        entity_id=assessment.id,
        action="update",
        changed_by_user_id=reviewer_id,
        old_values=old_values,
        new_values=new_values,
        tracked_fields=ASSESSMENT_REVIEW_TRACKED_FIELDS,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )

    await db.commit()
    await db.refresh(assessment)

    return EvidenceAssessmentResponse.from_assessment(assessment)


# ---------------------------------------------------------------------------
# Review queue
# ---------------------------------------------------------------------------

def build_review_queue_query(org_id: UUID, status_filter: str, limit: int, offset: int):
    """The confirmation queue, worst first.

    Split out from the endpoint so the ordering and the tenancy predicate can
    be asserted without a database. A queue that silently sorted by upload date
    would still look like a queue.

    Severity order is deliberate: the most gaps first (most likely to need a
    human), then the most objectives the AI could not read either way, then the
    least relevant evidence, then the oldest verdict. Relevance sorts ascending
    with nulls last — an unscored file is not the most urgent thing in the list,
    it is a file nobody has a relevance opinion about.
    """
    query = (
        select(
            EvidenceAssessment.evidence_file_id.label("file_id"),
            EvidenceAssessment.evidence_id,
            EvidenceFile.filename,
            EvidenceFile.uploaded_at,
            EvidenceFile.uploaded_by_user_id,
            EvidenceAssessment.status,
            EvidenceAssessment.relevance_score,
            EvidenceAssessment.gap_count,
            EvidenceAssessment.cannot_assess_count,
            EvidenceAssessment.version_number,
            EvidenceAssessment.assessed_at,
            EvidenceAssessment.review_decision,
            EvidenceAssessment.reviewed_at,
        )
        .join(EvidenceFile, EvidenceFile.id == EvidenceAssessment.evidence_file_id)
        .where(
            and_(
                EvidenceAssessment.organization_id == org_id,
                EvidenceAssessment.status.in_(REVIEWABLE_STATUSES),
                EvidenceFile.is_deleted == False,  # noqa: E712
            )
        )
        .order_by(
            desc(EvidenceAssessment.gap_count),
            desc(EvidenceAssessment.cannot_assess_count),
            nullslast(asc(EvidenceAssessment.relevance_score)),
            asc(EvidenceAssessment.assessed_at),
        )
        .limit(limit)
        .offset(offset)
    )

    if status_filter == "awaiting":
        query = query.where(EvidenceAssessment.review_decision.is_(None))
    elif status_filter == "reviewed":
        query = query.where(EvidenceAssessment.review_decision.isnot(None))

    return query


def _review_queue_count_query(org_id: UUID, status_filter: str):
    """How many rows the filter matches in total, not on this page."""
    query = (
        select(func.count(EvidenceAssessment.id))
        .join(EvidenceFile, EvidenceFile.id == EvidenceAssessment.evidence_file_id)
        .where(
            and_(
                EvidenceAssessment.organization_id == org_id,
                EvidenceAssessment.status.in_(REVIEWABLE_STATUSES),
                EvidenceFile.is_deleted == False,  # noqa: E712
            )
        )
    )
    if status_filter == "awaiting":
        query = query.where(EvidenceAssessment.review_decision.is_(None))
    elif status_filter == "reviewed":
        query = query.where(EvidenceAssessment.review_decision.isnot(None))
    return query


REVIEW_QUEUE_FILTERS = ("awaiting", "reviewed", "all")


@router.get(
    "/organizations/{org_id}/evidence/assessment/review-queue",
    response_model=AssessmentReviewQueueResponse,
    summary="Files whose AI assessment is waiting for a human decision",
    description="""
    List AI verdicts by how much they need a person, worst first: most gaps,
    then most objectives that could not be assessed, then least relevant, then
    oldest.

    Viewing the queue needs viewer access; acting on an entry needs editor.
    """,
)
async def get_review_queue(
    org_id: UUID,
    status: str = Query("awaiting", description="awaiting, reviewed, or all"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """
    List evidence files whose AI assessment awaits (or has had) a human decision.
    Requires: viewer role or higher.
    """
    if status not in REVIEW_QUEUE_FILTERS:
        raise HTTPException(
            status_code=422,
            detail=f"status must be one of: {', '.join(REVIEW_QUEUE_FILTERS)}",
        )

    rows = (await db.execute(build_review_queue_query(org_id, status, limit, offset))).all()
    total = (await db.execute(_review_queue_count_query(org_id, status))).scalar() or 0

    return AssessmentReviewQueueResponse(
        items=[
            AssessmentReviewQueueItem(
                file_id=row.file_id,
                evidence_id=row.evidence_id,
                filename=row.filename,
                uploaded_at=row.uploaded_at,
                uploaded_by_user_id=row.uploaded_by_user_id,
                status=row.status,
                relevance_score=float(row.relevance_score) if row.relevance_score is not None else None,
                gap_count=row.gap_count or 0,
                cannot_assess_count=row.cannot_assess_count or 0,
                version_number=row.version_number or 0,
                assessed_at=row.assessed_at,
                review_decision=row.review_decision,
                reviewed_at=row.reviewed_at,
            )
            for row in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


# ---------------------------------------------------------------------------
# Version history
# ---------------------------------------------------------------------------

@router.get(
    "/organizations/{org_id}/evidence/{evidence_id}/files/{file_id}/assessment/versions",
    response_model=List[EvidenceAssessmentVersionResponse],
    summary="Every AI verdict this file has received",
    description="""
    The file's assessment history, newest first. Each entry is frozen as it was
    when the verdict was reached, including the model and prompt version that
    produced it and any human decision recorded against it.
    """,
)
async def list_assessment_versions(
    org_id: UUID,
    evidence_id: str,
    file_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """
    List the frozen assessment versions for a file.
    Requires: viewer role or higher.
    """
    result = await db.execute(
        select(EvidenceAssessmentVersion)
        .where(
            and_(
                EvidenceAssessmentVersion.organization_id == org_id,
                EvidenceAssessmentVersion.evidence_file_id == file_id,
            )
        )
        .order_by(desc(EvidenceAssessmentVersion.version_number))
    )
    versions = result.scalars().all()
    return [EvidenceAssessmentVersionResponse.model_validate(v) for v in versions]
