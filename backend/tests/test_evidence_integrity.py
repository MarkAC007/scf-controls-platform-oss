"""
Tests for server-side evidence integrity verification (#57).

Covers the three pieces the feature is built from:

* `services/evidence_integrity_service.py` — the decisions, pure and I/O-free.
* `services/evidence_quarantine.py`        — the shared side effects both
                                             ingestion paths need.
* `tasks_evidence_integrity.py`            — the single fetch that measures a
                                             stored object and records what it
                                             found.

No database, no broker, no object storage — the boundaries are mocked, and the
task's session is stubbed so the assertions are about what the task *writes*
rather than about SQLAlchemy.
"""
import hashlib
import sys
import os
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# `models.System` declares a relationship to `SystemCatalogTemplate`, which lives
# in catalog_models. SQLAlchemy resolves that name lazily, at first instantiation
# of *any* mapped class — so constructing an AuditLog with only `models` imported
# raises. A Celery worker never hits this (it imports every module in
# celery_app.conf.include, catalog included); a test importing two service modules
# would, which is an artefact of the narrow import and not a defect under test.
import catalog_models  # noqa: F401  — completes the mapper registry


# ---------------------------------------------------------------------------
# Pure classification
# ---------------------------------------------------------------------------

class TestComputeSha256:
    def test_matches_hashlib(self):
        from services.evidence_integrity_service import compute_sha256
        assert compute_sha256(b"hello") == hashlib.sha256(b"hello").hexdigest()

    def test_empty_input_is_hashed_not_special_cased(self):
        from services.evidence_integrity_service import compute_sha256
        assert compute_sha256(b"") == hashlib.sha256(b"").hexdigest()


class TestClassifyHash:
    def test_matching_claim_is_verified(self):
        from services.evidence_integrity_service import classify_hash, HASH_VERIFIED
        digest = hashlib.sha256(b"x").hexdigest()
        status, details = classify_hash(digest, digest)
        assert status == HASH_VERIFIED
        assert details["computed_sha256"] == digest

    def test_differing_claim_is_a_mismatch_and_keeps_both_values(self):
        from services.evidence_integrity_service import classify_hash, HASH_MISMATCH
        computed = hashlib.sha256(b"actual").hexdigest()
        claimed = hashlib.sha256(b"claimed").hexdigest()
        status, details = classify_hash(claimed, computed)
        assert status == HASH_MISMATCH
        # Both survive in the record — the discrepancy is the finding.
        assert details["computed_sha256"] == computed
        assert details["asserted_sha256"] == claimed

    def test_absent_claim_is_unasserted_not_verified(self):
        """Nothing to corroborate is not the same as corroborated."""
        from services.evidence_integrity_service import classify_hash, HASH_UNASSERTED
        computed = hashlib.sha256(b"x").hexdigest()
        for claim in (None, "", "   "):
            status, _ = classify_hash(claim, computed)
            assert status == HASH_UNASSERTED

    def test_case_and_whitespace_do_not_raise_a_false_alarm(self):
        """Clients have always been free to send either case."""
        from services.evidence_integrity_service import classify_hash, HASH_VERIFIED
        computed = hashlib.sha256(b"x").hexdigest()
        status, _ = classify_hash(f"  {computed.upper()}  ", computed)
        assert status == HASH_VERIFIED


class TestPostureDecisions:
    """Pins the product ruling, so a later refactor cannot quietly reverse it."""

    def test_only_infected_blocks_download(self):
        from services.evidence_integrity_service import is_download_blocked
        assert is_download_blocked("infected") is True
        for status in ("pending", "clean", "skipped", "scan_error", None):
            assert is_download_blocked(status) is False

    def test_pending_scan_still_counts_toward_posture(self):
        from services.evidence_integrity_service import counts_toward_posture
        assert counts_toward_posture("pending", "pending") is True

    def test_infected_and_mismatch_are_excluded_from_posture(self):
        from services.evidence_integrity_service import counts_toward_posture
        assert counts_toward_posture("infected", "verified") is False
        assert counts_toward_posture("clean", "mismatch") is False

    def test_a_hash_mismatch_is_still_downloadable(self):
        """A reviewer investigating the discrepancy has to be able to see it."""
        from services.evidence_integrity_service import is_download_blocked
        assert is_download_blocked("clean") is False

    def test_badge_reports_the_most_urgent_state_first(self):
        from services.evidence_integrity_service import integrity_badge
        assert integrity_badge("infected", "mismatch") == "infected"
        assert integrity_badge("clean", "mismatch") == "hash_mismatch"
        assert integrity_badge("clean", "unavailable") == "unreadable"
        assert integrity_badge("pending", "pending") == "not_yet_scanned"
        assert integrity_badge("clean", "verified") is None


# ---------------------------------------------------------------------------
# Quarantine + system audit
# ---------------------------------------------------------------------------

