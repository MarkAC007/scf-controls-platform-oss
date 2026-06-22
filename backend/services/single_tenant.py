"""
Single-tenant safety guard for OSS self-hosted deployments.

Evaluates at startup whether the static master API key may be granted
admin access. Enabled only when OSS_SINGLE_TENANT is explicitly set to
a truthy value AND the database contains at most one organisation with
at most one human member (i.e. genuine single-tenant setup).

FAIL-CLOSED: any evaluation error leaves _single_tenant_active=False.
"""
import logging
import os
from typing import Optional

from sqlalchemy import select, func

from database import AsyncSessionLocal
from models import User, Organization, OrganizationMember
from services.service_account import SERVICE_ACCOUNT_EMAIL

logger = logging.getLogger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}

# Module-level cache — written once by evaluate_single_tenant() at startup.
_single_tenant_active: bool = False
_single_tenant_org_id: Optional[str] = None


def single_tenant_flag_set() -> bool:
    """Return True iff OSS_SINGLE_TENANT is set to an explicit truthy value.

    Treats "0", "false", "no", "", and unset as False.
    CRITICAL: does NOT use bool(os.getenv(...)) which would treat "0" as True.
    """
    return os.getenv("OSS_SINGLE_TENANT", "").strip().lower() in _TRUTHY


def is_single_tenant_active() -> bool:
    """Return True iff single-tenant mode was successfully activated at startup."""
    return _single_tenant_active


def single_tenant_org_id() -> Optional[str]:
    """Return the pinned organisation id, or None if still in pre-setup bootstrap."""
    return _single_tenant_org_id


async def evaluate_single_tenant() -> None:
    """Resolve ONCE at startup whether single-tenant master-key admin is safe.

    Sets _single_tenant_active and _single_tenant_org_id. MUST be called AFTER
    seed_service_account() so the service-account user exists and can be
    excluded from the human-member count.

    Fail-closed contract: any exception leaves _single_tenant_active=False.
    """
    global _single_tenant_active, _single_tenant_org_id

    # Reset to safe defaults before every evaluation.
    _single_tenant_active = False
    _single_tenant_org_id = None

    if not single_tenant_flag_set():
        # Flag off — stay disabled, no logging noise.
        return

    try:
        async with AsyncSessionLocal() as session:
            org_count = await session.scalar(
                select(func.count()).select_from(Organization)
            )
            human_members = await session.scalar(
                select(func.count())
                .select_from(OrganizationMember)
                .join(User, User.id == OrganizationMember.user_id)
                .where(User.email != SERVICE_ACCOUNT_EMAIL)
            )
            org_id = await session.scalar(select(Organization.id).limit(1))

        if (org_count or 0) > 1:
            logger.critical(
                "OSS_SINGLE_TENANT set but %s organizations exist — "
                "single-tenant DISABLED; master-key admin refused.",
                org_count,
            )
            return

        if (human_members or 0) > 1:
            logger.critical(
                "OSS_SINGLE_TENANT set but %s human members exist — "
                "single-tenant DISABLED; master-key admin refused.",
                human_members,
            )
            return

        _single_tenant_active = True
        _single_tenant_org_id = str(org_id) if org_id else None
        logger.warning(
            "OSS_SINGLE_TENANT active — static master key granted admin, "
            "pinned to org %s (None=pre-setup bootstrap).",
            _single_tenant_org_id,
        )

    except Exception as e:
        _single_tenant_active = False
        _single_tenant_org_id = None
        logger.critical(
            "single-tenant evaluation failed — failing closed "
            "(master-key admin DENIED): %s",
            e,
            exc_info=True,
        )
