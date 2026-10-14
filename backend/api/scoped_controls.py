"""
Scoped Controls API endpoints.
Handles CRUD operations for control scoping.
"""
import logging
from datetime import date, datetime
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, text, func, or_, literal, false
from sqlalchemy.orm import aliased
from typing import List, Optional, Literal, Tuple
from uuid import UUID

from database import get_db
from models import GeneratedDocument, ImplementationStatus, ScopedControl, Organization, OrganizationFrameworkSelection, assessment_is_stale
from catalog_models import SCFCatalogControl
from schemas import (
    ScopedControlResponse,
    ScopedControlCreate,
    ScopedControlUpdate,
    ScopedControlStats,
    ScopedControlsPaginatedResponse,
    SuccessResponse,
    BulkScopeFrameworkRequest,
    BulkScopeFrameworkResponse,
    BulkUnscopeFrameworkRequest,
    BulkUnscopeFrameworkResponse,
    ResetScopeResponse,
    BatchScopedControlRequest,
    BatchScopedControlResponse,
    ScopeOverrideRequest,
    FrameworkScopeSummaryResponse,
    FrameworkScopePreviewRequest,
    FrameworkScopePreviewResponse,
    FrameworkBasisRequest,
    FrameworkScopeSummaryItem,
    ControlBasisOverrideRequest,
    ControlMaterialityRequest,
    ControlProcedureDocumentRequest,
    ControlTargetMaturityRequest,
)
from auth import require_org_role, OrgMembership
from services.audit_service import (
    FRAMEWORK_SELECTION_TRACKED_FIELDS,
    SCOPED_CONTROL_TRACKED_FIELDS,
    detect_action_source,
    get_request_id,
    log_entity_changes,
)
from services.obligation_basis import (
    effective_basis_expr,
    is_mcr,
    mcr_exclusion_message,
    set_control_basis_override,
    set_selection_basis,
    unscope_touches_mcr,
)
from services.notifications import create_control_ready_for_review_notifications
from services.deficiencies import on_control_status
from services.materiality import (
    MATERIAL_WEIGHTING,
    is_material,
    material_exclusion_message,
    material_not_applicable_message,
    resolve_material,
    set_material_override,
    set_procedure_document,
    unscope_touches_material,
)
from services.target_maturity import (
    SETTINGS_KEY as MATURITY_TARGETS_KEY,
    resolve_target,
    set_control_target_override,
    stored_domain_targets,
)
from services.scoping_service import (
    bulk_scope_frameworks,
    bulk_unscope_frameworks,
    framework_scope_summary,
    preview_framework_change,
    set_individual_scope_override,
)
from services.team_assignments import (
    CONTROL_ASSIGNMENT_SPEC,
    accountable_owner_filter,
    resolve_caller_team_ids,
    team_assignment_filter,
)
def _tracked_values(control: ScopedControl) -> dict:
    """Snapshot audit fields while tolerating legacy/pre-migration row adapters."""
    return {field: getattr(control, field, None) for field in SCOPED_CONTROL_TRACKED_FIELDS}


ASSESSMENT_FIELDS = ("implementation_status", "maturity_level")


def _requested_selected(fields: dict) -> Optional[bool]:
    """``selected`` as this write would leave it, or None when the write omits it.

    An explicit null deselects: the row stores NULL and every reader treats it
    as out of scope, so it must face the same MCR guard as ``false``.
    """
    return bool(fields["selected"]) if "selected" in fields else None


async def _mcr_deselect_refusal(
    db: AsyncSession,
    org_id: UUID,
    scf_id: str,
    was_selected: Optional[bool],
    will_select: Optional[bool],
) -> Optional[str]:
    """Why this write may not take the control out of scope, or None (PIG R2).

    An MCR control leaves scope only through the scope-override endpoint, which
    records a reason and an approving admin. Every other write path that would
    deselect it, or record it as out of scope for the first time
    (``was_selected`` None), is refused. Writes that leave ``selected`` alone
    (``will_select`` None) or that touch a control already out of scope are not
    exclusions and pass.
    """
    if will_select is not False or was_selected is False:
        return None
    if await is_mcr(db, org_id, scf_id):
        return mcr_exclusion_message(scf_id)
    return None


