"""
Notifications API endpoints - manage user notifications and settings.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, update
from typing import List
from uuid import UUID
from datetime import datetime

from database import get_db
from auth import require_auth, User
from models import Notification, User as DBUser
from schemas import (
    NotificationResponse,
    NotificationSettings,
    SuccessResponse
)

router = APIRouter(
    tags=["notifications"],
    dependencies=[Depends(require_auth)]
)


@router.get("/api/notifications", response_model=dict)
async def list_notifications(
    unread_only: bool = False,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """List notifications for current user with unread count."""
    if not current_user.db_id:
        return {
            "unread_count": 0,
            "notifications": []
        }

    user_id = UUID(current_user.db_id)

    # Get unread count
    result = await db.execute(
        select(Notification)
        .where(
            and_(
                Notification.user_id == user_id,
                Notification.is_read == False
            )
        )
    )
    unread_count = len(result.scalars().all())

    # Get notifications
    query = select(Notification).where(Notification.user_id == user_id)

    if unread_only:
        query = query.where(Notification.is_read == False)

    query = query.order_by(Notification.created_at.desc()).limit(limit)

    result = await db.execute(query)
    notifications = result.scalars().all()

    notification_list = [
        {
            "id": n.id,
            "user_id": n.user_id,
            "type": n.type,
            "reference_type": n.reference_type,
            "reference_id": n.reference_id,
            "message": n.message,
            "is_read": n.is_read,
            "read_at": n.read_at,
            "created_at": n.created_at
        }
        for n in notifications
    ]

    return {
        "unread_count": unread_count,
        "notifications": notification_list
    }


@router.patch("/api/notifications/{notification_id}/read", response_model=NotificationResponse)
async def mark_notification_read(
    notification_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """Mark a notification as read."""
    if not current_user.db_id:
        raise HTTPException(status_code=403, detail="Only authenticated users can mark notifications as read")

    result = await db.execute(
        select(Notification).where(Notification.id == notification_id)
    )
    notification = result.scalar_one_or_none()

    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")

    # Verify it belongs to current user
    if str(notification.user_id) != current_user.db_id:
        raise HTTPException(status_code=403, detail="You can only mark your own notifications as read")

    # Mark as read
    notification.is_read = True
    notification.read_at = datetime.utcnow()

    await db.commit()
    await db.refresh(notification)

    return {
        "id": notification.id,
        "user_id": notification.user_id,
        "type": notification.type,
        "reference_type": notification.reference_type,
        "reference_id": notification.reference_id,
        "message": notification.message,
        "is_read": notification.is_read,
        "read_at": notification.read_at,
        "created_at": notification.created_at
    }


@router.patch("/api/notifications/read-all", response_model=SuccessResponse)
async def mark_all_notifications_read(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """Mark all notifications as read for current user."""
    if not current_user.db_id:
        raise HTTPException(status_code=403, detail="Only authenticated users can mark notifications as read")

    user_id = UUID(current_user.db_id)

    # Update all unread notifications
    await db.execute(
        update(Notification)
        .where(
            and_(
                Notification.user_id == user_id,
                Notification.is_read == False
            )
        )
        .values(is_read=True, read_at=datetime.utcnow())
    )

    await db.commit()

    return SuccessResponse(message="All notifications marked as read")


@router.get("/api/notifications/settings", response_model=NotificationSettings)
async def get_notification_settings(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """Get current user's notification settings."""
    if not current_user.db_id:
        raise HTTPException(status_code=403, detail="Only authenticated users have notification settings")

    result = await db.execute(
        select(DBUser).where(DBUser.id == UUID(current_user.db_id))
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return {
        "email_notifications_enabled": user.email_notifications_enabled,
        "notification_frequency": user.notification_frequency
    }


@router.patch("/api/notifications/settings", response_model=NotificationSettings)
async def update_notification_settings(
    settings: NotificationSettings,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """Update current user's notification settings."""
    if not current_user.db_id:
        raise HTTPException(status_code=403, detail="Only authenticated users can update notification settings")

    result = await db.execute(
        select(DBUser).where(DBUser.id == UUID(current_user.db_id))
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Update settings
    user.email_notifications_enabled = settings.email_notifications_enabled
    user.notification_frequency = settings.notification_frequency

    await db.commit()
    await db.refresh(user)

    return {
        "email_notifications_enabled": user.email_notifications_enabled,
        "notification_frequency": user.notification_frequency
    }
