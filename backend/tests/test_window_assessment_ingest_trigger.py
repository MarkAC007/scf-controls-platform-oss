"""Debounced window assessment on ingest (#569 parity, work-order item 6).

Upload confirmation and webhook ingest must schedule `assess_window_task`
for the evidence item, once per debounce interval, on the `evidence_window`
queue with `assessment_source="ingest"` — and must never fail the ingestion
when Redis or the broker is down.
"""
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import window_assessment_trigger as trig  # noqa: E402

ORG = uuid4()


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv("WINDOW_ASSESSMENT_ON_INGEST", raising=False)
    monkeypatch.delenv("WINDOW_ASSESSMENT_INGEST_DEBOUNCE_SECONDS", raising=False)


@pytest.fixture
def fake_task(monkeypatch):
    """Stand in for tasks_window_assessment.assess_window_task."""
    task = MagicMock()
    module = SimpleNamespace(assess_window_task=task)
    monkeypatch.setitem(sys.modules, "tasks_window_assessment", module)
    return task


@pytest.fixture
def fake_redis(monkeypatch):
    redis = MagicMock()
    redis.set = AsyncMock(return_value=True)
    module = SimpleNamespace(get_redis_client=AsyncMock(return_value=redis))
    monkeypatch.setitem(sys.modules, "redis_client", module)
    return redis


class TestKnobs:
    def test_defaults(self, clean_env):
        assert trig.ingest_trigger_enabled() is True
        assert trig.debounce_seconds() == 120

    @pytest.mark.parametrize("raw,expected", [("30", 30), ("0", 0), ("-5", 0), ("abc", 120), ("", 120), (" 45 ", 45)])
    def test_debounce_parsing(self, clean_env, monkeypatch, raw, expected):
        monkeypatch.setenv("WINDOW_ASSESSMENT_INGEST_DEBOUNCE_SECONDS", raw)
        assert trig.debounce_seconds() == expected

    @pytest.mark.parametrize("raw,expected", [("false", False), ("0", False), ("", True), ("yes", True), ("TRUE", True)])
    def test_enabled_parsing(self, clean_env, monkeypatch, raw, expected):
        monkeypatch.setenv("WINDOW_ASSESSMENT_ON_INGEST", raw)
        assert trig.ingest_trigger_enabled() is expected

    def test_key_is_scoped_to_org_and_evidence(self):
        assert trig.debounce_key(ORG, "E-1") == f"scf:window-assessment:ingest-debounce:{ORG}:E-1"
        assert trig.debounce_key(ORG, "E-1") != trig.debounce_key(uuid4(), "E-1")


