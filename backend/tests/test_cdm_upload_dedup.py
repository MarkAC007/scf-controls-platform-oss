"""Tests for graceful duplicate handling on CDM upload.

Behavior pinned here:

- Byte-identical re-upload (same org, healthy ingest) → 409 with a plain
  string detail naming the existing document; no new row, no ingest enqueue.
- Same filename with different content ("updated version") → predecessors
  are superseded: per-mapping audit rows (action=removed_with_document),
  per-document audit rows (action=superseded, new_value carries the
  superseding document id), one bulk delete, then the fresh insert.
- A failed prior attempt with identical content is superseded, not 409'd
  (retry path stays open).
- Supersede deletes run BEFORE the document-count cap check so replacing a
  document at the cap works.
- The dedup lookup is organization-scoped.
- Response gains superseded_document_ids / superseded_mappings_removed with
  backward-compatible defaults.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.sql.dml import Delete

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["ENABLE_CDM"] = "true"

import main  # noqa: E402
import api.cdm as cdm_router  # noqa: E402
from auth import OrgMembership, require_org_editor, require_org_viewer  # noqa: E402
from database import get_db  # noqa: E402
from models import AuditLog, CDMDocument  # noqa: E402
from services import cdm_storage  # noqa: E402
from services.cdm_tenancy import require_tenant_cdm_enabled  # noqa: E402

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")

PAYLOAD_V1 = b"policy content version one"
PAYLOAD_V2 = b"policy content version two - revised"

# sha256 of PAYLOAD_V1, computed the same way the route does.
import hashlib  # noqa: E402

SHA_V1 = hashlib.sha256(PAYLOAD_V1).hexdigest()
SHA_V2 = hashlib.sha256(PAYLOAD_V2).hexdigest()


def _make_doc(
    *,
    filename: str = "policy.txt",
    sha256: str = SHA_V1,
    ingest_status: str = "indexed",
    org_id: UUID = ORG_ID,
) -> CDMDocument:
    return CDMDocument(
        id=uuid4(),
        organization_id=org_id,
        original_filename=filename,
        mime_type="text/plain",
        sha256=sha256,
        size_bytes=42,
        ingest_status=ingest_status,
        created_at=datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc),
    )


class _Result:
    def __init__(self, value: Any):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalar(self):
        return self._value

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._value or []))

    def all(self):
        return list(self._value or [])


class _FakeAsyncSession:
    """Scripted session tracking adds, commits, and delete statements."""

    def __init__(self, scripted_results: Optional[List[Any]] = None):
        self._scripted = list(scripted_results or [])
        self.added: List[Any] = []
        self.commits = 0
        self.select_stmts: List[Any] = []
        self.delete_stmts: List[Any] = []
        self.event_log: List[str] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, obj: Any) -> None:
        return None

    async def execute(self, stmt):
        if isinstance(stmt, Delete):
            self.delete_stmts.append(stmt)
            self.event_log.append("delete")
            return _Result(None)
        self.select_stmts.append(stmt)
        if not self._scripted:
            raise AssertionError("FakeAsyncSession: ran out of scripted results")
        return _Result(self._scripted.pop(0))


@pytest.fixture
def storage_stub(monkeypatch):
    store: Dict[str, bytes] = {}

    def _write(key: str, body: bytes, org_id: str) -> None:
        store[key] = body

    monkeypatch.setattr(cdm_storage, "write_cdm_payload", _write)
    monkeypatch.setattr(cdm_router.cdm_storage, "write_cdm_payload", _write)
    yield store


@pytest.fixture
def celery_stub(monkeypatch):
    calls: List[str] = []

    def _delay(document_id: str):
        calls.append(document_id)
        return SimpleNamespace(id=str(uuid4()))

    monkeypatch.setattr(cdm_router.ingest_cdm_document, "delay", _delay)
    yield calls


@pytest.fixture
def caps_noop(monkeypatch):
    """Silence the slice-7 caps so scripted sessions stay deterministic."""

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(cdm_router, "assert_cdm_document_count_cap", _noop)
    monkeypatch.setattr(cdm_router, "assert_cdm_token_count_cap", _noop)
    yield


@pytest.fixture
def client_factory(storage_stub, celery_stub, monkeypatch):
    monkeypatch.setenv("ENABLE_CDM", "true")
    app = main.app

    def _build(
        session: _FakeAsyncSession,
        *,
        org: UUID = ORG_ID,
    ) -> TestClient:
        async def _override_db():
            yield session

        async def _override_auth():
            user = MagicMock()
            user.db_id = str(uuid4())
            user.email = "test@example.com"
            return OrgMembership(
                user=user, organization_id=org, role="editor", is_consultant=False
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


def _upload(client, filename: str, payload: bytes):
    return client.post(
        f"/api/organizations/{ORG_ID}/cdm/upload",
        files={"file": (filename, payload, "text/plain")},
    )


# -------------------------------------------------------------------------
# Exact duplicate → 409
# -------------------------------------------------------------------------


def test_duplicate_upload_rejected_409(client_factory, celery_stub, caps_noop):
    existing = _make_doc()
    session = _FakeAsyncSession([[existing]])
    client = client_factory(session)

    resp = _upload(client, "policy.txt", PAYLOAD_V1)

    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert isinstance(detail, str)
    assert existing.original_filename in detail
    assert str(existing.id) in detail
    assert session.added == []
    assert session.delete_stmts == []
    assert celery_stub == []


def test_duplicate_under_different_filename_rejected_409(
    client_factory, celery_stub, caps_noop
):
    """Identical bytes uploaded under a new name still count as a duplicate."""
    existing = _make_doc(filename="original-name.txt")
    session = _FakeAsyncSession([[existing]])
    client = client_factory(session)

    resp = _upload(client, "renamed-copy.txt", PAYLOAD_V1)

    assert resp.status_code == 409
    assert "original-name.txt" in resp.json()["detail"]
    assert session.added == []
    assert celery_stub == []


# -------------------------------------------------------------------------
# Updated version → supersede
# -------------------------------------------------------------------------


def test_new_version_supersedes_predecessor(client_factory, celery_stub, caps_noop):
    old = _make_doc(sha256=SHA_V1)
    mapping_rows = [
        (uuid4(), "proposed", uuid4(), old.id),
        (uuid4(), "accepted", uuid4(), old.id),
    ]
    session = _FakeAsyncSession([[old], mapping_rows, []])
    client = client_factory(session)

    resp = _upload(client, "policy.txt", PAYLOAD_V2)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["superseded_document_ids"] == [str(old.id)]
    assert body["superseded_mappings_removed"] == 2
    assert len(session.delete_stmts) == 1

    audit_rows = [row for row in session.added if isinstance(row, AuditLog)]
    mapping_audits = [r for r in audit_rows if r.entity_type == "cdm_mapping"]
    doc_audits = [r for r in audit_rows if r.entity_type == "cdm_document"]
    assert len(mapping_audits) == 2
    assert {r.action for r in mapping_audits} == {"removed_with_document"}
    assert len(doc_audits) == 1
    assert doc_audits[0].action == "superseded"

    doc_audit_payload = json.loads(doc_audits[0].new_value)
    assert doc_audit_payload["superseded_by_document_id"] == body["document_id"]
    assert doc_audit_payload["mappings_removed"] == 2

    new_docs = [row for row in session.added if isinstance(row, CDMDocument)]
    assert len(new_docs) == 1
    assert new_docs[0].sha256 == SHA_V2
    assert celery_stub == [str(new_docs[0].id)]


def test_failed_predecessor_same_sha_superseded(client_factory, celery_stub, caps_noop):
    """Re-uploading identical content after a failed ingest is a retry."""
    failed = _make_doc(sha256=SHA_V1, ingest_status="failed")
    session = _FakeAsyncSession([[failed], [], []])
    client = client_factory(session)

    resp = _upload(client, "policy.txt", PAYLOAD_V1)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["superseded_document_ids"] == [str(failed.id)]
    assert body["superseded_mappings_removed"] == 0
    assert len(session.delete_stmts) == 1
    assert len(celery_stub) == 1


def test_case_insensitive_filename_supersedes(client_factory, celery_stub, caps_noop):
    """POLICY.TXT replaces policy.txt — filename identity ignores case."""
    old = _make_doc(filename="policy.txt", sha256=SHA_V1)
    session = _FakeAsyncSession([[old], [], []])
    client = client_factory(session)

    resp = _upload(client, "POLICY.TXT", PAYLOAD_V2)

    assert resp.status_code == 200, resp.text
    assert resp.json()["superseded_document_ids"] == [str(old.id)]
    assert len(session.delete_stmts) == 1


def test_multiple_predecessors_all_superseded(client_factory, celery_stub, caps_noop):
    """Two prior versions under the same filename both get superseded."""
    old_a = _make_doc(sha256="a" * 64)
    old_b = _make_doc(sha256="b" * 64)
    session = _FakeAsyncSession([[old_a, old_b], [], []])
    client = client_factory(session)

    resp = _upload(client, "policy.txt", PAYLOAD_V2)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body["superseded_document_ids"]) == {str(old_a.id), str(old_b.id)}
    doc_audits = [
        row
        for row in session.added
        if isinstance(row, AuditLog) and row.entity_type == "cdm_document"
    ]
    assert len(doc_audits) == 2


# -------------------------------------------------------------------------
# Ordering, scoping, and contract
# -------------------------------------------------------------------------


def test_supersede_runs_before_document_count_cap(client_factory, monkeypatch):
    """Replacing a document at the cap must free the slot first."""
    old = _make_doc(sha256=SHA_V1)
    session = _FakeAsyncSession([[old], [], []])

    async def _recording_doc_cap(db, org_id):
        session.event_log.append("doc_cap")

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(
        cdm_router, "assert_cdm_document_count_cap", _recording_doc_cap
    )
    monkeypatch.setattr(cdm_router, "assert_cdm_token_count_cap", _noop)

    client = client_factory(session)
    resp = _upload(client, "policy.txt", PAYLOAD_V2)

    assert resp.status_code == 200, resp.text
    assert session.event_log.index("delete") < session.event_log.index("doc_cap")


def test_dedup_lookup_is_org_scoped(client_factory, celery_stub, caps_noop):
    """The classification query filters on organization_id."""
    session = _FakeAsyncSession([[]])
    client = client_factory(session)

    resp = _upload(client, "policy.txt", PAYLOAD_V1)

    assert resp.status_code == 200, resp.text
    dedup_stmt = str(session.select_stmts[0])
    assert "cdm_documents.organization_id" in dedup_stmt
    assert "cdm_documents.sha256" in dedup_stmt
    assert "cdm_documents.original_filename" in dedup_stmt


def test_upload_response_defaults_when_no_predecessors(
    client_factory, celery_stub, caps_noop
):
    """Fresh uploads keep the backward-compatible response shape."""
    session = _FakeAsyncSession([[]])
    client = client_factory(session)

    resp = _upload(client, "brand-new.txt", PAYLOAD_V1)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["superseded_document_ids"] == []
    assert body["superseded_mappings_removed"] == 0
    assert session.delete_stmts == []
    assert len(celery_stub) == 1


# -------------------------------------------------------------------------
# Proposal audit parity on supersede (issue 722)
# -------------------------------------------------------------------------


def test_supersede_writes_audit_row_per_affected_proposal(
    client_factory, celery_stub, caps_noop
):
    """Consolidated proposals removed by the supersede cascade get the same
    removed_with_document audit convention as their citation rows."""
    old = _make_doc(sha256=SHA_V1)
    proposal_id = uuid4()
    control_id = uuid4()
    proposal_rows = [(proposal_id, "accepted", control_id, old.id)]
    session = _FakeAsyncSession([[old], [], proposal_rows])
    client = client_factory(session)

    resp = _upload(client, "policy.txt", PAYLOAD_V2)

    assert resp.status_code == 200, resp.text
    proposal_audits = [
        row
        for row in session.added
        if isinstance(row, AuditLog) and row.entity_type == "cdm_control_proposal"
    ]
    assert len(proposal_audits) == 1
    row = proposal_audits[0]
    assert row.entity_id == proposal_id
    assert row.action == "removed_with_document"
    assert row.old_value == "accepted"
    payload = json.loads(row.new_value)
    assert payload["cdm_document_id"] == str(old.id)
    assert payload["scoped_control_id"] == str(control_id)
    assert payload["superseded_by_document_id"] == resp.json()["document_id"]
