"""
Organizations API endpoints.
Handles CRUD operations for organizations.

SECURITY: Multi-tenancy enforcement
- GET /organizations: Returns only organisations the user can access (membership + consultant)
- GET /organizations/{org_id}: Requires viewer role via membership or consultant relationship
- POST /organizations: Requires authentication, enforces subscription limits
- PATCH /organizations/{org_id}: Requires admin role
- DELETE /organizations/{org_id}: Requires admin role

SUBSCRIPTION: Organisation creation limits
- Free tier: 1 organisation
- Professional tier: 10 organisations
- Enterprise tier: 999 organisations (effectively unlimited)
- API key authenticated requests bypass subscription limits
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import List
from uuid import UUID

from database import get_db
from models import Organization, OrganizationMember
from schemas import OrganizationResponse, OrganizationCreate, SuccessResponse, OrganizationSettingsResponse, OrganizationSettingsUpdate
from auth import (
    require_auth,
    require_org_role,
    get_accessible_org_ids,
    User,
    OrgMembership
)
from services.subscription import get_user_subscription, can_create_organisation
from services.audit_service import log_entity_changes, detect_action_source, get_request_id, ORGANIZATION_TRACKED_FIELDS

logger = logging.getLogger(__name__)
# Rate limiting temporarily disabled - see Phase 0 debugging
# from rate_limiting import limiter, READ_RATE_LIMIT, WRITE_RATE_LIMIT

router = APIRouter(
    prefix="/organizations",
    tags=["organizations"]
)


@router.get("", response_model=List[OrganizationResponse])
# @limiter.limit(READ_RATE_LIMIT)  # Temporarily disabled
async def list_organizations(
    request: Request,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """
    Get organisations the authenticated user can access.

    Returns organisations via:
    - Direct membership (OrganizationMember table)
    - Active consultant relationships (ConsultantClientRelationship with status='active')

    SECURITY: Only returns organisations the user has explicit access to.
    """
    # Get the list of org IDs the user can access
    accessible_ids = await get_accessible_org_ids(user, db)

    if not accessible_ids:
        return []

    # Fetch the full organisation objects for accessible orgs
    result = await db.execute(
        select(Organization).where(Organization.id.in_(accessible_ids))
    )
    organizations = result.scalars().all()
    return organizations


@router.get("/{org_id}", response_model=OrganizationResponse)
# @limiter.limit(READ_RATE_LIMIT)  # Temporarily disabled
async def get_organization(
    request: Request,
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db)
):
    """
    Get organization by ID.

    SECURITY: Requires viewer role (direct membership or active consultant relationship).
    """
    result = await db.execute(select(Organization).where(Organization.id == org_id))
    organization = result.scalar_one_or_none()

    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")

    return organization


@router.post("", response_model=OrganizationResponse, status_code=status.HTTP_201_CREATED)
# # @limiter.limit(WRITE_RATE_LIMIT)  # Temporarily disabled  # Temporarily disabled
async def create_organization(
    request: Request,
    org_data: OrganizationCreate,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """
    Create a new organization.
    Slug must be unique.

    Note: This endpoint requires authentication but not membership checks,
    as it's used by the invite acceptance flow to create new organisations.

    SUBSCRIPTION: Enforces organisation limits based on user's subscription tier.
    - API key authenticated requests bypass subscription limits (for integrations)
    - Returns HTTP 402 Payment Required if limit is exceeded
    """
    # Skip subscription check for API key authenticated requests
    if user.auth_method != "api_key":
        # Get user's subscription (auto-creates free tier if none exists)
        subscription = await get_user_subscription(UUID(user.db_id), db)

        # Count organisations where user is an admin (owner)
        org_count_result = await db.execute(
            select(func.count(OrganizationMember.id))
            .where(OrganizationMember.user_id == UUID(user.db_id))
            .where(OrganizationMember.role == "admin")
        )
        current_org_count = org_count_result.scalar() or 0

        # Check if user can create another organisation
        if not can_create_organisation(subscription, current_org_count):
            logger.info(
                f"Organisation limit reached for user {user.db_id}: "
                f"current={current_org_count}, max={subscription.max_organisations}"
            )
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={
                    "message": "Organisation limit reached. Upgrade your subscription to create more organisations.",
                    "current_count": current_org_count,
                    "max_allowed": subscription.max_organisations,
                    "upgrade_url": "/subscription/upgrade"
                }
            )

    # Validate evidence storage is configured before creating org
    from services.storage_service import is_configured as storage_configured
    if not storage_configured():
        logger.warning(
            "Evidence storage not configured — org creation by user %s will "
            "proceed but evidence uploads will fail until storage is configured.",
            user.db_id,
        )

    # Check if slug already exists
    result = await db.execute(
        select(Organization).where(Organization.slug == org_data.slug)
    )
    existing_org = result.scalar_one_or_none()

    if existing_org:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Organization with slug '{org_data.slug}' already exists"
        )

    # Create new organization
    new_org = Organization(**org_data.model_dump())
    db.add(new_org)
    await db.flush()  # Get the ID before audit logging

    # Log creation
    new_values = {f: getattr(new_org, f) for f in ORGANIZATION_TRACKED_FIELDS if hasattr(new_org, f)}
    await log_entity_changes(
        db=db, organization_id=new_org.id, entity_type='organization',
        entity_id=new_org.id, action='create',
        changed_by_user_id=UUID(user.db_id) if user and user.db_id else None,
        old_values={}, new_values=new_values,
        tracked_fields=ORGANIZATION_TRACKED_FIELDS,
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )

    await db.commit()
    await db.refresh(new_org)

    return new_org


@router.patch("/{org_id}", response_model=OrganizationResponse)
# # @limiter.limit(WRITE_RATE_LIMIT)  # Temporarily disabled  # Temporarily disabled
async def update_organization(
    request: Request,
    org_id: UUID,
    org_data: OrganizationCreate,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db)
):
    """
    Update an existing organization.
    Only provided fields will be updated.

    SECURITY: Requires admin role (direct membership or active consultant relationship).
    """
    # Get existing organization (membership already verified by require_org_role)
    result = await db.execute(select(Organization).where(Organization.id == org_id))
    organization = result.scalar_one_or_none()

    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")

    # Check if new slug conflicts with another organization
    if org_data.slug != organization.slug:
        slug_check = await db.execute(
            select(Organization).where(Organization.slug == org_data.slug)
        )
        if slug_check.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Organization with slug '{org_data.slug}' already exists"
            )

    # Capture old values for audit trail
    old_values = {f: getattr(organization, f) for f in ORGANIZATION_TRACKED_FIELDS if hasattr(organization, f)}

    # Update organization fields
    for key, value in org_data.model_dump(exclude_unset=True).items():
        setattr(organization, key, value)

    # Capture new values and log changes
    new_values = {f: getattr(organization, f) for f in ORGANIZATION_TRACKED_FIELDS if hasattr(organization, f)}
    await log_entity_changes(
        db=db, organization_id=org_id, entity_type='organization',
        entity_id=organization.id, action='update',
        changed_by_user_id=UUID(membership.user.db_id) if membership.user and membership.user.db_id else None,
        old_values=old_values, new_values=new_values,
        tracked_fields=ORGANIZATION_TRACKED_FIELDS,
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )

    await db.commit()
    await db.refresh(organization)

    return organization


@router.delete("/{org_id}", response_model=SuccessResponse)
# @limiter.limit(WRITE_RATE_LIMIT)  # Temporarily disabled
async def delete_organization(
    request: Request,
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db)
):
    """
    Delete an organization.
    This will cascade delete all related scoped controls, evidence tracking, etc.

    SECURITY: Requires admin role (direct membership or active consultant relationship).
    """
    # Get organization (membership already verified by require_org_role)
    result = await db.execute(select(Organization).where(Organization.id == org_id))
    organization = result.scalar_one_or_none()

    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")

    # Delete organization (cascades to related records)
    await db.delete(organization)
    await db.commit()

    return SuccessResponse(
        message=f"Organization '{organization.name}' successfully deleted"
    )


@router.get("/{org_id}/settings", response_model=OrganizationSettingsResponse)
async def get_organization_settings(
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db)
):
    """
    Get organization settings (owner teams, etc.).

    SECURITY: Requires viewer role.
    """
    result = await db.execute(select(Organization).where(Organization.id == org_id))
    organization = result.scalar_one_or_none()

    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")

    settings = organization.settings or {}
    return OrganizationSettingsResponse(
        owner_teams=settings.get("owner_teams", []),
        is_trust_portal_enabled=settings.get("is_trust_portal_enabled", False),
        trust_portal_description=settings.get("trust_portal_description"),
    )


@router.patch("/{org_id}/settings", response_model=OrganizationSettingsResponse)
async def update_organization_settings(
    org_id: UUID,
    settings_data: OrganizationSettingsUpdate,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db)
):
    """
    Update organization settings (partial merge).

    SECURITY: Requires admin role.
    """
    result = await db.execute(select(Organization).where(Organization.id == org_id))
    organization = result.scalar_one_or_none()

    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")

    # Capture old settings for audit trail
    old_settings = dict(organization.settings or {})

    current_settings = dict(organization.settings or {})
    update = settings_data.model_dump(exclude_unset=True)

    if "owner_teams" in update:
        current_settings["owner_teams"] = update["owner_teams"]
    if "is_trust_portal_enabled" in update:
        current_settings["is_trust_portal_enabled"] = update["is_trust_portal_enabled"]
    if "trust_portal_description" in update:
        current_settings["trust_portal_description"] = update["trust_portal_description"]

    organization.settings = current_settings

    # Capture new settings and log changes
    new_settings = dict(organization.settings or {})
    await log_entity_changes(
        db=db, organization_id=org_id, entity_type='organization',
        entity_id=organization.id, action='update',
        changed_by_user_id=UUID(membership.user.db_id) if membership.user and membership.user.db_id else None,
        old_values={'settings': old_settings}, new_values={'settings': new_settings},
        tracked_fields={'settings'},
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )

    await db.commit()
    await db.refresh(organization)

    return OrganizationSettingsResponse(
        owner_teams=current_settings.get("owner_teams", []),
        is_trust_portal_enabled=current_settings.get("is_trust_portal_enabled", False),
        trust_portal_description=current_settings.get("trust_portal_description"),
    )
