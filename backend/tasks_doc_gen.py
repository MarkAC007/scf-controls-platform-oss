"""
Celery tasks for document generation.

Progress is reported through a Redis status key polled by the API:
    scf:cache:v1:docgen:{organization_id}
        -> {"status": queued|running|completed|completed_with_errors|partial|failed, ...}

``partial`` means the run hit its soft time limit and stopped cleanly part-way
through. It is distinct from ``completed_with_errors``: nothing was refused, the
batch simply ran out of time, and the documents it did produce are committed and
real. Re-running the same request finishes the remainder.

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

**Known trade-off, unresolved.** That reasoning was written when this task
could not exceed the global 600s limit. It now declares 10800s, and the stock
worker (``docker-compose.yml``) is a single container at ``--concurrency=2``
consuming every queue with ``worker_prefetch_multiplier=1``. Two organisations
starting full-catalogue runs can therefore occupy both slots for up to three
hours, during which nothing else — notifications, evidence assessment, vendor
research, catalog import — is consumed. Previously the worst case was ten
minutes. Raising the ceiling did not create the coupling, but it did make it
expensive.

The real fix is to stop the batch being one long task: dispatching each
document as its own short task (~300s) means no worker is ever pinned for
hours and the batch parallelises instead of running nose-to-tail. That is a
larger change — the organisation lock moves to an orchestrator and the status
key becomes an aggregation — and is deliberately not attempted here. Until
then, an operator running large generations should give the worker more
concurrency or run a second worker.
"""
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db_url import get_sync_database_url

logger = logging.getLogger(__name__)

DOCGEN_STATUS_PREFIX = "scf:cache:v1:docgen"

#: Hard and soft time limits for ``doc_gen.generate``.
#:
#: Generation is strictly sequential — one document per iteration of the loop
#: below, no fan-out. Measured against ``claude-opus-4-8``, a single document
#: takes 46-81s (median 67s, p95 81s). The full catalogue is 34 domains x 3
#: domain-scoped generators + 6 organisation-level = 108 documents, so a
#: worst-case complete run is 108 x 81s ~= 8,750s. The hard limit is set above
#: that with ~23% headroom.
#:
#: The task previously declared no limits at all and inherited the global
#: 600s/540s from ``celery_app.py``, which capped a batch at ~8 documents and
#: SIGKILLed anything larger mid-run.
#:
#: The grace window between soft and hard is deliberately wide (600s, vs the
#: global 60s): the soft handler has to roll back and finish reporting a batch
#: that may already hold a hundred results.
DOCGEN_TIME_LIMIT = 10800  # 3h
DOCGEN_SOFT_TIME_LIMIT = 10200  # 2h50m

#: TTLs for the progress key and the per-organisation generation lock.
#:
#: **Both are derived from the hard time limit on purpose.** They are the two
#: values that must outlive the longest possible run: a status key that expires
#: mid-run blanks the progress bar (and stops the client polling, because the
#: endpoint then reports ``idle``), and a lock that expires mid-run defeats the
#: already-running guard and permits a second concurrent generation for the same
#: organisation. Hard-coding them independently is how they came to be shorter
#: than any usable timeout in the first place; deriving them means raising
#: ``DOCGEN_TIME_LIMIT`` can never silently re-introduce that bug.
DOCGEN_STATUS_TTL = DOCGEN_TIME_LIMIT + 600
DOCGEN_LOCK_TTL = DOCGEN_TIME_LIMIT + 600

# ---------------------------------------------------------------------------
# Sync database session (Celery runs outside the async event loop)
# ---------------------------------------------------------------------------
_SYNC_DATABASE_URL = (
    get_sync_database_url("postgresql+asyncpg://odin:changeme@localhost:5432/odin_scf")
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
    except SoftTimeLimitExceeded:
        # Celery interrupting the worker, not Redis failing. The broad handler
        # below would log it as a cache problem and swallow it, and the signal
        # is delivered exactly once — losing it here means the batch runs on to
        # an uncatchable SIGKILL. Let it reach the task's own handler.
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("doc_gen status write failed for org=%s: %s", organization_id, exc)


def get_status(organization_id: str) -> Optional[Dict[str, Any]]:
    """Read the current status, or ``None`` if there is none."""
    try:
        raw = _get_sync_redis().get(docgen_status_key(organization_id))
        return json.loads(raw) if raw else None
    except SoftTimeLimitExceeded:
        # Celery interrupting the worker, not Redis failing. The broad handler
        # below would log it as a cache problem and swallow it, and the signal
        # is delivered exactly once — losing it here means the batch runs on to
        # an uncatchable SIGKILL. Let it reach the task's own handler.
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("doc_gen status read failed for org=%s: %s", organization_id, exc)
        return None


#: How stale a "running" status may be before its run is presumed dead.
#:
#: The progress callback rewrites the status key at every stage of every
#: document, and the slowest document observed was 81s, so a live run touches
#: this key far more often than this. Generous enough that a slow document is
#: never mistaken for a corpse; short enough that a genuinely dead run does not
#: wedge the organisation for the lock's full TTL.
DOCGEN_HEARTBEAT_STALE_AFTER = 600


def _incumbent_is_alive(existing: Optional[Dict[str, Any]]) -> bool:
    """Is there a generation genuinely in flight right now?

    Used only to decide whether a refusal should overwrite the shared status
    key. An unparseable or missing timestamp counts as NOT alive: the cost of
    being wrong that way is one spurious "already running" error, against
    silently wedging the organisation for hours the other way.
    """
    if not existing or existing.get("status") not in ("queued", "running"):
        return False
    raw = existing.get("updated_at")
    if not raw:
        return False
    try:
        seen = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return False
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - seen).total_seconds()
    return age < DOCGEN_HEARTBEAT_STALE_AFTER


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
    except SoftTimeLimitExceeded:
        # Celery interrupting the worker, not Redis failing. The broad handler
        # below would log it as a cache problem and swallow it, and the signal
        # is delivered exactly once — losing it here means the batch runs on to
        # an uncatchable SIGKILL. Let it reach the task's own handler.
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("doc_gen lock unavailable for org=%s: %s", organization_id, exc)
        return True


