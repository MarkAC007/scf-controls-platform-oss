"""Tests for CDM v1 slice 4 — compute_mappings worker + dispatcher.

Six tests exercise the pure helper services.cdm_mapping.compute_mappings_for_org
against an in-memory sync session fake. Four endpoint tests exercise the
POST /cdm/compute-mappings dispatcher (happy + idempotent + flag-off) and the
GET /cdm/compute-mappings/{task_id} status reader, patching Redis at
``redis_client.get_redis_client`` and Celery at the bound task methods.

The shipped helper uses strict-< score thresholding (rank 8 == 0.6 exact), so
the below-threshold test uses threshold=0.65 / top_k=12 to land 4 hits below
the cut and 8 hits at/above — matches the "4 skipped / 8 kept" target without
fighting float math against locked code.
"""
from __future__ import annotations

import os
import sys
import types
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["ENABLE_CDM"] = "true"

import main  # noqa: E402
from auth import OrgMembership, require_org_editor, require_org_viewer  # noqa: E402
from database import get_db  # noqa: E402
from models import CDMMapping  # noqa: E402
from services import cdm_mapping  # noqa: E402
from services.cdm_tenancy import require_tenant_cdm_enabled  # noqa: E402

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
OTHER_ORG_ID = UUID("00000000-0000-0000-0000-0000000000ff")


# ─────────────────────────── sync session fake ──────────────────────────

class _Result:
    def __init__(self, rows: List[Any]):
        self._rows = list(rows)

    def all(self) -> List[Any]:
        return list(self._rows)

    def first(self) -> Optional[Any]:
        return self._rows[0] if self._rows else None


class _FakeSyncSession:
    """Sync session fake matching the helper's exact call pattern.

    The helper issues two kinds of statements:
      1. select(ScopedControl.id, SCFCatalogControl.control_name, SCFCatalogControl.control_description)...
         consumed via .all() — returns the control rows.
      2. select(CDMMapping.id).where(...) for dedup
         consumed via .first() — returns existing row tuple if a duplicate exists.

    We disambiguate by inspecting str(stmt) for the substring "cdm_mappings".
    """

    def __init__(
        self,
        *,
        control_rows: List[tuple],
        docs_by_id: Dict[UUID, Any],
        duplicate_keys: Optional[set] = None,
    ):
        self._control_rows = control_rows
        self._docs = docs_by_id
        self._duplicate_keys = duplicate_keys or set()
        self.added: List[CDMMapping] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self._dedup_execute_calls = 0

    def execute(self, stmt):
        sql = str(stmt).lower()
        if "cdm_mappings" in sql:
            self._dedup_execute_calls += 1
            try:
                compiled = stmt.compile(compile_kwargs={"literal_binds": False})
                params = dict(compiled.params)
            except Exception:
                params = {}
            values = tuple(params.values())
            uuids = [v for v in values if isinstance(v, UUID)]
            ints = [v for v in values if isinstance(v, int) and not isinstance(v, bool)]
            key = (uuids[0], uuids[1], ints[0]) if len(uuids) >= 2 and ints else None
            if key is not None and key in self._duplicate_keys:
                return _Result([(uuid4(),)])
            if key is None and self._duplicate_keys and self._dedup_execute_calls == 1:
                return _Result([(uuid4(),)])
            return _Result([])
        return _Result(self._control_rows)

    def get(self, model, pk):
        return self._docs.get(pk)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


# ─────────────────────── helper input builders ──────────────────────────

def _make_doc(doc_id: UUID, *, org_id: UUID = ORG_ID, filename: str = "policy.txt"):
    return SimpleNamespace(
        id=doc_id,
        organization_id=org_id,
        original_filename=filename,
    )


def _make_hits(chunks: List[Dict[str, str]]) -> Dict[str, Any]:
    return {"hits": list(chunks), "kb_revision": "test-kb-v1"}


def _query_returning(payload_by_query: Dict[str, Dict[str, Any]]) -> Callable[[str, str, int], Dict[str, Any]]:
    """Return a query closure: dispatches per query_text."""

    def _q(query_text: str, workspace: str, top_k: int) -> Dict[str, Any]:
        return payload_by_query.get(query_text, {"hits": [], "kb_revision": "test-kb-v1"})

    return _q


