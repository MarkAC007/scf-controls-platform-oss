"""
Azure Blob Storage service for evidence files.

Drop-in alternative to s3_service.py for Azure deployments.
Uses azure-storage-blob SDK with SAS tokens for pre-signed URLs.
"""
import os
import re
import uuid
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Configuration from environment
AZURE_STORAGE_ACCOUNT_NAME = os.getenv("AZURE_STORAGE_ACCOUNT_NAME", "")
AZURE_STORAGE_ACCOUNT_KEY = os.getenv("AZURE_STORAGE_ACCOUNT_KEY", "")
EVIDENCE_CONTAINER = os.getenv("EVIDENCE_CONTAINER", "evidence")
EVIDENCE_URL_EXPIRY = int(os.getenv("EVIDENCE_URL_EXPIRY", "900"))  # 15 minutes
EVIDENCE_MAX_FILE_SIZE = int(os.getenv("EVIDENCE_MAX_FILE_SIZE", str(50 * 1024 * 1024)))  # 50MB

# Lazy-initialized clients
_blob_service_client = None
_container_client = None


def _get_blob_service_client():
    """Get or create the Azure BlobServiceClient (lazy initialization)."""
    global _blob_service_client
    if _blob_service_client is None:
        from azure.storage.blob import BlobServiceClient
        connection_string = (
            f"DefaultEndpointsProtocol=https;"
            f"AccountName={AZURE_STORAGE_ACCOUNT_NAME};"
            f"AccountKey={AZURE_STORAGE_ACCOUNT_KEY};"
            f"EndpointSuffix=core.windows.net"
        )
        _blob_service_client = BlobServiceClient.from_connection_string(connection_string)
    return _blob_service_client


def _get_container_client():
    """Get or create the container client for evidence."""
    global _container_client
    if _container_client is None:
        _container_client = _get_blob_service_client().get_container_client(EVIDENCE_CONTAINER)
    return _container_client


def _sanitize_filename(filename: str) -> str:
    """Sanitize filename for blob key usage."""
    sanitized = re.sub(r"[^\w\-.]", "_", filename)
    sanitized = re.sub(r"_+", "_", sanitized)
    return sanitized.strip("_")


def _sanitize_metadata_key(key: str) -> str:
    """Sanitize a metadata key for Azure Blob Storage.

    Azure requires metadata names to be valid C# identifiers:
    letters, digits, and underscores only.
    """
    return re.sub(r"[^a-zA-Z0-9_]", "_", key)


def _sanitize_metadata(metadata: dict) -> dict:
    """Sanitize all keys and values in a metadata dict for Azure Blob Storage.

    Keys: must be valid C# identifiers (letters, digits, underscores).
    Values: must be valid HTTP header values (printable ASCII).
    """
    sanitized = {}
    for k, v in metadata.items():
        safe_key = _sanitize_metadata_key(k)
        safe_value = re.sub(r"[^\x20-\x7E]", "", str(v))[:1024]
        sanitized[safe_key] = safe_value
    return sanitized


def _generate_object_key(org_id: str, filename: str) -> str:
    """Generate a unique blob key for an evidence file."""
    now = datetime.now(timezone.utc)
    short_uuid = uuid.uuid4().hex[:12]
    safe_filename = _sanitize_filename(filename)
    return f"evidence/{org_id}/{now.year}/{now.month:02d}/{short_uuid}_{safe_filename}"


def is_configured() -> bool:
    """Check if Azure Blob Storage is configured."""
    return bool(AZURE_STORAGE_ACCOUNT_NAME and AZURE_STORAGE_ACCOUNT_KEY)


def generate_upload_presigned_post(
    org_id: str,
    filename: str,
    content_type: str,
) -> dict:
    """
    Generate a SAS-based upload URL for browser-based evidence upload.

    Returns a dict with 'url', 'fields' (empty for Azure — SAS URL is self-contained),
    and 'object_key'.
    """
    from azure.storage.blob import BlobSasPermissions, generate_blob_sas

    if not is_configured():
        raise ValueError("Azure Blob Storage not configured")

    from services.s3_service import ALLOWED_CONTENT_TYPES
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise ValueError(
            f"Content type '{content_type}' not allowed. "
            f"Allowed types: {', '.join(sorted(ALLOWED_CONTENT_TYPES))}"
        )

    object_key = _generate_object_key(org_id, filename)

    sas_token = generate_blob_sas(
        account_name=AZURE_STORAGE_ACCOUNT_NAME,
        container_name=EVIDENCE_CONTAINER,
        blob_name=object_key,
        account_key=AZURE_STORAGE_ACCOUNT_KEY,
        permission=BlobSasPermissions(write=True, create=True),
        expiry=datetime.now(timezone.utc) + timedelta(seconds=EVIDENCE_URL_EXPIRY),
        content_type=content_type,
    )

    blob_url = (
        f"https://{AZURE_STORAGE_ACCOUNT_NAME}.blob.core.windows.net/"
        f"{EVIDENCE_CONTAINER}/{object_key}?{sas_token}"
    )

    logger.info(
        "Generated Azure upload URL for org=%s file=%s key=%s",
        org_id, filename, object_key,
    )

    return {
        "url": blob_url,
        "fields": {},  # Azure SAS URLs are self-contained — no form fields needed
        "object_key": object_key,
    }


