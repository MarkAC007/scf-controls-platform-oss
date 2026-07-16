"""Tests for bulk catalog export caching (ETag/304 + gzip)."""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from starlette.requests import Request


def _request_with_headers(headers: dict) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/catalog/bulk/controls",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "query_string": b"",
    }
    return Request(scope)


def test_not_modified_returns_304_on_matching_etag():
    from api.catalog import _not_modified, _BULK_CACHE_CONTROL
    etag = 'W/"1451-2026-01-01T00:00:00"'
    resp = _not_modified(_request_with_headers({"If-None-Match": etag}), etag)
    assert resp is not None
    assert resp.status_code == 304
    assert resp.headers["ETag"] == etag
    assert resp.headers["Cache-Control"] == _BULK_CACHE_CONTROL


def test_not_modified_passes_through_on_stale_or_absent_etag():
    from api.catalog import _not_modified
    etag = 'W/"1451-2026-01-01T00:00:00"'
    assert _not_modified(_request_with_headers({}), etag) is None
    assert _not_modified(
        _request_with_headers({"If-None-Match": 'W/"1451-2025-06-06T00:00:00"'}), etag
    ) is None


def test_bulk_endpoints_take_conditional_request_params():
    # Both bulk exports must accept request/response so they can serve 304s
    from api.catalog import bulk_export_controls, bulk_export_evidence
    for fn in (bulk_export_controls, bulk_export_evidence):
        params = inspect.signature(fn).parameters
        assert "request" in params and "response" in params


def test_gzip_middleware_registered():
    from fastapi.middleware.gzip import GZipMiddleware
    from main import app
    assert any(m.cls is GZipMiddleware for m in app.user_middleware)
