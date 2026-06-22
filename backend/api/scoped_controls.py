"""
Scoped Controls API endpoints.
Handles CRUD operations for control scoping.
"""
import logging
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, text, func, or_, literal
from sqlalchemy.orm import aliased
from typing import List, Set, Optional, Literal
from uuid import UUID

from database import get_db
from models import ScopedControl, Organization
from catalog_models import SCFCatalogControl
from schemas import (
    ScopedControlResponse,
    ScopedControlCreate,
    ScopedControlUpdate,
    ScopedControlStats,
    SuccessResponse,
    BulkScopeFrameworkRequest,
    BulkScopeFrameworkResponse,
    BulkUnscopeFrameworkRequest,
    BulkUnscopeFrameworkResponse,
    ResetScopeResponse,
    BatchScopedControlRequest,
    BatchScopedControlResponse,
)
from auth import require_org_role, OrgMembership
from services.audit_service import log_entity_changes, get_request_id, detect_action_source, SCOPED_CONTROL_TRACKED_FIELDS

logger = logging.getLogger(__name__)

router = APIRouter(tags=["scoped_controls"])


@router.get(
    "/organizations/{org_id}/scoped-controls",
    response_model=List[ScopedControlResponse]
)
async def list_scoped_controls(
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db)
):
    """
    Get all scoped controls for an organization.
    Requires: viewer role or higher.
    """
    # Get scoped controls (org existence verified by require_org_role)
    result = await db.execute(
        select(ScopedControl).where(ScopedControl.organization_id == org_id)
    )
    controls = result.scalars().all()
    return controls


@router.get(
    "/organizations/{org_id}/scoped-controls/stats",
    response_model=ScopedControlStats
)
async def get_scoped_control_stats(
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db)
):
    """
    Get aggregated stats for scoped controls.
    Requires: viewer role or higher.

    Returns server-side counts for the stats bar:
    - total_controls: Total controls in the SCF catalog
    - in_scope: Controls with selected=True
    - Per-status breakdowns (implemented, not_started, etc.)
    """
    # Total catalog controls
    total_controls = await db.scalar(
        select(func.count()).select_from(SCFCatalogControl)
    )

    # Aggregate scoped control counts using a single query with CASE WHEN
    status_counts = await db.execute(
        select(
            func.count().filter(ScopedControl.selected == True).label("in_scope"),
            func.count().filter(
                and_(ScopedControl.selected == True, ScopedControl.implementation_status == "implemented")
            ).label("implemented"),
            func.count().filter(
                and_(ScopedControl.selected == True, ScopedControl.implementation_status == "not_started")
            ).label("not_started"),
            func.count().filter(
                and_(ScopedControl.selected == True, ScopedControl.implementation_status == "in_progress")
            ).label("in_progress"),
            func.count().filter(
                and_(ScopedControl.selected == True, ScopedControl.implementation_status == "not_applicable")
            ).label("not_applicable"),
            func.count().filter(
                and_(ScopedControl.selected == True, ScopedControl.implementation_status == "at_risk")
            ).label("at_risk"),
            func.count().filter(
                and_(ScopedControl.selected == True, ScopedControl.implementation_status == "deferred")
            ).label("deferred"),
            func.count().filter(
                and_(ScopedControl.selected == True, ScopedControl.implementation_status == "ready_for_review")
            ).label("ready_for_review"),
            func.count().filter(
                and_(ScopedControl.selected == True, ScopedControl.implementation_status == "monitored")
            ).label("monitored"),
        ).where(ScopedControl.organization_id == org_id)
    )

    row = status_counts.one()

    return ScopedControlStats(
        total_controls=total_controls or 0,
        in_scope=row.in_scope or 0,
        implemented=row.implemented or 0,
        not_started=row.not_started or 0,
        in_progress=row.in_progress or 0,
        not_applicable=row.not_applicable or 0,
        at_risk=row.at_risk or 0,
        deferred=row.deferred or 0,
        ready_for_review=row.ready_for_review or 0,
        monitored=row.monitored or 0,
    )


