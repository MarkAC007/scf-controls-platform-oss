"""Control Documentation Mapper (CDM) API."""
import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from celery.exceptions import TimeoutError as CeleryTimeoutError
from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from auth import OrgMembership, require_org_editor, require_org_viewer
from catalog_models import SCFCatalogControl
from database import get_db
from models import AuditLog, CDMDocument, CDMMapping, ScopedControl
from schemas import (
    CDMComputeMappingsResponse,
    CDMComputeMappingsStatusResponse,
    CDMDocumentListResponse,
    CDMDocumentResponse,
    CDMJobStatusResponse,
    CDMMappingBulkRequest,
    CDMMappingBulkResponse,
    CDMMappingDismissRequest,
    CDMMappingListResponse,
    CDMMappingResponse,
    CDMMappingReviewRequest,
    CDMQueryRequest,
    CDMQueryResponse,
    CDMUploadResponse,
)
from services import cdm_storage
from services.cdm_tenancy import (
    assert_cdm_document_count_cap,
    assert_cdm_proposed_mappings_cap,
    assert_cdm_token_count_cap,
    get_tenant_cdm_enabled,
    require_tenant_cdm_enabled,
)
import tasks_cdm
from tasks_cdm import CDMQueryTimeoutError, CDMQueryUpstreamError, ingest_cdm_document


_CDM_COMPUTE_LOCK_KEY_PREFIX = "cdm:compute_lock:"
_CDM_COMPUTE_LOCK_TTL_SECONDS = 900


logger = logging.getLogger(__name__)
router = APIRouter(tags=["cdm"])

ALLOWED_CDM_CONTENT_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
    "text/markdown",
}


def _derive_query_text(
    explicit_query_text: str | None,
    control_name: str | None,
    control_description: str | None,
) -> str:
    if explicit_query_text is not None:
        stripped_query_text = explicit_query_text.strip()
        if stripped_query_text:
            return stripped_query_text

    parts = []
    if control_name is not None:
        stripped_name = control_name.strip()
        if stripped_name:
            parts.append(stripped_name)
    if control_description is not None:
        stripped_description = control_description.strip()
        if stripped_description:
            parts.append(stripped_description)

    if not parts:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="query_text is required when the control has no catalog name or description",
        )

    return ". ".join(parts)[:1000]


def require_cdm_enabled() -> None:
    """Deprecated env-only gate. Retained to avoid breaking any external
    callers that still import this symbol; new routes should use
    ``require_tenant_cdm_enabled`` (per-tenant flag + env fallback)."""
    if os.getenv("ENABLE_CDM", "false").lower() != "true":
        raise HTTPException(status_code=404, detail="CDM module not enabled")


