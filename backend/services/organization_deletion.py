"""Delete an organisation and everything it owns — one implementation, two doors.

Both ``DELETE /api/organizations/{org_id}`` (an organisation admin removing
their own tenant) and ``DELETE /api/consultant/clients/{org_id}/organisation``
(a consultant removing a client from the portal) end up here. There is one
copy of the order of operations because the order is the whole point:

1. **Refuse while a storage copy is in flight.** A copy run is writing this
   organisation's bytes into a new store as we speak; deleting the rows under
   it leaves objects nobody can find. 409 until it finishes.
2. **Capture every object key before any row goes.** Every ``EvidenceFile``
   row — including soft-deleted ones, whose bytes are still in the bucket —
   with the storage config it was written under, plus the platform-store
   blobs (generated-document versions) the organisation left behind.
   (Catalogue import workbooks are platform-wide, not the organisation's,
   and stay.)
3. **Record the platform audit event.** The organisation's own ``audit_log``
   rows go with it (ON DELETE CASCADE, and the append-only trigger only lets
   them go once the organisation has), so the record that it was deleted, and
   by whom, has to live in the platform-scoped table.
4. **Delete the ``evidence_files`` rows explicitly, then the organisation,
   then COMMIT.** ``evidence_files.storage_config_id`` is ON DELETE RESTRICT
   against ``evidence_storage_configs``, which cascades from the organisation;
   taking the files out first is what keeps the database's own cascade from
   tripping over that guard. The organisation row goes through the ORM so
   every relationship declared on ``Organization`` — and every database
   cascade behind it — fires exactly as it always has. This function owns the
   commit: the audit row and the delete land together or not at all, and
   nothing irreversible has happened outside the database yet.
5. **Only then delete the stored bytes, per file, from wherever they are.**
   Each captured key resolves its own store through
   ``storage_service.delete_evidence_object``, because a file written before a
   store switch lives in the old store, not the one the organisation writes to
   now. The order matters: if the transaction had failed after a sweep, the
   tenant would have kept an organisation whose every file 404s. Sweeping
   after the commit means a failure here leaves orphaned bytes — recoverable,
   and recorded — rather than a broken tenant. Best-effort per object: a
   failed delete is logged, counted, and written as a second platform audit
   event (``storage_orphaned``) so the erasure gap is durable, not a log line.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple
from uuid import UUID

from fastapi import Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import DocumentVersion, EvidenceFile, GeneratedDocument, Organization
from services import storage_service
from services.audit_service import detect_action_source, get_client_ip, get_request_id
from services.platform_audit import record_platform_event
from tasks_evidence_storage_copy import active_run_id

logger = logging.getLogger(__name__)

#: Platform audit action written when the row delete committed but one or
#: more objects could not be removed from storage.
STORAGE_ORPHANED_ACTION = "storage_orphaned"


class OrganizationDeleteConflict(Exception):
    """The organisation cannot be deleted right now; the message says why."""


class OrganizationNameMismatch(Exception):
    """``confirm_name`` did not match the organisation's current name."""


@dataclass
class OrganizationDeleteResult:
    organization_id: UUID
    organization_name: str
    evidence_files_deleted: int
    storage_objects_failed: int
    #: Keys that are still in a bucket after the rows are gone.
    orphaned_keys: List[str] = field(default_factory=list)


async def _delete_stored_bytes(organization_id: UUID, files: Sequence[Tuple]) -> List[str]:
    """Delete every file's object from the store it was written to. Returns the keys that failed."""
    failed: List[str] = []
    for file_id, s3_key, storage_config_id in files:
        try:
            await asyncio.to_thread(
                storage_service.delete_evidence_object,
                s3_key,
                str(organization_id),
                str(storage_config_id) if storage_config_id else None,
            )
        except Exception as exc:  # noqa: BLE001 — best-effort by design, see module docstring
            failed.append(s3_key)
            logger.warning(
                "Organisation delete: could not remove evidence object %s (file %s, org %s): %s",
                s3_key, file_id, organization_id, exc,
            )
    return failed