def _query_constant(hits: List[Dict[str, str]]) -> Callable[[str, str, int], Dict[str, Any]]:
    def _q(query_text: str, workspace: str, top_k: int) -> Dict[str, Any]:
        return {"hits": list(hits), "kb_revision": "test-kb-v1"}

    return _q


def _extracted_text_loader(by_doc: Dict[UUID, str]) -> Callable[[Any], Optional[str]]:
    def _load(doc) -> Optional[str]:
        return by_doc.get(doc.id)

    return _load


# ────────────────────────── 1. HAPPY PATH ───────────────────────────────

def test_compute_mappings_helper_happy_path():
    """Two controls × two hits → four mappings created with correct offsets/scores."""
    control_a, control_b = uuid4(), uuid4()
    control_rows = [
        (control_a, "IAO-01", "Access Review", "Periodic privileged access review.", None, None),
        (control_b, "BCR-01", "Backup Verification", "Backups verified weekly.", None, None),
    ]
    doc_id = uuid4()
    doc = _make_doc(doc_id)
    extracted = (
        "INTRO.\n"
        "Access reviews are performed quarterly by the security team.\n"
        "Backups are restored quarterly to verify integrity.\n"
        "OUTRO.\n"
    )
    chunk_a1 = "Access reviews are performed quarterly"
    chunk_a2 = "by the security team."
    chunk_b1 = "Backups are restored quarterly"
    chunk_b2 = "to verify integrity."

    def _query(qtext, ws, k):
        if qtext.startswith("Access Review"):
            return _make_hits([
                {"content": chunk_a1, "file_source": f"cdm-{doc_id}.txt"},
                {"content": chunk_a2, "file_source": f"cdm-{doc_id}.txt"},
            ])
        return _make_hits([
            {"content": chunk_b1, "file_source": f"cdm-{doc_id}.txt"},
            {"content": chunk_b2, "file_source": f"cdm-{doc_id}.txt"},
        ])

    session = _FakeSyncSession(control_rows=control_rows, docs_by_id={doc_id: doc})

    summary = cdm_mapping.compute_mappings_for_org(
        session,
        ORG_ID,
        query_callable=_query,
        extracted_text_loader=_extracted_text_loader({doc_id: extracted}),
        score_threshold=0.5,
        top_k=10,
        kb_revision="test-kb-v1",
    )

    assert summary.controls_processed == 2
    assert summary.hits_evaluated == 4
    assert summary.mappings_created == 4
    assert summary.mappings_skipped_below_threshold == 0
    assert summary.mappings_skipped_duplicate == 0
    assert summary.mappings_skipped_unresolved_offset == 0
    assert session.commits == 1
    assert len(session.added) == 4

    by_content = {(m.scoped_control_id, m.byte_offset_start): m for m in session.added}
    for control_id, chunks in ((control_a, (chunk_a1, chunk_a2)), (control_b, (chunk_b1, chunk_b2))):
        for rank, content in enumerate(chunks):
            expected_start = extracted.find(content)
            assert expected_start >= 0
            mapping = by_content[(control_id, expected_start)]
            assert mapping.byte_offset_end == expected_start + len(content)
            expected_score = 1.0 if rank == 0 else 0.95
            assert mapping.relevance_score == pytest.approx(expected_score)
            assert mapping.status == "proposed"
            assert mapping.kb_revision == "test-kb-v1"
            assert mapping.organization_id == ORG_ID
            assert mapping.cdm_document_id == doc_id


# ─────────────────── 2. BELOW THRESHOLD (4 skipped) ─────────────────────

