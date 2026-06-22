"""
S3 Evidence Storage Service.
Provides pre-signed URL generation and object tagging for evidence file uploads.
"""
import os
import re
import uuid
import logging
from datetime import datetime
from typing import Optional

import boto3
from botocore.config import Config

logger = logging.getLogger(__name__)

# Configuration from environment
EVIDENCE_BUCKET = os.getenv("EVIDENCE_BUCKET", "")
AWS_REGION = os.getenv("AWS_DEFAULT_REGION", "eu-west-1")
EVIDENCE_URL_EXPIRY = int(os.getenv("EVIDENCE_URL_EXPIRY", "900"))  # 15 minutes
EVIDENCE_MAX_FILE_SIZE = int(os.getenv("EVIDENCE_MAX_FILE_SIZE", str(50 * 1024 * 1024)))  # 50MB

# S3-compatible stores (e.g. self-hosted MinIO). When AWS_ENDPOINT_URL is set we
# talk to that endpoint with path-style addressing; when it's unset we behave
# exactly as before against AWS S3.
AWS_ENDPOINT_URL = os.getenv("AWS_ENDPOINT_URL", "").strip()
# Presigned URLs are handed to the browser, which generally cannot resolve the
# internal docker hostname (e.g. http://minio:9000). EVIDENCE_PUBLIC_ENDPOINT is
# the externally-reachable URL used only when signing those URLs. Signing is an
# offline operation, so no connectivity to this endpoint is needed server-side.
EVIDENCE_PUBLIC_ENDPOINT = os.getenv("EVIDENCE_PUBLIC_ENDPOINT", "").strip()
# AWS S3 supports SSE-S3 (AES256); MinIO rejects it unless a KMS is configured,
# so server-side encryption is only requested when talking to real AWS S3.
SSE_ENABLED = not AWS_ENDPOINT_URL

# Content-type allowlist for evidence uploads
ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/gif",
    "text/csv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # .xlsx
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
    "application/zip",
    "application/json",
    "text/plain",
    "text/yaml",            # .yml / .yaml
}

# Lazy-initialized boto3 clients: one for server-side ops (internal endpoint),
# one for signing browser-facing presigned URLs (public endpoint).
_s3_client = None
_s3_presign_client = None


def _build_client(endpoint_url: str):
    """Build a boto3 S3 client, using path-style addressing when a custom
    endpoint (MinIO / S3-compatible) is configured."""
    kwargs = {"region_name": AWS_REGION}
    if endpoint_url:
        kwargs["endpoint_url"] = endpoint_url
        kwargs["config"] = Config(
            s3={"addressing_style": "path"}, signature_version="s3v4"
        )
    return boto3.client("s3", **kwargs)


def _get_s3_client():
    """Client for server-side operations (reaches the storage backend directly)."""
    global _s3_client
    if _s3_client is None:
        _s3_client = _build_client(AWS_ENDPOINT_URL)
    return _s3_client


def _get_presign_client():
    """Client used to sign browser-facing presigned URLs. When a public endpoint
    is configured (MinIO behind localhost), sign against it so the URL is
    reachable from the browser; otherwise reuse the main client (AWS S3 URLs are
    already publicly reachable)."""
    global _s3_presign_client
    if EVIDENCE_PUBLIC_ENDPOINT:
        if _s3_presign_client is None:
            _s3_presign_client = _build_client(EVIDENCE_PUBLIC_ENDPOINT)
        return _s3_presign_client
    return _get_s3_client()


def _sanitize_filename(filename: str) -> str:
    """Sanitize filename for S3 key usage. Replace spaces and special chars."""
    # Keep alphanumerics, hyphens, underscores, and dots
    sanitized = re.sub(r"[^\w\-.]", "_", filename)
    # Collapse multiple underscores
    sanitized = re.sub(r"_+", "_", sanitized)
    return sanitized.strip("_")


def _generate_object_key(org_id: str, filename: str) -> str:
    """
    Generate a unique S3 object key for an evidence file.
    Format: evidence/{org_id}/{YYYY}/{MM}/{uuid12}_{filename}
    """
    now = datetime.utcnow()
    short_uuid = uuid.uuid4().hex[:12]
    safe_filename = _sanitize_filename(filename)
    return f"evidence/{org_id}/{now.year}/{now.month:02d}/{short_uuid}_{safe_filename}"


