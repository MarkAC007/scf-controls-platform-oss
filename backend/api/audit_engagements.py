"""
Audit Engagement Workspaces API — Phase D Foundation.
Handles CRUD for audit engagements and their materialised control scope.
Issue: #370 Audit Module — Scoped Engagement Workspaces
"""
import logging
from dataclasses import dataclass
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Security
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, text
from sqlalchemy.orm import selectinload
from typing import List, Optional
from uuid import UUID

from database import get_db
from models import (
    AuditEngagement,
    AuditEngagementStatus,
    EngagementAuditor,
    EngagementAuditorStatus,
    EngagementControlScope,
    EngagementQuery,
    EngagementQueryStatus,
    Comment,
    EvidenceFile,
    ScopedControl,
    ScopeStatus,
    User as DBUser,
)
from catalog_models import SCFCatalogControl, SCFCatalogEvidence
from schemas import (
    AuditEngagementCreate,
    AuditEngagementUpdate,
    AuditEngagementResponse,
    EngagementScopeItem,
    EngagementAuditorCreate,
    EngagementAuditorResponse,
    EngagementQueryCreate,
    EngagementQueryItem,
    EngagementQueryResponseCreate,
    EngagementQueryStatusUpdate,
    QueryResponseItem,
    FrameworkPresentation,
)
from schemas_catalog_upgrade import CatalogLifecycleBadge
from auth import (
    require_org_role,
    OrgMembership,
    require_auth,
    verify_org_membership,
    security,
    User,
)
from services.audit_service import log_entity_changes, detect_action_source, get_request_id
from services.engagement_presentation import build_framework_presentation
from services.engagement_queries import status_after_response, is_valid_query_transition
from services.notifications import create_engagement_query_raised_notifications


# =============================================================================
# Engagement-scoped access resolution (Increment 3)
#
# Auditors are NEVER granted access via OrganizationMember / consultant paths —
# that would expose every org endpoint. Instead read endpoints use
# require_engagement_read, which admits either a normal org member (viewer+) or
# an external auditor holding an ACTIVE grant to THIS specific engagement. A miss
# returns 404 (not 403) so engagement existence is never leaked across tenants.
# =============================================================================


@dataclass
class EngagementAccess:
    user: User
    organization_id: UUID
    engagement_id: UUID
    is_auditor: bool  # True = external auditor (read-only, confined to this engagement)
    role: str         # "auditor" or the member's org role


async def resolve_engagement_access(
    user: User,
    org_id: UUID,
    engagement_id: UUID,
    db: AsyncSession,
) -> EngagementAccess:
    """Resolve a caller's access to a single engagement.

    Order: confirm the engagement exists under the org, then try org membership,
    then an active auditor grant. Every failure path raises an identical 404 so a
    caller cannot distinguish "no such engagement" from "not authorised".
    """
    eng_result = await db.execute(
        select(AuditEngagement.id).where(
            and_(
                AuditEngagement.organization_id == org_id,
                AuditEngagement.id == engagement_id,
            )
        )
    )
    if eng_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Audit engagement not found")

    # Path 1: normal org member / consultant / api-key (viewer or higher).
    try:
        membership = await verify_org_membership(org_id, user, db, "viewer")
        return EngagementAccess(
            user=user, organization_id=org_id, engagement_id=engagement_id,
            is_auditor=False, role=membership.role,
        )
    except HTTPException:
        pass  # fall through to the auditor path

    # Path 2: external auditor holding an active grant to THIS engagement.
    if user.db_id:
        grant_result = await db.execute(
            select(EngagementAuditor.id).where(
                and_(
                    EngagementAuditor.engagement_id == engagement_id,
                    EngagementAuditor.user_id == UUID(user.db_id),
                    EngagementAuditor.status == EngagementAuditorStatus.ACTIVE.value,
                )
            )
        )
        if grant_result.scalar_one_or_none() is not None:
            return EngagementAccess(
                user=user, organization_id=org_id, engagement_id=engagement_id,
                is_auditor=True, role="auditor",
            )

    raise HTTPException(status_code=404, detail="Audit engagement not found")


