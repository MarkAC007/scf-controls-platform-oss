"""Evidence storage configuration API.

Phases 2 and 3 of the bring-your-own evidence storage work. Everything an
organisation administrator can do to the store their evidence goes to:

    POST   /api/organizations/{org_id}/evidence-storage/test
    POST   /api/organizations/{org_id}/evidence-storage
    GET    /api/organizations/{org_id}/evidence-storage
    GET    /api/organizations/{org_id}/evidence-storage/effective
    GET    /api/organizations/{org_id}/evidence-storage/{config_id}
    PATCH  /api/organizations/{org_id}/evidence-storage/{config_id}
    POST   /api/organizations/{org_id}/evidence-storage/{config_id}/activate
    POST   /api/organizations/{org_id}/evidence-storage/{config_id}/rotate
    POST   /api/organizations/{org_id}/evidence-storage/{config_id}/retire
    DELETE /api/organizations/{org_id}/evidence-storage/{config_id}
    POST   /api/organizations/{org_id}/evidence-storage/{config_id}/copy-to/{target_config_id}
    GET    /api/organizations/{org_id}/evidence-storage/copy-runs
    GET    /api/organizations/{org_id}/evidence-storage/copy-runs/{run_id}

The Settings screen that drives them is Phase 5; the copy panel is Phase 6.

This module is a **transport layer and nothing else**. Every rule about what a
configuration may contain, when it may change and what has to be proved before
it goes live lives in ``services/evidence_storage_admin.py``, so that the
installer's seeding path and any later admin route reach the same rule. A rule
enforced in a route holds only for requests that come through that route.

Four things about this surface are deliberate and should survive later edits.

**No request body may name ``is_bundled``, ``status``, ``key_version``,
``organization_id`` or ``secret_ciphertext``.** The request models carry
``extra="forbid"``, so a body containing one of those names is a 422 rather
than a silently ignored field — and the models do not define them, so no
amount of later refactoring turns "ignored" into "accepted". ``is_bundled`` is
the one that matters most: it is what exempts a row from the loopback,
RFC1918, CGNAT and ``.local`` address rules and permits the ``http`` scheme, so
a tenant who could set it could point the backend at the operator's network.

**No response ever carries the secret.** ``EvidenceStorageConfigResponse`` does
not name a field that could hold one; what it renders instead is a fixed mask
of the same eight characters for every configured row. The mask is a constant,
not a slice or a redaction of the stored value, so it cannot leak a length, a
prefix or a character class. There is a test asserting this model's exact field
set, because ``repr=False`` on a Pydantic field hides a value from ``repr()``
and serialises it anyway.

**A configuration id that belongs to another tenant answers 404, not 403.** A
distinct "exists but is not yours" reply would turn any of these routes into an
oracle for enumerating other tenants' configuration ids.

Three things about the connection test in particular are deliberate.

**It requires authentication and an organisation admin role.** The nearest
existing pattern, the two infrastructure health endpoints at
``api/tasks_api.py`` (``/health/redis``, ``/health/celery``), is followed for
the *response shape* — catch broadly and return ``success: false`` rather than
raise — and deliberately **not** followed for authentication: both of those are
undecorated, and this one dials an address the caller supplied.

**It never echoes anything from the far end.** No response body, no URL, no
credential, no exception message. A step carries its name, whether it
succeeded, the HTTP status and the exception's class name. A probe that
returned what it read would be a read oracle for every service the backend can
reach, which is the same reason the address guard's refusals name a class of
address rather than the address itself.

**It is rate limited.** None of the five existing ``/api/admin/integrations``
routes carries a rate limit; this one does, because each call is an outbound
connection to an address the caller chose.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import OrgMembership, User, require_org_role, user_is_platform_admin
from database import get_db
from models import EvidenceFile, EvidenceStorageConfig
from rate_limiting import rate_limit_read, rate_limit_write
from services import crypto, evidence_storage_admin, storage_config, storage_service
from services.audit_service import (
    detect_action_source,
    get_client_ip,
    get_request_id,
    get_user_agent,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["evidence-storage"])

#: What a configured secret renders as. A constant, the same eight characters
#: for every row, identical to the mask the Integrations screen already shows
#: (``webclient/src/components/IntegrationsSettings.tsx``). It is deliberately
#: not derived from the stored value in any way: a mask whose length tracked
#: the secret would leak the secret's length, and one that kept a prefix would
#: leak the prefix.
SECRET_MASK = "\u2022" * 8

MASTER_API_KEY_ACTOR = "api_key:master"

#: The 409 body an operator sees when there is no encryption key. Same shape as
#: ``api/integrations.py`` returns for the same cause, so one client-side
#: handler covers both screens.
NO_ENCRYPTION_KEY_DETAIL: Dict[str, Any] = {
    "message": "SCF_SECRET_KEY is not configured — see docs",
    "encryption_key_configured": False,
}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class EvidenceStorageTestRequest(BaseModel):
    """Which configuration to test.

    Omitted, or ``null``, means "whatever this organisation resolves to today"
    — the active org row, else the platform row, else the environment. Given,
    it must name a row belonging to this organisation, or the platform row and
    a platform administrator to ask for it.
    """

    config_id: Optional[UUID] = Field(
        default=None,
        description=(
            "Configuration row to test. Omit to test the configuration the "
            "organisation currently resolves to."
        ),
    )


class EvidenceStorageTestStep(BaseModel):
    """One step of the round trip.

    ``status_code`` is the HTTP status the store answered with, when there was
    one. ``error_class`` is an exception class name — never a message, and
    never anything the far end sent back.
    """

    name: str
    ok: bool
    status_code: Optional[int] = None
    error_class: Optional[str] = None


class EvidenceStorageTestResponse(BaseModel):
    success: bool
    config_id: str
    steps: List[EvidenceStorageTestStep]


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


async def _config_under_test(
    org_id: UUID,
    config_id: Optional[UUID],
    membership: OrgMembership,
    db: AsyncSession,
) -> storage_config.ResolvedStorageConfig:
    """Resolve which configuration this request may test.

    Cross-tenant reads are closed here: a row is testable only when it belongs
    to the organisation in the path, or when it is the platform-scope row and
    the caller is a platform administrator. Anything else answers **404**, not
    403, for the same reason ``auth.assert_user_in_org`` does — a distinct
    "exists but is not yours" reply would turn this into an oracle for
    enumerating other tenants' configuration ids.
    """
    if config_id is None:
        # No id: whatever this organisation resolves to today. The resolver
        # reads rows over a synchronous connection, so it goes to a thread.
        return await asyncio.to_thread(storage_config.resolve, str(org_id))

    result = await db.execute(
        select(EvidenceStorageConfig).where(EvidenceStorageConfig.id == config_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Storage configuration not found")

    if row.organization_id is None:
        if not await user_is_platform_admin(membership.user, db):
            raise HTTPException(
                status_code=404, detail="Storage configuration not found"
            )
    elif row.organization_id != org_id:
        raise HTTPException(status_code=404, detail="Storage configuration not found")

    return storage_config.config_from_row(storage_config.stored_row_from_orm(row))


@router.post(
    "/organizations/{org_id}/evidence-storage/test",
    response_model=EvidenceStorageTestResponse,
    summary="Test an evidence storage configuration",
    description=(
        "Write, read back and delete a throwaway object against the storage "
        "configuration, reporting each step. Returns 200 with success=false "
        "when the store cannot be reached; the reply never contains a response "
        "body, a URL or a credential."
    ),
)
@rate_limit_write
async def test_evidence_storage(
    request: Request,
    response: Response,
    org_id: UUID,
    payload: Optional[EvidenceStorageTestRequest] = None,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """Round-trip probe against one evidence storage configuration."""
    config_id = payload.config_id if payload else None

    try:
        config = await _config_under_test(org_id, config_id, membership, db)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        # A configuration that cannot even be materialised — an undecryptable
        # secret, most likely — is a result the administrator needs to see, not
        # a 500. The class name says which, without saying what.
        logger.warning(
            "Evidence storage configuration %s for org %s could not be read: %s",
            config_id,
            org_id,
            type(exc).__name__,
        )
        return EvidenceStorageTestResponse(
            success=False,
            config_id=str(config_id) if config_id else "unresolved",
            steps=[
                EvidenceStorageTestStep(
                    name="address", ok=False, error_class=type(exc).__name__
                )
            ],
        )

    report = await asyncio.to_thread(
        storage_service.probe_round_trip, config, str(org_id)
    )

    logger.info(
        "Evidence storage connection test for org=%s config=%s success=%s by %s",
        org_id,
        config.config_id,
        report.get("success"),
        membership.user.email,
    )

    return EvidenceStorageTestResponse(
        success=bool(report.get("success")),
        config_id=config.config_id,
        steps=[EvidenceStorageTestStep(**step) for step in report.get("steps", [])],
    )


# ---------------------------------------------------------------------------
# Phase 3 schemas
# ---------------------------------------------------------------------------


class EvidenceStorageConfigResponse(BaseModel):
    """One configuration, as an administrator may see it.

    **The field set of this model is the security boundary**, and there is a
    test asserting it exactly. ``secret_access_key`` and ``secret_ciphertext``
    are absent by construction rather than excluded by a serialiser option: an
    excluded field is one ``model_dump(exclude=None)`` away from being
    serialised, while a field that does not exist cannot be.

    ``access_key_id`` is present and in the clear because it is an identifier,
    not a credential — the Settings screen has to show which key is in use —
    and because an access key id alone opens nothing.

    ``secret_mask`` is :data:`SECRET_MASK` when a secret is stored and ``None``
    when none is, which is the one bit of information about the secret this
    surface discloses: whether there is one.

    ``source`` and ``managed_by_operator`` carry the same meaning here as on
    ``GET /api/admin/integrations`` — deliberately the same two names, so one
    client-side chip renders both screens. For a *row* they are a property of
    its scope, not of a resolution: a row with an ``organization_id`` is
    ``org`` and an organisation administrator owns it; the platform-scope row
    is ``platform`` and belongs to whoever installed the platform, so an
    organisation administrator cannot edit it (every write verb answers 404
    through an organisation URL). The third source, ``legacy_env``, has no row
    to appear on and is reachable only through the effective read below.
    """

    id: UUID
    organization_id: Optional[UUID]
    provider: str
    provider_label: str
    bucket: str
    region: Optional[str]
    endpoint_url: Optional[str]
    public_endpoint: Optional[str]
    path_style: bool
    sse_mode: str
    access_key_id: Optional[str]
    secret_mask: Optional[str]
    key_version: int
    status: str
    is_bundled: bool
    source: str
    managed_by_operator: bool
    created_at: Optional[datetime]
    updated_at: Optional[datetime]
    updated_by: Optional[str]


class EvidenceStorageConfigListResponse(BaseModel):
    items: List[EvidenceStorageConfigResponse]


class EvidenceStorageConfigCreateRequest(BaseModel):
    """What a request may set when creating a configuration.

    ``extra="forbid"`` is doing real work here. Without it, a body carrying
    ``is_bundled: true`` would be accepted and ignored — indistinguishable, to
    whoever sent it, from being accepted and honoured, and one careless
    ``**body.model_dump()`` away from actually being honoured. With it, that
    body is a 422 naming the field.
    """

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(
        ...,
        description="One of: aws_s3, gcs, minio, s3_compatible.",
    )
    bucket: str = Field(..., min_length=1, max_length=255)
    region: str = Field(default="", max_length=64)
    endpoint_url: str = Field(default="", max_length=500)
    public_endpoint: str = Field(default="", max_length=500)
    path_style: Optional[bool] = Field(
        default=None,
        description="Omit to take the provider preset's addressing style.",
    )
    access_key_id: str = Field(default="", max_length=255)
    secret_access_key: str = Field(
        default="",
        description="Write-only. Never returned by any endpoint.",
    )


class EvidenceStorageConfigUpdateRequest(BaseModel):
    """A partial edit of a draft. Every field omitted keeps its stored value.

    An omitted ``secret_access_key`` leaves the stored secret alone, which is
    what lets the Settings screen render the mask, submit the form unchanged
    and not blank the credential.
    """

    model_config = ConfigDict(extra="forbid")

    provider: Optional[str] = None
    bucket: Optional[str] = Field(default=None, min_length=1, max_length=255)
    region: Optional[str] = Field(default=None, max_length=64)
    endpoint_url: Optional[str] = Field(default=None, max_length=500)
    public_endpoint: Optional[str] = Field(default=None, max_length=500)
    path_style: Optional[bool] = None
    access_key_id: Optional[str] = Field(default=None, max_length=255)
    secret_access_key: Optional[str] = None


class EvidenceStorageRotateRequest(BaseModel):
    """A credential rotation. Changes no address and no bucket."""

    model_config = ConfigDict(extra="forbid")

    secret_access_key: str = Field(
        ...,
        min_length=1,
        description="The replacement secret. Write-only.",
    )
    access_key_id: Optional[str] = Field(
        default=None,
        max_length=255,
        description=(
            "New access key id, when the pair is rotated together. Omit to "
            "keep the stored one."
        ),
    )


class EvidenceStorageEffectiveResponse(BaseModel):
    """Where this organisation's evidence actually goes right now.

    The list above answers "what rows exist"; this answers "what is in force",
    and they are different questions whenever the answer is not a row of this
    organisation's own. An organisation with no row of its own resolves to the
    platform row, and an installation with no rows at all resolves to the
    process environment — which is the case every existing installation is in
    today, and the case the Settings screen has to be able to describe.

    **No credential field, not even a mask.** The configuration surface renders
    :data:`SECRET_MASK` because it is showing a row an administrator may edit
    and has to know whether a secret is already stored on. This surface is
    showing an *effective* store that may be the operator's, so it carries the
    address and the provenance and nothing about the credential beyond
    ``configured``. The access key id is omitted for the same reason: for the
    ``legacy_env`` source it is an operator value that no surface discloses
    today, and disclosing it here would be new.

    ``source`` is one of ``org``, ``platform`` or ``legacy_env`` — the
    resolver's own three constants, in its own resolution order.
    ``managed_by_operator`` is true for the last two: a platform row belongs to
    whoever installed the platform and ``legacy_env`` is the process
    environment, and an organisation administrator can change neither from this
    application.

    There is no ``none`` source, because the resolver cannot produce one:
    :func:`services.storage_config.resolve` falls through to
    :func:`services.storage_config.resolve_from_env`, which synthesises a
    ``legacy_env`` configuration whether or not the environment names a bucket.
    An installation that has configured nothing anywhere is therefore
    ``source="legacy_env", configured=false`` — which is the honest answer,
    since the environment *is* what would be consulted — and ``configured`` is
    the flag the Settings screen keys its blank-and-editable state off.
    """

    #: ``None`` for ``legacy_env``, which is synthesised and has no row.
    config_id: Optional[UUID] = None
    source: str
    managed_by_operator: bool
    #: Whether the resolved configuration actually names somewhere to put
    #: bytes. False means no bucket: nothing is configured anywhere.
    configured: bool
    #: True only for the store the installer provisioned inside this stack.
    #: Phase 5's card inferred this from ``source == 'platform' and provider
    #: == 'minio'`` because the field did not exist (D48) — sound only while
    #: the installer remains the sole writer of platform rows, which is not a
    #: property anyone should have to keep true. Always false for
    #: ``legacy_env``, which is the process environment and not a row.
    is_bundled: bool = False

    provider: Optional[str] = None
    provider_label: Optional[str] = None
    bucket: Optional[str] = None
    region: Optional[str] = None
    endpoint_url: Optional[str] = None
    public_endpoint: Optional[str] = None
    #: Addressing style. Path-style for every S3-compatible store.
    path_style: bool = False
    sse_mode: str = ""
    #: Present only when a row carries one; ``None`` for ``legacy_env``, whose
    #: credential has no version to bump.
    key_version: Optional[int] = None


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _serialise(row: EvidenceStorageConfig) -> EvidenceStorageConfigResponse:
    """Render one row. The mask is a constant; the secret is never read."""
    # `preset_for` never raises: an unrecognised provider falls back to the
    # generic S3-compatible preset, so a row written before the CHECK
    # constraint existed still renders and can still be corrected.
    label = storage_config.preset_for(row.provider).label

    return EvidenceStorageConfigResponse(
        id=row.id,
        organization_id=row.organization_id,
        provider=row.provider,
        provider_label=label,
        bucket=row.bucket,
        region=row.region,
        endpoint_url=row.endpoint_url,
        public_endpoint=row.public_endpoint,
        path_style=bool(row.path_style),
        sse_mode=row.sse_mode,
        access_key_id=row.access_key_id,
        secret_mask=SECRET_MASK if row.secret_ciphertext else None,
        key_version=int(row.key_version or 1),
        status=row.status,
        is_bundled=bool(row.is_bundled),
        source=(
            storage_config.SOURCE_PLATFORM
            if row.organization_id is None
            else storage_config.SOURCE_ORG
        ),
        # A platform row is the installer's or the operator's; an organisation
        # administrator reaching it through an organisation URL gets a 404 on
        # every write verb, so "you cannot edit this here" is a fact about the
        # row and is stated rather than inferred by the client.
        managed_by_operator=row.organization_id is None,
        created_at=row.created_at,
        updated_at=row.updated_at,
        updated_by=row.updated_by_label,
    )


def _actor(request: Request, user: User) -> evidence_storage_admin.Actor:
    """Build the audit actor, the way ``api/integrations.py`` does.

    A master-API-key request has no user row, so it is labelled rather than
    attributed: otherwise an automated write would look as though it came from
    whichever human happened to be an administrator.
    """
    if getattr(user, "auth_method", None) == "api_key":
        label = MASTER_API_KEY_ACTOR
        user_id: Optional[UUID] = None
    else:
        label = user.email or "unknown"
        user_id = UUID(user.db_id) if getattr(user, "db_id", None) else None

    return evidence_storage_admin.Actor(
        label=label,
        user_id=user_id,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )


def _not_found() -> HTTPException:
    """404 for "no such row" and for "not yours", with the same body.

    Anti-enumeration: a 403 on another tenant's configuration id would confirm
    the id exists. ``auth.assert_user_in_org`` answers the same way for the
    same reason.
    """
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Storage configuration not found",
    )


async def _writable_row(
    org_id: UUID, config_id: UUID, db: AsyncSession
) -> EvidenceStorageConfig:
    """Load a row this organisation may change, or raise 404.

    Strictly org-scoped, and that is not an oversight. The platform-scope row
    (``organization_id IS NULL``) holds the catalogue workbook and
    reconciliation artefacts for every tenant, so no request arriving through
    an organisation's URL may write to it — not even one from a platform
    administrator, who has an unambiguous path through the platform admin
    surface instead. Reads are looser; see :func:`_readable_row`.
    """
    result = await db.execute(
        select(EvidenceStorageConfig).where(EvidenceStorageConfig.id == config_id)
    )
    row = result.scalar_one_or_none()
    if row is None or row.organization_id != org_id:
        raise _not_found()
    return row


async def _readable_row(
    org_id: UUID, config_id: UUID, membership: OrgMembership, db: AsyncSession
) -> EvidenceStorageConfig:
    """Load a row this organisation may see, or raise 404.

    Adds one case to :func:`_writable_row`: a platform administrator may read
    the platform-scope row through an organisation's URL, because that row is
    part of the answer to "where does this organisation's evidence go" when the
    organisation has no row of its own.
    """
    result = await db.execute(
        select(EvidenceStorageConfig).where(EvidenceStorageConfig.id == config_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise _not_found()
    if row.organization_id is None:
        if not await user_is_platform_admin(membership.user, db):
            raise _not_found()
    elif row.organization_id != org_id:
        raise _not_found()
    return row


async def _org_file_count_for_config(
    org_id: UUID, config_id: UUID, db: AsyncSession
) -> int:
    """How many of *this* organisation's evidence files name ``config_id``.

    Scoped to the organisation deliberately. The platform row is shared, so a
    count across every tenant would answer a question nobody asked and would
    let one organisation's evidence keep another's copy alive.
    """
    return int(
        (
            await db.execute(
                select(func.count())
                .select_from(EvidenceFile)
                .where(
                    EvidenceFile.organization_id == org_id,
                    EvidenceFile.storage_config_id == config_id,
                )
            )
        ).scalar_one()
    )


async def _source_row(
    org_id: UUID, config_id: UUID, db: AsyncSession
) -> EvidenceStorageConfig:
    """Load a row this organisation may copy *out of*, or raise 404.

    Wider than :func:`_writable_row` and narrower than :func:`_readable_row`,
    because copying out of a store is a read of it — and the store an
    organisation most needs to copy out of is one it does not own.

    The ordinary sequence is: install with the bundled store, write evidence to
    it, then bring your own store. Activating that first own configuration
    stamps every existing file with the **platform** row, because that is
    genuinely where the bytes are. If the copy route would only accept
    org-scoped rows — which is what it did — every file written before the
    switch is stranded on the bundled store with no way to ask for it to be
    moved, and the feature's primary use case has no exit.

    So a platform-scope row is accepted as a source on ONE claim: this
    organisation has files stamped to it. "It resolves there now" reads like a
    second reasonable claim and is not one — the resolver falls back to the
    platform row for *any* organisation without a configuration of its own, so
    that clause would admit every tenant on this installation to the shared
    store. Another tenant's row is never accepted, and neither is a platform
    row this organisation has nothing in — 404 in both cases, the same body,
    for the anti-enumeration reason in :func:`_not_found`.

    This is source-only. The target still goes through :func:`_writable_row`,
    so no copy can ever write into the shared platform store.
    """
    result = await db.execute(
        select(EvidenceStorageConfig).where(EvidenceStorageConfig.id == config_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise _not_found()

    if row.organization_id == org_id:
        return row

    if row.organization_id is not None:
        raise _not_found()

    # Platform scope. The claim is the stamp, and only the stamp: this
    # organisation's own evidence files naming this row. "The organisation
    # resolves there" looks like a second reasonable claim and is not one —
    # *every* organisation without a configuration of its own resolves to the
    # platform row, including one that has never written a byte, so that test
    # admits everybody and scopes nothing.
    #
    # It also costs nothing to drop. An organisation that resolves to the
    # platform row has no active store of its own, and the target of a copy
    # must be exactly that, so such a request has no valid target anyway.
    if await _org_file_count_for_config(org_id, config_id, db):
        return row

    raise _not_found()


def _spec_error(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
    )


def _no_key() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT, detail=NO_ENCRYPTION_KEY_DETAIL
    )


def _merged_spec(
    row: EvidenceStorageConfig, body: EvidenceStorageConfigUpdateRequest
) -> evidence_storage_admin.ConfigSpec:
    """Fold a partial edit onto the stored row.

    ``model_fields_set`` rather than ``is not None``: an explicit
    ``"region": null`` and an omitted ``region`` are different intents, and
    only the omitted one should keep the stored value.
    """
    given = body.model_fields_set

    def pick(name: str, stored) -> Any:
        if name not in given:
            return stored
        return getattr(body, name)

    return evidence_storage_admin.ConfigSpec(
        provider=pick("provider", row.provider) or row.provider,
        bucket=pick("bucket", row.bucket) or "",
        region=pick("region", row.region) or "",
        endpoint_url=pick("endpoint_url", row.endpoint_url) or "",
        public_endpoint=pick("public_endpoint", row.public_endpoint) or "",
        path_style=(
            body.path_style if "path_style" in given else bool(row.path_style)
        ),
        access_key_id=pick("access_key_id", row.access_key_id) or "",
        # Omitted means "leave the stored secret alone"; the service treats an
        # empty string that way.
        secret_access_key=(body.secret_access_key or "") if "secret_access_key" in given else "",
    )


# ---------------------------------------------------------------------------
# Phase 3 endpoints
# ---------------------------------------------------------------------------
#
# Route order matters. ``/evidence-storage/test`` is declared above, before
# ``/evidence-storage/{config_id}``, so the literal path is matched as itself
# rather than parsed as a configuration id and answered with a 422 about an
# invalid UUID.


@router.post(
    "/organizations/{org_id}/evidence-storage",
    response_model=EvidenceStorageConfigResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a draft evidence storage configuration",
    description=(
        "Creates a configuration in the draft state. A draft is inert: nothing "
        "resolves it and no evidence is written to it until it is activated, "
        "which runs the connection probe first."
    ),
)
@rate_limit_write
async def create_evidence_storage_config(
    request: Request,
    response: Response,
    org_id: UUID,
    body: EvidenceStorageConfigCreateRequest,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    spec = evidence_storage_admin.ConfigSpec(
        provider=body.provider,
        bucket=body.bucket,
        region=body.region,
        endpoint_url=body.endpoint_url,
        public_endpoint=body.public_endpoint,
        path_style=body.path_style,
        access_key_id=body.access_key_id,
        secret_access_key=body.secret_access_key,
    )
    try:
        row = await evidence_storage_admin.create_config(
            db, org_id, spec, _actor(request, membership.user)
        )
    except evidence_storage_admin.StorageConfigSpecError as exc:
        raise _spec_error(exc)
    except storage_config.StorageConfigError as exc:
        raise _spec_error(exc)
    except (crypto.SecretKeyMissing, crypto.SecretKeyInvalid):
        raise _no_key()

    return _serialise(row)


@router.get(
    "/organizations/{org_id}/evidence-storage",
    response_model=EvidenceStorageConfigListResponse,
    summary="List this organisation's evidence storage configurations",
    description=(
        "Every configuration belonging to this organisation, newest first. The "
        "platform-scope configuration is not listed here: it is not this "
        "organisation's to change."
    ),
)
@rate_limit_read
async def list_evidence_storage_configs(
    request: Request,
    response: Response,
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(EvidenceStorageConfig)
        .where(EvidenceStorageConfig.organization_id == org_id)
        .order_by(EvidenceStorageConfig.created_at.desc())
    )
    return EvidenceStorageConfigListResponse(
        items=[_serialise(row) for row in result.scalars().all()]
    )


def _effective(config: storage_config.ResolvedStorageConfig) -> EvidenceStorageEffectiveResponse:
    """Render a resolved configuration. Reads no credential and carries none.

    Empty strings become ``None``: the resolver uses ``""`` for "not set" on
    every optional address field, and a client that had to distinguish an empty
    string from an absent value would get it wrong the first time.
    """

    def maybe(value: Optional[str]) -> Optional[str]:
        value = (value or "").strip()
        return value or None

    # `legacy-env` is a literal, not an id; anything that is not a row id
    # renders as "no row", rather than raising on the way out.
    try:
        config_id: Optional[UUID] = UUID(config.config_id)
    except (TypeError, ValueError):
        config_id = None

    version = (config.credential_version or "").strip()

    return EvidenceStorageEffectiveResponse(
        config_id=config_id,
        source=config.source,
        managed_by_operator=config.source != storage_config.SOURCE_ORG,
        configured=config.is_configured,
        is_bundled=bool(config.is_bundled),
        provider=config.provider,
        provider_label=storage_config.preset_for(config.provider).label,
        bucket=maybe(config.bucket),
        region=maybe(config.region),
        endpoint_url=maybe(config.endpoint_url),
        public_endpoint=maybe(config.public_endpoint),
        path_style=bool(config.path_style),
        sse_mode=config.sse_mode,
        key_version=int(version) if version.isdigit() else None,
    )


@router.get(
    "/organizations/{org_id}/evidence-storage/effective",
    response_model=EvidenceStorageEffectiveResponse,
    summary="Read the evidence storage configuration in force for this organisation",
    description=(
        "The configuration this organisation's evidence is actually written "
        "to, after resolution: its own active configuration, else the platform "
        "one, else the process environment. Carries the source it came from "
        "and whether the operator manages it. No credential, masked or "
        "otherwise, is returned."
    ),
)
@rate_limit_read
async def read_effective_evidence_storage_config(
    request: Request,
    response: Response,
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("admin")),
):
    """What is in force, not what rows exist.

    Declared **before** ``/evidence-storage/{config_id}`` on purpose: FastAPI
    matches routes in declaration order, so the other way round the literal
    ``effective`` would be parsed as a configuration id and answered with a 422
    about an invalid UUID. There is a test asserting the order.

    No ``db`` session is taken: the resolver reads rows over its own
    synchronous, cached connection, and its snapshot is shared with the
    workers. That is the point — this read answers with what a worker writing
    evidence right now would use, not with a fresh query that might disagree
    with it.
    """
    try:
        config = await asyncio.to_thread(storage_config.resolve, str(org_id))
    except storage_config.StorageConfigError as exc:
        # The one thing resolution refuses to do is fall back past a stored
        # secret it cannot decrypt — silently resolving to a different store
        # would send evidence somewhere nobody chose. Report it as the
        # configuration conflict it is, in the shape the Settings screen
        # already unwraps for the missing-key 409.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": str(exc)},
        )

    return _effective(config)


# ---------------------------------------------------------------------------
# Copy between stores (Phase 6)
# ---------------------------------------------------------------------------
# NOTE ON ORDER: everything in this section is declared BEFORE the
# `/{config_id}` routes below. FastAPI matches in declaration order, so a
# `copy-runs` path declared after `/{config_id}` would be captured by it and
# answered with a 422 for a config id that is not a UUID. The `/effective`
# route above is placed before `/{config_id}` for the same reason.


class EvidenceStorageCopyFailure(BaseModel):
    """One object the copy could not move, and why.

    The reason is written by the task and names a class of failure — a missing
    object, a size disagreement, a checksum mismatch, an exception type. It
    never carries a store's response body, a URL or a credential, for the same
    reason the connection probe does not.
    """

    model_config = ConfigDict(extra="forbid")

    s3_key: str
    reason: str


class EvidenceStorageCopyRunResponse(BaseModel):
    """A copy run, as a client sees it.

    Names no credential-bearing field, exactly like
    :class:`EvidenceStorageConfigResponse`, and for the same reason: the
    response model is the boundary, so a field that cannot be named cannot be
    leaked by a later refactor.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    organization_id: str
    source_config_id: str
    target_config_id: str
    status: str
    total: int
    copied: int
    failed: int
    skipped: int
    remaining: int
    failures: List[EvidenceStorageCopyFailure] = Field(default_factory=list)
    source_retired: bool = False
    #: Why the source was not retired, when it was not. A store this
    #: organisation does not own — the shared platform store above all — is
    #: never retired by a copy, and saying so plainly is the difference
    #: between an operator emptying a bucket and an operator not emptying a
    #: bucket that other tenants are still writing to.
    source_retired_reason: str = ""
    message: str = ""
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    updated_at: Optional[str] = None


