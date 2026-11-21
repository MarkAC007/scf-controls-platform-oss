# SPDX-License-Identifier: AGPL-3.0-only
"""What the viewer shows for a file a browser cannot draw by itself.

A browser can show a PDF, a picture and text. It cannot show a Word document,
an Excel workbook, a PowerPoint presentation, a ZIP archive or an email, and
the page should not be given a parser for each: those formats are where
hostile files live, and a parser in the page runs in the user's session.

So the server reads the file, with the same readers the AI assessment uses
(``services.evidence_readers``), and returns plain data: strings and lists of
strings. The page draws them as text. Nothing from the file is ever returned
as markup.

The limits are tighter than the assessment's
(``evidence_readers.PREVIEW_OFFICE_LIMITS``): this runs inside the API process
while a person waits, not in a worker.

``build_preview`` is synchronous and CPU-bound; call it off the event loop.
"""
import threading
from typing import Optional

from services import evidence_file_types, evidence_readers
from services.evidence_readers import PREVIEW_OFFICE_LIMITS, EvidenceReadError

#: A file larger than this is not previewed (it can still be downloaded).
PREVIEW_MAX_FILE_BYTES = 20 * 1024 * 1024

DOC_MAX_PARAGRAPHS = 2000
TEXT_MAX_CHARS = 200_000
SHEET_MAX_SHEETS = 20
SHEET_MAX_ROWS = 200
SHEET_MAX_COLUMNS = 50
SLIDES_MAX = 200
CELL_MAX_CHARS = 500

KIND_DOCUMENT = "document"
KIND_SPREADSHEET = "spreadsheet"
KIND_SLIDES = "slides"
KIND_ARCHIVE = "archive"
KIND_EMAIL = "email"


#: Previews built at the same moment. A preview is CPU and memory spent inside
#: the API process, on a thread that cannot be stopped once it has started,
#: so the number running together is what bounds the cost of a hostile file.
PREVIEW_MAX_AT_ONCE = 2
_slots = threading.BoundedSemaphore(PREVIEW_MAX_AT_ONCE)


class PreviewBusy(Exception):
    """Every preview slot is taken. Nothing was read; ask again shortly."""


def build_preview_in_turn(data: bytes, content_type: Optional[str], filename: str = "") -> dict:
    """``build_preview``, unless ``PREVIEW_MAX_AT_ONCE`` are already running.

    The slot is taken and given back on the thread that does the work, so a
    request that is dropped while its preview runs still frees it.
    """
    if not _slots.acquire(blocking=False):
        raise PreviewBusy()
    try:
        return build_preview(data, content_type, filename)
    finally:
        _slots.release()


class PreviewNotAvailable(Exception):
    """This kind of file has no server-side preview."""


def has_structured_preview(content_type: Optional[str], filename: str = "") -> bool:
    kind = evidence_file_types.get_type(content_type) or evidence_file_types.type_for_filename(filename)
    return kind is not None and kind.preview == evidence_file_types.PREVIEW_STRUCTURED


def build_preview(data: bytes, content_type: Optional[str], filename: str = "") -> dict:
    """Read a file into the structure the viewer draws.

    Raises ``PreviewNotAvailable`` for a type with no structured preview and
    ``EvidenceReadError`` (message safe to show) when the file cannot be read.
    """
    kind = evidence_file_types.get_type(content_type) or evidence_file_types.type_for_filename(filename)
    if kind is None or kind.preview != evidence_file_types.PREVIEW_STRUCTURED:
        raise PreviewNotAvailable(content_type or "unknown")

    ct = kind.content_type
    if "wordprocessingml" in ct:
        return _document(data)
    if "spreadsheetml" in ct:
        return _spreadsheet(data)
    if "presentationml" in ct:
        return _slides(data)
    if ct == "application/zip":
        return _archive(data)
    if ct == "message/rfc822":
        return _email(data)
    raise PreviewNotAvailable(ct)


def _result(kind: str, truncated: bool, note: Optional[str], **fields) -> dict:
    return {"kind": kind, "truncated": truncated, "note": note, **fields}


