"""Writing and activating an evidence storage configuration.

Phases 2 and 3 of the bring-your-own evidence storage work: Phase 2 added the
activation gate, Phase 3 the create, edit, rotate, retire and delete paths that
put an organisation's own credential in the database.

Every rule that matters lives here rather than in the API layer, so that the
Settings screen (Phase 5), the installer's seeding path (Phase 4) and any later
admin route all reach the same one. A rule enforced in a route holds only for
requests that happen to come through that route.

The rules, and why each is here:

**The probe gates activation.** :func:`activate_config` runs the real
round-trip probe against the row it is about to activate and refuses on
failure. The refusal carries the per-step report, so the caller can say *which*
step failed without the service having to format a message.

**Activation is two statements and must be one transaction.** The partial
unique indexes allow exactly one ``active`` row per scope, so the currently
active row has to be retired *before* the new one is activated or the second
statement trips the index. Doing that in one transaction is also what stops a
crash between the two leaving a scope with no active configuration at all.

**The address is validated on every save, not only on activation.** An
administrator finds out that an endpoint is refused when they save it, rather
than when the first evidence upload fails.

**Only drafts are editable.** An ``active`` row is serving traffic and a
``retired`` one may still be the only place some evidence can be read from, so
neither may be edited in place. Both can be rotated — a credential rotation
changes no address and no bucket — and a draft can be edited freely because
nothing resolves it.

**`is_bundled` is never written here.** It marks the store the installer
provisioned, and it is the *only* thing that exempts a row from the tenant half
of the address policy. Nothing that can be reached from a request may set it;
see :func:`create_config`.

**Nothing ever returns a stored secret.** This module hands back ORM rows; the
API layer's response model is what guarantees the secret is not serialised, and
there is a test asserting that model's exact field set.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models import EvidenceFile, EvidenceStorageConfig
from services import crypto, storage_config, storage_service
from services.platform_audit import record_platform_event

logger = logging.getLogger(__name__)

#: Entity type for the platform audit trail. Storage configuration changes are
#: recorded the same way integration credential changes are — in
#: ``platform_audit_log``, which carries **no value columns at all**, so it is
#: structurally incapable of leaking a credential. The webhook rotation
#: precedent writes ``new_value="[rotated]"`` into the org audit log; the
#: equivalent here is the action name, because there is no value column to put
#: it in. That is the stronger of the two shapes, not a weaker substitute.
ENTITY_TYPE = "evidence_storage_config"

ACTION_CREATED = "evidence_storage.config.created"
ACTION_UPDATED = "evidence_storage.config.updated"
ACTION_ACTIVATED = "evidence_storage.config.activated"
ACTION_ROTATED = "evidence_storage.config.rotated"
ACTION_RETIRED = "evidence_storage.config.retired"
ACTION_DELETED = "evidence_storage.config.deleted"


class StorageActivationError(RuntimeError):
    """Activation was refused. Carries the probe report when there is one.

    ``details`` carries any additional named facts the refusal turns on — a
    count of the files in the way, say — so the 409 body can be actionable
    without the message having to be parsed for a number.
    """

    def __init__(
        self,
        message: str,
        report: Optional[dict] = None,
        details: Optional[dict] = None,
    ):
        super().__init__(message)
        self.report = report or {}
        self.details = details or {}


class StorageConfigImmutable(RuntimeError):
    """An edit was attempted on a row that is not a draft."""


class StorageConfigInUse(RuntimeError):
    """A delete was refused because evidence files still reference the row.

    The foreign key is ``ON DELETE RESTRICT``, so the database would refuse it
    anyway — as an ``IntegrityError``, which reaches an administrator as a 500
    and says nothing actionable. Counting first turns that into a 409 naming
    how many files are in the way.
    """

    def __init__(self, message: str, file_count: int = 0):
        super().__init__(message)
        self.file_count = file_count


@dataclass(frozen=True)
class Actor:
    """Who made the change, for the platform audit row.

    Mirrors ``services.integration_secrets.Actor`` field for field so the two
    audit trails read identically.
    """

    label: str
    user_id: Optional[UUID] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    action_source: Optional[str] = None
    request_id: Optional[UUID] = None


@dataclass(frozen=True)
class ConfigSpec:
    """The fields a create or an edit may set.

    Deliberately **not** a superset of the table. ``is_bundled``, ``status``,
    ``key_version``, ``organization_id`` and ``secret_ciphertext`` are absent
    because no request may set any of them: the first is a security-relevant
    installer flag, the next two are state the service owns, the fourth is
    taken from the URL, and the last is derived from the plaintext secret. A
    request model that named any of them would be one refactor away from
    letting a tenant set it.
    """

    provider: str
    bucket: str
    region: str = ""
    endpoint_url: str = ""
    public_endpoint: str = ""
    path_style: Optional[bool] = None
    access_key_id: str = ""
    secret_access_key: str = ""


class StorageConfigSpecError(ValueError):
    """The submitted configuration is not a coherent one."""


def _preset_or_refuse(provider: str) -> storage_config.ProviderPreset:
    if provider not in storage_config.PRESETS:
        raise StorageConfigSpecError(
            f"Unknown evidence storage provider {provider!r}. "
            f"Choose one of: {', '.join(sorted(storage_config.PRESETS))}."
        )
    return storage_config.preset_for(provider)


def resolved_from_spec(
    spec: ConfigSpec,
    *,
    config_id: str,
    organization_id: Optional[str],
    credential_version: str = "1",
) -> storage_config.ResolvedStorageConfig:
    """Build the value object a spec describes, for validation and probing.

    Goes through :func:`services.storage_config.config_from_preset`, which is
    what makes the provider choice mean the same thing here, in the installer
    and in the driver's client construction. It defaults
    ``endpoint_is_operator_supplied`` to ``False`` — the tenant half of the
    address policy — and this function never overrides that.

    A preset whose endpoint is a property of the provider (Amazon S3 has none;
    Google Cloud Storage has exactly one) refuses a differing endpoint rather
    than silently ignoring it, because an administrator who typed one deserves
    to be told it will not be used.
    """
    preset = _preset_or_refuse(spec.provider)

    endpoint = (spec.endpoint_url or "").strip()
    if preset.endpoint_is_fixed:
        if endpoint and endpoint.rstrip("/") != preset.endpoint_url.rstrip("/"):
            raise StorageConfigSpecError(
                f"{preset.label} does not take a custom endpoint URL."
            )
        endpoint = preset.endpoint_url
    elif not endpoint:
        # Fall back to the preset's own endpoint rather than to nothing. An
        # empty endpoint means "Amazon S3, derived from the region", so a MinIO
        # row saved without one would quietly become an Amazon S3 row pointed at
        # a bucket that does not exist there. Taking the preset's endpoint
        # instead makes the same mistake arrive as an address refusal, which
        # says what is wrong.
        endpoint = preset.endpoint_url

    if not endpoint and spec.provider != storage_config.PROVIDER_AWS_S3:
        raise StorageConfigSpecError(
            f"{preset.label} needs an endpoint URL. Only Amazon S3 derives its "
            "own endpoint from the region."
        )

    bucket = (spec.bucket or "").strip()
    if not bucket:
        raise StorageConfigSpecError("A bucket name is required.")

    if preset.requires_credentials and not (spec.access_key_id or "").strip():
        raise StorageConfigSpecError(
            f"{preset.label} requires an access key id and a secret access key."
        )

    return storage_config.config_from_preset(
        spec.provider,
        config_id=config_id,
        source=(
            storage_config.SOURCE_PLATFORM
            if organization_id is None
            else storage_config.SOURCE_ORG
        ),
        bucket=bucket,
        region=(spec.region or "").strip() or preset.default_region,
        endpoint_url=endpoint,
        public_endpoint=(spec.public_endpoint or "").strip(),
        path_style=preset.path_style if spec.path_style is None else bool(spec.path_style),
        access_key_id=(spec.access_key_id or "").strip(),
        secret_access_key=spec.secret_access_key or "",
        credential_version=credential_version,
        organization_id=organization_id,
    )


async def _audit(
    session: AsyncSession,
    config_id: UUID,
    action: str,
    actor: Optional[Actor],
) -> None:
    """Add the audit row to the caller's transaction. The caller commits."""
    await record_platform_event(
        session,
        entity_type=ENTITY_TYPE,
        entity_id=str(config_id),
        action=action,
        actor=(actor.label if actor else "unknown"),
        actor_user_id=(actor.user_id if actor else None),
        ip_address=(actor.ip_address if actor else None),
        user_agent=(actor.user_agent if actor else None),
        action_source=(actor.action_source if actor else None),
        request_id=(actor.request_id if actor else None),
    )


