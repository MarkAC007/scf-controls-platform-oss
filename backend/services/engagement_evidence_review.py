"""Review state of evidence objects within an audit engagement.

The card on the engagement review board is the evidence object (SCF ERL id,
e.g. ``E-IAC-07``). This module owns the review stages, the one table of
permitted moves, and the writes that record a move. Routes call
``validate_transition`` first, then ``record_transition``, then commit.

The transition table is decided ONLY in ``validate_transition`` (and the pure
helpers that read the same table), so the API, the board UI hints and the
tests can never disagree about who may move a card where.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from catalog_models import SCFCatalogControl
from models import (
    AuditEngagement,
    EngagementControlScope,
    EngagementEvidenceReview,
    EngagementEvidenceReviewEvent,
    EngagementEvidenceReviewStatus,
    ScopeStatus,
)
from services.audit_service import log_entity_changes, detect_action_source, get_request_id


OPEN = EngagementEvidenceReviewStatus.OPEN.value
IN_PROGRESS = EngagementEvidenceReviewStatus.IN_PROGRESS.value
ACTION_REQUIRED = EngagementEvidenceReviewStatus.ACTION_REQUIRED.value
SUBMITTED = EngagementEvidenceReviewStatus.SUBMITTED.value
ACCEPTED = EngagementEvidenceReviewStatus.ACCEPTED.value

# Board order.
STATUSES: tuple[str, ...] = (OPEN, IN_PROGRESS, ACTION_REQUIRED, SUBMITTED, ACCEPTED)

ACTOR_AUDITOR = "auditor"
ACTOR_ORG = "org"

AUDITOR_KIND_INTERNAL = "internal"
AUDITOR_KIND_EXTERNAL = "external"

ENTITY_TYPE = "engagement_evidence_review"

# Org roles that may make org moves (verify_org_membership hierarchy).
_ORG_EDITOR_ROLES = frozenset({"editor", "admin"})


@dataclass(frozen=True)
class _Edge:
    actor: str           # ACTOR_AUDITOR | ACTOR_ORG
    needs_note: bool


# The transition table. (from, to) -> who moves it and whether a note is required.
_TRANSITIONS: dict[tuple[str, str], _Edge] = {
    (OPEN, IN_PROGRESS): _Edge(ACTOR_AUDITOR, False),
    (IN_PROGRESS, ACTION_REQUIRED): _Edge(ACTOR_AUDITOR, True),
    (IN_PROGRESS, ACCEPTED): _Edge(ACTOR_AUDITOR, False),
    (ACTION_REQUIRED, SUBMITTED): _Edge(ACTOR_ORG, True),
    (SUBMITTED, IN_PROGRESS): _Edge(ACTOR_AUDITOR, False),
    (SUBMITTED, ACTION_REQUIRED): _Edge(ACTOR_AUDITOR, True),
    (SUBMITTED, ACCEPTED): _Edge(ACTOR_AUDITOR, False),
    (ACCEPTED, IN_PROGRESS): _Edge(ACTOR_AUDITOR, True),
}


class TransitionError(Exception):
    """A refused move. ``status_code``: 403 wrong actor / self-accept,
    409 move not in the table, 422 required note missing."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class ReviewActor:
    user_id: UUID
    is_auditor: bool                 # active grant, either kind
    auditor_kind: Optional[str]      # 'external' | 'internal' | None
    org_role: Optional[str]          # 'viewer' | 'editor' | 'admin' | None (None = not a member)
    owns_evidence: bool = False      # assigned_user_id / owner_user_id on the tracking row
    in_accountable_team: bool = False
    responded_as_org: bool = False   # made the latest 'submitted' move on this card
    # Loaded once per request for an internal auditor, so each card's allowed
    # moves can be worked out without a query per card (see actor_for_card).
    team_ids: frozenset = frozenset()
    responded_review_ids: frozenset = frozenset()


# =============================================================================
# Pure rules
# =============================================================================

def _blank(value: Optional[str]) -> bool:
    return value is None or not str(value).strip()


def _can_act_as(actor: ReviewActor, side: str) -> bool:
    if side == ACTOR_AUDITOR:
        return bool(actor.is_auditor)
    return actor.org_role in _ORG_EDITOR_ROLES


def _self_accept_blocked(actor: ReviewActor) -> bool:
    """Segregation of duties: an internal auditor may not accept evidence they
    own, whose accountable team they belong to, or on which they themselves
    submitted the organisation's response. An internal auditor is an org
    member, so as an editor or admin they can still respond for the
    organisation; what they cannot do is then accept their own response.
    External auditors are never blocked by this rule."""
    return (
        actor.is_auditor
        and actor.auditor_kind == AUDITOR_KIND_INTERNAL
        and (actor.owns_evidence or actor.in_accountable_team or actor.responded_as_org)
    )