def refresh_lock(organization_id: str) -> None:
    """Re-arm the lock's TTL while a run is still making progress.

    ``acquire_lock`` sets the expiry once, at the start. Without this the lock
    is a countdown from the moment the batch began rather than from its last
    sign of life, so a long-but-healthy run eventually releases its own slot and
    a second generation can start alongside it. Refreshing per document keeps
    the "assumed dead" semantics the TTL is there to provide.

    Like the other lock helpers this never raises — a failed refresh must not
    destroy a batch that is otherwise producing documents.
    """
    try:
        _get_sync_redis().expire(docgen_lock_key(organization_id), DOCGEN_LOCK_TTL)
    except SoftTimeLimitExceeded:
        # Celery interrupting the worker, not Redis failing. The broad handler
        # below would log it as a cache problem and swallow it, and the signal
        # is delivered exactly once — losing it here means the batch runs on to
        # an uncatchable SIGKILL. Let it reach the task's own handler.
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("doc_gen lock refresh failed for org=%s: %s", organization_id, exc)


def release_lock(organization_id: str) -> None:
    """Drop the organisation's generation slot.

    Deliberately WITHOUT the ``SoftTimeLimitExceeded`` re-raise its four
    siblings above carry. This one runs in the task's ``finally``, after the
    batch has already decided its outcome and written its status. Re-raising
    out of a cleanup path would replace whatever was propagating and turn a
    finished run into a failed one, which is the opposite of what the guard is
    for everywhere else. Cleanup must not be able to change the verdict.
    """
    try:
        _get_sync_redis().delete(docgen_lock_key(organization_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("doc_gen lock release failed for org=%s: %s", organization_id, exc)


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------


@shared_task(
    name="doc_gen.generate",
    bind=True,
    max_retries=0,
    time_limit=DOCGEN_TIME_LIMIT,
    soft_time_limit=DOCGEN_SOFT_TIME_LIMIT,
)
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
        # Refuse, but do NOT overwrite the incumbent's progress key. The status
        # key is shared per organisation, so writing "failed" here reports the
        # *refusal* as though the *running* batch had died: the progress bar
        # dies and the client stops polling while generation is still going.
        #
        # This is reachable without any user doing anything wrong. Celery's
        # Redis transport redelivers a task that outlives the broker's
        # visibility timeout, and with acks_late that duplicate lands here
        # while the original is still working. The lock is what makes the
        # duplicate harmless — provided it stays quiet.
        # ...but only stay quiet for an incumbent that is demonstrably ALIVE.
        # The lock now outlives a whole run (DOCGEN_LOCK_TTL) and is refreshed
        # per document, so a worker killed without unwinding — redeploy, OOM,
        # the hard limit itself — leaves both the lock and a "running" status
        # behind for hours. Staying quiet on those would wedge the organisation
        # silently: every Generate would return 200, write nothing, and leave
        # the client polling a frozen progress bar with no error to act on.
        # Liveness is judged on the status key's own heartbeat, which
        # ``progress`` rewrites at every stage of every document.
        if not _incumbent_is_alive(get_status(organization_id)):
            _set_status(
                organization_id, "failed",
                error="A generation is already running for this organisation.",
            )
        return {"results": [], "generated": 0, "skipped": 0, "failed": 0,
                "error": "already_running"}

    results: List[Dict[str, Any]] = []
    generated = skipped = failed = 0
    interrupted = False
    session = None

    try:
        _set_status(
            organization_id, "running",
            total=len(requests), completed=0, stage="starting",
        )
        session = _get_sync_session()

        for index, request in enumerate(requests):
            # Re-arm the lock before each document, not once at the start.
            refresh_lock(organization_id)

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
            except SoftTimeLimitExceeded:
                # Celery is telling us the hard limit is coming. This MUST be
                # caught ahead of the broad handler below, because
                # SoftTimeLimitExceeded subclasses Exception: caught there, it
                # would be recorded as one failed document and the loop would
                # start the next one, spending the entire grace window on work
                # that cannot finish and guaranteeing an uncatchable SIGKILL.
                # SIGKILL also means the outer handler never runs, so the
                # status key is stranded at "running" until its TTL expires.
                #
                # Break instead, and let the summary below report what did get
                # made. Documents already committed are real — the commit is
                # per document — so an interrupted batch is a partial success,
                # not a dead task. The interrupted document is deliberately not
                # counted as failed: nothing about it was wrong, we ran out of
                # time.
                session.rollback()
                interrupted = True
                logger.warning(
                    "doc_gen soft time limit reached for org=%s after %d/%d "
                    "documents; stopping cleanly at %s",
                    organization_id, index, len(requests), label,
                )
                break
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
            "interrupted": interrupted,
        }
        if interrupted:
            # A distinct status rather than completed_with_errors: nothing
            # failed, the run was cut short. The client needs to be able to
            # tell "these six documents refused" from "we ran out of time at
            # document ninety" — the second one is re-runnable as-is.
            status = "partial"
        else:
            status = "completed" if failed == 0 else "completed_with_errors"
        _set_status(
            organization_id,
            status,
            total=len(requests),
            completed=len(results),
            **summary,
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
