"""
Celery task for AI recipe generation (systems knowledge catalog).

Runs the in-process engine (`services.recipe_generation_engine`) for a custom
system, persists the output as an org-private SystemCatalogTemplate (slug
`org-{org_id}-{system-slug}`, organization_id set, hidden from the public
picker) with `source='ai_generated'` recipes, and links the system to it via
systems.catalog_template_id.

Progress is reported through a Redis status key polled by the API:
    scf:cache:v1:recipegen:{system_id} -> {"status": queued|running|completed|failed, ...}
"""
import json
import logging
import os
from datetime import datetime, timedelta

from celery import shared_task
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)

TASK_PREFIX = "tasks_recipe_generation"

RECIPEGEN_STATUS_PREFIX = "scf:cache:v1:recipegen"
RECIPEGEN_STATUS_TTL = int(timedelta(hours=1).total_seconds())

# ---------------------------------------------------------------------------
# Sync database session (Celery runs outside the async event loop)
# ---------------------------------------------------------------------------
_SYNC_DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+asyncpg://odin:changeme@localhost:5432/odin_scf"
).replace("+asyncpg", "+psycopg2").replace("?ssl=require", "?sslmode=require")

_sync_engine = None
SyncSession = None


def _get_sync_session():
    """Lazily create the sync engine and session factory."""
    global _sync_engine, SyncSession
    if SyncSession is None:
        _sync_engine = create_engine(_SYNC_DATABASE_URL, pool_pre_ping=True, pool_size=2, max_overflow=3)
        SyncSession = sessionmaker(bind=_sync_engine, expire_on_commit=False)
    return SyncSession()


_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


def _get_sync_redis():
    """Return a synchronous Redis client."""
    import redis as sync_redis
    return sync_redis.from_url(
        _REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
    )


def recipegen_status_key(system_id: str) -> str:
    return f"{RECIPEGEN_STATUS_PREFIX}:{system_id}"


def _set_status(system_id: str, status: str, **extra):
    """Write the generation status to Redis. Never raises."""
    try:
        r = _get_sync_redis()
        payload = {"status": status, "updated_at": datetime.utcnow().isoformat(), **extra}
        r.setex(recipegen_status_key(system_id), RECIPEGEN_STATUS_TTL, json.dumps(payload))
    except Exception as exc:
        logger.warning(f"Failed to write recipegen status for {system_id}: {exc}")


def _persist_generated(session, org_id: str, system_id: str, system_name: str,
                       system_vendor: str, system_type: str, recipes: dict) -> int:
    """
    Upsert the org-private template + recipes and link the system to it.
    Returns the template id.

    The slug is keyed on the system's id (stable across renames, no
    collisions between similarly named systems) — organization_id carries
    privacy; the slug is only an upsert identity.
    """
    slug = f"org-{org_id}-sys-{system_id}"[:100]

    row = session.execute(
        text("SELECT id FROM system_catalog_templates WHERE slug = :slug"),
        {"slug": slug},
    ).fetchone()

    if row:
        template_id = row[0]
        session.execute(
            text("""
                UPDATE system_catalog_templates
                SET name = :name, vendor = :vendor, system_type = :system_type,
                    description = :description, version = 'ai-1', updated_at = now()
                WHERE id = :id
            """),
            {"id": template_id, "name": system_name, "vendor": system_vendor or "Unknown",
             "system_type": system_type,
             "description": f"AI-generated collection guidance for {system_name}."},
        )
        session.execute(
            text("DELETE FROM system_catalog_recipes WHERE template_id = :id"),
            {"id": template_id},
        )
    else:
        template_id = session.execute(
            text("""
                INSERT INTO system_catalog_templates
                    (slug, name, vendor, system_type, description, aliases,
                     is_fallback, organization_id, version)
                VALUES
                    (:slug, :name, :vendor, :system_type, :description, :aliases,
                     false, :org_id, 'ai-1')
                RETURNING id
            """),
            {
                "slug": slug,
                "name": system_name,
                "vendor": system_vendor or "Unknown",
                "system_type": system_type,
                "description": f"AI-generated collection guidance for {system_name}.",
                "aliases": json.dumps([]),
                "org_id": org_id,
            },
        ).fetchone()[0]

    for level, recipe in recipes.items():
        session.execute(
            text("""
                INSERT INTO system_catalog_recipes
                    (template_id, maturity_level, title, estimated_time, frequency,
                     steps, source, version)
                VALUES
                    (:template_id, :level, :title, :estimated_time, :frequency,
                     :steps, 'ai_generated', 'ai-1')
            """),
            {
                "template_id": template_id,
                "level": level,
                "title": recipe["title"][:500],
                "estimated_time": (recipe.get("estimated_time") or "")[:100] or None,
                "frequency": (recipe.get("frequency") or "")[:100] or None,
                "steps": json.dumps(recipe.get("steps", [])),
            },
        )

    session.execute(
        text("UPDATE systems SET catalog_template_id = :tid WHERE id = :sid"),
        {"tid": template_id, "sid": system_id},
    )
    return template_id


@shared_task(bind=True, name=f"{TASK_PREFIX}.run_recipe_generation", time_limit=600, soft_time_limit=540)
def run_recipe_generation(self, org_id: str, system_id: str) -> dict:
    """Generate and persist AI collection recipes for a custom system."""
    task_id = self.request.id
    logger.info(f"run_recipe_generation[{task_id}] starting for system={system_id}")
    _set_status(system_id, "running")

    try:
        session = _get_sync_session()
        try:
            row = session.execute(
                text("""
                    SELECT name, vendor, system_type, description
                    FROM systems
                    WHERE id = :sid AND organization_id = :oid
                """),
                {"sid": system_id, "oid": org_id},
            ).fetchone()
            if not row:
                raise RuntimeError("System not found")
            system_name, system_vendor, system_type, description = row

            from services.recipe_generation_engine import run_generation

            result = run_generation(
                system_name=system_name,
                vendor=system_vendor,
                system_type=system_type,
                description=description,
            )

            template_id = _persist_generated(
                session, org_id, system_id, system_name, system_vendor,
                system_type, result["recipes"],
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        _set_status(system_id, "completed", template_id=template_id)
        logger.info(f"Recipe generation for system {system_id} completed (template {template_id})")
        return {"system_id": system_id, "status": "completed", "template_id": template_id}

    except Exception as exc:
        logger.exception(f"Recipe generation for system {system_id} failed: {exc}")
        _set_status(system_id, "failed", error=str(exc)[:500])
        return {"system_id": system_id, "status": "failed", "error": str(exc)[:500]}
