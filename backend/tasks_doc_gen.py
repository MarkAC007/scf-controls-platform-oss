"""
Celery tasks for document generation.

Progress is reported through a Redis status key polled by the API:
    scf:cache:v1:docgen:{organization_id} -> {"status": queued|running|completed|failed, ...}

The key is per-organisation rather than per-document because it doubles as the
concurrency lock. Two simultaneous generations for one organisation would race
on the merge: both would read the same stored sections, both would compute a
merge against them, and the second write would silently discard the first
merge's decisions. One run at a time per organisation removes the race without
a distributed lock service.

**Queue choice.** These tasks route to ``default``, not to a dedicated queue.
``default`` is the one queue every Celery worker consumes out of the box, so
these tasks still run under a worker started with no ``-Q`` list at all. A
``doc_gen`` queue would work with the stock compose ``-Q`` list and be
silently dead under any other worker invocation, which is the worst of both
outcomes. ``tasks_automation`` routes to ``default`` for exactly this reason;
this follows the precedent rather than inventing a second convention.
"""
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from celery import shared_task
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)

DOCGEN_STATUS_PREFIX = "scf:cache:v1:docgen"
DOCGEN_STATUS_TTL = int(timedelta(hours=1).total_seconds())

#: How long an organisation's generation lock may be held before it is assumed
#: dead. Longer than the Celery soft time limit (540s) so a task that is merely
#: slow is never overtaken by a second run.
DOCGEN_LOCK_TTL = 900

# ---------------------------------------------------------------------------
# Sync database session (Celery runs outside the async event loop)
# ---------------------------------------------------------------------------
_SYNC_DATABASE_URL = (
    os.getenv("DATABASE_URL", "postgresql+asyncpg://odin:changeme@localhost:5432/odin_scf")
    .replace("+asyncpg", "+psycopg2")
    .replace("?ssl=require", "?sslmode=require")
)

_sync_engine = None
SyncSession = None


def _get_sync_session():
    global _sync_engine, SyncSession
    if SyncSession is None:
        _sync_engine = create_engine(
            _SYNC_DATABASE_URL, pool_pre_ping=True, pool_size=2, max_overflow=3
        )
        SyncSession = sessionmaker(bind=_sync_engine, expire_on_commit=False)
    return SyncSession()


_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


def _get_sync_redis():
    import redis as sync_redis
    return sync_redis.from_url(
        _REDIS_URL, decode_responses=True,
        socket_connect_timeout=5, socket_timeout=5,
    )


def docgen_status_key(organization_id: str) -> str:
    return f"{DOCGEN_STATUS_PREFIX}:{organization_id}"


def docgen_lock_key(organization_id: str) -> str:
    return f"{DOCGEN_STATUS_PREFIX}:lock:{organization_id}"


