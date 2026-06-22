"""Tests for CDM v1 slice 5 — accept/dismiss endpoints + audit ledger.

Six required ISCs (21-26) and four extras for full coverage:
  - happy accept (state transition + audit row)
  - happy dismiss with reason (state + audit row + reason persisted)
  - happy dismiss without body (reason becomes NULL)
  - accept on already-accepted → 409
  - dismiss on already-dismissed → 409
  - accept on wrong-org → 404
  - dismiss on wrong-org → 404
  - dismiss with empty-string reason → reason becomes NULL
  - audit row new_value JSON carries kb_revision and timestamps
  - CDM disabled → 404
"""
from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["ENABLE_CDM"] = "true"

import main  # noqa: E402
from auth import OrgMembership, require_org_editor  # noqa: E402
from database import get_db  # noqa: E402
from models import AuditLog, CDMMapping  # noqa: E402
from services.cdm_tenancy import require_tenant_cdm_enabled  # noqa: E402

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")


class _FakeAsyncSession:
    """Scripted async session for accept/dismiss flow.

    Behaviour:
    - ``execute(select(...))`` pops a scripted mapping (or None) for the SELECT,
      then for any subsequent UPDATE returns an object with the configured
      ``rowcount`` (default 1; tests set 0 to simulate a race-loss).
    - ``add(obj)`` records added objects (the audit log row in success paths).
    - ``commit()`` increments a counter.
    """

    def __init__(self, mapping_to_return: Any, *, update_rowcount: int = 1):
        self._mapping_to_return = mapping_to_return
        self._update_rowcount = update_rowcount
        self.executed_statements: List[Any] = []
        self.added: List[Any] = []
        self.commits = 0

    async def execute(self, stmt):
        self.executed_statements.append(stmt)
        # First execute is the SELECT; subsequent are UPDATEs in our flow.
        is_first = len(self.executed_statements) == 1

        if is_first:
            value = self._mapping_to_return

            class _SelectResult:
                def scalar_one_or_none(self_inner):
                    return value

            return _SelectResult()

        rowcount = self._update_rowcount

        class _UpdateResult:
            @property
            def rowcount(self_inner):
                return rowcount

        return _UpdateResult()

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        return None


def _make_mapping(
    *,
    org_id: UUID = ORG_ID,
    status_value: str = "proposed",
    kb_revision: str = "lightrag-v1",
) -> CDMMapping:
    """Build a hydrated CDMMapping ORM-like object (no DB session)."""
    return CDMMapping(
        id=uuid4(),
        organization_id=org_id,
        scoped_control_id=uuid4(),
        cdm_document_id=uuid4(),
        byte_offset_start=10,
        byte_offset_end=40,
        relevance_score=0.95,
        status=status_value,
        kb_revision=kb_revision,
    )


@pytest.fixture
def cdm_env(monkeypatch):
    monkeypatch.setenv("ENABLE_CDM", "true")
    yield


@pytest.fixture
def client_factory(cdm_env):
    """Build TestClient with a fake async session + editor auth override."""
    app = main.app

    actor_db_id = uuid4()

    def _build(
        session: _FakeAsyncSession,
        *,
        actor_id: UUID = actor_db_id,
    ) -> TestClient:
        async def _override_db():
            yield session

        async def _override_auth():
            user = MagicMock()
            user.db_id = str(actor_id)
            user.email = "editor@example.com"
            return OrgMembership(
                user=user, organization_id=ORG_ID, role="editor", is_consultant=False
            )

        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[require_org_editor] = _override_auth
        app.dependency_overrides[require_tenant_cdm_enabled] = lambda: None
        return TestClient(app)

    yield _build, actor_db_id

    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(require_org_editor, None)
    app.dependency_overrides.pop(require_tenant_cdm_enabled, None)


# ───────────────────────── ACCEPT — happy path ─────────────────────────


