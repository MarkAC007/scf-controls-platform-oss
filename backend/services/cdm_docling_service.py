"""Thin HTTP client for the scf-cdm-docling Container App.

This module is a thin HTTP client against the ``scf-cdm-docling`` Container
App (running off-the-shelf docling-serve). It does NOT import the
``docling`` Python package. Slice 13.2 (Epic #615 D-13.2.3) moved the heavy
Docling runtime out of the celery worker image into a dedicated
scale-to-zero sidecar.

Architecture:

    celery_worker ──HTTP──▶ scf-cdm-docling (docling-serve-cpu)
        │                       │
        │  POST /v1/convert/file/async ──▶ {task_id, task_status}
        │                       │
        │  GET /v1/status/poll/{task_id} (loop) ──▶ task_status
        │                       │
        │  GET /v1/result/{task_id} ──▶ {document.md_content, ...}
        ▼
    DoclingResult

Output contract (consumed by ``tasks_cdm.ingest_cdm_document`` and the
``derive_section`` mapping helper):

- ``markdown``: the ``document.md_content`` string from docling-serve.
  LightRAG indexes this directly so chunks carry heading context.
- ``sections``: ordered list of ``Section(level, title, byte_start,
  byte_end)`` where the byte offsets are positions inside the markdown
  string. ``byte_end`` is the byte_start of the *next* section (or the
  end of the document for the trailing section), so the half-open
  range ``[byte_start, byte_end)`` exactly covers the section body.
  Section boundaries are parsed directly from markdown ``#`` heading
  lines via regex (see ``_extract_sections``); docling-serve's
  ``json_content`` is empty by default and cannot be used (PRD F-11).
- ``page_count``: number of source pages (best effort; fallback 1).
- ``ocr_used``: True iff docling-serve's timings indicate OCR ran on
  any page (best effort; fallback False).
- ``intermediate_json``: the full ``/v1/result`` response envelope,
  persisted as ``.docling.json`` alongside the raw payload so
  re-chunking and auditor review are offline-safe.

Errors:
- ``DoclingUnsupportedFormatError``: caller routed a plain-text format
  (.txt/.csv/.json/.yaml) here by mistake. These belong on
  ``text_extraction_service``.
- ``DoclingExtractionError``: docling-serve itself failed (HTTP error,
  task failure, timeout, missing fields, misconfiguration). Wraps a
  clean message so the ingest task can log a failure and persist
  ``ingest_status='failed'``.

Environment:
- ``CDM_DOCLING_URL`` — REQUIRED. Base URL of the scf-cdm-docling
  Container App (e.g. ``http://scf-cdm-docling.internal.<env-id>.<region>.azurecontainerapps.io``).
- ``DOCLING_MAX_POLLS`` — optional, default 180. Max poll attempts.
- ``DOCLING_POLL_INTERVAL_SECONDS`` — optional, default 2.0. Seconds
  between poll attempts.

Total worst-case wall time: ``DOCLING_MAX_POLLS * DOCLING_POLL_INTERVAL_SECONDS``
= 360s by default. The celery task wrapping this call owns the retry
policy; this module fails fast on the first unrecoverable signal.
"""
from __future__ import annotations

import io
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


# Mime types Docling owns on the CDM ingest path.
_DOCLING_MIME_PREFIXES: tuple[str, ...] = (
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
    "image/",  # image-only PDFs are content_type pdf; standalone images go via OCR too
)

# Plain-text formats that MUST stay on ``text_extraction_service`` —
# Docling adds no value and pulls a large dependency chain.
_PLAIN_TEXT_MIME_PREFIXES: tuple[str, ...] = (
    "text/plain",
    "text/csv",
    "application/json",
    "application/yaml",
    "application/x-yaml",
    "text/yaml",
)

# Regex matching markdown ATX headings (one to six `#` followed by space/tab
# and the heading text). Anchored to start-of-line via MULTILINE so
# ``match.start()`` gives the byte offset of the `#` itself, which is the
# canonical section boundary used by ``cdm_mapping._resolve_from_sections``.
_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$", re.MULTILINE)

# Default polling budget. Override via env vars in production if a tenant's
# documents push past the 360s ceiling — but prefer to fix the document
# rather than raise the cap.
_DEFAULT_MAX_POLLS = 180
_DEFAULT_POLL_INTERVAL_SECONDS = 2.0


class DoclingUnsupportedFormatError(ValueError):
    """The caller routed a plain-text format to Docling. Use text_extraction_service."""


class DoclingExtractionError(RuntimeError):
    """docling-serve itself failed. The wrapped message is safe to surface in ingest_error."""


@dataclass(frozen=True)
class Section:
    """One heading-anchored section inside the Docling markdown output."""

    level: int
    title: str
    byte_start: int
    byte_end: int


@dataclass(frozen=True)
class DoclingResult:
    """Return shape of :func:`extract`. All fields are populated on success."""

    markdown: str
    sections: list[Section]
    page_count: int
    ocr_used: bool
    intermediate_json: dict[str, Any] = field(default_factory=dict)

    @property
    def word_count(self) -> int:
        """Word count of the markdown body — matches CDMDocument.word_count semantics."""
        return len(self.markdown.split()) if self.markdown else 0


