"""
Custom Risk Definitions API endpoints.
Handles CRUD operations for organisation-defined custom risks.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from typing import List
from uuid import UUID

from database import get_db
from models import CustomRiskDefinition, CustomRiskControlMapping, RiskAssessment, ScopedControl
from catalog_models import SCFCatalogControl
from schemas import (
    CustomRiskDefinitionCreate,
    CustomRiskDefinitionUpdate,
    CustomRiskDefinitionResponse,
    CustomRiskControlMappingCreate,
    SuccessResponse,
)
from auth import require_org_role, OrgMembership
from services.audit_service import (
    log_entity_changes, detect_action_source, get_request_id,
    CUSTOM_RISK_TRACKED_FIELDS, CUSTOM_RISK_CONTROL_MAPPING_TRACKED_FIELDS,
)

router = APIRouter(tags=["custom-risks"])


async def _next_risk_code(org_id: UUID, db: AsyncSession) -> str:
    """Generate the next R-ORG-N risk code for an organisation."""
    result = await db.execute(
        select(func.max(CustomRiskDefinition.risk_code)).where(
            CustomRiskDefinition.organization_id == org_id
        )
    )
    max_code = result.scalar()
    if max_code:
        next_num = int(max_code.split('-')[-1]) + 1
    else:
        next_num = 1
    return f"R-ORG-{next_num}"


@router.get(
    "/organizations/{org_id}/custom-risks",
    response_model=List[CustomRiskDefinitionResponse]
)
async def list_custom_risks(
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db)
):
    """List all custom risk definitions for an organisation."""
    result = await db.execute(
        select(CustomRiskDefinition).where(
            CustomRiskDefinition.organization_id == org_id
        ).order_by(CustomRiskDefinition.risk_code)
    )
    return result.scalars().all()


@router.post(
    "/organizations/{org_id}/custom-risks",
    response_model=CustomRiskDefinitionResponse,
    status_code=201
)
async def create_custom_risk(
    org_id: UUID,
    data: CustomRiskDefinitionCreate,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db)
):
    """Create a new custom risk definition and its assessment record."""
    current_user = membership.user
    user_id = UUID(current_user.db_id) if current_user and current_user.db_id else None

    risk_code = await _next_risk_code(org_id, db)

    # Create the definition
    definition = CustomRiskDefinition(
        organization_id=org_id,
        risk_code=risk_code,
        title=data.title,
        description=data.description,
        category_name=data.category_name,
        category_color=data.category_color,
        created_by_user_id=user_id,
    )
    db.add(definition)

    # Auto-create the corresponding risk assessment
    assessment = RiskAssessment(
        organization_id=org_id,
        risk_code=risk_code,
        treatment_status='identified',
        created_by_user_id=user_id,
    )
    db.add(assessment)

    await db.flush()

    # Audit log
    new_values = {f: getattr(definition, f) for f in CUSTOM_RISK_TRACKED_FIELDS if hasattr(definition, f)}
    await log_entity_changes(
        db=db, organization_id=org_id, entity_type='custom_risk_definition',
        entity_id=definition.id, action='create', changed_by_user_id=user_id,
        old_values={}, new_values=new_values,
        tracked_fields=CUSTOM_RISK_TRACKED_FIELDS,
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )

    await db.commit()
    await db.refresh(definition)
    return definition


@router.patch(
    "/organizations/{org_id}/custom-risks/{risk_code}",
    response_model=CustomRiskDefinitionResponse
)
async def update_custom_risk(
    org_id: UUID,
    risk_code: str,
    data: CustomRiskDefinitionUpdate,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db)
):
    """Update a custom risk definition's metadata."""
    result = await db.execute(
        select(CustomRiskDefinition).where(
            and_(
                CustomRiskDefinition.organization_id == org_id,
                CustomRiskDefinition.risk_code == risk_code
            )
        )
    )
    definition = result.scalar_one_or_none()
    if not definition:
        raise HTTPException(status_code=404, detail=f"Custom risk '{risk_code}' not found")

    current_user = membership.user
    user_id = UUID(current_user.db_id) if current_user and current_user.db_id else None

    old_values = {f: getattr(definition, f) for f in CUSTOM_RISK_TRACKED_FIELDS if hasattr(definition, f)}

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(definition, key, value)

    new_values = {f: getattr(definition, f) for f in CUSTOM_RISK_TRACKED_FIELDS if hasattr(definition, f)}
    await log_entity_changes(
        db=db, organization_id=org_id, entity_type='custom_risk_definition',
        entity_id=definition.id, action='update', changed_by_user_id=user_id,
        old_values=old_values, new_values=new_values,
        tracked_fields=CUSTOM_RISK_TRACKED_FIELDS,
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )

    await db.commit()
    await db.refresh(definition)
    return definition


