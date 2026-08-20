"""Celery tasks for catalogue imports and the catalog upgrade flow.

Two families share this module:

1. ``catalog.import`` — the OSS "bring your own SCF Excel" live catalogue
   import. A self-hosted operator uploads their licensed SCF .xlsx through the
   UI; the /api/admin/catalog/import endpoint stashes it in object storage and
   enqueues this task. The worker downloads the workbook, extracts it into the
   seeder JSON the backend consumes, and reseeds the catalogue tables. Runs
   ONLY in single-tenant (self-hosted) mode — guarded on OSS_SINGLE_TENANT so
   catalogue tables can never be force-reseeded in a multi-tenant/SaaS
   deployment. The legacy one-shot `catalog-importer` compose service remains
   available as a fallback; this task does not replace it.

2. ``catalog.upgrade_stage`` / ``catalog.upgrade_apply`` /
   ``catalog.upgrade_revert`` + the beat task ``catalog.cleanup_workbooks`` —
   the staged catalog upgrade flow (plan §4.2, WP1b). These operate on
   ``catalog_import_runs`` ledger rows, stash workbooks and diff details in
   object storage via the same s3_service hand-off pattern as the import task,
   and deliberately have NO single-tenant gate: staged diff + typed confirm +
   additive apply + revert replace it (plan §4.2.8).

Async database work runs inside asyncio.run() per task call, with the async
engine disposed inside the still-running loop (tasks_automation.py pattern).
"""
import asyncio
import json
import logging
import os
import sys
import tempfile
from typing import Optional
from uuid import UUID

from sqlalchemy import select

from celery_app import celery_app
from services import s3_service
from services.single_tenant import single_tenant_flag_set

# The extractor ships at /app/scripts in the backend image (see Dockerfile.backend).
if "/app/scripts" not in sys.path:
    sys.path.insert(0, "/app/scripts")

# DATA_DIR / reseed_catalog mirror what the backend seeds from on startup.
from catalog_seeder import DATA_DIR, reseed_catalog

logger = logging.getLogger(__name__)


@celery_app.task(
    name="catalog.import",
    queue="catalog",
    bind=True,
    autoretry_for=(),
    max_retries=0,
)
def import_catalog(self, object_key: str, original_filename: str = "scf.xlsx") -> dict:
    """Download the stashed SCF workbook, extract it, and reseed the catalogue."""
    if not single_tenant_flag_set():
        # Defence-in-depth: the endpoint already gates on single-tenant, but a
        # force-reseed must never run in a multi-tenant deployment.
        raise RuntimeError("catalog import refused: OSS_SINGLE_TENANT not set")

    self.update_state(state="PROGRESS", meta={"step": "downloading"})
    chunks = s3_service.download_blob_stream(object_key)
    if chunks is None:
        raise RuntimeError(f"uploaded workbook not found in storage: {object_key}")

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=True) as tmp:
        for chunk in chunks:
            tmp.write(chunk)
        tmp.flush()

        self.update_state(state="PROGRESS", meta={"step": "extracting"})
        import extract_scf_data  # noqa: E402 — path injected above; pandas loads here

        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            meta = extract_scf_data.extract_to_dir(tmp.name, DATA_DIR)
        except ValueError as exc:
            raise RuntimeError(f"not a valid SCF catalogue workbook: {exc}") from exc

    self.update_state(state="PROGRESS", meta={"step": "seeding", **meta})
    seed_results = asyncio.run(reseed_catalog(force=True))

    logger.info(
        "Catalogue import complete from %s: %s controls",
        original_filename,
        meta.get("controls"),
    )
    return {
        "status": "complete",
        "source_filename": original_filename,
        "catalog_meta": meta,
        "seed_results": seed_results,
    }


# ===========================================================================
# Catalog upgrade flow (WP1b, plan §4.2) — stage / apply / revert / cleanup
# ===========================================================================

# Diff details live NEXT TO the workbook per run. Workbooks are cleanup-eligible
# (last CLEANUP_KEEP_RUNS retained); diff details are NEVER cleaned up — the
# diff is the platform revert anchor (plan §4.1 M4).
UPGRADE_OBJECT_PREFIX = "_catalog-upgrade"
CLEANUP_KEEP_RUNS = 5

# Object-storage metadata requires an org id; upgrade artifacts are
# platform-level, not tenant data.
_PLATFORM_ORG_TAG = "platform"


def diff_detail_object_key(run_id) -> str:
    return f"{UPGRADE_OBJECT_PREFIX}/{run_id}/diff_detail.json"


async def _dispose_async_engine() -> None:
    # asyncio.run() closes its loop after each task call; dispose pooled
    # asyncpg connections inside the still-running loop (tasks_automation.py).
    from database import engine

    await engine.dispose()


