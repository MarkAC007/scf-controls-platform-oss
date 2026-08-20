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
from typing import Any, Dict

from celery import shared_task

from database import AsyncSessionLocal, engine
from services.notifications import (
    check_and_notify_due_tasks,
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
