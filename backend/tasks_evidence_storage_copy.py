"""Celery task that copies an organisation's evidence objects between stores.

An organisation that switches evidence store keeps every file it already had.
Those bytes are in the old store, and nothing moves them on its own: each
``EvidenceFile`` row carries the configuration its bytes were written under, so
it keeps reading from there indefinitely. This task is the supported way to
bring them across.

What it does, per file, in this order:

    head source -> stream source into a spooled temp file, hashing as it goes
    -> stream that into the target -> head target (size) -> read target back
    (SHA-256) -> update the row -> commit

**Update, never insert.** ``EvidenceFile.s3_key`` is ``String(1024) unique``
and keys are identical in shape across providers, so the same key exists in
both stores while the copy runs. A second row for the copy would violate that
constraint — and would double every count an auditor reads. The row's
``storage_config_id`` moves; nothing else about it changes.

**Commit per row.** That is what makes this resumable: a run that dies halfway
leaves every finished file pointing at the target and every unfinished one
pointing at the source, both readable, and a re-run simply skips the finished
ones. It is also what makes a failure partial-but-consistent rather than
catastrophic — there is no moment at which a file is neither here nor there.

**Nothing is ever deleted.** Not by this task, not on success, not on failure.
The source objects stay exactly where they are, which is what makes the switch
reversible right up until an operator deletes them deliberately. A test asserts
the source client's ``delete_object`` is never called across a whole run.

**Verification is end-to-end.** The digest is computed over the bytes read out
of the source and compared with a digest computed over the bytes read back out
of the target, plus a size comparison from both stores' HEAD. That costs one
extra read of every object, and it buys the only claim worth making: the bytes
in the target are the bytes that were in the source. A row that fails any check
is left pointing at the source and reported.

**Progress lives in Redis**, keyed by run id, exactly as ``tasks_doc_gen``
reports document generation. Long jobs in this repo split two ways: a job whose
run is a domain object of its own gets a table (``tasks_reconciliation``), and a
job that only needs to tell a polling UI how far it has got uses a Redis status
key (``tasks_doc_gen``). A copy run is the second kind, and ISC 62 asks for no
database change that is not needed. The consequence is stated plainly: if Redis
is lost the run record is lost. The *data* is not, because of the per-row
commit, and a re-trigger resumes from wherever the rows actually are.
"""
import hashlib
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import create_engine, func, or_, select
from sqlalchemy.orm import sessionmaker

from db_url import get_sync_database_url

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------

#: A copy is bounded by how much there is to copy, not by how much work the
#: platform wants to do, so these are generous. They still exist because an
#: unbounded task cannot be redelivered safely: `visibility_timeout` in
#: `celery_app.py` (11400s) must stay above every task's hard limit, and this
#: one is deliberately well under it.
COPY_TIME_LIMIT = 10800  # 3h
COPY_SOFT_TIME_LIMIT = 10200  # 2h50m

#: Redis key namespace. Sits alongside `scf:storage:version`, which is the
#: cross-process configuration version the resolver already watches.
COPY_RUN_PREFIX = "scf:storage:copyrun"

#: How long a finished run stays readable. Long enough that an administrator
#: who walked away from the screen still finds out how it went.
COPY_RUN_TTL = COPY_TIME_LIMIT + 86400

#: How many run ids are remembered per organisation, newest first.
COPY_RUN_HISTORY = 20

#: Above this many bytes the streamed object spills from memory onto disk.
#: Evidence objects are routinely tens of megabytes and a worker holding
#: several whole ones in memory is a worker that gets OOM-killed halfway
#: through a copy.
SPOOL_MAX_BYTES = 8 * 1024 * 1024

#: Read size when re-reading the target to verify it.
VERIFY_CHUNK_BYTES = 64 * 1024

# Run states.
STATE_QUEUED = "queued"
STATE_RUNNING = "running"
STATE_COMPLETED = "completed"
STATE_COMPLETED_WITH_ERRORS = "completed_with_errors"
STATE_FAILED = "failed"

