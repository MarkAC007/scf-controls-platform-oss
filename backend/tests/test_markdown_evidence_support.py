# SPDX-License-Identifier: AGPL-3.0-only
"""Markdown (.md / .markdown) is an accepted evidence file type.

Covers every gate an upload passes through: the canonical content-type
allowlist, the malware-scan service's own copy of it, the extension map used
when the content type has to be inferred from the S3 key, and the text
extractor that feeds the AI assessment.
"""
import pytest

from services.s3_service import ALLOWED_CONTENT_TYPES
from services.malware_scan_service import (
    ALLOWED_CONTENT_TYPES as SCAN_ALLOWED_CONTENT_TYPES,
    MalwareScanService,
)
from services.text_extraction_service import extract_text_from_bytes

MARKDOWN = b"# Access review\n\n| User | Role |\n|---|---|\n| alice | admin |\n\n- reviewed quarterly\n"


def test_markdown_in_canonical_allowlist():
    assert "text/markdown" in ALLOWED_CONTENT_TYPES


def test_scan_allowlist_does_not_drift_from_canonical():
    # The scan service keeps its own copy of the allowlist; a type accepted
    # at upload-URL time but refused at scan time is a silent upload failure.
    assert ALLOWED_CONTENT_TYPES <= SCAN_ALLOWED_CONTENT_TYPES


@pytest.mark.parametrize("filename", ["runbook.md", "RUNBOOK.MARKDOWN"])
def test_scan_accepts_markdown(filename):
    assert MalwareScanService().validate_file_type(filename, "text/markdown") is None


@pytest.mark.parametrize(
    "filename",
    ["access-review.md", "access-review.markdown", "ACCESS-REVIEW.MD"],
)
def test_extension_maps_to_markdown(filename):
    from api.evidence_files import _guess_content_type

    assert _guess_content_type(filename) == "text/markdown"


def test_extracts_markdown_by_content_type():
    result = extract_text_from_bytes(MARKDOWN, "text/markdown", "review.md")
    assert result.error is None
    assert result.extraction_method == "markdown"
    assert "| alice | admin |" in result.text


def test_extracts_markdown_by_filename_when_type_is_generic():
    result = extract_text_from_bytes(MARKDOWN, "application/octet-stream", "review.md")
    assert result.extraction_method == "markdown"
    assert result.text.startswith("# Access review")
