"""
Discussion thread on an engagement evidence review (the card).

Base path (the lead mounts this router under ``/api``):
    /organizations/{org_id}/engagements/{engagement_id}/evidence-reviews/{evidence_id}/comments

Rows live in the polymorphic ``comments`` table with
``commentable_type = EngagementEvidenceReview.COMMENTABLE_TYPE`` and
``commentable_id = review.id``. These routes are the ONLY way to reach them:
the org-level ``/api/comments`` routes refuse this type.

Visibility. ``shared`` is seen by everyone with engagement access. ``internal``
is an org-only note: only callers for whom ``can_see_internal`` is true may see
it. On reads, and on edit, delete or reply to an existing internal note, it
does not exist for everyone else: lists, counts and history leave it out, and
a lookup by id is 404, identical to an id that was never issued. Writing a new
internal note is the one 403: a grant holder (an external auditor, or an
internal auditor who is also an org member) who posts with
``visibility = internal`` is refused with 403; no existing note is revealed by
that. Mentions in an internal note are limited to
people who can see internal.
"""
import logging
from datetime import datetime
from typing import Dict, Iterable, List, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import (
    Comment,
    CommentHistory,
    EngagementAuditor,
    EngagementAuditorStatus,
    EngagementEvidenceReview,
    OrganizationMember,
    User as DBUser,
)
from api.audit_engagements import EngagementAccess, can_see_internal, require_engagement_read
from services.audit_service import (
    COMMENT_TRACKED_FIELDS,
    detect_action_source,
    get_request_id,
    log_entity_changes,
)
from services.notifications import create_engagement_mention_notifications

logger = logging.getLogger(__name__)

router = APIRouter(tags=["engagement_comments"])

BASE = "/organizations/{org_id}/engagements/{engagement_id}/evidence-reviews/{evidence_id}/comments"

SHARED = Comment.VISIBILITY_SHARED
INTERNAL = Comment.VISIBILITY_INTERNAL
COMMENTABLE_TYPE = EngagementEvidenceReview.COMMENTABLE_TYPE

_NOT_FOUND = "Comment not found"
UNNAMED = "Unnamed user"


# =============================================================================
# Request / response shapes (local: schemas.py is outside this module's remit)
# =============================================================================

class EngagementCommentCreate(BaseModel):
    content: str = Field(..., min_length=1)
    visibility: Optional[Literal["shared", "internal"]] = None  # a reply inherits its parent's
    parent_comment_id: Optional[UUID] = None
    mentions: Optional[List[UUID]] = None

    @field_validator("content")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Comment content cannot be blank.")
        return value


class EngagementCommentUpdate(BaseModel):
    # Content only. Visibility is fixed at creation; sending it is a 422.
    model_config = ConfigDict(extra="forbid")

    content: str = Field(..., min_length=1)

    @field_validator("content")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Comment content cannot be blank.")
        return value


class CommentPerson(BaseModel):
    user_id: Optional[UUID] = None
    name: Optional[str] = None


class EngagementCommentItem(BaseModel):
    id: UUID
    parent_comment_id: Optional[UUID] = None
    author: CommentPerson
    content: str
    visibility: str
    mentions: List[CommentPerson] = []
    is_edited: bool = False
    edited_at: Optional[datetime] = None
    is_deleted: bool = False
    created_at: Optional[datetime] = None
    can_edit: bool = False
    can_delete: bool = False


class EngagementCommentHistoryItem(BaseModel):
    id: UUID
    old_content: str
    edited_at: Optional[datetime] = None
    edited_by: CommentPerson


# =============================================================================
# Data access (small, single-purpose; the visibility rule is applied in Python
# by the callers below so it lives in exactly one place)
# =============================================================================

async def _load_review(db: AsyncSession, org_id: UUID, engagement_id: UUID, evidence_id: str):
    result = await db.execute(
        select(EngagementEvidenceReview).where(
            and_(
                EngagementEvidenceReview.organization_id == org_id,
                EngagementEvidenceReview.engagement_id == engagement_id,
                EngagementEvidenceReview.evidence_id == evidence_id,
            )
        )
    )
    return result.scalar_one_or_none()


async def _load_thread(db: AsyncSession, review_id: UUID) -> List[Comment]:
    """Every comment on the review, both visibilities, deleted included."""
    result = await db.execute(
        select(Comment)
        .where(
            and_(
                Comment.commentable_type == COMMENTABLE_TYPE,
                Comment.commentable_id == review_id,
            )
        )
        .order_by(Comment.created_at.asc())
    )
    return list(result.scalars().all())


