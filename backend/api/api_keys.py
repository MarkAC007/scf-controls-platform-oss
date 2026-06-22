"""
API Key management endpoints.

Allows organisation members to create, list, and revoke per-organisation
API keys for programmatic access.  Keys are scoped to a single org and
inherit the creating user's role at creation time.
"""
import hashlib
import secrets
import logging
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_db
from auth import require_org_role, OrgMembership
from models import ApiKey, User as DBUser
from schemas import ApiKeyCreate, ApiKeyResponse, ApiKeyCreatedResponse
from services.audit_service import log_entity_changes, detect_action_source, get_request_id, API_KEY_TRACKED_FIELDS

logger = logging.getLogger(__name__)

router = APIRouter(tags=["api-keys"])


def _generate_key() -> str:
    """Generate an API key with ``scf_`` prefix + 36 random hex chars."""
    return "scf_" + secrets.token_hex(18)


@router.post(
    "/organizations/{org_id}/api-keys",
    response_model=ApiKeyCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_api_key(
    org_id: UUID,
    body: ApiKeyCreate,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """
    Create a new API key for the current user, scoped to this organisation.

    The plaintext key is returned **once** in the response and is never stored.
    Requires: org admin role.
    """
    plaintext = _generate_key()
    prefix = plaintext[:8]
    key_hash = hashlib.sha256(plaintext.encode()).hexdigest()

    # Freeze role from the creating user's current membership role
    role = membership.role

    api_key = ApiKey(
        user_id=UUID(membership.user.db_id),
        organization_id=org_id,
        name=body.name,
        key_prefix=prefix,
        key_hash=key_hash,
        role=role,
        expires_at=body.expires_at,
    )
    db.add(api_key)
    await db.flush()

    # Audit log - ONLY safe metadata fields, NEVER key_hash or plaintext
    new_values = {f: getattr(api_key, f) for f in API_KEY_TRACKED_FIELDS if hasattr(api_key, f)}
    await log_entity_changes(
        db=db,
        organization_id=org_id,
        entity_type='api_key',
        entity_id=api_key.id,
        action='create',
        changed_by_user_id=UUID(membership.user.db_id) if membership.user and membership.user.db_id else None,
        old_values={},
        new_values=new_values,
        tracked_fields=API_KEY_TRACKED_FIELDS,
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )

    await db.commit()
    await db.refresh(api_key)

    logger.info(f"API key created: prefix={prefix}, user={membership.user.email}, org={org_id}")

    return ApiKeyCreatedResponse(
        id=api_key.id,
        name=api_key.name,
        key_prefix=prefix,
        role=role,
        is_active=True,
        expires_at=api_key.expires_at,
        last_used_at=None,
        created_at=api_key.created_at,
        user_id=UUID(membership.user.db_id),
        user_email=membership.user.email,
        plaintext_key=plaintext,
    )


@router.get(
    "/organizations/{org_id}/api-keys",
    response_model=List[ApiKeyResponse],
)
async def list_api_keys(
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """
    List API keys for this organisation.

    Admins see all keys; non-admins see only their own.
    """
    query = select(ApiKey).where(ApiKey.organization_id == org_id)

    if membership.role != "admin":
        # Non-admins can only see their own keys
        query = query.where(ApiKey.user_id == UUID(membership.user.db_id))

    query = query.order_by(ApiKey.created_at.desc())
    result = await db.execute(query)
    keys = result.scalars().all()

    # Bulk-load user emails
    user_ids = list({k.user_id for k in keys})
    email_map: dict[UUID, str] = {}
    if user_ids:
        users_result = await db.execute(
            select(DBUser.id, DBUser.email).where(DBUser.id.in_(user_ids))
        )
        email_map = {row[0]: row[1] for row in users_result.fetchall()}

    return [
        ApiKeyResponse(
            id=k.id,
            name=k.name,
            key_prefix=k.key_prefix,
            role=k.role,
            is_active=k.is_active,
            expires_at=k.expires_at,
            last_used_at=k.last_used_at,
            created_at=k.created_at,
            user_id=k.user_id,
            user_email=email_map.get(k.user_id),
        )
        for k in keys
    ]


@router.delete(
    "/organizations/{org_id}/api-keys/{key_id}",
    response_model=dict,
)
async def revoke_api_key(
    org_id: UUID,
    key_id: UUID,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """
    Revoke (soft-delete) an API key.

    Org admins can revoke any key in their org.
    Non-admins can only revoke their own keys.
    """
    result = await db.execute(
        select(ApiKey).where(
            (ApiKey.id == key_id) &
            (ApiKey.organization_id == org_id)
        )
    )
    api_key = result.scalar_one_or_none()

    if not api_key:
        raise HTTPException(status_code=404, detail="API key not found")

    # Non-admins can only revoke their own keys
    if membership.role != "admin" and api_key.user_id != UUID(membership.user.db_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only revoke your own API keys",
        )

    # Capture old values for audit logging before revocation - ONLY safe metadata fields
    old_values = {f: getattr(api_key, f) for f in API_KEY_TRACKED_FIELDS if hasattr(api_key, f)}

    api_key.is_active = False

    # Capture new values after revocation
    new_values = {f: getattr(api_key, f) for f in API_KEY_TRACKED_FIELDS if hasattr(api_key, f)}

    await log_entity_changes(
        db=db,
        organization_id=org_id,
        entity_type='api_key',
        entity_id=api_key.id,
        action='update',
        changed_by_user_id=UUID(membership.user.db_id) if membership.user and membership.user.db_id else None,
        old_values=old_values,
        new_values=new_values,
        tracked_fields=API_KEY_TRACKED_FIELDS,
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )

    await db.commit()

    logger.info(f"API key revoked: id={key_id}, org={org_id}, by={membership.user.email}")
    return {"message": "API key revoked successfully"}
