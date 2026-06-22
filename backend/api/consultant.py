"""
Consultant Portal API endpoints.

This module provides the REST API for the Consultant Portal, enabling
GRC consultants to manage multiple client organisations from a single
dashboard.

Endpoints:
    GET  /api/consultant/check         - Check if user is a consultant (no side effects)
    POST /api/consultant/register      - Register as a consultant (explicit opt-in)
    GET  /api/consultant/profile       - Get consultant profile (requires registration)
    PATCH /api/consultant/profile      - Update profile
    GET  /api/consultant/clients       - List client organisations
    POST /api/consultant/clients/invite - Send client invitation
    GET  /api/consultant/invites       - List pending invitations
    DELETE /api/consultant/invites/{id} - Cancel invitation
    GET  /api/consultant/invites/{token}/preview - Preview invitation (public)
    POST /api/consultant/invites/{token}/accept - Accept invitation
    GET  /api/consultant/dashboard     - Cross-org metrics dashboard
    DELETE /api/consultant/clients/{org_id} - Remove client relationship

Authorization:
    All endpoints require authentication via Google OAuth or API key.
    The authenticated user must explicitly register as a consultant via /register.

IMPORTANT - No Auto-Provisioning (Fix for Issue #74):
    Previously, ANY user who accessed consultant endpoints would automatically
    become a consultant. This was a bug that could cause clients to accidentally
    become consultants when viewing the portal.

    Now, users must explicitly call /consultant/register to become a consultant.
    The /consultant/check endpoint can be used to determine if a user is a
    consultant without any side effects.

Edge Cases Handled:
    - Explicit registration required (no auto-provisioning)
    - Pagination for consultants with many clients (50+)
    - Rate limiting considerations for invite endpoint
    - Soft delete (archive) vs hard delete for client relationships
    - Token expiration and reuse prevention for invites
"""
import logging
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from auth import require_auth, User
from models import ConsultantInviteStatus
from schemas import (
    ConsultantProfileResponse,
    ConsultantProfileCreate,
    ClientSummaryResponse,
    ClientSummaryMetrics,
    ConsultantInviteCreate,
    ConsultantInviteResponse,
    ConsultantDashboardResponse,
    ConsultantDashboardMetrics,
    AcceptInviteResponse,
    InvitePreviewResponse,
    OrganizationResponse,
    SuccessResponse,
    RemoveClientResponse,
    CreateClientOrgRequest,
    CreateClientOrgResponse,
    InviteOrgAdminRequest,
)
from services.consultant import ConsultantService
from services.email_service import send_invitation_email

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/consultant",
    tags=["consultant"],
)


# =============================================================================
# Helper: Get Consultant Profile (No Auto-Provisioning)
# =============================================================================

