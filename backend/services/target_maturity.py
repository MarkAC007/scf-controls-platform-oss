"""Target maturity: what level each control is meant to reach (PIG R5).

Two sources, in precedence order:

1. A per-control override on ``scoped_controls.target_maturity_override``,
   which always carries a reason.
2. The target for the control's domain, stored in ``Organization.settings``
   under ``maturity_targets`` as ``{"GOV": "L3", "RSK": "L2", ...}``.

A control in a domain with no target and no override has no target. Nothing
here invents one: a control without a target is a gap the
``controls_with_target`` gate is there to show, not something to fill with a
default.

A control belongs to a domain by its ``scf_id`` prefix, the same attribution
the journey's domain filter uses (``services/journey.py`` ``_prefix_predicate``),
so a gate filtered to GOV and the GOV target always mean the same controls.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Mapping, Optional, Tuple
from uuid import UUID

from sqlalchemy import and_, case, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from catalog_models import SCFCatalogDomain
from models import ScopedControl

#: The CMM levels, lowest first. Single digits, so they also sort as strings.
MATURITY_LEVELS = ("L0", "L1", "L2", "L3", "L4", "L5")

#: Key in ``Organization.settings`` holding the domain targets.
SETTINGS_KEY = "maturity_targets"


def domain_of(scf_id: str) -> str:
    """The domain code a control belongs to: its ``scf_id`` prefix, upper-cased."""
    return (scf_id or "").split("-", 1)[0].strip().upper()


def normalise_level(value: Any) -> Optional[str]:
    """``"l3"`` or ``" L3 "`` to ``"L3"``; anything that is not a CMM level to None."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip().upper()
    return cleaned if cleaned in MATURITY_LEVELS else None


def stored_domain_targets(settings: Optional[Mapping[str, Any]]) -> Dict[str, str]:
    """The domain targets an organisation has recorded, as ``{code: level}``.

    Defensive about what is stored. ``settings`` is a free JSON column that
    other writers share, so a malformed entry is dropped rather than allowed
    to fail every read of the settings or of a gate.
    """
    if not isinstance(settings, Mapping):
        return {}
    raw = settings.get(SETTINGS_KEY)
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, str] = {}
    for key, value in raw.items():
        level = normalise_level(value)
        if isinstance(key, str) and key.strip() and level:
            out[key.strip().upper()] = level
    return out


async def validate_domain_targets(
    db: AsyncSession, targets: Mapping[str, str]
) -> Tuple[Dict[str, str], list]:
    """Map each requested domain to its catalogue identifier.

    Returns ``(cleaned, unknown)``. A domain the catalogue does not hold is
    reported, never stored: a target saved under a misspelt code would sit
    in settings applying to no control while reading as configured.
    """
    rows = await db.execute(select(SCFCatalogDomain.identifier))
    known = {ident.strip().upper(): ident.strip() for (ident,) in rows if ident and ident.strip()}
    cleaned: Dict[str, str] = {}
    unknown = []
    for key, level in targets.items():
        code = known.get((key or "").strip().upper())
        if code is None:
            unknown.append(key)
        else:
            cleaned[code.upper()] = level
    return cleaned, sorted(unknown)


def resolve_target(
    scf_id: str,
    override: Optional[str],
    domain_targets: Mapping[str, str],
) -> Tuple[Optional[str], Optional[str]]:
    """``(level, source)`` for one control; source is ``"control"``, ``"domain"`` or None."""
    level = normalise_level(override)
    if level:
        return level, "control"
    inherited = domain_targets.get(domain_of(scf_id))
    if inherited:
        return inherited, "domain"
    return None, None


def effective_target_expr(domain_targets: Mapping[str, str]):
    """SQL for a scoped control's effective target, NULL when it has none.

    The override wins; otherwise the control's domain target, matched on the
    ``scf_id`` prefix exactly as the journey's domain filter matches it.
    ``istartswith`` with ``autoescape`` so an identifier containing ``_`` or
    ``%`` is never a wildcard.
    """
    inherited = None
    if domain_targets:
        inherited = case(
            *[
                (ScopedControl.scf_id.istartswith(f"{code}-", autoescape=True), literal(level))
                for code, level in sorted(domain_targets.items())
            ],
            else_=None,
        )
    if inherited is None:
        return ScopedControl.target_maturity_override
    return func.coalesce(ScopedControl.target_maturity_override, inherited)


def current_level_expr():
    """The control's recorded maturity in canonical form, or NULL if it is not a CMM level.

    ``maturity_level`` is a free string column, so a value outside L0 to L5
    reads as unrated rather than being compared as text.
    """
    cleaned = func.upper(func.btrim(ScopedControl.maturity_level))
    return case((cleaned.in_(MATURITY_LEVELS), cleaned), else_=None)


def at_target_clause(domain_targets: Mapping[str, str]):
    """A control that has a target and has reached it.

    ``L0`` to ``L5`` are single-digit, so string order is level order. An
    unrated control is never at target, whatever its target is.
    """
    target = effective_target_expr(domain_targets)
    current = current_level_expr()
    return and_(target.isnot(None), current.isnot(None), current >= target)


async def set_control_target_override(
    db: AsyncSession,
    org_id: UUID,
    scf_id: str,
    level: Optional[str],
    reason: Optional[str],
    user_id: Optional[UUID],
) -> ScopedControl:
    """Set or clear one control's target override. A set override needs a reason."""
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
    if level is None:
        control.target_maturity_override = None
        control.target_maturity_reason = None
        control.target_maturity_set_at = None
        control.target_maturity_set_by = None
    else:
        cleaned = (reason or "").strip()
        if not cleaned:
            raise ValueError("A target maturity override needs a reason")
        control.target_maturity_override = level
        control.target_maturity_reason = cleaned
        control.target_maturity_set_at = datetime.utcnow()
        control.target_maturity_set_by = user_id
    control.updated_by_user_id = user_id
    return control
