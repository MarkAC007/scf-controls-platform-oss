"""
Authentication module for CG SCF Backend.
Provides dual-mode authentication: Google OAuth2 JWT or API Key fallback.
Now includes user persistence to database.
"""
from fastapi import HTTPException, Security, status, Request, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime
import os
import logging

# Import database and models
from database import get_db
from services.service_account import get_service_account_id
from services.single_tenant import is_single_tenant_active, single_tenant_org_id
from models import (
    User as DBUser,
    Organization,
    OrganizationMember,
    OrganizationInvite,
    OrgInviteStatus,
    ConsultantInvite,
    ConsultantInviteStatus,
    ConsultantProfile,
    ConsultantClientRelationship,
    UserSubscription,
    ApiKey,
)

# Initialize HTTP Bearer security scheme
security = HTTPBearer()
logger = logging.getLogger(__name__)

# Configuration
GOOGLE_AUTH_ENABLED = os.getenv("GOOGLE_AUTH_ENABLED", "false").lower() == "true"
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
API_KEY = os.getenv("API_KEY")

# Log authentication configuration on startup
if GOOGLE_AUTH_ENABLED:
    logger.info(f"Google authentication ENABLED (Client ID: {GOOGLE_CLIENT_ID[:20]}...)")
else:
    logger.info("Google authentication DISABLED (API key only mode)")


class User:
    """User class representing an authenticated user (in-memory, not persisted).

    The subscription field is lazily loaded via get_subscription() to avoid
    N+1 query problems. Once loaded, it's cached for the request lifetime.
    """
    def __init__(
        self,
        user_id: str,
        email: Optional[str] = None,
        name: Optional[str] = None,
        auth_method: str = "api_key",
        db_id: Optional[str] = None,
        subscription: Optional["UserSubscription"] = None
    ):
        self.user_id = user_id  # Google 'sub' or 'api_user'
        self.email = email
        self.name = name
        self.auth_method = auth_method  # "google" or "api_key"
        self.db_id = db_id  # UUID from database (if persisted)
        self._subscription = subscription  # Lazily loaded subscription

    @property
    def subscription(self) -> Optional["UserSubscription"]:
        """Get the cached subscription (if loaded)."""
        return self._subscription

    @subscription.setter
    def subscription(self, value: "UserSubscription") -> None:
        """Set the subscription cache."""
        self._subscription = value

    def has_subscription(self) -> bool:
        """Check if subscription has been loaded."""
        return self._subscription is not None