def is_docling_format(content_type: str) -> bool:
    """Return True if ``content_type`` should be routed to Docling on CDM ingest."""
    ct = (content_type or "").lower()
    if any(ct.startswith(p) for p in _PLAIN_TEXT_MIME_PREFIXES):
        return False
    return any(ct.startswith(p) for p in _DOCLING_MIME_PREFIXES)


def extract(
    payload: bytes,
    content_type: str,
    filename: str = "",
) -> DoclingResult:
    """Submit ``payload`` to docling-serve and return the structured result.

    Flow:
        1. Validate format + non-empty payload.
        2. Resolve docling-serve base URL and polling config from env.
        3. POST /v1/convert/file/async with the payload as multipart form-data.
        4. Poll /v1/status/poll/{task_id} until success/failure/timeout.
        5. GET /v1/result/{task_id} and unpack ``document.md_content``.
        6. Re-derive section byte offsets from the markdown headings.

    Any unrecoverable signal (HTTP 5xx, task failure, polling timeout,
    missing fields) is raised as :class:`DoclingExtractionError`. The
    caller is responsible for retry — this function is single-attempt.
    """
    if not is_docling_format(content_type):
        raise DoclingUnsupportedFormatError(
            f"Docling does not handle content_type={content_type!r}; "
            "route to text_extraction_service instead."
        )
    if not payload:
        raise DoclingExtractionError("Docling received an empty payload")

    docling_url = (os.environ.get("CDM_DOCLING_URL") or "").strip()
    if not docling_url:
        raise DoclingExtractionError("CDM_DOCLING_URL not configured")

    max_polls = _env_int("DOCLING_MAX_POLLS", _DEFAULT_MAX_POLLS)
    poll_interval = _env_float("DOCLING_POLL_INTERVAL_SECONDS", _DEFAULT_POLL_INTERVAL_SECONDS)

    safe_name = filename or "cdm-document.bin"

    timeout = httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0)
    with httpx.Client(base_url=docling_url, timeout=timeout) as client:
        task_id = _submit(client, payload, content_type, safe_name)
        _poll_until_done(client, task_id, max_polls, poll_interval)
        result_body = _fetch_result(client, task_id)

    document = result_body.get("document")
    if not isinstance(document, dict) or "md_content" not in document:
        raise DoclingExtractionError("docling result missing document.md_content")

    markdown = document.get("md_content") or ""
    sections = _extract_sections(markdown)
    page_count = _page_count_from_result(result_body)
    ocr_used = _detect_ocr(result_body)

    return DoclingResult(
        markdown=markdown,
        sections=sections,
        page_count=page_count,
        ocr_used=ocr_used,
        intermediate_json=result_body,
    )


# ─────────────────────────── HTTP helpers ───────────────────────────


def _submit(
    client: httpx.Client,
    payload: bytes,
    content_type: str,
    filename: str,
) -> str:
    """POST the payload to docling-serve's async-convert endpoint. Return task_id."""
    files = {"files": (filename, io.BytesIO(payload), content_type)}
    try:
        response = client.post("/v1/convert/file/async", files=files)
    except httpx.HTTPError as exc:
        raise DoclingExtractionError(
            f"docling-serve unreachable: {exc.__class__.__name__}: {exc}"
        ) from exc

    if response.status_code >= 500:
        raise DoclingExtractionError(
            f"docling-serve unavailable: HTTP {response.status_code}"
        )
    if response.status_code >= 300:
        raise DoclingExtractionError(
            f"docling-serve submit failed: HTTP {response.status_code} — "
            f"{_truncate_body(response.text)}"
        )

    body = _parse_json(response, "submit")
    task_id = body.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        raise DoclingExtractionError("docling-serve submit response missing task_id")
    return task_id


def _poll_until_done(
    client: httpx.Client,
    task_id: str,
    max_polls: int,
    poll_interval: float,
) -> None:
    """Poll /v1/status/poll/{task_id} until status is success/failure/timeout.

    Returns normally on success; raises on failure or timeout. Does not
    retry HTTP 5xx — celery owns the retry policy upstream.
    """
    for _ in range(max_polls):
        try:
            response = client.get(f"/v1/status/poll/{task_id}")
        except httpx.HTTPError as exc:
            raise DoclingExtractionError(
                f"docling-serve poll unreachable: {exc.__class__.__name__}: {exc}"
            ) from exc

        if response.status_code >= 500:
            raise DoclingExtractionError(
                f"docling-serve unavailable: HTTP {response.status_code}"
            )
        if response.status_code >= 300:
            raise DoclingExtractionError(
                f"docling-serve poll failed: HTTP {response.status_code} — "
                f"{_truncate_body(response.text)}"
            )

        body = _parse_json(response, "poll")
        status = body.get("task_status")
        if status == "success":
            return
        if status == "failure":
            error_message = body.get("error_message") or "no error message"
            raise DoclingExtractionError(f"docling task failed: {error_message}")
        # Any other state (pending / started / queued / etc.) — keep polling.
        time.sleep(poll_interval)

    raise DoclingExtractionError(
        f"docling timeout after {max_polls * poll_interval}s for task {task_id}"
    )


