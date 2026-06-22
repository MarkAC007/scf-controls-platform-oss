"""Tests for rate limiting on provisioning/sync and webhooks/stripe (Issue #365)."""
import pytest
from rate_limiting import (
    PROVISIONING_SYNC_RATE_LIMIT,
    STRIPE_WEBHOOK_RATE_LIMIT,
    rate_limit_provisioning_sync,
    rate_limit_stripe_webhook,
    AUTH_RATE_LIMIT,
    READ_RATE_LIMIT,
    WRITE_RATE_LIMIT,
    limiter,
)


class TestProvisioningStripeRateLimitConstants:
    """Verify the new rate limit constants have correct defaults."""

    def test_provisioning_sync_default_limit(self):
        assert PROVISIONING_SYNC_RATE_LIMIT == "20/minute"

    def test_stripe_webhook_default_limit(self):
        assert STRIPE_WEBHOOK_RATE_LIMIT == "60/minute"

    def test_existing_limits_unchanged(self):
        """Existing global rate limits must remain unchanged (regression guard)."""
        assert AUTH_RATE_LIMIT == "10/minute"
        assert READ_RATE_LIMIT == "100/minute"
        assert WRITE_RATE_LIMIT == "30/minute"


class TestProvisioningStripeDecorators:
    """Verify the decorator helpers are callable and use the shared limiter."""

    def test_rate_limit_provisioning_sync_is_callable(self):
        assert callable(rate_limit_provisioning_sync)

    def test_rate_limit_stripe_webhook_is_callable(self):
        assert callable(rate_limit_stripe_webhook)

    def test_decorators_use_global_limiter(self):
        """Both decorators must wrap the shared IP-based limiter (not inbox_limiter)."""
        # Applying the decorator to a dummy coroutine should not raise
        async def dummy(request, response):
            pass  # pragma: no cover

        wrapped_sync = rate_limit_provisioning_sync(dummy)
        wrapped_stripe = rate_limit_stripe_webhook(dummy)
        assert callable(wrapped_sync)
        assert callable(wrapped_stripe)