async def validate_google_token(token: str, db: AsyncSession) -> User:
    """
    Validate Google OAuth2 access token using tokeninfo endpoint.

    Args:
        token: Google OAuth2 access token

    Returns:
        User: Authenticated user from Google

    Raises:
        HTTPException: If token is invalid
    """
    # First, validate that GOOGLE_CLIENT_ID is configured
    if not GOOGLE_CLIENT_ID:
        logger.error("GOOGLE_CLIENT_ID is not set but Google authentication is enabled")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Google authentication is misconfigured on the server",
        )

    logger.debug(f"Validating Google token (length: {len(token)})")
    logger.debug(f"Expected GOOGLE_CLIENT_ID: {GOOGLE_CLIENT_ID[:20]}...")

    try:
        import httpx

        # Verify the token using Google's tokeninfo endpoint
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f'https://oauth2.googleapis.com/tokeninfo?access_token={token}'
            )

            logger.debug(f"Google tokeninfo response status: {response.status_code}")

            if response.status_code != 200:
                error_detail = response.text
                logger.error(f"Token validation failed with status {response.status_code}: {error_detail}")

                # Check if token is expired (common error from Google)
                if "invalid_token" in error_detail.lower() or "expired" in error_detail.lower():
                    logger.warning("Google access token has expired (tokens expire after ~1 hour)")
                    raise Exception("Token expired. Please sign in again.")
                else:
                    raise Exception(f"Token validation failed: {response.status_code} - {error_detail}")

            token_info = response.json()
            logger.debug(f"Token info keys: {list(token_info.keys())}")

            # Verify the token is for our app with robust audience check
            token_aud = token_info.get('aud')
            logger.debug(f"Token audience (aud): {token_aud}")

            audience_valid = False

            if isinstance(token_aud, list):
                # If aud is a list, check if our client ID is in it
                audience_valid = GOOGLE_CLIENT_ID in token_aud
                logger.debug(f"Audience is list, match found: {audience_valid}")
            elif isinstance(token_aud, str):
                # If aud is a string, check for equality
                audience_valid = token_aud == GOOGLE_CLIENT_ID
                logger.debug(f"Audience is string, match: {audience_valid} (expected: {GOOGLE_CLIENT_ID}, got: {token_aud})")

            if not audience_valid:
                logger.error(f"Audience validation failed. Expected: {GOOGLE_CLIENT_ID}, Got: {token_aud}")
                raise Exception(f"Token is for different client ID. Expected: {GOOGLE_CLIENT_ID}, Got: {token_aud}")

            # Get user info
            logger.debug("Fetching user info from Google")
            user_response = await client.get(
                'https://www.googleapis.com/oauth2/v3/userinfo',
                headers={'Authorization': f'Bearer {token}'}
            )

            logger.debug(f"User info response status: {user_response.status_code}")

            if user_response.status_code != 200:
                error_detail = user_response.text
                logger.error(f"Failed to get user info: {user_response.status_code} - {error_detail}")
                raise Exception(f"Failed to get user info: {error_detail}")

            user_info = user_response.json()
            logger.debug(f"User info received: {user_info.get('email', 'no-email')}")

        # Extract user information
        # The 'sub' claim is required for user identification
        google_sub = user_info.get('sub')
        if not google_sub:
            logger.error("Missing 'sub' claim in user info response")
            raise Exception("Missing required 'sub' claim in user info response")

        email = user_info.get('email')
        display_name = user_info.get('name')

        logger.info(f"✅ Successfully validated Google token for user: {email}")

        # Persist user to database (create or update)
        # WEBSITE-FIRST PROVISIONING: Users must sign up via the marketing website first.
        # The website creates a provisioned user with google_sub = "pending:{email}"
        # When they first log into the platform, we link their Google account.
        try:
            # Check if user exists by google_sub (already logged in before)
            result = await db.execute(
                select(DBUser).where(DBUser.google_sub == google_sub)
            )
            db_user = result.scalar_one_or_none()

            if db_user:
                # Update existing user (returning user)
                db_user.email = email
                db_user.display_name = display_name
                db_user.last_login_at = datetime.utcnow()
                logger.debug(f"Updated existing user {db_user.id}")
            else:
                # New Google login - check if this user is provisioned from the website
                # Website creates users with google_sub = "pending:{email}"
                provisioned_result = await db.execute(
                    select(DBUser).where(DBUser.google_sub == f"pending:{email}")
                )
                provisioned_user = provisioned_result.scalar_one_or_none()

                if provisioned_user:
                    # WEBSITE-PROVISIONED USER: Link their Google account
                    provisioned_user.google_sub = google_sub
                    provisioned_user.email = email
                    provisioned_user.display_name = display_name
                    provisioned_user.last_login_at = datetime.utcnow()
                    db_user = provisioned_user
                    logger.info(f"Linked provisioned user {email} to Google account")
                else:
                    # NOT PROVISIONED via website — check if they have a pending
                    # invitation (consultant OR org-member).  If so, auto-provision
                    # the account so the invite-acceptance flow can proceed without
                    # requiring the user to sign up through the marketing website.
                    pending_invite_result = await db.execute(
                        select(ConsultantInvite).where(
                            (ConsultantInvite.email == email) &
                            (ConsultantInvite.status == ConsultantInviteStatus.PENDING.value)
                        )
                    )
                    pending_consultant_invite = pending_invite_result.scalar_one_or_none()

                    # Also check for pending org-member invites (e.g. consultant
                    # invited an admin to a client organisation via User Management)
                    pending_org_invite_result = await db.execute(
                        select(OrganizationInvite).where(
                            (OrganizationInvite.email == email) &
                            (OrganizationInvite.status == OrgInviteStatus.PENDING.value)
                        )
                    )
                    pending_org_invite = pending_org_invite_result.scalar_one_or_none()

                    has_valid_invite = (
                        (pending_consultant_invite and not pending_consultant_invite.is_expired()) or
                        (pending_org_invite and not pending_org_invite.is_expired())
                    )

                    if has_valid_invite:
                        # Auto-provision: create a minimal user record so they can
                        # accept the invite.  They get a free-tier subscription by
                        # default; the consultant-invite acceptance flow will attach
                        # them to the correct organisation.
                        invite_type = "consultant" if pending_consultant_invite else "org-member"
                        logger.info(f"Auto-provisioning user {email} — has pending {invite_type} invite")
                        db_user = DBUser(
                            google_sub=google_sub,
                            email=email,
                            display_name=display_name,
                            last_login_at=datetime.utcnow(),
                        )
                        db.add(db_user)
                        await db.flush()

                        # Create a default free-tier subscription
                        subscription = UserSubscription(
                            user_id=db_user.id,
                            tier="free",
                            is_active=True,
                        )
                        db.add(subscription)
                    elif os.getenv("OPEN_REGISTRATION", "false").lower() == "true":
                        # OPEN_REGISTRATION mode (Azure / test environments only):
                        # auto-provision user on first Google login without website signup.
                        logger.info(f"Open registration: auto-provisioning user {email}")
                        db_user = DBUser(
                            google_sub=google_sub,
                            email=email,
                            display_name=display_name,
                            last_login_at=datetime.utcnow(),
                        )
                        db.add(db_user)
                        await db.flush()
                        subscription = UserSubscription(
                            user_id=db_user.id,
                            tier="free",
                            is_active=True,
                        )
                        db.add(subscription)
                    else:
                        # No pending invite — block signup (website-first provisioning enforced)
                        logger.warning(f"Direct signup blocked for {email} - not provisioned via website")
                        raise HTTPException(
                            status_code=status.HTTP_403_FORBIDDEN,
                            detail={
                                "error": "account_not_provisioned",
                                "message": "Your account has not been provisioned. Please sign up at scfcontrolsplatform.com first.",
                                "redirect": "https://scfcontrolsplatform.com/signup"
                            }
                        )

            await db.commit()
            await db.refresh(db_user)

            logger.debug(f"User persisted to database with ID: {db_user.id}")

            # WEBSITE-FIRST PROVISIONING: Organisation linking is handled by the website
            # The website sync API creates the user's organisation and membership.
            # We only need to check for pending consultant invites here for the invite flow.

            # Check for pending consultant invites for this user's email
            # If they have pending invites, log it so they can use the invite acceptance flow
            try:
                pending_invite_result = await db.execute(
                    select(ConsultantInvite).where(
                        (ConsultantInvite.email == email) &
                        (ConsultantInvite.status == ConsultantInviteStatus.PENDING.value)
                    )
                )
                pending_invite = pending_invite_result.scalar_one_or_none()
                if pending_invite and not pending_invite.is_expired():
                    logger.info(f"User {email} has pending consultant invite - they can accept it after login")
            except Exception as invite_check_error:
                logger.warning(f"Failed to check for pending invites: {invite_check_error}")
                # Non-fatal - user can still login

        except HTTPException:
            # Re-raise HTTPException (e.g., 403 for non-provisioned users)
            raise
        except Exception as db_error:
            logger.error(f"Failed to persist user to database: {db_error}")
            await db.rollback()
            # Don't fail authentication if DB persist fails - continue with transient user
            db_user = None

        return User(
            user_id=google_sub,
            email=email,
            name=display_name,
            auth_method="google",
            db_id=str(db_user.id) if db_user else None
        )
    except HTTPException:
        # Re-raise HTTPException (e.g., 403 for non-provisioned users)
        # This must come BEFORE the generic Exception handler
        raise
    except Exception as e:
        logger.error(f"❌ Google token validation failed: {type(e).__name__}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid Google authentication token: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def validate_api_key(token: str) -> User:
    """
    Validate API key token.

    Args:
        token: API key token

    Returns:
        User: Authenticated user from API key

    Raises:
        HTTPException: If API key is invalid
    """
    if token != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = User(
        user_id="api_user",
        email="api@odin-scf.local",
        name="API User",
        auth_method="api_key",
    )
    if is_single_tenant_active():
        _svc_id = get_service_account_id()
        if _svc_id:
            user.db_id = _svc_id
    return user


async def validate_user_api_key(token: str, db: AsyncSession) -> User:
    """
    Validate a per-user, per-organisation API key (scf_... prefix).

    Extracts the 8-char prefix, looks up candidate keys, compares
    SHA-256 hashes, checks expiry, updates last_used_at, and returns
    an auth User with scoped organisation and role metadata.
    """
    import hashlib

    prefix = token[:8]
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    # Find active keys matching the prefix
    result = await db.execute(
        select(ApiKey).where(
            (ApiKey.key_prefix == prefix) &
            (ApiKey.is_active == True)  # noqa: E712
        )
    )
    candidates = result.scalars().all()

    matched_key = None
    for candidate in candidates:
        if candidate.key_hash == token_hash:
            matched_key = candidate
            break

    if not matched_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check expiry
    if matched_key.expires_at:
        if datetime.utcnow() > matched_key.expires_at:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API key has expired",
                headers={"WWW-Authenticate": "Bearer"},
            )

    # Update last_used_at
    matched_key.last_used_at = datetime.utcnow()
    await db.commit()

    # Load the owning user
    user_result = await db.execute(
        select(DBUser).where(DBUser.id == matched_key.user_id)
    )
    db_user = user_result.scalar_one_or_none()
    if not db_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key owner not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = User(
        user_id=db_user.google_sub,
        email=db_user.email,
        name=db_user.display_name,
        auth_method="user_api_key",
        db_id=str(db_user.id),
    )
    # Attach scoped org/role metadata for downstream auth checks
    user._api_key_org_id = matched_key.organization_id
    user._api_key_role = matched_key.role

    logger.info(f"✅ User authenticated via user API key: {db_user.email} (org={matched_key.organization_id}, role={matched_key.role})")
    return user