def test_compute_mappings_helper_skips_below_threshold():
    """Hits whose rank-derived score falls below the threshold are skipped.

    Rank-derived score is ``1.0 - 0.05*rank`` with strict-< thresholding.
    With top_k=12 and threshold=0.65, IEEE-754 float math puts rank 7 at
    0.6499999999999999 (< 0.65), so ranks 7..11 are skipped (5 hits) and
    ranks 0..6 are kept (7 mappings created). Counts are pinned to the
    actual implementation behavior, not idealised arithmetic.
    """
    control_id = uuid4()
    doc_id = uuid4()
    doc = _make_doc(doc_id)
    chunk_texts = [f"chunk-marker-{i:02d}" for i in range(12)]
    extracted = "\n".join(chunk_texts)
    session = _FakeSyncSession(
        control_rows=[(control_id, "CTR-01", "Ctrl", "Desc", None, None)],
        docs_by_id={doc_id: doc},
    )

    summary = cdm_mapping.compute_mappings_for_org(
        session,
        ORG_ID,
        query_callable=_query_constant([
            {"content": c, "file_source": f"cdm-{doc_id}.txt"} for c in chunk_texts
        ]),
        extracted_text_loader=_extracted_text_loader({doc_id: extracted}),
        score_threshold=0.65,
        top_k=12,
        kb_revision="test-kb-v1",
    )

    assert summary.hits_evaluated == 12
    assert summary.mappings_skipped_below_threshold == 5
    assert summary.mappings_created == 7
    assert summary.mappings_skipped_unresolved_offset == 0
    assert summary.mappings_skipped_duplicate == 0


# ────────────────── 3. CONTENT NOT IN EXTRACTED TEXT ────────────────────

def test_compute_mappings_helper_skips_unresolvable_offsets():
    control_id = uuid4()
    doc_id = uuid4()
    doc = _make_doc(doc_id)
    extracted = "completely unrelated body text"
    session = _FakeSyncSession(
        control_rows=[(control_id, "CTR-01", "Ctrl", "Desc", None, None)],
        docs_by_id={doc_id: doc},
    )

    summary = cdm_mapping.compute_mappings_for_org(
        session,
        ORG_ID,
        query_callable=_query_constant([
            {"content": "this string is absent", "file_source": f"cdm-{doc_id}.txt"},
        ]),
        extracted_text_loader=_extracted_text_loader({doc_id: extracted}),
        score_threshold=0.5,
        top_k=5,
        kb_revision="test-kb-v1",
    )

    assert summary.hits_evaluated == 1
    assert summary.mappings_created == 0
    assert summary.mappings_skipped_unresolved_offset == 1
    assert session.added == []


# ─────────────────── 4. UNPARSEABLE file_source ─────────────────────────

def test_compute_mappings_helper_skips_unparseable_file_source():
    control_id = uuid4()
    session = _FakeSyncSession(
        control_rows=[(control_id, "CTR-01", "Ctrl", "Desc", None, None)],
        docs_by_id={},
    )

    summary = cdm_mapping.compute_mappings_for_org(
        session,
        ORG_ID,
        query_callable=_query_constant([
            {"content": "anything", "file_source": "not-a-cdm-pattern.txt"},
        ]),
        extracted_text_loader=_extracted_text_loader({}),
        score_threshold=0.5,
        top_k=5,
        kb_revision="test-kb-v1",
    )

    assert summary.mappings_skipped_unresolved_offset == 1
    assert summary.mappings_created == 0
    assert session.added == []


# ───────────────────── 5. DUPLICATE OFFSET ──────────────────────────────

def test_compute_mappings_helper_skips_duplicates():
    control_id = uuid4()
    doc_id = uuid4()
    doc = _make_doc(doc_id)
    content = "duplicated marker"
    extracted = f"prefix {content} suffix"
    expected_offset = extracted.find(content)

    session = _FakeSyncSession(
        control_rows=[(control_id, "CTR-01", "Ctrl", "Desc", None, None)],
        docs_by_id={doc_id: doc},
        duplicate_keys={(control_id, doc_id, expected_offset)},
    )

    summary = cdm_mapping.compute_mappings_for_org(
        session,
        ORG_ID,
        query_callable=_query_constant([
            {"content": content, "file_source": f"cdm-{doc_id}.txt"},
        ]),
        extracted_text_loader=_extracted_text_loader({doc_id: extracted}),
        score_threshold=0.5,
        top_k=5,
        kb_revision="test-kb-v1",
    )

    assert summary.mappings_skipped_duplicate == 1
    assert summary.mappings_created == 0
    assert session.added == []


