"""
Unit tests for validation_service Rule 5: s3_object_exists (Issue #400).

Verifies scenarios:
  1. Object missing       → rule returns level="invalid"
  2. Object exists         → rule returns level="valid"
  3. scan_status="quarantined" → rule is skipped (level="valid")
  4. Storage not configured → rule returns level="warning"
  5. Exception during check → rule returns level="warning"

Uses unittest.mock — no external dependencies required.
Now tests against the storage_service facade (not s3_service directly).
"""
import pytest
from unittest.mock import MagicMock, patch
from uuid import uuid4


def _make_evidence_file(scan_status: str = "pending", s3_key: str = None) -> MagicMock:
    """Return a mock EvidenceFile with the given scan_status."""
    ef = MagicMock()
    ef.id = uuid4()
    ef.organization_id = uuid4()
    ef.evidence_id = "ERL-IAM-001"
    ef.scan_status = scan_status
    ef.s3_key = s3_key or f"evidence/{ef.organization_id}/inbox/delivery-abc.json"
    return ef


class TestRuleS3ObjectExists:
    """Tests for _rule_s3_object_exists via storage_service facade."""

    @pytest.mark.asyncio
    @patch("services.storage_service.check_object_exists", return_value=False)
    @patch("services.storage_service.is_configured", return_value=True)
    async def test_returns_invalid_when_object_missing(self, mock_configured, mock_exists):
        """Rule 5 returns level=invalid when object does not exist."""
        from services.validation_service import _rule_s3_object_exists
        ef = _make_evidence_file(scan_status="pending")
        result = await _rule_s3_object_exists(ef)

        assert result["rule"] == "s3_object_exists"
        assert result["level"] == "invalid"
        assert "s3_object_missing" in result.get("detail", "")

    @pytest.mark.asyncio
    @patch("services.storage_service.check_object_exists", return_value=True)
    @patch("services.storage_service.is_configured", return_value=True)
    async def test_returns_valid_when_object_exists(self, mock_configured, mock_exists):
        """Rule 5 returns level=valid when object exists."""
        from services.validation_service import _rule_s3_object_exists
        ef = _make_evidence_file(scan_status="clean")
        result = await _rule_s3_object_exists(ef)

        assert result["rule"] == "s3_object_exists"
        assert result["level"] == "valid"
        assert ef.s3_key in result["message"]

    @pytest.mark.asyncio
    @patch("services.storage_service.is_configured", return_value=True)
    async def test_skips_quarantined_files(self, mock_configured):
        """Rule 5 must skip the check for quarantined files."""
        from services.validation_service import _rule_s3_object_exists
        ef = _make_evidence_file(scan_status="quarantined")
        result = await _rule_s3_object_exists(ef)

        assert result["rule"] == "s3_object_exists"
        assert result["level"] == "valid"
        assert "quarantined" in result["message"].lower()

    @pytest.mark.asyncio
    @patch("services.storage_service.is_configured", return_value=False)
    async def test_returns_warning_when_storage_not_configured(self, mock_configured):
        """Rule 5 returns level=warning when storage is not configured."""
        from services.validation_service import _rule_s3_object_exists
        ef = _make_evidence_file(scan_status="pending")
        result = await _rule_s3_object_exists(ef)

        assert result["rule"] == "s3_object_exists"
        assert result["level"] == "warning"
        assert "not configured" in result["message"]

    @pytest.mark.asyncio
    @patch("services.storage_service.check_object_exists", side_effect=Exception("Connection timeout"))
    @patch("services.storage_service.is_configured", return_value=True)
    async def test_returns_warning_on_exception(self, mock_configured, mock_exists):
        """Rule 5 returns level=warning (not invalid) on unexpected errors."""
        from services.validation_service import _rule_s3_object_exists
        ef = _make_evidence_file(scan_status="clean")
        result = await _rule_s3_object_exists(ef)

        assert result["rule"] == "s3_object_exists"
        assert result["level"] == "warning"
        assert "Connection timeout" in result["message"]