async def _load_run(session, run_id: str):
    from models import CatalogImportRun

    result = await session.execute(
        select(CatalogImportRun).where(CatalogImportRun.id == UUID(str(run_id)))
    )
    run = result.scalars().first()
    if run is None:
        raise RuntimeError(f"catalog import run not found: {run_id}")
    return run


def _download_to_temp(object_key: str, suffix: str) -> str:
    chunks = s3_service.download_blob_stream(object_key)
    if chunks is None:
        raise RuntimeError(f"object not found in storage: {object_key}")
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        for chunk in chunks:
            tmp.write(chunk)
        return tmp.name


def _download_json(object_key: str) -> dict:
    chunks = s3_service.download_blob_stream(object_key)
    if chunks is None:
        raise RuntimeError(f"object not found in storage: {object_key}")
    return json.loads(b"".join(chunks))


async def _resolve_from_version(session) -> Optional[str]:
    """Ledger version, else max stamped row version (pre-first-upgrade bootstrap)."""
    from sqlalchemy import func as sa_func

    from catalog_models import SCFCatalogControl
    from services.catalog_apply import get_current_catalog_version

    version = await get_current_catalog_version(session)
    if version:
        return version
    result = await session.execute(
        select(sa_func.max(SCFCatalogControl.catalog_version))
    )
    return result.scalar()


async def _run_upgrade_stage(run_id: str, force: bool) -> dict:
    from database import AsyncSessionLocal
    from services.catalog_diff import VersionGuardError, stage_catalog_diff

    try:
        async with AsyncSessionLocal() as session:
            run = await _load_run(session, run_id)
            if run.status != "staging":
                raise RuntimeError(
                    f"run {run_id} is {run.status!r}, expected 'staging'"
                )
            if not run.workbook_object_key:
                raise RuntimeError(f"run {run_id} has no stashed workbook")

            from_version = await _resolve_from_version(session)
            workbook_path = _download_to_temp(run.workbook_object_key, ".xlsx")
            try:
                try:
                    staged = await stage_catalog_diff(
                        session, workbook_path, from_version or "", force=force
                    )
                except VersionGuardError as exc:
                    # Refused stage: recorded as a blocked run, not a failure.
                    run.status = "blocked"
                    run.from_version = from_version
                    run.sanity_report = {
                        "passed": False,
                        "checks": [
                            {
                                "check": f"version_guard_{exc.code}",
                                "passed": False,
                                "detail": str(exc),
                            }
                        ],
                    }
                    await session.commit()
                    return {"status": "blocked", "reason": str(exc)}
            finally:
                os.unlink(workbook_path)

            run.from_version = from_version
            run.to_version = staged.to_version
            run.sanity_report = staged.sanity_report.model_dump(mode="json")

            if not staged.sanity_report.passed:
                run.status = "blocked"
                await session.commit()
                return {"status": "blocked", "sanity_report": run.sanity_report}

            detail_key = diff_detail_object_key(run.id)
            s3_service.put_bytes(
                detail_key,
                staged.diff_detail.model_dump_json().encode("utf-8"),
                "application/json",
                _PLATFORM_ORG_TAG,
            )
            run.diff_detail_object_key = detail_key
            run.diff_summary = staged.diff_summary.model_dump(mode="json")
            run.status = "staged"
            if staged.forced:
                # Forced-and-recorded (plan §4.2.2): surface it in the report.
                run.sanity_report["checks"].append(
                    {
                        "check": "version_guard_forced",
                        "passed": True,
                        "detail": "same-version/downgrade stage forced by operator",
                    }
                )
            await session.commit()
            return {
                "status": "staged",
                "from_version": run.from_version,
                "to_version": run.to_version,
                "diff_summary": run.diff_summary,
            }
    finally:
        await _dispose_async_engine()


async def _mark_run_failed(run_id: str, error: str) -> None:
    from database import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session:
            run = await _load_run(session, run_id)
            run.status = "failed"
            await session.commit()
    except Exception:  # pragma: no cover — best-effort status write
        logger.exception("Could not mark run %s failed after: %s", run_id, error)


@celery_app.task(name="catalog.upgrade_stage", bind=True, autoretry_for=(), max_retries=0)
def upgrade_stage(self, run_id: str, force: bool = False) -> dict:
    """Extract + sanity-gate + diff a stashed workbook against the live catalog."""
    self.update_state(state="PROGRESS", meta={"step": "staging", "run_id": run_id})
    try:
        return asyncio.run(_run_upgrade_stage(run_id, force))
    except Exception as exc:
        asyncio.run(_mark_run_failed(run_id, str(exc)))
        raise


