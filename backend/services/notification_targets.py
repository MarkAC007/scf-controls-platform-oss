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

Control notifications have the same gap: they store the `scoped_controls` row
id, and the library opens a control by its SCF id (`AST-14`).

**Batched, never per-row.** The bell polls this endpoint. One `IN` query per
reference kind -- evidence, task, control -- costs the same for fifty
notifications as for one.
"""
from __future__ import annotations

import logging
from typing import Dict, Iterable, List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import EvidenceCollectionTask, EvidenceTracking, ScopedControl

logger = logging.getLogger(__name__)

#: reference_type values whose reference_id is an evidence_tracking row id.
EVIDENCE_REFERENCE_TYPES = frozenset({"evidence"})

#: reference_type values whose reference_id is an evidence_collection_task id.
TASK_REFERENCE_TYPES = frozenset({"task"})

#: reference_type values whose reference_id is a scoped_controls row id.
CONTROL_REFERENCE_TYPES = frozenset({"control"})


def _log_unresolved(kind: str) -> None:
    logger.exception(
        "Could not resolve %s notification targets; serving them without "
        "deep links",
        kind,
    )


async def resolve_reference_keys(
    db: AsyncSession,
    notifications: Iterable,
) -> Dict[UUID, Optional[str]]:
    """Map notification id -> the key its screen navigates by, for the ones that
    have one: an evidence id (`E-HRS-16`) or a control's SCF id (`AST-14`).

    Returns a dict keyed by notification id. A notification whose target cannot
    be resolved is **absent from the dict**, not present with a null -- callers
    use ``.get(id)`` and get ``None`` either way, and an absent key keeps the
    two states ("no evidence target" and "evidence target that no longer
    exists") from being conflated in the mapping itself.

    Never raises: a resolution failure must not take down the notification list,
    and a failing kind leaves the other kinds' keys in place.
    A bell that renders without deep links is a degraded bell; a bell that 500s
    is no bell.
    """
    rows = list(notifications)
    if not rows:
        return {}

    tracking_ids: List[UUID] = []
    task_ids: List[UUID] = []
    control_ids: List[UUID] = []
    for n in rows:
        if n.reference_id is None:
            continue
        if n.reference_type in EVIDENCE_REFERENCE_TYPES:
            tracking_ids.append(n.reference_id)
        elif n.reference_type in TASK_REFERENCE_TYPES:
            task_ids.append(n.reference_id)
        elif n.reference_type in CONTROL_REFERENCE_TYPES:
            control_ids.append(n.reference_id)

    if not tracking_ids and not task_ids and not control_ids:
        return {}

    tracking_to_key: Dict[UUID, str] = {}
    task_to_key: Dict[UUID, str] = {}
    control_to_key: Dict[UUID, str] = {}

    # Each kind fails alone: a failing control lookup must not cost the
    # evidence keys too, or the bell calls a live evidence item gone.
    if tracking_ids:
        try:
            result = await db.execute(
                select(EvidenceTracking.id, EvidenceTracking.evidence_id).where(
                    EvidenceTracking.id.in_(set(tracking_ids))
                )
            )
            tracking_to_key = {row.id: row.evidence_id for row in result.all()}
        except Exception:
            _log_unresolved("evidence")

    if task_ids:
        try:
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
            _log_unresolved("task")

    if control_ids:
        try:
            result = await db.execute(
                select(ScopedControl.id, ScopedControl.scf_id).where(
                    ScopedControl.id.in_(set(control_ids))
                )
            )
            control_to_key = {row.id: row.scf_id for row in result.all()}
        except Exception:
            _log_unresolved("control")

    resolved: Dict[UUID, Optional[str]] = {}
    for n in rows:
        if n.reference_id is None:
            continue
        if n.reference_type in EVIDENCE_REFERENCE_TYPES:
            key = tracking_to_key.get(n.reference_id)
        elif n.reference_type in TASK_REFERENCE_TYPES:
            key = task_to_key.get(n.reference_id)
        elif n.reference_type in CONTROL_REFERENCE_TYPES:
            key = control_to_key.get(n.reference_id)
        else:
            key = None
        if key:
            resolved[n.id] = key
    return resolved