@router.get("/organizations/{org_id}/scoped-controls-paginated")
async def list_scoped_controls_paginated(
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
    scope_status: Optional[str] = Query(None, description="Filter: in_scope, out_of_scope, or all"),
    domain: Optional[str] = Query(None, description="Filter by SCF domain identifier"),
    csf_function: Optional[str] = Query(None, description="Filter by NIST CSF function"),
    framework: Optional[str] = Query(None, description="Filter by framework mapping"),
    control_weighting: Optional[int] = Query(None, ge=0, le=10, description="Filter by control weighting (0-10)"),
    search: Optional[str] = Query(None, description="Search control name/description/ID"),
    limit: int = Query(50, ge=1, le=200, description="Max results per page"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
):
    """
    Get paginated controls with scoping status for an organization.
    Requires: viewer role or higher.

    This endpoint is optimized for the Control Scoping page with server-side
    filtering and pagination. Returns catalog controls joined with scoping data.

    Scope filters:
    - in_scope: Controls with selected=True in scoped_controls
    - out_of_scope: Controls NOT in scoped_controls or with selected=False
    - all (or None): All catalog controls with their scoping status
    """
    # Organization existence verified by require_org_role

    # Build the base query with LEFT JOIN
    # We need catalog controls with optional scoping data
    query = (
        select(
            SCFCatalogControl,
            ScopedControl.selected,
            ScopedControl.implementation_status,
            ScopedControl.selection_reason,
        )
        .outerjoin(
            ScopedControl,
            and_(
                SCFCatalogControl.scf_id == ScopedControl.scf_id,
                ScopedControl.organization_id == org_id
            )
        )
    )

    # Apply scope_status filter
    if scope_status == "in_scope":
        query = query.where(ScopedControl.selected == True)
    elif scope_status == "out_of_scope":
        query = query.where(
            or_(
                ScopedControl.scf_id.is_(None),
                ScopedControl.selected == False
            )
        )
    # "all" or None = no scope filter

    # Apply domain filter
    if domain:
        query = query.where(SCFCatalogControl.scf_id.like(f"{domain}-%"))

    # Apply CSF function filter
    if csf_function:
        query = query.where(SCFCatalogControl.nist_csf_function == csf_function)

    # Apply framework filter (JSONB key exists)
    if framework:
        query = query.where(
            text("scf_catalog_controls.framework_mappings ? :fw")
        ).params(fw=framework)

    # Apply control weighting filter
    if control_weighting is not None:
        query = query.where(SCFCatalogControl.control_weighting == control_weighting)

    # Apply search filter
    if search:
        search_term = f"%{search}%"
        query = query.where(
            or_(
                SCFCatalogControl.control_name.ilike(search_term),
                SCFCatalogControl.control_description.ilike(search_term),
                SCFCatalogControl.scf_id.ilike(search_term),
            )
        )

    # Get total count for pagination
    count_subquery = query.subquery()
    total = await db.scalar(select(func.count()).select_from(count_subquery))

    # Apply ordering and pagination
    query = query.order_by(SCFCatalogControl.scf_id).offset(offset).limit(limit)

    result = await db.execute(query)
    rows = result.all()

    # Build response with enriched control data
    controls = []
    for row in rows:
        catalog = row[0]  # SCFCatalogControl
        selected = row[1]  # ScopedControl.selected or None
        impl_status = row[2]  # ScopedControl.implementation_status or None
        selection_reason = row[3]  # ScopedControl.selection_reason or None

        controls.append({
            "scf_id": catalog.scf_id,
            "scf_domain": catalog.scf_domain,
            "control_name": catalog.control_name,
            "control_description": catalog.control_description,
            "control_question": catalog.control_question,
            "validation_cadence": catalog.validation_cadence,
            "control_weighting": catalog.control_weighting,
            "nist_csf_function": catalog.nist_csf_function,
            "evidence_requests": catalog.evidence_requests or [],
            "framework_mappings": catalog.framework_mappings or {},
            # Scoping status
            "is_scoped": selected is not None,
            "selected": selected or False,
            "implementation_status": impl_status,
            "selection_reason": selection_reason,
            # Extended data for detail view
            "pptdf_applicability": {
                "people": catalog.pptdf_people,
                "process": catalog.pptdf_process,
                "technology": catalog.pptdf_technology,
                "data": catalog.pptdf_data,
                "facility": catalog.pptdf_facility,
            },
            "cmm_maturity": {
                "level_0": catalog.cmm_level_0,
                "level_1": catalog.cmm_level_1,
                "level_2": catalog.cmm_level_2,
                "level_3": catalog.cmm_level_3,
                "level_4": catalog.cmm_level_4,
                "level_5": catalog.cmm_level_5,
            },
            "business_size_guidance": {
                "micro_small": catalog.biz_micro_small,
                "small": catalog.biz_small,
                "medium": catalog.biz_medium,
                "large": catalog.biz_large,
                "enterprise": catalog.biz_enterprise,
            },
            "scrm_focus": {
                "tier1_strategic": catalog.scrm_tier1_strategic,
                "tier2_operational": catalog.scrm_tier2_operational,
                "tier3_tactical": catalog.scrm_tier3_tactical,
            },
            "risk_threat_mapping": {
                "risk_codes": catalog.risk_codes or [],
                "threat_codes": catalog.threat_codes or [],
            },
        })

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "controls": controls,
    }


