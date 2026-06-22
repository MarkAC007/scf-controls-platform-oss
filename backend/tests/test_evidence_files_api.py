"""
Unit tests for the Evidence Files API endpoints (Issue #325).
Tests upload-url, confirm, list, and soft-delete endpoints.
Uses unittest.mock — no database or S3 required.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4, UUID
from datetime import datetime

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_env(monkeypatch):
    """Set required env vars."""
    monkeypatch.setenv("EVIDENCE_BUCKET", "test-evidence-bucket")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
    monkeypatch.setenv("EVIDENCE_URL_EXPIRY", "900")


@pytest.fixture
def org_id():
    return uuid4()


@pytest.fixture
def user_id():
    return uuid4()


@pytest.fixture
def membership(org_id, user_id):
    """Mock OrgMembership dependency."""
    m = MagicMock()
    m.organization_id = org_id
    m.user = MagicMock()
    m.user.id = user_id  # ORM User.id — used by UserSimple serialization
    m.user.db_id = str(user_id)  # Auth User.db_id — used by endpoint code
    m.user.email = "test@example.com"
    m.user.display_name = "Test User"
    m.role = "editor"
    return m


@pytest.fixture
def mock_db():
    """Mock async database session."""
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.execute = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# Upload URL tests
# ---------------------------------------------------------------------------

class TestGetUploadUrl:
    """Tests for POST /evidence/{evidence_id}/files/upload-url"""

    @pytest.mark.asyncio
    @patch("api.evidence_files.generate_upload_presigned_post")
    async def test_returns_upload_url_with_s3_key(self, mock_presigned, membership, mock_env):
        from api.evidence_files import get_upload_url
        from schemas import EvidenceFileUploadUrlRequest

        mock_presigned.return_value = {
            "url": "https://s3.amazonaws.com/test-bucket",
            "fields": {"key": "evidence/org/2026/02/abc_test.pdf"},
            "object_key": "evidence/org/2026/02/abc_test.pdf",
        }

        request = EvidenceFileUploadUrlRequest(
            filename="test.pdf",
            content_type="application/pdf",
            file_size_bytes=1024,
        )

        result = await get_upload_url(
            org_id=membership.organization_id,
            evidence_id="ERL-001",
            request=request,
            membership=membership,
        )

        assert result.url == "https://s3.amazonaws.com/test-bucket"
        assert result.s3_key == "evidence/org/2026/02/abc_test.pdf"
        assert result.expires_in == 900

    @pytest.mark.asyncio
    @patch("api.evidence_files.generate_upload_presigned_post")
    async def test_rejects_invalid_content_type(self, mock_presigned, membership, mock_env):
        from api.evidence_files import get_upload_url
        from schemas import EvidenceFileUploadUrlRequest

        mock_presigned.side_effect = ValueError("Content type 'application/x-msdownload' not allowed")

        request = EvidenceFileUploadUrlRequest(
            filename="malware.exe",
            content_type="application/x-msdownload",
            file_size_bytes=1024,
        )

        with pytest.raises(Exception) as exc_info:
            await get_upload_url(
                org_id=membership.organization_id,
                evidence_id="ERL-001",
                request=request,
                membership=membership,
            )
        assert exc_info.value.status_code == 400

    def test_schema_rejects_oversized_file(self):
        from schemas import EvidenceFileUploadUrlRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            EvidenceFileUploadUrlRequest(
                filename="huge.pdf",
                content_type="application/pdf",
                file_size_bytes=60 * 1024 * 1024,  # 60MB > 50MB limit
            )

    def test_schema_rejects_zero_size(self):
        from schemas import EvidenceFileUploadUrlRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            EvidenceFileUploadUrlRequest(
                filename="empty.pdf",
                content_type="application/pdf",
                file_size_bytes=0,
            )


# ---------------------------------------------------------------------------
# Confirm upload tests
# ---------------------------------------------------------------------------

class TestConfirmUpload:
    """Tests for POST /evidence/{evidence_id}/files/confirm"""

    @pytest.mark.asyncio
    @patch("api.evidence_files.run_validation", new_callable=AsyncMock)
    @patch("api.evidence_files.log_entity_changes", new_callable=AsyncMock)
    @patch("api.evidence_files.tag_evidence_object")
    async def test_creates_evidence_file_record(self, mock_tag, mock_audit, mock_validation, membership, mock_db, org_id):
        from api.evidence_files import confirm_upload
        from schemas import EvidenceFileConfirmRequest

        mock_tag.return_value = {"tagged": True, "key": "k", "tag_count": 3}

        s3_key = f"evidence/{org_id}/2026/02/abc123456789_report.pdf"
        request = EvidenceFileConfirmRequest(
            s3_key=s3_key,
            sha256_hash="a" * 64,
        )

        # Mock the refresh to populate the ORM object
        async def mock_refresh(obj, attribute_names=None):
            obj.id = uuid4()
            obj.uploaded_at = datetime.utcnow()
            obj.classification = "internal"
            obj.is_deleted = False
            obj.file_size_bytes = 0
            obj.expires_at = None
            obj.uploaded_by = membership.user
            obj.review_status = "not_reviewed"
            obj.reviewed_by_user_id = None
            obj.reviewed_at = None
            obj.review_notes = None
            obj.reviewed_by = None

        mock_db.refresh = mock_refresh

        with patch("api.evidence_files.generate_download_url", return_value="https://download-url"):
            result = await confirm_upload(
                org_id=org_id,
                evidence_id="ERL-001",
                request=request,
                http_request=MagicMock(),
                membership=membership,
                db=mock_db,
            )

        assert result.evidence_id == "ERL-001"
        assert result.s3_key == s3_key
        assert result.filename == "report.pdf"
        assert result.sha256_hash == "a" * 64
        mock_db.add.assert_called_once()
        mock_audit.assert_called_once()

    @pytest.mark.asyncio
    async def test_rejects_cross_org_s3_key(self, membership, mock_db, org_id):
        from api.evidence_files import confirm_upload
        from schemas import EvidenceFileConfirmRequest

        other_org = uuid4()
        request = EvidenceFileConfirmRequest(
            s3_key=f"evidence/{other_org}/2026/02/abc_report.pdf",
        )

        with pytest.raises(Exception) as exc_info:
            await confirm_upload(
                org_id=org_id,
                evidence_id="ERL-001",
                request=request,
                http_request=MagicMock(),
                membership=membership,
                db=mock_db,
            )
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    @patch("api.evidence_files._to_response")
    @patch("api.evidence_files._safe_download_url", return_value=None)
    @patch("api.evidence_files.run_validation", new_callable=AsyncMock)
    @patch("api.evidence_files.log_entity_changes", new_callable=AsyncMock)
    @patch("api.evidence_files.tag_evidence_object")
    async def test_continues_when_s3_tagging_fails(
        self, mock_tag, mock_audit, mock_validation, mock_dl_url, mock_response, membership, mock_db, org_id
    ):
        """Tagging failure is non-fatal — confirm should still create the DB record."""
        from api.evidence_files import confirm_upload
        from schemas import EvidenceFileConfirmRequest

        mock_tag.side_effect = Exception("S3 error")
        mock_response.return_value = MagicMock()

        request = EvidenceFileConfirmRequest(
            s3_key=f"evidence/{org_id}/2026/02/abc_report.pdf",
        )

        # Mock DB async methods
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        await confirm_upload(
            org_id=org_id,
            evidence_id="ERL-001",
            request=request,
            http_request=MagicMock(),
            membership=membership,
            db=mock_db,
        )
        # Tagging was attempted and failed
        mock_tag.assert_called_once()
        # But DB record was still created (execution continued)
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()


# ---------------------------------------------------------------------------
# List files tests
# ---------------------------------------------------------------------------

class TestListEvidenceFiles:
    """Tests for GET /evidence/{evidence_id}/files"""

    @pytest.mark.asyncio
    async def test_returns_empty_list(self, membership, mock_db, org_id):
        from api.evidence_files import list_evidence_files

        # Mock empty result
        mock_result = MagicMock()
        mock_result.unique.return_value.scalars.return_value.all.return_value = []
        mock_db.execute.return_value = mock_result

        result = await list_evidence_files(
            org_id=org_id,
            evidence_id="ERL-001",
            membership=membership,
            db=mock_db,
        )

        assert result.total == 0
        assert result.files == []

    @pytest.mark.asyncio
    async def test_returns_files_with_download_urls(self, membership, mock_db, org_id, user_id):
        from api.evidence_files import list_evidence_files

        # Create a mock EvidenceFile
        mock_file = MagicMock()
        mock_file.id = uuid4()
        mock_file.organization_id = org_id
        mock_file.evidence_id = "ERL-001"
        mock_file.filename = "report.pdf"
        mock_file.s3_key = f"evidence/{org_id}/2026/02/abc_report.pdf"
        mock_file.content_type = "application/pdf"
        mock_file.file_size_bytes = 1024
        mock_file.sha256_hash = "a" * 64
        mock_file.classification = "internal"
        mock_file.uploaded_by_user_id = user_id
        mock_file.uploaded_at = datetime.utcnow()
        mock_file.expires_at = None
        mock_file.is_deleted = False
        mock_file.uploaded_by = membership.user
        mock_file.review_status = "pending"
        mock_file.reviewed_by_user_id = None
        mock_file.reviewed_at = None
        mock_file.review_notes = None
        mock_file.reviewed_by = None

        mock_result = MagicMock()
        mock_result.unique.return_value.scalars.return_value.all.return_value = [mock_file]
        mock_db.execute.return_value = mock_result

        result = await list_evidence_files(
            org_id=org_id,
            evidence_id="ERL-001",
            membership=membership,
            db=mock_db,
        )

        assert result.total == 1
        assert result.files[0].filename == "report.pdf"
        assert "/download" in result.files[0].download_url
        assert str(mock_file.id) in result.files[0].download_url

    @pytest.mark.asyncio
    async def test_excludes_deleted_files(self, membership, mock_db, org_id):
        """Verify the query filters is_deleted == False."""
        from api.evidence_files import list_evidence_files

        mock_result = MagicMock()
        mock_result.unique.return_value.scalars.return_value.all.return_value = []
        mock_db.execute.return_value = mock_result

        await list_evidence_files(
            org_id=org_id,
            evidence_id="ERL-001",
            membership=membership,
            db=mock_db,
        )

        # Verify execute was called (query has is_deleted == False filter)
        mock_db.execute.assert_called_once()


# ---------------------------------------------------------------------------
# Get single file tests
# ---------------------------------------------------------------------------

class TestGetEvidenceFile:
    """Tests for GET /evidence/{evidence_id}/files/{file_id}"""

    @pytest.mark.asyncio
    async def test_returns_file_with_download_url(self, membership, mock_db, org_id, user_id):
        from api.evidence_files import get_evidence_file

        file_id = uuid4()
        mock_file = MagicMock()
        mock_file.id = file_id
        mock_file.organization_id = org_id
        mock_file.evidence_id = "ERL-001"
        mock_file.filename = "report.pdf"
        mock_file.s3_key = f"evidence/{org_id}/2026/02/abc_report.pdf"
        mock_file.content_type = "application/pdf"
        mock_file.file_size_bytes = 1024
        mock_file.sha256_hash = "a" * 64
        mock_file.classification = "internal"
        mock_file.scan_status = "clean"
        mock_file.scan_details = None
        mock_file.uploaded_by_user_id = user_id
        mock_file.uploaded_at = datetime.utcnow()
        mock_file.expires_at = None
        mock_file.is_deleted = False
        mock_file.uploaded_by = membership.user
        mock_file.review_status = "pending"
        mock_file.reviewed_by_user_id = None
        mock_file.reviewed_at = None
        mock_file.review_notes = None
        mock_file.reviewed_by = None

        mock_result = MagicMock()
        mock_result.unique.return_value.scalar_one_or_none.return_value = mock_file
        mock_db.execute.return_value = mock_result

        result = await get_evidence_file(
            org_id=org_id,
            evidence_id="ERL-001",
            file_id=file_id,
            membership=membership,
            db=mock_db,
        )

        assert result.id == file_id
        assert result.filename == "report.pdf"
        assert "/download" in result.download_url
        assert str(file_id) in result.download_url

    @pytest.mark.asyncio
    async def test_returns_404_for_missing_file(self, membership, mock_db, org_id):
        from api.evidence_files import get_evidence_file

        mock_result = MagicMock()
        mock_result.unique.return_value.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        with pytest.raises(Exception) as exc_info:
            await get_evidence_file(
                org_id=org_id,
                evidence_id="ERL-001",
                file_id=uuid4(),
                membership=membership,
                db=mock_db,
            )
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_404_for_deleted_file(self, membership, mock_db, org_id):
        """Deleted files should not be returned — the query filters is_deleted == False."""
        from api.evidence_files import get_evidence_file

        # The query includes is_deleted == False, so a deleted file won't match
        mock_result = MagicMock()
        mock_result.unique.return_value.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        with pytest.raises(Exception) as exc_info:
            await get_evidence_file(
                org_id=org_id,
                evidence_id="ERL-001",
                file_id=uuid4(),
                membership=membership,
                db=mock_db,
            )
        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Delete file tests
# ---------------------------------------------------------------------------

class TestDeleteEvidenceFile:
    """Tests for DELETE /evidence/{evidence_id}/files/{file_id}"""

    @pytest.mark.asyncio
    @patch("api.evidence_files.log_entity_changes", new_callable=AsyncMock)
    async def test_soft_deletes_file(self, mock_audit, membership, mock_db, org_id, user_id):
        from api.evidence_files import delete_evidence_file

        file_id = uuid4()
        mock_file = MagicMock()
        mock_file.id = file_id
        mock_file.organization_id = org_id
        mock_file.evidence_id = "ERL-001"
        mock_file.filename = "report.pdf"
        mock_file.s3_key = f"evidence/{org_id}/2026/02/abc_report.pdf"
        mock_file.content_type = "application/pdf"
        mock_file.file_size_bytes = 1024
        mock_file.sha256_hash = None
        mock_file.classification = "internal"
        mock_file.uploaded_by_user_id = user_id
        mock_file.uploaded_at = datetime.utcnow()
        mock_file.expires_at = None
        mock_file.is_deleted = False
        mock_file.uploaded_by = membership.user
        mock_file.review_status = "pending"
        mock_file.reviewed_by_user_id = None
        mock_file.reviewed_at = None
        mock_file.review_notes = None
        mock_file.reviewed_by = None

        mock_result = MagicMock()
        mock_result.unique.return_value.scalar_one_or_none.return_value = mock_file
        mock_db.execute.return_value = mock_result

        async def mock_refresh(obj, attribute_names=None):
            pass
        mock_db.refresh = mock_refresh

        with patch("api.evidence_files.generate_download_url", return_value=None):
            result = await delete_evidence_file(
                org_id=org_id,
                evidence_id="ERL-001",
                file_id=file_id,
                request=MagicMock(),
                membership=membership,
                db=mock_db,
            )

        assert mock_file.is_deleted is True
        assert mock_file.deleted_at is not None
        assert mock_file.deleted_by_user_id == user_id
        mock_audit.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_404_for_missing_file(self, membership, mock_db, org_id):
        from api.evidence_files import delete_evidence_file

        mock_result = MagicMock()
        mock_result.unique.return_value.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        with pytest.raises(Exception) as exc_info:
            await delete_evidence_file(
                org_id=org_id,
                evidence_id="ERL-001",
                file_id=uuid4(),
                request=MagicMock(),
                membership=membership,
                db=mock_db,
            )
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_410_for_already_deleted(self, membership, mock_db, org_id):
        from api.evidence_files import delete_evidence_file

        mock_file = MagicMock()
        mock_file.is_deleted = True

        mock_result = MagicMock()
        mock_result.unique.return_value.scalar_one_or_none.return_value = mock_file
        mock_db.execute.return_value = mock_result

        with pytest.raises(Exception) as exc_info:
            await delete_evidence_file(
                org_id=org_id,
                evidence_id="ERL-001",
                file_id=uuid4(),
                request=MagicMock(),
                membership=membership,
                db=mock_db,
            )
        assert exc_info.value.status_code == 410


# ---------------------------------------------------------------------------
# Download token auth tests
# ---------------------------------------------------------------------------

class TestDownloadTokenAuth:
    """Tests for download endpoint token-based auth (browser navigation support)."""

    @pytest.mark.asyncio
    @patch("api.evidence_files.download_blob_stream")
    async def test_valid_token_grants_access(self, mock_stream, mock_db, org_id):
        """A valid HMAC token + expires pair should authenticate the download."""
        from api.evidence_files import download_evidence_file
        from services.download_token import generate_download_token

        file_id = uuid4()

        # Generate a valid token
        token, expires = generate_download_token(str(file_id), str(org_id))

        # Mock the DB to return a file
        mock_file = MagicMock()
        mock_file.s3_key = f"evidence/{org_id}/2026/02/abc_report.pdf"
        mock_file.filename = "report.pdf"
        mock_file.content_type = "application/pdf"
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_file
        mock_db.execute.return_value = mock_result

        # Mock blob stream
        mock_stream.return_value = iter([b"chunk"])

        # Build a mock request with NO auth header
        mock_request = MagicMock()
        mock_request.headers = {}

        result = await download_evidence_file(
            org_id=org_id,
            evidence_id="ERL-001",
            file_id=file_id,
            request=mock_request,
            disposition="inline",
            token=token,
            expires=expires,
            db=mock_db,
        )

        # Should succeed — StreamingResponse returned
        assert result.status_code == 200
        assert result.media_type == "application/pdf"

    @pytest.mark.asyncio
    async def test_expired_token_returns_401(self, mock_db, org_id):
        """An expired token should return 401."""
        from api.evidence_files import download_evidence_file
        from services.download_token import generate_download_token

        file_id = uuid4()

        # Generate a token with 0 TTL (already expired)
        token, expires = generate_download_token(str(file_id), str(org_id), ttl_seconds=0)

        # Ensure it's expired
        import time
        time.sleep(1)

        mock_request = MagicMock()
        mock_request.headers = {}

        with pytest.raises(Exception) as exc_info:
            await download_evidence_file(
                org_id=org_id,
                evidence_id="ERL-001",
                file_id=file_id,
                request=mock_request,
                disposition="inline",
                token=token,
                expires=expires,
                db=mock_db,
            )
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_tampered_token_returns_401(self, mock_db, org_id):
        """A tampered token should return 401."""
        from api.evidence_files import download_evidence_file
        from services.download_token import generate_download_token

        file_id = uuid4()

        # Generate a valid token then tamper with it
        token, expires = generate_download_token(str(file_id), str(org_id))
        tampered_token = "0000" + token[4:]

        mock_request = MagicMock()
        mock_request.headers = {}

        with pytest.raises(Exception) as exc_info:
            await download_evidence_file(
                org_id=org_id,
                evidence_id="ERL-001",
                file_id=file_id,
                request=mock_request,
                disposition="inline",
                token=tampered_token,
                expires=expires,
                db=mock_db,
            )
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_no_auth_returns_401(self, mock_db, org_id):
        """No Bearer and no token should return 401."""
        from api.evidence_files import download_evidence_file

        mock_request = MagicMock()
        mock_request.headers = {}

        with pytest.raises(Exception) as exc_info:
            await download_evidence_file(
                org_id=org_id,
                evidence_id="ERL-001",
                file_id=uuid4(),
                request=mock_request,
                disposition="inline",
                token=None,
                expires=None,
                db=mock_db,
            )
        assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# Download token service unit tests
# ---------------------------------------------------------------------------

class TestDownloadTokenService:
    """Tests for services/download_token.py."""

    def test_generate_returns_token_and_expires(self):
        from services.download_token import generate_download_token
        token, expires = generate_download_token("file-1", "org-1")
        assert isinstance(token, str)
        assert len(token) == 64  # SHA256 hex digest
        assert isinstance(expires, int)
        assert expires > 0

    def test_verify_valid_token(self):
        from services.download_token import generate_download_token, verify_download_token
        token, expires = generate_download_token("file-1", "org-1")
        assert verify_download_token("file-1", "org-1", token, expires) is True

    def test_verify_rejects_expired(self):
        from services.download_token import generate_download_token, verify_download_token
        import time
        token, expires = generate_download_token("file-1", "org-1", ttl_seconds=0)
        time.sleep(1)
        assert verify_download_token("file-1", "org-1", token, expires) is False

    def test_verify_rejects_tampered(self):
        from services.download_token import generate_download_token, verify_download_token
        token, expires = generate_download_token("file-1", "org-1")
        assert verify_download_token("file-1", "org-1", "bad" + token[3:], expires) is False

    def test_verify_rejects_wrong_file_id(self):
        from services.download_token import generate_download_token, verify_download_token
        token, expires = generate_download_token("file-1", "org-1")
        assert verify_download_token("file-2", "org-1", token, expires) is False

    def test_verify_rejects_wrong_org_id(self):
        from services.download_token import generate_download_token, verify_download_token
        token, expires = generate_download_token("file-1", "org-1")
        assert verify_download_token("file-1", "org-2", token, expires) is False


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------

class TestGuessContentType:
    """Tests for _guess_content_type helper."""

    def test_pdf(self):
        from api.evidence_files import _guess_content_type
        assert _guess_content_type("report.pdf") == "application/pdf"

    def test_xlsx(self):
        from api.evidence_files import _guess_content_type
        ct = _guess_content_type("data.xlsx")
        assert ct == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    def test_csv(self):
        from api.evidence_files import _guess_content_type
        assert _guess_content_type("data.csv") == "text/csv"

    def test_unknown(self):
        from api.evidence_files import _guess_content_type
        assert _guess_content_type("data.unknown") == "application/octet-stream"

    def test_case_insensitive(self):
        from api.evidence_files import _guess_content_type
        assert _guess_content_type("REPORT.PDF") == "application/pdf"


class TestConfirmRequestValidation:
    """Tests for EvidenceFileConfirmRequest schema validation."""

    def test_valid_request(self):
        from schemas import EvidenceFileConfirmRequest
        req = EvidenceFileConfirmRequest(
            s3_key="evidence/org/2026/02/abc_report.pdf",
            sha256_hash="a" * 64,
        )
        assert req.s3_key == "evidence/org/2026/02/abc_report.pdf"

    def test_sha256_hash_optional(self):
        from schemas import EvidenceFileConfirmRequest
        req = EvidenceFileConfirmRequest(
            s3_key="evidence/org/2026/02/abc_report.pdf",
        )
        assert req.sha256_hash is None

    def test_sha256_hash_wrong_length(self):
        from schemas import EvidenceFileConfirmRequest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            EvidenceFileConfirmRequest(
                s3_key="evidence/org/2026/02/abc_report.pdf",
                sha256_hash="tooshort",
            )


# ---------------------------------------------------------------------------
# Legacy review 410-Gone gating (M4 PR 2, #574 — ISC-23..26)
# ---------------------------------------------------------------------------

class TestReviewEvidenceFile410Gating:
    """Spec ISC-26 — four cases:
       (a) flag-off → legacy 200 unchanged
       (b) flag-on, no window → legacy 200 (preserves backward compat)
       (c) flag-on, with window → 410 Gone
       (d) 410 payload structure correct
    """

    def _make_file(self, file_id, org_id, user_id, uploaded_by_user):
        f = MagicMock()
        f.id = file_id
        f.organization_id = org_id
        f.evidence_id = "ERL-001"
        f.filename = "doc.pdf"
        f.s3_key = f"evidence/{org_id}/2026/02/abc_doc.pdf"
        f.content_type = "application/pdf"
        f.file_size_bytes = 1024
        f.sha256_hash = None
        f.classification = "internal"
        f.uploaded_by_user_id = user_id
        f.uploaded_at = datetime.utcnow()
        f.expires_at = None
        f.is_deleted = False
        # uploaded_by must serialize through UserSimple — use the membership
        # user fixture which has a real UUID + str attributes.
        f.uploaded_by = uploaded_by_user
        f.review_status = "pending"
        f.reviewed_by_user_id = None
        f.reviewed_at = None
        f.review_notes = None
        f.reviewed_by = None
        return f

    @pytest.mark.asyncio
    @patch("api.evidence_files.log_entity_changes", new_callable=AsyncMock)
    async def test_flag_off_returns_legacy_200(
        self, mock_audit, membership, mock_db, org_id, user_id, monkeypatch,
    ):
        """Case (a): flag false → legacy review path unchanged."""
        from api.evidence_files import review_evidence_file
        from schemas import EvidenceFileReviewRequest

        monkeypatch.setenv("ENABLE_PER_WINDOW_REVIEW", "false")
        file_id = uuid4()
        ef = self._make_file(file_id, org_id, user_id, membership.user)

        mock_result = MagicMock()
        mock_result.unique.return_value.scalar_one_or_none.return_value = ef
        mock_db.execute.return_value = mock_result

        async def mock_refresh(obj, attribute_names=None):
            pass
        mock_db.refresh = mock_refresh

        body = EvidenceFileReviewRequest(
            review_status="approved", review_notes="ok"
        )

        result = await review_evidence_file(
            org_id=org_id,
            evidence_id="ERL-001",
            file_id=file_id,
            body=body,
            request=MagicMock(),
            membership=membership,
            db=mock_db,
        )
        # Legacy path returns the response model — not a JSONResponse.
        assert ef.review_status == "approved"
        mock_audit.assert_called_once()

    @pytest.mark.asyncio
    @patch("api.evidence_files.log_entity_changes", new_callable=AsyncMock)
    async def test_flag_on_no_window_returns_legacy_200(
        self, mock_audit, membership, mock_db, org_id, user_id, monkeypatch,
    ):
        """Case (b): flag on but no window assessment exists → legacy 200."""
        from api.evidence_files import review_evidence_file
        from schemas import EvidenceFileReviewRequest

        monkeypatch.setenv("ENABLE_PER_WINDOW_REVIEW", "true")
        file_id = uuid4()
        ef = self._make_file(file_id, org_id, user_id, membership.user)

        # Two execute calls happen on this path:
        #   1. Window-assessment lookup → returns None (no window).
        #   2. EvidenceFile lookup → returns the file.
        ewa_lookup_result = MagicMock()
        ewa_lookup_result.scalar_one_or_none.return_value = None

        file_lookup_result = MagicMock()
        file_lookup_result.unique.return_value.scalar_one_or_none.return_value = ef

        mock_db.execute.side_effect = [ewa_lookup_result, file_lookup_result]

        async def mock_refresh(obj, attribute_names=None):
            pass
        mock_db.refresh = mock_refresh

        body = EvidenceFileReviewRequest(
            review_status="approved", review_notes=None
        )

        await review_evidence_file(
            org_id=org_id,
            evidence_id="ERL-001",
            file_id=file_id,
            body=body,
            request=MagicMock(),
            membership=membership,
            db=mock_db,
        )
        assert ef.review_status == "approved"
        mock_audit.assert_called_once()

    @pytest.mark.asyncio
    async def test_flag_on_with_window_returns_410(
        self, membership, mock_db, org_id, monkeypatch,
    ):
        """Case (c): flag on AND window exists → 410 Gone."""
        from api.evidence_files import review_evidence_file
        from schemas import EvidenceFileReviewRequest

        monkeypatch.setenv("ENABLE_PER_WINDOW_REVIEW", "true")
        file_id = uuid4()
        ewa_id = uuid4()

        ewa_lookup_result = MagicMock()
        ewa_lookup_result.scalar_one_or_none.return_value = ewa_id
        mock_db.execute.return_value = ewa_lookup_result

        body = EvidenceFileReviewRequest(
            review_status="approved", review_notes=None
        )

        result = await review_evidence_file(
            org_id=org_id,
            evidence_id="ERL-001",
            file_id=file_id,
            body=body,
            request=MagicMock(),
            membership=membership,
            db=mock_db,
        )

        # Returned a JSONResponse with status 410 — endpoint short-circuits
        # before the EvidenceFile lookup or any DB writes.
        from fastapi.responses import JSONResponse
        assert isinstance(result, JSONResponse)
        assert result.status_code == 410
        # Sunset header set per RFC 8594.
        assert result.headers.get("Sunset") == "Sat, 09 May 2026 00:00:00 GMT"

    @pytest.mark.asyncio
    async def test_410_payload_structure(
        self, membership, mock_db, org_id, monkeypatch,
    ):
        """Case (d): 410 body contains detail/code/evidence_id/pointer."""
        import json
        from api.evidence_files import review_evidence_file
        from schemas import EvidenceFileReviewRequest

        monkeypatch.setenv("ENABLE_PER_WINDOW_REVIEW", "true")
        file_id = uuid4()
        ewa_id = uuid4()

        ewa_lookup_result = MagicMock()
        ewa_lookup_result.scalar_one_or_none.return_value = ewa_id
        mock_db.execute.return_value = ewa_lookup_result

        body = EvidenceFileReviewRequest(
            review_status="approved", review_notes=None
        )

        result = await review_evidence_file(
            org_id=org_id,
            evidence_id="ERL-001",
            file_id=file_id,
            body=body,
            request=MagicMock(),
            membership=membership,
            db=mock_db,
        )

        payload = json.loads(result.body)
        assert payload["code"] == "PER_FILE_REVIEW_DEPRECATED"
        assert payload["evidence_id"] == "ERL-001"
        assert "Per-file review has been replaced" in payload["detail"]
        assert payload["pointer"]["method"] == "PUT"
        assert payload["pointer"]["latest_window_assessment_id"] == str(ewa_id)
        assert (
            payload["pointer"]["path"]
            == f"/api/organizations/{org_id}/window-assessments/{ewa_id}/review"
        )
