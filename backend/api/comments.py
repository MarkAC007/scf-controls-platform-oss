"""
Comments API endpoints - manage comments on controls and evidence with history tracking.
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from typing import List, Optional
from uuid import UUID
from datetime import datetime

from database import get_db
from auth import require_auth, get_accessible_org_ids, User
from models import Comment, CommentHistory, User as DBUser, ScopedControl, EvidenceTracking, EvidenceCollectionTask
from schemas import (
    CommentCreate,
    CommentUpdate,
    CommentResponse,
    SuccessResponse
)
from services.notifications import create_mention_notifications
from services.audit_service import log_entity_changes, detect_action_source, get_request_id, COMMENT_TRACKED_FIELDS

router = APIRouter(
    tags=["comments"],
    dependencies=[Depends(require_auth)]
)


@router.post("/api/comments", response_model=CommentResponse, status_code=status.HTTP_201_CREATED)
async def create_comment(
    request: Request,
    comment_data: CommentCreate,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """Create a comment on a control, evidence, or task."""
    if not current_user.db_id:
        raise HTTPException(status_code=403, detail="Only authenticated users can comment")

    # Get user's accessible organisation IDs for tenant isolation
    accessible_org_ids = await get_accessible_org_ids(current_user, db)

    # Verify commentable exists AND belongs to user's accessible organisations
    # Return 404 (not 403) to avoid leaking existence of entities in other orgs
    if comment_data.commentable_type == "control":
        result = await db.execute(
            select(ScopedControl)
            .where(ScopedControl.id == comment_data.commentable_id)
            .where(ScopedControl.organization_id.in_(accessible_org_ids))
        )
        commentable = result.scalar_one_or_none()
    elif comment_data.commentable_type == "evidence":
        result = await db.execute(
            select(EvidenceTracking)
            .where(EvidenceTracking.id == comment_data.commentable_id)
            .where(EvidenceTracking.organization_id.in_(accessible_org_ids))
        )
        commentable = result.scalar_one_or_none()
    elif comment_data.commentable_type == "task":
        # Tasks inherit org access from their parent EvidenceTracking
        result = await db.execute(
            select(EvidenceCollectionTask)
            .join(EvidenceTracking, EvidenceCollectionTask.evidence_tracking_id == EvidenceTracking.id)
            .where(EvidenceCollectionTask.id == comment_data.commentable_id)
            .where(EvidenceTracking.organization_id.in_(accessible_org_ids))
        )
        commentable = result.scalar_one_or_none()
    else:
        raise HTTPException(status_code=400, detail="Invalid commentable_type. Must be 'control', 'evidence', or 'task'")

    if not commentable:
        raise HTTPException(status_code=404, detail=f"{comment_data.commentable_type.capitalize()} not found")

    # Create comment (with optional parent for threading)
    # Convert UUID objects to strings for JSONB storage
    mention_strings = [str(uid) for uid in (comment_data.mentions or [])]

    comment = Comment(
        commentable_type=comment_data.commentable_type,
        commentable_id=comment_data.commentable_id,
        user_id=UUID(current_user.db_id),
        parent_comment_id=comment_data.parent_comment_id,
        content=comment_data.content,
        mentions=mention_strings
    )
    db.add(comment)
    await db.flush()

    # Derive org_id from polymorphic parent for audit logging
    if comment_data.commentable_type == 'task':
        et_result = await db.execute(
            select(EvidenceTracking).where(EvidenceTracking.id == commentable.evidence_tracking_id)
        )
        et = et_result.scalar_one_or_none()
        audit_org_id = et.organization_id if et else None
    elif hasattr(commentable, 'organization_id'):
        audit_org_id = commentable.organization_id
    else:
        audit_org_id = None

    if audit_org_id is not None:
        new_values = {f: getattr(comment, f) for f in COMMENT_TRACKED_FIELDS if hasattr(comment, f)}
        await log_entity_changes(
            db=db,
            organization_id=audit_org_id,
            entity_type='comment',
            entity_id=comment.id,
            action='create',
            changed_by_user_id=UUID(current_user.db_id),
            old_values={},
            new_values=new_values,
            tracked_fields=COMMENT_TRACKED_FIELDS,
            action_source=detect_action_source(request),
            request_id=get_request_id(request),
        )

    await db.commit()
    await db.refresh(comment)

    # Get user data
    user_result = await db.execute(
        select(DBUser).where(DBUser.id == UUID(current_user.db_id))
    )
    user = user_result.scalar_one_or_none()

    # Create notifications for mentioned users (includes email if enabled)
    if comment_data.mentions and len(comment_data.mentions) > 0:
        # Convert to UUID objects for notification service (if they're strings from JSON)
        mention_uuids = [UUID(uid) if isinstance(uid, str) else uid for uid in comment_data.mentions]
        await create_mention_notifications(
            db=db,
            mentioned_user_ids=mention_uuids,
            comment_id=comment.id,
            commenter_id=UUID(current_user.db_id),
            commentable_type=comment_data.commentable_type,
            commentable_id=comment_data.commentable_id
        )

    return {
        "id": comment.id,
        "commentable_type": comment.commentable_type,
        "commentable_id": comment.commentable_id,
        "user_id": comment.user_id,
        "parent_comment_id": comment.parent_comment_id,
        "content": comment.content,
        "mentions": comment.mentions,
        "is_edited": comment.is_edited,
        "edited_at": comment.edited_at,
        "is_deleted": comment.is_deleted,
        "deleted_at": comment.deleted_at,
        "created_at": comment.created_at,
        "user": {
            "id": user.id,
            "email": user.email,
            "display_name": user.display_name
        } if user else None
    }


@router.get("/api/comments", response_model=List[CommentResponse])
async def list_comments(
    commentable_type: str = Query(..., regex="^(control|evidence|task)$"),
    commentable_id: UUID = Query(...),
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """List comments for a control, evidence, or task."""
    # Get user's accessible organisation IDs for tenant isolation
    accessible_org_ids = await get_accessible_org_ids(current_user, db)

    # Verify the target entity belongs to user's accessible organisations
    # Return empty list if entity not found/accessible (no information leakage)
    if commentable_type == "control":
        entity_result = await db.execute(
            select(ScopedControl)
            .where(ScopedControl.id == commentable_id)
            .where(ScopedControl.organization_id.in_(accessible_org_ids))
        )
        entity = entity_result.scalar_one_or_none()
    elif commentable_type == "evidence":
        entity_result = await db.execute(
            select(EvidenceTracking)
            .where(EvidenceTracking.id == commentable_id)
            .where(EvidenceTracking.organization_id.in_(accessible_org_ids))
        )
        entity = entity_result.scalar_one_or_none()
    elif commentable_type == "task":
        entity_result = await db.execute(
            select(EvidenceCollectionTask)
            .join(EvidenceTracking, EvidenceCollectionTask.evidence_tracking_id == EvidenceTracking.id)
            .where(EvidenceCollectionTask.id == commentable_id)
            .where(EvidenceTracking.organization_id.in_(accessible_org_ids))
        )
        entity = entity_result.scalar_one_or_none()
    else:
        entity = None

    # Return empty list if entity not found/accessible
    if not entity:
        return []

    result = await db.execute(
        select(Comment)
        .where(
            and_(
                Comment.commentable_type == commentable_type,
                Comment.commentable_id == commentable_id,
                Comment.is_deleted == False
            )
        )
        .order_by(Comment.created_at.asc())
    )
    comments = result.scalars().all()

    # Eagerly load user data
    comment_list = []
    for comment in comments:
        user_result = await db.execute(
            select(DBUser).where(DBUser.id == comment.user_id)
        )
        user = user_result.scalar_one_or_none()

        comment_dict = {
            "id": comment.id,
            "commentable_type": comment.commentable_type,
            "commentable_id": comment.commentable_id,
            "user_id": comment.user_id,
            "parent_comment_id": comment.parent_comment_id,
            "content": comment.content,
            "mentions": comment.mentions,
            "is_edited": comment.is_edited,
            "edited_at": comment.edited_at,
            "is_deleted": comment.is_deleted,
            "deleted_at": comment.deleted_at,
            "created_at": comment.created_at,
            "user": {
                "id": user.id,
                "email": user.email,
                "display_name": user.display_name
            } if user else None
        }
        comment_list.append(comment_dict)

    return comment_list


async def _verify_comment_access(
    comment: Comment,
    accessible_org_ids: list[UUID],
    db: AsyncSession
) -> bool:
    """Verify that a comment's target entity belongs to user's accessible organisations."""
    if comment.commentable_type == "control":
        result = await db.execute(
            select(ScopedControl)
            .where(ScopedControl.id == comment.commentable_id)
            .where(ScopedControl.organization_id.in_(accessible_org_ids))
        )
        return result.scalar_one_or_none() is not None
    elif comment.commentable_type == "evidence":
        result = await db.execute(
            select(EvidenceTracking)
            .where(EvidenceTracking.id == comment.commentable_id)
            .where(EvidenceTracking.organization_id.in_(accessible_org_ids))
        )
        return result.scalar_one_or_none() is not None
    elif comment.commentable_type == "task":
        result = await db.execute(
            select(EvidenceCollectionTask)
            .join(EvidenceTracking, EvidenceCollectionTask.evidence_tracking_id == EvidenceTracking.id)
            .where(EvidenceCollectionTask.id == comment.commentable_id)
            .where(EvidenceTracking.organization_id.in_(accessible_org_ids))
        )
        return result.scalar_one_or_none() is not None
    return False


@router.patch("/api/comments/{comment_id}", response_model=CommentResponse)
async def update_comment(
    request: Request,
    comment_id: UUID,
    comment_update: CommentUpdate,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """Edit a comment (only by the author)."""
    if not current_user.db_id:
        raise HTTPException(status_code=403, detail="Only authenticated users can edit comments")

    # Get user's accessible organisation IDs for tenant isolation
    accessible_org_ids = await get_accessible_org_ids(current_user, db)

    result = await db.execute(
        select(Comment).where(Comment.id == comment_id)
    )
    comment = result.scalar_one_or_none()

    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    # Verify the comment's target entity belongs to user's accessible organisations
    # Return 404 (not 403) to avoid leaking existence of comments in other orgs
    if not await _verify_comment_access(comment, accessible_org_ids, db):
        raise HTTPException(status_code=404, detail="Comment not found")

    # Verify user is the author
    if str(comment.user_id) != current_user.db_id:
        raise HTTPException(status_code=403, detail="You can only edit your own comments")

    # Capture old values for audit logging before mutation
    old_values = {f: getattr(comment, f) for f in COMMENT_TRACKED_FIELDS if hasattr(comment, f)}

    # Save history
    history = CommentHistory(
        comment_id=comment.id,
        old_content=comment.content,
        edited_by_user_id=UUID(current_user.db_id)
    )
    db.add(history)

    # Update comment
    comment.content = comment_update.content
    if comment_update.mentions is not None:
        # Convert UUID objects to strings for JSONB storage
        comment.mentions = [str(uid) for uid in comment_update.mentions]
    comment.is_edited = True
    comment.edited_at = datetime.utcnow()

    # Derive org_id from polymorphic parent for audit logging
    if comment.commentable_type == 'task':
        task_result = await db.execute(
            select(EvidenceCollectionTask).where(EvidenceCollectionTask.id == comment.commentable_id)
        )
        task_entity = task_result.scalar_one_or_none()
        if task_entity:
            et_result = await db.execute(
                select(EvidenceTracking).where(EvidenceTracking.id == task_entity.evidence_tracking_id)
            )
            et = et_result.scalar_one_or_none()
            audit_org_id = et.organization_id if et else None
        else:
            audit_org_id = None
    elif comment.commentable_type == 'control':
        sc_result = await db.execute(
            select(ScopedControl).where(ScopedControl.id == comment.commentable_id)
        )
        sc = sc_result.scalar_one_or_none()
        audit_org_id = sc.organization_id if sc else None
    elif comment.commentable_type == 'evidence':
        ev_result = await db.execute(
            select(EvidenceTracking).where(EvidenceTracking.id == comment.commentable_id)
        )
        ev = ev_result.scalar_one_or_none()
        audit_org_id = ev.organization_id if ev else None
    else:
        audit_org_id = None

    if audit_org_id is not None:
        new_values = {f: getattr(comment, f) for f in COMMENT_TRACKED_FIELDS if hasattr(comment, f)}
        await log_entity_changes(
            db=db,
            organization_id=audit_org_id,
            entity_type='comment',
            entity_id=comment.id,
            action='update',
            changed_by_user_id=UUID(current_user.db_id),
            old_values=old_values,
            new_values=new_values,
            tracked_fields=COMMENT_TRACKED_FIELDS,
            action_source=detect_action_source(request),
            request_id=get_request_id(request),
        )

    await db.commit()
    await db.refresh(comment)

    # Get user data
    user_result = await db.execute(
        select(DBUser).where(DBUser.id == comment.user_id)
    )
    user = user_result.scalar_one_or_none()

    return {
        "id": comment.id,
        "commentable_type": comment.commentable_type,
        "commentable_id": comment.commentable_id,
        "user_id": comment.user_id,
        "parent_comment_id": comment.parent_comment_id,
        "content": comment.content,
        "mentions": comment.mentions,
        "is_edited": comment.is_edited,
        "edited_at": comment.edited_at,
        "is_deleted": comment.is_deleted,
        "deleted_at": comment.deleted_at,
        "created_at": comment.created_at,
        "user": {
            "id": user.id,
            "email": user.email,
            "display_name": user.display_name
        } if user else None
    }


@router.delete("/api/comments/{comment_id}", response_model=SuccessResponse)
async def delete_comment(
    request: Request,
    comment_id: UUID,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """Soft delete a comment (only by the author)."""
    if not current_user.db_id:
        raise HTTPException(status_code=403, detail="Only authenticated users can delete comments")

    # Get user's accessible organisation IDs for tenant isolation
    accessible_org_ids = await get_accessible_org_ids(current_user, db)

    result = await db.execute(
        select(Comment).where(Comment.id == comment_id)
    )
    comment = result.scalar_one_or_none()

    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    # Verify the comment's target entity belongs to user's accessible organisations
    # Return 404 (not 403) to avoid leaking existence of comments in other orgs
    if not await _verify_comment_access(comment, accessible_org_ids, db):
        raise HTTPException(status_code=404, detail="Comment not found")

    # Verify user is the author
    if str(comment.user_id) != current_user.db_id:
        raise HTTPException(status_code=403, detail="You can only delete your own comments")

    # Capture old values for audit logging before soft delete
    old_values = {f: getattr(comment, f) for f in COMMENT_TRACKED_FIELDS if hasattr(comment, f)}

    # Soft delete
    comment.is_deleted = True
    comment.deleted_at = datetime.utcnow()

    # Derive org_id from polymorphic parent for audit logging
    if comment.commentable_type == 'task':
        task_result = await db.execute(
            select(EvidenceCollectionTask).where(EvidenceCollectionTask.id == comment.commentable_id)
        )
        task_entity = task_result.scalar_one_or_none()
        if task_entity:
            et_result = await db.execute(
                select(EvidenceTracking).where(EvidenceTracking.id == task_entity.evidence_tracking_id)
            )
            et = et_result.scalar_one_or_none()
            audit_org_id = et.organization_id if et else None
        else:
            audit_org_id = None
    elif comment.commentable_type == 'control':
        sc_result = await db.execute(
            select(ScopedControl).where(ScopedControl.id == comment.commentable_id)
        )
        sc = sc_result.scalar_one_or_none()
        audit_org_id = sc.organization_id if sc else None
    elif comment.commentable_type == 'evidence':
        ev_result = await db.execute(
            select(EvidenceTracking).where(EvidenceTracking.id == comment.commentable_id)
        )
        ev = ev_result.scalar_one_or_none()
        audit_org_id = ev.organization_id if ev else None
    else:
        audit_org_id = None

    if audit_org_id is not None:
        await log_entity_changes(
            db=db,
            organization_id=audit_org_id,
            entity_type='comment',
            entity_id=comment.id,
            action='delete',
            changed_by_user_id=UUID(current_user.db_id),
            old_values=old_values,
            new_values={},
            tracked_fields=COMMENT_TRACKED_FIELDS,
            action_source=detect_action_source(request),
            request_id=get_request_id(request),
        )

    await db.commit()

    return SuccessResponse(message="Comment deleted successfully")


@router.get("/api/comments/{comment_id}/history", response_model=List[dict])
async def get_comment_history(
    comment_id: UUID,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """Get edit history for a comment."""
    # Get user's accessible organisation IDs for tenant isolation
    accessible_org_ids = await get_accessible_org_ids(current_user, db)

    # Verify comment exists
    result = await db.execute(
        select(Comment).where(Comment.id == comment_id)
    )
    comment = result.scalar_one_or_none()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    # Verify the comment's target entity belongs to user's accessible organisations
    # Return 404 (not 403) to avoid leaking existence of comments in other orgs
    if not await _verify_comment_access(comment, accessible_org_ids, db):
        raise HTTPException(status_code=404, detail="Comment not found")

    # Get history
    result = await db.execute(
        select(CommentHistory)
        .where(CommentHistory.comment_id == comment_id)
        .order_by(CommentHistory.edited_at.desc())
    )
    history_items = result.scalars().all()

    # Eagerly load user data
    history_list = []
    for item in history_items:
        user_result = await db.execute(
            select(DBUser).where(DBUser.id == item.edited_by_user_id)
        )
        user = user_result.scalar_one_or_none()

        history_dict = {
            "id": item.id,
            "comment_id": item.comment_id,
            "old_content": item.old_content,
            "edited_at": item.edited_at,
            "edited_by": {
                "id": user.id,
                "email": user.email,
                "display_name": user.display_name
            } if user else None
        }
        history_list.append(history_dict)

    return history_list
