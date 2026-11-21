# SPDX-License-Identifier: AGPL-3.0-only
"""The viewer's preview of files a browser cannot draw (issue #1309, item 5).

Word, Excel, PowerPoint, ZIP and email are read on the server and returned as
plain data. The endpoint is held to the same rules as a download.
"""
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import evidence_preview_service as preview  # noqa: E402
from services.evidence_readers import EvidenceReadError  # noqa: E402

import evidence_samples as samples  # noqa: E402
from evidence_samples import MARKER, SAMPLES  # noqa: E402

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------

def test_word_preview_is_the_paragraph_text():
    result = preview.build_preview(samples.docx(("Access Control Policy", MARKER)), DOCX, "p.docx")
    assert result["kind"] == "document"
    assert result["paragraphs"] == ["Access Control Policy", MARKER]
    assert result["truncated"] is False
    assert "Table rows are shown as lines of text" in result["note"]


def test_word_preview_shows_table_rows():
    result = preview.build_preview(samples.docx_with_table(), DOCX, "matrix.docx")
    assert f"alice\tPayroll\t{MARKER}" in result["paragraphs"]


def test_word_preview_of_one_very_long_paragraph_shows_its_start(monkeypatch):
    monkeypatch.setattr(preview, "TEXT_MAX_CHARS", 50)
    result = preview.build_preview(samples.docx(("x" * 400, "after")), DOCX, "p.docx")
    assert result["paragraphs"] == ["x" * 50]
    assert result["truncated"] is True
    assert "Showing the start of this document" in result["note"]


def test_word_preview_stops_at_the_character_limit_between_paragraphs(monkeypatch):
    monkeypatch.setattr(preview, "TEXT_MAX_CHARS", 10)
    result = preview.build_preview(samples.docx(("12345", "67890", "x")), DOCX, "p.docx")
    assert result["paragraphs"] == ["12345", "67890"]
    assert result["truncated"] is True


@pytest.mark.parametrize("parser", ["docx.Document"])
def test_preview_refuses_a_document_the_worker_would_still_read(monkeypatch, parser):
    """The viewer is built in the API process: a part this size is not parsed there."""
    import importlib

    from services import evidence_readers as readers

    data = samples.docx_with_document_part(5)
    assert len(data) < 200 * 1024                      # small on disk, 5 MB to parse
    readers.check_office_container(data, "Word document")
    module_name, attribute = parser.rsplit(".", 1)
    called = []
    monkeypatch.setattr(
        importlib.import_module(module_name), attribute,
        lambda *args, **kwargs: called.append(True),
    )
    with pytest.raises(EvidenceReadError, match="too large to preview"):
        preview.build_preview(data, DOCX, "big.docx")
    assert called == []


def test_word_preview_stops_at_the_paragraph_limit(monkeypatch):
    monkeypatch.setattr(preview, "DOC_MAX_PARAGRAPHS", 3)
    result = preview.build_preview(samples.docx([f"p{index}" for index in range(10)]), DOCX, "p.docx")
    assert result["paragraphs"] == ["p0", "p1", "p2"]
    assert result["truncated"] is True
    assert "(3 of 10 paragraphs)" in result["note"]


def test_excel_preview_is_a_table_per_sheet():
    result = preview.build_preview(samples.xlsx(), XLSX, "reviews.xlsx")
    assert result["kind"] == "spreadsheet"
    assert [sheet["name"] for sheet in result["sheets"]] == ["Reviews", "Empty", "Owners"]
    assert result["sheets"][0]["rows"] == [
        ["User", "Reviewed", "Count"], ["alice", MARKER, "3"], ["bob", "", "4.5"],
    ]
    assert result["sheets"][1]["rows"] == []
    assert result["truncated"] is False