@router.delete(
    "/organizations/{org_id}/custom-risks/{risk_code}",
    response_model=SuccessResponse
)
async def delete_custom_risk(
    org_id: UUID,
    risk_code: str,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db)
):
    """Delete a custom risk definition and its assessment record."""
    result = await db.execute(
        select(CustomRiskDefinition).where(
            and_(
                CustomRiskDefinition.organization_id == org_id,
                CustomRiskDefinition.risk_code == risk_code
            )
        )
    )
    definition = result.scalar_one_or_none()
    if not definition:
        raise HTTPException(status_code=404, detail=f"Custom risk '{risk_code}' not found")

    current_user = membership.user
    user_id = UUID(current_user.db_id) if current_user and current_user.db_id else None

    # Audit log
    old_values = {f: getattr(definition, f) for f in CUSTOM_RISK_TRACKED_FIELDS if hasattr(definition, f)}
    await log_entity_changes(
        db=db, organization_id=org_id, entity_type='custom_risk_definition',
        entity_id=definition.id, action='delete', changed_by_user_id=user_id,
        old_values=old_values, new_values={},
        tracked_fields=CUSTOM_RISK_TRACKED_FIELDS,
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )

    # Delete control mappings for this custom risk
    mapping_result = await db.execute(
        select(CustomRiskControlMapping).where(
            and_(
                CustomRiskControlMapping.organization_id == org_id,
                CustomRiskControlMapping.risk_code == risk_code
            )
        )
    )
    for mapping in mapping_result.scalars().all():
        await db.delete(mapping)

    # Delete the corresponding risk assessment too
    assessment_result = await db.execute(
        select(RiskAssessment).where(
            and_(
                RiskAssessment.organization_id == org_id,
                RiskAssessment.risk_code == risk_code
            )
        )
    )
    assessment = assessment_result.scalar_one_or_none()
    if assessment:
        await db.delete(assessment)

    await db.delete(definition)
    await db.commit()

    return SuccessResponse(message=f"Custom risk '{risk_code}' deleted successfully")


# =============================================================================
# Custom Risk Control Mappings
# =============================================================================

@router.get(
    "/organizations/{org_id}/custom-risks/{risk_code}/controls",
    response_model=dict
)
async def list_custom_risk_controls(
    org_id: UUID,
    risk_code: str,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db)
):
    """List controls linked to a custom risk."""
    # Get the scf_ids from mappings
    result = await db.execute(
        select(CustomRiskControlMapping.scf_id).where(
            and_(
                CustomRiskControlMapping.organization_id == org_id,
                CustomRiskControlMapping.risk_code == risk_code
            )
        )
    )
    scf_ids = [row[0] for row in result.all()]

    if not scf_ids:
        return {
            "risk_code": risk_code,
            "total_catalog_controls": 0,
            "catalog_control_ids": [],
            "scoped_controls": []
        }

    # Look up control names from catalog
    catalog_result = await db.execute(
        select(SCFCatalogControl.scf_id, SCFCatalogControl.control_name).where(
            SCFCatalogControl.scf_id.in_(scf_ids)
        )
    )
    catalog_lookup = {row.scf_id: row.control_name for row in catalog_result.all()}

    # Get scoped controls for enrichment
    scoped_result = await db.execute(
        select(ScopedControl).where(
            and_(
                ScopedControl.organization_id == org_id,
                ScopedControl.scf_id.in_(scf_ids),
                ScopedControl.selected == True
            )
        ).order_by(ScopedControl.scf_id)
    )
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
        "risk_code": risk_code,
        "total_catalog_controls": len(scf_ids),
        "catalog_control_ids": scf_ids,
        "scoped_controls": scoped_response
    }


