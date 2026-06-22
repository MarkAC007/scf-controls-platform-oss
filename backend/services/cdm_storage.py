import os
import re
from uuid import UUID

from services import storage_service

try:
    from services.s3_service import _sanitize_filename as _sanitize_filename
except ImportError:  # pragma: no cover - fallback only used if S3 helper import breaks
    _sanitize_filename = None


MAX_UPLOAD_BYTES = int(os.getenv("CDM_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))


def _fallback_sanitize_filename(filename: str) -> str:
    sanitized = re.sub(r"[^\w\-.]", "_", filename)
    sanitized = re.sub(r"_+", "_", sanitized)
    return sanitized.strip("_")


def build_cdm_object_key(org_id: str | UUID, document_id: str | UUID, filename: str) -> str:
    safe_filename = (
        _sanitize_filename(filename) if _sanitize_filename is not None else _fallback_sanitize_filename(filename)
    )
    if not safe_filename:
        safe_filename = "upload.bin"
    return f"cdm/{org_id}/{document_id}/{safe_filename}"


def write_cdm_payload(key: str, body: bytes, org_id: str) -> None:
    storage_service.write_inbox_payload(key, body, org_id)


def download_cdm_payload(key: str) -> bytes:
    stream = storage_service.download_blob_stream(key)
    if stream is None:
        raise FileNotFoundError(f"CDM payload not found for key: {key}")
    return b"".join(stream)
