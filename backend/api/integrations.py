"""Platform-admin API for the five operator-settable integration credentials.

Contract §3e. Every endpoint requires platform admin. No response body ever
carries a stored value — not the success shapes, not the error shapes. The
write endpoints exist so an operator can turn on email, AI generation, single
sign-on and vendor research without editing a file on the host and restarting
the stack.

Evidence object storage is **not** one of them. It is configured per
organisation under ``/api/organizations/{org_id}/evidence-storage``, because
the table behind this one is keyed by credential name alone and is global to
the process, so it cannot express one store per organisation.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from auth import User, require_platform_admin
from database import get_db
from services import crypto
from services import integration_secrets
from services.audit_service import (
    detect_action_source,
    get_client_ip,
    get_request_id,
    get_user_agent,
)
from services.platform_audit import recent_events

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/integrations", tags=["platform-admin"])

MASTER_API_KEY_ACTOR = "api_key:master"


class IntegrationValueRequest(BaseModel):
    value: str = Field(..., description="The credential value. Write-only; never returned.")


def _actor(request: Request, user: User) -> integration_secrets.Actor:
    """Build the audit actor.

    Master-API-key requests have no user row, so they are labelled rather than
    attributed — otherwise an automated write would look like it came from
    whichever human happened to be a platform admin.
    """
    if getattr(user, "auth_method", None) == "api_key":
        label = MASTER_API_KEY_ACTOR
        user_id: Optional[UUID] = None
    else:
        label = user.email or "unknown"
        user_id = UUID(user.db_id) if getattr(user, "db_id", None) else None

    return integration_secrets.Actor(
        label=label,
        user_id=user_id,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )


@router.get("")
async def list_integrations(
    request: Request,
    user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Every settable credential, its source, and whether it is configured."""
    return {
        "encryption_key_configured": integration_secrets.encryption_key_configured(),
        "legacy_plaintext_rows": await integration_secrets.legacy_plaintext_rows(db),
        "items": await integration_secrets.list_items(db),
    }


@router.get("/health")
async def integrations_health(
    request: Request,
    user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Setup view: what is on, what is off, and how it is being managed."""
    return await integration_secrets.health(db)


@router.get("/audit")
async def integrations_audit(
    request: Request,
    limit: int = 50,
    user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Recent credential changes. Records who and when, never what."""
    events = await recent_events(
        db, entity_type=integration_secrets.ENTITY_TYPE, limit=limit
    )
    return {
        "items": [
            {
                "action": e.action,
                "entity_id": e.entity_id,
                "actor": e.actor,
                "created_at": e.created_at.isoformat() if e.created_at else None,
                "ip_address": e.ip_address,
                "action_source": e.action_source,
            }
            for e in events
        ]
    }


@router.put("/{name}")
async def set_integration(
    name: str,
    body: IntegrationValueRequest,
    request: Request,
    user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Store a credential, encrypted. Refused when the operator manages it."""
    try:
        return await integration_secrets.set_value(db, name, body.value, _actor(request, user))
    except integration_secrets.UnknownIntegration:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown integration: {name}",
        )
    except integration_secrets.EmptyValue:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Value must not be empty. Use DELETE to clear a credential.",
        )
    except integration_secrets.OperatorManaged as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": (
                    f"{name} is managed by the operator ({exc.args[0]}) and cannot be "
                    f"changed from the application."
                ),
                "managed_by_operator": True,
                "source": exc.args[0],
            },
        )
    except (crypto.SecretKeyMissing, crypto.SecretKeyInvalid):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "SCF_SECRET_KEY is not configured — see docs",
                "encryption_key_configured": False,
            },
        )


@router.delete("/{name}")
async def clear_integration(
    name: str,
    request: Request,
    user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Remove a stored credential. File and environment values are untouched."""
    try:
        return await integration_secrets.clear_value(db, name, _actor(request, user))
    except integration_secrets.UnknownIntegration:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown integration: {name}",
        )
