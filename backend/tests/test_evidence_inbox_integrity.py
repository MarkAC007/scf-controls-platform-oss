"""
Tests for the webhook inbox half of evidence integrity (#57).

The inbox is the second — and, before this change, only — path that created
`EvidenceFile` rows. It scanned for malware but recorded no digest, quarantined
without leaving an audit row, and advanced the evidence collection date even for
payloads it silently discarded.

Harness style follows tests/test_evidence_inbox_s3_write.py: a real ASGI
`Request` over a signed body, with storage, scanner, validation and the broker
mocked at their module boundaries.
"""
import hashlib
import hmac
import json
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import catalog_models  # noqa: F401  — completes the SQLAlchemy mapper registry

ORG_ID = uuid4()
EVIDENCE_ID = "ERL-IAM-001"
WEBHOOK_SECRET = "test-secret-abc123"


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()


def _endpoint():
    ep = MagicMock()
    ep.id = uuid4()
    ep.organization_id = ORG_ID
    ep.is_active = True
    ep.secret = WEBHOOK_SECRET
    ep.allowed_evidence_ids = None
    ep.last_delivery_at = None
    ep.delivery_count = 0
    return ep


def _request(body: bytes, endpoint, content_type: bytes = b"application/json"):
    from fastapi import Request

    scope = {
        "type": "http",
        "method": "POST",
        "path": f"/organizations/{ORG_ID}/evidence/{EVIDENCE_ID}/inbox",
        "query_string": b"",
        "headers": [
            (b"content-type", content_type),
            (b"x-scf-webhook-id", str(endpoint.id).encode()),
            (b"x-scf-signature", _sign(body).encode()),
        ],
    }

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def _db(tracker=None):
    """A session that answers endpoint lookup, idempotency check, tracker lookup."""
    db = AsyncMock()

    async def execute(stmt, *a, **kw):
        # Answers are keyed off the table the statement targets, not off call
        # order: the handler issues a different number of queries depending on
        # whether an event id was sent and whether the payload parsed, and a
        # positional mock would answer the wrong question for half the cases.
        sql = str(stmt)
        if "webhook_endpoints" in sql:
            return MagicMock(**{"scalar_one_or_none.return_value": _CURRENT_ENDPOINT[0]})
        if "evidence_tracking" in sql:
            return MagicMock(**{"scalar_one_or_none.return_value": tracker})
        return MagicMock(**{"scalar_one_or_none.return_value": None})

    db.execute = AsyncMock(side_effect=execute)
    added = []
    db.add = MagicMock(side_effect=added.append)
    db.added = added

    async def fake_flush():
        for obj in added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid4()

    db.flush = AsyncMock(side_effect=fake_flush)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


_CURRENT_ENDPOINT = [None]


async def _ingest(body, db, content_type=b"application/json"):
    from starlette.responses import Response
    from api.evidence_inbox import ingest_evidence

    return await ingest_evidence(
        request=_request(body, _CURRENT_ENDPOINT[0], content_type),
        response=Response(),
        org_id=ORG_ID,
        evidence_id=EVIDENCE_ID,
        db=db,
    )


@pytest.fixture(autouse=True)
def endpoint():
    _CURRENT_ENDPOINT[0] = _endpoint()
    yield _CURRENT_ENDPOINT[0]


def _scan_service(status="clean"):
    result = MagicMock()
    result.status = status
    result.details = {"message": "eicar"} if status == "infected" else {}
    svc = AsyncMock()
    svc.scan_bytes = AsyncMock(return_value=result)
    return svc


def _evidence_files(db):
    return [o for o in db.added if hasattr(o, "s3_key")]


class TestInboxRecordsADigest:
    @pytest.mark.asyncio
    @patch("api.evidence_inbox.enqueue_integrity_verification")
    @patch("api.evidence_inbox.write_inbox_payload")
    @patch("api.evidence_inbox.run_validation", new_callable=AsyncMock)
    @patch("api.evidence_inbox.get_scan_service")
    @patch("api.evidence_inbox.create_audit_entry", new_callable=AsyncMock)
    async def test_digest_is_taken_at_ingest_with_no_extra_read(
        self, mock_audit, mock_scan, mock_validate, mock_write, mock_enqueue
    ):
        """The bytes are already in hand here, so hashing them costs nothing."""
        mock_scan.return_value = _scan_service("clean")
        body = json.dumps({"source": "test", "data": {"status": "compliant"}}).encode()
        db = _db()

        await _ingest(body, db)

        files = _evidence_files(db)
        assert len(files) == 1
        assert files[0].sha256_hash == hashlib.sha256(body).hexdigest()

    @pytest.mark.asyncio
    @patch("api.evidence_inbox.enqueue_integrity_verification")
    @patch("api.evidence_inbox.write_inbox_payload")
    @patch("api.evidence_inbox.run_validation", new_callable=AsyncMock)
    @patch("api.evidence_inbox.get_scan_service")
    @patch("api.evidence_inbox.create_audit_entry", new_callable=AsyncMock)
    async def test_stored_object_is_verified_afterwards(
        self, mock_audit, mock_scan, mock_validate, mock_write, mock_enqueue
    ):
        """The ingest digest describes what arrived; the task checks what landed."""
        mock_scan.return_value = _scan_service("clean")
        db = _db()

        await _ingest(json.dumps({"source": "t", "data": {}}).encode(), db)

        mock_enqueue.assert_called_once()

    @pytest.mark.asyncio
    @patch("api.evidence_inbox.enqueue_integrity_verification")
    @patch("api.evidence_inbox.write_inbox_payload")
    @patch("api.evidence_inbox.run_validation", new_callable=AsyncMock)
    @patch("api.evidence_inbox.get_scan_service")
    @patch("api.evidence_inbox.create_audit_entry", new_callable=AsyncMock)
    async def test_no_uploader_is_invented_for_system_ingest(
        self, mock_audit, mock_scan, mock_validate, mock_write, mock_enqueue
    ):
        mock_scan.return_value = _scan_service("clean")
        db = _db()

        await _ingest(json.dumps({"source": "t", "data": {}}).encode(), db)

        assert _evidence_files(db)[0].uploaded_by_user_id is None


