# SPDX-License-Identifier: AGPL-3.0-only
"""Engagement evidence review board and dashboard API.

The card is the evidence object (SCF ERL id). It maps to the engagement's
in-scope, non-retired controls and, through them, to the clauses of the
framework chosen in the tab. Columns are review stages; collection state
(files, in-window counts, AI verdict) is on the card.

Every route sits behind ``require_engagement_read`` (org member viewer+, or an
auditor with an ACTIVE grant). The card list and the dashboard share one
loader (``_load_board_data``) and one card builder, so the board and the
dashboard can never disagree.

No comment, comment count or anything derived from comments is returned by
this module: internal comments must never reach an auditor. The only comment
work here is writing the one shared response comment when the organisation
submits evidence back to the auditor.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Set
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.audit_engagements import (
    EngagementAccess,
    _access_user_id,
    _fetch_evidence_by_scf,
    _not_retired,
    require_engagement_read,
)
from api.evidence_files import _proxy_download_url
from catalog_models import SCFCatalogControl, SCFCatalogEvidence
from database import get_db
from models import (
    AuditEngagement,
    Comment,
    EngagementAuditor,
    EngagementAuditorStatus,
    EngagementControlScope,
    EngagementEvidenceReview,
    EngagementEvidenceReviewEvent,
    EngagementQuery,
    EngagementQueryStatus,
    EvidenceAssessment,
    EvidenceFile,
    OrganizationMember,
    ScopeStatus,
    TeamMember,
    User as DBUser,
)
from schemas_engagement_review import (
    ArtifactDownload,
    EvidenceCardDetail,
    EvidenceReviewBoard,
    EvidenceReviewerAssignment,
    EvidenceReviewEventItem,
    EvidenceReviewStatusChange,
    ReviewDashboard,
    ReviewPeople,
)
from services import engagement_evidence_owner as owner_service
from services import engagement_evidence_review as review_service
from services import notifications as notification_service
from services.engagement_evidence_review import (
    ACTOR_AUDITOR,
    ACTOR_ORG,
    STATUSES,
    ReviewActor,
    TransitionError,
)
from services.engagement_presentation import _in_window, natural_clause_sort_key
from services.engagement_rollup import build_rollup, count_statuses

logger = logging.getLogger(__name__)

router = APIRouter(tags=["engagement_evidence_reviews"])

BASE = "/organizations/{org_id}/engagements/{engagement_id}"

AI_VERDICTS = ("sufficient", "partial", "insufficient", "not_assessed")
# Worst wins. Statuses outside this map (pending, processing, unassessable,
# error) and files with no assessment row do not count towards the verdict.
_VERDICT_RANK = {"sufficient": 1, "partial": 2, "insufficient": 3}

UNNAMED = "Unnamed user"


# =============================================================================
# Loading (shared by the board and the dashboard)
# =============================================================================

@dataclass
class BoardData:
    engagement: Any
    # scf_id -> {"scf_id", "title", "mappings": {framework: [clause ids]}}
    controls: Dict[str, Dict[str, Any]]
    # evidence_id -> {"title"}
    evidence_meta: Dict[str, Dict[str, Any]]
    # evidence_id -> sorted in-scope scf_ids it supports
    evidence_controls: Dict[str, List[str]]
    # evidence_id -> review row
    reviews: Dict[str, Any]
    # evidence_id -> [artifact dicts as _fetch_evidence_by_scf returns them]
    files_by_evidence: Dict[str, List[Dict[str, Any]]]
    # file id -> EvidenceAssessment.status
    assessment_by_file: Dict[Any, str]
    open_queries_by_evidence: Dict[str, int]
    open_queries_by_scf: Dict[str, int]
    owners: Dict[str, Any]
    user_names: Dict[Any, str]
    first_transition_at: Optional[datetime] = None
    last_transition_at: Optional[datetime] = None
    # Control-level (evidence_id NULL) query counts by status other than open;
    # they decide whether a no-evidence control rolls up as accepted.
    closed_queries_by_scf: Dict[str, int] = field(default_factory=dict)
    answered_queries_by_scf: Dict[str, int] = field(default_factory=dict)


def _name(display_name: Optional[str]) -> str:
    # Never fall back to the email address: auditors read these responses.
    return display_name or UNNAMED


async def _user_names(db: AsyncSession, user_ids: Set[Any]) -> Dict[Any, str]:
    ids = {u for u in user_ids if u}
    if not ids:
        return {}
    result = await db.execute(
        select(DBUser.id, DBUser.display_name).where(DBUser.id.in_(ids))
    )
    return {uid: _name(display_name) for uid, display_name in result.fetchall()}


async def _load_engagement(db: AsyncSession, org_id: UUID, engagement_id: UUID):
    result = await db.execute(
        select(AuditEngagement).where(
            and_(
                AuditEngagement.organization_id == org_id,
                AuditEngagement.id == engagement_id,
            )
        )
    )
    engagement = result.scalar_one_or_none()
    if engagement is None:
        raise HTTPException(status_code=404, detail="Audit engagement not found")
    return engagement


async def _load_board_data(db: AsyncSession, org_id: UUID, engagement_id: UUID) -> BoardData:
    """Every read the board and the dashboard need, in a fixed number of bulk
    queries whatever the card count."""
    engagement = await _load_engagement(db, org_id, engagement_id)

    # A control added to scope later gets its cards here.
    inserted = await review_service.materialise_reviews(db, engagement_id, org_id)
    if inserted:
        await db.commit()

    scope_result = await db.execute(
        select(
            EngagementControlScope.scf_id,
            SCFCatalogControl.control_name,
            SCFCatalogControl.framework_mappings,
        )
        .outerjoin(SCFCatalogControl, EngagementControlScope.scf_id == SCFCatalogControl.scf_id)
        .where(
            EngagementControlScope.engagement_id == engagement_id,
            EngagementControlScope.scope_status == ScopeStatus.IN_SCOPE.value,
            _not_retired(),
        )
    )
    controls = {
        scf_id: {"scf_id": scf_id, "title": name, "mappings": mappings or {}}
        for scf_id, name, mappings in scope_result.fetchall()
    }

    evidence_by_scf, evidence_objects_by_scf = await _fetch_evidence_by_scf(
        db, org_id, set(controls)
    )

    evidence_meta: Dict[str, Dict[str, Any]] = {}
    evidence_controls: Dict[str, Set[str]] = {}
    for scf, objects in evidence_objects_by_scf.items():
        for obj in objects:
            eid = obj["evidence_id"]
            evidence_meta.setdefault(eid, {"title": obj.get("artifact_title")})
            evidence_controls.setdefault(eid, set()).add(scf)

    files_by_evidence: Dict[str, List[Dict[str, Any]]] = {}
    seen_files: Set[Any] = set()
    for artifacts in evidence_by_scf.values():
        for art in artifacts:
            if art["id"] in seen_files:
                continue
            seen_files.add(art["id"])
            files_by_evidence.setdefault(art["evidence_id"], []).append(art)

    review_result = await db.execute(
        select(EngagementEvidenceReview).where(
            EngagementEvidenceReview.engagement_id == engagement_id,
            EngagementEvidenceReview.organization_id == org_id,
        )
    )
    reviews = {r.evidence_id: r for r in review_result.scalars().all()}

    assessment_by_file: Dict[Any, str] = {}
    if seen_files:
        assessment_result = await db.execute(
            select(EvidenceAssessment.evidence_file_id, EvidenceAssessment.status).where(
                EvidenceAssessment.organization_id == org_id,
                EvidenceAssessment.evidence_file_id.in_(seen_files),
            )
        )
        assessment_by_file = dict(assessment_result.fetchall())

    # One grouped query for every status: open counts feed the cards; closed
    # and answered control-level counts decide no-evidence controls.
    query_result = await db.execute(
        select(
            EngagementQuery.evidence_id, EngagementQuery.scf_id,
            EngagementQuery.status, func.count(),
        )
        .where(EngagementQuery.engagement_id == engagement_id)
        .group_by(EngagementQuery.evidence_id, EngagementQuery.scf_id, EngagementQuery.status)
    )
    open_queries_by_evidence: Dict[str, int] = {}
    open_queries_by_scf: Dict[str, int] = {}
    closed_queries_by_scf: Dict[str, int] = {}
    answered_queries_by_scf: Dict[str, int] = {}
    by_status = {
        EngagementQueryStatus.OPEN.value: open_queries_by_scf,
        EngagementQueryStatus.CLOSED.value: closed_queries_by_scf,
        EngagementQueryStatus.ANSWERED.value: answered_queries_by_scf,
    }
    for evidence_id, scf_id, status, n in query_result.fetchall():
        if evidence_id:
            if status == EngagementQueryStatus.OPEN.value:
                open_queries_by_evidence[evidence_id] = open_queries_by_evidence.get(evidence_id, 0) + n
        elif scf_id and status in by_status:
            bucket = by_status[status]
            bucket[scf_id] = bucket.get(scf_id, 0) + n

    event_result = await db.execute(
        select(
            func.min(EngagementEvidenceReviewEvent.created_at),
            func.max(EngagementEvidenceReviewEvent.created_at),
        ).where(
            EngagementEvidenceReviewEvent.engagement_id == engagement_id,
            EngagementEvidenceReviewEvent.event_type == EngagementEvidenceReviewEvent.EVENT_TRANSITION,
        )
    )
    first_at, last_at = event_result.one()

    owners = await owner_service.resolve_evidence_owners(db, org_id, list(reviews))

    user_ids: Set[Any] = set()
    for r in reviews.values():
        user_ids.add(r.assigned_reviewer_user_id)
        user_ids.add(r.last_decision_by_user_id)
    user_names = await _user_names(db, user_ids)

    return BoardData(
        engagement=engagement,
        controls=controls,
        evidence_meta=evidence_meta,
        evidence_controls={e: sorted(s) for e, s in evidence_controls.items()},
        reviews=reviews,
        files_by_evidence=files_by_evidence,
        assessment_by_file=assessment_by_file,
        open_queries_by_evidence=open_queries_by_evidence,
        open_queries_by_scf=open_queries_by_scf,
        closed_queries_by_scf=closed_queries_by_scf,
        answered_queries_by_scf=answered_queries_by_scf,
        owners=owners,
        user_names=user_names,
        first_transition_at=first_at,
        last_transition_at=last_at,
    )


# =============================================================================
# Actor / viewer
# =============================================================================

async def _active_grant_kind(db: AsyncSession, engagement_id: UUID, user_id: Optional[UUID]) -> Optional[str]:
    if user_id is None:
        return None
    result = await db.execute(
        select(EngagementAuditor.kind).where(
            EngagementAuditor.engagement_id == engagement_id,
            EngagementAuditor.user_id == user_id,
            EngagementAuditor.status == EngagementAuditorStatus.ACTIVE.value,
        )
    )
    kind = result.scalar_one_or_none()
    if kind is None:
        return None
    return kind or EngagementAuditor.KIND_EXTERNAL


async def _build_actor(db: AsyncSession, access: EngagementAccess) -> ReviewActor:
    user_id = _access_user_id(access)
    kind = await _active_grant_kind(db, access.engagement_id, user_id)
    team_ids: frozenset = frozenset()
    responded: frozenset = frozenset()
    if kind == EngagementAuditor.KIND_INTERNAL and user_id is not None:
        team_ids, responded = await _self_accept_context(db, access, user_id)
    return ReviewActor(
        user_id=user_id,
        is_auditor=kind is not None,
        auditor_kind=kind,
        # resolve_engagement_access tries membership first, so is_auditor on
        # the access object means "admitted only by a grant": not a member.
        org_role=None if access.is_auditor else access.role,
        team_ids=team_ids,
        responded_review_ids=responded,
    )


async def _self_accept_context(db: AsyncSession, access: EngagementAccess, user_id: UUID):
    """What the self-accept rule needs to know about an internal auditor:
    the teams they are on, and the cards whose latest organisation response
    they submitted themselves. Two queries per request, not per card."""
    team_rows = await db.execute(
        select(TeamMember.team_id).where(
            TeamMember.organization_id == access.organization_id,
            TeamMember.user_id == user_id,
        )
    )
    team_ids = frozenset(row[0] for row in team_rows.all())

    event_rows = await db.execute(
        select(
            EngagementEvidenceReviewEvent.review_id,
            EngagementEvidenceReviewEvent.actor_user_id,
        )
        .where(
            EngagementEvidenceReviewEvent.engagement_id == access.engagement_id,
            EngagementEvidenceReviewEvent.event_type
            == EngagementEvidenceReviewEvent.EVENT_TRANSITION,
            EngagementEvidenceReviewEvent.to_status == review_service.SUBMITTED,
        )
        .order_by(EngagementEvidenceReviewEvent.created_at.asc())
    )
    latest_submitter: Dict[UUID, Optional[UUID]] = {}
    for review_id, actor_user_id in event_rows.all():
        latest_submitter[review_id] = actor_user_id
    responded = frozenset(rid for rid, uid in latest_submitter.items() if uid == user_id)
    return team_ids, responded


def _viewer(actor: ReviewActor) -> Dict[str, Any]:
    return {
        "user_id": actor.user_id,
        "is_auditor": actor.is_auditor,
        "auditor_kind": actor.auditor_kind,
        "org_role": actor.org_role,
        "can_assign_reviewer": actor.is_auditor or actor.org_role == "admin",
        # Not for anyone acting as an auditor, internal ones included
        # (_build_actor sets is_auditor from the grant, not membership).
        # Evidence is owned by a team (noUserAssignmentControls), and setting the
        # accountable team is an org-admin route, so only an admin is offered it.
        "can_reassign_owner": actor.org_role == "admin" and not actor.is_auditor,
    }


# =============================================================================
# Pure card assembly
# =============================================================================

def _framework_or_422(engagement, framework: Optional[str]) -> str:
    frameworks = list(engagement.frameworks or [])
    if framework is None:
        if not frameworks:
            raise HTTPException(status_code=422, detail="This engagement has no frameworks.")
        return frameworks[0]
    if framework not in frameworks:
        raise HTTPException(
            status_code=422,
            detail=f"Framework '{framework}' is not one of this engagement's frameworks.",
        )
    return framework


def _window(engagement):
    return (engagement.start_date, engagement.end_date)


def worst_ai_verdict(statuses: List[Optional[str]]) -> str:
    """Worst of the in-window files' existing assessment statuses."""
    worst = 0
    verdict = "not_assessed"
    for status in statuses:
        rank = _VERDICT_RANK.get(status or "")
        if rank and rank > worst:
            worst, verdict = rank, status
    return verdict


