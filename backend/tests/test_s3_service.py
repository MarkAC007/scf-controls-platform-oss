"""
Unit tests for the S3 Evidence Storage Service (Issue #324).
Uses unittest.mock to mock boto3 — no external dependencies required.

Phase 0 (#967) replaced the driver's import-time module constants with a
per-call :class:`ResolvedStorageConfig`, so these tests now build a config and
hand it to each call rather than assigning to module globals. ``_get_s3_client``
became ``_client``, which takes the config and caches per config identity.
"""
import pytest
from unittest.mock import patch, MagicMock

from services.storage_config import (
    PROVIDER_AWS_S3,
    SOURCE_LEGACY_ENV,
    SSE_AES256,
    ResolvedStorageConfig,
    StorageNotConfigured,
)


@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    """Set required env vars for all tests."""
    monkeypatch.setenv("EVIDENCE_BUCKET", "test-evidence-bucket")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
    monkeypatch.setenv("EVIDENCE_URL_EXPIRY", "900")
    monkeypatch.setenv("EVIDENCE_MAX_FILE_SIZE", str(50 * 1024 * 1024))


def _config(bucket: str = "test-evidence-bucket") -> ResolvedStorageConfig:
    """A resolved config for real AWS S3 — no endpoint, so SSE-S3 is requested."""
    return ResolvedStorageConfig(
        config_id="test",
        source=SOURCE_LEGACY_ENV,
        provider=PROVIDER_AWS_S3,
        bucket=bucket,
        region="eu-west-1",
        sse_mode=SSE_AES256,
        url_expiry=900,
        max_file_size=50 * 1024 * 1024,
    )


@pytest.fixture
def config():
    return _config()


@pytest.fixture
def s3_service(mock_env):
    """The driver module, with its client cache cleared between tests."""
    import services.s3_service as mod
    mod.reset_client_cache()
    yield mod
    mod.reset_client_cache()


class TestSanitizeFilename:
    """Tests for filename sanitization."""

    def test_spaces_replaced(self, s3_service):
        assert s3_service._sanitize_filename("my file.pdf") == "my_file.pdf"

    def test_special_chars_replaced(self, s3_service):
        assert s3_service._sanitize_filename("report (2).pdf") == "report_2_.pdf"

    def test_multiple_underscores_collapsed(self, s3_service):
        assert s3_service._sanitize_filename("a   b   c.txt") == "a_b_c.txt"

    def test_clean_filename_unchanged(self, s3_service):
        assert s3_service._sanitize_filename("clean-file_v2.pdf") == "clean-file_v2.pdf"


class TestGenerateObjectKey:
    """Tests for S3 object key generation."""

    def test_key_has_evidence_prefix(self, s3_service):
        key = s3_service._generate_object_key("org-123", "test.pdf")
        assert key.startswith("evidence/org-123/")

    def test_key_has_date_components(self, s3_service):
        key = s3_service._generate_object_key("org-123", "test.pdf")
        parts = key.split("/")
        # evidence / org_id / year / month / uuid_filename
        assert len(parts) == 5
        assert parts[2].isdigit()  # year
        assert parts[3].isdigit()  # month

    def test_key_has_uuid_prefix_on_filename(self, s3_service):
        key = s3_service._generate_object_key("org-123", "test.pdf")
        filename_part = key.split("/")[-1]
        # uuid12_filename
        assert "_" in filename_part
        uuid_part = filename_part.split("_")[0]
        assert len(uuid_part) == 12

    def test_key_sanitizes_filename(self, s3_service):
        key = s3_service._generate_object_key("org-123", "my file (1).pdf")
        assert " " not in key
        assert "(" not in key


class TestGenerateUploadPresignedPost:
    """Tests for upload pre-signed POST generation."""

    @patch("services.s3_service._client")
    def test_returns_url_fields_and_key(self, mock_get_client, s3_service):
        mock_client = MagicMock()
        mock_client.generate_presigned_post.return_value = {
            "url": "https://s3.amazonaws.com/test-evidence-bucket",
            "fields": {
                "key": "evidence/org-1/2026/02/abc123456789_test.pdf",
                "Content-Type": "application/pdf",
                "x-amz-server-side-encryption": "AES256",
                "policy": "base64policy",
                "x-amz-signature": "sig",
            },
        }
        mock_get_client.return_value = mock_client

        result = s3_service.generate_upload_presigned_post(
            org_id="org-1",
            filename="test.pdf",
            content_type="application/pdf",
            config=_config(),
        )

        assert "url" in result
        assert "fields" in result
        assert "object_key" in result
        assert result["object_key"].startswith("evidence/org-1/")

    @patch("services.s3_service._client")
    def test_enforces_content_type_allowlist(self, mock_get_client, s3_service):
        with pytest.raises(ValueError, match="not allowed"):
            s3_service.generate_upload_presigned_post(
                org_id="org-1",
                filename="malware.exe",
                content_type="application/x-msdownload",
                config=_config(),
            )

    @patch("services.s3_service._client")
    def test_allows_pdf(self, mock_get_client, s3_service):
        mock_client = MagicMock()
        mock_client.generate_presigned_post.return_value = {
            "url": "https://s3.amazonaws.com/bucket",
            "fields": {"key": "evidence/org-1/2026/02/abc_test.pdf"},
        }
        mock_get_client.return_value = mock_client

        result = s3_service.generate_upload_presigned_post(
            org_id="org-1",
            filename="test.pdf",
            content_type="application/pdf",
            config=_config(),
        )
        assert result is not None

    @patch("services.s3_service._client")
    def test_allows_xlsx(self, mock_get_client, s3_service):
        mock_client = MagicMock()
        mock_client.generate_presigned_post.return_value = {
            "url": "https://s3.amazonaws.com/bucket",
            "fields": {"key": "evidence/org-1/2026/02/abc_data.xlsx"},
        }
        mock_get_client.return_value = mock_client

        result = s3_service.generate_upload_presigned_post(
            org_id="org-1",
            filename="data.xlsx",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            config=_config(),
        )
        assert result is not None

    def test_raises_if_no_bucket(self, s3_service):
        with pytest.raises(StorageNotConfigured, match="No evidence store is configured"):
            s3_service.generate_upload_presigned_post(
                org_id="org-1",
                filename="test.pdf",
                content_type="application/pdf",
                config=_config(bucket=""),
            )

    @patch("services.s3_service._client")
    def test_presigned_post_conditions_include_sse(self, mock_get_client, s3_service):
        mock_client = MagicMock()
        mock_client.generate_presigned_post.return_value = {
            "url": "https://s3.amazonaws.com/bucket",
            "fields": {},
        }
        mock_get_client.return_value = mock_client

        s3_service.generate_upload_presigned_post(
            org_id="org-1",
            filename="test.pdf",
            content_type="application/pdf",
            config=_config(),
        )

        call_kwargs = mock_client.generate_presigned_post.call_args
        conditions = call_kwargs.kwargs.get("Conditions") or call_kwargs[1].get("Conditions")
        # Verify SSE condition is present
        assert {"x-amz-server-side-encryption": "AES256"} in conditions