async def _exclusion_refusal(
    db: AsyncSession,
    org_id: UUID,
    scf_id: str,
    was_selected: Optional[bool],
    will_select: Optional[bool],
    *,
    was_status: Optional[str] = None,
    will_status: Optional[str] = None,
    is_admin: bool = False,
) -> Optional[Tuple[int, str]]:
    """``(status_code, detail)`` when this write may not proceed, or None.

    The MCR guard (PIG R2) first, then materiality (PIG R6):

    * A material control leaves scope only through the scope-override
      endpoint, where an admin approves the exclusion with a reason. Every
      other path that would deselect it is refused with 409, as for MCR.
    * Marking a material control ``not_applicable`` needs an organisation
      admin (403 otherwise). The admin's write is the approval, and the audit
      log records them against the status change.
    """
    refusal = await _mcr_deselect_refusal(db, org_id, scf_id, was_selected, will_select)
    if refusal:
        return 409, refusal
    deselects = will_select is False and was_selected is not False
    to_not_applicable = (
        will_status == ImplementationStatus.NOT_APPLICABLE.value
        and was_status != ImplementationStatus.NOT_APPLICABLE.value
    )
    if not deselects and not (to_not_applicable and not is_admin):
        return None
    if not await is_material(db, org_id, scf_id):
        return None
    if deselects:
        return 409, material_exclusion_message(scf_id)
    return 403, material_not_applicable_message(scf_id)


def _stamp_rescope_and_assessment(control: ScopedControl, old_values: dict, update_data: dict) -> None:
    """Keep the two re-scope staleness stamps honest on a direct control write.

    ``assessment_recorded_at`` moves when status or maturity actually changes;
    ``scope_restored_at`` moves when this write flips the control back into
    scope. Both feed ``ScopedControl.assessment_stale``; the scoping service
    stamps the same fields on its own paths (bulk scope, override, migration).
    """
    now = datetime.utcnow()
    if any(
        field in update_data and update_data[field] != old_values.get(field)
        for field in ASSESSMENT_FIELDS
    ):
        control.assessment_recorded_at = now
    if update_data.get("selected") is True and not old_values.get("selected"):
        control.scope_restored_at = now


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


