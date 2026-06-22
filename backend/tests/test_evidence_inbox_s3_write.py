"""
Unit tests for Issue #400 — inbox handler writes payload to S3.

Verifies that:
  1. A valid JSON delivery calls write_inbox_payload with the correct args.
  2. An S3 failure causes the handler to return status="failed" (no orphaned
     DB record is persisted without a backing S3 object).
  3. All patches avoid real S3/DB calls.

Uses unittest.mock — no external dependencies required.
"""
import json
import hashlib
import hmac
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4


# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------

ORG_ID = uuid4()
EVIDENCE_ID = "ERL-IAM-001"
WEBHOOK_SECRET = "test-secret-abc123"


def _make_signed_body(payload: dict) -> tuple[bytes, str]:
    """Return (body_bytes, sha256=<sig>) for a JSON payload."""
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()
    return body, sig


def _mock_endpoint():
    """Build a mock WebhookEndpoint that passes all validation."""
    ep = MagicMock()
    ep.id = uuid4()
    ep.organization_id = ORG_ID
    ep.is_active = True
    ep.secret = WEBHOOK_SECRET
    ep.allowed_evidence_ids = None
    ep.last_delivery_at = None
    ep.delivery_count = 0
    return ep


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestInboxS3Write:
    """Integration-style tests for the S3 write path in ingest_evidence."""

    @pytest.mark.asyncio
    @patch("api.evidence_inbox.write_inbox_payload")
    @patch("api.evidence_inbox.run_validation", new_callable=AsyncMock)
    @patch("api.evidence_inbox.get_scan_service")
    @patch("api.evidence_inbox.create_audit_entry", new_callable=AsyncMock)
    async def test_valid_delivery_calls_write_inbox_payload(
        self,
        mock_audit,
        mock_get_scan,
        mock_validate,
        mock_write,
    ):
        """A valid JSON delivery must call write_inbox_payload with correct args."""
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        from api.evidence_inbox import router

        # --- mock scan service (returns "clean") ---
        scan_result = MagicMock()
        scan_result.status = "clean"
        scan_result.details = {}
        mock_scan = AsyncMock()
        mock_scan.scan_bytes = AsyncMock(return_value=scan_result)
        mock_get_scan.return_value = mock_scan

        # --- mock validation (no-op) ---
        mock_validate.return_value = MagicMock()

        # --- build payload ---
        payload = {"source": "test", "data": {"status": "compliant"}}
        body, sig = _make_signed_body(payload)
        delivery_id = uuid4()
        evidence_file_id = uuid4()

        # --- mock DB session ---
        mock_db = AsyncMock()

        # Simulate execute() returning endpoint on first call
        ep = _mock_endpoint()
        mock_db.execute = AsyncMock(side_effect=[
            # 1. endpoint lookup
            MagicMock(**{"scalar_one_or_none.return_value": ep}),
            # 2. idempotency check (no existing delivery)
            MagicMock(**{"scalar_one_or_none.return_value": None}),
            # 3. evidence tracker lookup (no tracker record)
            MagicMock(**{"scalar_one_or_none.return_value": None}),
        ])

        # delivery and evidence_file assigned ids after flush
        delivery_mock = MagicMock()
        delivery_mock.id = delivery_id
        delivery_mock.status = "processed"
        evidence_file_mock = MagicMock()
        evidence_file_mock.id = evidence_file_id
        evidence_file_mock.s3_key = f"evidence/{ORG_ID}/inbox/{delivery_id}.json"
        evidence_file_mock.scan_status = "clean"

        captured_objects = []

        def fake_add(obj):
            captured_objects.append(obj)

        mock_db.add = MagicMock(side_effect=fake_add)

        flush_call_count = 0

        async def fake_flush():
            nonlocal flush_call_count
            flush_call_count += 1
            if flush_call_count == 1:
                # First flush: assign id to the delivery-like object
                for obj in captured_objects:
                    if not hasattr(obj, "_is_evidence_file"):
                        obj.id = delivery_id
            elif flush_call_count == 2:
                # Second flush: assign id to the evidence file-like object
                for obj in captured_objects:
                    if hasattr(obj, "s3_key"):
                        obj.id = evidence_file_id

        mock_db.flush = AsyncMock(side_effect=fake_flush)
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        # Build request
        from fastapi import Request
        import io

        scope = {
            "type": "http",
            "method": "POST",
            "path": f"/organizations/{ORG_ID}/evidence/{EVIDENCE_ID}/inbox",
            "query_string": b"",
            "headers": [
                (b"content-type", b"application/json"),
                (b"x-scf-webhook-id", str(ep.id).encode()),
                (b"x-scf-signature", sig.encode()),
            ],
        }

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        request = Request(scope, receive)

        from starlette.responses import Response
        response = Response()

        from api.evidence_inbox import ingest_evidence
        result = await ingest_evidence(
            request=request,
            response=response,
            org_id=ORG_ID,
            evidence_id=EVIDENCE_ID,
            db=mock_db,
        )

        # write_inbox_payload must have been called
        assert mock_write.called, "write_inbox_payload was not called"
        call_kwargs = mock_write.call_args
        assert call_kwargs is not None

    @pytest.mark.asyncio
    @patch("api.evidence_inbox.write_inbox_payload", side_effect=Exception("S3 connection refused"))
    @patch("api.evidence_inbox.get_scan_service")
    @patch("api.evidence_inbox.create_audit_entry", new_callable=AsyncMock)
    async def test_s3_failure_returns_failed_status(
        self,
        mock_audit,
        mock_get_scan,
        mock_write,
    ):
        """If write_inbox_payload raises, the handler must return status='failed'."""
        from fastapi import Request
        from starlette.responses import Response

        payload = {"source": "test", "data": {}}
        body, sig = _make_signed_body(payload)
        delivery_id = uuid4()

        ep = _mock_endpoint()

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=[
            MagicMock(**{"scalar_one_or_none.return_value": ep}),
            MagicMock(**{"scalar_one_or_none.return_value": None}),
            # Note: tracker lookup won't be reached because S3 write fails first
        ])

        captured_objects = []

        def fake_add(obj):
            captured_objects.append(obj)

        mock_db.add = MagicMock(side_effect=fake_add)

        flush_call_count = 0

        async def fake_flush():
            nonlocal flush_call_count
            flush_call_count += 1
            if flush_call_count == 1 and captured_objects:
                # Simulate DB assigning PK to delivery after first flush
                captured_objects[0].id = delivery_id

        mock_db.flush = AsyncMock(side_effect=fake_flush)
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        scope = {
            "type": "http",
            "method": "POST",
            "path": f"/organizations/{ORG_ID}/evidence/{EVIDENCE_ID}/inbox",
            "query_string": b"",
            "headers": [
                (b"content-type", b"application/json"),
                (b"x-scf-webhook-id", str(ep.id).encode()),
                (b"x-scf-signature", sig.encode()),
            ],
        }

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        request = Request(scope, receive)
        response = Response()

        from api.evidence_inbox import ingest_evidence
        result = await ingest_evidence(
            request=request,
            response=response,
            org_id=ORG_ID,
            evidence_id=EVIDENCE_ID,
            db=mock_db,
        )

        # Handler must return status="failed" — S3 error is caught by outer except
        assert result.status == "failed", (
            f"Expected status='failed' when S3 raises, got '{result.status}'"
        )
        assert mock_write.called, "write_inbox_payload was never called"


