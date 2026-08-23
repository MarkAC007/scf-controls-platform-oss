"""
Task Generator Service - Auto-generate evidence collection tasks based on frequency.

Two callers, one rule. `generate_task_for_tracking` decides what a single tracking
row is owed; `generate_evidence_tasks` is the nightly sweep that asks it that
question for every row. The write paths in `api/evidence_tracking.py` ask the same
question the moment a row becomes eligible, so an org does not wait for 01:00 UTC to
see its first task (#789).

The split exists because the alternative — a second copy of the eligibility rule on
the write path — is the exact failure shape this epic keeps finding: one concept
declared per subsystem, disagreeing silently. There is one declaration.
"""
from dataclasses import dataclass
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from datetime import date, timedelta
from typing import Optional
from uuid import UUID
import logging

from models import EvidenceTracking, EvidenceCollectionTask, User
from database import AsyncSessionLocal
from services.frequency_vocabulary import (
    TASK_INTERVAL_DAYS,
    is_time_based,
    normalize as normalize_frequency,
    task_interval_days,
)

logger = logging.getLogger(__name__)


# Frequency handling is delegated to services.frequency_vocabulary — the single
# source of truth shared with the freshness engine and the UI dropdown (#783).
# Before that module existed this file held its own map, which disagreed with
# the freshness map on 'annually' and had no key at all for 'real_time', so
# every real-time-collected record was silently skipped with a WARNING that
# nothing surfaced.
#
# Kept as module-level names because existing tests and callers import them.
# NOTE: keyed by CANONICAL values only. The old map also held the spellings
# 'annually', 'yearly', 'bi-weekly', 'semi-annual' and 'semi-annually';
# those now resolve through frequency_vocabulary.normalize() instead, so look
# a value up as `task_interval_days(raw)`, never `FREQUENCY_DAYS[raw]`.
FREQUENCY_DAYS = {
    freq: days for freq, days in TASK_INTERVAL_DAYS.items() if days is not None
}

# Recognised cadences that deliberately do NOT produce scheduled tasks
# (real-time collectors push continuously; on-demand has no cadence).
# Distinct from an unrecognised value, which is still a warning.
NON_TASK_FREQUENCIES = frozenset(
    freq for freq, days in TASK_INTERVAL_DAYS.items() if days is None
)

# `SKIP_FREQUENCIES` (previously ['as required', 'as needed', 'continuous',
# 'ongoing', 'ad hoc', 'on demand']) is deliberately GONE rather than redefined.
# Every spelling it held is now an alias resolving to `on_demand` or `real_time`,
# so a redefined list would share the old name while matching none of the old
# values — a consumer doing `if raw in SKIP_FREQUENCIES` would silently stop
# skipping and start generating tasks. An ImportError is the honest failure.


# ---------------------------------------------------------------------------
# One row's worth of the decision
# ---------------------------------------------------------------------------

#: Every way `generate_task_for_tracking` can decline, as a stable vocabulary.
#: Callers log the reason rather than inferring one from `created is False`;
#: "not tracked" and "frequency we do not recognise" are different problems and
#: only one of them is a data defect worth warning about.
SKIP_NOT_TRACKED = "not_tracked"
SKIP_NO_FREQUENCY = "no_frequency"
SKIP_UNRECOGNISED_FREQUENCY = "unrecognised_frequency"
SKIP_NON_SCHEDULING = "non_scheduling"
SKIP_DUPLICATE = "duplicate"
CREATED = "created"


@dataclass
class TaskGenerationOutcome:
    """What one tracking row was owed, and what happened."""

    created: bool
    reason: str
    due_date: Optional[date] = None
    assigned_user_id: Optional[UUID] = None


def _first_due_date(days_interval: int, last_collection: Optional[date]) -> date:
    """When the next collection is owed.

    With a previous collection this is simply one interval on. Without one, long
    cadences are not pushed a full interval out — an annual item would otherwise
    produce a task due in 370 days, which is indistinguishable from no task at
    all for the person trying to start collecting today.
    """
    if last_collection:
        return last_collection + timedelta(days=days_interval)
    if days_interval >= 30:
        return date.today() + timedelta(days=30)
    return date.today() + timedelta(days=days_interval)


