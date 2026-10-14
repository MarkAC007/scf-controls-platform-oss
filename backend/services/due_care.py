"""Due-care pack (PIG R10): control validation, posture snapshots, governance data.

Three records the SCRMS-PIG due-care steps (27 to 30) ask the governing body
to see, and that the platform did not keep:

* **Control validation.** Each in-scope control has a validation cadence
  (the catalogue's, unless the organisation set its own). A person records a
  validation; the cadence then gives the next validation date. A control
  never validated has no date: it is ``never``, not "due from some date the
  platform made up", and it counts as not validated.
* **Posture snapshots.** Once a month, counts of the in-scope controls by
  implementation status, obligation basis and materiality, and of open
  deficiencies. One per organisation per month, never updated, so "what did
  it look like a quarter ago" has an answer.
* **Governance data.** What the Governance Oversight Report generator reads:
  everything above plus risk acceptances, deficiencies, vendors and
  compensating controls, gathered on the synchronous session document
  generation runs in.

The SQL here is built once and executed by either an async session (the API,
the scheduled tasks) or a sync one (document generation).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple
from uuid import UUID

from dateutil.relativedelta import relativedelta
from sqlalchemy import func, literal_column, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from catalog_models import SCFCatalogControl
from models import (
    OrganizationFrameworkSelection,
    PostureSnapshot,
    RiskAssessment,
    ScopedControl,
    Vendor,
    VendorActionItem,
    VendorCompensatingControl,
)
from services.doc_gen.context import IMPLEMENTED_STATUSES  # as the Tier 1 documents count them
from services.deficiencies import _with_control, is_deficiency, is_open, material_deficiency
from services.materiality import material_expr
from services.obligation_basis import scoped_control_basis_expr
from services.risk_acceptance import STATE_APPROVED, STATE_EXPIRED, STATE_UNAPPROVED, state_filter

SNAPSHOT_ENTITY_TYPE = "posture_snapshot"
TRIGGER_SCHEDULED = "scheduled"
TRIGGER_MANUAL = "manual"

#: Months between validations, by cadence as the catalogue writes it
#: (lower-cased). Anything else is an unknown cadence: no next date can be
#: computed, so the control never counts as validated within its cadence.
CADENCE_MONTHS = {
    "annual": 12,
    "semi-annual": 6,
    "quarterly": 3,
    "monthly": 1,
}

STATE_NEVER = "never"
STATE_UNKNOWN_CADENCE = "unknown_cadence"
STATE_OVERDUE = "overdue"
STATE_DUE_SOON = "due_soon"
STATE_CURRENT = "current"
VALIDATION_STATES = (STATE_CURRENT, STATE_DUE_SOON, STATE_OVERDUE, STATE_NEVER, STATE_UNKNOWN_CADENCE)
#: A validation is within its cadence while its next date has not passed.
WITHIN_CADENCE = (STATE_CURRENT, STATE_DUE_SOON)

#: How far ahead a validation reads as due soon, and is announced.
DUE_SOON_DAYS = 30
NOTICE_DAYS = 14
#: The window the governance report calls "recent".
RECENT_DAYS = 90



# ---------------------------------------------------------------------------
# Validation cadence
# ---------------------------------------------------------------------------


def cadence_months(cadence: Optional[str]) -> Optional[int]:
    return CADENCE_MONTHS.get((cadence or "").strip().lower())


def next_validation_due(last_validated_at: Optional[datetime], cadence: Optional[str]) -> Optional[date]:
    """The date the next validation is due, or None (never validated, or unknown cadence)."""
    months = cadence_months(cadence)
    if last_validated_at is None or months is None:
        return None
    return last_validated_at.date() + relativedelta(months=months)


def validation_state(
    last_validated_at: Optional[datetime], cadence: Optional[str], today: Optional[date] = None
) -> str:
    today = today or date.today()
    if last_validated_at is None:
        return STATE_NEVER
    due = next_validation_due(last_validated_at, cadence)
    if due is None:
        return STATE_UNKNOWN_CADENCE
    if due < today:
        return STATE_OVERDUE
    if due <= today + timedelta(days=DUE_SOON_DAYS):
        return STATE_DUE_SOON
    return STATE_CURRENT


def effective_cadence_expr():
    """SQL: the control's own validation cadence, else the catalogue's, over ScopedControl rows.

    A blank override falls back to the catalogue, as it does in generated
    documents; COALESCE alone would keep the empty string.
    """
    catalog = (
        select(SCFCatalogControl.validation_cadence)
        .where(SCFCatalogControl.scf_id == ScopedControl.scf_id)
        .correlate(ScopedControl)
        .scalar_subquery()
    )
    return func.coalesce(func.nullif(func.trim(ScopedControl.validation_cadence), ""), catalog)


def validation_rows_stmt(org_id: UUID, *extra):
    """In-scope controls with what their validation state is computed from."""
    return (
        select(
            ScopedControl.id,
            ScopedControl.scf_id,
            effective_cadence_expr().label("cadence"),
            ScopedControl.last_validated_at,
            ScopedControl.last_validated_by,
            ScopedControl.last_validation_note,
        )
        .where(
            ScopedControl.organization_id == org_id,
            ScopedControl.selected.is_(True),
            *extra,
        )
        .order_by(ScopedControl.scf_id)
    )


def validation_counts(rows: Iterable[Any], today: Optional[date] = None) -> Dict[str, int]:
    """Count rows of ``validation_rows_stmt`` by validation state."""
    today = today or date.today()
    counts = {state: 0 for state in VALIDATION_STATES}
    for row in rows:
        counts[validation_state(row.last_validated_at, row.cadence, today)] += 1
    return counts


def validation_record(control: ScopedControl, note: str, user_id: Optional[UUID], now: datetime) -> None:
    """Stamp a validation on the control. The caller audits and commits."""
    control.last_validated_at = now
    control.last_validated_by = user_id
    control.last_validation_note = note
    control.updated_by_user_id = user_id


# ---------------------------------------------------------------------------
# Posture
# ---------------------------------------------------------------------------


def _posture_control_stmt(org_id: UUID):
    return select(
        func.coalesce(ScopedControl.implementation_status, "not_started"),
        scoped_control_basis_expr(),
        material_expr(),
    ).where(ScopedControl.organization_id == org_id, ScopedControl.selected.is_(True))


def _posture_deficiency_stmt(org_id: UUID):
    return _with_control(
        select(VendorActionItem.due_date, material_deficiency()), org_id,
    ).where(VendorActionItem.organization_id == org_id, is_deficiency(), is_open())


def summarise_posture(control_rows, deficiency_rows, today: date) -> Dict[str, Any]:
    """The snapshot counts from the two posture queries' rows."""
    status: Dict[str, int] = {}
    basis = {"mcr": 0, "dsr": 0, "none": 0}
    materiality = {"material": 0, "not_material": 0}
    in_scope = 0
    for impl, row_basis, material in control_rows:
        in_scope += 1
        status[impl] = status.get(impl, 0) + 1
        basis[row_basis if row_basis in basis else "none"] += 1
        materiality["material" if material else "not_material"] += 1
    deficiencies = {"open": 0, "material_open": 0, "overdue": 0, "without_due_date": 0}
    for due_date, material in deficiency_rows:
        deficiencies["open"] += 1
        deficiencies["material_open"] += 1 if material else 0
        if due_date is None:
            deficiencies["without_due_date"] += 1
        elif due_date < today:
            deficiencies["overdue"] += 1
    return {
        "in_scope": in_scope,
        "status": dict(sorted(status.items())),
        "basis": basis,
        "materiality": materiality,
        "deficiencies": deficiencies,
    }


