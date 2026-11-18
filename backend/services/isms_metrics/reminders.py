# SPDX-License-Identifier: AGPL-3.0-only
"""Daily reminders for security objectives (clauses 6.2 and 9.1).

One sweep, :func:`check_and_notify_objectives`, run each morning by
``tasks_automation.notify_objectives_task``. It raises three notification
types, each through the house :func:`services.notifications._emit`, so
dedup, the one-transaction write and the email follow the same pattern
as every other notifier:

=============================  ==========================================  ==========================
Type                           When                                        Dedup instant (``dedup_on``)
=============================  ==========================================  ==========================
``objective_measurement_due``  a logged metric reads *first measurement    the current period start
                               due* or *measurement overdue*               (the metric's start when it
                                                                           has no period)
``objective_due``              the due date is 1 to 14 days away           the day the window opened
                                                                           (the date minus 14 days)
``objective_due``              the due date is today or has passed         the date itself
``objective_review_due``       the same two steps for the review date      the same
=============================  ==========================================  ==========================

The guard is *type + reference + created on or after the instant*. Keying
the "coming up" reminder on the window's first day and the "reached" one on
the date itself means the second is never swallowed by the first, and a due
date moved later opens a fresh window of its own.

Only **active**, unarchived objectives are swept. Proposed ones are not yet
confirmed; achieved and missed ones are closed.

Recipients come through the ownership chain
(:func:`services.owner_resolution.resolve_recipients_for`): the named owner,
then the responsible team's primary and delegate, then the organisation's
admins. A named owner who is no longer a member of the organisation is
dropped before the chain runs, so the reminder falls to the team instead of
reaching someone who has left.

One objective that fails is logged and skipped; the rest of the sweep runs.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import ObjectiveMetric, Organization, OrganizationMember, SecurityObjective
from services.isms_metrics import status as status_rules
from services.owner_resolution import Item, resolve_recipients_for

logger = logging.getLogger(__name__)

REFERENCE_TYPE = "security_objective"
MEASUREMENT_DUE = "objective_measurement_due"
OBJECTIVE_DUE = "objective_due"
REVIEW_DUE = "objective_review_due"
NOTIFICATION_TYPES = (MEASUREMENT_DUE, OBJECTIVE_DUE, REVIEW_DUE)

#: How far ahead a due or review date is announced.
NOTICE_DAYS = 14

#: Organisation setting: the measurement reminder goes out once at most this
#: many days of the period remain, today counted, and never before the period
#: starts. 14 is the last fortnight; a number at least as long as the period
#: (366 covers a year) is the first day of every period.
MEASUREMENT_REMINDER_DAYS_KEY = "isms_measurement_reminder_days"
DEFAULT_MEASUREMENT_REMINDER_DAYS = NOTICE_DAYS
MAX_MEASUREMENT_REMINDER_DAYS = 366

_PERIOD_WORD = {"monthly": "month", "quarterly": "quarter", "annually": "year"}

_OBJECTIVE_COLUMNS = (
    SecurityObjective.id, SecurityObjective.organization_id, SecurityObjective.ref,
    SecurityObjective.name, SecurityObjective.objective_type, SecurityObjective.lifecycle_status,
    SecurityObjective.owning_team_id, SecurityObjective.owner_user_id,
    SecurityObjective.due_date, SecurityObjective.review_date,
)


def _label(objective) -> str:
    return f'Objective {objective.ref} "{objective.name}"'


def measurement_message(objective, frequency: Optional[str], first: bool) -> str:
    if first:
        return f"{_label(objective)}: no value has been logged yet."
    word = _PERIOD_WORD.get(frequency or "")
    period = f"this {word}'s value" if word else "the latest value"
    return f"{_label(objective)}: {period} has not been logged."


def due_message(objective, due: date, reached: bool) -> str:
    if reached:
        return f"{_label(objective)} reached its due date on {due.isoformat()} and is still active."
    return f"{_label(objective)} is due on {due.isoformat()}."


def review_message(objective, review: date, reached: bool) -> str:
    if reached:
        return f"{_label(objective)} reached its review date on {review.isoformat()}."
    return f"{_label(objective)} is due for review on {review.isoformat()}."


def date_notice(day: Optional[date], today: date) -> Optional[tuple]:
    """``(reached, dedup_on)`` when ``day`` is inside the notice window, else None.

    Coming up: 1 to 14 days away, keyed on the day the window opened.
    Reached: today or earlier, keyed on the date itself.
    """
    if day is None:
        return None
    if day <= today:
        return True, day
    if (day - today).days <= NOTICE_DAYS:
        return False, day - timedelta(days=NOTICE_DAYS)
    return None


def _snapshot(metric: ObjectiveMetric) -> SimpleNamespace:
    """Plain values: a rollback in ``_emit`` must not expire what the sweep reads."""
    return SimpleNamespace(**{c.key: getattr(metric, c.key) for c in ObjectiveMetric.__table__.columns})


def resolve_measurement_reminder_days(settings: Any) -> int:
    """The organisation's setting, or the default when it is absent or not a whole number from 1 to 366.

    The settings JSON is written by more than one path, so a stray value reads
    as the default instead of stopping the sweep.
    """
    value = settings.get(MEASUREMENT_REMINDER_DAYS_KEY) if isinstance(settings, dict) else None
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= MAX_MEASUREMENT_REMINDER_DAYS:
        return value
    return DEFAULT_MEASUREMENT_REMINDER_DAYS


def measurement_window_open(frequency: Optional[str], today: date, lead_days: int) -> bool:
    """True once at most ``lead_days`` days of the period remain, today counted.

    A metric with no calendar period has no window to wait for: it is open.
    """
    end = status_rules.period_end(frequency, today)
    if end is None:
        return True
    return (end - today).days + 1 <= lead_days


async def _lead_days(db: AsyncSession, org_id: UUID) -> int:
    return resolve_measurement_reminder_days(
        await db.scalar(select(Organization.settings).where(Organization.id == org_id))
    )


async def _measurement_notice(db: AsyncSession, objective, today: date) -> Optional[tuple]:
    """``(message, dedup_on)`` when a logged metric is waiting for its value."""
    from services.isms_metrics.service import active_metric, read_objective

    found = await active_metric(db, objective.organization_id, objective.id)
    if found is None or found.kind != "logged":
        return None
    metric = _snapshot(found)
    reading = await read_objective(db, objective.organization_id, objective, metric, today)
    state = reading.get("status")
    if state not in (status_rules.FIRST_MEASUREMENT_DUE, status_rules.MEASUREMENT_OVERDUE):
        return None
    if not measurement_window_open(metric.frequency, today, await _lead_days(db, objective.organization_id)):
        return None
    dedup_on = status_rules.period_start(metric.frequency, today) or metric.active_from
    first = state == status_rules.FIRST_MEASUREMENT_DUE
    return measurement_message(objective, metric.frequency, first), dedup_on


async def _still_member(db: AsyncSession, org_id: UUID, user_id: Optional[UUID]) -> bool:
    if user_id is None:
        return False
    return (await db.scalar(
        select(OrganizationMember.user_id).where(
            OrganizationMember.organization_id == org_id, OrganizationMember.user_id == user_id,
        )
    )) is not None


async def recipients_for(db: AsyncSession, objective, event_key: str) -> frozenset:
    """Named owner, then the team's primary and delegate, then the admins.

    The owner counts only while still a member of the organisation: the
    column outlives the membership. The team and admin tiers need no such
    check, because team membership cascades from organisation membership.
    """
    owner = objective.owner_user_id
    if not await _still_member(db, objective.organization_id, owner):
        owner = None
    resolved = await resolve_recipients_for(
        db,
        Item(
            item_type=REFERENCE_TYPE,
            item_id=objective.id,
            organization_id=objective.organization_id,
            explicit_user_ids=(owner,),
            owning_team_id=objective.owning_team_id,
        ),
        event_key,
    )
    return resolved.user_ids


async def _notify(db, objective, event_key: str, message: str, dedup_on) -> int:
    from services.notifications import _emit

    recipients = await recipients_for(db, objective, event_key)
    if not recipients:
        return 0
    return await _emit(
        db,
        notification_type=event_key,
        reference_type=REFERENCE_TYPE,
        reference_id=objective.id,
        organization_id=objective.organization_id,
        message=message,
        recipient_ids=recipients,
        dedup_on=dedup_on,
        email_subject=f"Objective {objective.ref} needs attention",
        email_body=message,
    )


async def _measurement_step(db: AsyncSession, objective, today: date) -> int:
    notice = await _measurement_notice(db, objective, today)
    if notice is None:
        return 0
    return await _notify(db, objective, MEASUREMENT_DUE, *notice)


async def _due_step(db: AsyncSession, objective, today: date) -> int:
    notice = date_notice(objective.due_date, today)
    if notice is None:
        return 0
    reached, dedup_on = notice
    return await _notify(
        db, objective, OBJECTIVE_DUE, due_message(objective, objective.due_date, reached), dedup_on,
    )


async def _review_step(db: AsyncSession, objective, today: date) -> int:
    notice = date_notice(objective.review_date, today)
    if notice is None:
        return 0
    reached, dedup_on = notice
    return await _notify(
        db, objective, REVIEW_DUE, review_message(objective, objective.review_date, reached), dedup_on,
    )


#: The three reminders of one objective. Each stands alone in the sweep: one
#: that fails (a bad result row under the measurement reminder, say) must not
#: cost the owner the other two, that morning and every morning after.
_STEPS = (("measurement", _measurement_step), ("due date", _due_step), ("review date", _review_step))


async def _active_objectives(db: AsyncSession) -> List[Any]:
    """Every active, unarchived objective, as plain rows."""
    return list((await db.execute(
        select(*_OBJECTIVE_COLUMNS).where(
            SecurityObjective.lifecycle_status == "active",
            SecurityObjective.archived_at.is_(None),
        ).order_by(SecurityObjective.organization_id, SecurityObjective.ref)
    )).all())


async def check_and_notify_objectives(db: AsyncSession, today: Optional[date] = None) -> int:
    """Run the daily sweep; returns the number of notification rows written."""
    today = today or date.today()
    written = 0
    failed: Dict[UUID, str] = {}
    try:
        objectives = await _active_objectives(db)
    except Exception as exc:
        logger.error("objective reminders: could not list objectives: %s", exc, exc_info=True)
        await db.rollback()
        return 0
    for objective in objectives:
        for step_name, step in _STEPS:
            try:
                written += await step(db, objective, today)
            except Exception as exc:
                if type(exc).__name__ == "SoftTimeLimitExceeded":  # the worker is stopping the task
                    raise
                failed[objective.id] = str(exc)
                logger.error(
                    "objective reminders: %s reminder for %s in org %s failed: %s",
                    step_name, objective.ref, objective.organization_id, exc, exc_info=True,
                )
                # Without this a SQL error leaves the transaction aborted and
                # every later reminder in the sweep fails with it.
                await db.rollback()
    logger.info(
        "objective reminders: %s notifications written, %s objectives failed", written, len(failed),
    )
    return written