async def get_consultant_profile(
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Dependency that requires the authenticated user to have a consultant profile.

    Unlike the previous implementation, this does NOT auto-provision profiles.
    Users must explicitly register as consultants via /consultant/register.
    """
    if not current_user.db_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account not fully provisioned. Please sign in with Google first.",
        )

    service = ConsultantService(db)
    profile = await service.get_profile(UUID(current_user.db_id))

    if not profile:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not registered as a consultant. Please register first.",
        )

    if not profile.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Consultant profile has been deactivated. Contact support.",
        )

    return profile, service


# =============================================================================
# Status & Registration Endpoints
# =============================================================================

@router.get("/check")
async def check_consultant_status(
    request: Request,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Check if the current user is registered as a consultant.

    This endpoint does NOT auto-provision profiles. It simply checks
    whether the user has an existing consultant profile AND whether
    their subscription tier grants consultant access.

    Use this endpoint to determine whether to show the Consultant Portal
    UI elements to the user.

    Returns:
        Dict with is_consultant boolean, subscription tier status, and profile_id if applicable.
    """
    if not current_user.db_id:
        return {
            "is_consultant": False,
            "profile_id": None,
            "is_active": False,
            "has_consultant_subscription": False,
            "reason": "User account not fully provisioned",
        }

    service = ConsultantService(db)
    profile = await service.get_profile(UUID(current_user.db_id))

    # Check subscription tier for consultant access
    has_consultant_subscription = False
    try:
        from services.subscription import get_user_subscription
        subscription = await get_user_subscription(UUID(current_user.db_id), db)
        has_consultant_subscription = (
            subscription.tier == "consultant" and subscription.is_active
        )
    except Exception:
        logger.warning(f"Failed to check subscription for user {current_user.db_id}")

    if not profile:
        return {
            "is_consultant": False,
            "profile_id": None,
            "is_active": False,
            "has_consultant_subscription": has_consultant_subscription,
            "reason": None,
        }

    return {
        "is_consultant": True,
        "profile_id": str(profile.id),
        "is_active": profile.is_active,
        "has_consultant_subscription": has_consultant_subscription,
        "reason": None if profile.is_active else "Profile deactivated",
    }


@router.post("/register")
async def register_as_consultant(request: Request):
    """
    REMOVED: Self-registration as consultant is no longer supported.

    Consultant profiles are now provisioned exclusively through the
    marketing website subscription flow (Stripe -> sync -> platform).

    Subscribe at the marketing website to become a consultant.

    Returns:
        410 Gone
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="Self-registration as consultant is no longer supported. "
        "Please subscribe to the consultant tier at the marketing website.",
    )


# =============================================================================
# Organisation Pre-Creation Endpoints (New Flow)
# =============================================================================

@router.post("/clients/organisations", response_model=CreateClientOrgResponse, status_code=status.HTTP_201_CREATED)
async def create_client_organisation(
    request: Request,
    org_data: CreateClientOrgRequest,
    current_user: User = Depends(require_auth),
    profile_and_service=Depends(get_consultant_profile),
    db: AsyncSession = Depends(get_db),
):
    """
    Pre-create a client organisation.

    The consultant creates the organisation first, then invites an admin
    user to take ownership. The org starts with awaiting_admin=true.

    Args:
        org_data: Organisation name

    Returns:
        CreateClientOrgResponse: The created organisation.

    Raises:
        400: If client limit reached
    """
    profile, service = profile_and_service

    try:
        org = await service.create_client_organisation(
            profile=profile,
            org_name=org_data.name,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    return CreateClientOrgResponse(
        success=True,
        message=f"Organisation '{org.name}' created. Invite an admin to complete setup.",
        organization=OrganizationResponse(
            id=org.id,
            name=org.name,
            slug=org.slug,
            created_at=org.created_at,
            updated_at=org.updated_at,
        ),
        awaiting_admin=True,
    )


@router.post("/clients/{org_id}/invite-admin", response_model=ConsultantInviteResponse, status_code=status.HTTP_201_CREATED)
async def invite_org_admin(
    request: Request,
    org_id: UUID,
    invite_data: InviteOrgAdminRequest,
    current_user: User = Depends(require_auth),
    profile_and_service=Depends(get_consultant_profile),
    db: AsyncSession = Depends(get_db),
):
    """
    Invite an admin user to a pre-created client organisation.

    The invitee will join the existing organisation as admin
    (no new org creation on acceptance).

    Args:
        org_id: The pre-created organisation ID
        invite_data: Email and optional message

    Returns:
        ConsultantInviteResponse: The created invitation.

    Raises:
        400: For validation errors
        403: If org doesn't belong to consultant
    """
    profile, service = profile_and_service

    try:
        invite = await service.invite_org_admin(
            profile=profile,
            org_id=org_id,
            email=invite_data.email,
            message=invite_data.message,
            consultant_email=current_user.email,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except PermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e),
        )

    # Send invitation email
    inviter_name = current_user.name or current_user.email.split('@')[0]
    email_id = await send_invitation_email(
        to_email=invite_data.email,
        organization_name=invite.organization_name,
        inviter_name=inviter_name,
        invite_token=invite.invite_token,
        custom_message=invite_data.message,
    )

    if email_id:
        logger.info(f"Admin invite email sent: email_id={email_id}")

    return ConsultantInviteResponse(
        id=invite.id,
        email=invite.email,
        organization_name=invite.organization_name,
        organization_id=invite.organization_id,
        status=invite.status,
        invite_token=invite.invite_token,
        expires_at=invite.expires_at,
        created_at=invite.created_at,
    )


# =============================================================================
# Profile Endpoints
# =============================================================================

@router.get("/profile", response_model=ConsultantProfileResponse)
async def get_profile(
    request: Request,
    profile_and_service=Depends(get_consultant_profile),
    db: AsyncSession = Depends(get_db),
):
    """
    Get the current user's consultant profile.

    Requires the user to be registered as a consultant. Use /consultant/check
    to verify consultant status, and /consultant/register to become a consultant.

    Returns:
        ConsultantProfileResponse: The consultant's profile with active client count.

    Raises:
        403: If user is not registered as a consultant
    """
    profile, service = profile_and_service

    # Calculate active client count
    active_count = len([
        r for r in profile.client_relationships
        if r.status == "active"
    ])

    return ConsultantProfileResponse(
        id=profile.id,
        user_id=profile.user_id,
        company_name=profile.company_name,
        is_active=profile.is_active,
        max_clients=profile.max_clients,
        active_client_count=active_count,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


@router.patch("/profile", response_model=ConsultantProfileResponse)
async def update_profile(
    request: Request,
    profile_data: ConsultantProfileCreate,
    profile_and_service=Depends(get_consultant_profile),
    db: AsyncSession = Depends(get_db),
):
    """
    Update the consultant's profile.

    Args:
        profile_data: Updated profile fields (currently only company_name)

    Returns:
        ConsultantProfileResponse: The updated profile.
    """
    profile, service = profile_and_service

    updated = await service.update_profile(
        profile=profile,
        company_name=profile_data.company_name,
    )

    active_count = len([
        r for r in updated.client_relationships
        if r.status == "active"
    ])

    return ConsultantProfileResponse(
        id=updated.id,
        user_id=updated.user_id,
        company_name=updated.company_name,
        is_active=updated.is_active,
        max_clients=updated.max_clients,
        active_client_count=active_count,
        created_at=updated.created_at,
        updated_at=updated.updated_at,
    )


# =============================================================================
# Client Management Endpoints
# =============================================================================

@router.get("/clients", response_model=List[ClientSummaryResponse])
async def list_clients(
    request: Request,
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
    limit: int = Query(default=50, ge=1, le=100, description="Max results (1-100)"),
    include_metrics: bool = Query(default=True, description="Include per-client metrics"),
    profile_and_service=Depends(get_consultant_profile),
    db: AsyncSession = Depends(get_db),
):
    """
    List all client organisations for the consultant.

    Returns a paginated list of clients with optional summary metrics including:
    - Control counts by status
    - Evidence tracking status
    - Framework readiness score

    Supports pagination for consultants with many clients (50+).

    Args:
        offset: Pagination offset (default 0)
        limit: Max results per page (default 50, max 100)
        include_metrics: Whether to include per-client metrics (default True)

    Returns:
        List[ClientSummaryResponse]: List of client summaries with metrics.
    """
    profile, service = profile_and_service

    clients = await service.list_clients(
        profile=profile,
        include_metrics=include_metrics,
        offset=offset,
        limit=limit,
    )

    # Transform to response schema
    return [
        ClientSummaryResponse(
            id=client["id"],
            organization_id=client["organization_id"],
            organization_name=client["organization_name"],
            organization_slug=client["organization_slug"],
            role=client["role"],
            status=client["status"],
            linked_at=client["linked_at"],
            metrics=ClientSummaryMetrics(**client["metrics"]) if client.get("metrics") else ClientSummaryMetrics(),
        )
        for client in clients
    ]


@router.delete("/clients/{org_id}", response_model=RemoveClientResponse)
async def remove_client(
    request: Request,
    org_id: UUID,
    archive: bool = Query(default=True, description="Archive instead of hard delete"),
    profile_and_service=Depends(get_consultant_profile),
    db: AsyncSession = Depends(get_db),
):
    """
    Remove or archive a client relationship.

    By default, relationships are archived (soft delete) to preserve history.
    Set archive=false for permanent deletion.

    Note: This only removes the consultant's access to the organisation.
    The organisation itself is not deleted.

    Args:
        org_id: Organisation ID to remove
        archive: If True (default), archive; if False, hard delete

    Returns:
        RemoveClientResponse: Confirmation of the action.

    Raises:
        404: If client relationship not found
    """
    profile, service = profile_and_service

    success, message = await service.remove_client(
        profile=profile,
        org_id=org_id,
        archive=archive,
    )

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=message,
        )

    return RemoveClientResponse(
        success=True,
        message=message,
        organization_id=org_id,
        action="archived" if archive else "deleted",
    )


# =============================================================================
# Invitation Endpoints
# =============================================================================

@router.post("/clients/invite", response_model=ConsultantInviteResponse, status_code=status.HTTP_201_CREATED)
async def create_invite(
    request: Request,
    invite_data: ConsultantInviteCreate,
    current_user: User = Depends(require_auth),
    profile_and_service=Depends(get_consultant_profile),
    db: AsyncSession = Depends(get_db),
):
    """
    Send an invitation to a new client.

    Creates a unique invitation token that the invitee can use to:
    1. Create their organisation
    2. Link it to this consultant

    The invitation expires after 7 days.

    Rate Limiting Consideration: This endpoint should have stricter rate limits
    than read endpoints to prevent abuse.

    Args:
        invite_data: Email, organisation name, and optional message

    Returns:
        ConsultantInviteResponse: The created invitation with token.

    Raises:
        400: If client limit reached, duplicate pending invite, or self-invitation
    """
    profile, service = profile_and_service

    invite, message = await service.create_invite(
        profile=profile,
        email=invite_data.email,
        organization_name=invite_data.organization_name,
        message=invite_data.message,
        consultant_email=current_user.email,
    )

    if not invite:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=message,
        )

    logger.info(f"Invitation created: consultant={profile.id}, email={invite_data.email}")

    # Send invitation email with tokenized URL
    inviter_name = current_user.name or current_user.email.split('@')[0]
    email_id = await send_invitation_email(
        to_email=invite_data.email,
        organization_name=invite_data.organization_name,
        inviter_name=inviter_name,
        invite_token=invite.invite_token,
        custom_message=invite_data.message,
    )

    if email_id:
        logger.info(f"Invitation email sent: email_id={email_id}")
    else:
        logger.warning(f"Invitation email not sent (service may be disabled)")

    return ConsultantInviteResponse(
        id=invite.id,
        email=invite.email,
        organization_name=invite.organization_name,
        status=invite.status,
        invite_token=invite.invite_token,  # Only shown on creation
        expires_at=invite.expires_at,
        created_at=invite.created_at,
    )


@router.get("/invites", response_model=List[ConsultantInviteResponse])
async def list_invites(
    request: Request,
    status_filter: Optional[str] = Query(
        default=None,
        alias="status",
        description="Filter by status (pending, accepted, expired, cancelled)"
    ),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    profile_and_service=Depends(get_consultant_profile),
    db: AsyncSession = Depends(get_db),
):
    """
    List all invitations sent by this consultant.

    Can be filtered by status to show only pending invites.

    Args:
        status: Filter by invite status (optional)
        offset: Pagination offset
        limit: Max results (1-100)

    Returns:
        List[ConsultantInviteResponse]: List of invitations.
    """
    profile, service = profile_and_service

    invites = await service.list_invites(
        profile=profile,
        status=status_filter,
        offset=offset,
        limit=limit,
    )

    return [
        ConsultantInviteResponse(
            id=inv.id,
            email=inv.email,
            organization_name=inv.organization_name,
            status=inv.status,
            invite_token=None,  # Don't expose token in list view
            expires_at=inv.expires_at,
            created_at=inv.created_at,
        )
        for inv in invites
    ]


@router.delete("/invites/{invite_id}", response_model=SuccessResponse)
async def cancel_invite(
    request: Request,
    invite_id: UUID,
    profile_and_service=Depends(get_consultant_profile),
    db: AsyncSession = Depends(get_db),
):
    """
    Cancel a pending invitation.

    Only pending invitations can be cancelled.

    Args:
        invite_id: The invitation ID to cancel

    Returns:
        SuccessResponse: Confirmation message.

    Raises:
        404: If invitation not found
        400: If invitation is not pending
    """
    profile, service = profile_and_service

    success, message = await service.cancel_invite(
        profile=profile,
        invite_id=invite_id,
    )

    if not success:
        if "not found" in message.lower():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=message,
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=message,
        )

    return SuccessResponse(success=True, message=message)


@router.get("/invites/{token}/preview", response_model=InvitePreviewResponse)
async def preview_invite(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Get invitation details for preview before accepting.

    This is a PUBLIC endpoint (no authentication required) so users can
    view the invitation details before signing in.

    Args:
        token: The invitation token from the invite email

    Returns:
        InvitePreviewResponse: Invitation details for display.

    Raises:
        404: If token is invalid or invite not found
    """
    from sqlalchemy import select
    from sqlalchemy.orm import joinedload
    from models import ConsultantInvite, User as DBUser

    # Find the invite with consultant profile
    result = await db.execute(
        select(ConsultantInvite)
        .where(ConsultantInvite.invite_token == token)
        .options(joinedload(ConsultantInvite.consultant))
    )
    invite = result.unique().scalar_one_or_none()

    if not invite:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invitation not found or invalid token",
        )

    # Get consultant user details
    consultant_user = None
    if invite.consultant and invite.consultant.user_id:
        user_result = await db.execute(
            select(DBUser).where(DBUser.id == invite.consultant.user_id)
        )
        consultant_user = user_result.scalar_one_or_none()

    # Check if expired
    is_expired = invite.is_expired()

    # Update status if expired but not already marked
    if is_expired and invite.status == ConsultantInviteStatus.PENDING.value:
        invite.status = ConsultantInviteStatus.EXPIRED.value
        await db.commit()

    # PII masking for public preview: mask email to show first char + domain
    masked_email = "Unknown"
    if consultant_user and consultant_user.email:
        raw = consultant_user.email
        if "@" in raw:
            local, domain = raw.split("@", 1)
            masked_email = f"{local[0]}***@{domain}" if local else raw
        else:
            masked_email = raw

    return InvitePreviewResponse(
        organization_name=invite.organization_name,
        consultant_name=consultant_user.display_name if consultant_user else None,
        consultant_email=masked_email,
        expires_at=invite.expires_at,
        is_expired=is_expired,
        status=invite.status,
    )


