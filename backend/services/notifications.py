"""
Notification Service - Create and manage notifications for users.

This service provides helper functions to create notifications for various events:
- User assignments
- @mentions in comments
- Tasks due soon
- Tasks overdue
- Evidence review rejections
- Controls marked ready for review
- Composite assessments transitioning to insufficient
- Auditor queries raised on engagements

Also sends email notifications via Resend when enabled.

#822 phase 4 — recipients, dedup and escalation
-----------------------------------------------

Every recipient used to be hand-derived at each of the nine ``Notification(...)``
creation sites below. Nine expressions, three of which disagreed about what
"the owner" meant, and two of which (evidence rejection, composite
insufficient) had already grown ad-hoc fallback and set-based dedup — this
pattern in miniature, written twice.

They now all call one function,
:func:`services.owner_resolution.resolve_recipients_for`, and the routing
policy for each type is a row in :data:`~services.owner_resolution.EVENTS`
rather than an ``if`` at the call site. What that buys, concretely:

* **Ownership stops evaporating.** ``assigned_user_id`` and ``owner_user_id``
  are ``ON DELETE SET NULL``, so the day somebody leaves, tier 1 empties and
  every item they held silently notifies nobody. Tier 2 — the accountable
  team's primary *and* delegate — outlives its members.
* **An unassigned task is no longer skipped forever.** Both schedulers below
  used to open with ``if not task.assigned_user_id: continue``, which meant no
  due warning, no overdue warning and no escalation, for the entire life of
  the task. That was live in production. The ``continue`` is gone and the
  chain replaces it rather than a special case.
* **The result is a set.** Somebody who is both the explicit assignee and the
  accountable team's primary receives one notification, not two.

Three volume controls, because this feature multiplies recipients and
notification fatigue would make the whole schema counter-productive:

1. **The dedup key is ``type + reference_id + date``**, not
   ``(user_id, type, reference_id, date)``. The old key worked only because
   there was exactly one recipient; with a set, a run that failed part way
   through re-notified whoever had already been told. Every recipient's row
   for one event is written in **one transaction**, all-or-nothing, so
   "already notified" is never half true. See :func:`_emit`.
2. **Bulk operations emit one aggregate notification per recipient** — see
   :func:`create_bulk_team_assignment_notifications`, which produces
   *"12 controls assigned to Security Operations"* rather than twelve rows.
3. **Team-tier routing activates only when a team is assigned to the item.**
   With no accountable team, tier 2 is empty and the chain falls straight
   through it, so an organisation that has never created a team resolves to
   exactly the people it resolves to today. That is a property of the data,
   not a feature flag, and ``tests/test_notification_recipients.py`` asserts
   it site by site.

Escalation (overdue, at risk, evidence rejected) additionally notifies the
accountable team on top of the resolved set, and fires on **threshold
crossings** rather than on state — see
:mod:`services.notification_escalation` for why a daily scheduler asking "are
you overdue?" produces sixty notifications about one task by day thirty.
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy import select
from uuid import UUID
from datetime import date, timedelta
from typing import Iterable, Optional, Sequence
import logging

from models import (
    Notification,
    User,
    Assignment,
    AuditEngagement,
    Comment,
    EngagementQuery,
    EvidenceCollectionTask,
    EvidenceTracking,
    OrganizationMember,
    ScopedControl,
)
from services.notification_escalation import pending_escalation_threshold
from services.owner_resolution import (
    Item,
    RecipientResolution,
    resolve_recipients_for,
    resolve_recipients_for_sync,
)
from services.email_service import (
    send_assignment_notification_email,
    send_task_due_notification_email,
    send_task_overdue_notification_email,
    send_mention_notification_email,
    send_event_notification_email,
    send_event_notification_email_sync
)

logger = logging.getLogger(__name__)


async def _get_org_admin_user_ids(
    db: AsyncSession,
    organization_id: UUID,
    exclude_user_id: UUID = None
) -> list[UUID]:
    """Return deduplicated org admin user ids, excluding the acting user."""
    result = await db.execute(
        select(OrganizationMember.user_id).where(
            OrganizationMember.organization_id == organization_id,
            OrganizationMember.role == 'admin'
        )
    )
    admin_ids = set(result.scalars().all())
    if exclude_user_id is not None:
        admin_ids.discard(exclude_user_id)
    return list(admin_ids)


async def _get_user_name(db: AsyncSession, user_id: UUID) -> str:
    """Return a user's display name (or email), defaulting to 'Someone'."""
    if not user_id:
        return "Someone"
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        return "Someone"
    return user.display_name or user.email