def generate_download_url(
    org_id: str,
    file_key: str,
    filename: Optional[str] = None,
) -> str:
    """Generate a SAS-based download URL for an evidence file."""
    from azure.storage.blob import BlobSasPermissions, generate_blob_sas

    if not is_configured():
        raise ValueError("Azure Blob Storage not configured")

    expected_prefix = f"evidence/{org_id}/"
    if not file_key.startswith(expected_prefix):
        raise ValueError(
            f"Access denied: file key does not belong to organization {org_id}"
        )

    content_disposition = None
    if filename:
        content_disposition = f'attachment; filename="{filename}"'

    sas_token = generate_blob_sas(
        account_name=AZURE_STORAGE_ACCOUNT_NAME,
        container_name=EVIDENCE_CONTAINER,
        blob_name=file_key,
        account_key=AZURE_STORAGE_ACCOUNT_KEY,
        permission=BlobSasPermissions(read=True),
        expiry=datetime.now(timezone.utc) + timedelta(seconds=EVIDENCE_URL_EXPIRY),
        content_disposition=content_disposition,
    )

    url = (
        f"https://{AZURE_STORAGE_ACCOUNT_NAME}.blob.core.windows.net/"
        f"{EVIDENCE_CONTAINER}/{file_key}?{sas_token}"
    )

    logger.info(
        "Generated Azure download URL for org=%s key=%s",
        org_id, file_key,
    )

    return url


def generate_download_url_by_key(
    file_key: str,
    filename: Optional[str] = None,
) -> str:
    """
    Generate a SAS-based download URL for any blob key (no org prefix validation).

    Unlike generate_download_url(), this does not enforce the evidence/{org_id}/ prefix,
    making it suitable for DPSIA reports stored under dpsia-reports/.
    """
    from azure.storage.blob import BlobSasPermissions, generate_blob_sas

    if not is_configured():
        raise ValueError("Azure Blob Storage not configured")

    content_disposition = None
    if filename:
        content_disposition = f'attachment; filename="{filename}"'

    sas_token = generate_blob_sas(
        account_name=AZURE_STORAGE_ACCOUNT_NAME,
        container_name=EVIDENCE_CONTAINER,
        blob_name=file_key,
        account_key=AZURE_STORAGE_ACCOUNT_KEY,
        permission=BlobSasPermissions(read=True),
        expiry=datetime.now(timezone.utc) + timedelta(seconds=EVIDENCE_URL_EXPIRY),
        content_disposition=content_disposition,
    )

    url = (
        f"https://{AZURE_STORAGE_ACCOUNT_NAME}.blob.core.windows.net/"
        f"{EVIDENCE_CONTAINER}/{file_key}?{sas_token}"
    )

    logger.info("Generated download URL for key=%s", file_key)

    return url


def tag_evidence_object(
    file_key: str,
    org_id: str,
    evidence_id: Optional[str] = None,
    uploaded_by: Optional[str] = None,
) -> dict:
    """Apply tags (blob metadata) to an uploaded evidence file."""
    if not is_configured():
        raise ValueError("Azure Blob Storage not configured")

    blob_client = _get_container_client().get_blob_client(file_key)

    metadata = {"organization_id": org_id}
    if evidence_id:
        metadata["evidence_id"] = evidence_id
    if uploaded_by:
        metadata["uploaded_by"] = uploaded_by

    blob_client.set_blob_metadata(_sanitize_metadata(metadata))

    logger.info(
        "Tagged Azure blob key=%s org=%s evidence_id=%s",
        file_key, org_id, evidence_id,
    )

    return {"tagged": True, "key": file_key, "tag_count": len(metadata)}


def move_to_quarantine(file_key: str, org_id: str) -> str:
    """Move an infected file to the quarantine prefix."""
    file_id = uuid.uuid4().hex[:12]
    filename = file_key.rsplit("/", 1)[-1] if "/" in file_key else file_key
    quarantine_key = f"quarantine/{org_id}/{file_id}_{filename}"

    if not is_configured():
        logger.warning("Azure Blob Storage not configured — cannot quarantine file")
        return quarantine_key

    try:
        container = _get_container_client()
        source_blob = container.get_blob_client(file_key)
        dest_blob = container.get_blob_client(quarantine_key)

        # Copy source to quarantine
        dest_blob.start_copy_from_url(source_blob.url)
        # Delete original
        source_blob.delete_blob()
        logger.info("File quarantined: %s -> %s", file_key, quarantine_key)
    except Exception as e:
        logger.error("Failed to quarantine file %s: %s", file_key, str(e), exc_info=True)

    return quarantine_key


def write_inbox_payload(s3_key: str, body: bytes, org_id: str) -> None:
    """Write raw webhook inbox payload bytes to Azure Blob Storage."""
    if not is_configured():
        raise ValueError("Azure Blob Storage not configured")

    blob_client = _get_container_client().get_blob_client(s3_key)
    blob_client.upload_blob(
        body,
        overwrite=True,
        content_settings=_content_settings("application/json"),
        metadata=_sanitize_metadata({"organization_id": org_id}),
    )
    logger.info("Wrote inbox payload to Azure Blob: %s (%d bytes)", s3_key, len(body))


def download_blob_stream(file_key: str):
    """Download a blob and return a chunk iterator for streaming responses.

    Returns:
        An iterator of bytes chunks from the blob, or None if blob doesn't exist.

    Raises:
        ValueError: If Azure is not configured.
    """
    if not is_configured():
        raise ValueError("Azure Blob Storage not configured")

    try:
        blob_client = _get_container_client().get_blob_client(file_key)
        stream = blob_client.download_blob()
        return stream.chunks()
    except Exception as e:
        logger.error("Failed to download blob %s: %s", file_key, str(e), exc_info=True)
        return None


def check_object_exists(file_key: str) -> bool:
    """Check if a blob exists in the evidence container."""
    if not is_configured():
        return False

    try:
        blob_client = _get_container_client().get_blob_client(file_key)
        blob_client.get_blob_properties()
        return True
    except Exception:
        return False


def _content_settings(content_type: str):
    """Create Azure ContentSettings for a given content type."""
    from azure.storage.blob import ContentSettings
    return ContentSettings(content_type=content_type)