def _framework_controls(data: BoardData, framework: str) -> Dict[str, Dict[str, Any]]:
    """In-scope controls with at least one clause in this framework."""
    out = {}
    for scf, ctl in data.controls.items():
        clauses = (ctl["mappings"] or {}).get(framework) or []
        if clauses:
            out[scf] = {
                "scf_id": scf,
                "title": ctl["title"],
                "clauses": sorted(set(clauses), key=natural_clause_sort_key),
            }
    return out


def _person(data: BoardData, user_id) -> Optional[Dict[str, Any]]:
    if not user_id:
        return None
    return {"user_id": user_id, "name": data.user_names.get(user_id, UNNAMED)}


def _owner_dict(owner) -> Dict[str, Any]:
    if owner is None:
        return {
            "tier": owner_service.TIER_ORG_ADMIN, "label": owner_service.UNOWNED_LABEL,
            "team_id": None, "team_name": None, "is_tracked": False, "tracking_id": None,
            "assigned_user_id": None, "user_ids": [],
        }
    return {
        "tier": owner.tier,
        "label": owner.label,
        "team_id": str(owner.team_id) if owner.team_id else None,
        "team_name": owner.team_name,
        "is_tracked": owner.is_tracked,
        "tracking_id": owner.tracking_id,
        "assigned_user_id": owner.assigned_user_id,
        "user_ids": list(owner.user_ids),
    }