def actor_for_card(
    actor: ReviewActor,
    *,
    review_id,
    owner_user_ids=(),
    accountable_team_id=None,
    accountable_team_ids=(),
) -> ReviewActor:
    """Pure. The actor as they stand towards one card, so the moves a card
    offers are the moves the server will allow. Only an internal auditor
    differs from card to card. Any accountable team counts, as it does in
    the server-side check (owner_service.evidence_ownership_flags)."""
    if not (actor.is_auditor and actor.auditor_kind == AUDITOR_KIND_INTERNAL):
        return actor
    teams = {t for t in (accountable_team_id, *accountable_team_ids) if t is not None}
    return replace(
        actor,
        owns_evidence=actor.user_id in {u for u in owner_user_ids if u is not None},
        in_accountable_team=bool(teams & set(actor.team_ids)),
        responded_as_org=review_id in actor.responded_review_ids,
    )


def validate_transition(
    current: str,
    target: str,
    actor: ReviewActor,
    *,
    note: Optional[str] = None,
    linked_artifact_count: int = 0,
    no_artifact_reason: Optional[str] = None,
) -> str:
    """Pure. Returns the actor_kind to record ('auditor' | 'org'). Raises TransitionError.

    Check order: move not in the table (409) -> wrong side (403) ->
    internal-auditor self-accept (403) -> required note missing (422).
    """
    edge = _TRANSITIONS.get((current, target))
    if edge is None:
        raise TransitionError(
            409, f"Cannot move evidence review from '{current}' to '{target}'."
        )

    if not _can_act_as(actor, edge.actor):
        if edge.actor == ACTOR_AUDITOR:
            detail = (
                f"Only an engagement auditor can move evidence review from "
                f"'{current}' to '{target}'."
            )
        else:
            detail = (
                f"Only an organisation editor or admin can move evidence review from "
                f"'{current}' to '{target}'."
            )
        raise TransitionError(403, detail)

    if target == ACCEPTED and _self_accept_blocked(actor):
        if actor.owns_evidence:
            reason = "you own this evidence"
        elif actor.in_accountable_team:
            reason = "you are on the team accountable for this evidence"
        else:
            reason = "you submitted the organisation's response on it"
        raise TransitionError(
            403,
            f"Segregation of duties: an internal auditor cannot accept evidence "
            f"because {reason}. Another auditor must accept it.",
        )

    if edge.needs_note and _blank(note):
        if (current, target) == (ACTION_REQUIRED, SUBMITTED):
            detail = "A response note is required to submit evidence back to the auditor."
        elif current == ACCEPTED:
            detail = "A reason is required to reopen accepted evidence."
        else:
            detail = "A note is required to tell the organisation what action is needed."
        raise TransitionError(422, detail)

    if (current, target) == (ACTION_REQUIRED, SUBMITTED):
        if linked_artifact_count < 1 and _blank(no_artifact_reason):
            raise TransitionError(
                422,
                "Link at least one artifact, or explain why there is none, "
                "before submitting.",
            )

    return edge.actor


def allowed_targets(current: str, actor: ReviewActor) -> list[str]:
    """Pure. Targets this actor could move to from `current` (ignores note
    requirements; excludes 'accepted' for an internal auditor blocked by the
    self-accept rule). Returned in board order."""
    targets = []
    for status in STATUSES:
        edge = _TRANSITIONS.get((current, status))
        if edge is None or not _can_act_as(actor, edge.actor):
            continue
        if status == ACCEPTED and _self_accept_blocked(actor):
            continue
        targets.append(status)
    return targets


def note_required(current: str, target: str) -> bool:
    """Pure, for the UI: whether the move needs a note."""
    edge = _TRANSITIONS.get((current, target))
    return bool(edge and edge.needs_note)


# =============================================================================
# Scope and materialisation
# =============================================================================

async def evidence_ids_in_scope(db, engagement_id, organization_id) -> set[str]:
    """Evidence objects mapped (either catalog side) to in-scope, non-retired controls.

    Goes through api.audit_engagements._fetch_evidence_by_scf so the board and
    the presentation tree can never diverge. Lazy import: that module imports
    services at load time.
    """
    from api.audit_engagements import _fetch_evidence_by_scf, _not_retired

    result = await db.execute(
        select(EngagementControlScope.scf_id)
        .join(AuditEngagement, EngagementControlScope.engagement_id == AuditEngagement.id)
        .outerjoin(SCFCatalogControl, EngagementControlScope.scf_id == SCFCatalogControl.scf_id)
        .where(
            EngagementControlScope.engagement_id == engagement_id,
            AuditEngagement.organization_id == organization_id,
            EngagementControlScope.scope_status == ScopeStatus.IN_SCOPE.value,
            _not_retired(),
        )
    )
    scf_ids = {row[0] for row in result.fetchall()}
    if not scf_ids:
        return set()

    _evidence_by_scf, evidence_objects_by_scf = await _fetch_evidence_by_scf(
        db, organization_id, scf_ids
    )
    return {
        item["evidence_id"]
        for items in evidence_objects_by_scf.values()
        for item in items
    }


