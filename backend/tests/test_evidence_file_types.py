# SPDX-License-Identifier: AGPL-3.0-only
"""One list of accepted evidence file types (issue #1309, item 6).

The storage allowlist, the malware scan's allowlist, the extension map used
when a record is confirmed and the endpoint the page reads are all the same
definition. These tests fail when any of them stops being so.
"""
import os
import sys
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import evidence_file_types as types  # noqa: E402
from services.malware_scan_service import ALLOWED_CONTENT_TYPES as SCAN_ALLOWED  # noqa: E402
from services.storage_service import ALLOWED_CONTENT_TYPES as STORAGE_ALLOWED  # noqa: E402

from evidence_samples import SAMPLES  # noqa: E402

PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


# ---------------------------------------------------------------------------
# The lists agree
# ---------------------------------------------------------------------------

def test_storage_and_scan_lists_are_the_single_definition():
    assert set(STORAGE_ALLOWED) == set(types.ALLOWED_CONTENT_TYPES)
    assert set(SCAN_ALLOWED) == set(types.ALLOWED_CONTENT_TYPES)


def test_no_module_keeps_its_own_copy_of_the_list():
    """The drift test: a hand-written allowlist anywhere else fails here.

    Reads the source of every module that used to hold a copy and requires it
    to take the list from the definition rather than spell out content types.
    """
    backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for relative in ("services/s3_service.py", "services/malware_scan_service.py"):
        source = open(os.path.join(backend, relative)).read()
        assert "ALLOWED_CONTENT_TYPES = set(evidence_file_types.ALLOWED_CONTENT_TYPES)" in source, relative
        assert '"application/pdf"' not in source, f"{relative} spells out a content type again"
    api_source = open(os.path.join(backend, "api/evidence_files.py")).read()
    assert '".pdf"' not in api_source, "api/evidence_files.py keeps its own extension map again"


def test_extension_map_is_the_single_definition():
    from api.evidence_files import _EXTENSION_CONTENT_TYPES

    assert _EXTENSION_CONTENT_TYPES == types.EXTENSION_CONTENT_TYPES
    for kind in types.EVIDENCE_FILE_TYPES:
        assert kind.extensions, kind.content_type
        for ext in kind.extensions:
            assert ext == ext.lower() and ext.startswith(".")
            assert types.EXTENSION_CONTENT_TYPES[ext] == kind.content_type


def test_every_accepted_type_has_a_sample_and_every_sample_is_accepted():
    """The oracle for the tests that walk the list: nothing is skipped quietly."""
    assert set(SAMPLES) == set(types.ALLOWED_CONTENT_TYPES)


# ---------------------------------------------------------------------------
# What is accepted, and what is not
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("content_type", [
    "application/xml", "image/webp", PPTX, "message/rfc822", "text/plain", "application/zip",
])
def test_new_types_are_accepted_by_storage_and_scan(content_type):
    assert content_type in STORAGE_ALLOWED
    assert content_type in SCAN_ALLOWED


@pytest.mark.parametrize("content_type", [
    "text/html", "image/svg+xml", "application/xhtml+xml", "application/javascript",
    "text/xml", "image/heic", "application/octet-stream",
])
def test_script_capable_and_unknown_types_are_refused(content_type):
    assert content_type not in STORAGE_ALLOWED
    assert content_type not in SCAN_ALLOWED
    assert types.get_type(content_type) is None


@pytest.mark.parametrize("filename", ["page.html", "page.htm", "logo.svg", "run.js", "noext"])
def test_html_and_svg_names_are_not_given_an_accepted_type(filename):
    from api.evidence_files import _guess_content_type

    assert _guess_content_type(filename) == "application/octet-stream"


@pytest.mark.parametrize("content_type", ["text/html", "image/svg+xml"])
def test_presigned_upload_refuses_html_and_svg_and_names_the_accepted_types(monkeypatch, content_type):
    from services import s3_service

    monkeypatch.setattr(s3_service, "_cfg", lambda config=None: MagicMock())
    monkeypatch.setattr(s3_service, "_require_bucket", lambda cfg: "test-bucket")
    with pytest.raises(ValueError) as refused:
        s3_service.generate_upload_presigned_post("org", "page.html", content_type)
    message = str(refused.value)
    assert "not allowed" in message
    assert "Allowed types:" in message
    assert "application/pdf" in message


def test_upload_request_schema_takes_the_longest_content_type():
    from schemas import EvidenceFileUploadUrlRequest

    request = EvidenceFileUploadUrlRequest(filename="deck.pptx", content_type=PPTX, file_size_bytes=10)
    assert request.content_type == PPTX