# ---------------------------------------------------------------------------
# The shared writer
#
# One place where notification rows are created, so the dedup key, the
# transaction boundary and the email fan-out cannot differ between the nine
# events that use them.
# ---------------------------------------------------------------------------

def _wants_immediate_email(user) -> bool:
    return bool(
        user is not None
        and user.email_notifications_enabled
        and user.notification_frequency == 'immediate'
    )


async def _load_users(db: AsyncSession, user_ids: Iterable[UUID]) -> list:
    """The recipient rows, in one query rather than one per recipient.

    A user id with no row is dropped silently: it is a deleted account, and a
    notification pointing at one is a foreign-key violation that would abort
    the whole batch — including the recipients who do still exist.
    """
    ids = [u for u in set(user_ids) if u is not None]
    if not ids:
        return []
    result = await db.execute(select(User).where(User.id.in_(ids)))
    return list(result.scalars().all())


async def _already_notified(
    db: AsyncSession,
    *,
    notification_type: str,
    reference_id: UUID,
    on_or_after: date,
) -> bool:
    """The event-level dedup guard, keyed on ``type + reference_id + date``.

    Deliberately **not** keyed on ``user_id``. The old per-user key was correct
    only while there was exactly one recipient; the moment a set is involved,
    a run that wrote two of three rows and then failed would, on its next
    attempt, re-notify the two who already knew. One key for the event means
    the event has either been announced or it has not.

    Served by ``ix_notifications_type_reference_created``, added by the phase 4
    schema lane for exactly this predicate.
    """
    result = await db.execute(
        select(Notification.id).where(
            Notification.type == notification_type,
            Notification.reference_id == reference_id,
            Notification.created_at >= on_or_after,
        ).limit(1)
    )
    return result.scalars().first() is not None


async def _emit(
    db: AsyncSession,
    *,
    notification_type: str,
    reference_type: str,
    reference_id: UUID,
    organization_id: UUID,
    message: str,
    recipient_ids: Iterable[UUID],
    dedup_on: Optional[date] = None,
    email_subject: Optional[str] = None,
    email_body: Optional[str] = None,
) -> int:
    """Write one notification per recipient, atomically, and send the emails.

    Args:
        organization_id: the tenant the referenced entity belongs to (#852).
            Required — a notification without an org has no read boundary.
        dedup_on: when given, the whole emission is skipped if any
            notification of this type already exists for this reference on or
            after that date. This is the event-level key described in
            :func:`_already_notified`; pass ``None`` for user-triggered events,
            where doing the thing twice legitimately notifies twice.

    Returns:
        The number of notification rows written. ``0`` covers every reason
        for writing none — nobody resolved, everybody was excluded, the event
        was already announced today — and none of them is an error.

    Emails are sent **after** the commit, never before. A mail provider that
    is slow, rate-limited or down must not be able to roll back the in-app
    notifications that are this service's actual guarantee; the older sites
    sent inside the transaction and could.
    """
    recipients = {r for r in recipient_ids if r is not None}
    if not recipients:
        return 0

    try:
        if dedup_on is not None and await _already_notified(
            db,
            notification_type=notification_type,
            reference_id=reference_id,
            on_or_after=dedup_on,
        ):
            return 0

        users = await _load_users(db, recipients)
        if not users:
            return 0

        for user in users:
            db.add(Notification(
                user_id=user.id,
                organization_id=organization_id,
                type=notification_type,
                reference_type=reference_type,
                reference_id=reference_id,
                message=message,
            ))

        # One commit for the whole recipient set. All of them are notified or
        # none of them are, which is what makes the event-level dedup key above
        # a truthful answer rather than a guess.
        await db.commit()

    except Exception as e:
        logger.error(
            f"Failed to create {notification_type} notifications for "
            f"{reference_type} {reference_id}: {e}"
        )
        await db.rollback()
        return 0

    if email_subject:
        for user in users:
            if not _wants_immediate_email(user):
                continue
            try:
                await send_event_notification_email(
                    to_email=user.email,
                    to_name=user.display_name or user.email,
                    subject=email_subject,
                    body_line=email_body or message,
                    event_type=notification_type,
                )
            except Exception as e:
                logger.error(f"Failed to send {notification_type} email: {e}")

    return len(users)