TERMINAL_STATES = (STATE_COMPLETED, STATE_COMPLETED_WITH_ERRORS, STATE_FAILED)

# ---------------------------------------------------------------------------
# Sync database session (Celery runs outside the async event loop)
# ---------------------------------------------------------------------------

_SYNC_DATABASE_URL = get_sync_database_url(
    "postgresql+asyncpg://cg:cg@localhost:5432/cg_scf"
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


# ---------------------------------------------------------------------------
# Run records (Redis)
# ---------------------------------------------------------------------------

_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


def _get_sync_redis():
    import redis as sync_redis

    return sync_redis.from_url(
        _REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
    )


def run_key(run_id: str) -> str:
    return f"{COPY_RUN_PREFIX}:run:{run_id}"


def org_history_key(organization_id: str) -> str:
    return f"{COPY_RUN_PREFIX}:org:{organization_id}"


def org_active_key(organization_id: str) -> str:
    return f"{COPY_RUN_PREFIX}:active:{organization_id}"


def write_run(record: Dict[str, Any]) -> None:
    """Persist a run record. Never raises.

    Progress reporting must not be able to fail a copy. A copy whose progress
    bar broke is a UI defect; a copy abandoned because Redis blinked would
    leave an administrator with no idea which files had moved.
    """
    try:
        record = dict(record)
        record["updated_at"] = datetime.now(timezone.utc).isoformat()
        client = _get_sync_redis()
        client.setex(run_key(record["run_id"]), COPY_RUN_TTL, json.dumps(record))
        if record.get("organization_id"):
            history = org_history_key(str(record["organization_id"]))
            client.lrem(history, 0, record["run_id"])
            client.lpush(history, record["run_id"])
            client.ltrim(history, 0, COPY_RUN_HISTORY - 1)
            client.expire(history, COPY_RUN_TTL)
    except SoftTimeLimitExceeded:
        # Celery interrupting the worker, not Redis failing. The broad handler
        # below would log it as a cache problem and swallow it, and the signal
        # arrives exactly once — losing it here means the run continues to an
        # uncatchable SIGKILL.
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("Copy run status write failed for %s: %s", record.get("run_id"), exc)


def read_run(run_id: str) -> Optional[Dict[str, Any]]:
    """One run record, or ``None``."""
    try:
        raw = _get_sync_redis().get(run_key(run_id))
        return json.loads(raw) if raw else None
    except SoftTimeLimitExceeded:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("Copy run status read failed for %s: %s", run_id, exc)
        return None


def list_runs(organization_id: str, limit: int = COPY_RUN_HISTORY) -> List[Dict[str, Any]]:
    """This organisation's recent runs, newest first."""
    try:
        client = _get_sync_redis()
        ids = client.lrange(org_history_key(organization_id), 0, max(0, limit - 1))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Copy run list failed for org=%s: %s", organization_id, exc)
        return []

    runs = []
    for rid in ids:
        record = read_run(rid)
        if record is not None:
            runs.append(record)
    return runs


def active_run_id(organization_id: str) -> Optional[str]:
    """The run id of a copy currently in flight for this organisation, if any.

    The marker is what the trigger endpoint refuses on, and it carries the same
    TTL as the task's hard time limit so a worker killed with SIGKILL cannot
    wedge the organisation permanently. A marker whose run record says the run
    finished is treated as stale and cleared: the task clears its own marker on
    every exit path it can reach, and this covers the one it cannot.
    """
    try:
        rid = _get_sync_redis().get(org_active_key(organization_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Copy run active check failed for org=%s: %s", organization_id, exc)
        return None
    if not rid:
        return None
    record = read_run(rid)
    if record is not None and record.get("status") in TERMINAL_STATES:
        clear_active(organization_id)
        return None
    return rid


def claim_active(organization_id: str, run_id: str) -> bool:
    """Claim the organisation's copy slot. ``False`` if someone else holds it.

    SET NX, so two administrators pressing the button at the same moment
    produce one run and one refusal rather than two runs racing each other over
    the same rows. Redis being unreachable returns ``True`` — the copy itself
    is idempotent per row, so the cost of a duplicate run is wasted egress, and
    refusing every copy because the lock store is down is the worse failure.
    """
    try:
        client = _get_sync_redis()
        return bool(
            client.set(org_active_key(organization_id), run_id, nx=True, ex=COPY_TIME_LIMIT)
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Copy run claim failed for org=%s: %s", organization_id, exc)
        return True


def clear_active(organization_id: str) -> None:
    try:
        _get_sync_redis().delete(org_active_key(organization_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Copy run release failed for org=%s: %s", organization_id, exc)


def new_run_record(
    run_id: str,
    organization_id: str,
    source_config_id: str,
    target_config_id: str,
    status: str = STATE_QUEUED,
) -> Dict[str, Any]:
    """The shape every reader of a run sees. No credential, ever."""
    return {
        "run_id": run_id,
        "organization_id": organization_id,
        "source_config_id": source_config_id,
        "target_config_id": target_config_id,
        "status": status,
        "total": 0,
        "copied": 0,
        "failed": 0,
        "skipped": 0,
        "remaining": 0,
        "failures": [],
        "source_retired": False,
        "source_retired_reason": "",
        "message": "",
        "started_at": None,
        "finished_at": None,
    }


# ---------------------------------------------------------------------------
# The copy
# ---------------------------------------------------------------------------


def _digest_of_target(storage_service, target_config, s3_key: str) -> str:
    """SHA-256 of what is actually in the target now.

    Read back rather than inferred. An ETag is not a digest on a multipart
    upload and is not a digest at all on a store that encrypts server-side, so
    comparing ETags would pass on exactly the objects most likely to be wrong.
    """
    chunks = storage_service.download_blob_stream_for_config(target_config, s3_key)
    if chunks is None:
        raise RuntimeError("target object could not be read back")
    digest = hashlib.sha256()
    for chunk in chunks:
        digest.update(chunk)
    return digest.hexdigest()


def copy_one_object(
    storage_service,
    source_config,
    target_config,
    s3_key: str,
    content_type: str,
    org_id: str,
) -> Dict[str, Any]:
    """Move one object's bytes source -> target and prove they arrived.

    Returns ``{"ok": True, "size": int, "sha256": str}`` or
    ``{"ok": False, "reason": str}``. Never deletes anything, from either
    store. Never raises: a per-object failure is data the run reports, not a
    reason to abandon the files after it.
    """
    try:
        source_head = storage_service.head_object(source_config, s3_key)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"source unreadable ({type(exc).__name__})"}

    if source_head is None:
        # The row says there is an object and the source store disagrees. That
        # is an integrity finding in its own right, and copying nothing while
        # reporting success would bury it.
        return {"ok": False, "reason": "object not found in the source store"}

    source_size = int(source_head.get("size") or 0)

    try:
        with tempfile.SpooledTemporaryFile(max_size=SPOOL_MAX_BYTES) as buffer:
            storage_service.download_object_to_fileobj(source_config, s3_key, buffer)

            buffer.seek(0)
            digest = hashlib.sha256()
            read_size = 0
            while True:
                chunk = buffer.read(VERIFY_CHUNK_BYTES)
                if not chunk:
                    break
                read_size += len(chunk)
                digest.update(chunk)
            source_sha256 = digest.hexdigest()

            if read_size != source_size:
                return {
                    "ok": False,
                    "reason": (
                        f"source read {read_size} bytes, HEAD said {source_size}"
                    ),
                }

            buffer.seek(0)
            storage_service.upload_object_from_fileobj(
                target_config, s3_key, buffer, content_type, org_id
            )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"copy failed ({type(exc).__name__})"}

    try:
        target_head = storage_service.head_object(target_config, s3_key)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"target unreadable ({type(exc).__name__})"}

    if target_head is None:
        return {"ok": False, "reason": "object absent from the target after the write"}

    target_size = int(target_head.get("size") or 0)
    if target_size != source_size:
        return {
            "ok": False,
            "reason": f"size mismatch: source {source_size}, target {target_size}",
        }

    try:
        target_sha256 = _digest_of_target(storage_service, target_config, s3_key)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"target verification failed ({type(exc).__name__})"}

    if target_sha256 != source_sha256:
        return {"ok": False, "reason": "checksum mismatch between source and target"}

    return {"ok": True, "size": source_size, "sha256": source_sha256}


def _rows_to_copy(session, org_id: str, source_config_id: str):
    """Every evidence file of this organisation whose bytes are in the source.

    Two classes of row qualify:

    1. Rows naming the source outright. This is the normal case: a file is
       stamped with its store when it is written, and every file an
       organisation held is stamped with the outgoing store when it activates a
       new one.
    2. Rows carrying NULL **when the source is still what this organisation
       resolves to**. NULL means "resolve me by organisation", so those bytes
       are in the source exactly while the source is the answer. Once the
       organisation has switched, NULL no longer points at the source and this
       clause correctly contributes nothing.

    Soft-deleted rows are included deliberately. They still hold bytes, they
    still count towards ``files_referencing`` — which is what refuses the
    deletion of a configuration still in use — and leaving them behind would
    make the source impossible to retire while telling the administrator the
    copy had finished.
    """
    from models import EvidenceFile
    from services import storage_config as storage_config_module

    predicates = [EvidenceFile.storage_config_id == source_config_id]

    effective = storage_config_module.resolve(str(org_id))
    if str(effective.config_id) == str(source_config_id):
        predicates.append(EvidenceFile.storage_config_id.is_(None))

    return (
        session.execute(
            select(EvidenceFile)
            .where(
                EvidenceFile.organization_id == org_id,
                or_(*predicates),
            )
            .order_by(EvidenceFile.uploaded_at.asc())
        )
        .scalars()
        .all()
    )


#: What the run record says instead of "retired" when the source is a store
#: this organisation does not own. It is a fact about the installation, not a
#: failure, and the panel repeats it verbatim.
PLATFORM_SOURCE_REASON = "platform store, left in service"
PLATFORM_SOURCE_IN_USE_REASON = (
    "platform store, left in service; {count} of this organisation's file(s) "
    "still reference it"
)
BUNDLED_SOURCE_REASON = (
    "bundled store, left in service; it is managed by the installation"
)


async def _org_files_referencing(session, evidence_file_model, org_id, config_id) -> int:
    """This organisation's files that still name ``config_id``.

    Deliberately not ``evidence_storage_admin.files_referencing``, which counts
    across every tenant. On a shared platform row that count is another
    organisation's business, and letting it decide this run's report would make
    a completed copy look unfinished for a reason the operator cannot act on.
    """
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(evidence_file_model)
                .where(
                    evidence_file_model.organization_id == org_id,
                    evidence_file_model.storage_config_id == config_id,
                )
            )
        ).scalar_one()
    )