async def require_auth(
    credentials: HTTPAuthorizationCredentials = Security(security),
    db: AsyncSession = Depends(get_db)
) -> User:
    """
    Authentication dependency that requires a valid Bearer token.

    Supports two authentication modes based on GOOGLE_AUTH_ENABLED:

    Mode 1 (GOOGLE_AUTH_ENABLED=true):
      1. Try Google OAuth2 token validation first
      2. Fallback to API key if Google validation fails

    Mode 2 (GOOGLE_AUTH_ENABLED=false):
      - Only validate API key, skip Google entirely

    Args:
        credentials: HTTP Authorization credentials from the request header

    Returns:
        User: Authenticated user object

    Raises:
        HTTPException: If authentication fails
    """
    if not credentials:
        logger.warning("Authentication failed: No credentials provided")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    logger.debug(f"🔐 Auth attempt - Token length: {len(token)}, Google Auth: {GOOGLE_AUTH_ENABLED}")

    # Try Google Auth first if enabled
    if GOOGLE_AUTH_ENABLED:
        logger.debug("Attempting Google token validation...")
        try:
            user = await validate_google_token(token, db)
            logger.info(f"✅ User authenticated via Google: {user.email}")
            return user
        except HTTPException as e:
            # 403 = account not provisioned - DO NOT fall back to API key
            # This must be returned to the frontend for proper redirect handling
            if e.status_code == status.HTTP_403_FORBIDDEN:
                logger.warning(f"Account not provisioned, returning 403: {e.detail}")
                raise
            # Other errors (401, 500, etc.) - try API key fallback
            logger.warning(f"Google auth failed (will try API key fallback): {e.detail}")
            pass

    # Fallback to static API key validation
    logger.debug("Attempting static API key validation...")
    try:
        user = await validate_api_key(token)
        logger.info("✅ User authenticated via static API key")
        return user
    except HTTPException:
        logger.debug("Static API key validation failed, trying user API key...")

    # Fallback to per-user API key (DB lookup)
    try:
        user = await validate_user_api_key(token, db)
        return user
    except HTTPException:
        pass

    # All methods failed
    logger.error("❌ All authentication methods failed")
    if GOOGLE_AUTH_ENABLED:
        detail = "Invalid authentication token. Provide a valid Google JWT, API key, or user API key."
    else:
        detail = "Invalid API key. Google Auth is not configured."

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


