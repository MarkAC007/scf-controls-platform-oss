"""
Risk Assessments API endpoints.
Handles CRUD operations for organisation-scoped risk assessments.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from sqlalchemy.orm import selectinload
from typing import List, Optional
from uuid import UUID

from database import get_db
from models import RiskAssessment, Organization, User, ScopedControl, OrganizationRiskProfile, CustomRiskDefinition, CustomRiskControlMapping
from catalog_models import SCFCatalogControl
from schemas import (
    RiskAssessmentResponse,
    RiskAssessmentCreate,
    RiskAssessmentUpdate,
    RiskMatrixResponse,
    RiskMatrixCell,
    RiskSummaryResponse,
    ControlRiskMapping,
    SuccessResponse
)
from auth import require_org_role, OrgMembership
from services.audit_service import log_entity_changes, detect_action_source, get_request_id, RISK_ASSESSMENT_TRACKED_FIELDS

router = APIRouter(tags=["risk-assessments"])


def get_risk_level(score: int, low_max: int = 4, medium_max: int = 9, high_max: int = 16) -> str:
    """Calculate risk level from score (1-25) using configurable thresholds."""
    if score <= low_max:
        return "low"
    if score <= medium_max:
        return "medium"
    if score <= high_max:
        return "high"
    return "critical"


def enrich_risk_response(assessment: RiskAssessment, low_max: int = 4, medium_max: int = 9, high_max: int = 16) -> dict:
    """Add computed fields to risk assessment response using configurable thresholds."""
    data = {
        "id": assessment.id,
        "organization_id": assessment.organization_id,
        "risk_code": assessment.risk_code,
        "likelihood": assessment.likelihood,
        "impact": assessment.impact,
        "residual_likelihood": assessment.residual_likelihood,
        "residual_impact": assessment.residual_impact,
        "treatment_status": assessment.treatment_status,
        "treatment_plan": assessment.treatment_plan,
        "treatment_due_date": assessment.treatment_due_date,
        "owner_user_id": assessment.owner_user_id,
        "next_review_date": assessment.next_review_date,
        "notes": assessment.notes,
        "created_at": assessment.created_at,
        "updated_at": assessment.updated_at,
        "created_by_user_id": assessment.created_by_user_id,
        "updated_by_user_id": assessment.updated_by_user_id,
        "inherent_risk_score": assessment.inherent_risk_score,
        "residual_risk_score": assessment.residual_risk_score,
        "inherent_risk_level": assessment.get_inherent_risk_level(low_max, medium_max, high_max),
        "residual_risk_level": assessment.get_residual_risk_level(low_max, medium_max, high_max),
        "owner": assessment.owner if assessment.owner else None,
    }
    return data


async def _load_thresholds(org_id: UUID, db: AsyncSession) -> tuple:
    """Load risk thresholds for an organisation, falling back to defaults."""
    result = await db.execute(
        select(OrganizationRiskProfile).where(
            OrganizationRiskProfile.organization_id == org_id
        )
    )
    profile = result.scalar_one_or_none()
    if profile:
        return (profile.low_max, profile.medium_max, profile.high_max)
    return (4, 9, 16)


@router.get(
    "/organizations/{org_id}/risk-assessments",
    response_model=List[RiskAssessmentResponse]
)
async def list_risk_assessments(
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    treatment_status: Optional[str] = Query(None, description="Filter by treatment status"),
    risk_level: Optional[str] = Query(None, description="Filter by inherent risk level (low, medium, high, critical)"),
    category: Optional[str] = Query(None, description="Filter by risk category (e.g., AC, AM, BC)"),
    db: AsyncSession = Depends(get_db)
):
    """
    Get all risk assessments for an organisation.
    Requires: viewer role or higher.
    Optionally filter by treatment_status, risk_level, or category.
    """
    # Build query
    query = select(RiskAssessment).where(
        RiskAssessment.organization_id == org_id
    ).options(
        selectinload(RiskAssessment.owner)
    )

    if treatment_status:
        query = query.where(RiskAssessment.treatment_status == treatment_status)

    if category:
        # Filter by risk code category (e.g., "AC" matches "R-AC-1", "R-AC-2", etc.)
        query = query.where(RiskAssessment.risk_code.like(f"R-{category}-%"))

    # Order by risk code
    query = query.order_by(RiskAssessment.risk_code)

    result = await db.execute(query)
    assessments = result.scalars().all()

    # Load org risk profile thresholds
    low_max, medium_max, high_max = await _load_thresholds(org_id, db)

    # If risk_level filter is specified, filter in Python (requires computed score)
    if risk_level:
        assessments = [
            a for a in assessments
            if a.get_inherent_risk_level(low_max, medium_max, high_max) == risk_level
        ]

    return [enrich_risk_response(a, low_max, medium_max, high_max) for a in assessments]


@router.get(
    "/organizations/{org_id}/risk-assessments/{risk_code}",
    response_model=RiskAssessmentResponse
)
async def get_risk_assessment(
    org_id: UUID,
    risk_code: str,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db)
):
    """
    Get a single risk assessment by risk code.
    Requires: viewer role or higher.
    """
    query = select(RiskAssessment).where(
        and_(
            RiskAssessment.organization_id == org_id,
            RiskAssessment.risk_code == risk_code
        )
    ).options(
        selectinload(RiskAssessment.owner)
    )

    result = await db.execute(query)
    assessment = result.scalar_one_or_none()

    if not assessment:
        raise HTTPException(status_code=404, detail=f"Risk assessment for '{risk_code}' not found")

    # Load org risk profile thresholds
    low_max, medium_max, high_max = await _load_thresholds(org_id, db)

    return enrich_risk_response(assessment, low_max, medium_max, high_max)


@router.post(
    "/organizations/{org_id}/risk-assessments",
    response_model=RiskAssessmentResponse,
    status_code=201
)
async def create_or_update_risk_assessment(
    org_id: UUID,
    assessment_data: RiskAssessmentCreate,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db)
):
    """
    Create or update a risk assessment (upsert).
    Requires: editor role or higher.
    If assessment for risk_code exists, it will be updated.
    """
    current_user = membership.user

    # Check if assessment already exists
    existing = await db.execute(
        select(RiskAssessment).where(
            and_(
                RiskAssessment.organization_id == org_id,
                RiskAssessment.risk_code == assessment_data.risk_code
            )
        )
    )
    assessment = existing.scalar_one_or_none()

    user_id = UUID(current_user.db_id) if current_user and current_user.db_id else None

    if assessment:
        # Capture old values before updating
        old_values = {f: getattr(assessment, f) for f in RISK_ASSESSMENT_TRACKED_FIELDS if hasattr(assessment, f)}

        # Update existing assessment
        update_data = assessment_data.model_dump(exclude_unset=True, exclude={'risk_code'})
        for key, value in update_data.items():
            setattr(assessment, key, value)
        if current_user and current_user.db_id:
            assessment.updated_by_user_id = UUID(current_user.db_id)

        # Capture new values and audit log
        new_values = {f: getattr(assessment, f) for f in RISK_ASSESSMENT_TRACKED_FIELDS if hasattr(assessment, f)}
        await log_entity_changes(
            db=db, organization_id=org_id, entity_type='risk_assessment',
            entity_id=assessment.id, action='update', changed_by_user_id=user_id,
            old_values=old_values, new_values=new_values,
            tracked_fields=RISK_ASSESSMENT_TRACKED_FIELDS,
            action_source=detect_action_source(request),
            request_id=get_request_id(request),
        )
    else:
        # Create new assessment
        assessment = RiskAssessment(
            organization_id=org_id,
            created_by_user_id=UUID(current_user.db_id) if current_user and current_user.db_id else None,
            **assessment_data.model_dump()
        )
        db.add(assessment)
        await db.flush()

        # Audit log: risk assessment created
        new_values = {f: getattr(assessment, f) for f in RISK_ASSESSMENT_TRACKED_FIELDS if hasattr(assessment, f)}
        await log_entity_changes(
            db=db, organization_id=org_id, entity_type='risk_assessment',
            entity_id=assessment.id, action='create', changed_by_user_id=user_id,
            old_values={}, new_values=new_values,
            tracked_fields=RISK_ASSESSMENT_TRACKED_FIELDS,
            action_source=detect_action_source(request),
            request_id=get_request_id(request),
        )

    await db.commit()
    await db.refresh(assessment)

    # Load relationships
    query = select(RiskAssessment).where(
        RiskAssessment.id == assessment.id
    ).options(
        selectinload(RiskAssessment.owner)
    )
    result = await db.execute(query)
    assessment = result.scalar_one()

    # Load org risk profile thresholds
    low_max, medium_max, high_max = await _load_thresholds(org_id, db)

    return enrich_risk_response(assessment, low_max, medium_max, high_max)


@router.patch(
    "/organizations/{org_id}/risk-assessments/{risk_code}",
    response_model=RiskAssessmentResponse
)
async def update_risk_assessment(
    org_id: UUID,
    risk_code: str,
    assessment_update: RiskAssessmentUpdate,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db)
):
    """
    Partially update a risk assessment.
    Requires: editor role or higher.
    Only provided fields will be updated.
    """
    current_user = membership.user

    result = await db.execute(
        select(RiskAssessment).where(
            and_(
                RiskAssessment.organization_id == org_id,
                RiskAssessment.risk_code == risk_code
            )
        )
    )
    assessment = result.scalar_one_or_none()

    if not assessment:
        raise HTTPException(status_code=404, detail=f"Risk assessment for '{risk_code}' not found")

    # Capture old values before any updates
    old_values = {f: getattr(assessment, f) for f in RISK_ASSESSMENT_TRACKED_FIELDS if hasattr(assessment, f)}

    # Update fields
    update_data = assessment_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(assessment, key, value)

    # Track who updated
    if current_user and current_user.db_id:
        assessment.updated_by_user_id = UUID(current_user.db_id)

    # Capture new values and audit log
    new_values = {f: getattr(assessment, f) for f in RISK_ASSESSMENT_TRACKED_FIELDS if hasattr(assessment, f)}
    user_id = UUID(current_user.db_id) if current_user and current_user.db_id else None
    await log_entity_changes(
        db=db, organization_id=org_id, entity_type='risk_assessment',
        entity_id=assessment.id, action='update', changed_by_user_id=user_id,
        old_values=old_values, new_values=new_values,
        tracked_fields=RISK_ASSESSMENT_TRACKED_FIELDS,
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )

    await db.commit()
    await db.refresh(assessment)

    # Load relationships
    query = select(RiskAssessment).where(
        RiskAssessment.id == assessment.id
    ).options(
        selectinload(RiskAssessment.owner)
    )
    result = await db.execute(query)
    assessment = result.scalar_one()

    # Load org risk profile thresholds
    low_max, medium_max, high_max = await _load_thresholds(org_id, db)

    return enrich_risk_response(assessment, low_max, medium_max, high_max)


@router.delete(
    "/organizations/{org_id}/risk-assessments/{risk_code}",
    response_model=SuccessResponse
)
async def delete_risk_assessment(
    org_id: UUID,
    risk_code: str,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db)
):
    """
    Delete a risk assessment.
    Requires: editor role or higher.
    """
    result = await db.execute(
        select(RiskAssessment).where(
            and_(
                RiskAssessment.organization_id == org_id,
                RiskAssessment.risk_code == risk_code
            )
        )
    )
    assessment = result.scalar_one_or_none()

    if not assessment:
        raise HTTPException(status_code=404, detail=f"Risk assessment for '{risk_code}' not found")

    # Audit log: risk assessment deleted
    old_values = {f: getattr(assessment, f) for f in RISK_ASSESSMENT_TRACKED_FIELDS if hasattr(assessment, f)}
    user_id = UUID(membership.user.db_id) if membership.user and membership.user.db_id else None
    await log_entity_changes(
        db=db, organization_id=org_id, entity_type='risk_assessment',
        entity_id=assessment.id, action='delete', changed_by_user_id=user_id,
        old_values=old_values, new_values={},
        tracked_fields=RISK_ASSESSMENT_TRACKED_FIELDS,
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )

    await db.delete(assessment)
    await db.commit()

    return SuccessResponse(message=f"Risk assessment for '{risk_code}' deleted successfully")


@router.get(
    "/organizations/{org_id}/risk-matrix",
    response_model=RiskMatrixResponse
)
async def get_risk_matrix(
    org_id: UUID,
    matrix_type: str = Query("inherent", regex="^(inherent|residual)$"),
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db)
):
    """
    Get the 5x5 risk matrix data for visualisation.
    Requires: viewer role or higher.

    Returns 25 cells representing the matrix, each with:
    - likelihood (1-5)
    - impact (1-5)
    - score (1-25)
    - level (low, medium, high, critical)
    - risk_codes in that cell
    - count of risks

    Use matrix_type='inherent' for pre-control risk or 'residual' for post-control risk.
    """
    # Fetch all assessments
    result = await db.execute(
        select(RiskAssessment).where(RiskAssessment.organization_id == org_id)
    )
    assessments = result.scalars().all()

    # Load org risk profile thresholds
    low_max, medium_max, high_max = await _load_thresholds(org_id, db)

    # Build the matrix (25 cells)
    cells = []
    by_level = {"low": 0, "medium": 0, "high": 0, "critical": 0}
    total_assessed = 0
    total_unassessed = 0

    for likelihood in range(1, 6):
        for impact in range(1, 6):
            score = likelihood * impact
            level = get_risk_level(score, low_max, medium_max, high_max)

            # Find risks in this cell
            risk_codes = []
            for a in assessments:
                if matrix_type == "inherent":
                    if a.likelihood == likelihood and a.impact == impact:
                        risk_codes.append(a.risk_code)
                else:  # residual
                    if a.residual_likelihood == likelihood and a.residual_impact == impact:
                        risk_codes.append(a.risk_code)

            cells.append(RiskMatrixCell(
                likelihood=likelihood,
                impact=impact,
                score=score,
                level=level,
                risk_codes=risk_codes,
                count=len(risk_codes)
            ))

    # Calculate summary statistics
    for a in assessments:
        if matrix_type == "inherent":
            if a.likelihood is not None and a.impact is not None:
                total_assessed += 1
                level = a.get_inherent_risk_level(low_max, medium_max, high_max)
                if level:
                    by_level[level] += 1
            else:
                total_unassessed += 1
        else:  # residual
            if a.residual_likelihood is not None and a.residual_impact is not None:
                total_assessed += 1
                level = a.get_residual_risk_level(low_max, medium_max, high_max)
                if level:
                    by_level[level] += 1
            else:
                total_unassessed += 1

    return RiskMatrixResponse(
        organization_id=org_id,
        matrix_type=matrix_type,
        cells=cells,
        total_assessed=total_assessed,
        total_unassessed=total_unassessed,
        by_level=by_level
    )


@router.get(
    "/organizations/{org_id}/risk-summary",
    response_model=RiskSummaryResponse
)
async def get_risk_summary(
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db)
):
    """
    Get summary statistics for risk assessments.
    Requires: viewer role or higher.

    Returns counts by risk level (inherent and residual) and treatment status.
    """
    # Fetch all assessments
    result = await db.execute(
        select(RiskAssessment).where(RiskAssessment.organization_id == org_id)
    )
    assessments = result.scalars().all()

    # Load org risk profile thresholds
    low_max, medium_max, high_max = await _load_thresholds(org_id, db)

    # SCF catalog risks (static) + org custom risks (dynamic)
    SCF_RISK_COUNT = 39  # From risk_codes.json
    custom_count_result = await db.execute(
        select(func.count()).select_from(CustomRiskDefinition).where(
            CustomRiskDefinition.organization_id == org_id
        )
    )
    custom_risk_count = custom_count_result.scalar() or 0
    total_risks = SCF_RISK_COUNT + custom_risk_count

    # Calculate statistics
    assessed_risks = 0
    inherent_by_level = {"low": 0, "medium": 0, "high": 0, "critical": 0}
    residual_by_level = {"low": 0, "medium": 0, "high": 0, "critical": 0}
    by_status = {}

    for a in assessments:
        # Count as assessed if inherent scores are set
        if a.likelihood is not None and a.impact is not None:
            assessed_risks += 1

        # Inherent risk level
        inherent_level = a.get_inherent_risk_level(low_max, medium_max, high_max)
        if inherent_level:
            inherent_by_level[inherent_level] += 1

        # Residual risk level
        residual_level = a.get_residual_risk_level(low_max, medium_max, high_max)
        if residual_level:
            residual_by_level[residual_level] += 1

        # Treatment status
        status = a.treatment_status or "identified"
        by_status[status] = by_status.get(status, 0) + 1

    return RiskSummaryResponse(
        organization_id=org_id,
        total_risks=total_risks,
        assessed_risks=assessed_risks,
        unassessed_risks=total_risks - len(assessments),
        inherent_low=inherent_by_level["low"],
        inherent_medium=inherent_by_level["medium"],
        inherent_high=inherent_by_level["high"],
        inherent_critical=inherent_by_level["critical"],
        residual_low=residual_by_level["low"],
        residual_medium=residual_by_level["medium"],
        residual_high=residual_by_level["high"],
        residual_critical=residual_by_level["critical"],
        by_treatment_status=by_status
    )


@router.get(
    "/organizations/{org_id}/risks-for-control/{scf_id}",
    response_model=dict
)
async def get_risks_for_control(
    org_id: UUID,
    scf_id: str,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db)
):
    """
    Get risk codes and assessments linked to a specific control.
    Requires: viewer role or higher.

    Returns:
    - catalog_risk_codes: Risk codes from the SCF catalog for this control
    - assessments: Org-specific risk assessments for those risk codes

    The control-to-risk mapping comes from SCFCatalogControl.risk_codes.
    """
    # Look up the SCF catalog control to get its risk_codes
    catalog_result = await db.execute(
        select(SCFCatalogControl.risk_codes).where(
            SCFCatalogControl.scf_id == scf_id.upper()
        )
    )
    catalog_row = catalog_result.first()

    if not catalog_row:
        raise HTTPException(
            status_code=404,
            detail=f"Control {scf_id} not found in SCF catalog"
        )

    risk_codes = catalog_row[0] or []

    # If no risk codes mapped, return early
    if not risk_codes:
        return {
            "scf_id": scf_id.upper(),
            "catalog_risk_codes": [],
            "assessments": []
        }

    # Get org-specific risk assessments for these risk codes
    query = select(RiskAssessment).where(
        and_(
            RiskAssessment.organization_id == org_id,
            RiskAssessment.risk_code.in_(risk_codes)
        )
    ).options(
        selectinload(RiskAssessment.owner)
    ).order_by(RiskAssessment.risk_code)

    result = await db.execute(query)
    assessments = result.scalars().all()

    # Load org risk profile thresholds
    low_max, medium_max, high_max = await _load_thresholds(org_id, db)

    return {
        "scf_id": scf_id.upper(),
        "catalog_risk_codes": risk_codes,
        "assessments": [enrich_risk_response(a, low_max, medium_max, high_max) for a in assessments]
    }


@router.get(
    "/organizations/{org_id}/controls-for-risk/{risk_code}",
    response_model=dict
)
async def get_controls_for_risk(
    org_id: UUID,
    risk_code: str,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db)
):
    """
    Get controls that address a specific risk code.
    Requires: viewer role or higher.

    For SCF risks: uses the risk_codes JSONB array in SCFCatalogControl.
    For custom risks (R-ORG-*): uses the custom_risk_control_mappings table.

    Returns the same response shape in both cases.
    """
    normalised_risk_code = risk_code.upper()

    # Determine the source of control IDs
    if normalised_risk_code.startswith('R-ORG-'):
        # Custom risk: get linked controls from mappings table
        mapping_result = await db.execute(
            select(CustomRiskControlMapping.scf_id).where(
                and_(
                    CustomRiskControlMapping.organization_id == org_id,
                    CustomRiskControlMapping.risk_code == normalised_risk_code
                )
            )
        )
        mapped_scf_ids = [row[0] for row in mapping_result.all()]

        if not mapped_scf_ids:
            return {
                "risk_code": normalised_risk_code,
                "total_catalog_controls": 0,
                "catalog_control_ids": [],
                "scoped_controls": []
            }

        # Look up control names from catalog
        catalog_result = await db.execute(
            select(SCFCatalogControl.scf_id, SCFCatalogControl.control_name).where(
                SCFCatalogControl.scf_id.in_(mapped_scf_ids)
            )
        )
        catalog_lookup = {row.scf_id: row.control_name for row in catalog_result.all()}
        catalog_control_ids = mapped_scf_ids
    else:
        # SCF risk: get controls from catalog JSONB containment
        catalog_query = select(SCFCatalogControl.scf_id, SCFCatalogControl.control_name).where(
            SCFCatalogControl.risk_codes.contains([normalised_risk_code])
        ).order_by(SCFCatalogControl.scf_id)

        catalog_result = await db.execute(catalog_query)
        catalog_controls = catalog_result.all()

        if not catalog_controls:
            return {
                "risk_code": normalised_risk_code,
                "total_catalog_controls": 0,
                "catalog_control_ids": [],
                "scoped_controls": []
            }

        catalog_control_ids = [c.scf_id for c in catalog_controls]
        catalog_lookup = {c.scf_id: c.control_name for c in catalog_controls}

    # Get scoped controls for this org
    scoped_query = select(ScopedControl).where(
        and_(
            ScopedControl.organization_id == org_id,
            ScopedControl.scf_id.in_(catalog_control_ids),
            ScopedControl.selected == True
        )
    ).order_by(ScopedControl.scf_id)

    scoped_result = await db.execute(scoped_query)
    scoped_controls = scoped_result.scalars().all()

    scoped_response = []
    for sc in scoped_controls:
        scoped_response.append({
            "scf_id": sc.scf_id,
            "control_name": catalog_lookup.get(sc.scf_id, "Unknown"),
            "implementation_status": sc.implementation_status,
            "priority": sc.priority,
            "target_date": sc.target_date.isoformat() if sc.target_date else None,
        })

    return {
        "risk_code": normalised_risk_code,
        "total_catalog_controls": len(catalog_control_ids),
        "catalog_control_ids": catalog_control_ids,
        "scoped_controls": scoped_response
    }