async def _resolve_org_for(
    db: AsyncSession,
    item_type: str,
    item_id: UUID,
) -> Optional[UUID]:
    """Resolve the tenant that owns the referenced entity (#852).

    Used by the directed events (assignment, mention) and the
    single-recipient task helpers, whose callers do not pass an org.
    Returns None when the entity no longer exists — the caller must then
    skip the notification rather than write a row with no tenant.
    """
    if item_type == 'control':
        stmt = select(ScopedControl.organization_id).where(ScopedControl.id == item_id)
    elif item_type == 'evidence':
        stmt = select(EvidenceTracking.organization_id).where(EvidenceTracking.id == item_id)
    elif item_type == 'task':
        stmt = select(EvidenceCollectionTask.organization_id).where(EvidenceCollectionTask.id == item_id)
    elif item_type == 'engagement_query':
        stmt = (
            select(AuditEngagement.organization_id)
            .join(EngagementQuery, EngagementQuery.engagement_id == AuditEngagement.id)
            .where(EngagementQuery.id == item_id)
        )
    else:
        logger.warning(f"Cannot resolve org for unknown item type {item_type}")
        return None

    result = await db.execute(stmt)
    return result.scalars().first()


async def create_assignment_notification(
    db: AsyncSession,
    user_id: UUID,
    assignable_type: str,
    assignable_id: UUID,
    assigned_by_user_id: UUID = None
):
    """Create notification when a user is assigned to a control or evidence.

    A **directed** event: somebody has just named this person, so the chain
    resolves tier 1 and stops. Falling an assignment through to the accountable
    team, and then to every org admin, would page an organisation because one
    assignment named a user who no longer exists.
    """
    try:
        recipients = await resolve_recipients_for(
            db,
            Item(
                item_type=assignable_type,
                item_id=assignable_id,
                organization_id=None,
                explicit_user_ids=(user_id,),
            ),
            'assignment',
        )
        if not recipients:
            logger.warning(f"No recipient resolved for assignment {assignable_id}")
            return None

        # Get user details
        result = await db.execute(
            select(User).where(User.id.in_(list(recipients.user_ids)))
        )
        users = list(result.scalars().all())
        if not users:
            logger.warning(f"User {user_id} not found for assignment notification")
            return None

        assigned_by_name = await _get_user_name(db, assigned_by_user_id)
        message = f"{assigned_by_name} assigned you to a {assignable_type}"

        organization_id = await _resolve_org_for(db, assignable_type, assignable_id)
        if organization_id is None:
            logger.warning(
                f"No org resolvable for {assignable_type} {assignable_id} - "
                f"skipping assignment notification"
            )
            return None

        notification = None
        for user in users:
            notification = Notification(
                user_id=user.id,
                organization_id=organization_id,
                type='assignment',
                reference_type=assignable_type,
                reference_id=assignable_id,
                message=message
            )
            db.add(notification)
        await db.commit()

        logger.info(f"Created assignment notification for user {user_id}")

        # Send email if user has email notifications enabled
        for user in users:
            if _wants_immediate_email(user):
                await send_assignment_notification_email(
                    to_email=user.email,
                    to_name=user.display_name or user.email,
                    assignable_type=assignable_type,
                    assignable_id=str(assignable_id),
                    assigned_by_name=assigned_by_name
                )

        return notification

    except Exception as e:
        logger.error(f"Failed to create assignment notification: {e}")
        await db.rollback()
        return None


async def create_mention_notifications(
    db: AsyncSession,
    mentioned_user_ids: list[UUID],
    comment_id: UUID,
    commenter_id: UUID,
    commentable_type: str,
    commentable_id: UUID
):
    """Create notifications for users mentioned in a comment.

    A **directed** event, and the one where that matters most: a comment that
    mentions nobody must notify nobody. Routing a mention through the ownership
    chain would turn a typo'd handle into a page for the accountable team, and
    an empty mention list into a page for every administrator in the
    organisation.
    """
    notifications_created = 0

    try:
        recipients = await resolve_recipients_for(
            db,
            Item(
                item_type=commentable_type,
                item_id=commentable_id,
                organization_id=None,
                explicit_user_ids=tuple(mentioned_user_ids or ()),
            ),
            'mention',
            # Don't notify self.
            exclude_user_ids=(commenter_id,),
        )
        if not recipients:
            return 0

        # Get commenter's name and comment content
        result = await db.execute(
            select(User).where(User.id == commenter_id)
        )
        commenter = result.scalar_one_or_none()
        commenter_name = commenter.display_name or commenter.email if commenter else "Someone"

        # Get comment content for email preview
        comment_result = await db.execute(
            select(Comment).where(Comment.id == comment_id)
        )
        comment = comment_result.scalar_one_or_none()
        comment_preview = comment.content if comment else "No preview available"

        organization_id = await _resolve_org_for(db, commentable_type, commentable_id)
        if organization_id is None:
            logger.warning(
                f"No org resolvable for {commentable_type} {commentable_id} - "
                f"skipping mention notifications"
            )
            return 0

        for user_id in recipients.user_ids:
            # Get mentioned user details
            user_result = await db.execute(
                select(User).where(User.id == user_id)
            )
            mentioned_user = user_result.scalar_one_or_none()
            if not mentioned_user:
                continue

            message = f"{commenter_name} mentioned you in a comment on a {commentable_type}"

            # Create in-app notification
            notification = Notification(
                user_id=user_id,
                organization_id=organization_id,
                type='mention',
                reference_type='comment',
                reference_id=comment_id,
                message=message
            )
            db.add(notification)
            notifications_created += 1

            # Send email if user has immediate notifications enabled
            if _wants_immediate_email(mentioned_user):
                await send_mention_notification_email(
                    to_email=mentioned_user.email,
                    to_name=mentioned_user.display_name or mentioned_user.email,
                    commenter_name=commenter_name,
                    commentable_type=commentable_type,
                    comment_preview=comment_preview
                )

        await db.commit()
        logger.info(f"Created {notifications_created} mention notifications")
        return notifications_created

    except Exception as e:
        logger.error(f"Failed to create mention notifications: {e}")
        await db.rollback()
        return 0