# Alias for dependency injection - same as require_auth but named for clarity
get_current_user = require_auth


async def load_user_subscription(user: User, db: AsyncSession) -> User:
    """
    Load subscription context for an authenticated user.

    This function populates the user's subscription field from the database,
    avoiding N+1 query problems by loading it once per request.

    If no subscription exists, a free tier subscription is auto-created.

    Args:
        user: Authenticated user object
        db: Database session

    Returns:
        User: Same user object with subscription populated
    """
    # API key users don't have subscriptions - skip loading
    if user.auth_method == "api_key" or not user.db_id:
        return user

    # Already loaded - return immediately
    if user.has_subscription():
        return user

    from uuid import UUID
    from services.subscription import get_user_subscription

    try:
        subscription = await get_user_subscription(UUID(user.db_id), db)
        user.subscription = subscription
        logger.debug(f"Loaded subscription for user {user.email}: tier={subscription.tier}")
    except Exception as e:
        logger.warning(f"Failed to load subscription for user {user.email}: {e}")
        # Don't fail auth if subscription loading fails

    return user


async def require_auth_with_subscription(
    credentials: HTTPAuthorizationCredentials = Security(security),
    db: AsyncSession = Depends(get_db)
) -> User:
    """
    Authentication dependency that also loads subscription context.

    Use this for endpoints that need subscription data (e.g., for limit checks).
    The subscription is available via user.subscription after authentication.

    Args:
        credentials: HTTP Authorization credentials from the request header
        db: Database session

    Returns:
        User: Authenticated user with subscription loaded

    Raises:
        HTTPException: If authentication fails
    """
    user = await require_auth(credentials, db)
    return await load_user_subscription(user, db)