def _stamp(row: EvidenceStorageConfig, actor: Optional[Actor]) -> None:
    if actor is None:
        return
    if actor.user_id is not None:
        row.updated_by_user_id = actor.user_id
    if actor.label:
        row.updated_by_label = actor.label[:200]


async def create_config(
    session: AsyncSession,
    organization_id: UUID,
    spec: ConfigSpec,
    actor: Optional[Actor] = None,
) -> EvidenceStorageConfig:
    """Create a **draft** configuration for one organisation.

    Draft, always. A new row cannot arrive active: activation runs the probe,
    and a configuration that has never been proved to work must not be able to
    become the place an organisation's evidence goes.

    ``is_bundled`` is not a parameter and is not settable through any path that
    reaches here. It is the flag that exempts a row from the loopback, RFC1918,
    CGNAT and ``.local`` address rules and permits the ``http`` scheme, so a
    request that could set it would be a request that could point the backend
    at the operator's own network. Only the installer writes it.

    The secret is encrypted before the row is built, not after, so a missing
    ``SCF_SECRET_KEY`` is refused before anything is written rather than
    leaving a half-made row behind.
    """
    resolved = resolved_from_spec(
        spec,
        config_id="(unsaved)",
        organization_id=str(organization_id),
    )
    # Save-time address validation, before the row exists. Raises
    # StorageConfigError, which the API maps to 422.
    storage_config.validate_config_for_save(resolved)

    ciphertext = (
        crypto.encrypt(spec.secret_access_key)
        if (spec.secret_access_key or "").strip()
        else None
    )

    row = EvidenceStorageConfig(
        organization_id=organization_id,
        provider=resolved.provider,
        bucket=resolved.bucket,
        region=resolved.region,
        endpoint_url=resolved.endpoint_url,
        public_endpoint=resolved.public_endpoint,
        path_style=resolved.path_style,
        sse_mode=resolved.sse_mode,
        access_key_id=resolved.access_key_id,
        secret_ciphertext=ciphertext,
        key_version=1,
        status=storage_config.STATUS_DRAFT,
        is_bundled=False,
    )
    _stamp(row, actor)
    session.add(row)
    await session.flush()

    await _audit(session, row.id, ACTION_CREATED, actor)
    await session.commit()
    await session.refresh(row)

    logger.info(
        "Evidence storage configuration %s created as a draft for org %s by %s",
        row.id,
        organization_id,
        actor.label if actor else "unknown",
    )
    return row