async def _load_comment(db: AsyncSession, review_id: UUID, comment_id: UUID) -> Optional[Comment]:
    """A comment by id, only if it belongs to THIS review."""
    result = await db.execute(
        select(Comment).where(
            and_(
                Comment.id == comment_id,
                Comment.commentable_type == COMMENTABLE_TYPE,
                Comment.commentable_id == review_id,
            )
        )
    )
    return result.scalar_one_or_none()


async def _load_history(db: AsyncSession, comment_id: UUID) -> List[CommentHistory]:
    result = await db.execute(
        select(CommentHistory)
        .where(CommentHistory.comment_id == comment_id)
        .order_by(CommentHistory.edited_at.desc())
    )
    return list(result.scalars().all())


async def _user_names(db: AsyncSession, user_ids: Iterable[UUID]) -> Dict[UUID, str]:
    ids = {uid for uid in user_ids if uid is not None}
    if not ids:
        return {}
    # Display name only, never the email address: an external auditor reads
    # this thread, and the organisation reads the auditor's comments.
    result = await db.execute(
        select(DBUser.id, DBUser.display_name).where(DBUser.id.in_(ids))
    )
    return {uid: (display_name or UNNAMED) for uid, display_name in result.fetchall()}


async def _engagement_people(db: AsyncSession, org_id: UUID, engagement_id: UUID) -> tuple:
    """(org member user ids, user ids holding an ACTIVE grant on this engagement)."""
    members_result = await db.execute(
        select(OrganizationMember.user_id).where(OrganizationMember.organization_id == org_id)
    )
    grants_result = await db.execute(
        select(EngagementAuditor.user_id).where(
            and_(
                EngagementAuditor.engagement_id == engagement_id,
                EngagementAuditor.status == EngagementAuditorStatus.ACTIVE.value,
            )
        )
    )
    return (
        {row[0] for row in members_result.fetchall()},
        {row[0] for row in grants_result.fetchall()},
    )


# =============================================================================
# Rules
# =============================================================================

def _caller_id(access: EngagementAccess) -> Optional[UUID]:
    return UUID(access.user.db_id) if access.user and access.user.db_id else None


def _visible(comment: Comment, sees_internal: bool) -> bool:
    return sees_internal or (comment.visibility or SHARED) == SHARED


def _is_moderator(access: EngagementAccess, sees_internal: bool) -> bool:
    """An org admin acting as the organisation (no auditor grant) may remove
    anyone's comment. Anyone holding a grant can delete only their own."""
    return sees_internal and access.role == "admin"


async def _context(db, access, org_id, engagement_id, evidence_id):
    review = await _load_review(db, org_id, engagement_id, evidence_id)
    if review is None:
        raise HTTPException(status_code=404, detail="Evidence review not found")
    sees_internal = await can_see_internal(db, access)
    return review, sees_internal


async def _visible_comment_or_404(db, review, comment_id, sees_internal) -> Comment:
    comment = await _load_comment(db, review.id, comment_id)
    if comment is None or not _visible(comment, sees_internal):
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return comment


def _mention_ids(comment: Comment) -> List[UUID]:
    out = []
    for raw in comment.mentions or []:
        try:
            out.append(UUID(str(raw)))
        except ValueError:
            continue
    return out


def _serialize(comment: Comment, names: Dict[UUID, str], caller_id, moderator: bool) -> EngagementCommentItem:
    deleted = bool(comment.is_deleted)
    is_author = caller_id is not None and comment.user_id == caller_id
    return EngagementCommentItem(
        id=comment.id,
        parent_comment_id=comment.parent_comment_id,
        author=CommentPerson(user_id=comment.user_id, name=names.get(comment.user_id)),
        # A soft-deleted comment keeps its place in the thread, blank.
        content="" if deleted else comment.content,
        visibility=comment.visibility or SHARED,
        mentions=[] if deleted else [
            CommentPerson(user_id=uid, name=names.get(uid)) for uid in _mention_ids(comment)
        ],
        is_edited=bool(comment.is_edited),
        edited_at=comment.edited_at,
        is_deleted=deleted,
        created_at=comment.created_at,
        can_edit=is_author and not deleted,
        can_delete=(is_author or moderator) and not deleted,
    )


# The organisation's audit log is readable by every member, and a member who
# holds an internal-auditor grant is one of them. So the text of an internal
# note is never written there: the log records that the note was created,
# edited or deleted, and nothing of what it said. (The audit-log route also
# hides those rows from anyone holding a grant on the engagement.)
_INTERNAL_AUDIT_FIELDS = frozenset(COMMENT_TRACKED_FIELDS) - {"content"}