def _copy_run_response(record: Dict[str, Any]) -> EvidenceStorageCopyRunResponse:
    """Build the response from a run record, field by named field.

    Deliberately not ``**record``: the record is a dict this process wrote to
    Redis and read back, and constructing the model from whatever keys it
    happens to carry is how a field nobody intended to publish gets published.
    """
    return EvidenceStorageCopyRunResponse(
        run_id=str(record.get("run_id") or ""),
        organization_id=str(record.get("organization_id") or ""),
        source_config_id=str(record.get("source_config_id") or ""),
        target_config_id=str(record.get("target_config_id") or ""),
        status=str(record.get("status") or ""),
        total=int(record.get("total") or 0),
        copied=int(record.get("copied") or 0),
        failed=int(record.get("failed") or 0),
        skipped=int(record.get("skipped") or 0),
        remaining=int(record.get("remaining") or 0),
        failures=[
            EvidenceStorageCopyFailure(
                s3_key=str(item.get("s3_key") or ""),
                reason=str(item.get("reason") or ""),
            )
            for item in (record.get("failures") or [])
            if isinstance(item, dict)
        ],
        source_retired=bool(record.get("source_retired")),
        source_retired_reason=str(record.get("source_retired_reason") or ""),
        message=str(record.get("message") or ""),
        started_at=record.get("started_at"),
        finished_at=record.get("finished_at"),
        updated_at=record.get("updated_at"),
    )


