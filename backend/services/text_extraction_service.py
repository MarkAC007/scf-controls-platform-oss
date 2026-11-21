# SPDX-License-Identifier: AGPL-3.0-only
"""
Text Extraction Service for Evidence AI Assessment.

Extracts readable text content from evidence files stored in S3/Azure Blob.
Routes by content_type to the appropriate extraction method.

Every accepted evidence type (services/evidence_file_types.py) is read here:
  - PDF (via pymupdf/fitz); a PDF with no text layer becomes page pictures
  - Word, Excel, PowerPoint (services/evidence_readers.py)
  - CSV, JSON, YAML, Markdown, XML, plain text (direct read)
  - Email (.eml): headers and body
  - Pictures (PNG, JPEG, GIF, WebP): handed on as pictures for the model
  - ZIP: unpacked in memory, each member read by its own kind

A result can carry text, pictures, or both. Pictures are for an engine that
can look at them; ``visual_only`` tells a text-only engine that the text it
has been given is a description of pictures and not the evidence itself.
"""
import json
import logging
from dataclasses import dataclass, field
from typing import Optional

from services import evidence_file_types, evidence_readers
from services.evidence_readers import EvidenceReadError, ImagePart

logger = logging.getLogger(__name__)

# Maximum text length to return (token budget control)
MAX_TEXT_LENGTH = 50_000  # ~12,500 tokens

#: Most pictures one evidence file may contribute to a model call. Each costs
#: up to about 5,000 input tokens at the size they are sent.
MAX_IMAGES_PER_FILE = 10
#: Pages of a scanned PDF that are turned into pictures. Same number: a
#: scanned page is one picture.
MAX_SCANNED_PDF_PAGES = MAX_IMAGES_PER_FILE
#: Files of a ZIP archive named in the listing the model is given.
ZIP_MAX_LISTED = 100
#: Long edge, in pixels, a scanned page is drawn at. About 190 dpi for A4.
SCANNED_PDF_PAGE_EDGE = 1600
#: A page of a PDF is treated as a scan when it has less text than this and
#: pictures covering at least this share of it. That is a scanned page with a
#: stamp on it (a header, a page number, a reference mark), or a signed page
#: scanned into an otherwise typed document. A cover page with a logo is not.
SCANNED_PAGE_MAX_TEXT_CHARS = 100
SCANNED_PAGE_MIN_COVER = 0.5


@dataclass
class ExtractedContent:
    """Result of text extraction from an evidence file."""
    text: str
    # pdf, pdf_scanned, docx, xlsx, pptx, csv, json, txt, yaml, markdown, xml,
    # eml, image, zip, unsupported, error
    extraction_method: str
    page_count: Optional[int] = None
    word_count: int = 0
    truncated: bool = False
    error: Optional[str] = None
    #: Pictures to show a model that can look at them, in reading order.
    images: list = field(default_factory=list)
    #: True when the evidence is only in ``images`` and ``text`` merely
    #: describes them. A text-only engine has nothing to judge in such a file.
    visual_only: bool = False
    #: What was left out, in a sentence, when ``truncated`` is not about the
    #: character budget (pages of a scan, files in an archive).
    truncation_note: Optional[str] = None
    #: Pictures this file would have contributed that were not prepared because
    #: the caller asked for fewer (or for none).
    pictures_left_out: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.images and (not self.text or not self.text.strip())


def extract_text_from_bytes(
    data: bytes,
    content_type: str,
    filename: str = "",
    max_length: Optional[int] = MAX_TEXT_LENGTH,
    with_images: bool = True,
) -> ExtractedContent:
    """Extract readable text from raw file bytes.

    Routes to the appropriate extractor based on content_type.
    Never raises — returns error details in ExtractedContent.

    ``max_length`` caps the returned text to control LLM token spend on
    assessment paths. Pass ``None`` when the caller chunks the text itself
    and truncation would silently drop document content.

    ``with_images=False`` is for a caller that only uses the text. Pictures
    and scanned pages are then described and flagged (``visual_only``) but not
    decoded, drawn or re-encoded, which is most of the cost of reading them.
    """
    try:
        return _extract(
            data, content_type, filename, max_length, inside_archive=False,
            image_budget=MAX_IMAGES_PER_FILE if with_images else 0,
        )

    except Exception as exc:
        logger.error("Text extraction failed for %s (%s): %s", filename, content_type, exc, exc_info=True)
        return ExtractedContent(
            text="",
            extraction_method="error",
            error=f"Extraction failed: {str(exc)[:500]}",
        )