async def _retire_source_if_empty(
    source_config_id: str, run_id: str, org_id: str
) -> Tuple[bool, str]:
    """Report whether the source is out of service with nothing left in it.

    Returns ``(retired, reason)``. The reason is empty when the answer is a
    plain yes; otherwise it is a sentence the panel can show verbatim.

    **Scope guard, and it is the important half.** A source may legitimately be
    the shared platform store — that is how a bundled install gets its evidence
    out — and retiring that row would take the bundled store out of service for
    every other tenant on the installation. So a row that is not this
    organisation's own is never retired, whatever the reference count says, and
    neither is a row the installer marked ``is_bundled``. For a platform
    source, "finished with" is asked of *this organisation's* rows only, since
    other tenants' files are none of this run's business.

    Retires it when it is still active and nothing references it, going
    through ``evidence_storage_admin.retire_config`` rather than issuing an
    UPDATE, so the audit row, the version bump and the cache invalidation
    happen exactly as they do when an administrator retires a configuration by
    hand. Retiring is not deleting: the row stays, the objects stay, and every
    file that still names it keeps resolving.

    The return value answers the question the screen actually asks — *is the
    old store finished with?* — not *did this run perform the retirement*. The
    difference is not academic: activating a new store already retires the
    outgoing one, so by the time a copy runs the source is normally retired
    already. Reporting False there would have told every operator in the
    ordinary flow that the store they had just emptied was still in service.
    """
    from database import AsyncSessionLocal, engine
    from models import EvidenceFile, EvidenceStorageConfig
    from services import evidence_storage_admin, storage_config

    try:
        async with AsyncSessionLocal() as session:
            row = (
                await session.execute(
                    select(EvidenceStorageConfig).where(
                        EvidenceStorageConfig.id == UUID(str(source_config_id))
                    )
                )
            ).scalars().first()
            if row is None:
                return False, "the source configuration no longer exists"

            org_uuid = UUID(str(org_id))

            # -- the scope guard, before any count and before any write ----
            if row.organization_id is None:
                remaining_here = await _org_files_referencing(
                    session, EvidenceFile, org_uuid, row.id
                )
                if remaining_here:
                    return False, PLATFORM_SOURCE_IN_USE_REASON.format(
                        count=remaining_here
                    )
                return False, PLATFORM_SOURCE_REASON
            if row.organization_id != org_uuid:
                return False, "the source belongs to another organisation"
            if row.is_bundled:
                return False, BUNDLED_SOURCE_REASON

            # The reference count is the gate, whatever the status says. A
            # store with files still in it is not finished with, and a store
            # with none is — whether this run retired it or activation did.
            still_referencing = await evidence_storage_admin.files_referencing(
                session, row.id
            )
            if still_referencing:
                logger.info(
                    "Copy run %s left storage configuration %s in service: "
                    "%s file(s) still reference it",
                    run_id,
                    source_config_id,
                    still_referencing,
                )
                return False, (
                    f"{still_referencing} evidence file(s) still reference it"
                )

            if row.status == storage_config.STATUS_ACTIVE:
                await evidence_storage_admin.retire_config(
                    session,
                    row,
                    evidence_storage_admin.Actor(label=f"evidence-store-copy:{run_id}"),
                )
            return True, ""
    except Exception:  # noqa: BLE001 — retirement is the tidy-up, not the job
        logger.exception(
            "Copy run %s could not retire source configuration %s",
            run_id,
            source_config_id,
        )
        return False, "the source configuration could not be retired"
    finally:
        await engine.dispose()