class TestInboxQuarantine:
    @pytest.mark.asyncio
    @patch("api.evidence_inbox.enqueue_integrity_verification")
    @patch("api.evidence_inbox.write_inbox_payload")
    @patch("api.evidence_inbox.get_scan_service")
    @patch("api.evidence_inbox.create_audit_entry", new_callable=AsyncMock)
    @patch("services.storage_service.move_to_quarantine", return_value="quarantine/org/real-key")
    async def test_infected_payload_uses_the_key_storage_returned(
        self, mock_move, mock_audit, mock_scan, mock_write, mock_enqueue
    ):
        """The hand-rebuilt key is gone — the DB now names where the object is."""
        mock_scan.return_value = _scan_service("infected")
        db = _db()

        result = await _ingest(json.dumps({"source": "t", "data": {}}).encode(), db)

        assert result.status == "rejected"
        assert _evidence_files(db)[0].s3_key == "quarantine/org/real-key"

    @pytest.mark.asyncio
    @patch("api.evidence_inbox.enqueue_integrity_verification")
    @patch("api.evidence_inbox.write_inbox_payload")
    @patch("api.evidence_inbox.get_scan_service")
    @patch("api.evidence_inbox.create_audit_entry", new_callable=AsyncMock)
    @patch("services.storage_service.move_to_quarantine", return_value="quarantine/org/real-key")
    async def test_quarantine_writes_an_audit_row(
        self, mock_move, mock_audit, mock_scan, mock_write, mock_enqueue
    ):
        """Moving a customer's evidence used to leave no trace anyone could query."""
        from models import AuditLog

        mock_scan.return_value = _scan_service("infected")
        db = _db()

        await _ingest(json.dumps({"source": "t", "data": {}}).encode(), db)

        rows = [o for o in db.added if isinstance(o, AuditLog)]
        assert len(rows) == 1
        assert rows[0].field_name == "s3_key"
        assert rows[0].new_value == "quarantine/org/real-key"
        assert rows[0].action_source == "system"


class TestUningestablePayloads:
    """TOMBSTONE coverage: the multipart half of this endpoint never existed."""

    @pytest.mark.asyncio
    @patch("api.evidence_inbox.enqueue_integrity_verification")
    @patch("api.evidence_inbox.write_inbox_payload")
    @patch("api.evidence_inbox.get_scan_service")
    @patch("api.evidence_inbox.create_audit_entry", new_callable=AsyncMock)
    async def test_non_json_payload_creates_no_evidence_file(
        self, mock_audit, mock_scan, mock_write, mock_enqueue
    ):
        db = _db()
        await _ingest(b"--boundary\r\nnot json\r\n", db, content_type=b"multipart/form-data; boundary=boundary")

        assert _evidence_files(db) == []
        mock_write.assert_not_called()

    @pytest.mark.asyncio
    @patch("api.evidence_inbox.enqueue_integrity_verification")
    @patch("api.evidence_inbox.write_inbox_payload")
    @patch("api.evidence_inbox.get_scan_service")
    @patch("api.evidence_inbox.create_audit_entry", new_callable=AsyncMock)
    async def test_non_json_payload_is_reported_as_rejected_not_processed(
        self, mock_audit, mock_scan, mock_write, mock_enqueue
    ):
        """Returning success for a discarded payload is how the drop stayed invisible."""
        db = _db()
        result = await _ingest(
            b"--boundary\r\nnot json\r\n", db,
            content_type=b"multipart/form-data; boundary=boundary",
        )

        assert result.status == "rejected"
        assert "not ingested" in result.message

    @pytest.mark.asyncio
    @patch("api.evidence_inbox.enqueue_integrity_verification")
    @patch("api.evidence_inbox.write_inbox_payload")
    @patch("api.evidence_inbox.get_scan_service")
    @patch("api.evidence_inbox.create_audit_entry", new_callable=AsyncMock)
    async def test_discarded_payload_does_not_advance_the_collection_date(
        self, mock_audit, mock_scan, mock_write, mock_enqueue
    ):
        """Freshness must not be claimed for evidence that was never stored."""
        tracker = MagicMock()
        tracker.last_collection_date = None
        db = _db(tracker=tracker)

        await _ingest(
            b"--boundary\r\nnot json\r\n", db,
            content_type=b"multipart/form-data; boundary=boundary",
        )

        assert tracker.last_collection_date is None

    @pytest.mark.asyncio
    @patch("api.evidence_inbox.enqueue_integrity_verification")
    @patch("api.evidence_inbox.write_inbox_payload")
    @patch("api.evidence_inbox.run_validation", new_callable=AsyncMock)
    @patch("api.evidence_inbox.get_scan_service")
    @patch("api.evidence_inbox.create_audit_entry", new_callable=AsyncMock)
    async def test_a_stored_payload_still_advances_the_collection_date(
        self, mock_audit, mock_scan, mock_validate, mock_write, mock_enqueue
    ):
        """The guard is on 'nothing was stored', not on webhooks in general."""
        mock_scan.return_value = _scan_service("clean")
        tracker = MagicMock()
        tracker.last_collection_date = None
        db = _db(tracker=tracker)

        await _ingest(json.dumps({"source": "t", "data": {}}).encode(), db)

        assert tracker.last_collection_date is not None