@router.get(
    "/organizations/{org_id}/scoped-controls-paginated",
    response_model=ScopedControlsPaginatedResponse,
)
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
    my_teams: bool = Query(False, description="Filter to controls assigned to any team the caller belongs to. Intersects with team_id rather than overriding it."),
    function_id: Optional[UUID] = Query(None, description="Filter to controls assigned to any team aligned to this function"),
    accountable_owner_type: Optional[str] = Query(None, description="Filter to items whose accountable team's primary owner has this member_type: internal or external_contractor"),
    basis: Optional[Literal["mcr", "dsr", "unclassified"]] = Query(None, description="Filter by obligation basis: mcr, dsr, or unclassified (no classified framework and no override)"),
    material: Optional[bool] = Query(None, description="Filter by materiality: true for material controls (catalogue weighting 10 or an override), false for the rest"),
    sort: Literal["scf_id", "weighting"] = Query("scf_id", description="Order by scf_id (default), or by weighting: material controls first, then catalogue weighting highest first, then scf_id"),
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

    # Obligation basis (PIG R2): the per-control override, else the strongest
    # basis among the org's active classified selections that map the control.
    basis_expr = effective_basis_expr(
        SCFCatalogControl.scf_id, org_id, ScopedControl.basis_override
    )

    # Target maturity (PIG R5): the organisation's domain targets ride along
    # as one scalar-subquery column rather than a second round trip; each
    # scoped row then resolves its own target from its override or its domain.
    # Only the targets key is selected, not the whole settings document, since
    # the value repeats on every row of the page.
    org_settings_col = (
        select(Organization.settings[MATURITY_TARGETS_KEY])
        .where(Organization.id == org_id)
        .scalar_subquery()
    )

    # Materiality (PIG R6): the override, else the catalogue weighting.
    material_col = func.coalesce(
        ScopedControl.material_override,
        SCFCatalogControl.control_weighting >= MATERIAL_WEIGHTING,
        false(),
    )
    # The linked procedure's lifecycle status now, scoped to this org.
    procedure_status_col = (
        select(GeneratedDocument.lifecycle_status)
        .where(
            GeneratedDocument.id == ScopedControl.procedure_document_id,
            GeneratedDocument.organization_id == org_id,
        )
        .scalar_subquery()
    )

    # Build the base query with LEFT JOIN
    # We need catalog controls with optional scoping data
    query = (
        select(
            SCFCatalogControl,
            ScopedControl.selected,
            ScopedControl.implementation_status,
            ScopedControl.selection_reason,
            # The ORG's own maturity setting. NOT the catalogue's recommended
            # levels (those are the cmm_* columns, emitted as `cmm_maturity`).
            ScopedControl.maturity_level,
            ScopedControl.id,
            ScopedControl.priority,
            ScopedControl.out_of_scope_justification,
            ScopedControl.scope_override,
            ScopedControl.scope_override_reason,
            ScopedControl.scope_override_set_at,
            ScopedControl.scope_restored_at,
            ScopedControl.assessment_recorded_at,
            basis_expr.label("obligation_basis"),
            ScopedControl.basis_override,
            ScopedControl.basis_override_reason,
            ScopedControl.target_maturity_override,
            ScopedControl.target_maturity_reason,
            org_settings_col.label("org_settings"),
            ScopedControl.material_override,
            ScopedControl.material_reason,
            ScopedControl.procedure_document_id,
            procedure_status_col.label("procedure_document_status"),
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

    if material is not None:
        query = query.where(material_col.is_(material))

    if basis == "unclassified":
        query = query.where(basis_expr.is_(None))
    elif basis is not None:
        query = query.where(basis_expr == basis)

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
    # `my_teams` (#1052) resolves the CALLER's teams and hands them to the same
    # helper as an id collection. It intersects with `team_id` rather than
    # overriding it: both clauses AND, like every other filter here. Asking for
    # a team you are not on therefore returns nothing, which is the honest
    # answer; the UI stops you reaching that state by constraining the picker.
    #
    # A caller on no team resolves to `[]`, which the helper renders as a
    # never-true clause — an empty list, not the whole organisation under a
    # label promising otherwise.
    caller_team_ids = None
    if my_teams:
        caller_team_ids = await resolve_caller_team_ids(
            db, organization_id=org_id, user_id=UUID(membership.user.db_id),
        )

    assignment_filter = team_assignment_filter(
        CONTROL_ASSIGNMENT_SPEC,
        ScopedControl.id,
        organization_id=org_id,
        team_id=team_id,
        team_ids=caller_team_ids,
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
    if sort == "weighting":
        # PIG R6: material controls first, then the heaviest, so scoping work
        # starts with them.
        query = query.order_by(
            material_col.desc(),
            SCFCatalogControl.control_weighting.desc().nulls_last(),
            SCFCatalogControl.scf_id,
        )
    else:
        query = query.order_by(SCFCatalogControl.scf_id)
    query = query.offset(offset).limit(limit)

    result = await db.execute(query)
    rows = result.all()

    # Build response with enriched control data
    controls = []
    domain_targets = stored_domain_targets(
        {MATURITY_TARGETS_KEY: rows[0][18]} if rows and len(rows[0]) > 18 else None
    )
    for row in rows:
        catalog = row[0]  # SCFCatalogControl
        selected = row[1]  # ScopedControl.selected or None
        impl_status = row[2]  # ScopedControl.implementation_status or None
        selection_reason = row[3]  # ScopedControl.selection_reason or None
        maturity_level = row[4]  # ScopedControl.maturity_level or None
        scoped_control_id = row[5] if len(row) > 5 else None
        priority = row[6] if len(row) > 6 else None
        out_of_scope_justification = row[7] if len(row) > 7 else None
        scope_override = row[8] if len(row) > 8 else None
        scope_override_reason = row[9] if len(row) > 9 else None
        scope_override_set_at = row[10] if len(row) > 10 else None
        scope_restored_at = row[11] if len(row) > 11 else None
        assessment_recorded_at = row[12] if len(row) > 12 else None
        obligation_basis = row[13] if len(row) > 13 else None
        basis_override = row[14] if len(row) > 14 else None
        basis_override_reason = row[15] if len(row) > 15 else None
        target_override = row[16] if len(row) > 16 else None
        target_reason = row[17] if len(row) > 17 else None
        material_override = row[19] if len(row) > 19 else None
        material_reason = row[20] if len(row) > 20 else None
        procedure_document_id = row[21] if len(row) > 21 else None
        procedure_document_status = row[22] if len(row) > 22 else None
        # A property of the control, so it reads the same scoped or not: a
        # control with no scope record follows its catalogue weighting.
        is_material_row, material_source = resolve_material(
            material_override, catalog.control_weighting
        )
        # A target belongs to a control with a scope record; a catalogue row
        # the org never scoped has none, whatever its domain's target is. A
        # descoped row keeps its target so it is there if the control comes
        # back, but the gates and the report count selected controls only.
        target, target_source = (
            resolve_target(catalog.scf_id, target_override, domain_targets)
            if selected is not None else (None, None)
        )

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
            "out_of_scope_justification": out_of_scope_justification,
            "scoped_control_id": scoped_control_id,
            "priority": priority,
            "scope_override": scope_override,
            "scope_override_reason": scope_override_reason,
            "scope_override_set_at": scope_override_set_at,
            "obligation_basis": obligation_basis,
            "basis_source": (
                "override" if basis_override else ("framework" if obligation_basis else None)
            ),
            "basis_override_reason": basis_override_reason,
            # The org's own maturity setting — the scoping list renders this
            # column, so omitting it left the UI hardcoding an em dash.
            "maturity_level": maturity_level,
            "target_maturity": target,
            "target_source": target_source,
            "target_maturity_reason": target_reason if target_source == "control" else None,
            "material": is_material_row,
            "material_source": material_source,
            "material_reason": material_reason if material_source == "control" else None,
            "procedure_document_id": procedure_document_id,
            "procedure_document_status": procedure_document_status if procedure_document_id else None,
            # Re-scope staleness (same rule as ScopedControl.assessment_stale)
            "scope_restored_at": scope_restored_at,
            "assessment_recorded_at": assessment_recorded_at,
            "assessment_stale": assessment_is_stale(
                selected=selected,
                implementation_status=impl_status,
                maturity_level=maturity_level,
                scope_restored_at=scope_restored_at,
                assessment_recorded_at=assessment_recorded_at,
            ),
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

    refusal = await _exclusion_refusal(
        db,
        org_id,
        control_data.scf_id,
        bool(existing_control.selected) if existing_control else None,
        _requested_selected(control_dict) if existing_control else bool(control_dict.get("selected")),
        was_status=existing_control.implementation_status if existing_control else None,
        will_status=control_dict.get("implementation_status"),
        is_admin=membership.is_admin,
    )
    if refusal:
        raise HTTPException(status_code=refusal[0], detail=refusal[1])

    if existing_control:
        # Capture old values for audit trail
        old_values = _tracked_values(existing_control)
        was_status = existing_control.implementation_status
        was_selected = bool(existing_control.selected)

        # Update existing control
        for key, value in control_dict.items():
            setattr(existing_control, key, value)
        existing_control.updated_by_user_id = user_id
        _stamp_rescope_and_assessment(existing_control, old_values, control_dict)

        # Auto-set completion_date on implementation status transitions (#250)
        if 'implementation_status' in control_dict:
            new_status = control_dict['implementation_status']
            if new_status == 'implemented' and not existing_control.completion_date:
                existing_control.completion_date = date.today()
            elif new_status != 'implemented' and existing_control.completion_date:
                existing_control.completion_date = None

        # Capture new values and log changes
        new_values = _tracked_values(existing_control)
        await log_entity_changes(
            db=db, organization_id=org_id, entity_type='scoped_control',
            entity_id=existing_control.id, action='update', changed_by_user_id=user_id,
            old_values=old_values, new_values=new_values,
            scf_id=control_data.scf_id, tracked_fields=SCOPED_CONTROL_TRACKED_FIELDS,
            action_source=detect_action_source(request),
            request_id=get_request_id(request),
        )
        # PIG R8: a control going at risk raises a deficiency.
        await on_control_status(
            db, existing_control, was_status, user_id,
            detect_action_source(request), get_request_id(request), was_selected,
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
        new_values = _tracked_values(new_control)
        await log_entity_changes(
            db=db, organization_id=org_id, entity_type='scoped_control',
            entity_id=new_control.id, action='create', changed_by_user_id=user_id,
            old_values={}, new_values=new_values,
            scf_id=control_data.scf_id, tracked_fields=SCOPED_CONTROL_TRACKED_FIELDS,
            action_source=detect_action_source(request),
            request_id=get_request_id(request),
        )
        await on_control_status(
            db, new_control, None, user_id,
            detect_action_source(request), get_request_id(request),
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
    old_values = _tracked_values(control)
    was_status = control.implementation_status
    was_selected = bool(control.selected)

    # Update only provided fields (flatten pptdf_applicability)
    update_data = flatten_pptdf(control_update.model_dump(exclude_unset=True))
    refusal = await _exclusion_refusal(
        db, org_id, scf_id, bool(control.selected), _requested_selected(update_data),
        was_status=control.implementation_status,
        will_status=update_data.get("implementation_status"),
        is_admin=membership.is_admin,
    )
    if refusal:
        raise HTTPException(status_code=refusal[0], detail=refusal[1])
    for key, value in update_data.items():
        setattr(control, key, value)
    control.updated_by_user_id = user_id
    _stamp_rescope_and_assessment(control, old_values, update_data)

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
    new_values = _tracked_values(control)
    await log_entity_changes(
        db=db, organization_id=org_id, entity_type='scoped_control',
        entity_id=control.id, action='update', changed_by_user_id=user_id,
        old_values=old_values, new_values=new_values,
        scf_id=scf_id, tracked_fields=SCOPED_CONTROL_TRACKED_FIELDS,
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )
    # PIG R8: a control going at risk raises a deficiency.
    await on_control_status(
        db, control, was_status, user_id,
        detect_action_source(request), get_request_id(request), was_selected,
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

    refusal = await _exclusion_refusal(db, org_id, scf_id, bool(control.selected), False)
    if refusal:
        raise HTTPException(status_code=refusal[0], detail=refusal[1])

    # Log deletion before removing
    old_values = _tracked_values(control)
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


@router.get(
    "/organizations/{org_id}/framework-scoping",
    response_model=FrameworkScopeSummaryResponse,
)
async def get_framework_scope_summary(
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """Organization-aware framework coverage and selection state."""
    return await framework_scope_summary(db, org_id)


@router.post(
    "/organizations/{org_id}/framework-scoping/preview",
    response_model=FrameworkScopePreviewResponse,
)
async def preview_framework_scope_change(
    org_id: UUID,
    preview: FrameworkScopePreviewRequest,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """Server-authoritative exact add/remove effects; never mutates scope."""
    return await preview_framework_change(
        db, org_id, preview.frameworks, preview.operation
    )


@router.put(
    "/organizations/{org_id}/scoped-controls/{scf_id}/scope-override",
    response_model=ScopedControlResponse,
)
async def put_scope_override(
    org_id: UUID,
    scf_id: str,
    override: ScopeOverrideRequest,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """Set an individual inclusion/exclusion, or restore framework inheritance."""
    existing_result = await db.execute(
        select(ScopedControl).where(
            and_(
                ScopedControl.organization_id == org_id,
                ScopedControl.scf_id == scf_id,
            )
        )
    )
    existing = existing_result.scalar_one_or_none()
    old_values = (
        _tracked_values(existing)
        if existing
        else {}
    )
    user_id = UUID(membership.user.db_id)
    try:
        control = await set_individual_scope_override(
            db,
            org_id,
            scf_id,
            override.action,
            override.reason,
            user_id,
            commit=False,
            # The approver of an MCR exclusion is the authenticated admin
            # making it, never an id taken from the request body.
            approver_user_id=user_id if membership.is_admin else None,
        )
    except ValueError as exc:
        status = 404 if "catalog" in str(exc).lower() else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    except PermissionError as exc:
        # An MCR exclusion attempted by a non-admin.
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    await db.flush()
    new_values = _tracked_values(control)
    await log_entity_changes(
        db=db,
        organization_id=org_id,
        entity_type="scoped_control",
        entity_id=control.id,
        action="create" if not old_values else "update",
        changed_by_user_id=user_id,
        old_values=old_values,
        new_values=new_values,
        scf_id=scf_id,
        tracked_fields=SCOPED_CONTROL_TRACKED_FIELDS,
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )
    await db.commit()
    await db.refresh(control)
    return control


@router.put(
    "/organizations/{org_id}/framework-scoping/{framework_id}/basis",
    response_model=FrameworkScopeSummaryItem,
)
async def put_framework_basis(
    org_id: UUID,
    framework_id: str,
    body: FrameworkBasisRequest,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """Record the obligation basis of a scoped framework (PIG R2).

    Admin only. Reclassifying a framework from MCR to DSR changes which
    exclusions need an approver, so it is the same tier as that approval.
    The body replaces all three fields; send nulls to clear them.
    """
    try:
        selection, old_values = await set_selection_basis(
            db,
            org_id,
            framework_id,
            body.basis,
            body.obligation_type,
            body.obligation_source,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await db.flush()
    await log_entity_changes(
        db=db,
        organization_id=org_id,
        entity_type="organization_framework_selection",
        entity_id=selection.id,
        action="update",
        changed_by_user_id=_member_user_id(membership),
        old_values=old_values,
        new_values={f: getattr(selection, f) for f in FRAMEWORK_SELECTION_TRACKED_FIELDS},
        tracked_fields=FRAMEWORK_SELECTION_TRACKED_FIELDS,
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )
    summary = await framework_scope_summary(db, org_id)
    item = next((i for i in summary["frameworks"] if i["id"] == framework_id), None)
    if item is None:
        # Scoped but no longer mapped by any active catalogue control: refuse
        # before committing, so a 404 never leaves a write behind.
        raise HTTPException(status_code=404, detail=f"Framework '{framework_id}' is not in the catalogue")
    await db.commit()
    return item


@router.put(
    "/organizations/{org_id}/scoped-controls/{scf_id}/basis-override",
    response_model=ScopedControlResponse,
)
async def put_control_basis_override(
    org_id: UUID,
    scf_id: str,
    body: ControlBasisOverrideRequest,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """Override one control's derived obligation basis, with a reason (PIG R2).

    Admin only, for the same reason as the framework basis: overriding MCR to
    DSR would otherwise route around the exclusion approval. ``basis: null``
    clears the override and the control derives its basis again.
    """
    existing = (await db.execute(
        select(ScopedControl).where(
            and_(ScopedControl.organization_id == org_id, ScopedControl.scf_id == scf_id)
        )
    )).scalar_one_or_none()
    old_values = _tracked_values(existing) if existing else {}
    user_id = _member_user_id(membership)
    try:
        control = await set_control_basis_override(
            db, org_id, scf_id, body.basis, body.reason, user_id
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.flush()
    await log_entity_changes(
        db=db,
        organization_id=org_id,
        entity_type="scoped_control",
        entity_id=control.id,
        action="update",
        changed_by_user_id=user_id,
        old_values=old_values,
        new_values=_tracked_values(control),
        scf_id=scf_id,
        tracked_fields=SCOPED_CONTROL_TRACKED_FIELDS,
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )
    await db.commit()
    await db.refresh(control)
    return control


@router.put(
    "/organizations/{org_id}/scoped-controls/{scf_id}/target-maturity",
    response_model=ScopedControlResponse,
)
async def put_control_target_maturity(
    org_id: UUID,
    scf_id: str,
    body: ControlTargetMaturityRequest,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """Override one control's inherited target maturity, with a reason (PIG R5).

    Admin only, like the domain targets it departs from: lowering one control's
    target is how a ``controls_at_target`` gate would otherwise be met without
    the control improving. ``level: null`` clears the override and the control
    inherits its domain's target again.
    """
    existing = (await db.execute(
        select(ScopedControl).where(
            and_(ScopedControl.organization_id == org_id, ScopedControl.scf_id == scf_id)
        )
    )).scalar_one_or_none()
    old_values = _tracked_values(existing) if existing else {}
    user_id = _member_user_id(membership)
    try:
        control = await set_control_target_override(
            db, org_id, scf_id, body.level, body.reason, user_id
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.flush()
    await log_entity_changes(
        db=db,
        organization_id=org_id,
        entity_type="scoped_control",
        entity_id=control.id,
        action="update",
        changed_by_user_id=user_id,
        old_values=old_values,
        new_values=_tracked_values(control),
        scf_id=scf_id,
        tracked_fields=SCOPED_CONTROL_TRACKED_FIELDS,
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )
    await db.commit()
    await db.refresh(control)
    return control


async def _audited_control_write(
    db: AsyncSession,
    org_id: UUID,
    scf_id: str,
    request: Request,
    user_id: Optional[UUID],
    write,
) -> ScopedControl:
    """Run one per-control write and audit what it changed.

    ``write`` is a coroutine function taking no arguments and returning the
    control; LookupError reads as 404, ValueError as 400, PermissionError as 403.
    """
    existing = (await db.execute(
        select(ScopedControl).where(
            and_(ScopedControl.organization_id == org_id, ScopedControl.scf_id == scf_id)
        )
    )).scalar_one_or_none()
    old_values = _tracked_values(existing) if existing else {}
    try:
        control = await write()
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.flush()
    await log_entity_changes(
        db=db,
        organization_id=org_id,
        entity_type="scoped_control",
        entity_id=control.id,
        action="update",
        changed_by_user_id=user_id,
        old_values=old_values,
        new_values=_tracked_values(control),
        scf_id=scf_id,
        tracked_fields=SCOPED_CONTROL_TRACKED_FIELDS,
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )
    await db.commit()
    await db.refresh(control)
    return control


@router.put(
    "/organizations/{org_id}/scoped-controls/{scf_id}/materiality",
    response_model=ScopedControlResponse,
)
async def put_control_materiality(
    org_id: UUID,
    scf_id: str,
    body: ControlMaterialityRequest,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """Override whether one control is material, with a reason (PIG R6).

    Admin only, and the admin is recorded as the approver: deciding a control
    is not material is what lets it be excluded or marked not applicable
    without one. ``material: null`` returns the control to its catalogue
    default (material at weighting 10); when that makes the control not
    material, it needs a reason too.
    """
    user_id = _member_user_id(membership)

    async def write():
        if user_id is None:
            raise PermissionError(
                "Changing whether a control is material is recorded against an "
                "approving admin; use an admin's own credentials"
            )
        return await set_material_override(
            db, org_id, scf_id, body.material, body.reason, user_id
        )

    return await _audited_control_write(db, org_id, scf_id, request, user_id, write)


@router.put(
    "/organizations/{org_id}/scoped-controls/{scf_id}/procedure-document",
    response_model=ScopedControlResponse,
)
async def put_control_procedure_document(
    org_id: UUID,
    scf_id: str,
    body: ControlProcedureDocumentRequest,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """Link the approved procedure document for one control (PIG R6, step 2d).

    Editors and above, since control owners hold that role. The document must
    be one of this organisation's procedures and approved when linked.
    ``document_id: null`` unlinks it.
    """
    user_id = _member_user_id(membership)

    async def write():
        return await set_procedure_document(db, org_id, scf_id, body.document_id, user_id)

    return await _audited_control_write(db, org_id, scf_id, request, user_id, write)


def _member_user_id(membership: OrgMembership) -> Optional[UUID]:
    """The acting user's id, or None for a platform key with no user row."""
    db_id = getattr(getattr(membership, "user", None), "db_id", None)
    return UUID(db_id) if db_id else None


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
            "removal_reason": "No longer pursuing ISO 27017 certification",
            "orphan_evidence_action": "keep"
        }

    Tracked evidence whose only in-scope requirers leave scope is reported in
    ``orphaned_evidence``; pass ``orphan_evidence_action="untrack"`` to switch
    it off and close its open tasks in the same transaction.

    Removing a framework that carries an MCR obligation (an active MCR
    selection, or an in-scope control overridden to MCR that it maps) needs an
    admin and a removal reason (PIG R2), the same bar as excluding one control.
    So does removing one that would take a material control out of scope (PIG R6).
    """
    if await unscope_touches_mcr(db, org_id, request.frameworks):
        if not membership.is_admin:
            raise HTTPException(
                status_code=403,
                detail="Removing a framework that carries an MCR obligation needs an organisation admin",
            )
        if not (request.removal_reason or "").strip():
            raise HTTPException(
                status_code=400,
                detail="Removing a framework that carries an MCR obligation needs a removal_reason",
            )
    if await unscope_touches_material(db, org_id, request.frameworks):
        # PIG R6: the same bar for a framework whose removal would take a
        # material control out of scope.
        if not membership.is_admin:
            raise HTTPException(
                status_code=403,
                detail="Removing this framework would take a material control out of scope, which needs an organisation admin",
            )
        if not (request.removal_reason or "").strip():
            raise HTTPException(
                status_code=400,
                detail="Removing this framework would take a material control out of scope, which needs a removal_reason",
            )
    result = await bulk_unscope_frameworks(
        db=db,
        org_id=org_id,
        framework_ids=request.frameworks,
        removal_reason=request.removal_reason,
        orphan_evidence_action=request.orphan_evidence_action,
    )

    return BulkUnscopeFrameworkResponse(
        success=True,
        removed=result.removed,
        protected=result.protected,
        already_out_of_scope=result.already_out_of_scope,
        total=result.total,
        protected_by=result.protected_by,
        frameworks_processed=result.frameworks_processed,
        message=result.message,
        orphaned_evidence=result.orphaned_evidence,
        open_tasks_affected=result.open_tasks_affected,
        orphan_evidence_action=result.orphan_evidence_action,
        untracked_evidence=result.untracked_evidence,
        tasks_closed=result.tasks_closed,
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

    # Clear effective scope, durable individual overrides, and active framework
    # selections in one transaction. Implementation metadata remains untouched.
    await db.execute(
        ScopedControl.__table__.update()
        .where(ScopedControl.organization_id == org_id)
        .values(
            selected=False,
            out_of_scope_justification="Scope reset — all controls removed from scope",
            scope_override=None,
            scope_override_reason=None,
            scope_override_set_at=None,
            scope_override_set_by=None,
            scope_override_approved_by=None,
            updated_by_user_id=UUID(membership.user.db_id),
        )
    )
    await db.execute(
        OrganizationFrameworkSelection.__table__.update()
        .where(
            and_(
                OrganizationFrameworkSelection.organization_id == org_id,
                OrganizationFrameworkSelection.active == True,  # noqa: E712
            )
        )
        .values(active=False)
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

            op_fields = op.model_dump(exclude={'scf_id'}, exclude_unset=True)
            refusal = await _exclusion_refusal(
                db,
                org_id,
                op.scf_id,
                bool(existing.selected) if existing else None,
                _requested_selected(op_fields) if existing else bool(op_fields.get("selected", True)),
                was_status=existing.implementation_status if existing else None,
                will_status=op_fields.get("implementation_status"),
                is_admin=membership.is_admin,
            )
            if refusal:
                failed_count += 1
                errors.append(f"{op.scf_id}: {refusal[1]}")
                continue

            if existing:
                # Capture old values for audit
                old_values = _tracked_values(existing)
                was_status = existing.implementation_status
                was_selected = bool(existing.selected)

                # Apply updates from operation — all provided fields
                update_fields = op.model_dump(exclude={'scf_id'}, exclude_unset=True)
                for field_name, value in update_fields.items():
                    setattr(existing, field_name, value)
                existing.updated_by_user_id = user_id
                _stamp_rescope_and_assessment(existing, old_values, update_fields)

                # Auto-set completion_date on implementation status transitions
                if op.implementation_status is not None:
                    if op.implementation_status == 'implemented' and not existing.completion_date:
                        existing.completion_date = date.today()
                    elif op.implementation_status != 'implemented' and existing.completion_date:
                        existing.completion_date = None

                # Audit log
                new_values = _tracked_values(existing)
                await log_entity_changes(
                    db=db, organization_id=org_id, entity_type='scoped_control',
                    entity_id=existing.id, action='update', changed_by_user_id=user_id,
                    old_values=old_values, new_values=new_values,
                    scf_id=op.scf_id, tracked_fields=SCOPED_CONTROL_TRACKED_FIELDS,
                    action_source=detect_action_source(http_request),
                    request_id=get_request_id(http_request),
                )
                await on_control_status(
                    db, existing, was_status, user_id,
                    detect_action_source(http_request), get_request_id(http_request), was_selected,
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
                new_values = _tracked_values(new_control)
                await log_entity_changes(
                    db=db, organization_id=org_id, entity_type='scoped_control',
                    entity_id=new_control.id, action='create', changed_by_user_id=user_id,
                    old_values={}, new_values=new_values,
                    scf_id=op.scf_id, tracked_fields=SCOPED_CONTROL_TRACKED_FIELDS,
                    action_source=detect_action_source(http_request),
                    request_id=get_request_id(http_request),
                )
                await on_control_status(
                    db, new_control, None, user_id,
                    detect_action_source(http_request), get_request_id(http_request),
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
