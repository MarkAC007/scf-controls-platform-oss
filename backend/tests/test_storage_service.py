"""
Unit tests for the storage service facade (storage_service.py).

Tests backend detection (S3 vs Azure vs none) and delegation to the
appropriate implementation.
"""
import importlib
import pytest
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Backend detection
# ---------------------------------------------------------------------------


class TestBackendDetection:
    """Tests for auto-detecting the storage backend."""

    def test_detects_azure_when_account_name_set(self, monkeypatch):
        monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_NAME", "teststorage")
        monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_KEY", "dGVzdGtleQ==")
        monkeypatch.delenv("EVIDENCE_BUCKET", raising=False)

        import services.storage_service as mod
        mod._BACKEND = None  # Reset detection cache
        assert mod._detect_backend() == "azure"

    def test_detects_s3_when_bucket_set(self, monkeypatch):
        monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_NAME", raising=False)
        monkeypatch.setenv("EVIDENCE_BUCKET", "test-bucket")

        import services.storage_service as mod
        mod._BACKEND = None
        assert mod._detect_backend() == "s3"

    def test_detects_none_when_nothing_set(self, monkeypatch):
        monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_NAME", raising=False)
        monkeypatch.delenv("EVIDENCE_BUCKET", raising=False)

        import services.storage_service as mod
        mod._BACKEND = None
        assert mod._detect_backend() == "none"

    def test_azure_takes_precedence_over_s3(self, monkeypatch):
        monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_NAME", "teststorage")
        monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_KEY", "dGVzdGtleQ==")
        monkeypatch.setenv("EVIDENCE_BUCKET", "test-bucket")

        import services.storage_service as mod
        mod._BACKEND = None
        assert mod._detect_backend() == "azure"


class TestIsConfigured:
    """Tests for is_configured()."""

    def test_configured_with_s3(self, monkeypatch):
        monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_NAME", raising=False)
        monkeypatch.setenv("EVIDENCE_BUCKET", "test-bucket")

        import services.storage_service as mod
        import services.s3_service as s3_mod
        mod._BACKEND = None
        s3_mod.EVIDENCE_BUCKET = "test-bucket"
        assert mod.is_configured() is True

    def test_not_configured(self, monkeypatch):
        monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_NAME", raising=False)
        monkeypatch.delenv("EVIDENCE_BUCKET", raising=False)

        import services.storage_service as mod
        mod._BACKEND = None
        assert mod.is_configured() is False


class TestDelegation:
    """Tests that facade delegates to the correct backend."""

    def test_write_inbox_payload_raises_when_not_configured(self, monkeypatch):
        monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_NAME", raising=False)
        monkeypatch.delenv("EVIDENCE_BUCKET", raising=False)

        import services.storage_service as mod
        mod._BACKEND = None

        with pytest.raises(ValueError, match="not configured"):
            mod.write_inbox_payload("key", b"body", "org-1")

    @patch("services.s3_service.write_inbox_payload")
    def test_write_inbox_payload_delegates_to_s3(self, mock_s3_write, monkeypatch):
        monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_NAME", raising=False)
        monkeypatch.setenv("EVIDENCE_BUCKET", "test-bucket")

        import services.storage_service as mod
        mod._BACKEND = None

        mod.write_inbox_payload("key", b"body", "org-1")
        mock_s3_write.assert_called_once_with("key", b"body", "org-1")

    def test_generate_upload_raises_when_not_configured(self, monkeypatch):
        monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_NAME", raising=False)
        monkeypatch.delenv("EVIDENCE_BUCKET", raising=False)

        import services.storage_service as mod
        mod._BACKEND = None

        with pytest.raises(ValueError, match="not configured"):
            mod.generate_upload_presigned_post("org-1", "test.pdf", "application/pdf")


# ---------------------------------------------------------------------------
# Azure Blob Service
# ---------------------------------------------------------------------------


