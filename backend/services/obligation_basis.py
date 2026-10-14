"""Obligation basis: why a control is in scope (PIG R2).

A control is in scope because some obligation put it there. Either it is a
minimum compliance requirement (``mcr``) owed to a statute, regulator or
contract, or it is a discretionary security requirement (``dsr``) the
organisation chose for itself. The difference decides what an exclusion
needs and what an auditor is shown.

The basis enters on the framework selection, not the catalogue. A
selection's ``basis`` says why that framework is in scope. A control's basis
is derived from the active selections that map it, through the same
``framework_mappings`` crosswalk that bulk scope and engagements use. The
strongest basis wins: one MCR framework makes the control MCR, however many
DSR frameworks also map it.

A per-control ``basis_override`` replaces the derived value and always
carries a reason. An absent basis stays absent. A selection nobody has
classified contributes nothing, and a control with no classified selection
reads as unclassified. Unclassified is not counted as either basis.

There are two forms of the derivation, and they must agree:

* ``derive_basis`` is the Python form, for code that already holds catalogue
  rows (summary, preview, engagement freeze, the exclusion guard).
* ``effective_basis_expr`` is the SQL form, for filtering and counting in
  the database (the scoping list and journey checks).
"""
from datetime import datetime
from typing import Dict, Iterable, Optional, Sequence, Tuple
from uuid import UUID

from sqlalchemy import and_, case, literal, select
from sqlalchemy.dialects.postgresql import array
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from catalog_models import SCFCatalogControl
from models import OrganizationFrameworkSelection, ScopedControl

BASES = ("mcr", "dsr")
OBLIGATION_TYPES = ("statutory", "regulatory", "contractual", "discretionary")
_RANK = {"mcr": 2, "dsr": 1}


def strongest(bases: Iterable[Optional[str]]) -> Optional[str]:
    """The strongest basis present, or None when nothing is classified."""
    best: Optional[str] = None
    for basis in bases:
        if basis in _RANK and (best is None or _RANK[basis] > _RANK[best]):
            best = basis
    return best


def rank(basis: Optional[str]) -> int:
    """Ordering for downgrade detection: mcr > dsr > unclassified."""
    return _RANK.get(basis or "", 0)


