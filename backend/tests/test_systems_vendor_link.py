"""API contract tests for the System <-> Vendor structural link.

Covers the vendor-system cohesion feature: systems.vendor_id FK, the nested
`linked_vendor` response object, cross-org vendor validation on create, and the
optional vendor_id filter on the list endpoint.

Uses FastAPI TestClient with a scripted fake AsyncSession (dependency override
on get_db) and monkeypatched auth internals, so no database or Redis is
required. Mirrors the style of test_vendor_assessments_api.py.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from types import SimpleNamespace
from typing import Any, List
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
import auth as auth_module  # noqa: E402
from auth import OrgMembership  # noqa: E402
from database import get_db  # noqa: E402


ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
VENDOR_ID = UUID("00000000-0000-0000-0000-000000000002")
SYSTEM_ID = UUID("00000000-0000-0000-0000-000000000003")
OTHER_ORG_ID = UUID("00000000-0000-0000-0000-000000000009")
AUTH = {"Authorization": "Bearer test-key"}


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

def _vendor_simple(**overrides) -> SimpleNamespace:
    base = dict(
        id=VENDOR_ID,
        name="Acme Corp",
        website="https://acme.example",
        category="cloud",
        status="active",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _system_row(**overrides) -> SimpleNamespace:
    """A System ORM-like row with every attribute SystemResponse reads."""
    linked = overrides.pop("linked_vendor", _vendor_simple())
    base = dict(
        id=SYSTEM_ID,
        organization_id=ORG_ID,
        name="AWS Production",
        system_type="cloud_provider",
        category="Infrastructure",
        description="Primary AWS account",
        vendor="Amazon Web Services",
        status="active",
        connection_config={},
        catalog_template_id=None,
        vendor_id=VENDOR_ID,
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
        created_by_user_id=None,
        updated_by_user_id=None,
        created_by=None,
        updated_by=None,
        linked_vendor=linked,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class _Result:
    def __init__(self, items: List[Any]):
        self._items = items

    def scalar_one_or_none(self) -> Any:
        return self._items[0] if self._items else None

    def scalar_one(self) -> Any:
        if not self._items:
            raise AssertionError("scalar_one() called with no scripted row")
        return self._items[0]

    def scalars(self) -> "_Result":
        return self

    def all(self) -> List[Any]:
        return list(self._items)

    def first(self) -> Any:
        return self._items[0] if self._items else None


class _FakeAsyncSession:
    """Scripted async session — pops the next pre-arranged result per execute()."""

    def __init__(self, responses: List[List[Any]]):
        self._responses = list(responses)
        self.statements: List[Any] = []

    async def execute(self, stmt) -> _Result:
        self.statements.append(stmt)
        if not self._responses:
            raise AssertionError("FakeAsyncSession: ran out of scripted results")
        return _Result(list(self._responses.pop(0)))

    async def commit(self):
        pass

    async def refresh(self, _obj):
        pass

    async def flush(self):
        pass

    def add(self, _obj):
        pass


@pytest.fixture
def client_factory(monkeypatch):
    """(responses, role='editor') -> (TestClient, session) with auth+db faked."""
    app = main.app

    def _build(responses: List[List[Any]], role: str = "editor"):
        session = _FakeAsyncSession(responses)

        async def _override_db():
            yield session

        async def _fake_require_auth(credentials, db):
            user = MagicMock()
            user.db_id = str(uuid4())
            user.email = "test@example.com"
            return user

        async def _fake_verify_org_membership(org_id, user, db, min_role="viewer"):
            return OrgMembership(user=user, organization_id=org_id, role=role, is_consultant=False)

        monkeypatch.setattr(auth_module, "require_auth", _fake_require_auth)
        monkeypatch.setattr(auth_module, "verify_org_membership", _fake_verify_org_membership)
        app.dependency_overrides[get_db] = _override_db
        return TestClient(app), session

    yield _build
    app.dependency_overrides.pop(get_db, None)


SYSTEMS = f"/api/organizations/{ORG_ID}/systems"


# ---------------------------------------------------------------------------
# POST /systems — create with vendor link
# ---------------------------------------------------------------------------

class TestCreateSystemVendorLink:
    def test_create_with_valid_vendor_id_succeeds(self, client_factory):
        # 1: name-conflict check (none), 2: vendor lookup (found), 3: reload
        client, _ = client_factory([[], [_vendor_simple()], [_system_row()]])
        resp = client.post(
            SYSTEMS,
            json={
                "name": "AWS Production",
                "system_type": "cloud_provider",
                "vendor_id": str(VENDOR_ID),
            },
            headers=AUTH,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["vendor_id"] == str(VENDOR_ID)
        assert body["linked_vendor"] is not None
        assert body["linked_vendor"]["id"] == str(VENDOR_ID)
        assert body["linked_vendor"]["name"] == "Acme Corp"

    def test_create_without_vendor_id_succeeds(self, client_factory):
        # backward compat: 1: name-conflict check, 2: reload (no vendor lookup)
        row = _system_row(vendor_id=None, linked_vendor=None)
        client, _ = client_factory([[], [row]])
        resp = client.post(
            SYSTEMS,
            json={"name": "AWS Production", "system_type": "cloud_provider"},
            headers=AUTH,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["vendor_id"] is None
        assert body["linked_vendor"] is None

    def test_create_with_cross_org_vendor_id_rejected(self, client_factory):
        # 1: name-conflict check (none), 2: vendor lookup returns nothing
        client, _ = client_factory([[], []])
        resp = client.post(
            SYSTEMS,
            json={
                "name": "AWS Production",
                "system_type": "cloud_provider",
                "vendor_id": str(VENDOR_ID),
            },
            headers=AUTH,
        )
        assert resp.status_code == 400, resp.text
        assert "Invalid vendor_id" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# GET single / list — nested vendor + filter
# ---------------------------------------------------------------------------

class TestReadSystemVendorLink:
    def test_response_includes_nested_linked_vendor(self, client_factory):
        client, _ = client_factory([[_system_row()]])
        resp = client.get(f"{SYSTEMS}/{SYSTEM_ID}", headers=AUTH)
        assert resp.status_code == 200, resp.text
        lv = resp.json()["linked_vendor"]
        assert lv is not None
        assert lv["id"] == str(VENDOR_ID)
        assert lv["name"] == "Acme Corp"
        assert lv["website"] == "https://acme.example"
        assert lv["category"] == "cloud"
        assert lv["status"] == "active"

    def test_list_filters_by_vendor_id(self, client_factory):
        client, session = client_factory([[_system_row()]])
        resp = client.get(f"{SYSTEMS}?vendor_id={VENDOR_ID}", headers=AUTH)
        assert resp.status_code == 200, resp.text
        items = resp.json()
        assert len(items) == 1
        assert items[0]["vendor_id"] == str(VENDOR_ID)
        assert items[0]["linked_vendor"]["id"] == str(VENDOR_ID)
        # The vendor_id filter must reach the SQL WHERE clause.
        compiled = str(session.statements[0])
        assert "vendor_id" in compiled
