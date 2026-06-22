"""
Audit Log API endpoints.
Read-only endpoints for querying the immutable audit trail.
"""
import logging
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, or_
from datetime import datetime
from typing import Optional
from uuid import UUID

from database import get_db
from models import AuditLog, User
from schemas import AuditLogResponse, AuditLogListResponse
from auth import require_org_role, OrgMembership

logger = logging.getLogger(__name__)

router = APIRouter(tags=["audit_log"])


@router.get(
    "/organizations/{org_id}/audit-log",
    response_model=AuditLogListResponse
)
async def list_audit_log(
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
    entity_type: Optional[str] = Query(None, description="Filter by entity type"),
    entity_id: Optional[UUID] = Query(None, description="Filter by entity ID"),
    scf_id: Optional[str] = Query(None, description="Filter by SCF control ID"),
    action: Optional[str] = Query(None, description="Filter by action (create/update/delete)"),
    changed_by_user_id: Optional[UUID] = Query(None, description="Filter by user who made change"),
    action_source: Optional[str] = Query(None, description="Filter by action source (ui/api_key/mcp/system)"),
    request_id: Optional[UUID] = Query(None, description="Filter by request correlation ID"),
    date_from: Optional[datetime] = Query(None, description="Filter from this date (inclusive)"),
    date_to: Optional[datetime] = Query(None, description="Filter to this date (inclusive)"),
    actor_id: Optional[UUID] = Query(None, description="Filter by actor user ID"),
    search_text: Optional[str] = Query(None, description="Search entity_type, field_name, old_value, new_value"),
    limit: int = Query(50, ge=1, le=200, description="Max entries to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
):
    """
    Query the audit log for an organization.
    Requires: viewer role or higher.
    Returns chronological change history with optional filters.
    """
    # Build query with filters
    query = select(AuditLog).where(AuditLog.organization_id == org_id)
    count_query = select(func.count(AuditLog.id)).where(AuditLog.organization_id == org_id)

    if entity_type:
        query = query.where(AuditLog.entity_type == entity_type)
        count_query = count_query.where(AuditLog.entity_type == entity_type)
    if entity_id:
        query = query.where(AuditLog.entity_id == entity_id)
        count_query = count_query.where(AuditLog.entity_id == entity_id)
    if scf_id:
        query = query.where(AuditLog.scf_id == scf_id)
        count_query = count_query.where(AuditLog.scf_id == scf_id)
    if action:
        query = query.where(AuditLog.action == action)
        count_query = count_query.where(AuditLog.action == action)
    if changed_by_user_id:
        query = query.where(AuditLog.changed_by_user_id == changed_by_user_id)
        count_query = count_query.where(AuditLog.changed_by_user_id == changed_by_user_id)
    if action_source:
        query = query.where(AuditLog.action_source == action_source)
        count_query = count_query.where(AuditLog.action_source == action_source)
    if request_id:
        query = query.where(AuditLog.request_id == request_id)
        count_query = count_query.where(AuditLog.request_id == request_id)
    if date_from:
        query = query.where(AuditLog.changed_at >= date_from)
        count_query = count_query.where(AuditLog.changed_at >= date_from)
    if date_to:
        query = query.where(AuditLog.changed_at <= date_to)
        count_query = count_query.where(AuditLog.changed_at <= date_to)
    if actor_id:
        query = query.where(AuditLog.changed_by_user_id == actor_id)
        count_query = count_query.where(AuditLog.changed_by_user_id == actor_id)
    if search_text:
        search_pat = f"%{search_text}%"
        text_filter = or_(
            AuditLog.entity_type.ilike(search_pat),
            AuditLog.field_name.ilike(search_pat),
            AuditLog.old_value.ilike(search_pat),
            AuditLog.new_value.ilike(search_pat),
        )
        query = query.where(text_filter)
        count_query = count_query.where(text_filter)

    # Get total count
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Get entries ordered by most recent first
    query = query.order_by(AuditLog.changed_at.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    entries = result.scalars().all()

    # Enrich with user emails
    user_ids = {e.changed_by_user_id for e in entries}
    user_emails = {}
    if user_ids:
        user_result = await db.execute(
            select(User.id, User.email).where(User.id.in_(user_ids))
        )
        user_emails = {row[0]: row[1] for row in user_result.fetchall()}

    response_entries = []
    for entry in entries:
        entry_dict = AuditLogResponse.model_validate(entry)
        entry_dict.changed_by_email = user_emails.get(entry.changed_by_user_id)
        response_entries.append(entry_dict)

    return AuditLogListResponse(
        entries=response_entries,
        total=total,
        offset=offset,
        limit=limit,
    )