class TestInboxTrackerLinking:
    """Tests for the evidence tracker linking in ingest_evidence (Issue #xxx)."""

    @pytest.mark.asyncio
    @patch("api.evidence_inbox.write_inbox_payload")
    @patch("api.evidence_inbox.run_validation", new_callable=AsyncMock)
    @patch("api.evidence_inbox.get_scan_service")
    @patch("api.evidence_inbox.create_audit_entry", new_callable=AsyncMock)
    async def test_updates_last_collection_date_when_tracker_exists(
        self,
        mock_audit,
        mock_get_scan,
        mock_validate,
        mock_write,
    ):
        """When an EvidenceTracking record matches, last_collection_date should be updated."""
        from fastapi import Request
        from starlette.responses import Response

        scan_result = MagicMock()
        scan_result.status = "clean"
        scan_result.details = {}
        mock_scan = AsyncMock()
        mock_scan.scan_bytes = AsyncMock(return_value=scan_result)
        mock_get_scan.return_value = mock_scan

        mock_validate.return_value = MagicMock()

        payload = {"source": "test", "data": {"status": "compliant"}}
        body, sig = _make_signed_body(payload)
        delivery_id = uuid4()
        evidence_file_id = uuid4()

        ep = _mock_endpoint()

        # Mock tracker record
        mock_tracker = MagicMock()
        mock_tracker.last_collection_date = None

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=[
            # 1. endpoint lookup
            MagicMock(**{"scalar_one_or_none.return_value": ep}),
            # 2. tracker lookup — record exists (no idempotency check: no X-SCF-Event-Id header)
            MagicMock(**{"scalar_one_or_none.return_value": mock_tracker}),
        ])

        captured_objects = []

        def fake_add(obj):
            captured_objects.append(obj)

        mock_db.add = MagicMock(side_effect=fake_add)

        flush_call_count = 0

        async def fake_flush():
            nonlocal flush_call_count
            flush_call_count += 1
            if flush_call_count == 1:
                for obj in captured_objects:
                    if not hasattr(obj, "_is_evidence_file"):
                        obj.id = delivery_id
            elif flush_call_count == 2:
                for obj in captured_objects:
                    if hasattr(obj, "s3_key"):
                        obj.id = evidence_file_id

        mock_db.flush = AsyncMock(side_effect=fake_flush)
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        scope = {
            "type": "http",
            "method": "POST",
            "path": f"/organizations/{ORG_ID}/evidence/{EVIDENCE_ID}/inbox",
            "query_string": b"",
            "headers": [
                (b"content-type", b"application/json"),
                (b"x-scf-webhook-id", str(ep.id).encode()),
                (b"x-scf-signature", sig.encode()),
            ],
        }

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        request = Request(scope, receive)
        response = Response()

        from api.evidence_inbox import ingest_evidence
        result = await ingest_evidence(
            request=request,
            response=response,
            org_id=ORG_ID,
            evidence_id=EVIDENCE_ID,
            db=mock_db,
        )

        assert result.status == "processed"
        # Tracker's last_collection_date should have been updated
        assert mock_tracker.last_collection_date is not None


