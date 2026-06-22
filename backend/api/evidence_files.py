"""
Evidence Files API endpoints (Issues #324, #325, #482).
Handles pre-signed URL generation, file record tracking, lifecycle management,
and evidence file review/approval workflow.

Endpoints:
  POST   /organizations/{org_id}/evidence/{evidence_id}/files/upload-url  — Get pre-signed upload URL
  POST   /organizations/{org_id}/evidence/{evidence_id}/files/confirm     — Confirm upload, create record
  GET    /organizations/{org_id}/evidence/{evidence_id}/files             — List files for evidence item
  GET    /organizations/{org_id}/evidence/{evidence_id}/files/{file_id}   — Get single file metadata
  GET    /organizations/{org_id}/evidence/{evidence_id}/files/{file_id}/download — Stream file content
  DELETE /organizations/{org_id}/evidence/{evidence_id}/files/{file_id}   — Soft-delete a file
  PATCH  /organizations/{org_id}/evidence/{evidence_id}/files/{file_id}/review — Review a file
"""
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import JSONResponse, StreamingResponse
from uuid import UUID
from typing import List, Optional
import logging
import os

from sqlalchemy import select, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from auth import require_org_role, OrgMembership
from database import get_db
from models import EvidenceFile, EvidenceWindowAssessment
from schemas import (
    EvidenceFileUploadUrlRequest,
    EvidenceFileUploadUrlResponse,
    EvidenceFileConfirmRequest,
    EvidenceFileResponse,
    EvidenceFileListResponse,
    EvidenceFileReviewRequest,
)
from services.storage_service import (
    generate_upload_presigned_post,
    generate_download_url,
    tag_evidence_object,
    download_blob_stream,
    EVIDENCE_URL_EXPIRY,
)
from services.audit_service import (
    log_entity_changes,
    get_request_id,
    detect_action_source,
    EVIDENCE_FILE_TRACKED_FIELDS,
)
from services.validation_service import run_validation
from services.download_token import generate_download_token, verify_download_token

logger = logging.getLogger(__name__)

router = APIRouter(tags=["evidence_files"])


# ---------------------------------------------------------------------------
# Upload URL generation
# ---------------------------------------------------------------------------