# ---------------------------------------------------------------------------
# Message wording, factored out so the scheduler and the single-recipient
# helpers below cannot word the same event differently.
# ---------------------------------------------------------------------------

def _task_due_message(evidence_id: str, due_date: date, today: date = None) -> str:
    days_until_due = (due_date - (today or date.today())).days
    if days_until_due <= 0:
        return f"Evidence collection task for {evidence_id} is due today!"
    return (
        f"Evidence collection task for {evidence_id} is due in "
        f"{days_until_due} day(s)"
    )


def _task_overdue_message(evidence_id: str, due_date: date, today: date = None) -> str:
    days_overdue = ((today or date.today()) - due_date).days
    return (
        f"Evidence collection task for {evidence_id} is overdue by "
        f"{days_overdue} day(s)"
    )


async def create_task_due_notification(
    db: AsyncSession,
    user_id: UUID,
    task_id: UUID,
    evidence_id: str,
    due_date: date
):
    """Create notification when a task is due soon.

    Kept as a single-recipient entry point for callers outside the scheduler.
    The scheduler itself resolves a recipient *set* and writes it through
    :func:`_emit` in one transaction — see :func:`check_and_notify_due_tasks`.
    """
    try:
        organization_id = await _resolve_org_for(db, 'task', task_id)
        if organization_id is None:
            logger.warning(f"No org resolvable for task {task_id} - skipping due notification")
            return None

        notification = Notification(
            user_id=user_id,
            organization_id=organization_id,
            type='task_due',
            reference_type='task',
            reference_id=task_id,
            message=_task_due_message(evidence_id, due_date),
        )
        db.add(notification)
        await db.commit()

        logger.info(f"Created task due notification for user {user_id}")
        return notification

    except Exception as e:
        logger.error(f"Failed to create task due notification: {e}")
        await db.rollback()
        return None


async def create_task_overdue_notification(
    db: AsyncSession,
    user_id: UUID,
    task_id: UUID,
    evidence_id: str,
    due_date: date
):
    """Create notification when a task is overdue.

    As with :func:`create_task_due_notification`, the scheduler no longer goes
    through here — it resolves a set and gates on the escalation threshold.
    """
    try:
        organization_id = await _resolve_org_for(db, 'task', task_id)
        if organization_id is None:
            logger.warning(f"No org resolvable for task {task_id} - skipping overdue notification")
            return None

        notification = Notification(
            user_id=user_id,
            organization_id=organization_id,
            type='task_overdue',
            reference_type='task',
            reference_id=task_id,
            message=_task_overdue_message(evidence_id, due_date),
        )
        db.add(notification)
        await db.commit()

        logger.info(f"Created task overdue notification for user {user_id}")
        return notification

    except Exception as e:
        logger.error(f"Failed to create task overdue notification: {e}")
        await db.rollback()
        return None


def _task_item(task, evidence) -> Item:
    """The resolver's view of a collection task.

    Tier 1 is the task's own assignee. Tier 2 is its ``owning_team_id`` when
    set — the setup / collect / review split on one evidence item is routinely
    three different functions — and otherwise, through ``parent``, the evidence
    item's accountable team. Nothing is copied down onto the task, so the two
    cannot drift apart.
    """
    return Item(
        item_type='task',
        item_id=task.id,
        organization_id=task.organization_id,
        explicit_user_ids=(task.assigned_user_id,),
        owning_team_id=task.owning_team_id,
        parent=Item(
            item_type='evidence',
            item_id=task.evidence_tracking_id,
            organization_id=task.organization_id,
            explicit_user_ids=(),
        ) if evidence is not None else None,
    )