def require_engagement_read():
    """Dependency: read access to an engagement (org member OR active auditor)."""
    async def dependency(
        org_id: UUID,
        engagement_id: UUID,
        credentials: HTTPAuthorizationCredentials = Security(security),
        db: AsyncSession = Depends(get_db),
    ) -> EngagementAccess:
        user = await require_auth(credentials, db)
        return await resolve_engagement_access(user, org_id, engagement_id, db)

    return dependency

logger = logging.getLogger(__name__)

router = APIRouter(tags=["audit_engagements"])

ENGAGEMENT_TRACKED_FIELDS = ['name', 'frameworks', 'status', 'start_date', 'end_date']


class AuditEngagementVersionedResponse(AuditEngagementResponse):
    """Engagement response + the catalog version it was assessed under.

    Plan §4.4 consumer 6: engagement views always resolve (including
    deprecated catalog rows — frozen scope renders forever) and expose the
    engagement's own catalog_version so "assessed under SCF {v}" can render.
    """
    catalog_version: Optional[str] = None


class EngagementScopeItemBadged(EngagementScopeItem, CatalogLifecycleBadge):
    """Scope item + catalog lifecycle badge — the frozen scope keeps
    rendering after a control is retired, marked as deprecated."""


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
    Snapshot the *complete* framework-mapped control set for an engagement,
    tagging each control with the organisation's scope decision.

    For every SCF control that maps to any requested framework (via the crosswalk
    ``framework_mappings`` JSONB), insert one EngagementControlScope row tagged:
        - in_scope     : a ScopedControl exists with selected=True
        - excluded     : a ScopedControl exists with selected=False (justification frozen)
        - not_tracked  : the org has no ScopedControl row for this control

    ``source_frameworks`` records which of the engagement's frameworks pulled the
    control in (the intersection of the engagement frameworks and the control's
    own mappings). Returns the number of scope rows inserted.
    """
    if not frameworks:
        return 0

    # Build JSONB containment conditions to find catalog controls in any requested framework
    framework_conditions = " OR ".join(
        f"framework_mappings ? :fw_{i}" for i in range(len(frameworks))
    )
    params = {f"fw_{i}": fw for i, fw in enumerate(frameworks)}

    # Fetch scf_id + framework_mappings so we can attribute source_frameworks per control.
    catalog_query = text(f"""
        SELECT scf_id, framework_mappings
        FROM scf_catalog_controls
        WHERE {framework_conditions}
    """)
    catalog_result = await db.execute(catalog_query, params)
    catalog_rows = catalog_result.fetchall()

    if not catalog_rows:
        logger.info(
            "No catalog controls found for frameworks=%s engagement=%s",
            frameworks, engagement_id
        )
        return 0

    fw_set = set(frameworks)
    # scf_id -> the engagement frameworks this control actually maps to
    source_frameworks_by_scf = {
        scf_id: sorted(fw_set & set((mappings or {}).keys()))
        for scf_id, mappings in catalog_rows
    }
    framework_scf_ids = set(source_frameworks_by_scf.keys())

    # Fetch ALL of the org's scoped controls in the mapped set (selected or not).
    scoped_result = await db.execute(
        select(
            ScopedControl.id,
            ScopedControl.scf_id,
            ScopedControl.selected,
            ScopedControl.out_of_scope_justification,
        ).where(
            and_(
                ScopedControl.organization_id == org_id,
                ScopedControl.scf_id.in_(framework_scf_ids),
            )
        )
    )
    scoped_by_scf = {row[1]: row for row in scoped_result.fetchall()}

    scope_rows = []
    for scf_id in sorted(framework_scf_ids):
        scoped = scoped_by_scf.get(scf_id)
        if scoped is None:
            scope_status = ScopeStatus.NOT_TRACKED.value
            scoped_control_id = None
            justification = None
        elif scoped[2]:  # selected=True
            scope_status = ScopeStatus.IN_SCOPE.value
            scoped_control_id = scoped[0]
            justification = None
        else:  # tracked but selected=False
            scope_status = ScopeStatus.EXCLUDED.value
            scoped_control_id = scoped[0]
            justification = scoped[3]

        scope_rows.append(
            EngagementControlScope(
                engagement_id=engagement_id,
                scf_id=scf_id,
                scoped_control_id=scoped_control_id,
                scope_status=scope_status,
                out_of_scope_justification=justification,
                source_frameworks=source_frameworks_by_scf[scf_id],
            )
        )

    db.add_all(scope_rows)
    logger.info(
        "Materialised engagement=%s scope: %d controls (frameworks=%s)",
        engagement_id, len(scope_rows), frameworks
    )
    return len(scope_rows)


# =============================================================================
# List engagements
# =============================================================================

@router.get(
    "/organizations/{org_id}/engagements",
    response_model=List[AuditEngagementVersionedResponse],
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
        resp = AuditEngagementVersionedResponse.model_validate(eng)
        resp.scope_count = scope_count
        responses.append(resp)

    return responses


# =============================================================================
# Create engagement
# =============================================================================

@router.post(
    "/organizations/{org_id}/engagements",
    response_model=AuditEngagementVersionedResponse,
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

    resp = AuditEngagementVersionedResponse.model_validate(new_engagement)
    resp.scope_count = scope_count
    return resp


# =============================================================================
# Get single engagement
# =============================================================================

@router.get(
    "/organizations/{org_id}/engagements/{engagement_id}",
    response_model=AuditEngagementVersionedResponse,
)
async def get_engagement(
    org_id: UUID,
    engagement_id: UUID,
    access: EngagementAccess = Depends(require_engagement_read()),
    db: AsyncSession = Depends(get_db),
):
    """
    Get a single audit engagement by ID.
    Includes the materialised scope_count.
    Readable by org members (viewer+) and the engagement's granted auditors.
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

    resp = AuditEngagementVersionedResponse.model_validate(engagement)
    resp.scope_count = scope_count
    return resp