@router.post(
    "/organizations/{org_id}/evidence/{evidence_id}/files/upload-url",
    response_model=EvidenceFileUploadUrlResponse,
    summary="Get pre-signed upload URL",
    description="Generate a pre-signed S3 POST URL for uploading evidence files. Validates content type against the allowlist and enforces a 50 MB size limit.",
)
async def get_upload_url(
    org_id: UUID,
    evidence_id: str,
    request: EvidenceFileUploadUrlRequest,
    membership: OrgMembership = Depends(require_org_role("editor")),
):
    """
    Generate a pre-signed POST URL for uploading evidence to S3.
    Requires: editor role or higher.

    Validates content_type against allowlist and file_size_bytes <= 50MB.
    Returns URL, form fields, and the S3 key needed for the confirm step.
    """
    try:
        result = generate_upload_presigned_post(
            org_id=str(org_id),
            filename=request.filename,
            content_type=request.content_type,
        )
        return EvidenceFileUploadUrlResponse(
            url=result["url"],
            fields=result["fields"],
            s3_key=result["object_key"],
            expires_in=EVIDENCE_URL_EXPIRY,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Failed to generate upload URL: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate upload URL")


# ---------------------------------------------------------------------------
# Confirm upload — creates EvidenceFile record
# ---------------------------------------------------------------------------

@router.post(
    "/organizations/{org_id}/evidence/{evidence_id}/files/confirm",
    response_model=EvidenceFileResponse,
    status_code=201,
    summary="Confirm evidence file upload",
    description="Confirm a successful S3 upload by creating an EvidenceFile metadata record. Tags the S3 object and triggers automatic validation.",
)
async def confirm_upload(
    org_id: UUID,
    evidence_id: str,
    request: EvidenceFileConfirmRequest,
    http_request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """
    Confirm a successful S3 upload by creating an EvidenceFile metadata record.
    Requires: editor role or higher.

    1. Tags the S3 object with org/evidence/user metadata.
    2. Creates the EvidenceFile database record.
    3. Logs to audit trail.
    """
    # Validate s3_key belongs to this org
    expected_prefix = f"evidence/{org_id}/"
    if not request.s3_key.startswith(expected_prefix):
        raise HTTPException(status_code=403, detail="S3 key does not belong to this organization")

    # Tag the S3 object (non-fatal — tagging is audit metadata, not critical)
    try:
        tag_evidence_object(
            file_key=request.s3_key,
            org_id=str(org_id),
            evidence_id=evidence_id,
            uploaded_by=str(UUID(membership.user.db_id)),
        )
    except Exception as e:
        logger.warning("Failed to tag S3 object %s: %s (non-fatal, continuing)", request.s3_key, e, exc_info=True)

    # Extract filename from s3_key (last segment after uuid prefix)
    key_filename = request.s3_key.rsplit("/", 1)[-1]
    # Remove the uuid12_ prefix to get original sanitized filename
    if "_" in key_filename:
        key_filename = key_filename.split("_", 1)[1]

    # Detect content_type from s3_key extension (fallback)
    content_type = _guess_content_type(key_filename)

    evidence_file = EvidenceFile(
        organization_id=org_id,
        evidence_id=evidence_id,
        filename=key_filename,
        s3_key=request.s3_key,
        content_type=content_type,
        file_size_bytes=0,  # Will be updated when S3 HEAD is called in future
        sha256_hash=request.sha256_hash,
        uploaded_by_user_id=UUID(membership.user.db_id),
    )
    db.add(evidence_file)
    await db.flush()

    # Run validation (never raises — results stored in DB)
    await run_validation(
        db=db,
        evidence_file=evidence_file,
        validation_source="manual_upload",
    )

    # Audit trail
    new_values = {f: getattr(evidence_file, f) for f in EVIDENCE_FILE_TRACKED_FIELDS}
    await log_entity_changes(
        db=db,
        organization_id=org_id,
        entity_type="evidence_file",
        entity_id=evidence_file.id,
        action="create",
        changed_by_user_id=UUID(membership.user.db_id),
        old_values={},
        new_values=new_values,
        tracked_fields=EVIDENCE_FILE_TRACKED_FIELDS,
        action_source=detect_action_source(http_request),
        request_id=get_request_id(http_request),
    )

    await db.commit()
    # Full refresh loads server defaults (uploaded_at) then relationship
    await db.refresh(evidence_file)
    await db.refresh(evidence_file, attribute_names=["uploaded_by"])

    # Generate proxy download URL for response
    download_url = _proxy_download_url(org_id, evidence_id, evidence_file.id)

    return _to_response(evidence_file, download_url)


# ---------------------------------------------------------------------------
# List files for an evidence item
# ---------------------------------------------------------------------------

@router.get(
    "/organizations/{org_id}/evidence/{evidence_id}/files",
    response_model=EvidenceFileListResponse,
    summary="List evidence files",
    description="List all non-deleted evidence files for an evidence item. Includes pre-signed download URLs with 15-minute expiry.",
)
async def list_evidence_files(
    org_id: UUID,
    evidence_id: str,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """
    List all non-deleted evidence files for an evidence item.
    Requires: viewer role or higher.
    Includes pre-signed download URLs (15-min expiry).
    """
    result = await db.execute(
        select(EvidenceFile)
        .options(joinedload(EvidenceFile.uploaded_by))
        .where(
            and_(
                EvidenceFile.organization_id == org_id,
                EvidenceFile.evidence_id == evidence_id,
                EvidenceFile.is_deleted == False,
            )
        )
        .order_by(EvidenceFile.uploaded_at.desc())
    )
    files = result.unique().scalars().all()

    file_responses = []
    for f in files:
        download_url = _proxy_download_url(org_id, evidence_id, f.id)
        file_responses.append(_to_response(f, download_url))

    return EvidenceFileListResponse(files=file_responses, total=len(file_responses))


# ---------------------------------------------------------------------------
# Get single file
# ---------------------------------------------------------------------------

@router.get(
    "/organizations/{org_id}/evidence/{evidence_id}/files/{file_id}",
    response_model=EvidenceFileResponse,
    summary="Get a single evidence file",
    description="Get metadata and a pre-signed download URL for a single evidence file. The download URL expires after 15 minutes.",
)
async def get_evidence_file(
    org_id: UUID,
    evidence_id: str,
    file_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """
    Get a single evidence file by ID.
    Requires: viewer role or higher.
    Returns metadata and a pre-signed download URL (15-min expiry).
    """
    result = await db.execute(
        select(EvidenceFile)
        .options(joinedload(EvidenceFile.uploaded_by))
        .where(
            and_(
                EvidenceFile.organization_id == org_id,
                EvidenceFile.evidence_id == evidence_id,
                EvidenceFile.id == file_id,
                EvidenceFile.is_deleted == False,
            )
        )
    )
    evidence_file = result.unique().scalar_one_or_none()

    if not evidence_file:
        raise HTTPException(status_code=404, detail="Evidence file not found")

    download_url = _proxy_download_url(org_id, evidence_id, file_id)
    return _to_response(evidence_file, download_url)


# ---------------------------------------------------------------------------
# Download proxy — streams blob content through the backend
# ---------------------------------------------------------------------------

@router.get(
    "/organizations/{org_id}/evidence/{evidence_id}/files/{file_id}/download",
    summary="Download an evidence file",
    description="Stream evidence file content. Auth: Bearer token (API) or signed URL params (browser).",
)
async def download_evidence_file(
    org_id: UUID,
    evidence_id: str,
    file_id: UUID,
    request: Request,
    disposition: str = "inline",
    token: Optional[str] = Query(None),
    expires: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Stream evidence file content through the backend.

    Two auth methods (at least one required):
    1. Bearer token (Authorization header) — for API/MCP clients
    2. Signed URL token (?token=...&expires=...) — for browser navigation
    """
    authed = False

    # Method 1: Bearer auth — extract credentials and delegate to require_org_role
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        try:
            from fastapi.security import HTTPAuthorizationCredentials
            bearer_token = auth_header[len("Bearer "):]
            creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=bearer_token)
            checker = require_org_role("viewer")
            await checker(org_id=org_id, credentials=creds, db=db)
            authed = True
        except Exception:
            pass

    # Method 2: Signed URL token
    if not authed and token and expires is not None:
        authed = verify_download_token(str(file_id), str(org_id), token, expires)

    if not authed:
        raise HTTPException(status_code=401, detail="Not authenticated")

    result = await db.execute(
        select(EvidenceFile).where(
            and_(
                EvidenceFile.organization_id == org_id,
                EvidenceFile.evidence_id == evidence_id,
                EvidenceFile.id == file_id,
                EvidenceFile.is_deleted == False,
            )
        )
    )
    evidence_file = result.scalar_one_or_none()

    if not evidence_file:
        raise HTTPException(status_code=404, detail="Evidence file not found")

    try:
        chunks = download_blob_stream(evidence_file.s3_key)
    except ValueError:
        raise HTTPException(status_code=503, detail="Evidence storage not configured")

    if chunks is None:
        raise HTTPException(status_code=404, detail="Evidence file not found in storage")

    disp = "attachment" if disposition == "attachment" else "inline"
    content_disposition = f'{disp}; filename="{evidence_file.filename}"'

    return StreamingResponse(
        chunks,
        media_type=evidence_file.content_type,
        headers={"Content-Disposition": content_disposition},
    )


# ---------------------------------------------------------------------------
# Soft delete
# ---------------------------------------------------------------------------

@router.delete(
    "/organizations/{org_id}/evidence/{evidence_id}/files/{file_id}",
    response_model=EvidenceFileResponse,
    summary="Soft-delete an evidence file",
    description="Soft-delete an evidence file. Marks the record as deleted but retains the S3 object for audit and retention requirements.",
)
async def delete_evidence_file(
    org_id: UUID,
    evidence_id: str,
    file_id: UUID,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """
    Soft-delete an evidence file.
    Requires: editor role or higher.

    Sets is_deleted=true, deleted_at, deleted_by_user_id.
    Does NOT delete from S3 (retention/audit requirements).
    """
    result = await db.execute(
        select(EvidenceFile)
        .options(joinedload(EvidenceFile.uploaded_by))
        .where(
            and_(
                EvidenceFile.organization_id == org_id,
                EvidenceFile.evidence_id == evidence_id,
                EvidenceFile.id == file_id,
            )
        )
    )
    evidence_file = result.unique().scalar_one_or_none()

    if not evidence_file:
        raise HTTPException(status_code=404, detail="Evidence file not found")

    if evidence_file.is_deleted:
        raise HTTPException(status_code=410, detail="Evidence file already deleted")

    # Capture before state
    old_values = {f: getattr(evidence_file, f) for f in EVIDENCE_FILE_TRACKED_FIELDS}

    # Soft delete
    evidence_file.is_deleted = True
    evidence_file.deleted_at = datetime.utcnow()
    evidence_file.deleted_by_user_id = UUID(membership.user.db_id)

    # Capture after state
    new_values = {f: getattr(evidence_file, f) for f in EVIDENCE_FILE_TRACKED_FIELDS}

    await log_entity_changes(
        db=db,
        organization_id=org_id,
        entity_type="evidence_file",
        entity_id=evidence_file.id,
        action="update",
        changed_by_user_id=UUID(membership.user.db_id),
        old_values=old_values,
        new_values=new_values,
        tracked_fields=EVIDENCE_FILE_TRACKED_FIELDS,
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )

    await db.commit()
    await db.refresh(evidence_file, attribute_names=["uploaded_by"])

    return _to_response(evidence_file, download_url=None)


# ---------------------------------------------------------------------------
# Review evidence file (#482)
# ---------------------------------------------------------------------------

@router.patch(
    "/organizations/{org_id}/evidence/{evidence_id}/files/{file_id}/review",
    response_model=EvidenceFileResponse,
    summary="Review an evidence file",
    description="Approve, reject, or request revision on an uploaded evidence file.",
)
async def review_evidence_file(
    org_id: UUID,
    evidence_id: str,
    file_id: UUID,
    body: EvidenceFileReviewRequest,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """
    Set the review status of an evidence file.
    Requires: editor role or higher.

    M4 PR 2 (#574 — ISC-23..26): when ``ENABLE_PER_WINDOW_REVIEW`` is on
    AND a window assessment exists for this org+evidence_id, the legacy
    per-file review path returns 410 Gone with a pointer to the new
    per-window endpoint. Tracking-only evidence with no window assessment
    falls through to the legacy 200 response, preserving backward
    compatibility for freshly added evidence IDs.
    """
    # ISC-23: lazy env read so monkeypatch works in tests.
    if os.getenv("ENABLE_PER_WINDOW_REVIEW", "false").lower() == "true":
        ewa_lookup = await db.execute(
            select(EvidenceWindowAssessment.id)
            .where(
                and_(
                    EvidenceWindowAssessment.organization_id == org_id,
                    EvidenceWindowAssessment.evidence_id == evidence_id,
                )
            )
            .order_by(desc(EvidenceWindowAssessment.window_end))
            .limit(1)
        )
        latest_ewa_id = ewa_lookup.scalar_one_or_none()
        if latest_ewa_id is not None:
            # ISC-24: 410 Gone with pointer payload + Sunset header (RFC 8594).
            payload = {
                "detail": (
                    "Per-file review has been replaced by per-window review "
                    "for this evidence object."
                ),
                "code": "PER_FILE_REVIEW_DEPRECATED",
                "evidence_id": evidence_id,
                "pointer": {
                    "method": "PUT",
                    "path": (
                        f"/api/organizations/{org_id}/window-assessments/"
                        f"{latest_ewa_id}/review"
                    ),
                    "latest_window_assessment_id": str(latest_ewa_id),
                },
            }
            return JSONResponse(
                status_code=410,
                content=payload,
                headers={"Sunset": "Sat, 09 May 2026 00:00:00 GMT"},
            )

    valid_statuses = {"approved", "rejected", "needs_revision"}
    if body.review_status not in valid_statuses:
        raise HTTPException(
            status_code=422,
            detail=f"review_status must be one of: {', '.join(sorted(valid_statuses))}",
        )

    result = await db.execute(
        select(EvidenceFile)
        .options(
            joinedload(EvidenceFile.uploaded_by),
            joinedload(EvidenceFile.reviewed_by),
        )
        .where(
            and_(
                EvidenceFile.organization_id == org_id,
                EvidenceFile.evidence_id == evidence_id,
                EvidenceFile.id == file_id,
                EvidenceFile.is_deleted == False,
            )
        )
    )
    evidence_file = result.unique().scalar_one_or_none()

    if not evidence_file:
        raise HTTPException(status_code=404, detail="Evidence file not found")

    old_values = {f: getattr(evidence_file, f) for f in EVIDENCE_FILE_TRACKED_FIELDS}

    evidence_file.review_status = body.review_status
    evidence_file.reviewed_by_user_id = UUID(membership.user.db_id)
    evidence_file.reviewed_at = datetime.utcnow()
    evidence_file.review_notes = body.review_notes

    new_values = {f: getattr(evidence_file, f) for f in EVIDENCE_FILE_TRACKED_FIELDS}

    await log_entity_changes(
        db=db,
        organization_id=org_id,
        entity_type="evidence_file",
        entity_id=evidence_file.id,
        action="update",
        changed_by_user_id=UUID(membership.user.db_id),
        old_values=old_values,
        new_values=new_values,
        tracked_fields=EVIDENCE_FILE_TRACKED_FIELDS,
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )

    await db.commit()
    await db.refresh(evidence_file, attribute_names=["uploaded_by", "reviewed_by"])

    download_url = _proxy_download_url(org_id, evidence_id, file_id)
    return _to_response(evidence_file, download_url=download_url)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_download_url(org_id: str, s3_key: str, filename: str) -> str | None:
    """Generate direct SAS/pre-signed download URL. Used as fallback only."""
    try:
        return generate_download_url(org_id=org_id, file_key=s3_key, filename=filename)
    except (ValueError, Exception):
        return None


def _proxy_download_url(org_id: UUID, evidence_id: str, file_id: UUID) -> str:
    """Generate a backend-proxied download URL with signed token.

    Includes HMAC token + expiry in query params so browsers can access
    the URL without sending Authorization headers (img src, iframe, window.open).
    """
    token, expires = generate_download_token(str(file_id), str(org_id))
    return f"/api/organizations/{org_id}/evidence/{evidence_id}/files/{file_id}/download?token={token}&expires={expires}"


def _to_response(evidence_file: EvidenceFile, download_url: str | None) -> EvidenceFileResponse:
    """Convert an EvidenceFile ORM instance to a response schema."""
    return EvidenceFileResponse(
        id=evidence_file.id,
        organization_id=evidence_file.organization_id,
        evidence_id=evidence_file.evidence_id,
        filename=evidence_file.filename,
        s3_key=evidence_file.s3_key,
        content_type=evidence_file.content_type,
        file_size_bytes=evidence_file.file_size_bytes,
        sha256_hash=evidence_file.sha256_hash,
        classification=evidence_file.classification,
        uploaded_by_user_id=evidence_file.uploaded_by_user_id,
        uploaded_at=evidence_file.uploaded_at,
        expires_at=evidence_file.expires_at,
        is_deleted=evidence_file.is_deleted,
        download_url=download_url,
        uploaded_by=evidence_file.uploaded_by,
        review_status=evidence_file.review_status,
        reviewed_by_user_id=evidence_file.reviewed_by_user_id,
        reviewed_at=evidence_file.reviewed_at,
        review_notes=evidence_file.review_notes,
        reviewed_by=getattr(evidence_file, 'reviewed_by', None),
    )


_EXTENSION_CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".zip": "application/zip",
    ".json": "application/json",
    ".txt": "text/plain",
    ".yml":  "text/yaml",
    ".yaml": "text/yaml",
}


def _guess_content_type(filename: str) -> str:
    """Guess content type from filename extension."""
    for ext, ct in _EXTENSION_CONTENT_TYPES.items():
        if filename.lower().endswith(ext):
            return ct
    return "application/octet-stream"