async def check_and_notify_due_tasks(db: AsyncSession):
    """
    Check for tasks due soon and create notifications.
    Should be run daily as a cron job.

    The ``if not task.assigned_user_id: continue`` that used to open this loop
    is gone. An unassigned task was silently skipped by this scheduler for its
    entire life — no due warning, no overdue warning, no escalation — which is
    the accountability-evaporation problem #822 exists to fix, and it was live.
    The resolution chain replaces it: an unassigned task with an owning team
    now notifies that team.
    """
    notifications_created = 0

    try:
        # Get tasks due in next 3 days (not completed, not already notified today)
        today = date.today()
        three_days_from_now = today + timedelta(days=3)

        # Joined rather than fetched per task: the evidence item supplies the
        # key the message names, and the old code issued one query per task to
        # get it.
        result = await db.execute(
            select(EvidenceCollectionTask, EvidenceTracking)
            .join(
                EvidenceTracking,
                EvidenceCollectionTask.evidence_tracking_id == EvidenceTracking.id,
            )
            .where(
                EvidenceCollectionTask.status != 'completed',
                EvidenceCollectionTask.due_date <= three_days_from_now,
                EvidenceCollectionTask.due_date >= today,
            )
        )
        rows = result.all()

        for task, evidence in rows:
            recipients = await resolve_recipients_for(
                db, _task_item(task, evidence), 'task_due',
            )
            if not recipients:
                logger.info(
                    f"Task {task.id} is due but resolves to nobody - "
                    f"no assignee, no owning team, no org admins"
                )
                continue

            evidence_key = evidence.evidence_id if evidence else "Unknown"
            message = _task_due_message(evidence_key, task.due_date, today)

            written = await _emit(
                db,
                notification_type='task_due',
                reference_type='task',
                reference_id=task.id,
                organization_id=task.organization_id,
                message=message,
                recipient_ids=recipients.user_ids,
                # Once per task per day, for the whole recipient set, however
                # many of them there are.
                dedup_on=today,
            )
            notifications_created += written

            if written:
                days_until = (task.due_date - today).days
                for user in await _load_users(db, recipients.user_ids):
                    if _wants_immediate_email(user):
                        await send_task_due_notification_email(
                            to_email=user.email,
                            to_name=user.display_name or user.email,
                            evidence_id=evidence_key,
                            due_date=task.due_date,
                            days_until_due=days_until,
                        )

        logger.info(f"Created {notifications_created} task due notifications")
        return notifications_created

    except Exception as e:
        logger.error(f"Failed to check and notify due tasks: {e}")
        return 0


async def check_and_notify_overdue_tasks(db: AsyncSession):
    """
    Check for overdue tasks and create notifications.
    Should be run daily as a cron job.

    Two changes from the version this replaces, both of them #822 acceptance
    criteria:

    * The ``if not task.assigned_user_id: continue`` is gone, for the reason
      given on :func:`check_and_notify_due_tasks`.
    * Overdue is an **escalation** event, and escalation fires on threshold
      crossings rather than on state. This scheduler runs daily; a task that
      escalated because it *is* overdue escalated again tomorrow and every day
      after, so one stalled task produced sixty notifications by day thirty.
      It now escalates once on becoming overdue, then at +7 and +30 days —
      derived from the notification rows that already exist, with no new
      column. See :mod:`services.notification_escalation`.

    Because escalation is additive, the accountable team's primary and delegate
    are notified **on top of** whoever the chain resolved, so a stalled task
    reaches the owning team even when an individual is assigned to it.
    """
    notifications_created = 0

    try:
        # Get overdue tasks (not completed, due date passed)
        today = date.today()

        result = await db.execute(
            select(EvidenceCollectionTask, EvidenceTracking)
            .join(
                EvidenceTracking,
                EvidenceCollectionTask.evidence_tracking_id == EvidenceTracking.id,
            )
            .where(
                EvidenceCollectionTask.status != 'completed',
                EvidenceCollectionTask.due_date < today,
            )
        )
        rows = result.all()

        for task, evidence in rows:
            threshold = await pending_escalation_threshold(
                db,
                notification_type='task_overdue',
                reference_id=task.id,
                due_date=task.due_date,
                today=today,
            )
            if threshold is None:
                # Already escalated at the highest threshold this task has
                # crossed. It escalates again at the next one, not tomorrow.
                continue

            recipients = await resolve_recipients_for(
                db, _task_item(task, evidence), 'task_overdue',
            )
            if not recipients:
                logger.info(
                    f"Task {task.id} is overdue but resolves to nobody - "
                    f"no assignee, no owning team, no org admins"
                )
                continue

            evidence_key = evidence.evidence_id if evidence else "Unknown"
            message = _task_overdue_message(evidence_key, task.due_date, today)

            written = await _emit(
                db,
                notification_type='task_overdue',
                reference_type='task',
                reference_id=task.id,
                organization_id=task.organization_id,
                message=message,
                recipient_ids=recipients.user_ids,
                # No date-based dedup: the threshold gate above already
                # suppresses a second run on the same day, because any row
                # written today is on or after every threshold this task has
                # crossed. A second key here would be a second answer to one
                # question.
            )
            notifications_created += written

            if written:
                logger.info(
                    f"Escalated task {task.id} at the +{threshold}d threshold "
                    f"to {written} recipient(s) (tier={recipients.tier})"
                )
                days_overdue = (today - task.due_date).days
                for user in await _load_users(db, recipients.user_ids):
                    if _wants_immediate_email(user):
                        await send_task_overdue_notification_email(
                            to_email=user.email,
                            to_name=user.display_name or user.email,
                            evidence_id=evidence_key,
                            due_date=task.due_date,
                            days_overdue=days_overdue,
                        )

        logger.info(f"Created {notifications_created} task overdue notifications")
        return notifications_created

    except Exception as e:
        logger.error(f"Failed to check and notify overdue tasks: {e}")
        return 0


