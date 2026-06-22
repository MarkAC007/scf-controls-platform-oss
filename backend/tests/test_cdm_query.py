"""Tests for CDM v1 slice 3.5c — POST /cdm/query (Celery-proxied LightRAG query).

Covers slice 3.5c endpoint ISC-24..ISC-28. Uses the FastAPI TestClient pattern
from test_cdm_ingest.py: a FakeAsyncSession with scripted execute() results, a
dependency_overrides bag for auth + db, and direct monkeypatching of
``tasks_cdm.query_cdm.apply_async`` so no Celery delivery occurs.

The Celery .get() call is exercised via asyncio.to_thread inside the route, so
the fake AsyncResult must expose a synchronous .get(...) — mirroring real
celery.result.AsyncResult.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from celery.exceptions import TimeoutError as CeleryTimeoutError
from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["ENABLE_CDM"] = "true"

import main  # noqa: E402
from auth import OrgMembership, require_org_editor, require_org_viewer  # noqa: E402
from database import get_db  # noqa: E402
from services.cdm_tenancy import require_tenant_cdm_enabled  # noqa: E402
from tasks_cdm import CDMQueryUpstreamError  # noqa: E402

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")


class _FakeAsyncSession:
    """Scripted async session — feeds tuples to .execute().one_or_none()."""

    def __init__(self, scripted_rows: Optional[List[Any]] = None):
        self._scripted = list(scripted_rows or [])
        self.executed = 0

    async def execute(self, _stmt):  # noqa: D401 — match SQLAlchemy signature
        if not self._scripted:
            raise AssertionError("FakeAsyncSession: ran out of scripted rows")
        nxt = self._scripted.pop(0)
        self.executed += 1

        class _Result:
            def __init__(self, value):
                self._value = value

            def one_or_none(self):
                return self._value

        return _Result(nxt)


@pytest.fixture
def cdm_env(monkeypatch):
    monkeypatch.setenv("ENABLE_CDM", "true")
    yield


@pytest.fixture
def apply_async_stub(monkeypatch):
    """Stub tasks_cdm.query_cdm.apply_async so no Celery delivery occurs.

    The fixture yields a controller dict the test populates BEFORE the request
    runs. Controller fields:
      - "args": captured args from the apply_async call
      - "kwargs": captured kwargs from the apply_async call
      - "result_payload": the dict that .get() will return on success
      - "result_exc": optional exception .get() should raise instead
    """
    import api.cdm as cdm_router

    controller: Dict[str, Any] = {
        "args": None,
        "kwargs": None,
        "result_payload": None,
        "result_exc": None,
    }

    def _fake_apply_async(args=None, kwargs=None, **opts):
        controller["args"] = list(args) if args is not None else None
        controller["kwargs"] = dict(kwargs) if kwargs is not None else None
        controller["opts"] = opts

        class _FakeAsyncResult:
            def get(self, timeout=None, propagate=True):
                if controller["result_exc"] is not None:
                    raise controller["result_exc"]
                return controller["result_payload"]

        return _FakeAsyncResult()

    # Patch the bound method on the task object so the route's
    # `tasks_cdm.query_cdm.apply_async(...)` call goes through the stub.
    monkeypatch.setattr(
        cdm_router.tasks_cdm.query_cdm, "apply_async", _fake_apply_async
    )
    yield controller


@pytest.fixture
def client_factory(cdm_env, apply_async_stub):
    """Build TestClient with a fake async session + auth override."""
    app = main.app

    def _build(
        session: _FakeAsyncSession,
        *,
        role: Optional[str] = "viewer",
        org: UUID = ORG_ID,
    ) -> TestClient:
        async def _override_db():
            yield session

        async def _override_auth():
            if role is None:
                raise HTTPException(status_code=403, detail="forbidden")
            user = MagicMock()
            user.db_id = str(uuid4())
            user.email = "test@example.com"
            return OrgMembership(
                user=user, organization_id=org, role=role, is_consultant=False
            )

        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[require_org_editor] = _override_auth
        app.dependency_overrides[require_org_viewer] = _override_auth
        app.dependency_overrides[require_tenant_cdm_enabled] = lambda: None
        return TestClient(app)

    yield _build

    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(require_org_editor, None)
    app.dependency_overrides.pop(require_org_viewer, None)
    app.dependency_overrides.pop(require_tenant_cdm_enabled, None)


def _scoped_control_row(control_id: UUID, *, name="Access Review", description="Periodic review"):
    """Build a row tuple matching the route's select() column order."""
    return SimpleNamespace(
        id=control_id,
        scf_id="IAC-01",
        control_name=name,
        control_description=description,
    )


def _ok_payload(hits=None):
    return {
        "hits": hits if hits is not None else [
            {
                "content": "Reviews quarterly.",
                "chunk_id": "c-1",
                "reference_id": "r-1",
                "file_path": "cdm-doc.txt",
                "file_source": "cdm-doc.txt",
            }
        ],
        "kb_revision": "lightrag-v1",
        "mode": "hybrid",
    }