@router.post(
    "/organizations/{org_id}/cdm/upload",
    response_model=CDMUploadResponse,
)
async def upload_cdm_document(
    org_id: UUID,
    _: None = Depends(require_tenant_cdm_enabled),
    membership: OrgMembership = Depends(require_org_editor),
    db: AsyncSession = Depends(get_db),
    file: UploadFile = File(...),
) -> CDMUploadResponse:
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file must include a filename",
        )

    content_type = file.content_type or ""
    if content_type not in ALLOWED_CDM_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported CDM upload content type: {content_type or 'missing'}",
        )

    try:
        payload = await file.read()
    finally:
        await file.close()

    if not payload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty",
        )
    if len(payload) > cdm_storage.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Uploaded file exceeds CDM_MAX_UPLOAD_BYTES ({cdm_storage.MAX_UPLOAD_BYTES} bytes)",
        )

    # Slice 7 caps. Document-count cap is checked pre-insert; token cap is
    # checked against the current accumulator + a rough projection (bytes/6
    # ≈ words). True word_count lands during extraction; this is a soft
    # admission cap, not a billing-grade meter.
    await assert_cdm_document_count_cap(db, org_id)
    projected_words = max(len(payload) // 6, 0)
    await assert_cdm_token_count_cap(db, org_id, projected_words)

    document = CDMDocument(
        id=uuid4(),
        organization_id=org_id,
        original_filename=file.filename,
        mime_type=content_type,
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        upload_user_id=UUID(membership.user.db_id) if membership.user.db_id else None,
        ingest_status="pending",
        ingest_error=None,
    )
    db.add(document)
    await db.commit()
    await db.refresh(document)

    object_key = cdm_storage.build_cdm_object_key(org_id, document.id, file.filename)

    try:
        cdm_storage.write_cdm_payload(object_key, payload, str(org_id))
    except Exception as exc:
        document.ingest_status = "failed"
        document.ingest_error = f"Upload storage write failed: {str(exc)[:950]}"
        await db.commit()
        logger.exception("CDM upload storage write failed for %s", document.id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to store uploaded CDM payload",
        ) from exc

    try:
        ingest_cdm_document.delay(str(document.id))
    except Exception as exc:
        document.ingest_status = "failed"
        document.ingest_error = f"Ingest task enqueue failed: {str(exc)[:950]}"
        await db.commit()
        logger.exception("CDM ingest enqueue failed for %s", document.id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to enqueue CDM ingest task",
        ) from exc

    return CDMUploadResponse(
        document_id=document.id,
        ingest_status=document.ingest_status,
    )


@router.get(
    "/organizations/{org_id}/cdm/jobs/{document_id}",
    response_model=CDMJobStatusResponse,
)
async def get_cdm_job_status(
    org_id: UUID,
    document_id: UUID,
    _: None = Depends(require_tenant_cdm_enabled),
    membership: OrgMembership = Depends(require_org_viewer),
    db: AsyncSession = Depends(get_db),
) -> CDMJobStatusResponse:
    del membership

    result = await db.execute(
        select(CDMDocument).where(
            CDMDocument.id == document_id,
            CDMDocument.organization_id == org_id,
        )
    )
    document = result.scalar_one_or_none()
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="CDM document not found")

    return CDMJobStatusResponse(
        document_id=document.id,
        ingest_status=document.ingest_status,
        ingest_error=document.ingest_error,
        word_count=document.word_count,
    )