async def update_config(
    session: AsyncSession,
    row: EvidenceStorageConfig,
    spec: ConfigSpec,
    actor: Optional[Actor] = None,
) -> EvidenceStorageConfig:
    """Edit a **draft** configuration in place.

    Refuses anything that is not a draft. An ``active`` row is where an
    organisation's evidence is being written right now, and a ``retired`` one
    may still be the only place some of it can be read from, so an in-place
    edit of either would move bytes that already exist. The supported paths for
    those are :func:`rotate_secret` — which changes no address — and creating a
    new draft and activating it, which is what the copy job (Phase 6) is built
    around.

    An omitted secret leaves the stored one alone; supplying one replaces it
    **without** bumping ``key_version``, because a draft has no cached client
    anywhere to invalidate. Use :func:`rotate_secret` on a live row.
    """
    if row.status != storage_config.STATUS_DRAFT:
        raise StorageConfigImmutable(
            f"Evidence storage configuration {row.id} is {row.status} and cannot "
            "be edited. Rotate its credential, or create a new configuration "
            "and activate it."
        )

    resolved = resolved_from_spec(
        spec,
        config_id=str(row.id),
        organization_id=str(row.organization_id) if row.organization_id else None,
        credential_version=str(row.key_version),
    )
    storage_config.validate_config_for_save(resolved)

    if (spec.secret_access_key or "").strip():
        row.secret_ciphertext = crypto.encrypt(spec.secret_access_key)

    row.provider = resolved.provider
    row.bucket = resolved.bucket
    row.region = resolved.region
    row.endpoint_url = resolved.endpoint_url
    row.public_endpoint = resolved.public_endpoint
    row.path_style = resolved.path_style
    row.sse_mode = resolved.sse_mode
    row.access_key_id = resolved.access_key_id
    _stamp(row, actor)

    await _audit(session, row.id, ACTION_UPDATED, actor)
    await session.commit()
    await session.refresh(row)
    return row


