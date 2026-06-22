"""
Unit tests for services/subscription.py — feature flag helper (Issue #363).

Covers:
- has_feature() returns correct values for all platform-native tiers
- has_feature() returns correct values for website tier aliases
- Enterprise tier grants full access to all features
- Inactive subscription denies all features
- None subscription denies all features
- Unknown tier denies unknown features gracefully
- api_key and user_api_key auth methods bypass feature checks
- google auth method does NOT bypass feature checks
- Unknown feature name returns False (safe default)
"""
import pytest
from unittest.mock import MagicMock

from services.subscription import has_feature, TIER_FEATURES, _API_KEY_AUTH_METHODS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sub(tier: str, is_active: bool = True) -> MagicMock:
    """Return a lightweight mock that behaves like a UserSubscription."""
    sub = MagicMock()
    sub.tier = tier
    sub.is_active = is_active
    return sub


# ---------------------------------------------------------------------------
# Platform-native tier: free
# ---------------------------------------------------------------------------

class TestFreeTier:
    def test_api_access_denied(self):
        assert has_feature(_make_sub("free"), "api_access") is False

    def test_sso_denied(self):
        assert has_feature(_make_sub("free"), "sso") is False


# ---------------------------------------------------------------------------
# Platform-native tier: professional
# ---------------------------------------------------------------------------

class TestProfessionalTier:
    def test_api_access_granted(self):
        assert has_feature(_make_sub("professional"), "api_access") is True

    def test_sso_denied(self):
        assert has_feature(_make_sub("professional"), "sso") is False


# ---------------------------------------------------------------------------
# Platform-native tier: enterprise (full access)
# ---------------------------------------------------------------------------

class TestEnterpriseTier:
    def test_api_access_granted(self):
        assert has_feature(_make_sub("enterprise"), "api_access") is True

    def test_sso_granted(self):
        assert has_feature(_make_sub("enterprise"), "sso") is True


# ---------------------------------------------------------------------------
# Website tier aliases
# ---------------------------------------------------------------------------

class TestWebsiteTierAliases:
    def test_pro_api_access_granted(self):
        assert has_feature(_make_sub("pro"), "api_access") is True

    def test_pro_sso_denied(self):
        assert has_feature(_make_sub("pro"), "sso") is False

    def test_consultant_api_access_granted(self):
        assert has_feature(_make_sub("consultant"), "api_access") is True

    def test_consultant_sso_denied(self):
        assert has_feature(_make_sub("consultant"), "sso") is False

    def test_custom_api_access_granted(self):
        assert has_feature(_make_sub("custom"), "api_access") is True

    def test_custom_sso_granted(self):
        assert has_feature(_make_sub("custom"), "sso") is True


# ---------------------------------------------------------------------------
# Inactive subscription
# ---------------------------------------------------------------------------

class TestInactiveSubscription:
    def test_enterprise_inactive_api_access_denied(self):
        assert has_feature(_make_sub("enterprise", is_active=False), "api_access") is False

    def test_enterprise_inactive_sso_denied(self):
        assert has_feature(_make_sub("enterprise", is_active=False), "sso") is False

    def test_professional_inactive_api_access_denied(self):
        assert has_feature(_make_sub("professional", is_active=False), "api_access") is False


# ---------------------------------------------------------------------------
# None subscription
# ---------------------------------------------------------------------------

class TestNoneSubscription:
    def test_api_access_denied(self):
        assert has_feature(None, "api_access") is False

    def test_sso_denied(self):
        assert has_feature(None, "sso") is False


# ---------------------------------------------------------------------------
# Unknown tier
# ---------------------------------------------------------------------------

class TestUnknownTier:
    def test_unknown_tier_api_access_denied(self):
        assert has_feature(_make_sub("unknown_tier"), "api_access") is False

    def test_unknown_tier_sso_denied(self):
        assert has_feature(_make_sub("unknown_tier"), "sso") is False


# ---------------------------------------------------------------------------
# API key auth bypass
# ---------------------------------------------------------------------------

class TestApiKeyBypass:
    """API key auth methods must bypass feature checks regardless of tier."""

    def test_api_key_bypasses_free_tier_api_access(self):
        assert has_feature(_make_sub("free"), "api_access", auth_method="api_key") is True

    def test_api_key_bypasses_free_tier_sso(self):
        assert has_feature(_make_sub("free"), "sso", auth_method="api_key") is True

    def test_user_api_key_bypasses_free_tier_api_access(self):
        assert has_feature(_make_sub("free"), "api_access", auth_method="user_api_key") is True

    def test_user_api_key_bypasses_free_tier_sso(self):
        assert has_feature(_make_sub("free"), "sso", auth_method="user_api_key") is True

    def test_api_key_bypasses_none_subscription(self):
        assert has_feature(None, "api_access", auth_method="api_key") is True

    def test_user_api_key_bypasses_none_subscription(self):
        assert has_feature(None, "sso", auth_method="user_api_key") is True

    def test_api_key_bypasses_inactive_subscription(self):
        assert (
            has_feature(_make_sub("free", is_active=False), "api_access", auth_method="api_key")
            is True
        )

    def test_google_auth_does_not_bypass(self):
        """google auth must NOT bypass — free tier should still be denied."""
        assert has_feature(_make_sub("free"), "api_access", auth_method="google") is False

    def test_default_auth_method_does_not_bypass(self):
        """Default auth_method is 'google' and must not bypass."""
        assert has_feature(_make_sub("free"), "api_access") is False

    def test_api_key_auth_methods_constant(self):
        """Verify the constant contains exactly the expected values."""
        assert _API_KEY_AUTH_METHODS == {"api_key", "user_api_key"}


# ---------------------------------------------------------------------------
# Unknown feature name
# ---------------------------------------------------------------------------

class TestUnknownFeature:
    def test_enterprise_unknown_feature_returns_false(self):
        """Unknown feature names must default to False (safe deny)."""
        assert has_feature(_make_sub("enterprise"), "nonexistent_feature") is False

    def test_api_key_bypasses_unknown_feature(self):
        """API key bypass applies even for unknown feature names."""
        assert has_feature(_make_sub("free"), "nonexistent_feature", auth_method="api_key") is True


# ---------------------------------------------------------------------------
# TIER_FEATURES completeness
# ---------------------------------------------------------------------------

class TestTierFeaturesCompleteness:
    """Sanity checks on the TIER_FEATURES mapping itself."""

    EXPECTED_TIERS = {"free", "professional", "enterprise", "pro", "consultant", "custom"}
    EXPECTED_FEATURES = {"api_access", "sso"}

    def test_all_expected_tiers_present(self):
        assert self.EXPECTED_TIERS.issubset(TIER_FEATURES.keys())

    def test_all_tiers_have_required_features(self):
        for tier, flags in TIER_FEATURES.items():
            missing = self.EXPECTED_FEATURES - flags.keys()
            assert not missing, f"Tier '{tier}' is missing features: {missing}"

    def test_all_flag_values_are_bool(self):
        for tier, flags in TIER_FEATURES.items():
            for feature, value in flags.items():
                assert isinstance(value, bool), (
                    f"TIER_FEATURES['{tier}']['{feature}'] must be bool, got {type(value)}"
                )
