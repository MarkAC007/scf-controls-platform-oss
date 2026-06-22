"""
User Scope Preferences API - persistent per-user, per-org framework filter preferences.
Issue #362: Audit Scope Filters
"""
import logging
from fastapi import APIRouter, Depends, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from typing import List
from uuid import UUID

from database import get_db
from models import UserScopePreferences
from auth import require_org_role, OrgMembership

logger = logging.getLogger(__name__)

router = APIRouter(tags=["scope_preferences"])


@router.get(
    "/organizations/{org_id}/scope-preferences",
    response_model=dict,
)
async def get_scope_preferences(
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """
    Get the current user's scope preferences for this organization.
    Returns empty preferences if none set yet.
    Requires: viewer role or higher.
    """
    user_id_str = membership.user.db_id if membership.user else None
    if not user_id_str:
        return {"active_frameworks": [], "audit_mode_locked": False, "audit_label": None}

    user_id = UUID(user_id_str)

    result = await db.execute(
        select(UserScopePreferences).where(
            UserScopePreferences.user_id == user_id,
            UserScopePreferences.org_id == org_id,
        )
    )
    prefs = result.scalar_one_or_none()

    if not prefs:
        return {"active_frameworks": [], "audit_mode_locked": False, "audit_label": None}

    return {
        "active_frameworks": prefs.active_frameworks or [],
        "audit_mode_locked": prefs.audit_mode_locked,
        "audit_label": prefs.audit_label,
    }


@router.put(
    "/organizations/{org_id}/scope-preferences",
    response_model=dict,
)
async def upsert_scope_preferences(
    org_id: UUID,
    body: dict = Body(...),
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """
    Upsert the current user's framework scope preferences for this organization.
    Body: {"active_frameworks": ["ISO 27001", "SOC 2"]}
    Requires: viewer role or higher.
    """
    user_id_str = membership.user.db_id if membership.user else None
    if not user_id_str:
        return {"active_frameworks": [], "audit_mode_locked": False, "audit_label": None}

    user_id = UUID(user_id_str)
    active_frameworks = body.get("active_frameworks", [])
    if not isinstance(active_frameworks, list):
        active_frameworks = []

    stmt = (
        pg_insert(UserScopePreferences)
        .values(user_id=user_id, org_id=org_id, active_frameworks=active_frameworks)
        .on_conflict_do_update(
            constraint='uq_user_scope_preferences',
            set_={'active_frameworks': active_frameworks},
        )
    )
    await db.execute(stmt)
    await db.commit()

    logger.info("Scope preferences updated for user=%s org=%s frameworks=%s", user_id, org_id, active_frameworks)
    return {"active_frameworks": active_frameworks, "audit_mode_locked": False, "audit_label": None}
