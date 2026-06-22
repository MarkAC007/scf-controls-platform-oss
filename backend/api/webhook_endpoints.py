"""
Webhook endpoint management API (Issue #214 — Evidence Inbox).

Allows org admins to create, list, revoke, and rotate webhook endpoints
that external systems use to push evidence into the platform.

Endpoints:
  POST   /organizations/{org_id}/webhook-endpoints                              — Create endpoint
  GET    /organizations/{org_id}/webhook-endpoints                              — List endpoints
  GET    /organizations/{org_id}/webhook-endpoints/{endpoint_id}                — Get endpoint
  DELETE /organizations/{org_id}/webhook-endpoints/{endpoint_id}                — Revoke endpoint
  POST   /organizations/{org_id}/webhook-endpoints/{endpoint_id}/rotate-secret  — Rotate secret
  GET    /organizations/{org_id}/webhook-endpoints/{endpoint_id}/deliveries     — List deliveries
"""
import secrets
import logging
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, Query, status
from sqlalchemy import select, and_, func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_org_role, OrgMembership
from database import get_db
from models import WebhookEndpoint, WebhookDelivery
from schemas import (
    WebhookEndpointCreate,
    WebhookEndpointResponse,
    WebhookEndpointCreatedResponse,
    WebhookDeliveryResponse,
    WebhookDeliveryListResponse,
)
from services.audit_service import create_audit_entry, get_request_id, detect_action_source
from rate_limiting import rate_limit_write, rate_limit_read

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhook-endpoints"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _generate_webhook_secret() -> str:
    """Generate a webhook secret with ``whsec_`` prefix + 48 hex chars."""
    return "whsec_" + secrets.token_hex(24)


