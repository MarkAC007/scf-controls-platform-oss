"""
Consultant Service - Business logic for the Consultant Portal.

This service handles:
- Consultant profile management (auto-provisioning)
- Client organisation relationships
- Invitation workflow with secure tokens
- Cross-org metrics aggregation

Design Considerations:
- Pagination: All list endpoints support offset/limit for 50+ clients
- Rate limiting: Invite endpoints should have stricter limits
- Token security: 32-byte cryptographically secure tokens
- Soft delete: Client relationships are archived, not deleted
"""
import secrets
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any, Tuple
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_
from sqlalchemy.orm import joinedload

from models import (
    User,
    Organization,
    OrganizationMember,
    ConsultantProfile,
    ConsultantClientRelationship,
    ConsultantInvite,
    ScopedControl,
    EvidenceTracking,
    ConsultantInviteStatus,
    ConsultantClientStatus,
    UserSubscription,
)
from services.subscription import get_user_subscription, can_create_organisation
from services.org_utils import generate_unique_slug
from services.domain_validation import is_public_domain

logger = logging.getLogger(__name__)

# Configuration
INVITE_TOKEN_BYTES = 32  # 256 bits of entropy
INVITE_EXPIRY_DAYS = 7  # Invites expire after 7 days
DEFAULT_CONSULTANT_MAX_CLIENTS = 20


