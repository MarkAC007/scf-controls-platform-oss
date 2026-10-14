"""Control deficiency record, a plan of action and milestones (PIG R8).

A deficiency is an organisation's action item with no vendor
(:class:`models.VendorActionItem`): the same owner, due date, priority and
status as a vendor action item, raised against a scoped control or a risk.

A finding from a capability test (PIG R11, ``services/capability_tests.py``)
is one too, linked to its test. Any deficiency may carry a root cause.

Three events raise one automatically, against the control concerned:

* a control's implementation status becoming ``at_risk``;
* its composite evidence assessment becoming ``insufficient``;
* a reviewer rejecting evidence mapped to it.

A control at risk that comes (back) into scope counts as going at risk.

Each is raised once: while an open deficiency from the same source (and, for
a rejection, the same evidence) stands against the control, another is not
added. Nothing closes one automatically. Completing it needs a retest note,
which the database also enforces; cancelling it does not.

``open`` and ``in_progress`` are open, and an open deficiency counts until
someone closes it: descoping or deleting its control does not. It keeps the
control's ``scf_id``, so its domain survives the control. A deficiency is
material when its control is (:mod:`services.materiality`); once the control
is deleted, when the catalogue weights it so, or when the catalogue no longer
knows it (failing closed). One raised against a risk alone has no control,
and so is never material.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Dict, List, Optional
from uuid import UUID

from sqlalchemy import and_, case, func, select, text, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from catalog_models import SCFCatalogControl
from models import AuditLog, ScopedControl, VendorActionItem
from services.audit_service import log_entity_changes
from services.materiality import MATERIAL_WEIGHTING, material_expr

OPEN_STATUSES = ("open", "in_progress")
COMPLETED = "completed"
CANCELLED = "cancelled"
CLOSED_STATUSES = (COMPLETED, CANCELLED)

SOURCE_AT_RISK = "control_at_risk"
SOURCE_COMPOSITE = "composite_insufficient"
SOURCE_REJECTED = "evidence_rejected"
SOURCES = (SOURCE_AT_RISK, SOURCE_COMPOSITE, SOURCE_REJECTED)
#: A finding recorded with a capability test (PIG R11), with the test in
#: ``capability_test_id``. Raised by a person, so not ``auto_generated``.
SOURCE_CAPABILITY_TEST = "capability_test"

#: Fields written to the audit log for a deficiency.
DEFICIENCY_TRACKED_FIELDS = frozenset({
    "title",
    "description",
    "priority",
    "status",
    "owner_name",
    "owner_user_id",
    "due_date",
    "completed_date",
    "scoped_control_id",
    "scf_id",
    "risk_code",
    "source",
    "source_ref",
    "retest_note",
    "closed_by_user_id",
    "capability_test_id",
    "root_cause",
})

ENTITY_TYPE = "deficiency"


def is_deficiency():
    """SQL: an action item that is a deficiency, over VendorActionItem rows."""
    return VendorActionItem.vendor_id.is_(None)


def is_open():
    return VendorActionItem.status.in_(OPEN_STATUSES)


def tracked_values(item: VendorActionItem) -> Dict[str, object]:
    return {f: getattr(item, f) for f in DEFICIENCY_TRACKED_FIELDS}


def _title(source: str, scf_id: str, source_ref: Optional[str]) -> str:
    if source == SOURCE_AT_RISK:
        return f"Control {scf_id} is at risk"
    if source == SOURCE_COMPOSITE:
        return f"Evidence for control {scf_id} is insufficient"
    return f"Evidence {source_ref} was rejected for control {scf_id}"


def _new_item(control: ScopedControl, source: str, source_ref: Optional[str]) -> VendorActionItem:
    return VendorActionItem(
        organization_id=control.organization_id,
        vendor_id=None,
        scoped_control_id=control.id,
        scf_id=control.scf_id,
        title=_title(source, control.scf_id, source_ref)[:255],
        priority="medium",
        status="open",
        owner_user_id=control.owner_user_id,
        auto_generated=True,
        source=source,
        source_ref=source_ref,
    )


def _standing(control_id: UUID, source: str, source_ref: Optional[str]):
    """SQL: an open deficiency already raised from this source for this control."""
    conds = [
        is_deficiency(),
        is_open(),
        VendorActionItem.scoped_control_id == control_id,
        VendorActionItem.source == source,
    ]
    conds.append(
        VendorActionItem.source_ref.is_(None) if source_ref is None
        else VendorActionItem.source_ref == source_ref
    )
    return select(VendorActionItem.id).where(*conds).limit(1)


async def raise_for_control(
    db: AsyncSession,
    control: ScopedControl,
    source: str,
    source_ref: Optional[str] = None,
    actor_user_id: Optional[UUID] = None,
    action_source: Optional[str] = None,
    request_id: Optional[UUID] = None,
) -> Optional[VendorActionItem]:
    """Raise an automatic deficiency against ``control``; None if one stands.

    Added to the caller's transaction with its audit rows; the caller
    commits. A control out of scope raises nothing.
    """
    if not control.selected:
        return None
    if (await db.execute(_standing(control.id, source, source_ref))).first():
        return None
    item = _new_item(control, source, source_ref)
    db.add(item)
    await db.flush()
    await log_entity_changes(
        db=db,
        organization_id=control.organization_id,
        entity_type=ENTITY_TYPE,
        entity_id=item.id,
        action="create",
        changed_by_user_id=actor_user_id,
        old_values={},
        new_values=tracked_values(item),
        scf_id=control.scf_id,
        tracked_fields=DEFICIENCY_TRACKED_FIELDS,
        action_source=action_source or "system",
        request_id=request_id,
    )
    return item


async def on_control_status(
    db: AsyncSession,
    control: ScopedControl,
    previous_status: Optional[str],
    actor_user_id: Optional[UUID] = None,
    action_source: Optional[str] = None,
    request_id: Optional[UUID] = None,
    previous_selected: Optional[bool] = None,
) -> Optional[VendorActionItem]:
    """Raise a deficiency when a write has just put ``control`` at risk.

    Going at risk is either the status changing to ``at_risk`` or an at-risk
    control coming into scope (``previous_selected`` False). A write that
    leaves an in-scope control at risk raises nothing: one closed while the
    control stayed at risk is not raised again on every later edit.
    """
    if control.implementation_status != "at_risk":
        return None
    if previous_status == "at_risk" and previous_selected is not False:
        return None
    return await raise_for_control(
        db, control, SOURCE_AT_RISK, None, actor_user_id, action_source, request_id,
    )


_CONTROLS_FOR_EVIDENCE = text(
    "SELECT jsonb_array_elements_text(control_mappings) FROM scf_catalog_evidence "
    "WHERE evidence_id = :evidence_id AND status = 'active' "
    "AND jsonb_typeof(control_mappings) = 'array'"
)


async def on_evidence_rejected(
    db: AsyncSession,
    organization_id: UUID,
    evidence_id: str,
    actor_user_id: Optional[UUID] = None,
    action_source: Optional[str] = None,
    request_id: Optional[UUID] = None,
) -> List[VendorActionItem]:
    """Raise a deficiency on every in-scope control the rejected evidence supports.

    The catalogue maps evidence to controls; one rejection weakens each of
    them. A retired evidence request maps to nothing, as in the composite.
    """
    scf_ids = sorted({
        r[0] for r in (await db.execute(_CONTROLS_FOR_EVIDENCE, {"evidence_id": evidence_id})).all()
    })
    if not scf_ids:
        return []
    controls = (await db.execute(
        select(ScopedControl).where(
            ScopedControl.organization_id == organization_id,
            ScopedControl.scf_id.in_(scf_ids),
            ScopedControl.selected.is_(True),
        ).order_by(ScopedControl.scf_id)
    )).scalars().all()
    raised = []
    for control in controls:
        item = await raise_for_control(
            db, control, SOURCE_REJECTED, evidence_id, actor_user_id, action_source, request_id,
        )
        if item is not None:
            raised.append(item)
    return raised


def on_composite_insufficient_sync(
    session: Session, organization_id: UUID, scf_id: str
) -> Optional[VendorActionItem]:
    """Raise a deficiency when a control's composite has just gone insufficient.

    For the composite worker, which has a synchronous session and no user:
    the audit rows are attributed to the platform (``changed_by_user_id``
    NULL, ``action_source`` ``system``), as the quarantine worker's are. The
    caller commits.
    """
    control = session.execute(
        select(ScopedControl).where(
            ScopedControl.organization_id == organization_id,
            ScopedControl.scf_id == scf_id,
        )
    ).scalars().first()
    if control is None or not control.selected:
        return None
    if session.execute(_standing(control.id, SOURCE_COMPOSITE, None)).first():
        return None
    item = _new_item(control, SOURCE_COMPOSITE, None)
    session.add(item)
    session.flush()
    for field, value in sorted(tracked_values(item).items()):
        session.add(AuditLog(
            organization_id=organization_id,
            entity_type=ENTITY_TYPE,
            entity_id=item.id,
            action="create",
            field_name=field,
            old_value=None,
            new_value=json.dumps(value, default=str),
            scf_id=scf_id,
            changed_by_user_id=None,
            action_source="system",
        ))
    return item


def blank_to_none(value: Optional[str]) -> Optional[str]:
    """Free text as stored: trimmed, and None when nothing is left."""
    return value.strip() if value and value.strip() else None


def closure_problem(status: str, retest_note: Optional[str]) -> Optional[str]:
    """Why a deficiency cannot take ``status`` with this retest note, or None."""
    if status == COMPLETED and not (retest_note or "").strip():
        return "Completing a deficiency needs a retest note: what was retested, and the result"
    return None


def apply_status(
    item: VendorActionItem, new_status: str, user_id: Optional[UUID], today: Optional[date] = None,
) -> None:
    """Record who closed a deficiency and when; reopening clears both.

    A reopened deficiency also loses its retest note, so closing it again
    needs a fresh retest. The audit log keeps the old one.
    """
    previous = item.status
    item.status = new_status
    if new_status in CLOSED_STATUSES:
        if previous != new_status:
            item.closed_by_user_id = user_id
        item.completed_date = (today or date.today()) if new_status == COMPLETED else None
    elif previous in CLOSED_STATUSES:
        item.closed_by_user_id = None
        item.completed_date = None
        item.retest_note = None


def material_deficiency():
    """SQL: a deficiency is material, over VendorActionItem outer-joined to its control.

    While the control exists, its own materiality (override included). Once
    it is deleted, the catalogue's weighting for the ``scf_id`` it kept, and
    material if the catalogue no longer knows it: unknown is never a pass.
    """
    catalog_default = (
        select(SCFCatalogControl.control_weighting >= MATERIAL_WEIGHTING)
        .where(SCFCatalogControl.scf_id == VendorActionItem.scf_id)
        .correlate(VendorActionItem)
        .scalar_subquery()
    )
    return and_(
        VendorActionItem.scf_id.isnot(None),
        case(
            (ScopedControl.id.is_(None), func.coalesce(catalog_default, true())),
            else_=material_expr(),
        ),
    )


def _with_control(query, organization_id: UUID):
    return query.outerjoin(
        ScopedControl,
        and_(
            ScopedControl.id == VendorActionItem.scoped_control_id,
            ScopedControl.organization_id == organization_id,
        ),
    )


async def open_counts(db: AsyncSession, organization_id: UUID) -> Dict[str, object]:
    """Open deficiencies per control and per domain.

    Counted by the ``scf_id`` each deficiency keeps, so one whose control was
    descoped or deleted still counts. One raised against a risk alone has no
    control and no domain; it is counted as ``unlinked``.
    """
    rows = (await db.execute(
        _with_control(
            select(VendorActionItem.scf_id, material_deficiency()), organization_id,
        ).where(
            VendorActionItem.organization_id == organization_id,
            is_deficiency(),
            is_open(),
        )
    )).all()
    per_control: Dict[str, Dict[str, object]] = {}
    unlinked = 0
    for scf_id, material in rows:
        if scf_id is None:
            unlinked += 1
            continue
        entry = per_control.setdefault(scf_id, {"scf_id": scf_id, "open_count": 0, "material": False})
        entry["open_count"] += 1
        entry["material"] = entry["material"] or bool(material)
    by_control = [per_control[k] for k in sorted(per_control)]
    domains: Dict[str, int] = {}
    for entry in by_control:
        code = domain_of(entry["scf_id"])
        domains[code] = domains.get(code, 0) + entry["open_count"]
    return {
        "open_total": len(rows),
        "material_open_total": sum(1 for _, material in rows if material),
        "unlinked_open": unlinked,
        "by_control": by_control,
        "by_domain": [{"domain": d, "open_count": n} for d, n in sorted(domains.items())],
    }


def domain_of(scf_id: Optional[str]) -> Optional[str]:
    return scf_id.split("-", 1)[0] if scf_id else None


async def count_open(
    db: AsyncSession, organization_id: UUID, scf_clause=None, material_only: bool = False,
) -> int:
    """Open deficiencies for the journey checks.

    ``scf_clause`` is a predicate over ``VendorActionItem.scf_id`` (a domain
    filter); with it, or with ``material_only``, only deficiencies against a
    control are counted, since only those have a domain or a materiality.
    """
    conds = [
        VendorActionItem.organization_id == organization_id,
        is_deficiency(),
        is_open(),
    ]
    query = select(func.count(VendorActionItem.id))
    if scf_clause is not None:
        conds.append(scf_clause)
    if material_only:
        query = _with_control(query, organization_id)
        conds.append(material_deficiency())
    return int(await db.scalar(query.where(and_(*conds))) or 0)
