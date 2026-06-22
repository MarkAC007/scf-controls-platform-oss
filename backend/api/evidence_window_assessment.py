"""
Evidence Windowed Assessment API endpoints (M1a).

Provides on-demand portfolio-level assessment of evidence over a time window
derived from the catalog frequency. Scores the set of files uploaded within
a window against all mapped SCF controls as a single portfolio.

Endpoints:
  POST /organizations/{org_id}/evidence/{evidence_id}/assess-window         — Trigger
  GET  /organizations/{org_id}/evidence/{evidence_id}/window-assessments     — List
  GET  /organizations/{org_id}/evidence/window-assessments/{assessment_id}   — Detail
  POST /organizations/{org_id}/evidence/assess-windows-bulk                 — Bulk
  POST /organizations/{org_id}/evidence/window-assessments/refresh-stale    — Refresh stale
  GET  /organizations/{org_id}/evidence/window-assessments/summary          — Summary
  PUT  /organizations/{org_id}/window-assessments/{ewa_id}/review           — Review (M4 PR 2)
"""
import logging
import os
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, and_, func, case, desc, text
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_org_role, OrgMembership
from database import get_db
from models import EvidenceTracking, EvidenceWindowAssessment
from tasks_window_assessment import assess_window_task
from schemas import (
    EvidenceWindowAssessmentResponse,
    EvidenceWindowAssessmentRequest,
    EvidenceWindowAssessmentBulkRequest,
    EvidenceWindowAssessmentSummary,
    WindowAssessmentReviewRequest,
)
from services.audit_service import (
    log_entity_changes,
    get_request_id,
    detect_action_source,
    WINDOW_ASSESSMENT_TRACKED_FIELDS,
)


# Valid review statuses per ISC-11. ``not_reviewed`` permitted to allow
# revocation without a separate DELETE endpoint.
_VALID_REVIEW_STATUSES = {"approved", "rejected", "needs_revision", "not_reviewed"}

logger = logging.getLogger(__name__)

router = APIRouter(tags=["evidence-window-assessment"])


# ---------------------------------------------------------------------------
# POST trigger windowed assessment
# ---------------------------------------------------------------------------

@router.post(
    "/organizations/{org_id}/evidence/{evidence_id}/assess-window",
    status_code=202,
    summary="Trigger windowed evidence assessment",
    description="""
    Queue a windowed AI assessment for an evidence object. The window is
    derived from the catalog frequency on the EvidenceTracking row for
    this organisation/evidence pair.

    The assessment scores the set of files uploaded within the window as
    a portfolio against all mapped SCF controls. Missing expected artifact
    types surface as coverage gaps.

    Returns 202 Accepted. Poll the list/detail endpoint for the result.

    422 if the evidence has no tracking row or no frequency set.
    """,
)
async def trigger_window_assessment(
    org_id: UUID,
    evidence_id: str,
    body: EvidenceWindowAssessmentRequest,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """
    Trigger a windowed AI assessment.
    Requires: editor role or higher.
    """
    tracking_result = await db.execute(
        select(EvidenceTracking).where(
            and_(
                EvidenceTracking.organization_id == org_id,
                EvidenceTracking.evidence_id == evidence_id,
            )
        )
    )
    tracking = tracking_result.scalar_one_or_none()

    if not tracking:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Evidence {evidence_id} is not tracked for this organisation. "
                f"Use update_evidence to enable tracking and set a frequency first."
            ),
        )

    if not tracking.frequency:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Evidence {evidence_id} has no frequency set. "
                f"Use update_evidence to set a frequency (e.g. daily, weekly, monthly) "
                f"so the assessment window can be computed."
            ),
        )

    user_id = str(membership.user.db_id)
    assess_window_task.delay(
        organization_id=str(org_id),
        evidence_id=evidence_id,
        requested_by_user_id=user_id,
        assessment_source=body.assessment_source,
    )

    return {
        "queued": True,
        "evidence_id": evidence_id,
        "frequency": tracking.frequency,
        "message": f"Windowed assessment queued for {evidence_id}",
    }


# ---------------------------------------------------------------------------
# GET list window assessments for an evidence ID
# ---------------------------------------------------------------------------