def _build_card(
    data: BoardData,
    review,
    framework_controls: Dict[str, Dict[str, Any]],
    actor: ReviewActor,
) -> Dict[str, Any]:
    eid = review.evidence_id
    controls = [
        framework_controls[scf]
        for scf in data.evidence_controls.get(eid, [])
        if scf in framework_controls
    ]
    clauses = sorted(
        {cl for c in controls for cl in c["clauses"]}, key=natural_clause_sort_key
    )

    files = data.files_by_evidence.get(eid, [])
    window = _window(data.engagement)
    in_window_statuses = []
    in_window_count = 0
    for art in files:
        flagged, _basis = _in_window(art, window)
        if flagged:
            in_window_count += 1
            in_window_statuses.append(data.assessment_by_file.get(art["id"]))

    owner = data.owners.get(eid)
    card_actor = review_service.actor_for_card(
        actor,
        review_id=review.id,
        owner_user_ids=(owner.assigned_user_id, owner.owner_user_id) if owner else (),
        accountable_team_id=owner.team_id if owner else None,
        accountable_team_ids=owner.team_ids if owner else (),
    )
    allowed = review_service.allowed_targets(review.status, card_actor)
    return {
        "review_id": review.id,
        "evidence_id": eid,
        "title": (data.evidence_meta.get(eid) or {}).get("title"),
        "status": review.status,
        "controls": [dict(c) for c in controls],
        "clauses": clauses,
        "file_count": len(files),
        "in_window_count": in_window_count,
        "ai_verdict": worst_ai_verdict(in_window_statuses),
        "open_query_count": data.open_queries_by_evidence.get(eid, 0),
        "reviewer": _person(data, review.assigned_reviewer_user_id),
        "owner": _owner_dict(data.owners.get(eid)),
        "allowed_transitions": allowed,
        "note_required": {t: review_service.note_required(review.status, t) for t in allowed},
        "updated_at": review.updated_at,
    }