async def posture_counts(db: AsyncSession, org_id: UUID, today: Optional[date] = None) -> Dict[str, Any]:
    today = today or date.today()
    controls = (await db.execute(_posture_control_stmt(org_id))).all()
    deficiencies = (await db.execute(_posture_deficiency_stmt(org_id))).all()
    return summarise_posture(controls, deficiencies, today)


def period_of(day: date) -> date:
    """The month a snapshot taken on ``day`` is for: its first day."""
    return day.replace(day=1)


class SnapshotExists(Exception):
    """This organisation already has a snapshot for this month."""


async def take_snapshot(
    db: AsyncSession,
    org_id: UUID,
    trigger: str,
    user_id: Optional[UUID] = None,
    today: Optional[date] = None,
) -> PostureSnapshot:
    """Take this month's snapshot. The caller audits and commits.

    Raises SnapshotExists when the month already has one: a snapshot is a
    record of what the posture was, and taking it again would replace it.
    """
    today = today or date.today()
    period = period_of(today)
    existing = await db.scalar(
        select(PostureSnapshot.id).where(
            PostureSnapshot.organization_id == org_id, PostureSnapshot.period == period,
        )
    )
    if existing is not None:
        raise SnapshotExists(f"A posture snapshot for {period:%Y-%m} has already been taken")
    snapshot = PostureSnapshot(
        organization_id=org_id,
        period=period,
        taken_at=datetime.utcnow(),
        trigger=trigger,
        taken_by_user_id=user_id,
        counts=await posture_counts(db, org_id, today),
    )
    try:
        # A savepoint, so a schedule and a person taking the month's snapshot
        # at once leaves the loser's transaction usable for the 409.
        async with db.begin_nested():
            db.add(snapshot)
    except IntegrityError:
        raise SnapshotExists(f"A posture snapshot for {period:%Y-%m} has already been taken")
    return snapshot


