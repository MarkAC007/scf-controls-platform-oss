"""Celery tasks driving per-org catalog reconciliation apply/rollback (WP2c,
plan §4.3, §4.5).

A separate module on purpose: WP1b owns ``tasks_catalog.py`` (platform stage/
apply/revert); these tasks operate on ``organization_reconciliation_runs`` and
share only the ``catalog`` queue with it (routing in ``celery_app.py``).

Flow per task: the API endpoint pre-flights the guards synchronously and
enqueues; the task flips the run to its in-flight status, then calls the
service, which re-checks every guard under the dual advisory locks and runs
the whole mutation in one transaction. Typed guard refusals restore the run's
resting status (nothing was mutated); anything else marks the run 'failed'.

Post-commit side effects (plan §4.3): org-admin notifications with
``reference_type='catalog'`` and, after an apply, the existing composite
backfill task for the org.

Async database work runs inside ``asyncio.run()`` per task call, with the
async engine disposed inside the still-running loop (tasks_catalog.py /
tasks_automation.py pattern).
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional
from uuid import UUID

from celery_app import celery_app

logger = logging.getLogger(__name__)

# Existing org-wide composite backfill (services/composite_service.py); routed
# to its own queue by celery_app.task_routes.
COMPOSITE_BACKFILL_TASK = "services.composite_service.backfill_all_composites_task"


async def _dispose_async_engine() -> None:
    # asyncio.run() closes its loop after each task call; dispose pooled
    # asyncpg connections inside the still-running loop.
    from database import engine

    await engine.dispose()


async def _set_org_run_status(run_id: str, status: str, only_if: str) -> None:
    """Best-effort status write in a fresh session (after a failure/refusal).

    ``only_if`` guards against clobbering a run this task never actually
    flipped in-flight (e.g. a bogus enqueue against an already-applied run).
    """
    from sqlalchemy import select

    from database import AsyncSessionLocal
    from models import OrganizationReconciliationRun

    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(OrganizationReconciliationRun).where(
                    OrganizationReconciliationRun.id == UUID(str(run_id))
                )
            )
            run = result.scalars().first()
            if run is not None and run.status == only_if:
                run.status = status
                run.updated_at = datetime.utcnow()
                await session.commit()
    except Exception:  # pragma: no cover — best-effort status write
        logger.exception("Could not set reconciliation run %s to %s", run_id, status)
    finally:
        await _dispose_async_engine()


async def _run_reconcile_apply(
    run_id: str, organization_id: str, user_id: Optional[str]
) -> dict:
    from database import AsyncSessionLocal
    from services import reconciliation_service as recon
    from services.notifications import create_catalog_reconciliation_notifications

    org_uuid = UUID(organization_id)
    run_uuid = UUID(run_id)
    user_uuid = UUID(user_id) if user_id else None

    try:
        async with AsyncSessionLocal() as session:
            run = await recon.get_org_run(session, org_uuid, run_uuid)
            if run.status not in ("previewed", "applying"):
                raise recon.RunStateError(
                    f"run {run_id} is {run.status!r}, expected 'previewed'"
                )
            run.status = "applying"
            run.updated_at = datetime.utcnow()
            await session.commit()

            report = await recon.apply_reconciliation_run(
                session, org_uuid, run_uuid, user_id=user_uuid
            )

            await create_catalog_reconciliation_notifications(
                session,
                org_uuid,
                run_uuid,
                event="applied",
                from_version=report.from_version,
                to_version=report.to_version,
                actor_user_id=user_uuid,
            )

        # After commit: recompute the org's composites against the new scope
        # (plan §4.3 — reuse the existing backfill task).
        celery_app.send_task(
            COMPOSITE_BACKFILL_TASK, kwargs={"organization_id": organization_id}
        )
        return report.as_dict()
    finally:
        await _dispose_async_engine()


@celery_app.task(name="org.reconcile_apply", bind=True, autoretry_for=(), max_retries=0)
def org_reconcile_apply(
    self, run_id: str, organization_id: str, user_id: Optional[str] = None
) -> dict:
    """Apply an org's previewed reconciliation run (plan §4.3)."""
    from services.reconciliation_service import ReconciliationError

    self.update_state(
        state="PROGRESS",
        meta={"step": "applying", "run_id": run_id, "organization_id": organization_id},
    )
    try:
        return asyncio.run(_run_reconcile_apply(run_id, organization_id, user_id))
    except ReconciliationError:
        # Guard refusal under the locks: nothing was mutated — put the run
        # back where the admin can re-preview or retry it.
        asyncio.run(_set_org_run_status(run_id, "previewed", only_if="applying"))
        raise
    except Exception as exc:
        asyncio.run(_set_org_run_status(run_id, "failed", only_if="applying"))
        logger.error("org.reconcile_apply failed for run %s: %s", run_id, exc)
        raise


async def _run_reconcile_rollback(
    run_id: str, organization_id: str, user_id: Optional[str]
) -> dict:
    from database import AsyncSessionLocal
    from services import reconciliation_service as recon
    from services.notifications import create_catalog_reconciliation_notifications

    org_uuid = UUID(organization_id)
    run_uuid = UUID(run_id)
    user_uuid = UUID(user_id) if user_id else None

    try:
        async with AsyncSessionLocal() as session:
            run = await recon.get_org_run(session, org_uuid, run_uuid)
            if run.status not in ("applied", "rolling_back"):
                raise recon.RunStateError(
                    f"run {run_id} is {run.status!r}, expected 'applied'"
                )
            run.status = "rolling_back"
            run.updated_at = datetime.utcnow()
            await session.commit()

            report = await recon.rollback_reconciliation_run(
                session, org_uuid, run_uuid, user_id=user_uuid
            )

            await create_catalog_reconciliation_notifications(
                session,
                org_uuid,
                run_uuid,
                event="rolled_back",
                from_version=report.from_version,
                to_version=report.to_version,
                actor_user_id=user_uuid,
            )
        return report.as_dict()
    finally:
        await _dispose_async_engine()


@celery_app.task(name="org.reconcile_rollback", bind=True, autoretry_for=(), max_retries=0)
def org_reconcile_rollback(
    self, run_id: str, organization_id: str, user_id: Optional[str] = None
) -> dict:
    """Snapshot-restore rollback of an org's latest applied run (plan §4.3)."""
    from services.reconciliation_service import ReconciliationError

    self.update_state(
        state="PROGRESS",
        meta={
            "step": "rolling_back",
            "run_id": run_id,
            "organization_id": organization_id,
        },
    )
    try:
        return asyncio.run(_run_reconcile_rollback(run_id, organization_id, user_id))
    except ReconciliationError:
        asyncio.run(_set_org_run_status(run_id, "applied", only_if="rolling_back"))
        raise
    except Exception as exc:
        asyncio.run(_set_org_run_status(run_id, "failed", only_if="rolling_back"))
        logger.error("org.reconcile_rollback failed for run %s: %s", run_id, exc)
        raise
