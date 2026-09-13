"""Tier-3 credential store: the five values an operator may set in the app.

Contract §3d. Precedence is fixed in `services.secrets.get_secret`: a value in
a mounted file or the environment always wins over a value in this table. That
is deliberate — an operator who manages a credential outside the application
must not have it silently overridden from a web form, so those rows are
reported as `managed_by_operator` and writes to them are refused.

Nothing in this module ever returns a stored value to a caller.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from models import IntegrationSecret
from services import crypto
from services import secrets as _secrets
from services.platform_audit import record_platform_event

logger = logging.getLogger(__name__)

#: name -> (human label, what the credential unlocks)
LABELS: Dict[str, tuple[str, str]] = {
    "OIDC_CLIENT_SECRET": (
        "OIDC client secret",
        "Single sign-on with an external identity provider",
    ),
    "RESEND_API_KEY": (
        "Resend API key",
        "Transactional email: invitations, notifications",
    ),
    "ANTHROPIC_API_KEY": (
        "Anthropic API key",
        "AI document generation and evidence assessment",
    ),
    # `AZURE_STORAGE_ACCOUNT_KEY` was removed here and from
    # `services.secrets.TIER3_NAMES` together. Evidence object storage is
    # configured per organisation in `evidence_storage_configs`, which this
    # name-keyed global table cannot express, and the Azure field was inert on
    # its own besides. See the comment above `TIER3_NAMES`. This dict is what
    # the Integrations screen renders, so removing the entry is also what takes
    # the misleading field off that screen.
    "HIBP_API_KEY": (
        "Have I Been Pwned API key",
        "Vendor breach research",
    ),
    "NVD_API_KEY": (
        "NVD API key",
        "Higher NVD rate limit for vendor CVE research",
    ),
}

ENTITY_TYPE = "integration_secret"


class UnknownIntegration(Exception):
    """The name is not one of the five settable credentials."""


class OperatorManaged(Exception):
    """A file or environment value is present; the app must not shadow it."""


class EmptyValue(Exception):
    """A write was attempted with an empty value."""


@dataclass
class Actor:
    """Who made the change, for the platform audit row."""

    label: str
    user_id: Optional[UUID] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    action_source: Optional[str] = None
    request_id: Optional[UUID] = None


# ---------------------------------------------------------------------------
# Provider registration (sync path, used by services.secrets)
# ---------------------------------------------------------------------------

def register() -> None:
    """Register this module as the database tier for `services.secrets`."""
    _secrets.register_db_provider(_load_all)


def _load_all() -> Dict[str, str]:
    """Every decryptable configured row, as {name: plaintext}.

    Runs on a short-lived synchronous connection because `get_secret` is called
    from synchronous code (Celery tasks, module import paths) as well as from
    the async API. Returns {} rather than raising on any failure — a missing
    key, a missing table on a pre-migration database, or an unreachable
    database must degrade to the file and environment tiers, never break a
    request.
    """
    try:
        if crypto.get_fernet() is None:
            return {}
    except crypto.SecretKeyInvalid:
        logger.warning("SCF_SECRET_KEY is not a valid Fernet key — database credential tier disabled")
        return {}

    try:
        from sqlalchemy import create_engine

        import db_url

        engine = create_engine(
            db_url.get_sync_database_url(),
            pool_pre_ping=True,
            pool_size=1,
            max_overflow=0,
        )
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    text("SELECT name, value_ciphertext FROM integration_secrets")
                ).fetchall()
        finally:
            engine.dispose()
    except Exception as exc:  # noqa: BLE001 — degrade to file/env, never raise
        # Logs the exception TYPE only; no value or DSN ever reaches the message.
        # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
        logger.debug("integration_secrets tier unavailable: %s", type(exc).__name__)
        return {}

    out: Dict[str, str] = {}
    for name, ciphertext in rows:
        if name not in LABELS:
            continue
        try:
            value = crypto.decrypt(ciphertext)
        except (crypto.DecryptError, crypto.SecretKeyMissing):
            logger.warning("Stored value for %s could not be decrypted — skipping", name)
            continue
        if value:
            out[name] = value
    return out


# ---------------------------------------------------------------------------
# Async API surface
# ---------------------------------------------------------------------------

async def _rows_by_name(db: AsyncSession) -> Dict[str, IntegrationSecret]:
    result = await db.execute(select(IntegrationSecret))
    return {row.name: row for row in result.scalars().all()}


def _operator_source(name: str) -> Optional[str]:
    """"file" or "env" when the operator manages this name outside the app."""
    import os

    path = (os.getenv(f"{name}_FILE") or "").strip()
    if path:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                if fh.read().strip():
                    return "file"
        except OSError:
            pass
    env = os.getenv(name)
    if env is not None and env.strip():
        return "env"
    return None


def _item(name: str, row: Optional[IntegrationSecret]) -> Dict[str, Any]:
    label, feature = LABELS[name]
    operator_source = _operator_source(name)
    source = operator_source or ("db" if row is not None else None)
    return {
        "name": name,
        "label": label,
        "feature": feature,
        "configured": source is not None,
        "source": source,
        "managed_by_operator": operator_source is not None,
        "updated_at": row.updated_at.isoformat() if row is not None and row.updated_at else None,
        "updated_by": (row.updated_by_label if row is not None else None),
    }


async def list_items(db: AsyncSession) -> List[Dict[str, Any]]:
    rows = await _rows_by_name(db)
    return [_item(name, rows.get(name)) for name in LABELS]


async def legacy_plaintext_rows(db: AsyncSession) -> int:
    """Rows still holding unencrypted values across every encrypted column.

    This is the number `backfill-encrypt` would act on. It is a count, not a
    sample: no value is ever read out of the database to produce it.
    """
    total = 0
    prefix = crypto.PREFIX
    statements = (
        "SELECT count(*) FROM integration_secrets WHERE value_ciphertext NOT LIKE :p",
        "SELECT count(*) FROM webhook_endpoints WHERE secret NOT LIKE :p",
        "SELECT count(*) FROM organization_invites WHERE invite_token NOT LIKE :p",
        "SELECT count(*) FROM consultant_invites WHERE invite_token NOT LIKE :p",
    )
    for stmt in statements:
        try:
            result = await db.execute(text(stmt), {"p": prefix + "%"})
            total += int(result.scalar() or 0)
        except Exception:  # noqa: BLE001 — a table may not exist yet
            await db.rollback()
    return total


async def set_value(
    db: AsyncSession, name: str, value: str, actor: Actor
) -> Dict[str, Any]:
    if name not in LABELS:
        raise UnknownIntegration(name)
    if value is None or not value.strip():
        raise EmptyValue(name)
    operator_source = _operator_source(name)
    if operator_source is not None:
        raise OperatorManaged(operator_source)

    # Raises SecretKeyMissing / SecretKeyInvalid — the router maps both to 409.
    ciphertext = crypto.encrypt(value.strip())

    rows = await _rows_by_name(db)
    existing = rows.get(name)
    action = "integration.secret.replaced" if existing is not None else "integration.secret.set"

    if existing is not None:
        existing.value_ciphertext = ciphertext
        existing.key_version = 1
        existing.updated_by_user_id = actor.user_id
        existing.updated_by_label = actor.label
        row = existing
    else:
        row = IntegrationSecret(
            name=name,
            value_ciphertext=ciphertext,
            key_version=1,
            updated_by_user_id=actor.user_id,
            updated_by_label=actor.label,
        )
        db.add(row)

    await _audit(db, name, action, actor)
    await db.commit()
    await db.refresh(row)

    _secrets.bump_version()
    return _item(name, row)


async def clear_value(db: AsyncSession, name: str, actor: Actor) -> Dict[str, Any]:
    if name not in LABELS:
        raise UnknownIntegration(name)
    rows = await _rows_by_name(db)
    existing = rows.get(name)
    if existing is None:
        raise UnknownIntegration(name)

    await db.delete(existing)
    await _audit(db, name, "integration.secret.cleared", actor)
    await db.commit()

    _secrets.bump_version()
    return _item(name, None)


async def _audit(db: AsyncSession, name: str, action: str, actor: Actor) -> None:
    await record_platform_event(
        db=db,
        entity_type=ENTITY_TYPE,
        entity_id=name,
        action=action,
        actor=actor.label,
        actor_user_id=actor.user_id,
        ip_address=actor.ip_address,
        user_agent=actor.user_agent,
        action_source=actor.action_source,
        request_id=actor.request_id,
    )


def encryption_key_configured() -> bool:
    try:
        return crypto.get_fernet() is not None
    except crypto.SecretKeyInvalid:
        return False


async def health(db: AsyncSession) -> Dict[str, Any]:
    items = await list_items(db)
    sources = {item["source"] for item in items if item["source"]}
    operator_sources = {
        item["source"] for item in items if item["managed_by_operator"]
    }
    if not operator_sources:
        secrets_dir_mode = "env"
    elif operator_sources == {"file"}:
        secrets_dir_mode = "file"
    elif operator_sources == {"env"}:
        secrets_dir_mode = "env"
    else:
        secrets_dir_mode = "mixed"

    return {
        "encryption_key_configured": encryption_key_configured(),
        "configured": [i["name"] for i in items if i["configured"]],
        "unconfigured": [i["name"] for i in items if not i["configured"]],
        "items": items,
        "secrets_dir_mode": secrets_dir_mode,
    }