class TestAzureBlobService:
    """Tests for Azure Blob Storage implementation."""

    @pytest.fixture(autouse=True)
    def setup_azure_env(self, monkeypatch):
        monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_NAME", "teststorage")
        monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_KEY", "dGVzdGtleQ==")
        monkeypatch.setenv("EVIDENCE_CONTAINER", "evidence")

        import services.azure_blob_service as mod
        mod.AZURE_STORAGE_ACCOUNT_NAME = "teststorage"
        mod.AZURE_STORAGE_ACCOUNT_KEY = "dGVzdGtleQ=="
        mod.EVIDENCE_CONTAINER = "evidence"
        mod._blob_service_client = None
        mod._container_client = None
        return mod

    def test_is_configured(self, setup_azure_env):
        assert setup_azure_env.is_configured() is True

    def test_not_configured_when_empty(self, setup_azure_env, monkeypatch):
        setup_azure_env.AZURE_STORAGE_ACCOUNT_NAME = ""
        assert setup_azure_env.is_configured() is False

    def test_generate_object_key_format(self, setup_azure_env):
        key = setup_azure_env._generate_object_key("org-123", "test.pdf")
        assert key.startswith("evidence/org-123/")
        parts = key.split("/")
        assert len(parts) == 5

    def test_sanitize_filename(self, setup_azure_env):
        assert setup_azure_env._sanitize_filename("my file (1).pdf") == "my_file_1_.pdf"

    @patch("azure.storage.blob.generate_blob_sas", return_value="sig=test")
    def test_generate_upload_presigned_post_returns_sas_url(self, mock_sas, setup_azure_env):
        result = setup_azure_env.generate_upload_presigned_post(
            org_id="org-1",
            filename="test.pdf",
            content_type="application/pdf",
        )
        assert "url" in result
        assert "teststorage.blob.core.windows.net" in result["url"]
        assert "sig=test" in result["url"]
        assert result["fields"] == {}  # Azure SAS URLs are self-contained
        assert result["object_key"].startswith("evidence/org-1/")

    @patch("azure.storage.blob.generate_blob_sas", return_value="sig=test")
    def test_generate_upload_rejects_bad_content_type(self, mock_sas, setup_azure_env):
        with pytest.raises(ValueError, match="not allowed"):
            setup_azure_env.generate_upload_presigned_post(
                org_id="org-1",
                filename="malware.exe",
                content_type="application/x-msdownload",
            )

    @patch("azure.storage.blob.generate_blob_sas", return_value="sig=test")
    def test_generate_download_url_returns_sas_url(self, mock_sas, setup_azure_env):
        url = setup_azure_env.generate_download_url(
            org_id="org-1",
            file_key="evidence/org-1/2026/03/abc_test.pdf",
        )
        assert "teststorage.blob.core.windows.net" in url
        assert "sig=test" in url

    @patch("azure.storage.blob.generate_blob_sas", return_value="sig=test")
    def test_generate_download_url_rejects_cross_org(self, mock_sas, setup_azure_env):
        with pytest.raises(ValueError, match="Access denied"):
            setup_azure_env.generate_download_url(
                org_id="org-1",
                file_key="evidence/org-2/2026/03/abc_test.pdf",
            )

    def test_write_inbox_payload_raises_when_not_configured(self, setup_azure_env):
        setup_azure_env.AZURE_STORAGE_ACCOUNT_NAME = ""
        with pytest.raises(ValueError, match="not configured"):
            setup_azure_env.write_inbox_payload("key", b"body", "org-1")

    def test_tag_evidence_object_raises_when_not_configured(self, setup_azure_env):
        setup_azure_env.AZURE_STORAGE_ACCOUNT_NAME = ""
        with pytest.raises(ValueError, match="not configured"):
            setup_azure_env.tag_evidence_object("key", "org-1")

    def test_check_object_exists_returns_false_when_not_configured(self, setup_azure_env):
        setup_azure_env.AZURE_STORAGE_ACCOUNT_NAME = ""
        assert setup_azure_env.check_object_exists("key") is False