@router.post(
    "/organizations/{org_id}/evidence-storage/{config_id}/copy-to/{target_config_id}",
    response_model=EvidenceStorageCopyRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Copy this organisation's evidence from one store to another",
    description=(
        "Enqueues a background copy of every evidence object this organisation "
        "holds under the source configuration into the target configuration. "
        "Each object is verified by size and checksum and each file is moved "
        "one at a time, so a re-trigger resumes rather than repeating. Nothing "
        "is ever deleted from the source. The source configuration is retired "
        "only once no evidence file references it."
    ),
)
@rate_limit_write
async def copy_evidence_storage(
    request: Request,
    response: Response,
    org_id: UUID,
    config_id: UUID,
    target_config_id: UUID,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """Start a copy into one of this organisation's own active stores.

    The **target** must belong to this organisation and be active. The
    **source** may also be the platform-scope store when this organisation
    resolves there or has files stamped to it, because that is where a bundled
    install's evidence actually is and there is otherwise no way to ask for it
    to be moved. See :func:`_source_row`. The platform store is never emptied
    or retired by a copy; other tenants keep using it.

    Refusals, and why each one is a refusal rather than something the task
    works out later:

    * Either id belonging to another tenant, or not existing — 404, the same
      body for both, like every other route here. A platform id this
      organisation has no claim on answers the same way.
    * The platform row named as the **target** — 404, by the same rule: it is
      not a row this organisation may write to.
    * Source and target the same — 422. There is no such thing as copying a
      store onto itself, and letting it run would retire the organisation's
      only active configuration at the end.
    * Target not ``active`` — 422. A draft has not passed a connection test, so
      the copy's first write would be the first time anyone found out whether
      the platform can write there at all.
    * A run already in flight for this organisation — 409. Two runs over the
      same rows would both read every object and race on the same commits.
    """
    from tasks_evidence_storage_copy import (
        active_run_id,
        claim_active,
        clear_active,
        copy_evidence_store,
        new_run_record,
        write_run,
    )

    source = await _source_row(org_id, config_id, db)
    target = await _writable_row(org_id, target_config_id, db)

    if source.id == target.id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The source and target evidence stores are the same configuration.",
        )

    if target.status != storage_config.STATUS_ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "The target evidence store is not active. Test and activate it "
                "first, so the copy is not the first thing to discover whether "
                "the platform can write to it."
            ),
        )

    in_flight = await asyncio.to_thread(active_run_id, str(org_id))
    if in_flight:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": (
                    "A copy is already running for this organisation. Wait for "
                    "it to finish, or re-trigger it once it has — a re-run "
                    "resumes rather than repeating work."
                ),
                "run_id": in_flight,
            },
        )

    run_id = str(uuid4())
    if not await asyncio.to_thread(claim_active, str(org_id), run_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "A copy is already running for this organisation.",
                "run_id": await asyncio.to_thread(active_run_id, str(org_id)) or "",
            },
        )

    record = new_run_record(
        run_id, str(org_id), str(source.id), str(target.id)
    )
    await asyncio.to_thread(write_run, record)

    try:
        copy_evidence_store.delay(
            str(org_id), str(source.id), str(target.id), run_id
        )
    except Exception:  # noqa: BLE001 — a broker that refused must not hold the slot
        await asyncio.to_thread(clear_active, str(org_id))
        logger.exception("Could not enqueue evidence store copy for org %s", org_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The copy could not be queued. Try again shortly.",
        )

    logger.info(
        "Evidence store copy %s queued for organisation %s: %s -> %s by %s",
        run_id,
        org_id,
        source.id,
        target.id,
        _actor(request, membership.user).label,
    )
    return _copy_run_response(record)