def _document(data: bytes) -> dict:
    paragraphs = evidence_readers.read_docx_paragraphs(data, limits=PREVIEW_OFFICE_LIMITS)
    total = len(paragraphs)
    shown = []
    chars = 0
    cut_short = False
    for paragraph in paragraphs[:DOC_MAX_PARAGRAPHS]:
        room = TEXT_MAX_CHARS - chars
        if len(paragraph) > room:
            # Show what fits of the paragraph that crosses the limit, so a
            # document that is one very long paragraph is not shown as empty.
            if room > 0:
                shown.append(paragraph[:room])
            cut_short = True
            break
        shown.append(paragraph)
        chars += len(paragraph)
    truncated = cut_short or len(shown) < total
    return _result(
        KIND_DOCUMENT, truncated,
        f"Showing the start of this document ({len(shown):,} of {total:,} paragraphs). "
        "Download the file to read the rest."
        if truncated else
        "Text only. Table rows are shown as lines of text; pictures and layout are not shown. "
        "Download the file to see them.",
        paragraphs=shown,
    )


def _spreadsheet(data: bytes) -> dict:
    # One sheet more than is shown, so "there are more" can be said truthfully.
    sheets = evidence_readers.read_xlsx_sheets(
        data,
        max_rows_per_sheet=SHEET_MAX_ROWS,
        max_columns=SHEET_MAX_COLUMNS + 1,
        max_sheets=SHEET_MAX_SHEETS + 1,
        limits=PREVIEW_OFFICE_LIMITS,
    )
    more_sheets = len(sheets) > SHEET_MAX_SHEETS
    out = []
    any_cut = more_sheets
    for sheet in sheets[:SHEET_MAX_SHEETS]:
        wide = any(len(row) > SHEET_MAX_COLUMNS for row in sheet.rows)
        rows = [[cell[:CELL_MAX_CHARS] for cell in row[:SHEET_MAX_COLUMNS]] for row in sheet.rows]
        cut = sheet.truncated or wide
        any_cut = any_cut or cut
        out.append({"name": sheet.name[:200], "rows": rows, "truncated": cut})
    return _result(
        KIND_SPREADSHEET, any_cut,
        (
            f"Showing up to {SHEET_MAX_ROWS} rows and {SHEET_MAX_COLUMNS} columns of the first "
            f"{SHEET_MAX_SHEETS} sheets. Download the file to see all of it."
        ) if any_cut else "Cell values only. Formulas, formatting and charts are not shown.",
        sheets=out,
    )


def _slides(data: bytes) -> dict:
    slides = evidence_readers.read_pptx_slides(data, limits=PREVIEW_OFFICE_LIMITS)
    total = len(slides)
    shown = [
        {"number": slide.number, "paragraphs": [p[:TEXT_MAX_CHARS] for p in slide.paragraphs]}
        for slide in slides[:SLIDES_MAX]
    ]
    truncated = total > len(shown)
    return _result(
        KIND_SLIDES, truncated,
        f"Showing the first {len(shown)} of {total} slides." if truncated
        else "Slide text only. Pictures, charts and layout are not shown.",
        slides=shown,
    )


def _archive(data: bytes) -> dict:
    zf, members = evidence_readers.open_evidence_zip(data)
    zf.close()
    listed = [
        {
            "name": member.name,
            "size_bytes": member.size_bytes,
            "label": member.label,
            "readable": member.readable,
            "note": member.note,
        }
        for member in members
    ]
    readable = sum(1 for member in members if member.readable)
    return _result(
        KIND_ARCHIVE, False,
        f"{len(members):,} file{'s' if len(members) != 1 else ''} in this archive; "
        f"the AI assessment reads {readable:,} of them. Download the archive to open the files.",
        members=listed,
    )


def _email(data: bytes) -> dict:
    message = evidence_readers.read_eml(data)
    body = message.body
    truncated = len(body) > TEXT_MAX_CHARS
    return _result(
        KIND_EMAIL, truncated,
        "Showing the start of the message body." if truncated
        else ("Attachments are listed and not opened." if message.attachments else None),
        headers=message.headers,
        body=body[:TEXT_MAX_CHARS],
        attachments=message.attachments,
    )


__all__ = [
    "EvidenceReadError",
    "PREVIEW_MAX_AT_ONCE",
    "PREVIEW_MAX_FILE_BYTES",
    "PreviewBusy",
    "PreviewNotAvailable",
    "build_preview",
    "build_preview_in_turn",
    "has_structured_preview",
]