async def require_admin(
    credentials: HTTPAuthorizationCredentials = Security(security),
    db: AsyncSession = Depends(get_db)
) -> User:
    """
    Authentication dependency that requires a valid Bearer token AND admin role.

    This is used for sensitive operations like database backup/restore that should
    only be accessible to administrators.

    The user must be an admin of at least one organization to access admin-only endpoints.

    Args:
        credentials: HTTP Authorization credentials from the request header
        db: Database session

    Returns:
        User: Authenticated admin user object

    Raises:
        HTTPException: If authentication fails or user is not an admin
    """
    # First, authenticate the user
    user = await require_auth(credentials, db)

    # Static API key users are considered admins (for automation/CI purposes)
    if user.auth_method == "api_key":
        logger.info("Admin access granted via static API key")
        return user

    # User API key — check the frozen role on the key
    if user.auth_method == "user_api_key":
        if getattr(user, '_api_key_role', None) == "admin":
            logger.info(f"Admin access granted via user API key for {user.email}")
            return user
        logger.warning(f"Admin access denied for user API key {user.email} (role={getattr(user, '_api_key_role', '?')})")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access denied: Your API key does not have admin role",
        )

    # For Google-authenticated users, check if they're an admin of any organization
    if not user.db_id:
        logger.warning(f"User {user.email} has no database ID - denying admin access")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access denied: User account not fully provisioned",
        )

    # Check for admin role in any organization
    from uuid import UUID
    admin_check = await db.execute(
        select(OrganizationMember).where(
            (OrganizationMember.user_id == UUID(user.db_id)) &
            (OrganizationMember.role == "admin")
        )
    )
    is_admin = admin_check.scalar_one_or_none() is not None

    if not is_admin:
        logger.warning(f"Admin access denied for user {user.email} - not an admin of any organization")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access denied: This operation requires administrator privileges",
        )

    logger.info(f"Admin access granted for user {user.email}")
    return user


