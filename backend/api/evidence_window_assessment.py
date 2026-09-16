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
  POST /organizations/{org_id}/evidence/window-assessments/{assessment_id}/verdict/review — Confirm/override
  GET  /organizations/{org_id}/evidence/window-assessments/{assessment_id}/versions       — History
"""
import logging
import os
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, and_, func, case, desc, text
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_org_role, OrgMembership
from database import get_db
from models import (
    EvidenceFile,
    EvidenceTracking,
    EvidenceWindowAssessment,
    EvidenceWindowAssessmentVersion,
)
from tasks_window_assessment import assess_window_task
from schemas import (
    EvidenceAssessmentReviewRequest,
    EvidenceWindowAssessmentResponse,
    EvidenceWindowAssessmentRequest,
    EvidenceWindowAssessmentBulkRequest,
    EvidenceWindowAssessmentSummary,
    EvidenceWindowAssessmentVersionResponse,
    WindowAssessmentReviewRequest,
)
from services.assessment_verdict import derive_assessment_status
from services.audit_service import (
    log_entity_changes,
    get_client_ip,
    get_request_id,
    get_user_agent,
    detect_action_source,
    WINDOW_ASSESSMENT_TRACKED_FIELDS,
)
from services.assurance_policy import get_assurance_policy
from services.notifications import create_evidence_rejected_notifications
from services.review_workflow import (
    SOD_REFUSAL_DETAIL,
    reviewer_is_sole_uploader,
    transition_allowed,
    transition_error,
)


# Valid review statuses per ISC-11. ``not_reviewed`` permitted to allow
# revocation without a separate DELETE endpoint.
_VALID_REVIEW_STATUSES = {"approved", "rejected", "needs_revision", "not_reviewed"}

#: Window statuses a human can be asked to confirm. ``insufficient_sample``
#: is included because it is a verdict about the window (too few files to
#: judge the cadence), not a pipeline failure; ``error`` and the in-flight
#: states are not, because asking someone to confirm them would be asking
#: them to endorse a verdict that does not exist.
WINDOW_REVIEWABLE_STATUSES = ("sufficient", "partial", "insufficient", "insufficient_sample", "unassessable")

#: What the audit log follows across a verdict confirmation: the decision
#: block plus everything an override is allowed to move.
WINDOW_VERDICT_REVIEW_TRACKED_FIELDS = {
    "status",
    "gap_count",
    "cannot_assess_count",
    "review_decision",
    "verdict_reviewed_by_user_id",
    "verdict_reviewed_at",
}

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
            func.count(case((EvidenceWindowAssessment.status == "unassessable", 1))).label("unassessable"),
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
        unassessable_count=row.unassessable or 0,
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
    ``needs_revision``, ``not_reviewed``. Re-sending the current status is
    idempotent. Audit log captures `old → new`.

    Transitions are constrained (#803): a rejection cannot become an
    approval in one step — route it through ``needs_revision`` so the
    re-assessment is on the record.

    Where the organization has set ``require_reviewer_independence``, a
    reviewer who is the *sole* uploader of the files in the window is
    refused.

    422 on invalid ``review_status``. 404 if the EWA row is missing or
    belongs to a different organization. 409 on a disallowed transition.
    403 on a segregation-of-duties refusal.
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

    # D9 — constrained transitions, for every org. This is not a policy
    # opt-in: an audit trail in which a rejection becomes an approval with
    # no step in between records that a decision changed without recording
    # that anything was reconsidered.
    if not transition_allowed(ewa.review_status, body.review_status):
        raise HTTPException(
            status_code=409,
            detail=transition_error(ewa.review_status, body.review_status),
        )

    reviewer_id = UUID(membership.user.db_id)

    # D8 — segregation of duties, opt-in per org. Only an approval is
    # gated: sending your own evidence back for revision, or rejecting it,
    # takes nothing on trust, and blocking it would just leave
    # single-handed teams unable to withdraw a mistake.
    policy = await get_assurance_policy(db, org_id)
    if policy.require_reviewer_independence and body.review_status == "approved":
        file_ids = [str(f) for f in (ewa.file_ids or [])]
        if file_ids:
            uploader_rows = await db.execute(
                select(EvidenceFile.uploaded_by_user_id).where(
                    and_(
                        EvidenceFile.organization_id == org_id,
                        EvidenceFile.id.in_(file_ids),
                    )
                )
            )
            if reviewer_is_sole_uploader(
                uploader_rows.scalars().all(), reviewer_id
            ):
                raise HTTPException(status_code=403, detail=SOD_REFUSAL_DETAIL)

    old_values = {f: getattr(ewa, f) for f in WINDOW_ASSESSMENT_TRACKED_FIELDS}

    ewa.review_status = body.review_status
    ewa.reviewed_by_user_id = reviewer_id
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

    if body.review_status == "rejected":
        await create_evidence_rejected_notifications(
            db,
            organization_id=org_id,
            evidence_id=ewa.evidence_id,
            rejected_by_user_id=UUID(membership.user.db_id),
        )

    return ewa


# ---------------------------------------------------------------------------
# Verdict confirmation — confirm / override (parity with the per-file layer)
# ---------------------------------------------------------------------------

def _effective_window_ao_findings(ao_findings: Optional[List[dict]], overrides_by_id: dict) -> List[dict]:
    """The AI's objective answers with the human's substituted where given.

    Objectives the reviewer did not touch keep the AI's designation and its
    file attribution: an override is a targeted disagreement, not a
    wholesale re-authoring.
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
    "/organizations/{org_id}/evidence/window-assessments/{assessment_id}/verdict/review",
    response_model=EvidenceWindowAssessmentResponse,
    summary="Confirm or override the AI verdict on a window",
    description="""
    Record a human decision on the current AI verdict for this window.

    `confirmed` means the verdict stands as the AI produced it. `overridden`
    replaces one or more per-objective designations with the reviewer's, and
    requires both a reason and at least one objective — the window's recorded
    status and gap counts are then re-derived from the resulting designations.
    An `insufficient_sample` window keeps that status through an override: the
    sample size is a fact about the window, not a designation.

    The frozen version row keeps the AI's original answers either way. An
    override is recorded as a disagreement alongside them, never as an edit.

    This is independent of the acceptance review (`PUT .../review`): that
    says what the organisation decided to do with the evidence; this says
    whether a person has stood behind the AI's reading of it.

    One decision per version. Re-assessing the window produces a new version,
    which starts unreviewed again.
    """,
    responses={
        403: {"description": "Segregation of duties: the reviewer is the sole uploader of the files in the window"},
        404: {"description": "No such window assessment in this organisation"},
        409: {"description": "Verdict is not in a reviewable state, has no version, or is already decided"},
        422: {"description": "Override names an objective this verdict does not contain"},
    },
)
async def review_window_verdict(
    org_id: UUID,
    assessment_id: UUID,
    body: EvidenceAssessmentReviewRequest,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """Confirm or override the AI verdict on a window assessment. Requires: editor."""
    result = await db.execute(
        select(EvidenceWindowAssessment).where(
            and_(
                EvidenceWindowAssessment.id == assessment_id,
                EvidenceWindowAssessment.organization_id == org_id,
            )
        )
    )
    ewa = result.scalar_one_or_none()
    if not ewa:
        raise HTTPException(status_code=404, detail="Window assessment not found")

    if ewa.status not in WINDOW_REVIEWABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"This window assessment is '{ewa.status}' and has no verdict to confirm. "
                f"Reviewable states are: {', '.join(WINDOW_REVIEWABLE_STATUSES)}."
            ),
        )

    if ewa.current_version_id is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "This window assessment has no recorded version to review. Re-run the "
                "assessment to produce one."
            ),
        )

    version_result = await db.execute(
        select(EvidenceWindowAssessmentVersion).where(
            and_(
                EvidenceWindowAssessmentVersion.id == ewa.current_version_id,
                EvidenceWindowAssessmentVersion.organization_id == org_id,
            )
        )
    )
    version = version_result.scalar_one_or_none()
    if version is None:
        raise HTTPException(status_code=409, detail="This window assessment's current version could not be loaded.")

    if version.review_decision is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"This verdict was already {version.review_decision} and a version carries "
                "one decision. Re-assess the window to produce a new verdict to review."
            ),
        )

    reviewer_id = UUID(membership.user.db_id)

    # Segregation of duties, opt-in per org. Same helper as the acceptance
    # review so the two cannot drift on what independence means.
    policy = await get_assurance_policy(db, org_id)
    if policy.require_reviewer_independence:
        file_ids = [str(f) for f in (ewa.file_ids or [])]
        if file_ids:
            uploader_rows = await db.execute(
                select(EvidenceFile.uploaded_by_user_id).where(
                    and_(
                        EvidenceFile.organization_id == org_id,
                        EvidenceFile.id.in_(file_ids),
                    )
                )
            )
            if reviewer_is_sole_uploader(uploader_rows.scalars().all(), reviewer_id):
                raise HTTPException(status_code=403, detail=SOD_REFUSAL_DETAIL)

    old_values = {f: getattr(ewa, f) for f in WINDOW_VERDICT_REVIEW_TRACKED_FIELDS}
    decided_at = datetime.utcnow()

    ao_overrides_payload = None
    if body.decision == "overridden":
        known_ao_ids = {entry.get("ao_id") for entry in (version.ao_findings or [])}
        if not known_ao_ids:
            raise HTTPException(
                status_code=422,
                detail=(
                    "This verdict has no per-objective answers to override (it predates "
                    "objective-grounded window assessment, or the window held no files). "
                    "Re-assess the window to produce them, or confirm the verdict as it stands."
                ),
            )
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
                raise HTTPException(status_code=422, detail=f"Objective '{item.ao_id}' is listed more than once.")
            overrides_by_id[item.ao_id] = item

        ai_by_id = {
            entry.get("ao_id"): entry.get("suggested_designation")
            for entry in (version.ao_findings or [])
        }
        # ai_designation comes off the frozen row, never off the request.
        ao_overrides_payload = [
            {
                "ao_id": item.ao_id,
                "ai_designation": ai_by_id.get(item.ao_id),
                "human_designation": item.human_designation,
                "note": item.note or "",
            }
            for item in body.ao_overrides
        ]

        effective = _effective_window_ao_findings(version.ao_findings, overrides_by_id)
        designations = [entry.get("suggested_designation") for entry in effective]
        derived_status, unassessable_reason = derive_assessment_status(designations)

        ewa.ao_findings = effective
        ewa.gap_count = designations.count("gap_identified")
        ewa.cannot_assess_count = designations.count("cannot_assess")
        # The sample-size verdict is not a designation and is not the
        # reviewer's to move here: too few files is still too few files.
        if derived_status is not None and ewa.status != "insufficient_sample":
            ewa.status = derived_status
            ewa.unassessable_reason = unassessable_reason

    version.review_decision = body.decision
    version.review_reason = body.reason
    version.reviewed_by_user_id = reviewer_id
    version.reviewed_at = decided_at
    version.ao_overrides = ao_overrides_payload

    ewa.review_decision = body.decision
    ewa.review_reason = body.reason
    ewa.verdict_reviewed_by_user_id = reviewer_id
    ewa.verdict_reviewed_at = decided_at

    new_values = {f: getattr(ewa, f) for f in WINDOW_VERDICT_REVIEW_TRACKED_FIELDS}

    await log_entity_changes(
        db=db,
        organization_id=org_id,
        entity_type="evidence_window_assessment",
        entity_id=ewa.id,
        action="update",
        changed_by_user_id=reviewer_id,
        old_values=old_values,
        new_values=new_values,
        tracked_fields=WINDOW_VERDICT_REVIEW_TRACKED_FIELDS,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )

    await db.commit()
    await db.refresh(ewa)
    return ewa


# ---------------------------------------------------------------------------
# Version history
# ---------------------------------------------------------------------------

@router.get(
    "/organizations/{org_id}/evidence/window-assessments/{assessment_id}/versions",
    response_model=List[EvidenceWindowAssessmentVersionResponse],
    summary="Every AI verdict this window has received",
    description="""
    The window's assessment history, newest first. Each entry is frozen as it
    was when the verdict was reached, including the model and prompt version
    that produced it and any human decision recorded against it.
    """,
)
async def list_window_assessment_versions(
    org_id: UUID,
    assessment_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """List the frozen versions of a window assessment. Requires: viewer."""
    result = await db.execute(
        select(EvidenceWindowAssessmentVersion)
        .where(
            and_(
                EvidenceWindowAssessmentVersion.organization_id == org_id,
                EvidenceWindowAssessmentVersion.window_assessment_id == assessment_id,
            )
        )
        .order_by(desc(EvidenceWindowAssessmentVersion.version_number))
    )
    versions = result.scalars().all()
    return [EvidenceWindowAssessmentVersionResponse.model_validate(v) for v in versions]
