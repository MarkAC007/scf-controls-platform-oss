"""Tests for CDM reingest and stuck-ingest detection.

The reingest endpoint replaces a 501 stub. Its contract:

- failed / indexing_failed documents are reset to 'pending' and re-dispatched
  against the payload already in storage — same row, no re-upload, so the
  per-checksum supersede invariant cannot be violated
- an in-flight document whose ingest_started_at predates the Celery hard time
  limit is treated as stalled and is retryable too
- healthy or actively in-flight documents are skipped, never errored:
  retry-all must be safe to click
- every reset writes an audit row (action=reingest) carrying the old status
  and previous error

The document list endpoint grows the derived is_stale flag plus word_count;
both are covered here because this is the first suite to exercise
GET /cdm/documents at all.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, List
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["ENABLE_CDM"] = "true"

import main  # noqa: E402
from auth import OrgMembership, require_org_editor, require_org_viewer  # noqa: E402
from database import get_db  # noqa: E402
from models import AuditLog, CDMDocument  # noqa: E402
from services.cdm_tenancy import require_tenant_cdm_enabled  # noqa: E402

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")


def _make_document(
    *,
    status: str = "failed",
    error: str | None = "boom",
    started_at: datetime | None = None,
    word_count: int | None = None,
) -> CDMDocument:
    return CDMDocument(
        id=uuid4(),
        organization_id=ORG_ID,
        original_filename="policy.pdf",
        mime_type="application/pdf",
        sha256="a" * 64,
        size_bytes=1024,
        ingest_status=status,
        ingest_error=error,
        ingest_started_at=started_at,
        word_count=word_count,
        created_at=datetime.now(timezone.utc),
    )


class _ReingestFakeSession:
    """Single SELECT returning the org's (optionally id-filtered) documents."""

    def __init__(self, documents: List[CDMDocument]):
        self._documents = documents
        self.added: list[Any] = []
        self.commits = 0

    async def execute(self, stmt):  # type: ignore[no-untyped-def]
        docs = list(self._documents)

        class _R:
            def scalars(self_inner):
                class _S:
                    def all(self_inner2):
                        return docs

                return _S()

        return _R()

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        return None


class _ListFakeSession:
    """Scripted session for GET /cdm/documents: count, then row select."""

    def __init__(self, documents: List[CDMDocument]):
        self._documents = documents
        self._call_index = 0

    async def execute(self, stmt):  # type: ignore[no-untyped-def]
        self._call_index += 1
        docs = list(self._documents)
        if self._call_index == 1:

            class _Count:
                def scalar(self_inner):
                    return len(docs)

            return _Count()

        class _Rows:
            def scalars(self_inner):
                class _S:
                    def all(self_inner2):
                        return docs

                return _S()

        return _Rows()


@pytest.fixture
def editor_client():
    app = main.app
    actor_db_id = uuid4()

    def _build(session) -> TestClient:  # type: ignore[no-untyped-def]
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
        app.dependency_overrides[require_org_viewer] = _override_auth
        app.dependency_overrides[require_tenant_cdm_enabled] = lambda: None
        return TestClient(app)

    yield _build, actor_db_id

    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(require_org_editor, None)
    app.dependency_overrides.pop(require_org_viewer, None)
    app.dependency_overrides.pop(require_tenant_cdm_enabled, None)


@pytest.fixture
def dispatch_spy(monkeypatch):
    calls: list[str] = []

    class _Task:
        @staticmethod
        def delay(document_id: str) -> None:
            calls.append(document_id)

    import api.cdm as cdm_api

    monkeypatch.setattr(cdm_api, "ingest_cdm_document", _Task)
    return calls


# ─────────────────── Reingest ───────────────────


def test_reingest_failed_document_resets_and_dispatches(editor_client, dispatch_spy):
    build, actor_id = editor_client
    document = _make_document(status="failed", error="parser exploded")
    session = _ReingestFakeSession([document])
    client = build(session)

    resp = client.post(f"/api/organizations/{ORG_ID}/cdm/reingest", json={})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dispatched_document_ids"] == [str(document.id)]
    assert body["skipped_document_ids"] == []

    assert document.ingest_status == "pending"
    assert document.ingest_error is None
    assert document.ingest_started_at is None
    assert dispatch_spy == [str(document.id)]

    audit_rows = [obj for obj in session.added if isinstance(obj, AuditLog)]
    assert len(audit_rows) == 1
    row = audit_rows[0]
    assert row.entity_type == "cdm_document"
    assert row.action == "reingest"
    assert row.old_value == "failed"
    assert row.changed_by_user_id == actor_id
    assert session.commits == 1