# =============================================================================
# Update engagement
# =============================================================================

@router.patch(
    "/organizations/{org_id}/engagements/{engagement_id}",
    response_model=AuditEngagementVersionedResponse,
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

    resp = AuditEngagementVersionedResponse.model_validate(engagement)
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
    response_model=List[EngagementScopeItemBadged],
)
async def get_engagement_scope(
    org_id: UUID,
    engagement_id: UUID,
    access: EngagementAccess = Depends(require_engagement_read()),
    db: AsyncSession = Depends(get_db),
):
    """
    Get the materialised control scope for an audit engagement.
    Returns each scoped control with its scf_id and control_name from the catalog.
    Readable by org members (viewer+) and the engagement's granted auditors.
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

    # Fetch scope items keyed by scf_id (scoped_control_id may be NULL for
    # not_tracked controls), left-joining the catalog for the control name.
    scope_result = await db.execute(
        select(
            EngagementControlScope.id,
            EngagementControlScope.scoped_control_id,
            EngagementControlScope.added_at,
            EngagementControlScope.scf_id,
            EngagementControlScope.scope_status,
            EngagementControlScope.out_of_scope_justification,
            EngagementControlScope.source_frameworks,
            SCFCatalogControl.control_name,
            SCFCatalogControl.status,
            SCFCatalogControl.retired_in_version,
            SCFCatalogControl.superseded_by,
        )
        .outerjoin(SCFCatalogControl, EngagementControlScope.scf_id == SCFCatalogControl.scf_id)
        .where(EngagementControlScope.engagement_id == engagement_id)
        .order_by(EngagementControlScope.scf_id)
    )
    rows = scope_result.fetchall()

    return [
        EngagementScopeItemBadged(
            id=row[0],
            scoped_control_id=row[1],
            added_at=row[2],
            scf_id=row[3],
            scope_status=row[4],
            out_of_scope_justification=row[5],
            source_frameworks=row[6] or [],
            control_name=row[7],
            catalog_status=row[8],
            retired_in_version=row[9],
            superseded_by=row[10],
        )
        for row in rows
    ]


# =============================================================================
# Framework-native presentation (Increment 2)
# =============================================================================

@router.get(
    "/organizations/{org_id}/engagements/{engagement_id}/presentation",
    response_model=FrameworkPresentation,
)
async def get_engagement_presentation(
    org_id: UUID,
    engagement_id: UUID,
    framework: str = Query(..., description="Framework to present from (must be one of the engagement's frameworks)"),
    access: EngagementAccess = Depends(require_engagement_read()),
    db: AsyncSession = Depends(get_db),
):
    """
    Present an engagement's controls from a single framework's perspective:
    SCF controls re-sequenced under that framework's own clause / Annex A ids
    (derived from the catalog framework_mappings), each with its scope status,
    live implementation status and evidence (flagged in/out of the audit window).
    Readable by org members (viewer+) and the engagement's granted auditors.
    """
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

    if framework not in (engagement.frameworks or []):
        raise HTTPException(
            status_code=400,
            detail=f"Framework '{framework}' is not in scope for this engagement.",
        )

    # Frozen scope joined to the catalog (name + mappings) and the live ScopedControl.
    scope_result = await db.execute(
        select(
            EngagementControlScope.scf_id,
            EngagementControlScope.scope_status,
            EngagementControlScope.out_of_scope_justification,
            EngagementControlScope.source_frameworks,
            EngagementControlScope.scoped_control_id,
            SCFCatalogControl.control_name,
            SCFCatalogControl.framework_mappings,
            ScopedControl.implementation_status,
            ScopedControl.maturity_level,
            ScopedControl.owner,
        )
        .outerjoin(SCFCatalogControl, EngagementControlScope.scf_id == SCFCatalogControl.scf_id)
        .outerjoin(ScopedControl, EngagementControlScope.scoped_control_id == ScopedControl.id)
        .where(EngagementControlScope.engagement_id == engagement_id)
    )
    rows = scope_result.fetchall()

    scope_rows = []
    mappings_by_scf = {}
    live_by_scf = {}
    target_scf_ids = set()
    for r in rows:
        scf_id = r[0]
        target_scf_ids.add(scf_id)
        scope_rows.append({
            "scf_id": scf_id,
            "scope_status": r[1],
            "out_of_scope_justification": r[2],
            "source_frameworks": r[3] or [],
            "scoped_control_id": r[4],
            "control_name": r[5],
        })
        mappings_by_scf[scf_id] = r[6] or {}
        if r[4] is not None:  # tracked control -> live fields available
            live_by_scf[scf_id] = {
                "implementation_status": r[7],
                "maturity_level": r[8],
                "owner": r[9],
            }

    evidence_by_scf = await _fetch_evidence_by_scf(db, org_id, target_scf_ids)
    queries_by_scf = await _fetch_queries_by_scf(db, engagement_id)

    tree = build_framework_presentation(
        framework=framework,
        scope_rows=scope_rows,
        mappings_by_scf=mappings_by_scf,
        live_by_scf=live_by_scf,
        evidence_by_scf=evidence_by_scf,
        window=(engagement.start_date, engagement.end_date),
        queries_by_scf=queries_by_scf,
    )
    tree["start_date"] = engagement.start_date
    tree["end_date"] = engagement.end_date
    return tree


async def _fetch_evidence_by_scf(db: AsyncSession, org_id: UUID, scf_ids: set) -> dict:
    """Map each requested scf_id to its live, non-deleted evidence artifacts.

    Linkage: EvidenceFile.evidence_id -> SCFCatalogEvidence.control_mappings
    (a flat JSONB array of scf_ids). One artifact mapped to several controls
    appears under each. Returns {scf_id: [artifact dicts]}.
    """
    if not scf_ids:
        return {}

    # Reverse the catalog mapping: scf_id -> {evidence_ids} (272 ERL rows, cheap to scan).
    catalog_result = await db.execute(
        select(SCFCatalogEvidence.evidence_id, SCFCatalogEvidence.control_mappings)
    )
    scf_to_evidence_ids: dict = {}
    for evidence_id, mappings in catalog_result.fetchall():
        for scf in (mappings or []):
            if scf in scf_ids:
                scf_to_evidence_ids.setdefault(scf, set()).add(evidence_id)

    all_evidence_ids = {eid for eids in scf_to_evidence_ids.values() for eid in eids}
    if not all_evidence_ids:
        return {}

    file_result = await db.execute(
        select(
            EvidenceFile.id,
            EvidenceFile.evidence_id,
            EvidenceFile.filename,
            EvidenceFile.uploaded_at,
            EvidenceFile.review_status,
        ).where(
            and_(
                EvidenceFile.organization_id == org_id,
                EvidenceFile.evidence_id.in_(all_evidence_ids),
                EvidenceFile.is_deleted == False,  # noqa: E712
            )
        )
    )
    files_by_evidence_id: dict = {}
    for fid, evidence_id, filename, uploaded_at, review_status in file_result.fetchall():
        files_by_evidence_id.setdefault(evidence_id, []).append({
            "id": fid,
            "filename": filename,
            "uploaded_at": uploaded_at,
            "review_status": review_status,
        })

    evidence_by_scf: dict = {}
    for scf, evidence_ids in scf_to_evidence_ids.items():
        artifacts = []
        for eid in evidence_ids:
            artifacts.extend(files_by_evidence_id.get(eid, []))
        if artifacts:
            evidence_by_scf[scf] = artifacts
    return evidence_by_scf


async def _fetch_queries_by_scf(db: AsyncSession, engagement_id: UUID) -> dict:
    """Map each control (scf_id) to a lightweight summary of its queries, for the
    presentation view. {scf_id: [{id, title, status}]}."""
    result = await db.execute(
        select(EngagementQuery.scf_id, EngagementQuery.id, EngagementQuery.title, EngagementQuery.status)
        .where(EngagementQuery.engagement_id == engagement_id)
        .order_by(EngagementQuery.created_at.desc())
    )
    queries_by_scf: dict = {}
    for scf_id, qid, title, status in result.fetchall():
        queries_by_scf.setdefault(scf_id, []).append({
            "id": str(qid),
            "title": title,
            "status": status,
        })
    return queries_by_scf


# =============================================================================
# Auditor access grants (Increment 3)
# =============================================================================

async def _load_engagement_for_org(db: AsyncSession, org_id: UUID, engagement_id: UUID) -> AuditEngagement:
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
    return engagement


def _auditor_response(grant: EngagementAuditor, email: Optional[str]) -> EngagementAuditorResponse:
    return EngagementAuditorResponse(
        id=grant.id,
        engagement_id=grant.engagement_id,
        user_id=grant.user_id,
        email=email,
        status=grant.status,
        invited_at=grant.invited_at,
        accepted_at=grant.accepted_at,
        revoked_at=grant.revoked_at,
    )


@router.get(
    "/organizations/{org_id}/engagements/{engagement_id}/auditors",
    response_model=List[EngagementAuditorResponse],
)
async def list_engagement_auditors(
    org_id: UUID,
    engagement_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """List the auditors granted access to an engagement. Requires: viewer+."""
    await _load_engagement_for_org(db, org_id, engagement_id)

    result = await db.execute(
        select(EngagementAuditor, DBUser.email)
        .outerjoin(DBUser, EngagementAuditor.user_id == DBUser.id)
        .where(EngagementAuditor.engagement_id == engagement_id)
        .order_by(EngagementAuditor.invited_at)
    )
    return [_auditor_response(grant, email) for grant, email in result.fetchall()]


@router.post(
    "/organizations/{org_id}/engagements/{engagement_id}/auditors",
    response_model=EngagementAuditorResponse,
    status_code=201,
)
async def grant_engagement_auditor(
    org_id: UUID,
    engagement_id: UUID,
    payload: EngagementAuditorCreate,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """Grant an existing user read access to this engagement. Requires: admin.

    Re-granting a previously revoked auditor reactivates the existing grant.
    """
    await _load_engagement_for_org(db, org_id, engagement_id)

    user_result = await db.execute(
        select(DBUser.id, DBUser.email).where(DBUser.id == payload.user_id)
    )
    user_row = user_result.first()
    if user_row is None:
        raise HTTPException(status_code=404, detail="User not found")

    granting_user = membership.user
    granter_id = UUID(granting_user.db_id) if granting_user and granting_user.db_id else None
    now = datetime.utcnow()

    existing_result = await db.execute(
        select(EngagementAuditor).where(
            and_(
                EngagementAuditor.engagement_id == engagement_id,
                EngagementAuditor.user_id == payload.user_id,
            )
        )
    )
    grant = existing_result.scalar_one_or_none()
    if grant is not None:
        grant.status = EngagementAuditorStatus.ACTIVE.value
        grant.revoked_at = None
        grant.accepted_at = grant.accepted_at or now
    else:
        grant = EngagementAuditor(
            engagement_id=engagement_id,
            user_id=payload.user_id,
            status=EngagementAuditorStatus.ACTIVE.value,
            invited_by_user_id=granter_id,
            accepted_at=now,
        )
        db.add(grant)

    await db.commit()
    await db.refresh(grant)
    logger.info("Auditor granted: engagement=%s user=%s by=%s", engagement_id, payload.user_id, granter_id)
    return _auditor_response(grant, user_row[1])


@router.delete(
    "/organizations/{org_id}/engagements/{engagement_id}/auditors/{auditor_id}",
    status_code=204,
)
async def revoke_engagement_auditor(
    org_id: UUID,
    engagement_id: UUID,
    auditor_id: UUID,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """Revoke an auditor's access to an engagement. Requires: admin."""
    result = await db.execute(
        select(EngagementAuditor)
        .join(AuditEngagement, EngagementAuditor.engagement_id == AuditEngagement.id)
        .where(
            and_(
                EngagementAuditor.id == auditor_id,
                EngagementAuditor.engagement_id == engagement_id,
                AuditEngagement.organization_id == org_id,
            )
        )
    )
    grant = result.scalar_one_or_none()
    if not grant:
        raise HTTPException(status_code=404, detail="Auditor grant not found")

    grant.status = EngagementAuditorStatus.REVOKED.value
    grant.revoked_at = datetime.utcnow()
    await db.commit()
    logger.info("Auditor revoked: engagement=%s grant=%s", engagement_id, auditor_id)


