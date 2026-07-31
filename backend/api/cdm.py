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
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from auth import OrgMembership, require_org_editor, require_org_viewer
from catalog_models import SCFCatalogAssessmentObjective, SCFCatalogControl
from database import get_db
from models import AuditLog, CDMControlProposal, CDMDocument, CDMMapping, ScopedControl
from schemas import (
    CDMComputeMappingsResponse,
    CDMComputeMappingsStatusResponse,
    CDMControlProposalListResponse,
    CDMControlProposalResponse,
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
    CDMReingestRequest,
    CDMReingestResponse,
    CDMUploadResponse,
)
from services import cdm_consolidation, cdm_mapping, cdm_retrieval, cdm_storage
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

# An in-flight ingest older than this is dead: the Celery hard time limit is
# 600s (celery_app.task_time_limit), plus grace for queue wait and clock skew.
_CDM_INGEST_STALE_AFTER_SECONDS = 700

_CDM_IN_FLIGHT_STATUSES = ("pending", "parsing", "indexing")
_CDM_RETRYABLE_STATUSES = ("failed", "indexing_failed")


def _ingest_is_stale(document: CDMDocument, now: datetime) -> bool:
    """True when the row claims to be in flight but its worker is gone."""
    if document.ingest_status not in ("parsing", "indexing"):
        return False
    started = document.ingest_started_at
    if started is None:
        return False
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return (now - started).total_seconds() > _CDM_INGEST_STALE_AFTER_SECONDS


logger = logging.getLogger(__name__)
router = APIRouter(tags=["cdm"])

ALLOWED_CDM_CONTENT_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
    "text/markdown",
}