async def create_evidence_rejected_notifications(
    db: AsyncSession,
    organization_id: UUID,
    evidence_id: str,
    rejected_by_user_id: UUID = None
):
    """Create notifications when an evidence review is rejected.

    Rejection is an **escalation** event, so the accountable team's primary and
    delegate are notified on top of whoever the chain resolves. The reviewer is
    never notified about their own action.

    Tier 1 here is ``assigned_user_id`` *or* ``owner_user_id`` — the coalesce,
    not both — because that is what this site resolved before #822 and #822's
    tier 1 is explicitly "today's behaviour, unchanged". What counts as an
    explicit assignment differs per item and is the call site's decision; the
    chain's job is what happens when tier 1 is empty.

    Returns:
        The number of notifications created. ``0`` when the only recipient was
        the reviewer, or when nothing resolved.
    """
    try:
        result = await db.execute(
            select(EvidenceTracking).where(
                EvidenceTracking.organization_id == organization_id,
                EvidenceTracking.evidence_id == evidence_id
            ).limit(1)
        )
        tracking = result.scalars().first()
        if not tracking:
            logger.info(f"No evidence tracking row for {evidence_id} - skipping rejection notification")
            return 0

        recipients = await resolve_recipients_for(
            db,
            Item(
                item_type='evidence',
                item_id=tracking.id,
                organization_id=organization_id,
                explicit_user_ids=(
                    tracking.assigned_user_id or tracking.owner_user_id,
                ),
            ),
            'evidence_rejected',
            exclude_user_ids=(rejected_by_user_id,),
        )
        if not recipients:
            return 0

        reviewer_name = await _get_user_name(db, rejected_by_user_id)
        message = f"{reviewer_name} rejected evidence {evidence_id}"

        created = await _emit(
            db,
            notification_type='evidence_rejected',
            reference_type='evidence',
            reference_id=tracking.id,
            organization_id=organization_id,
            message=message,
            recipient_ids=recipients.user_ids,
            email_subject=f"Evidence {evidence_id} was rejected",
        )
        logger.info(
            f"Created {created} evidence rejected notifications "
            f"(tier={recipients.tier})"
        )
        return created

    except Exception as e:
        logger.error(f"Failed to create evidence rejected notification: {e}")
        await db.rollback()
        return 0


async def create_control_ready_for_review_notifications(
    db: AsyncSession,
    organization_id: UUID,
    scoped_control_id: UUID,
    scf_id: str,
    actor_user_id: UUID = None
):
    """Create notifications for org admins when a control becomes ready for review.

    Routed as an **org-admin** event rather than through the ownership chain.
    The admins are the audience here, not the fallback: they are the people who
    perform the review being asked for. Resolving this through the chain would
    hand the review request to the control's own assignee — the person who just
    submitted it — and to nobody who can action it.
    """
    try:
        recipients = await resolve_recipients_for(
            db,
            Item(
                item_type='control',
                item_id=scoped_control_id,
                organization_id=organization_id,
            ),
            'control_ready_for_review',
            exclude_user_ids=(actor_user_id,),
        )
        if not recipients:
            return 0

        actor_name = await _get_user_name(db, actor_user_id)
        message = f"{actor_name} marked control {scf_id} as ready for review"

        created = await _emit(
            db,
            notification_type='control_ready_for_review',
            reference_type='control',
            reference_id=scoped_control_id,
            organization_id=organization_id,
            message=message,
            recipient_ids=recipients.user_ids,
            email_subject=f"Control {scf_id} is ready for review",
        )
        logger.info(f"Created {created} control ready for review notifications")
        return created

    except Exception as e:
        logger.error(f"Failed to create control ready for review notifications: {e}")
        await db.rollback()
        return 0


