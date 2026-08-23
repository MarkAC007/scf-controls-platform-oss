"""Resolve the navigable key for a page of notifications.

A notification stores `reference_type` + `reference_id`, and for evidence that
id is the `evidence_tracking` row UUID. Every screen -- and the `?item=` deep
link added in #785 -- addresses evidence by its `evidence_id` string
(`E-HRS-16`), so the UUID alone cannot navigate anywhere.

Worse, task notifications never stored the evidence reference at all.
`create_task_due_notification` and `create_task_overdue_notification` both
receive `evidence_id`, interpolate it into the message ("Evidence collection
task for E-HRS-16 is overdue by 4 day(s)") and then store only the task id. The
notification names an item in its own text and could not reach it.

**Resolved on read, never stored.** The evidence_id already exists exactly once,
on `evidence_tracking`. Copying it onto `notifications` would make a second
source of truth for one fact -- the failure this codebase has already paid for
several times -- and would need a backfill no better than the join it replaces.
Resolving on read also repairs every notification already in the table.

**Batched, never per-row.** The bell polls this endpoint. Two `IN` queries -- one
for evidence references, one for task references -- cost the same for fifty
notifications as for one.
"""
from __future__ import annotations

import logging
from typing import Dict, Iterable, List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import EvidenceCollectionTask, EvidenceTracking

logger = logging.getLogger(__name__)

#: reference_type values whose reference_id is an evidence_tracking row id.
EVIDENCE_REFERENCE_TYPES = frozenset({"evidence"})

#: reference_type values whose reference_id is an evidence_collection_task id.
TASK_REFERENCE_TYPES = frozenset({"task"})


async def resolve_reference_keys(
    db: AsyncSession,
    notifications: Iterable,
) -> Dict[UUID, Optional[str]]:
    """Map notification id -> human evidence key, for the ones that have one.

    Returns a dict keyed by notification id. A notification whose target cannot
    be resolved is **absent from the dict**, not present with a null -- callers
    use ``.get(id)`` and get ``None`` either way, and an absent key keeps the
    two states ("no evidence target" and "evidence target that no longer
    exists") from being conflated in the mapping itself.

    Never raises: a resolution failure must not take down the notification list.
    A bell that renders without deep links is a degraded bell; a bell that 500s
    is no bell.
    """
    rows = list(notifications)
    if not rows:
        return {}

    tracking_ids: List[UUID] = []
    task_ids: List[UUID] = []
    for n in rows:
        if n.reference_id is None:
            continue
        if n.reference_type in EVIDENCE_REFERENCE_TYPES:
            tracking_ids.append(n.reference_id)
        elif n.reference_type in TASK_REFERENCE_TYPES:
            task_ids.append(n.reference_id)

    if not tracking_ids and not task_ids:
        return {}

    tracking_to_key: Dict[UUID, str] = {}
    task_to_key: Dict[UUID, str] = {}

    try:
        if tracking_ids:
            result = await db.execute(
                select(EvidenceTracking.id, EvidenceTracking.evidence_id).where(
                    EvidenceTracking.id.in_(set(tracking_ids))
                )
            )
            tracking_to_key = {row.id: row.evidence_id for row in result.all()}

        if task_ids:
            # One join, not one query per task: the task carries the tracking
            # FK, and the tracking row carries the key the UI navigates by.
            result = await db.execute(
                select(
                    EvidenceCollectionTask.id,
                    EvidenceTracking.evidence_id,
                )
                .join(
                    EvidenceTracking,
                    EvidenceCollectionTask.evidence_tracking_id == EvidenceTracking.id,
                )
                .where(EvidenceCollectionTask.id.in_(set(task_ids)))
            )
            task_to_key = {row.id: row.evidence_id for row in result.all()}
    except Exception:
        logger.exception(
            "Could not resolve notification targets; serving notifications "
            "without deep links"
        )
        return {}

    resolved: Dict[UUID, Optional[str]] = {}
    for n in rows:
        if n.reference_id is None:
            continue
        if n.reference_type in EVIDENCE_REFERENCE_TYPES:
            key = tracking_to_key.get(n.reference_id)
        elif n.reference_type in TASK_REFERENCE_TYPES:
            key = task_to_key.get(n.reference_id)
        else:
            key = None
        if key:
            resolved[n.id] = key
    return resolved