@router.get("/my-engagements", response_model=List[AuditEngagementVersionedResponse])
async def list_my_auditor_engagements(
    credentials: HTTPAuthorizationCredentials = Security(security),
    db: AsyncSession = Depends(get_db),
):
    """Engagements the authenticated user can access as an external auditor.

    The auditor's entry point: it is org-agnostic and returns only engagements
    for which the user holds an ACTIVE grant.
    """
    user = await require_auth(credentials, db)
    if not user.db_id:
        return []

    result = await db.execute(
        select(AuditEngagement)
        .join(EngagementAuditor, EngagementAuditor.engagement_id == AuditEngagement.id)
        .where(
            and_(
                EngagementAuditor.user_id == UUID(user.db_id),
                EngagementAuditor.status == EngagementAuditorStatus.ACTIVE.value,
            )
        )
        .order_by(AuditEngagement.created_at.desc())
    )
    return [AuditEngagementVersionedResponse.model_validate(eng) for eng in result.scalars().all()]


# =============================================================================
# Structured queries (Increment 4)
#
# Auditor raises a query on a control (open) -> owner responds (answered) ->
# auditor closes (closed). Readable/writable by anyone with engagement access
# (member or granted auditor); responses reuse the polymorphic Comment model.
# =============================================================================

def _access_user_id(access: "EngagementAccess") -> Optional[UUID]:
    return UUID(access.user.db_id) if access.user and access.user.db_id else None


