"""Platform-scoped audit trail for actions with no organisation.

Contract §3d. The existing `audit_log` table requires a non-null
`organization_id`; platform-level credential changes have none, so they get
their own table. It carries NO value columns by construction, which is what
makes it structurally incapable of leaking a credential.
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import PlatformAuditLog

logger = logging.getLogger(__name__)


async def record_platform_event(
    db: AsyncSession,
    entity_type: str,
    entity_id: str,
    action: str,
    actor: str,
    actor_user_id: Optional[UUID] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    action_source: Optional[str] = None,
    request_id: Optional[UUID] = None,
) -> PlatformAuditLog:
    """Add a platform audit row to the session. The caller commits.

    Leaving the commit to the caller keeps the audit row in the same
    transaction as the change it records, matching `audit_service`.
    """
    entry = PlatformAuditLog(
        id=uuid.uuid4(),
        entity_type=entity_type[:50],
        entity_id=entity_id[:200],
        action=action[:40],
        actor=actor[:200],
        actor_user_id=actor_user_id,
        ip_address=ip_address[:45] if ip_address else None,
        user_agent=user_agent,
        action_source=action_source[:20] if action_source else None,
        request_id=request_id,
    )
    db.add(entry)
    return entry


async def recent_events(
    db: AsyncSession,
    entity_type: Optional[str] = None,
    limit: int = 50,
) -> list[PlatformAuditLog]:
    """Most recent platform audit rows, newest first."""
    stmt = select(PlatformAuditLog).order_by(PlatformAuditLog.created_at.desc())
    if entity_type:
        stmt = stmt.where(PlatformAuditLog.entity_type == entity_type)
    result = await db.execute(stmt.limit(max(1, min(limit, 500))))
    return list(result.scalars().all())