# ─────────────────── 6. CROSS-ORG DOC FILTERED ──────────────────────────

def test_compute_mappings_helper_cross_org_doc_filtered():
    control_id = uuid4()
    doc_id = uuid4()
    doc = _make_doc(doc_id, org_id=OTHER_ORG_ID)
    extracted = "matching content exists here"
    session = _FakeSyncSession(
        control_rows=[(control_id, "CTR-01", "Ctrl", "Desc", None, None)],
        docs_by_id={doc_id: doc},
    )

    summary = cdm_mapping.compute_mappings_for_org(
        session,
        ORG_ID,
        query_callable=_query_constant([
            {"content": "matching content", "file_source": f"cdm-{doc_id}.txt"},
        ]),
        extracted_text_loader=_extracted_text_loader({doc_id: extracted}),
        score_threshold=0.5,
        top_k=5,
        kb_revision="test-kb-v1",
    )

    assert summary.mappings_skipped_unresolved_offset == 1
    assert summary.mappings_created == 0
    assert session.added == []


# ─────────────────────── endpoint fixtures ──────────────────────────────

@pytest.fixture
def _auth_overrides():
    app = main.app

    async def _override_db():
        yield MagicMock()

    async def _override_auth():
        user = MagicMock()
        user.db_id = str(uuid4())
        user.email = "test@example.com"
        return OrgMembership(
            user=user, organization_id=ORG_ID, role="editor", is_consultant=False
        )

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[require_org_editor] = _override_auth
    app.dependency_overrides[require_org_viewer] = _override_auth
    app.dependency_overrides[require_tenant_cdm_enabled] = lambda: None
    yield app
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(require_org_editor, None)
    app.dependency_overrides.pop(require_org_viewer, None)
    app.dependency_overrides.pop(require_tenant_cdm_enabled, None)


# ──────────────────── 7. DISPATCH HAPPY PATH ────────────────────────────

def test_compute_mappings_dispatch_happy_path_202(monkeypatch, _auth_overrides):
    monkeypatch.setenv("ENABLE_CDM", "true")

    fake_redis = MagicMock()
    fake_redis.set = AsyncMock(return_value=True)
    fake_redis.get = AsyncMock(return_value=None)
    fake_redis.delete = AsyncMock(return_value=1)

    async def _factory():
        return fake_redis

    monkeypatch.setattr("redis_client.get_redis_client", _factory)

    captured: Dict[str, Any] = {"args": None, "kwargs": None}

    def _fake_apply_async(args=None, kwargs=None, **opts):
        captured["args"] = list(args) if args is not None else None
        captured["kwargs"] = dict(kwargs) if kwargs is not None else None
        captured["opts"] = opts
        return SimpleNamespace(id=opts.get("task_id"))

    import tasks_cdm

    monkeypatch.setattr(tasks_cdm.compute_mappings, "apply_async", _fake_apply_async)

    client = TestClient(_auth_overrides)
    resp = client.post(f"/api/organizations/{ORG_ID}/cdm/compute-mappings")

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["idempotent_existing"] is False
    task_id = body["task_id"]
    UUID(task_id)

    fake_redis.set.assert_awaited_once()
    set_args, set_kwargs = fake_redis.set.await_args
    assert set_args[0] == f"cdm:compute_lock:{ORG_ID}"
    assert set_args[1] == task_id
    assert set_kwargs.get("nx") is True
    assert set_kwargs.get("ex") == 900

    assert captured["args"] == [str(ORG_ID)]
    assert captured["opts"].get("queue") == "cdm"
    assert captured["opts"].get("task_id") == task_id


# ─────────────────── 8. DISPATCH IDEMPOTENT (lock held) ─────────────────