async def require_platform_admin(
    credentials: HTTPAuthorizationCredentials = Security(security),
    db: AsyncSession = Depends(get_db)
) -> User:
    """
    Authentication dependency that requires platform admin privileges.

    Platform admin is a cross-organisation role that grants access to:
    - View/manage all users across all organisations
    - View/manage all organisations
    - Grant/revoke platform admin privileges

    This is distinct from organisation-level admin (OrganizationMember.role='admin'),
    which only grants admin access within a specific organisation.

    Args:
        credentials: HTTP Authorization credentials from the request header
        db: Database session

    Returns:
        User: Authenticated platform admin user object

    Raises:
        HTTPException: If authentication fails or user is not a platform admin
    """
    # First, authenticate the user
    user = await require_auth(credentials, db)

    # Static API key users are considered platform admins (for automation/CI purposes)
    if user.auth_method == "api_key":
        logger.info("Platform admin access granted via static API key")
        return user

    # User API key — must check the actual DB user's is_platform_admin flag
    if user.auth_method == "user_api_key":
        if not user.db_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Platform admin access denied: User account not fully provisioned",
            )
        from uuid import UUID as _UUID
        pa_check = await db.execute(
            select(DBUser).where(
                (DBUser.id == _UUID(user.db_id)) &
                (DBUser.is_platform_admin == True)  # noqa: E712
            )
        )
        if pa_check.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Platform admin access denied: Your user account is not a platform administrator",
            )
        logger.info(f"Platform admin access granted via user API key for {user.email}")
        return user

    # For Google-authenticated users, check the is_platform_admin flag
    if not user.db_id:
        logger.warning(f"User {user.email} has no database ID - denying platform admin access")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Platform admin access denied: User account not fully provisioned",
        )

    # Check the is_platform_admin flag on the user record
    from uuid import UUID
    platform_admin_check = await db.execute(
        select(DBUser).where(
            (DBUser.id == UUID(user.db_id)) &
            (DBUser.is_platform_admin == True)  # noqa: E712
        )
    )
    is_platform_admin = platform_admin_check.scalar_one_or_none() is not None

    if not is_platform_admin:
        logger.warning(f"Platform admin access denied for user {user.email}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Platform admin access denied: This operation requires platform administrator privileges",
        )

    logger.info(f"Platform admin access granted for user {user.email}")
    return user


async def optional_auth(request: Request) -> Optional[User]:
    """
    Optional authentication dependency that allows unauthenticated access.

    Returns:
        Optional[User]: Authenticated user object if valid credentials provided, None otherwise
    """
    # Check if Authorization header is present
    authorization = request.headers.get("Authorization")
    if not authorization:
        return None

    # Try to extract and validate the token
    try:
        scheme, credentials = authorization.split()
        if scheme.lower() != "bearer":
            return None

        # Create credentials object manually
        from fastapi.security import HTTPAuthorizationCredentials
        creds = HTTPAuthorizationCredentials(scheme=scheme, credentials=credentials)

        return await require_auth(creds)
    except (ValueError, HTTPException):
        return None


# =============================================================================
# Organization Membership Authorization
# =============================================================================

from dataclasses import dataclass
from uuid import UUID
from typing import Literal

# Role hierarchy: admin > editor > viewer
ROLE_HIERARCHY = {"admin": 3, "editor": 2, "viewer": 1}


@dataclass
class OrgMembership:
    """Represents a user's membership in an organization.

    Users can access an organisation via two paths:
    1. Direct membership - OrganizationMember table (client admins, team members)
    2. Consultant relationship - ConsultantClientRelationship table (consultants)

    The is_consultant field distinguishes between these access paths.
    """
    user: User
    organization_id: UUID
    role: str  # "admin", "editor", or "viewer"
    is_consultant: bool = False  # True if access is via ConsultantClientRelationship

    def has_role(self, min_role: str) -> bool:
        """Check if user has at least the specified role level."""
        return ROLE_HIERARCHY.get(self.role, 0) >= ROLE_HIERARCHY.get(min_role, 0)

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_editor(self) -> bool:
        return self.role in ("admin", "editor")

    @property
    def is_viewer(self) -> bool:
        return self.role in ("admin", "editor", "viewer")