async def create_engagement_query_raised_notifications(
    db: AsyncSession,
    organization_id: UUID,
    query_id: UUID,
    scf_id: str,
    raised_by_user_id: UUID = None
):
    """Create notifications for org admins when an auditor query is raised.

    An **org-admin** event, for the same reason as
    :func:`create_control_ready_for_review_notifications`: answering an
    auditor is the organisation's response, not one control owner's.
    """
    try:
        recipients = await resolve_recipients_for(
            db,
            Item(
                item_type='engagement_query',
                item_id=query_id,
                organization_id=organization_id,
            ),
            'engagement_query_raised',
            exclude_user_ids=(raised_by_user_id,),
        )
        if not recipients:
            return 0

        raiser_name = await _get_user_name(db, raised_by_user_id)
        message = f"{raiser_name} raised a query on control {scf_id}"

        created = await _emit(
            db,
            notification_type='engagement_query_raised',
            reference_type='engagement_query',
            reference_id=query_id,
            organization_id=organization_id,
            message=message,
            recipient_ids=recipients.user_ids,
            email_subject=f"New query raised on control {scf_id}",
        )
        logger.info(f"Created {created} engagement query raised notifications")
        return created

    except Exception as e:
        logger.error(f"Failed to create engagement query raised notifications: {e}")
        await db.rollback()
        return 0


def create_composite_insufficient_notifications_sync(
    session: Session,
    organization_id: UUID,
    scf_id: str
):
    """Create notifications when a control's composite transitions to insufficient.

    Sync variant for Celery task contexts (composite_service uses a
    synchronous session). The caller is responsible for only invoking this on a
    genuine status transition.

    Recipients come from the same chain as every other site, through
    :func:`~services.owner_resolution.resolve_recipients_for_sync`, which
    executes the same statement builders against a synchronous ``Session``.
    Tier 1 is the control's owner **and** assignee — both, which is what this
    site resolved before #822. An insufficient composite is an escalation, so
    the accountable team is notified on top.
    """
    notifications_created = 0

    try:
        control = session.execute(
            select(ScopedControl).where(
                ScopedControl.organization_id == organization_id,
                ScopedControl.scf_id == scf_id
            ).limit(1)
        ).scalars().first()
        if not control:
            logger.info(f"No scoped control for {scf_id} - skipping composite insufficient notification")
            return 0

        recipients = resolve_recipients_for_sync(
            session,
            Item(
                item_type='control',
                item_id=control.id,
                organization_id=organization_id,
                explicit_user_ids=(
                    control.owner_user_id, control.assigned_user_id,
                ),
            ),
            'composite_insufficient',
        )
        if not recipients:
            return 0

        message = f"Composite evidence assessment for control {scf_id} is now insufficient"

        for user_id in recipients.user_ids:
            user = session.execute(
                select(User).where(User.id == user_id)
            ).scalar_one_or_none()
            if not user:
                continue

            notification = Notification(
                user_id=user_id,
                organization_id=organization_id,
                type='composite_insufficient',
                reference_type='control',
                reference_id=control.id,
                message=message
            )
            session.add(notification)
            notifications_created += 1

            if _wants_immediate_email(user):
                send_event_notification_email_sync(
                    to_email=user.email,
                    to_name=user.display_name or user.email,
                    subject=f"Control {scf_id} evidence is insufficient",
                    body_line=message,
                    event_type='composite_insufficient'
                )

        session.commit()
        logger.info(f"Created {notifications_created} composite insufficient notifications")
        return notifications_created

    except Exception as e:
        logger.error(f"Failed to create composite insufficient notifications: {e}")
        session.rollback()
        return 0