async def _load_query_for_engagement(db: AsyncSession, engagement_id: UUID, query_id: UUID) -> EngagementQuery:
    result = await db.execute(
        select(EngagementQuery).where(
            and_(
                EngagementQuery.id == query_id,
                EngagementQuery.engagement_id == engagement_id,
            )
        )
    )
    query = result.scalar_one_or_none()
    if not query:
        raise HTTPException(status_code=404, detail="Query not found")
    return query


async def _response_counts(db: AsyncSession, query_ids: List[UUID]) -> dict:
    if not query_ids:
        return {}
    result = await db.execute(
        select(Comment.commentable_id, func.count())
        .where(
            and_(
                Comment.commentable_type == EngagementQuery.COMMENTABLE_TYPE,
                Comment.commentable_id.in_(query_ids),
                Comment.is_deleted == False,  # noqa: E712
            )
        )
        .group_by(Comment.commentable_id)
    )
    return {row[0]: row[1] for row in result.fetchall()}


def _query_item(query: EngagementQuery, email: Optional[str], response_count: int, responses=None) -> EngagementQueryItem:
    return EngagementQueryItem(
        id=query.id,
        engagement_id=query.engagement_id,
        scf_id=query.scf_id,
        raised_by_user_id=query.raised_by_user_id,
        raised_by_email=email,
        title=query.title,
        body=query.body,
        status=query.status,
        created_at=query.created_at,
        updated_at=query.updated_at,
        closed_at=query.closed_at,
        response_count=response_count,
        responses=responses,
    )