@router.post("/invites/{token}/accept", response_model=AcceptInviteResponse)
async def accept_invite(
    request: Request,
    token: str,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Accept an invitation and create the organisation.

    This endpoint is called by the invitee (not the consultant).
    It creates the organisation and links it to the consultant.

    The accepting user becomes an admin of the new organisation.

    Args:
        token: The invitation token from the invite email

    Returns:
        AcceptInviteResponse: The created organisation details.

    Raises:
        400: If token is invalid, expired, or already used
        403: If user is not fully provisioned
    """
    if not current_user.db_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account not fully provisioned. Please sign in with Google first.",
        )

    service = ConsultantService(db)
    org, message = await service.accept_invite(
        token=token,
        user_id=UUID(current_user.db_id),
        user_email=current_user.email,
    )

    if not org:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=message,
        )

    logger.info(f"Invitation accepted: user={current_user.db_id}, org={org.id}")

    return AcceptInviteResponse(
        success=True,
        message=message,
        organization=OrganizationResponse(
            id=org.id,
            name=org.name,
            slug=org.slug,
            created_at=org.created_at,
            updated_at=org.updated_at,
        ),
    )


# =============================================================================
# Dashboard Endpoint
# =============================================================================

@router.get("/dashboard", response_model=ConsultantDashboardResponse)
async def get_dashboard(
    request: Request,
    profile_and_service=Depends(get_consultant_profile),
    db: AsyncSession = Depends(get_db),
):
    """
    Get the consultant dashboard with aggregated cross-org metrics.

    This is the main entry point for the consultant portal, providing:
    - Consultant profile summary
    - Total and active client counts
    - Pending invitation count
    - Aggregated control status breakdown
    - Average framework readiness across clients
    - Recent activity across all clients

    Performance Note: This endpoint aggregates data from potentially many
    organisations. For consultants with 50+ clients, response time may
    increase. Consider caching for production.

    Returns:
        ConsultantDashboardResponse: Complete dashboard data.
    """
    profile, service = profile_and_service

    # Get aggregated metrics
    metrics_data = await service.get_dashboard_metrics(profile)

    # Get client list (limited for dashboard view)
    clients = await service.list_clients(
        profile=profile,
        include_metrics=True,
        offset=0,
        limit=20,  # Dashboard shows top 20 clients
    )

    # Build profile response
    active_count = metrics_data["active_clients"]
    profile_response = ConsultantProfileResponse(
        id=profile.id,
        user_id=profile.user_id,
        company_name=profile.company_name,
        is_active=profile.is_active,
        max_clients=profile.max_clients,
        active_client_count=active_count,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )

    # Build metrics response
    metrics_response = ConsultantDashboardMetrics(
        total_clients=metrics_data["total_clients"],
        active_clients=metrics_data["active_clients"],
        pending_invites=metrics_data["pending_invites"],
        total_controls_across_clients=metrics_data["total_controls_across_clients"],
        implemented_controls_across_clients=metrics_data["implemented_controls_across_clients"],
        average_framework_readiness=metrics_data["average_framework_readiness"],
        controls_by_status=metrics_data["controls_by_status"],
        recent_activity=metrics_data["recent_activity"],
    )

    # Build client list response
    clients_response = [
        ClientSummaryResponse(
            id=client["id"],
            organization_id=client["organization_id"],
            organization_name=client["organization_name"],
            organization_slug=client["organization_slug"],
            role=client["role"],
            status=client["status"],
            linked_at=client["linked_at"],
            metrics=ClientSummaryMetrics(**client["metrics"]) if client.get("metrics") else ClientSummaryMetrics(),
        )
        for client in clients
    ]

    return ConsultantDashboardResponse(
        profile=profile_response,
        metrics=metrics_response,
        clients=clients_response,
    )