class EvidenceStorageCopySourceResponse(BaseModel):
    """One store this organisation could copy evidence out of.

    Separate from the configuration list on purpose. The list answers "what
    configurations does this organisation own", which is what the settings card
    renders and edits; this answers "where is this organisation's evidence",
    which has one more entry in it — the platform store a bundled install wrote
    to before the organisation brought its own.

    It carries no credential and no ``is_bundled`` control (D42/D36): the
    platform entry is a read-only fact about where bytes are, never something
    the client can act on beyond naming it as a source.
    """

    model_config = ConfigDict(extra="forbid")

    config_id: UUID
    scope: str
    provider: str
    provider_label: str
    bucket: str
    endpoint_url: Optional[str] = None
    status: str
    file_count: int


@router.get(
    "/organizations/{org_id}/evidence-storage/copy-sources",
    response_model=List[EvidenceStorageCopySourceResponse],
    summary="Stores this organisation could copy evidence out of",
    description=(
        "Every store that holds evidence belonging to this organisation and "
        "is not the store in force now: the organisation's own earlier "
        "configurations, and — when a bundled install wrote to it before the "
        "organisation brought its own store — the platform store. A copy out "
        "of the platform store never empties or retires it."
    ),
)
@rate_limit_read
async def list_evidence_storage_copy_sources(
    request: Request,
    response: Response,
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """The source list, built from where the bytes are rather than from ownership.

    The active own row is excluded: it is the target, and a copy onto itself is
    refused by the route that starts one.
    """
    rows = (
        (
            await db.execute(
                select(EvidenceStorageConfig)
                .where(EvidenceStorageConfig.organization_id == org_id)
                .order_by(EvidenceStorageConfig.created_at.asc())
            )
        )
        .scalars()
        .all()
    )

    out: List[EvidenceStorageCopySourceResponse] = []
    for row in rows:
        if row.status == storage_config.STATUS_ACTIVE:
            continue
        out.append(
            EvidenceStorageCopySourceResponse(
                config_id=row.id,
                scope=storage_config.SOURCE_ORG,
                provider=row.provider,
                provider_label=storage_config.preset_for(row.provider).label,
                bucket=row.bucket or "",
                endpoint_url=row.endpoint_url,
                status=row.status,
                file_count=await _org_file_count_for_config(org_id, row.id, db),
            )
        )

    # The platform store, but only when this organisation's evidence is
    # actually in it. Offering it otherwise would invite a copy that moves
    # nothing and would name a store the organisation has no business in.
    platform = (
        (
            await db.execute(
                select(EvidenceStorageConfig).where(
                    EvidenceStorageConfig.organization_id.is_(None),
                    EvidenceStorageConfig.status.in_(
                        [storage_config.STATUS_ACTIVE, storage_config.STATUS_RETIRED]
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    for row in platform:
        count = await _org_file_count_for_config(org_id, row.id, db)
        if not count:
            continue
        out.append(
            EvidenceStorageCopySourceResponse(
                config_id=row.id,
                scope=storage_config.SOURCE_PLATFORM,
                provider=row.provider,
                provider_label=storage_config.preset_for(row.provider).label,
                bucket=row.bucket or "",
                endpoint_url=row.endpoint_url,
                status=row.status,
                file_count=count,
            )
        )

    return out


@router.get(
    "/organizations/{org_id}/evidence-storage/copy-runs",
    response_model=List[EvidenceStorageCopyRunResponse],
    summary="Recent evidence store copy runs for this organisation",
)
@rate_limit_read
async def list_evidence_storage_copy_runs(
    request: Request,
    response: Response,
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("admin")),
):
    from tasks_evidence_storage_copy import list_runs

    records = await asyncio.to_thread(list_runs, str(org_id))
    return [_copy_run_response(record) for record in records]


@router.get(
    "/organizations/{org_id}/evidence-storage/copy-runs/{run_id}",
    response_model=EvidenceStorageCopyRunResponse,
    summary="One evidence store copy run",
)
@rate_limit_read
async def get_evidence_storage_copy_run(
    request: Request,
    response: Response,
    org_id: UUID,
    run_id: str,
    membership: OrgMembership = Depends(require_org_role("admin")),
):
    """One run, or 404.

    A run belonging to another organisation answers 404 with the same body as a
    run that does not exist, for the same anti-enumeration reason every other
    route here does. The check is on the record's own organisation id, not on
    the URL: the run id is a UUID this server minted, and trusting the path to
    scope it would make the endpoint readable by any admin of any tenant.
    """
    from tasks_evidence_storage_copy import read_run

    record = await asyncio.to_thread(read_run, str(run_id))
    if record is None or str(record.get("organization_id")) != str(org_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Copy run not found",
        )
    return _copy_run_response(record)


@router.get(
    "/organizations/{org_id}/evidence-storage/{config_id}",
    response_model=EvidenceStorageConfigResponse,
    summary="Read one evidence storage configuration",
)
@rate_limit_read
async def get_evidence_storage_config(
    request: Request,
    response: Response,
    org_id: UUID,
    config_id: UUID,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    row = await _readable_row(org_id, config_id, membership, db)
    return _serialise(row)


@router.patch(
    "/organizations/{org_id}/evidence-storage/{config_id}",
    response_model=EvidenceStorageConfigResponse,
    summary="Edit a draft evidence storage configuration",
    description=(
        "Only a draft may be edited. An active configuration is where evidence "
        "is being written right now and a retired one may still be the only "
        "place some evidence can be read from, so both refuse an in-place "
        "edit with 409. Rotate the credential, or create a new configuration "
        "and activate it."
    ),
)
@rate_limit_write
async def update_evidence_storage_config(
    request: Request,
    response: Response,
    org_id: UUID,
    config_id: UUID,
    body: EvidenceStorageConfigUpdateRequest,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    row = await _writable_row(org_id, config_id, db)
    try:
        updated = await evidence_storage_admin.update_config(
            db, row, _merged_spec(row, body), _actor(request, membership.user)
        )
    except evidence_storage_admin.StorageConfigImmutable as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except evidence_storage_admin.StorageConfigSpecError as exc:
        raise _spec_error(exc)
    except storage_config.StorageConfigError as exc:
        raise _spec_error(exc)
    except (crypto.SecretKeyMissing, crypto.SecretKeyInvalid):
        raise _no_key()

    return _serialise(updated)


@router.post(
    "/organizations/{org_id}/evidence-storage/{config_id}/activate",
    response_model=EvidenceStorageConfigResponse,
    summary="Activate an evidence storage configuration",
    description=(
        "Runs the write, read-back and delete probe against the configuration "
        "and, only if every step passes, retires the currently active one and "
        "makes this one live — both statements in one transaction. A failing "
        "probe is a 409 carrying the per-step report."
    ),
)
@rate_limit_write
async def activate_evidence_storage_config(
    request: Request,
    response: Response,
    org_id: UUID,
    config_id: UUID,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    # The row is loaded once here to close the cross-tenant case with a 404
    # before anything is probed, and once again inside `activate_config`, which
    # is the only place activation happens for any caller.
    await _writable_row(org_id, config_id, db)

    try:
        row = await evidence_storage_admin.activate_config(
            db, config_id, actor=_actor(request, membership.user)
        )
    except evidence_storage_admin.StorageActivationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": str(exc),
                "report": exc.report,
                **exc.details,
            },
        )
    except storage_config.StorageConfigError as exc:
        raise _spec_error(exc)

    return _serialise(row)


@router.post(
    "/organizations/{org_id}/evidence-storage/{config_id}/rotate",
    response_model=EvidenceStorageConfigResponse,
    summary="Rotate the stored credential",
    description=(
        "Re-encrypts the configuration under a new secret and bumps its key "
        "version, which every process uses as part of its storage client cache "
        "key — so the next operation in every worker builds a new client. An "
        "active configuration is probed with the new credential before "
        "anything is written; a failing probe leaves the old credential in "
        "place and answers 409."
    ),
)
@rate_limit_write
async def rotate_evidence_storage_secret(
    request: Request,
    response: Response,
    org_id: UUID,
    config_id: UUID,
    body: EvidenceStorageRotateRequest,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    row = await _writable_row(org_id, config_id, db)
    try:
        rotated = await evidence_storage_admin.rotate_secret(
            db,
            row,
            body.secret_access_key,
            access_key_id=body.access_key_id,
            actor=_actor(request, membership.user),
        )
    except evidence_storage_admin.StorageActivationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": str(exc), "report": exc.report},
        )
    except evidence_storage_admin.StorageConfigSpecError as exc:
        raise _spec_error(exc)
    except storage_config.StorageConfigError as exc:
        raise _spec_error(exc)
    except (crypto.SecretKeyMissing, crypto.SecretKeyInvalid):
        raise _no_key()

    return _serialise(rotated)


@router.post(
    "/organizations/{org_id}/evidence-storage/{config_id}/retire",
    response_model=EvidenceStorageConfigResponse,
    summary="Take an evidence storage configuration out of service",
    description=(
        "The row stays, so evidence already written under it keeps a "
        "resolvable configuration. What changes is that the resolver stops "
        "loading it, and the organisation falls back to the platform "
        "configuration and then to the environment."
    ),
)
@rate_limit_write
async def retire_evidence_storage_config(
    request: Request,
    response: Response,
    org_id: UUID,
    config_id: UUID,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    row = await _writable_row(org_id, config_id, db)
    retired = await evidence_storage_admin.retire_config(
        db, row, _actor(request, membership.user)
    )
    return _serialise(retired)


@router.delete(
    "/organizations/{org_id}/evidence-storage/{config_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an evidence storage configuration",
    description=(
        "Refused with 409 while the configuration is active, and refused with "
        "409 and a file count while any evidence file still holds its bytes "
        "under it."
    ),
)
@rate_limit_write
async def delete_evidence_storage_config(
    request: Request,
    response: Response,
    org_id: UUID,
    config_id: UUID,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    row = await _writable_row(org_id, config_id, db)
    try:
        await evidence_storage_admin.delete_config(
            db, row, _actor(request, membership.user)
        )
    except evidence_storage_admin.StorageConfigImmutable as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except evidence_storage_admin.StorageConfigInUse as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": str(exc), "evidence_file_count": exc.file_count},
        )

    # Nothing is returned: FastAPI then uses the injected `response`, which is
    # the object slowapi wrote the rate-limit headers into. Returning a fresh
    # Response here would discard them.
    return None