def _extract(
    data: bytes,
    content_type: str,
    filename: str,
    max_length: Optional[int],
    inside_archive: bool,
    image_budget: int = MAX_IMAGES_PER_FILE,
) -> ExtractedContent:
    """Route one file to its reader.

    ``inside_archive`` is true for a file being read out of a ZIP. An archive
    found there is not opened: see ``evidence_readers.open_evidence_zip``.
    ``image_budget`` is the most pictures the file may prepare; 0 for none.
    """
    ct = (content_type or "").split(";")[0].strip().lower()
    name = (filename or "").lower()

    if "pdf" in ct:
        return _extract_pdf(data, max_length, image_budget)
    elif "wordprocessingml" in ct or name.endswith(".docx"):
        return _extract_docx(data, max_length)
    elif "spreadsheetml" in ct or name.endswith(".xlsx"):
        return _extract_xlsx(data, max_length)
    elif "presentationml" in ct or name.endswith(".pptx"):
        return _extract_pptx(data, max_length)
    elif "csv" in ct:
        return _extract_text(data, "csv", max_length)
    elif "json" in ct:
        return _extract_json(data, max_length)
    elif "yaml" in ct or "yml" in ct:
        return _extract_text(data, "yaml", max_length)
    elif "markdown" in ct or name.endswith((".md", ".markdown")):
        return _extract_text(data, "markdown", max_length)
    elif ct in ("application/xml", "text/xml") or name.endswith(".xml"):
        # Read as text, never parsed: the model is shown what the file says,
        # and nothing an entity or a stylesheet reference could pull in.
        return _extract_text(data, "xml", max_length)
    elif ct == "message/rfc822" or name.endswith(".eml"):
        return _extract_eml(data, max_length)
    elif "text/plain" in ct or name.endswith((".txt", ".log")):
        return _extract_text(data, "txt", max_length)
    elif ct.startswith("image/"):
        return _extract_image(data, ct, filename, image_budget)
    elif ct in ("application/zip", "application/x-zip-compressed") or name.endswith(".zip"):
        if inside_archive:
            return ExtractedContent(
                text="", extraction_method="unsupported",
                error="An archive inside an archive is not opened",
            )
        return _extract_zip(data, max_length, image_budget)
    else:
        return ExtractedContent(
            text="",
            extraction_method="unsupported",
            error=f"Unsupported content type for text extraction: {content_type}",
        )


def _truncate(text: str, max_length: Optional[int]) -> tuple[str, bool]:
    """Truncate ``text`` to ``max_length`` chars, return (text, was_truncated).

    ``max_length=None`` disables truncation.
    """
    if max_length is None or len(text) <= max_length:
        return text, False
    return text[:max_length] + "\n\n[... truncated ...]", True


def _word_count(text: str) -> int:
    return len(text.split())


# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------

def _looks_scanned(page, text: str) -> bool:
    """A page that is a picture of a page: next to no text, mostly image."""
    if len(text) >= SCANNED_PAGE_MAX_TEXT_CHARS:
        return False
    area = abs(page.rect.width * page.rect.height) or 1.0
    covered = 0.0
    for info in page.get_image_info():
        x0, y0, x1, y1 = info.get("bbox") or (0, 0, 0, 0)
        covered += abs((x1 - x0) * (y1 - y0))
    return covered >= SCANNED_PAGE_MIN_COVER * area


def _page_numbers(indexes: list) -> str:
    numbers = [str(index + 1) for index in indexes]
    if len(numbers) == 1:
        return numbers[0]
    if len(numbers) > 8:
        return ", ".join(numbers[:8]) + " and others"
    return ", ".join(numbers[:-1]) + " and " + numbers[-1]


def _draw_pages(doc, indexes) -> list:
    """Draw pages of a PDF as pictures for the model."""
    import fitz  # pymupdf

    total = doc.page_count
    images = []
    for index in indexes:
        page = doc.load_page(index)
        longest = max(page.rect.width, page.rect.height, 1.0)
        zoom = min(SCANNED_PDF_PAGE_EDGE / longest, 4.0)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        try:
            images.append(
                evidence_readers.prepare_image(pixmap.tobytes("png"), f"Page {index + 1} of {total}")
            )
        except EvidenceReadError as exc:
            logger.warning("Scanned PDF page %d could not be prepared: %s", index + 1, exc)
    return images


