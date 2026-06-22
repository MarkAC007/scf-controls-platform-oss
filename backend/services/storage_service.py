"""
Evidence Storage Service — unified facade for S3 and Azure Blob Storage.

Auto-detects the storage backend from environment variables:
- If AZURE_STORAGE_ACCOUNT_NAME is set → Azure Blob Storage
- Else if EVIDENCE_BUCKET is set → AWS S3
- Else → not configured

All callers should import from this module instead of s3_service directly.
"""
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Re-export constants that callers need regardless of backend
from services.s3_service import ALLOWED_CONTENT_TYPES  # noqa: F401

EVIDENCE_URL_EXPIRY = int(os.getenv("EVIDENCE_URL_EXPIRY", "900"))

# ---------------------------------------------------------------------------
# Backend detection
# ---------------------------------------------------------------------------

_BACKEND: Optional[str] = None


def _detect_backend() -> str:
    """Detect which storage backend is configured."""
    global _BACKEND
    if _BACKEND is not None:
        return _BACKEND

    if os.getenv("AZURE_STORAGE_ACCOUNT_NAME"):
        _BACKEND = "azure"
        logger.info("Storage backend: Azure Blob Storage")
    elif os.getenv("EVIDENCE_BUCKET"):
        _BACKEND = "s3"
        logger.info("Storage backend: AWS S3")
    else:
        _BACKEND = "none"
        logger.warning("No storage backend configured (neither AZURE_STORAGE_ACCOUNT_NAME nor EVIDENCE_BUCKET set)")

    return _BACKEND


def get_backend() -> str:
    """Return the detected backend name: 'azure', 's3', or 'none'."""
    return _detect_backend()


def is_configured() -> bool:
    """Check if any evidence storage backend is configured and ready."""
    backend = _detect_backend()
    if backend == "azure":
        from services.azure_blob_service import is_configured as azure_configured
        return azure_configured()
    elif backend == "s3":
        from services.s3_service import EVIDENCE_BUCKET
        return bool(EVIDENCE_BUCKET)
    return False


# ---------------------------------------------------------------------------
# Public API — delegates to the active backend
# ---------------------------------------------------------------------------

def generate_upload_presigned_post(
    org_id: str,
    filename: str,
    content_type: str,
) -> dict:
    """Generate a pre-signed upload URL (S3 POST or Azure SAS)."""
    backend = _detect_backend()
    if backend == "azure":
        from services.azure_blob_service import generate_upload_presigned_post as azure_fn
        return azure_fn(org_id, filename, content_type)
    elif backend == "s3":
        from services.s3_service import generate_upload_presigned_post as s3_fn
        return s3_fn(org_id, filename, content_type)
    raise ValueError("Evidence storage not configured")


def generate_download_url(
    org_id: str,
    file_key: str,
    filename: Optional[str] = None,
) -> str:
    """Generate a pre-signed download URL."""
    backend = _detect_backend()
    if backend == "azure":
        from services.azure_blob_service import generate_download_url as azure_fn
        return azure_fn(org_id, file_key, filename)
    elif backend == "s3":
        from services.s3_service import generate_download_url as s3_fn
        return s3_fn(org_id, file_key, filename)
    raise ValueError("Evidence storage not configured")


def tag_evidence_object(
    file_key: str,
    org_id: str,
    evidence_id: Optional[str] = None,
    uploaded_by: Optional[str] = None,
) -> dict:
    """Apply metadata/tags to an uploaded evidence file."""
    backend = _detect_backend()
    if backend == "azure":
        from services.azure_blob_service import tag_evidence_object as azure_fn
        return azure_fn(file_key, org_id, evidence_id, uploaded_by)
    elif backend == "s3":
        from services.s3_service import tag_evidence_object as s3_fn
        return s3_fn(file_key, org_id, evidence_id, uploaded_by)
    raise ValueError("Evidence storage not configured")


def move_to_quarantine(file_key: str, org_id: str) -> str:
    """Move an infected file to the quarantine prefix."""
    backend = _detect_backend()
    if backend == "azure":
        from services.azure_blob_service import move_to_quarantine as azure_fn
        return azure_fn(file_key, org_id)
    elif backend == "s3":
        from services.s3_service import move_to_quarantine as s3_fn
        return s3_fn(file_key, org_id)
    # Fallback: return quarantine key even if no backend
    import uuid
    file_id = uuid.uuid4().hex[:12]
    fname = file_key.rsplit("/", 1)[-1] if "/" in file_key else file_key
    return f"quarantine/{org_id}/{file_id}_{fname}"


def write_inbox_payload(s3_key: str, body: bytes, org_id: str) -> None:
    """Write raw webhook inbox payload bytes to storage."""
    backend = _detect_backend()
    if backend == "azure":
        from services.azure_blob_service import write_inbox_payload as azure_fn
        azure_fn(s3_key, body, org_id)
    elif backend == "s3":
        from services.s3_service import write_inbox_payload as s3_fn
        s3_fn(s3_key, body, org_id)
    else:
        raise ValueError("Evidence storage not configured")


def download_blob_stream(file_key: str):
    """Download an evidence file and return a chunk iterator for streaming.

    Returns an iterator of bytes chunks, or None if the file doesn't exist.
    Used by the download proxy endpoint to stream files through the backend.
    """
    backend = _detect_backend()
    if backend == "azure":
        from services.azure_blob_service import download_blob_stream as azure_fn
        return azure_fn(file_key)
    elif backend == "s3":
        from services.s3_service import download_blob_stream as s3_fn
        return s3_fn(file_key)
    raise ValueError("Evidence storage not configured")


def check_object_exists(file_key: str) -> bool:
    """Check if an object/blob exists in evidence storage."""
    backend = _detect_backend()
    if backend == "azure":
        from services.azure_blob_service import check_object_exists as azure_fn
        return azure_fn(file_key)
    elif backend == "s3":
        from services.s3_service import EVIDENCE_BUCKET, _get_s3_client
        if not EVIDENCE_BUCKET:
            return False
        try:
            _get_s3_client().head_object(Bucket=EVIDENCE_BUCKET, Key=file_key)
            return True
        except Exception:
            return False
    return False