@router.get(
    "/organizations/{org_id}/engagements/{engagement_id}/queries",
    response_model=List[EngagementQueryItem],
)
async def list_engagement_queries(
    org_id: UUID,
    engagement_id: UUID,
    scf_id: Optional[str] = Query(None, description="Filter to a single control"),
    status: Optional[str] = Query(None, description="Filter by status"),
    access: EngagementAccess = Depends(require_engagement_read()),
    db: AsyncSession = Depends(get_db),
):
    """List queries for an engagement (member or granted auditor)."""
    conditions = [EngagementQuery.engagement_id == engagement_id]
    if scf_id:
        conditions.append(EngagementQuery.scf_id == scf_id)
    if status:
        conditions.append(EngagementQuery.status == status)

    result = await db.execute(
        select(EngagementQuery, DBUser.email)
        .outerjoin(DBUser, EngagementQuery.raised_by_user_id == DBUser.id)
        .where(and_(*conditions))
        .order_by(EngagementQuery.created_at.desc())
    )
    rows = result.fetchall()
    counts = await _response_counts(db, [q.id for q, _ in rows])
    return [_query_item(q, email, counts.get(q.id, 0)) for q, email in rows]


@router.post(
    "/organizations/{org_id}/engagements/{engagement_id}/queries",
    response_model=EngagementQueryItem,
    status_code=201,
)
async def create_engagement_query(
    org_id: UUID,
    engagement_id: UUID,
    payload: EngagementQueryCreate,
    access: EngagementAccess = Depends(require_engagement_read()),
    db: AsyncSession = Depends(get_db),
):
    """Raise a query on a control (member or granted auditor)."""
    query = EngagementQuery(
        engagement_id=engagement_id,
        scf_id=payload.scf_id,
        raised_by_user_id=_access_user_id(access),
        title=payload.title,
        body=payload.body,
        status=EngagementQueryStatus.OPEN.value,
    )
    db.add(query)
    await db.commit()
    await db.refresh(query)
    logger.info("Query raised: engagement=%s scf=%s by=%s", engagement_id, payload.scf_id, _access_user_id(access))

    await create_engagement_query_raised_notifications(
        db,
        organization_id=org_id,
        query_id=query.id,
        scf_id=payload.scf_id,
        raised_by_user_id=_access_user_id(access),
    )

    return _query_item(query, access.user.email if access.user else None, 0, responses=[])