async def create_catalog_reconciliation_notifications(
    db: AsyncSession,
    organization_id: UUID,
    run_id: UUID,
    event: str,
    from_version: str = None,
    to_version: str = None,
    actor_user_id: UUID = None,
) -> int:
    """Notify org admins that a catalog reconciliation was applied or rolled
    back (WP2c, plan §4.3). ``event`` is 'applied' or 'rolled_back';
    reference_type='catalog' points the NotificationBell at the org
    reconciliation run.

    An **org-admin** event. A catalogue reconciliation is an organisation-wide
    change with no owning item, so there is no tier 1 or tier 2 for it to
    resolve; admins are the audience by construction.
    """
    try:
        notification_type = f'catalog_reconciliation_{event}'

        recipients = await resolve_recipients_for(
            db,
            Item(
                item_type='catalog',
                item_id=run_id,
                organization_id=organization_id,
            ),
            notification_type,
            exclude_user_ids=(actor_user_id,),
        )
        if not recipients:
            return 0

        if event == "rolled_back":
            message = (
                f"Catalog reconciliation rolled back — your organisation is back "
                f"on catalog {from_version}"
            )
            subject = f"Catalog reconciliation rolled back to {from_version}"
        else:
            message = (
                f"Catalog reconciliation applied — your organisation is now on "
                f"catalog {to_version}"
            )
            subject = f"Catalog reconciled to {to_version}"

        created = await _emit(
            db,
            notification_type=notification_type,
            reference_type='catalog',
            reference_id=run_id,
            organization_id=organization_id,
            message=message,
            recipient_ids=recipients.user_ids,
            email_subject=subject,
        )
        logger.info(
            f"Created {created} catalog reconciliation "
            f"notifications ({event}) for org {organization_id}"
        )
        return created

    except Exception as e:
        logger.error(f"Failed to create catalog reconciliation notifications: {e}")
        await db.rollback()
        return 0


# ---------------------------------------------------------------------------
# Bulk aggregation
# ---------------------------------------------------------------------------

#: Plural nouns for the aggregate message, so "1 control" does not read as
#: "1 controls". Keyed by the team-assignment registry's ``type_key``.
_ITEM_NOUNS = {
    'control': ('control', 'controls'),
    'evidence': ('evidence item', 'evidence items'),
    'risk': ('risk', 'risks'),
    'vendor': ('vendor', 'vendors'),
}


async def create_bulk_team_assignment_notifications(
    db: AsyncSession,
    *,
    organization_id: UUID,
    team_id: UUID,
    team_name: str,
    item_type: str,
    item_ids: Sequence[UUID],
    actor_user_id: UUID = None,
) -> int:
    """One aggregate notification per recipient for a bulk team assignment.

    *"12 controls assigned to Security Operations"* — one row per person, not
    one row per item. Assigning fifty controls to a team is a live path
    (#800 shipped bulk evidence actions), and the naive implementation of it
    produces a hundred notifications and a team that mutes the platform.

    Recipients are the newly assigned team's primary and delegate. Not the
    chain: the event is "your team has been given this work", so the audience
    is defined by the team, and falling back to org admins would announce every
    bulk assignment to the administrators who performed it.

    ``reference_id`` is the **team**, not any one item — the notification is
    about the assignment, and a notification whose reference points at an
    arbitrary member of a set of fifty is a deep link to the wrong place.

    Returns:
        The number of notification rows written — at most two.
    """
    items = list(item_ids)
    if not items:
        return 0

    try:
        from services.owner_resolution import _team_roster_stmt

        rows = list((await db.execute(
            _team_roster_stmt(organization_id, team_id)
        )).all())
        recipients = {
            user_id for user_id, _role in rows if user_id is not None
        }
        recipients.discard(actor_user_id)
        if not recipients:
            return 0

        singular, plural = _ITEM_NOUNS.get(item_type, (item_type, f"{item_type}s"))
        noun = singular if len(items) == 1 else plural
        message = f"{len(items)} {noun} assigned to {team_name}"

        return await _emit(
            db,
            notification_type='team_assignment',
            reference_type='team',
            reference_id=team_id,
            organization_id=organization_id,
            message=message,
            recipient_ids=recipients,
            email_subject=f"{len(items)} {noun} assigned to {team_name}",
        )

    except Exception as e:
        logger.error(f"Failed to create bulk team assignment notifications: {e}")
        await db.rollback()
        return 0


if __name__ == "__main__":
    """
    Run this script as a cron job:
    0 9 * * * cd /app && python -m services.notifications
    """
    import asyncio
    from database import AsyncSessionLocal

    logging.basicConfig(level=logging.INFO)

    async def main():
        async with AsyncSessionLocal() as db:
            due = await check_and_notify_due_tasks(db)
            overdue = await check_and_notify_overdue_tasks(db)
            print(f"Notifications created: {due} due, {overdue} overdue")

    asyncio.run(main())
