from __future__ import annotations

import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.cdm_lightrag import get_lightrag_client


def test_get_lightrag_client_raises_when_flag_off(monkeypatch):
    monkeypatch.setenv("ENABLE_CDM_LIGHTRAG", "false")
    monkeypatch.setenv("LIGHTRAG_BASE_URL", "http://example.test")

    with pytest.raises(
        RuntimeError,
        match="LightRAG client requested but ENABLE_CDM_LIGHTRAG=false",
    ):
        get_lightrag_client()


def test_get_lightrag_client_raises_when_base_url_missing(monkeypatch):
    monkeypatch.setenv("ENABLE_CDM_LIGHTRAG", "true")
    monkeypatch.setenv("LIGHTRAG_BASE_URL", "")

    with pytest.raises(
        RuntimeError,
        match="LIGHTRAG_BASE_URL required when ENABLE_CDM_LIGHTRAG=true",
    ):
        get_lightrag_client()


def test_get_lightrag_client_returns_structural_client_without_httpx(monkeypatch):
    monkeypatch.setenv("ENABLE_CDM_LIGHTRAG", "true")
    monkeypatch.setenv("LIGHTRAG_BASE_URL", "http://example.test")

    def _raise_if_called(self, *args, **kwargs):
        raise AssertionError("httpx.Client.__init__ should not be called")

    monkeypatch.setattr(httpx.Client, "__init__", _raise_if_called)

    client = get_lightrag_client()

    assert client.base_url == "http://example.test"


def _stub_httpx_client_init(monkeypatch):
    """Stub httpx.Client.__init__ to a no-op for tests.

    The httpx.Client constructor builds an SSL context via certifi, which is
    expensive and may fail in offline dev environments. The tests mock
    Client.get/Client.post directly so the real __init__ is unnecessary —
    nothing about the client state matters once both HTTP methods are stubbed.
    """

    def _noop_init(self, *args, **kwargs):
        return None

    monkeypatch.setattr(httpx.Client, "__init__", _noop_init)


def test_health_returns_json(monkeypatch):
    """ISC-19: health() returns the parsed JSON dict on 200."""
    monkeypatch.setenv("ENABLE_CDM_LIGHTRAG", "true")
    monkeypatch.setenv("LIGHTRAG_BASE_URL", "http://example.test")
    _stub_httpx_client_init(monkeypatch)

    def _mock_get(self, url, **kwargs):
        return httpx.Response(
            200,
            json={"status": "healthy", "configuration": {"workspace": "default"}},
            request=httpx.Request("GET", "http://example.test/health"),
        )

    monkeypatch.setattr(httpx.Client, "get", _mock_get)

    client = get_lightrag_client()
    result = client.health()

    assert result == {"status": "healthy", "configuration": {"workspace": "default"}}


def test_health_raises_on_non_2xx(monkeypatch):
    """ISC-20: health() raises HTTPStatusError on 5xx."""
    monkeypatch.setenv("ENABLE_CDM_LIGHTRAG", "true")
    monkeypatch.setenv("LIGHTRAG_BASE_URL", "http://example.test")
    _stub_httpx_client_init(monkeypatch)

    def _mock_get(self, url, **kwargs):
        return httpx.Response(
            500,
            json={"error": "down"},
            request=httpx.Request("GET", "http://example.test/health"),
        )

    monkeypatch.setattr(httpx.Client, "get", _mock_get)

    client = get_lightrag_client()
    with pytest.raises(httpx.HTTPStatusError):
        client.health()


