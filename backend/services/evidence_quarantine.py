"""
Quarantine, system-actor audit, and deferred-verification dispatch (#57).

The side-effecting helpers that both evidence ingestion paths need. Kept apart
from `evidence_integrity_service`, which holds the decisions and stays free of
storage, database and broker so it can be tested on its own.

Both ingestion paths can discover malware, and until now only one of them acted
on it — the webhook inbox — while the browser upload path was never scanned at
all. Even the inbox's handling had two defects worth pulling out into one place:

* It wrote **no audit row**. Moving a customer's evidence to a quarantine prefix
  is one of the most consequential things the platform does to a file without
  being asked, and it left no trace anyone could query.
* It rebuilt the destination key by hand —
  `f"quarantine/{org_id}/{file.id}_{filename}"` — instead of using the key
  `move_to_quarantine` actually returned. Any divergence between the two, now or
  after a change to the storage layer, leaves the database naming a location the
  object is not at.

Both callers use these helpers so the behaviour cannot drift apart again.

The functions take a *session* rather than an `AsyncSession` or a sync `Session`
specifically: `Session.add()` has the same synchronous signature on both, and the
callers are one of each — the API path is async, the Celery worker is not.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def write_system_audit_row(
    session,
    *,
    organization_id,
    entity_id,
    action: str,
    field_name: Optional[str] = None,
    old_value: Optional[str] = None,
    new_value: Optional[str] = None,
    entity_type: str = "evidence_file",
) -> None:
    """Append one audit row attributed to the platform itself.

    `services.audit_service` is async and awaits a flush; a Celery worker has no
    event loop to await in. The row is constructed directly instead.
    `changed_by_user_id` is NULL — no human took this action — and
    `action_source='system'` is what distinguishes it from an unattributed user
    action rather than leaving a reader to guess.
    """
    from models import AuditLog

    session.add(
        AuditLog(
            organization_id=organization_id,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            field_name=field_name,
            old_value=old_value,
            new_value=new_value,
            changed_by_user_id=None,
            action_source="system",
        )
    )


def quarantine_evidence_file(session, evidence_file) -> None:
    """Move an infected object out of the evidence prefix and record the move.

    Mutates `evidence_file.s3_key` to whatever the storage layer reports as the
    new location, and appends the audit row. A move that fails is still audited,
    with the key unchanged, so the attempt is not lost.
    """
    from services.storage_service import move_to_quarantine

    original_key = evidence_file.s3_key
    try:
        new_key = move_to_quarantine(original_key, str(evidence_file.organization_id))
    except Exception as exc:  # noqa: BLE001 — a failed move must still be recorded
        logger.error("Quarantine move failed for %s: %s", original_key, exc, exc_info=True)
        write_system_audit_row(
            session,
            organization_id=evidence_file.organization_id,
            entity_id=evidence_file.id,
            action="update",
            field_name="s3_key",
            old_value=original_key,
            new_value=original_key,
        )
        return

    if new_key:
        evidence_file.s3_key = new_key

    logger.warning(
        "Infected evidence quarantined: file=%s org=%s %s -> %s",
        evidence_file.id,
        evidence_file.organization_id,
        original_key,
        evidence_file.s3_key,
    )
    write_system_audit_row(
        session,
        organization_id=evidence_file.organization_id,
        entity_id=evidence_file.id,
        action="update",
        field_name="s3_key",
        old_value=original_key,
        new_value=evidence_file.s3_key,
    )


def enqueue_integrity_verification(evidence_file_id) -> None:
    """Queue the hash-and-scan pass for a newly created file record.

    Never raises. A broker outage must not fail an ingestion the caller already
    completed — the row stays at `hash_verification_status='pending'` and the
    hourly backlog sweep collects it, which is exactly the state the sweep
    exists to drain. Imported lazily so importing this module does not drag in
    Celery.
    """
    try:
        from tasks_evidence_integrity import verify_evidence_file_task

        verify_evidence_file_task.delay(str(evidence_file_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Could not enqueue integrity verification for %s: %s — the backlog sweep will retry",
            evidence_file_id,
            exc,
        )