@pytest.mark.asyncio
class TestSchedule:
    async def test_first_ingest_claims_key_and_enqueues_after_debounce(self, clean_env, fake_task, fake_redis):
        assert await trig.schedule_window_assessment_on_ingest(ORG, "E-1", trigger="upload") is True
        fake_redis.set.assert_awaited_once_with(trig.debounce_key(ORG, "E-1"), "upload", nx=True, ex=120)
        fake_task.apply_async.assert_called_once_with(
            kwargs={"organization_id": str(ORG), "evidence_id": "E-1", "assessment_source": "ingest"},
            countdown=120,
            queue="evidence_window",
        )

    async def test_second_ingest_inside_interval_is_debounced(self, clean_env, fake_task, fake_redis):
        fake_redis.set.return_value = None  # SET NX lost: key already held
        assert await trig.schedule_window_assessment_on_ingest(ORG, "E-1", trigger="webhook") is False
        fake_task.apply_async.assert_not_called()

    async def test_custom_debounce_drives_both_ttl_and_countdown(self, clean_env, monkeypatch, fake_task, fake_redis):
        monkeypatch.setenv("WINDOW_ASSESSMENT_INGEST_DEBOUNCE_SECONDS", "30")
        await trig.schedule_window_assessment_on_ingest(ORG, "E-1", trigger="upload")
        assert fake_redis.set.await_args.kwargs["ex"] == 30
        assert fake_task.apply_async.call_args.kwargs["countdown"] == 30

    async def test_zero_debounce_skips_redis_and_enqueues_immediately(self, clean_env, monkeypatch, fake_task, fake_redis):
        monkeypatch.setenv("WINDOW_ASSESSMENT_INGEST_DEBOUNCE_SECONDS", "0")
        assert await trig.schedule_window_assessment_on_ingest(ORG, "E-1", trigger="upload") is True
        fake_redis.set.assert_not_awaited()
        assert fake_task.apply_async.call_args.kwargs["countdown"] == 0

    async def test_disabled_does_nothing(self, clean_env, monkeypatch, fake_task, fake_redis):
        monkeypatch.setenv("WINDOW_ASSESSMENT_ON_INGEST", "false")
        assert await trig.schedule_window_assessment_on_ingest(ORG, "E-1", trigger="upload") is False
        fake_redis.set.assert_not_awaited()
        fake_task.apply_async.assert_not_called()

    async def test_redis_outage_is_fail_open_and_still_enqueues(self, clean_env, fake_task, fake_redis):
        fake_redis.set.side_effect = ConnectionError("redis down")
        assert await trig.schedule_window_assessment_on_ingest(ORG, "E-1", trigger="upload") is True
        fake_task.apply_async.assert_called_once()

    async def test_broker_outage_never_raises(self, clean_env, fake_task, fake_redis):
        fake_task.apply_async.side_effect = RuntimeError("broker down")
        assert await trig.schedule_window_assessment_on_ingest(ORG, "E-1", trigger="upload") is False


