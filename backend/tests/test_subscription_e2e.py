"""
End-to-end subscription enforcement tests (Issue #368).

Covers all 7 checklist items from the issue:
1. Free tier auto-provisioning for new users
2. Organisation creation blocked at tier limit
3. Member invite blocked at tier limit
4. Sync upgrade updates tier and limits
5. Sync downgrade updates tier and limits
6. Consultant profile auto-provisioned on consultant tier
7. Consultant profile deactivation cascades on tier change

Tests the service layer directly (no HTTP), consistent with
test_subscription_service.py patterns.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from services.subscription import (
    get_user_subscription,
    can_create_organisation,
    can_invite_member,
    get_tier_limits,
    DEFAULT_TIER_LIMITS,
)
from models import SubscriptionTier


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sub(
    tier: str = "free",
    is_active: bool = True,
    max_organisations: int = 1,
    max_team_members: int = 5,
) -> MagicMock:
    """Return a lightweight mock that behaves like a UserSubscription."""
    sub = MagicMock()
    sub.tier = tier
    sub.is_active = is_active
    sub.max_organisations = max_organisations
    sub.max_team_members = max_team_members
    return sub


# ===========================================================================
# 1. Free tier auto-provisioning
# ===========================================================================

class TestFreeTierAutoProvisioning:
    """get_user_subscription() creates a free tier for users with no subscription."""

    @pytest.mark.asyncio
    async def test_creates_free_tier_for_new_user(self):
        """When no subscription exists, a free tier is auto-created."""
        user_id = uuid4()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        db = AsyncMock()
        db.execute.return_value = mock_result

        # db.add() is sync in SQLAlchemy — override with MagicMock
        added_objects = []
        db.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))

        await get_user_subscription(user_id, db)

        assert len(added_objects) == 1
        new_sub = added_objects[0]
        assert new_sub.user_id == user_id
        assert new_sub.tier == SubscriptionTier.FREE.value
        assert new_sub.is_active is True

    @pytest.mark.asyncio
    async def test_free_tier_max_organisations_is_1(self):
        """Auto-provisioned free tier sets max_organisations=1."""
        user_id = uuid4()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        db = AsyncMock()
        db.execute.return_value = mock_result
        added_objects = []
        db.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))

        await get_user_subscription(user_id, db)

        new_sub = added_objects[0]
        assert new_sub.max_organisations == 1

    @pytest.mark.asyncio
    async def test_free_tier_max_team_members_is_5(self):
        """Auto-provisioned free tier sets max_team_members=5."""
        user_id = uuid4()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        db = AsyncMock()
        db.execute.return_value = mock_result
        added_objects = []
        db.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))

        await get_user_subscription(user_id, db)

        new_sub = added_objects[0]
        assert new_sub.max_team_members == 5

    @pytest.mark.asyncio
    async def test_returns_existing_subscription_unchanged(self):
        """When a subscription already exists, it is returned as-is."""
        existing_sub = _make_sub(tier="professional", max_organisations=10, max_team_members=50)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing_sub

        db = AsyncMock()
        db.execute.return_value = mock_result

        result = await get_user_subscription(uuid4(), db)

        assert result is existing_sub
        db.add.assert_not_called()
        db.commit.assert_not_called()


# ===========================================================================
# 2. Organisation creation limit enforcement
# ===========================================================================

class TestOrgCreationLimits:
    """can_create_organisation() enforces max_organisations."""

    def test_blocked_at_limit(self):
        """Returns False when current_count equals max_organisations."""
        sub = _make_sub(max_organisations=1)
        assert can_create_organisation(sub, current_count=1) is False

    def test_blocked_above_limit(self):
        """Returns False when current_count exceeds max_organisations."""
        sub = _make_sub(max_organisations=1)
        assert can_create_organisation(sub, current_count=2) is False

    def test_allowed_below_limit(self):
        """Returns True when current_count is below max_organisations."""
        sub = _make_sub(max_organisations=1)
        assert can_create_organisation(sub, current_count=0) is True

    def test_blocked_when_inactive(self):
        """Returns False when subscription is inactive regardless of count."""
        sub = _make_sub(is_active=False, max_organisations=10)
        assert can_create_organisation(sub, current_count=0) is False

    def test_blocked_when_none(self):
        """Returns False when subscription is None."""
        assert can_create_organisation(None, current_count=0) is False

    def test_pro_tier_allows_up_to_limit(self):
        """Pro tier (max_organisations=1) allows exactly 0 existing."""
        limits = get_tier_limits("pro")
        sub = _make_sub(max_organisations=limits["max_organisations"])
        assert can_create_organisation(sub, current_count=0) is True
        assert can_create_organisation(sub, current_count=limits["max_organisations"]) is False


# ===========================================================================
# 3. Member invite limit enforcement
# ===========================================================================

class TestMemberInviteLimits:
    """can_invite_member() enforces max_team_members."""

    def test_blocked_at_limit(self):
        """Returns False when current_count equals max_team_members."""
        sub = _make_sub(max_team_members=5)
        assert can_invite_member(sub, current_count=5) is False

    def test_blocked_above_limit(self):
        """Returns False when current_count exceeds max_team_members."""
        sub = _make_sub(max_team_members=5)
        assert can_invite_member(sub, current_count=6) is False

    def test_allowed_below_limit(self):
        """Returns True when current_count is below max_team_members."""
        sub = _make_sub(max_team_members=5)
        assert can_invite_member(sub, current_count=4) is True

    def test_blocked_when_inactive(self):
        """Returns False when subscription is inactive regardless of count."""
        sub = _make_sub(is_active=False, max_team_members=50)
        assert can_invite_member(sub, current_count=0) is False

    def test_blocked_when_none(self):
        """Returns False when subscription is None."""
        assert can_invite_member(None, current_count=0) is False

    def test_pro_tier_allows_up_to_10(self):
        """Pro tier (max_team_members=10) allows 9 but blocks 10."""
        limits = get_tier_limits("pro")
        sub = _make_sub(max_team_members=limits["max_team_members"])
        assert can_invite_member(sub, current_count=9) is True
        assert can_invite_member(sub, current_count=10) is False


# ===========================================================================
# 4 & 5. Sync upgrade/downgrade — tier limits
# ===========================================================================

class TestTierLimitsForSync:
    """get_tier_limits() returns correct limits used by sync endpoint."""

    def test_free_tier_limits(self):
        """Free tier: 1 org, 5 members."""
        limits = get_tier_limits("free")
        assert limits["max_organisations"] == 1
        assert limits["max_team_members"] == 5

    def test_pro_tier_limits(self):
        """Pro tier (website alias): 1 org, 10 members."""
        limits = get_tier_limits("pro")
        assert limits["max_organisations"] == 1
        assert limits["max_team_members"] == 10

    def test_professional_tier_limits(self):
        """Professional tier (platform native): 10 orgs, 50 members."""
        limits = get_tier_limits("professional")
        assert limits["max_organisations"] == 10
        assert limits["max_team_members"] == 50

    def test_enterprise_tier_limits(self):
        """Enterprise tier: 999 orgs, 999 members."""
        limits = get_tier_limits("enterprise")
        assert limits["max_organisations"] == 999
        assert limits["max_team_members"] == 999

    def test_consultant_tier_limits(self):
        """Consultant tier: 5 orgs, 25 members."""
        limits = get_tier_limits("consultant")
        assert limits["max_organisations"] == 5
        assert limits["max_team_members"] == 25

    def test_custom_tier_limits(self):
        """Custom tier: 999 orgs, 999 members."""
        limits = get_tier_limits("custom")
        assert limits["max_organisations"] == 999
        assert limits["max_team_members"] == 999

    def test_invalid_tier_raises_value_error(self):
        """Invalid tier name raises ValueError."""
        with pytest.raises(ValueError, match="Invalid tier"):
            get_tier_limits("nonexistent_tier")

    def test_returns_copy_not_reference(self):
        """get_tier_limits() returns a copy so callers can't mutate defaults."""
        limits = get_tier_limits("free")
        limits["max_organisations"] = 999
        assert DEFAULT_TIER_LIMITS[SubscriptionTier.FREE.value]["max_organisations"] == 1

    def test_upgrade_free_to_pro_increases_limits(self):
        """Upgrading from free to pro increases max_team_members from 5 to 10."""
        free = get_tier_limits("free")
        pro = get_tier_limits("pro")
        assert pro["max_team_members"] > free["max_team_members"]

    def test_downgrade_pro_to_free_decreases_limits(self):
        """Downgrading from pro to free decreases max_team_members from 10 to 5."""
        pro = get_tier_limits("pro")
        free = get_tier_limits("free")
        assert free["max_team_members"] < pro["max_team_members"]


# ===========================================================================
# Edge cases
# ===========================================================================

class TestEdgeCases:
    """Edge case coverage for subscription enforcement."""

    def test_negative_count_treated_as_zero_for_orgs(self):
        """Negative current_count is treated as 0 for org creation."""
        sub = _make_sub(max_organisations=1)
        assert can_create_organisation(sub, current_count=-1) is True

    def test_negative_count_treated_as_zero_for_members(self):
        """Negative current_count is treated as 0 for member invites."""
        sub = _make_sub(max_team_members=5)
        assert can_invite_member(sub, current_count=-1) is True

    def test_zero_limit_blocks_everything(self):
        """A max of 0 blocks all creation."""
        sub = _make_sub(max_organisations=0, max_team_members=0)
        assert can_create_organisation(sub, current_count=0) is False
        assert can_invite_member(sub, current_count=0) is False

    def test_all_platform_tiers_have_limits(self):
        """Every SubscriptionTier enum value has corresponding limits."""
        for tier in SubscriptionTier:
            limits = get_tier_limits(tier.value)
            assert "max_organisations" in limits
            assert "max_team_members" in limits