def _extract_pdf(
    data: bytes, max_length: Optional[int], image_budget: int = MAX_IMAGES_PER_FILE,
) -> ExtractedContent:
    """Extract text from PDF using pymupdf (fitz).

    A PDF with no text at all is a scan and goes on as page pictures. A PDF
    with text may still hold scanned pages (a stamped scan, a signed page):
    those pages go on as pictures beside the text.
    """
    try:
        import fitz  # pymupdf
    except ImportError:
        return ExtractedContent(
            text="",
            extraction_method="pdf",
            error="pymupdf not installed — cannot extract PDF text",
        )

    try:
        doc = fitz.open(stream=data, filetype="pdf")
        try:
            pages = []
            scanned_pages = []
            for index, page in enumerate(doc):
                text = page.get_text("text")
                if text.strip():
                    pages.append(text)
                if _looks_scanned(page, text.strip()):
                    scanned_pages.append(index)

            full_text = "\n\n".join(pages)
            if not full_text.strip():
                # No text layer: a scan, or a document saved as pictures. The
                # pages themselves are the evidence, so they go on as pictures.
                return _scanned_pdf(doc, image_budget)
            wanted = scanned_pages[:MAX_SCANNED_PDF_PAGES]
            images = _draw_pages(doc, wanted[:max(0, image_budget)])
        finally:
            doc.close()

        text, truncated = _truncate(full_text, max_length)
        note = None
        if scanned_pages:
            # After the cut, so the model is always told the pictures are there.
            shown = len(images)
            if image_budget <= 0:
                ending = "Not shown here."
            elif shown == 0:
                ending = "They could not be drawn as pictures."
            elif shown == len(scanned_pages):
                ending = "Attached as pictures."
            else:
                ending = f"The first {shown} are attached as pictures."
            text += (
                f"\n\n[Scanned pages with little or no text: {_page_numbers(scanned_pages)}. {ending}]"
            )
            if image_budget > 0 and shown < len(scanned_pages):
                truncated = True
                note = (
                    f"Only {shown} of the {len(scanned_pages)} scanned pages of this PDF "
                    "were assessed as pictures."
                )
        return ExtractedContent(
            text=text,
            extraction_method="pdf",
            page_count=len(pages),
            word_count=_word_count(text),
            truncated=truncated,
            images=images,
            truncation_note=note,
            pictures_left_out=len(wanted) - min(len(wanted), max(0, image_budget)),
        )

    except Exception as exc:
        return ExtractedContent(
            text="",
            extraction_method="pdf",
            error=f"PDF extraction error: {str(exc)[:500]}",
        )


def _scanned_pdf(doc, image_budget: int = MAX_IMAGES_PER_FILE) -> ExtractedContent:
    """Turn the first pages of a PDF with no text layer into pictures."""
    total = doc.page_count
    if total == 0:
        return ExtractedContent(
            text="", extraction_method="pdf", page_count=0,
            error="PDF contains no pages",
        )
    wanted = min(total, MAX_SCANNED_PDF_PAGES)
    if image_budget <= 0:
        # Described, not drawn: the caller reads text only.
        return ExtractedContent(
            text=f"[Scanned PDF: {total} page{'s' if total != 1 else ''}, no text layer.]",
            extraction_method="pdf_scanned",
            page_count=total,
            visual_only=True,
            pictures_left_out=wanted,
        )
    images = _draw_pages(doc, range(min(wanted, image_budget)))
    if not images:
        return ExtractedContent(
            text="", extraction_method="pdf", page_count=total,
            error="PDF contains no extractable text and its pages could not be drawn as pictures",
        )
    shown = len(images)
    text = (
        f"[Scanned PDF: {total} page{'s' if total != 1 else ''}, no text layer. "
        f"{'Page 1 is' if shown == 1 else f'Pages 1 to {shown} are'} attached as pictures.]"
    )
    truncated = total > shown
    return ExtractedContent(
        text=text,
        extraction_method="pdf_scanned",
        page_count=total,
        word_count=0,
        truncated=truncated,
        images=images,
        visual_only=True,
        truncation_note=(
            f"Only the first {shown} of {total} pages of this scanned PDF were assessed."
            if truncated else None
        ),
        pictures_left_out=wanted - min(wanted, image_budget),
    )


# ---------------------------------------------------------------------------
# Pictures
# ---------------------------------------------------------------------------

