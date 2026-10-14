"""Risk acceptance and standards exceptions (PIG R7).

Accepting a risk is a decision someone answers for. It is recorded with the
admin who approved it, when, the rationale, and an expiry, and only through
:func:`accept_risk`, which the acceptance endpoint calls with the caller's own
user id.

``treatment_status = 'accepted'`` can still be set by the ordinary risk
writes: the risk detail page autosaves its whole form, status included, so
refusing the status there would fail every later edit to that risk. Such an
acceptance carries no approval, and the journey check
``risks_accepted_without_approver_max`` counts it, as it counts an approval
whose expiry has passed.

After an ordinary write (:func:`after_ordinary_write`):

* An approval in force is withdrawn only by an admin. Below admin, a write
  that would move the risk out of 'accepted' keeps the status and applies the
  rest; that is also what a stale autosave sends.
* Moving a risk out of 'accepted' clears its approval, so accepting it again
  needs a fresh one.
* Re-scoring an accepted risk clears its approval: what was approved has
  changed. The risk stays accepted, unapproved, until an admin accepts it
  again. Changing the control links of an accepted custom risk does the same
  (:func:`links_changed`).

An exception against a standard is a custom risk whose control link carries
the document that sets the standard. It is accepted through the same path.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Iterable, Optional
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import GeneratedDocument, RiskAssessment

#: The treatment status an acceptance records.
ACCEPTED = "accepted"

#: The status a revoked acceptance returns to: analysed, awaiting a new
#: treatment decision.
REVOKED_STATUS = "analysed"

#: ``acceptance_state`` values.
STATE_APPROVED = "approved"
STATE_EXPIRED = "expired"
STATE_UNAPPROVED = "unapproved"

ACCEPTANCE_FIELDS = (
    "accepted_by",
    "accepted_at",
    "acceptance_rationale",
    "acceptance_expires_on",
)


def acceptance_state(assessment, today: Optional[date] = None) -> Optional[str]:
    """``approved``, ``expired`` or ``unapproved`` for an accepted risk; else None.

    An approval whose approver has since been deleted (``accepted_by`` set
    NULL) is unapproved: nobody is left to answer for it.
    """
    if assessment.treatment_status != ACCEPTED:
        return None
    if assessment.accepted_at is None or assessment.accepted_by is None:
        return STATE_UNAPPROVED
    today = today or date.today()
    if assessment.acceptance_expires_on is None or assessment.acceptance_expires_on < today:
        return STATE_EXPIRED
    return STATE_APPROVED


def approval_in_force(assessment, today: Optional[date] = None) -> bool:
    """Whether the acceptance record on this row is an approval still in force."""
    today = today or date.today()
    return (
        assessment.accepted_at is not None
        and assessment.accepted_by is not None
        and assessment.acceptance_expires_on is not None
        and assessment.acceptance_expires_on >= today
    )


def without_current_approval(today: date):
    """SQL: an accepted risk with no approval in force, over RiskAssessment rows."""
    return and_(
        RiskAssessment.treatment_status == ACCEPTED,
        or_(
            RiskAssessment.accepted_at.is_(None),
            RiskAssessment.accepted_by.is_(None),
            RiskAssessment.acceptance_expires_on.is_(None),
            RiskAssessment.acceptance_expires_on < today,
        ),
    )


def state_filter(state: str, today: date):
    """SQL for one ``acceptance_state`` value."""
    if state == STATE_APPROVED:
        return and_(
            RiskAssessment.treatment_status == ACCEPTED,
            RiskAssessment.accepted_at.isnot(None),
            RiskAssessment.accepted_by.isnot(None),
            RiskAssessment.acceptance_expires_on >= today,
        )
    if state == STATE_EXPIRED:
        return and_(
            RiskAssessment.treatment_status == ACCEPTED,
            RiskAssessment.accepted_at.isnot(None),
            RiskAssessment.accepted_by.isnot(None),
            RiskAssessment.acceptance_expires_on < today,
        )
    if state == STATE_UNAPPROVED:
        return and_(
            RiskAssessment.treatment_status == ACCEPTED,
            or_(RiskAssessment.accepted_at.is_(None), RiskAssessment.accepted_by.is_(None)),
        )
    raise ValueError(f"Unknown acceptance state '{state}'")


#: The fields an acceptance was approved against. Changing any of them on an
#: accepted risk withdraws the approval.
SCORE_FIELDS = ("likelihood", "impact", "residual_likelihood", "residual_impact")


def clear_acceptance(assessment) -> None:
    for field in ACCEPTANCE_FIELDS:
        setattr(assessment, field, None)


def after_ordinary_write(
    assessment,
    before: dict,
    may_withdraw: bool,
    today: Optional[date] = None,
) -> None:
    """Keep the approval honest after an ordinary risk write.

    ``before`` holds the tracked values from before the write was applied;
    the acceptance fields themselves are never written this way, so the
    record on ``assessment`` is still the one in force before the write.
    ``may_withdraw`` is true for an organisation admin.
    """
    previous_status = before.get("treatment_status")
    if previous_status == ACCEPTED and assessment.treatment_status != ACCEPTED:
        if not may_withdraw and approval_in_force(assessment, today):
            assessment.treatment_status = ACCEPTED
        else:
            clear_acceptance(assessment)
            return
    if assessment.treatment_status == ACCEPTED and any(
        before.get(f) != getattr(assessment, f) for f in SCORE_FIELDS
    ):
        clear_acceptance(assessment)



async def _assessment(db: AsyncSession, org_id: UUID, risk_code: str) -> RiskAssessment:
    assessment = (await db.execute(
        select(RiskAssessment).where(
            RiskAssessment.organization_id == org_id,
            RiskAssessment.risk_code == risk_code,
        )
    )).scalar_one_or_none()
    if assessment is None:
        raise LookupError(f"Risk assessment for '{risk_code}' not found")
    return assessment


async def accept_risk(
    db: AsyncSession,
    org_id: UUID,
    risk_code: str,
    rationale: Optional[str],
    expires_on: Optional[date],
    approver_user_id: Optional[UUID],
    today: Optional[date] = None,
) -> RiskAssessment:
    """Record an acceptance: the approver, now, the rationale and the expiry.

    ``approver_user_id`` is the admin making the call: the caller passes its
    own id, never one from a payload. A renewal replaces the previous record;
    the audit log keeps it.
    """
    if approver_user_id is None:
        raise PermissionError(
            "Accepting a risk is recorded against an approving admin; "
            "use an admin's own credentials"
        )
    cleaned = (rationale or "").strip()
    if not cleaned:
        raise ValueError("Accepting a risk needs a rationale")
    today = today or date.today()
    if expires_on is None or expires_on <= today:
        raise ValueError("Accepting a risk needs an expiry date after today")
    assessment = await _assessment(db, org_id, risk_code)
    assessment.treatment_status = ACCEPTED
    assessment.accepted_by = approver_user_id
    assessment.accepted_at = datetime.utcnow()
    assessment.acceptance_rationale = cleaned
    assessment.acceptance_expires_on = expires_on
    assessment.updated_by_user_id = approver_user_id
    return assessment


async def revoke_acceptance(
    db: AsyncSession,
    org_id: UUID,
    risk_code: str,
    user_id: Optional[UUID],
) -> RiskAssessment:
    """Withdraw an acceptance; the risk returns to 'analysed'."""
    if user_id is None:
        raise PermissionError(
            "Withdrawing a risk acceptance is recorded against an admin; "
            "use an admin's own credentials"
        )
    assessment = await _assessment(db, org_id, risk_code)
    if assessment.treatment_status != ACCEPTED:
        raise ValueError(f"'{risk_code}' is not accepted")
    assessment.treatment_status = REVOKED_STATUS
    clear_acceptance(assessment)
    assessment.updated_by_user_id = user_id
    return assessment


async def links_changed(
    db: AsyncSession, org_id: UUID, risk_code: str,
) -> Optional[tuple]:
    """Withdraw the approval of an accepted risk whose control links changed.

    An exception against a standard is approved for the controls and
    documents it links. Returns ``(assessment, before)`` when an approval was
    cleared, for the caller to audit; None otherwise.
    """
    assessment = (await db.execute(
        select(RiskAssessment).where(
            RiskAssessment.organization_id == org_id,
            RiskAssessment.risk_code == risk_code,
        )
    )).scalar_one_or_none()
    if (
        assessment is None
        or assessment.treatment_status != ACCEPTED
        or assessment.accepted_at is None
    ):
        return None
    before = {f: getattr(assessment, f) for f in ACCEPTANCE_FIELDS}
    clear_acceptance(assessment)
    return assessment, before


async def org_document(db: AsyncSession, org_id: UUID, document_id: UUID) -> GeneratedDocument:
    """The organisation's document, or LookupError: never another org's."""
    doc = (await db.execute(
        select(GeneratedDocument).where(
            GeneratedDocument.id == document_id,
            GeneratedDocument.organization_id == org_id,
        )
    )).scalar_one_or_none()
    if doc is None:
        raise LookupError("No such document in this organisation")
    return doc


def expiry_recipients(
    accepted_by: Optional[UUID],
    owner_user_id: Optional[UUID],
    member_ids: Iterable[UUID],
    admin_ids: Iterable[UUID],
) -> set:
    """Who hears that an acceptance expired: its approver and the risk owner.

    Only while they are still members of the organisation: a notice for
    someone who has left is one nobody sees. With neither left, the
    organisation's admins, so an expiry always reaches someone who can renew
    or withdraw it.
    """
    members = set(member_ids)
    named = {u for u in (accepted_by, owner_user_id) if u is not None and u in members}
    return named or {u for u in admin_ids if u is not None}