async def generate_task_for_tracking(
    db: AsyncSession,
    evidence: EvidenceTracking,
) -> TaskGenerationOutcome:
    """Create the collection task this tracking row is currently owed, if any.

    Adds to ``db`` and **never commits**. The caller's transaction decides
    whether the task lands, which is what lets a request handler call this
    inside its own unit of work: if the tracking write rolls back, so does the
    task it would have implied.

    ``evidence.id`` must already exist, so a freshly created row needs a
    ``db.flush()`` first. Without an id the duplicate check below cannot match
    anything and every call would create another task.

    Idempotent by the same ±3-day window the nightly sweep has always used, and
    that matters more here than it did there: the web client re-saves the whole
    tracking object on every debounced field edit, so this runs on keystrokes.
    """
    if not evidence.is_tracked:
        return TaskGenerationOutcome(False, SKIP_NOT_TRACKED)

    if not evidence.frequency:
        return TaskGenerationOutcome(False, SKIP_NO_FREQUENCY)

    frequency = normalize_frequency(evidence.frequency)

    # An unrecognised value is a data problem worth warning about.
    if frequency is None:
        logger.warning(
            f"Unrecognised frequency '{evidence.frequency}' for evidence "
            f"{evidence.evidence_id}. Expected one of: "
            f"{', '.join(sorted(FREQUENCY_DAYS))} "
            f"(or {', '.join(sorted(NON_TASK_FREQUENCIES))}, "
            f"which schedule nothing by design)"
        )
        return TaskGenerationOutcome(False, SKIP_UNRECOGNISED_FREQUENCY)

    # A recognised cadence that deliberately schedules nothing
    # (real_time, on_demand) is not an error — do not warn.
    if not is_time_based(frequency):
        logger.debug(
            f"Frequency '{frequency}' is non-scheduling for evidence "
            f"{evidence.evidence_id} — no task generated"
        )
        return TaskGenerationOutcome(False, SKIP_NON_SCHEDULING)

    days_interval = task_interval_days(frequency)
    next_due = _first_due_date(days_interval, evidence.last_collection_date)

    # Check if task already exists for this due date (or within 3 days)
    result = await db.execute(
        select(EvidenceCollectionTask).where(
            and_(
                EvidenceCollectionTask.evidence_tracking_id == evidence.id,
                EvidenceCollectionTask.due_date >= next_due - timedelta(days=3),
                EvidenceCollectionTask.due_date <= next_due + timedelta(days=3),
                EvidenceCollectionTask.status != 'completed'
            )
        )
    )
    if result.scalar_one_or_none() is not None:
        logger.debug(
            f"Task already exists for evidence {evidence.evidence_id} due {next_due}"
        )
        return TaskGenerationOutcome(False, SKIP_DUPLICATE, due_date=next_due)

    # Determine assigned user (prefer assigned_user, fallback to owner)
    assigned_user_id = evidence.assigned_user_id or evidence.owner_user_id

    task = EvidenceCollectionTask(
        evidence_tracking_id=evidence.id,
        due_date=next_due,
        status='not_started',
        assigned_user_id=assigned_user_id,
        auto_generated=True,
        task_type='collection',
        title=f'Collect Evidence: {evidence.evidence_id}',
        description=f'Scheduled {frequency} collection of evidence {evidence.evidence_id}.',
        priority='medium'
    )
    db.add(task)

    # Update evidence next_collection_date
    evidence.next_collection_date = next_due

    if assigned_user_id is None:
        # An unassigned task is created but is inert: the due-date
        # notifier skips it (notifications.py) and it can never
        # appear in anyone's ?assigned_to_me work queue. Before #781
        # this was every task, silently. Say so at INFO so an empty
        # queue is diagnosable from the logs rather than inferred.
        logger.info(
            f"Created UNASSIGNED task for evidence {evidence.evidence_id} "
            f"due {next_due} - no assigned_user_id or owner_user_id on the "
            f"tracking record, so no notification will be sent and it will "
            f"not appear in any user's work queue"
        )
    else:
        logger.info(
            f"Created task for evidence {evidence.evidence_id} due {next_due} "
            f"assigned to {assigned_user_id}"
        )

    return TaskGenerationOutcome(
        True, CREATED, due_date=next_due, assigned_user_id=assigned_user_id
    )


# ---------------------------------------------------------------------------
# The nightly sweep
# ---------------------------------------------------------------------------


async def generate_evidence_tasks():
    """
    Generate evidence collection tasks for all tracked evidence based on frequency.

    Runs at 01:00 UTC (see `celery_app.beat_schedule`). It is the safety net, not
    the primary path: the tracking write paths generate a row's first task as soon
    as it becomes eligible, so this sweep exists to catch rows whose eligibility
    changed without a write — a `last_collection_date` moving on, a task being
    completed, an import that bypassed the API.

    The per-row decision is `generate_task_for_tracking`; this function only
    supplies the rows and owns the transaction.
    """
    logger.info("Starting evidence task generation...")

    async with AsyncSessionLocal() as db:
        # Get all evidence tracking records with frequency set
        result = await db.execute(
            select(EvidenceTracking).where(
                and_(
                    EvidenceTracking.is_tracked == True,
                    EvidenceTracking.frequency.isnot(None),
                    EvidenceTracking.frequency != ''
                )
            )
        )
        evidence_records = result.scalars().all()

        logger.info(f"Found {len(evidence_records)} evidence records with frequency")

        tasks_created = 0
        tasks_skipped = 0

        for evidence in evidence_records:
            try:
                outcome = await generate_task_for_tracking(db, evidence)
                if outcome.created:
                    tasks_created += 1
                else:
                    tasks_skipped += 1
            except Exception as e:
                logger.error(f"Error generating task for evidence {evidence.evidence_id}: {e}")
                continue

        # Commit all changes
        try:
            await db.commit()
            logger.info(f"Task generation complete: {tasks_created} created, {tasks_skipped} skipped")
        except Exception as e:
            logger.error(f"Failed to commit tasks: {e}")
            await db.rollback()

    return {
        "tasks_created": tasks_created,
        "tasks_skipped": tasks_skipped
    }


if __name__ == "__main__":
    """
    Run this script as a cron job:
    0 0 * * * cd /app && python -m services.task_generator
    """
    import asyncio
    logging.basicConfig(level=logging.INFO)

    result = asyncio.run(generate_evidence_tasks())
    print(f"Task generation complete: {result}")
