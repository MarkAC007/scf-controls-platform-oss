"""
Celery tasks for server-side evidence integrity verification (#57).

Evidence reaches object storage without passing through the backend: the browser
uploads straight to S3/Azure with a presigned grant, and the confirm endpoint
only ever sees a key and a hash string the client chose. That design is right —
proxying gigabytes through uvicorn would be worse — but it means the platform
had never read a single byte of the evidence it was scoring, and had never
scanned a browser-uploaded file for malware at all.

This module closes that with **one deferred fetch per file** that does all of the
measuring at once:

    fetch bytes → SHA-256 → malware scan → size → persist

One fetch, not three. Egress on evidence stores is metered and the objects are
large; hashing and scanning from separate reads would double the bill to learn
two things about the same bytes, and would open a window in which the two
answers describe different objects.

Two entry points:

* `verify_evidence_file_task` — one file. Enqueued by the ingestion paths as
  soon as a record exists.
* `sweep_unverified_evidence_task` — the backlog. Every file that predates this
  feature starts at `hash_verification_status='pending'`; this beat task walks
  them oldest-first in bounded batches until there are none left. It is
  deliberately a background drain rather than a migration step: a tenant with
  tens of thousands of files would otherwise turn a deploy into a multi-hour
  egress job that cannot report progress or be resumed.

**Queue routing.** Both tasks route to `default`, and that is not laziness.
`default` is the one queue every Celery worker consumes out of the box, so
these tasks still run under a worker started as a bare
`celery -A celery_app worker` with no `-Q`. A dedicated `evidence_integrity`
queue would work with the stock compose `-Q` list and be silently dead under
any other worker invocation — which for a security control is the worst
possible failure mode, because the dashboard would show scanning enabled and
no file would ever be scanned.
"""
import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import UUID

from celery import shared_task
from sqlalchemy import create_engine, select, and_
from sqlalchemy.orm import sessionmaker

from services.evidence_integrity_service import (
    HASH_MISMATCH,
    HASH_PENDING,
    HASH_UNAVAILABLE,
    SCAN_INFECTED,
    classify_hash,
    compute_sha256,
)
from services.evidence_quarantine import quarantine_evidence_file, write_system_audit_row

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sync DB session (psycopg2 pattern from tasks_assessment.py)
# ---------------------------------------------------------------------------

_SYNC_DATABASE_URL = (
    os.getenv("DATABASE_URL", "postgresql+asyncpg://cg:cg@localhost:5432/cg_scf")
    .replace("+asyncpg", "+psycopg2")
    .replace("?ssl=require", "?sslmode=require")
)

_sync_engine = None
SyncSession = None


def _get_sync_session():
    global _sync_engine, SyncSession
    if SyncSession is None:
        _sync_engine = create_engine(
            _SYNC_DATABASE_URL,
            pool_pre_ping=True,
            pool_size=2,
            max_overflow=3,
        )
        SyncSession = sessionmaker(bind=_sync_engine, expire_on_commit=False)
    return SyncSession()


def _sweep_batch_size() -> int:
    """How many files one beat tick hands to the verifier.

    Read per call rather than captured at import so an operator can retune a
    running deployment by restarting beat alone.
    """
    try:
        size = int(os.getenv("EVIDENCE_INTEGRITY_SWEEP_BATCH", "50"))
    except ValueError:
        return 50
    return max(1, min(size, 500))


# ---------------------------------------------------------------------------
# The single fetch
# ---------------------------------------------------------------------------

def _scan_bytes_sync(data: bytes, filename: str, content_type: str):
    """Run the async scanner from sync Celery code.

    Returns the `ScanResult`, or None when the scanner itself failed — which is
    reported as a scan error rather than being allowed to fail the whole task
    and lose the hash we already computed.
    """
    from services.malware_scan_service import get_scan_service

    try:
        return asyncio.run(
            get_scan_service().scan_bytes(
                data=data,
                filename=filename,
                claimed_content_type=content_type or "",
            )
        )
    except Exception as exc:  # noqa: BLE001 — scanner failure must not lose the digest
        logger.error("Malware scan raised for %s: %s", filename, exc, exc_info=True)
        return None


