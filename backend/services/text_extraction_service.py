"""
Text Extraction Service for Evidence AI Assessment.

Extracts readable text content from evidence files stored in S3/Azure Blob.
Routes by content_type to the appropriate extraction method.

Supported formats:
  - PDF (via pymupdf/fitz)
  - DOCX (via python-docx)
  - CSV, JSON, YAML, TXT (direct read)
  - Images (stub — returns unsupported for now, vision phase 2)
"""
import io
import json
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# Maximum text length to return (token budget control)
MAX_TEXT_LENGTH = 50_000  # ~12,500 tokens


@dataclass
class ExtractedContent:
    """Result of text extraction from an evidence file."""
    text: str
    extraction_method: str  # pdf, docx, csv, json, txt, yaml, ocr, unsupported
    page_count: Optional[int] = None
    word_count: int = 0
    truncated: bool = False
    error: Optional[str] = None

    @property
    def is_empty(self) -> bool:
        return not self.text or not self.text.strip()


def extract_text_from_bytes(
    data: bytes,
    content_type: str,
    filename: str = "",
    max_length: Optional[int] = MAX_TEXT_LENGTH,
) -> ExtractedContent:
    """Extract readable text from raw file bytes.

    Routes to the appropriate extractor based on content_type.
    Never raises — returns error details in ExtractedContent.

    ``max_length`` caps the returned text to control LLM token spend on
    assessment paths. Pass ``None`` for ingest paths (CDM/LightRAG) where
    the downstream consumer chunks the text itself and truncation would
    silently drop document content.
    """
    try:
        ct = content_type.lower()

        if "pdf" in ct:
            return _extract_pdf(data, max_length)
        elif "wordprocessingml" in ct or filename.lower().endswith(".docx"):
            return _extract_docx(data, max_length)
        elif "csv" in ct:
            return _extract_text(data, "csv", max_length)
        elif "json" in ct:
            return _extract_json(data, max_length)
        elif "yaml" in ct or "yml" in ct:
            return _extract_text(data, "yaml", max_length)
        elif "text/plain" in ct or filename.lower().endswith(".txt"):
            return _extract_text(data, "txt", max_length)
        elif ct.startswith("image/"):
            # Phase 2: vision model support
            return ExtractedContent(
                text="",
                extraction_method="unsupported",
                error="Image files require vision model support (phase 2)",
            )
        else:
            return ExtractedContent(
                text="",
                extraction_method="unsupported",
                error=f"Unsupported content type for text extraction: {content_type}",
            )

    except Exception as exc:
        logger.error("Text extraction failed for %s (%s): %s", filename, content_type, exc, exc_info=True)
        return ExtractedContent(
            text="",
            extraction_method="error",
            error=f"Extraction failed: {str(exc)[:500]}",
        )


def _truncate(text: str, max_length: Optional[int]) -> tuple[str, bool]:
    """Truncate ``text`` to ``max_length`` chars, return (text, was_truncated).

    ``max_length=None`` disables truncation (CDM ingest path).
    """
    if max_length is None or len(text) <= max_length:
        return text, False
    return text[:max_length] + "\n\n[... truncated ...]", True


def _word_count(text: str) -> int:
    return len(text.split())


# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------

def _extract_pdf(data: bytes, max_length: Optional[int]) -> ExtractedContent:
    """Extract text from PDF using pymupdf (fitz)."""
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
        pages = []
        for page in doc:
            text = page.get_text("text")
            if text.strip():
                pages.append(text)
        doc.close()

        full_text = "\n\n".join(pages)
        if not full_text.strip():
            return ExtractedContent(
                text="",
                extraction_method="pdf",
                page_count=len(doc) if hasattr(doc, '__len__') else 0,
                error="PDF contains no extractable text (may be scanned/image-only)",
            )

        text, truncated = _truncate(full_text, max_length)
        return ExtractedContent(
            text=text,
            extraction_method="pdf",
            page_count=len(pages),
            word_count=_word_count(text),
            truncated=truncated,
        )

    except Exception as exc:
        return ExtractedContent(
            text="",
            extraction_method="pdf",
            error=f"PDF extraction error: {str(exc)[:500]}",
        )


# ---------------------------------------------------------------------------
# DOCX extraction
# ---------------------------------------------------------------------------

def _extract_docx(data: bytes, max_length: Optional[int]) -> ExtractedContent:
    """Extract text from DOCX using python-docx."""
    try:
        from docx import Document
    except ImportError:
        return ExtractedContent(
            text="",
            extraction_method="docx",
            error="python-docx not installed — cannot extract DOCX text",
        )

    try:
        doc = Document(io.BytesIO(data))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
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

    except Exception as exc:
        return ExtractedContent(
            text="",
            extraction_method="docx",
            error=f"DOCX extraction error: {str(exc)[:500]}",
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
    """Extract plain text content (CSV, TXT, YAML)."""
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

def download_evidence_bytes(s3_key: str) -> Optional[bytes]:
    """Download evidence file as bytes from S3/Azure Blob storage.

    Collects streaming chunks into a single bytes object.
    Returns None if file not found or storage not configured.
    """
    from services.storage_service import download_blob_stream, is_configured

    if not is_configured():
        logger.warning("Evidence storage not configured — cannot download file")
        return None

    try:
        chunks = download_blob_stream(s3_key)
        if chunks is None:
            return None
        return b"".join(chunks)
    except (ValueError, Exception) as exc:
        logger.error("Failed to download evidence file %s: %s", s3_key, exc)
        return None