# -------------------------------------------------------------------------
# ISC-24: happy path with explicit query_text
# -------------------------------------------------------------------------


def test_query_happy_path_with_explicit_text(client_factory, apply_async_stub):
    """ISC-24: explicit query_text reaches the task verbatim; 200 + correct shape."""
    control_id = uuid4()
    session = _FakeAsyncSession(scripted_rows=[_scoped_control_row(control_id)])
    apply_async_stub["result_payload"] = _ok_payload()

    client = client_factory(session)
    resp = client.post(
        f"/api/organizations/{ORG_ID}/cdm/query",
        json={
            "control_id": str(control_id),
            "query_text": "explicit user-provided query",
            "limit": 7,
        },
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kb_revision"] == "lightrag-v1"
    assert isinstance(body["hits"], list) and len(body["hits"]) == 1
    assert body["hits"][0]["content"] == "Reviews quarterly."

    # Task was dispatched with the EXPLICIT text, not the derived one.
    assert apply_async_stub["args"] == [
        "explicit user-provided query",
        str(ORG_ID),
        7,
    ]
    assert apply_async_stub["opts"].get("queue") == "cdm"


# -------------------------------------------------------------------------
# ISC-25: auto-derived query text from control metadata
# -------------------------------------------------------------------------


def test_query_auto_text_from_control(client_factory, apply_async_stub):
    """ISC-25: query_text=None → task receives '<name>. <description>' truncated to 1000 chars."""
    control_id = uuid4()
    row = _scoped_control_row(
        control_id,
        name="Privileged Access Review",
        description="Reviewers must verify quarterly that privileged accounts remain justified.",
    )
    session = _FakeAsyncSession(scripted_rows=[row])
    apply_async_stub["result_payload"] = _ok_payload()

    client = client_factory(session)
    resp = client.post(
        f"/api/organizations/{ORG_ID}/cdm/query",
        json={"control_id": str(control_id), "query_text": None, "limit": 10},
    )

    assert resp.status_code == 200, resp.text
    assert apply_async_stub["args"] is not None
    derived_text = apply_async_stub["args"][0]
    assert derived_text == (
        "Privileged Access Review. "
        "Reviewers must verify quarterly that privileged accounts remain justified."
    )
    assert len(derived_text) <= 1000
    assert apply_async_stub["args"][1] == str(ORG_ID)
    assert apply_async_stub["args"][2] == 10


# -------------------------------------------------------------------------
# ISC-26: 404 when control is missing or in another org
# -------------------------------------------------------------------------


def test_query_control_not_found_404(client_factory, apply_async_stub):
    """ISC-26: scoped control lookup returns None → 404, no task dispatched."""
    control_id = uuid4()
    session = _FakeAsyncSession(scripted_rows=[None])

    client = client_factory(session)
    resp = client.post(
        f"/api/organizations/{ORG_ID}/cdm/query",
        json={"control_id": str(control_id), "query_text": "anything", "limit": 10},
    )

    assert resp.status_code == 404
    # The task must NOT have been dispatched.
    assert apply_async_stub["args"] is None


# -------------------------------------------------------------------------
# ISC-27: Celery .get() timeout → 504
# -------------------------------------------------------------------------


def test_query_celery_timeout_504(client_factory, apply_async_stub):
    """ISC-27: AsyncResult.get raises celery TimeoutError → HTTP 504 with proxy-safe detail."""
    control_id = uuid4()
    session = _FakeAsyncSession(scripted_rows=[_scoped_control_row(control_id)])
    apply_async_stub["result_exc"] = CeleryTimeoutError("worker took too long")

    client = client_factory(session)
    resp = client.post(
        f"/api/organizations/{ORG_ID}/cdm/query",
        json={"control_id": str(control_id), "query_text": "x", "limit": 10},
    )

    assert resp.status_code == 504
    assert "tim" in resp.json()["detail"].lower()


# -------------------------------------------------------------------------
# ISC-28: upstream LightRAG failure (propagated from worker) → 502
# -------------------------------------------------------------------------


def test_query_upstream_error_502(client_factory, apply_async_stub):
    """ISC-28: CDMQueryUpstreamError propagated by worker → HTTP 502."""
    control_id = uuid4()
    session = _FakeAsyncSession(scripted_rows=[_scoped_control_row(control_id)])
    apply_async_stub["result_exc"] = CDMQueryUpstreamError("LightRAG 503: unavailable")

    client = client_factory(session)
    resp = client.post(
        f"/api/organizations/{ORG_ID}/cdm/query",
        json={"control_id": str(control_id), "query_text": "x", "limit": 10},
    )

    assert resp.status_code == 502
    assert "LightRAG" in resp.json()["detail"]