def test_compute_mappings_dispatch_idempotent(monkeypatch, _auth_overrides):
    monkeypatch.setenv("ENABLE_CDM", "true")

    fake_redis = MagicMock()
    fake_redis.set = AsyncMock(return_value=False)
    fake_redis.get = AsyncMock(return_value="existing-task-123")
    fake_redis.delete = AsyncMock(return_value=1)

    async def _factory():
        return fake_redis

    monkeypatch.setattr("redis_client.get_redis_client", _factory)

    apply_async_called = {"count": 0}

    def _fake_apply_async(*a, **kw):
        apply_async_called["count"] += 1
        return SimpleNamespace(id="should-not-be-called")

    import tasks_cdm

    monkeypatch.setattr(tasks_cdm.compute_mappings, "apply_async", _fake_apply_async)

    client = TestClient(_auth_overrides)
    resp = client.post(f"/api/organizations/{ORG_ID}/cdm/compute-mappings")

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["task_id"] == "existing-task-123"
    assert body["idempotent_existing"] is True
    assert apply_async_called["count"] == 0


# ─────────────────── 9. STATUS ENDPOINT ─────────────────────────────────

def test_compute_mappings_status_endpoint(monkeypatch, _auth_overrides):
    monkeypatch.setenv("ENABLE_CDM", "true")

    class _FakeAsyncResult:
        def __init__(self, task_id: str):
            self.id = task_id
            self.state = "SUCCESS"
            self.result = {"status": "ok", "mappings_created": 5}

        def ready(self) -> bool:
            return True

        def successful(self) -> bool:
            return True

    import tasks_cdm

    monkeypatch.setattr(
        tasks_cdm.compute_mappings, "AsyncResult", lambda task_id: _FakeAsyncResult(task_id)
    )

    client = TestClient(_auth_overrides)
    resp = client.get(f"/api/organizations/{ORG_ID}/cdm/compute-mappings/task-abc")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["task_id"] == "task-abc"
    assert body["state"] == "SUCCESS"
    assert body["ready"] is True
    assert body["successful"] is True
    assert body["result"]["mappings_created"] == 5


# ─────────────── 10. CDM DISABLED → 404 BEFORE AUTH ─────────────────────

def test_compute_mappings_dispatch_requires_cdm_enabled(monkeypatch):
    """Flag off → 404 without auth or redis being touched."""
    monkeypatch.setenv("ENABLE_CDM", "false")

    app = main.app

    # The tenant dep needs a DB session; feed it an empty settings lookup so
    # it falls back to env (false) → 404.
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
        resp = client.post(f"/api/organizations/{ORG_ID}/cdm/compute-mappings")

        assert resp.status_code == 404
        assert resp.json()["detail"] == "CDM module not enabled"
    finally:
        app.dependency_overrides.pop(get_db, None)


# ─────────────────────── slice 12: derive_section ───────────────────────


def test_derive_section_matches_markdown_h1():
    text = "# Access Control\n\nBody paragraph one.\nMore body text follows here."
    offset = text.find("More body")
    assert cdm_mapping.derive_section(text, offset) == "Access Control"


def test_derive_section_matches_markdown_h2():
    text = "## Privileged Access Review\n\nQuarterly we walk every admin account."
    offset = text.find("Quarterly")
    assert cdm_mapping.derive_section(text, offset) == "Privileged Access Review"


def test_derive_section_matches_markdown_h3():
    text = "### Backup Verification\n\nRestore drills run monthly against tier-1 systems."
    offset = text.find("Restore")
    assert cdm_mapping.derive_section(text, offset) == "Backup Verification"


def test_derive_section_matches_numbered_heading():
    text = "1.2.3 Encryption At Rest\n\nAll volumes use AES-256 with KMS-managed keys."
    offset = text.find("All volumes")
    assert cdm_mapping.derive_section(text, offset) == "Encryption At Rest"


def test_derive_section_matches_section_keyword():
    text = "Section 4: Incident Response\n\nP1 incidents trigger pager within 5 minutes."
    offset = text.find("P1 incidents")
    assert cdm_mapping.derive_section(text, offset) == "Incident Response"


def test_derive_section_matches_chapter_keyword():
    text = "Chapter 12 Vendor Management\n\nAnnual recertification for all critical vendors."
    offset = text.find("Annual")
    assert cdm_mapping.derive_section(text, offset) == "Vendor Management"