def _extract_image(
    data: bytes, content_type: str, filename: str, image_budget: int = MAX_IMAGES_PER_FILE,
) -> ExtractedContent:
    """Hand a picture on for a model that can look at it."""
    kind = evidence_file_types.get_type(content_type)
    label = kind.label if kind else "Picture"
    if image_budget <= 0:
        # Described from its header, not decoded: the caller reads text only.
        try:
            width, height = evidence_readers.image_size(data)
        except EvidenceReadError as exc:
            return ExtractedContent(text="", extraction_method="image", error=str(exc))
        return ExtractedContent(
            text=f"[Picture: {label}, {width} x {height} pixels]",
            extraction_method="image",
            visual_only=True,
            pictures_left_out=1,
        )
    try:
        # The label travels to the model beside the picture, outside the
        # evidence block, so it is the type's own name and never the file's.
        image = evidence_readers.prepare_image(data, label)
    except EvidenceReadError as exc:
        return ExtractedContent(text="", extraction_method="image", error=str(exc))
    return ExtractedContent(
        text=f"[Picture: {label}, {image.width} x {image.height} pixels]",
        extraction_method="image",
        images=[image],
        visual_only=True,
    )


# ---------------------------------------------------------------------------
# DOCX extraction
# ---------------------------------------------------------------------------

def _extract_docx(data: bytes, max_length: Optional[int]) -> ExtractedContent:
    """Extract text from DOCX using python-docx."""
    try:
        paragraphs = evidence_readers.read_docx_paragraphs(data)
    except ImportError:
        return ExtractedContent(
            text="",
            extraction_method="docx",
            error="python-docx not installed — cannot extract DOCX text",
        )
    except EvidenceReadError as exc:
        return ExtractedContent(text="", extraction_method="docx", error=str(exc))

    full_text = "\n".join(paragraphs)
    if not full_text.strip():
        return ExtractedContent(
            text="",
            extraction_method="docx",
            error="DOCX contains no extractable text",
        )

    text, truncated = _truncate(full_text, max_length)
    return ExtractedContent(
        text=text,
        extraction_method="docx",
        word_count=_word_count(text),
        truncated=truncated,
    )


# ---------------------------------------------------------------------------
# Excel extraction
# ---------------------------------------------------------------------------

def _extract_xlsx(data: bytes, max_length: Optional[int]) -> ExtractedContent:
    """Extract the cell values of every sheet of an Excel workbook.

    Each sheet is introduced by its name and written one row per line with
    tabs between cells, which is how a model reads a table most reliably.
    """
    try:
        sheets = evidence_readers.read_xlsx_sheets(data, max_chars=max_length)
    except ImportError:
        return ExtractedContent(
            text="", extraction_method="xlsx",
            error="openpyxl not installed — cannot extract Excel text",
        )
    except EvidenceReadError as exc:
        return ExtractedContent(text="", extraction_method="xlsx", error=str(exc))

    blocks = []
    for sheet in sheets:
        if not sheet.rows:
            continue
        lines = [f"## Sheet: {sheet.name}"] + ["\t".join(row) for row in sheet.rows]
        blocks.append("\n".join(lines))
    full_text = "\n\n".join(blocks)
    if not full_text.strip():
        return ExtractedContent(
            text="", extraction_method="xlsx",
            error="Excel workbook contains no cell values",
        )

    text, truncated = _truncate(full_text, max_length)
    return ExtractedContent(
        text=text,
        extraction_method="xlsx",
        page_count=len(sheets),
        word_count=_word_count(text),
        truncated=truncated or any(sheet.truncated for sheet in sheets),
    )


# ---------------------------------------------------------------------------
# PowerPoint extraction
# ---------------------------------------------------------------------------

def _extract_pptx(data: bytes, max_length: Optional[int]) -> ExtractedContent:
    """Extract the text of each slide of a PowerPoint presentation."""
    try:
        slides = evidence_readers.read_pptx_slides(data)
    except EvidenceReadError as exc:
        return ExtractedContent(text="", extraction_method="pptx", error=str(exc))

    blocks = [
        "\n".join([f"## Slide {slide.number}"] + slide.paragraphs)
        for slide in slides if slide.paragraphs
    ]
    full_text = "\n\n".join(blocks)
    if not full_text.strip():
        return ExtractedContent(
            text="", extraction_method="pptx", page_count=len(slides),
            error="PowerPoint presentation contains no text (its slides may be pictures)",
        )
    text, truncated = _truncate(full_text, max_length)
    return ExtractedContent(
        text=text,
        extraction_method="pptx",
        page_count=len(slides),
        word_count=_word_count(text),
        truncated=truncated,
    )