def generate_upload_presigned_post(
    org_id: str,
    filename: str,
    content_type: str,
) -> dict:
    """
    Generate a pre-signed POST for browser-based evidence upload.

    Returns a dict with 'url' and 'fields' for the browser to POST directly to S3.
    Enforces: file size limit, content-type allowlist, SSE-S3, org metadata.

    Raises:
        ValueError: If content_type is not in the allowlist or bucket not configured.
        ClientError: If AWS S3 call fails.
    """
    if not EVIDENCE_BUCKET:
        raise ValueError("EVIDENCE_BUCKET environment variable not configured")

    if content_type not in ALLOWED_CONTENT_TYPES:
        raise ValueError(
            f"Content type '{content_type}' not allowed. "
            f"Allowed types: {', '.join(sorted(ALLOWED_CONTENT_TYPES))}"
        )

    object_key = _generate_object_key(org_id, filename)
    client = _get_presign_client()

    conditions = [
        ["content-length-range", 1, EVIDENCE_MAX_FILE_SIZE],
        {"Content-Type": content_type},
        {"x-amz-meta-organization-id": org_id},
    ]

    fields = {
        "Content-Type": content_type,
        "x-amz-meta-organization-id": org_id,
    }

    if SSE_ENABLED:
        conditions.append({"x-amz-server-side-encryption": "AES256"})
        fields["x-amz-server-side-encryption"] = "AES256"

    presigned = client.generate_presigned_post(
        Bucket=EVIDENCE_BUCKET,
        Key=object_key,
        Fields=fields,
        Conditions=conditions,
        ExpiresIn=EVIDENCE_URL_EXPIRY,
    )

    logger.info(
        "Generated upload URL for org=%s file=%s key=%s",
        org_id, filename, object_key,
    )

    return {
        "url": presigned["url"],
        "fields": presigned["fields"],
        "object_key": object_key,
    }


def generate_download_url(
    org_id: str,
    file_key: str,
    filename: Optional[str] = None,
) -> str:
    """
    Generate a pre-signed GET URL for downloading an evidence file.

    Validates that the file_key belongs to the requesting organization.

    Args:
        org_id: Organization ID (for access scoping).
        file_key: S3 object key.
        filename: Optional friendly filename for Content-Disposition.

    Returns:
        Pre-signed GET URL string.

    Raises:
        ValueError: If file_key doesn't match org scope or bucket not configured.
        ClientError: If AWS S3 call fails.
    """
    if not EVIDENCE_BUCKET:
        raise ValueError("EVIDENCE_BUCKET environment variable not configured")

    expected_prefix = f"evidence/{org_id}/"
    if not file_key.startswith(expected_prefix):
        raise ValueError(
            f"Access denied: file key does not belong to organization {org_id}"
        )

    client = _get_presign_client()

    params = {
        "Bucket": EVIDENCE_BUCKET,
        "Key": file_key,
    }

    if filename:
        params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'

    url = client.generate_presigned_url(
        "get_object",
        Params=params,
        ExpiresIn=EVIDENCE_URL_EXPIRY,
    )

    logger.info(
        "Generated download URL for org=%s key=%s",
        org_id, file_key,
    )

    return url


def tag_evidence_object(
    file_key: str,
    org_id: str,
    evidence_id: Optional[str] = None,
    uploaded_by: Optional[str] = None,
) -> dict:
    """
    Apply S3 object tags to an uploaded evidence file.
    Called after upload confirmation to add audit/compliance metadata.

    Args:
        file_key: S3 object key.
        org_id: Organization ID.
        evidence_id: Optional evidence tracking record ID.
        uploaded_by: Optional user ID who uploaded.

    Returns:
        Dict with tagging result.

    Raises:
        ValueError: If bucket not configured.
        ClientError: If AWS S3 call fails.
    """
    if not EVIDENCE_BUCKET:
        raise ValueError("EVIDENCE_BUCKET environment variable not configured")

    tags = [
        {"Key": "organization_id", "Value": org_id},
    ]
    if evidence_id:
        tags.append({"Key": "evidence_id", "Value": evidence_id})
    if uploaded_by:
        tags.append({"Key": "uploaded_by", "Value": uploaded_by})

    client = _get_s3_client()

    client.put_object_tagging(
        Bucket=EVIDENCE_BUCKET,
        Key=file_key,
        Tagging={"TagSet": tags},
    )

    logger.info(
        "Tagged evidence object key=%s org=%s evidence_id=%s",
        file_key, org_id, evidence_id,
    )

    return {"tagged": True, "key": file_key, "tag_count": len(tags)}