# ---------------------------------------------------------------------------
# The stored content type comes from the file name, through the definition
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("filename,expected", [
    ("server.log", "text/plain"),
    ("NOTES.TXT", "text/plain"),
    ("report.xml", "application/xml"),
    ("screen.webp", "image/webp"),
    ("deck.pptx", PPTX),
    ("signoff.eml", "message/rfc822"),
    ("archive.tar.zip", "application/zip"),
    ("photo.JPEG", "image/jpeg"),
])
def test_stored_content_type_for_a_name(filename, expected):
    from api.evidence_files import _guess_content_type

    assert _guess_content_type(filename) == expected


def test_sample_file_names_resolve_to_their_own_type():
    for content_type, (filename, _build, _marker) in SAMPLES.items():
        assert types.content_type_for_filename(filename) == content_type


# ---------------------------------------------------------------------------
# The endpoint the page builds its upload control from
# ---------------------------------------------------------------------------

def test_listing_describes_every_accepted_type():
    listing = types.public_listing()
    assert listing["max_file_size_bytes"] == 50 * 1024 * 1024
    assert {row["content_type"] for row in listing["types"]} == set(STORAGE_ALLOWED)
    for row in listing["types"]:
        assert row["label"] and row["extensions"]
        assert row["preview"] in {"pdf", "image", "markdown", "code", "csv", "structured"}
        assert row["ai_readable"] is True
    labels = [row["label"] for row in listing["types"]]
    assert len(labels) == len(set(labels)), "two types share a label: a refusal message would repeat it"


def test_listing_size_limit_matches_the_upload_schema():
    from pydantic import ValidationError
    from schemas import EvidenceFileUploadUrlRequest

    limit = types.MAX_EVIDENCE_FILE_BYTES
    EvidenceFileUploadUrlRequest(filename="a.pdf", content_type="application/pdf", file_size_bytes=limit)
    with pytest.raises(ValidationError):
        EvidenceFileUploadUrlRequest(filename="a.pdf", content_type="application/pdf", file_size_bytes=limit + 1)


def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.evidence_files import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    return app, TestClient(app, raise_server_exceptions=False)


def test_endpoint_returns_the_listing_to_a_signed_in_user():
    from auth import require_auth

    app, client = _client()
    app.dependency_overrides[require_auth] = lambda: MagicMock()
    response = client.get("/api/evidence-file-types")
    assert response.status_code == 200
    assert response.json() == types.public_listing()


def test_endpoint_refuses_a_caller_who_is_not_signed_in():
    _app, client = _client()
    response = client.get("/api/evidence-file-types")
    assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Downloads: nothing a browser would run leaves under our origin as itself
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("content_type", ["application/xml", "message/rfc822"])
def test_script_capable_accepted_types_are_not_inline_safe(content_type):
    assert types.is_inline_safe(content_type) is False


@pytest.mark.parametrize("content_type", [
    "text/html", "image/svg+xml", "application/xhtml+xml", "text/xml",
    "TEXT/HTML; charset=utf-8", "application/javascript", "message/partial",
])
def test_a_stored_type_that_was_never_accepted_is_still_not_served_as_itself(content_type):
    """A row written by another path, or before a type was withdrawn."""
    assert types.is_inline_safe(content_type) is False


@pytest.mark.parametrize("content_type", [
    "application/pdf", "image/png", "text/plain", "text/csv", "application/json",
    "application/zip", "application/octet-stream",
])
def test_ordinary_types_keep_their_own_content_type(content_type):
    from api.evidence_files import _served_as

    assert types.is_inline_safe(content_type) is True
    assert _served_as(content_type, "inline") == (content_type, "inline")
    assert _served_as(content_type, "attachment") == (content_type, "attachment")


@pytest.mark.parametrize("disposition", ["inline", "attachment"])
def test_xml_is_served_as_a_plain_text_attachment_whatever_was_asked(disposition):
    from api.evidence_files import _served_as

    assert _served_as("application/xml", disposition) == ("text/plain; charset=utf-8", "attachment")