@router.post(
    "/organizations/{org_id}/custom-risks/{risk_code}/controls",
    response_model=SuccessResponse,
    status_code=201
)
async def add_custom_risk_control(
    org_id: UUID,
    risk_code: str,
    data: CustomRiskControlMappingCreate,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db)
):
    """Link a scoped control to a custom risk."""
    # Verify the custom risk exists
    risk_result = await db.execute(
        select(CustomRiskDefinition).where(
            and_(
                CustomRiskDefinition.organization_id == org_id,
                CustomRiskDefinition.risk_code == risk_code
            )
        )
    )
    if not risk_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail=f"Custom risk '{risk_code}' not found")

    # Check for duplicate
    existing = await db.execute(
        select(CustomRiskControlMapping).where(
            and_(
                CustomRiskControlMapping.organization_id == org_id,
                CustomRiskControlMapping.risk_code == risk_code,
                CustomRiskControlMapping.scf_id == data.scf_id
            )
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Control '{data.scf_id}' is already linked to '{risk_code}'")

    current_user = membership.user
    user_id = UUID(current_user.db_id) if current_user and current_user.db_id else None

    mapping = CustomRiskControlMapping(
        organization_id=org_id,
        risk_code=risk_code,
        scf_id=data.scf_id,
        created_by_user_id=user_id,
    )
    db.add(mapping)
    await db.flush()

    # Audit log
    new_values = {f: getattr(mapping, f) for f in CUSTOM_RISK_CONTROL_MAPPING_TRACKED_FIELDS}
    await log_entity_changes(
        db=db, organization_id=org_id, entity_type='custom_risk_control_mapping',
        entity_id=mapping.id, action='create', changed_by_user_id=user_id,
        old_values={}, new_values=new_values,
        tracked_fields=CUSTOM_RISK_CONTROL_MAPPING_TRACKED_FIELDS,
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )

    await db.commit()
    return SuccessResponse(message=f"Control '{data.scf_id}' linked to '{risk_code}'")


@router.delete(
    "/organizations/{org_id}/custom-risks/{risk_code}/controls/{scf_id}",
    response_model=SuccessResponse
)
async def remove_custom_risk_control(
    org_id: UUID,
    risk_code: str,
    scf_id: str,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db)
):
    """Remove a control link from a custom risk."""
    result = await db.execute(
        select(CustomRiskControlMapping).where(
            and_(
                CustomRiskControlMapping.organization_id == org_id,
                CustomRiskControlMapping.risk_code == risk_code,
                CustomRiskControlMapping.scf_id == scf_id
            )
        )
    )
    mapping = result.scalar_one_or_none()
    if not mapping:
        raise HTTPException(status_code=404, detail=f"Mapping not found for '{risk_code}' -> '{scf_id}'")

    current_user = membership.user
    user_id = UUID(current_user.db_id) if current_user and current_user.db_id else None

    # Audit log
    old_values = {f: getattr(mapping, f) for f in CUSTOM_RISK_CONTROL_MAPPING_TRACKED_FIELDS}
    await log_entity_changes(
        db=db, organization_id=org_id, entity_type='custom_risk_control_mapping',
        entity_id=mapping.id, action='delete', changed_by_user_id=user_id,
        old_values=old_values, new_values={},
        tracked_fields=CUSTOM_RISK_CONTROL_MAPPING_TRACKED_FIELDS,
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )

    await db.delete(mapping)
    await db.commit()
    return SuccessResponse(message=f"Control '{scf_id}' unlinked from '{risk_code}'")
