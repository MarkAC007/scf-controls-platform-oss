"""
Subscription Service - Business logic for subscription tier management.

This service handles:
- Subscription lookup and auto-provisioning
- Organisation limit checking
- Team member limit checking
- Feature flag evaluation per subscription tier

Design Considerations:
- Auto-provisioning: If a user has no subscription, a free tier is created
- Thread safety: Uses database operations, safe for concurrent access
- Idempotent: Multiple calls to get_user_subscription return the same result
- Feature flags: Derived from tier at runtime; no extra DB columns required
"""
import logging
from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from models import UserSubscription, SubscriptionTier

logger = logging.getLogger(__name__)

# Default subscription tier limits
# Platform-native tiers
DEFAULT_TIER_LIMITS = {
    SubscriptionTier.FREE.value: {
        "max_organisations": 1,
        "max_team_members": 5,
    },
    SubscriptionTier.PROFESSIONAL.value: {
        "max_organisations": 10,
        "max_team_members": 50,
    },
    SubscriptionTier.ENTERPRISE.value: {
        "max_organisations": 999,
        "max_team_members": 999,
    },
    # Website tier aliases (synced from marketing site)
    # These map to the tier names used in Stripe products
    "pro": {
        "max_organisations": 1,
        "max_team_members": 10,
    },
    "consultant": {
        "max_organisations": 5,
        "max_team_members": 25,
    },
    "custom": {
        "max_organisations": 999,
        "max_team_members": 999,
    },
}

# Feature flags per subscription tier.
# Keys map to the feature names accepted by has_feature().
# Platform-native tiers
TIER_FEATURES: dict[str, dict[str, bool]] = {
    SubscriptionTier.FREE.value: {
        "api_access": False,
        "sso": False,
    },
    SubscriptionTier.PROFESSIONAL.value: {
        "api_access": True,
        "sso": False,
    },
    SubscriptionTier.ENTERPRISE.value: {
        "api_access": True,
        "sso": True,
    },
    # Website tier aliases
    "pro": {
        "api_access": True,
        "sso": False,
    },
    "consultant": {
        "api_access": True,
        "sso": False,
    },
    "custom": {
        "api_access": True,
        "sso": True,
    },
}

# Auth methods that represent machine/automation access and bypass feature checks.
_API_KEY_AUTH_METHODS = {"api_key", "user_api_key"}


async def get_user_subscription(user_id: UUID, db: AsyncSession) -> UserSubscription:
    """
    Get subscription for a user, creating a free tier if none exists.

    This function is idempotent - calling it multiple times for the same user
    will return the same subscription (or create one on first call).

    Args:
        user_id: The user's UUID
        db: Database session

    Returns:
        UserSubscription: The user's subscription (existing or newly created)
    """
    # Look up existing subscription
    result = await db.execute(
        select(UserSubscription).where(UserSubscription.user_id == user_id)
    )
    subscription = result.scalar_one_or_none()

    if subscription:
        logger.debug(f"Found existing subscription for user {user_id}: tier={subscription.tier}")
        return subscription

    # No subscription found - create free tier
    logger.info(f"Creating free tier subscription for user {user_id}")
    free_limits = DEFAULT_TIER_LIMITS[SubscriptionTier.FREE.value]
    subscription = UserSubscription(
        user_id=user_id,
        tier=SubscriptionTier.FREE.value,
        max_organisations=free_limits["max_organisations"],
        max_team_members=free_limits["max_team_members"],
        is_active=True,
    )
    db.add(subscription)
    await db.commit()
    await db.refresh(subscription)

    return subscription


def can_create_organisation(subscription: UserSubscription, current_count: int) -> bool:
    """
    Check if user can create another organisation based on subscription limits.

    Args:
        subscription: The user's subscription
        current_count: Number of organisations the user currently owns

    Returns:
        bool: True if user can create another organisation, False otherwise

    Examples:
        >>> sub = UserSubscription(tier='free', max_organisations=1, is_active=True)
        >>> can_create_organisation(sub, 0)
        True
        >>> can_create_organisation(sub, 1)
        False
    """
    if subscription is None:
        logger.warning("No subscription provided - denying organisation creation")
        return False

    if not subscription.is_active:
        logger.debug(f"Subscription inactive - denying organisation creation")
        return False

    if current_count < 0:
        logger.warning(f"Negative current_count ({current_count}) - treating as 0")
        current_count = 0

    allowed = current_count < subscription.max_organisations
    logger.debug(
        f"Organisation limit check: current={current_count}, "
        f"max={subscription.max_organisations}, allowed={allowed}"
    )
    return allowed


