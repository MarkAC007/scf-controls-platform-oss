"""Tests for CDM v1 slice 2 — ingest pipeline (upload, job status, Celery task).

Covers slice 2 ISC-10..ISC-27. Uses FastAPI TestClient with `dependency_overrides`
mirroring the pattern in `test_control_composites_api.py`. Storage is mocked via
monkeypatch on `services.cdm_storage`; the Celery delay is mocked so the upload
endpoint returns synchronously without touching Redis.

The task itself is exercised by invoking `tasks_cdm.ingest_cdm_document.run(...)`
directly (bypasses Celery delivery; runs sync in-process), with storage stubbed
to an in-memory dict.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
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
from auth import OrgMembership, require_org_editor, require_org_viewer  # noqa: E402
from database import get_db  # noqa: E402
from services import cdm_storage  # noqa: E402
from services.cdm_tenancy import require_tenant_cdm_enabled  # noqa: E402

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
OTHER_ORG_ID = UUID("00000000-0000-0000-0000-0000000000ff")


class _FakeAsyncSession:
    """Scripted async session — tracks added rows in a dict by id.

    Supports add/commit/refresh/execute(select)/get-style lookups used by the
    CDM router. Each test pre-loads `documents` and supplies scripted execute
    results via `scripted_results`.
    """

    def __init__(self, scripted_results: Optional[List[Any]] = None):
        self.documents: Dict[UUID, Any] = {}
        self._scripted = list(scripted_results or [])
        self.added: List[Any] = []
        self.commits = 0

    def add(self, obj: Any) -> None:
        self.added.append(obj)
        if hasattr(obj, "id"):
            self.documents[obj.id] = obj

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, obj: Any) -> None:
        return None

    async def execute(self, _stmt):
        if not self._scripted:
            raise AssertionError("FakeAsyncSession: ran out of scripted results")
        nxt = self._scripted.pop(0)

        class _Result:
            def __init__(self, value):
                self._value = value

            def scalar_one_or_none(self):
                return self._value

        return _Result(nxt)


@pytest.fixture
def cdm_env(monkeypatch):
    """Force ENABLE_CDM on for the test scope."""
    monkeypatch.setenv("ENABLE_CDM", "true")
    yield


@pytest.fixture
def storage_stub(monkeypatch):
    """In-memory storage that intercepts write/download/build calls."""
    store: Dict[str, bytes] = {}

    def _write(key: str, body: bytes, org_id: str) -> None:
        store[key] = body

    def _download(key: str) -> bytes:
        if key not in store:
            raise FileNotFoundError(f"missing key {key}")
        return store[key]

    monkeypatch.setattr(cdm_storage, "write_cdm_payload", _write)
    monkeypatch.setattr(cdm_storage, "download_cdm_payload", _download)
    # Patch the names as imported by the router and the task module too.
    import api.cdm as cdm_router
    import tasks_cdm

    monkeypatch.setattr(cdm_router.cdm_storage, "write_cdm_payload", _write)
    monkeypatch.setattr(tasks_cdm.cdm_storage, "write_cdm_payload", _write)
    monkeypatch.setattr(tasks_cdm.cdm_storage, "download_cdm_payload", _download)
    yield store


@pytest.fixture
def celery_stub(monkeypatch):
    """Stub the Celery delay call so upload returns without touching Redis."""
    import api.cdm as cdm_router

    calls: List[str] = []

    def _delay(document_id: str):
        calls.append(document_id)
        return SimpleNamespace(id=str(uuid4()))

    monkeypatch.setattr(cdm_router.ingest_cdm_document, "delay", _delay)
    yield calls


@pytest.fixture
def client_factory(cdm_env, storage_stub, celery_stub):
    """Build TestClient with a fake async session + auth override."""
    app = main.app

    def _build(
        session: _FakeAsyncSession,
        *,
        role: Optional[str] = "editor",
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


# -------------------------------------------------------------------------
# ISC-10..ISC-17: Upload endpoint
# -------------------------------------------------------------------------


def test_upload_happy_path_creates_row_and_returns_pending(client_factory, celery_stub):
    """ISC-10/14/16: upload accepts text/plain, creates row, enqueues task."""
    session = _FakeAsyncSession()
    client = client_factory(session)

    resp = client.post(
        f"/api/organizations/{ORG_ID}/cdm/upload",
        files={"file": ("policy.txt", b"hello world", "text/plain")},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "document_id" in body
    assert body["ingest_status"] == "pending"

    # Row was added and persisted.
    assert len(session.added) == 1
    doc = session.added[0]
    assert doc.organization_id == ORG_ID
    assert doc.original_filename == "policy.txt"
    assert doc.mime_type == "text/plain"
    assert doc.size_bytes == len(b"hello world")
    assert len(doc.sha256) == 64

    # Celery task enqueued exactly once with the new document_id.
    assert celery_stub == [str(doc.id)]


def test_upload_wrong_content_type_rejected_415(client_factory):
    """ISC-11: unsupported content type rejected with 415."""
    session = _FakeAsyncSession()
    client = client_factory(session)

    resp = client.post(
        f"/api/organizations/{ORG_ID}/cdm/upload",
        files={"file": ("payload.zip", b"PK\x03\x04binary", "application/zip")},
    )

    assert resp.status_code == 415
    assert "content type" in resp.json()["detail"].lower()
    assert session.added == []


def test_upload_oversize_rejected_413(client_factory, monkeypatch):
    """ISC-12: file larger than CDM_MAX_UPLOAD_BYTES rejected with 413."""
    import api.cdm as cdm_router

    monkeypatch.setattr(cdm_router.cdm_storage, "MAX_UPLOAD_BYTES", 8)

    session = _FakeAsyncSession()
    client = client_factory(session)

    resp = client.post(
        f"/api/organizations/{ORG_ID}/cdm/upload",
        files={"file": ("big.txt", b"way too big for the cap", "text/plain")},
    )

    assert resp.status_code == 413
    assert session.added == []


def test_upload_empty_body_rejected_400(client_factory):
    """Empty payload is rejected before write / enqueue."""
    session = _FakeAsyncSession()
    client = client_factory(session)

    resp = client.post(
        f"/api/organizations/{ORG_ID}/cdm/upload",
        files={"file": ("empty.txt", b"", "text/plain")},
    )

    assert resp.status_code == 400


# -------------------------------------------------------------------------
# ISC-18..ISC-20: Job status endpoint
# -------------------------------------------------------------------------


def test_job_status_after_upload_returns_pending(client_factory):
    """ISC-18: GET /jobs/{document_id} returns the row's ingest_status."""
    doc_id = uuid4()
    fake_doc = SimpleNamespace(
        id=doc_id,
        organization_id=ORG_ID,
        ingest_status="pending",
        ingest_error=None,
        word_count=None,
    )
    session = _FakeAsyncSession(scripted_results=[fake_doc])
    client = client_factory(session)

    resp = client.get(f"/api/organizations/{ORG_ID}/cdm/jobs/{doc_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["document_id"] == str(doc_id)
    assert body["ingest_status"] == "pending"
    assert body["ingest_error"] is None
    assert body["word_count"] is None


def test_job_status_returns_persisted_word_count(client_factory):
    """GET /jobs/{document_id} returns the persisted word_count when present."""
    doc_id = uuid4()
    fake_doc = SimpleNamespace(
        id=doc_id,
        organization_id=ORG_ID,
        ingest_status="parsed",
        ingest_error=None,
        word_count=42,
    )
    session = _FakeAsyncSession(scripted_results=[fake_doc])
    client = client_factory(session)

    resp = client.get(f"/api/organizations/{ORG_ID}/cdm/jobs/{doc_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["document_id"] == str(doc_id)
    assert body["ingest_status"] == "parsed"
    assert body["ingest_error"] is None
    assert body["word_count"] == 42


def test_job_status_404_when_row_in_other_org(client_factory):
    """ISC-19: tenant isolation — query against wrong org returns 404."""
    doc_id = uuid4()
    session = _FakeAsyncSession(scripted_results=[None])
    client = client_factory(session)

    resp = client.get(f"/api/organizations/{ORG_ID}/cdm/jobs/{doc_id}")

    assert resp.status_code == 404


# -------------------------------------------------------------------------
# ISC-21..ISC-25: Celery task transitions
# -------------------------------------------------------------------------


def test_ingest_task_transitions_to_parsed(monkeypatch, storage_stub):
    """ISC-21..25: synchronous task invocation transitions pending → parsing → parsed."""
    import tasks_cdm

    doc_id = uuid4()
    object_key = f"cdm/{ORG_ID}/{doc_id}/policy.txt"
    storage_stub[object_key] = b"The control framework requires periodic review."

    fake_doc = SimpleNamespace(
        id=doc_id,
        organization_id=ORG_ID,
        original_filename="policy.txt",
        mime_type="text/plain",
        ingest_status="pending",
        ingest_error=None,
        word_count=None,
    )

    class _FakeSyncSession:
        def __init__(self):
            self.commits = 0
            self.closed = False

        def get(self, model, pk):
            return fake_doc if pk == doc_id else None

        def commit(self):
            self.commits += 1

        def rollback(self):
            pass

        def close(self):
            self.closed = True

    fake_session = _FakeSyncSession()
    monkeypatch.setattr(tasks_cdm, "_get_sync_session", lambda: fake_session)

    result = tasks_cdm.ingest_cdm_document.run(str(doc_id))

    assert result["status"] == "parsed"
    assert fake_doc.ingest_status == "parsed"
    assert fake_doc.ingest_error is None
    assert result["word_count"] >= 1
    assert fake_doc.word_count >= 1
    # Extracted text was written back to a sibling key.
    assert f"{object_key}.extracted.txt" in storage_stub
    assert fake_session.commits >= 2  # parsing transition + parsed transition
    assert fake_session.closed


def test_ingest_task_transitions_to_failed_when_storage_missing(monkeypatch, storage_stub):
    """ISC-25: any exception sets failed + ingest_error, does not re-raise."""
    import tasks_cdm

    doc_id = uuid4()
    fake_doc = SimpleNamespace(
        id=doc_id,
        organization_id=ORG_ID,
        original_filename="missing.txt",
        mime_type="text/plain",
        ingest_status="pending",
        ingest_error=None,
    )

    class _FakeSyncSession:
        def get(self, model, pk):
            return fake_doc if pk == doc_id else None

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(tasks_cdm, "_get_sync_session", lambda: _FakeSyncSession())

    # storage_stub has no entry for the key — download will FileNotFoundError.
    result = tasks_cdm.ingest_cdm_document.run(str(doc_id))

    assert result["status"] == "failed"
    assert fake_doc.ingest_status == "failed"
    assert fake_doc.ingest_error is not None


def test_feature_flag_off_returns_404_before_auth(monkeypatch):
    """ISC-17/20 + PR #617 invariant: flag-off probes get 404, not 401/403."""
    monkeypatch.setenv("ENABLE_CDM", "false")

    app = main.app

    # The tenant dep needs a DB session to look up Organization.settings.
    # Feed an empty settings dict so it falls back to env (false) → 404.
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
        # Deliberately do NOT override auth — we want to confirm the flag check
        # short-circuits before auth dependencies fire.
        client = TestClient(app)
        resp = client.get(f"/api/organizations/{ORG_ID}/cdm/jobs/{uuid4()}")

        assert resp.status_code == 404
        assert resp.json()["detail"] == "CDM module not enabled"
    finally:
        app.dependency_overrides.pop(get_db, None)


# -------------------------------------------------------------------------
# ISC-24..26: LightRAG indexing path (slice 3.5b)
# -------------------------------------------------------------------------


def test_ingest_indexing_path_happy(monkeypatch, storage_stub):
    """ISC-24: flag on + LightRAG insert succeeds → status=indexed, kb_revision_at_ingest set."""
    import tasks_cdm

    doc_id = uuid4()
    object_key = f"cdm/{ORG_ID}/{doc_id}/policy.txt"
    storage_stub[object_key] = b"The control framework requires periodic review."

    fake_doc = SimpleNamespace(
        id=doc_id,
        organization_id=ORG_ID,
        original_filename="policy.txt",
        mime_type="text/plain",
        ingest_status="pending",
        ingest_error=None,
        word_count=None,
        kb_revision_at_ingest=None,
    )

    class _FakeSyncSession:
        def __init__(self):
            self.commits = 0

        def get(self, model, pk):
            return fake_doc if pk == doc_id else None

        def commit(self):
            self.commits += 1

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(tasks_cdm, "_get_sync_session", lambda: _FakeSyncSession())
    monkeypatch.setattr(tasks_cdm, "is_lightrag_enabled", lambda: True)

    insert_calls = []
    mock_client = MagicMock()

    def _capture_insert(**kwargs):
        insert_calls.append(kwargs)
        return {"status": "success", "message": "ok", "track_id": "tid-1"}

    mock_client.insert.side_effect = _capture_insert
    monkeypatch.setattr(tasks_cdm, "get_lightrag_client", lambda: mock_client)
    monkeypatch.setenv("CDM_KB_REVISION", "lightrag-v1")

    result = tasks_cdm.ingest_cdm_document.run(str(doc_id))

    assert result["status"] == "indexed"
    assert fake_doc.ingest_status == "indexed"
    assert fake_doc.ingest_error is None
    assert fake_doc.kb_revision_at_ingest == "lightrag-v1"
    # Insert was called exactly once with the right workspace + file_source.
    assert len(insert_calls) == 1
    call = insert_calls[0]
    assert call["workspace"] == str(ORG_ID)
    assert call["file_source"] == f"cdm-{doc_id}.txt"
    assert "control framework" in call["text"]


def test_ingest_indexing_failure_records_indexing_failed(monkeypatch, storage_stub):
    """ISC-25: LightRAG insert raises → status=indexing_failed, ingest_error populated, task returns normally."""
    import tasks_cdm

    doc_id = uuid4()
    object_key = f"cdm/{ORG_ID}/{doc_id}/policy.txt"
    storage_stub[object_key] = b"Some policy text body."

    fake_doc = SimpleNamespace(
        id=doc_id,
        organization_id=ORG_ID,
        original_filename="policy.txt",
        mime_type="text/plain",
        ingest_status="pending",
        ingest_error=None,
        word_count=None,
        kb_revision_at_ingest=None,
    )

    class _FakeSyncSession:
        def get(self, model, pk):
            return fake_doc if pk == doc_id else None

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(tasks_cdm, "_get_sync_session", lambda: _FakeSyncSession())
    monkeypatch.setattr(tasks_cdm, "is_lightrag_enabled", lambda: True)

    mock_client = MagicMock()
    mock_client.insert.side_effect = RuntimeError("LightRAG exploded")
    monkeypatch.setattr(tasks_cdm, "get_lightrag_client", lambda: mock_client)

    result = tasks_cdm.ingest_cdm_document.run(str(doc_id))

    assert result["status"] == "indexing_failed"
    assert fake_doc.ingest_status == "indexing_failed"
    assert fake_doc.ingest_error is not None
    assert "LightRAG exploded" in fake_doc.ingest_error
    # KB revision NOT set on failure — only set on successful index.
    assert fake_doc.kb_revision_at_ingest is None


def test_ingest_flag_off_skips_lightrag(monkeypatch, storage_stub):
    """ISC-26: flag off → no client construction, no HTTP, status stays parsed."""
    import tasks_cdm

    doc_id = uuid4()
    object_key = f"cdm/{ORG_ID}/{doc_id}/policy.txt"
    storage_stub[object_key] = b"Plain text payload."

    fake_doc = SimpleNamespace(
        id=doc_id,
        organization_id=ORG_ID,
        original_filename="policy.txt",
        mime_type="text/plain",
        ingest_status="pending",
        ingest_error=None,
        word_count=None,
        kb_revision_at_ingest=None,
    )

    class _FakeSyncSession:
        def get(self, model, pk):
            return fake_doc if pk == doc_id else None

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(tasks_cdm, "_get_sync_session", lambda: _FakeSyncSession())
    monkeypatch.setattr(tasks_cdm, "is_lightrag_enabled", lambda: False)

    sentinel = MagicMock(
        side_effect=AssertionError(
            "get_lightrag_client must not be called when flag off"
        )
    )
    monkeypatch.setattr(tasks_cdm, "get_lightrag_client", sentinel)

    result = tasks_cdm.ingest_cdm_document.run(str(doc_id))

    assert result["status"] == "parsed"
    assert fake_doc.ingest_status == "parsed"
    assert fake_doc.kb_revision_at_ingest is None
    sentinel.assert_not_called()


# ─────────────────────── slice 13: Docling ingest routing ────────────────────


def _docling_result_fixture(markdown: str = "# Doc\n\n## Section A\n\nbody A here.\n"):
    """Build a DoclingResult fixture matching the cdm_docling_service dataclass.

    Imported lazily so the module import doesn't pay the docling cost.
    """
    from services.cdm_docling_service import DoclingResult, Section

    return DoclingResult(
        markdown=markdown,
        sections=[
            Section(level=0, title="Doc", byte_start=0, byte_end=8),
            Section(level=1, title="Section A", byte_start=8, byte_end=len(markdown)),
        ],
        page_count=3,
        ocr_used=False,
        intermediate_json={"name": "fixture", "texts": 2},
    )


def test_ingest_routes_pdf_to_docling(monkeypatch, storage_stub):
    """Slice 13 ISC-18, ISC-21, ISC-22, ISC-23, ISC-24, ISC-25: PDF mime routes
    through Docling; .docling.json + .extracted.md persisted; LightRAG insert
    receives markdown with the .md file_source."""
    import tasks_cdm
    from services import cdm_docling_service

    doc_id = uuid4()
    object_key = f"cdm/{ORG_ID}/{doc_id}/policy.pdf"
    storage_stub[object_key] = b"%PDF-1.4 fake bytes"

    fake_doc = SimpleNamespace(
        id=doc_id,
        organization_id=ORG_ID,
        original_filename="policy.pdf",
        mime_type="application/pdf",
        ingest_status="pending",
        ingest_error=None,
        word_count=None,
        kb_revision_at_ingest=None,
    )

    class _FakeSyncSession:
        def get(self, model, pk):
            return fake_doc if pk == doc_id else None

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    fixture = _docling_result_fixture()
    monkeypatch.setattr(tasks_cdm, "_get_sync_session", lambda: _FakeSyncSession())
    monkeypatch.setattr(cdm_docling_service, "extract", lambda *_a, **_kw: fixture)
    monkeypatch.setattr(tasks_cdm, "is_lightrag_enabled", lambda: True)

    insert_calls = []
    mock_client = MagicMock()
    mock_client.insert.side_effect = lambda **kw: insert_calls.append(kw) or {
        "status": "success",
        "message": "ok",
        "track_id": "tid-doc",
    }
    monkeypatch.setattr(tasks_cdm, "get_lightrag_client", lambda: mock_client)

    result = tasks_cdm.ingest_cdm_document.run(str(doc_id))

    assert result["status"] == "indexed"
    assert result["extraction_method"] == "docling"
    # Markdown was persisted at .extracted.md (not .extracted.txt).
    assert f"{object_key}.extracted.md" in storage_stub
    assert b"# Doc" in storage_stub[f"{object_key}.extracted.md"]
    # Docling intermediate JSON was persisted.
    assert f"{object_key}.docling.json" in storage_stub
    assert b"fixture" in storage_stub[f"{object_key}.docling.json"]
    # LightRAG insert called with markdown text + .md file_source.
    assert len(insert_calls) == 1
    assert insert_calls[0]["file_source"] == f"cdm-{doc_id}.md"
    assert insert_calls[0]["workspace"] == str(ORG_ID)
    assert "# Doc" in insert_calls[0]["text"]
    # Word count came from the Docling result.
    assert fake_doc.word_count == fixture.word_count


def test_ingest_routes_text_keeps_legacy_extractor(monkeypatch, storage_stub):
    """Slice 13 ISC-20: text/plain stays on text_extraction_service with the
    legacy .extracted.txt persistence and the cdm-{id}.txt file_source."""
    import tasks_cdm

    doc_id = uuid4()
    object_key = f"cdm/{ORG_ID}/{doc_id}/notes.txt"
    storage_stub[object_key] = b"Plain text policy content."

    fake_doc = SimpleNamespace(
        id=doc_id,
        organization_id=ORG_ID,
        original_filename="notes.txt",
        mime_type="text/plain",
        ingest_status="pending",
        ingest_error=None,
        word_count=None,
        kb_revision_at_ingest=None,
    )

    class _FakeSyncSession:
        def get(self, model, pk):
            return fake_doc if pk == doc_id else None

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    # Sentinel — Docling must NOT be invoked on the text branch.
    from services import cdm_docling_service

    docling_sentinel = MagicMock(
        side_effect=AssertionError("cdm_docling_service.extract must not run for text/plain")
    )
    monkeypatch.setattr(cdm_docling_service, "extract", docling_sentinel)

    monkeypatch.setattr(tasks_cdm, "_get_sync_session", lambda: _FakeSyncSession())
    monkeypatch.setattr(tasks_cdm, "is_lightrag_enabled", lambda: True)

    insert_calls = []
    mock_client = MagicMock()
    mock_client.insert.side_effect = lambda **kw: insert_calls.append(kw) or {
        "status": "success",
        "message": "ok",
        "track_id": "tid-txt",
    }
    monkeypatch.setattr(tasks_cdm, "get_lightrag_client", lambda: mock_client)

    result = tasks_cdm.ingest_cdm_document.run(str(doc_id))

    assert result["status"] == "indexed"
    assert result["extraction_method"] == "text"
    assert f"{object_key}.extracted.txt" in storage_stub
    assert f"{object_key}.extracted.md" not in storage_stub
    assert f"{object_key}.docling.json" not in storage_stub
    docling_sentinel.assert_not_called()
    assert insert_calls[0]["file_source"] == f"cdm-{doc_id}.txt"


def test_ingest_docling_failure_marks_failed(monkeypatch, storage_stub):
    """Slice 13 ISC-27: DoclingExtractionError raised by the service lands the
    document in ``failed`` status with the error message captured."""
    import tasks_cdm
    from services import cdm_docling_service
    from services.cdm_docling_service import DoclingExtractionError

    doc_id = uuid4()
    object_key = f"cdm/{ORG_ID}/{doc_id}/broken.pdf"
    storage_stub[object_key] = b"%PDF-1.4 broken bytes"

    fake_doc = SimpleNamespace(
        id=doc_id,
        organization_id=ORG_ID,
        original_filename="broken.pdf",
        mime_type="application/pdf",
        ingest_status="pending",
        ingest_error=None,
        word_count=None,
        kb_revision_at_ingest=None,
    )

    class _FakeSyncSession:
        def get(self, model, pk):
            return fake_doc if pk == doc_id else None

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    def _boom(*_a, **_kw):
        raise DoclingExtractionError("simulated Docling crash")

    monkeypatch.setattr(cdm_docling_service, "extract", _boom)
    monkeypatch.setattr(tasks_cdm, "_get_sync_session", lambda: _FakeSyncSession())
    monkeypatch.setattr(tasks_cdm, "is_lightrag_enabled", lambda: True)
    monkeypatch.setattr(
        tasks_cdm,
        "get_lightrag_client",
        lambda: MagicMock(side_effect=AssertionError("LightRAG must not be called on Docling failure")),
    )

    result = tasks_cdm.ingest_cdm_document.run(str(doc_id))

    assert result["status"] == "failed"
    assert "simulated Docling crash" in result["error"]
    assert fake_doc.ingest_status == "failed"
    assert "simulated Docling crash" in (fake_doc.ingest_error or "")