# Ingest statuses that mark a prior upload as broken: an identical re-upload
# supersedes such a row (retry path) instead of being rejected as a duplicate.
REPLACEABLE_INGEST_STATUSES = {"failed", "indexing_failed"}


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

    sha256 = hashlib.sha256(payload).hexdigest()
    new_document_id = uuid4()
    # Case-insensitive, whitespace-tolerant filename identity: source
    # filesystems (macOS/Windows) are case-insensitive, so "Policy.PDF"
    # re-exported as "policy.pdf" is the same document to the user.
    normalized_filename = file.filename.strip().lower()

    # Duplicate / supersede handling. One org-scoped lookup classifies prior
    # uploads: identical healthy content is rejected with a clear message;
    # a changed file with the same name — or a failed prior attempt with
    # identical content — is superseded. Supersede = the documented
    # delete-only lifecycle (see delete_cdm_document) performed for the user
    # in one atomic step: audit rows, then row deletes (FK cascade removes
    # chunks, intents, and mappings), then the fresh insert, in one commit.
    # A healthy sha-sibling stored under a DIFFERENT filename survives a
    # supersede on this filename — it has independent identity to the user.
    existing_result = await db.execute(
        select(CDMDocument).where(
            CDMDocument.organization_id == org_id,
            or_(
                CDMDocument.sha256 == sha256,
                func.lower(func.trim(CDMDocument.original_filename))
                == normalized_filename,
            ),
        )
    )
    existing_docs = existing_result.scalars().all()

    duplicate = next(
        (
            doc
            for doc in existing_docs
            if doc.sha256 == sha256
            and doc.ingest_status not in REPLACEABLE_INGEST_STATUSES
        ),
        None,
    )
    if duplicate is not None:
        uploaded_at = (
            duplicate.created_at.date().isoformat()
            if duplicate.created_at
            else "an earlier date"
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"This document is already uploaded as "
                f"'{duplicate.original_filename}' (document {duplicate.id}, "
                f"uploaded {uploaded_at}). Upload a changed version under the "
                "same filename to replace it, or delete the existing document "
                "first."
            ),
        )

    predecessors = [
        doc
        for doc in existing_docs
        if (
            doc.original_filename.strip().lower() == normalized_filename
            and doc.sha256 != sha256
        )
        or (doc.sha256 == sha256 and doc.ingest_status in REPLACEABLE_INGEST_STATUSES)
    ]

    superseded_document_ids: list[UUID] = []
    superseded_mappings_removed = 0
    if predecessors:
        actor_user_id = _resolve_actor_user_id(membership)
        predecessor_ids = list(dict.fromkeys(doc.id for doc in predecessors))
        affected_result = await db.execute(
            select(
                CDMMapping.id,
                CDMMapping.status,
                CDMMapping.scoped_control_id,
                CDMMapping.cdm_document_id,
            ).where(
                CDMMapping.cdm_document_id.in_(predecessor_ids),
                CDMMapping.organization_id == org_id,
            )
        )
        affected_mappings = affected_result.all()
        now = datetime.now(timezone.utc)

        for mapping_id, mapping_status, scoped_control_id, mapping_doc_id in (
            affected_mappings
        ):
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
                            "cdm_document_id": str(mapping_doc_id),
                            "scoped_control_id": str(scoped_control_id),
                            "superseded_by_document_id": str(new_document_id),
                        }
                    ),
                    changed_by_user_id=actor_user_id,
                )
            )

        # #722: proposal rows go with the document via the same FK cascade —
        # audit them like their citations, or the cascade deletes silently.
        affected_proposals = (
            await db.execute(
                select(
                    CDMControlProposal.id,
                    CDMControlProposal.status,
                    CDMControlProposal.scoped_control_id,
                    CDMControlProposal.cdm_document_id,
                ).where(
                    CDMControlProposal.cdm_document_id.in_(predecessor_ids),
                    CDMControlProposal.organization_id == org_id,
                )
            )
        ).all()
        for proposal_id, proposal_status, scoped_control_id, proposal_doc_id in (
            affected_proposals
        ):
            db.add(
                AuditLog(
                    organization_id=org_id,
                    entity_type="cdm_control_proposal",
                    entity_id=proposal_id,
                    action="removed_with_document",
                    field_name="status",
                    old_value=proposal_status,
                    new_value=json.dumps(
                        {
                            "removed_at": now.isoformat(),
                            "cdm_document_id": str(proposal_doc_id),
                            "scoped_control_id": str(scoped_control_id),
                            "superseded_by_document_id": str(new_document_id),
                        }
                    ),
                    changed_by_user_id=actor_user_id,
                )
            )

        for doc in predecessors:
            doc_mappings_removed = sum(
                1 for row in affected_mappings if row[3] == doc.id
            )
            db.add(
                AuditLog(
                    organization_id=org_id,
                    entity_type="cdm_document",
                    entity_id=doc.id,
                    action="superseded",
                    field_name="ingest_status",
                    old_value=doc.ingest_status,
                    new_value=json.dumps(
                        {
                            "superseded_at": now.isoformat(),
                            "superseded_by_document_id": str(new_document_id),
                            "original_filename": doc.original_filename,
                            "sha256": doc.sha256,
                            "mappings_removed": doc_mappings_removed,
                        }
                    ),
                    changed_by_user_id=actor_user_id,
                )
            )

        # FK cascade removes chunks, intents, and mappings for these rows.
        await db.execute(
            delete(CDMDocument).where(
                CDMDocument.id.in_(predecessor_ids),
                CDMDocument.organization_id == org_id,
            )
        )
        superseded_document_ids = predecessor_ids
        superseded_mappings_removed = len(affected_mappings)

    # Slice 7 caps. Document-count cap is checked pre-insert; token cap is
    # checked against the current accumulator + a rough projection (bytes/6
    # ≈ words). True word_count lands during extraction; this is a soft
    # admission cap, not a billing-grade meter. Runs AFTER the supersede
    # deletes (same session/transaction) so replacing a document at the cap
    # works — the freed slots are visible to the cap queries.
    await assert_cdm_document_count_cap(db, org_id)
    projected_words = max(len(payload) // 6, 0)
    await assert_cdm_token_count_cap(db, org_id, projected_words)

    document = CDMDocument(
        id=new_document_id,
        organization_id=org_id,
        original_filename=file.filename,
        mime_type=content_type,
        sha256=sha256,
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
        superseded_document_ids=superseded_document_ids,
        superseded_mappings_removed=superseded_mappings_removed,
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

    now = datetime.now(timezone.utc)
    responses = []
    for document in documents:
        response = CDMDocumentResponse.model_validate(document)
        response.is_stale = _ingest_is_stale(document, now)
        responses.append(response)

    return CDMDocumentListResponse(documents=responses, total=total)


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

    # #722: proposal rows share the document's FK cascade — same audit
    # convention as their citations.
    affected_proposals = (
        await db.execute(
            select(
                CDMControlProposal.id,
                CDMControlProposal.status,
                CDMControlProposal.scoped_control_id,
            ).where(
                CDMControlProposal.cdm_document_id == document_id,
                CDMControlProposal.organization_id == org_id,
            )
        )
    ).all()
    for proposal_id, proposal_status, scoped_control_id in affected_proposals:
        db.add(
            AuditLog(
                organization_id=org_id,
                entity_type="cdm_control_proposal",
                entity_id=proposal_id,
                action="removed_with_document",
                field_name="status",
                old_value=proposal_status,
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
    # #722: a citation-level decision must not leave the parent card
    # contradicting its rows.
    await _rederive_parent_proposals(db, org_id, [mapping.control_proposal_id])
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
    await _rederive_parent_proposals(db, org_id, [mapping.control_proposal_id])
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

    # #722: one rederive per distinct parent across the whole batch.
    await _rederive_parent_proposals(
        db,
        org_id,
        [m.control_proposal_id for m in loaded.values()],
    )
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


async def _rederive_parent_proposals(
    db: AsyncSession, org_id: UUID, proposal_ids
) -> None:
    """Keep parent proposal statuses coherent after citation-level actions (#722).

    Same derivation rule as the consolidation pass (any accepted child →
    accepted; else any stale → stale; else all dismissed → dismissed; else
    proposed). Caller owns the commit — this runs inside the endpoint's
    transaction so the parent can never be observed contradicting its rows.
    """
    for proposal_id in {p for p in proposal_ids if p is not None}:
        current = (
            await db.execute(
                select(CDMControlProposal.status).where(
                    CDMControlProposal.id == proposal_id,
                    CDMControlProposal.organization_id == org_id,
                )
            )
        ).scalar_one_or_none()
        if current is None:
            continue
        children = (
            await db.execute(
                select(CDMMapping.status).where(
                    CDMMapping.control_proposal_id == proposal_id
                )
            )
        ).scalars().all()
        derived = cdm_consolidation.derive_proposal_status(children)
        if derived != current:
            await db.execute(
                update(CDMControlProposal)
                .where(CDMControlProposal.id == proposal_id)
                .values(status=derived, updated_at=datetime.now(timezone.utc))
            )


@router.get(
    "/organizations/{org_id}/cdm/proposals",
    response_model=CDMControlProposalListResponse,
)
async def list_cdm_control_proposals(
    org_id: UUID,
    _: None = Depends(require_tenant_cdm_enabled),
    membership: OrgMembership = Depends(require_org_viewer),
    db: AsyncSession = Depends(get_db),
    control_id: UUID | None = Query(None),
    document_id: UUID | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> CDMControlProposalListResponse:
    """List control-level proposals with nested citations (#722).

    Ordered by consolidated score descending — the queue's job is review
    priority, not recency. Citations are fetched with one IN query over the
    page, never per row.
    """
    query = (
        select(
            CDMControlProposal,
            ScopedControl.scf_id,
            SCFCatalogControl.control_name,
            CDMDocument.original_filename,
        )
        .join(ScopedControl, CDMControlProposal.scoped_control_id == ScopedControl.id)
        .outerjoin(SCFCatalogControl, ScopedControl.scf_id == SCFCatalogControl.scf_id)
        .join(CDMDocument, CDMControlProposal.cdm_document_id == CDMDocument.id)
        .where(CDMControlProposal.organization_id == org_id)
    )
    count_query = select(func.count(CDMControlProposal.id)).where(
        CDMControlProposal.organization_id == org_id
    )

    if control_id:
        query = query.where(CDMControlProposal.scoped_control_id == control_id)
        count_query = count_query.where(
            CDMControlProposal.scoped_control_id == control_id
        )
    if document_id:
        query = query.where(CDMControlProposal.cdm_document_id == document_id)
        count_query = count_query.where(
            CDMControlProposal.cdm_document_id == document_id
        )
    if status_filter:
        query = query.where(CDMControlProposal.status == status_filter)
        count_query = count_query.where(CDMControlProposal.status == status_filter)

    total = (await db.execute(count_query)).scalar() or 0

    query = (
        query.order_by(
            CDMControlProposal.consolidated_score.desc(),
            CDMControlProposal.created_at.desc(),
        )
        .offset(offset)
        .limit(limit)
    )
    rows = (await db.execute(query)).all()

    proposals: list[CDMControlProposalResponse] = []
    by_id: dict[UUID, CDMControlProposalResponse] = {}
    meta_by_id: dict[UUID, tuple] = {}
    for proposal, scf_id, control_name, original_filename in rows:
        response = CDMControlProposalResponse.model_validate(proposal)
        response.scf_id = scf_id
        response.control_name = control_name
        response.original_filename = original_filename
        proposals.append(response)
        by_id[proposal.id] = response
        meta_by_id[proposal.id] = (scf_id, original_filename)

    if by_id:
        citation_rows = (
            await db.execute(
                select(CDMMapping)
                .where(CDMMapping.control_proposal_id.in_(list(by_id.keys())))
                .order_by(CDMMapping.relevance_score.desc())
            )
        ).scalars().all()
        for mapping in citation_rows:
            citation = CDMMappingResponse.model_validate(mapping)
            scf_id, original_filename = meta_by_id[mapping.control_proposal_id]
            citation.scf_id = scf_id
            citation.original_filename = original_filename
            by_id[mapping.control_proposal_id].citations.append(citation)

    return CDMControlProposalListResponse(
        proposals=proposals,
        total=total,
        offset=offset,
        limit=limit,
    )


async def _load_proposal_for_transition(
    db: AsyncSession,
    org_id: UUID,
    proposal_id: UUID,
) -> CDMControlProposal:
    """Load a proposal scoped to org; 404 if missing or wrong tenant."""
    result = await db.execute(
        select(CDMControlProposal).where(
            CDMControlProposal.id == proposal_id,
            CDMControlProposal.organization_id == org_id,
        )
    )
    proposal = result.scalar_one_or_none()
    if proposal is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="CDM control proposal not found",
        )
    return proposal


@router.post("/organizations/{org_id}/cdm/proposals/{proposal_id}/accept")
async def accept_cdm_control_proposal(
    org_id: UUID,
    proposal_id: UUID,
    _: None = Depends(require_tenant_cdm_enabled),
    membership: OrgMembership = Depends(require_org_editor),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Accept a control proposal; cascade to its proposed citations (#722).

    One decision, one click: the proposal flips proposed → accepted with the
    same optimistic gate as mapping accept, and every still-proposed child
    citation follows in the same transaction, each with its own audit row —
    the per-citation audit trail stays whole even when the action happens at
    the parent level.
    """
    actor_user_id = _resolve_actor_user_id(membership)
    proposal = await _load_proposal_for_transition(db, org_id, proposal_id)

    if proposal.status != "proposed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Proposal is in state '{proposal.status}', not 'proposed'",
        )

    now = datetime.now(timezone.utc)
    result = await db.execute(
        update(CDMControlProposal)
        .where(
            CDMControlProposal.id == proposal_id,
            CDMControlProposal.organization_id == org_id,
            CDMControlProposal.status == "proposed",
        )
        .values(
            status="accepted",
            accepted_at=now,
            accepted_by_user_id=actor_user_id,
            updated_at=now,
        )
    )
    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Proposal is no longer in 'proposed' state",
        )

    child_rows = (
        await db.execute(
            select(CDMMapping.id, CDMMapping.kb_revision).where(
                CDMMapping.control_proposal_id == proposal_id,
                CDMMapping.status == "proposed",
            )
        )
    ).all()
    for mapping_id, mapping_kb_revision in child_rows:
        upd = await db.execute(
            update(CDMMapping)
            .where(
                CDMMapping.id == mapping_id,
                CDMMapping.status == "proposed",
            )
            .values(
                status="accepted",
                accepted_at=now,
                accepted_by_user_id=actor_user_id,
            )
        )
        if upd.rowcount == 0:
            continue
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
                        "kb_revision": mapping_kb_revision,
                        "accepted_at": now.isoformat(),
                        "via_control_proposal_id": str(proposal_id),
                    }
                ),
                changed_by_user_id=actor_user_id,
            )
        )

    db.add(
        AuditLog(
            organization_id=org_id,
            entity_type="cdm_control_proposal",
            entity_id=proposal_id,
            action="accept",
            field_name="status",
            old_value="proposed",
            new_value=json.dumps(
                {
                    "status": "accepted",
                    "kb_revision": proposal.kb_revision,
                    "accepted_at": now.isoformat(),
                    "citations_accepted": len(child_rows),
                }
            ),
            changed_by_user_id=actor_user_id,
        )
    )
    await db.commit()

    return {
        "proposal_id": str(proposal_id),
        "status": "accepted",
        "accepted_at": now.isoformat(),
        "accepted_by_user_id": str(actor_user_id),
        "citations_accepted": len(child_rows),
    }


@router.post("/organizations/{org_id}/cdm/proposals/{proposal_id}/dismiss")
async def dismiss_cdm_control_proposal(
    org_id: UUID,
    proposal_id: UUID,
    _: None = Depends(require_tenant_cdm_enabled),
    membership: OrgMembership = Depends(require_org_editor),
    db: AsyncSession = Depends(get_db),
    body: Optional[dict] = Body(default=None),
) -> dict:
    """Dismiss a control proposal; cascade to its proposed citations (#722).

    Optional ``reason`` is stored on the proposal and every dismissed
    citation, mirroring the single-mapping endpoint's body contract.
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

    proposal = await _load_proposal_for_transition(db, org_id, proposal_id)

    if proposal.status != "proposed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Proposal is in state '{proposal.status}', not 'proposed'",
        )

    now = datetime.now(timezone.utc)
    result = await db.execute(
        update(CDMControlProposal)
        .where(
            CDMControlProposal.id == proposal_id,
            CDMControlProposal.organization_id == org_id,
            CDMControlProposal.status == "proposed",
        )
        .values(
            status="dismissed",
            dismissed_at=now,
            dismissed_by_user_id=actor_user_id,
            dismiss_reason=reason,
            updated_at=now,
        )
    )
    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Proposal is no longer in 'proposed' state",
        )

    child_rows = (
        await db.execute(
            select(CDMMapping.id, CDMMapping.kb_revision).where(
                CDMMapping.control_proposal_id == proposal_id,
                CDMMapping.status == "proposed",
            )
        )
    ).all()
    for mapping_id, mapping_kb_revision in child_rows:
        upd = await db.execute(
            update(CDMMapping)
            .where(
                CDMMapping.id == mapping_id,
                CDMMapping.status == "proposed",
            )
            .values(
                status="dismissed",
                dismissed_at=now,
                dismissed_by_user_id=actor_user_id,
                dismiss_reason=reason,
            )
        )
        if upd.rowcount == 0:
            continue
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
                        "kb_revision": mapping_kb_revision,
                        "dismissed_at": now.isoformat(),
                        "reason": reason,
                        "via_control_proposal_id": str(proposal_id),
                    }
                ),
                changed_by_user_id=actor_user_id,
            )
        )

    db.add(
        AuditLog(
            organization_id=org_id,
            entity_type="cdm_control_proposal",
            entity_id=proposal_id,
            action="dismiss",
            field_name="status",
            old_value="proposed",
            new_value=json.dumps(
                {
                    "status": "dismissed",
                    "kb_revision": proposal.kb_revision,
                    "dismissed_at": now.isoformat(),
                    "reason": reason,
                    "citations_dismissed": len(child_rows),
                }
            ),
            changed_by_user_id=actor_user_id,
        )
    )
    await db.commit()

    return {
        "proposal_id": str(proposal_id),
        "status": "dismissed",
        "dismissed_at": now.isoformat(),
        "dismissed_by_user_id": str(actor_user_id),
        "reason": reason,
        "citations_dismissed": len(child_rows),
    }


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


@router.post(
    "/organizations/{org_id}/cdm/reingest",
    response_model=CDMReingestResponse,
)
async def reingest_cdm_documents(
    org_id: UUID,
    request: CDMReingestRequest = Body(default=CDMReingestRequest()),
    _: None = Depends(require_tenant_cdm_enabled),
    membership: OrgMembership = Depends(require_org_editor),
    db: AsyncSession = Depends(get_db),
) -> CDMReingestResponse:
    """Re-dispatch ingest for failed or stalled documents.

    Resets the existing row and re-runs ``cdm.ingest`` against the payload
    already in storage — no re-upload, no second row, so the per-checksum
    supersede invariant is untouched. Documents that are healthy or actively
    in flight are skipped, not errored: retry-all must be safe to click.
    """
    query = select(CDMDocument).where(CDMDocument.organization_id == org_id)
    if request.document_ids:
        query = query.where(CDMDocument.id.in_(request.document_ids))
    result = await db.execute(query)
    documents = result.scalars().all()

    found_ids = {document.id for document in documents}
    missing = [
        document_id
        for document_id in (request.document_ids or [])
        if document_id not in found_ids
    ]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"CDM document not found: {missing[0]}",
        )

    now = datetime.now(timezone.utc)
    actor_user_id = UUID(membership.user.db_id) if membership.user.db_id else None
    to_dispatch: list[CDMDocument] = []
    skipped: list[UUID] = []

    for document in documents:
        retryable = document.ingest_status in _CDM_RETRYABLE_STATUSES or _ingest_is_stale(
            document, now
        )
        if not retryable:
            skipped.append(document.id)
            continue
        db.add(
            AuditLog(
                organization_id=org_id,
                entity_type="cdm_document",
                entity_id=document.id,
                action="reingest",
                field_name="ingest_status",
                old_value=document.ingest_status,
                new_value=json.dumps(
                    {
                        "reingested_at": now.isoformat(),
                        "previous_error": (document.ingest_error or "")[:500] or None,
                    }
                ),
                changed_by_user_id=actor_user_id,
            )
        )
        document.ingest_status = "pending"
        document.ingest_error = None
        document.ingest_started_at = None
        to_dispatch.append(document)

    await db.commit()

    dispatched: list[UUID] = []
    for document in to_dispatch:
        try:
            ingest_cdm_document.delay(str(document.id))
            dispatched.append(document.id)
        except Exception as exc:
            document.ingest_status = "failed"
            document.ingest_error = f"Reingest enqueue failed: {str(exc)[:950]}"
            skipped.append(document.id)
            logger.exception("CDM reingest enqueue failed for %s", document.id)
    if len(dispatched) != len(to_dispatch):
        await db.commit()

    return CDMReingestResponse(
        dispatched_document_ids=dispatched,
        skipped_document_ids=skipped,
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

    backend = cdm_retrieval.get_retrieval_backend()
    if backend.name == cdm_retrieval.PostgresFTSBackend.name:
        return await _query_via_postgres_fts(
            db,
            org_id,
            backend,
            scf_id=control_row.scf_id,
            control_name=control_row.control_name,
            control_question=control_row.control_description,
            override_text=request.query_text,
            limit=request.limit,
        )

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

    return CDMQueryResponse(
        hits=hits,
        kb_revision=kb_revision,
        retrieval_tier=backend.name,
        can_produce_mappings=backend.can_produce_mappings,
        candidates_shown=len(hits),
        # LightRAG returns no pre-truncation total. Reporting len(hits) as the
        # total would assert coverage we cannot observe, so this stays null and
        # the UI omits the "of N" rather than inventing one.
        candidates_total=None,
        no_results_reason=(
            "no_matching_passages" if not hits else None
        ),
    )


async def _query_via_postgres_fts(
    db: AsyncSession,
    org_id: UUID,
    backend: "cdm_retrieval.PostgresFTSBackend",
    *,
    scf_id: Optional[str],
    control_name: Optional[str],
    control_question: Optional[str],
    override_text: Optional[str],
    limit: int,
) -> CDMQueryResponse:
    """Serve /cdm/query from Postgres FTS, synchronously.

    No Celery hop and no 504 path: this is one indexed statement against a
    table in the request's own database. v1 routed the same user action
    through a broker to a service the self-hosted stack does not run.
    """
    objectives: tuple[str, ...] = ()
    if override_text:
        # An explicit search box query is the user's words, not the control's.
        objectives = (override_text,)
    elif scf_id:
        objective_rows = await db.execute(
            select(SCFCatalogAssessmentObjective.objective_text)
            .where(SCFCatalogAssessmentObjective.scf_id == scf_id)
            .order_by(SCFCatalogAssessmentObjective.ao_id)
        )
        objectives = tuple(row[0] for row in objective_rows.all() if row[0])

    query = cdm_retrieval.ControlQuery(
        scf_id=scf_id,
        control_name=None if override_text else control_name,
        control_question=None if override_text else control_question,
        objectives=objectives,
    )

    built = backend.build_statement(org_id, query, limit=limit)
    if built is None:
        return CDMQueryResponse(
            hits=[],
            kb_revision=cdm_mapping.get_kb_revision(),
            retrieval_tier=backend.name,
            can_produce_mappings=backend.can_produce_mappings,
            candidates_shown=0,
            candidates_total=0,
            no_results_reason="control_has_no_query_text",
        )

    sql, params = built
    result = await db.execute(sql, params)
    chunks, total = backend.rows_to_chunks(result.mappings().all())

    hits = [
        {
            "chunk_id": str(chunk.chunk_id),
            "cdm_document_id": str(chunk.cdm_document_id),
            "ordinal": chunk.ordinal,
            "heading": chunk.heading,
            "content": chunk.body,
            "char_start": chunk.char_start,
            "char_end": chunk.char_end,
            "ts_rank": chunk.ts_rank,
            "matched_objectives": list(chunk.matched_objectives),
        }
        for chunk in chunks
    ]

    no_results_reason = None
    if not hits:
        # Which of the two zero states this is changes what the user should do,
        # so the distinction is resolved here rather than left to the UI.
        ingested = await db.execute(
            select(func.count())
            .select_from(CDMDocument)
            .where(
                CDMDocument.organization_id == org_id,
                # 'indexed' is the LightRAG-on terminal state; both mean
                # "text extracted and searchable".
                CDMDocument.ingest_status.in_(("parsed", "indexed")),
            )
        )
        no_results_reason = (
            "no_matching_passages" if (ingested.scalar() or 0) > 0 else "no_documents_ingested"
        )

    return CDMQueryResponse(
        hits=hits,
        kb_revision=cdm_mapping.get_kb_revision(),
        retrieval_tier=backend.name,
        can_produce_mappings=backend.can_produce_mappings,
        candidates_shown=len(hits),
        candidates_total=total,
        no_results_reason=no_results_reason,
    )


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


@router.post(
    "/organizations/{org_id}/cdm/backfill-chunks",
    response_model=CDMComputeMappingsResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def dispatch_cdm_backfill_chunks(
    org_id: UUID,
    _: None = Depends(require_tenant_cdm_enabled),
    membership: OrgMembership = Depends(require_org_editor),
    db: AsyncSession = Depends(get_db),
) -> CDMComputeMappingsResponse:
    """Chunk documents ingested before CDM v2 and clear stale proposals.

    Editor-gated because it discards ``proposed`` mappings. Accepted and
    dismissed rows are preserved by the task — a human decision outranks a
    maintenance job.
    """
    del membership, db

    try:
        async_result = tasks_cdm.backfill_chunks.apply_async(
            args=[str(org_id)],
            queue="cdm",
        )
    except Exception as exc:
        logger.exception("CDM backfill_chunks dispatch failed for %s", org_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to dispatch CDM backfill_chunks task",
        ) from exc

    return CDMComputeMappingsResponse(task_id=async_result.id, idempotent_existing=False)


@router.get(
    "/organizations/{org_id}/cdm/backfill-chunks/{task_id}",
    response_model=CDMComputeMappingsStatusResponse,
)
async def get_cdm_backfill_chunks_status(
    org_id: UUID,
    task_id: str,
    _: None = Depends(require_tenant_cdm_enabled),
    membership: OrgMembership = Depends(require_org_viewer),
) -> CDMComputeMappingsStatusResponse:
    del membership, org_id

    async_result = tasks_cdm.backfill_chunks.AsyncResult(task_id)
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
        state=async_result.state or "PENDING",
        ready=ready,
        successful=successful,
        result=result_payload,
    )
