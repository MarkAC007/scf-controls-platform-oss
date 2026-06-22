"""CDM tenancy gating + hard caps.

Slice 7. Provides three layers of admission control on top of the CDM
endpoints:

1. **Per-tenant feature flag** via ``Organization.settings.cdm_enabled``.
   Falls back to the global env ``ENABLE_CDM``. Tenant explicit value
   (true or false) always wins over env.

2. **Quota caps** (documents, tokens, outstanding proposed mappings) so
   one tenant cannot blow the LightRAG storage / review-queue budget.

3. **Structured 4xx** — every cap raises ``HTTPException`` whose body has
   ``{"detail": <msg>, "cap": <name>}`` so the UI can distinguish which
   limit fired and surface a targeted message.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional
from uuid import UUID

from fastapi import Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import CDMDocument, CDMMapping, Organization


logger = logging.getLogger(__name__)


# ───────────────────────── Feature flag ─────────────────────────


def _env_cdm_enabled() -> bool:
    return os.getenv("ENABLE_CDM", "false").lower() == "true"


async def get_tenant_cdm_enabled(db: AsyncSession, org_id: UUID) -> bool:
    """Resolve the effective CDM-enabled state for one org.

    Per-tenant explicit value (true OR false) always wins over env.
    Missing per-tenant value → fall back to global env.
    """
    result = await db.execute(
        select(Organization.settings).where(Organization.id == org_id)
    )
    settings = result.scalar_one_or_none()

    if isinstance(settings, dict) and "cdm_enabled" in settings:
        tenant_value = settings.get("cdm_enabled")
        if isinstance(tenant_value, bool):
            return tenant_value

    return _env_cdm_enabled()


async def require_tenant_cdm_enabled(
    org_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    """FastAPI dependency — 404 when CDM is not enabled for this tenant."""
    if not await get_tenant_cdm_enabled(db, org_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="CDM module not enabled",
        )


# ───────────────────────── Cap config ─────────────────────────


def _get_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
        return value if value >= 0 else default
    except ValueError:
        return default


def get_cdm_documents_cap() -> int:
    return _get_int_env("CDM_CAP_DOCUMENTS", 250)


def get_cdm_tokens_cap() -> int:
    return _get_int_env("CDM_CAP_TOKENS", 50_000_000)


def get_cdm_proposed_mappings_cap() -> int:
    return _get_int_env("CDM_CAP_PROPOSED_MAPPINGS", 10_000)


# ───────────────────────── Cap checks ─────────────────────────


def _cap_exceeded(detail: str, cap_name: str) -> HTTPException:
    """Construct a 409 with the structured cap-body shape."""
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"detail": detail, "cap": cap_name},
    )


async def assert_cdm_document_count_cap(db: AsyncSession, org_id: UUID) -> None:
    """Raise 409 if the org already holds the document-count cap."""
    cap = get_cdm_documents_cap()
    try:
        result = await db.execute(
            select(func.count(CDMDocument.id)).where(
                CDMDocument.organization_id == org_id,
            )
        )
        current = result.scalar() or 0
    except Exception:
        logger.exception(
            "CDM document-cap check failed for org %s — proceeding fail-open", org_id
        )
        return

    if current >= cap:
        raise _cap_exceeded(
            f"CDM document cap reached ({current}/{cap})",
            "documents",
        )


async def assert_cdm_token_count_cap(
    db: AsyncSession, org_id: UUID, incoming_words: int
) -> None:
    """Raise 409 if adding ``incoming_words`` would breach the token cap.

    ``incoming_words`` is the upload payload's word_count (sliced 2 stamps
    this on CDMDocument). Tokens ≈ words (D-3); the cap is a soft budget,
    not a billing-grade meter.
    """
    cap = get_cdm_tokens_cap()
    try:
        result = await db.execute(
            select(func.coalesce(func.sum(CDMDocument.word_count), 0)).where(
                CDMDocument.organization_id == org_id,
            )
        )
        current_total = int(result.scalar() or 0)
    except Exception:
        logger.exception(
            "CDM token-cap check failed for org %s — proceeding fail-open", org_id
        )
        return

    projected_total = current_total + max(incoming_words, 0)
    if projected_total > cap:
        raise _cap_exceeded(
            f"CDM token cap would be exceeded ({projected_total}/{cap})",
            "tokens",
        )


async def assert_cdm_proposed_mappings_cap(
    db: AsyncSession, org_id: UUID
) -> None:
    """Raise 409 if the org already has the maximum proposed mappings outstanding."""
    cap = get_cdm_proposed_mappings_cap()
    try:
        result = await db.execute(
            select(func.count(CDMMapping.id)).where(
                CDMMapping.organization_id == org_id,
                CDMMapping.status == "proposed",
            )
        )
        current = result.scalar() or 0
    except Exception:
        logger.exception(
            "CDM proposed-mappings-cap check failed for org %s — proceeding fail-open",
            org_id,
        )
        return

    if current >= cap:
        raise _cap_exceeded(
            f"CDM proposed-mappings cap reached ({current}/{cap})",
            "proposed_mappings",
        )