async def materialise_reviews(db, engagement_id, organization_id) -> int:
    """Idempotent: insert an 'open' review for every in-scope evidence object that
    has none (INSERT ... ON CONFLICT DO NOTHING). Never deletes, never changes an
    existing row. Returns rows inserted. Does not commit."""
    # The session runs with autoflush off: flush so a caller's pending scope
    # rows (create_engagement) are visible to the scope read below.
    await db.flush()
    in_scope = await evidence_ids_in_scope(db, engagement_id, organization_id)
    if not in_scope:
        return 0

    existing_result = await db.execute(
        select(EngagementEvidenceReview.evidence_id).where(
            EngagementEvidenceReview.engagement_id == engagement_id
        )
    )
    existing = {row[0] for row in existing_result.fetchall()}
    missing = sorted(in_scope - existing)
    if not missing:
        return 0

    # ON CONFLICT covers a concurrent materialise between the read and the insert.
    stmt = (
        pg_insert(EngagementEvidenceReview)
        .values([
            {
                "engagement_id": engagement_id,
                "organization_id": organization_id,
                "evidence_id": evidence_id,
                "status": OPEN,
            }
            for evidence_id in missing
        ])
        .on_conflict_do_nothing(index_elements=["engagement_id", "evidence_id"])
        .returning(EngagementEvidenceReview.id)
    )
    result = await db.execute(stmt)
    return len(result.fetchall())


# =============================================================================
# Recording moves
# =============================================================================

def _request_context(request) -> tuple[Optional[str], Optional[UUID]]:
    if request is None:
        return None, None
    return detect_action_source(request), get_request_id(request)


async def record_transition(
    db,
    *,
    review,
    to_status: str,
    actor_user_id,
    actor_kind: str,
    note: Optional[str],
    request=None,
) -> EngagementEvidenceReviewEvent:
    """Mutates review (status, last_decision_by_user_id, last_decision_at,
    decision_note, updated_at), adds the event row, writes audit_log via
    log_entity_changes (entity_type='engagement_evidence_review', action='update',
    tracked_fields={'status'}). Does not validate (call validate_transition
    first) and does not commit."""
    now = datetime.utcnow()
    from_status = review.status
    clean_note = None if _blank(note) else note.strip()

    review.status = to_status
    review.last_decision_by_user_id = actor_user_id
    review.last_decision_at = now
    review.decision_note = clean_note
    review.updated_at = now

    event = EngagementEvidenceReviewEvent(
        review_id=review.id,
        engagement_id=review.engagement_id,
        organization_id=review.organization_id,
        evidence_id=review.evidence_id,
        event_type=EngagementEvidenceReviewEvent.EVENT_TRANSITION,
        from_status=from_status,
        to_status=to_status,
        actor_user_id=actor_user_id,
        actor_kind=actor_kind,
        note=clean_note,
        created_at=now,
    )
    db.add(event)

    action_source, request_id = _request_context(request)
    await log_entity_changes(
        db=db,
        organization_id=review.organization_id,
        entity_type=ENTITY_TYPE,
        entity_id=review.id,
        action="update",
        changed_by_user_id=actor_user_id,
        old_values={"status": from_status},
        new_values={"status": to_status},
        tracked_fields={"status"},
        action_source=action_source,
        request_id=request_id,
    )
    return event


async def record_reviewer_assignment(
    db,
    *,
    review,
    reviewer_user_id,
    actor_user_id,
    actor_kind: str,
    request=None,
) -> EngagementEvidenceReviewEvent:
    """Sets assigned_reviewer_user_id, adds a 'reviewer_assigned' event
    (from_status == to_status), writes audit_log. Does not commit."""
    now = datetime.utcnow()
    previous = review.assigned_reviewer_user_id

    review.assigned_reviewer_user_id = reviewer_user_id
    review.updated_at = now

    event = EngagementEvidenceReviewEvent(
        review_id=review.id,
        engagement_id=review.engagement_id,
        organization_id=review.organization_id,
        evidence_id=review.evidence_id,
        event_type=EngagementEvidenceReviewEvent.EVENT_REVIEWER_ASSIGNED,
        from_status=review.status,
        to_status=review.status,
        actor_user_id=actor_user_id,
        actor_kind=actor_kind,
        note=None,
        created_at=now,
    )
    db.add(event)

    action_source, request_id = _request_context(request)
    await log_entity_changes(
        db=db,
        organization_id=review.organization_id,
        entity_type=ENTITY_TYPE,
        entity_id=review.id,
        action="update",
        changed_by_user_id=actor_user_id,
        old_values={"assigned_reviewer_user_id": previous},
        new_values={"assigned_reviewer_user_id": reviewer_user_id},
        tracked_fields={"assigned_reviewer_user_id"},
        action_source=action_source,
        request_id=request_id,
    )
    return event