def _set_status(organization_id: str, status: str, **extra) -> None:
    """Write generation status to Redis. Never raises.

    Status reporting must not be able to fail a generation — a document that
    was produced but whose progress bar broke is a UI defect; a document lost
    because Redis blinked is a data defect.
    """
    try:
        payload = {
            "status": status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            **extra,
        }
        _get_sync_redis().setex(
            docgen_status_key(organization_id), DOCGEN_STATUS_TTL, json.dumps(payload)
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("doc_gen status write failed for org=%s: %s", organization_id, exc)


def get_status(organization_id: str) -> Optional[Dict[str, Any]]:
    """Read the current status, or ``None`` if there is none."""
    try:
        raw = _get_sync_redis().get(docgen_status_key(organization_id))
        return json.loads(raw) if raw else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("doc_gen status read failed for org=%s: %s", organization_id, exc)
        return None


def acquire_lock(organization_id: str) -> bool:
    """Claim the organisation's generation slot.

    Returns ``True`` if the caller now holds it. If Redis is unreachable the
    call returns ``True`` — refusing every generation because the progress
    cache is down would be a worse failure than the race the lock prevents,
    and the race requires two concurrent runs to actually occur.
    """
    try:
        return bool(
            _get_sync_redis().set(
                docgen_lock_key(organization_id), "1", nx=True, ex=DOCGEN_LOCK_TTL
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("doc_gen lock unavailable for org=%s: %s", organization_id, exc)
        return True


def release_lock(organization_id: str) -> None:
    try:
        _get_sync_redis().delete(docgen_lock_key(organization_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("doc_gen lock release failed for org=%s: %s", organization_id, exc)


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------


@shared_task(name="doc_gen.generate", bind=True, max_retries=0)
def generate_documents_task(
    self,
    organization_id: str,
    requests: List[Dict[str, Any]],
    user_id: Optional[str] = None,
    user_email: Optional[str] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Run one or more document generations for an organisation.

    Args:
        organization_id: Resolved from membership by the API before enqueue.
        requests: ``[{"generator": "policy", "domain_id": "IAC"}, ...]``.
        user_id, user_email: Recorded on documents and transitions.
        force: Bypass the fingerprint skip.

    Returns:
        ``{"results": [...], "generated": n, "skipped": n, "failed": n}``.

    Retries are disabled deliberately. A generation is not idempotent from the
    user's point of view — it can consume model tokens and it can produce a
    merge conflict a human must resolve — so a silent retry could bill twice
    and queue two review tasks for one request.
    """
    from services.doc_gen.licence import LicenceError
    from services.doc_gen.pipeline import PipelineError, run_generation
    from services.doc_gen.registry import GeneratorNotFound

    if not acquire_lock(organization_id):
        logger.info("doc_gen already running for org=%s; refusing", organization_id)
        _set_status(
            organization_id, "failed",
            error="A generation is already running for this organisation.",
        )
        return {"results": [], "generated": 0, "skipped": 0, "failed": 0,
                "error": "already_running"}

    results: List[Dict[str, Any]] = []
    generated = skipped = failed = 0
    session = None

    try:
        _set_status(
            organization_id, "running",
            total=len(requests), completed=0, stage="starting",
        )
        session = _get_sync_session()

        for index, request in enumerate(requests):
            generator_name = request.get("generator")
            domain_id = request.get("domain_id")
            label = f"{generator_name}{f' ({domain_id})' if domain_id else ''}"

            def progress(stage: str, message: str, _label=label, _i=index) -> None:
                _set_status(
                    organization_id, "running",
                    total=len(requests), completed=_i,
                    stage=stage, message=f"{_label}: {message}",
                )

            try:
                result = run_generation(
                    session,
                    organization_id=organization_id,
                    generator_name=generator_name,
                    domain_id=domain_id,
                    user_id=user_id,
                    user_email=user_email,
                    force=force,
                    progress=progress,
                )
                session.commit()
                results.append(result.to_dict())
                if result.action == "skipped":
                    skipped += 1
                else:
                    generated += 1
            except (LicenceError, PipelineError, GeneratorNotFound) as exc:
                # Expected refusals. Roll back this document only — one refused
                # generator must not discard documents already committed in
                # this batch.
                session.rollback()
                failed += 1
                logger.info("doc_gen refused %s for org=%s: %s",
                            label, organization_id, exc)
                results.append({
                    "generator": generator_name, "domain_id": domain_id or "",
                    "action": "failed", "error": str(exc),
                })
            except Exception as exc:  # noqa: BLE001
                session.rollback()
                failed += 1
                logger.exception("doc_gen failed %s for org=%s", label, organization_id)
                results.append({
                    "generator": generator_name, "domain_id": domain_id or "",
                    "action": "failed", "error": f"Generation failed: {exc}",
                })

        summary = {
            "results": results,
            "generated": generated,
            "skipped": skipped,
            "failed": failed,
        }
        _set_status(
            organization_id,
            "completed" if failed == 0 else "completed_with_errors",
            total=len(requests), completed=len(requests), **summary,
        )
        return summary

    except Exception as exc:  # noqa: BLE001
        logger.exception("doc_gen batch failed for org=%s", organization_id)
        _set_status(organization_id, "failed", error=str(exc))
        raise
    finally:
        if session is not None:
            session.close()
        release_lock(organization_id)