def test_accept_happy_path_persists_state_and_audit_row(client_factory):
    """ISC-21: accept transitions status, stamps actor+timestamp, writes one audit row."""
    build, actor_id = client_factory
    mapping = _make_mapping()
    session = _FakeAsyncSession(mapping)

    client = build(session)
    resp = client.post(
        f"/api/organizations/{ORG_ID}/cdm/mappings/{mapping.id}/accept"
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "accepted"
    assert body["mapping_id"] == str(mapping.id)
    assert body["accepted_by_user_id"] == str(actor_id)
    assert body["accepted_at"]  # ISO-8601 string present

    # Audit row appended (and only one), correct shape.
    audit_rows = [obj for obj in session.added if isinstance(obj, AuditLog)]
    assert len(audit_rows) == 1
    row = audit_rows[0]
    assert row.entity_type == "cdm_mapping"
    assert row.entity_id == mapping.id
    assert row.action == "accept"
    assert row.field_name == "status"
    assert row.old_value == "proposed"
    assert row.changed_by_user_id == actor_id
    new_value = json.loads(row.new_value)
    assert new_value["status"] == "accepted"
    assert new_value["kb_revision"] == "lightrag-v1"
    assert new_value["accepted_at"]

    assert session.commits == 1


# ───────────────────────── DISMISS — happy paths ─────────────────────────


def test_dismiss_happy_path_with_reason_persists_state_and_audit_row(client_factory):
    """ISC-22: dismiss with reason transitions state and stamps the reason in audit row."""
    build, actor_id = client_factory
    mapping = _make_mapping()
    session = _FakeAsyncSession(mapping)

    client = build(session)
    resp = client.post(
        f"/api/organizations/{ORG_ID}/cdm/mappings/{mapping.id}/dismiss",
        json={"reason": "False positive — control already covered elsewhere"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "dismissed"
    assert body["reason"] == "False positive — control already covered elsewhere"
    assert body["dismissed_by_user_id"] == str(actor_id)

    audit_rows = [obj for obj in session.added if isinstance(obj, AuditLog)]
    assert len(audit_rows) == 1
    row = audit_rows[0]
    assert row.action == "dismiss"
    assert row.old_value == "proposed"
    new_value = json.loads(row.new_value)
    assert new_value["status"] == "dismissed"
    assert new_value["kb_revision"] == "lightrag-v1"
    assert new_value["reason"] == "False positive — control already covered elsewhere"


def test_dismiss_without_body_succeeds_with_null_reason(client_factory):
    """ISC-23: dismiss with no body succeeds; reason becomes NULL."""
    build, _ = client_factory
    mapping = _make_mapping()
    session = _FakeAsyncSession(mapping)

    client = build(session)
    resp = client.post(
        f"/api/organizations/{ORG_ID}/cdm/mappings/{mapping.id}/dismiss"
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "dismissed"
    assert body["reason"] is None

    audit_rows = [obj for obj in session.added if isinstance(obj, AuditLog)]
    assert len(audit_rows) == 1
    new_value = json.loads(audit_rows[0].new_value)
    assert new_value["reason"] is None


def test_dismiss_with_empty_reason_persists_null(client_factory):
    """Empty-string reason normalized to NULL — keeps DB queries simple."""
    build, _ = client_factory
    mapping = _make_mapping()
    session = _FakeAsyncSession(mapping)

    client = build(session)
    resp = client.post(
        f"/api/organizations/{ORG_ID}/cdm/mappings/{mapping.id}/dismiss",
        json={"reason": "   "},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["reason"] is None


# ───────────────────────── 409 — bad source state ─────────────────────────


def test_accept_on_already_accepted_returns_409(client_factory):
    """ISC-24: source state must be 'proposed' — already-accepted returns 409."""
    build, _ = client_factory
    mapping = _make_mapping(status_value="accepted")
    session = _FakeAsyncSession(mapping)

    client = build(session)
    resp = client.post(
        f"/api/organizations/{ORG_ID}/cdm/mappings/{mapping.id}/accept"
    )

    assert resp.status_code == 409, resp.text
    # No audit row written, no commit performed.
    audit_rows = [obj for obj in session.added if isinstance(obj, AuditLog)]
    assert audit_rows == []
    assert session.commits == 0


def test_dismiss_on_already_dismissed_returns_409(client_factory):
    """ISC-25: dismissed mapping cannot be re-dismissed."""
    build, _ = client_factory
    mapping = _make_mapping(status_value="dismissed")
    session = _FakeAsyncSession(mapping)

    client = build(session)
    resp = client.post(
        f"/api/organizations/{ORG_ID}/cdm/mappings/{mapping.id}/dismiss",
        json={"reason": "anything"},
    )

    assert resp.status_code == 409, resp.text
    audit_rows = [obj for obj in session.added if isinstance(obj, AuditLog)]
    assert audit_rows == []
    assert session.commits == 0


def test_accept_race_loser_returns_409(client_factory):
    """D-1: optimistic concurrency — UPDATE WHERE status='proposed' returns rowcount=0 → 409."""
    build, _ = client_factory
    mapping = _make_mapping()  # SELECT still sees 'proposed' but UPDATE races and loses
    session = _FakeAsyncSession(mapping, update_rowcount=0)

    client = build(session)
    resp = client.post(
        f"/api/organizations/{ORG_ID}/cdm/mappings/{mapping.id}/accept"
    )

    assert resp.status_code == 409, resp.text
    assert "no longer" in resp.json()["detail"].lower()


# ───────────────────────── 404 — wrong org ─────────────────────────


def test_accept_on_wrong_org_returns_404(client_factory):
    """ISC-26: mapping not in caller's org returns 404, never 200/409."""
    build, _ = client_factory
    # SELECT scoped to org returns None → 404.
    session = _FakeAsyncSession(mapping_to_return=None)

    client = build(session)
    resp = client.post(
        f"/api/organizations/{ORG_ID}/cdm/mappings/{uuid4()}/accept"
    )

    assert resp.status_code == 404, resp.text


def test_dismiss_on_wrong_org_returns_404(client_factory):
    build, _ = client_factory
    session = _FakeAsyncSession(mapping_to_return=None)

    client = build(session)
    resp = client.post(
        f"/api/organizations/{ORG_ID}/cdm/mappings/{uuid4()}/dismiss",
        json={"reason": "n/a"},
    )

    assert resp.status_code == 404, resp.text


# ───────────────────────── CDM flag off → 404 ─────────────────────────


def test_accept_requires_cdm_enabled(monkeypatch):
    """ISC-20 mirror: when ENABLE_CDM=false, endpoint returns 404."""
    monkeypatch.setenv("ENABLE_CDM", "false")

    app = main.app

    class _SettingsSession:
        async def execute(self, _stmt):
            class _R:
                def scalar_one_or_none(self_inner):
                    return None
            return _R()

    async def _override_db():
        yield _SettingsSession()

    app.dependency_overrides[get_db] = _override_db
    try:
        client = TestClient(app)
        resp = client.post(
            f"/api/organizations/{ORG_ID}/cdm/mappings/{uuid4()}/accept"
        )
        assert resp.status_code == 404
        assert "CDM module not enabled" in resp.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_db, None)
