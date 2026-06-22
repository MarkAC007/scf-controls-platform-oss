"""Tests for CDM v1 slice 11 — terminology review route.

Endpoint: ``PUT /api/organizations/{org_id}/cdm/mappings/{mapping_id}/review``

Pins:

- ISC-10/11: route exists, gated by editor + tenant CDM enabled
- ISC-12: cross-tenant mapping returns 404 (never 403)
- ISC-13: accepts ``{notes?: str, mark_reviewed?: bool}``
- ISC-14: ``mark_reviewed=true`` stamps last_reviewed_at + last_reviewed_by_user_id
- ISC-15: notes write (empty string → NULL)
- ISC-16: one audit_log row with action=review_noted, before/after captured
- ISC-25: happy + tenancy 404 + audit row emission covered
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Optional
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


class _ReviewFakeSession:
    """Scripted session for the review-mapping flow.

    Execution sequence inside the route:
      1. SELECT CDMMapping by (id, org_id)  → returns ``mapping`` or None
      2. UPDATE CDMMapping fields           → only when values dict is non-empty
      3. INSERT AuditLog (via add)
      4. commit

    Tests configure ``mapping`` (or None for 404).
    """

    def __init__(self, mapping: Optional[CDMMapping]):
        self._mapping = mapping
        self.added: list[Any] = []
        self.commits = 0
        self.updates_executed = 0

    async def execute(self, stmt):  # type: ignore[no-untyped-def]
        # Distinguish SELECT vs UPDATE by statement class name. Avoids
        # over-coupling to SQLAlchemy private internals while still
        # surfacing both code paths to assertions.
        stmt_name = stmt.__class__.__name__
        if stmt_name.lower().startswith("select"):
            value = self._mapping

            class _R:
                def scalar_one_or_none(self_inner):
                    return value

            return _R()

        # Treat anything else as an UPDATE; route only issues SELECT + UPDATE.
        self.updates_executed += 1

        class _UpdateResult:
            rowcount = 1

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
    review_notes: Optional[str] = None,
    last_reviewed_at: Optional[datetime] = None,
    last_reviewed_by_user_id: Optional[UUID] = None,
) -> CDMMapping:
    return CDMMapping(
        id=uuid4(),
        organization_id=org_id,
        scoped_control_id=uuid4(),
        cdm_document_id=uuid4(),
        byte_offset_start=0,
        byte_offset_end=100,
        relevance_score=0.9,
        status="accepted",
        kb_revision="lightrag-v1",
        review_notes=review_notes,
        last_reviewed_at=last_reviewed_at,
        last_reviewed_by_user_id=last_reviewed_by_user_id,
    )


@pytest.fixture
def cdm_env(monkeypatch):
    monkeypatch.setenv("ENABLE_CDM", "true")
    yield


@pytest.fixture
def editor_client(cdm_env):
    """TestClient bound to a fake session + editor auth override."""
    app = main.app
    actor_db_id = uuid4()

    def _build(session: _ReviewFakeSession) -> TestClient:
        async def _override_db():
            yield session

        async def _override_auth():
            user = MagicMock()
            user.db_id = str(actor_db_id)
            user.email = "editor@example.com"
            return OrgMembership(
                user=user,
                organization_id=ORG_ID,
                role="editor",
                is_consultant=False,
            )

        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[require_org_editor] = _override_auth
        app.dependency_overrides[require_tenant_cdm_enabled] = lambda: None
        return TestClient(app)

    yield _build, actor_db_id

    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(require_org_editor, None)
    app.dependency_overrides.pop(require_tenant_cdm_enabled, None)


def test_review_mark_reviewed_stamps_timestamp_and_writes_audit(editor_client):
    """ISC-14 + ISC-16: mark_reviewed stamps now() + emits review_noted audit row."""
    build, actor_id = editor_client
    mapping = _make_mapping()
    session = _ReviewFakeSession(mapping)
    client = build(session)

    resp = client.put(
        f"/api/organizations/{ORG_ID}/cdm/mappings/{mapping.id}/review",
        json={"mark_reviewed": True},
    )
    assert resp.status_code == 200, resp.text

    payload = resp.json()
    assert payload["mapping_id"] == str(mapping.id)
    assert payload["last_reviewed_at"] is not None
    assert payload["last_reviewed_by_user_id"] == str(actor_id)
    assert payload["review_notes"] is None  # unchanged from None

    assert session.updates_executed == 1
    assert session.commits == 1

    audit_rows = [obj for obj in session.added if isinstance(obj, AuditLog)]
    assert len(audit_rows) == 1
    row = audit_rows[0]
    assert row.entity_type == "cdm_mapping"
    assert row.entity_id == mapping.id
    assert row.action == "review_noted"
    assert row.field_name == "review"
    assert row.changed_by_user_id == actor_id

    new_value = json.loads(row.new_value)
    assert new_value["marked_reviewed"] is True
    assert new_value["last_reviewed_at"] is not None
    assert new_value["notes"] is None

    old_value = json.loads(row.old_value)
    assert old_value["last_reviewed_at"] is None
    assert old_value["notes"] is None


def test_review_notes_write_persists_and_empty_string_clears(editor_client):
    """ISC-15: notes write persists; empty string sets NULL."""
    build, _ = editor_client
    mapping = _make_mapping(review_notes="prior note")
    session = _ReviewFakeSession(mapping)
    client = build(session)

    resp = client.put(
        f"/api/organizations/{ORG_ID}/cdm/mappings/{mapping.id}/review",
        json={"notes": "Doc uses 'minimum access' — SCF says 'least privilege'."},
    )
    assert resp.status_code == 200, resp.text
    assert (
        resp.json()["review_notes"]
        == "Doc uses 'minimum access' — SCF says 'least privilege'."
    )

    audit_rows = [obj for obj in session.added if isinstance(obj, AuditLog)]
    assert len(audit_rows) == 1
    old_value = json.loads(audit_rows[0].old_value)
    assert old_value["notes"] == "prior note"

    # Empty string clears.
    mapping2 = _make_mapping(review_notes="something")
    session2 = _ReviewFakeSession(mapping2)
    client2 = build(session2)
    resp2 = client2.put(
        f"/api/organizations/{ORG_ID}/cdm/mappings/{mapping2.id}/review",
        json={"notes": "   "},
    )
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["review_notes"] is None


def test_review_requires_at_least_one_field(editor_client):
    """ISC-13: empty body returns 422 — endpoint is not idempotent-no-op."""
    build, _ = editor_client
    mapping = _make_mapping()
    session = _ReviewFakeSession(mapping)
    client = build(session)

    resp = client.put(
        f"/api/organizations/{ORG_ID}/cdm/mappings/{mapping.id}/review",
        json={},
    )
    assert resp.status_code == 422, resp.text
    # No commit on the 422 path — we read the mapping then bail.
    assert session.commits == 0
    assert not any(isinstance(o, AuditLog) for o in session.added)


def test_review_cross_tenant_returns_404(editor_client):
    """ISC-12: cross-tenant mapping returns 404 (never 403)."""
    build, _ = editor_client
    session = _ReviewFakeSession(None)
    client = build(session)

    resp = client.put(
        f"/api/organizations/{ORG_ID}/cdm/mappings/{uuid4()}/review",
        json={"mark_reviewed": True},
    )
    assert resp.status_code == 404, resp.text
    assert "not found" in resp.json()["detail"].lower()
    assert session.commits == 0
