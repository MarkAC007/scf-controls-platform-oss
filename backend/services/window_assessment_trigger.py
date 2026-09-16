"""Debounced window assessment on ingest (#569 parity, work-order item 6).

Until now a window assessment ran only from the nightly sweep or a manual
request, so a file uploaded at 09:00 was not reflected in the record-level
verdict until 04:00 UTC the next day. Both ingest paths (browser upload
confirmation and the webhook inbox) now schedule `assess_window_task` for
the evidence item, debounced per (organisation, evidence) so a collector
that posts twenty files in a minute produces one assessment that sees all
twenty, not twenty assessments.

Mechanics: the first ingest inside an interval claims a Redis key with
`SET NX EX <debounce>` and enqueues the task with `countdown=<debounce>`;
later ingests inside the interval find the key held and do nothing. The
task runs after the interval and reads the window from the database, so
every file committed before it starts is included. The nightly sweep stays
as the safety net.

Everything here is fail-open: a broker or Redis outage never fails an
ingestion the caller already committed. With Redis unavailable the task is
enqueued anyway — `assess_window` caches on the window hash, so a duplicate
run is cheap.
"""
from __future__ import annotations

import logging
import os
from uuid import UUID

logger = logging.getLogger(__name__)

DEFAULT_DEBOUNCE_SECONDS = 120
_TRUTHY = ("1", "true", "yes", "on")
ASSESSMENT_SOURCE = "ingest"
QUEUE = "evidence_window"


def ingest_trigger_enabled() -> bool:
    return (os.getenv("WINDOW_ASSESSMENT_ON_INGEST") or "true").strip().lower() in _TRUTHY


def debounce_seconds() -> int:
    raw = (os.getenv("WINDOW_ASSESSMENT_INGEST_DEBOUNCE_SECONDS") or "").strip()
    if not raw:
        return DEFAULT_DEBOUNCE_SECONDS
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "WINDOW_ASSESSMENT_INGEST_DEBOUNCE_SECONDS=%r is not an integer; using %d",
            raw, DEFAULT_DEBOUNCE_SECONDS,
        )
        return DEFAULT_DEBOUNCE_SECONDS
    return max(0, value)


def debounce_key(organization_id: UUID | str, evidence_id: str) -> str:
    return f"scf:window-assessment:ingest-debounce:{organization_id}:{evidence_id}"


async def schedule_window_assessment_on_ingest(
    organization_id: UUID | str,
    evidence_id: str,
    *,
    trigger: str,
) -> bool:
    """Enqueue one window assessment per (org, evidence) per debounce interval.

    `trigger` is a label for the log line ("upload" or "webhook"). Returns
    True when a task was enqueued, False when debounced, disabled or the
    enqueue failed. Never raises.
    """
    if not ingest_trigger_enabled():
        return False

    delay = debounce_seconds()
    if delay > 0:
        try:
            from redis_client import get_redis_client

            redis = await get_redis_client()
            claimed = await redis.set(
                debounce_key(organization_id, evidence_id), trigger, nx=True, ex=delay,
            )
            if not claimed:
                logger.info(
                    "Window assessment already scheduled for evidence=%s org=%s; "
                    "%s ingest debounced",
                    evidence_id, organization_id, trigger,
                )
                return False
        except Exception as exc:  # noqa: BLE001 — fail-open by design
            logger.warning(
                "Window assessment debounce unavailable for evidence=%s org=%s (%s); "
                "enqueueing without debounce",
                evidence_id, organization_id, exc,
            )

    try:
        from tasks_window_assessment import assess_window_task

        assess_window_task.apply_async(
            kwargs={
                "organization_id": str(organization_id),
                "evidence_id": evidence_id,
                "assessment_source": ASSESSMENT_SOURCE,
            },
            countdown=delay,
            queue=QUEUE,
        )
    except Exception as exc:  # noqa: BLE001 — fail-open by design
        logger.warning(
            "Could not enqueue window assessment for evidence=%s org=%s after %s ingest: %s "
            "— the nightly sweep will pick it up",
            evidence_id, organization_id, trigger, exc,
        )
        return False

    logger.info(
        "Window assessment scheduled for evidence=%s org=%s in %ds after %s ingest",
        evidence_id, organization_id, delay, trigger,
    )
    return True