def test_reingest_skips_healthy_and_in_flight_documents(editor_client, dispatch_spy):
    build, _ = editor_client
    healthy = _make_document(status="parsed", error=None)
    live = _make_document(
        status="parsing",
        error=None,
        started_at=datetime.now(timezone.utc) - timedelta(seconds=30),
    )
    session = _ReingestFakeSession([healthy, live])
    client = build(session)

    resp = client.post(f"/api/organizations/{ORG_ID}/cdm/reingest", json={})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dispatched_document_ids"] == []
    assert set(body["skipped_document_ids"]) == {str(healthy.id), str(live.id)}
    assert dispatch_spy == []
    assert healthy.ingest_status == "parsed"
    assert live.ingest_status == "parsing"
    assert [obj for obj in session.added if isinstance(obj, AuditLog)] == []


def test_reingest_retries_stale_in_flight_document(editor_client, dispatch_spy):
    build, _ = editor_client
    stalled = _make_document(
        status="parsing",
        error=None,
        started_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    session = _ReingestFakeSession([stalled])
    client = build(session)

    resp = client.post(f"/api/organizations/{ORG_ID}/cdm/reingest", json={})

    assert resp.status_code == 200, resp.text
    assert resp.json()["dispatched_document_ids"] == [str(stalled.id)]
    assert stalled.ingest_status == "pending"
    assert dispatch_spy == [str(stalled.id)]


def test_reingest_indexing_failed_is_retryable(editor_client, dispatch_spy):
    build, _ = editor_client
    document = _make_document(status="indexing_failed", error="lightrag down")
    session = _ReingestFakeSession([document])
    client = build(session)

    resp = client.post(
        f"/api/organizations/{ORG_ID}/cdm/reingest",
        json={"document_ids": [str(document.id)]},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["dispatched_document_ids"] == [str(document.id)]
    assert dispatch_spy == [str(document.id)]


def test_reingest_unknown_document_id_is_404(editor_client, dispatch_spy):
    build, _ = editor_client
    session = _ReingestFakeSession([])
    client = build(session)

    resp = client.post(
        f"/api/organizations/{ORG_ID}/cdm/reingest",
        json={"document_ids": [str(uuid4())]},
    )

    assert resp.status_code == 404, resp.text
    assert dispatch_spy == []


def test_reingest_enqueue_failure_marks_document_failed(editor_client, monkeypatch):
    build, _ = editor_client
    document = _make_document(status="failed", error="boom")
    session = _ReingestFakeSession([document])
    client = build(session)

    class _BrokenTask:
        @staticmethod
        def delay(document_id: str) -> None:
            raise RuntimeError("broker down")

    import api.cdm as cdm_api

    monkeypatch.setattr(cdm_api, "ingest_cdm_document", _BrokenTask)

    resp = client.post(f"/api/organizations/{ORG_ID}/cdm/reingest", json={})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dispatched_document_ids"] == []
    assert body["skipped_document_ids"] == [str(document.id)]
    assert document.ingest_status == "failed"
    assert "Reingest enqueue failed" in (document.ingest_error or "")
    # First commit for the reset, second for the failure rollback-state.
    assert session.commits == 2


# ─────────────────── Document list: is_stale + word_count ───────────────────


def test_list_documents_returns_word_count_and_not_stale(editor_client):
    build, _ = editor_client
    document = _make_document(status="parsed", error=None, word_count=1234)
    session = _ListFakeSession([document])
    client = build(session)

    resp = client.get(f"/api/organizations/{ORG_ID}/cdm/documents")

    assert resp.status_code == 200, resp.text
    row = resp.json()["documents"][0]
    assert row["word_count"] == 1234
    assert row["is_stale"] is False


def test_list_documents_flags_stale_in_flight_row(editor_client):
    build, _ = editor_client
    stalled = _make_document(
        status="indexing",
        error=None,
        started_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    fresh = _make_document(
        status="parsing",
        error=None,
        started_at=datetime.now(timezone.utc) - timedelta(seconds=10),
    )
    session = _ListFakeSession([stalled, fresh])
    client = build(session)

    resp = client.get(f"/api/organizations/{ORG_ID}/cdm/documents")

    assert resp.status_code == 200, resp.text
    by_id = {row["id"]: row for row in resp.json()["documents"]}
    assert by_id[str(stalled.id)]["is_stale"] is True
    assert by_id[str(fresh.id)]["is_stale"] is False