def _fake_file(**overrides):
    f = MagicMock()
    f.id = overrides.get("id", uuid4())
    f.organization_id = overrides.get("organization_id", uuid4())
    f.s3_key = overrides.get("s3_key", "evidence/org/2026/02/abc_report.pdf")
    f.filename = overrides.get("filename", "report.pdf")
    f.content_type = overrides.get("content_type", "application/pdf")
    f.sha256_hash = overrides.get("sha256_hash", None)
    f.scan_status = overrides.get("scan_status", "pending")
    f.hash_verification_status = overrides.get("hash_verification_status", "pending")
    f.is_deleted = overrides.get("is_deleted", False)
    return f


class TestQuarantine:
    def test_uses_the_key_storage_returned_not_a_reconstructed_one(self):
        """The inbox used to rebuild the destination key by hand.

        Nothing guaranteed the two agreed, so the database could name a location
        the object was not at.
        """
        from services.evidence_quarantine import quarantine_evidence_file

        session = MagicMock()
        f = _fake_file()
        with patch(
            "services.storage_service.move_to_quarantine",
            return_value="quarantine/org/the-real-key.pdf",
        ):
            quarantine_evidence_file(session, f)

        assert f.s3_key == "quarantine/org/the-real-key.pdf"

    def test_writes_an_audit_row(self):
        from services.evidence_quarantine import quarantine_evidence_file

        session = MagicMock()
        f = _fake_file()
        original = f.s3_key
        with patch("services.storage_service.move_to_quarantine", return_value="quarantine/k"):
            quarantine_evidence_file(session, f)

        session.add.assert_called_once()
        row = session.add.call_args.args[0]
        assert row.entity_type == "evidence_file"
        assert row.field_name == "s3_key"
        assert row.old_value == original
        assert row.new_value == "quarantine/k"
        assert row.action_source == "system"
        assert row.changed_by_user_id is None

    def test_a_failed_move_is_still_audited_and_leaves_the_key_alone(self):
        from services.evidence_quarantine import quarantine_evidence_file

        session = MagicMock()
        f = _fake_file()
        original = f.s3_key
        with patch(
            "services.storage_service.move_to_quarantine",
            side_effect=RuntimeError("bucket unreachable"),
        ):
            quarantine_evidence_file(session, f)

        assert f.s3_key == original
        session.add.assert_called_once()

    def test_enqueue_never_raises_when_the_broker_is_down(self):
        """An upload the user already completed must not fail on a broker outage."""
        from services.evidence_quarantine import enqueue_integrity_verification

        with patch.dict(sys.modules, {"tasks_evidence_integrity": None}):
            enqueue_integrity_verification(uuid4())  # must not raise


# ---------------------------------------------------------------------------
# The verification task
# ---------------------------------------------------------------------------

class _Session:
    """Minimal stand-in for the task's sync session."""

    def __init__(self, obj):
        self._obj = obj
        self.added = []
        self.committed = False
        self.rolled_back = False

    def execute(self, _stmt):
        result = MagicMock()
        result.scalar_one_or_none.return_value = self._obj
        return result

    def add(self, row):
        self.added.append(row)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass


def _scan(status="clean", details=None):
    from services.malware_scan_service import ScanResult
    return ScanResult(status=status, details=details or {})