# ---------------------------------------------------------------------------
# Email extraction
# ---------------------------------------------------------------------------

def _extract_eml(data: bytes, max_length: Optional[int]) -> ExtractedContent:
    """Extract the headers and body of an email message."""
    try:
        message = evidence_readers.read_eml(data)
    except EvidenceReadError as exc:
        return ExtractedContent(text="", extraction_method="eml", error=str(exc))

    lines = [
        f"{label}: {message.headers[key]}"
        for key, label in (("from", "From"), ("to", "To"), ("cc", "Cc"),
                           ("subject", "Subject"), ("date", "Date"))
        if message.headers.get(key)
    ]
    parts = ["\n".join(lines)] if lines else []
    if message.body:
        parts.append(message.body)
    if message.attachments:
        listed = "\n".join(
            f"- {a['name']} ({a['content_type']}, {a['size_bytes']:,} bytes)"
            for a in message.attachments
        )
        parts.append(f"Attachments (listed, not read):\n{listed}")
    text, truncated = _truncate("\n\n".join(parts), max_length)
    return ExtractedContent(
        text=text,
        extraction_method="eml",
        word_count=_word_count(text),
        truncated=truncated,
    )


# ---------------------------------------------------------------------------
# ZIP extraction
# ---------------------------------------------------------------------------

