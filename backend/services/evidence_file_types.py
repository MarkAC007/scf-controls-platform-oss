# SPDX-License-Identifier: AGPL-3.0-only
"""The one definition of which files are accepted as evidence.

Five places used to answer "is this file accepted, and what is it?" on their
own: the storage allowlist, the malware scan's copy of it, the extension map
used when a record is confirmed, the page's upload control and the viewer.
They drifted (issue #1309): plain text was accepted by the API and refused by
the page, and a file the API took could be one the AI assessment could not
read.

Everything now derives from ``EVIDENCE_FILE_TYPES``. To accept a new kind of
file, add one row here and teach ``text_extraction_service`` to read it; the
allowlists, the extension map, the ``/evidence-file-types`` endpoint and the
page follow.

This module imports nothing from the application, so every layer (storage
driver, scan service, API, tasks) can import it without a cycle.

HTML and SVG are deliberately absent. Evidence downloads are served from the
application's own origin, and both can carry script.
"""
from dataclasses import dataclass
from typing import Optional

#: Upload size limit. Mirrors ``EvidenceFileUploadUrlRequest.file_size_bytes``.
MAX_EVIDENCE_FILE_BYTES = 50 * 1024 * 1024

# How the viewer shows a file.
PREVIEW_PDF = "pdf"                # browser's own PDF viewer
PREVIEW_IMAGE = "image"            # <img>
PREVIEW_MARKDOWN = "markdown"      # rendered markdown
PREVIEW_CODE = "code"              # the bytes, verbatim, as text
PREVIEW_CSV = "csv"                # table drawn by the page
PREVIEW_STRUCTURED = "structured"  # read on the server: GET .../files/{id}/preview
PREVIEW_NONE = "none"

# How the AI assessment reads a file.
AI_TEXT = "text"        # text is extracted and sent
AI_IMAGE = "image"      # sent to the model as a picture
AI_ARCHIVE = "archive"  # unpacked; each member read by its own kind


@dataclass(frozen=True)
class EvidenceFileType:
    """One accepted kind of evidence file."""

    content_type: str
    label: str
    extensions: tuple
    preview: str
    ai: str
    #: False when a browser handed this content type from our origin could run
    #: script in it. Such a file is only ever sent as a plain-text attachment.
    inline_safe: bool = True


_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

EVIDENCE_FILE_TYPES: tuple = (
    EvidenceFileType("application/pdf", "PDF", (".pdf",), PREVIEW_PDF, AI_TEXT),
    EvidenceFileType("image/png", "PNG image", (".png",), PREVIEW_IMAGE, AI_IMAGE),
    EvidenceFileType("image/jpeg", "JPEG image", (".jpg", ".jpeg"), PREVIEW_IMAGE, AI_IMAGE),
    EvidenceFileType("image/gif", "GIF image", (".gif",), PREVIEW_IMAGE, AI_IMAGE),
    EvidenceFileType("image/webp", "WebP image", (".webp",), PREVIEW_IMAGE, AI_IMAGE),
    EvidenceFileType("text/csv", "CSV", (".csv",), PREVIEW_CSV, AI_TEXT),
    EvidenceFileType("text/plain", "Plain text", (".txt", ".log"), PREVIEW_CODE, AI_TEXT),
    EvidenceFileType("text/markdown", "Markdown", (".md", ".markdown"), PREVIEW_MARKDOWN, AI_TEXT),
    EvidenceFileType("text/yaml", "YAML", (".yml", ".yaml"), PREVIEW_CODE, AI_TEXT),
    EvidenceFileType("application/json", "JSON", (".json",), PREVIEW_CODE, AI_TEXT),
    # XML can carry script (an XHTML-namespaced <script>), so it is never
    # served as XML: see ``inline_safe`` and the download handler.
    EvidenceFileType("application/xml", "XML", (".xml",), PREVIEW_CODE, AI_TEXT, inline_safe=False),
    EvidenceFileType(_DOCX, "Word document", (".docx",), PREVIEW_STRUCTURED, AI_TEXT),
    EvidenceFileType(_XLSX, "Excel workbook", (".xlsx",), PREVIEW_STRUCTURED, AI_TEXT),
    EvidenceFileType(_PPTX, "PowerPoint presentation", (".pptx",), PREVIEW_STRUCTURED, AI_TEXT),
    EvidenceFileType("application/zip", "ZIP archive", (".zip",), PREVIEW_STRUCTURED, AI_ARCHIVE),
    # An email can hold an HTML part; some browsers will draw one.
    EvidenceFileType("message/rfc822", "Email message", (".eml",), PREVIEW_STRUCTURED, AI_TEXT, inline_safe=False),
)

