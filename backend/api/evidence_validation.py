"""
Evidence Validation API endpoints (Issue #218).

Provides read access to validation results, re-validation trigger,
and aggregate summary metrics for the dashboard.

Endpoints:
  GET  /organizations/{org_id}/evidence/{eid}/files/{fid}/validation  — Get result
  POST /organizations/{org_id}/evidence/{eid}/files/{fid}/validate    — Re-validate
  GET  /organizations/{org_id}/evidence/validation/summary            — Dashboard metrics
"""
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, and_, func, case
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_org_role, OrgMembership
from database import get_db
from models import EvidenceFile, EvidenceValidationResult
from schemas import (
    EvidenceValidationResultResponse,
    EvidenceValidationSummary,
)
from services.validation_service import run_validation

logger = logging.getLogger(__name__)

router = APIRouter(tags=["evidence-validation"])


# ---------------------------------------------------------------------------
# GET validation result for a specific file
# ---------------------------------------------------------------------------

@router.get(
    "/organizations/{org_id}/evidence/{evidence_id}/files/{file_id}/validation",
    response_model=EvidenceValidationResultResponse,
    summary="Get validation result for a file",
    description="Retrieve the validation result for a specific evidence file, including status, checks performed, and any issues found.",
)
async def get_validation_result(
    org_id: UUID,
    evidence_id: str,
    file_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """
    Get the validation result for a specific evidence file.
    Requires: viewer role or higher.
    """
    result = await db.execute(
        select(EvidenceValidationResult).where(
            and_(
                EvidenceValidationResult.evidence_file_id == file_id,
                EvidenceValidationResult.organization_id == org_id,
            )
        )
    )
    validation = result.scalar_one_or_none()

    if not validation:
        raise HTTPException(status_code=404, detail="No validation result found for this file")

    return validation


# ---------------------------------------------------------------------------
# POST re-validate a specific file
# ---------------------------------------------------------------------------

@router.post(
    "/organizations/{org_id}/evidence/{evidence_id}/files/{file_id}/validate",
    response_model=EvidenceValidationResultResponse,
    summary="Re-validate an evidence file",
    description="Re-run the validation engine against a specific evidence file. Upserts the validation result record.",
)
async def revalidate_file(
    org_id: UUID,
    evidence_id: str,
    file_id: UUID,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """
    Re-run validation for a specific evidence file (upsert).
    Requires: editor role or higher.
    """
    # Look up the evidence file
    file_result = await db.execute(
        select(EvidenceFile).where(
            and_(
                EvidenceFile.id == file_id,
                EvidenceFile.organization_id == org_id,
                EvidenceFile.evidence_id == evidence_id,
            )
        )
    )
    evidence_file = file_result.scalar_one_or_none()

    if not evidence_file:
        raise HTTPException(status_code=404, detail="Evidence file not found")

    if evidence_file.is_deleted:
        raise HTTPException(status_code=410, detail="Evidence file has been deleted")

    validation_result = await run_validation(
        db=db,
        evidence_file=evidence_file,
        validation_source="manual_upload",
    )

    await db.commit()
    await db.refresh(validation_result)

    return validation_result


# ---------------------------------------------------------------------------
# GET aggregate validation summary for the org
# ---------------------------------------------------------------------------

@router.get(
    "/organizations/{org_id}/evidence/validation/summary",
    response_model=EvidenceValidationSummary,
    summary="Get validation summary metrics",
    description="Aggregate validation metrics for the organisation dashboard. Returns counts by status (valid, warning, partial, invalid) and overall pass rate.",
)
async def get_validation_summary(
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """
    Aggregate validation metrics for the dashboard.
    Requires: viewer role or higher.

    Returns counts by status and overall pass rate.
    """
    result = await db.execute(
        select(
            func.count(EvidenceValidationResult.id).label("total"),
            func.count(
                case(
                    (EvidenceValidationResult.status == "valid", 1),
                )
            ).label("valid_count"),
            func.count(
                case(
                    (EvidenceValidationResult.status == "warning", 1),
                )
            ).label("warning_count"),
            func.count(
                case(
                    (EvidenceValidationResult.status == "partial", 1),
                )
            ).label("partial_count"),
            func.count(
                case(
                    (EvidenceValidationResult.status == "invalid", 1),
                )
            ).label("invalid_count"),
        ).where(
            EvidenceValidationResult.organization_id == org_id
        )
    )
    row = result.one()

    total = row.total or 0
    valid = row.valid_count or 0
    pass_rate = round(valid / total, 4) if total > 0 else 0.0

    return EvidenceValidationSummary(
        total_files=total,
        valid_count=valid,
        warning_count=row.warning_count or 0,
        partial_count=row.partial_count or 0,
        invalid_count=row.invalid_count or 0,
        pass_rate=pass_rate,
    )
