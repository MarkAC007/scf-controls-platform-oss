"""Tests for per-endpoint inbox rate limiting (Issue #216)."""
import pytest
from rate_limiting import (
    get_inbox_rate_key,
    INBOX_RATE_LIMIT,
    inbox_limiter,
    AUTH_RATE_LIMIT,
    READ_RATE_LIMIT,
    WRITE_RATE_LIMIT,
    limiter,
)


class TestInboxRateKeyFunction:
    """Test the composite key generation for inbox rate limiting."""

    def test_composite_key_format(self):
        """Key should be inbox:{org_id}:{webhook_id}."""
        from unittest.mock import MagicMock
        request = MagicMock()
        request.path_params = {"org_id": "org-123"}
        request.headers = {"X-SCF-Webhook-Id": "wh-456"}
        key = get_inbox_rate_key(request)
        assert key == "inbox:org-123:wh-456"

    def test_missing_org_id(self):
        """Missing org_id should use 'unknown'."""
        from unittest.mock import MagicMock
        request = MagicMock()
        request.path_params = {}
        request.headers = {"X-SCF-Webhook-Id": "wh-456"}
        key = get_inbox_rate_key(request)
        assert key == "inbox:unknown:wh-456"

    def test_missing_webhook_id(self):
        """Missing webhook header should use 'unknown'."""
        from unittest.mock import MagicMock
        request = MagicMock()
        request.path_params = {"org_id": "org-123"}
        request.headers = {}
        key = get_inbox_rate_key(request)
        assert key == "inbox:org-123:unknown"

    def test_different_endpoints_get_different_keys(self):
        """Two different webhook endpoints should get independent counters."""
        from unittest.mock import MagicMock
        req1 = MagicMock()
        req1.path_params = {"org_id": "org-1"}
        req1.headers = {"X-SCF-Webhook-Id": "wh-1"}

        req2 = MagicMock()
        req2.path_params = {"org_id": "org-1"}
        req2.headers = {"X-SCF-Webhook-Id": "wh-2"}

        assert get_inbox_rate_key(req1) != get_inbox_rate_key(req2)


class TestRateLimitConfiguration:
    """Test that rate limit configuration is correct."""

    def test_inbox_default_limit(self):
        assert INBOX_RATE_LIMIT == "60/minute"

    def test_existing_limits_unchanged(self):
        """Existing global rate limits must remain unchanged."""
        assert AUTH_RATE_LIMIT == "10/minute"
        assert READ_RATE_LIMIT == "100/minute"
        assert WRITE_RATE_LIMIT == "30/minute"

    def test_inbox_limiter_separate_from_global(self):
        """Inbox limiter must be a separate instance from the global limiter."""
        assert inbox_limiter is not limiter

    def test_inbox_limiter_enabled(self):
        """Inbox limiter should follow RATE_LIMITING_ENABLED."""
        assert inbox_limiter.enabled == limiter.enabled
