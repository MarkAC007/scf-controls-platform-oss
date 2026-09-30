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

from models import EvidenceTracking, EvidenceCollectionTask, Organization, ScopedControl, User
from catalog_models import SCFCatalogControl
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
SKIP_AUTO_GENERATION_DISABLED = "auto_generation_disabled"
#: Tracked, scheduled, switched on — but no in-scope control lists this
#: evidence in its evidence_requests. Un-scoping a control does not touch its
#: evidence rows (by design: files and history survive), so without this gate a
#: row orphaned by a scope change keeps minting collection work forever.
SKIP_NOT_REQUIRED_BY_SCOPE = "not_required_by_scope"
CREATED = "created"


# ---------------------------------------------------------------------------
# The organisation-level switch
# ---------------------------------------------------------------------------

#: Key in ``Organization.settings``. Absent means on: every organisation that
#: existed before the switch keeps the behaviour it had.
AUTO_TASK_GENERATION_SETTING_KEY = "auto_task_generation_enabled"


def resolve_auto_task_generation(settings) -> bool:
    """Whether an organisation wants tasks minted for it automatically.

    Reads the settings JSON the way `services.jev_assessment.resolve_engine`
    does — one declaration, shared by the settings endpoint (which echoes it)
    and the generator (which obeys it). Anything that is not a dict, and any
    missing key, means enabled.
    """
    if not isinstance(settings, dict):
        return True
    value = settings.get(AUTO_TASK_GENERATION_SETTING_KEY, True)
    if value is True or value is False:
        return value
    # The settings column is free JSON written by more than one path. Only a
    # real boolean turns the switch off; "false", 0 or null read as the
    # default rather than silently starving an organisation of tasks.
    return True


async def _org_auto_generation_enabled(db: AsyncSession, organization_id) -> bool:
    result = await db.execute(
        select(Organization.settings).where(Organization.id == organization_id)
    )
    return resolve_auto_task_generation(result.scalar_one_or_none())


async def _required_by_scope(db: AsyncSession, evidence: EvidenceTracking) -> bool:
    """Whether at least one in-scope control of the row's org requests this evidence.

    Same rule as ``services.scoping_service.effective_evidence_ids`` — that one
    returns the whole set for an org (the sweep uses it once per org); this one
    asks the question for a single row on the write path with an EXISTS-shaped
    query rather than materialising the org's full requirement set per keystroke.
    """
    result = await db.execute(
        select(ScopedControl.scf_id)
        .join(SCFCatalogControl, SCFCatalogControl.scf_id == ScopedControl.scf_id)
        .where(
            and_(
                ScopedControl.organization_id == evidence.organization_id,
                ScopedControl.selected == True,  # noqa: E712
                SCFCatalogControl.evidence_requests.contains([evidence.evidence_id]),
            )
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


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
    *,
    auto_generation_enabled: Optional[bool] = None,
    required_by_scope: Optional[bool] = None,
) -> TaskGenerationOutcome:
    """Create the collection task this tracking row is currently owed, if any.

    ``auto_generation_enabled`` is the organisation's switch
    (``settings.auto_task_generation_enabled``). ``None`` — the write-path
    default — looks it up for ``evidence.organization_id``; the sweep, which
    has already excluded switched-off organisations from its SELECT, passes
    ``True`` so it does not ask once per row. Manual task creation never comes
    through here, so the switch cannot stop a person creating a task by hand.

    ``required_by_scope`` is whether some in-scope control of the org lists
    this evidence in its ``evidence_requests``. ``None`` looks it up for the
    row; the sweep computes each org's required set once and passes the
    answer per row. Evidence nobody in scope asks for is tracked-but-idle: it
    keeps its files and history, it just stops being scheduled.

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

    # The organisation's switch is consulted only once a row has proved it
    # would otherwise mint a task — after the cheap checks, before the first
    # query — so a switched-off organisation costs one lookup, not a scan.
    if auto_generation_enabled is None:
        auto_generation_enabled = await _org_auto_generation_enabled(
            db, evidence.organization_id
        )
    if not auto_generation_enabled:
        logger.debug(
            f"Automatic task generation is switched off for organisation "
            f"{evidence.organization_id}; not generating for {evidence.evidence_id}"
        )
        return TaskGenerationOutcome(False, SKIP_AUTO_GENERATION_DISABLED, due_date=next_due)

    # Scope is the last gate before the duplicate query: it is a lookup of its
    # own, so it runs only for rows that have cleared every free check.
    if required_by_scope is None:
        required_by_scope = await _required_by_scope(db, evidence)
    if not required_by_scope:
        logger.debug(
            f"No in-scope control requires evidence {evidence.evidence_id} for "
            f"organisation {evidence.organization_id}; not generating"
        )
        return TaskGenerationOutcome(False, SKIP_NOT_REQUIRED_BY_SCOPE, due_date=next_due)

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
        # Denormalised from the parent evidence item (#822 §6) — the column the
        # composite foreign keys join through to keep a task's team inside its
        # own tenant.
        organization_id=evidence.organization_id,
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


async def _required_evidence_by_org(db: AsyncSession) -> dict:
    """organization_id → set of evidence ids some in-scope control requests.

    The per-org flavour of ``scoping_service.effective_evidence_ids``, gathered
    in one query for the sweep. An org with no scoped controls has no entry,
    which reads as "nothing required" — the same answer the write path gives.
    """
    result = await db.execute(
        select(ScopedControl.organization_id, SCFCatalogControl.evidence_requests)
        .join(SCFCatalogControl, SCFCatalogControl.scf_id == ScopedControl.scf_id)
        .where(ScopedControl.selected == True)  # noqa: E712
    )
    required: dict = {}
    for org_id, requests in result.all():
        if not isinstance(requests, (list, tuple)):
            continue
        required.setdefault(org_id, set()).update(str(r) for r in requests if r)
    return required


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
    logger.debug("Starting evidence task generation...")

    async with AsyncSessionLocal() as db:
        # Organisations that have switched automatic generation off are left
        # out of the SELECT entirely: their rows are not "skipped", they were
        # never candidates, and a sweep over a large tenant that has opted
        # out should cost nothing.
        settings_result = await db.execute(select(Organization.id, Organization.settings))
        disabled_org_ids = [
            org_id for org_id, settings in settings_result.all()
            if not resolve_auto_task_generation(settings)
        ]

        conditions = [
            EvidenceTracking.is_tracked == True,
            EvidenceTracking.frequency.isnot(None),
            EvidenceTracking.frequency != '',
        ]
        if disabled_org_ids:
            conditions.append(EvidenceTracking.organization_id.notin_(disabled_org_ids))

        result = await db.execute(select(EvidenceTracking).where(and_(*conditions)))
        evidence_records = result.scalars().all()

        # One requirement set per organisation, computed once, rather than one
        # EXISTS query per row: evidence_requests is on the catalog row, scope
        # is on scoped_controls, and the sweep already has every row in hand.
        required_by_org = await _required_evidence_by_org(db)

        logger.info(
            f"Found {len(evidence_records)} evidence records with frequency "
            f"(disabled_orgs={len(disabled_org_ids)})"
        )

        tasks_created = 0
        tasks_skipped = 0

        for evidence in evidence_records:
            try:
                # Switched-off organisations were excluded above, so every row
                # here belongs to one with generation on.
                required = evidence.evidence_id in required_by_org.get(evidence.organization_id, set())
                outcome = await generate_task_for_tracking(db, evidence,
                                                           auto_generation_enabled=True,
                                                           required_by_scope=required)
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
