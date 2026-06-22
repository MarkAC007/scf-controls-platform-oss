"""Unit tests for backend.services.cdm_docling_service.

Slice 13.2 (Epic #615 D-13.2.3). The service is now a thin HTTP client
against the ``scf-cdm-docling`` Container App (running off-the-shelf
docling-serve). These tests mock the three docling-serve endpoints with
``httpx.MockTransport`` so the suite stays fast, deterministic, and does
not require the ``docling`` pip package on the test host. A real
end-to-end docling-serve round-trip lives in the staging verification
step (see PRD ISC-42/43).
"""
from __future__ import annotations

import json
import os
import sys
from typing import Callable

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ENABLE_CDM", "true")

from services import cdm_docling_service  # noqa: E402
from services.cdm_docling_service import (  # noqa: E402
    DoclingExtractionError,
    DoclingResult,
    DoclingUnsupportedFormatError,
    Section,
    extract,
    is_docling_format,
)


# ───────────────────────── mock-transport plumbing ──────────────────────────


def _install_mock_client(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    """Replace ``cdm_docling_service.httpx.Client`` with a constructor that
    returns an httpx.Client backed by ``httpx.MockTransport(handler)``.

    Discards any ``base_url`` / ``timeout`` kwargs the production code
    passes — the MockTransport intercepts all requests regardless of host.
    """
    real_client = httpx.Client

    def _factory(**kwargs):
        # Preserve base_url so production code's relative paths resolve;
        # ignore timeout (MockTransport doesn't honour it anyway).
        base_url = kwargs.get("base_url", "http://test-docling")
        return real_client(transport=httpx.MockTransport(handler), base_url=base_url)

    monkeypatch.setattr(cdm_docling_service.httpx, "Client", _factory)


def _json_response(status_code: int, payload: dict) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        content=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"},
    )