def build_framework_cards(data: BoardData, framework: str, actor: ReviewActor):
    """(cards, framework_controls) for one framework tab, unfiltered. A card
    whose controls have no clause in this framework is left out."""
    framework_controls = _framework_controls(data, framework)
    cards = []
    for eid in sorted(data.reviews):
        card = _build_card(data, data.reviews[eid], framework_controls, actor)
        if card["clauses"]:
            cards.append(card)
    return cards, framework_controls


def _no_evidence_controls(data: BoardData, cards, framework_controls) -> List[Dict[str, Any]]:
    with_cards = {c["scf_id"] for card in cards for c in card["controls"]}
    return [
        {
            "scf_id": scf,
            "title": ctl["title"],
            "clauses": list(ctl["clauses"]),
            "open_query_count": data.open_queries_by_scf.get(scf, 0),
        }
        for scf, ctl in sorted(framework_controls.items())
        if scf not in with_cards
    ]


def _matches_search(needle: str, haystack: List[Optional[str]]) -> bool:
    return any(needle in (h or "").lower() for h in haystack)


def filter_cards(
    cards: List[Dict[str, Any]],
    *,
    actor: ReviewActor,
    status: Optional[str] = None,
    clause: Optional[str] = None,
    scf_id: Optional[str] = None,
    ai_verdict_filter: Optional[str] = None,
    has_open_queries: Optional[bool] = None,
    reviewer: Optional[str] = None,
    search: Optional[str] = None,
    mine: bool = False,
) -> List[Dict[str, Any]]:
    needle = (search or "").strip().lower()
    reviewer_id = None
    if reviewer and reviewer != "none":
        reviewer_id = UUID(reviewer)
    out = []
    for card in cards:
        if status and card["status"] != status:
            continue
        if clause and clause not in card["clauses"]:
            continue
        if scf_id and scf_id not in {c["scf_id"] for c in card["controls"]}:
            continue
        if ai_verdict_filter and card["ai_verdict"] != ai_verdict_filter:
            continue
        if has_open_queries is not None and (card["open_query_count"] > 0) != has_open_queries:
            continue
        if reviewer == "none" and card["reviewer"] is not None:
            continue
        if reviewer_id is not None and (card["reviewer"] or {}).get("user_id") != reviewer_id:
            continue
        if needle and not _matches_search(
            needle,
            [card["evidence_id"], card["title"]]
            + [c["scf_id"] for c in card["controls"]]
            + card["clauses"],
        ):
            continue
        if mine:
            is_reviewer = (
                actor.user_id is not None
                and (card["reviewer"] or {}).get("user_id") == actor.user_id
            )
            is_owner = (
                actor.org_role is not None
                and actor.user_id is not None
                and actor.user_id in set(card["owner"]["user_ids"])
            )
            if not (is_reviewer or is_owner):
                continue
        out.append(card)
    return out


