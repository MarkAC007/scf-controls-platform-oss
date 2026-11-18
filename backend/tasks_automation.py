# SPDX-License-Identifier: AGPL-3.0-only
"""Celery tasks for daily GRC workflow automation.

Design invariants:
  - These wrappers only wire already-implemented async services into Celery.
  - TASK_AUTOMATION_ENABLED is enforced at runtime as defence-in-depth; a
    manually enqueued task must no-op when automation is disabled.
  - Async database work runs inside asyncio.run(), and the async SQLAlchemy
    engine is disposed before that temporary loop closes.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import date
from typing import Any, Dict, List
from uuid import UUID

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import AsyncSessionLocal, engine
from models import Organization
from services.due_care import take_monthly_snapshots
from services.notifications import (
    check_and_notify_due_control_validations,
    check_and_notify_due_tasks,
    check_and_notify_expired_risk_acceptances,
    check_and_notify_overdue_tasks,
)
from services.task_generator import generate_evidence_tasks

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TASK_PREFIX = "tasks_automation"
AUTOMATION_FLAG = "TASK_AUTOMATION_ENABLED"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _automation_enabled() -> bool:
    """Automation is ON unless TASK_AUTOMATION_ENABLED is explicitly false/0."""
    return os.getenv(AUTOMATION_FLAG, "true").strip().lower() not in ("false", "0")


async def _dispose_async_engine() -> None:
    # asyncio.run() closes its loop after each task call. Disposing the engine
    # inside that still-running loop closes pooled asyncpg connections before
    # they become bound to a dead loop. This is safe because Celery is prefork
    # with worker_prefetch_multiplier=1, so one task runs per process at a time.
    await engine.dispose()


async def _run_generate_evidence_tasks() -> Dict[str, Any]:
    try:
        result = await generate_evidence_tasks()
        return {
            "status": "completed",
            "tasks_created": int(result["tasks_created"]),
            "tasks_skipped": int(result["tasks_skipped"]),
        }
    finally:
        await _dispose_async_engine()


async def _run_due_notifications() -> Dict[str, Any]:
    try:
        async with AsyncSessionLocal() as db:
            notifications_created = await check_and_notify_due_tasks(db)
        return {
            "status": "completed",
            "notifications_created": int(notifications_created),
        }
    finally:
        await _dispose_async_engine()


async def _run_overdue_notifications() -> Dict[str, Any]:
    try:
        async with AsyncSessionLocal() as db:
            notifications_created = await check_and_notify_overdue_tasks(db)
        return {
            "status": "completed",
            "notifications_created": int(notifications_created),
        }
    finally:
        await _dispose_async_engine()


async def _run_risk_acceptance_expiry() -> Dict[str, Any]:
    try:
        async with AsyncSessionLocal() as db:
            notifications_created = await check_and_notify_expired_risk_acceptances(db)
        return {
            "status": "completed",
            "notifications_created": int(notifications_created),
        }
    finally:
        await _dispose_async_engine()


async def _run_auditor_grant_expiry() -> Dict[str, Any]:
    from services.org_auditors import revoke_expired_grants

    try:
        async with AsyncSessionLocal() as db:
            revoked = await revoke_expired_grants(db)
        return {"status": "completed", "grants_revoked": int(revoked)}
    finally:
        await _dispose_async_engine()


async def _run_control_validation_notices() -> Dict[str, Any]:
    try:
        async with AsyncSessionLocal() as db:
            notifications_created = await check_and_notify_due_control_validations(db)
        return {
            "status": "completed",
            "notifications_created": int(notifications_created),
        }
    finally:
        await _dispose_async_engine()


async def _run_posture_snapshots() -> Dict[str, Any]:
    try:
        async with AsyncSessionLocal() as db:
            taken = await take_monthly_snapshots(db)
        return {"status": "completed", "snapshots_taken": int(taken)}
    finally:
        await _dispose_async_engine()


async def _organization_ids(db: AsyncSession) -> List[UUID]:
    return list((await db.execute(select(Organization.id).order_by(Organization.id))).scalars().all())


async def record_isms_metric_results(db: AsyncSession, today: date) -> Dict[str, int]:
    """Write today's computed ISMS metric results for every organisation.

    Each organisation is committed on its own: one that fails is rolled back
    and logged, and the rest still run. A second run on the same day writes
    nothing (the service skips a metric already recorded today).
    """
    from services.isms_metrics import service as isms_metrics

    written = organisations = failed = 0
    for org_id in await _organization_ids(db):
        try:
            count = await isms_metrics.record_nightly_results(db, org_id, today)
            await db.commit()
            written += int(count)
            organisations += 1
        except SoftTimeLimitExceeded:  # the worker is stopping the task: not one organisation's failure
            await db.rollback()
            raise
        except Exception as exc:  # one organisation must not stop the rest
            logger.error("isms metric results failed for org %s: %s", org_id, exc, exc_info=True)
            await db.rollback()
            failed += 1
    logger.info(
        "isms metric results for %s: %s row(s) written across %s organisation(s), %s failed",
        today, written, organisations, failed,
    )
    return {"results_written": written, "organisations": organisations, "organisations_failed": failed}


async def _run_isms_metric_results() -> Dict[str, Any]:
    try:
        async with AsyncSessionLocal() as db:
            counts = await record_isms_metric_results(db, date.today())
        return {"status": "completed", **counts}
    finally:
        await _dispose_async_engine()


def _disabled_result(task_name: str) -> Dict[str, Any]:
    return {
        "status": "disabled",
        "task": task_name,
        "disabled_by": AUTOMATION_FLAG,
    }


# ---------------------------------------------------------------------------
# Task generation
# ---------------------------------------------------------------------------
@shared_task(
    bind=True,
    name=f"{TASK_PREFIX}.generate_evidence_tasks_task",
    time_limit=1800,
    soft_time_limit=1500,
)
def generate_evidence_tasks_task(self) -> Dict[str, Any]:
    """Generate evidence collection tasks for tracked evidence."""
    task_id = self.request.id
    if not _automation_enabled():
        logger.info(
            "generate_evidence_tasks_task[%s] skipped: %s disabled",
            task_id,
            AUTOMATION_FLAG,
        )
        return _disabled_result("generate_evidence_tasks_task")

    logger.info("generate_evidence_tasks_task[%s] starting", task_id)
    try:
        result = asyncio.run(_run_generate_evidence_tasks())
        logger.info(
            "generate_evidence_tasks_task[%s] completed created=%s skipped=%s",
            task_id,
            result["tasks_created"],
            result["tasks_skipped"],
        )
        return result
    except Exception as exc:
        logger.error("generate_evidence_tasks_task[%s] failed: %s", task_id, exc, exc_info=True)
        raise


# ---------------------------------------------------------------------------
# Due task notifications
# ---------------------------------------------------------------------------
@shared_task(
    bind=True,
    name=f"{TASK_PREFIX}.notify_due_tasks_task",
    time_limit=1800,
    soft_time_limit=1500,
)
def notify_due_tasks_task(self) -> Dict[str, Any]:
    """Create notifications for evidence collection tasks due soon."""
    task_id = self.request.id
    if not _automation_enabled():
        logger.info(
            "notify_due_tasks_task[%s] skipped: %s disabled",
            task_id,
            AUTOMATION_FLAG,
        )
        return _disabled_result("notify_due_tasks_task")

    logger.info("notify_due_tasks_task[%s] starting", task_id)
    try:
        result = asyncio.run(_run_due_notifications())
        logger.info(
            "notify_due_tasks_task[%s] completed notifications=%s",
            task_id,
            result["notifications_created"],
        )
        return result
    except Exception as exc:
        logger.error("notify_due_tasks_task[%s] failed: %s", task_id, exc, exc_info=True)
        raise


# ---------------------------------------------------------------------------
# Overdue task notifications
# ---------------------------------------------------------------------------
@shared_task(
    bind=True,
    name=f"{TASK_PREFIX}.notify_overdue_tasks_task",
    time_limit=1800,
    soft_time_limit=1500,
)
def notify_overdue_tasks_task(self) -> Dict[str, Any]:
    """Create notifications for overdue evidence collection tasks."""
    task_id = self.request.id
    if not _automation_enabled():
        logger.info(
            "notify_overdue_tasks_task[%s] skipped: %s disabled",
            task_id,
            AUTOMATION_FLAG,
        )
        return _disabled_result("notify_overdue_tasks_task")

    logger.info("notify_overdue_tasks_task[%s] starting", task_id)
    try:
        result = asyncio.run(_run_overdue_notifications())
        logger.info(
            "notify_overdue_tasks_task[%s] completed notifications=%s",
            task_id,
            result["notifications_created"],
        )
        return result
    except Exception as exc:
        logger.error("notify_overdue_tasks_task[%s] failed: %s", task_id, exc, exc_info=True)
        raise


# ---------------------------------------------------------------------------
# Risk acceptance expiry (PIG R7)
# ---------------------------------------------------------------------------
@shared_task(
    bind=True,
    name=f"{TASK_PREFIX}.notify_expired_risk_acceptances_task",
    time_limit=1800,
    soft_time_limit=1500,
)
def notify_expired_risk_acceptances_task(self) -> Dict[str, Any]:
    """Announce risk acceptances whose expiry has passed."""
    task_id = self.request.id
    if not _automation_enabled():
        logger.info(
            "notify_expired_risk_acceptances_task[%s] skipped: %s disabled",
            task_id,
            AUTOMATION_FLAG,
        )
        return _disabled_result("notify_expired_risk_acceptances_task")

    logger.info("notify_expired_risk_acceptances_task[%s] starting", task_id)
    try:
        result = asyncio.run(_run_risk_acceptance_expiry())
        logger.info(
            "notify_expired_risk_acceptances_task[%s] completed notifications=%s",
            task_id,
            result["notifications_created"],
        )
        return result
    except Exception as exc:
        logger.error(
            "notify_expired_risk_acceptances_task[%s] failed: %s", task_id, exc, exc_info=True
        )
        raise


# ---------------------------------------------------------------------------
# External auditor access ends with the engagement
# ---------------------------------------------------------------------------
@shared_task(
    bind=True,
    name=f"{TASK_PREFIX}.revoke_expired_auditor_grants_task",
    time_limit=600,
    soft_time_limit=540,
)
def revoke_expired_auditor_grants_task(self) -> Dict[str, Any]:
    """Revoke external auditor grants the day after an engagement's end date.

    Not behind TASK_AUTOMATION_ENABLED: this is access control, not a
    notification, and switching notifications off must not leave an outside
    auditor's access open past the engagement.
    """
    task_id = self.request.id
    logger.info("revoke_expired_auditor_grants_task[%s] starting", task_id)
    result = asyncio.run(_run_auditor_grant_expiry())
    logger.info("revoke_expired_auditor_grants_task[%s] revoked=%s", task_id, result["grants_revoked"])
    return result


# ---------------------------------------------------------------------------
# Control validation due (PIG R10)
# ---------------------------------------------------------------------------
@shared_task(
    bind=True,
    name=f"{TASK_PREFIX}.notify_due_control_validations_task",
    time_limit=1800,
    soft_time_limit=1500,
)
def notify_due_control_validations_task(self) -> Dict[str, Any]:
    """Announce controls whose next validation is due soon or has passed."""
    task_id = self.request.id
    if not _automation_enabled():
        logger.info(
            "notify_due_control_validations_task[%s] skipped: %s disabled",
            task_id,
            AUTOMATION_FLAG,
        )
        return _disabled_result("notify_due_control_validations_task")

    logger.info("notify_due_control_validations_task[%s] starting", task_id)
    try:
        result = asyncio.run(_run_control_validation_notices())
        logger.info(
            "notify_due_control_validations_task[%s] completed notifications=%s",
            task_id,
            result["notifications_created"],
        )
        return result
    except Exception as exc:
        logger.error(
            "notify_due_control_validations_task[%s] failed: %s", task_id, exc, exc_info=True
        )
        raise


# ---------------------------------------------------------------------------
# Monthly posture snapshot (PIG R10)
# ---------------------------------------------------------------------------
@shared_task(
    bind=True,
    name=f"{TASK_PREFIX}.take_posture_snapshots_task",
    time_limit=1800,
    soft_time_limit=1500,
)
def take_posture_snapshots_task(self) -> Dict[str, Any]:
    """Take this month's posture snapshot for every organisation without one."""
    task_id = self.request.id
    if not _automation_enabled():
        logger.info(
            "take_posture_snapshots_task[%s] skipped: %s disabled",
            task_id,
            AUTOMATION_FLAG,
        )
        return _disabled_result("take_posture_snapshots_task")

    logger.info("take_posture_snapshots_task[%s] starting", task_id)
    try:
        result = asyncio.run(_run_posture_snapshots())
        logger.info(
            "take_posture_snapshots_task[%s] completed snapshots=%s",
            task_id,
            result["snapshots_taken"],
        )
        return result
    except Exception as exc:
        logger.error("take_posture_snapshots_task[%s] failed: %s", task_id, exc, exc_info=True)
        raise