def _make_router(
    *,
    submit: httpx.Response | None = None,
    poll_sequence: list[httpx.Response] | None = None,
    result: httpx.Response | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    """Build a request handler that dispatches by path. ``poll_sequence`` is
    consumed in order; once exhausted, the last response is returned for any
    further poll request."""
    poll_state = {"index": 0}
    poll_responses = list(poll_sequence or [])

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/v1/convert/file/async"):
            if submit is None:
                raise AssertionError("Unexpected submit call in this test")
            return submit
        if "/v1/status/poll/" in path:
            if not poll_responses:
                raise AssertionError("Unexpected poll call in this test")
            idx = min(poll_state["index"], len(poll_responses) - 1)
            poll_state["index"] += 1
            return poll_responses[idx]
        if "/v1/result/" in path:
            if result is None:
                raise AssertionError("Unexpected result call in this test")
            return result
        raise AssertionError(f"Unexpected request path: {path}")

    return handler


@pytest.fixture
def docling_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set the env vars required by the service to a test-fast configuration."""
    monkeypatch.setenv("CDM_DOCLING_URL", "http://test-docling")
    monkeypatch.setenv("DOCLING_MAX_POLLS", "10")
    monkeypatch.setenv("DOCLING_POLL_INTERVAL_SECONDS", "0.01")


# ─────────────────────────── routing ────────────────────────────────


def test_is_docling_format_routes_pdf_docx_image_to_docling():
    assert is_docling_format("application/pdf") is True
    assert is_docling_format("application/vnd.openxmlformats-officedocument.wordprocessingml.document") is True
    assert is_docling_format("application/msword") is True
    assert is_docling_format("image/png") is True
    assert is_docling_format("image/jpeg") is True


def test_is_docling_format_rejects_plain_text_formats():
    assert is_docling_format("text/plain") is False
    assert is_docling_format("text/csv") is False
    assert is_docling_format("application/json") is False
    assert is_docling_format("application/yaml") is False
    assert is_docling_format("application/x-yaml") is False
    assert is_docling_format("text/yaml") is False


def test_is_docling_format_handles_case_and_empty():
    assert is_docling_format("APPLICATION/PDF") is True
    assert is_docling_format("") is False
    assert is_docling_format("application/octet-stream") is False


# ──────────────────────── error contracts ───────────────────────────


def test_extract_raises_on_plain_text_format():
    with pytest.raises(DoclingUnsupportedFormatError) as exc_info:
        extract(b"some,csv,row\n", "text/csv", "data.csv")
    assert "text/csv" in str(exc_info.value)


def test_extract_raises_on_empty_payload():
    with pytest.raises(DoclingExtractionError) as exc_info:
        extract(b"", "application/pdf", "empty.pdf")
    assert "empty" in str(exc_info.value).lower()


def test_extract_wraps_docling_exceptions_as_extraction_error(
    monkeypatch: pytest.MonkeyPatch, docling_env: None
):
    """A 5xx from docling-serve must surface as DoclingExtractionError with
    a clean 'unavailable' message so the celery task can persist
    ``ingest_status='failed'`` and trigger its retry policy."""
    handler = _make_router(
        submit=httpx.Response(
            status_code=500,
            content=b"internal server error",
            headers={"content-type": "text/plain"},
        ),
    )
    _install_mock_client(monkeypatch, handler)

    with pytest.raises(DoclingExtractionError) as exc_info:
        extract(b"\x25PDF-1.4\nsome bytes\n", "application/pdf", "bad.pdf")
    message = str(exc_info.value)
    assert "docling-serve unavailable" in message
    assert "500" in message


# ──────────────────────── happy path ────────────────────────────────


_HAPPY_MARKDOWN = (
    "# Policy Document\n\n"
    "Introduction paragraph here.\n\n"
    "## Access Control\n\n"
    "Body of access control section.\n\n"
    "## Encryption\n\n"
    "Body of encryption section.\n"
)


def _happy_handler():
    return _make_router(
        submit=_json_response(200, {"task_id": "abc-123", "task_status": "pending"}),
        poll_sequence=[_json_response(200, {"task_status": "success"})],
        result=_json_response(
            200,
            {
                "document": {
                    "md_content": _HAPPY_MARKDOWN,
                    "json_content": {},
                    "html_content": "",
                    "text_content": "",
                    "doctags_content": "",
                    "filename": "policy.pdf",
                },
                "status": "success",
                "processing_time": 1.23,
                "timings": {},
                "errors": [],
            },
        ),
    )


def test_extract_returns_markdown_and_sections_from_fake_docling(
    monkeypatch: pytest.MonkeyPatch, docling_env: None
):
    _install_mock_client(monkeypatch, _happy_handler())

    result = extract(b"\x25PDF-fake", "application/pdf", "policy.pdf")

    assert isinstance(result, DoclingResult)
    assert result.markdown == _HAPPY_MARKDOWN
    assert result.ocr_used is False
    # intermediate_json must be the full envelope, not just the document.
    assert result.intermediate_json["status"] == "success"
    assert result.intermediate_json["document"]["md_content"] == _HAPPY_MARKDOWN

    titles = [s.title for s in result.sections]
    levels = [s.level for s in result.sections]
    assert titles == ["Policy Document", "Access Control", "Encryption"]
    assert levels == [1, 2, 2]
    assert all(isinstance(s, Section) for s in result.sections)


def test_extract_byte_ranges_form_a_contiguous_partition(
    monkeypatch: pytest.MonkeyPatch, docling_env: None
):
    """ISC-12 — each section's byte_end equals the next section's byte_start
    (and the trailing section ends at len(markdown))."""
    _install_mock_client(monkeypatch, _happy_handler())

    result = extract(b"x", "application/pdf", "policy.pdf")

    secs = result.sections
    assert len(secs) == 3
    for i in range(len(secs) - 1):
        assert secs[i].byte_end == secs[i + 1].byte_start, (
            f"Section {i} byte_end {secs[i].byte_end} should equal next byte_start {secs[i+1].byte_start}"
        )
    assert secs[-1].byte_end == len(result.markdown)


def test_extract_word_count_property_matches_markdown_split(
    monkeypatch: pytest.MonkeyPatch, docling_env: None
):
    markdown = "# H\n\none two three four five"
    handler = _make_router(
        submit=_json_response(200, {"task_id": "wc-1", "task_status": "pending"}),
        poll_sequence=[_json_response(200, {"task_status": "success"})],
        result=_json_response(
            200,
            {
                "document": {"md_content": markdown, "json_content": {}},
                "status": "success",
                "timings": {},
                "errors": [],
            },
        ),
    )
    _install_mock_client(monkeypatch, handler)

    result = extract(b"x", "application/pdf", "x.pdf")
    assert result.word_count == len(markdown.split())
    assert result.word_count == 7


def test_extract_handles_empty_texts_list_with_no_sections(
    monkeypatch: pytest.MonkeyPatch, docling_env: None
):
    """Markdown with no ``#`` heading lines must yield an empty sections list."""
    markdown = "Plain paragraph with no headings detected at all."
    handler = _make_router(
        submit=_json_response(200, {"task_id": "hl-1", "task_status": "pending"}),
        poll_sequence=[_json_response(200, {"task_status": "success"})],
        result=_json_response(
            200,
            {
                "document": {"md_content": markdown, "json_content": {}},
                "status": "success",
                "timings": {},
                "errors": [],
            },
        ),
    )
    _install_mock_client(monkeypatch, handler)

    result = extract(b"x", "application/pdf", "headless.pdf")
    assert result.markdown == markdown
    assert result.sections == []


# ──────────────────────── new failure modes ─────────────────────────


def test_extract_raises_on_task_failure_status(
    monkeypatch: pytest.MonkeyPatch, docling_env: None
):
    """task_status=='failure' from poll → DoclingExtractionError carrying the
    error_message verbatim so the celery task can log a meaningful failure."""
    handler = _make_router(
        submit=_json_response(200, {"task_id": "fail-1", "task_status": "pending"}),
        poll_sequence=[
            _json_response(
                200,
                {"task_status": "failure", "error_message": "PDF corrupt"},
            )
        ],
    )
    _install_mock_client(monkeypatch, handler)

    with pytest.raises(DoclingExtractionError) as exc_info:
        extract(b"\x25PDF-bad", "application/pdf", "corrupt.pdf")
    assert "PDF corrupt" in str(exc_info.value)


def test_extract_raises_on_polling_timeout(
    monkeypatch: pytest.MonkeyPatch,
):
    """Polling that never reaches success/failure within DOCLING_MAX_POLLS
    attempts → DoclingExtractionError with 'timeout' in the message."""
    monkeypatch.setenv("CDM_DOCLING_URL", "http://test-docling")
    monkeypatch.setenv("DOCLING_MAX_POLLS", "3")
    monkeypatch.setenv("DOCLING_POLL_INTERVAL_SECONDS", "0.01")

    handler = _make_router(
        submit=_json_response(200, {"task_id": "slow-1", "task_status": "pending"}),
        poll_sequence=[
            _json_response(200, {"task_status": "pending"}),
            _json_response(200, {"task_status": "started"}),
            _json_response(200, {"task_status": "started"}),
        ],
    )
    _install_mock_client(monkeypatch, handler)

    with pytest.raises(DoclingExtractionError) as exc_info:
        extract(b"\x25PDF-slow", "application/pdf", "slow.pdf")
    assert "timeout" in str(exc_info.value).lower()
    assert "slow-1" in str(exc_info.value)