def filter_no_evidence_controls(
    controls: List[Dict[str, Any]],
    *,
    clause: Optional[str] = None,
    scf_id: Optional[str] = None,
    has_open_queries: Optional[bool] = None,
    search: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Only the filters that mean something for a control without a card."""
    needle = (search or "").strip().lower()
    out = []
    for ctl in controls:
        if clause and clause not in ctl["clauses"]:
            continue
        if scf_id and ctl["scf_id"] != scf_id:
            continue
        if has_open_queries is not None and (ctl["open_query_count"] > 0) != has_open_queries:
            continue
        if needle and not _matches_search(needle, [ctl["scf_id"], ctl["title"]] + ctl["clauses"]):
            continue
        out.append(ctl)
    return out


def _engagement_summary(engagement, *, with_created: bool = False) -> Dict[str, Any]:
    out = {
        "id": engagement.id,
        "name": engagement.name,
        "status": engagement.status,
        "start_date": engagement.start_date,
        "end_date": engagement.end_date,
        "frameworks": list(engagement.frameworks or []),
    }
    if with_created:
        out["created_at"] = engagement.created_at
    return out


async def _card_detail(
    db: AsyncSession,
    data: BoardData,
    evidence_id: str,
    framework: str,
    actor: ReviewActor,
) -> Dict[str, Any]:
    review = data.reviews.get(evidence_id)
    # No in-scope, non-retired control: the card is on no board.
    if review is None or not data.evidence_controls.get(evidence_id):
        raise HTTPException(status_code=404, detail="Evidence review not found")
    framework_controls = _framework_controls(data, framework)
    card = _build_card(data, review, framework_controls, actor)

    window = _window(data.engagement)
    artifacts = []
    for art in sorted(
        data.files_by_evidence.get(evidence_id, []),
        key=lambda a: (a.get("uploaded_at") or datetime.min),
        reverse=True,
    ):
        flagged, basis = _in_window(art, window)
        artifacts.append({
            "id": art["id"],
            "filename": art["filename"],
            "uploaded_at": art.get("uploaded_at"),
            "effective_start": art.get("effective_period_start"),
            "effective_end": art.get("effective_period_end"),
            "in_window": flagged,
            "window_basis": basis,
            "ai_status": data.assessment_by_file.get(art["id"]),
        })

    desc_result = await db.execute(
        select(SCFCatalogEvidence.artifact_description).where(
            SCFCatalogEvidence.evidence_id == evidence_id
        )
    )
    description = desc_result.scalar_one_or_none()

    card.update({
        "artifacts": artifacts,
        "decision_note": review.decision_note,
        "last_decision_at": review.last_decision_at,
        "last_decision_by": _person(data, review.last_decision_by_user_id),
        "description": description,
    })
    return card


# =============================================================================
# Routes — reads
# =============================================================================

def _validate_choice(value: Optional[str], allowed, name: str) -> None:
    if value is not None and value not in allowed:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid {name} '{value}'. Expected one of: {', '.join(allowed)}.",
        )


@router.get(f"{BASE}/evidence-reviews", response_model=EvidenceReviewBoard)
async def list_evidence_reviews(
    org_id: UUID,
    engagement_id: UUID,
    framework: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    clause: Optional[str] = Query(None),
    scf_id: Optional[str] = Query(None),
    ai_verdict: Optional[str] = Query(None),
    has_open_queries: Optional[bool] = Query(None),
    reviewer: Optional[str] = Query(None, description="Reviewer user id, or 'none'"),
    search: Optional[str] = Query(None),
    mine: bool = Query(False),
    access: EngagementAccess = Depends(require_engagement_read()),
    db: AsyncSession = Depends(get_db),
):
    """The review board for one framework tab: evidence cards, the controls
    that have no evidence object, filter options and what the caller may do."""
    _validate_choice(status, STATUSES, "status")
    _validate_choice(ai_verdict, AI_VERDICTS, "ai_verdict")
    if reviewer not in (None, "none"):
        try:
            UUID(reviewer)
        except ValueError:
            raise HTTPException(status_code=422, detail="reviewer must be a user id or 'none'.")

    data = await _load_board_data(db, org_id, engagement_id)
    fw = _framework_or_422(data.engagement, framework)
    actor = await _build_actor(db, access)

    all_cards, framework_controls = build_framework_cards(data, fw, actor)
    no_evidence = _no_evidence_controls(data, all_cards, framework_controls)

    cards = filter_cards(
        all_cards, actor=actor, status=status, clause=clause, scf_id=scf_id,
        ai_verdict_filter=ai_verdict, has_open_queries=has_open_queries,
        reviewer=reviewer, search=search, mine=mine,
    )
    no_evidence_filtered = filter_no_evidence_controls(
        no_evidence, clause=clause, scf_id=scf_id,
        has_open_queries=has_open_queries, search=search,
    )

    reviewers: Dict[Any, Dict[str, Any]] = {}
    for card in all_cards:
        if card["reviewer"]:
            reviewers[card["reviewer"]["user_id"]] = card["reviewer"]

    return {
        "engagement": _engagement_summary(data.engagement),
        "framework": fw,
        "totals": {"evidence": len(all_cards), "controls": len(framework_controls)},
        "counts": count_statuses(c["status"] for c in cards),
        "cards": cards,
        "no_evidence_controls": no_evidence_filtered,
        "filter_options": {
            "clauses": sorted(
                {cl for c in framework_controls.values() for cl in c["clauses"]},
                key=natural_clause_sort_key,
            ),
            "scf_ids": sorted(framework_controls),
            "reviewers": sorted(reviewers.values(), key=lambda p: (p["name"], str(p["user_id"]))),
        },
        "viewer": _viewer(actor),
    }


@router.get(f"{BASE}/evidence-reviews/{{evidence_id}}", response_model=EvidenceCardDetail)
async def get_evidence_review(
    org_id: UUID,
    engagement_id: UUID,
    evidence_id: str,
    framework: Optional[str] = Query(None),
    access: EngagementAccess = Depends(require_engagement_read()),
    db: AsyncSession = Depends(get_db),
):
    """One card with its artifacts, the last decision and the catalog text."""
    data = await _load_board_data(db, org_id, engagement_id)
    fw = _framework_or_422(data.engagement, framework)
    actor = await _build_actor(db, access)
    return await _card_detail(db, data, evidence_id, fw, actor)


@router.get(
    f"{BASE}/evidence-reviews/{{evidence_id}}/files/{{file_id}}",
    response_model=ArtifactDownload,
)
async def get_evidence_review_file(
    org_id: UUID,
    engagement_id: UUID,
    evidence_id: str,
    file_id: UUID,
    access: EngagementAccess = Depends(require_engagement_read()),
    db: AsyncSession = Depends(get_db),
):
    """Signed download link for one artifact, so an external auditor (who
    cannot call the org-level evidence routes) can open it. The link is the
    same user-bound proxy link the org-level single-file GET mints; following
    it writes the custody (download) record there."""
    review_result = await db.execute(
        select(EngagementEvidenceReview.id).where(
            EngagementEvidenceReview.engagement_id == engagement_id,
            EngagementEvidenceReview.organization_id == org_id,
            EngagementEvidenceReview.evidence_id == evidence_id,
        )
    )
    if review_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Evidence file not found")
    await _require_on_board(db, org_id, engagement_id, evidence_id, "Evidence file not found")

    file_result = await db.execute(
        select(EvidenceFile).where(
            EvidenceFile.organization_id == org_id,
            EvidenceFile.evidence_id == evidence_id,
            EvidenceFile.id == file_id,
            EvidenceFile.is_deleted == False,  # noqa: E712
        )
    )
    evidence_file = file_result.scalar_one_or_none()
    if evidence_file is None:
        raise HTTPException(status_code=404, detail="Evidence file not found")

    if _access_user_id(access) is None:
        download_url = None  # no user to bind the signed link to
    else:
        download_url = _proxy_download_url(org_id, evidence_id, file_id, access)
    return {"download_url": download_url, "filename": evidence_file.filename}


@router.get(
    f"{BASE}/evidence-reviews/{{evidence_id}}/events",
    response_model=List[EvidenceReviewEventItem],
)
async def list_evidence_review_events(
    org_id: UUID,
    engagement_id: UUID,
    evidence_id: str,
    access: EngagementAccess = Depends(require_engagement_read()),
    db: AsyncSession = Depends(get_db),
):
    """The card's history, oldest first."""
    await _require_on_board(db, org_id, engagement_id, evidence_id, "Evidence review not found")
    review_result = await db.execute(
        select(EngagementEvidenceReview.id).where(
            EngagementEvidenceReview.engagement_id == engagement_id,
            EngagementEvidenceReview.organization_id == org_id,
            EngagementEvidenceReview.evidence_id == evidence_id,
        )
    )
    review_id = review_result.scalar_one_or_none()
    if review_id is None:
        raise HTTPException(status_code=404, detail="Evidence review not found")

    event_result = await db.execute(
        select(EngagementEvidenceReviewEvent)
        .where(EngagementEvidenceReviewEvent.review_id == review_id)
        .order_by(EngagementEvidenceReviewEvent.created_at, EngagementEvidenceReviewEvent.id)
    )
    events = list(event_result.scalars().all())
    names = await _user_names(db, {e.actor_user_id for e in events})
    return [
        {
            "id": e.id,
            "event_type": e.event_type,
            "from_status": e.from_status,
            "to_status": e.to_status,
            "actor": (
                {"user_id": e.actor_user_id, "name": names.get(e.actor_user_id, UNNAMED)}
                if e.actor_user_id else None
            ),
            "actor_kind": e.actor_kind,
            "note": e.note,
            "created_at": e.created_at,
        }
        for e in events
    ]


@router.get(f"{BASE}/review-people", response_model=ReviewPeople)
async def list_review_people(
    org_id: UUID,
    engagement_id: UUID,
    access: EngagementAccess = Depends(require_engagement_read()),
    db: AsyncSession = Depends(get_db),
):
    """People with access to this engagement: active auditors and org members.
    No email addresses."""
    auditor_result = await db.execute(
        select(EngagementAuditor.user_id, EngagementAuditor.kind, DBUser.display_name)
        .join(DBUser, EngagementAuditor.user_id == DBUser.id)
        .where(
            EngagementAuditor.engagement_id == engagement_id,
            EngagementAuditor.status == EngagementAuditorStatus.ACTIVE.value,
        )
    )
    member_result = await db.execute(
        select(OrganizationMember.user_id, OrganizationMember.role, DBUser.display_name)
        .join(DBUser, OrganizationMember.user_id == DBUser.id)
        .where(OrganizationMember.organization_id == org_id)
    )
    auditors = [
        {"user_id": uid, "name": _name(name), "kind": kind or EngagementAuditor.KIND_EXTERNAL}
        for uid, kind, name in auditor_result.fetchall()
    ]
    members = [
        {"user_id": uid, "name": _name(name), "role": role}
        for uid, role, name in member_result.fetchall()
    ]
    auditors.sort(key=lambda p: (p["name"].lower(), str(p["user_id"])))
    members.sort(key=lambda p: (p["name"].lower(), str(p["user_id"])))
    return {"auditors": auditors, "members": members}


@router.get(f"{BASE}/review-dashboard", response_model=ReviewDashboard)
async def get_review_dashboard(
    org_id: UUID,
    engagement_id: UUID,
    framework: Optional[str] = Query(None),
    access: EngagementAccess = Depends(require_engagement_read()),
    db: AsyncSession = Depends(get_db),
):
    """Roll-up of the board for one framework: evidence -> controls -> clauses."""
    data = await _load_board_data(db, org_id, engagement_id)
    fw = _framework_or_422(data.engagement, framework)
    actor = await _build_actor(db, access)
    return build_dashboard(data, fw, actor)


def build_dashboard(data: BoardData, framework: str, actor: ReviewActor, today: Optional[date] = None):
    cards, framework_controls = build_framework_cards(data, framework, actor)
    rollup = build_rollup(
        cards=[
            {
                "evidence_id": c["evidence_id"],
                "title": c["title"],
                "status": c["status"],
                "controls": [ctl["scf_id"] for ctl in c["controls"]],
                "open_query_count": c["open_query_count"],
            }
            for c in cards
        ],
        controls=list(framework_controls.values()),
        clause_ids=sorted({cl for c in framework_controls.values() for cl in c["clauses"]}),
        open_control_queries=data.open_queries_by_scf,
        closed_control_queries=data.closed_queries_by_scf,
        answered_control_queries=data.answered_queries_by_scf,
        start_date=data.engagement.start_date,
        end_date=data.engagement.end_date,
        first_transition_at=data.first_transition_at,
        last_transition_at=data.last_transition_at,
        today=today,
    )
    return {
        "engagement": _engagement_summary(data.engagement, with_created=True),
        "framework": framework,
        **rollup,
    }


# =============================================================================
# Routes — writes
# =============================================================================

async def _lock_review(db: AsyncSession, org_id: UUID, engagement_id: UUID, evidence_id: str):
    result = await db.execute(
        select(EngagementEvidenceReview)
        .where(
            EngagementEvidenceReview.engagement_id == engagement_id,
            EngagementEvidenceReview.organization_id == org_id,
            EngagementEvidenceReview.evidence_id == evidence_id,
        )
        .with_for_update()
    )
    review = result.scalar_one_or_none()
    if review is None:
        raise HTTPException(status_code=404, detail="Evidence review not found")
    return review


_STATUS_LABELS = {
    review_service.OPEN: "Open",
    review_service.IN_PROGRESS: "In progress",
    review_service.ACTION_REQUIRED: "Action required",
    review_service.SUBMITTED: "Submitted",
    review_service.ACCEPTED: "Accepted",
}


async def _require_on_board(
    db: AsyncSession, org_id: UUID, engagement_id: UUID, evidence_id: str, detail: str,
) -> None:
    """404, with the same text as an unknown id, when the evidence object
    supports no in-scope, non-retired control: its review row may still exist
    (materialise never deletes), but it is on no board or dashboard."""
    in_scope = await review_service.evidence_ids_in_scope(db, engagement_id, org_id)
    if evidence_id not in in_scope:
        raise HTTPException(status_code=404, detail=detail)


@router.post(
    f"{BASE}/evidence-reviews/{{evidence_id}}/status",
    response_model=EvidenceCardDetail,
)
async def change_evidence_review_status(
    org_id: UUID,
    engagement_id: UUID,
    evidence_id: str,
    payload: EvidenceReviewStatusChange,
    request: Request,
    framework: Optional[str] = Query(None),
    access: EngagementAccess = Depends(require_engagement_read()),
    db: AsyncSession = Depends(get_db),
):
    """Move a card. The transition table lives in
    ``services.engagement_evidence_review.validate_transition``."""
    engagement = await _load_engagement(db, org_id, engagement_id)
    fw = _framework_or_422(engagement, framework)

    await _require_on_board(db, org_id, engagement_id, evidence_id, "Evidence review not found")
    review = await _lock_review(db, org_id, engagement_id, evidence_id)
    if payload.from_status is not None and payload.from_status != review.status:
        label = _STATUS_LABELS.get(review.status, review.status)
        raise HTTPException(
            status_code=409,
            detail=f"This card has moved since you loaded it; it is now {label}.",
        )
    actor = await _build_actor(db, access)
    if actor.user_id is None:
        raise HTTPException(status_code=403, detail="A signed-in user is required to move evidence.")

    target = payload.to_status
    if target == review_service.ACCEPTED and actor.is_auditor and actor.auditor_kind == "internal":
        owns, in_team = await owner_service.evidence_ownership_flags(
            db, org_id, evidence_id, actor.user_id
        )
        # The review row is locked and the actor was built after the lock, so
        # "who made the latest organisation response" is current.
        actor = replace(
            actor,
            owns_evidence=owns,
            in_accountable_team=in_team,
            responded_as_org=review.id in actor.responded_review_ids,
        )

    file_ids = list(dict.fromkeys(payload.artifact_file_ids or []))
    try:
        actor_kind = review_service.validate_transition(
            review.status, target, actor,
            note=payload.note,
            linked_artifact_count=len(file_ids),
            no_artifact_reason=payload.no_artifact_reason,
        )
    except TransitionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)

    linked_files: List[Any] = []
    if target == review_service.SUBMITTED and file_ids:
        file_result = await db.execute(
            select(EvidenceFile.id, EvidenceFile.filename).where(
                EvidenceFile.organization_id == org_id,
                EvidenceFile.evidence_id == evidence_id,
                EvidenceFile.id.in_(file_ids),
                EvidenceFile.is_deleted == False,  # noqa: E712
            )
        )
        found = {fid: name for fid, name in file_result.fetchall()}
        if set(found) != set(file_ids):
            raise HTTPException(
                status_code=422,
                detail="Every linked artifact must be a file of this evidence object in this organisation.",
            )
        linked_files = [found[fid] for fid in file_ids]

    await review_service.record_transition(
        db, review=review, to_status=target, actor_user_id=actor.user_id,
        actor_kind=actor_kind, note=payload.note, request=request,
    )

    if target == review_service.SUBMITTED:
        note = (payload.note or "").strip()
        if linked_files:
            tail = "Linked artifacts: " + ", ".join(linked_files)
        else:
            tail = "No artifact linked: " + (payload.no_artifact_reason or "").strip()
        db.add(Comment(
            commentable_type=EngagementEvidenceReview.COMMENTABLE_TYPE,
            commentable_id=review.id,
            user_id=actor.user_id,
            content=f"{note}\n\n{tail}" if note else tail,
            visibility=Comment.VISIBILITY_SHARED,
        ))

    review_id = review.id
    reviewer_id = review.assigned_reviewer_user_id
    await db.commit()

    try:
        if target == review_service.ACTION_REQUIRED:
            await notification_service.create_engagement_evidence_action_required_notifications(
                db, organization_id=org_id, engagement_id=engagement_id,
                review_id=review_id, evidence_id=evidence_id,
                actor_user_id=actor.user_id, note=payload.note,
            )
        elif target == review_service.SUBMITTED:
            await notification_service.create_engagement_evidence_submitted_notifications(
                db, organization_id=org_id, engagement_id=engagement_id,
                review_id=review_id, evidence_id=evidence_id,
                actor_user_id=actor.user_id, assigned_reviewer_user_id=reviewer_id,
            )
    except Exception as exc:  # noqa: BLE001 — a notification must never fail the move
        logger.error("Evidence review notification failed for %s: %s", review_id, exc, exc_info=True)

    data = await _load_board_data(db, org_id, engagement_id)
    return await _card_detail(db, data, evidence_id, fw, actor)