class TestVerifyEvidenceFileTask:
    def _run(self, evidence_file, data, scan_status="clean"):
        import tasks_evidence_integrity as t

        session = _Session(evidence_file)
        with patch.object(t, "_get_sync_session", return_value=session), \
             patch("services.text_extraction_service.download_evidence_bytes", return_value=data), \
             patch.object(t, "_scan_bytes_sync", return_value=_scan(scan_status)):
            result = t.verify_evidence_file_task(str(evidence_file.id))
        return session, result

    def test_one_fetch_hashes_and_scans_the_same_bytes(self):
        """The single fetch is the point — two reads could describe two objects."""
        import tasks_evidence_integrity as t

        data = b"the actual evidence"
        f = _fake_file(sha256_hash=hashlib.sha256(data).hexdigest())
        session = _Session(f)

        with patch.object(t, "_get_sync_session", return_value=session), \
             patch(
                 "services.text_extraction_service.download_evidence_bytes",
                 return_value=data,
             ) as mock_fetch, \
             patch.object(t, "_scan_bytes_sync", return_value=_scan("clean")) as mock_scan:
            t.verify_evidence_file_task(str(f.id))

        mock_fetch.assert_called_once_with(f.s3_key)
        assert mock_scan.call_args.args[0] is data

    def test_records_the_server_computed_digest(self):
        data = b"payload"
        f = _fake_file(sha256_hash=hashlib.sha256(data).hexdigest())
        self._run(f, data)
        assert f.computed_sha256 == hashlib.sha256(data).hexdigest()
        assert f.hash_verification_status == "verified"
        assert f.hash_verified_at is not None

    def test_never_overwrites_the_client_assertion(self):
        """The claim is the only evidence that a mismatch was ever a mismatch."""
        data = b"payload"
        claimed = "b" * 64
        f = _fake_file(sha256_hash=claimed)
        self._run(f, data)
        assert f.sha256_hash == claimed
        assert f.computed_sha256 == hashlib.sha256(data).hexdigest()
        assert f.hash_verification_status == "mismatch"

    def test_populates_the_real_file_size(self):
        """confirm_upload hard-codes 0 with a comment promising a HEAD 'in future'."""
        data = b"0123456789"
        f = _fake_file()
        self._run(f, data)
        assert f.file_size_bytes == 10

    def test_unreadable_object_is_unavailable_not_pending(self):
        """'Looked at and could not be read' is a different fact from 'not looked at'."""
        f = _fake_file()
        session, result = self._run(f, None)
        assert f.hash_verification_status == "unavailable"
        assert result["status"] == "unavailable"
        assert session.added, "the unreadable verdict must leave an audit row"

    def test_infected_file_is_quarantined(self):
        import tasks_evidence_integrity as t

        f = _fake_file()
        session = _Session(f)
        with patch.object(t, "_get_sync_session", return_value=session), \
             patch("services.text_extraction_service.download_evidence_bytes", return_value=b"x"), \
             patch.object(t, "_scan_bytes_sync", return_value=_scan("infected")), \
             patch(
                 "services.storage_service.move_to_quarantine",
                 return_value="quarantine/org/x",
             ) as mock_move:
            t.verify_evidence_file_task(str(f.id))

        mock_move.assert_called_once()
        assert f.s3_key == "quarantine/org/x"
        assert f.scan_status == "infected"

    def test_a_scanner_failure_does_not_lose_the_digest(self):
        import tasks_evidence_integrity as t

        data = b"payload"
        f = _fake_file()
        session = _Session(f)
        with patch.object(t, "_get_sync_session", return_value=session), \
             patch("services.text_extraction_service.download_evidence_bytes", return_value=data), \
             patch.object(t, "_scan_bytes_sync", return_value=None):
            t.verify_evidence_file_task(str(f.id))

        assert f.scan_status == "scan_error"
        assert f.computed_sha256 == hashlib.sha256(data).hexdigest()

    def test_a_raising_scanner_is_absorbed_not_propagated(self):
        """The swallow lives inside `_scan_bytes_sync`, so exercise it there.

        The test above patches the helper out entirely, which proves the task
        handles a None verdict but says nothing about what happens when the
        scanner itself throws. If that exception escaped, the task would retry
        and eventually fail, discarding a digest it had already computed over
        bytes it had already paid to fetch.
        """
        import tasks_evidence_integrity as t

        data = b"payload"
        f = _fake_file()
        session = _Session(f)

        class _Boom:
            async def scan_bytes(self, **kwargs):
                raise RuntimeError("clamd unreachable")

        with patch.object(t, "_get_sync_session", return_value=session), \
             patch("services.text_extraction_service.download_evidence_bytes", return_value=data), \
             patch("services.malware_scan_service.get_scan_service", return_value=_Boom()):
            result = t.verify_evidence_file_task(str(f.id))

        assert result["status"] == "verified"
        assert f.scan_status == "scan_error"
        assert f.computed_sha256 == hashlib.sha256(data).hexdigest()

    def test_soft_deleted_files_are_skipped(self):
        f = _fake_file(is_deleted=True)
        _, result = self._run(f, b"x")
        assert result["status"] == "skipped_deleted"

    def test_missing_row_is_reported_not_raised(self):
        import tasks_evidence_integrity as t

        session = _Session(None)
        with patch.object(t, "_get_sync_session", return_value=session):
            result = t.verify_evidence_file_task(str(uuid4()))
        assert result["status"] == "not_found"


class TestSweepBatchSize:
    @pytest.mark.parametrize("value,expected", [
        (None, 50),
        ("10", 10),
        ("0", 1),
        ("100000", 500),
        ("not-a-number", 50),
    ])
    def test_batch_size_is_bounded(self, monkeypatch, value, expected):
        import tasks_evidence_integrity as t

        if value is None:
            monkeypatch.delenv("EVIDENCE_INTEGRITY_SWEEP_BATCH", raising=False)
        else:
            monkeypatch.setenv("EVIDENCE_INTEGRITY_SWEEP_BATCH", value)
        assert t._sweep_batch_size() == expected


# ---------------------------------------------------------------------------
# Queue routing
# ---------------------------------------------------------------------------

class TestQueueRouting:
    """A security control that runs in compose and is dead in production is worse
    than one that is visibly off: the dashboard would show scanning enabled while
    nothing was ever scanned.

    A worker started as a bare `celery -A celery_app worker` with no `-Q`
    consumes `default` and nothing else, so these tasks must route there.
    """

    def test_both_tasks_route_to_default(self):
        from celery_app import celery_app

        routes = celery_app.conf.task_routes
        assert routes["tasks_evidence_integrity.verify_evidence_file_task"]["queue"] == "default"
        assert routes["tasks_evidence_integrity.sweep_unverified_evidence_task"]["queue"] == "default"

    def test_the_task_module_is_registered_for_import(self):
        from celery_app import celery_app

        assert "tasks_evidence_integrity" in celery_app.conf.include