def move_to_quarantine(file_key: str, org_id: str) -> str:
    """Move an infected file from its current location to the quarantine prefix.

    Args:
        file_key: Current S3 key of the infected file
        org_id: Organisation ID for quarantine path

    Returns:
        New quarantine S3 key
    """
    import uuid as _uuid
    file_id = str(_uuid.uuid4())[:12]
    filename = file_key.rsplit("/", 1)[-1] if "/" in file_key else file_key
    quarantine_key = f"quarantine/{org_id}/{file_id}_{filename}"

    if not EVIDENCE_BUCKET:
        logger.warning("EVIDENCE_BUCKET not configured — cannot quarantine file")
        return quarantine_key

    try:
        client = _get_s3_client()
        # Copy to quarantine
        client.copy_object(
            Bucket=EVIDENCE_BUCKET,
            CopySource={"Bucket": EVIDENCE_BUCKET, "Key": file_key},
            Key=quarantine_key,
            MetadataDirective="REPLACE",
            Metadata={"x-scf-quarantine-reason": "malware-detected"},
        )
        # Delete original
        client.delete_object(Bucket=EVIDENCE_BUCKET, Key=file_key)
        logger.info("File quarantined: %s -> %s", file_key, quarantine_key)
    except Exception as e:
        logger.error("Failed to quarantine file %s: %s", file_key, str(e), exc_info=True)

    return quarantine_key


def download_blob_stream(file_key: str):
    """Download an S3 object and return a chunk iterator for streaming responses.

    Returns:
        An iterator of bytes chunks, or None if object doesn't exist.

    Raises:
        ValueError: If EVIDENCE_BUCKET not configured.
    """
    if not EVIDENCE_BUCKET:
        raise ValueError("EVIDENCE_BUCKET environment variable not configured")

    try:
        client = _get_s3_client()
        response = client.get_object(Bucket=EVIDENCE_BUCKET, Key=file_key)
        return response["Body"].iter_chunks(chunk_size=64 * 1024)
    except Exception as e:
        logger.error("Failed to download S3 object %s: %s", file_key, str(e), exc_info=True)
        return None


def write_inbox_payload(s3_key: str, body: bytes, org_id: str) -> None:
    """Write raw webhook inbox payload bytes to S3.

    Called by the evidence inbox handler immediately after the EvidenceFile
    DB record is created (fix for Issue #400).

    Raises:
        ValueError: If EVIDENCE_BUCKET not configured.
        ClientError: If S3 write fails (caller should let this propagate so
                     the DB transaction rolls back).
    """
    if not EVIDENCE_BUCKET:
        raise ValueError("EVIDENCE_BUCKET environment variable not configured")

    client = _get_s3_client()
    put_kwargs = {
        "Bucket": EVIDENCE_BUCKET,
        "Key": s3_key,
        "Body": body,
        "ContentType": "application/json",
        "Metadata": {"x-scf-org-id": org_id},
    }
    if SSE_ENABLED:
        put_kwargs["ServerSideEncryption"] = "AES256"
    client.put_object(**put_kwargs)
    logger.info("Wrote inbox payload to S3: %s (%d bytes)", s3_key, len(body))


def put_bytes(s3_key: str, body: bytes, content_type: str, org_id: str) -> None:
    """Write arbitrary bytes to S3 with an explicit content type.

    Generic sibling of write_inbox_payload — used for the OSS catalogue-import
    hand-off, where the backend stashes an operator-supplied SCF .xlsx so the
    Celery worker (a separate container) can read it back via
    download_blob_stream. Lets ClientError propagate to the caller.

    Raises:
        ValueError: If EVIDENCE_BUCKET not configured.
    """
    if not EVIDENCE_BUCKET:
        raise ValueError("EVIDENCE_BUCKET environment variable not configured")

    client = _get_s3_client()
    put_kwargs = {
        "Bucket": EVIDENCE_BUCKET,
        "Key": s3_key,
        "Body": body,
        "ContentType": content_type,
        "Metadata": {"x-scf-org-id": org_id},
    }
    if SSE_ENABLED:
        put_kwargs["ServerSideEncryption"] = "AES256"
    client.put_object(**put_kwargs)
    logger.info("Wrote object to S3: %s (%d bytes, %s)", s3_key, len(body), content_type)
