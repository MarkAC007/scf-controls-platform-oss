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
from typing import List, Optional, Literal
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
from services.notifications import create_control_ready_for_review_notifications
from services.scoping_service import bulk_scope_frameworks, bulk_unscope_frameworks
from services.team_assignments import (
    CONTROL_ASSIGNMENT_SPEC,
    accountable_owner_filter,
    team_assignment_filter,
)
from services.org_utils import MEMBER_TYPES, invalid_member_type_detail

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
    # Total catalog controls (aggregates count active only — a retired
    # control is not part of the addressable catalog)
    total_controls = await db.scalar(
        select(func.count())
        .select_from(SCFCatalogControl)
        .where(SCFCatalogControl.status == 'active')
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
    team_id: Optional[UUID] = Query(None, description="Filter to controls this team is assigned to (accountable or consulted)"),
    function_id: Optional[UUID] = Query(None, description="Filter to controls assigned to any team aligned to this function"),
    accountable_owner_type: Optional[str] = Query(None, description="Filter to items whose accountable team's primary owner has this member_type: internal or external_contractor"),
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
        # Deprecated catalog rows stay visible ONLY where the org has data on
        # them (retirement must never silently hide an org's compliance work);
        # deprecated rows without org data drop out of the listing.
        .where(
            or_(
                SCFCatalogControl.status == 'active',
                ScopedControl.id.isnot(None),
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

    # Apply team / function filters (#822 phase 3)
    #
    # Placed before the count so `total` counts what the page shows. A filter
    # applied after this line would paginate a footer that disagreed with its
    # own rows.
    #
    # Semantics are ANY assigned team, not accountable-only: #822 gives
    # consulted teams visibility of the item and withholds only the routine
    # notification, so a team's list is everything it is on, not just what it
    # owns. Both filters together intersect.
    #
    # This is an EXISTS, so a control with several assigned teams stays one
    # row. It correlates on ScopedControl.id, which the LEFT JOIN leaves NULL
    # for a catalog control the org has never scoped -- such a control has no
    # scoped row to hang an assignment off and is correctly excluded.
    assignment_filter = team_assignment_filter(
        CONTROL_ASSIGNMENT_SPEC,
        ScopedControl.id,
        organization_id=org_id,
        team_id=team_id,
        function_id=function_id,
    )
    if assignment_filter is not None:
        query = query.where(assignment_filter)

    # Apply the accountable-owner filter (#822 phase 2)
    #
    # The contractor half of member_type: which controls is an external
    # contractor actually on the hook for. Shares one helper with the evidence
    # list so the two cannot disagree about who "owns" an item. Also before the
    # count, for the same reason as above.
    if accountable_owner_type is not None and accountable_owner_type not in MEMBER_TYPES:
        raise HTTPException(
            status_code=400,
            detail=invalid_member_type_detail("accountable_owner_type"),
        )

    owner_filter = accountable_owner_filter(
        CONTROL_ASSIGNMENT_SPEC,
        ScopedControl.id,
        organization_id=org_id,
        accountable_owner_type=accountable_owner_type,
    )
    if owner_filter is not None:
        query = query.where(owner_filter)

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
            # Catalog lifecycle badge (deprecated rows render badged, never hidden)
            "catalog_status": catalog.status,
            "retired_in_version": catalog.retired_in_version,
            "superseded_by": catalog.superseded_by,
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


async def _deprecated_catalog_refusal(db: AsyncSession, scf_id: str) -> Optional[dict]:
    """Return a 409 detail dict when scf_id names a deprecated catalog control.

    Scoping writes may not create NEW org rows against deprecated controls;
    updates to rows the org already holds stay allowed (handled by callers).
    """
    row = (
        await db.execute(
            select(
                SCFCatalogControl.status,
                SCFCatalogControl.retired_in_version,
                SCFCatalogControl.superseded_by,
            ).where(SCFCatalogControl.scf_id == scf_id)
        )
    ).first()
    if row is None or row.status != 'deprecated':
        return None
    message = f"Control {scf_id} is deprecated and cannot be newly scoped"
    if row.superseded_by:
        message += f"; it is superseded by {row.superseded_by}"
    return {
        "message": message,
        "scf_id": scf_id,
        "catalog_status": "deprecated",
        "retired_in_version": row.retired_in_version,
        "superseded_by": row.superseded_by,
    }


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
        # New scoped rows are refused for deprecated catalog controls (409);
        # updating an existing row for a deprecated control stays allowed above.
        refusal = await _deprecated_catalog_refusal(db, control_data.scf_id)
        if refusal is not None:
            raise HTTPException(status_code=409, detail=refusal)

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

    # Notify only on a genuine transition into ready_for_review
    became_ready_for_review = (
        update_data.get('implementation_status') == 'ready_for_review'
        and old_values.get('implementation_status') != 'ready_for_review'
    )

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

    if became_ready_for_review:
        await create_control_ready_for_review_notifications(
            db,
            organization_id=org_id,
            scoped_control_id=control.id,
            scf_id=scf_id,
            actor_user_id=user_id,
        )

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
    result = await bulk_scope_frameworks(
        db=db,
        org_id=org_id,
        framework_ids=request.frameworks,
        user_id=UUID(membership.user.db_id),
        selection_reason=request.selection_reason,
    )

    return BulkScopeFrameworkResponse(
        success=True,
        added=result.added,
        updated=result.updated,
        skipped=result.skipped,
        total=result.total,
        frameworks_processed=result.frameworks_processed,
        message=result.message
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
    result = await bulk_unscope_frameworks(
        db=db,
        org_id=org_id,
        framework_ids=request.frameworks,
        removal_reason=request.removal_reason,
    )

    return BulkUnscopeFrameworkResponse(
        success=True,
        removed=result.removed,
        protected=result.protected,
        already_out_of_scope=result.already_out_of_scope,
        total=result.total,
        protected_by=result.protected_by,
        frameworks_processed=result.frameworks_processed,
        message=result.message
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
                # New scoped rows are refused for deprecated catalog controls;
                # recorded as a per-op error so the rest of the batch proceeds.
                refusal = await _deprecated_catalog_refusal(db, op.scf_id)
                if refusal is not None:
                    failed_count += 1
                    errors.append(f"{op.scf_id}: {refusal['message']}")
                    continue

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
