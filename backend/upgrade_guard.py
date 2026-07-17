"""Backend-side migration guard (upgrade design Part E, §2.5).

The canonical operator workflow is ``git pull && docker compose up --build``,
which lets the FastAPI lifespan auto-migrate the database to Alembic head with
no backup and no version-jump check. This module runs INSIDE that lifespan
path (called from ``database.run_alembic_migrations`` before ``command.upgrade``)
so the safety guarantees hold regardless of how the stack is started — a bare
``compose up`` fails CLOSED rather than silently migrating.

It deliberately does NOT hook Alembic's ``env.py``, so a direct
``alembic upgrade head`` on the CLI (the ``scripts/upgrade.sh`` one-shot path,
which backs up first) bypasses the guard by design.

Two enforcement layers:
  1. Version floor (unconditional): the image bakes ``min_upgradable_version``;
     if the last applied platform version is below it, refuse and name the
     required intermediate stop. Fail closed.
  2. Backup-ack sentinel: when pending migrations exist on a non-empty database
     outside development, require ``SCF_MIGRATE_ACK`` to match the image target
     version (or "any"). ``scripts/upgrade.sh`` sets this after its backup.

Both inputs (``version`` and ``min_upgradable_version``) come from the
image-baked ``/app/build_info.json`` so a bind-mounted host file cannot spoof
them (design C3). A missing/unreadable build_info => permissive dev floor of
"0.0.0" with a warning.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# Image-baked build metadata (Dockerfile.backend writes this at build time).
# Canonical path is the container root (/build_info.json), deliberately OUTSIDE
# /app so the ./backend:/app source bind mount cannot shadow it. /app/build_info.json
# is a silent fallback for any image-only deployment that still bakes it there.
# BUILD_INFO_PATH is overridable for tests.
BUILD_INFO_PATH = os.getenv("SCF_BUILD_INFO_PATH", "/build_info.json")
BUILD_INFO_FALLBACK_PATH = "/app/build_info.json"

# Permissive floor used when no build metadata is available (dev images).
DEFAULT_FLOOR = "0.0.0"


# ---------------------------------------------------------------------------
# Pure semver helpers (no third-party dependency, per the forward-only policy).
# ---------------------------------------------------------------------------
def parse_version(version: str) -> Tuple[int, int, int]:
    """Parse ``"vX.Y.Z"`` / ``"X.Y.Z"`` into a comparable ``(major, minor, patch)``.

    Leading ``v`` is stripped. Missing components default to 0. Any non-numeric
    component (or pre-release suffix such as ``"1-rc1"``) contributes only its
    leading integer, or 0 if there is none — enough for the coarse floor/latest
    comparisons this guard needs, without pulling in a semver library.
    """
    if not version:
        return (0, 0, 0)
    cleaned = version.strip()
    if cleaned[:1] in ("v", "V"):
        cleaned = cleaned[1:]
    parts: List[int] = []
    for raw in cleaned.split(".")[:3]:
        num = ""
        for ch in raw.strip():
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num) if num else 0)
    while len(parts) < 3:
        parts.append(0)
    return (parts[0], parts[1], parts[2])


def compare_versions(a: str, b: str) -> int:
    """Return -1 if ``a < b``, 0 if equal, 1 if ``a > b`` (semver-ish)."""
    pa, pb = parse_version(a), parse_version(b)
    if pa < pb:
        return -1
    if pa > pb:
        return 1
    return 0


# ---------------------------------------------------------------------------
# Build metadata
# ---------------------------------------------------------------------------
def read_build_info(path: Optional[str] = None) -> Optional[dict]:
    """Read the image-baked build_info.json, or ``None`` if absent/unreadable.

    With no explicit ``path``, tries the canonical container-root path first, then
    the ``/app`` fallback, returning the first valid JSON object found.
    """
    candidates = [path] if path else [BUILD_INFO_PATH, BUILD_INFO_FALLBACK_PATH]
    for target in candidates:
        try:
            with open(target) as fh:
                data = json.load(fh)
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as exc:
            logger.warning("Failed to read build_info at %s: %s", target, exc)
            continue
        if not isinstance(data, dict):
            logger.warning("build_info at %s is not a JSON object; ignoring", target)
            continue
        return data
    return None


# ---------------------------------------------------------------------------
# Pure decision logic (unit-testable without a database or an image).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class GuardDecision:
    """Outcome of the guard evaluation.

    ``permit`` True means migration may proceed. When False, ``message`` is the
    operator-facing refusal reason and ``code`` classifies it
    (``floor`` | ``legacy_floor`` | ``ack`` | ``ack_mismatch``).
    """
    permit: bool
    code: str
    message: str


def evaluate_guard(
    *,
    floor: str,
    target: Optional[str],
    last_applied: Optional[str],
    alembic_version_present: bool,
    upgrade_state_present: bool,
    pending: bool,
    environment: str,
    migrate_ack: Optional[str],
) -> GuardDecision:
    """Decide whether an auto-migration may proceed. Pure — no I/O.

    See module docstring for the two enforcement layers. Order matters: the
    fresh-install short-circuit first, then the unconditional floor check, then
    the ack sentinel.
    """
    target_tag = f"v{target}" if target else "vX.Y.Z"

    # Fresh / empty database => initial install. Permit with no ack, no floor.
    db_fresh = (not alembic_version_present) and (not upgrade_state_present)
    if db_fresh:
        return GuardDecision(True, "initial_install", "Fresh database — initial install permitted.")

    # Already at Alembic head => no migration will run, so there is nothing this
    # guard can protect against. Permit unconditionally. This matters after the
    # scripts/upgrade.sh one-shot (`alembic upgrade head` bypasses the guard and
    # records no version): without it, the very next `compose up` would hit the
    # floor/legacy refusal and strand a fully successful upgrade in a crash
    # loop. The lifespan records the running version right after this, repairing
    # legacy (pre-guard) installs on their first post-upgrade boot.
    if not pending:
        return GuardDecision(True, "at_head", "Database already at Alembic head — nothing to migrate.")

    # ---- Floor check (unconditional, fail closed) ----
    if last_applied is not None:
        if compare_versions(last_applied, floor) < 0:
            return GuardDecision(
                False,
                "floor",
                (
                    f"Refusing to migrate: this build requires upgrading from >= {floor}; "
                    f"you are on {last_applied} — upgrade to {floor} first "
                    f"(see scripts/upgrade.sh)."
                ),
            )
    elif alembic_version_present:
        # Non-empty DB with migration history but no recorded platform version:
        # a pre-guard (legacy) install upgrading in. Permit the floor check only
        # when the image imposes no real floor; otherwise refuse and name it.
        if compare_versions(floor, DEFAULT_FLOOR) > 0:
            return GuardDecision(
                False,
                "legacy_floor",
                (
                    f"Refusing to migrate: this build requires the previous install to be "
                    f">= {floor}, but no recorded platform version was found (pre-upgrade-guard "
                    f"install). Upgrade to {floor} first via scripts/upgrade.sh."
                ),
            )

    # ---- Backup-ack sentinel ----
    # Only bites when there is actually work to do (pending migrations) on a
    # non-empty DB outside development. Development is always permissive.
    if pending and environment != "development":
        if not migrate_ack:
            return GuardDecision(
                False,
                "ack",
                (
                    "Refusing to auto-migrate an existing database without a backup "
                    f"acknowledgement. Run ./scripts/upgrade.sh {target_tag} (it backs up "
                    "first), or set SCF_MIGRATE_ACK="
                    f"{target or '<version>'} if you have your own backup."
                ),
            )
        if migrate_ack != "any" and migrate_ack != (target or ""):
            return GuardDecision(
                False,
                "ack_mismatch",
                (
                    f"Refusing to auto-migrate: SCF_MIGRATE_ACK={migrate_ack!r} does not match "
                    f"this build's target version {target or '<unknown>'!r}. Run "
                    f"./scripts/upgrade.sh {target_tag} (it backs up first), or set "
                    f"SCF_MIGRATE_ACK={target or '<version>'} (or 'any') if you have your own backup."
                ),
            )

    return GuardDecision(True, "ok", "Migration permitted.")


def apply_guard_decision(decision: GuardDecision) -> None:
    """Log the decision; raise ``SystemExit(1)`` when it refuses (fail closed)."""
    if decision.permit:
        logger.info("Migration guard: %s (%s)", decision.message, decision.code)
        return
    logger.error("Migration guard REFUSED (%s): %s", decision.code, decision.message)
    raise SystemExit(1)


# ---------------------------------------------------------------------------
# Database-facing wrappers (run inside the FastAPI lifespan, sync engine).
# ---------------------------------------------------------------------------
def _sync_url(database_url: str) -> str:
    """Convert the async DATABASE_URL to a sync psycopg2 URL (Celery convention)."""
    return database_url.replace("+asyncpg", "+psycopg2").replace("?ssl=require", "?sslmode=require")


def _gather_state(alembic_cfg, database_url: str):
    """Read guard inputs from the live database. Returns the kwargs for evaluate_guard's
    DB-derived fields: (alembic_version_present, upgrade_state_present, last_applied, pending)."""
    from sqlalchemy import create_engine, inspect, text
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    engine = create_engine(_sync_url(database_url), pool_pre_ping=True)
    try:
        inspector = inspect(engine)
        alembic_version_present = inspector.has_table("alembic_version")
        upgrade_state_present = inspector.has_table("platform_upgrade_state")

        last_applied: Optional[str] = None
        if upgrade_state_present:
            with engine.connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT version FROM platform_upgrade_state "
                        "ORDER BY applied_at DESC, id DESC LIMIT 1"
                    )
                ).fetchone()
            if row is not None:
                last_applied = row[0]

        head = ScriptDirectory.from_config(alembic_cfg).get_current_head()
        with engine.connect() as conn:
            current = MigrationContext.configure(conn).get_current_revision()
        pending = current != head

        return alembic_version_present, upgrade_state_present, last_applied, pending
    finally:
        engine.dispose()


def run_migration_guard(alembic_cfg, database_url: str) -> None:
    """Enforce the migration guard before ``command.upgrade`` runs.

    Called from ``database.run_alembic_migrations`` (lifespan path only). Raises
    ``SystemExit(1)`` when the migration is refused.
    """
    build_info = read_build_info()
    if build_info is None:
        logger.warning(
            "No build_info at %s or %s — treating version floor as %s (permissive dev image).",
            BUILD_INFO_PATH,
            BUILD_INFO_FALLBACK_PATH,
            DEFAULT_FLOOR,
        )
        floor = DEFAULT_FLOOR
        target: Optional[str] = None
    else:
        floor = str(build_info.get("min_upgradable_version") or DEFAULT_FLOOR)
        target = build_info.get("version")
        target = str(target) if target else None

    alembic_version_present, upgrade_state_present, last_applied, pending = _gather_state(
        alembic_cfg, database_url
    )

    decision = evaluate_guard(
        floor=floor,
        target=target,
        last_applied=last_applied,
        alembic_version_present=alembic_version_present,
        upgrade_state_present=upgrade_state_present,
        pending=pending,
        environment=os.getenv("ENVIRONMENT", "production"),
        migrate_ack=os.getenv("SCF_MIGRATE_ACK"),
    )
    apply_guard_decision(decision)


def record_applied_version(database_url: str) -> None:
    """Record the image's platform version in ``platform_upgrade_state``.

    Called after a successful ``command.upgrade`` (or when already at head).
    Inserts a row only when the version differs from the last recorded one, so
    the table stays an append-only history of distinct applied versions. A no-op
    when build_info is absent (dev image) or the table does not yet exist.
    """
    build_info = read_build_info()
    version = build_info.get("version") if build_info else None
    if not version:
        logger.debug("No image version in build_info; skipping platform_upgrade_state record.")
        return
    version = str(version)

    from sqlalchemy import create_engine, inspect, text

    engine = create_engine(_sync_url(database_url), pool_pre_ping=True)
    try:
        if not inspect(engine).has_table("platform_upgrade_state"):
            logger.warning("platform_upgrade_state table absent after migration; cannot record version.")
            return
        with engine.begin() as conn:
            last = conn.execute(
                text(
                    "SELECT version FROM platform_upgrade_state "
                    "ORDER BY applied_at DESC, id DESC LIMIT 1"
                )
            ).fetchone()
            if last is not None and last[0] == version:
                return
            conn.execute(
                text("INSERT INTO platform_upgrade_state (version) VALUES (:v)"),
                {"v": version},
            )
        logger.info("Recorded applied platform version %s in platform_upgrade_state.", version)
    finally:
        engine.dispose()