@router.put(
    f"{BASE}/evidence-reviews/{{evidence_id}}/reviewer",
    response_model=EvidenceCardDetail,
)
async def assign_evidence_reviewer(
    org_id: UUID,
    engagement_id: UUID,
    evidence_id: str,
    payload: EvidenceReviewerAssignment,
    request: Request,
    framework: Optional[str] = Query(None),
    access: EngagementAccess = Depends(require_engagement_read()),
    db: AsyncSession = Depends(get_db),
):
    """Assign (or clear) the auditor reviewing a card. Auditor or org admin."""
    engagement = await _load_engagement(db, org_id, engagement_id)
    fw = _framework_or_422(engagement, framework)

    actor = await _build_actor(db, access)
    if actor.user_id is None:
        raise HTTPException(status_code=403, detail="A signed-in user is required to assign a reviewer.")
    if not (actor.is_auditor or actor.org_role == "admin"):
        raise HTTPException(
            status_code=403,
            detail="Only an engagement auditor or an organisation admin can assign the reviewer.",
        )

    await _require_on_board(db, org_id, engagement_id, evidence_id, "Evidence review not found")
    review = await _lock_review(db, org_id, engagement_id, evidence_id)

    new_reviewer = payload.user_id
    if new_reviewer is not None:
        kind = await _active_grant_kind(db, engagement_id, new_reviewer)
        if kind is None:
            raise HTTPException(
                status_code=422,
                detail="The reviewer must hold an active auditor grant on this engagement.",
            )

    previous = review.assigned_reviewer_user_id
    await review_service.record_reviewer_assignment(
        db, review=review, reviewer_user_id=new_reviewer,
        actor_user_id=actor.user_id,
        actor_kind=ACTOR_AUDITOR if actor.is_auditor else ACTOR_ORG,
        request=request,
    )
    review_id = review.id
    await db.commit()

    if new_reviewer is not None and new_reviewer != previous:
        try:
            await notification_service.create_engagement_evidence_reassigned_notifications(
                db, organization_id=org_id, engagement_id=engagement_id,
                review_id=review_id, evidence_id=evidence_id,
                actor_user_id=actor.user_id, new_reviewer_user_id=new_reviewer,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Reviewer notification failed for %s: %s", review_id, exc, exc_info=True)

    data = await _load_board_data(db, org_id, engagement_id)
    return await _card_detail(db, data, evidence_id, fw, actor)