async def rotate_secret(
    session: AsyncSession,
    row: EvidenceStorageConfig,
    secret_access_key: str,
    access_key_id: Optional[str] = None,
    actor: Optional[Actor] = None,
) -> EvidenceStorageConfig:
    """Replace the stored credential and make every process notice.

    The sequence, and why it is this order:

    1. Build the configuration the row **would** have after the rotation, with
       the new credential in it. Nothing is written yet.
    2. Re-validate the address. Cheap, and it keeps the save-time check on
       every write path rather than on most of them.
    3. **If the row is active, probe it with the new credential before
       committing anything.** An active row is the store an organisation's
       evidence is being written to right now; committing a credential that
       does not work would take that store offline, and the administrator would
       find out from a failed upload rather than from the button they pressed.
       A draft is not probed because activation will probe it, and a retired row
       is not probed because nothing resolves it and its store may legitimately
       be unreachable — while its credential may still need rotating to keep
       old evidence readable.
    4. Write the ciphertext and bump ``key_version``.
    5. Commit, and only then announce. A version bump that raced a rollback
       would tell every worker to re-read a row that was never written.

    ``key_version`` is in the boto3 client cache key, and so is a fingerprint of
    the credential itself, so a rotated secret produces a different key and
    therefore a **new client for every operation** — not only for the offline
    signing paths. That is the difference between this and the cached-connection
    bug the Azure path still has.
    """
    secret = (secret_access_key or "").strip()
    if not secret:
        raise StorageConfigSpecError(
            "A new secret access key is required. Rotation never clears a "
            "credential; delete the configuration to do that."
        )

    new_key_id = (access_key_id or "").strip() or (row.access_key_id or "")
    next_version = int(row.key_version or 1) + 1

    stored = storage_config.stored_row_from_orm(row)
    current = storage_config.config_from_row(stored)
    candidate = dataclasses.replace(
        current,
        access_key_id=new_key_id,
        secret_access_key=secret,
        credential_version=str(next_version),
    )

    storage_config.validate_config_for_save(candidate)

    if row.status == storage_config.STATUS_ACTIVE:
        probe_scope = (
            str(row.organization_id) if row.organization_id else "platform"
        )
        report = await run_connection_probe(candidate, probe_scope)
        if not report.get("success"):
            failed = next(
                (
                    step["name"]
                    for step in report.get("steps", [])
                    if not step.get("ok")
                ),
                "unknown",
            )
            raise StorageActivationError(
                "The new credential was not accepted by the store: the "
                f"connection test failed at the {failed} step. The previous "
                "credential is unchanged.",
                report,
            )

    row.access_key_id = new_key_id
    row.secret_ciphertext = crypto.encrypt(secret)
    row.key_version = next_version
    _stamp(row, actor)

    await _audit(session, row.id, ACTION_ROTATED, actor)
    await session.commit()
    await session.refresh(row)

    storage_config.bump_version()
    storage_service.invalidate()

    # False positive: the word "credential" is in the message text only; the
    # arguments are the row id, a version number and the actor label.
    # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
    logger.info(
        "Evidence storage credential rotated for configuration %s (version %s) by %s",
        row.id,
        next_version,
        actor.label if actor else "unknown",
    )
    return row