# ---------------------------------------------------------------------------
# Nightly ISMS metric results (clause 9.1)
# ---------------------------------------------------------------------------
@shared_task(
    bind=True,
    name=f"{TASK_PREFIX}.record_isms_metric_results_task",
    time_limit=1800,
    soft_time_limit=1500,
)
def record_isms_metric_results_task(self) -> Dict[str, Any]:
    """Record today's value of every live ISMS metric, for every organisation."""
    task_id = self.request.id
    if not _automation_enabled():
        logger.info(
            "record_isms_metric_results_task[%s] skipped: %s disabled",
            task_id,
            AUTOMATION_FLAG,
        )
        return _disabled_result("record_isms_metric_results_task")

    logger.info("record_isms_metric_results_task[%s] starting", task_id)
    try:
        result = asyncio.run(_run_isms_metric_results())
        logger.info(
            "record_isms_metric_results_task[%s] completed results=%s organisations=%s failed=%s",
            task_id,
            result["results_written"],
            result["organisations"],
            result["organisations_failed"],
        )
        return result
    except Exception as exc:
        logger.error("record_isms_metric_results_task[%s] failed: %s", task_id, exc, exc_info=True)
        raise


# ---------------------------------------------------------------------------
# Security objective reminders (clauses 6.2, 9.1)
# ---------------------------------------------------------------------------
async def _run_objective_reminders() -> Dict[str, Any]:
    from services.isms_metrics.reminders import check_and_notify_objectives

    try:
        async with AsyncSessionLocal() as db:
            notifications_created = await check_and_notify_objectives(db)
        return {
            "status": "completed",
            "notifications_created": int(notifications_created),
        }
    finally:
        await _dispose_async_engine()


@shared_task(
    bind=True,
    name=f"{TASK_PREFIX}.notify_objectives_task",
    time_limit=1800,
    soft_time_limit=1500,
)
def notify_objectives_task(self) -> Dict[str, Any]:
    """Remind owners of objectives whose measurement, due date or review date is due."""
    task_id = self.request.id
    if not _automation_enabled():
        logger.info(
            "notify_objectives_task[%s] skipped: %s disabled",
            task_id,
            AUTOMATION_FLAG,
        )
        return _disabled_result("notify_objectives_task")

    logger.info("notify_objectives_task[%s] starting", task_id)
    try:
        result = asyncio.run(_run_objective_reminders())
        logger.info(
            "notify_objectives_task[%s] completed notifications=%s",
            task_id,
            result["notifications_created"],
        )
        return result
    except Exception as exc:
        logger.error("notify_objectives_task[%s] failed: %s", task_id, exc, exc_info=True)
        raise