@pytest.mark.asyncio
class TestUploadConfirmationHook:
    """confirm_upload schedules the window assessment for tracked evidence only."""

    @pytest.fixture(autouse=True)
    def signing_secret(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_TOKEN_SECRET", "test-signing-secret")

    @staticmethod
    def _membership():
        m = MagicMock()
        m.organization_id = ORG
        m.user = MagicMock()
        m.user.id = uuid4()
        m.user.db_id = str(m.user.id)
        m.user.email = "test@example.com"
        m.user.display_name = "Test User"
        m.role = "editor"
        return m

    @staticmethod
    def _db(tracker, membership):
        db = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock()
        db.commit = AsyncMock()
        db.execute = AsyncMock()
        db.execute.return_value.scalar_one_or_none = MagicMock(return_value=tracker)

        async def refresh(obj, attribute_names=None):
            from evidence_mocks import unasserted

            obj.id = uuid4()
            from datetime import datetime

            obj.uploaded_at = datetime.utcnow()
            obj.classification = "internal"
            obj.scan_status = "clean"
            obj.scan_details = None
            obj.computed_sha256 = None
            obj.hash_verification_status = "pending"
            obj.hash_verified_at = None
            obj.hash_verification_details = None
            unasserted(obj)
            obj.is_deleted = False
            obj.file_size_bytes = 0
            obj.expires_at = None
            obj.uploaded_by = membership.user
            obj.review_status = "not_reviewed"
            obj.reviewed_by_user_id = None
            obj.reviewed_at = None
            obj.review_notes = None
            obj.reviewed_by = None

        db.refresh = refresh
        return db

    async def _confirm(self, tracker):
        from api.evidence_files import confirm_upload
        from schemas import EvidenceFileConfirmRequest
        from services.upload_ticket import mint_upload_ticket

        membership = self._membership()
        s3_key = f"evidence/{ORG}/2026/09/abc123456789_report.pdf"
        ticket = mint_upload_ticket(
            object_key=s3_key, org_id=str(ORG), evidence_id="ERL-001", user_id=membership.user.db_id,
        )
        request = EvidenceFileConfirmRequest(s3_key=s3_key, sha256_hash="a" * 64, upload_ticket=ticket)
        with patch("api.evidence_files.enqueue_integrity_verification"), \
                patch("api.evidence_files.run_validation", new_callable=AsyncMock), \
                patch("api.evidence_files.log_entity_changes", new_callable=AsyncMock), \
                patch("api.evidence_files.tag_evidence_object", return_value={"tagged": True}), \
                patch("api.evidence_files.generate_download_url", return_value="https://d"), \
                patch("api.evidence_files.schedule_window_assessment_on_ingest", new_callable=AsyncMock) as sched:
            await confirm_upload(
                org_id=ORG, evidence_id="ERL-001", request=request, http_request=MagicMock(),
                membership=membership, db=self._db(tracker, membership),
            )
        return sched

    async def test_tracked_upload_schedules_window_assessment(self):
        tracker = MagicMock()
        tracker.last_collection_date = None
        sched = await self._confirm(tracker)
        sched.assert_awaited_once_with(ORG, "ERL-001", trigger="upload")

    async def test_untracked_upload_does_not_schedule(self):
        sched = await self._confirm(None)
        sched.assert_not_awaited()


@pytest.mark.asyncio
class TestWebhookIngestHook:
    """ingest_evidence schedules the window assessment for a stored, tracked delivery."""

    async def _ingest(self, tracker):
        from fastapi import Request
        from starlette.responses import Response
        from test_evidence_inbox_s3_write import _make_signed_body, _mock_endpoint, ORG_ID, EVIDENCE_ID
        from api.evidence_inbox import ingest_evidence

        scan_result = MagicMock()
        scan_result.status = "clean"
        scan_result.details = {}
        scan_service = AsyncMock()
        scan_service.scan_bytes = AsyncMock(return_value=scan_result)

        body, sig = _make_signed_body({"source": "test", "data": {"status": "compliant"}})
        ep = _mock_endpoint()
        mock_db = AsyncMock()

        def execute(stmt):
            # Answer by the table being queried rather than by call order, so
            # an extra lookup added upstream cannot silently shift the answers.
            sql = str(stmt)
            if "webhook_endpoints" in sql:
                row = ep
            elif "evidence_tracking" in sql:
                row = tracker
            else:
                row = None
            return MagicMock(**{"scalar_one_or_none.return_value": row})

        mock_db.execute = AsyncMock(side_effect=execute)
        added = []
        mock_db.add = MagicMock(side_effect=added.append)

        async def fake_flush():
            for obj in added:
                if getattr(obj, "id", None) is None:
                    obj.id = uuid4()

        mock_db.flush = AsyncMock(side_effect=fake_flush)
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        scope = {
            "type": "http", "method": "POST",
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

        with patch("api.evidence_inbox.write_inbox_payload"), \
                patch("api.evidence_inbox.run_validation", new_callable=AsyncMock), \
                patch("api.evidence_inbox.get_scan_service", return_value=scan_service), \
                patch("api.evidence_inbox.create_audit_entry", new_callable=AsyncMock), \
                patch("api.evidence_inbox.enqueue_integrity_verification"), \
                patch("api.evidence_inbox.schedule_window_assessment_on_ingest", new_callable=AsyncMock) as sched:
            result = await ingest_evidence(
                request=Request(scope, receive), response=Response(),
                org_id=ORG_ID, evidence_id=EVIDENCE_ID, db=mock_db,
            )
        return result, sched, ORG_ID, EVIDENCE_ID

    async def test_tracked_delivery_schedules_after_commit(self):
        tracker = MagicMock()
        tracker.last_collection_date = None
        result, sched, org_id, evidence_id = await self._ingest(tracker)
        assert result.status == "processed"
        sched.assert_awaited_once_with(org_id, evidence_id, trigger="webhook")

    async def test_untracked_delivery_does_not_schedule(self):
        result, sched, _, _ = await self._ingest(None)
        assert result.status == "processed"
        sched.assert_not_awaited()