# ---------------------------------------------------------------------------
# CRUD endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/organizations/{org_id}/webhook-endpoints",
    response_model=WebhookEndpointCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create webhook endpoint",
    description="Create a new webhook endpoint for an organisation. Returns the plaintext secret once -- store it securely.",
)
@rate_limit_write
async def create_webhook_endpoint(
    request: Request,
    response: Response,
    org_id: UUID,
    body: WebhookEndpointCreate,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """Create a new webhook endpoint. Returns the plaintext secret once."""
    plaintext_secret = _generate_webhook_secret()

    endpoint = WebhookEndpoint(
        organization_id=org_id,
        name=body.name,
        description=body.description,
        secret=plaintext_secret,
        secret_prefix=plaintext_secret[:12],
        allowed_evidence_ids=body.allowed_evidence_ids,
        rate_limit_per_minute=body.rate_limit_per_minute,
        created_by_user_id=UUID(membership.user.db_id),
    )
    db.add(endpoint)
    await db.flush()

    await create_audit_entry(
        db=db,
        organization_id=org_id,
        entity_type="webhook_endpoint",
        entity_id=endpoint.id,
        action="create",
        changed_by_user_id=UUID(membership.user.db_id),
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )

    await db.commit()
    await db.refresh(endpoint)

    logger.info(
        "Webhook endpoint created: id=%s, name=%s, org=%s, by=%s",
        endpoint.id, endpoint.name, org_id, membership.user.email,
    )

    return WebhookEndpointCreatedResponse(
        id=endpoint.id,
        organization_id=endpoint.organization_id,
        name=endpoint.name,
        description=endpoint.description,
        secret_prefix=endpoint.secret_prefix,
        is_active=endpoint.is_active,
        allowed_evidence_ids=endpoint.allowed_evidence_ids,
        created_by_user_id=endpoint.created_by_user_id,
        last_delivery_at=endpoint.last_delivery_at,
        delivery_count=endpoint.delivery_count,
        rate_limit_per_minute=endpoint.rate_limit_per_minute,
        created_at=endpoint.created_at,
        updated_at=endpoint.updated_at,
        plaintext_secret=plaintext_secret,
    )


@router.get(
    "/organizations/{org_id}/webhook-endpoints",
    response_model=List[WebhookEndpointResponse],
    summary="List webhook endpoints",
    description="List all webhook endpoints for an organisation, ordered by creation date (newest first).",
)
@rate_limit_read
async def list_webhook_endpoints(
    request: Request,
    response: Response,
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """List all webhook endpoints for an organisation."""
    result = await db.execute(
        select(WebhookEndpoint)
        .where(WebhookEndpoint.organization_id == org_id)
        .order_by(WebhookEndpoint.created_at.desc())
    )
    endpoints = result.scalars().all()
    return [WebhookEndpointResponse.model_validate(ep) for ep in endpoints]


@router.get(
    "/organizations/{org_id}/webhook-endpoints/{endpoint_id}",
    response_model=WebhookEndpointResponse,
    summary="Get webhook endpoint details",
    description="Retrieve a single webhook endpoint by ID. Includes delivery stats and configuration.",
)
@rate_limit_read
async def get_webhook_endpoint(
    request: Request,
    response: Response,
    org_id: UUID,
    endpoint_id: UUID,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """Get a single webhook endpoint by ID."""
    result = await db.execute(
        select(WebhookEndpoint).where(
            and_(
                WebhookEndpoint.id == endpoint_id,
                WebhookEndpoint.organization_id == org_id,
            )
        )
    )
    endpoint = result.scalar_one_or_none()
    if not endpoint:
        raise HTTPException(status_code=404, detail="Webhook endpoint not found")
    return WebhookEndpointResponse.model_validate(endpoint)


@router.delete(
    "/organizations/{org_id}/webhook-endpoints/{endpoint_id}",
    response_model=dict,
    summary="Revoke a webhook endpoint",
    description="Revoke (soft-delete) a webhook endpoint. Sets `is_active=false`; future deliveries will be rejected.",
)
@rate_limit_write
async def revoke_webhook_endpoint(
    request: Request,
    response: Response,
    org_id: UUID,
    endpoint_id: UUID,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """Revoke a webhook endpoint (sets is_active=false)."""
    result = await db.execute(
        select(WebhookEndpoint).where(
            and_(
                WebhookEndpoint.id == endpoint_id,
                WebhookEndpoint.organization_id == org_id,
            )
        )
    )
    endpoint = result.scalar_one_or_none()
    if not endpoint:
        raise HTTPException(status_code=404, detail="Webhook endpoint not found")

    if not endpoint.is_active:
        raise HTTPException(status_code=400, detail="Webhook endpoint is already revoked")

    endpoint.is_active = False

    await create_audit_entry(
        db=db,
        organization_id=org_id,
        entity_type="webhook_endpoint",
        entity_id=endpoint.id,
        action="delete",
        changed_by_user_id=UUID(membership.user.db_id),
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )

    await db.commit()

    logger.info(
        "Webhook endpoint revoked: id=%s, org=%s, by=%s",
        endpoint_id, org_id, membership.user.email,
    )
    return {"message": "Webhook endpoint revoked successfully"}


@router.post(
    "/organizations/{org_id}/webhook-endpoints/{endpoint_id}/rotate-secret",
    response_model=WebhookEndpointCreatedResponse,
    summary="Rotate webhook signing secret",
    description="Generate a new HMAC signing secret for the endpoint. The old secret is immediately invalidated. Returns the new plaintext secret once.",
)
@rate_limit_write
async def rotate_webhook_secret(
    request: Request,
    response: Response,
    org_id: UUID,
    endpoint_id: UUID,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """Rotate the webhook secret. Returns the new plaintext secret once."""
    result = await db.execute(
        select(WebhookEndpoint).where(
            and_(
                WebhookEndpoint.id == endpoint_id,
                WebhookEndpoint.organization_id == org_id,
            )
        )
    )
    endpoint = result.scalar_one_or_none()
    if not endpoint:
        raise HTTPException(status_code=404, detail="Webhook endpoint not found")

    if not endpoint.is_active:
        raise HTTPException(status_code=400, detail="Cannot rotate secret for a revoked endpoint")

    new_secret = _generate_webhook_secret()
    endpoint.secret = new_secret
    endpoint.secret_prefix = new_secret[:12]

    await create_audit_entry(
        db=db,
        organization_id=org_id,
        entity_type="webhook_endpoint",
        entity_id=endpoint.id,
        action="update",
        changed_by_user_id=UUID(membership.user.db_id),
        field_name="secret",
        old_value=None,  # never log secrets
        new_value="[rotated]",
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )

    await db.commit()
    await db.refresh(endpoint)

    logger.info(
        "Webhook secret rotated: id=%s, org=%s, by=%s",
        endpoint_id, org_id, membership.user.email,
    )

    return WebhookEndpointCreatedResponse(
        id=endpoint.id,
        organization_id=endpoint.organization_id,
        name=endpoint.name,
        description=endpoint.description,
        secret_prefix=endpoint.secret_prefix,
        is_active=endpoint.is_active,
        allowed_evidence_ids=endpoint.allowed_evidence_ids,
        created_by_user_id=endpoint.created_by_user_id,
        last_delivery_at=endpoint.last_delivery_at,
        delivery_count=endpoint.delivery_count,
        rate_limit_per_minute=endpoint.rate_limit_per_minute,
        created_at=endpoint.created_at,
        updated_at=endpoint.updated_at,
        plaintext_secret=new_secret,
    )


@router.get(
    "/organizations/{org_id}/webhook-endpoints/{endpoint_id}/deliveries",
    response_model=WebhookDeliveryListResponse,
    summary="List delivery logs",
    description="Paginated list of delivery logs for a webhook endpoint, ordered newest first. Includes signature status and processing outcome.",
)
@rate_limit_read
async def list_deliveries(
    request: Request,
    response: Response,
    org_id: UUID,
    endpoint_id: UUID,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """List delivery logs for a webhook endpoint (paginated, newest first)."""
    # Verify endpoint exists and belongs to org
    ep_result = await db.execute(
        select(WebhookEndpoint.id).where(
            and_(
                WebhookEndpoint.id == endpoint_id,
                WebhookEndpoint.organization_id == org_id,
            )
        )
    )
    if not ep_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Webhook endpoint not found")

    # Count total
    count_result = await db.execute(
        select(sa_func.count()).select_from(WebhookDelivery).where(
            WebhookDelivery.webhook_endpoint_id == endpoint_id
        )
    )
    total = count_result.scalar()

    # Fetch page
    result = await db.execute(
        select(WebhookDelivery)
        .where(WebhookDelivery.webhook_endpoint_id == endpoint_id)
        .order_by(WebhookDelivery.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    deliveries = result.scalars().all()

    return WebhookDeliveryListResponse(
        deliveries=[WebhookDeliveryResponse.model_validate(d) for d in deliveries],
        total=total,
    )