def _audit_fields(comment: Comment) -> set:
    if (comment.visibility or SHARED) == INTERNAL:
        return set(_INTERNAL_AUDIT_FIELDS)
    return set(COMMENT_TRACKED_FIELDS)


async def _audit(db, request, org_id, comment, action, actor_id, old_values, new_values):
    await log_entity_changes(
        db=db, organization_id=org_id, entity_type="comment",
        entity_id=comment.id, action=action, changed_by_user_id=actor_id,
        old_values=old_values, new_values=new_values,
        tracked_fields=_audit_fields(comment),
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )


def _snapshot(comment: Comment) -> dict:
    return {f: getattr(comment, f) for f in _audit_fields(comment) if hasattr(comment, f)}


# =============================================================================
# Routes
# =============================================================================

@router.get(BASE, response_model=List[EngagementCommentItem])
async def list_review_comments(
    org_id: UUID,
    engagement_id: UUID,
    evidence_id: str,
    visibility: Literal["shared", "internal"] = Query("shared"),
    access: EngagementAccess = Depends(require_engagement_read()),
    db: AsyncSession = Depends(get_db),
):
    """The thread for one visibility, oldest first."""
    review, sees_internal = await _context(db, access, org_id, engagement_id, evidence_id)
    if visibility == INTERNAL and not sees_internal:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)

    thread = [c for c in await _load_thread(db, review.id)
              if (c.visibility or SHARED) == visibility and _visible(c, sees_internal)]
    names = await _user_names(
        db, [c.user_id for c in thread] + [uid for c in thread for uid in _mention_ids(c)]
    )
    moderator = _is_moderator(access, sees_internal)
    caller_id = _caller_id(access)
    return [_serialize(c, names, caller_id, moderator) for c in thread]


@router.get(BASE + "/counts")
async def count_review_comments(
    org_id: UUID,
    engagement_id: UUID,
    evidence_id: str,
    access: EngagementAccess = Depends(require_engagement_read()),
    db: AsyncSession = Depends(get_db),
):
    """``{"shared": n}``, plus ``"internal": m`` only for someone who can see it.
    The key is absent (not zero) for an auditor."""
    review, sees_internal = await _context(db, access, org_id, engagement_id, evidence_id)
    live = [c for c in await _load_thread(db, review.id) if not c.is_deleted]
    counts = {"shared": sum(1 for c in live if (c.visibility or SHARED) == SHARED)}
    if sees_internal:
        counts["internal"] = sum(1 for c in live if c.visibility == INTERNAL)
    return counts


@router.post(BASE, response_model=EngagementCommentItem, status_code=201)
async def create_review_comment(
    org_id: UUID,
    engagement_id: UUID,
    evidence_id: str,
    payload: EngagementCommentCreate,
    request: Request,
    access: EngagementAccess = Depends(require_engagement_read()),
    db: AsyncSession = Depends(get_db),
):
    review, sees_internal = await _context(db, access, org_id, engagement_id, evidence_id)
    caller_id = _caller_id(access)
    if caller_id is None:
        raise HTTPException(status_code=403, detail="A user account is required to comment.")

    if payload.parent_comment_id is not None:
        parent = await _visible_comment_or_404(db, review, payload.parent_comment_id, sees_internal)
        parent_visibility = parent.visibility or SHARED
        if payload.visibility is not None and payload.visibility != parent_visibility:
            raise HTTPException(
                status_code=422,
                detail=f"A reply takes its parent's visibility ('{parent_visibility}').",
            )
        visibility = parent_visibility
    else:
        visibility = payload.visibility or SHARED

    if visibility == INTERNAL and not sees_internal:
        raise HTTPException(status_code=403, detail="Internal notes are for the organisation only.")

    # Mentions: only people with access to this engagement; for an internal
    # note, only people who can see internal (members holding no grant).
    mentioned = list(dict.fromkeys(payload.mentions or []))
    if mentioned:
        members, grant_holders = await _engagement_people(db, org_id, engagement_id)
        allowed = (members - grant_holders) if visibility == INTERNAL else (members | grant_holders)
        rejected = [uid for uid in mentioned if uid not in allowed]
        if rejected:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "These people cannot be mentioned on this comment.",
                    "rejected_user_ids": [str(uid) for uid in rejected],
                },
            )

    comment = Comment(
        commentable_type=COMMENTABLE_TYPE,
        commentable_id=review.id,
        user_id=caller_id,
        parent_comment_id=payload.parent_comment_id,
        content=payload.content,
        mentions=[str(uid) for uid in mentioned],
        visibility=visibility,
        is_edited=False,
        is_deleted=False,
    )
    db.add(comment)
    await db.flush()
    await _audit(db, request, org_id, comment, "create", caller_id, {}, _snapshot(comment))
    await db.commit()
    await db.refresh(comment)

    if mentioned:
        await create_engagement_mention_notifications(
            db,
            organization_id=org_id,
            engagement_id=engagement_id,
            review_id=review.id,
            evidence_id=review.evidence_id,
            comment_id=comment.id,
            author_user_id=caller_id,
            mentioned_user_ids=mentioned,
        )

    names = await _user_names(db, [caller_id] + mentioned)
    return _serialize(comment, names, caller_id, _is_moderator(access, sees_internal))


