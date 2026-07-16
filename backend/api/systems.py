"""
Systems Registry API endpoints.
Handles CRUD operations for systems that provide evidence.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from sqlalchemy.orm import selectinload
from typing import List, Optional
from uuid import UUID

from database import get_db
from models import System, Organization, User, Vendor
from catalog_models import SystemCatalogTemplate
from schemas import (
    SystemResponse,
    SystemCreate,
    SystemUpdate,
    SuccessResponse
)
from auth import require_org_role, OrgMembership, get_current_user
from services.audit_service import log_entity_changes, detect_action_source, get_request_id, SYSTEM_TRACKED_FIELDS

router = APIRouter(tags=["systems"])


@router.get(
    "/organizations/{org_id}/systems",
    response_model=List[SystemResponse]
)
async def list_systems(
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    system_type: Optional[str] = Query(None, description="Filter by system type"),
    status: Optional[str] = Query(None, description="Filter by status (active, inactive, deprecated)"),
    vendor_id: Optional[UUID] = Query(None, description="Filter by linked TPRM vendor id"),
    db: AsyncSession = Depends(get_db)
):
    """
    Get all systems for an organization.
    Requires: viewer role or higher.
    Optionally filter by system_type, status and/or linked vendor_id.
    """
    # Organization existence verified by require_org_role

    # Build query with optional filters
    query = select(System).where(System.organization_id == org_id)

    if system_type:
        query = query.where(System.system_type == system_type)
    if status:
        query = query.where(System.status == status)
    if vendor_id:
        query = query.where(System.vendor_id == vendor_id)

    # Include user and linked-vendor relationships for response
    query = query.options(
        selectinload(System.created_by),
        selectinload(System.updated_by),
        selectinload(System.linked_vendor)
    )

    # Order by name for consistent results
    query = query.order_by(System.name)

    result = await db.execute(query)
    systems = result.scalars().all()
    return systems


@router.get(
    "/organizations/{org_id}/systems/{system_id}",
    response_model=SystemResponse
)
async def get_system(
    org_id: UUID,
    system_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db)
):
    """
    Get a single system by ID.
    Requires: viewer role or higher.
    """
    query = select(System).where(
        and_(
            System.organization_id == org_id,
            System.id == system_id
        )
    ).options(
        selectinload(System.created_by),
        selectinload(System.updated_by),
        selectinload(System.linked_vendor)
    )

    result = await db.execute(query)
    system = result.scalar_one_or_none()

    if not system:
        raise HTTPException(status_code=404, detail="System not found")

    return system


@router.get(
    "/organizations/{org_id}/systems/by-name/{name}",
    response_model=SystemResponse
)
async def get_system_by_name(
    org_id: UUID,
    name: str,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db)
):
    """
    Get a single system by name (unique per organization).
    Requires: viewer role or higher.
    """
    query = select(System).where(
        and_(
            System.organization_id == org_id,
            System.name == name
        )
    ).options(
        selectinload(System.created_by),
        selectinload(System.updated_by),
        selectinload(System.linked_vendor)
    )

    result = await db.execute(query)
    system = result.scalar_one_or_none()

    if not system:
        raise HTTPException(status_code=404, detail="System not found")

    return system


@router.post(
    "/organizations/{org_id}/systems",
    response_model=SystemResponse,
    status_code=201
)
async def create_system(
    org_id: UUID,
    system_data: SystemCreate,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db)
):
    """
    Create a new system.
    Requires: editor role or higher.
    System names must be unique within an organization.
    """
    # Organization existence verified by require_org_role
    current_user = membership.user

    # Check if system with same name already exists
    existing = await db.execute(
        select(System).where(
            and_(
                System.organization_id == org_id,
                System.name == system_data.name
            )
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail=f"System with name '{system_data.name}' already exists in this organization"
        )

    # If created from a catalog template, the template must exist and be global
    if system_data.catalog_template_id is not None:
        template_result = await db.execute(
            select(SystemCatalogTemplate).where(
                and_(
                    SystemCatalogTemplate.id == system_data.catalog_template_id,
                    SystemCatalogTemplate.organization_id.is_(None),
                )
            )
        )
        if not template_result.scalar_one_or_none():
            raise HTTPException(
                status_code=400,
                detail="Unknown catalog template"
            )

    # If linking a TPRM vendor, it must exist and belong to this same org
    if system_data.vendor_id is not None:
        vendor_result = await db.execute(
            select(Vendor).where(
                and_(
                    Vendor.id == system_data.vendor_id,
                    Vendor.organization_id == org_id
                )
            )
        )
        if not vendor_result.scalar_one_or_none():
            raise HTTPException(
                status_code=400,
                detail="Invalid vendor_id: Vendor not found or belongs to different organization"
            )

    # Create new system
    new_system = System(
        organization_id=org_id,
        created_by_user_id=UUID(current_user.db_id) if current_user and current_user.db_id else None,
        **system_data.model_dump()
    )
    db.add(new_system)
    await db.flush()  # Get the ID before audit logging

    # Log creation
    new_values = {f: getattr(new_system, f) for f in SYSTEM_TRACKED_FIELDS if hasattr(new_system, f)}
    await log_entity_changes(
        db=db, organization_id=org_id, entity_type='system',
        entity_id=new_system.id, action='create',
        changed_by_user_id=UUID(current_user.db_id) if current_user and current_user.db_id else None,
        old_values={}, new_values=new_values,
        tracked_fields=SYSTEM_TRACKED_FIELDS,
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )

    await db.commit()
    await db.refresh(new_system)

    # Load relationships for response
    query = select(System).where(System.id == new_system.id).options(
        selectinload(System.created_by),
        selectinload(System.updated_by),
        selectinload(System.linked_vendor)
    )
    result = await db.execute(query)
    return result.scalar_one()


@router.patch(
    "/organizations/{org_id}/systems/{system_id}",
    response_model=SystemResponse
)
async def update_system(
    org_id: UUID,
    system_id: UUID,
    system_update: SystemUpdate,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db)
):
    """
    Partially update a system.
    Requires: editor role or higher.
    Only provided fields will be updated.
    """
    current_user = membership.user
    result = await db.execute(
        select(System).where(
            and_(
                System.organization_id == org_id,
                System.id == system_id
            )
        )
    )
    system = result.scalar_one_or_none()

    if not system:
        raise HTTPException(status_code=404, detail="System not found")

    # Capture old values for audit trail
    old_values = {f: getattr(system, f) for f in SYSTEM_TRACKED_FIELDS if hasattr(system, f)}

    # If linking to a catalog template, it must exist and be global
    # (same rule as create_system; None is allowed to unlink)
    update_data = system_update.model_dump(exclude_unset=True)
    if update_data.get("catalog_template_id") is not None:
        template_result = await db.execute(
            select(SystemCatalogTemplate).where(
                and_(
                    SystemCatalogTemplate.id == update_data["catalog_template_id"],
                    SystemCatalogTemplate.organization_id.is_(None),
                )
            )
        )
        if not template_result.scalar_one_or_none():
            # Keep an existing link to the system's own org-private
            # (AI-generated) template intact when the edit form echoes it back
            if update_data["catalog_template_id"] != system.catalog_template_id:
                raise HTTPException(status_code=400, detail="Unknown catalog template")

    # If linking a TPRM vendor, it must exist and belong to this same org
    # (None is allowed to unlink and is not validated)
    if update_data.get("vendor_id") is not None:
        vendor_result = await db.execute(
            select(Vendor).where(
                and_(
                    Vendor.id == update_data["vendor_id"],
                    Vendor.organization_id == org_id
                )
            )
        )
        if not vendor_result.scalar_one_or_none():
            raise HTTPException(
                status_code=400,
                detail="Invalid vendor_id: Vendor not found or belongs to different organization"
            )

    # If name is being changed, check for conflicts
    if "name" in update_data and update_data["name"] != system.name:
        existing = await db.execute(
            select(System).where(
                and_(
                    System.organization_id == org_id,
                    System.name == update_data["name"],
                    System.id != system_id
                )
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=409,
                detail=f"System with name '{update_data['name']}' already exists in this organization"
            )

    # Update fields
    for key, value in update_data.items():
        setattr(system, key, value)

    # Track who updated
    if current_user:
        system.updated_by_user_id = UUID(current_user.db_id) if current_user.db_id else None

    # Capture new values and log changes
    new_values = {f: getattr(system, f) for f in SYSTEM_TRACKED_FIELDS if hasattr(system, f)}
    await log_entity_changes(
        db=db, organization_id=org_id, entity_type='system',
        entity_id=system.id, action='update',
        changed_by_user_id=UUID(membership.user.db_id) if membership.user and membership.user.db_id else None,
        old_values=old_values, new_values=new_values,
        tracked_fields=SYSTEM_TRACKED_FIELDS,
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )

    await db.commit()
    await db.refresh(system)

    # Load relationships for response
    query = select(System).where(System.id == system.id).options(
        selectinload(System.created_by),
        selectinload(System.updated_by),
        selectinload(System.linked_vendor)
    )
    result = await db.execute(query)
    return result.scalar_one()


@router.delete(
    "/organizations/{org_id}/systems/{system_id}",
    response_model=SuccessResponse
)
async def delete_system(
    org_id: UUID,
    system_id: UUID,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db)
):
    """
    Delete a system.
    Requires: editor role or higher.
    Note: This will also delete any associated SystemEvidenceCapability records.
    """
    result = await db.execute(
        select(System).where(
            and_(
                System.organization_id == org_id,
                System.id == system_id
            )
        )
    )
    system = result.scalar_one_or_none()

    if not system:
        raise HTTPException(status_code=404, detail="System not found")

    # Capture old values for audit trail
    old_values = {f: getattr(system, f) for f in SYSTEM_TRACKED_FIELDS if hasattr(system, f)}

    system_name = system.name

    # Log deletion before removing
    await log_entity_changes(
        db=db, organization_id=org_id, entity_type='system',
        entity_id=system.id, action='delete',
        changed_by_user_id=UUID(membership.user.db_id) if membership.user and membership.user.db_id else None,
        old_values=old_values, new_values={},
        tracked_fields=SYSTEM_TRACKED_FIELDS,
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )

    await db.delete(system)
    await db.commit()

    return SuccessResponse(message=f"System '{system_name}' deleted successfully")