async def take_monthly_snapshots(db: AsyncSession, today: Optional[date] = None) -> int:
    """Take this month's snapshot for every organisation that has none. Run on the first.

    Each organisation is its own transaction, so one failing does not cost
    the others theirs; a month already taken (by a person, or an earlier run)
    is left as it is. Each snapshot is audited as a system write.
    """
    import logging

    from models import Organization
    from services.audit_service import log_entity_changes

    logger = logging.getLogger(__name__)
    today = today or date.today()
    org_ids = (await db.execute(select(Organization.id).order_by(Organization.id))).scalars().all()
    taken = 0
    for org_id in org_ids:
        try:
            snapshot = await take_snapshot(db, org_id, TRIGGER_SCHEDULED, None, today)
            await log_entity_changes(
                db=db,
                organization_id=org_id,
                entity_type=SNAPSHOT_ENTITY_TYPE,
                entity_id=snapshot.id,
                action="create",
                changed_by_user_id=None,
                old_values={},
                new_values=snapshot_values(snapshot),
                action_source="system",
            )
            await db.commit()
            taken += 1
        except SnapshotExists:
            await db.rollback()
        except Exception as exc:  # one organisation must not stop the rest
            logger.error("posture snapshot failed for org %s: %s", org_id, exc, exc_info=True)
            await db.rollback()
    return taken


def snapshot_values(snapshot: PostureSnapshot) -> Dict[str, Any]:
    """The audited fields of a snapshot."""
    return {
        "period": snapshot.period,
        "trigger": snapshot.trigger,
        "taken_by_user_id": snapshot.taken_by_user_id,
        "counts": snapshot.counts,
    }


# ---------------------------------------------------------------------------
# Governance Oversight Report data (synchronous: document generation)
# ---------------------------------------------------------------------------


def _quarter_ago_snapshot(session, org_id, today: date) -> Optional[PostureSnapshot]:
    """The latest snapshot from at least three months before this month."""
    cutoff = period_of(today) - relativedelta(months=3)
    return (
        session.query(PostureSnapshot)
        .filter(PostureSnapshot.organization_id == org_id, PostureSnapshot.period <= cutoff)
        .order_by(PostureSnapshot.period.desc())
        .first()
    )


