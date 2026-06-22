"""
Vendor Reports API endpoints (Issue #61).
Handles report generation, export, email delivery, and vendor risk scoring.
"""
import json
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc
from sqlalchemy.orm import selectinload
from typing import List, Optional
from uuid import UUID

from database import get_db
from models import Vendor, VendorAssessment, VendorReport
from schemas import (
    VendorReportResponse,
    VendorReportGenerateRequest,
    VendorReportEmailRequest,
    SuccessResponse,
)
from auth import require_org_role, OrgMembership
from services.vendor_reports import (
    generate_report,
    export_as_pdf,
    export_as_docx,
    send_report_email,
)

router = APIRouter(tags=["vendor-reports"])


# =============================================================================
# Report Generation and CRUD
# =============================================================================

@router.post(
    "/organizations/{org_id}/vendors/{vendor_id}/reports",
    response_model=VendorReportResponse,
    status_code=201,
)
async def generate_vendor_report(
    org_id: UUID,
    vendor_id: UUID,
    body: VendorReportGenerateRequest,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """Generate a new vendor assessment report. Requires editor role."""
    # Validate vendor belongs to org
    vendor = await _get_vendor_or_404(org_id, vendor_id, db)

    try:
        report = await generate_report(
            db=db,
            vendor_id=str(vendor_id),
            organization_id=str(org_id),
            user_id=membership.user.db_id,
            assessment_id=str(body.assessment_id) if body.assessment_id else None,
            report_type=body.report_type,
        )

        # Reload with relationships
        result = await db.execute(
            select(VendorReport).where(VendorReport.id == report.id).options(
                selectinload(VendorReport.generated_by)
            )
        )
        return result.scalar_one()

    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get(
    "/organizations/{org_id}/vendors/{vendor_id}/reports",
    response_model=List[VendorReportResponse],
)
async def list_vendor_reports(
    org_id: UUID,
    vendor_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """List all reports for a vendor. Requires viewer role."""
    await _get_vendor_or_404(org_id, vendor_id, db)

    result = await db.execute(
        select(VendorReport)
        .where(VendorReport.vendor_id == vendor_id)
        .options(selectinload(VendorReport.generated_by))
        .order_by(desc(VendorReport.created_at))
    )
    return result.scalars().all()


@router.get(
    "/organizations/{org_id}/vendors/{vendor_id}/reports/{report_id}",
    response_model=VendorReportResponse,
)
async def get_vendor_report(
    org_id: UUID,
    vendor_id: UUID,
    report_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """Get a single vendor report. Requires viewer role."""
    await _get_vendor_or_404(org_id, vendor_id, db)

    result = await db.execute(
        select(VendorReport)
        .where(VendorReport.id == report_id, VendorReport.vendor_id == vendor_id)
        .options(selectinload(VendorReport.generated_by))
    )
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


# =============================================================================
# Report Export
# =============================================================================

@router.get(
    "/organizations/{org_id}/vendors/{vendor_id}/reports/{report_id}/export",
)
async def export_vendor_report(
    org_id: UUID,
    vendor_id: UUID,
    report_id: UUID,
    format: str = Query("pdf", pattern="^(pdf|docx|json|markdown)$"),
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """Export a vendor report in the specified format. Requires viewer role."""
    await _get_vendor_or_404(org_id, vendor_id, db)

    result = await db.execute(
        select(VendorReport).where(
            VendorReport.id == report_id,
            VendorReport.vendor_id == vendor_id,
        )
    )
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    safe_title = report.title.replace(" ", "_")[:100]

    if format == "pdf":
        try:
            pdf_bytes = export_as_pdf(report.content_markdown, report.title)
            return Response(
                content=pdf_bytes,
                media_type="application/pdf",
                headers={"Content-Disposition": f'attachment; filename="{safe_title}.pdf"'}
            )
        except ImportError as exc:
            raise HTTPException(status_code=501, detail=str(exc))

    elif format == "docx":
        try:
            docx_bytes = export_as_docx(report.content_markdown, report.title)
            return Response(
                content=docx_bytes,
                media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                headers={"Content-Disposition": f'attachment; filename="{safe_title}.docx"'}
            )
        except ImportError as exc:
            raise HTTPException(status_code=501, detail=str(exc))

    elif format == "json":
        json_bytes = json.dumps(report.content_json or {}, indent=2, default=str).encode()
        return Response(
            content=json_bytes,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{safe_title}.json"'}
        )

    else:  # markdown
        return Response(
            content=report.content_markdown.encode(),
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="{safe_title}.md"'}
        )


# =============================================================================
# Report Email
# =============================================================================

@router.post(
    "/organizations/{org_id}/vendors/{vendor_id}/reports/{report_id}/email",
    response_model=SuccessResponse,
)
async def email_vendor_report(
    org_id: UUID,
    vendor_id: UUID,
    report_id: UUID,
    body: VendorReportEmailRequest,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """Email a vendor report. Requires editor role."""
    vendor = await _get_vendor_or_404(org_id, vendor_id, db)

    result = await db.execute(
        select(VendorReport).where(
            VendorReport.id == report_id,
            VendorReport.vendor_id == vendor_id,
        )
    )
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    email_id = await send_report_email(
        to_email=body.to_email,
        to_name=body.to_name,
        report=report,
        vendor_name=vendor.name,
    )

    if email_id:
        return SuccessResponse(message=f"Report emailed successfully (ID: {email_id})")
    else:
        raise HTTPException(status_code=503, detail="Email service unavailable or failed to send")


# =============================================================================
# Report Delete
# =============================================================================

@router.delete(
    "/organizations/{org_id}/vendors/{vendor_id}/reports/{report_id}",
    response_model=SuccessResponse,
)
async def delete_vendor_report(
    org_id: UUID,
    vendor_id: UUID,
    report_id: UUID,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """Delete a vendor report. Requires admin role."""
    await _get_vendor_or_404(org_id, vendor_id, db)

    result = await db.execute(
        select(VendorReport).where(
            VendorReport.id == report_id,
            VendorReport.vendor_id == vendor_id,
        )
    )
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    await db.delete(report)
    await db.commit()
    return SuccessResponse(message="Report deleted successfully")


# =============================================================================
# Consultant Access
# =============================================================================

@router.get(
    "/consultant/clients/{org_id}/vendor-reports",
    response_model=List[VendorReportResponse],
)
async def list_client_vendor_reports(
    org_id: UUID,
    vendor_id: Optional[UUID] = Query(None, description="Filter by vendor"),
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """List vendor reports for a consultant's client. Requires viewer role."""
    query = select(VendorReport).where(
        VendorReport.organization_id == org_id
    ).options(selectinload(VendorReport.generated_by))

    if vendor_id:
        query = query.where(VendorReport.vendor_id == vendor_id)

    query = query.order_by(desc(VendorReport.created_at))

    result = await db.execute(query)
    return result.scalars().all()


# =============================================================================
# Vendor Risk Scoring (Issue #60)
# =============================================================================

@router.post(
    "/organizations/{org_id}/vendors/{vendor_id}/calculate-risk",
    response_model=dict,
)
async def calculate_vendor_risk_endpoint(
    org_id: UUID,
    vendor_id: UUID,
    assessment_id: Optional[UUID] = Query(None, description="Specific assessment to score"),
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """
    Calculate risk scores for a vendor assessment using research data.
    Requires editor role.

    If assessment_id is not provided, uses the most recent assessment.
    Optionally generates AI analysis if ANTHROPIC_API_KEY is configured.
    """
    from services.vendor_risk_scoring import calculate_vendor_risk, generate_ai_analysis
    from services.vendor_research import get_latest as get_latest_research

    vendor = await _get_vendor_or_404(org_id, vendor_id, db)

    # Get assessment
    if assessment_id:
        assess_result = await db.execute(
            select(VendorAssessment).where(
                VendorAssessment.id == assessment_id,
                VendorAssessment.vendor_id == vendor_id,
            )
        )
        assessment = assess_result.scalar_one_or_none()
    else:
        assess_result = await db.execute(
            select(VendorAssessment)
            .where(VendorAssessment.vendor_id == vendor_id)
            .order_by(desc(VendorAssessment.assessment_date))
            .limit(1)
        )
        assessment = assess_result.scalar_one_or_none()

    if not assessment:
        # Auto-create an assessment so risk scoring can proceed
        current_user = membership.user
        assessment = VendorAssessment(
            vendor_id=vendor_id,
            assessment_type="initial",
            assessment_date=date.today(),
            status="in_progress",
            created_by_user_id=UUID(current_user.db_id) if current_user and current_user.db_id else None,
        )
        db.add(assessment)

    # Get latest research data
    research_data = await get_latest_research(db=db, vendor_id=str(vendor_id))

    # Guard: if assessment was scored by DPSIA (has final_risk_score but no
    # factor breakdown) and no legacy research data exists, return the existing
    # DPSIA scores rather than overwriting them with zeros.
    has_dpsia_score = (
        assessment.final_risk_score is not None
        and assessment.status == "completed"
    )
    has_factor_scores = any([
        assessment.breach_score, assessment.certification_score,
        assessment.cve_score, assessment.regulatory_score,
        assessment.data_handling_score,
    ])
    if has_dpsia_score and not has_factor_scores and not research_data:
        return {
            "assessment_id": str(assessment.id),
            "vendor_id": str(vendor_id),
            "breach_score": assessment.breach_score,
            "certification_score": assessment.certification_score,
            "cve_score": assessment.cve_score,
            "regulatory_score": assessment.regulatory_score,
            "data_handling_score": assessment.data_handling_score,
            "likelihood": assessment.likelihood,
            "impact": assessment.impact,
            "final_risk_score": assessment.final_risk_score,
            "risk_level": assessment.risk_level,
            "ai_analysis": assessment.ai_analysis,
            "has_research_data": False,
            "dpsia_protected": True,
        }

    # Calculate risk scores
    assessment = await calculate_vendor_risk(
        db=db,
        assessment=assessment,
        research_data=research_data,
    )

    # Generate AI analysis if research data available
    if research_data:
        ai_text = await generate_ai_analysis(
            vendor_name=vendor.name,
            domain=vendor.website,
            research_data=research_data,
            factor_scores={
                "breach_score": assessment.breach_score or 0,
                "certification_score": assessment.certification_score or 0,
                "cve_score": assessment.cve_score or 0,
                "regulatory_score": assessment.regulatory_score or 0,
                "data_handling_score": assessment.data_handling_score or 0,
            },
            final_score=assessment.final_risk_score or 0,
            risk_level=assessment.risk_level or "medium",
        )
        if ai_text:
            assessment.ai_analysis = ai_text

    # Mark assessment as completed now that scoring is done
    assessment.status = "completed"

    # Update vendor-level risk too
    vendor.risk_score = assessment.final_risk_score
    vendor.risk_level = assessment.risk_level

    await db.commit()
    await db.refresh(assessment)

    return {
        "assessment_id": str(assessment.id),
        "vendor_id": str(vendor_id),
        "breach_score": assessment.breach_score,
        "certification_score": assessment.certification_score,
        "cve_score": assessment.cve_score,
        "regulatory_score": assessment.regulatory_score,
        "data_handling_score": assessment.data_handling_score,
        "likelihood": assessment.likelihood,
        "impact": assessment.impact,
        "final_risk_score": assessment.final_risk_score,
        "risk_level": assessment.risk_level,
        "ai_analysis": assessment.ai_analysis,
        "has_research_data": research_data is not None,
    }


# =============================================================================
# Vendor Risk Matrix (Issue #60)
# =============================================================================

@router.get(
    "/organizations/{org_id}/vendor-risk-matrix",
)
async def get_vendor_risk_matrix(
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """
    Get vendor risk data formatted for the 5x5 risk matrix.
    Returns cells with vendor names instead of risk codes.
    """
    from api.risk_assessments import get_risk_level as get_level, _load_thresholds

    # Get all vendors for this org
    vendor_result = await db.execute(
        select(Vendor).where(Vendor.organization_id == org_id)
    )
    vendors = vendor_result.scalars().all()

    # Get latest assessment for each vendor
    vendor_assessments = []
    for vendor in vendors:
        assess_result = await db.execute(
            select(VendorAssessment)
            .where(VendorAssessment.vendor_id == vendor.id)
            .order_by(desc(VendorAssessment.assessment_date))
            .limit(1)
        )
        assessment = assess_result.scalar_one_or_none()
        if assessment and assessment.likelihood and assessment.impact:
            vendor_assessments.append((vendor, assessment))

    # Load thresholds
    low_max, medium_max, high_max = await _load_thresholds(org_id, db)

    # Build matrix cells
    cells = []
    by_level = {"low": 0, "medium": 0, "high": 0, "critical": 0}

    for likelihood in range(1, 6):
        for impact in range(1, 6):
            score = likelihood * impact
            level = get_level(score, low_max, medium_max, high_max)

            vendor_names = []
            vendor_ids = []
            for vendor, assessment in vendor_assessments:
                if assessment.likelihood == likelihood and assessment.impact == impact:
                    vendor_names.append(vendor.name)
                    vendor_ids.append(str(vendor.id))

            cells.append({
                "likelihood": likelihood,
                "impact": impact,
                "score": score,
                "level": level,
                "vendor_names": vendor_names,
                "vendor_ids": vendor_ids,
                "count": len(vendor_names),
            })

    for vendor, assessment in vendor_assessments:
        level = get_level(
            assessment.likelihood * assessment.impact,
            low_max, medium_max, high_max
        )
        by_level[level] += 1

    return {
        "organization_id": str(org_id),
        "matrix_type": "vendor",
        "cells": cells,
        "total_vendors": len(vendors),
        "total_assessed": len(vendor_assessments),
        "total_unassessed": len(vendors) - len(vendor_assessments),
        "by_level": by_level,
    }


# =============================================================================
# Helper Functions
# =============================================================================

async def _get_vendor_or_404(org_id: UUID, vendor_id: UUID, db: AsyncSession) -> Vendor:
    """Fetch vendor or raise 404."""
    result = await db.execute(
        select(Vendor).where(
            and_(Vendor.organization_id == org_id, Vendor.id == vendor_id)
        )
    )
    vendor = result.scalar_one_or_none()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
    return vendor