@router.get(
    "/organizations/{org_id}/scoped-controls/{scf_id}",
    response_model=ScopedControlResponse
)
async def get_scoped_control(
    org_id: UUID,
    scf_id: str,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db)
):
    """
    Get a single scoped control by SCF ID.
    Requires: viewer role or higher.
    """
    result = await db.execute(
        select(ScopedControl).where(
            and_(
                ScopedControl.organization_id == org_id,
                ScopedControl.scf_id == scf_id
            )
        )
    )
    control = result.scalar_one_or_none()

    if not control:
        raise HTTPException(status_code=404, detail="Scoped control not found")

    return control


def flatten_pptdf(data: dict) -> dict:
    """
    Flatten pptdf_applicability nested object into individual pptdf_* fields.
    The schema uses nested PPTDFApplicability but the DB has individual columns.
    """
    result = dict(data)
    pptdf = result.pop('pptdf_applicability', None)
    if pptdf:
        result['pptdf_people'] = pptdf.get('people', False)
        result['pptdf_process'] = pptdf.get('process', False)
        result['pptdf_technology'] = pptdf.get('technology', False)
        result['pptdf_data'] = pptdf.get('data', False)
        result['pptdf_facility'] = pptdf.get('facility', False)
    return result


@router.post(
    "/organizations/{org_id}/scoped-controls",
    response_model=ScopedControlResponse,
    status_code=201
)
async def create_or_update_scoped_control(
    org_id: UUID,
    control_data: ScopedControlCreate,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """
    Create or update a scoped control (upsert).
    Requires: editor role or higher.
    If a control with the same scf_id exists, it will be updated.
    Otherwise, a new control will be created.
    """
    # Organization existence verified by require_org_role

    # Check if control already exists
    result = await db.execute(
        select(ScopedControl).where(
            and_(
                ScopedControl.organization_id == org_id,
                ScopedControl.scf_id == control_data.scf_id
            )
        )
    )
    existing_control = result.scalar_one_or_none()

    # Flatten pptdf_applicability into individual fields
    control_dict = flatten_pptdf(control_data.model_dump(exclude_unset=True))

    user_id = UUID(membership.user.db_id)

    if existing_control:
        # Capture old values for audit trail
        old_values = {f: getattr(existing_control, f) for f in SCOPED_CONTROL_TRACKED_FIELDS}

        # Update existing control
        for key, value in control_dict.items():
            setattr(existing_control, key, value)
        existing_control.updated_by_user_id = user_id

        # Auto-set completion_date on implementation status transitions (#250)
        if 'implementation_status' in control_dict:
            new_status = control_dict['implementation_status']
            if new_status == 'implemented' and not existing_control.completion_date:
                existing_control.completion_date = date.today()
            elif new_status != 'implemented' and existing_control.completion_date:
                existing_control.completion_date = None

        # Capture new values and log changes
        new_values = {f: getattr(existing_control, f) for f in SCOPED_CONTROL_TRACKED_FIELDS}
        await log_entity_changes(
            db=db, organization_id=org_id, entity_type='scoped_control',
            entity_id=existing_control.id, action='update', changed_by_user_id=user_id,
            old_values=old_values, new_values=new_values,
            scf_id=control_data.scf_id, tracked_fields=SCOPED_CONTROL_TRACKED_FIELDS,
            action_source=detect_action_source(request),
            request_id=get_request_id(request),
        )

        await db.commit()
        await db.refresh(existing_control)
        return existing_control
    else:
        # Create new control - need full dict for creation
        full_dict = flatten_pptdf(control_data.model_dump())
        new_control = ScopedControl(
            organization_id=org_id,
            created_by_user_id=user_id,
            updated_by_user_id=user_id,
            **full_dict
        )
        db.add(new_control)
        await db.flush()  # Get the ID before audit logging

        # Log creation
        new_values = {f: getattr(new_control, f) for f in SCOPED_CONTROL_TRACKED_FIELDS}
        await log_entity_changes(
            db=db, organization_id=org_id, entity_type='scoped_control',
            entity_id=new_control.id, action='create', changed_by_user_id=user_id,
            old_values={}, new_values=new_values,
            scf_id=control_data.scf_id, tracked_fields=SCOPED_CONTROL_TRACKED_FIELDS,
            action_source=detect_action_source(request),
            request_id=get_request_id(request),
        )

        await db.commit()
        await db.refresh(new_control)
        return new_control


@router.patch(
    "/organizations/{org_id}/scoped-controls/{scf_id}",
    response_model=ScopedControlResponse
)
async def update_scoped_control(
    org_id: UUID,
    scf_id: str,
    control_update: ScopedControlUpdate,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """
    Partially update a scoped control.
    Requires: editor role or higher.
    Only provided fields will be updated.
    """
    result = await db.execute(
        select(ScopedControl).where(
            and_(
                ScopedControl.organization_id == org_id,
                ScopedControl.scf_id == scf_id
            )
        )
    )
    control = result.scalar_one_or_none()

    if not control:
        raise HTTPException(status_code=404, detail="Scoped control not found")

    user_id = UUID(membership.user.db_id)

    # Capture old values for audit trail
    old_values = {f: getattr(control, f) for f in SCOPED_CONTROL_TRACKED_FIELDS}

    # Update only provided fields (flatten pptdf_applicability)
    update_data = flatten_pptdf(control_update.model_dump(exclude_unset=True))
    for key, value in update_data.items():
        setattr(control, key, value)
    control.updated_by_user_id = user_id

    # Auto-set completion_date on implementation status transitions (#250)
    if 'implementation_status' in update_data:
        new_status = update_data['implementation_status']
        if new_status == 'implemented' and not control.completion_date:
            control.completion_date = date.today()
        elif new_status != 'implemented' and control.completion_date:
            control.completion_date = None

    # Log field-level changes
    new_values = {f: getattr(control, f) for f in SCOPED_CONTROL_TRACKED_FIELDS}
    await log_entity_changes(
        db=db, organization_id=org_id, entity_type='scoped_control',
        entity_id=control.id, action='update', changed_by_user_id=user_id,
        old_values=old_values, new_values=new_values,
        scf_id=scf_id, tracked_fields=SCOPED_CONTROL_TRACKED_FIELDS,
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )

    await db.commit()
    await db.refresh(control)
    return control


@router.delete(
    "/organizations/{org_id}/scoped-controls/{scf_id}",
    response_model=SuccessResponse
)
async def delete_scoped_control(
    org_id: UUID,
    scf_id: str,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """
    Delete a scoped control.
    Requires: editor role or higher.
    """
    result = await db.execute(
        select(ScopedControl).where(
            and_(
                ScopedControl.organization_id == org_id,
                ScopedControl.scf_id == scf_id
            )
        )
    )
    control = result.scalar_one_or_none()

    if not control:
        raise HTTPException(status_code=404, detail="Scoped control not found")

    user_id = UUID(membership.user.db_id)

    # Log deletion before removing
    old_values = {f: getattr(control, f) for f in SCOPED_CONTROL_TRACKED_FIELDS}
    await log_entity_changes(
        db=db, organization_id=org_id, entity_type='scoped_control',
        entity_id=control.id, action='delete', changed_by_user_id=user_id,
        old_values=old_values, new_values={},
        scf_id=scf_id, tracked_fields=SCOPED_CONTROL_TRACKED_FIELDS,
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )

    await db.delete(control)
    await db.commit()

    return SuccessResponse(message=f"Scoped control {scf_id} deleted successfully")


@router.post(
    "/organizations/{org_id}/scoped-controls/bulk-scope-framework",
    response_model=BulkScopeFrameworkResponse,
    status_code=200
)
async def bulk_scope_by_framework(
    org_id: UUID,
    request: BulkScopeFrameworkRequest,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db)
):
    """
    Bulk-scope controls by framework.
    Requires: editor role or higher.

    Adds all controls mapped to the specified framework(s) to the organization's
    scope. This operation is ADDITIVE ONLY - existing scoped controls are never
    modified or overwritten.

    Example:
        POST /organizations/{org_id}/scoped-controls/bulk-scope-framework
        {
            "frameworks": ["iso_27001_2022"],
            "selection_reason": "Required by ISO 27001:2022 certification"
        }
    """
    # Organization existence verified by require_org_role

    # Get controls mapped to the requested frameworks
    # The framework_mappings column is JSONB with framework IDs as keys
    # We use jsonb_object_keys to find controls that have any of the requested frameworks
    framework_conditions = " OR ".join(
        f"framework_mappings ? :fw_{i}" for i in range(len(request.frameworks))
    )
    params = {f"fw_{i}": fw for i, fw in enumerate(request.frameworks)}

    catalog_query = text(f"""
        SELECT scf_id
        FROM scf_catalog_controls
        WHERE {framework_conditions}
    """)

    catalog_result = await db.execute(catalog_query, params)
    framework_control_ids: Set[str] = {row[0] for row in catalog_result.fetchall()}

    if not framework_control_ids:
        return BulkScopeFrameworkResponse(
            success=True,
            added=0,
            skipped=0,
            total=0,
            frameworks_processed=request.frameworks,
            message=f"No controls found for frameworks: {', '.join(request.frameworks)}"
        )

    # Get existing scoped controls for this org WITH their selected status
    existing_query = await db.execute(
        select(ScopedControl.scf_id, ScopedControl.selected)
        .where(ScopedControl.organization_id == org_id)
    )
    existing_controls = {row[0]: row[1] for row in existing_query.fetchall()}

    # Partition framework controls into three buckets
    new_control_ids: Set[str] = set()
    needs_update_ids: Set[str] = set()
    already_scoped_ids: Set[str] = set()

    for scf_id in framework_control_ids:
        if scf_id not in existing_controls:
            new_control_ids.add(scf_id)
        elif not existing_controls[scf_id]:
            needs_update_ids.add(scf_id)
        else:
            already_scoped_ids.add(scf_id)

    reason = request.selection_reason or f"Bulk scoped from: {', '.join(request.frameworks)}"

    # Batch insert new controls
    added_count = 0
    for scf_id in new_control_ids:
        new_control = ScopedControl(
            organization_id=org_id,
            scf_id=scf_id,
            selected=True,
            implementation_status="not_started",
            selection_reason=reason,
        )
        db.add(new_control)
        added_count += 1

    # Update existing controls that have selected=False → True
    updated_count = 0
    if needs_update_ids:
        await db.execute(
            ScopedControl.__table__.update()
            .where(
                and_(
                    ScopedControl.organization_id == org_id,
                    ScopedControl.scf_id.in_(needs_update_ids)
                )
            )
            .values(selected=True, selection_reason=reason)
        )
        updated_count = len(needs_update_ids)

    if added_count > 0 or updated_count > 0:
        await db.commit()

    skipped_count = len(already_scoped_ids)

    logger.info(
        f"Bulk scope by framework: org={org_id}, frameworks={request.frameworks}, "
        f"added={added_count}, updated={updated_count}, skipped={skipped_count}"
    )

    # Build response message
    framework_names = ", ".join(request.frameworks)
    parts = []
    if added_count > 0:
        parts.append(f"Added {added_count} new controls")
    if updated_count > 0:
        parts.append(f"updated {updated_count} existing controls")
    if parts:
        message = f"{' and '.join(parts)} from {framework_names}"
        if skipped_count > 0:
            message += f" ({skipped_count} already in scope)"
    else:
        message = f"All {len(framework_control_ids)} controls from {framework_names} already in scope"

    return BulkScopeFrameworkResponse(
        success=True,
        added=added_count,
        updated=updated_count,
        skipped=skipped_count,
        total=len(framework_control_ids),
        frameworks_processed=request.frameworks,
        message=message
    )


@router.post(
    "/organizations/{org_id}/scoped-controls/bulk-unscope-framework",
    response_model=BulkUnscopeFrameworkResponse,
    status_code=200
)
async def bulk_unscope_by_framework(
    org_id: UUID,
    request: BulkUnscopeFrameworkRequest,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db)
):
    """
    Bulk un-scope controls by framework with overlap protection.
    Requires: editor role or higher.

    Removes controls mapped to the specified framework(s) from scope, but
    ONLY if they have no overlap with other frameworks that are currently
    in scope. Controls shared with other active frameworks are protected.

    Example:
        POST /organizations/{org_id}/scoped-controls/bulk-unscope-framework
        {
            "frameworks": ["iso_27017_2015"],
            "removal_reason": "No longer pursuing ISO 27017 certification"
        }
    """
    removing_frameworks = set(request.frameworks)

    # 1. Find all catalog controls mapped to the frameworks being removed
    framework_conditions = " OR ".join(
        f"framework_mappings ? :fw_{i}" for i in range(len(request.frameworks))
    )
    params = {f"fw_{i}": fw for i, fw in enumerate(request.frameworks)}

    catalog_query = text(f"""
        SELECT scf_id, framework_mappings
        FROM scf_catalog_controls
        WHERE {framework_conditions}
    """)

    catalog_result = await db.execute(catalog_query, params)
    catalog_rows = catalog_result.fetchall()

    if not catalog_rows:
        return BulkUnscopeFrameworkResponse(
            success=True,
            removed=0,
            protected=0,
            already_out_of_scope=0,
            total=0,
            frameworks_processed=request.frameworks,
            message=f"No controls found for frameworks: {', '.join(request.frameworks)}"
        )

    # Build map: scf_id → set of framework keys
    control_frameworks: dict[str, set] = {}
    for row in catalog_rows:
        scf_id = row[0]
        fw_mappings = row[1] or {}
        control_frameworks[scf_id] = set(fw_mappings.keys())

    framework_control_ids = set(control_frameworks.keys())

    # 2. Get all in-scope controls for this org
    in_scope_query = await db.execute(
        select(ScopedControl.scf_id)
        .where(
            and_(
                ScopedControl.organization_id == org_id,
                ScopedControl.selected == True,
            )
        )
    )
    in_scope_ids: Set[str] = {row[0] for row in in_scope_query.fetchall()}

    # 3. Determine which frameworks were EXPLICITLY scoped by the user.
    # We parse selection_reason ("Bulk scoped from: iso_27001_2022, ...") to find
    # frameworks the user intentionally added. This avoids the bug where checking
    # ALL framework_mappings of in-scope controls produces a huge set (each SCF
    # control maps to 10-50+ frameworks), causing every control to appear
    # "protected" by frameworks the user never explicitly scoped.
    explicit_fw_query = await db.execute(
        select(ScopedControl.selection_reason)
        .where(
            and_(
                ScopedControl.organization_id == org_id,
                ScopedControl.selected == True,
                ScopedControl.selection_reason.like("Bulk scoped from:%")
            )
        )
        .distinct()
    )
    explicitly_scoped_frameworks: Set[str] = set()
    for row in explicit_fw_query.fetchall():
        if row[0]:
            fw_part = row[0].replace("Bulk scoped from:", "").strip()
            for fw in fw_part.split(", "):
                fw = fw.strip()
                if fw:
                    explicitly_scoped_frameworks.add(fw)

    active_frameworks: Set[str] = explicitly_scoped_frameworks - removing_frameworks

    # 4. For each candidate control, check overlap with explicitly-scoped frameworks
    to_remove: Set[str] = set()
    protected_controls: Set[str] = set()
    already_out: Set[str] = set()
    protected_by_count: dict[str, int] = {}

    for scf_id in framework_control_ids:
        if scf_id not in in_scope_ids:
            already_out.add(scf_id)
            continue

        # Check if this control maps to any other explicitly-scoped framework
        other_active_fws = control_frameworks[scf_id] & active_frameworks
        if other_active_fws:
            # Protected — overlaps with other in-scope frameworks
            protected_controls.add(scf_id)
            for fw in other_active_fws:
                protected_by_count[fw] = protected_by_count.get(fw, 0) + 1
        else:
            # Safe to remove — no overlap
            to_remove.add(scf_id)

    # 5. Bulk update: set selected=False for removable controls
    removed_count = 0
    if to_remove:
        reason = request.removal_reason or f"Bulk un-scoped from: {', '.join(request.frameworks)}"
        await db.execute(
            ScopedControl.__table__.update()
            .where(
                and_(
                    ScopedControl.organization_id == org_id,
                    ScopedControl.scf_id.in_(to_remove)
                )
            )
            .values(selected=False, selection_reason=reason)
        )
        removed_count = len(to_remove)
        await db.commit()

    logger.info(
        f"Bulk unscope by framework: org={org_id}, frameworks={request.frameworks}, "
        f"removed={removed_count}, protected={len(protected_controls)}, "
        f"already_out={len(already_out)}, "
        f"explicitly_scoped={explicitly_scoped_frameworks}, "
        f"active_after_removal={active_frameworks}"
    )

    # Build response message
    framework_names = ", ".join(request.frameworks)
    if removed_count > 0:
        message = f"Removed {removed_count} controls from {framework_names}"
        if protected_controls:
            message += f". {len(protected_controls)} controls protected by overlap with other in-scope frameworks"
    elif protected_controls:
        message = (
            f"No controls removed from {framework_names} — all {len(protected_controls)} "
            f"are shared with other in-scope frameworks"
        )
    else:
        message = f"No in-scope controls found for {framework_names}"

    return BulkUnscopeFrameworkResponse(
        success=True,
        removed=removed_count,
        protected=len(protected_controls),
        already_out_of_scope=len(already_out),
        total=len(framework_control_ids),
        protected_by=protected_by_count,
        frameworks_processed=request.frameworks,
        message=message
    )


@router.post(
    "/organizations/{org_id}/scoped-controls/reset-scope",
    response_model=ResetScopeResponse,
    status_code=200
)
async def reset_all_scope(
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db)
):
    """
    Remove ALL controls from scope (set selected=False).
    Requires: admin role.

    This is a destructive operation that removes every control from scope.
    Implementation data (notes, status, history) is preserved — controls
    can be re-scoped later without losing that data.
    """
    # Count in-scope controls before reset
    in_scope_count = await db.scalar(
        select(func.count())
        .where(
            and_(
                ScopedControl.organization_id == org_id,
                ScopedControl.selected == True,
            )
        )
    ) or 0

    if in_scope_count == 0:
        return ResetScopeResponse(
            success=True,
            removed=0,
            message="No controls are currently in scope"
        )

    # Bulk update: set all to selected=False
    await db.execute(
        ScopedControl.__table__.update()
        .where(
            and_(
                ScopedControl.organization_id == org_id,
                ScopedControl.selected == True,
            )
        )
        .values(selected=False, selection_reason="Scope reset — all controls removed from scope")
    )
    await db.commit()

    logger.info(
        f"Reset scope: org={org_id}, removed={in_scope_count}"
    )

    return ResetScopeResponse(
        success=True,
        removed=in_scope_count,
        message=f"Removed all {in_scope_count} controls from scope"
    )