@router.get(
    "/organizations/{org_id}/cdm/documents",
    response_model=CDMDocumentListResponse,
)
async def list_cdm_documents(
    org_id: UUID,
    _: None = Depends(require_tenant_cdm_enabled),
    membership: OrgMembership = Depends(require_org_viewer),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> CDMDocumentListResponse:
    query = select(CDMDocument).where(CDMDocument.organization_id == org_id)
    count_query = select(func.count(CDMDocument.id)).where(CDMDocument.organization_id == org_id)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.order_by(CDMDocument.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    documents = result.scalars().all()

    return CDMDocumentListResponse(
        documents=[CDMDocumentResponse.model_validate(document) for document in documents],
        total=total,
    )


@router.delete(
    "/organizations/{org_id}/cdm/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_cdm_document(
    org_id: UUID,
    document_id: UUID,
    _: None = Depends(require_tenant_cdm_enabled),
    membership: OrgMembership = Depends(require_org_editor),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Remove a CDM document and cascade-delete its mappings.

    Lifecycle: delete-only. An "update" is delete + re-upload by the user —
    LightRAG indexing is content-addressed so an in-place rewrite would be
    delete+insert anyway, and the audit story stays cleaner when every
    document has exactly one creation event and (optionally) one deletion
    event.

    Cascade behaviour:
    - ``cdm_mappings.cdm_document_id`` has ``ON DELETE CASCADE`` so the
      mappings are removed at DB level. We still emit one audit_log row
      per affected mapping before the DELETE so the audit trail records
      *what* was removed and *why* (action = ``removed_with_document``).
    - One audit_log row for the document itself with action = ``deleted``.
    - LightRAG-side workspace cleanup is deferred (orphan ``file_source``
      entries are invisible to users; ``file_source`` is uuid-unique so
      re-uploads can't collide).
    """
    actor_user_id = _resolve_actor_user_id(membership)

    # Tenancy-checked load. 404 covers both "not found" and "wrong org" —
    # we never leak existence across tenants.
    doc_result = await db.execute(
        select(CDMDocument).where(
            CDMDocument.id == document_id,
            CDMDocument.organization_id == org_id,
        )
    )
    document = doc_result.scalar_one_or_none()
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="CDM document not found",
        )

    # Snapshot mapping ids + statuses BEFORE the cascade fires so the
    # audit ledger records what was actually removed.
    affected_result = await db.execute(
        select(CDMMapping.id, CDMMapping.status, CDMMapping.scoped_control_id).where(
            CDMMapping.cdm_document_id == document_id,
            CDMMapping.organization_id == org_id,
        )
    )
    affected_mappings = affected_result.all()

    now = datetime.now(timezone.utc)

    for mapping_id, mapping_status, scoped_control_id in affected_mappings:
        db.add(
            AuditLog(
                organization_id=org_id,
                entity_type="cdm_mapping",
                entity_id=mapping_id,
                action="removed_with_document",
                field_name="status",
                old_value=mapping_status,
                new_value=json.dumps(
                    {
                        "removed_at": now.isoformat(),
                        "cdm_document_id": str(document_id),
                        "scoped_control_id": str(scoped_control_id),
                    }
                ),
                changed_by_user_id=actor_user_id,
            )
        )

    db.add(
        AuditLog(
            organization_id=org_id,
            entity_type="cdm_document",
            entity_id=document_id,
            action="deleted",
            field_name="ingest_status",
            old_value=document.ingest_status,
            new_value=json.dumps(
                {
                    "deleted_at": now.isoformat(),
                    "original_filename": document.original_filename,
                    "sha256": document.sha256,
                    "mappings_removed": len(affected_mappings),
                }
            ),
            changed_by_user_id=actor_user_id,
        )
    )

    # FK cascade removes cdm_mappings rows automatically.
    await db.execute(
        delete(CDMDocument).where(
            CDMDocument.id == document_id,
            CDMDocument.organization_id == org_id,
        )
    )
    await db.commit()

    return None


@router.get(
    "/organizations/{org_id}/cdm/mappings",
    response_model=CDMMappingListResponse,
)
async def list_cdm_mappings(
    org_id: UUID,
    _: None = Depends(require_tenant_cdm_enabled),
    membership: OrgMembership = Depends(require_org_viewer),
    db: AsyncSession = Depends(get_db),
    control_id: UUID | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> CDMMappingListResponse:
    query = (
        select(CDMMapping, ScopedControl.scf_id, CDMDocument.original_filename)
        .join(ScopedControl, CDMMapping.scoped_control_id == ScopedControl.id)
        .join(CDMDocument, CDMMapping.cdm_document_id == CDMDocument.id)
        .where(CDMMapping.organization_id == org_id)
    )
    count_query = select(func.count(CDMMapping.id)).where(CDMMapping.organization_id == org_id)

    if control_id:
        query = query.where(CDMMapping.scoped_control_id == control_id)
        count_query = count_query.where(CDMMapping.scoped_control_id == control_id)
    if status:
        query = query.where(CDMMapping.status == status)
        count_query = count_query.where(CDMMapping.status == status)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.order_by(CDMMapping.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(query)

    mappings = []
    for mapping, scf_id, original_filename in result.all():
        mapping_response = CDMMappingResponse.model_validate(mapping)
        mapping_response.scf_id = scf_id
        mapping_response.original_filename = original_filename
        mappings.append(mapping_response)

    return CDMMappingListResponse(
        mappings=mappings,
        total=total,
        offset=offset,
        limit=limit,
    )


async def _load_mapping_for_transition(
    db: AsyncSession,
    org_id: UUID,
    mapping_id: UUID,
) -> CDMMapping:
    """Load a mapping scoped to org; 404 if missing or wrong tenant."""
    result = await db.execute(
        select(CDMMapping).where(
            CDMMapping.id == mapping_id,
            CDMMapping.organization_id == org_id,
        )
    )
    mapping = result.scalar_one_or_none()
    if mapping is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="CDM mapping not found",
        )
    return mapping


def _resolve_actor_user_id(membership: OrgMembership) -> UUID:
    """Resolve the caller's DB user UUID; 403 if missing (defensive)."""
    db_id = getattr(membership.user, "db_id", None)
    if not db_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Authenticated user has no database identity",
        )
    if isinstance(db_id, UUID):
        return db_id
    try:
        return UUID(str(db_id))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Authenticated user identity is malformed",
        ) from exc


@router.post("/organizations/{org_id}/cdm/mappings/{mapping_id}/accept")
async def accept_cdm_mapping(
    org_id: UUID,
    mapping_id: UUID,
    _: None = Depends(require_tenant_cdm_enabled),
    membership: OrgMembership = Depends(require_org_editor),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Transition a proposed mapping → accepted; write one audit row.

    D-1: optimistic concurrency via UPDATE WHERE status='proposed'. Race
    losers see ``rowcount=0`` and get a 409.
    D-2: audit row's ``new_value`` is a JSON blob carrying status + kb_revision
    so slice 6 can detect KB drift on re-ingest without joining back to the
    mapping row.
    """
    actor_user_id = _resolve_actor_user_id(membership)
    mapping = await _load_mapping_for_transition(db, org_id, mapping_id)

    if mapping.status != "proposed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Mapping is in state '{mapping.status}', not 'proposed'",
        )

    now = datetime.now(timezone.utc)
    update_stmt = (
        update(CDMMapping)
        .where(
            CDMMapping.id == mapping_id,
            CDMMapping.organization_id == org_id,
            CDMMapping.status == "proposed",
        )
        .values(
            status="accepted",
            accepted_at=now,
            accepted_by_user_id=actor_user_id,
        )
    )
    result = await db.execute(update_stmt)
    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Mapping is no longer in 'proposed' state",
        )

    db.add(
        AuditLog(
            organization_id=org_id,
            entity_type="cdm_mapping",
            entity_id=mapping_id,
            action="accept",
            field_name="status",
            old_value="proposed",
            new_value=json.dumps(
                {
                    "status": "accepted",
                    "kb_revision": mapping.kb_revision,
                    "accepted_at": now.isoformat(),
                }
            ),
            changed_by_user_id=actor_user_id,
        )
    )
    await db.commit()

    return {
        "mapping_id": str(mapping_id),
        "status": "accepted",
        "accepted_at": now.isoformat(),
        "accepted_by_user_id": str(actor_user_id),
    }


@router.post("/organizations/{org_id}/cdm/mappings/{mapping_id}/dismiss")
async def dismiss_cdm_mapping(
    org_id: UUID,
    mapping_id: UUID,
    _: None = Depends(require_tenant_cdm_enabled),
    membership: OrgMembership = Depends(require_org_editor),
    db: AsyncSession = Depends(get_db),
    body: Optional[dict] = Body(default=None),
) -> dict:
    """Transition a proposed mapping → dismissed; write one audit row.

    Optional ``reason`` body field is persisted to ``dismiss_reason``.
    Empty body is allowed (reason becomes NULL).
    """
    actor_user_id = _resolve_actor_user_id(membership)

    reason: Optional[str] = None
    if isinstance(body, dict):
        raw_reason = body.get("reason")
        if raw_reason is not None:
            if not isinstance(raw_reason, str):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="reason must be a string when provided",
                )
            stripped = raw_reason.strip()
            reason = stripped or None

    mapping = await _load_mapping_for_transition(db, org_id, mapping_id)

    if mapping.status != "proposed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Mapping is in state '{mapping.status}', not 'proposed'",
        )

    now = datetime.now(timezone.utc)
    update_stmt = (
        update(CDMMapping)
        .where(
            CDMMapping.id == mapping_id,
            CDMMapping.organization_id == org_id,
            CDMMapping.status == "proposed",
        )
        .values(
            status="dismissed",
            dismissed_at=now,
            dismissed_by_user_id=actor_user_id,
            dismiss_reason=reason,
        )
    )
    result = await db.execute(update_stmt)
    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Mapping is no longer in 'proposed' state",
        )

    db.add(
        AuditLog(
            organization_id=org_id,
            entity_type="cdm_mapping",
            entity_id=mapping_id,
            action="dismiss",
            field_name="status",
            old_value="proposed",
            new_value=json.dumps(
                {
                    "status": "dismissed",
                    "kb_revision": mapping.kb_revision,
                    "dismissed_at": now.isoformat(),
                    "reason": reason,
                }
            ),
            changed_by_user_id=actor_user_id,
        )
    )
    await db.commit()

    return {
        "mapping_id": str(mapping_id),
        "status": "dismissed",
        "dismissed_at": now.isoformat(),
        "dismissed_by_user_id": str(actor_user_id),
        "reason": reason,
    }


async def _bulk_transition(
    db: AsyncSession,
    org_id: UUID,
    actor_user_id: UUID,
    mapping_ids: list[UUID],
    target_status: str,
    reason: Optional[str],
) -> CDMMappingBulkResponse:
    """Shared core for bulk-accept and bulk-dismiss.

    Same invariants as the single-mapping endpoints:
    - tenancy-filtered (cross-tenant ids fall into ``not_found``)
    - optimistic UPDATE WHERE status='proposed' per row (race losers fall into ``skipped``)
    - one audit_log entry per successfully transitioned mapping
    """
    deduped_ids = list({mid for mid in mapping_ids})
    response = CDMMappingBulkResponse()
    if not deduped_ids:
        return response

    result = await db.execute(
        select(CDMMapping).where(
            CDMMapping.organization_id == org_id,
            CDMMapping.id.in_(deduped_ids),
        )
    )
    loaded = {m.id: m for m in result.scalars().all()}
    response.not_found = [mid for mid in deduped_ids if mid not in loaded]

    now = datetime.now(timezone.utc)
    for mid, mapping in loaded.items():
        if mapping.status != "proposed":
            response.skipped.append(mid)
            continue

        if target_status == "accepted":
            values = {
                "status": "accepted",
                "accepted_at": now,
                "accepted_by_user_id": actor_user_id,
            }
        else:
            values = {
                "status": "dismissed",
                "dismissed_at": now,
                "dismissed_by_user_id": actor_user_id,
                "dismiss_reason": reason,
            }

        update_stmt = (
            update(CDMMapping)
            .where(
                CDMMapping.id == mid,
                CDMMapping.organization_id == org_id,
                CDMMapping.status == "proposed",
            )
            .values(**values)
        )
        upd = await db.execute(update_stmt)
        if upd.rowcount == 0:
            response.skipped.append(mid)
            continue

        if target_status == "accepted":
            audit_payload = {
                "status": "accepted",
                "kb_revision": mapping.kb_revision,
                "accepted_at": now.isoformat(),
            }
            action = "accept"
        else:
            audit_payload = {
                "status": "dismissed",
                "kb_revision": mapping.kb_revision,
                "dismissed_at": now.isoformat(),
                "reason": reason,
            }
            action = "dismiss"

        db.add(
            AuditLog(
                organization_id=org_id,
                entity_type="cdm_mapping",
                entity_id=mid,
                action=action,
                field_name="status",
                old_value="proposed",
                new_value=json.dumps(audit_payload),
                changed_by_user_id=actor_user_id,
            )
        )

        if target_status == "accepted":
            response.accepted.append(mid)
        else:
            response.dismissed.append(mid)

    await db.commit()
    return response


@router.post(
    "/organizations/{org_id}/cdm/mappings/bulk-accept",
    response_model=CDMMappingBulkResponse,
)
async def bulk_accept_cdm_mappings(
    org_id: UUID,
    body: CDMMappingBulkRequest,
    _: None = Depends(require_tenant_cdm_enabled),
    membership: OrgMembership = Depends(require_org_editor),
    db: AsyncSession = Depends(get_db),
) -> CDMMappingBulkResponse:
    """Accept up to 200 proposed mappings in one transaction.

    Per-mapping race-safe: each row uses the same UPDATE WHERE status='proposed'
    gate as the single endpoint. Cross-tenant ids land in ``not_found``; already-
    accepted/dismissed ids land in ``skipped``. Never raises 409 on partial
    failure — the caller inspects the response to act on each list.
    """
    actor_user_id = _resolve_actor_user_id(membership)
    return await _bulk_transition(
        db, org_id, actor_user_id, body.mapping_ids, "accepted", None
    )


@router.post(
    "/organizations/{org_id}/cdm/mappings/bulk-dismiss",
    response_model=CDMMappingBulkResponse,
)
async def bulk_dismiss_cdm_mappings(
    org_id: UUID,
    body: CDMMappingBulkRequest,
    _: None = Depends(require_tenant_cdm_enabled),
    membership: OrgMembership = Depends(require_org_editor),
    db: AsyncSession = Depends(get_db),
) -> CDMMappingBulkResponse:
    """Dismiss up to 200 proposed mappings in one transaction.

    Optional ``reason`` is applied to every dismissed row. Same partial-success
    semantics as ``bulk-accept``.
    """
    actor_user_id = _resolve_actor_user_id(membership)
    reason: Optional[str] = None
    if body.reason is not None:
        stripped = body.reason.strip()
        reason = stripped or None
    return await _bulk_transition(
        db, org_id, actor_user_id, body.mapping_ids, "dismissed", reason
    )


@router.put("/organizations/{org_id}/cdm/mappings/{mapping_id}/review")
async def review_cdm_mapping(
    org_id: UUID,
    mapping_id: UUID,
    _: None = Depends(require_tenant_cdm_enabled),
    membership: OrgMembership = Depends(require_org_editor),
    db: AsyncSession = Depends(get_db),
    body: CDMMappingReviewRequest = Body(default_factory=CDMMappingReviewRequest),
) -> dict:
    """Record a terminology-alignment review on an accepted mapping.

    Slice 11. Two fields, one row:
    - ``notes``        — free-form reviewer text. Empty string clears.
    - ``mark_reviewed`` — when true, stamps ``last_reviewed_at = now()``
                         and ``last_reviewed_by_user_id = actor``.

    Tenancy: 404 (never 403) on cross-tenant mapping_id so existence
    never leaks. One ``audit_log`` row per write, ``action=review_noted``.
    """
    actor_user_id = _resolve_actor_user_id(membership)
    mapping = await _load_mapping_for_transition(db, org_id, mapping_id)

    notes_provided = body.notes is not None
    mark_reviewed = bool(body.mark_reviewed)

    if not notes_provided and not mark_reviewed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least one of notes or mark_reviewed must be provided",
        )

    old_notes = mapping.review_notes
    old_reviewed_at = mapping.last_reviewed_at

    new_notes: Optional[str] = old_notes
    if notes_provided:
        # Empty string → NULL (caller signalling "clear the note").
        stripped = (body.notes or "").strip()
        new_notes = stripped or None

    now = datetime.now(timezone.utc)
    new_reviewed_at = now if mark_reviewed else old_reviewed_at
    new_reviewer_id = actor_user_id if mark_reviewed else mapping.last_reviewed_by_user_id

    values: dict = {}
    if notes_provided:
        values["review_notes"] = new_notes
    if mark_reviewed:
        values["last_reviewed_at"] = now
        values["last_reviewed_by_user_id"] = actor_user_id

    if values:
        await db.execute(
            update(CDMMapping)
            .where(
                CDMMapping.id == mapping_id,
                CDMMapping.organization_id == org_id,
            )
            .values(**values)
        )

    db.add(
        AuditLog(
            organization_id=org_id,
            entity_type="cdm_mapping",
            entity_id=mapping_id,
            action="review_noted",
            field_name="review",
            old_value=json.dumps(
                {
                    "notes": old_notes,
                    "last_reviewed_at": old_reviewed_at.isoformat() if old_reviewed_at else None,
                }
            ),
            new_value=json.dumps(
                {
                    "notes": new_notes,
                    "last_reviewed_at": new_reviewed_at.isoformat() if new_reviewed_at else None,
                    "marked_reviewed": mark_reviewed,
                }
            ),
            changed_by_user_id=actor_user_id,
        )
    )
    await db.commit()

    return {
        "mapping_id": str(mapping_id),
        "review_notes": new_notes,
        "last_reviewed_at": new_reviewed_at.isoformat() if new_reviewed_at else None,
        "last_reviewed_by_user_id": str(new_reviewer_id) if new_reviewer_id else None,
    }


@router.post("/organizations/{org_id}/cdm/reingest")
async def reingest_cdm_documents(
    org_id: UUID,
    _: None = Depends(require_tenant_cdm_enabled),
    membership: OrgMembership = Depends(require_org_editor),
    db: AsyncSession = Depends(get_db),
) -> None:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Reingest endpoint pending — LightRAG revision workflow in next session",
    )


@router.post(
    "/organizations/{org_id}/cdm/query",
    response_model=CDMQueryResponse,
)
async def query_cdm_mappings(
    org_id: UUID,
    request: CDMQueryRequest,
    _: None = Depends(require_tenant_cdm_enabled),
    membership: OrgMembership = Depends(require_org_viewer),
    db: AsyncSession = Depends(get_db),
) -> CDMQueryResponse:
    del membership

    control_result = await db.execute(
        select(
            ScopedControl.id,
            ScopedControl.scf_id,
            SCFCatalogControl.control_name,
            SCFCatalogControl.control_description,
        )
        .outerjoin(SCFCatalogControl, ScopedControl.scf_id == SCFCatalogControl.scf_id)
        .where(
            ScopedControl.id == request.control_id,
            ScopedControl.organization_id == org_id,
        )
    )
    control_row = control_result.one_or_none()
    if control_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scoped control not found")

    query_text = _derive_query_text(
        request.query_text,
        control_row.control_name,
        control_row.control_description,
    )

    async_result = tasks_cdm.query_cdm.apply_async(
        args=[query_text, str(org_id), request.limit],
        queue="cdm",
    )

    try:
        result = await asyncio.to_thread(async_result.get, timeout=30, propagate=True)
    except CeleryTimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="LightRAG query timed out",
        ) from exc
    except CDMQueryTimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=str(exc) or "LightRAG query timed out",
        ) from exc
    except CDMQueryUpstreamError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc) or "LightRAG query failed",
        ) from exc

    if not isinstance(result, dict):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="CDM query task returned a non-object payload",
        )

    hits = result.get("hits")
    if not isinstance(hits, list):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="CDM query task payload missing list 'hits'",
        )

    kb_revision = result.get("kb_revision")
    if kb_revision is not None and not isinstance(kb_revision, str):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="CDM query task payload has invalid 'kb_revision'",
        )

    return CDMQueryResponse(hits=hits, kb_revision=kb_revision)


@router.post(
    "/organizations/{org_id}/cdm/compute-mappings",
    response_model=CDMComputeMappingsResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def dispatch_cdm_compute_mappings(
    org_id: UUID,
    _: None = Depends(require_tenant_cdm_enabled),
    membership: OrgMembership = Depends(require_org_editor),
    db: AsyncSession = Depends(get_db),
) -> CDMComputeMappingsResponse:
    """Dispatch the cdm.compute_mappings batch task for one org.

    Idempotent: if a per-org lock already holds an in-flight task_id, return
    that one with ``idempotent_existing=True`` instead of starting a new task.
    Lock TTL is 900s — task's ``finally`` block clears the lock so a re-run
    can be dispatched immediately after the task settles.
    """
    del membership

    # Slice 7 cap: refuse to start a new batch when the review queue is full.
    await assert_cdm_proposed_mappings_cap(db, org_id)

    lock_key = f"{_CDM_COMPUTE_LOCK_KEY_PREFIX}{org_id}"

    try:
        from redis_client import get_redis_client

        redis = await get_redis_client()
    except Exception:
        logger.exception("CDM compute_mappings: redis_client unavailable")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="CDM compute lock unavailable",
        )

    # SET NX with TTL — atomic insert-or-skip. If skipped, fetch the existing
    # task_id and return it.
    new_task_id = str(uuid4())
    acquired = await redis.set(
        lock_key,
        new_task_id,
        nx=True,
        ex=_CDM_COMPUTE_LOCK_TTL_SECONDS,
    )
    if not acquired:
        existing_task_id = await redis.get(lock_key)
        if existing_task_id:
            return CDMComputeMappingsResponse(
                task_id=str(existing_task_id),
                idempotent_existing=True,
            )
        # Lock vanished between SETNX and GET — fall through and retry once.
        acquired = await redis.set(
            lock_key,
            new_task_id,
            nx=True,
            ex=_CDM_COMPUTE_LOCK_TTL_SECONDS,
        )
        if not acquired:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="CDM compute lock contention — retry shortly",
            )

    # Dispatch with the exact task_id we just stamped into the lock, so the
    # task_id Mark polls matches the lock owner. apply_async accepts task_id.
    try:
        tasks_cdm.compute_mappings.apply_async(
            args=[str(org_id)],
            queue="cdm",
            task_id=new_task_id,
        )
    except Exception:
        # Release the lock so retries are possible.
        try:
            await redis.delete(lock_key)
        except Exception:
            logger.exception(
                "CDM compute_mappings: failed to release lock after dispatch error for %s",
                org_id,
            )
        logger.exception("CDM compute_mappings dispatch failed for %s", org_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to dispatch CDM compute_mappings task",
        )

    return CDMComputeMappingsResponse(task_id=new_task_id, idempotent_existing=False)


@router.get(
    "/organizations/{org_id}/cdm/compute-mappings/{task_id}",
    response_model=CDMComputeMappingsStatusResponse,
)
async def get_cdm_compute_mappings_status(
    org_id: UUID,
    task_id: str,
    _: None = Depends(require_tenant_cdm_enabled),
    membership: OrgMembership = Depends(require_org_viewer),
) -> CDMComputeMappingsStatusResponse:
    """Return Celery AsyncResult state for a prior compute_mappings dispatch."""
    del membership, org_id

    async_result = tasks_cdm.compute_mappings.AsyncResult(task_id)
    state = async_result.state or "PENDING"
    ready = bool(async_result.ready())

    successful: bool | None = None
    result_payload: dict | None = None
    if ready:
        successful = bool(async_result.successful())
        raw_result = async_result.result
        if isinstance(raw_result, dict):
            result_payload = raw_result
        elif raw_result is not None:
            result_payload = {"value": str(raw_result)[:1000]}

    return CDMComputeMappingsStatusResponse(
        task_id=task_id,
        state=state,
        ready=ready,
        successful=successful,
        result=result_payload,
    )
