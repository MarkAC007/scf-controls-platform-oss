# SPDX-License-Identifier: AGPL-3.0-only
"""Pydantic v2 request/response models for the engagement evidence review board
and its dashboard (``api/engagement_evidence_reviews.py``).

Nothing here carries a comment, a comment count or anything derived from
comments: internal comments must never reach an auditor, including as counts.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

ReviewStatus = Literal["open", "in_progress", "action_required", "submitted", "accepted"]
AiVerdict = Literal["sufficient", "partial", "insufficient", "not_assessed"]


# ----------------------------------------------------------------------------
# Shared pieces
# ----------------------------------------------------------------------------

class PersonRef(BaseModel):
    user_id: UUID
    name: str


class EngagementSummary(BaseModel):
    id: UUID
    name: str
    status: str
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    frameworks: List[str] = Field(default_factory=list)


class EngagementSummaryWithCreated(EngagementSummary):
    created_at: Optional[datetime] = None


class StatusCounts(BaseModel):
    open: int = 0
    in_progress: int = 0
    action_required: int = 0
    submitted: int = 0
    accepted: int = 0


class CardControl(BaseModel):
    scf_id: str
    title: Optional[str] = None
    clauses: List[str] = Field(default_factory=list)


class CardOwner(BaseModel):
    tier: Optional[str] = None
    label: str
    team_name: Optional[str] = None
    is_tracked: bool = False
    tracking_id: Optional[UUID] = None
    assigned_user_id: Optional[UUID] = None
    user_ids: List[UUID] = Field(default_factory=list)


class EvidenceCard(BaseModel):
    review_id: UUID
    evidence_id: str
    title: Optional[str] = None
    status: ReviewStatus
    controls: List[CardControl] = Field(default_factory=list)
    clauses: List[str] = Field(default_factory=list)
    file_count: int = 0
    in_window_count: int = 0
    ai_verdict: AiVerdict = "not_assessed"
    open_query_count: int = 0
    reviewer: Optional[PersonRef] = None
    owner: CardOwner
    allowed_transitions: List[str] = Field(default_factory=list)
    note_required: Dict[str, bool] = Field(default_factory=dict)
    updated_at: Optional[datetime] = None


class CardArtifact(BaseModel):
    id: UUID
    filename: str
    uploaded_at: Optional[datetime] = None
    effective_start: Optional[date] = None
    effective_end: Optional[date] = None
    in_window: bool
    window_basis: str
    ai_status: Optional[str] = None


class EvidenceCardDetail(EvidenceCard):
    artifacts: List[CardArtifact] = Field(default_factory=list)
    decision_note: Optional[str] = None
    last_decision_at: Optional[datetime] = None
    last_decision_by: Optional[PersonRef] = None
    description: Optional[str] = None


class NoEvidenceControl(BaseModel):
    scf_id: str
    title: Optional[str] = None
    clauses: List[str] = Field(default_factory=list)
    open_query_count: int = 0


class FilterOptions(BaseModel):
    clauses: List[str] = Field(default_factory=list)
    scf_ids: List[str] = Field(default_factory=list)
    reviewers: List[PersonRef] = Field(default_factory=list)


class Viewer(BaseModel):
    user_id: Optional[UUID] = None
    is_auditor: bool
    auditor_kind: Optional[Literal["external", "internal"]] = None
    org_role: Optional[str] = None
    can_assign_reviewer: bool
    can_reassign_owner: bool


class BoardTotals(BaseModel):
    evidence: int
    controls: int


class EvidenceReviewBoard(BaseModel):
    engagement: EngagementSummary
    framework: str
    totals: BoardTotals
    counts: StatusCounts
    cards: List[EvidenceCard]
    no_evidence_controls: List[NoEvidenceControl]
    filter_options: FilterOptions
    viewer: Viewer


# ----------------------------------------------------------------------------
# Writes
# ----------------------------------------------------------------------------

class EvidenceReviewStatusChange(BaseModel):
    to_status: ReviewStatus
    # The status the caller saw. When set and the card has moved since, the
    # move is refused with 409 instead of applying to a state nobody saw.
    from_status: Optional[ReviewStatus] = None
    note: Optional[str] = None
    artifact_file_ids: List[UUID] = Field(default_factory=list)
    no_artifact_reason: Optional[str] = None


class EvidenceReviewerAssignment(BaseModel):
    user_id: Optional[UUID] = None


class ArtifactDownload(BaseModel):
    download_url: Optional[str] = None
    filename: str


# ----------------------------------------------------------------------------
# Events and people
# ----------------------------------------------------------------------------

class EvidenceReviewEventItem(BaseModel):
    id: UUID
    event_type: str
    from_status: str
    to_status: str
    actor: Optional[PersonRef] = None
    actor_kind: str
    note: Optional[str] = None
    created_at: Optional[datetime] = None


class ReviewAuditorPerson(BaseModel):
    user_id: UUID
    name: str
    kind: str


class ReviewMemberPerson(BaseModel):
    user_id: UUID
    name: str
    role: str


class ReviewPeople(BaseModel):
    auditors: List[ReviewAuditorPerson]
    members: List[ReviewMemberPerson]


# ----------------------------------------------------------------------------
# Dashboard
# ----------------------------------------------------------------------------

class StatusBlock(BaseModel):
    total: int
    by_status: StatusCounts


class ClauseRollupItem(BaseModel):
    clause: str
    group: str
    status: ReviewStatus
    control_count: int
    evidence_count: int


class ClauseGroup(BaseModel):
    key: str
    label: str
    clauses_total: int
    clauses_accepted: int


class ActionRequiredItem(BaseModel):
    evidence_id: str
    title: Optional[str] = None
    held_controls: List[str]
    held_clauses: List[str]
    open_query_count: int


class OpenQueryCounts(BaseModel):
    total: int
    evidence: int
    control: int


class TimelineItem(BaseModel):
    key: str
    label: str
    date: Optional[str] = None
    done: bool


class ReviewDashboard(BaseModel):
    engagement: EngagementSummaryWithCreated
    framework: str
    evidence: StatusBlock
    controls: StatusBlock
    clauses: StatusBlock
    clause_rollup: List[ClauseRollupItem]
    groups: List[ClauseGroup]
    action_required: List[ActionRequiredItem]
    no_evidence_controls: List[NoEvidenceControl]
    open_queries: OpenQueryCounts
    timeline: List[TimelineItem]