@router.post(
    "/organizations/{org_id}/scoped-controls/batch",
    response_model=BatchScopedControlResponse,
    status_code=200
)
async def batch_update_scoped_controls(
    org_id: UUID,
    request: BatchScopedControlRequest,
    http_request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """
    Batch create/update scoped controls in a single transaction.
    Requires: editor role or higher.
    Max 500 operations per request.
    """
    user_id = UUID(membership.user.db_id)
    updated_count = 0
    created_count = 0
    failed_count = 0
    errors: List[str] = []
    result_controls: List[ScopedControl] = []

    for op in request.operations:
        try:
            # Check if control already exists
            result = await db.execute(
                select(ScopedControl).where(
                    and_(
                        ScopedControl.organization_id == org_id,
                        ScopedControl.scf_id == op.scf_id
                    )
                )
            )
            existing = result.scalar_one_or_none()

            if existing:
                # Capture old values for audit
                old_values = {f: getattr(existing, f) for f in SCOPED_CONTROL_TRACKED_FIELDS}

                # Apply updates from operation — all provided fields
                update_fields = op.model_dump(exclude={'scf_id'}, exclude_unset=True)
                for field_name, value in update_fields.items():
                    setattr(existing, field_name, value)
                existing.updated_by_user_id = user_id

                # Auto-set completion_date on implementation status transitions
                if op.implementation_status is not None:
                    if op.implementation_status == 'implemented' and not existing.completion_date:
                        existing.completion_date = date.today()
                    elif op.implementation_status != 'implemented' and existing.completion_date:
                        existing.completion_date = None

                # Audit log
                new_values = {f: getattr(existing, f) for f in SCOPED_CONTROL_TRACKED_FIELDS}
                await log_entity_changes(
                    db=db, organization_id=org_id, entity_type='scoped_control',
                    entity_id=existing.id, action='update', changed_by_user_id=user_id,
                    old_values=old_values, new_values=new_values,
                    scf_id=op.scf_id, tracked_fields=SCOPED_CONTROL_TRACKED_FIELDS,
                    action_source=detect_action_source(http_request),
                    request_id=get_request_id(http_request),
                )

                result_controls.append(existing)
                updated_count += 1
            else:
                # Create new control with all provided fields
                create_data = op.model_dump(exclude={'scf_id'}, exclude_unset=True)
                new_control = ScopedControl(
                    organization_id=org_id,
                    scf_id=op.scf_id,
                    selected=create_data.pop('selected', True),
                    implementation_status=create_data.pop('implementation_status', 'not_started'),
                    created_by_user_id=user_id,
                    updated_by_user_id=user_id,
                    **create_data,
                )
                db.add(new_control)
                await db.flush()

                # Audit log
                new_values = {f: getattr(new_control, f) for f in SCOPED_CONTROL_TRACKED_FIELDS}
                await log_entity_changes(
                    db=db, organization_id=org_id, entity_type='scoped_control',
                    entity_id=new_control.id, action='create', changed_by_user_id=user_id,
                    old_values={}, new_values=new_values,
                    scf_id=op.scf_id, tracked_fields=SCOPED_CONTROL_TRACKED_FIELDS,
                    action_source=detect_action_source(http_request),
                    request_id=get_request_id(http_request),
                )

                result_controls.append(new_control)
                created_count += 1
        except Exception as e:
            failed_count += 1
            errors.append(f"{op.scf_id}: {str(e)}")
            logger.error(f"Batch operation failed for {op.scf_id}: {e}")

    await db.commit()

    # Refresh all controls to get updated timestamps
    for control in result_controls:
        await db.refresh(control)

    logger.info(
        f"Batch scoped controls: org={org_id}, updated={updated_count}, "
        f"created={created_count}, failed={failed_count}"
    )

    return BatchScopedControlResponse(
        updated=updated_count,
        created=created_count,
        failed=failed_count,
        errors=errors,
        controls=result_controls,
    )