def test_insert_posts_workspace_header_and_body(monkeypatch):
    """ISC-21: insert() sends text + file_source in body and sanitized workspace in LIGHTRAG-WORKSPACE header."""
    monkeypatch.setenv("ENABLE_CDM_LIGHTRAG", "true")
    monkeypatch.setenv("LIGHTRAG_BASE_URL", "http://example.test")
    _stub_httpx_client_init(monkeypatch)

    captured: dict[str, object] = {}

    def _mock_post(self, url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        captured["headers"] = kwargs.get("headers")
        return httpx.Response(
            200,
            json={"status": "success", "message": "received", "track_id": "abc123"},
            request=httpx.Request("POST", "http://example.test/documents/text"),
        )

    monkeypatch.setattr(httpx.Client, "post", _mock_post)

    client = get_lightrag_client()
    result = client.insert(
        text="hello policy",
        workspace="00000000-0000-0000-0000-000000000042",
        file_source="cdm-00000000-0000-0000-0000-000000000042.txt",
    )

    assert result == {"status": "success", "message": "received", "track_id": "abc123"}
    assert captured["url"] == "/documents/text"
    body = captured["json"]
    assert isinstance(body, dict)
    assert body.get("text") == "hello policy"
    # file_source is required by LightRAG's route (HTTP 400 otherwise) — must be on the wire.
    assert body.get("file_source") == "cdm-00000000-0000-0000-0000-000000000042.txt"
    headers = captured["headers"] or {}
    # Sanitization: hyphens replaced with underscores so LightRAG accepts the value verbatim.
    assert headers.get("LIGHTRAG-WORKSPACE") == "00000000_0000_0000_0000_000000000042"


def test_insert_raises_on_non_2xx(monkeypatch):
    """ISC-22: insert() raises HTTPStatusError on 4xx."""
    monkeypatch.setenv("ENABLE_CDM_LIGHTRAG", "true")
    monkeypatch.setenv("LIGHTRAG_BASE_URL", "http://example.test")
    _stub_httpx_client_init(monkeypatch)

    def _mock_post(self, url, **kwargs):
        return httpx.Response(
            400,
            json={"error": "invalid"},
            request=httpx.Request("POST", "http://example.test/documents/text"),
        )

    monkeypatch.setattr(httpx.Client, "post", _mock_post)

    client = get_lightrag_client()
    with pytest.raises(httpx.HTTPStatusError):
        client.insert(text="bad", workspace="org-1", file_source="cdm-org-1.txt")


# -------------------------------------------------------------------------
# Slice 3.5c — ISC-21..23: query() client method
# -------------------------------------------------------------------------


_QUERY_DATA_RESPONSE = {
    "status": "success",
    "message": "ok",
    "data": {
        "chunks": [
            {
                "content": "Access reviews must occur quarterly.",
                "chunk_id": "chunk-001",
                "reference_id": "ref-001",
                "file_path": "cdm-doc-1.txt",
            },
            {
                "content": "Privileged accounts require MFA.",
                "chunk_id": "chunk-002",
                "reference_id": "ref-001",
                "file_path": "cdm-doc-1.txt",
            },
        ],
        "references": [
            {"reference_id": "ref-001", "file_path": "cdm-doc-1.txt"},
        ],
        "entities": [],
        "relationships": [],
    },
    "metadata": {"query_mode": "hybrid"},
}


def test_query_returns_hits(monkeypatch):
    """ISC-21 (client): query() returns the parsed JSON dict on 200."""
    monkeypatch.setenv("ENABLE_CDM_LIGHTRAG", "true")
    monkeypatch.setenv("LIGHTRAG_BASE_URL", "http://example.test")
    _stub_httpx_client_init(monkeypatch)

    def _mock_post(self, url, **kwargs):
        return httpx.Response(
            200,
            json=_QUERY_DATA_RESPONSE,
            request=httpx.Request("POST", "http://example.test/query/data"),
        )

    monkeypatch.setattr(httpx.Client, "post", _mock_post)

    client = get_lightrag_client()
    result = client.query(
        "access review cadence",
        workspace="00000000-0000-0000-0000-000000000042",
        top_k=10,
    )

    assert result == _QUERY_DATA_RESPONSE


def test_query_sends_workspace_header(monkeypatch):
    """ISC-22 (client): query() sends sanitized LIGHTRAG-WORKSPACE header and probed body shape."""
    monkeypatch.setenv("ENABLE_CDM_LIGHTRAG", "true")
    monkeypatch.setenv("LIGHTRAG_BASE_URL", "http://example.test")
    _stub_httpx_client_init(monkeypatch)

    captured: dict[str, object] = {}

    def _mock_post(self, url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        captured["headers"] = kwargs.get("headers")
        return httpx.Response(
            200,
            json=_QUERY_DATA_RESPONSE,
            request=httpx.Request("POST", "http://example.test/query/data"),
        )

    monkeypatch.setattr(httpx.Client, "post", _mock_post)

    client = get_lightrag_client()
    client.query(
        "encryption at rest",
        workspace="aaaa-bbbb",
        top_k=5,
    )

    assert captured["url"] == "/query/data"
    body = captured["json"]
    assert isinstance(body, dict)
    assert body.get("query") == "encryption at rest"
    assert body.get("mode") == "hybrid"
    assert body.get("top_k") == 5
    headers = captured["headers"] or {}
    # Sanitization: hyphens → underscores so LightRAG's get_workspace_from_request accepts verbatim.
    assert headers.get("LIGHTRAG-WORKSPACE") == "aaaa_bbbb"


def test_query_raises_on_non_2xx(monkeypatch):
    """ISC-23 (client): query() raises httpx.HTTPStatusError on 5xx."""
    monkeypatch.setenv("ENABLE_CDM_LIGHTRAG", "true")
    monkeypatch.setenv("LIGHTRAG_BASE_URL", "http://example.test")
    _stub_httpx_client_init(monkeypatch)

    def _mock_post(self, url, **kwargs):
        return httpx.Response(
            500,
            json={"error": "boom"},
            request=httpx.Request("POST", "http://example.test/query/data"),
        )

    monkeypatch.setattr(httpx.Client, "post", _mock_post)

    client = get_lightrag_client()
    with pytest.raises(httpx.HTTPStatusError):
        client.query("anything", workspace="org-1", top_k=10)
