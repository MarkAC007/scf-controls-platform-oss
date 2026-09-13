"""
Evidence Storage Service — unified facade over the object-storage drivers.

**This is the only module permitted to import ``services.s3_service``.** The
rule used to live in this docstring alone, which is why six callsites had
drifted past it by the time #967 was filed; it is now enforced mechanically by
``tests/test_storage_phase0_facade.py``.

Backend selection:

- a resolved configuration naming a bucket → S3
- else → not configured

``AZURE_STORAGE_ACCOUNT_NAME`` used to come first and select the Azure Blob
driver. Phase 7 retired that selection (D13): it is read only to warn, and is
otherwise ignored. The Azure branches below are consequently unreachable and
are kept only so an operator's ``.env`` and the compose pass-throughs do not
have to change in the same commit.

What changed in Phase 0 is that none of it is frozen any more. The backend
verdict is re-evaluated on every call rather than memoised for the life of the
process, storage settings arrive as a resolved
:class:`~services.storage_config.ResolvedStorageConfig` rather than as module
constants, and :func:`invalidate` exists so a configuration change can be pushed
through explicitly. Phase 1 calls that from the cross-process version key so a
change reaches a Celery worker within a couple of seconds.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from services import storage_config
from services.storage_config import ResolvedStorageConfig, StorageNotConfigured

logger = logging.getLogger(__name__)

# Re-export constants that callers need regardless of backend
from services.s3_service import ALLOWED_CONTENT_TYPES  # noqa: F401

BACKEND_AZURE = "azure"
BACKEND_S3 = "s3"
BACKEND_NONE = "none"

# ---------------------------------------------------------------------------
# Backend detection
# ---------------------------------------------------------------------------

#: The last backend verdict that was logged. This is *not* a memo that short-
#: circuits detection — detection re-runs on every call, which is what lets the
#: backend change at runtime. It exists only so the process does not log the
#: same verdict on every single storage operation.
_BACKEND: Optional[str] = None


def _detect_backend() -> str:
    """Detect which storage backend is configured, on every call."""
    global _BACKEND

    if os.getenv("AZURE_STORAGE_ACCOUNT_NAME"):
        # DEPRECATED AND IGNORED (Phase 7, D13/D53).
        #
        # This used to select the Azure Blob backend, ahead of everything else,
        # on one environment variable. Mark confirmed no customer is on Azure
        # Blob evidence storage, and the bring-your-own-storage work made the
        # S3 driver the only one that can be configured per organisation — so
        # every Azure branch below is a path that reads one account out of the
        # process environment no matter which organisation, or which file, is
        # being asked about. Phase 6 made four of them refuse a per-row
        # argument rather than answer it wrongly; this removes the selection
        # that made them reachable in the first place.
        #
        # DEPRECATE rather than DELETE, deliberately. Deleting the backend
        # would not stop at `storage_service` and `azure_blob_service`: the
        # variables are passed through `docker-compose.yml` (two services),
        # named in `.env.example`, in `README.oss.md` and in `CLAUDE.md`, the
        # webclient still keys an upload header off a `provider` of "azure",
        # and `backend/installer/validate.py` knows about them. That is a
        # wider blast radius than a storage decision should carry on this
        # branch, and none of it is load-bearing once the selection is gone.
        #
        # What an Azure install sees: this warning on every backend verdict
        # change, and then whatever its S3 configuration says — which, if it
        # has none, is the actionable 409 naming the Settings screen. Loud and
        # specific beats a silent switch to a store nobody asked for.
        logger.warning(
            "AZURE_STORAGE_ACCOUNT_NAME is set, but Azure Blob evidence "
            "storage is RETIRED and this setting is IGNORED. Evidence is "
            "resolved from the evidence storage configuration (Settings, "
            "Evidence storage) or from the AWS_/EVIDENCE_ environment. "
            "Remove AZURE_STORAGE_ACCOUNT_NAME to silence this."
        )
    if storage_config.resolve_platform().is_configured:
        backend = BACKEND_S3
        message = "Storage backend: S3"
        level = logging.INFO
    else:
        backend = BACKEND_NONE
        message = (
            "No evidence store configured: no active configuration row and no "
            "EVIDENCE_BUCKET. Configure one under Settings, Evidence storage."
        )
        level = logging.WARNING

    if backend != _BACKEND:
        logger.log(level, message)
        _BACKEND = backend
    return backend


def reset_backend_cache() -> None:
    """Forget the last logged backend verdict.

    Detection itself is not cached, so this only affects logging. It is kept as
    a named operation because ``invalidate()`` is the thing callers should
    reach for, and because the tests that predate Phase 0 poke ``_BACKEND``
    directly.
    """
    global _BACKEND
    _BACKEND = None


def invalidate() -> None:
    """Drop every cached artefact derived from storage configuration.

    Call this after a configuration change so the next operation resolves
    afresh and builds new clients. It clears only *this* process. To reach the
    Celery workers as well, a writer calls
    :func:`services.storage_config.bump_version`, which INCRs the shared
    ``scf:storage:version`` key that every resolver re-checks every couple of
    seconds — the same mechanism ``services/secrets.py`` already uses.
    """
    from services import s3_service

    reset_backend_cache()
    storage_config.invalidate()
    s3_service.reset_client_cache()


def get_backend() -> str:
    """Return the detected backend name: 'azure', 's3', or 'none'."""
    return _detect_backend()


def resolve_config(org_id: Optional[str] = None) -> ResolvedStorageConfig:
    """The resolved storage configuration for ``org_id`` (None = platform)."""
    return storage_config.resolve(org_id) if org_id else storage_config.resolve_platform()


def current_config_row_id(org_id: Optional[str]) -> Optional[str]:
    """The id of the configuration ROW an organisation writes to right now, or
    ``None`` when no row applies.

    ``None`` means the environment-synthesised configuration, which has no row
    and therefore nothing an evidence file could point at. Stamping a file with
    the literal ``legacy-env`` id would be a foreign key that does not resolve;
    leaving the column NULL means exactly what it has always meant — "resolve
    this the way everything resolved before".

    This is what an ingestion path records on the file it has just written, so
    that a later switch of store cannot make those bytes unreachable.
    """
    config_id = resolve_config(org_id).config_id
    if not config_id or config_id == storage_config.LEGACY_ENV_CONFIG_ID:
        return None
    return str(config_id)


def resolve_config_for_file(
    org_id: Optional[str],
    storage_config_id: Optional[str],
) -> ResolvedStorageConfig:
    """Where one stored object's bytes actually are.

    Per *file*, not per organisation. A file whose ``storage_config_id`` is set
    reads from that configuration whatever the organisation is writing to now,
    and whatever that configuration's status is. That is what keeps every file
    readable while a copy between stores is in flight, and after one.
    """
    return storage_config.resolve_for_file(org_id, storage_config_id)


def is_configured() -> bool:
    """Check if any evidence storage backend is configured and ready."""
    backend = _detect_backend()
    if backend == BACKEND_AZURE:
        from services.azure_blob_service import is_configured as azure_configured
        return azure_configured()
    elif backend == BACKEND_S3:
        return storage_config.resolve_platform().is_configured
    return False


# ---------------------------------------------------------------------------
# Public API — delegates to the active backend
# ---------------------------------------------------------------------------

AZURE_NO_PER_ROW = (
    "Per-file storage resolution is not implemented on the Azure Blob backend. "
    "The caller named a specific storage configuration, and the Azure driver "
    "has no way to honour it, so serving the request would read from the wrong "
    "store silently. Azure evidence storage is retired (ISA D1/D13)."
)


def _refuse_azure_per_row() -> None:
    """Refuse, loudly, rather than ignore a per-row argument.

    ``_detect_backend`` checks ``AZURE_STORAGE_ACCOUNT_NAME`` first, so these
    branches are reachable on any installation that sets one env var — they are
    not dead code. What they cannot do is honour ``storage_config_id``: the
    Azure driver takes no configuration, so every per-row call would quietly
    fall back to the one account in the environment. On an installation with
    two stores that is a wrong answer with no error, which is worse than a
    refusal.
    """
    raise NotImplementedError(AZURE_NO_PER_ROW)


def generate_upload_presigned_post(
    org_id: str,
    filename: str,
    content_type: str,
) -> dict:
    """Generate a pre-signed upload URL (S3 POST or Azure SAS).

    The result carries ``expires_in`` so the caller reports the expiry that was
    actually signed rather than a value frozen at import (R8).
    """
    backend = _detect_backend()
    if backend == BACKEND_AZURE:
        from services.azure_blob_service import (
            EVIDENCE_URL_EXPIRY as azure_expiry,
            generate_upload_presigned_post as azure_fn,
        )
        result = azure_fn(org_id, filename, content_type)
        result.setdefault("expires_in", azure_expiry)
        return result
    elif backend == BACKEND_S3:
        from services.s3_service import generate_upload_presigned_post as s3_fn
        return s3_fn(org_id, filename, content_type, config=resolve_config(org_id))
    raise StorageNotConfigured("Evidence storage not configured")


def generate_download_url(
    org_id: str,
    file_key: str,
    filename: Optional[str] = None,
    storage_config_id: Optional[str] = None,
) -> str:
    """Generate a pre-signed download URL.

    ``storage_config_id`` is the file's own store when the caller knows it, and
    it wins over resolution by organisation. Signing against the organisation's
    *current* store would produce a URL to an object that is not there yet —
    the failure a copy between stores is otherwise guaranteed to cause.
    """
    backend = _detect_backend()
    if backend == BACKEND_AZURE:
        if storage_config_id is not None:
            _refuse_azure_per_row()
        from services.azure_blob_service import generate_download_url as azure_fn
        return azure_fn(org_id, file_key, filename)
    elif backend == BACKEND_S3:
        from services.s3_service import generate_download_url as s3_fn
        return s3_fn(
            org_id,
            file_key,
            filename,
            config=resolve_config_for_file(org_id, storage_config_id),
        )
    raise StorageNotConfigured("Evidence storage not configured")


def get_url_expiry(org_id: Optional[str] = None) -> int:
    """The presigned-URL lifetime for ``org_id``, resolved per config.

    Replaces the module-level ``EVIDENCE_URL_EXPIRY`` constant that used to be
    imported and echoed to clients (R8). Prefer the ``expires_in`` value that
    :func:`generate_upload_presigned_post` returns — it is the expiry that was
    actually signed. This exists for callers that need the number without
    signing anything.
    """
    if _detect_backend() == BACKEND_AZURE:
        from services.azure_blob_service import EVIDENCE_URL_EXPIRY as azure_expiry
        return azure_expiry
    return resolve_config(org_id).url_expiry


def tag_evidence_object(
    file_key: str,
    org_id: str,
    evidence_id: Optional[str] = None,
    uploaded_by: Optional[str] = None,
) -> dict:
    """Apply metadata/tags to an uploaded evidence file."""
    backend = _detect_backend()
    if backend == BACKEND_AZURE:
        from services.azure_blob_service import tag_evidence_object as azure_fn
        return azure_fn(file_key, org_id, evidence_id, uploaded_by)
    elif backend == BACKEND_S3:
        from services.s3_service import tag_evidence_object as s3_fn
        return s3_fn(
            file_key, org_id, evidence_id, uploaded_by, config=resolve_config(org_id)
        )
    raise StorageNotConfigured("Evidence storage not configured")


def move_to_quarantine(
    file_key: str,
    org_id: str,
    storage_config_id: Optional[str] = None,
) -> str:
    """Move an infected file to the quarantine prefix.

    ``storage_config_id`` is the file's own store, and it wins over resolution
    by organisation for the same reason the download path resolves per row: an
    infected object whose bytes are in a store the organisation has since
    stopped writing to must be quarantined *there*. Resolving by organisation
    moved nothing, and the failure surfaced only as an audit row with the key
    unchanged.
    """
    backend = _detect_backend()
    if backend == BACKEND_AZURE:
        if storage_config_id is not None:
            _refuse_azure_per_row()
        from services.azure_blob_service import move_to_quarantine as azure_fn
        return azure_fn(file_key, org_id)
    elif backend == BACKEND_S3:
        from services.s3_service import move_to_quarantine as s3_fn
        return s3_fn(
            file_key,
            org_id,
            config=resolve_config_for_file(org_id, storage_config_id),
        )
    # Fallback: return quarantine key even if no backend
    import uuid
    file_id = uuid.uuid4().hex[:12]
    fname = file_key.rsplit("/", 1)[-1] if "/" in file_key else file_key
    return f"quarantine/{org_id}/{file_id}_{fname}"


def write_inbox_payload(s3_key: str, body: bytes, org_id: str) -> None:
    """Write raw webhook inbox payload bytes to storage."""
    backend = _detect_backend()
    if backend == BACKEND_AZURE:
        from services.azure_blob_service import write_inbox_payload as azure_fn
        azure_fn(s3_key, body, org_id)
    elif backend == BACKEND_S3:
        from services.s3_service import write_inbox_payload as s3_fn
        s3_fn(s3_key, body, org_id, config=resolve_config(org_id))
    else:
        raise StorageNotConfigured("Evidence storage not configured")


def put_bytes(
    s3_key: str,
    body: bytes,
    content_type: str,
    org_id: str,
) -> None:
    """Write arbitrary bytes to storage with an explicit content type.

    Added in Phase 0 (#967). This existed only on ``s3_service``, so the four
    catalogue callsites that needed it imported the driver directly — which is
    both why the facade drifted and why the Azure path could never have
    completed a catalogue import, the mandatory first step of any install.

    Raises:
        ValueError: If evidence storage is not configured, or the active
            backend has no implementation.
    """
    backend = _detect_backend()
    if backend == BACKEND_S3:
        from services.s3_service import put_bytes as s3_fn
        s3_fn(s3_key, body, content_type, org_id, config=resolve_config(org_id))
        return
    if backend == BACKEND_AZURE:
        # Deliberately not implemented: D1/D13 retire Azure Blob evidence
        # storage and Mark has confirmed no customer is on it. Raising names
        # the situation instead of failing later inside a Celery task.
        raise ValueError(
            "put_bytes is not supported on the Azure Blob backend; Azure "
            "evidence storage is retired (see ISA D1/D13)."
        )
    raise StorageNotConfigured("Evidence storage not configured")


def delete_object(s3_key: str) -> None:
    """Delete a single object from storage.

    Added in Phase 0 (#967) so the catalogue workbook cleanup does not have to
    reach past the facade for a raw boto3 client.

    Raises:
        ValueError: If evidence storage is not configured, or the active
            backend has no implementation.
    """
    backend = _detect_backend()
    if backend == BACKEND_S3:
        from services.s3_service import delete_object as s3_fn
        s3_fn(s3_key, config=storage_config.resolve_platform())
        return
    if backend == BACKEND_AZURE:
        raise ValueError(
            "delete_object is not supported on the Azure Blob backend; Azure "
            "evidence storage is retired (see ISA D1/D13)."
        )
    raise StorageNotConfigured("Evidence storage not configured")


def download_blob_stream(
    file_key: str,
    org_id: Optional[str] = None,
    storage_config_id: Optional[str] = None,
):
    """Download an evidence file and return a chunk iterator for streaming.

    Returns an iterator of bytes chunks, or None if the file doesn't exist.
    Used by the download proxy endpoint to stream files through the backend.

    Both scoping arguments are optional and default to the platform store,
    which is what every platform-scope caller (the catalogue workbook, an
    upgrade diff, a reconciliation detail blob) wants and what this function
    did unconditionally before. An evidence read passes both, so the file is
    served from the store its bytes are in rather than from whichever store its
    organisation is writing to now.
    """
    backend = _detect_backend()
    if backend == BACKEND_AZURE:
        if storage_config_id is not None:
            _refuse_azure_per_row()
        from services.azure_blob_service import download_blob_stream as azure_fn
        return azure_fn(file_key)
    elif backend == BACKEND_S3:
        from services.s3_service import download_blob_stream as s3_fn
        config = (
            resolve_config_for_file(org_id, storage_config_id)
            if (org_id is not None or storage_config_id is not None)
            else storage_config.resolve_platform()
        )
        return s3_fn(file_key, config=config)
    raise StorageNotConfigured("Evidence storage not configured")


def check_object_exists(
    file_key: str,
    org_id: Optional[str] = None,
    storage_config_id: Optional[str] = None,
) -> bool:
    """Check if an object/blob exists in evidence storage.

    Both scoping arguments are optional and default to the platform store, the
    same way :func:`download_blob_stream` does, because the platform-scope
    callers (the catalogue workbook, an upgrade diff) legitimately want that
    store. An evidence read passes both, so the object is looked for in the
    store its bytes are in rather than in the platform store.

    Resolving unconditionally to the platform store — which is what this did
    before — reported every file of every organisation on its own store as
    missing, from its first upload onward, not merely after a switch.
    """
    backend = _detect_backend()
    if backend == BACKEND_AZURE:
        if storage_config_id is not None:
            _refuse_azure_per_row()
        from services.azure_blob_service import check_object_exists as azure_fn
        return azure_fn(file_key)
    elif backend == BACKEND_S3:
        from services.s3_service import check_object_exists as s3_fn
        config = (
            resolve_config_for_file(org_id, storage_config_id)
            if (org_id is not None or storage_config_id is not None)
            else storage_config.resolve_platform()
        )
        return s3_fn(file_key, config=config)
    return False


def probe_round_trip(config: ResolvedStorageConfig, org_id: str) -> dict:
    """Put, get and delete a throwaway object against ``config``.

    Takes an explicit configuration rather than resolving one, because the
    whole point of a connection test is to exercise a configuration the
    resolver will not hand out: a ``draft`` row an administrator is still
    filling in. Everything else on this facade resolves; this does not, and
    that asymmetry is the feature.

    Returns ``{"success": bool, "steps": [{"name", "ok", "status_code"?,
    "error_class"?}]}``. Never raises, and never carries a response body, a URL
    or a credential back to the caller.

    S3-only by construction: Azure Blob has no round-trip probe and is retired
    for evidence storage (D1/D13). The backend is not consulted, because the
    configuration under test names the store directly.
    """
    from services import s3_service

    return s3_service.probe_round_trip(config, org_id)


def platform_object_client():
    """A raw client plus bucket name for the platform store.

    The facade deliberately exposes no list-by-prefix or bulk-delete API:
    deleting by prefix is a one-off retirement operation, not a platform
    capability. ``scripts/cdm_retirement_purge.py`` genuinely needs one, so this
    is a named, documented escape hatch for it rather than a reason for that
    script to import the driver directly and reopen the drift this phase closes.

    Returns:
        ``(client, bucket)`` for the S3 backend.

    Raises:
        ValueError: If the active backend is not S3.
    """
    if _detect_backend() != BACKEND_S3:
        raise ValueError("platform_object_client() is only available on the S3 backend")
    from services import s3_service

    config = storage_config.resolve_platform()
    return s3_service._client(config), config.bucket


# ---------------------------------------------------------------------------
# Copy primitives (Phase 6)
# ---------------------------------------------------------------------------
#
# These three take an explicit configuration rather than resolving one, for the
# same reason `probe_round_trip` does: a copy operates on two stores at once,
# and exactly one of them is the one the resolver would hand out. Everything
# else on this facade resolves; these do not, and that asymmetry is the
# feature.
#
# S3-only by construction. Azure Blob is retired for evidence storage (D1/D13)
# and no customer is on it, so there is no Azure source or target to copy
# between; the backend is not consulted, because both configurations name their
# store directly.


def head_object(config: ResolvedStorageConfig, file_key: str) -> Optional[dict]:
    """Size, ETag and content type of one object in ``config``, or ``None``.

    Raises ClientError for anything that is not a missing object, so the copy
    job can tell a store that refused it from an object that is not there.
    """
    from services import s3_service

    return s3_service.head_object(file_key, config=config)


def download_blob_stream_for_config(config: ResolvedStorageConfig, file_key: str):
    """Chunk iterator for one object in ``config``, or ``None`` if it is absent.

    The explicit-configuration sibling of :func:`download_blob_stream`, used by
    the copy job to read an object back out of the target and hash it. Reading
    it back is the only way to say the bytes arrived: an ETag is not a digest
    on a multipart upload and not a digest at all under server-side encryption.
    """
    from services import s3_service

    return s3_service.download_blob_stream(file_key, config=config)


def download_object_to_fileobj(
    config: ResolvedStorageConfig, file_key: str, fileobj
) -> None:
    """Stream one object out of ``config`` into an open binary file object."""
    from services import s3_service

    s3_service.download_to_fileobj(file_key, fileobj, config=config)


def upload_object_from_fileobj(
    config: ResolvedStorageConfig,
    s3_key: str,
    fileobj,
    content_type: str,
    org_id: str,
) -> None:
    """Stream an open binary file object into ``config``."""
    from services import s3_service

    s3_service.upload_fileobj(s3_key, fileobj, content_type, org_id, config=config)


# ---------------------------------------------------------------------------
# Health — the platform-wide answer to "is there a store, and which one"
# ---------------------------------------------------------------------------

#: Component name on ``GET /health``.
HEALTH_COMPONENT = "evidence_storage"

# `status` vocabulary. Deliberately three words and no more: a health consumer
# branches on them, and every extra value is a branch somebody forgets.
HEALTH_OK = "ok"
HEALTH_UNCONFIGURED = "unconfigured"
HEALTH_ERROR = "error"

# `source` vocabulary. Four values, because "bundled" and "platform" are the
# same row shape but a very different operational fact — one is inside this
# stack and inside `scripts/backup.sh`, the other is not. `none` exists here
# and deliberately does NOT exist on the org-scoped effective read (D42): the
# resolver cannot synthesise "none" for an organisation, but the platform
# question "is anything configured at all" genuinely has that answer.
HEALTH_SOURCE_BUNDLED = "bundled"
HEALTH_SOURCE_PLATFORM = "platform"
HEALTH_SOURCE_LEGACY_ENV = "legacy_env"
HEALTH_SOURCE_NONE = "none"


def evidence_storage_health() -> dict:
    """The ``evidence_storage`` component of ``GET /health`` (ISC 54).

    **What it answers.** The PLATFORM effective state — which store this
    installation resolves to when no organisation is in scope. An organisation
    is not a meaningful subject on a platform-wide, unauthenticated route, and
    per-org state is already readable, authenticated and org-scoped, at
    ``GET /api/organizations/{org_id}/evidence-storage/effective``.

    This is also the answer to the backlog item in D46: `scripts/backup.sh`
    keys on "is a store bundled", which is not the same question as "is the
    bundled store the one in use". A bundled install repointed at external S3
    reports ``source: "platform"`` here while the backup script still tars the
    now-unused volume. *This* is where "in use" is answered.

    **What it must not carry.** No bucket, no endpoint, no region, no key id,
    no credential, not even a masked one. ``/health`` takes no credentials and
    is reachable by anyone who can reach the port — it is wired into the
    container healthcheck and the load balancer, so authenticating it would
    break both. Everything here is therefore deliberately non-identifying:
    which KIND of store, and whether it works. A bucket name is an asset
    inventory; a provider name is not.

    **Why it does not dial the store.** A round trip per poll would turn a
    healthcheck into a traffic generator against a customer's object store —
    Docker polls this every 30 seconds for the life of the container — and one
    transient network blip would flap the container health and restart a
    perfectly healthy backend. So there is no probe here, cached or otherwise.
    The data comes from the resolver's own snapshot, which already has a
    :data:`services.storage_config.CACHE_TTL_SECONDS` TTL, so the common case
    touches neither the database nor the network. Dialling the store is an
    explicit, authenticated, administrator-triggered action with its own
    endpoint (``POST .../evidence-storage/test``, D34), which is where a
    reachability answer belongs and where a failure has somebody to tell.

    ``status: "error"`` therefore means *resolution* failed — an undecryptable
    secret being the case that actually happens (D27 keeps a database outage
    from getting here by falling back to the environment). That is a real,
    local, cheap signal, and it is the one a monitor can act on.
    """
    try:
        config = storage_config.resolve_platform()
    except Exception as exc:  # noqa: BLE001 — health never raises
        # The class name, never the message: a resolution error can quote a
        # configuration id and a scope, and this route has no reader we can
        # vouch for. The full message is in the log, where it is already.
        logger.warning("Evidence storage health: resolution failed: %s", exc)
        return {
            "status": HEALTH_ERROR,
            "source": HEALTH_SOURCE_NONE,
            "provider": None,
            "error": type(exc).__name__,
        }

    if not config.is_configured:
        return {
            "status": HEALTH_UNCONFIGURED,
            "source": HEALTH_SOURCE_NONE,
            "provider": None,
        }

    if config.source == storage_config.SOURCE_PLATFORM:
        source = HEALTH_SOURCE_BUNDLED if config.is_bundled else HEALTH_SOURCE_PLATFORM
    else:
        # `resolve_platform` yields either a platform row or the environment
        # synthesis; there is no third case, and SOURCE_ORG cannot appear.
        source = HEALTH_SOURCE_LEGACY_ENV

    return {
        "status": HEALTH_OK,
        "source": source,
        "provider": config.provider,
    }
