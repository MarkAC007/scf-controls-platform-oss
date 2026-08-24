"""Escalation thresholds, derived from the notifications already sent (#822 §Notifications).

The scheduler runs once a day and asks each open item "are you overdue?". That
question is about **state**, and state is true again tomorrow: an item that
escalates because it *is* overdue escalates again the next day, and the day
after, and by day thirty one stalled task has produced thirty notifications and
a team that has muted the platform. #822 is explicit that escalation must fire
on the **crossing** — once on becoming overdue, then at +7 and +30 days.

**No new column.** "Has the 7-day threshold already fired for this item?" is
answerable from the rows already in ``notifications``, and the phase 4 schema
lane added ``ix_notifications_type_reference_created`` on
``(type, reference_id, created_at)`` precisely so that answering it is an index
lookup rather than a scan. A ``last_escalated_at`` column would be a second
source of truth for a fact the notification table already records, and it would
need a backfill that could only guess.

The derivation, stated once:

    A notification for threshold *T* can only have been written on or after
    ``due_date + T`` — that is the first day the threshold was reachable. So
    the threshold has already fired if, and only if, some existing
    notification for this item was created on or after that date.

Which gives, for a task due on day 0 and a scheduler running every day:

    ====  ============  ===============  ==========
    day   crossed       trigger date     fires?
    ====  ============  ===============  ==========
    1     0             due + 0          yes
    2-6   0             due + 0          no  (day 1's row is >= due+0)
    7     0, 7          due + 7          yes
    8-29  0, 7          due + 7          no
    30    0, 7, 30      due + 30         yes
    ====  ============  ===============  ==========

Three notifications in thirty days instead of thirty. A task overdue for five
days has produced exactly one, which is the assertion #822 asks for.

Self-repairing, which a stored counter would not be: a run that dies after
writing some recipients' rows and before writing the rest leaves the threshold
recorded as fired, and the transactional write in ``services/notifications.py``
is what makes "some rows" impossible in the first place. A run that dies before
writing anything leaves no row, and tomorrow's run fires the same threshold —
late by a day rather than lost.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Iterable, Optional, Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Notification

logger = logging.getLogger(__name__)

#: Days past the due date at which an item escalates. ``0`` is the crossing
#: into overdue itself — the first scheduler run after the due date passes.
#: Ordered, and read in order; keep it that way.
ESCALATION_THRESHOLD_DAYS: Sequence[int] = (0, 7, 30)


def escalation_threshold(
    *,
    due_date: date,
    today: date,
    prior_notification_dates: Iterable[date],
) -> Optional[int]:
    """The threshold to fire today, or ``None`` if today is not a crossing.

    Pure. Every branch of the table in this module's docstring is a two-line
    test against this function, with no database and no scheduler.

    Args:
        due_date: the item's due date. Not overdue until the day after.
        today: the scheduler's date.
        prior_notification_dates: the ``created_at`` dates of notifications
            already written for this item and type. Order is irrelevant; only
            the latest one can suppress anything.

    Returns:
        The threshold in days (one of :data:`ESCALATION_THRESHOLD_DAYS`), or
        ``None`` when the item is not yet overdue or the highest threshold it
        has crossed has already been notified.
    """
    days_overdue = (today - due_date).days
    if days_overdue <= 0:
        # Due today is not overdue. `check_and_notify_due_tasks` owns the
        # approach-to-deadline warning; this function owns only the far side.
        return None

    crossed = [t for t in ESCALATION_THRESHOLD_DAYS if days_overdue >= t]
    if not crossed:
        return None

    # The highest threshold reached, not every threshold reached. An item that
    # is 40 days overdue the first time anyone looks at it — a scheduler that
    # was switched off, an item imported with a historic due date — escalates
    # once, at 30, rather than emitting a backlog of three notifications for
    # deadlines that all passed before anybody could have acted on them.
    threshold = max(crossed)
    trigger_date = due_date + timedelta(days=threshold)

    for notified_on in prior_notification_dates:
        if notified_on is not None and notified_on >= trigger_date:
            return None

    return threshold


def _as_date(value) -> Optional[date]:
    """``created_at`` is a naive DateTime; the thresholds are whole days."""
    if value is None:
        return None
    return value.date() if hasattr(value, "date") else value


async def pending_escalation_threshold(
    db: AsyncSession,
    *,
    notification_type: str,
    reference_id: UUID,
    due_date: date,
    today: date,
) -> Optional[int]:
    """:func:`escalation_threshold` against the notifications already stored.

    One indexed read on ``(type, reference_id, created_at)``. Only rows from
    ``due_date`` onwards are fetched — anything earlier cannot suppress any
    threshold, and on a table that is never pruned that predicate is the
    difference between reading three rows and reading an item's entire
    notification history.
    """
    result = await db.execute(
        select(Notification.created_at).where(
            Notification.type == notification_type,
            Notification.reference_id == reference_id,
            Notification.created_at >= due_date,
        )
    )
    return escalation_threshold(
        due_date=due_date,
        today=today,
        prior_notification_dates=[_as_date(v) for v in result.scalars().all()],
    )