async def verify_org_membership(
    org_id: UUID,
    user: User,
    db: AsyncSession,
    min_role: str = "viewer"
) -> OrgMembership:
    """
    Verify that a user has access to an organization with sufficient role.

    Users can access an organisation via TWO paths:
    1. Direct Membership - OrganizationMember table
       Used for: Client admins, client team members
    2. Consultant Relationship - ConsultantClientRelationship table
       Used for: Consultants managing client organisations (must be 'active' status)

    Args:
        org_id: Organization UUID to check access for
        user: Authenticated user
        db: Database session
        min_role: Minimum required role ("viewer", "editor", or "admin")

    Returns:
        OrgMembership: The user's membership details (includes is_consultant flag)

    Raises:
        HTTPException: 403 if user has no access or lacks required role
        HTTPException: 404 if organization doesn't exist
    """
    # Static API key: full org access in single-tenant mode only (security: prevents IDOR in multi-tenant/prod)
    if user.auth_method == "api_key":
        if is_single_tenant_active() and (single_tenant_org_id() is None or str(org_id) == single_tenant_org_id()):
            logger.debug(f"Static API key granted {min_role} on org {org_id} (single-tenant, pinned)")
            return OrgMembership(user=user, organization_id=org_id, role="admin", is_consultant=False)
        else:
            logger.critical(
                f"SECURITY: Static API key attempted org-scoped access to {org_id} in "
                f"{os.getenv('ENVIRONMENT', 'production')} — denied. Use per-org API keys instead."
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Static API key cannot access organization-scoped endpoints in this environment. Use a per-organization API key.",
            )

    # User API key — scoped to a single organisation with frozen role
    if user.auth_method == "user_api_key":
        api_key_org_id = getattr(user, '_api_key_org_id', None)
        api_key_role = getattr(user, '_api_key_role', None)

        if api_key_org_id is None or api_key_role is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: Malformed API key metadata",
            )

        # Enforce single-org scoping
        if UUID(str(api_key_org_id)) != org_id:
            logger.warning(
                f"API key org mismatch: key scoped to {api_key_org_id}, request for {org_id}"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: API key is not scoped to this organisation",
            )

        # Check role level
        user_role_level = ROLE_HIERARCHY.get(api_key_role, 0)
        required_role_level = ROLE_HIERARCHY.get(min_role, 0)
        if user_role_level < required_role_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied: API key role '{api_key_role}' insufficient, requires '{min_role}'",
            )

        logger.debug(
            f"Access granted (user API key): {user.email} ({api_key_role}) "
            f"accessing org {org_id} (required: {min_role})"
        )
        return OrgMembership(user=user, organization_id=org_id, role=api_key_role, is_consultant=False)

    # User must have a database ID for membership checks
    if not user.db_id:
        logger.warning(f"User {user.email} has no database ID - cannot verify org membership")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: User account not fully provisioned",
        )

    user_uuid = UUID(user.db_id)

    # Check organization exists
    org_result = await db.execute(
        select(Organization).where(Organization.id == org_id)
    )
    org = org_result.scalar_one_or_none()
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    # Path 1: Check direct membership (OrganizationMember)
    membership_result = await db.execute(
        select(OrganizationMember).where(
            (OrganizationMember.organization_id == org_id) &
            (OrganizationMember.user_id == user_uuid)
        )
    )
    membership = membership_result.scalar_one_or_none()

    if membership:
        # Check role level for direct membership
        user_role_level = ROLE_HIERARCHY.get(membership.role, 0)
        required_role_level = ROLE_HIERARCHY.get(min_role, 0)

        if user_role_level < required_role_level:
            logger.warning(
                f"Access denied: User {user.email} has role '{membership.role}' "
                f"but '{min_role}' is required for organization {org_id}"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied: This action requires '{min_role}' role or higher",
            )

        logger.debug(
            f"Access granted (direct member): User {user.email} ({membership.role}) "
            f"accessing organization {org_id} (required: {min_role})"
        )

        return OrgMembership(
            user=user,
            organization_id=org_id,
            role=membership.role,
            is_consultant=False
        )

    # Path 2: Check consultant relationship (ConsultantClientRelationship)
    # Must have an active status to access
    consultant_result = await db.execute(
        select(ConsultantClientRelationship)
        .join(ConsultantProfile, ConsultantClientRelationship.consultant_id == ConsultantProfile.id)
        .where(
            (ConsultantClientRelationship.organization_id == org_id) &
            (ConsultantProfile.user_id == user_uuid) &
            (ConsultantClientRelationship.status == "active")
        )
    )
    consultant_rel = consultant_result.scalar_one_or_none()

    if consultant_rel:
        # Check role level for consultant relationship
        user_role_level = ROLE_HIERARCHY.get(consultant_rel.role, 0)
        required_role_level = ROLE_HIERARCHY.get(min_role, 0)

        if user_role_level < required_role_level:
            logger.warning(
                f"Access denied: Consultant {user.email} has role '{consultant_rel.role}' "
                f"but '{min_role}' is required for organization {org_id}"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied: This action requires '{min_role}' role or higher",
            )

        logger.debug(
            f"Access granted (consultant): User {user.email} ({consultant_rel.role}) "
            f"accessing organization {org_id} (required: {min_role})"
        )

        return OrgMembership(
            user=user,
            organization_id=org_id,
            role=consultant_rel.role,
            is_consultant=True
        )

    # No access via either path
    logger.warning(
        f"Access denied: User {user.email} has no membership or active consultant "
        f"relationship with organization {org_id}"
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Access denied: You are not a member of this organization",
    )


