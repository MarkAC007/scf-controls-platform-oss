"""Tests for CDM v1 slice 7 — per-tenant flag + hard caps.

Helper-level tests for `services.cdm_tenancy` cover:
- per-tenant flag overriding env (both directions)
- cap helpers raising 409 with structured body
- env overrides for cap thresholds

Endpoint-level tests cover wiring of the helpers into the live routes.
"""
from __future__ import annotations

import os
import sys
from typing import Any, List, Optional
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["ENABLE_CDM"] = "true"

import main  # noqa: E402
from auth import OrgMembership, require_org_editor, require_org_viewer  # noqa: E402
from database import get_db  # noqa: E402
from services import cdm_tenancy  # noqa: E402

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")


# ───────────────────────── Helper-level ─────────────────────────


class _ScalarSession:
    """Tiny async session whose execute() returns a scripted .scalar_one_or_none()."""

    def __init__(self, scalar_value: Any = None):
        self._value = scalar_value

    async def execute(self, _stmt):
        captured = self._value

        class _R:
            def scalar_one_or_none(self_inner):
                return captured

            def scalar(self_inner):
                return captured

        return _R()


@pytest.mark.asyncio
async def test_per_tenant_false_overrides_env_true(monkeypatch):
    """ISC-5: tenant cdm_enabled=false wins over env=true."""
    monkeypatch.setenv("ENABLE_CDM", "true")
    session = _ScalarSession({"cdm_enabled": False})
    assert await cdm_tenancy.get_tenant_cdm_enabled(session, ORG_ID) is False


@pytest.mark.asyncio
async def test_per_tenant_true_overrides_env_false(monkeypatch):
    """ISC-6: tenant cdm_enabled=true wins over env=false."""
    monkeypatch.setenv("ENABLE_CDM", "false")
    session = _ScalarSession({"cdm_enabled": True})
    assert await cdm_tenancy.get_tenant_cdm_enabled(session, ORG_ID) is True


@pytest.mark.asyncio
async def test_missing_tenant_value_falls_back_to_env_true(monkeypatch):
    """ISC-16 / fallback: no tenant value → env wins."""
    monkeypatch.setenv("ENABLE_CDM", "true")
    session = _ScalarSession({"other_setting": "x"})
    assert await cdm_tenancy.get_tenant_cdm_enabled(session, ORG_ID) is True


@pytest.mark.asyncio
async def test_missing_tenant_value_falls_back_to_env_false(monkeypatch):
    """ISC-18: no tenant value + env=false → False."""
    monkeypatch.setenv("ENABLE_CDM", "false")
    session = _ScalarSession({})
    assert await cdm_tenancy.get_tenant_cdm_enabled(session, ORG_ID) is False


@pytest.mark.asyncio
async def test_require_dep_raises_404_when_disabled(monkeypatch):
    """ISC-4: the FastAPI dep raises 404 when tenant flag is off."""
    monkeypatch.setenv("ENABLE_CDM", "false")
    session = _ScalarSession({})
    with pytest.raises(HTTPException) as exc_info:
        await cdm_tenancy.require_tenant_cdm_enabled(ORG_ID, session)  # type: ignore[arg-type]
    assert exc_info.value.status_code == 404


# ───────────────────────── Cap helpers ─────────────────────────


@pytest.mark.asyncio
async def test_document_cap_raises_at_threshold(monkeypatch):
    """ISC-7: doc count >= cap → 409 with cap='documents'."""
    monkeypatch.setenv("CDM_CAP_DOCUMENTS", "5")
    session = _ScalarSession(5)
    with pytest.raises(HTTPException) as exc_info:
        await cdm_tenancy.assert_cdm_document_count_cap(session, ORG_ID)  # type: ignore[arg-type]
    assert exc_info.value.status_code == 409
    detail = exc_info.value.detail
    assert isinstance(detail, dict)
    assert detail["cap"] == "documents"
    assert "5/5" in detail["detail"]


@pytest.mark.asyncio
async def test_document_cap_passes_below_threshold(monkeypatch):
    """Doc count below cap → no raise."""
    monkeypatch.setenv("CDM_CAP_DOCUMENTS", "5")
    session = _ScalarSession(4)
    # No exception raised.
    await cdm_tenancy.assert_cdm_document_count_cap(session, ORG_ID)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_token_cap_raises_when_projection_exceeds(monkeypatch):
    """ISC-8: current + incoming > cap → 409 with cap='tokens'."""
    monkeypatch.setenv("CDM_CAP_TOKENS", "1000")
    session = _ScalarSession(900)
    with pytest.raises(HTTPException) as exc_info:
        await cdm_tenancy.assert_cdm_token_count_cap(session, ORG_ID, 200)  # type: ignore[arg-type]
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["cap"] == "tokens"


@pytest.mark.asyncio
async def test_token_cap_passes_when_projection_within(monkeypatch):
    monkeypatch.setenv("CDM_CAP_TOKENS", "1000")
    session = _ScalarSession(500)
    await cdm_tenancy.assert_cdm_token_count_cap(session, ORG_ID, 200)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_proposed_mappings_cap_raises_at_threshold(monkeypatch):
    """ISC-9: proposed count >= cap → 409 with cap='proposed_mappings'."""
    monkeypatch.setenv("CDM_CAP_PROPOSED_MAPPINGS", "10")
    session = _ScalarSession(10)
    with pytest.raises(HTTPException) as exc_info:
        await cdm_tenancy.assert_cdm_proposed_mappings_cap(session, ORG_ID)  # type: ignore[arg-type]
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["cap"] == "proposed_mappings"