def _extract_zip(
    data: bytes, max_length: Optional[int], image_budget: int = MAX_IMAGES_PER_FILE,
) -> ExtractedContent:
    """Read a ZIP archive: a list of what it holds, then each readable file.

    Unpacked in memory, one member at a time, within the limits in
    ``evidence_readers`` (member count, total and per-member unpacked size).
    Members share the text budget evenly so one large log cannot push every
    other file out of what the model sees. Pictures are prepared only while
    there is room to send them: a member past the picture limit is listed and
    not decoded.
    """
    text_only = image_budget <= 0
    try:
        zf, members = evidence_readers.open_evidence_zip(data)
    except EvidenceReadError as exc:
        return ExtractedContent(text="", extraction_method="zip", error=str(exc))

    try:
        if not members:
            return ExtractedContent(
                text="", extraction_method="zip", error="ZIP archive contains no files",
            )
        readable = [m for m in members if m.readable]
        share = None
        if max_length is not None:
            share = max(2_000, max_length // max(1, len(readable)))

        listing = ["ZIP archive contents:"]
        sections = []
        images = []
        left_out = []
        used = 0
        text_members = 0
        picture_members = 0
        pictures_left_out = 0
        for member in members:
            if not member.readable:
                listing.append(f"- {member.name} ({member.size_bytes:,} bytes): {member.note}")
                continue
            if max_length is not None and used >= max_length:
                listing.append(f"- {member.name} ({member.size_bytes:,} bytes): not read, text budget spent")
                left_out.append(member.name)
                continue
            try:
                blob = evidence_readers.read_zip_member(zf, member)
            except EvidenceReadError as exc:
                listing.append(f"- {member.name} ({member.size_bytes:,} bytes): {exc}")
                continue
            inner = _extract(
                blob, member.content_type, member.name, share, inside_archive=True,
                # Never more than there is room left to send.
                image_budget=max(0, image_budget - len(images)),
            )
            if inner.is_empty:
                listing.append(
                    f"- {member.name} ({member.size_bytes:,} bytes): "
                    f"{inner.error or 'no readable content'}"
                )
                continue
            # A member's name is chosen by whoever built the archive. It is
            # given to the model in the listing, inside the evidence block;
            # the label beside each picture carries only its number there.
            first_picture = len(images) + 1
            for image in inner.images:
                from_pdf = inner.extraction_method.startswith("pdf")
                image.label = (
                    f"{image.label} of a PDF in the ZIP archive" if from_pdf
                    else "a picture in the ZIP archive"
                )
                images.append(image)
            attached = ""
            if inner.images:
                attached = (
                    f", attached as picture {first_picture}" if len(inner.images) == 1
                    else f", attached as pictures {first_picture} to {len(images)}"
                )
            pictures_left_out += inner.pictures_left_out
            if inner.visual_only:
                picture_members += 1
            else:
                text_members += 1
            if inner.visual_only and not inner.images:
                # A picture that is not being sent has nothing else to add.
                if text_only:
                    listing.append(
                        f"- {member.name} ({member.size_bytes:,} bytes): {member.label}, "
                        "a picture or a scan, not shown here"
                    )
                else:
                    listing.append(
                        f"- {member.name} ({member.size_bytes:,} bytes): not shown, "
                        f"picture limit of {MAX_IMAGES_PER_FILE} reached"
                    )
                    left_out.append(member.name)
                continue
            listing.append(f"- {member.name} ({member.size_bytes:,} bytes): {member.label}{attached}")
            if inner.truncated or (inner.pictures_left_out and not text_only):
                left_out.append(member.name)
            sections.append(f"===== File: {member.name} =====\n{inner.text}")
            used += len(inner.text)
    finally:
        zf.close()

    if not sections and not picture_members:
        return ExtractedContent(
            text="", extraction_method="zip",
            error="ZIP archive contains no files the platform can read",
        )

    if len(listing) - 1 > ZIP_MAX_LISTED:
        # The listing sits outside the text budget; a few hundred long names
        # would otherwise cost more than the files they name.
        more = len(listing) - 1 - ZIP_MAX_LISTED
        listing = listing[: ZIP_MAX_LISTED + 1] + [f"- and {more:,} more files, not listed here"]
    full_text = "\n".join(listing)
    if sections:
        full_text += "\n\n" + "\n\n".join(sections)
    text, cut = _truncate(full_text, None if max_length is None else max_length + len("\n".join(listing)) + 2)
    truncated = cut or bool(left_out)
    note = None
    if left_out:
        shown = ", ".join(left_out[:5]) + (" and others" if len(left_out) > 5 else "")
        note = f"Some files in this ZIP archive were read only in part or not at all: {shown}."
    return ExtractedContent(
        text=text,
        extraction_method="zip",
        page_count=len(members),
        word_count=_word_count(text),
        truncated=truncated,
        images=images,
        # Each member says for itself whether it is only a picture.
        visual_only=picture_members > 0 and text_members == 0,
        truncation_note=note,
        pictures_left_out=pictures_left_out,
    )


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------

def _extract_json(data: bytes, max_length: Optional[int]) -> ExtractedContent:
    """Extract formatted text from JSON data."""
    try:
        parsed = json.loads(data.decode("utf-8"))
        formatted = json.dumps(parsed, indent=2, default=str)
        text, truncated = _truncate(formatted, max_length)

        return ExtractedContent(
            text=text,
            extraction_method="json",
            word_count=_word_count(text),
            truncated=truncated,
        )

    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return ExtractedContent(
            text="",
            extraction_method="json",
            error=f"JSON parse error: {str(exc)[:500]}",
        )


# ---------------------------------------------------------------------------
# Plain text extraction (CSV, TXT, YAML)
# ---------------------------------------------------------------------------

def _extract_text(data: bytes, method: str, max_length: Optional[int]) -> ExtractedContent:
    """Extract plain text content (CSV, TXT, YAML, Markdown)."""
    try:
        # Try UTF-8 first, fall back to latin-1
        try:
            raw_text = data.decode("utf-8")
        except UnicodeDecodeError:
            raw_text = data.decode("latin-1")

        text, truncated = _truncate(raw_text, max_length)

        return ExtractedContent(
            text=text,
            extraction_method=method,
            word_count=_word_count(text),
            truncated=truncated,
        )

    except Exception as exc:
        return ExtractedContent(
            text="",
            extraction_method=method,
            error=f"Text extraction error: {str(exc)[:500]}",
        )


# ---------------------------------------------------------------------------
# Helper: download file bytes from storage
# ---------------------------------------------------------------------------

def download_evidence_bytes(
    s3_key: str,
    org_id: Optional[str] = None,
    storage_config_id: Optional[str] = None,
) -> Optional[bytes]:
    """Download evidence file as bytes from S3/Azure Blob storage.

    Collects streaming chunks into a single bytes object.
    Returns None if file not found or storage not configured.

    ``org_id`` and ``storage_config_id`` say which store the object is in.
    Both are optional so existing callers keep the platform-store behaviour,
    but every caller reading an ``EvidenceFile`` should pass them: after an
    organisation switches store, a read that resolves by organisation looks in
    the new store for bytes that are still in the old one.
    """
    from services.storage_service import download_blob_stream, is_configured

    if not is_configured():
        logger.warning("Evidence storage not configured — cannot download file")
        return None

    try:
        chunks = download_blob_stream(
            s3_key, org_id=org_id, storage_config_id=storage_config_id
        )
        if chunks is None:
            return None
        return b"".join(chunks)
    except (ValueError, Exception) as exc:
        logger.error("Failed to download evidence file %s: %s", s3_key, exc)
        return None
