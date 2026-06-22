"""
Audit Engagement Workspaces API — Phase D Foundation.
Handles CRUD for audit engagements and their materialised control scope.
Issue: #370 Audit Module — Scoped Engagement Workspaces
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, text
from sqlalchemy.orm import selectinload
from typing import List, Optional
from uuid import UUID

from database import get_db
from models import AuditEngagement, AuditEngagementStatus, EngagementControlScope, ScopedControl
from catalog_models import SCFCatalogControl
from schemas import (
    AuditEngagementCreate,
    AuditEngagementUpdate,
    AuditEngagementResponse,
    EngagementScopeItem,
)
from auth import require_org_role, OrgMembership
from services.audit_service import log_entity_changes, detect_action_source, get_request_id

logger = logging.getLogger(__name__)

router = APIRouter(tags=["audit_engagements"])

ENGAGEMENT_TRACKED_FIELDS = ['name', 'frameworks', 'status', 'start_date', 'end_date']


# =============================================================================
# Helper: materialise scope
# =============================================================================

async def _materialise_scope(
    db: AsyncSession,
    engagement_id: UUID,
    org_id: UUID,
    frameworks: List[str],
) -> int:
    """
    Query selected scoped controls for the org filtered by the requested frameworks,
    then bulk-insert EngagementControlScope rows.

    Uses the same JSONB containment pattern as scoped_controls.py:
    framework_mappings is a JSONB dict whose keys are framework IDs.

    Returns the number of scope rows inserted.
    """
    if not frameworks:
        return 0

    # Build JSONB containment conditions to find catalog controls in any requested framework
    framework_conditions = " OR ".join(
        f"framework_mappings ? :fw_{i}" for i in range(len(frameworks))
    )
    params = {f"fw_{i}": fw for i, fw in enumerate(frameworks)}

    # Get scf_ids that belong to at least one of the requested frameworks
    catalog_query = text(f"""
        SELECT scf_id
        FROM scf_catalog_controls
        WHERE {framework_conditions}
    """)
    catalog_result = await db.execute(catalog_query, params)
    framework_scf_ids = {row[0] for row in catalog_result.fetchall()}

    if not framework_scf_ids:
        logger.info(
            "No catalog controls found for frameworks=%s engagement=%s",
            frameworks, engagement_id
        )
        return 0

    # Get scoped controls for this org that are selected=True and in the framework set
    scoped_result = await db.execute(
        select(ScopedControl.id, ScopedControl.scf_id).where(
            and_(
                ScopedControl.organization_id == org_id,
                ScopedControl.selected == True,  # noqa: E712
                ScopedControl.scf_id.in_(framework_scf_ids),
            )
        )
    )
    matching_controls = scoped_result.fetchall()

    if not matching_controls:
        logger.info(
            "No selected scoped controls matched frameworks=%s for org=%s",
            frameworks, org_id
        )
        return 0

    # Bulk-insert EngagementControlScope rows
    scope_rows = [
        EngagementControlScope(
            engagement_id=engagement_id,
            scoped_control_id=row[0],
        )
        for row in matching_controls
    ]
    db.add_all(scope_rows)
    return len(scope_rows)


# =============================================================================
# List engagements
# =============================================================================

@router.get(
    "/organizations/{org_id}/engagements",
    response_model=List[AuditEngagementResponse],
)
async def list_engagements(
    org_id: UUID,
    status: Optional[str] = Query(None, description="Filter by engagement status"),
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """
    List all audit engagements for an organisation.
    Optionally filter by status.
    Requires: viewer role or higher.
    """
    query = select(AuditEngagement).where(AuditEngagement.organization_id == org_id)

    if status:
        query = query.where(AuditEngagement.status == status)

    query = query.order_by(AuditEngagement.created_at.desc())
    result = await db.execute(query)
    engagements = result.scalars().all()

    # Annotate scope_count for each engagement
    responses = []
    for eng in engagements:
        count_result = await db.execute(
            select(func.count()).select_from(EngagementControlScope).where(
                EngagementControlScope.engagement_id == eng.id
            )
        )
        scope_count = count_result.scalar_one()
        resp = AuditEngagementResponse.model_validate(eng)
        resp.scope_count = scope_count
        responses.append(resp)

    return responses


# =============================================================================
# Create engagement
# =============================================================================

@router.post(
    "/organizations/{org_id}/engagements",
    response_model=AuditEngagementResponse,
    status_code=201,
)
async def create_engagement(
    org_id: UUID,
    engagement_data: AuditEngagementCreate,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """
    Create a new audit engagement and materialise its control scope.
    On creation, queries all selected scoped controls for this org that are
    mapped to the requested frameworks, and snapshots them as EngagementControlScope rows.
    Requires: editor role or higher.
    """
    current_user = membership.user
    user_id = UUID(current_user.db_id) if current_user and current_user.db_id else None

    new_engagement = AuditEngagement(
        organization_id=org_id,
        created_by_user_id=user_id,
        name=engagement_data.name,
        frameworks=engagement_data.frameworks,
        status=AuditEngagementStatus.DRAFT.value,
        start_date=engagement_data.start_date,
        end_date=engagement_data.end_date,
    )
    db.add(new_engagement)
    await db.flush()  # get the ID before materialising scope

    # Auto-materialise scope from selected scoped controls filtered by frameworks
    scope_count = await _materialise_scope(
        db=db,
        engagement_id=new_engagement.id,
        org_id=org_id,
        frameworks=engagement_data.frameworks,
    )
    logger.info(
        "Engagement created id=%s name=%s org=%s scope_count=%d",
        new_engagement.id, new_engagement.name, org_id, scope_count
    )

    # Audit trail — only when a db user_id is available (not for platform API key calls)
    if user_id is not None:
        new_values = {f: getattr(new_engagement, f) for f in ENGAGEMENT_TRACKED_FIELDS if hasattr(new_engagement, f)}
        await log_entity_changes(
            db=db, organization_id=org_id, entity_type='audit_engagement',
            entity_id=new_engagement.id, action='create', changed_by_user_id=user_id,
            old_values={}, new_values=new_values,
            tracked_fields=ENGAGEMENT_TRACKED_FIELDS,
            action_source=detect_action_source(request),
            request_id=get_request_id(request),
        )

    await db.commit()
    await db.refresh(new_engagement)

    resp = AuditEngagementResponse.model_validate(new_engagement)
    resp.scope_count = scope_count
    return resp


# =============================================================================
# Get single engagement
# =============================================================================

@router.get(
    "/organizations/{org_id}/engagements/{engagement_id}",
    response_model=AuditEngagementResponse,
)
async def get_engagement(
    org_id: UUID,
    engagement_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """
    Get a single audit engagement by ID.
    Includes the materialised scope_count.
    Requires: viewer role or higher.
    """
    result = await db.execute(
        select(AuditEngagement).where(
            and_(
                AuditEngagement.organization_id == org_id,
                AuditEngagement.id == engagement_id,
            )
        )
    )
    engagement = result.scalar_one_or_none()

    if not engagement:
        raise HTTPException(status_code=404, detail="Audit engagement not found")

    count_result = await db.execute(
        select(func.count()).select_from(EngagementControlScope).where(
            EngagementControlScope.engagement_id == engagement_id
        )
    )
    scope_count = count_result.scalar_one()

    resp = AuditEngagementResponse.model_validate(engagement)
    resp.scope_count = scope_count
    return resp


# =============================================================================
# Update engagement
# =============================================================================

@router.patch(
    "/organizations/{org_id}/engagements/{engagement_id}",
    response_model=AuditEngagementResponse,
)
async def update_engagement(
    org_id: UUID,
    engagement_id: UUID,
    engagement_update: AuditEngagementUpdate,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """
    Partially update an audit engagement.
    Only provided fields are updated. Status transitions are caller-controlled.
    Requires: editor role or higher.
    """
    current_user = membership.user
    user_id = UUID(current_user.db_id) if current_user and current_user.db_id else None

    result = await db.execute(
        select(AuditEngagement).where(
            and_(
                AuditEngagement.organization_id == org_id,
                AuditEngagement.id == engagement_id,
            )
        )
    )
    engagement = result.scalar_one_or_none()

    if not engagement:
        raise HTTPException(status_code=404, detail="Audit engagement not found")

    old_values = {f: getattr(engagement, f) for f in ENGAGEMENT_TRACKED_FIELDS if hasattr(engagement, f)}

    update_data = engagement_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(engagement, key, value)

    if user_id is not None:
        new_values = {f: getattr(engagement, f) for f in ENGAGEMENT_TRACKED_FIELDS if hasattr(engagement, f)}
        await log_entity_changes(
            db=db, organization_id=org_id, entity_type='audit_engagement',
            entity_id=engagement.id, action='update', changed_by_user_id=user_id,
            old_values=old_values, new_values=new_values,
            tracked_fields=ENGAGEMENT_TRACKED_FIELDS,
            action_source=detect_action_source(request),
            request_id=get_request_id(request),
        )

    await db.commit()
    await db.refresh(engagement)

    count_result = await db.execute(
        select(func.count()).select_from(EngagementControlScope).where(
            EngagementControlScope.engagement_id == engagement_id
        )
    )
    scope_count = count_result.scalar_one()

    resp = AuditEngagementResponse.model_validate(engagement)
    resp.scope_count = scope_count
    return resp


# =============================================================================
# Delete engagement
# =============================================================================

@router.delete(
    "/organizations/{org_id}/engagements/{engagement_id}",
    status_code=204,
)
async def delete_engagement(
    org_id: UUID,
    engagement_id: UUID,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """
    Delete an audit engagement. Only DRAFT engagements can be deleted.
    Non-draft engagements return 409 Conflict.
    Requires: admin role.
    """
    current_user = membership.user
    user_id = UUID(current_user.db_id) if current_user and current_user.db_id else None

    result = await db.execute(
        select(AuditEngagement).where(
            and_(
                AuditEngagement.organization_id == org_id,
                AuditEngagement.id == engagement_id,
            )
        )
    )
    engagement = result.scalar_one_or_none()

    if not engagement:
        raise HTTPException(status_code=404, detail="Audit engagement not found")

    if engagement.status != AuditEngagementStatus.DRAFT.value:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot delete engagement with status '{engagement.status}'. Only DRAFT engagements can be deleted."
        )

    if user_id is not None:
        old_values = {f: getattr(engagement, f) for f in ENGAGEMENT_TRACKED_FIELDS if hasattr(engagement, f)}
        await log_entity_changes(
            db=db, organization_id=org_id, entity_type='audit_engagement',
            entity_id=engagement.id, action='delete', changed_by_user_id=user_id,
            old_values=old_values, new_values={},
            tracked_fields=ENGAGEMENT_TRACKED_FIELDS,
            action_source=detect_action_source(request),
            request_id=get_request_id(request),
        )

    await db.delete(engagement)
    await db.commit()


# =============================================================================
# Get materialised scope
# =============================================================================

@router.get(
    "/organizations/{org_id}/engagements/{engagement_id}/scope",
    response_model=List[EngagementScopeItem],
)
async def get_engagement_scope(
    org_id: UUID,
    engagement_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """
    Get the materialised control scope for an audit engagement.
    Returns each scoped control with its scf_id and control_name from the catalog.
    Requires: viewer role or higher.
    """
    # Verify engagement belongs to this org
    eng_result = await db.execute(
        select(AuditEngagement).where(
            and_(
                AuditEngagement.organization_id == org_id,
                AuditEngagement.id == engagement_id,
            )
        )
    )
    engagement = eng_result.scalar_one_or_none()

    if not engagement:
        raise HTTPException(status_code=404, detail="Audit engagement not found")

    # Fetch scope items with catalog join for scf_id and control_name
    scope_result = await db.execute(
        select(
            EngagementControlScope.id,
            EngagementControlScope.scoped_control_id,
            EngagementControlScope.added_at,
            ScopedControl.scf_id,
            SCFCatalogControl.control_name,
        )
        .join(ScopedControl, EngagementControlScope.scoped_control_id == ScopedControl.id)
        .outerjoin(SCFCatalogControl, ScopedControl.scf_id == SCFCatalogControl.scf_id)
        .where(EngagementControlScope.engagement_id == engagement_id)
        .order_by(ScopedControl.scf_id)
    )
    rows = scope_result.fetchall()

    return [
        EngagementScopeItem(
            id=row[0],
            scoped_control_id=row[1],
            added_at=row[2],
            scf_id=row[3],
            control_name=row[4],
        )
        for row in rows
    ]
