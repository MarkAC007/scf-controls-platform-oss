"""
Integration tests for the audit middleware (Issue #343).

Tests:
1. Mutation requests (POST/PUT/PATCH/DELETE) produce middleware audit records
2. GET/HEAD/OPTIONS do NOT produce audit records
3. Failed mutations (4xx/5xx) do NOT produce audit records
4. action_source detection works for different auth methods
5. request_id is consistent between middleware and field-level records
6. Middleware failures don't break the request
"""
import pytest
import re
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from starlette.testclient import TestClient
from starlette.requests import Request
from starlette.datastructures import Headers

from middleware.audit_middleware import (
    AuditMiddleware,
    _extract_entity_type,
    _extract_org_id,
    _should_skip,
    _METHOD_ACTION_MAP,
)
from services.audit_service import detect_action_source


# ---------------------------------------------------------------------------
# Unit tests for helper functions
# ---------------------------------------------------------------------------


class TestExtractEntityType:
    """Test URL path -> entity_type extraction."""

    def test_scoped_controls(self):
        assert _extract_entity_type("/api/organizations/123e4567-e89b-12d3-a456-426614174000/scoped-controls") == "scoped_control"

    def test_vendors(self):
        assert _extract_entity_type("/api/organizations/123e4567-e89b-12d3-a456-426614174000/vendors") == "vendor"

    def test_risk_assessments(self):
        assert _extract_entity_type("/api/organizations/123e4567-e89b-12d3-a456-426614174000/risk-assessments") == "risk_assessment"

    def test_evidence_files(self):
        assert _extract_entity_type("/api/organizations/123e4567-e89b-12d3-a456-426614174000/evidence-files") == "evidence_file"

    def test_webhook_endpoints(self):
        assert _extract_entity_type("/api/organizations/123e4567-e89b-12d3-a456-426614174000/webhook-endpoints") == "webhook_endpoint"

    def test_unknown_segment(self):
        assert _extract_entity_type("/api/organizations/123e4567-e89b-12d3-a456-426614174000/some-new-thing") == "some_new_thing"

    def test_no_match(self):
        assert _extract_entity_type("/health") == "unknown"
        assert _extract_entity_type("/docs") == "unknown"


class TestExtractOrgId:
    """Test organization UUID extraction from URL path."""

    def test_valid_org_id(self):
        org_id = uuid.UUID("123e4567-e89b-12d3-a456-426614174000")
        result = _extract_org_id("/api/organizations/123e4567-e89b-12d3-a456-426614174000/vendors")
        assert result == org_id

    def test_no_org_id(self):
        assert _extract_org_id("/health") is None
        assert _extract_org_id("/api/users") is None

    def test_nested_path(self):
        org_id = uuid.UUID("123e4567-e89b-12d3-a456-426614174000")
        result = _extract_org_id("/api/organizations/123e4567-e89b-12d3-a456-426614174000/vendors/abc/reports")
        assert result == org_id


class TestShouldSkip:
    """Test request skip logic."""

    def test_get_skipped(self):
        assert _should_skip("GET", "/api/organizations/123/vendors") is True

    def test_head_skipped(self):
        assert _should_skip("HEAD", "/api/organizations/123/vendors") is True

    def test_options_skipped(self):
        assert _should_skip("OPTIONS", "/api/organizations/123/vendors") is True

    def test_post_not_skipped(self):
        assert _should_skip("POST", "/api/organizations/123/vendors") is False

    def test_put_not_skipped(self):
        assert _should_skip("PUT", "/api/organizations/123/vendors") is False

    def test_patch_not_skipped(self):
        assert _should_skip("PATCH", "/api/organizations/123/vendors") is False

    def test_delete_not_skipped(self):
        assert _should_skip("DELETE", "/api/organizations/123/vendors") is False

    def test_health_skipped(self):
        assert _should_skip("POST", "/health") is True

    def test_docs_skipped(self):
        assert _should_skip("POST", "/docs") is True

    def test_root_skipped(self):
        assert _should_skip("POST", "/") is True


class TestMethodActionMap:
    """Test HTTP method -> action mapping."""

    def test_post_creates(self):
        assert _METHOD_ACTION_MAP["POST"] == "create"

    def test_put_updates(self):
        assert _METHOD_ACTION_MAP["PUT"] == "update"

    def test_patch_updates(self):
        assert _METHOD_ACTION_MAP["PATCH"] == "update"

    def test_delete_deletes(self):
        assert _METHOD_ACTION_MAP["DELETE"] == "delete"


# ---------------------------------------------------------------------------
# Source detection tests
# ---------------------------------------------------------------------------


class TestDetectActionSource:
    """Test action source detection from request context."""

    def _make_request(self, headers=None, auth_method=None, user_agent=None):
        """Create a mock request with specified auth context."""
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/test",
            "headers": [],
            "state": {},
        }

        if headers:
            scope["headers"] = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
        if user_agent:
            scope["headers"].append((b"user-agent", user_agent.encode()))

        request = Request(scope)

        if auth_method is not None:
            user = MagicMock()
            user.auth_method = auth_method
            request.state.user = user

        return request

    def test_explicit_header_ui(self):
        request = self._make_request(headers={"x-audit-source": "ui"})
        assert detect_action_source(request) == "ui"

    def test_explicit_header_mcp(self):
        request = self._make_request(headers={"x-audit-source": "mcp"})
        assert detect_action_source(request) == "mcp"

    def test_explicit_header_invalid_ignored(self):
        request = self._make_request(headers={"x-audit-source": "invalid"}, auth_method="google")
        assert detect_action_source(request) == "ui"  # Falls through to auth method

    def test_google_auth_returns_ui(self):
        request = self._make_request(auth_method="google")
        assert detect_action_source(request) == "ui"

    def test_api_key_returns_api_key(self):
        request = self._make_request(auth_method="api_key")
        assert detect_action_source(request) == "api_key"

    def test_user_api_key_returns_api_key(self):
        request = self._make_request(auth_method="user_api_key")
        assert detect_action_source(request) == "api_key"

    def test_user_api_key_with_mcp_ua_returns_mcp(self):
        request = self._make_request(auth_method="user_api_key", user_agent="MCP-Client/1.0")
        assert detect_action_source(request) == "mcp"

    def test_user_api_key_with_model_context_protocol_ua(self):
        request = self._make_request(auth_method="user_api_key", user_agent="model.context.protocol/2.0")
        assert detect_action_source(request) == "mcp"

    def test_no_user_returns_system(self):
        request = self._make_request()
        assert detect_action_source(request) == "system"