@router.get(
    "/organizations/{org_id}/engagements/{engagement_id}/queries/{query_id}",
    response_model=EngagementQueryItem,
)
async def get_engagement_query(
    org_id: UUID,
    engagement_id: UUID,
    query_id: UUID,
    access: EngagementAccess = Depends(require_engagement_read()),
    db: AsyncSession = Depends(get_db),
):
    """Get a single query with its full response thread."""
    query = await _load_query_for_engagement(db, engagement_id, query_id)

    raiser_email = None
    if query.raised_by_user_id:
        email_result = await db.execute(select(DBUser.email).where(DBUser.id == query.raised_by_user_id))
        raiser_email = email_result.scalar_one_or_none()

    resp_result = await db.execute(
        select(Comment, DBUser.email)
        .outerjoin(DBUser, Comment.user_id == DBUser.id)
        .where(
            and_(
                Comment.commentable_type == EngagementQuery.COMMENTABLE_TYPE,
                Comment.commentable_id == query_id,
                Comment.is_deleted == False,  # noqa: E712
            )
        )
        .order_by(Comment.created_at)
    )
    resp_rows = resp_result.fetchall()
    responses = [
        QueryResponseItem(id=c.id, user_id=c.user_id, email=email, content=c.content, created_at=c.created_at)
        for c, email in resp_rows
    ]
    return _query_item(query, raiser_email, len(responses), responses=responses)