def test_excel_preview_limits_rows_columns_and_sheets(monkeypatch):
    monkeypatch.setattr(preview, "SHEET_MAX_ROWS", 5)
    monkeypatch.setattr(preview, "SHEET_MAX_COLUMNS", 3)
    monkeypatch.setattr(preview, "SHEET_MAX_SHEETS", 2)
    wide = [[f"r{row}c{col}" for col in range(6)] for row in range(20)]
    result = preview.build_preview(samples.xlsx({"A": wide, "B": [["x"]], "C": [["y"]]}), XLSX, "big.xlsx")

    assert [sheet["name"] for sheet in result["sheets"]] == ["A", "B"]
    first = result["sheets"][0]
    assert len(first["rows"]) == 5 and all(len(row) == 3 for row in first["rows"])
    assert first["truncated"] is True
    assert result["sheets"][1]["truncated"] is False
    assert result["truncated"] is True


def test_powerpoint_preview_is_the_text_of_each_slide():
    result = preview.build_preview(samples.pptx(), PPTX, "deck.pptx")
    assert result["kind"] == "slides"
    assert result["slides"] == [
        {"number": 1, "paragraphs": ["Security awareness"]},
        {"number": 2, "paragraphs": [MARKER]},
    ]


def test_zip_preview_lists_every_file_and_opens_none():
    result = preview.build_preview(samples.evidence_zip(), "application/zip", "bundle.zip")
    assert result["kind"] == "archive"
    by_name = {member["name"]: member for member in result["members"]}
    assert set(by_name) == {
        "notes/readme.txt", "export.csv", "screens/console.png", "inner.zip",
        "tool.exe", "__MACOSX/._readme.txt",
    }
    assert by_name["notes/readme.txt"]["readable"] is True
    assert by_name["notes/readme.txt"]["label"] == "Plain text"
    assert by_name["inner.zip"]["readable"] is False
    assert "not opened" in by_name["inner.zip"]["note"]
    assert by_name["tool.exe"]["readable"] is False
    assert "6 files in this archive; the AI assessment reads 3 of them" in result["note"]
    # A listing: no file content comes back.
    assert MARKER not in str(result)


def test_email_preview_is_headers_body_and_attachment_names():
    result = preview.build_preview(samples.eml(), "message/rfc822", "signoff.eml")
    assert result["kind"] == "email"
    assert result["headers"]["from"] == "Alice Admin <alice@example.com>"
    assert result["headers"]["subject"] == "Access review sign-off"
    assert MARKER in result["body"]
    assert result["attachments"] == [{"name": "export.csv", "content_type": "text/csv", "size_bytes": 6}]


def test_html_email_preview_carries_no_markup():
    result = preview.build_preview(samples.eml(html_only=True, attachment=False), "message/rfc822", "h.eml")
    assert result["body"] == MARKER
    assert "<" not in result["body"]


def test_preview_holds_only_plain_data():
    """Nothing but strings, numbers, booleans, lists and dicts: it is drawn as text."""
    def plain(value):
        if isinstance(value, dict):
            return all(isinstance(key, str) and plain(item) for key, item in value.items())
        if isinstance(value, list):
            return all(plain(item) for item in value)
        return value is None or isinstance(value, (str, int, float, bool))

    for content_type, (filename, build, _marker) in SAMPLES.items():
        if preview.has_structured_preview(content_type, filename):
            assert plain(preview.build_preview(build(), content_type, filename)), content_type


@pytest.mark.parametrize("content_type", sorted(SAMPLES))
def test_every_structured_type_has_a_preview_and_no_other_type_does(content_type):
    from services import evidence_file_types as types

    filename, build, _marker = SAMPLES[content_type]
    if types.get_type(content_type).preview == types.PREVIEW_STRUCTURED:
        assert preview.build_preview(build(), content_type, filename)["kind"]
    else:
        assert not preview.has_structured_preview(content_type, filename)
        with pytest.raises(preview.PreviewNotAvailable):
            preview.build_preview(build(), content_type, filename)