async def _delete_platform_objects(organization_id: UUID, keys: Sequence[str]) -> List[str]:
    """Delete platform-store objects (document version blobs). Returns the keys that failed."""
    failed: List[str] = []
    for key in keys:
        if not key:
            continue
        try:
            await asyncio.to_thread(storage_service.delete_object, key)
        except Exception as exc:  # noqa: BLE001 — best-effort by design
            failed.append(key)
            logger.warning(
                "Organisation delete: could not remove platform object %s (org %s): %s",
                key, organization_id, exc,
            )
    return failed


def _request_context(request: Optional[Request]) -> dict:
    if request is None:
        return {"ip_address": None, "user_agent": None, "action_source": None, "request_id": None}
    return {
        "ip_address": get_client_ip(request),
        "user_agent": request.headers.get("user-agent"),
        "action_source": detect_action_source(request),
        "request_id": get_request_id(request),
    }


async def delete_organization_completely(
    db: AsyncSession,
    organization: Organization,
    *,
    confirm_name: str,
    actor_email: str,
    actor_user_id: Optional[UUID],
    request: Optional[Request] = None,
) -> OrganizationDeleteResult:
    """Remove an organisation and all of its data. See the module docstring for the order.

    Commits. By the time this returns the organisation row is gone; what may
    remain is a set of storage objects listed in ``orphaned_keys``.

    Raises:
        OrganizationNameMismatch: ``confirm_name`` is not the organisation's name.
        OrganizationDeleteConflict: a storage copy run is in flight.
    """
    if confirm_name != organization.name:
        raise OrganizationNameMismatch(
            "confirm_name does not match the organisation name. "
            "Type the organisation's name exactly as shown to confirm deletion."
        )

    org_id = organization.id
    org_name = organization.name

    in_flight = await asyncio.to_thread(active_run_id, str(org_id))
    if in_flight:
        raise OrganizationDeleteConflict(
            f"An evidence storage copy (run {in_flight}) is in progress for this "
            "organisation. Wait for it to finish, then try again."
        )

    # 2. Capture keys. Every file, soft-deleted or not: the bytes are still
    #    there either way, and the rows are about to go.
    files_result = await db.execute(
        select(EvidenceFile.id, EvidenceFile.s3_key, EvidenceFile.storage_config_id)
        .where(EvidenceFile.organization_id == org_id)
    )
    files = files_result.all()

    blobs_result = await db.execute(
        select(DocumentVersion.blob_key)
        .join(GeneratedDocument, DocumentVersion.document_id == GeneratedDocument.id)
        .where(GeneratedDocument.organization_id == org_id)
        .where(DocumentVersion.blob_key.isnot(None))
    )
    platform_keys = [key for (key,) in blobs_result.all() if key]

    context = _request_context(request)

    # 3. The record that survives the organisation.
    await record_platform_event(
        db,
        entity_type="organization",
        entity_id=str(org_id),
        action="delete",
        actor=actor_email,
        actor_user_id=actor_user_id,
        **context,
    )

    # 4. Files first (RESTRICT guard), then the organisation and its cascades,
    #    then commit — nothing outside the database has been touched yet.
    await db.execute(delete(EvidenceFile).where(EvidenceFile.organization_id == org_id))
    await db.delete(organization)
    await db.commit()

    logger.info(
        "Organisation %s (%s) rows deleted by %s; sweeping %d evidence objects and %d platform objects",
        org_id, org_name, actor_email, len(files), len(platform_keys),
    )

    # 5. Now the bytes, from the captured keys.
    orphaned = await _delete_stored_bytes(org_id, files)
    orphaned += await _delete_platform_objects(org_id, platform_keys)

    if orphaned:
        logger.error(
            "Organisation %s (%s) deleted but %d storage objects remain: %s",
            org_id, org_name, len(orphaned), orphaned,
        )
        await record_platform_event(
            db,
            entity_type="organization",
            entity_id=str(org_id),
            action=STORAGE_ORPHANED_ACTION,
            actor=actor_email,
            actor_user_id=actor_user_id,
            **context,
        )
        await db.commit()

    return OrganizationDeleteResult(
        organization_id=org_id,
        organization_name=org_name,
        evidence_files_deleted=len(files),
        storage_objects_failed=len(orphaned),
        orphaned_keys=orphaned,
    )