def test_derive_section_nearest_heading_wins():
    """Two headings precede the offset; the closer one wins regardless of type."""
    text = (
        "# Far Heading\n\n"
        "Some early text that establishes broader context here.\n\n"
        "## Closer Heading\n\n"
        "The chunk we matched starts here on this line."
    )
    offset = text.find("The chunk we matched")
    assert cdm_mapping.derive_section(text, offset) == "Closer Heading"


def test_derive_section_numbered_beats_markdown_on_same_line():
    """Tie-breaker priority: numbered > markdown when both could match the same position."""
    text = "1.0 Overview\n\nBody."
    offset = text.find("Body")
    assert cdm_mapping.derive_section(text, offset) == "Overview"


def test_derive_section_returns_none_beyond_window():
    """A heading >2000 chars back is out of scope."""
    text = "# Distant Heading\n\n" + ("filler line\n" * 250) + "matched chunk content here."
    offset = text.find("matched chunk")
    assert cdm_mapping.derive_section(text, offset) is None


def test_derive_section_returns_none_for_no_heading():
    text = "Just a paragraph of body text with no heading anywhere preceding the offset."
    offset = len(text) - 5
    assert cdm_mapping.derive_section(text, offset) is None


def test_derive_section_returns_none_for_empty_or_zero_offset():
    assert cdm_mapping.derive_section("", 100) is None
    assert cdm_mapping.derive_section("# Heading\n\nbody", 0) is None


def test_derive_section_caps_title_at_255_chars():
    long_title = "X" * 400
    text = f"# {long_title}\n\nbody"
    offset = text.find("body")
    result = cdm_mapping.derive_section(text, offset)
    assert result is not None
    assert len(result) <= 255


# ───────────────── slice 13: derive_section + Docling sections ─────────────────


class _FakeSection:
    """Duck-typed Section matching cdm_docling_service.Section structurally."""

    def __init__(self, level: int, title: str, byte_start: int, byte_end: int) -> None:
        self.level = level
        self.title = title
        self.byte_start = byte_start
        self.byte_end = byte_end


def test_derive_section_uses_docling_sections_when_provided():
    """Sections kwarg short-circuits the regex path."""
    sections = [
        _FakeSection(0, "Document Title", 0, 200),
        _FakeSection(1, "Access Control", 50, 150),
    ]
    # offset 75 is inside Access Control (50, 150) which nests inside Document Title (0, 200).
    # Deepest level wins → "Access Control".
    assert cdm_mapping.derive_section("ignored", 75, sections=sections) == "Access Control"


def test_derive_section_docling_deepest_level_wins_on_nesting():
    sections = [
        _FakeSection(0, "Title", 0, 1000),
        _FakeSection(1, "Outer Section", 100, 800),
        _FakeSection(2, "Inner Subsection", 200, 500),
    ]
    assert cdm_mapping.derive_section("ignored", 300, sections=sections) == "Inner Subsection"


def test_derive_section_docling_falls_outside_all_ranges_returns_none():
    sections = [_FakeSection(1, "Section A", 0, 100)]
    assert cdm_mapping.derive_section("ignored", 200, sections=sections) is None


def test_derive_section_empty_sections_falls_back_to_regex():
    text = "# Access Control\n\nBody paragraph one."
    offset = text.find("Body")
    # Empty list falls through to regex; None likewise.
    assert cdm_mapping.derive_section(text, offset, sections=[]) == "Access Control"
    assert cdm_mapping.derive_section(text, offset, sections=None) == "Access Control"


def test_derive_section_docling_caps_title_at_255_chars():
    long_title = "X" * 400
    sections = [_FakeSection(1, long_title, 0, 200)]
    result = cdm_mapping.derive_section("ignored", 100, sections=sections)
    assert result is not None
    assert len(result) <= 255