class TestDownloadResponseHeaders:
    @staticmethod
    def _file(org_id, filename, content_type):
        row = MagicMock()
        row.id = uuid4()
        row.organization_id = org_id
        row.s3_key = f"evidence/{org_id}/2026/11/abc_{filename}"
        row.filename = filename
        row.content_type = content_type
        row.scan_status = "clean"
        row.storage_config_id = None
        return row

    @staticmethod
    async def _download(monkeypatch, filename, content_type, disposition="inline"):
        from unittest.mock import AsyncMock

        from api.evidence_files import download_evidence_file
        from services.download_token import generate_download_token

        monkeypatch.setenv("DOWNLOAD_TOKEN_SECRET", "test-signing-secret")
        org_id, file_id, user_id = uuid4(), uuid4(), uuid4()
        token, expires = generate_download_token(str(file_id), str(org_id), str(user_id))
        db = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = TestDownloadResponseHeaders._file(org_id, filename, content_type)
        db.execute.return_value = result
        request = MagicMock()
        request.headers = {}
        request.client = None
        with patch("api.evidence_files.create_audit_entry", new_callable=AsyncMock), \
                patch("api.evidence_files._auditor_link_still_valid", new_callable=AsyncMock, return_value=True), \
                patch("api.evidence_files.download_blob_stream", return_value=iter([b"<x/>"])):
            return await download_evidence_file(
                org_id=org_id, evidence_id="ERL-001", file_id=file_id, request=request,
                disposition=disposition, token=token, expires=expires, db=db,
            )

    @pytest.mark.asyncio
    async def test_xml_download_is_plain_text_attachment_with_nosniff(self, monkeypatch):
        response = await self._download(monkeypatch, "report.xml", "application/xml")
        assert response.media_type == "text/plain; charset=utf-8"
        assert response.headers["content-disposition"].startswith("attachment;")
        assert response.headers["x-content-type-options"] == "nosniff"

    @pytest.mark.asyncio
    async def test_pdf_download_is_unchanged_but_carries_nosniff(self, monkeypatch):
        response = await self._download(monkeypatch, "report.pdf", "application/pdf")
        assert response.media_type == "application/pdf"
        assert response.headers["content-disposition"].startswith("inline;")
        assert response.headers["x-content-type-options"] == "nosniff"

    @pytest.mark.asyncio
    async def test_text_download_carries_nosniff(self, monkeypatch):
        response = await self._download(monkeypatch, "server.log", "text/plain", "attachment")
        assert response.media_type == "text/plain"
        assert response.headers["x-content-type-options"] == "nosniff"


# ---------------------------------------------------------------------------
# A name with no listed extension is refused before the upload, not after it
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("filename", [
    "report.pdf", "NOTES.TXT", "server.log", "archive.tar.zip", ".pdf", "deck.pptx",
])
def test_names_with_a_listed_extension_are_not_refused(filename):
    assert types.refusal_for_filename(filename) is None


@pytest.mark.parametrize("filename", [
    "sshd.conf", "photo.jfif", "README", "notes.", "page.html", "logo.svg", "report.pdf.exe", "",
])
def test_names_without_a_listed_extension_are_refused_with_the_list(filename):
    refusal = types.refusal_for_filename(filename)
    assert refusal.startswith("The file name must end in an accepted extension.")
    for extension in (".pdf", ".log", ".zip", ".eml"):
        assert extension in refusal
    assert ".html" not in refusal and ".svg" not in refusal


def test_a_name_that_passes_is_stored_under_a_listed_type_after_the_key_is_built():
    """What upload-url lets through, confirm must not turn into an unknown type."""
    from api.evidence_files import _guess_content_type
    from services.s3_service import _generate_object_key

    for name in ("Q3 access review (final).PDF", "sshd config.log", "a b_c.d.yaml", ".pdf"):
        assert types.refusal_for_filename(name) is None
        key_name = _generate_object_key("org", name).rsplit("/", 1)[-1].split("_", 1)[1]
        assert _guess_content_type(key_name) in types.ALLOWED_CONTENT_TYPES, name


class TestUploadUrlRefusesAnUnlistedName:
    @staticmethod
    async def _ask(filename, content_type):
        from api.evidence_files import get_upload_url
        from schemas import EvidenceFileUploadUrlRequest

        membership = MagicMock()
        membership.organization_id = uuid4()
        membership.user.db_id = str(uuid4())
        with patch("api.evidence_files.generate_upload_presigned_post") as signer:
            signer.return_value = {
                "url": "https://store.example/bucket", "fields": {},
                "object_key": f"evidence/org/2026/11/abc_{filename}",
                "expires_in": 900, "method": "POST", "provider": "aws_s3",
            }
            try:
                with patch("api.evidence_files.mint_upload_ticket", return_value="ticket"):
                    result = await get_upload_url(
                        org_id=membership.organization_id, evidence_id="ERL-001",
                        request=EvidenceFileUploadUrlRequest(
                            filename=filename, content_type=content_type, file_size_bytes=10,
                        ),
                        membership=membership,
                    )
            except Exception as exc:  # noqa: BLE001 — the HTTPException is the result under test
                return exc, signer
            return result, signer

    @pytest.mark.asyncio
    @pytest.mark.parametrize("filename,content_type", [
        ("sshd.conf", "text/plain"),        # a browser calls this plain text
        ("photo.jfif", "image/jpeg"),       # and this a JPEG
        ("README", "text/plain"),
    ])
    async def test_an_accepted_type_under_an_unlisted_name_is_refused_and_nothing_is_signed(
        self, filename, content_type,
    ):
        from fastapi import HTTPException

        outcome, signer = await self._ask(filename, content_type)
        assert isinstance(outcome, HTTPException)
        assert outcome.status_code == 400
        assert "must end in an accepted extension" in outcome.detail
        signer.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_listed_name_is_signed_as_before(self):
        outcome, signer = await self._ask("server.log", "text/plain")
        assert outcome.s3_key == "evidence/org/2026/11/abc_server.log"
        signer.assert_called_once()