async def retire_config(
    session: AsyncSession,
    row: EvidenceStorageConfig,
    actor: Optional[Actor] = None,
) -> EvidenceStorageConfig:
    """Take a configuration out of service without deleting it.

    The row stays, so evidence written under it keeps a resolvable
    configuration and the copy job (Phase 6) keeps a source to read from. What
    changes is that the resolver stops loading it: only ``active`` rows are
    loaded, so the organisation falls back to the platform row and then to the
    environment.
    """
    if row.status == storage_config.STATUS_RETIRED:
        return row

    row.status = storage_config.STATUS_RETIRED
    _stamp(row, actor)

    await _audit(session, row.id, ACTION_RETIRED, actor)
    await session.commit()
    await session.refresh(row)

    storage_config.bump_version()
    storage_service.invalidate()
    return row


async def files_referencing(session: AsyncSession, config_id: UUID) -> int:
    """How many evidence files name this configuration as where their bytes are."""
    result = await session.execute(
        select(func.count())
        .select_from(EvidenceFile)
        .where(EvidenceFile.storage_config_id == config_id)
    )
    return int(result.scalar() or 0)


async def delete_config(
    session: AsyncSession,
    row: EvidenceStorageConfig,
    actor: Optional[Actor] = None,
) -> None:
    """Delete a ``draft`` or ``retired`` configuration.

    An ``active`` row is refused: deleting the store an organisation is
    currently writing to is never what someone meant to do, and retiring it
    first is one extra call that makes the intent explicit.

    A row any evidence file still references is refused with a count. The
    foreign key is ``ON DELETE RESTRICT`` so the database would refuse it too,
    but as an ``IntegrityError`` that surfaces as a 500 and tells the
    administrator nothing about what to do next.
    """
    if row.status == storage_config.STATUS_ACTIVE:
        raise StorageConfigImmutable(
            f"Evidence storage configuration {row.id} is active. Retire it "
            "first, or activate a different configuration."
        )

    in_use = await files_referencing(session, row.id)
    if in_use:
        raise StorageConfigInUse(
            f"{in_use} evidence file(s) still hold their bytes under this "
            "configuration, so it cannot be deleted. Copy them to another "
            "store first.",
            in_use,
        )

    config_id = row.id
    await session.delete(row)
    await _audit(session, config_id, ACTION_DELETED, actor)
    await session.commit()

    storage_config.bump_version()
    storage_service.invalidate()

    logger.info(
        "Evidence storage configuration %s deleted by %s",
        config_id,
        actor.label if actor else "unknown",
    )


async def run_connection_probe(
    config: storage_config.ResolvedStorageConfig,
    org_id: str,
) -> dict:
    """Run the round-trip probe without blocking the event loop.

    boto3 is synchronous, and the probe dials an address a tenant supplied. The
    probe client's own connect and read timeouts bound how long this can take;
    ``asyncio.to_thread`` keeps that time off the event loop, which is the same
    treatment ``services/reconciliation_service.py`` gives its blocking storage
    reads.
    """
    return await asyncio.to_thread(storage_service.probe_round_trip, config, org_id)


def _as_actor(
    actor: Optional[object], actor_user_id: Optional[UUID] = None
) -> Optional[Actor]:
    """Accept either an :class:`Actor` or a bare label.

    :func:`activate_config` predates :class:`Actor` and its existing callers
    pass a string. Rather than break them — or grow a second actor parameter
    that means the same thing — both forms are accepted and normalised here.
    """
    if actor is None and actor_user_id is None:
        return None
    if isinstance(actor, Actor):
        return actor
    return Actor(label=str(actor) if actor else "unknown", user_id=actor_user_id)


def _row_id_or_none(config_id: Optional[str]) -> Optional[UUID]:
    """A resolved configuration's id as a UUID, or ``None`` when it has no row.

    The environment-synthesised configuration carries the literal
    ``legacy-env`` id and no row, so there is nothing for a file to point at
    and NULL keeps meaning what it already meant. A malformed id is treated the
    same way rather than raising: a stamp is a best-effort improvement on NULL,
    never a reason to refuse an activation.
    """
    if not config_id or config_id == storage_config.LEGACY_ENV_CONFIG_ID:
        return None
    try:
        return UUID(str(config_id))
    except (ValueError, AttributeError, TypeError):
        return None


