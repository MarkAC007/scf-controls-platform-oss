"""Organisation compensating controls (PIG R9).

A compensating control with no vendor is recorded against one of the
organisation's controls: the gap, what compensates for it, and how effective
that is. Compensating for a material control is a decision to live with the
gap, so it needs the risk acceptance R7 records: an accepted risk linked to
the control, with an approval in force when the compensation is recorded,
named in ``risk_code``. On any other control ``risk_code`` is optional, and
when given it must still be a risk linked to the control that the
organisation has assessed.

A risk is linked to a control when the catalogue maps the control to it
(``SCFCatalogControl.risk_codes``) or the organisation has linked its own
custom risk to it (``CustomRiskControlMapping``), the link a standards
exception is approved for.

The guard runs when a record is written. ``standing`` reports where each
record stands now: whether its control is material, the acceptance state of
its risk while that risk is still linked to the control, and whether it is
still justified, so a lapse, a withdrawal or a control that became material
afterwards shows.
"""
from datetime import date
from typing import Dict, Iterable, Optional, Set, Tuple
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from catalog_models import SCFCatalogControl
from models import CustomRiskControlMapping, RiskAssessment, ScopedControl, VendorCompensatingControl
from services.materiality import default_material
from services.risk_acceptance import STATE_APPROVED, acceptance_state, state_filter

ENTITY_TYPE = "compensating_control"

COMPENSATING_TRACKED_FIELDS = frozenset({
    "gap_description",
    "compensating_control",
    "effectiveness_rating",
    "risk_reduction_notes",
    "scoped_control_id",
    "scf_id",
    "risk_code",
})


def is_org_level():
    """SQL: a compensating control with no vendor, over VendorCompensatingControl rows."""
    return VendorCompensatingControl.vendor_id.is_(None)


def tracked_values(row: VendorCompensatingControl) -> Dict[str, object]:
    return {f: getattr(row, f) for f in COMPENSATING_TRACKED_FIELDS}


async def linked_risk_codes(db: AsyncSession, org_id: UUID, scf_id: str) -> Set[str]:
    """The risks linked to this control: the catalogue's and the organisation's own."""
    catalog = await db.scalar(
        select(SCFCatalogControl.risk_codes).where(SCFCatalogControl.scf_id == scf_id)
    )
    custom = (await db.execute(
        select(CustomRiskControlMapping.risk_code).where(
            CustomRiskControlMapping.organization_id == org_id,
            func.upper(CustomRiskControlMapping.scf_id) == scf_id,
        )
    )).scalars().all()
    return set(catalog or []) | set(custom)


async def is_material_here(db: AsyncSession, org_id: UUID, scf_id: str) -> bool:
    """Whether this control is material for the organisation, failing closed.

    As ``materiality.is_material``: the organisation's override, else the
    catalogue weighting. A control the catalogue does not know (and the
    organisation no longer scopes) is material: unknown is never a pass, as
    for the deficiency gates.
    """
    override = await db.scalar(
        select(ScopedControl.material_override).where(
            ScopedControl.organization_id == org_id,
            ScopedControl.scf_id == scf_id,
        )
    )
    if override is not None:
        return bool(override)
    row = (await db.execute(
        select(SCFCatalogControl.control_weighting).where(SCFCatalogControl.scf_id == scf_id)
    )).first()
    return True if row is None else default_material(row[0])


async def _accepted_in_force(
    db: AsyncSession, org_id: UUID, codes: Iterable[str], today: date
) -> list:
    codes = sorted(codes)
    if not codes:
        return []
    return sorted((await db.execute(
        select(RiskAssessment.risk_code).where(
            RiskAssessment.organization_id == org_id,
            RiskAssessment.risk_code.in_(codes),
            state_filter(STATE_APPROVED, today),
        )
    )).scalars().all())


async def justification_problem(
    db: AsyncSession,
    org_id: UUID,
    scf_id: str,
    risk_code: Optional[str],
    today: Optional[date] = None,
) -> Optional[str]:
    """Why compensating for this control with this risk code is refused, or None."""
    today = today or date.today()
    material = await is_material_here(db, org_id, scf_id)
    linked = await linked_risk_codes(db, org_id, scf_id)
    if risk_code is None:
        if not material:
            return None
        in_force = await _accepted_in_force(db, org_id, linked, today)
        hint = (
            f" Accepted risks linked to it with an approval in force: {', '.join(in_force)}."
            if in_force else
            " None of the risks linked to it has an accepted, approved acceptance in force."
        )
        return (
            f"{scf_id} is a material control: compensating for it needs an accepted risk "
            f"linked to it with an approval in force, named in risk_code.{hint}"
        )
    if risk_code not in linked:
        return f"{risk_code} is not a risk linked to {scf_id}"
    assessment = (await db.execute(
        select(RiskAssessment).where(
            RiskAssessment.organization_id == org_id,
            RiskAssessment.risk_code == risk_code,
        )
    )).scalar_one_or_none()
    if assessment is None:
        return f"{risk_code} is not a risk this organisation has assessed"
    if material and acceptance_state(assessment, today) != STATE_APPROVED:
        return (
            f"{scf_id} is a material control: {risk_code} needs to be accepted, with an "
            "approval in force, before it can justify compensating for it"
        )
    return None


async def acceptance_states(
    db: AsyncSession, org_id: UUID, codes: Iterable[str], today: Optional[date] = None
) -> Dict[str, Optional[str]]:
    """``acceptance_state`` of each assessed risk code (absent when not assessed)."""
    codes = sorted({c for c in codes if c})
    if not codes:
        return {}
    today = today or date.today()
    rows = (await db.execute(
        select(RiskAssessment).where(
            RiskAssessment.organization_id == org_id,
            RiskAssessment.risk_code.in_(codes),
        )
    )).scalars().all()
    return {row.risk_code: acceptance_state(row, today) for row in rows}


async def standing(
    db: AsyncSession, org_id: UUID, rows: Iterable[VendorCompensatingControl], today: Optional[date] = None
) -> Dict[UUID, Tuple[bool, Optional[str], bool]]:
    """``(material, risk_acceptance_state, justified)`` for each row, as things stand now.

    The acceptance state is shown only while the risk is still linked to the
    row's control: a risk unlinked (or deleted, and its code reused) no
    longer speaks for it. A row is justified when its control is not
    material, or its risk is accepted with an approval in force.
    """
    rows = list(rows)
    today = today or date.today()
    states = await acceptance_states(db, org_id, [r.risk_code for r in rows], today)
    material: Dict[str, bool] = {}
    linked: Dict[str, Set[str]] = {}
    for scf_id in {r.scf_id for r in rows}:
        material[scf_id] = await is_material_here(db, org_id, scf_id)
        linked[scf_id] = await linked_risk_codes(db, org_id, scf_id)
    out = {}
    for r in rows:
        state = states.get(r.risk_code) if r.risk_code in linked[r.scf_id] else None
        out[r.id] = (material[r.scf_id], state, not material[r.scf_id] or state == STATE_APPROVED)
    return out