@router.get(
    "/organizations/{org_id}/evidence/{evidence_id}/window-assessments",
    response_model=list[EvidenceWindowAssessmentResponse],
    summary="List windowed assessments for evidence",
    description="Return the most recent windowed assessments for a given evidence ID.",
)
async def list_window_assessments(
    org_id: UUID,
    evidence_id: str,
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """
    List windowed assessments (newest first) for an evidence ID.
    Requires: viewer role or higher.
    """
    result = await db.execute(
        select(EvidenceWindowAssessment)
        .where(
            and_(
                EvidenceWindowAssessment.organization_id == org_id,
                EvidenceWindowAssessment.evidence_id == evidence_id,
            )
        )
        .order_by(desc(EvidenceWindowAssessment.assessed_at))
        .offset(offset)
        .limit(limit)
    )
    return result.scalars().all()


# ---------------------------------------------------------------------------
# GET summary metrics — MUST be registered before the {assessment_id} route
# below so FastAPI's path matcher resolves the literal ``summary`` segment
# correctly. Otherwise ``GET .../window-assessments/summary`` binds to the
# {assessment_id} UUID slot and returns 422 ("'summary' is not a valid UUID").
# ---------------------------------------------------------------------------

@router.get(
    "/organizations/{org_id}/evidence/window-assessments/summary",
    response_model=EvidenceWindowAssessmentSummary,
    summary="Windowed assessment summary metrics",
    description="Aggregate windowed-assessment metrics for the organisation.",
)
async def get_window_assessment_summary(
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """
    Aggregate windowed-assessment metrics.
    Requires: viewer role or higher.
    """
    result = await db.execute(
        select(
            func.count(EvidenceWindowAssessment.id).label("total"),
            func.count(case((EvidenceWindowAssessment.status == "sufficient", 1))).label("sufficient"),
            func.count(case((EvidenceWindowAssessment.status == "partial", 1))).label("partial"),
            func.count(case((EvidenceWindowAssessment.status == "insufficient", 1))).label("insufficient"),
            func.count(case((EvidenceWindowAssessment.status == "insufficient_sample", 1))).label("insufficient_sample"),
            func.count(case((EvidenceWindowAssessment.status == "pending", 1))).label("pending"),
            func.count(case((EvidenceWindowAssessment.status == "error", 1))).label("error"),
            func.avg(EvidenceWindowAssessment.relevance_score).label("avg_score"),
            func.sum(EvidenceWindowAssessment.cost_cents).label("total_cost"),
        ).where(EvidenceWindowAssessment.organization_id == org_id)
    )
    row = result.one()

    return EvidenceWindowAssessmentSummary(
        total_windows_assessed=row.total or 0,
        sufficient_count=row.sufficient or 0,
        partial_count=row.partial or 0,
        insufficient_count=row.insufficient or 0,
        insufficient_sample_count=row.insufficient_sample or 0,
        pending_count=row.pending or 0,
        error_count=row.error or 0,
        average_relevance_score=round(float(row.avg_score), 2) if row.avg_score else None,
        total_cost_cents=round(float(row.total_cost), 4) if row.total_cost else None,
    )


# ---------------------------------------------------------------------------
# GET single window assessment detail
# ---------------------------------------------------------------------------

@router.get(
    "/organizations/{org_id}/evidence/window-assessments/{assessment_id}",
    response_model=EvidenceWindowAssessmentResponse,
    summary="Get a windowed assessment by ID",
    description="Retrieve a single windowed assessment detail.",
)
async def get_window_assessment(
    org_id: UUID,
    assessment_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """
    Get a single windowed assessment by ID.
    Requires: viewer role or higher.
    """
    result = await db.execute(
        select(EvidenceWindowAssessment).where(
            and_(
                EvidenceWindowAssessment.id == assessment_id,
                EvidenceWindowAssessment.organization_id == org_id,
            )
        )
    )
    assessment = result.scalar_one_or_none()

    if not assessment:
        raise HTTPException(status_code=404, detail="Windowed assessment not found")

    return assessment


# ---------------------------------------------------------------------------
# POST bulk windowed assessments
# ---------------------------------------------------------------------------

@router.post(
    "/organizations/{org_id}/evidence/assess-windows-bulk",
    status_code=202,
    summary="Bulk trigger windowed assessments",
    description="""
    Queue windowed assessments for multiple evidence IDs. Capped at 25 per
    request. Each evidence ID must have tracking with a frequency set or
    that specific item is skipped and reported in the response.
    """,
)
async def bulk_trigger_window_assessments(
    org_id: UUID,
    body: EvidenceWindowAssessmentBulkRequest,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """
    Queue windowed assessments for multiple evidence IDs.
    Requires: editor role or higher.
    """
    tracking_result = await db.execute(
        select(EvidenceTracking).where(
            and_(
                EvidenceTracking.organization_id == org_id,
                EvidenceTracking.evidence_id.in_(body.evidence_ids),
            )
        )
    )
    tracking_rows = {t.evidence_id: t for t in tracking_result.scalars().all()}

    queued: list[str] = []
    skipped: list[dict] = []
    user_id = str(membership.user.db_id)

    for evidence_id in body.evidence_ids:
        tracking = tracking_rows.get(evidence_id)
        if not tracking:
            skipped.append({"evidence_id": evidence_id, "reason": "no tracking row"})
            continue
        if not tracking.frequency:
            skipped.append({"evidence_id": evidence_id, "reason": "no frequency set"})
            continue

        assess_window_task.delay(
            organization_id=str(org_id),
            evidence_id=evidence_id,
            requested_by_user_id=user_id,
            assessment_source="bulk",
        )
        queued.append(evidence_id)

    return {
        "queued": len(queued),
        "skipped": len(skipped),
        "queued_evidence_ids": queued,
        "skipped_detail": skipped,
    }


# ---------------------------------------------------------------------------
# POST refresh stale window assessments — same selection criteria as nightly
# ---------------------------------------------------------------------------

# Same default cap as ``tasks_window_assessment.NIGHTLY_REFRESH_CAP`` so the
# manual button and the 04:00 UTC beat task behave identically. Kept in sync
# via env override.
_REFRESH_STALE_CAP = int(os.getenv("WINDOW_ASSESSMENT_NIGHTLY_CAP", "100"))


@router.post(
    "/organizations/{org_id}/evidence/window-assessments/refresh-stale",
    status_code=202,
    summary="Reassess all stale window assessments",
    description=(
        "Find every (evidence_id) for the org where the latest file landed "
        "after the latest window assessment (or no assessment exists yet) "
        "and queue a fresh ``assess_window_task`` for each. Capped at "
        "WINDOW_ASSESSMENT_NIGHTLY_CAP (default 100). Same selection logic "
        "as the nightly beat task — exposed manually so reviewers can refresh "
        "without waiting for 04:00 UTC."
    ),
)
async def refresh_stale_window_assessments(
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    user_id = str(membership.user.db_id)
    rows = (
        await db.execute(
            text(
                """
                WITH per_evidence_latest AS (
                    SELECT ef.evidence_id,
                           MAX(ef.uploaded_at) AS latest_file_at
                      FROM evidence_files ef
                     WHERE ef.is_deleted = false
                       AND ef.organization_id = :org_id
                     GROUP BY ef.evidence_id
                ),
                per_window_latest AS (
                    SELECT ewa.evidence_id,
                           MAX(ewa.assessed_at) AS latest_assessed_at
                      FROM evidence_window_assessments ewa
                     WHERE ewa.organization_id = :org_id
                     GROUP BY ewa.evidence_id
                )
                SELECT pe.evidence_id
                  FROM per_evidence_latest pe
             LEFT JOIN per_window_latest pw USING (evidence_id)
                 WHERE pw.latest_assessed_at IS NULL
                    OR pe.latest_file_at > pw.latest_assessed_at
                 ORDER BY pe.latest_file_at ASC
                 LIMIT :cap
                """
            ),
            {"org_id": str(org_id), "cap": _REFRESH_STALE_CAP},
        )
    ).mappings().all()

    candidate_ids = [r["evidence_id"] for r in rows]

    tracking_result = await db.execute(
        select(EvidenceTracking).where(
            and_(
                EvidenceTracking.organization_id == org_id,
                EvidenceTracking.evidence_id.in_(candidate_ids),
            )
        )
    ) if candidate_ids else None
    tracking_rows = (
        {t.evidence_id: t for t in tracking_result.scalars().all()}
        if tracking_result is not None
        else {}
    )

    queued: list[str] = []
    skipped: list[dict] = []
    for evidence_id in candidate_ids:
        tracking = tracking_rows.get(evidence_id)
        if not tracking or not tracking.frequency:
            skipped.append(
                {"evidence_id": evidence_id, "reason": "no tracking row or frequency"}
            )
            continue
        assess_window_task.delay(
            organization_id=str(org_id),
            evidence_id=evidence_id,
            requested_by_user_id=user_id,
            assessment_source="auto",
        )
        queued.append(evidence_id)

    return {
        "queued": len(queued),
        "skipped": len(skipped),
        "candidates": len(candidate_ids),
        "cap": _REFRESH_STALE_CAP,
        "queued_evidence_ids": queued,
        "skipped_detail": skipped,
    }


# ---------------------------------------------------------------------------
# PUT review window assessment (M4 PR 2, #574 — ISC-10..16)
# ---------------------------------------------------------------------------

@router.put(
    "/organizations/{org_id}/window-assessments/{ewa_id}/review",
    response_model=EvidenceWindowAssessmentResponse,
    summary="Review a windowed evidence assessment",
    description="""
    Set the review state of a windowed evidence assessment. Replaces the
    legacy per-file review path when ``ENABLE_PER_WINDOW_REVIEW`` is on.

    Valid ``review_status`` values: ``approved``, ``rejected``,
    ``needs_revision``, ``not_reviewed``. Transitions are unrestricted
    (any → any) and idempotent. Audit log captures `old → new`.

    422 on invalid ``review_status``. 404 if the EWA row is missing or
    belongs to a different organization.
    """,
)
async def review_window_assessment(
    org_id: UUID,
    ewa_id: UUID,
    body: WindowAssessmentReviewRequest,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """Set review status on a windowed evidence assessment.

    Requires: editor role or higher (matches legacy per-file review and
    M1a window assessment write paths — ISC-12).
    """
    if body.review_status not in _VALID_REVIEW_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=(
                "review_status must be one of: "
                f"{', '.join(sorted(_VALID_REVIEW_STATUSES))}"
            ),
        )

    result = await db.execute(
        select(EvidenceWindowAssessment).where(
            and_(
                EvidenceWindowAssessment.id == ewa_id,
                EvidenceWindowAssessment.organization_id == org_id,
            )
        )
    )
    ewa = result.scalar_one_or_none()

    if not ewa:
        raise HTTPException(status_code=404, detail="Window assessment not found")

    old_values = {f: getattr(ewa, f) for f in WINDOW_ASSESSMENT_TRACKED_FIELDS}

    ewa.review_status = body.review_status
    ewa.reviewed_by_user_id = UUID(membership.user.db_id)
    ewa.reviewed_at = datetime.utcnow()
    ewa.review_notes = body.review_notes

    new_values = {f: getattr(ewa, f) for f in WINDOW_ASSESSMENT_TRACKED_FIELDS}

    await log_entity_changes(
        db=db,
        organization_id=org_id,
        entity_type="evidence_window_assessment",
        entity_id=ewa.id,
        action="update",
        changed_by_user_id=UUID(membership.user.db_id),
        old_values=old_values,
        new_values=new_values,
        tracked_fields=WINDOW_ASSESSMENT_TRACKED_FIELDS,
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )

    await db.commit()
    await db.refresh(ewa)

    # M4 PR 3 (D2): ``needs_revision`` dispatches a fresh window assessment
    # via Celery. Non-blocking — the request returns immediately; the new
    # assessment is created asynchronously by the worker. Dispatch failure
    # does NOT roll back the review mutation (the audit trail is correct;
    # the nightly refresh will eventually pick up the evidence anyway).
    if body.review_status == "needs_revision":
        try:
            assess_window_task.apply_async(
                kwargs={
                    "organization_id": str(ewa.organization_id),
                    "evidence_id": ewa.evidence_id,
                    "requested_by_user_id": membership.user.db_id,
                    "assessment_source": "review_revision",
                },
            )
        except Exception as exc:  # noqa: BLE001 — Celery dispatch best-effort
            logger.warning(
                "review endpoint: needs_revision dispatch failed ewa=%s: %s",
                ewa.id,
                exc,
            )

    return ewa
