"""LightRAG HTTP client for CDM knowledge graph operations.

Lifecycle:
- Factory ``get_lightrag_client()`` enforces ``ENABLE_CDM_LIGHTRAG=true`` and a
  non-empty ``LIGHTRAG_BASE_URL`` env var.
- Client is a thin sync wrapper around httpx — one client per task instance is
  fine (the celery_worker runs the ingest task; each task call gets its own).
- The underlying ``httpx.Client`` is constructed lazily on first HTTP call so
  that consumers (and the factory's structural-correctness tests) can probe the
  client without paying for a TCP pool until a real request is made.
- Every HTTP call raises on non-2xx via ``raise_for_status()``.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

# Connect timeout has to cover Container App cold start (image pull + boot);
# read timeout has to cover LightRAG's inline entity extraction + embedding,
# which can take 5–30s for a typical policy document and longer for long docs.
_CONNECT_TIMEOUT_SECONDS = 15.0
_READ_TIMEOUT_SECONDS = 180.0


class CDMLightRAGClient:
    """Thin sync wrapper around LightRAG's HTTP API."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        # Lazy init — httpx.Client is constructed on first HTTP call so that
        # consumers can introspect a client (and tests can monkey-patch
        # httpx.Client.__init__ to verify no connection is opened) without
        # paying for a TCP pool. See tests/test_cdm_lightrag.py.
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.base_url,
                timeout=httpx.Timeout(
                    connect=_CONNECT_TIMEOUT_SECONDS,
                    read=_READ_TIMEOUT_SECONDS,
                    write=_READ_TIMEOUT_SECONDS,
                    pool=_READ_TIMEOUT_SECONDS,
                ),
            )
        return self._client

    def health(self) -> dict[str, Any]:
        """GET /health — return parsed JSON dict, raise on non-2xx."""
        response = self._get_client().get("/health")
        response.raise_for_status()
        return response.json()

    def insert(
        self,
        text: str,
        *,
        workspace: str,
        file_source: str,
        kb_revision: str | None = None,
    ) -> dict[str, Any]:
        """POST /documents/text — submit text for LightRAG indexing in the per-org workspace.

        Wire contract (probed against LightRAG main @ HKUDS/LightRAG):
        - Body: {"text": <text>, "file_source": <file_source>}  (InsertTextRequest)
        - Header: LIGHTRAG-WORKSPACE: <sanitized workspace>     (multi-workspace dispatch)
        - file_source is REQUIRED (HTTP 400 otherwise; valid file source check at route)
        - workspace is sanitized client-side: LightRAG strips [^a-zA-Z0-9_] so we
          replace '-' with '_' to keep UUIDs round-trip stable.
        - Returns InsertResponse: {"status", "message", "track_id"}.

        kb_revision is accepted for forward-compat (slice 5 audit hook); not sent
        on the wire in 3.5b — kb_revision tracking lives in CDMDocument, not LightRAG.
        """
        sanitized_workspace = workspace.replace("-", "_")
        payload = {"text": text, "file_source": file_source}
        headers = {"LIGHTRAG-WORKSPACE": sanitized_workspace}
        response = self._get_client().post(
            "/documents/text",
            json=payload,
            headers=headers,
        )
        response.raise_for_status()
        return response.json()

    def query(
        self,
        query_text: str,
        *,
        workspace: str,
        mode: str = "hybrid",
        top_k: int = 10,
    ) -> dict[str, Any]:
        """POST /query/data — tenant-scoped passage retrieval.

        Wire contract (probed against LightRAG main @ HKUDS/LightRAG):
        - Endpoint: ``POST /query/data`` returns raw retrieval data without LLM
          synthesis; ``/query`` returns generated prose instead.
        - Body schema: ``QueryRequest`` includes ``query: str``,
          ``mode: Literal["local", "global", "hybrid", "naive", "mix", "bypass"]``,
          and optional ``top_k: int``. The route calls
          ``rag.aquery_data(request.query, param=request.to_query_params(False))``.
        - Header behavior: send ``LIGHTRAG-WORKSPACE: <sanitized workspace>``.
          Upstream server code resolves that header via
          ``get_workspace_from_request()`` and sanitizes non
          ``[a-zA-Z0-9_]`` characters.
        - Response shape: ``QueryDataResponse`` with top-level ``status``,
          ``message``, ``data``, and ``metadata`` keys; raw passages live in
          ``data["chunks"]`` and references in ``data["references"]``.
        """
        sanitized_workspace = workspace.replace("-", "_")
        payload = {
            "query": query_text,
            "mode": mode,
            "top_k": top_k,
        }
        headers = {"LIGHTRAG-WORKSPACE": sanitized_workspace}
        response = self._get_client().post(
            "/query/data",
            json=payload,
            headers=headers,
        )
        response.raise_for_status()
        return response.json()


def is_lightrag_enabled() -> bool:
    return os.getenv("ENABLE_CDM_LIGHTRAG", "false").lower() == "true"


def get_lightrag_client() -> CDMLightRAGClient:
    if not is_lightrag_enabled():
        raise RuntimeError("LightRAG client requested but ENABLE_CDM_LIGHTRAG=false")

    base_url = os.getenv("LIGHTRAG_BASE_URL")
    if not base_url:
        raise RuntimeError("LIGHTRAG_BASE_URL required when ENABLE_CDM_LIGHTRAG=true")

    return CDMLightRAGClient(base_url)