def derive_basis(
    mapping_keys: Iterable[str],
    basis_by_framework: Dict[str, str],
    override: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """(basis, source) for one control.

    ``source`` is ``"override"`` when a per-control override decided it,
    ``"framework"`` when an active selection did, and None when unclassified.
    """
    if override in _RANK:
        return override, "override"
    derived = strongest(basis_by_framework.get(fw) for fw in mapping_keys)
    return derived, ("framework" if derived else None)


async def selection_bases(db: AsyncSession, org_id: UUID) -> Dict[str, str]:
    """framework_id → basis for the org's active, classified selections."""
    rows = await db.execute(
        select(OrganizationFrameworkSelection.framework_id, OrganizationFrameworkSelection.basis).where(
            OrganizationFrameworkSelection.organization_id == org_id,
            OrganizationFrameworkSelection.active.is_(True),
            OrganizationFrameworkSelection.basis.isnot(None),
        )
    )
    return {fw: basis for fw, basis in rows.fetchall()}


async def effective_bases(
    db: AsyncSession, org_id: UUID, scf_ids: Iterable[str]
) -> Dict[str, Optional[str]]:
    """scf_id → effective basis for the named controls."""
    wanted = sorted(set(scf_ids))
    if not wanted:
        return {}
    by_framework = await selection_bases(db, org_id)
    mappings = dict(
        (await db.execute(
            select(SCFCatalogControl.scf_id, SCFCatalogControl.framework_mappings).where(
                SCFCatalogControl.scf_id.in_(wanted)
            )
        )).fetchall()
    )
    overrides = dict(
        (await db.execute(
            select(ScopedControl.scf_id, ScopedControl.basis_override).where(
                ScopedControl.organization_id == org_id,
                ScopedControl.scf_id.in_(wanted),
            )
        )).fetchall()
    )
    return {
        scf_id: derive_basis(
            (mappings.get(scf_id) or {}).keys(), by_framework, overrides.get(scf_id)
        )[0]
        for scf_id in wanted
    }


def _classified_selection_exists(scf_id_col, org_id_col, basis: str):
    """EXISTS an active selection with this basis whose framework maps the control.

    The catalogue is aliased so the subquery never auto-correlates into an
    outer query that already selects from the catalogue.
    """
    cat = aliased(SCFCatalogControl)
    return (
        select(literal(1))
        .select_from(OrganizationFrameworkSelection)
        .join(cat, cat.scf_id == scf_id_col)
        .where(
            OrganizationFrameworkSelection.organization_id == org_id_col,
            OrganizationFrameworkSelection.active.is_(True),
            OrganizationFrameworkSelection.basis == basis,
            cat.framework_mappings.has_key(OrganizationFrameworkSelection.framework_id),
        )
        .exists()
    )


def effective_basis_expr(scf_id_col, org_id_col, override_col):
    """SQL form of ``derive_basis``: override, else strongest classified selection."""
    return case(
        (override_col.isnot(None), override_col),
        (_classified_selection_exists(scf_id_col, org_id_col, "mcr"), literal("mcr")),
        (_classified_selection_exists(scf_id_col, org_id_col, "dsr"), literal("dsr")),
        else_=None,
    )


def scoped_control_basis_expr():
    """``effective_basis_expr`` bound to a query over ScopedControl rows."""
    return effective_basis_expr(
        ScopedControl.scf_id, ScopedControl.organization_id, ScopedControl.basis_override
    )


# ---------------------------------------------------------------------------
# Excluding an MCR control
# ---------------------------------------------------------------------------


def mcr_exclusion_message(scf_id: str) -> str:
    return (
        f"{scf_id} is a minimum compliance requirement (MCR). Excluding it needs a "
        "reason and an organisation admin: an admin uses the scope-override "
        "endpoint with action 'exclude' and a reason, and is recorded as the approver."
    )


async def is_mcr(db: AsyncSession, org_id: UUID, scf_id: str) -> bool:
    return (await effective_bases(db, org_id, [scf_id])).get(scf_id) == "mcr"


def require_approver(approver_user_id: Optional[UUID]) -> UUID:
    """The admin approving an MCR exclusion, or PermissionError.

    The approver is never named in the request: the caller passes its own id
    only when the authenticated membership is an organisation admin. An id
    typed into a payload would let an editor record an approval nobody gave.
    """
    if approver_user_id is None:
        raise PermissionError(
            "Excluding an MCR control needs an admin of this organisation; "
            "the admin who makes the change is recorded as the approver"
        )
    return approver_user_id


async def unscope_touches_mcr(
    db: AsyncSession, org_id: UUID, framework_ids: Sequence[str]
) -> bool:
    """Whether removing these frameworks could strip an MCR obligation.

    True when one of them is an active MCR selection, or when an in-scope
    control overridden to MCR is mapped to one of them. Conservative: a
    control another framework keeps in scope still counts, so the check never
    under-reports.
    """
    wanted = sorted(set(framework_ids))
    if not wanted:
        return False
    mcr_selection = await db.scalar(
        select(literal(1)).where(
            select(OrganizationFrameworkSelection.id)
            .where(
                OrganizationFrameworkSelection.organization_id == org_id,
                OrganizationFrameworkSelection.framework_id.in_(wanted),
                OrganizationFrameworkSelection.active.is_(True),
                OrganizationFrameworkSelection.basis == "mcr",
            )
            .exists()
        )
    )
    if mcr_selection:
        return True
    mcr_override = await db.scalar(
        select(literal(1)).where(
            select(ScopedControl.id)
            .join(SCFCatalogControl, SCFCatalogControl.scf_id == ScopedControl.scf_id)
            .where(
                ScopedControl.organization_id == org_id,
                ScopedControl.selected.is_(True),
                ScopedControl.basis_override == "mcr",
                SCFCatalogControl.framework_mappings.has_any(array(wanted)),
            )
            .exists()
        )
    )
    return bool(mcr_override)


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

SELECTION_BASIS_FIELDS = ("basis", "obligation_type", "obligation_source")
CONTROL_BASIS_FIELDS = (
    "basis_override",
    "basis_override_reason",
    "basis_override_set_at",
    "basis_override_set_by",
)


async def set_selection_basis(
    db: AsyncSession,
    org_id: UUID,
    framework_id: str,
    basis: Optional[str],
    obligation_type: Optional[str],
    obligation_source: Optional[str],
) -> Tuple[OrganizationFrameworkSelection, dict]:
    """Record why a scoped framework is in scope. Returns (selection, old values).

    Only an active selection can be classified. A framework the org has not
    scoped has no obligation to describe, and a stale inactive row would carry
    a basis into the next re-scope unnoticed.
    """
    selection = (await db.execute(
        select(OrganizationFrameworkSelection).where(
            OrganizationFrameworkSelection.organization_id == org_id,
            OrganizationFrameworkSelection.framework_id == framework_id,
            OrganizationFrameworkSelection.active.is_(True),
        )
    )).scalar_one_or_none()
    if selection is None:
        raise LookupError(f"Framework '{framework_id}' is not scoped for this organisation")
    old = {f: getattr(selection, f) for f in SELECTION_BASIS_FIELDS}
    selection.basis = basis
    selection.obligation_type = obligation_type
    selection.obligation_source = (obligation_source or "").strip() or None
    return selection, old


async def set_control_basis_override(
    db: AsyncSession,
    org_id: UUID,
    scf_id: str,
    basis: Optional[str],
    reason: Optional[str],
    user_id: Optional[UUID],
) -> ScopedControl:
    """Set or clear one control's basis override. A set override needs a reason."""
    control = (await db.execute(
        select(ScopedControl).where(
            and_(
                ScopedControl.organization_id == org_id,
                ScopedControl.scf_id == scf_id,
            )
        )
    )).scalar_one_or_none()
    if control is None:
        raise LookupError(f"{scf_id} has no scope record in this organisation")
    if basis is None:
        control.basis_override = None
        control.basis_override_reason = None
        control.basis_override_set_at = None
        control.basis_override_set_by = None
    else:
        cleaned = (reason or "").strip()
        if not cleaned:
            raise ValueError("A basis override needs a reason")
        control.basis_override = basis
        control.basis_override_reason = cleaned
        control.basis_override_set_at = datetime.utcnow()
        control.basis_override_set_by = user_id
    control.updated_by_user_id = user_id
    return control