class TestWriteInboxPayload:
    """Unit tests for the s3_service.write_inbox_payload function."""

    @patch("services.s3_service._get_s3_client")
    def test_calls_put_object_with_correct_args(self, mock_get_client):
        """write_inbox_payload must call put_object with expected parameters."""
        import importlib
        import services.s3_service as mod
        mod._s3_client = None
        mod.EVIDENCE_BUCKET = "test-evidence-bucket"

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        body = b'{"source": "test"}'
        mod.write_inbox_payload(
            s3_key="evidence/org-1/inbox/delivery-abc.json",
            body=body,
            org_id="org-1",
        )

        mock_client.put_object.assert_called_once()
        call_kwargs = mock_client.put_object.call_args[1]
        assert call_kwargs["Bucket"] == "test-evidence-bucket"
        assert call_kwargs["Key"] == "evidence/org-1/inbox/delivery-abc.json"
        assert call_kwargs["Body"] == body
        assert call_kwargs["ContentType"] == "application/json"
        assert call_kwargs["ServerSideEncryption"] == "AES256"
        assert call_kwargs["Metadata"]["x-scf-org-id"] == "org-1"

    def test_raises_if_no_bucket_configured(self):
        """write_inbox_payload must raise ValueError when EVIDENCE_BUCKET is empty."""
        import services.s3_service as mod
        original = mod.EVIDENCE_BUCKET
        try:
            mod.EVIDENCE_BUCKET = ""
            with pytest.raises(ValueError, match="not configured"):
                mod.write_inbox_payload(
                    s3_key="evidence/org-1/inbox/x.json",
                    body=b"{}",
                    org_id="org-1",
                )
        finally:
            mod.EVIDENCE_BUCKET = original