class ConsultantService:
    """Service class for consultant portal operations."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # =========================================================================
    # Profile Management
    # =========================================================================

    async def get_profile(self, user_id: UUID) -> Optional[ConsultantProfile]:
        """
        Get consultant profile if it exists.

        Args:
            user_id: The authenticated user's database ID

        Returns:
            ConsultantProfile or None if user is not a consultant
        """
        result = await self.db.execute(
            select(ConsultantProfile)
            .where(ConsultantProfile.user_id == user_id)
            .options(joinedload(ConsultantProfile.client_relationships))
        )
        profile = result.unique().scalar_one_or_none()

        if profile:
            logger.debug(f"Found consultant profile for user {user_id}")
        else:
            logger.debug(f"No consultant profile found for user {user_id}")

        return profile

    async def create_profile(
        self,
        user_id: UUID,
        company_name: Optional[str] = None,
    ) -> ConsultantProfile:
        """
        Create a new consultant profile (explicit registration).

        This requires explicit user consent to become a consultant.
        Used by the /consultant/register endpoint.

        Args:
            user_id: The authenticated user's database ID
            company_name: Optional company/consultancy name

        Returns:
            ConsultantProfile: The newly created profile

        Raises:
            ValueError: If user already has a consultant profile
        """
        # Check if profile already exists
        existing = await self.get_profile(user_id)
        if existing:
            raise ValueError("User already has a consultant profile")

        logger.info(f"Creating new consultant profile for user {user_id}")
        profile = ConsultantProfile(
            user_id=user_id,
            company_name=company_name,
            max_clients=DEFAULT_CONSULTANT_MAX_CLIENTS,
            is_active=True,
        )
        self.db.add(profile)
        await self.db.commit()
        await self.db.refresh(profile)

        # Reload with relationships
        result = await self.db.execute(
            select(ConsultantProfile)
            .where(ConsultantProfile.id == profile.id)
            .options(joinedload(ConsultantProfile.client_relationships))
        )
        return result.unique().scalar_one()

    async def get_or_create_profile(self, user_id: UUID) -> ConsultantProfile:
        """
        DEPRECATED: Get consultant profile, creating one if it doesn't exist.

        This method is kept for backwards compatibility but should not be used
        for new code. Use get_profile() and create_profile() separately instead.

        Args:
            user_id: The authenticated user's database ID

        Returns:
            ConsultantProfile: The user's consultant profile
        """
        profile = await self.get_profile(user_id)
        if profile:
            return profile

        return await self.create_profile(user_id)

    async def update_profile(
        self,
        profile: ConsultantProfile,
        company_name: Optional[str] = None,
    ) -> ConsultantProfile:
        """
        Update consultant profile details.

        Args:
            profile: The profile to update
            company_name: Optional new company name

        Returns:
            ConsultantProfile: The updated profile
        """
        if company_name is not None:
            profile.company_name = company_name

        await self.db.commit()
        await self.db.refresh(profile)
        return profile

    # =========================================================================
    # Client Management
    # =========================================================================

    async def list_clients(
        self,
        profile: ConsultantProfile,
        include_metrics: bool = True,
        offset: int = 0,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        List all client organisations for a consultant with optional metrics.

        Args:
            profile: The consultant's profile
            include_metrics: Whether to include per-client metrics
            offset: Pagination offset
            limit: Pagination limit (max 100)

        Returns:
            List of client summaries with metrics
        """
        limit = min(limit, 100)  # Cap at 100 for performance

        # Get client relationships with organisation data
        result = await self.db.execute(
            select(ConsultantClientRelationship)
            .where(ConsultantClientRelationship.consultant_id == profile.id)
            .where(ConsultantClientRelationship.status != ConsultantClientStatus.SUSPENDED.value)
            .options(joinedload(ConsultantClientRelationship.organization))
            .order_by(ConsultantClientRelationship.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        relationships = result.unique().scalars().all()

        clients = []
        for rel in relationships:
            org = rel.organization
            client_data = {
                "id": rel.id,
                "organization_id": org.id,
                "organization_name": org.name,
                "organization_slug": org.slug,
                "role": rel.role,
                "status": rel.status,
                "linked_at": rel.created_at,
                "metrics": {},
            }

            if include_metrics:
                client_data["metrics"] = await self._get_client_metrics(org.id)

            clients.append(client_data)

        return clients

    async def _get_client_metrics(self, org_id: UUID) -> Dict[str, Any]:
        """
        Calculate summary metrics for a single client organisation.

        Args:
            org_id: Organisation ID

        Returns:
            Dict with control and evidence metrics
        """
        # Get control counts by status
        control_result = await self.db.execute(
            select(
                ScopedControl.implementation_status,
                func.count(ScopedControl.id).label("count")
            )
            .where(ScopedControl.organization_id == org_id)
            .where(ScopedControl.selected == True)
            .group_by(ScopedControl.implementation_status)
        )
        control_counts = {row[0] or "no_status": row[1] for row in control_result.all()}

        # Get evidence counts
        evidence_result = await self.db.execute(
            select(func.count(EvidenceTracking.id))
            .where(EvidenceTracking.organization_id == org_id)
        )
        total_evidence = evidence_result.scalar() or 0

        tracked_evidence_result = await self.db.execute(
            select(func.count(EvidenceTracking.id))
            .where(EvidenceTracking.organization_id == org_id)
            .where(EvidenceTracking.is_tracked == True)
        )
        tracked_evidence = tracked_evidence_result.scalar() or 0

        # Calculate readiness (simplified - full calculation would use framework mappings)
        total_controls = sum(control_counts.values())
        implemented = control_counts.get("implemented", 0) + control_counts.get("monitored", 0)
        readiness = (implemented / total_controls * 100) if total_controls > 0 else 0.0

        return {
            "total_controls": total_controls,
            "implemented_controls": implemented,
            "in_progress_controls": control_counts.get("in_progress", 0),
            "at_risk_controls": control_counts.get("at_risk", 0),
            "total_evidence": total_evidence,
            "tracked_evidence": tracked_evidence,
            "framework_readiness": round(readiness, 1),
        }

    async def remove_client(
        self,
        profile: ConsultantProfile,
        org_id: UUID,
        archive: bool = True,
    ) -> Tuple[bool, str]:
        """
        Remove or archive a client relationship.

        Args:
            profile: The consultant's profile
            org_id: Organisation ID to remove
            archive: If True, set status to 'suspended'; if False, hard delete

        Returns:
            Tuple of (success, message)
        """
        # Find the relationship
        result = await self.db.execute(
            select(ConsultantClientRelationship)
            .where(ConsultantClientRelationship.consultant_id == profile.id)
            .where(ConsultantClientRelationship.organization_id == org_id)
        )
        relationship = result.scalar_one_or_none()

        if not relationship:
            return False, "Client relationship not found"

        if archive:
            relationship.status = ConsultantClientStatus.SUSPENDED.value
            await self.db.commit()
            logger.info(f"Archived client relationship: consultant={profile.id}, org={org_id}")
            return True, "Client relationship archived"
        else:
            await self.db.delete(relationship)
            await self.db.commit()
            logger.info(f"Deleted client relationship: consultant={profile.id}, org={org_id}")
            return True, "Client relationship deleted"

    # =========================================================================
    # Invitation Workflow
    # =========================================================================

    async def create_invite(
        self,
        profile: ConsultantProfile,
        email: str,
        organization_name: str,
        message: Optional[str] = None,
        consultant_email: Optional[str] = None,
    ) -> Tuple[Optional[ConsultantInvite], str]:
        """
        Create an invitation for a new client.

        Args:
            profile: The consultant's profile
            email: Invitee's email address
            organization_name: Proposed organisation name
            message: Optional custom message
            consultant_email: The consultant's email (for self-invitation check)

        Returns:
            Tuple of (invite or None, error message or success message)
        """
        # Normalise email
        email = email.strip().lower()

        # Prevent self-invitation
        if consultant_email and email == consultant_email.strip().lower():
            return None, "Cannot invite yourself as a client"

        # Block public email domains (gmail, outlook, etc.)
        if is_public_domain(email):
            return None, (
                "Please use a corporate email address. "
                "Public email providers (e.g. Gmail, Outlook) are not supported for invitations."
            )

        # Check consultant's client limit
        active_count = len([
            r for r in profile.client_relationships
            if r.status == ConsultantClientStatus.ACTIVE.value
        ])
        pending_invites_result = await self.db.execute(
            select(func.count(ConsultantInvite.id))
            .where(ConsultantInvite.consultant_id == profile.id)
            .where(ConsultantInvite.status == ConsultantInviteStatus.PENDING.value)
        )
        pending_count = pending_invites_result.scalar() or 0

        if active_count + pending_count >= profile.max_clients:
            return None, f"Client limit reached ({profile.max_clients}). Upgrade your plan or remove inactive clients."

        # Check for duplicate pending invite
        existing_result = await self.db.execute(
            select(ConsultantInvite)
            .where(ConsultantInvite.consultant_id == profile.id)
            .where(ConsultantInvite.email == email)
            .where(ConsultantInvite.status == ConsultantInviteStatus.PENDING.value)
        )
        if existing_result.scalar_one_or_none():
            return None, f"A pending invitation already exists for {email}"

        # Generate secure token
        token = secrets.token_urlsafe(INVITE_TOKEN_BYTES)
        expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=INVITE_EXPIRY_DAYS)

        # Create invite
        invite = ConsultantInvite(
            consultant_id=profile.id,
            email=email,
            organization_name=organization_name,
            invite_token=token,
            status=ConsultantInviteStatus.PENDING.value,
            expires_at=expires_at,
        )
        self.db.add(invite)
        await self.db.commit()
        await self.db.refresh(invite)

        logger.info(f"Created invite: consultant={profile.id}, email={email}, org={organization_name}")
        return invite, "Invitation created successfully"

    async def list_invites(
        self,
        profile: ConsultantProfile,
        status: Optional[str] = None,
        offset: int = 0,
        limit: int = 50,
    ) -> List[ConsultantInvite]:
        """
        List invitations for a consultant.

        Args:
            profile: The consultant's profile
            status: Filter by status (optional)
            offset: Pagination offset
            limit: Pagination limit

        Returns:
            List of invites
        """
        query = (
            select(ConsultantInvite)
            .where(ConsultantInvite.consultant_id == profile.id)
            .order_by(ConsultantInvite.created_at.desc())
            .offset(offset)
            .limit(min(limit, 100))
        )

        if status:
            query = query.where(ConsultantInvite.status == status)

        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def cancel_invite(
        self,
        profile: ConsultantProfile,
        invite_id: UUID,
    ) -> Tuple[bool, str]:
        """
        Cancel a pending invitation.

        Args:
            profile: The consultant's profile
            invite_id: Invitation ID to cancel

        Returns:
            Tuple of (success, message)
        """
        result = await self.db.execute(
            select(ConsultantInvite)
            .where(ConsultantInvite.id == invite_id)
            .where(ConsultantInvite.consultant_id == profile.id)
        )
        invite = result.scalar_one_or_none()

        if not invite:
            return False, "Invitation not found"

        if invite.status != ConsultantInviteStatus.PENDING.value:
            return False, f"Cannot cancel invitation with status '{invite.status}'"

        invite.status = ConsultantInviteStatus.CANCELLED.value
        await self.db.commit()

        logger.info(f"Cancelled invite: {invite_id}")
        return True, "Invitation cancelled"

    async def create_client_organisation(
        self,
        profile: ConsultantProfile,
        org_name: str,
    ) -> Organization:
        """
        Pre-create a client organisation for a consultant.

        The org is created with awaiting_admin=True and linked to the consultant.
        The consultant must then invite an admin user to take ownership.

        Args:
            profile: The consultant's profile
            org_name: Name for the new organisation

        Returns:
            Organization: The pre-created organisation

        Raises:
            ValueError: If consultant has reached client limit
        """
        # Check client limit
        active_count_result = await self.db.execute(
            select(func.count(ConsultantClientRelationship.id))
            .where(ConsultantClientRelationship.consultant_id == profile.id)
            .where(ConsultantClientRelationship.status == ConsultantClientStatus.ACTIVE.value)
        )
        active_count = active_count_result.scalar() or 0

        pending_invites_result = await self.db.execute(
            select(func.count(ConsultantInvite.id))
            .where(ConsultantInvite.consultant_id == profile.id)
            .where(ConsultantInvite.status == ConsultantInviteStatus.PENDING.value)
        )
        pending_count = pending_invites_result.scalar() or 0

        # Count awaiting-admin orgs as well
        awaiting_result = await self.db.execute(
            select(func.count(Organization.id))
            .where(Organization.created_by_consultant_id == profile.id)
            .where(Organization.awaiting_admin == True)
        )
        awaiting_count = awaiting_result.scalar() or 0

        total_allocated = active_count + pending_count + awaiting_count
        if total_allocated >= profile.max_clients:
            raise ValueError(
                f"Client limit reached ({profile.max_clients}). "
                "Upgrade your plan or remove inactive clients."
            )

        slug = await generate_unique_slug(org_name, self.db)

        org = Organization(
            name=org_name,
            slug=slug,
            awaiting_admin=True,
            created_by_consultant_id=profile.id,
        )
        self.db.add(org)
        await self.db.flush()

        # Create consultant-client relationship
        relationship = ConsultantClientRelationship(
            consultant_id=profile.id,
            organization_id=org.id,
            role="admin",
            status=ConsultantClientStatus.ACTIVE.value,
            accepted_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        self.db.add(relationship)

        await self.db.commit()
        await self.db.refresh(org)

        logger.info(f"Consultant {profile.id} pre-created org '{org_name}' (id={org.id}, awaiting_admin=True)")
        return org

    async def invite_org_admin(
        self,
        profile: ConsultantProfile,
        org_id: UUID,
        email: str,
        message: Optional[str] = None,
        consultant_email: Optional[str] = None,
    ) -> ConsultantInvite:
        """
        Invite an admin user to a pre-created client organisation.

        Args:
            profile: The consultant's profile
            org_id: Pre-created organisation ID
            email: Admin user's email
            message: Optional custom message
            consultant_email: The consultant's email for self-invite check

        Returns:
            ConsultantInvite: The created invitation

        Raises:
            ValueError: For validation failures
            PermissionError: If org doesn't belong to consultant
        """
        # Normalise email
        email = email.strip().lower()

        # Self-invite check
        if consultant_email and email == consultant_email.strip().lower():
            raise ValueError("Cannot invite yourself as a client admin")

        # Verify org belongs to this consultant and is awaiting admin
        result = await self.db.execute(
            select(Organization).where(Organization.id == org_id)
        )
        org = result.scalar_one_or_none()
        if not org:
            raise ValueError("Organisation not found")

        if org.created_by_consultant_id != profile.id:
            raise PermissionError("Organisation does not belong to this consultant")

        if not org.awaiting_admin:
            raise ValueError("Organisation already has an admin. Use org member invites instead.")

        # Block public email domains (gmail, outlook, etc.) but allow any corporate domain.
        # Consultants invite clients at different companies, so cross-domain is expected.
        if is_public_domain(email):
            raise ValueError(
                "Please use a corporate email address. "
                "Public email providers (e.g. Gmail, Outlook) are not supported for invitations."
            )

        # Check for duplicate pending invite
        existing_result = await self.db.execute(
            select(ConsultantInvite)
            .where(ConsultantInvite.consultant_id == profile.id)
            .where(ConsultantInvite.email == email)
            .where(ConsultantInvite.organization_id == org_id)
            .where(ConsultantInvite.status == ConsultantInviteStatus.PENDING.value)
        )
        if existing_result.scalar_one_or_none():
            raise ValueError(f"A pending invitation already exists for {email}")

        # Generate secure token
        token = secrets.token_urlsafe(INVITE_TOKEN_BYTES)
        expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=INVITE_EXPIRY_DAYS)

        invite = ConsultantInvite(
            consultant_id=profile.id,
            email=email,
            organization_name=org.name,
            organization_id=org_id,
            invite_token=token,
            status=ConsultantInviteStatus.PENDING.value,
            expires_at=expires_at,
        )
        self.db.add(invite)
        await self.db.commit()
        await self.db.refresh(invite)

        logger.info(f"Created admin invite: consultant={profile.id}, org={org_id}, email={email}")
        return invite

    async def accept_invite(
        self,
        token: str,
        user_id: UUID,
        user_email: str,
    ) -> Tuple[Optional[Organization], str]:
        """
        Accept an invitation and join the pre-created organisation.

        Security fixes applied:
        - TD-01: Email verification (accepting user must match invite email)
        - TD-02: SELECT ... FOR UPDATE locking (race condition prevention)
        - TD-23: Re-validates subscription limits at acceptance time

        Args:
            token: The invite token
            user_id: The accepting user's ID
            user_email: The accepting user's email (for verification)

        Returns:
            Tuple of (organization or None, error/success message)
        """
        from sqlalchemy import text

        # Find the invite WITH lock to prevent race conditions (TD-02)
        result = await self.db.execute(
            select(ConsultantInvite)
            .where(ConsultantInvite.invite_token == token)
            .with_for_update()
            .options(joinedload(ConsultantInvite.consultant))
        )
        invite = result.unique().scalar_one_or_none()

        if not invite:
            return None, "Invalid invitation token"

        if invite.status != ConsultantInviteStatus.PENDING.value:
            return None, f"Invitation has already been {invite.status}"

        if invite.is_expired():
            invite.status = ConsultantInviteStatus.EXPIRED.value
            await self.db.commit()
            return None, "Invitation has expired"

        # TD-01: Verify accepting user's email matches the invitation
        if user_email.strip().lower() != invite.email.strip().lower():
            return None, "This invitation was sent to a different email address. Please sign in with the correct account."

        # TD-23: Re-validate consultant subscription is still active
        consultant_user_id = invite.consultant.user_id
        consultant_subscription = await get_user_subscription(consultant_user_id, self.db)

        if not consultant_subscription.is_active:
            return None, "The consultant's subscription is no longer active."

        # Check if the invite has a pre-created org (new flow)
        if invite.organization_id:
            # New flow: join pre-created org
            result = await self.db.execute(
                select(Organization).where(Organization.id == invite.organization_id)
            )
            org = result.scalar_one_or_none()
            if not org:
                return None, "The organisation for this invitation no longer exists."

            # Make invitee an admin
            membership = OrganizationMember(
                organization_id=org.id,
                user_id=user_id,
                role="admin",
            )
            self.db.add(membership)

            # Clear awaiting_admin flag
            org.awaiting_admin = False

            # Update invite status
            invite.status = ConsultantInviteStatus.ACCEPTED.value

            await self.db.commit()
            await self.db.refresh(org)

            logger.info(f"Invite accepted (pre-created org): token={token[:8]}..., org={org.id}, user={user_id}")
            return org, "You have joined the organisation as admin"
        else:
            # Legacy flow: create org at acceptance time (backwards compat)
            # Count current active client organisations for this consultant
            active_client_count_result = await self.db.execute(
                select(func.count(ConsultantClientRelationship.id))
                .where(ConsultantClientRelationship.consultant_id == invite.consultant_id)
                .where(ConsultantClientRelationship.status == ConsultantClientStatus.ACTIVE.value)
            )
            current_client_count = active_client_count_result.scalar() or 0

            if not can_create_organisation(consultant_subscription, current_client_count):
                return None, f"Consultant has reached their organisation limit ({consultant_subscription.max_organisations}). Upgrade required to add more clients."

            slug = await generate_unique_slug(invite.organization_name, self.db)

            org = Organization(
                name=invite.organization_name,
                slug=slug,
            )
            self.db.add(org)
            await self.db.flush()

            # Make invitee an admin
            membership = OrganizationMember(
                organization_id=org.id,
                user_id=user_id,
                role="admin",
            )
            self.db.add(membership)

            # Create consultant-client relationship
            relationship = ConsultantClientRelationship(
                consultant_id=invite.consultant_id,
                organization_id=org.id,
                role="admin",
                status=ConsultantClientStatus.ACTIVE.value,
                accepted_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
            self.db.add(relationship)

            # Update invite status
            invite.status = ConsultantInviteStatus.ACCEPTED.value

            await self.db.commit()
            await self.db.refresh(org)

            logger.info(f"Invite accepted (legacy): token={token[:8]}..., org={org.id}, user={user_id}")
            return org, "Organisation created successfully"

    # =========================================================================
    # Dashboard Metrics
    # =========================================================================

    async def get_dashboard_metrics(
        self,
        profile: ConsultantProfile,
    ) -> Dict[str, Any]:
        """
        Get aggregated metrics across all client organisations.

        This is the main dashboard data endpoint, providing:
        - Total/active client counts
        - Pending invite count
        - Aggregated control status counts
        - Average framework readiness
        - Recent activity

        Args:
            profile: The consultant's profile

        Returns:
            Dict with aggregated metrics
        """
        # Get all active client org IDs
        result = await self.db.execute(
            select(ConsultantClientRelationship.organization_id)
            .where(ConsultantClientRelationship.consultant_id == profile.id)
            .where(ConsultantClientRelationship.status == ConsultantClientStatus.ACTIVE.value)
        )
        active_org_ids = [row[0] for row in result.all()]

        # Count relationships
        total_clients_result = await self.db.execute(
            select(func.count(ConsultantClientRelationship.id))
            .where(ConsultantClientRelationship.consultant_id == profile.id)
        )
        total_clients = total_clients_result.scalar() or 0

        # Count pending invites
        pending_invites_result = await self.db.execute(
            select(func.count(ConsultantInvite.id))
            .where(ConsultantInvite.consultant_id == profile.id)
            .where(ConsultantInvite.status == ConsultantInviteStatus.PENDING.value)
        )
        pending_invites = pending_invites_result.scalar() or 0

        # Aggregate control counts across all active clients
        controls_by_status: Dict[str, int] = {}
        total_controls = 0
        implemented_controls = 0
        total_readiness = 0.0

        if active_org_ids:
            control_result = await self.db.execute(
                select(
                    ScopedControl.implementation_status,
                    func.count(ScopedControl.id).label("count")
                )
                .where(ScopedControl.organization_id.in_(active_org_ids))
                .where(ScopedControl.selected == True)
                .group_by(ScopedControl.implementation_status)
            )
            for row in control_result.all():
                status = row[0] or "no_status"
                count = row[1]
                controls_by_status[status] = count
                total_controls += count
                if status in ("implemented", "monitored"):
                    implemented_controls += count

            # Calculate average readiness
            if total_controls > 0 and len(active_org_ids) > 0:
                total_readiness = (implemented_controls / total_controls) * 100

        # Recent activity (simplified - would be more complex in production)
        # For now, just return recently updated controls
        recent_activity: List[Dict[str, Any]] = []
        if active_org_ids:
            recent_controls_result = await self.db.execute(
                select(ScopedControl)
                .where(ScopedControl.organization_id.in_(active_org_ids))
                .where(ScopedControl.selected == True)
                .order_by(ScopedControl.updated_at.desc())
                .limit(10)
            )
            for control in recent_controls_result.scalars().all():
                recent_activity.append({
                    "type": "control_updated",
                    "scf_id": control.scf_id,
                    "organization_id": str(control.organization_id),
                    "status": control.implementation_status,
                    "updated_at": control.updated_at.isoformat() if control.updated_at else None,
                })

        return {
            "total_clients": total_clients,
            "active_clients": len(active_org_ids),
            "pending_invites": pending_invites,
            "total_controls_across_clients": total_controls,
            "implemented_controls_across_clients": implemented_controls,
            "average_framework_readiness": round(total_readiness, 1),
            "controls_by_status": controls_by_status,
            "recent_activity": recent_activity,
        }


# =========================================================================
# Utility Functions
# =========================================================================

async def get_consultant_service(db: AsyncSession) -> ConsultantService:
    """Factory function to create a ConsultantService instance."""
    return ConsultantService(db)
