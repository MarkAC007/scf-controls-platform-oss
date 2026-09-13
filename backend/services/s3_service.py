"""
S3 Evidence Storage Service.

The single object-storage driver. Every public function takes a
:class:`~services.storage_config.ResolvedStorageConfig` describing *which* store
to talk to; nothing here reads the environment, and nothing here is resolved at
import time.

**Import this module from ``services/storage_service.py`` and nowhere else.**
That rule used to be a docstring, which is why it drifted — six callsites ended
up reaching past the facade. It is now enforced mechanically by
``tests/test_storage_phase0_facade.py``.
"""
from __future__ import annotations

import re
import threading
import uuid
import logging
from collections import OrderedDict
from datetime import datetime
from typing import Optional

import boto3
from botocore.config import Config

from services.storage_config import (
    ROLE_INTERNAL,
    ROLE_PRESIGN,
    ROLE_PROBE,
    ResolvedStorageConfig,
    StorageNotConfigured,
    assert_endpoint_allowed,
    preset_for,
    resolve_platform,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Connection-probe limits
# ---------------------------------------------------------------------------
# A tenant supplies the endpoint the probe dials, so the probe must not be able
# to make the backend sit on a socket. Short timeouts and a single attempt, on
# the probe path only: the clients that serve real traffic keep botocore's
# defaults, because a retry there is protecting a user's upload rather than
# answering a form.
PROBE_CONNECT_TIMEOUT = 3
PROBE_READ_TIMEOUT = 5
PROBE_MAX_ATTEMPTS = 1
#: Body written by the round-trip probe. Small, fixed, and not secret.
PROBE_BODY = b"scf-evidence-storage-connection-test"
PROBE_CONTENT_TYPE = "text/plain"

# Content-type allowlist for evidence uploads. A policy constant, not a storage
# setting: it does not vary by provider and is not read from the environment.
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

# ---------------------------------------------------------------------------
# Client cache
# ---------------------------------------------------------------------------
# Keyed by config identity (config id + credential version + credential
# fingerprint + role + endpoint + region + addressing), NOT by module global.
# This is what makes a rotation or a provider switch produce a *new* client
# instead of silently reusing a stale one: the old key is simply never asked
# for again. Bounded so that a long-lived worker rotating repeatedly does not
# accumulate clients forever.
_CLIENT_CACHE_MAX = 32
_client_cache: "OrderedDict[tuple, object]" = OrderedDict()
_client_cache_lock = threading.Lock()


def reset_client_cache() -> None:
    """Drop every cached boto3 client.

    Called by ``storage_service.invalidate()``. Phase 1 wires that to the
    cross-process version key so a config change reaches a Celery worker.
    """
    with _client_cache_lock:
        _client_cache.clear()


def addressing_style_for(config: ResolvedStorageConfig) -> str:
    """How buckets are addressed for ``config``.

    ``path`` puts the bucket in the URL path; ``auto`` is botocore's
    virtual-host addressing, which falls back to path style by itself for a
    bucket whose name cannot be a DNS label. ``auto`` rather than a hard
    ``virtual`` is deliberate: a bucket named ``my.data.bucket`` cannot be
    reached over TLS virtual-host style, and today's code passes no addressing
    preference at all on the AWS path, so pinning ``virtual`` would be a
    behaviour change dressed as a preset.
    """
    return "path" if config.path_style else "auto"


def client_kwargs(config: ResolvedStorageConfig, role: str = ROLE_INTERNAL) -> dict:
    """The boto3 ``client()`` keyword arguments ``config`` produces.

    Pure, and public, so that the preset table can be tested for what it
    actually causes rather than for what it says. This is ISC 16: four presets,
    one code path, and a test per preset asserting the endpoint, addressing
    style, signature version and region that reach botocore.

    Credentials are passed explicitly when the config carries them (ISC-7).
    When it does not, boto3's ambient chain is left to resolve them, which is
    how an IAM instance role / IRSA deployment still works — that is the one
    case where ambient resolution is the intended behaviour rather than an
    accident.
    """
    endpoint_url = config.endpoint_for(role)
    preset = preset_for(config.provider)

    config_kwargs = {
        "s3": {"addressing_style": addressing_style_for(config)},
        "signature_version": preset.signature_version,
    }
    if role == ROLE_PROBE:
        config_kwargs["connect_timeout"] = PROBE_CONNECT_TIMEOUT
        config_kwargs["read_timeout"] = PROBE_READ_TIMEOUT
        config_kwargs["retries"] = {"max_attempts": PROBE_MAX_ATTEMPTS}

    kwargs = {
        "region_name": config.region,
        "config": Config(**config_kwargs),
    }
    if endpoint_url:
        kwargs["endpoint_url"] = endpoint_url

    if config.access_key_id and config.secret_access_key:
        kwargs["aws_access_key_id"] = config.access_key_id
        kwargs["aws_secret_access_key"] = config.secret_access_key
        if config.session_token:
            kwargs["aws_session_token"] = config.session_token
    else:
        # False positive: the word "credentials" is in the message text only; the
        # sole argument is the config id. No key material is available here.
        # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
        logger.debug(
            "No explicit storage credentials on config %s — deferring to the "
            "boto3 credential chain (instance role / IRSA).",
            config.config_id,
        )

    return kwargs


def _build_client(config: ResolvedStorageConfig, role: str):
    """Build a boto3 S3 client for ``config``."""
    return boto3.client("s3", **client_kwargs(config, role))


def _client(config: ResolvedStorageConfig, role: str = ROLE_INTERNAL):
    """The client for ``config`` in ``role``, building and caching on demand.

    The endpoint hook runs on *every* call, before the cache is consulted. That
    placement is deliberate: ISA §7 requires address validation to re-run at
    connect time rather than only at save time, and a check that ran only when
    a client was constructed would be skipped for the entire life of a cached
    client — which is precisely how a DNS rebind gets through.
    """
    assert_endpoint_allowed(config)

    key = config.client_cache_key(role)
    with _client_cache_lock:
        client = _client_cache.get(key)
        if client is not None:
            _client_cache.move_to_end(key)
            return client

    client = _build_client(config, role)

    with _client_cache_lock:
        _client_cache[key] = client
        _client_cache.move_to_end(key)
        while len(_client_cache) > _CLIENT_CACHE_MAX:
            _client_cache.popitem(last=False)
    return client


def _presign_client(config: ResolvedStorageConfig):
    """Client used to sign browser-facing URLs.

    When a public endpoint is configured (MinIO behind localhost), sign against
    it so the URL is reachable from the browser; otherwise this resolves to the
    same client as the internal role, because AWS S3 URLs are already publicly
    reachable.
    """
    return _client(config, ROLE_PRESIGN)


def _cfg(config: Optional[ResolvedStorageConfig]) -> ResolvedStorageConfig:
    """Normalise the optional ``config`` argument.

    ``storage_service`` always passes one explicitly. The fallback exists for
    direct-driver tests, and resolves per call — never at import.
    """
    return config if config is not None else resolve_platform()


def _require_bucket(config: ResolvedStorageConfig) -> str:
    if not config.bucket:
        # Same condition as the facade's own guard, and it must carry the same
        # type so the API answers it with the one actionable 409 rather than a
        # 500 (ISC 53). The wording no longer names an environment variable:
        # since Phase 1 a bucket comes from a configuration row far more often
        # than from `.env`.
        raise StorageNotConfigured(
            "No evidence store is configured (no bucket in the resolved "
            "configuration)"
        )
    return config.bucket


def _sse_kwargs(config: ResolvedStorageConfig) -> dict:
    """Server-side-encryption arguments for a write, from the resolved config."""
    return {"ServerSideEncryption": config.sse_mode} if config.sse_enabled else {}


def is_configured(config: Optional[ResolvedStorageConfig] = None) -> bool:
    """Whether the resolved configuration names a bucket to write to."""
    return _cfg(config).is_configured


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


def generate_upload_presigned_post(
    org_id: str,
    filename: str,
    content_type: str,
    config: Optional[ResolvedStorageConfig] = None,
) -> dict:
    """
    Generate a pre-signed POST for browser-based evidence upload.

    Returns a dict with 'url', 'fields', 'object_key' and 'expires_in'. The
    expiry is returned rather than read from a module constant so the value the
    client is told matches the value that was actually signed (R8).

    Raises:
        ValueError: If content_type is not in the allowlist or bucket not configured.
        ClientError: If AWS S3 call fails.
    """
    cfg = _cfg(config)
    bucket = _require_bucket(cfg)

    if content_type not in ALLOWED_CONTENT_TYPES:
        raise ValueError(
            f"Content type '{content_type}' not allowed. "
            f"Allowed types: {', '.join(sorted(ALLOWED_CONTENT_TYPES))}"
        )

    object_key = _generate_object_key(org_id, filename)
    client = _presign_client(cfg)

    conditions = [
        ["content-length-range", 1, cfg.max_file_size],
        {"Content-Type": content_type},
        {"x-amz-meta-organization-id": org_id},
    ]

    fields = {
        "Content-Type": content_type,
        "x-amz-meta-organization-id": org_id,
    }

    if cfg.sse_enabled:
        conditions.append({"x-amz-server-side-encryption": cfg.sse_mode})
        fields["x-amz-server-side-encryption"] = cfg.sse_mode

    presigned = client.generate_presigned_post(
        Bucket=bucket,
        Key=object_key,
        Fields=fields,
        Conditions=conditions,
        ExpiresIn=cfg.url_expiry,
    )

    logger.info(
        "Generated upload URL for org=%s file=%s key=%s",
        org_id, filename, object_key,
    )

    return {
        "url": presigned["url"],
        "fields": presigned["fields"],
        "object_key": object_key,
        "expires_in": cfg.url_expiry,
        # Stated by the layer that signed it, not inferred downstream. A
        # presigned POST with no extra fields is a legal reply, so "fields is
        # empty" never meant "not a POST" — the browser used to read it that
        # way. A preset that signs a PUT says so here instead.
        "method": "POST",
        "provider": cfg.provider,
    }


def generate_download_url(
    org_id: str,
    file_key: str,
    filename: Optional[str] = None,
    config: Optional[ResolvedStorageConfig] = None,
) -> str:
    """
    Generate a pre-signed GET URL for downloading an evidence file.

    Validates that the file_key belongs to the requesting organization. This
    prefix check stays in place as defence in depth even once configuration is
    per-organisation (ISA R2).

    Raises:
        ValueError: If file_key doesn't match org scope or bucket not configured.
        ClientError: If AWS S3 call fails.
    """
    cfg = _cfg(config)
    bucket = _require_bucket(cfg)

    expected_prefix = f"evidence/{org_id}/"
    if not file_key.startswith(expected_prefix):
        raise ValueError(
            f"Access denied: file key does not belong to organization {org_id}"
        )

    client = _presign_client(cfg)

    params = {
        "Bucket": bucket,
        "Key": file_key,
    }

    if filename:
        params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'

    url = client.generate_presigned_url(
        "get_object",
        Params=params,
        ExpiresIn=cfg.url_expiry,
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
    config: Optional[ResolvedStorageConfig] = None,
) -> dict:
    """
    Apply S3 object tags to an uploaded evidence file.
    Called after upload confirmation to add audit/compliance metadata.

    Raises:
        ValueError: If bucket not configured.
        ClientError: If AWS S3 call fails.
    """
    cfg = _cfg(config)
    bucket = _require_bucket(cfg)

    tags = [
        {"Key": "organization_id", "Value": org_id},
    ]
    if evidence_id:
        tags.append({"Key": "evidence_id", "Value": evidence_id})
    if uploaded_by:
        tags.append({"Key": "uploaded_by", "Value": uploaded_by})

    client = _client(cfg)

    client.put_object_tagging(
        Bucket=bucket,
        Key=file_key,
        Tagging={"TagSet": tags},
    )

    logger.info(
        "Tagged evidence object key=%s org=%s evidence_id=%s",
        file_key, org_id, evidence_id,
    )

    return {"tagged": True, "key": file_key, "tag_count": len(tags)}


def move_to_quarantine(
    file_key: str,
    org_id: str,
    config: Optional[ResolvedStorageConfig] = None,
) -> str:
    """Move an infected file from its current location to the quarantine prefix.

    Returns the new quarantine key. Never raises: a quarantine that cannot be
    completed is logged, and the caller still gets the key it should record.
    """
    cfg = _cfg(config)

    import uuid as _uuid
    file_id = str(_uuid.uuid4())[:12]
    filename = file_key.rsplit("/", 1)[-1] if "/" in file_key else file_key
    quarantine_key = f"quarantine/{org_id}/{file_id}_{filename}"

    if not cfg.bucket:
        logger.warning("EVIDENCE_BUCKET not configured — cannot quarantine file")
        return quarantine_key

    try:
        client = _client(cfg)
        # Copy to quarantine
        client.copy_object(
            Bucket=cfg.bucket,
            CopySource={"Bucket": cfg.bucket, "Key": file_key},
            Key=quarantine_key,
            MetadataDirective="REPLACE",
            Metadata={"x-scf-quarantine-reason": "malware-detected"},
        )
        # Delete original
        client.delete_object(Bucket=cfg.bucket, Key=file_key)
        logger.info("File quarantined: %s -> %s", file_key, quarantine_key)
    except Exception as e:
        logger.error("Failed to quarantine file %s: %s", file_key, str(e), exc_info=True)

    return quarantine_key


def download_blob_stream(
    file_key: str,
    config: Optional[ResolvedStorageConfig] = None,
):
    """Download an S3 object and return a chunk iterator for streaming responses.

    Returns:
        An iterator of bytes chunks, or None if the object doesn't exist.

    Raises:
        ValueError: If the resolved config names no bucket.
    """
    cfg = _cfg(config)
    bucket = _require_bucket(cfg)

    try:
        client = _client(cfg)
        response = client.get_object(Bucket=bucket, Key=file_key)
        return response["Body"].iter_chunks(chunk_size=64 * 1024)
    except Exception as e:
        logger.error("Failed to download S3 object %s: %s", file_key, str(e), exc_info=True)
        return None


def check_object_exists(
    file_key: str,
    config: Optional[ResolvedStorageConfig] = None,
) -> bool:
    """Whether an object exists. Never raises — an unreachable store is False."""
    cfg = _cfg(config)
    if not cfg.bucket:
        return False
    try:
        _client(cfg).head_object(Bucket=cfg.bucket, Key=file_key)
        return True
    except Exception:
        return False


def write_inbox_payload(
    s3_key: str,
    body: bytes,
    org_id: str,
    config: Optional[ResolvedStorageConfig] = None,
) -> None:
    """Write raw webhook inbox payload bytes to S3.

    Called by the evidence inbox handler immediately after the EvidenceFile
    DB record is created (fix for Issue #400).

    Raises:
        ValueError: If the resolved config names no bucket.
        ClientError: If the S3 write fails (caller should let this propagate so
                     the DB transaction rolls back).
    """
    cfg = _cfg(config)
    bucket = _require_bucket(cfg)

    client = _client(cfg)
    put_kwargs = {
        "Bucket": bucket,
        "Key": s3_key,
        "Body": body,
        "ContentType": "application/json",
        "Metadata": {"x-scf-org-id": org_id},
        **_sse_kwargs(cfg),
    }
    client.put_object(**put_kwargs)
    logger.info("Wrote inbox payload to S3: %s (%d bytes)", s3_key, len(body))


def put_bytes(
    s3_key: str,
    body: bytes,
    content_type: str,
    org_id: str,
    config: Optional[ResolvedStorageConfig] = None,
) -> None:
    """Write arbitrary bytes to S3 with an explicit content type.

    Generic sibling of write_inbox_payload — used for the OSS catalogue-import
    hand-off, where the backend stashes an operator-supplied SCF .xlsx so the
    Celery worker (a separate container) can read it back via
    download_blob_stream. Lets ClientError propagate to the caller.

    Raises:
        ValueError: If the resolved config names no bucket.
    """
    cfg = _cfg(config)
    bucket = _require_bucket(cfg)

    client = _client(cfg)
    put_kwargs = {
        "Bucket": bucket,
        "Key": s3_key,
        "Body": body,
        "ContentType": content_type,
        "Metadata": {"x-scf-org-id": org_id},
        **_sse_kwargs(cfg),
    }
    client.put_object(**put_kwargs)
    logger.info("Wrote object to S3: %s (%d bytes, %s)", s3_key, len(body), content_type)


def head_object(
    file_key: str,
    config: Optional[ResolvedStorageConfig] = None,
) -> Optional[dict]:
    """Object metadata without fetching the body, or ``None`` if it is absent.

    Returns ``{"size": int, "etag": str, "content_type": str}``. Distinct from
    :func:`check_object_exists`, which answers a boolean and swallows every
    error: the copy job needs the size, and needs to be able to tell "not
    there" from "the store refused the request".

    Raises:
        ClientError: For anything other than a 404 — an expired credential and
            a missing object must not read the same to the caller.
    """
    from botocore.exceptions import ClientError

    cfg = _cfg(config)
    bucket = _require_bucket(cfg)
    try:
        response = _client(cfg).head_object(Bucket=bucket, Key=file_key)
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        status = _status_code_of(exc.response)
        if code in ("404", "NoSuchKey", "NotFound") or status == 404:
            return None
        raise
    return {
        "size": int(response.get("ContentLength") or 0),
        "etag": str(response.get("ETag") or "").strip('"'),
        "content_type": str(response.get("ContentType") or ""),
    }


def download_to_fileobj(
    file_key: str,
    fileobj,
    config: Optional[ResolvedStorageConfig] = None,
) -> None:
    """Stream an object into an open binary file object.

    Uses boto3's managed transfer so a large object is fetched in ranged parts
    rather than held whole in memory. Lets ClientError propagate: the copy job
    records a per-object failure and moves on, which it can only do if it is
    told.

    Raises:
        ValueError: If the resolved config names no bucket.
        ClientError: If the read fails.
    """
    cfg = _cfg(config)
    bucket = _require_bucket(cfg)
    _client(cfg).download_fileobj(Bucket=bucket, Key=file_key, Fileobj=fileobj)


def upload_fileobj(
    s3_key: str,
    fileobj,
    content_type: str,
    org_id: str,
    config: Optional[ResolvedStorageConfig] = None,
) -> None:
    """Stream an open binary file object into storage.

    The streaming sibling of :func:`put_bytes`, for the copy job: an evidence
    object may be tens of megabytes and reading every one of them fully into
    memory to move it is a way to lose a Celery worker. Carries the same
    ``x-scf-org-id`` metadata tag and the same server-side-encryption mode a
    normal write does, so a copied object is indistinguishable from one
    uploaded directly.

    Raises:
        ValueError: If the resolved config names no bucket.
        ClientError: If the write fails.
    """
    cfg = _cfg(config)
    bucket = _require_bucket(cfg)
    extra = {
        "ContentType": content_type or "application/octet-stream",
        "Metadata": {"x-scf-org-id": org_id},
        **_sse_kwargs(cfg),
    }
    _client(cfg).upload_fileobj(
        Fileobj=fileobj, Bucket=bucket, Key=s3_key, ExtraArgs=extra
    )


def delete_object(
    s3_key: str,
    config: Optional[ResolvedStorageConfig] = None,
) -> None:
    """Delete a single object.

    Added so the catalogue workbook cleanup no longer has to reach past the
    facade for a raw client. Lets ClientError propagate — a caller doing
    best-effort cleanup is the one that should decide to swallow it.

    Raises:
        ValueError: If the resolved config names no bucket.
    """
    cfg = _cfg(config)
    bucket = _require_bucket(cfg)

    _client(cfg).delete_object(Bucket=bucket, Key=s3_key)
    logger.info("Deleted object from S3: %s", s3_key)


# ---------------------------------------------------------------------------
# Connection probe
# ---------------------------------------------------------------------------


#: The steps a probe reports, in the order it attempts them.
PROBE_STEPS = ("address", "put", "get", "delete")


def _status_code_of(response) -> Optional[int]:
    """The HTTP status a boto3 response or ClientError carries, if any."""
    if not isinstance(response, dict):
        return None
    metadata = response.get("ResponseMetadata")
    if not isinstance(metadata, dict):
        return None
    code = metadata.get("HTTPStatusCode")
    return int(code) if isinstance(code, int) else None


def _step(name: str, ok: bool, status_code=None, error_class=None) -> dict:
    """One line of the probe report.

    Deliberately four keys and no more. **No response body, no URL, no
    credential and no exception message** ever appears here: the reply goes to
    an organisation administrator who supplied the endpoint, so anything echoed
    from the far end turns the probe into a read oracle for whatever the
    backend can reach. The class name says what went wrong without saying what
    came back.
    """
    entry = {"name": name, "ok": ok}
    if status_code is not None:
        entry["status_code"] = status_code
    if error_class is not None:
        entry["error_class"] = error_class
    return entry


def probe_key(org_id: str) -> str:
    """The throwaway key a probe writes to.

    Under the organisation's own prefix so that a credential scoped to that
    prefix — which is what a correctly scoped policy looks like — can still be
    tested, and under ``.probe/`` so the object is recognisable if a delete
    ever fails to land.
    """
    return f"evidence/{org_id}/.probe/{uuid.uuid4().hex}"


def probe_round_trip(config: ResolvedStorageConfig, org_id: str) -> dict:
    """Put, get and delete a throwaway object, reporting each step.

    Returns ``{"success": bool, "steps": [...]}``. Never raises: a probe that
    cannot reach the store is a *result*, not an error, which is the shape the
    two existing infrastructure health endpoints already use. Steps that were
    not reached are reported explicitly rather than omitted, so a caller can
    render the whole sequence.

    The delete runs whenever the put succeeded, even if the get failed, so a
    failed probe does not leave an object behind.

    **Exactly one entry per step name, in ``PROBE_STEPS`` order.** The report is
    a per-step result (criterion 19), so a step reported twice — once as
    ``NotAttempted`` by an early exit and once with the real outcome of the
    cleanup delete — is not one. The outcomes are therefore recorded into a
    mapping keyed by step name, and the list is built **once, after the
    ``finally`` has run**, filling ``NotAttempted`` for whatever was never
    reached. There is one exit point and the caller can never hold a list that
    is still being appended to.
    """
    key = probe_key(org_id)
    outcomes: dict = {}

    def record(name: str, ok: bool, status_code=None, error_class=None) -> None:
        """Record one step's outcome, replacing any earlier one for that name."""
        outcomes[name] = _step(
            name, ok, status_code=status_code, error_class=error_class
        )

    client = None
    put_succeeded = False

    def attempt() -> None:
        """The probe proper. Free to return early; the report is built later."""
        nonlocal client, put_succeeded

        # -- address -----------------------------------------------------
        try:
            assert_endpoint_allowed(config)
            if not config.bucket:
                raise ValueError(
                    "No bucket is configured for this storage configuration"
                )
            client = _client(config, ROLE_PROBE)
        except Exception as exc:  # noqa: BLE001 — a refusal is a result, not a 500
            record("address", False, error_class=type(exc).__name__)
            return

        record("address", True)

        # -- put ---------------------------------------------------------
        try:
            response = client.put_object(
                Bucket=config.bucket,
                Key=key,
                Body=PROBE_BODY,
                ContentType=PROBE_CONTENT_TYPE,
                Metadata={"x-scf-org-id": str(org_id)},
                **_sse_kwargs(config),
            )
            put_succeeded = True
            record("put", True, status_code=_status_code_of(response))
        except Exception as exc:  # noqa: BLE001
            record(
                "put",
                False,
                status_code=_status_code_of(getattr(exc, "response", None)),
                error_class=type(exc).__name__,
            )
            return

        # -- get ---------------------------------------------------------
        try:
            response = client.get_object(Bucket=config.bucket, Key=key)
            body = response["Body"].read()
            try:
                response["Body"].close()
            except Exception:  # noqa: BLE001 — closing is best effort
                pass
            if body != PROBE_BODY:
                # Read back something other than what was written. Reported as
                # a class, never as the bytes themselves.
                record(
                    "get",
                    False,
                    status_code=_status_code_of(response),
                    error_class="ContentMismatch",
                )
                return
            record("get", True, status_code=_status_code_of(response))
        except Exception as exc:  # noqa: BLE001
            record(
                "get",
                False,
                status_code=_status_code_of(getattr(exc, "response", None)),
                error_class=type(exc).__name__,
            )
            return

    try:
        attempt()
    finally:
        # -- delete ------------------------------------------------------
        # In the `finally` so the throwaway object is removed even when the get
        # failed and `attempt` returned early. Its result *replaces* whatever
        # the report would otherwise have said about the delete, rather than
        # sitting alongside it.
        if put_succeeded and client is not None:
            try:
                response = client.delete_object(Bucket=config.bucket, Key=key)
                record("delete", True, status_code=_status_code_of(response))
            except Exception as exc:  # noqa: BLE001
                record(
                    "delete",
                    False,
                    status_code=_status_code_of(getattr(exc, "response", None)),
                    error_class=type(exc).__name__,
                )

    steps = [
        outcomes.get(name) or _step(name, False, error_class="NotAttempted")
        for name in PROBE_STEPS
    ]
    success = all(entry["ok"] for entry in steps)
    return {"success": success, "steps": steps}