@shared_task(
    bind=True,
    name="tasks_evidence_storage_copy.copy_evidence_store",
    time_limit=COPY_TIME_LIMIT,
    soft_time_limit=COPY_SOFT_TIME_LIMIT,
)
def copy_evidence_store(
    self,
    org_id: str,
    source_config_id: str,
    target_config_id: str,
    run_id: str,
) -> Dict[str, Any]:
    """Copy every evidence object of ``org_id`` from one store to the other.

    Routed to ``default`` for the reason given in ``celery_app.py`` for the
    other evidence tasks: ``default`` is the one queue every worker consumes
    out of the box, and a copy whose queue nobody is listening to would report
    progress forever and move nothing.
    """
    import asyncio

    from services import storage_config as storage_config_module
    from services import storage_service

    record = read_run(run_id) or new_run_record(
        run_id, str(org_id), str(source_config_id), str(target_config_id)
    )
    record["status"] = STATE_RUNNING
    record["started_at"] = record.get("started_at") or datetime.now(
        timezone.utc
    ).isoformat()
    write_run(record)

    session = _get_sync_session()
    try:
        source_config = storage_config_module.resolve_for_file(
            str(org_id), str(source_config_id)
        )
        target_config = storage_config_module.resolve_for_file(
            str(org_id), str(target_config_id)
        )

        if str(source_config.config_id) != str(source_config_id):
            raise RuntimeError(
                f"source configuration {source_config_id} could not be resolved"
            )
        if str(target_config.config_id) != str(target_config_id):
            raise RuntimeError(
                f"target configuration {target_config_id} could not be resolved"
            )
        if not target_config.bucket:
            raise RuntimeError("target configuration names no bucket")

        # The column is a UUID; the resolved config carries its id as a
        # string. Coercing here rather than at assignment keeps the failure —
        # a malformed id — before any row is touched.
        target_uuid = UUID(str(target_config.config_id))

        rows = _rows_to_copy(session, org_id, source_config_id)
        record["total"] = len(rows)
        record["remaining"] = len(rows)
        write_run(record)

        copied = 0
        failed = 0
        skipped = 0
        failures: List[Dict[str, str]] = []

        for index, evidence_file in enumerate(rows, start=1):
            # Resumability, the cheap half: a re-run of a partly finished copy
            # walks straight past everything the previous run committed.
            if str(evidence_file.storage_config_id or "") == str(target_config_id):
                skipped += 1
            else:
                outcome = copy_one_object(
                    storage_service,
                    source_config,
                    target_config,
                    evidence_file.s3_key,
                    evidence_file.content_type,
                    str(org_id),
                )

                if outcome["ok"]:
                    # Update in place. A second row would breach the unique
                    # constraint on `s3_key` and double every count.
                    evidence_file.storage_config_id = target_uuid
                    session.commit()
                    copied += 1
                else:
                    # Left pointing at the source: partial-but-consistent. The
                    # file is still readable, from where its bytes are.
                    session.rollback()
                    failed += 1
                    failures.append(
                        {"s3_key": evidence_file.s3_key, "reason": outcome["reason"]}
                    )
                    logger.warning(
                        "Copy run %s could not move %s: %s",
                        run_id,
                        evidence_file.s3_key,
                        outcome["reason"],
                    )

            record["copied"] = copied
            record["failed"] = failed
            record["skipped"] = skipped
            record["remaining"] = len(rows) - index
            # Bounded so one broken store cannot grow the record without limit.
            record["failures"] = failures[:100]
            write_run(record)

        source_retired = False
        source_retired_reason = ""
        if failed == 0:
            source_retired, source_retired_reason = asyncio.run(
                _retire_source_if_empty(str(source_config_id), run_id, str(org_id))
            )

        record["source_retired"] = source_retired
        record["source_retired_reason"] = source_retired_reason
        record["status"] = STATE_COMPLETED if failed == 0 else STATE_COMPLETED_WITH_ERRORS
        record["finished_at"] = datetime.now(timezone.utc).isoformat()
        record["message"] = (
            f"Copied {copied} file(s); {failed} failed; {skipped} already in the "
            "target store."
        )
        write_run(record)
        logger.info(
            "Copy run %s finished: %s copied, %s failed, %s skipped, source "
            "retired=%s",
            run_id,
            copied,
            failed,
            skipped,
            source_retired,
        )
        return {
            "run_id": run_id,
            "status": record["status"],
            "copied": copied,
            "failed": failed,
            "skipped": skipped,
            "source_retired": source_retired,
            "source_retired_reason": source_retired_reason,
        }
    except Exception as exc:  # noqa: BLE001 — the run record must say what happened
        session.rollback()
        record["status"] = STATE_FAILED
        record["finished_at"] = datetime.now(timezone.utc).isoformat()
        # Type only. An exception string from a storage client can carry an
        # endpoint, a bucket policy or a signed URL.
        record["message"] = f"The copy stopped: {type(exc).__name__}."
        write_run(record)
        logger.exception("Copy run %s failed", run_id)
        raise
    finally:
        session.close()
        clear_active(str(org_id))