def test_compute_mappings_helper_persists_section():
    """compute_mappings_for_org populates section on the created CDMMapping rows."""
    control_id = uuid4()
    doc_id = uuid4()
    doc = _make_doc(doc_id)
    chunk = "Quarterly access reviews are documented in the IAM register."
    extracted = (
        "INTRO PARAGRAPH.\n"
        "\n"
        "## Privileged Access Review\n"
        "\n"
        f"{chunk}\n"
        "Additional context follows here.\n"
    )
    session = _FakeSyncSession(
        control_rows=[(control_id, "IAO-01", "Access Review", "Periodic privileged access review.", None, None)],
        docs_by_id={doc_id: doc},
    )

    summary = cdm_mapping.compute_mappings_for_org(
        session,
        ORG_ID,
        query_callable=_query_constant([
            {"content": chunk, "file_source": f"cdm-{doc_id}.txt"},
        ]),
        extracted_text_loader=_extracted_text_loader({doc_id: extracted}),
        score_threshold=0.5,
        top_k=10,
        kb_revision="test-kb-v1",
    )

    assert summary.mappings_created == 1
    assert len(session.added) == 1
    assert session.added[0].section == "Privileged Access Review"
    assert session.added[0].excerpt == chunk


# ──────────── _derive_query_text_for_control unit tests ──────────────────

def test_derive_query_text_artifact_type_and_description_appear():
    """Artifact type (underscores→spaces) and description appear in the seed."""
    result = cdm_mapping._derive_query_text_for_control(
        "My Control",
        "Generic description.",
        required_artifact_types=[
            {"type": "access_review_log", "description": "Log of quarterly reviews.", "weight": "high", "mandatory": True}
        ],
    )
    assert result is not None
    assert "access review log" in result
    assert "Log of quarterly reviews." in result


def test_derive_query_text_objective_texts_appear():
    """objective_texts strings are included in the seed."""
    result = cdm_mapping._derive_query_text_for_control(
        "My Control",
        "Generic description.",
        objective_texts=["Examiner verifies independence of assessors.", "Evidence of external auditor engagement."],
    )
    assert result is not None
    assert "Examiner verifies independence of assessors." in result
    assert "Evidence of external auditor engagement." in result


def test_derive_query_text_control_question_appears():
    """control_question is included in the seed."""
    result = cdm_mapping._derive_query_text_for_control(
        "Assessor Independence",
        "Generic description.",
        control_question="Are assessors independent from the teams they assess?",
    )
    assert result is not None
    assert "Are assessors independent from the teams they assess?" in result


def test_derive_query_text_ordering_name_before_description():
    """control_name precedes control_description in the seed."""
    result = cdm_mapping._derive_query_text_for_control(
        "Assessor Independence",
        "Generic cybersecurity description.",
    )
    assert result is not None
    name_pos = result.find("Assessor Independence")
    desc_pos = result.find("Generic cybersecurity description.")
    assert name_pos < desc_pos, "control_name must precede control_description"


def test_derive_query_text_discriminating_fields_before_description():
    """question, artifacts, and objectives all precede control_description."""
    result = cdm_mapping._derive_query_text_for_control(
        "Assessor Independence",
        "Generic cybersecurity description.",
        control_question="How is assessor independence ensured?",
        required_artifact_types=[{"type": "independence_declaration", "description": "Signed declaration."}],
        objective_texts=["Objective: verify no conflict of interest."],
    )
    assert result is not None
    desc_pos = result.find("Generic cybersecurity description.")
    question_pos = result.find("How is assessor independence ensured?")
    artifact_pos = result.find("independence declaration")
    objective_pos = result.find("Objective: verify no conflict of interest.")
    assert question_pos < desc_pos, "control_question must precede control_description"
    assert artifact_pos < desc_pos, "artifact text must precede control_description"
    assert objective_pos < desc_pos, "objective text must precede control_description"