@pytest.mark.parametrize("content_type,filename", [
    (DOCX, "x.docx"), (XLSX, "x.xlsx"), (PPTX, "x.pptx"), ("application/zip", "x.zip"),
])
def test_an_unreadable_file_raises_a_message_fit_to_show(content_type, filename):
    with pytest.raises(EvidenceReadError) as refused:
        preview.build_preview(b"not what it says it is", content_type, filename)
    assert str(refused.value)


def test_a_zip_bomb_is_refused_by_the_preview_too():
    bomb = samples.zip_declaring(210 * 1024 * 1024, members=3)
    with pytest.raises(EvidenceReadError, match="unpacks to"):
        preview.build_preview(bomb, "application/zip", "bomb.zip")


# ---------------------------------------------------------------------------
# The endpoint
# ---------------------------------------------------------------------------

def _file(org_id, filename="policy.docx", content_type=DOCX, scan_status="clean", size=1000):
    row = MagicMock()
    row.id = uuid4()
    row.organization_id = org_id
    row.s3_key = f"evidence/{org_id}/2026/11/abc_{filename}"
    row.filename = filename
    row.content_type = content_type
    row.scan_status = scan_status
    row.file_size_bytes = size
    row.storage_config_id = None
    return row


def _membership(org_id):
    membership = MagicMock()
    membership.organization_id = org_id
    membership.user.db_id = str(uuid4())
    membership.role = "viewer"
    return membership


