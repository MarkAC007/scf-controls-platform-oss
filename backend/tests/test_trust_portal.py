"""
Tests for the public trust portal API.

Covers:
- Posture band conversion logic
- Public schema safety (no raw data leaks)
- Rate limit configuration
"""
import pytest
from datetime import datetime, timezone

from api.trust_portal import _posture_to_band
from schemas import TrustPortalResponse, TrustPortalThemeSummary, TrustPortalFramework
from rate_limiting import TRUST_PORTAL_RATE_LIMIT, rate_limit_trust_portal


# =============================================================================
# _posture_to_band unit tests
# =============================================================================

class TestPostureToBand:
    """Test posture percentage to band conversion."""

    def test_strong_at_70(self):
        assert _posture_to_band(70.0) == "Strong"

    def test_strong_above_70(self):
        assert _posture_to_band(85.5) == "Strong"

    def test_strong_at_100(self):
        assert _posture_to_band(100.0) == "Strong"

    def test_moderate_at_40(self):
        assert _posture_to_band(40.0) == "Moderate"

    def test_moderate_at_69(self):
        assert _posture_to_band(69.9) == "Moderate"

    def test_developing_below_40(self):
        assert _posture_to_band(39.9) == "Developing"

    def test_developing_at_zero(self):
        assert _posture_to_band(0.0) == "Developing"

    def test_developing_at_20(self):
        assert _posture_to_band(20.0) == "Developing"


# =============================================================================
# Schema safety tests
# =============================================================================

class TestPublicSchemaSafety:
    """Ensure public schemas never contain sensitive internal fields."""

    def test_theme_summary_has_no_raw_percentage(self):
        """TrustPortalThemeSummary must not expose posture_percentage."""
        fields = TrustPortalThemeSummary.model_fields
        assert "posture_percentage" not in fields
        assert "scf_id" not in fields
        assert "total_controls" not in fields
        assert "scoped_controls" not in fields
        assert "maturity_score" not in fields

    def test_theme_summary_has_no_posture_breakdown(self):
        """TrustPortalThemeSummary must not expose individual status counts."""
        fields = TrustPortalThemeSummary.model_fields
        assert "monitored" not in fields
        assert "implemented" not in fields
        assert "not_started" not in fields
        assert "at_risk" not in fields
        assert "deferred" not in fields

    def test_response_has_no_org_id(self):
        """TrustPortalResponse must not expose the org UUID."""
        fields = TrustPortalResponse.model_fields
        assert "organization_id" not in fields
        assert "org_id" not in fields

    def test_framework_has_no_internal_ids(self):
        """TrustPortalFramework must not expose framework keys."""
        fields = TrustPortalFramework.model_fields
        assert "framework_key" not in fields
        assert "framework_id" not in fields

    def test_response_serialisation(self):
        """Verify a valid response can be constructed and serialised."""
        resp = TrustPortalResponse(
            organization_name="Test Org",
            organization_slug="test-org",
            description="A test organisation.",
            themes=[
                TrustPortalThemeSummary(
                    name="Identity & Access Management",
                    icon="shield-check",
                    display_order=1,
                    posture_band="Strong",
                    evidence_confidence="strong",
                ),
            ],
            frameworks=[
                TrustPortalFramework(name="ISO 27001", control_count=142),
            ],
            last_updated=datetime.now(timezone.utc),
            generated_at=datetime.now(timezone.utc),
        )
        data = resp.model_dump()
        assert data["organization_slug"] == "test-org"
        assert data["themes"][0]["posture_band"] == "Strong"
        assert data["frameworks"][0]["name"] == "ISO 27001"
        # Verify no raw fields leaked through
        assert "posture_percentage" not in str(data)
        assert "scf_id" not in str(data)


# =============================================================================
# Four-axis schema tests (Option B — issue-wide multi-axis display)
# =============================================================================