@pytest.mark.asyncio
async def test_caps_default_to_brief_values(monkeypatch):
    """Defaults: 250 docs / 50M tokens / 10k proposed-mappings."""
    monkeypatch.delenv("CDM_CAP_DOCUMENTS", raising=False)
    monkeypatch.delenv("CDM_CAP_TOKENS", raising=False)
    monkeypatch.delenv("CDM_CAP_PROPOSED_MAPPINGS", raising=False)
    assert cdm_tenancy.get_cdm_documents_cap() == 250
    assert cdm_tenancy.get_cdm_tokens_cap() == 50_000_000
    assert cdm_tenancy.get_cdm_proposed_mappings_cap() == 10_000


@pytest.mark.asyncio
async def test_cap_fail_open_on_query_error(monkeypatch):
    """D-4: cap helpers fail open on SQL errors — log and continue."""
    monkeypatch.setenv("CDM_CAP_DOCUMENTS", "5")

    class _BoomSession:
        async def execute(self, _stmt):
            raise RuntimeError("simulated DB hiccup")

    # No exception raised — request continues.
    await cdm_tenancy.assert_cdm_document_count_cap(_BoomSession(), ORG_ID)  # type: ignore[arg-type]


# ───────────────────────── Endpoint wiring ─────────────────────────


@pytest.fixture
def cdm_env(monkeypatch):
    monkeypatch.setenv("ENABLE_CDM", "true")
    yield


@pytest.fixture
def auth_override_editor():
    app = main.app
    actor_id = uuid4()

    async def _override():
        user = MagicMock()
        user.db_id = str(actor_id)
        user.email = "editor@example.com"
        return OrgMembership(
            user=user, organization_id=ORG_ID, role="editor", is_consultant=False
        )

    app.dependency_overrides[require_org_editor] = _override
    yield actor_id
    app.dependency_overrides.pop(require_org_editor, None)


def test_upload_blocked_at_document_cap(cdm_env, auth_override_editor, monkeypatch):
    """ISC-19: upload at the doc cap returns 409 with cap='documents'."""
    monkeypatch.setenv("CDM_CAP_DOCUMENTS", "3")
    app = main.app

    # Async session whose .execute() returns 3 for the doc-count query.
    # The require_tenant_cdm_enabled dep's tenant lookup must come first,
    # so we feed scripted responses in order: settings dict → doc count.
    scripted = [{"cdm_enabled": True}, 3]

    class _Sess:
        async def execute(self, _stmt):
            value = scripted.pop(0)

            class _R:
                def scalar_one_or_none(self_inner):
                    return value

                def scalar(self_inner):
                    return value

            return _R()

    async def _override_db():
        yield _Sess()

    app.dependency_overrides[get_db] = _override_db
    try:
        client = TestClient(app)
        resp = client.post(
            f"/api/organizations/{ORG_ID}/cdm/upload",
            files={"file": ("a.txt", b"some content", "text/plain")},
        )
        assert resp.status_code == 409, resp.text
        body = resp.json()
        # FastAPI wraps HTTPException.detail (which is itself a dict) in body["detail"].
        assert body["detail"]["cap"] == "documents"
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_upload_blocked_at_token_cap(cdm_env, auth_override_editor, monkeypatch):
    """ISC-20: upload over token cap returns 409 with cap='tokens'."""
    monkeypatch.setenv("CDM_CAP_DOCUMENTS", "100")
    monkeypatch.setenv("CDM_CAP_TOKENS", "10")
    app = main.app

    # Order of .execute() calls in upload after the dep chain:
    #   1. require_tenant_cdm_enabled → settings dict
    #   2. assert_cdm_document_count_cap → current doc count (low)
    #   3. assert_cdm_token_count_cap → current word total (already at cap)
    scripted = [{"cdm_enabled": True}, 0, 10]

    class _Sess:
        async def execute(self, _stmt):
            value = scripted.pop(0)

            class _R:
                def scalar_one_or_none(self_inner):
                    return value

                def scalar(self_inner):
                    return value

            return _R()

    async def _override_db():
        yield _Sess()

    app.dependency_overrides[get_db] = _override_db
    try:
        client = TestClient(app)
        resp = client.post(
            f"/api/organizations/{ORG_ID}/cdm/upload",
            files={"file": ("a.txt", b"x" * 600, "text/plain")},  # ~100 projected words
        )
        assert resp.status_code == 409, resp.text
        body = resp.json()
        assert body["detail"]["cap"] == "tokens"
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_compute_mappings_blocked_at_proposed_cap(cdm_env, auth_override_editor, monkeypatch):
    """ISC-21: dispatch returns 409 when the proposed-mappings cap is reached."""
    monkeypatch.setenv("CDM_CAP_PROPOSED_MAPPINGS", "5")
    app = main.app

    # Dep order:
    #   1. require_tenant_cdm_enabled → settings dict
    #   2. assert_cdm_proposed_mappings_cap → current proposed count (at cap)
    scripted = [{"cdm_enabled": True}, 5]

    class _Sess:
        async def execute(self, _stmt):
            value = scripted.pop(0)

            class _R:
                def scalar_one_or_none(self_inner):
                    return value

                def scalar(self_inner):
                    return value

            return _R()

    async def _override_db():
        yield _Sess()

    app.dependency_overrides[get_db] = _override_db
    try:
        client = TestClient(app)
        resp = client.post(
            f"/api/organizations/{ORG_ID}/cdm/compute-mappings"
        )
        assert resp.status_code == 409, resp.text
        body = resp.json()
        assert body["detail"]["cap"] == "proposed_mappings"
    finally:
        app.dependency_overrides.pop(get_db, None)
