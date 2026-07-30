"""CDM v2 — pluggable document extraction (epic #709, Part 4).

CDM v1's ingest path routes every binary format to a Docling sidecar defined
only in ``terraform-azure/container-apps-cdm-docling.tf`` — a decommissioned
Azure Container App. ``docker-compose.yml`` does not ship it, so a self-hoster
uploading a PDF fails at extraction, before retrieval is ever reached. That
makes CDM's ingest unusable on the deployment target we actually ship.

This module makes **in-process extraction the default** and demotes Docling to
an opt-in backend, mirroring what the retrieval tier does with LightRAG.

Deliberately thin. ``text_extraction_service`` already extracts PDF (via
PyMuPDF, which is already a dependency), DOCX, CSV, JSON, YAML and plain text,
and it is well covered by existing tests. Writing a second PDF extractor here
would duplicate a tested path and give it somewhere to drift to. What was
missing was never the extraction — it was the routing.

Table-aware conversion genuinely matters for SoA and register documents, so
Docling remains available: this is an explicit trade-off, not a silent
downgrade. It simply stops being a hard dependency of the default path.
"""
from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

BACKEND_INPROCESS = "inprocess"
BACKEND_DOCLING = "docling"


@dataclass(frozen=True)
class ExtractionResult:
    """Text extracted from an uploaded document, plus its provenance.

    ``text_sha256`` is the hash of exactly the text that chunk offsets will
    index. Persisting it is what lets offset resolution later detect that the
    extractor has drifted (a PyMuPDF bump, a settings change) instead of
    citing a span that has silently moved.
    """

    text: str
    word_count: int
    backend: str
    file_source: str
    text_sha256: str

    @staticmethod
    def build(text: str, word_count: int, backend: str, file_source: str) -> "ExtractionResult":
        return ExtractionResult(
            text=text,
            word_count=word_count,
            backend=backend,
            file_source=file_source,
            text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )


def get_extraction_backend() -> str:
    """Resolve the configured extraction backend.

    Defaults to in-process. Selecting ``docling`` without ``CDM_DOCLING_URL``
    falls back rather than failing the ingest: an unreachable optional
    sidecar must not break the path that works without it.
    """
    configured = os.getenv("CDM_EXTRACTION_BACKEND", BACKEND_INPROCESS).strip().lower()
    if configured == BACKEND_DOCLING:
        if not (os.getenv("CDM_DOCLING_URL") or "").strip():
            logger.warning(
                "CDM_EXTRACTION_BACKEND=docling but CDM_DOCLING_URL is unset; "
                "falling back to in-process extraction"
            )
            return BACKEND_INPROCESS
        return BACKEND_DOCLING
    return BACKEND_INPROCESS


def should_use_docling(content_type: str) -> bool:
    """True only when Docling is both selected and applicable to this format.

    Two gates, in this order: the operator must have opted in, and the format
    must be one Docling handles. Reversing the order would import the Docling
    service on every ingest, which pulls its dependency tree for nothing.
    """
    if get_extraction_backend() != BACKEND_DOCLING:
        return False
    from services import cdm_docling_service

    return cdm_docling_service.is_docling_format(content_type)