async def _call(row, data=b"", org_id=None, audit=None):
    from api.evidence_files import preview_evidence_file

    org_id = org_id or (row.organization_id if row is not None else uuid4())
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    db.execute.return_value = result
    request = MagicMock()
    request.headers = {}
    request.client = None
    chunks = None if data is None else iter([data[: len(data) // 2], data[len(data) // 2:]])
    with patch("api.evidence_files.create_audit_entry", new=audit or AsyncMock()), \
            patch("api.evidence_files.download_blob_stream", return_value=chunks) as stream:
        response = await preview_evidence_file(
            org_id=org_id, evidence_id="ERL-001", file_id=uuid4(), request=request,
            membership=_membership(org_id), db=db,
        )
    return response, stream


class TestPreviewEndpoint:
    @pytest.mark.asyncio
    async def test_returns_the_preview_and_writes_a_custody_row(self):
        org_id = uuid4()
        audit = AsyncMock()
        response, stream = await _call(_file(org_id), samples.docx(), audit=audit)

        assert response["kind"] == "document"
        assert MARKER in response["paragraphs"]
        # Read from the store the file is in, for the file's organisation.
        assert stream.call_args.kwargs["org_id"] == str(org_id)
        audit.assert_awaited_once()
        entry = audit.await_args.kwargs
        assert entry["action"] == "download" and "preview" in entry["new_value"]
        assert entry["organization_id"] == org_id

    @pytest.mark.asyncio
    async def test_a_quarantined_file_is_refused_and_never_read(self):
        from fastapi import HTTPException

        with patch("api.evidence_files.download_blob_stream") as stream:
            with pytest.raises(HTTPException) as refused:
                await _call(_file(uuid4(), scan_status="infected"), samples.docx())
        assert refused.value.status_code == 403
        assert "quarantined" in refused.value.detail
        stream.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_file_in_another_organisation_is_not_found(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as refused:
            await _call(None, org_id=uuid4())
        assert refused.value.status_code == 404

    @pytest.mark.asyncio
    async def test_the_lookup_is_scoped_to_the_organisation_in_the_path(self):
        from api.evidence_files import preview_evidence_file

        org_id, file_id = uuid4(), uuid4()
        db = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        db.execute.return_value = result
        with pytest.raises(Exception):
            await preview_evidence_file(
                org_id=org_id, evidence_id="ERL-001", file_id=file_id, request=MagicMock(),
                membership=_membership(org_id), db=db,
            )
        statement = db.execute.await_args.args[0]
        compiled = str(statement.compile(compile_kwargs={"literal_binds": True}))
        assert org_id.hex in compiled.replace("-", "")
        assert "organization_id" in compiled and "is_deleted" in compiled

    @pytest.mark.asyncio
    @pytest.mark.parametrize("filename,content_type", [
        ("report.pdf", "application/pdf"), ("screen.png", "image/png"),
        ("server.log", "text/plain"), ("page.html", "text/html"),
    ])
    async def test_types_the_browser_shows_itself_have_no_preview(self, filename, content_type):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as refused:
            await _call(_file(uuid4(), filename, content_type), b"x")
        assert refused.value.status_code == 415

    @pytest.mark.asyncio
    async def test_a_file_recorded_as_too_large_is_refused_before_it_is_read(self):
        from fastapi import HTTPException

        row = _file(uuid4(), size=preview.PREVIEW_MAX_FILE_BYTES + 1)
        with patch("api.evidence_files.download_blob_stream") as stream:
            with pytest.raises(HTTPException) as refused:
                await _call(row, samples.docx())
        assert refused.value.status_code == 413
        stream.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_file_larger_than_its_record_says_is_stopped_while_reading(self, monkeypatch):
        from fastapi import HTTPException

        monkeypatch.setattr(preview, "PREVIEW_MAX_FILE_BYTES", 100)
        with pytest.raises(HTTPException) as refused:
            await _call(_file(uuid4(), size=50), b"z" * 5000)
        assert refused.value.status_code == 413

    @pytest.mark.asyncio
    async def test_an_unreadable_file_is_a_422_with_a_plain_reason(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as refused:
            await _call(_file(uuid4()), b"this is not a Word document")
        assert refused.value.status_code == 422
        assert "Word document" in refused.value.detail

    @pytest.mark.asyncio
    async def test_bytes_missing_from_storage_are_a_404(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as refused:
            await _call(_file(uuid4()), None)
        assert refused.value.status_code == 404


class TestPreviewsAreBuiltAFewAtATime:
    """A preview is CPU and memory spent in the API process; the number at once is bounded."""

    @staticmethod
    def _take_every_slot():
        for _ in range(preview.PREVIEW_MAX_AT_ONCE):
            assert preview._slots.acquire(blocking=False)

    @staticmethod
    def _give_back_every_slot():
        for _ in range(preview.PREVIEW_MAX_AT_ONCE):
            preview._slots.release()

    def test_a_preview_is_refused_while_every_slot_is_taken_and_built_once_one_is_free(self):
        self._take_every_slot()
        try:
            with pytest.raises(preview.PreviewBusy):
                preview.build_preview_in_turn(samples.docx(), DOCX, "policy.docx")
        finally:
            self._give_back_every_slot()
        assert preview.build_preview_in_turn(samples.docx(), DOCX, "policy.docx")["kind"] == "document"

    def test_a_preview_that_fails_gives_its_slot_back(self):
        for _ in range(preview.PREVIEW_MAX_AT_ONCE + 3):
            with pytest.raises(EvidenceReadError):
                preview.build_preview_in_turn(b"not a Word document", DOCX, "policy.docx")
        assert preview.build_preview_in_turn(samples.docx(), DOCX, "policy.docx")["kind"] == "document"

    def test_only_so_many_run_together(self):
        import threading

        running, most, gate = [], [], threading.Event()

        def slow(*_):
            running.append(1)
            most.append(len(running))
            gate.wait(5)
            running.pop()
            return {"kind": "document"}

        outcomes = []

        def ask():
            try:
                outcomes.append(preview.build_preview_in_turn(b"", DOCX, "a.docx"))
            except preview.PreviewBusy:
                outcomes.append("busy")

        with patch.object(preview, "build_preview", slow):
            threads = [threading.Thread(target=ask) for _ in range(preview.PREVIEW_MAX_AT_ONCE + 4)]
            for thread in threads:
                thread.start()
            deadline = time.monotonic() + 5    # the four that found no slot answer at once
            while len(outcomes) < 4 and time.monotonic() < deadline:
                time.sleep(0.01)
            gate.set()
            for thread in threads:
                thread.join(5)
        assert max(most) == preview.PREVIEW_MAX_AT_ONCE
        assert outcomes.count("busy") == 4

    @pytest.mark.asyncio
    async def test_the_endpoint_says_try_again_when_no_slot_frees_in_time(self, monkeypatch):
        from fastapi import HTTPException
        import api.evidence_files as endpoint

        monkeypatch.setattr(endpoint, "PREVIEW_WAIT_TRIES", 3)
        monkeypatch.setattr(endpoint, "PREVIEW_WAIT_SECONDS", 0)
        self._take_every_slot()
        try:
            with pytest.raises(HTTPException) as refused:
                await _call(_file(uuid4()), samples.docx())
        finally:
            self._give_back_every_slot()
        assert refused.value.status_code == 429
        assert refused.value.headers["Retry-After"]
        assert "busy" in refused.value.detail

    @pytest.mark.asyncio
    async def test_the_endpoint_waits_for_a_slot_and_then_answers(self, monkeypatch):
        import api.evidence_files as endpoint

        monkeypatch.setattr(endpoint, "PREVIEW_WAIT_SECONDS", 0)
        answers = iter([preview.PreviewBusy(), preview.PreviewBusy(), {"kind": "document", "paragraphs": []}])

        def in_turn(*_):
            answer = next(answers)
            if isinstance(answer, Exception):
                raise answer
            return answer

        monkeypatch.setattr(preview, "build_preview_in_turn", in_turn)
        response, _ = await _call(_file(uuid4()), samples.docx())
        assert response["kind"] == "document"


@pytest.mark.parametrize("build,content_type,name,reason", [
    (lambda: samples.xlsx_with_entities(1, 50), XLSX, "a.xlsx", "XML entities"),
    (lambda: samples.docx_with_document_part(5, "word/document.bin"), DOCX, "a.docx", "too large to preview"),
    (lambda: samples.office_hiding_its_size(samples.pptx, 1, __import__("zipfile").ZIP_BZIP2), PPTX, "a.pptx", "compression method"),
    (lambda: samples.eml_with_long_header("Content-Type", 400, "text/plain;"), "message/rfc822", "a.eml", "too long to read"),
], ids=["entities", "renamed-part", "bzip2", "email-header"])
def test_hostile_files_are_refused_by_the_preview_with_a_plain_reason(build, content_type, name, reason):
    with pytest.raises(EvidenceReadError, match=reason):
        preview.build_preview(build(), content_type, name)


def test_a_zip_preview_says_which_files_are_packed_in_a_way_that_is_not_read():
    result = preview.build_preview(samples.zip_hiding_its_size(megabytes=1), "application/zip", "a.zip")
    [member] = result["members"]
    assert "compression method" in member["note"]


class TestPreviewNeedsMembership:
    """Through the real router and the real role checker: no stubbing of auth."""

    @staticmethod
    def _client():
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from api.evidence_files import router
        from database import get_db

        app = FastAPI()
        app.include_router(router, prefix="/api")
        app.dependency_overrides[get_db] = lambda: AsyncMock()
        return TestClient(app, raise_server_exceptions=False)

    def test_no_credentials_no_preview(self):
        url = f"/api/organizations/{uuid4()}/evidence/ERL-001/files/{uuid4()}/preview"
        with patch("api.evidence_files.download_blob_stream") as stream:
            response = self._client().get(url)
        assert response.status_code in (401, 403)
        stream.assert_not_called()

    def test_the_route_declares_the_viewer_role_dependency(self):
        from api.evidence_files import router

        route = next(r for r in router.routes if r.path.endswith("/files/{file_id}/preview"))
        names = {
            getattr(dep.call, "__qualname__", "") for dep in route.dependant.dependencies
        }
        assert any("require_org_role" in name for name in names), names
