"""Tests for CDM bulk-accept / bulk-dismiss endpoints.

Mirrors test_cdm_accept_dismiss invariants but exercises the batched path:
  - partial success: proposed ids → accepted/dismissed, others → skipped
  - cross-tenant ids → not_found (existence never leaked)
  - request-size cap (>200) → 422
  - reason persisted to every dismissed row
  - one AuditLog row per successfully transitioned mapping
  - CDM disabled → 404
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["ENABLE_CDM"] = "true"

import main  # noqa: E402
from auth import OrgMembership, require_org_editor  # noqa: E402
from database import get_db  # noqa: E402
from models import AuditLog, CDMMapping  # noqa: E402
from services.cdm_tenancy import require_tenant_cdm_enabled  # noqa: E402

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")


def _mapping(mapping_id: UUID, status: str = "proposed", kb_revision: str = "rev-1"):
    """Lightweight CDMMapping stand-in for SELECT scalars().all()."""
    m = MagicMock(spec=CDMMapping)
    m.id = mapping_id
    m.organization_id = ORG_ID
    m.status = status
    m.kb_revision = kb_revision
    return m


class _BulkSession:
    """Scripted async session for the bulk-transition flow.

    First execute() — the bulk SELECT — returns ``loaded_mappings`` as
    ``scalars().all()``. Subsequent execute() calls are UPDATEs whose
    rowcount comes from ``update_rowcounts`` (default 1 each, in order).
    """

    def __init__(
        self,
        loaded_mappings: List[Any],
        update_rowcounts: Optional[List[int]] = None,
    ) -> None:
        self.loaded_mappings = loaded_mappings
        self.update_rowcounts = list(update_rowcounts or [])
        self.added: List[Any] = []
        self.commits = 0
        self._selected = False

    async def execute(self, _stmt):
        if not self._selected:
            self._selected = True
            mappings = self.loaded_mappings

            class _Scalars:
                def __init__(self, items):
                    self._items = items

                def all(self):
                    return self._items

            class _SelectResult:
                def __init__(self, items):
                    self._items = items

                def scalars(self):
                    return _Scalars(self._items)

            return _SelectResult(mappings)

        rc = self.update_rowcounts.pop(0) if self.update_rowcounts else 1

        class _UpdateResult:
            rowcount = rc

        return _UpdateResult()

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


@pytest.fixture
def client_factory():
    """Yield (build_client, override_cleanup) — same pattern as test_cdm_accept_dismiss."""
    app = main.app

    def build(session: _BulkSession) -> TestClient:
        async def _override_db():
            yield session

        async def _override_cdm_flag():
            return None

        def _override_membership():
            user = MagicMock()
            user.db_id = UUID("00000000-0000-0000-0000-000000000099")
            membership = MagicMock(spec=OrgMembership)
            membership.user = user
            return membership

        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[require_tenant_cdm_enabled] = _override_cdm_flag
        app.dependency_overrides[require_org_editor] = _override_membership
        return TestClient(app)

    yield build, None

    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(require_tenant_cdm_enabled, None)
    app.dependency_overrides.pop(require_org_editor, None)


# ───────────────────────── happy path: bulk-accept ─────────────────────────


def test_bulk_accept_all_proposed_transitions_all(client_factory):
    build, _ = client_factory
    ids = [uuid4(), uuid4(), uuid4()]
    session = _BulkSession(loaded_mappings=[_mapping(i) for i in ids])

    client = build(session)
    resp = client.post(
        f"/api/organizations/{ORG_ID}/cdm/mappings/bulk-accept",
        json={"mapping_ids": [str(i) for i in ids]},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert sorted(body["accepted"]) == sorted(str(i) for i in ids)
    assert body["skipped"] == []
    assert body["not_found"] == []
    # one audit row per accepted mapping
    audit_rows = [o for o in session.added if isinstance(o, AuditLog)]
    assert len(audit_rows) == 3
    assert all(a.action == "accept" for a in audit_rows)
    assert all(json.loads(a.new_value)["status"] == "accepted" for a in audit_rows)
    assert session.commits == 1


# ───────────────────────── happy path: bulk-dismiss with reason ─────────────


def test_bulk_dismiss_with_reason_persisted_per_row(client_factory):
    build, _ = client_factory
    ids = [uuid4(), uuid4()]
    session = _BulkSession(loaded_mappings=[_mapping(i) for i in ids])

    client = build(session)
    resp = client.post(
        f"/api/organizations/{ORG_ID}/cdm/mappings/bulk-dismiss",
        json={"mapping_ids": [str(i) for i in ids], "reason": "duplicate of policy v3"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert sorted(body["dismissed"]) == sorted(str(i) for i in ids)
    audit_rows = [o for o in session.added if isinstance(o, AuditLog)]
    assert len(audit_rows) == 2
    for a in audit_rows:
        payload = json.loads(a.new_value)
        assert payload["status"] == "dismissed"
        assert payload["reason"] == "duplicate of policy v3"


# ───────────────────────── partial: mix of states ─────────────────────────


def test_bulk_accept_skips_already_accepted_mappings(client_factory):
    build, _ = client_factory
    proposed = uuid4()
    already = uuid4()
    session = _BulkSession(
        loaded_mappings=[_mapping(proposed), _mapping(already, status="accepted")]
    )

    client = build(session)
    resp = client.post(
        f"/api/organizations/{ORG_ID}/cdm/mappings/bulk-accept",
        json={"mapping_ids": [str(proposed), str(already)]},
    )

    body = resp.json()
    assert body["accepted"] == [str(proposed)]
    assert body["skipped"] == [str(already)]
    assert body["not_found"] == []


# ───────────────────────── race: UPDATE rowcount=0 falls into skipped ──────


def test_bulk_accept_treats_race_loss_as_skipped(client_factory):
    build, _ = client_factory
    a, b = uuid4(), uuid4()
    # Both load as proposed but second UPDATE returns rowcount=0 (lost the race).
    session = _BulkSession(
        loaded_mappings=[_mapping(a), _mapping(b)],
        update_rowcounts=[1, 0],
    )

    client = build(session)
    resp = client.post(
        f"/api/organizations/{ORG_ID}/cdm/mappings/bulk-accept",
        json={"mapping_ids": [str(a), str(b)]},
    )

    body = resp.json()
    assert body["accepted"] == [str(a)]
    assert body["skipped"] == [str(b)]


# ───────────────────────── tenancy: missing ids → not_found ─────────────────


def test_bulk_accept_unknown_ids_go_to_not_found(client_factory):
    build, _ = client_factory
    known = uuid4()
    missing = uuid4()
    # Only `known` is returned by the SELECT.
    session = _BulkSession(loaded_mappings=[_mapping(known)])

    client = build(session)
    resp = client.post(
        f"/api/organizations/{ORG_ID}/cdm/mappings/bulk-accept",
        json={"mapping_ids": [str(known), str(missing)]},
    )

    body = resp.json()
    assert body["accepted"] == [str(known)]
    assert body["not_found"] == [str(missing)]


# ───────────────────────── input cap ─────────────────────────


def test_bulk_accept_rejects_more_than_200_ids(client_factory):
    build, _ = client_factory
    session = _BulkSession(loaded_mappings=[])
    client = build(session)

    too_many = [str(uuid4()) for _ in range(201)]
    resp = client.post(
        f"/api/organizations/{ORG_ID}/cdm/mappings/bulk-accept",
        json={"mapping_ids": too_many},
    )

    assert resp.status_code == 422, resp.text


def test_bulk_accept_rejects_empty_list(client_factory):
    build, _ = client_factory
    session = _BulkSession(loaded_mappings=[])
    client = build(session)

    resp = client.post(
        f"/api/organizations/{ORG_ID}/cdm/mappings/bulk-accept",
        json={"mapping_ids": []},
    )

    assert resp.status_code == 422


# ───────────────────────── CDM flag off → 404 ─────────────────────────


def test_bulk_accept_requires_cdm_enabled(monkeypatch):
    monkeypatch.setenv("ENABLE_CDM", "false")
    app = main.app

    class _NullSession:
        async def execute(self, _s):
            class _R:
                def scalar_one_or_none(self_inner):
                    return None

            return _R()

    async def _override_db():
        yield _NullSession()

    app.dependency_overrides[get_db] = _override_db
    try:
        client = TestClient(app)
        resp = client.post(
            f"/api/organizations/{ORG_ID}/cdm/mappings/bulk-accept",
            json={"mapping_ids": [str(uuid4())]},
        )
        assert resp.status_code == 404
        assert "CDM module not enabled" in resp.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_db, None)