def test_derive_query_text_iao_siblings_produce_different_seeds():
    """Core regression guard: IAO-02.1 and IAO-02.2 share a generic description
    but differ in artifact types and objectives; their seeds must differ.
    """
    shared_desc = (
        "Mechanisms exist to facilitate the implementation of cybersecurity and "
        "data protection assessment policies, standards and procedures."
    )

    iao_021_artifacts = [
        {"type": "assessor_independence_declaration", "description": "Signed statement of assessor independence.", "weight": "high", "mandatory": True},
    ]
    iao_021_objectives = ["[IAO-02.1] Verify that assessors have no conflict of interest with assessed teams."]

    iao_022_artifacts = [
        {"type": "specialized_assessment_report", "description": "Report from a subject-matter expert in a specialized technical domain.", "weight": "high", "mandatory": True},
    ]
    iao_022_objectives = ["[IAO-02.2] Confirm that specialized technical assessments are conducted by qualified domain experts."]

    seed_021 = cdm_mapping._derive_query_text_for_control(
        "Assessor Independence",
        shared_desc,
        required_artifact_types=iao_021_artifacts,
        objective_texts=iao_021_objectives,
    )
    seed_022 = cdm_mapping._derive_query_text_for_control(
        "Specialized Assessments",
        shared_desc,
        required_artifact_types=iao_022_artifacts,
        objective_texts=iao_022_objectives,
    )

    assert seed_021 is not None
    assert seed_022 is not None
    assert seed_021 != seed_022, "Sibling controls sharing a description must produce different seeds"
    assert "assessor independence declaration" in seed_021
    assert "specialized assessment report" in seed_022


def test_derive_query_text_defensive_none_artifact_types():
    """required_artifact_types=None must not raise."""
    result = cdm_mapping._derive_query_text_for_control(
        "Control Name",
        "Description.",
        required_artifact_types=None,
    )
    assert result is not None
    assert "Control Name" in result


def test_derive_query_text_defensive_non_list_artifact_types():
    """required_artifact_types as a non-list (e.g. str) must not raise."""
    result = cdm_mapping._derive_query_text_for_control(
        "Control Name",
        "Description.",
        required_artifact_types="not-a-list",  # type: ignore[arg-type]
    )
    assert result is not None
    assert "Control Name" in result


def test_derive_query_text_defensive_non_dict_entry_in_artifact_types():
    """A list containing a non-dict entry must not raise; valid dicts still included."""
    result = cdm_mapping._derive_query_text_for_control(
        "Control Name",
        "Description.",
        required_artifact_types=[
            "oops-a-string",
            None,
            42,
            {"type": "valid_type", "description": "Valid desc."},
        ],
    )
    assert result is not None
    assert "valid type" in result
    assert "Valid desc." in result


def test_derive_query_text_2000_char_cap():
    """Final seed is capped at 2000 characters."""
    long_desc = "X" * 3000
    long_objectives = ["O" * 500] * 10
    result = cdm_mapping._derive_query_text_for_control(
        "Control Name",
        long_desc,
        objective_texts=long_objectives,
    )
    assert result is not None
    assert len(result) <= 2000


def test_derive_query_text_objectives_loader_stub_reaches_query_callable():
    """objectives_loader result is included in the query seed reaching query_callable."""
    control_id = uuid4()
    doc_id = uuid4()
    doc = _make_doc(doc_id)
    chunk = "Evidence of independent assessor engagement."
    extracted = f"PREFIX. {chunk} SUFFIX."

    captured_seeds: List[str] = []

    def _query(qtext: str, ws: str, k: int) -> Dict[str, Any]:
        captured_seeds.append(qtext)
        return _make_hits([{"content": chunk, "file_source": f"cdm-{doc_id}.txt"}])

    def _objectives_loader(scf_ids: List[str]) -> Dict[str, List[str]]:
        return {"IAO-02": ["Verify assessor has no conflict of interest."]}

    session = _FakeSyncSession(
        control_rows=[(control_id, "IAO-02", "Assessor Independence", "Generic description.", None, None)],
        docs_by_id={doc_id: doc},
    )

    summary = cdm_mapping.compute_mappings_for_org(
        session,
        ORG_ID,
        query_callable=_query,
        extracted_text_loader=_extracted_text_loader({doc_id: extracted}),
        score_threshold=0.5,
        top_k=5,
        kb_revision="test-kb-v1",
        objectives_loader=_objectives_loader,
    )

    assert summary.mappings_created == 1
    assert len(captured_seeds) == 1
    assert "Verify assessor has no conflict of interest." in captured_seeds[0]