class TestFourAxisSchema:
    """Theme summary exposes the four KSI axis bands and show_axes toggle."""

    def test_theme_summary_has_four_axis_bands(self):
        fields = TrustPortalThemeSummary.model_fields
        assert "implementation_band" in fields
        assert "maturity_band" in fields
        assert "evidence_coverage_band" in fields
        assert "evidence_quality_band" in fields

    def test_axis_bands_are_optional(self):
        """Bands must be Optional so legacy consumers and empty-data orgs still parse."""
        summary = TrustPortalThemeSummary(
            name="Governance",
            icon="shield-check",
            display_order=1,
            posture_band="Moderate",
            evidence_confidence="none",
        )
        assert summary.implementation_band is None
        assert summary.maturity_band is None
        assert summary.evidence_coverage_band is None
        assert summary.evidence_quality_band is None

    def test_axis_bands_accept_strong_moderate_developing(self):
        summary = TrustPortalThemeSummary(
            name="Identity & Access Management",
            icon="key",
            display_order=2,
            posture_band="Strong",
            evidence_confidence="strong",
            implementation_band="Strong",
            maturity_band="Moderate",
            evidence_coverage_band="Developing",
            evidence_quality_band="Strong",
        )
        assert summary.implementation_band == "Strong"
        assert summary.maturity_band == "Moderate"
        assert summary.evidence_coverage_band == "Developing"
        assert summary.evidence_quality_band == "Strong"

    def test_response_has_show_axes_flag(self):
        fields = TrustPortalResponse.model_fields
        assert "show_axes" in fields

    def test_show_axes_defaults_to_false(self):
        resp = TrustPortalResponse(
            organization_name="Test Org",
            organization_slug="test-org",
            description=None,
            themes=[],
            frameworks=[],
            last_updated=datetime.now(timezone.utc),
            generated_at=datetime.now(timezone.utc),
        )
        assert resp.show_axes is False

    def test_show_axes_true_in_response(self):
        resp = TrustPortalResponse(
            organization_name="Test Org",
            organization_slug="test-org",
            description=None,
            themes=[],
            frameworks=[],
            last_updated=datetime.now(timezone.utc),
            generated_at=datetime.now(timezone.utc),
            show_axes=True,
        )
        data = resp.model_dump()
        assert data["show_axes"] is True

    def test_axis_bands_still_no_raw_scores(self):
        """Bands are exposed, raw floats are not — privacy model preserved."""
        fields = TrustPortalThemeSummary.model_fields
        assert "implementation_coverage" not in fields
        assert "evidence_coverage" not in fields
        assert "evidence_quality" not in fields
        assert "composite_score" not in fields
        assert "evidence_quality_warning" not in fields


# =============================================================================
# Composite band tests (drive the headline bar from KPS, not implementation)
# =============================================================================

class TestCompositeBand:
    """Theme summary exposes composite_band so clients can render a KPS-driven
    headline instead of the implementation-only posture_band."""

    def test_theme_summary_has_composite_band(self):
        fields = TrustPortalThemeSummary.model_fields
        assert "composite_band" in fields

    def test_composite_band_is_optional(self):
        summary = TrustPortalThemeSummary(
            name="Governance",
            icon="shield-check",
            display_order=1,
            posture_band="Moderate",
            evidence_confidence="none",
        )
        assert summary.composite_band is None

    def test_composite_band_accepts_strong_moderate_developing(self):
        for band in ("Strong", "Moderate", "Developing"):
            summary = TrustPortalThemeSummary(
                name="Identity & Access Management",
                icon="key",
                display_order=2,
                posture_band="Strong",
                evidence_confidence="strong",
                composite_band=band,
            )
            assert summary.composite_band == band

    def test_composite_score_still_not_exposed(self):
        """Raw composite_score float must never cross the public boundary."""
        fields = TrustPortalThemeSummary.model_fields
        assert "composite_score" not in fields


# =============================================================================
# Rate limit configuration tests
# =============================================================================

class TestRateLimitConfig:
    """Verify trust portal rate limit is configured correctly."""

    def test_default_rate_limit(self):
        assert TRUST_PORTAL_RATE_LIMIT == "30/minute"

    def test_decorator_is_callable(self):
        assert callable(rate_limit_trust_portal)