def governance_data(session, organization_id, today: Optional[date] = None) -> Dict[str, Any]:
    """What the Governance Oversight Report reads, on a synchronous session.

    Plain values only (dates as ISO strings), so the result can be hashed into
    the report's prompt key and rendered without touching the database again.
    """
    from user_display import user_label
    from models import User

    today = today or date.today()
    recent = today - timedelta(days=RECENT_DAYS)
    org_id = organization_id

    control_rows = session.execute(_posture_control_stmt(org_id)).all()
    deficiency_rows = session.execute(_posture_deficiency_stmt(org_id)).all()
    posture = summarise_posture(control_rows, deficiency_rows, today)
    material_implemented = sum(
        1 for impl, _, material in control_rows if material and impl in IMPLEMENTED_STATUSES
    )
    implemented = sum(1 for impl, _, _ in control_rows if impl in IMPLEMENTED_STATUSES)

    validation_rows = session.execute(validation_rows_stmt(org_id)).all()
    validations = validation_counts(validation_rows, today)
    validated_recently = sum(
        1 for r in validation_rows if r.last_validated_at and r.last_validated_at.date() >= recent
    )

    # Open material deficiencies, soonest due first; none due last.
    material_open = session.execute(
        _with_control(
            select(
                VendorActionItem.title,
                VendorActionItem.scf_id,
                VendorActionItem.status,
                VendorActionItem.due_date,
                VendorActionItem.owner_name,
                VendorActionItem.owner_user_id,
            ),
            org_id,
        ).where(
            VendorActionItem.organization_id == org_id, is_deficiency(), is_open(),
            material_deficiency(),
        )
    ).all()
    owners = {
        u.id: user_label(u)
        for u in session.query(User).filter(
            User.id.in_({r.owner_user_id for r in material_open if r.owner_user_id})
        ).all()
    } if any(r.owner_user_id for r in material_open) else {}
    material_list = sorted(
        (
            {
                "title": r.title,
                "scf_id": r.scf_id,
                "status": r.status,
                "due_date": r.due_date.isoformat() if r.due_date else None,
                "owner": owners.get(r.owner_user_id) or r.owner_name,
            }
            for r in material_open
        ),
        key=lambda d: (d["due_date"] is None, d["due_date"] or "", d["scf_id"] or "", d["title"]),
    )
    without_owner = int(session.scalar(
        select(func.count(VendorActionItem.id)).where(
            VendorActionItem.organization_id == org_id, is_deficiency(), is_open(),
            VendorActionItem.owner_user_id.is_(None),
            func.coalesce(func.trim(VendorActionItem.owner_name), "") == "",
        )
    ) or 0)
    # Literal defaults, not bound parameters: Postgres matches a GROUP BY
    # expression to the selected one textually, and two binds never match.
    source_expr = func.coalesce(VendorActionItem.source, literal_column("'manual'"))
    raised_recently: Dict[str, int] = {}
    for source, count in session.execute(
        select(source_expr, func.count(VendorActionItem.id))
        .where(
            VendorActionItem.organization_id == org_id, is_deficiency(),
            VendorActionItem.created_at >= recent,
        )
        .group_by(source_expr)
    ).all():
        raised_recently[source] = int(count)
    completed_recently = int(session.scalar(
        select(func.count(VendorActionItem.id)).where(
            VendorActionItem.organization_id == org_id, is_deficiency(),
            VendorActionItem.status == "completed",
            VendorActionItem.completed_date >= recent,
        )
    ) or 0)

    org_risks = RiskAssessment.organization_id == org_id
    treatment_expr = func.coalesce(RiskAssessment.treatment_status, literal_column("'identified'"))
    treatment: Dict[str, int] = {}
    for status, count in session.execute(
        select(treatment_expr, func.count(RiskAssessment.id)).where(org_risks).group_by(treatment_expr)
    ).all():
        treatment[status] = int(count)
    acceptance = {
        state: int(session.scalar(
            select(func.count(RiskAssessment.id)).where(org_risks, state_filter(state, today))
        ) or 0)
        for state in (STATE_APPROVED, STATE_EXPIRED, STATE_UNAPPROVED)
    }
    expired = [
        {"risk_code": code, "expires_on": expires.isoformat()}
        for code, expires in session.execute(
            select(RiskAssessment.risk_code, RiskAssessment.acceptance_expires_on)
            .where(org_risks, state_filter(STATE_EXPIRED, today))
            .order_by(RiskAssessment.acceptance_expires_on, RiskAssessment.risk_code)
        ).all()
    ]

    vendors: Dict[str, int] = {}
    for criticality, count in session.execute(
        select(Vendor.criticality, func.count(Vendor.id))
        .where(Vendor.organization_id == org_id)
        .group_by(Vendor.criticality)
    ).all():
        vendors[criticality or "unrated"] = int(count)
    vendors_recent = int(session.scalar(
        select(func.count(Vendor.id)).where(Vendor.organization_id == org_id, Vendor.created_at >= recent)
    ) or 0)
    compensating = int(session.scalar(
        select(func.count(VendorCompensatingControl.id)).where(
            VendorCompensatingControl.organization_id == org_id,
            VendorCompensatingControl.vendor_id.is_(None),
        )
    ) or 0)

    frameworks = [
        {"framework_id": fw, "basis": basis}
        for fw, basis in session.execute(
            select(OrganizationFrameworkSelection.framework_id, OrganizationFrameworkSelection.basis)
            .where(
                OrganizationFrameworkSelection.organization_id == org_id,
                OrganizationFrameworkSelection.active.is_(True),
            )
            .order_by(OrganizationFrameworkSelection.framework_id)
        ).all()
    ]

    previous = _quarter_ago_snapshot(session, org_id, today)
    return {
        "as_of": today.isoformat(),
        "recent_days": RECENT_DAYS,
        "posture": posture,
        "implemented": implemented,
        "material_implemented": material_implemented,
        "validation": validations,
        "validated_recently": validated_recently,
        "material_open_deficiencies": material_list,
        "deficiencies_without_owner": without_owner,
        "deficiencies_raised_recently": dict(sorted(raised_recently.items())),
        "deficiencies_completed_recently": completed_recently,
        "risk_treatment": dict(sorted(treatment.items())),
        "risk_acceptance": acceptance,
        "expired_acceptances": expired,
        "vendors_by_criticality": dict(sorted(vendors.items())),
        "vendors_added_recently": vendors_recent,
        "compensating_controls": compensating,
        "frameworks": frameworks,
        "previous_snapshot": (
            {"period": previous.period.isoformat(), "counts": previous.counts} if previous else None
        ),
    }