_BY_CONTENT_TYPE = {t.content_type: t for t in EVIDENCE_FILE_TYPES}

#: Every content type an upload may declare.
ALLOWED_CONTENT_TYPES: frozenset = frozenset(_BY_CONTENT_TYPE)

#: Lower-case extension (with the dot) to content type.
EXTENSION_CONTENT_TYPES: dict = {
    ext: t.content_type for t in EVIDENCE_FILE_TYPES for ext in t.extensions
}


def get_type(content_type: Optional[str]) -> Optional[EvidenceFileType]:
    """The definition for a content type, or None when it is not accepted."""
    if not content_type:
        return None
    return _BY_CONTENT_TYPE.get(content_type.split(";")[0].strip().lower())


def content_type_for_filename(filename: Optional[str]) -> Optional[str]:
    """Content type for a file name by its extension, or None when unknown."""
    if not filename or "." not in filename:
        return None
    ext = "." + filename.rsplit(".", 1)[1].lower()
    return EXTENSION_CONTENT_TYPES.get(ext)


def type_for_filename(filename: Optional[str]) -> Optional[EvidenceFileType]:
    """The definition for a file name by its extension, or None when unknown."""
    return get_type(content_type_for_filename(filename))


def refusal_for_filename(filename: Optional[str]) -> Optional[str]:
    """Why a file of this name cannot be uploaded, or None when it can.

    The bytes go straight to storage, so when the record is made the name is
    all the server has, and the stored type is read from it. The integrity
    scan then quarantines a record whose type is not on the list. A name with
    no listed extension (``sshd.conf``, ``photo.jfif``, ``README``) is
    therefore refused here, with a reason, before anything is uploaded: the
    alternative is a file that is accepted and then reported as hostile.
    """
    if content_type_for_filename(filename) is not None:
        return None
    extensions = ", ".join(sorted({ext for kind in EVIDENCE_FILE_TYPES for ext in kind.extensions}))
    return f"The file name must end in an accepted extension. Accepted: {extensions}"


#: Content-type fragments a browser may treat as a document that runs script.
_ACTIVE_CONTENT_MARKERS = ("html", "xml", "svg", "javascript", "ecmascript")


def is_inline_safe(content_type: Optional[str]) -> bool:
    """Whether a download of this type may be sent with its own content type.

    Accepted types answer from their row. A type this module does not know (a
    record written by another path, or before a type was removed) is judged
    by its name, and refused when it looks like something a browser would
    run: this is the last check before bytes leave under our own origin, so
    it does not assume every row was written through the allowlist.
    """
    known = get_type(content_type)
    if known:
        return known.inline_safe
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct.startswith("message/"):
        return False
    return not any(marker in ct for marker in _ACTIVE_CONTENT_MARKERS)


def public_listing() -> dict:
    """What the page needs to build its upload control and choose a preview."""
    return {
        "max_file_size_bytes": MAX_EVIDENCE_FILE_BYTES,
        "types": [
            {
                "content_type": t.content_type,
                "label": t.label,
                "extensions": list(t.extensions),
                "preview": t.preview,
                "ai_readable": True,
            }
            for t in EVIDENCE_FILE_TYPES
        ],
    }