@shared_task(
    bind=True,
    name="tasks_evidence_integrity.verify_evidence_file_task",
    time_limit=600,
    soft_time_limit=540,
    rate_limit="20/m",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 2},
)
def verify_evidence_file_task(self, evidence_file_id: str) -> Dict[str, Any]:
    """Fetch one stored evidence object once; hash it, scan it, record both.

    Idempotent: re-running re-measures and overwrites the server's own columns.
    It never touches `sha256_hash`, which is the uploader's claim and the only
    record that a mismatch was a mismatch.
    """
    from models import EvidenceFile
    from services.text_extraction_service import download_evidence_bytes

    session = _get_sync_session()
    try:
        evidence_file = session.execute(
            select(EvidenceFile).where(EvidenceFile.id == UUID(evidence_file_id))
        ).scalar_one_or_none()

        if evidence_file is None:
            logger.warning("Evidence file %s not found — nothing to verify", evidence_file_id)
            return {"status": "not_found", "evidence_file_id": evidence_file_id}

        if evidence_file.is_deleted:
            logger.info("Evidence file %s is soft-deleted — skipping verification", evidence_file_id)
            return {"status": "skipped_deleted", "evidence_file_id": evidence_file_id}

        # ---- the one fetch -------------------------------------------------
        data = download_evidence_bytes(evidence_file.s3_key)

        if data is None:
            # The record says there is an object; storage disagrees. That is
            # itself an integrity finding, and a distinct one from "not looked
            # at yet" — an auditor needs to be able to tell them apart.
            evidence_file.hash_verification_status = HASH_UNAVAILABLE
            evidence_file.hash_verified_at = datetime.utcnow()
            evidence_file.hash_verification_details = {
                "message": "Object could not be read from evidence storage.",
                "s3_key": evidence_file.s3_key,
            }
            write_system_audit_row(
                session,
                organization_id=evidence_file.organization_id,
                entity_id=evidence_file.id,
                action="update",
                field_name="hash_verification_status",
                old_value=HASH_PENDING,
                new_value=HASH_UNAVAILABLE,
            )
            session.commit()
            logger.error("Evidence object unreadable: %s", evidence_file.s3_key)
            return {"status": HASH_UNAVAILABLE, "evidence_file_id": evidence_file_id}

        computed = compute_sha256(data)
        hash_status, hash_details = classify_hash(evidence_file.sha256_hash, computed)
        scan_result = _scan_bytes_sync(data, evidence_file.filename, evidence_file.content_type)

        previous_hash_status = evidence_file.hash_verification_status
        previous_scan_status = evidence_file.scan_status

        # `sha256_hash` is untouched on purpose — see the module docstring.
        evidence_file.computed_sha256 = computed
        evidence_file.hash_verification_status = hash_status
        evidence_file.hash_verified_at = datetime.utcnow()
        evidence_file.hash_verification_details = hash_details

        # The size the confirm endpoint hard-coded to 0 with a comment promising
        # a HEAD request "in future". This is that future, and it costs nothing
        # extra because the bytes are already in hand.
        evidence_file.file_size_bytes = len(data)

        if scan_result is not None:
            evidence_file.scan_status = scan_result.status
            evidence_file.scan_details = scan_result.details
        else:
            evidence_file.scan_status = "scan_error"
            evidence_file.scan_details = {"message": "Scanner raised; see worker logs."}

        write_system_audit_row(
            session,
            organization_id=evidence_file.organization_id,
            entity_id=evidence_file.id,
            action="update",
            field_name="hash_verification_status",
            old_value=previous_hash_status,
            new_value=hash_status,
        )
        if evidence_file.scan_status != previous_scan_status:
            write_system_audit_row(
                session,
                organization_id=evidence_file.organization_id,
                entity_id=evidence_file.id,
                action="update",
                field_name="scan_status",
                old_value=previous_scan_status,
                new_value=evidence_file.scan_status,
            )

        if hash_status == HASH_MISMATCH:
            logger.error(
                "Evidence integrity mismatch on file %s (%s): stored object hashes to %s, "
                "upload asserted %s",
                evidence_file.id,
                evidence_file.s3_key,
                computed,
                hash_details.get("asserted_sha256"),
            )

        if evidence_file.scan_status == SCAN_INFECTED:
            quarantine_evidence_file(session, evidence_file)

        session.commit()
        return {
            "status": "verified",
            "evidence_file_id": evidence_file_id,
            "hash_verification_status": hash_status,
            "scan_status": evidence_file.scan_status,
            "file_size_bytes": evidence_file.file_size_bytes,
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Backlog drain
# ---------------------------------------------------------------------------

@shared_task(
    bind=True,
    name="tasks_evidence_integrity.sweep_unverified_evidence_task",
    time_limit=300,
    soft_time_limit=270,
)
def sweep_unverified_evidence_task(self, limit: Optional[int] = None) -> Dict[str, Any]:
    """Hand the oldest still-unverified files to the verifier, a batch at a time.

    Oldest-first because the files most likely to be relied on in an audit are
    the ones that have been sitting there longest. The batch size bounds how much
    egress a single tick can start; `verify_evidence_file_task` carries a
    `rate_limit` that bounds how fast the batch is actually consumed.
    """
    from models import EvidenceFile

    batch = _sweep_batch_size() if limit is None else max(1, int(limit))
    session = _get_sync_session()
    try:
        rows = session.execute(
            select(EvidenceFile.id)
            .where(
                and_(
                    EvidenceFile.is_deleted == False,  # noqa: E712 — SQL, not Python
                    EvidenceFile.hash_verification_status == HASH_PENDING,
                )
            )
            .order_by(EvidenceFile.uploaded_at.asc())
            .limit(batch)
        ).scalars().all()
    finally:
        session.close()

    for file_id in rows:
        verify_evidence_file_task.delay(str(file_id))

    if rows:
        logger.info("Evidence integrity sweep dispatched %s file(s) for verification", len(rows))
    return {"dispatched": len(rows), "batch_size": batch}
