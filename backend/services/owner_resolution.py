"""The owner-resolution fallback chain (#822 phase 3).

Who is responsible for this item?

    1. Explicit user assignment on the item  (assigned_user_id / owner_user_id
       / the polymorphic ``assignments`` table)
    2. The accountable team's PRIMARY **and** DELEGATE, both, in parallel
    3. Organisation admins

The first tier with somebody in it wins. That is the entire contract, and it
exists because tier 1 evaporates: ``scoped_controls.assigned_user_id``,
``owner_user_id`` and their ``evidence_tracking`` equivalents are all
``ON DELETE SET NULL``, so the day somebody leaves, ownership silently
disappears from every control and every evidence item they held. Tier 2 is
durable — a team outlives its members — and tier 3 is the existing last-resort
behaviour, kept.

**Primary and delegate are notified together, not delegate-on-escalation.** A
primary on annual leave would otherwise be a silent single point of failure,
which is precisely the failure mode teams exist to remove.

**Consulted (non-accountable) teams are deliberately not on this path.** A
control with five consulted teams would page ten people for an ordinary event.
Being consulted means being informed, not being paged; escalation is a separate
decision that belongs to the caller, above this function.

Two entry points, and the split matters:

* :func:`resolve_owners` is **pure**. No session, no I/O, no ``async``. Tiers
  in, set out. It is the single place the chain is decided, and every branch of
  it — including the one where every tier is empty — is a three-line unit test
  in ``tests/test_owner_resolution.py``. This is what replaces the nine
  hand-rolled recipient expressions in ``services/notifications.py``.
* :func:`resolve_item_owners` is a thin async convenience that fetches the
  three tiers for one item and calls the pure function, so callers do not each
  re-derive the same two queries.

Phase 3 defines and tests this; **phase 4 wires it into notifications**.
Nothing in ``services/notifications.py`` is touched here, and nothing in this
module imports it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence, Tuple
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import OrganizationMember, Team, TeamMember

#: Tier names, returned on the result so a caller can log or word a message
#: differently when the chain bottomed out at the org's admins.
OWNER_TIER_EXPLICIT = "explicit_assignment"
OWNER_TIER_ACCOUNTABLE_TEAM = "accountable_team"
OWNER_TIER_ORG_ADMIN = "organization_admin"

#: The membership roles tier 2 pages. ``member`` is deliberately absent:
#: being on the accountable team is not the same as being accountable for it,
#: and paging a whole roster is how a notification system gets muted.
ACCOUNTABLE_MEMBERSHIP_ROLES = frozenset({"primary", "delegate"})


@dataclass(frozen=True)
class OwnerResolution:
    """The resolved recipients, and which tier they came from.

    ``tier`` is ``None`` only when *every* tier was empty. It reports the tier
    that won even when :attr:`user_ids` is empty after exclusion, so a caller
    can tell "nobody is accountable for this at all" from "the only person
    accountable is the one who just acted".
    """

    user_ids: frozenset
    tier: Optional[str]

    def __bool__(self) -> bool:
        return bool(self.user_ids)


def _clean(ids: Iterable[Optional[UUID]]) -> set:
    """Drop the Nones. They are the normal state of a SET NULL column, not an
    error, and they must not count towards a tier being populated."""
    return {i for i in ids if i is not None}


def resolve_owners(
    *,
    explicit_user_ids: Iterable[Optional[UUID]] = (),
    accountable_team_members: Iterable[Tuple[Optional[UUID], Optional[str]]] = (),
    organization_admin_user_ids: Iterable[Optional[UUID]] = (),
    exclude_user_ids: Iterable[Optional[UUID]] = (),
) -> OwnerResolution:
    """Resolve who is responsible for an item. Pure; never raises.

    Args:
        explicit_user_ids: Tier 1. Every explicitly named user on the item —
            for a control that is ``assigned_user_id`` *and* ``owner_user_id``,
            plus any rows in the polymorphic ``assignments`` table. ``None``
            entries are ignored, which is the point: a ``SET NULL``-emptied
            column leaves the tier empty and the chain moves on.
        accountable_team_members: Tier 2, as ``(user_id, membership_role)``
            pairs from the **accountable** team only. Pairs whose role is not
            ``primary`` or ``delegate`` are ignored.
        organization_admin_user_ids: Tier 3, the existing last resort.
        exclude_user_ids: Users never to return — in practice the person whose
            action triggered the event.

    Returns:
        An :class:`OwnerResolution`. With nothing in any tier this is
        ``OwnerResolution(frozenset(), None)`` — an empty answer, not an
        exception. A raise here would take down a whole scheduler run over one
        unassigned item, which is the opposite of what this feature is for.

    Note:
        Exclusion is applied to the *winning* tier, after that tier is chosen —
        it never causes a fall-through to the next one. Filtering first would
        turn "Alice edited the control she owns" into a page for Alice's entire
        accountable team, which is amplification rather than delivery. Today's
        behaviour at ``notifications.py:451`` is to skip, and skipping is what
        is preserved.
    """
    tiers: Sequence[Tuple[str, set]] = (
        (OWNER_TIER_EXPLICIT, _clean(explicit_user_ids)),
        (OWNER_TIER_ACCOUNTABLE_TEAM, _clean(
            user_id for user_id, role in accountable_team_members
            if role in ACCOUNTABLE_MEMBERSHIP_ROLES
        )),
        (OWNER_TIER_ORG_ADMIN, _clean(organization_admin_user_ids)),
    )

    excluded = _clean(exclude_user_ids)
    for tier, candidates in tiers:
        if candidates:
            return OwnerResolution(
                user_ids=frozenset(candidates - excluded), tier=tier,
            )

    return OwnerResolution(user_ids=frozenset(), tier=None)


async def _accountable_team_members(
    db: AsyncSession, organization_id: UUID, item_type: str, item_id: UUID,
) -> list:
    """The accountable team's primary and delegate, as ``(user_id, role)`` pairs.

    One query. The ``organization_id`` predicate on the assignment is a second
    lock on the tenant boundary — the composite foreign keys already guarantee
    the team and the item share an organisation, so this is belt and braces
    against being handed an item id from elsewhere, not the isolation control
    itself.
    """
    # Imported here rather than at module scope: services.team_assignments
    # imports models and audit_service, and keeping this edge local avoids
    # binding this module's import order to the registry's.
    from services.team_assignments import TEAM_ASSIGNMENT_TYPES

    spec = TEAM_ASSIGNMENT_TYPES.get(item_type)
    if spec is None:
        return []

    result = await db.execute(
        select(TeamMember.user_id, TeamMember.membership_role)
        .join(Team, Team.id == TeamMember.team_id)
        .join(spec.model, spec.model.team_id == Team.id)
        .where(
            spec.item_column == item_id,
            spec.model.is_accountable.is_(True),
            spec.model.organization_id == organization_id,
            TeamMember.membership_role.in_(ACCOUNTABLE_MEMBERSHIP_ROLES),
        )
    )
    return list(result.all())


async def _organization_admin_user_ids(
    db: AsyncSession, organization_id: UUID,
) -> list:
    """Tier 3. Mirrors ``notifications._get_org_admin_user_ids`` without
    importing it — phase 4 collapses the two, and phase 3 does not touch
    ``services/notifications.py``."""
    result = await db.execute(
        select(OrganizationMember.user_id).where(
            OrganizationMember.organization_id == organization_id,
            OrganizationMember.role == "admin",
        )
    )
    return list(result.scalars().all())


async def resolve_item_owners(
    db: AsyncSession,
    *,
    organization_id: UUID,
    item_type: str,
    item_id: UUID,
    explicit_user_ids: Iterable[Optional[UUID]] = (),
    exclude_user_ids: Iterable[Optional[UUID]] = (),
) -> OwnerResolution:
    """Fetch the tiers for one item and resolve them.

    A thin wrapper: it does the two reads tiers 2 and 3 need and hands
    everything to :func:`resolve_owners`, which remains the only place the
    chain is decided. Tier 1 stays a parameter because what counts as an
    explicit assignment differs per item type and the caller already has the
    row loaded — re-fetching a control here to read two columns off it would be
    a query for nothing.

    Both tiers are fetched unconditionally, including when tier 1 already
    wins. Two indexed reads is the price of keeping every tier decision inside
    the pure function instead of half-reimplementing it here to save a query;
    callers resolving in bulk should batch above this rather than optimise
    inside it.
    """
    return resolve_owners(
        explicit_user_ids=explicit_user_ids,
        accountable_team_members=await _accountable_team_members(
            db, organization_id, item_type, item_id,
        ),
        organization_admin_user_ids=await _organization_admin_user_ids(
            db, organization_id,
        ),
        exclude_user_ids=exclude_user_ids,
    )