async def _run_upgrade_apply(run_id: str) -> dict:
    from database import AsyncSessionLocal
    from schemas_catalog_upgrade import DiffDetail
    from services.catalog_apply import apply_catalog_run, purge_trust_portal_cache

    try:
        async with AsyncSessionLocal() as session:
            run = await _load_run(session, run_id)
            if run.status not in ("staged", "applying"):
                raise RuntimeError(
                    f"run {run_id} is {run.status!r}, expected 'staged'/'applying'"
                )
            if not run.diff_detail_object_key:
                raise RuntimeError(f"run {run_id} has no stored diff detail")

            run.status = "applying"
            await session.commit()

            detail = DiffDetail.model_validate(
                _download_json(run.diff_detail_object_key)
            )
            report = await apply_catalog_run(session, run, detail)

        # Post-commit cache handling (plan §4.2.7): a failed purge must not
        # fail an already-applied run — the trust portal TTL is 15 minutes.
        result = report.as_dict()
        try:
            result["trust_portal_cache_keys_purged"] = await purge_trust_portal_cache()
        except Exception as exc:
            logger.warning("Trust-portal cache purge failed after apply: %s", exc)
            result["trust_portal_cache_keys_purged"] = None
            result["trust_portal_cache_purge_error"] = str(exc)
        return result
    finally:
        await _dispose_async_engine()


@celery_app.task(name="catalog.upgrade_apply", bind=True, autoretry_for=(), max_retries=0)
def upgrade_apply(self, run_id: str) -> dict:
    """Apply a staged run in one advisory-locked transaction (plan §4.2.4)."""
    self.update_state(state="PROGRESS", meta={"step": "applying", "run_id": run_id})
    try:
        return asyncio.run(_run_upgrade_apply(run_id))
    except Exception as exc:
        asyncio.run(_mark_run_failed(run_id, str(exc)))
        raise


async def _run_upgrade_revert(run_id: str) -> dict:
    from database import AsyncSessionLocal
    from schemas_catalog_upgrade import DiffDetail
    from services.catalog_apply import purge_trust_portal_cache, revert_catalog_run

    try:
        async with AsyncSessionLocal() as session:
            run = await _load_run(session, run_id)
            if not run.diff_detail_object_key:
                raise RuntimeError(f"run {run_id} has no stored diff detail")
            detail = DiffDetail.model_validate(
                _download_json(run.diff_detail_object_key)
            )
            report = await revert_catalog_run(session, run, detail)

        result = report.as_dict()
        try:
            result["trust_portal_cache_keys_purged"] = await purge_trust_portal_cache()
        except Exception as exc:
            logger.warning("Trust-portal cache purge failed after revert: %s", exc)
            result["trust_portal_cache_keys_purged"] = None
            result["trust_portal_cache_purge_error"] = str(exc)
        return result
    finally:
        await _dispose_async_engine()


@celery_app.task(name="catalog.upgrade_revert", bind=True, autoretry_for=(), max_retries=0)
def upgrade_revert(self, run_id: str) -> dict:
    """Revert the latest applied run from its stored diff (plan §4.2.6).

    Typed refusals (RevertBlockedError / RevertNotLatestError) propagate as
    task failures WITHOUT flipping the run to 'failed' — the run stays
    'applied' because nothing changed. Other errors mark the run 'failed'.
    """
    from services.catalog_apply import RevertRefusedError

    self.update_state(state="PROGRESS", meta={"step": "reverting", "run_id": run_id})
    try:
        return asyncio.run(_run_upgrade_revert(run_id))
    except RevertRefusedError:
        raise
    except Exception as exc:
        asyncio.run(_mark_run_failed(run_id, str(exc)))
        raise


async def _run_cleanup_workbooks() -> dict:
    from database import AsyncSessionLocal
    from models import CatalogImportRun

    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(CatalogImportRun)
                .where(CatalogImportRun.workbook_object_key.isnot(None))
                .order_by(CatalogImportRun.created_at.desc())
            )
            runs = result.scalars().all()
            deleted = []
            for run in runs[CLEANUP_KEEP_RUNS:]:
                key = run.workbook_object_key
                try:
                    # s3_service has no public delete helper; use the client the
                    # way move_to_quarantine does. Deletes the WORKBOOK only —
                    # the diff detail is the revert anchor and is never removed.
                    client = s3_service._get_s3_client()
                    client.delete_object(Bucket=s3_service.EVIDENCE_BUCKET, Key=key)
                except Exception as exc:
                    logger.warning("Workbook cleanup failed for %s: %s", key, exc)
                    continue
                run.workbook_object_key = None
                deleted.append(key)
            await session.commit()
        return {"status": "complete", "retained": CLEANUP_KEEP_RUNS, "deleted": deleted}
    finally:
        await _dispose_async_engine()


@celery_app.task(name="catalog.cleanup_workbooks", autoretry_for=(), max_retries=0)
def cleanup_workbooks() -> dict:
    """Beat task: retain the last 5 runs' workbooks, delete older ones."""
    return asyncio.run(_run_cleanup_workbooks())
