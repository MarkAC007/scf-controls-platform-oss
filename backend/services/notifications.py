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
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy import select
from uuid import UUID
from datetime import date, timedelta
import logging

from models import (
    Notification,
    User,
    Assignment,
    Comment,
    EvidenceCollectionTask,
    EvidenceTracking,
    OrganizationMember,
    ScopedControl,
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


async def create_assignment_notification(
    db: AsyncSession,
    user_id: UUID,
    assignable_type: str,
    assignable_id: UUID,
    assigned_by_user_id: UUID = None
):
    """Create notification when a user is assigned to a control or evidence."""
    try:
        # Get user details
        result = await db.execute(
            select(User).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()
        if not user:
            logger.warning(f"User {user_id} not found for assignment notification")
            return None

        # Get assigner's name if available
        assigned_by_name = "Someone"
        if assigned_by_user_id:
            result = await db.execute(
                select(User).where(User.id == assigned_by_user_id)
            )
            assigner = result.scalar_one_or_none()
            if assigner:
                assigned_by_name = assigner.display_name or assigner.email

        message = f"{assigned_by_name} assigned you to a {assignable_type}"

        # Create in-app notification
        notification = Notification(
            user_id=user_id,
            type='assignment',
            reference_type=assignable_type,
            reference_id=assignable_id,
            message=message
        )
        db.add(notification)
        await db.commit()

        logger.info(f"Created assignment notification for user {user_id}")

        # Send email if user has email notifications enabled
        if user.email_notifications_enabled and user.notification_frequency == 'immediate':
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
    """Create notifications for users mentioned in a comment."""
    notifications_created = 0

    try:
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

        for user_id in mentioned_user_ids:
            # Don't notify self
            if user_id == commenter_id:
                continue

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
                type='mention',
                reference_type='comment',
                reference_id=comment_id,
                message=message
            )
            db.add(notification)
            notifications_created += 1

            # Send email if user has immediate notifications enabled
            if mentioned_user.email_notifications_enabled and mentioned_user.notification_frequency == 'immediate':
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


async def create_task_due_notification(
    db: AsyncSession,
    user_id: UUID,
    task_id: UUID,
    evidence_id: str,
    due_date: date
):
    """Create notification when a task is due soon."""
    try:
        days_until_due = (due_date - date.today()).days

        if days_until_due <= 0:
            message = f"Evidence collection task for {evidence_id} is due today!"
        else:
            message = f"Evidence collection task for {evidence_id} is due in {days_until_due} day(s)"

        notification = Notification(
            user_id=user_id,
            type='task_due',
            reference_type='task',
            reference_id=task_id,
            message=message
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
    """Create notification when a task is overdue."""
    try:
        days_overdue = (date.today() - due_date).days

        message = f"Evidence collection task for {evidence_id} is overdue by {days_overdue} day(s)"

        notification = Notification(
            user_id=user_id,
            type='task_overdue',
            reference_type='task',
            reference_id=task_id,
            message=message
        )
        db.add(notification)
        await db.commit()

        logger.info(f"Created task overdue notification for user {user_id}")
        return notification

    except Exception as e:
        logger.error(f"Failed to create task overdue notification: {e}")
        await db.rollback()
        return None


async def check_and_notify_due_tasks(db: AsyncSession):
    """
    Check for tasks due soon and create notifications.
    Should be run daily as a cron job.
    """
    notifications_created = 0

    try:
        # Get tasks due in next 3 days (not completed, not already notified today)
        today = date.today()
        three_days_from_now = today + timedelta(days=3)

        result = await db.execute(
            select(EvidenceCollectionTask).where(
                EvidenceCollectionTask.status != 'completed',
                EvidenceCollectionTask.due_date <= three_days_from_now,
                EvidenceCollectionTask.due_date >= today
            )
        )
        tasks = result.scalars().all()

        for task in tasks:
            if not task.assigned_user_id:
                continue

            # Check if already notified today
            result = await db.execute(
                select(Notification).where(
                    Notification.user_id == task.assigned_user_id,
                    Notification.type == 'task_due',
                    Notification.reference_id == task.id,
                    Notification.created_at >= today
                )
            )
            existing = result.scalar_one_or_none()

            if existing:
                continue

            # Get evidence info
            from models import EvidenceTracking
            result = await db.execute(
                select(EvidenceTracking).where(EvidenceTracking.id == task.evidence_tracking_id)
            )
            evidence = result.scalar_one_or_none()
            evidence_id = evidence.evidence_id if evidence else "Unknown"

            # Get user details for email
            user_result = await db.execute(
                select(User).where(User.id == task.assigned_user_id)
            )
            user = user_result.scalar_one_or_none()

            # Create notification
            await create_task_due_notification(
                db, task.assigned_user_id, task.id, evidence_id, task.due_date
            )
            notifications_created += 1

            # Send email if user has immediate notifications enabled
            if user and user.email_notifications_enabled and user.notification_frequency == 'immediate':
                days_until = (task.due_date - today).days
                await send_task_due_notification_email(
                    to_email=user.email,
                    to_name=user.display_name or user.email,
                    evidence_id=evidence_id,
                    due_date=task.due_date,
                    days_until_due=days_until
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
    """
    notifications_created = 0

    try:
        # Get overdue tasks (not completed, due date passed)
        today = date.today()

        result = await db.execute(
            select(EvidenceCollectionTask).where(
                EvidenceCollectionTask.status != 'completed',
                EvidenceCollectionTask.due_date < today
            )
        )
        tasks = result.scalars().all()

        for task in tasks:
            if not task.assigned_user_id:
                continue

            # Check if already notified today
            result = await db.execute(
                select(Notification).where(
                    Notification.user_id == task.assigned_user_id,
                    Notification.type == 'task_overdue',
                    Notification.reference_id == task.id,
                    Notification.created_at >= today
                )
            )
            existing = result.scalar_one_or_none()

            if existing:
                continue

            # Get evidence info
            from models import EvidenceTracking
            result = await db.execute(
                select(EvidenceTracking).where(EvidenceTracking.id == task.evidence_tracking_id)
            )
            evidence = result.scalar_one_or_none()
            evidence_id = evidence.evidence_id if evidence else "Unknown"

            # Get user details for email
            user_result = await db.execute(
                select(User).where(User.id == task.assigned_user_id)
            )
            user = user_result.scalar_one_or_none()

            # Create notification
            await create_task_overdue_notification(
                db, task.assigned_user_id, task.id, evidence_id, task.due_date
            )
            notifications_created += 1

            # Send email if user has immediate notifications enabled
            if user and user.email_notifications_enabled and user.notification_frequency == 'immediate':
                days_overdue = (today - task.due_date).days
                await send_task_overdue_notification_email(
                    to_email=user.email,
                    to_name=user.display_name or user.email,
                    evidence_id=evidence_id,
                    due_date=task.due_date,
                    days_overdue=days_overdue
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
    """Create notification when an evidence review is rejected.

    Recipient is the evidence item's assigned user, falling back to the
    owner. The reviewer is never notified about their own action.
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
            return None

        recipient_id = tracking.assigned_user_id or tracking.owner_user_id
        if not recipient_id or recipient_id == rejected_by_user_id:
            return None

        user_result = await db.execute(select(User).where(User.id == recipient_id))
        user = user_result.scalar_one_or_none()
        if not user:
            logger.warning(f"User {recipient_id} not found for evidence rejected notification")
            return None

        reviewer_name = await _get_user_name(db, rejected_by_user_id)
        message = f"{reviewer_name} rejected evidence {evidence_id}"

        notification = Notification(
            user_id=recipient_id,
            type='evidence_rejected',
            reference_type='evidence',
            reference_id=tracking.id,
            message=message
        )
        db.add(notification)
        await db.commit()

        logger.info(f"Created evidence rejected notification for user {recipient_id}")

        if user.email_notifications_enabled and user.notification_frequency == 'immediate':
            await send_event_notification_email(
                to_email=user.email,
                to_name=user.display_name or user.email,
                subject=f"Evidence {evidence_id} was rejected",
                body_line=message,
                event_type='evidence_rejected'
            )

        return notification

    except Exception as e:
        logger.error(f"Failed to create evidence rejected notification: {e}")
        await db.rollback()
        return None


async def create_control_ready_for_review_notifications(
    db: AsyncSession,
    organization_id: UUID,
    scoped_control_id: UUID,
    scf_id: str,
    actor_user_id: UUID = None
):
    """Create notifications for org admins when a control becomes ready for review."""
    notifications_created = 0

    try:
        admin_ids = await _get_org_admin_user_ids(db, organization_id, exclude_user_id=actor_user_id)
        if not admin_ids:
            return 0

        actor_name = await _get_user_name(db, actor_user_id)
        message = f"{actor_name} marked control {scf_id} as ready for review"

        for user_id in admin_ids:
            user_result = await db.execute(select(User).where(User.id == user_id))
            user = user_result.scalar_one_or_none()
            if not user:
                continue

            notification = Notification(
                user_id=user_id,
                type='control_ready_for_review',
                reference_type='control',
                reference_id=scoped_control_id,
                message=message
            )
            db.add(notification)
            notifications_created += 1

            if user.email_notifications_enabled and user.notification_frequency == 'immediate':
                await send_event_notification_email(
                    to_email=user.email,
                    to_name=user.display_name or user.email,
                    subject=f"Control {scf_id} is ready for review",
                    body_line=message,
                    event_type='control_ready_for_review'
                )

        await db.commit()
        logger.info(f"Created {notifications_created} control ready for review notifications")
        return notifications_created

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
    """Create notifications for org admins when an auditor query is raised."""
    notifications_created = 0

    try:
        admin_ids = await _get_org_admin_user_ids(db, organization_id, exclude_user_id=raised_by_user_id)
        if not admin_ids:
            return 0

        raiser_name = await _get_user_name(db, raised_by_user_id)
        message = f"{raiser_name} raised a query on control {scf_id}"

        for user_id in admin_ids:
            user_result = await db.execute(select(User).where(User.id == user_id))
            user = user_result.scalar_one_or_none()
            if not user:
                continue

            notification = Notification(
                user_id=user_id,
                type='engagement_query_raised',
                reference_type='engagement_query',
                reference_id=query_id,
                message=message
            )
            db.add(notification)
            notifications_created += 1

            if user.email_notifications_enabled and user.notification_frequency == 'immediate':
                await send_event_notification_email(
                    to_email=user.email,
                    to_name=user.display_name or user.email,
                    subject=f"New query raised on control {scf_id}",
                    body_line=message,
                    event_type='engagement_query_raised'
                )

        await db.commit()
        logger.info(f"Created {notifications_created} engagement query raised notifications")
        return notifications_created

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
    synchronous session). Recipients are the scoped control's owner and
    assigned users when set, falling back to org admins. The caller is
    responsible for only invoking this on a genuine status transition.
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

        recipient_ids = {uid for uid in (control.owner_user_id, control.assigned_user_id) if uid}
        if not recipient_ids:
            recipient_ids = set(
                session.execute(
                    select(OrganizationMember.user_id).where(
                        OrganizationMember.organization_id == organization_id,
                        OrganizationMember.role == 'admin'
                    )
                ).scalars().all()
            )
        if not recipient_ids:
            return 0

        message = f"Composite evidence assessment for control {scf_id} is now insufficient"

        for user_id in recipient_ids:
            user = session.execute(
                select(User).where(User.id == user_id)
            ).scalar_one_or_none()
            if not user:
                continue

            notification = Notification(
                user_id=user_id,
                type='composite_insufficient',
                reference_type='control',
                reference_id=control.id,
                message=message
            )
            session.add(notification)
            notifications_created += 1

            if user.email_notifications_enabled and user.notification_frequency == 'immediate':
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