async def get_accessible_org_ids(
    user: User,
    db: AsyncSession
) -> list[UUID]:
    """
    Get all organization IDs the user can access.

    Returns organisations accessible via:
    1. Direct membership (OrganizationMember)
    2. Active consultant relationships (ConsultantClientRelationship with status='active')

    Args:
        user: Authenticated user
        db: Database session

    Returns:
        List of organization UUIDs the user can access
    """
    # Static API key: all orgs in single-tenant mode only (security: prevents IDOR in multi-tenant/prod)
    if user.auth_method == "api_key":
        if is_single_tenant_active():
            result = await db.execute(select(Organization.id))
            return [row[0] for row in result.fetchall()]
        else:
            logger.warning("Static API key cannot enumerate organizations in non-dev environment")
            return []

    # User API key — scoped to a single organisation
    if user.auth_method == "user_api_key":
        api_key_org_id = getattr(user, '_api_key_org_id', None)
        if api_key_org_id:
            return [api_key_org_id]
        return []

    # User must have a database ID
    if not user.db_id:
        return []

    user_uuid = UUID(user.db_id)
    org_ids = set()

    # Path 1: Direct memberships
    member_result = await db.execute(
        select(OrganizationMember.organization_id).where(
            OrganizationMember.user_id == user_uuid
        )
    )
    for row in member_result.fetchall():
        org_ids.add(row[0])

    # Path 2: Active consultant relationships
    consultant_result = await db.execute(
        select(ConsultantClientRelationship.organization_id)
        .join(ConsultantProfile, ConsultantClientRelationship.consultant_id == ConsultantProfile.id)
        .where(
            (ConsultantProfile.user_id == user_uuid) &
            (ConsultantClientRelationship.status == "active")
        )
    )
    for row in consultant_result.fetchall():
        org_ids.add(row[0])

    return list(org_ids)


# =============================================================================
# Organization-scoped dependency factories
# =============================================================================

def require_org_role(min_role: Literal["viewer", "editor", "admin"] = "viewer"):
    """
    Factory function that creates a dependency requiring organization membership.

    Usage in endpoint:
        @router.get("/organizations/{org_id}/data")
        async def get_data(
            org_id: UUID,
            membership: OrgMembership = Depends(require_org_role("viewer")),
            db: AsyncSession = Depends(get_db)
        ):
            # membership.user, membership.organization_id, membership.role available
            ...

    Args:
        min_role: Minimum required role ("viewer", "editor", or "admin")

    Returns:
        A FastAPI dependency function
    """
    async def dependency(
        org_id: UUID,
        credentials: HTTPAuthorizationCredentials = Security(security),
        db: AsyncSession = Depends(get_db)
    ) -> OrgMembership:
        user = await require_auth(credentials, db)
        return await verify_org_membership(org_id, user, db, min_role)

    return dependency


# Pre-built dependencies for common use cases
require_org_viewer = require_org_role("viewer")
require_org_editor = require_org_role("editor")
require_org_admin = require_org_role("admin")
