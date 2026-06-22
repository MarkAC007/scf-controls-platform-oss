"""
Vendor Management API endpoints.
Handles CRUD operations for vendors, assessments, and certifications (TPRM).
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from sqlalchemy.orm import selectinload
from typing import List, Optional
from uuid import UUID

from database import get_db
from models import (
    Vendor, VendorAssessment, VendorCertification,
    VendorClaimVerification, VendorCIAControl,
    VendorActionItem, VendorCompensatingControl,
)
from schemas import (
    VendorResponse,
    VendorCreate,
    VendorUpdate,
    VendorAssessmentResponse,
    VendorAssessmentCreate,
    VendorAssessmentUpdate,
    VendorCertificationResponse,
    VendorCertificationCreate,
    VendorCertificationUpdate,
    SuccessResponse,
    VendorResearchTriggerRequest,
    VendorResearchTriggerResponse,
    VendorResearchStatusResponse,
    VendorResearchResultResponse,
    VendorClaimVerificationResponse,
    VendorClaimVerificationCreate,
    VendorClaimVerificationUpdate,
    VendorCIAControlResponse,
    VendorCIAControlCreate,
    VendorCIAControlUpdate,
    VendorActionItemResponse,
    VendorActionItemCreate,
    VendorActionItemUpdate,
    VendorCompensatingControlResponse,
    VendorCompensatingControlCreate,
    VendorCompensatingControlUpdate,
    DPSIATriggerRequest,
    DPSIATriggerResponse,
    DPSIAStatusResponse,
    DPSIAResultResponse,
)
from auth import require_org_role, OrgMembership
from services.audit_service import log_entity_changes, detect_action_source, get_request_id, VENDOR_TRACKED_FIELDS
from services.vendor import check_vendor_limit
from services.vendor_research import (
    trigger_research,
    get_status as get_research_status,
    get_results as get_research_results,
    get_latest as get_research_latest,
)
from services.dpsia_assessment import (
    trigger_assessment as trigger_dpsia,
    get_status as get_dpsia_status,
    get_results as get_dpsia_results,
    get_latest as get_dpsia_latest,
    get_active as get_dpsia_active,
)

router = APIRouter(tags=["vendors"])


# =============================================================================
# Vendor CRUD
# =============================================================================

@router.get(
    "/organizations/{org_id}/vendors",
    response_model=List[VendorResponse]
)
async def list_vendors(
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    status: Optional[str] = Query(None, description="Filter by vendor status"),
    criticality: Optional[str] = Query(None, description="Filter by criticality level"),
    category: Optional[str] = Query(None, description="Filter by category"),
    search: Optional[str] = Query(None, description="Search by vendor name"),
    db: AsyncSession = Depends(get_db)
):
    """
    List all vendors for an organisation.
    Requires: viewer role or higher.
    Supports filtering by status, criticality, category, and search by name.
    """
    query = select(Vendor).where(Vendor.organization_id == org_id)

    if status:
        query = query.where(Vendor.status == status)
    if criticality:
        query = query.where(Vendor.criticality == criticality)
    if category:
        query = query.where(Vendor.category == category)
    if search:
        query = query.where(func.lower(Vendor.name).contains(search.lower()))

    query = query.options(
        selectinload(Vendor.created_by),
        selectinload(Vendor.updated_by)
    ).order_by(Vendor.name)

    result = await db.execute(query)
    return result.scalars().all()


@router.post(
    "/organizations/{org_id}/vendors",
    response_model=VendorResponse,
    status_code=201
)
async def create_vendor(
    org_id: UUID,
    vendor_data: VendorCreate,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db)
):
    """
    Create a new vendor.
    Requires: editor role or higher.
    Vendor names must be unique (case-insensitive) within an organisation.
    Enforces subscription tier vendor limits.
    """
    current_user = membership.user

    # Check subscription tier vendor limit
    can_create = await check_vendor_limit(org_id, db)
    if not can_create:
        raise HTTPException(
            status_code=403,
            detail="Vendor limit reached for your subscription tier. Please upgrade to add more vendors."
        )

    # Check for duplicate vendor name (case-insensitive)
    existing = await db.execute(
        select(Vendor).where(
            and_(
                Vendor.organization_id == org_id,
                func.lower(Vendor.name) == vendor_data.name.lower()
            )
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail=f"Vendor with name '{vendor_data.name}' already exists in this organisation"
        )

    new_vendor = Vendor(
        organization_id=org_id,
        created_by_user_id=UUID(current_user.db_id) if current_user and current_user.db_id else None,
        **vendor_data.model_dump()
    )
    db.add(new_vendor)
    await db.flush()

    # Audit log: vendor created
    new_values = {f: getattr(new_vendor, f) for f in VENDOR_TRACKED_FIELDS if hasattr(new_vendor, f)}
    user_id = UUID(current_user.db_id) if current_user and current_user.db_id else None
    await log_entity_changes(
        db=db, organization_id=org_id, entity_type='vendor',
        entity_id=new_vendor.id, action='create', changed_by_user_id=user_id,
        old_values={}, new_values=new_values,
        tracked_fields=VENDOR_TRACKED_FIELDS,
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )

    await db.commit()
    await db.refresh(new_vendor)

    # Load relationships for response
    query = select(Vendor).where(Vendor.id == new_vendor.id).options(
        selectinload(Vendor.created_by),
        selectinload(Vendor.updated_by)
    )
    result = await db.execute(query)
    return result.scalar_one()


@router.get(
    "/organizations/{org_id}/vendors/{vendor_id}",
    response_model=VendorResponse
)
async def get_vendor(
    org_id: UUID,
    vendor_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db)
):
    """
    Get a single vendor by ID.
    Requires: viewer role or higher.
    """
    query = select(Vendor).where(
        and_(
            Vendor.organization_id == org_id,
            Vendor.id == vendor_id
        )
    ).options(
        selectinload(Vendor.created_by),
        selectinload(Vendor.updated_by)
    )

    result = await db.execute(query)
    vendor = result.scalar_one_or_none()

    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")

    return vendor


@router.patch(
    "/organizations/{org_id}/vendors/{vendor_id}",
    response_model=VendorResponse
)
async def update_vendor(
    org_id: UUID,
    vendor_id: UUID,
    vendor_update: VendorUpdate,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db)
):
    """
    Partially update a vendor.
    Requires: editor role or higher.
    Only provided fields will be updated.
    """
    current_user = membership.user
    result = await db.execute(
        select(Vendor).where(
            and_(
                Vendor.organization_id == org_id,
                Vendor.id == vendor_id
            )
        )
    )
    vendor = result.scalar_one_or_none()

    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")

    # Capture old values before any updates
    old_values = {f: getattr(vendor, f) for f in VENDOR_TRACKED_FIELDS if hasattr(vendor, f)}

    # If name is being changed, check for conflicts
    update_data = vendor_update.model_dump(exclude_unset=True)
    if "name" in update_data and update_data["name"].lower() != vendor.name.lower():
        existing = await db.execute(
            select(Vendor).where(
                and_(
                    Vendor.organization_id == org_id,
                    func.lower(Vendor.name) == update_data["name"].lower(),
                    Vendor.id != vendor_id
                )
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=409,
                detail=f"Vendor with name '{update_data['name']}' already exists in this organisation"
            )

    # Update fields
    for key, value in update_data.items():
        setattr(vendor, key, value)

    # Track who updated
    if current_user:
        vendor.updated_by_user_id = UUID(current_user.db_id) if current_user.db_id else None

    # Capture new values and audit log
    new_values = {f: getattr(vendor, f) for f in VENDOR_TRACKED_FIELDS if hasattr(vendor, f)}
    user_id = UUID(current_user.db_id) if current_user and current_user.db_id else None
    await log_entity_changes(
        db=db, organization_id=org_id, entity_type='vendor',
        entity_id=vendor.id, action='update', changed_by_user_id=user_id,
        old_values=old_values, new_values=new_values,
        tracked_fields=VENDOR_TRACKED_FIELDS,
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )

    await db.commit()
    await db.refresh(vendor)

    # Load relationships for response
    query = select(Vendor).where(Vendor.id == vendor.id).options(
        selectinload(Vendor.created_by),
        selectinload(Vendor.updated_by)
    )
    result = await db.execute(query)
    return result.scalar_one()


@router.delete(
    "/organizations/{org_id}/vendors/{vendor_id}",
    response_model=SuccessResponse
)
async def delete_vendor(
    org_id: UUID,
    vendor_id: UUID,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db)
):
    """
    Delete a vendor and all associated assessments and certifications.
    Requires: admin role.
    """
    result = await db.execute(
        select(Vendor).where(
            and_(
                Vendor.organization_id == org_id,
                Vendor.id == vendor_id
            )
        )
    )
    vendor = result.scalar_one_or_none()

    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")

    vendor_name = vendor.name

    # Audit log: vendor deleted
    old_values = {f: getattr(vendor, f) for f in VENDOR_TRACKED_FIELDS if hasattr(vendor, f)}
    user_id = UUID(membership.user.db_id) if membership.user and membership.user.db_id else None
    await log_entity_changes(
        db=db, organization_id=org_id, entity_type='vendor',
        entity_id=vendor.id, action='delete', changed_by_user_id=user_id,
        old_values=old_values, new_values={},
        tracked_fields=VENDOR_TRACKED_FIELDS,
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )

    await db.delete(vendor)
    await db.commit()

    return SuccessResponse(message=f"Vendor '{vendor_name}' deleted successfully")


# =============================================================================
# Vendor Assessment CRUD
# =============================================================================

@router.get(
    "/organizations/{org_id}/vendors/{vendor_id}/assessments",
    response_model=List[VendorAssessmentResponse]
)
async def list_vendor_assessments(
    org_id: UUID,
    vendor_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db)
):
    """
    List all assessments for a vendor.
    Requires: viewer role or higher.
    """
    # Verify vendor belongs to org
    vendor = await _get_vendor_or_404(org_id, vendor_id, db)

    query = select(VendorAssessment).where(
        VendorAssessment.vendor_id == vendor_id
    ).options(
        selectinload(VendorAssessment.created_by),
        selectinload(VendorAssessment.updated_by),
        selectinload(VendorAssessment.assessor)
    ).order_by(VendorAssessment.assessment_date.desc())

    result = await db.execute(query)
    return result.scalars().all()


@router.post(
    "/organizations/{org_id}/vendors/{vendor_id}/assessments",
    response_model=VendorAssessmentResponse,
    status_code=201
)
async def create_vendor_assessment(
    org_id: UUID,
    vendor_id: UUID,
    assessment_data: VendorAssessmentCreate,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db)
):
    """
    Create a new vendor assessment.
    Requires: editor role or higher.
    """
    current_user = membership.user
    await _get_vendor_or_404(org_id, vendor_id, db)

    new_assessment = VendorAssessment(
        vendor_id=vendor_id,
        created_by_user_id=UUID(current_user.db_id) if current_user and current_user.db_id else None,
        **assessment_data.model_dump()
    )
    db.add(new_assessment)
    await db.commit()
    await db.refresh(new_assessment)

    query = select(VendorAssessment).where(VendorAssessment.id == new_assessment.id).options(
        selectinload(VendorAssessment.created_by),
        selectinload(VendorAssessment.updated_by),
        selectinload(VendorAssessment.assessor)
    )
    result = await db.execute(query)
    return result.scalar_one()


@router.patch(
    "/organizations/{org_id}/vendors/{vendor_id}/assessments/{assessment_id}",
    response_model=VendorAssessmentResponse
)
async def update_vendor_assessment(
    org_id: UUID,
    vendor_id: UUID,
    assessment_id: UUID,
    assessment_update: VendorAssessmentUpdate,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db)
):
    """
    Partially update a vendor assessment.
    Requires: editor role or higher.
    """
    current_user = membership.user
    await _get_vendor_or_404(org_id, vendor_id, db)

    result = await db.execute(
        select(VendorAssessment).where(
            and_(
                VendorAssessment.vendor_id == vendor_id,
                VendorAssessment.id == assessment_id
            )
        )
    )
    assessment = result.scalar_one_or_none()
    if not assessment:
        raise HTTPException(status_code=404, detail="Vendor assessment not found")

    update_data = assessment_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(assessment, key, value)

    if current_user:
        assessment.updated_by_user_id = UUID(current_user.db_id) if current_user.db_id else None

    await db.commit()
    await db.refresh(assessment)

    query = select(VendorAssessment).where(VendorAssessment.id == assessment.id).options(
        selectinload(VendorAssessment.created_by),
        selectinload(VendorAssessment.updated_by),
        selectinload(VendorAssessment.assessor)
    )
    result = await db.execute(query)
    return result.scalar_one()


@router.delete(
    "/organizations/{org_id}/vendors/{vendor_id}/assessments/{assessment_id}",
    response_model=SuccessResponse
)
async def delete_vendor_assessment(
    org_id: UUID,
    vendor_id: UUID,
    assessment_id: UUID,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db)
):
    """
    Delete a vendor assessment.
    Requires: editor role or higher.
    """
    await _get_vendor_or_404(org_id, vendor_id, db)

    result = await db.execute(
        select(VendorAssessment).where(
            and_(
                VendorAssessment.vendor_id == vendor_id,
                VendorAssessment.id == assessment_id
            )
        )
    )
    assessment = result.scalar_one_or_none()
    if not assessment:
        raise HTTPException(status_code=404, detail="Vendor assessment not found")

    await db.delete(assessment)
    await db.commit()

    return SuccessResponse(message="Vendor assessment deleted successfully")


# =============================================================================
# Vendor Certification CRUD
# =============================================================================

@router.get(
    "/organizations/{org_id}/vendors/{vendor_id}/certifications",
    response_model=List[VendorCertificationResponse]
)
async def list_vendor_certifications(
    org_id: UUID,
    vendor_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db)
):
    """
    List all certifications for a vendor.
    Requires: viewer role or higher.
    """
    await _get_vendor_or_404(org_id, vendor_id, db)

    query = select(VendorCertification).where(
        VendorCertification.vendor_id == vendor_id
    ).options(
        selectinload(VendorCertification.created_by),
        selectinload(VendorCertification.updated_by)
    ).order_by(VendorCertification.certification_name)

    result = await db.execute(query)
    return result.scalars().all()


@router.post(
    "/organizations/{org_id}/vendors/{vendor_id}/certifications",
    response_model=VendorCertificationResponse,
    status_code=201
)
async def create_vendor_certification(
    org_id: UUID,
    vendor_id: UUID,
    cert_data: VendorCertificationCreate,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db)
):
    """
    Create a new vendor certification.
    Requires: editor role or higher.
    """
    current_user = membership.user
    await _get_vendor_or_404(org_id, vendor_id, db)

    new_cert = VendorCertification(
        vendor_id=vendor_id,
        created_by_user_id=UUID(current_user.db_id) if current_user and current_user.db_id else None,
        **cert_data.model_dump()
    )
    db.add(new_cert)
    await db.commit()
    await db.refresh(new_cert)

    query = select(VendorCertification).where(VendorCertification.id == new_cert.id).options(
        selectinload(VendorCertification.created_by),
        selectinload(VendorCertification.updated_by)
    )
    result = await db.execute(query)
    return result.scalar_one()


@router.patch(
    "/organizations/{org_id}/vendors/{vendor_id}/certifications/{cert_id}",
    response_model=VendorCertificationResponse
)
async def update_vendor_certification(
    org_id: UUID,
    vendor_id: UUID,
    cert_id: UUID,
    cert_update: VendorCertificationUpdate,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db)
):
    """
    Partially update a vendor certification.
    Requires: editor role or higher.
    """
    current_user = membership.user
    await _get_vendor_or_404(org_id, vendor_id, db)

    result = await db.execute(
        select(VendorCertification).where(
            and_(
                VendorCertification.vendor_id == vendor_id,
                VendorCertification.id == cert_id
            )
        )
    )
    cert = result.scalar_one_or_none()
    if not cert:
        raise HTTPException(status_code=404, detail="Vendor certification not found")

    update_data = cert_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(cert, key, value)

    if current_user:
        cert.updated_by_user_id = UUID(current_user.db_id) if current_user.db_id else None

    await db.commit()
    await db.refresh(cert)

    query = select(VendorCertification).where(VendorCertification.id == cert.id).options(
        selectinload(VendorCertification.created_by),
        selectinload(VendorCertification.updated_by)
    )
    result = await db.execute(query)
    return result.scalar_one()


@router.delete(
    "/organizations/{org_id}/vendors/{vendor_id}/certifications/{cert_id}",
    response_model=SuccessResponse
)
async def delete_vendor_certification(
    org_id: UUID,
    vendor_id: UUID,
    cert_id: UUID,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db)
):
    """
    Delete a vendor certification.
    Requires: editor role or higher.
    """
    await _get_vendor_or_404(org_id, vendor_id, db)

    result = await db.execute(
        select(VendorCertification).where(
            and_(
                VendorCertification.vendor_id == vendor_id,
                VendorCertification.id == cert_id
            )
        )
    )
    cert = result.scalar_one_or_none()
    if not cert:
        raise HTTPException(status_code=404, detail="Vendor certification not found")

    await db.delete(cert)
    await db.commit()

    return SuccessResponse(message="Vendor certification deleted successfully")


# =============================================================================
# Consultant Access Endpoint
# =============================================================================

@router.get(
    "/consultant/clients/{org_id}/vendors",
    response_model=List[VendorResponse]
)
async def list_client_vendors(
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    status: Optional[str] = Query(None, description="Filter by vendor status"),
    db: AsyncSession = Depends(get_db)
):
    """
    List vendors for a consultant's client organisation.
    Uses existing require_org_role("viewer") which validates consultant relationships.
    """
    query = select(Vendor).where(Vendor.organization_id == org_id)

    if status:
        query = query.where(Vendor.status == status)

    query = query.options(
        selectinload(Vendor.created_by),
        selectinload(Vendor.updated_by)
    ).order_by(Vendor.name)

    result = await db.execute(query)
    return result.scalars().all()


# =============================================================================
# Vendor Research (Issue #59)
# =============================================================================

@router.post(
    "/organizations/{org_id}/vendors/{vendor_id}/research",
    response_model=VendorResearchTriggerResponse,
    status_code=202,
)
async def trigger_vendor_research(
    org_id: UUID,
    vendor_id: UUID,
    body: VendorResearchTriggerRequest,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """Trigger AI-powered research for a vendor. Requires editor role."""
    # Validate vendor exists within the organisation
    await _get_vendor_or_404(org_id, vendor_id, db)

    try:
        result = await trigger_research(
            db=db,
            vendor_id=str(vendor_id),
            user_id=membership.user.db_id,
            domain_override=body.domain_override,
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get(
    "/organizations/{org_id}/vendors/{vendor_id}/research/{job_id}/status",
    response_model=VendorResearchStatusResponse,
)
async def get_vendor_research_status(
    org_id: UUID,
    vendor_id: UUID,
    job_id: str,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """Poll the status of a vendor research job. Requires viewer role."""
    await _get_vendor_or_404(org_id, vendor_id, db)

    result = await get_research_status(db=db, vendor_id=str(vendor_id), job_id=job_id)
    if not result:
        raise HTTPException(status_code=404, detail="Research job not found")
    return result


@router.get(
    "/organizations/{org_id}/vendors/{vendor_id}/research/{job_id}",
    response_model=VendorResearchResultResponse,
)
async def get_vendor_research_results(
    org_id: UUID,
    vendor_id: UUID,
    job_id: str,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """Get full results for a completed research job. Requires viewer role."""
    await _get_vendor_or_404(org_id, vendor_id, db)

    result = await get_research_results(db=db, vendor_id=str(vendor_id), job_id=job_id)
    if not result:
        raise HTTPException(status_code=404, detail="Research job not found")
    return result


@router.get(
    "/organizations/{org_id}/vendors/{vendor_id}/research/latest",
    response_model=VendorResearchResultResponse,
)
async def get_vendor_research_latest(
    org_id: UUID,
    vendor_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """Get the most recent completed research for a vendor. Requires viewer role."""
    await _get_vendor_or_404(org_id, vendor_id, db)

    result = await get_research_latest(db=db, vendor_id=str(vendor_id))
    if not result:
        raise HTTPException(status_code=404, detail="No completed research found for this vendor")
    return result


# =============================================================================
# Helper Functions
# =============================================================================

async def _get_vendor_or_404(org_id: UUID, vendor_id: UUID, db: AsyncSession) -> Vendor:
    """Helper to fetch a vendor or raise 404."""
    result = await db.execute(
        select(Vendor).where(
            and_(
                Vendor.organization_id == org_id,
                Vendor.id == vendor_id
            )
        )
    )
    vendor = result.scalar_one_or_none()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
    return vendor


# =============================================================================
# Vendor Claim Verifications (DPSIA Enhancement)
# =============================================================================

@router.get(
    "/organizations/{org_id}/vendors/{vendor_id}/claim-verifications",
    response_model=List[VendorClaimVerificationResponse],
)
async def list_claim_verifications(
    org_id: UUID,
    vendor_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """List all claim verifications for a vendor. Requires viewer role."""
    await _get_vendor_or_404(org_id, vendor_id, db)

    result = await db.execute(
        select(VendorClaimVerification)
        .where(VendorClaimVerification.vendor_id == vendor_id)
        .order_by(VendorClaimVerification.created_at.desc())
    )
    return result.scalars().all()


@router.post(
    "/organizations/{org_id}/vendors/{vendor_id}/claim-verifications",
    response_model=VendorClaimVerificationResponse,
    status_code=201,
)
async def create_claim_verification(
    org_id: UUID,
    vendor_id: UUID,
    data: VendorClaimVerificationCreate,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """Create a manual claim verification. Requires editor role."""
    await _get_vendor_or_404(org_id, vendor_id, db)

    verification = VendorClaimVerification(
        vendor_id=vendor_id,
        **data.model_dump(),
    )
    db.add(verification)
    await db.commit()
    await db.refresh(verification)
    return verification


@router.patch(
    "/organizations/{org_id}/vendors/{vendor_id}/claim-verifications/{cv_id}",
    response_model=VendorClaimVerificationResponse,
)
async def update_claim_verification(
    org_id: UUID,
    vendor_id: UUID,
    cv_id: UUID,
    data: VendorClaimVerificationUpdate,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """Update a claim verification. Requires editor role."""
    await _get_vendor_or_404(org_id, vendor_id, db)

    result = await db.execute(
        select(VendorClaimVerification).where(
            and_(
                VendorClaimVerification.vendor_id == vendor_id,
                VendorClaimVerification.id == cv_id,
            )
        )
    )
    verification = result.scalar_one_or_none()
    if not verification:
        raise HTTPException(status_code=404, detail="Claim verification not found")

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(verification, key, value)

    await db.commit()
    await db.refresh(verification)
    return verification


@router.delete(
    "/organizations/{org_id}/vendors/{vendor_id}/claim-verifications/{cv_id}",
    response_model=SuccessResponse,
)
async def delete_claim_verification(
    org_id: UUID,
    vendor_id: UUID,
    cv_id: UUID,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """Delete a claim verification. Requires editor role."""
    await _get_vendor_or_404(org_id, vendor_id, db)

    result = await db.execute(
        select(VendorClaimVerification).where(
            and_(
                VendorClaimVerification.vendor_id == vendor_id,
                VendorClaimVerification.id == cv_id,
            )
        )
    )
    verification = result.scalar_one_or_none()
    if not verification:
        raise HTTPException(status_code=404, detail="Claim verification not found")

    await db.delete(verification)
    await db.commit()
    return SuccessResponse(message="Claim verification deleted successfully")


@router.post(
    "/organizations/{org_id}/vendors/{vendor_id}/verify-claims",
    response_model=List[VendorClaimVerificationResponse],
    status_code=201,
)
async def trigger_claim_verification(
    org_id: UUID,
    vendor_id: UUID,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """
    Trigger automated claim verification against research data.
    Cross-references vendor certifications, breach disclosures, and
    compliance claims against HIBP, regulatory, and other research sources.
    Requires editor role.
    """
    await _get_vendor_or_404(org_id, vendor_id, db)

    from services.vendor_verification import verify_vendor_claims

    try:
        verifications = await verify_vendor_claims(
            db=db,
            vendor_id=str(vendor_id),
        )
        return verifications
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Verification failed: {exc}")


# =============================================================================
# Vendor CIA Controls (DPSIA Enhancement)
# =============================================================================

@router.get(
    "/organizations/{org_id}/vendors/{vendor_id}/assessments/{assessment_id}/cia-controls",
    response_model=List[VendorCIAControlResponse],
)
async def list_cia_controls(
    org_id: UUID,
    vendor_id: UUID,
    assessment_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """List CIA controls for a specific assessment. Requires viewer role."""
    await _get_vendor_or_404(org_id, vendor_id, db)

    result = await db.execute(
        select(VendorCIAControl)
        .where(VendorCIAControl.assessment_id == assessment_id)
        .order_by(VendorCIAControl.pillar, VendorCIAControl.control_name)
    )
    return result.scalars().all()


@router.post(
    "/organizations/{org_id}/vendors/{vendor_id}/assessments/{assessment_id}/cia-controls",
    response_model=VendorCIAControlResponse,
    status_code=201,
)
async def create_cia_control(
    org_id: UUID,
    vendor_id: UUID,
    assessment_id: UUID,
    data: VendorCIAControlCreate,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """Create a CIA control for an assessment. Requires editor role."""
    await _get_vendor_or_404(org_id, vendor_id, db)

    # Verify assessment exists
    assess_result = await db.execute(
        select(VendorAssessment).where(
            and_(
                VendorAssessment.vendor_id == vendor_id,
                VendorAssessment.id == assessment_id,
            )
        )
    )
    if not assess_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Vendor assessment not found")

    control = VendorCIAControl(
        assessment_id=assessment_id,
        **data.model_dump(),
    )
    db.add(control)
    await db.commit()
    await db.refresh(control)
    return control


@router.patch(
    "/organizations/{org_id}/cia-controls/{control_id}",
    response_model=VendorCIAControlResponse,
)
async def update_cia_control(
    org_id: UUID,
    control_id: UUID,
    data: VendorCIAControlUpdate,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """Update a CIA control. Requires editor role."""
    result = await db.execute(
        select(VendorCIAControl).where(VendorCIAControl.id == control_id)
    )
    control = result.scalar_one_or_none()
    if not control:
        raise HTTPException(status_code=404, detail="CIA control not found")

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(control, key, value)

    await db.commit()
    await db.refresh(control)
    return control


@router.delete(
    "/organizations/{org_id}/cia-controls/{control_id}",
    response_model=SuccessResponse,
)
async def delete_cia_control(
    org_id: UUID,
    control_id: UUID,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """Delete a CIA control. Requires editor role."""
    result = await db.execute(
        select(VendorCIAControl).where(VendorCIAControl.id == control_id)
    )
    control = result.scalar_one_or_none()
    if not control:
        raise HTTPException(status_code=404, detail="CIA control not found")

    await db.delete(control)
    await db.commit()
    return SuccessResponse(message="CIA control deleted successfully")


# =============================================================================
# Vendor Action Items (DPSIA Enhancement)
# =============================================================================

@router.get(
    "/organizations/{org_id}/vendors/{vendor_id}/action-items",
    response_model=List[VendorActionItemResponse],
)
async def list_action_items(
    org_id: UUID,
    vendor_id: UUID,
    status: Optional[str] = Query(None, description="Filter by status"),
    priority: Optional[str] = Query(None, description="Filter by priority"),
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """List action items for a vendor. Requires viewer role."""
    await _get_vendor_or_404(org_id, vendor_id, db)

    query = select(VendorActionItem).where(VendorActionItem.vendor_id == vendor_id)

    if status:
        query = query.where(VendorActionItem.status == status)
    if priority:
        query = query.where(VendorActionItem.priority == priority)

    query = query.order_by(VendorActionItem.created_at.desc())

    result = await db.execute(query)
    return result.scalars().all()


@router.post(
    "/organizations/{org_id}/vendors/{vendor_id}/action-items",
    response_model=VendorActionItemResponse,
    status_code=201,
)
async def create_action_item(
    org_id: UUID,
    vendor_id: UUID,
    data: VendorActionItemCreate,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """Create an action item for a vendor. Requires editor role."""
    await _get_vendor_or_404(org_id, vendor_id, db)

    item = VendorActionItem(
        vendor_id=vendor_id,
        **data.model_dump(),
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


@router.patch(
    "/organizations/{org_id}/vendors/{vendor_id}/action-items/{item_id}",
    response_model=VendorActionItemResponse,
)
async def update_action_item(
    org_id: UUID,
    vendor_id: UUID,
    item_id: UUID,
    data: VendorActionItemUpdate,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """Update an action item. Requires editor role."""
    await _get_vendor_or_404(org_id, vendor_id, db)

    result = await db.execute(
        select(VendorActionItem).where(
            and_(
                VendorActionItem.vendor_id == vendor_id,
                VendorActionItem.id == item_id,
            )
        )
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Action item not found")

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(item, key, value)

    await db.commit()
    await db.refresh(item)
    return item


@router.delete(
    "/organizations/{org_id}/vendors/{vendor_id}/action-items/{item_id}",
    response_model=SuccessResponse,
)
async def delete_action_item(
    org_id: UUID,
    vendor_id: UUID,
    item_id: UUID,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """Delete an action item. Requires editor role."""
    await _get_vendor_or_404(org_id, vendor_id, db)

    result = await db.execute(
        select(VendorActionItem).where(
            and_(
                VendorActionItem.vendor_id == vendor_id,
                VendorActionItem.id == item_id,
            )
        )
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Action item not found")

    await db.delete(item)
    await db.commit()
    return SuccessResponse(message="Action item deleted successfully")


@router.get(
    "/organizations/{org_id}/vendor-action-items",
    response_model=List[VendorActionItemResponse],
)
async def list_all_vendor_action_items(
    org_id: UUID,
    status: Optional[str] = Query(None, description="Filter by status"),
    priority: Optional[str] = Query(None, description="Filter by priority"),
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """
    List all action items across all vendors in an organisation.
    Useful for dashboard views. Requires viewer role.
    """
    # Get all vendor IDs in this org
    vendor_ids_result = await db.execute(
        select(Vendor.id).where(Vendor.organization_id == org_id)
    )
    vendor_ids = [row[0] for row in vendor_ids_result.fetchall()]

    if not vendor_ids:
        return []

    query = select(VendorActionItem).where(
        VendorActionItem.vendor_id.in_(vendor_ids)
    )

    if status:
        query = query.where(VendorActionItem.status == status)
    if priority:
        query = query.where(VendorActionItem.priority == priority)

    query = query.order_by(VendorActionItem.created_at.desc())

    result = await db.execute(query)
    return result.scalars().all()


# =============================================================================
# Vendor Compensating Controls (DPSIA Enhancement)
# =============================================================================

@router.get(
    "/organizations/{org_id}/vendors/{vendor_id}/compensating-controls",
    response_model=List[VendorCompensatingControlResponse],
)
async def list_compensating_controls(
    org_id: UUID,
    vendor_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """List compensating controls for a vendor. Requires viewer role."""
    await _get_vendor_or_404(org_id, vendor_id, db)

    result = await db.execute(
        select(VendorCompensatingControl)
        .where(VendorCompensatingControl.vendor_id == vendor_id)
        .order_by(VendorCompensatingControl.created_at.desc())
    )
    return result.scalars().all()


@router.post(
    "/organizations/{org_id}/vendors/{vendor_id}/compensating-controls",
    response_model=VendorCompensatingControlResponse,
    status_code=201,
)
async def create_compensating_control(
    org_id: UUID,
    vendor_id: UUID,
    data: VendorCompensatingControlCreate,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """Create a compensating control. Requires editor role."""
    await _get_vendor_or_404(org_id, vendor_id, db)

    control = VendorCompensatingControl(
        vendor_id=vendor_id,
        **data.model_dump(),
    )
    db.add(control)
    await db.commit()
    await db.refresh(control)
    return control


@router.patch(
    "/organizations/{org_id}/vendors/{vendor_id}/compensating-controls/{cc_id}",
    response_model=VendorCompensatingControlResponse,
)
async def update_compensating_control(
    org_id: UUID,
    vendor_id: UUID,
    cc_id: UUID,
    data: VendorCompensatingControlUpdate,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """Update a compensating control. Requires editor role."""
    await _get_vendor_or_404(org_id, vendor_id, db)

    result = await db.execute(
        select(VendorCompensatingControl).where(
            and_(
                VendorCompensatingControl.vendor_id == vendor_id,
                VendorCompensatingControl.id == cc_id,
            )
        )
    )
    control = result.scalar_one_or_none()
    if not control:
        raise HTTPException(status_code=404, detail="Compensating control not found")

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(control, key, value)

    await db.commit()
    await db.refresh(control)
    return control


@router.delete(
    "/organizations/{org_id}/vendors/{vendor_id}/compensating-controls/{cc_id}",
    response_model=SuccessResponse,
)
async def delete_compensating_control(
    org_id: UUID,
    vendor_id: UUID,
    cc_id: UUID,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """Delete a compensating control. Requires editor role."""
    await _get_vendor_or_404(org_id, vendor_id, db)

    result = await db.execute(
        select(VendorCompensatingControl).where(
            and_(
                VendorCompensatingControl.vendor_id == vendor_id,
                VendorCompensatingControl.id == cc_id,
            )
        )
    )
    control = result.scalar_one_or_none()
    if not control:
        raise HTTPException(status_code=404, detail="Compensating control not found")

    await db.delete(control)
    await db.commit()
    return SuccessResponse(message="Compensating control deleted successfully")


# =============================================================================
# DPSIA Assessment (Lambda Integration)
# =============================================================================

@router.post(
    "/organizations/{org_id}/vendors/{vendor_id}/dpsia",
    response_model=DPSIATriggerResponse,
    status_code=202,
)
async def trigger_dpsia_assessment(
    org_id: UUID,
    vendor_id: UUID,
    body: DPSIATriggerRequest,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """Trigger a DPSIA Lambda assessment for a vendor. Requires editor role."""
    await _get_vendor_or_404(org_id, vendor_id, db)

    try:
        result = await trigger_dpsia(
            db=db,
            vendor_id=str(vendor_id),
            organization_id=str(org_id),
            services_used=body.services_used,
            user_id=membership.user.db_id,
            assessment_type=body.assessment_type,
            data_role=body.data_role,
            client_name=body.client_name,
            additional_context=body.additional_context,
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get(
    "/organizations/{org_id}/vendors/{vendor_id}/dpsia/{job_id}/status",
    response_model=DPSIAStatusResponse,
)
async def get_vendor_dpsia_status(
    org_id: UUID,
    vendor_id: UUID,
    job_id: str,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """Poll the status of a DPSIA assessment job. Requires viewer role."""
    await _get_vendor_or_404(org_id, vendor_id, db)

    result = await get_dpsia_status(db=db, vendor_id=str(vendor_id), job_id=job_id)
    if not result:
        raise HTTPException(status_code=404, detail="DPSIA assessment job not found")
    return result


@router.get(
    "/organizations/{org_id}/vendors/{vendor_id}/dpsia/{job_id}",
    response_model=DPSIAResultResponse,
)
async def get_vendor_dpsia_results(
    org_id: UUID,
    vendor_id: UUID,
    job_id: str,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """Get full results for a completed DPSIA assessment. Requires viewer role."""
    await _get_vendor_or_404(org_id, vendor_id, db)

    result = await get_dpsia_results(db=db, vendor_id=str(vendor_id), job_id=job_id)
    if not result:
        raise HTTPException(status_code=404, detail="DPSIA assessment job not found")
    return result


@router.get(
    "/organizations/{org_id}/vendors/{vendor_id}/dpsia/latest",
    response_model=DPSIAResultResponse,
)
async def get_vendor_dpsia_latest(
    org_id: UUID,
    vendor_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """Get the most recent completed DPSIA assessment. Requires viewer role."""
    await _get_vendor_or_404(org_id, vendor_id, db)

    result = await get_dpsia_latest(db=db, vendor_id=str(vendor_id))
    if not result:
        raise HTTPException(status_code=404, detail="No completed DPSIA assessment found for this vendor")
    return result


@router.get(
    "/organizations/{org_id}/vendors/{vendor_id}/dpsia/active",
    response_model=DPSIAStatusResponse,
)
async def get_vendor_dpsia_active(
    org_id: UUID,
    vendor_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """Get the active (pending/running) DPSIA assessment, if any. Requires viewer role."""
    await _get_vendor_or_404(org_id, vendor_id, db)

    result = await get_dpsia_active(db=db, vendor_id=str(vendor_id))
    if not result:
        raise HTTPException(status_code=404, detail="No active DPSIA assessment for this vendor")
    return result


@router.get(
    "/organizations/{org_id}/vendors/{vendor_id}/dpsia/{job_id}/docx",
)
async def download_dpsia_docx(
    org_id: UUID,
    vendor_id: UUID,
    job_id: str,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """Get a presigned URL to download the DPSIA DOCX report. Requires viewer role."""
    from fastapi.responses import RedirectResponse
    await _get_vendor_or_404(org_id, vendor_id, db)

    from models import VendorDPSIAAssessment
    result = await db.execute(
        select(VendorDPSIAAssessment).where(
            and_(
                VendorDPSIAAssessment.vendor_id == vendor_id,
                VendorDPSIAAssessment.job_id == job_id,
            )
        )
    )
    row = result.scalar_one_or_none()
    if not row or not row.report_docx_s3_key:
        raise HTTPException(status_code=404, detail="DOCX report not available")

    from services.azure_blob_service import generate_download_url_by_key, is_configured as azure_configured
    if not azure_configured():
        raise HTTPException(status_code=503, detail="Blob storage not configured")
    presigned_url = generate_download_url_by_key(
        file_key=row.report_docx_s3_key,
        filename=row.report_filename,
    )
    return RedirectResponse(url=presigned_url)