def red_flags(data: Dict[str, Any]) -> List[str]:
    """The due-care red flags the governance data raises, in report order.

    * Everything green with no documented exceptions: every in-scope control
      implemented, and no open deficiency, accepted risk or compensating
      control on record. The guide reads that as an unexamined programme,
      not a perfect one.
    * Accepted risks whose acceptance has expired.
    * Material deficiencies with no due date.
    """
    flags: List[str] = []
    posture = data["posture"]
    accepted = sum(data["risk_acceptance"].values())
    if (
        posture["in_scope"]
        and data["implemented"] == posture["in_scope"]
        and posture["deficiencies"]["open"] == 0
        and accepted == 0
        and data["compensating_controls"] == 0
    ):
        flags.append(
            "Everything is green with no documented exceptions: every in-scope control is "
            "implemented, and there is no open deficiency, accepted risk or compensating control."
        )
    if data["expired_acceptances"]:
        codes = ", ".join(e["risk_code"] for e in data["expired_acceptances"])
        flags.append(
            f"{len(data['expired_acceptances'])} accepted risk(s) past the expiry of their "
            f"acceptance: {codes}."
        )
    undated = [d for d in data["material_open_deficiencies"] if d["due_date"] is None]
    if undated:
        flags.append(f"{len(undated)} material deficiency(ies) without a due date.")
    return flags


def validation_summary(rows: Iterable[Any], today: date) -> List[Dict[str, Any]]:
    """Rows of ``validation_rows_stmt`` as API dicts, with next date and state."""
    out = []
    for r in rows:
        out.append({
            "scoped_control_id": r.id,
            "scf_id": r.scf_id,
            "validation_cadence": r.cadence,
            "last_validated_at": r.last_validated_at,
            "last_validated_by": r.last_validated_by,
            "last_validation_note": r.last_validation_note,
            "next_validation_due": next_validation_due(r.last_validated_at, r.cadence),
            "validation_state": validation_state(r.last_validated_at, r.cadence, today),
        })
    return out


def due_for_notice(rows: Iterable[Any], today: date) -> List[Tuple[Any, date]]:
    """Validated controls whose next validation is due within NOTICE_DAYS, or past."""
    out = []
    horizon = today + timedelta(days=NOTICE_DAYS)
    for r in rows:
        due = next_validation_due(r.last_validated_at, r.cadence)
        if due is not None and due <= horizon:
            out.append((r, due))
    return out
