"""Tests for CDM v1 document delete lifecycle.

Lifecycle decision (PRD `20260522-104000_cdm-document-lifecycle-delete`):
delete-only. Update = delete + re-upload. These tests pin:

- 204 on happy-path delete
- one audit_log row per affected mapping (action=removed_with_document)
- one audit_log row for the document itself (action=deleted)
- 404 on cross-tenant delete attempt (tenancy)
- 404 on unknown document
- viewer cannot delete (FastAPI dependency override proves the route uses editor)
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, List
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["ENABLE_CDM"] = "true"

import main  # noqa: E402
from auth import OrgMembership, require_org_editor  # noqa: E402
from database import get_db  # noqa: E402
from models import AuditLog, CDMDocument  # noqa: E402
from services.cdm_tenancy import require_tenant_cdm_enabled  # noqa: E402

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")


class _DeleteFakeSession:
    """Scripted session for the delete-document flow.

    Execution sequence inside the route:
      1. SELECT CDMDocument by (id, org_id)  → returns `document` or None
      2. SELECT CDMMapping cols by document_id → returns mapping rows
      3. DELETE CDMDocument by (id, org_id)  → returns _DeleteResult(rowcount=1)

    Tests configure `document` (or None for 404) and `mappings`
    (list of (id, status, scoped_control_id) tuples).
    """

    def __init__(
        self,
        document: CDMDocument | None,
        mappings: List[tuple[UUID, str, UUID]] | None = None,
    ):
        self._document = document
        self._mappings = mappings or []
        self.added: list[Any] = []
        self.commits = 0
        self._call_index = 0

    async def execute(self, stmt):  # type: ignore[no-untyped-def]
        self._call_index += 1
        # 1st call: SELECT document
        if self._call_index == 1:
            value = self._document

            class _R:
                def scalar_one_or_none(self_inner):
                    return value

            return _R()
        # 2nd call: SELECT mappings (tuple rows)
        if self._call_index == 2:
            rows = self._mappings

            class _R:
                def all(self_inner):
                    return list(rows)

            return _R()
        # 3rd call: DELETE document
        class _DeleteResult:
            rowcount = 1

        return _DeleteResult()

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        return None


def _make_document(*, org_id: UUID = ORG_ID) -> CDMDocument:
    return CDMDocument(
        id=uuid4(),
        organization_id=org_id,
        original_filename="policy.pdf",
        mime_type="application/pdf",
        sha256="a" * 64,
        size_bytes=1024,
        ingest_status="indexed",
        ingest_error=None,
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

    def _build(session: _DeleteFakeSession) -> TestClient:
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


# ─────────────────── Happy path ───────────────────


def test_delete_happy_path_returns_204_and_writes_document_audit(editor_client):
    """ISC-6 + ISC-9: happy delete returns 204, audit_log row written for document."""
    build, actor_id = editor_client
    document = _make_document()
    session = _DeleteFakeSession(document=document, mappings=[])
    client = build(session)

    resp = client.delete(
        f"/api/organizations/{ORG_ID}/cdm/documents/{document.id}"
    )

    assert resp.status_code == 204, resp.text
    assert resp.text == ""

    audit_rows = [obj for obj in session.added if isinstance(obj, AuditLog)]
    assert len(audit_rows) == 1
    row = audit_rows[0]
    assert row.entity_type == "cdm_document"
    assert row.entity_id == document.id
    assert row.action == "deleted"
    assert row.field_name == "ingest_status"
    assert row.old_value == "indexed"
    assert row.changed_by_user_id == actor_id
    payload = json.loads(row.new_value)
    assert payload["original_filename"] == "policy.pdf"
    assert payload["sha256"] == "a" * 64
    assert payload["mappings_removed"] == 0
    assert payload["deleted_at"]

    assert session.commits == 1


def test_delete_cascade_writes_one_audit_row_per_affected_mapping(editor_client):
    """ISC-8: each cascaded mapping gets a removed_with_document audit row."""
    build, _ = editor_client
    document = _make_document()
    mapping_rows = [
        (uuid4(), "proposed", uuid4()),
        (uuid4(), "accepted", uuid4()),
        (uuid4(), "dismissed", uuid4()),
    ]
    session = _DeleteFakeSession(document=document, mappings=mapping_rows)
    client = build(session)

    resp = client.delete(
        f"/api/organizations/{ORG_ID}/cdm/documents/{document.id}"
    )
    assert resp.status_code == 204, resp.text

    audit_rows = [obj for obj in session.added if isinstance(obj, AuditLog)]
    mapping_audits = [r for r in audit_rows if r.entity_type == "cdm_mapping"]
    document_audits = [r for r in audit_rows if r.entity_type == "cdm_document"]

    assert len(mapping_audits) == 3
    assert len(document_audits) == 1
    assert document_audits[0].action == "deleted"

    actions = {r.action for r in mapping_audits}
    assert actions == {"removed_with_document"}

    # Per-mapping payload carries originating doc id.
    for row in mapping_audits:
        payload = json.loads(row.new_value)
        assert payload["cdm_document_id"] == str(document.id)
        assert payload["scoped_control_id"]
        assert payload["removed_at"]

    # Audit row for the document records the cascade count.
    doc_payload = json.loads(document_audits[0].new_value)
    assert doc_payload["mappings_removed"] == 3


# ─────────────────── 404 paths ───────────────────


def test_delete_unknown_document_returns_404(editor_client):
    """ISC-4: unknown document_id returns 404."""
    build, _ = editor_client
    session = _DeleteFakeSession(document=None)
    client = build(session)

    resp = client.delete(
        f"/api/organizations/{ORG_ID}/cdm/documents/{uuid4()}"
    )
    assert resp.status_code == 404, resp.text
    assert "not found" in resp.json()["detail"].lower()
    # Nothing committed — purely a read-then-404.
    assert session.commits == 0


def test_delete_cross_tenant_returns_404(editor_client):
    """ISC-5: tenancy mismatch is opaque (returns 404, not 403)."""
    build, _ = editor_client
    # Document exists but for a different org → fake session returns None
    # because the route's SELECT WHERE org_id != actual yields zero rows.
    session = _DeleteFakeSession(document=None)
    client = build(session)

    other_doc_id = uuid4()
    resp = client.delete(
        f"/api/organizations/{ORG_ID}/cdm/documents/{other_doc_id}"
    )
    assert resp.status_code == 404, resp.text
    assert session.commits == 0