def can_invite_member(subscription: UserSubscription, current_count: int) -> bool:
    """
    Check if user can invite another team member based on subscription limits.

    Args:
        subscription: The user's subscription
        current_count: Number of team members currently in the organisation

    Returns:
        bool: True if user can invite another member, False otherwise

    Examples:
        >>> sub = UserSubscription(tier='free', max_team_members=5, is_active=True)
        >>> can_invite_member(sub, 4)
        True
        >>> can_invite_member(sub, 5)
        False
    """
    if subscription is None:
        logger.warning("No subscription provided - denying member invitation")
        return False

    if not subscription.is_active:
        logger.debug(f"Subscription inactive - denying member invitation")
        return False

    if current_count < 0:
        logger.warning(f"Negative current_count ({current_count}) - treating as 0")
        current_count = 0

    allowed = current_count < subscription.max_team_members
    logger.debug(
        f"Team member limit check: current={current_count}, "
        f"max={subscription.max_team_members}, allowed={allowed}"
    )
    return allowed


def get_tier_limits(tier: str) -> dict:
    """
    Get the default limits for a subscription tier.

    Args:
        tier: The tier name ('free', 'professional', 'enterprise')

    Returns:
        dict: Dictionary with 'max_organisations' and 'max_team_members'

    Raises:
        ValueError: If tier is not valid
    """
    if tier not in DEFAULT_TIER_LIMITS:
        valid_tiers = list(DEFAULT_TIER_LIMITS.keys())
        raise ValueError(f"Invalid tier '{tier}'. Valid tiers: {valid_tiers}")

    return DEFAULT_TIER_LIMITS[tier].copy()


def has_feature(
    subscription: Optional[UserSubscription],
    feature: str,
    auth_method: str = "google",
) -> bool:
    """
    Check if a subscription has access to a feature flag.

    API key auth methods (``api_key`` and ``user_api_key``) always bypass
    feature checks so that machine/automation callers are never blocked by
    tier restrictions.

    Args:
        subscription: The user's subscription, or None.
        feature: Feature name to check.  Supported values: ``'api_access'``,
            ``'sso'``.
        auth_method: The authentication method used for the current request.
            Defaults to ``'google'``.  Pass ``'api_key'`` or
            ``'user_api_key'`` to bypass all feature checks.

    Returns:
        bool: True if the subscription grants access to the feature.

    Examples:
        >>> sub = UserSubscription(tier='enterprise', is_active=True)
        >>> has_feature(sub, 'api_access')
        True
        >>> has_feature(sub, 'sso')
        True
        >>> sub_free = UserSubscription(tier='free', is_active=True)
        >>> has_feature(sub_free, 'api_access')
        False
        >>> has_feature(sub_free, 'api_access', auth_method='api_key')
        True
    """
    # API key callers bypass all feature checks (automation support)
    if auth_method in _API_KEY_AUTH_METHODS:
        logger.debug(
            "Feature check '%s' bypassed: auth_method=%s", feature, auth_method
        )
        return True

    if subscription is None:
        logger.warning(
            "Feature check '%s' denied: no subscription provided", feature
        )
        return False

    if not subscription.is_active:
        logger.debug(
            "Feature check '%s' denied: subscription inactive", feature
        )
        return False

    tier_flags = TIER_FEATURES.get(subscription.tier)
    if tier_flags is None:
        logger.warning(
            "Feature check '%s' denied: unknown tier '%s'", feature, subscription.tier
        )
        return False

    allowed = tier_flags.get(feature, False)
    logger.debug(
        "Feature check '%s': tier=%s allowed=%s", feature, subscription.tier, allowed
    )
    return allowed