@router.patch(BASE + "/{comment_id}", response_model=EngagementCommentItem)
async def update_review_comment(
    org_id: UUID,
    engagement_id: UUID,
    evidence_id: str,
    comment_id: UUID,
    payload: EngagementCommentUpdate,
    request: Request,
    access: EngagementAccess = Depends(require_engagement_read()),
    db: AsyncSession = Depends(get_db),
):
    """Author only. Keeps the previous content in comment_history."""
    review, sees_internal = await _context(db, access, org_id, engagement_id, evidence_id)
    comment = await _visible_comment_or_404(db, review, comment_id, sees_internal)
    caller_id = _caller_id(access)
    if caller_id is None or comment.user_id != caller_id:
        raise HTTPException(status_code=403, detail="You can only edit your own comments")
    if comment.is_deleted:
        raise HTTPException(status_code=409, detail="A deleted comment cannot be edited.")

    old_values = _snapshot(comment)
    db.add(CommentHistory(comment_id=comment.id, old_content=comment.content, edited_by_user_id=caller_id))
    comment.content = payload.content
    comment.is_edited = True
    comment.edited_at = datetime.utcnow()
    await _audit(db, request, org_id, comment, "update", caller_id, old_values, _snapshot(comment))
    await db.commit()
    await db.refresh(comment)

    names = await _user_names(db, [comment.user_id] + _mention_ids(comment))
    return _serialize(comment, names, caller_id, _is_moderator(access, sees_internal))


@router.delete(BASE + "/{comment_id}", status_code=204)
async def delete_review_comment(
    org_id: UUID,
    engagement_id: UUID,
    evidence_id: str,
    comment_id: UUID,
    request: Request,
    access: EngagementAccess = Depends(require_engagement_read()),
    db: AsyncSession = Depends(get_db),
):
    """Soft delete: the author, or an org admin acting as the organisation."""
    review, sees_internal = await _context(db, access, org_id, engagement_id, evidence_id)
    comment = await _visible_comment_or_404(db, review, comment_id, sees_internal)
    caller_id = _caller_id(access)
    is_author = caller_id is not None and comment.user_id == caller_id
    if not (is_author or _is_moderator(access, sees_internal)):
        raise HTTPException(status_code=403, detail="You can only delete your own comments")

    if not comment.is_deleted:
        old_values = _snapshot(comment)
        comment.is_deleted = True
        comment.deleted_at = datetime.utcnow()
        if caller_id is not None:
            await _audit(db, request, org_id, comment, "delete", caller_id, old_values, {})
        await db.commit()
    return Response(status_code=204)


@router.get(BASE + "/{comment_id}/history", response_model=List[EngagementCommentHistoryItem])
async def review_comment_history(
    org_id: UUID,
    engagement_id: UUID,
    evidence_id: str,
    comment_id: UUID,
    access: EngagementAccess = Depends(require_engagement_read()),
    db: AsyncSession = Depends(get_db),
):
    """Previous versions, newest first. Empty for a deleted comment, whose
    content the thread no longer shows either."""
    review, sees_internal = await _context(db, access, org_id, engagement_id, evidence_id)
    comment = await _visible_comment_or_404(db, review, comment_id, sees_internal)
    if comment.is_deleted:
        return []
    items = await _load_history(db, comment.id)
    names = await _user_names(db, [h.edited_by_user_id for h in items])
    return [
        EngagementCommentHistoryItem(
            id=h.id,
            old_content=h.old_content,
            edited_at=h.edited_at,
            edited_by=CommentPerson(user_id=h.edited_by_user_id, name=names.get(h.edited_by_user_id)),
        )
        for h in items
    ]