def _fetch_result(client: httpx.Client, task_id: str) -> dict[str, Any]:
    """GET /v1/result/{task_id}. Return the parsed JSON envelope."""
    try:
        response = client.get(f"/v1/result/{task_id}")
    except httpx.HTTPError as exc:
        raise DoclingExtractionError(
            f"docling-serve result fetch unreachable: {exc.__class__.__name__}: {exc}"
        ) from exc

    if response.status_code >= 300:
        raise DoclingExtractionError(
            f"docling-serve result fetch failed: HTTP {response.status_code}"
        )

    body = _parse_json(response, "result")
    if not isinstance(body, dict):
        raise DoclingExtractionError("docling result envelope is not a JSON object")
    return body


def _parse_json(response: httpx.Response, label: str) -> dict[str, Any]:
    """Best-effort JSON decode with a clean error message."""
    try:
        body = response.json()
    except ValueError as exc:
        raise DoclingExtractionError(
            f"docling-serve {label} response is not JSON: {_truncate_body(response.text)}"
        ) from exc
    if not isinstance(body, dict):
        raise DoclingExtractionError(
            f"docling-serve {label} response is not a JSON object"
        )
    return body


def _truncate_body(text: str, limit: int = 500) -> str:
    """Trim a response body for inclusion in error messages."""
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _env_int(name: str, default: int) -> int:
    """Read a positive int env var. Falls back to ``default`` on missing/invalid."""
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid int for %s=%r; using default %d", name, raw, default)
        return default
    if value <= 0:
        logger.warning("Non-positive %s=%d; using default %d", name, value, default)
        return default
    return value


def _env_float(name: str, default: float) -> float:
    """Read a positive float env var. Falls back to ``default`` on missing/invalid."""
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Invalid float for %s=%r; using default %s", name, raw, default)
        return default
    if value <= 0:
        logger.warning("Non-positive %s=%s; using default %s", name, value, default)
        return default
    return value


# ─────────────────────────── parsing helpers ───────────────────────────


def _extract_sections(markdown: str) -> list[Section]:
    """Parse markdown ATX headings into ordered ``Section`` byte ranges.

    Strategy (per PRD F-11):
    - docling-serve returns ``json_content = {}`` by default, so we cannot
      walk ``DoclingDocument.texts`` for typed heading items.
    - Instead, regex-match ATX heading lines directly in ``md_content``.
    - ``byte_start`` is the start of the heading line (``#`` itself).
    - ``byte_end`` for section i is the byte_start of section i+1, or
      ``len(markdown)`` for the trailing section, giving a contiguous
      half-open partition over the markdown body.
    """
    if not markdown:
        return []

    heads: list[tuple[int, int, str]] = []
    for match in _HEADING_RE.finditer(markdown):
        title = match.group(2).strip()
        if not title:
            continue
        level = len(match.group(1))
        heads.append((match.start(), level, title))

    if not heads:
        return []

    end = len(markdown)
    sections: list[Section] = []
    for i, (byte_start, level, title) in enumerate(heads):
        byte_end = heads[i + 1][0] if i + 1 < len(heads) else end
        sections.append(
            Section(
                level=level,
                title=title,
                byte_start=byte_start,
                byte_end=byte_end,
            )
        )
    return sections


def _page_count_from_result(result: dict[str, Any]) -> int:
    """Best-effort page count from the docling-serve result envelope.

    docling-serve's documented envelope does not include a top-level page
    count for asynchronous conversions, and ``document.json_content`` is
    empty by default (PRD F-11). We probe a few likely locations and
    fall back to 1 — under-reporting is safer than over-reporting for
    audit telemetry.
    """
    document = result.get("document")
    if isinstance(document, dict):
        json_content = document.get("json_content")
        if isinstance(json_content, dict):
            pages = json_content.get("pages")
            if isinstance(pages, dict) and pages:
                return len(pages)
            num_pages = json_content.get("num_pages")
            if isinstance(num_pages, int) and num_pages > 0:
                return num_pages

    timings = result.get("timings")
    if isinstance(timings, dict):
        pages = timings.get("pages")
        if isinstance(pages, int) and pages > 0:
            return pages

    return 1


def _detect_ocr(result: dict[str, Any]) -> bool:
    """True iff any timings key references OCR (case-insensitive).

    docling-serve emits per-stage timing keys (e.g. ``layout``, ``ocr``,
    ``table_structure``); presence of an ``ocr``-named key indicates the
    OCR stage ran. Absence means OCR was not invoked on this document.
    """
    timings = result.get("timings")
    if not isinstance(timings, dict):
        return False
    return any("ocr" in str(key).lower() for key in timings.keys())