@router.post(
    "/organizations/{org_id}/engagements/{engagement_id}/queries/{query_id}/responses",
    response_model=EngagementQueryItem,
    status_code=201,
)
async def respond_to_engagement_query(
    org_id: UUID,
    engagement_id: UUID,
    query_id: UUID,
    payload: EngagementQueryResponseCreate,
    access: EngagementAccess = Depends(require_engagement_read()),
    db: AsyncSession = Depends(get_db),
):
    """Post a response to a query (reuses Comment); advances open -> answered."""
    query = await _load_query_for_engagement(db, engagement_id, query_id)
    user_id = _access_user_id(access)
    if user_id is None:
        raise HTTPException(status_code=403, detail="A user account is required to respond.")

    comment = Comment(
        commentable_type=EngagementQuery.COMMENTABLE_TYPE,
        commentable_id=query_id,
        user_id=user_id,
        content=payload.content,
    )
    db.add(comment)

    new_status = status_after_response(query.status)
    if new_status != query.status:
        query.status = new_status

    await db.commit()
    await db.refresh(query)

    counts = await _response_counts(db, [query_id])
    return _query_item(query, access.user.email if access.user else None, counts.get(query_id, 0))


@router.patch(
    "/organizations/{org_id}/engagements/{engagement_id}/queries/{query_id}",
    response_model=EngagementQueryItem,
)
async def update_engagement_query_status(
    org_id: UUID,
    engagement_id: UUID,
    query_id: UUID,
    payload: EngagementQueryStatusUpdate,
    access: EngagementAccess = Depends(require_engagement_read()),
    db: AsyncSession = Depends(get_db),
):
    """Change a query's status (e.g. close, or reopen). Enforces the lifecycle."""
    query = await _load_query_for_engagement(db, engagement_id, query_id)

    if payload.status not in EngagementQueryStatus.values():
        raise HTTPException(status_code=400, detail=f"Unknown status '{payload.status}'.")
    if not is_valid_query_transition(query.status, payload.status):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot move query from '{query.status}' to '{payload.status}'.",
        )

    query.status = payload.status
    query.closed_at = datetime.utcnow() if payload.status == EngagementQueryStatus.CLOSED.value else None
    await db.commit()
    await db.refresh(query)

    counts = await _response_counts(db, [query_id])
    return _query_item(query, access.user.email if access.user else None, counts.get(query_id, 0))