class TestGenerateDownloadUrl:
    """Tests for download pre-signed URL generation."""

    @patch("services.s3_service._client")
    def test_returns_url_for_valid_key(self, mock_get_client, s3_service):
        mock_client = MagicMock()
        mock_client.generate_presigned_url.return_value = "https://s3.amazonaws.com/signed-url"
        mock_get_client.return_value = mock_client

        url = s3_service.generate_download_url(
            org_id="org-1",
            file_key="evidence/org-1/2026/02/abc123_test.pdf",
            config=_config(),
        )
        assert url == "https://s3.amazonaws.com/signed-url"

    @patch("services.s3_service._client")
    def test_rejects_cross_org_access(self, mock_get_client, s3_service):
        with pytest.raises(ValueError, match="Access denied"):
            s3_service.generate_download_url(
                org_id="org-1",
                file_key="evidence/org-2/2026/02/abc123_test.pdf",
                config=_config(),
            )

    @patch("services.s3_service._client")
    def test_rejects_non_evidence_key(self, mock_get_client, s3_service):
        with pytest.raises(ValueError, match="Access denied"):
            s3_service.generate_download_url(
                org_id="org-1",
                file_key="static/org-1/something.pdf",
                config=_config(),
            )

    def test_raises_if_no_bucket(self, s3_service):
        with pytest.raises(StorageNotConfigured, match="No evidence store is configured"):
            s3_service.generate_download_url(
                org_id="org-1",
                file_key="evidence/org-1/2026/02/abc123_test.pdf",
                config=_config(bucket=""),
            )

    @patch("services.s3_service._client")
    def test_includes_content_disposition_when_filename_given(self, mock_get_client, s3_service):
        mock_client = MagicMock()
        mock_client.generate_presigned_url.return_value = "https://s3.amazonaws.com/signed-url"
        mock_get_client.return_value = mock_client

        s3_service.generate_download_url(
            org_id="org-1",
            file_key="evidence/org-1/2026/02/abc123_test.pdf",
            filename="friendly-name.pdf",
            config=_config(),
        )

        call_kwargs = mock_client.generate_presigned_url.call_args
        params = call_kwargs.kwargs.get("Params") or call_kwargs[1].get("Params")
        assert "ResponseContentDisposition" in params
        assert "friendly-name.pdf" in params["ResponseContentDisposition"]


class TestTagEvidenceObject:
    """Tests for S3 object tagging."""

    @patch("services.s3_service._client")
    def test_applies_org_tag(self, mock_get_client, s3_service):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        result = s3_service.tag_evidence_object(
            file_key="evidence/org-1/2026/02/abc123_test.pdf",
            org_id="org-1",
            config=_config(),
        )

        assert result["tagged"] is True
        assert result["tag_count"] == 1

        call_kwargs = mock_client.put_object_tagging.call_args
        tagging = call_kwargs.kwargs.get("Tagging") or call_kwargs[1].get("Tagging")
        tag_keys = [t["Key"] for t in tagging["TagSet"]]
        assert "organization_id" in tag_keys

    @patch("services.s3_service._client")
    def test_applies_all_tags_when_provided(self, mock_get_client, s3_service):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        result = s3_service.tag_evidence_object(
            file_key="evidence/org-1/2026/02/abc123_test.pdf",
            org_id="org-1",
            evidence_id="ev-456",
            uploaded_by="user-789",
            config=_config(),
        )

        assert result["tag_count"] == 3

        call_kwargs = mock_client.put_object_tagging.call_args
        tagging = call_kwargs.kwargs.get("Tagging") or call_kwargs[1].get("Tagging")
        tag_keys = [t["Key"] for t in tagging["TagSet"]]
        assert "organization_id" in tag_keys
        assert "evidence_id" in tag_keys
        assert "uploaded_by" in tag_keys

    def test_raises_if_no_bucket(self, s3_service):
        with pytest.raises(StorageNotConfigured, match="No evidence store is configured"):
            s3_service.tag_evidence_object(
                file_key="evidence/org-1/2026/02/abc123_test.pdf",
                org_id="org-1",
                config=_config(bucket=""),
            )