async def activate_config(
    session: AsyncSession,
    config_id: UUID,
    actor: Optional[object] = None,
    actor_user_id: Optional[UUID] = None,
) -> EvidenceStorageConfig:
    """Promote one configuration to ``active``, if its connection test passes.

    The sequence, in order, and all of the database work in one transaction:

    1. Load the row. A row that does not exist is an error, not a refusal.
    2. Validate its endpoint address at save time, as well as at connect time.
    3. Run the round-trip probe against it. **A failing probe refuses the
       activation** — this is the gate, and it is why there is no way to make a
       configuration live without having proved that the platform can write to
       it, read it back and delete it.
    4. Retire whichever row is active for the same scope, then activate this
       one. Both statements, one transaction.
    5. Announce the change so other processes — the Celery workers above all —
       stop using the configuration that was just retired.

    Raises:
        StorageActivationError: if the row is missing, its address is refused,
            or the probe fails. The exception carries the probe report.
    """
    result = await session.execute(
        select(EvidenceStorageConfig).where(EvidenceStorageConfig.id == config_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise StorageActivationError(f"Evidence storage configuration {config_id} not found")

    stored = storage_config.stored_row_from_orm(row)
    resolved = storage_config.config_from_row(stored)

    # Save-time address validation. The same policy the driver applies at
    # connect time, run here so the refusal arrives when the administrator acts
    # rather than when the first upload fails.
    storage_config.validate_config_for_save(resolved)

    if not resolved.bucket:
        raise StorageActivationError(
            "Evidence storage configuration names no bucket and cannot be activated"
        )

    scope_org_id = row.organization_id
    probe_scope = str(scope_org_id) if scope_org_id else "platform"
    report = await run_connection_probe(resolved, probe_scope)

    if not report.get("success"):
        failed = next(
            (step["name"] for step in report.get("steps", []) if not step.get("ok")),
            "unknown",
        )
        raise StorageActivationError(
            "Evidence storage configuration cannot be activated: its connection "
            f"test failed at the {failed} step.",
            report,
        )

    # -- pin the outgoing store onto the files that are in it -------------
    #
    # Every evidence file written before this feature carries a NULL
    # `storage_config_id`, which means "resolve me by organisation". That is
    # correct right up until the organisation's resolution changes, which is
    # exactly what the next few statements do: the instant this row goes
    # active, every one of those files would start resolving to a store its
    # bytes are not in.
    #
    # So before switching, record where they actually are. This is the last
    # moment the answer is knowable.
    #
    # One indexed UPDATE, inside the activation transaction, so a rollback
    # leaves the stamps and the switch equally undone. It is a no-op on the
    # second and every later activation, because those files are stamped by
    # then and new uploads are stamped as they arrive.
    if scope_org_id is not None:
        outgoing = storage_config.resolve(str(scope_org_id))
        outgoing_config_id = _row_id_or_none(outgoing.config_id)

        if outgoing_config_id is None:
            # The outgoing configuration is the environment-synthesised one.
            # It has no row, so there is nothing for a file to point at, and
            # every NULL row would silently start resolving to the store this
            # activation is about to make current — which does not hold their
            # bytes.
            #
            # There is no honest stamp available here, so this refuses rather
            # than proceeding. The operator's step is to seed the platform row
            # (the installer does this; see the storage bootstrap), which gives
            # those files a row to be stamped with, and then to retry.
            unstamped = (
                await session.execute(
                    select(func.count())
                    .select_from(EvidenceFile)
                    .where(
                        EvidenceFile.organization_id == scope_org_id,
                        EvidenceFile.storage_config_id.is_(None),
                    )
                )
            ).scalar_one()
            if unstamped:
                raise StorageActivationError(
                    f"{unstamped} evidence file(s) of this organisation are "
                    "not yet associated with a storage configuration, and the "
                    "store in force is the one named in the installation's "
                    "environment, which has no configuration row to associate "
                    "them with. Activating now would leave those files "
                    "resolving to this new store, which does not hold them. "
                    "Seed the platform storage configuration first (the "
                    "installer does this), then activate.",
                    details={"unstamped_files": int(unstamped)},
                )

        if outgoing_config_id is not None and outgoing_config_id != config_id:
            stamped = await session.execute(
                update(EvidenceFile)
                .where(
                    EvidenceFile.organization_id == scope_org_id,
                    EvidenceFile.storage_config_id.is_(None),
                )
                .values(storage_config_id=outgoing_config_id)
            )
            if stamped.rowcount:
                logger.info(
                    "Pinned %s evidence file(s) for organisation %s to storage "
                    "configuration %s before activating %s",
                    stamped.rowcount,
                    scope_org_id,
                    outgoing_config_id,
                    config_id,
                )

    # -- the transaction -------------------------------------------------
    # Retire first. The partial unique indexes permit one active row per scope,
    # so activating before retiring would trip the index rather than replace
    # the row.
    scope_predicate = (
        EvidenceStorageConfig.organization_id.is_(None)
        if scope_org_id is None
        else EvidenceStorageConfig.organization_id == scope_org_id
    )
    await session.execute(
        update(EvidenceStorageConfig)
        .where(
            scope_predicate,
            EvidenceStorageConfig.status == storage_config.STATUS_ACTIVE,
            EvidenceStorageConfig.id != config_id,
        )
        .values(status=storage_config.STATUS_RETIRED)
    )

    row.status = storage_config.STATUS_ACTIVE
    resolved_actor = _as_actor(actor, actor_user_id)
    _stamp(row, resolved_actor)

    await _audit(session, row.id, ACTION_ACTIVATED, resolved_actor)
    await session.commit()
    await session.refresh(row)

    # Only after the commit: a version bump that raced a rollback would tell
    # every worker to reload rows that were never written.
    storage_config.bump_version()
    storage_service.invalidate()

    logger.info(
        "Evidence storage configuration %s activated for %s scope by %s",
        config_id,
        probe_scope,
        (resolved_actor.label if resolved_actor else "unknown"),
    )
    return row


# ---------------------------------------------------------------------------
# The bundled platform row — the installer's one write
# ---------------------------------------------------------------------------

#: The `.env` key the installer writes to say which object store it provisioned.
#: ``COMPOSE_PROFILES`` cannot serve here: it is read by the docker CLI on the
#: host and is never forwarded into a container, so the backend has no way to
#: see whether the bundled MinIO is part of its own stack.
BOOTSTRAP_ENV = "EVIDENCE_STORAGE_BOOTSTRAP"
BOOTSTRAP_BUNDLED_MINIO = "bundled_minio"

ACTION_SEEDED = "evidence_storage.config.seeded"


async def seed_bundled_platform_config(
    session: AsyncSession,
) -> Optional[EvidenceStorageConfig]:
    """Write the single platform-scope row describing the bundled MinIO.

    **This is the only writer of ``is_bundled=True`` in the codebase**, and a
    test walks the source tree asserting exactly that. ``is_bundled`` is what
    exempts a row from the tenant half of the address policy — the loopback,
    RFC1918, CGNAT and ``.local`` refusals and the ``http`` scheme ban — so a
    second writer reachable from a request would be a request that can point the
    backend at the operator's own network. It is not reachable from any HTTP
    route: the only caller is the application lifespan.

    Idempotent, because it runs on every boot and installs run more than one
    process:

    * it does nothing unless ``EVIDENCE_STORAGE_BOOTSTRAP`` is
      ``bundled_minio``. The variable defaults to empty in ``docker-compose.yml``
      rather than to the bundled value, so an existing install that has never
      run the installer never starts seeding a row by surprise;
    * it does nothing when **any** platform-scope row already exists, whatever
      its status. A retired or draft platform row is an operator's decision, and
      re-seeding over it would silently redirect evidence back to the bundled
      store;
    * a concurrent seed from a second replica trips the partial unique index and
      is caught as "already seeded" rather than failing the boot.

    The credential is the **scoped** MinIO account, read file-aware through
    :mod:`services.secrets` so that the secrets overlay's
    ``AWS_ACCESS_KEY_ID_FILE`` works exactly as it does everywhere else. Before
    this phase that pair was the MinIO root account; an install provisioned then
    still has the old shape, and seeding its row is still correct — the row
    records what the credential *is*, and migrating it to a scoped account is a
    separate, documented operator step.

    Returns the row it wrote, or ``None`` when it did nothing.
    """
    import os

    from sqlalchemy.exc import IntegrityError

    from services import secrets as secrets_module

    bootstrap = (os.environ.get(BOOTSTRAP_ENV) or "").strip()
    if bootstrap != BOOTSTRAP_BUNDLED_MINIO:
        logger.debug(
            "Bundled evidence storage not seeded: %s is %r, not %r",
            BOOTSTRAP_ENV,
            bootstrap,
            BOOTSTRAP_BUNDLED_MINIO,
        )
        return None

    existing = (
        await session.execute(
            select(EvidenceStorageConfig.id)
            .where(EvidenceStorageConfig.organization_id.is_(None))
            .limit(1)
        )
    ).first()
    if existing is not None:
        logger.debug("Bundled evidence storage already has a platform row; leaving it alone")
        return None

    access_key_id = (secrets_module.get_secret("AWS_ACCESS_KEY_ID") or "").strip()
    secret_access_key = (secrets_module.get_secret("AWS_SECRET_ACCESS_KEY") or "").strip()
    bucket = (os.environ.get("EVIDENCE_BUCKET") or "evidence").strip()
    public_endpoint = (os.environ.get("EVIDENCE_PUBLIC_ENDPOINT") or "").strip()

    if not access_key_id or not secret_access_key:
        # The stack says it bundles MinIO but the credential never arrived. A
        # row with no credential resolves to a store nothing can write to, which
        # is worse than no row at all: the legacy-environment fallback at least
        # reports itself honestly as unconfigured.
        logger.warning(
            "%s is %s but no application credential is readable; "
            "the bundled evidence storage row was NOT seeded.",
            BOOTSTRAP_ENV,
            BOOTSTRAP_BUNDLED_MINIO,
        )
        return None

    # Through the preset, so the bundled row carries the same endpoint,
    # addressing, signature and encryption settings as any other minio row
    # rather than a second hand-written copy of them.
    resolved = storage_config.config_from_preset(
        storage_config.PROVIDER_MINIO,
        config_id="(unsaved)",
        bucket=bucket,
        public_endpoint=public_endpoint,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        # The installer provisioned this address, not a tenant administrator.
        # Without this the row's own http://minio:9000 endpoint is refused.
        endpoint_is_operator_supplied=True,
    )
    storage_config.validate_config_for_save(resolved)

    row = EvidenceStorageConfig(
        organization_id=None,
        provider=resolved.provider,
        bucket=resolved.bucket,
        region=resolved.region,
        endpoint_url=resolved.endpoint_url,
        public_endpoint=resolved.public_endpoint,
        path_style=resolved.path_style,
        sse_mode=resolved.sse_mode,
        access_key_id=resolved.access_key_id,
        secret_ciphertext=crypto.encrypt(secret_access_key),
        key_version=1,
        status=storage_config.STATUS_ACTIVE,
        is_bundled=True,
    )
    session.add(row)
    try:
        await session.flush()
        await _audit(session, row.id, ACTION_SEEDED, None)
        await session.commit()
    except IntegrityError:
        # Another replica won the race. The partial unique index on one active
        # platform row is what makes that safe to ignore.
        await session.rollback()
        logger.info("Bundled evidence storage row was seeded concurrently; nothing to do")
        return None

    await session.refresh(row)

    # Every writer of a configuration row announces itself, or a Celery worker
    # that resolved before this boot keeps its cached snapshot for a minute.
    storage_config.bump_version()

    logger.info(
        "Seeded the bundled evidence storage configuration %s "
        "(provider=%s bucket=%s endpoint=%s)",
        row.id,
        row.provider,
        row.bucket,
        row.endpoint_url,
    )
    return row
