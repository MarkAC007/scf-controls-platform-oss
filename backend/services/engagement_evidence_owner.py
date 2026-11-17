# SPDX-License-Identifier: AGPL-3.0-only
"""Who owns an evidence object on the engagement review board.

The board's card is the evidence object (an SCF ERL id such as ``E-IAC-07``).
Its owner is whoever the organisation's own ``EvidenceTracking`` row for that
id says, resolved through the same chain every notification uses
(:func:`services.owner_resolution.resolve_owners`):

    1. the explicitly assigned user (``assigned_user_id`` / ``owner_user_id``)
    2. the accountable team's primary and delegate
    3. the organisation's admins

The chain is decided in one place — the pure ``resolve_owners`` — and this
module only does the reads, in bulk, so a board of two hundred cards costs a
fixed handful of queries rather than one per card.

An evidence object the organisation has no tracking row for, or one nobody is
assigned to, lands on the admins and is labelled ``Unowned → org admins`` so
the board says so plainly instead of showing a blank owner.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    EvidenceTeamAssignment,
    EvidenceTracking,
    OrganizationMember,
    Team,
    TeamMember,
    User,
)
from services.owner_resolution import (
    ACCOUNTABLE_MEMBERSHIP_ROLES,
    OWNER_TIER_ACCOUNTABLE_TEAM,
    OWNER_TIER_EXPLICIT,
    resolve_owners,
)

TIER_EXPLICIT = "explicit"
TIER_ACCOUNTABLE_TEAM = "accountable_team"
TIER_ORG_ADMIN = "org_admin"

UNOWNED_LABEL = "Unowned → org admins"
UNNAMED_LABEL = "Unnamed user"


@dataclass(frozen=True)
class EvidenceOwner:
    evidence_id: str
    tracking_id: Optional[UUID]      # None when the org has no EvidenceTracking row
    is_tracked: bool
    tier: Optional[str]              # 'explicit' | 'accountable_team' | 'org_admin' | None
    user_ids: tuple                  # resolved people for that tier
    assigned_user_id: Optional[UUID]
    owner_user_id: Optional[UUID]
    team_id: Optional[UUID]          # accountable team, when one is assigned
    team_name: Optional[str]
    label: str
    # Every accountable team, team_id first. team_id/team_name stay the first
    # one for display; the self-accept check must look at all of them.
    team_ids: tuple = ()


def _display_name(user) -> Optional[str]:
    """Display name, applied to a bulk-loaded row instead of one query per
    user. Never the email address: the owner label is on the review card, and
    an external auditor reads that card."""
    if user is None:
        return None
    return user.display_name or UNNAMED_LABEL


async def resolve_evidence_owners(
    db: AsyncSession, organization_id: UUID, evidence_ids: Iterable[str],
) -> dict:
    """Owners for many evidence objects. At most four queries, whatever the count:
    tracking rows, accountable teams with their primary/delegate, org admins,
    and the explicit owners' names."""
    ids = sorted({e for e in evidence_ids if e})
    if not ids:
        return {}

    tracking_rows = list((await db.execute(
        select(EvidenceTracking).where(
            EvidenceTracking.organization_id == organization_id,
            EvidenceTracking.evidence_id.in_(ids),
        )
    )).scalars().all())
    tracking_by_evidence: dict = {}
    for row in tracking_rows:
        # One row per (org, evidence_id) in practice; keep the first if not.
        tracking_by_evidence.setdefault(row.evidence_id, row)

    # Accountable team per tracking row, and its primary/delegate, in one query.
    teams_by_tracking: dict = {}      # tracking_id -> (team_id, team_name)
    team_ids_by_tracking: dict = {}   # tracking_id -> [team_id, ...] every accountable team
    members_by_tracking: dict = {}    # tracking_id -> [(user_id, role)]
    tracking_ids = [row.id for row in tracking_by_evidence.values()]
    if tracking_ids:
        team_rows = (await db.execute(
            select(
                EvidenceTeamAssignment.evidence_tracking_id,
                Team.id,
                Team.name,
                TeamMember.user_id,
                TeamMember.membership_role,
            )
            .join(Team, Team.id == EvidenceTeamAssignment.team_id)
            .outerjoin(
                TeamMember,
                and_(
                    TeamMember.team_id == Team.id,
                    TeamMember.organization_id == organization_id,
                    TeamMember.membership_role.in_(ACCOUNTABLE_MEMBERSHIP_ROLES),
                ),
            )
            .where(
                EvidenceTeamAssignment.evidence_tracking_id.in_(tracking_ids),
                EvidenceTeamAssignment.organization_id == organization_id,
                EvidenceTeamAssignment.is_accountable.is_(True),
            )
        )).all()
        for tracking_id, team_id, team_name, user_id, role in team_rows:
            teams_by_tracking.setdefault(tracking_id, (team_id, team_name))
            seen = team_ids_by_tracking.setdefault(tracking_id, [])
            if team_id not in seen:
                seen.append(team_id)
            if user_id is not None:
                members_by_tracking.setdefault(tracking_id, []).append((user_id, role))

    admin_ids = list((await db.execute(
        select(OrganizationMember.user_id).where(
            OrganizationMember.organization_id == organization_id,
            OrganizationMember.role == "admin",
        )
    )).scalars().all())

    resolutions: dict = {}
    explicit_ids: set = set()
    for evidence_id in ids:
        row = tracking_by_evidence.get(evidence_id)
        explicit = (row.assigned_user_id, row.owner_user_id) if row else ()
        members = members_by_tracking.get(row.id, []) if row else []
        resolution = resolve_owners(
            explicit_user_ids=explicit,
            accountable_team_members=members,
            organization_admin_user_ids=admin_ids,
        )
        resolutions[evidence_id] = resolution
        if resolution.tier == OWNER_TIER_EXPLICIT:
            explicit_ids.update(u for u in explicit if u is not None)

    names: dict = {}
    if explicit_ids:
        users = (await db.execute(
            select(User).where(User.id.in_(list(explicit_ids)))
        )).scalars().all()
        names = {u.id: _display_name(u) for u in users}

    owners: dict = {}
    for evidence_id in ids:
        row = tracking_by_evidence.get(evidence_id)
        resolution = resolutions[evidence_id]
        team_id, team_name = (
            teams_by_tracking.get(row.id, (None, None)) if row else (None, None)
        )
        assigned = row.assigned_user_id if row else None
        owner_user = row.owner_user_id if row else None

        if resolution.tier == OWNER_TIER_EXPLICIT:
            tier = TIER_EXPLICIT
            named = assigned if assigned is not None else owner_user
            label = names.get(named) or "Unknown user"
        elif resolution.tier == OWNER_TIER_ACCOUNTABLE_TEAM:
            tier = TIER_ACCOUNTABLE_TEAM
            label = team_name or "Accountable team"
        else:
            # Tiers 1 and 2 empty: no row, untracked and unassigned, or
            # tracked with nobody named. The admins answer for it.
            tier = TIER_ORG_ADMIN
            label = UNOWNED_LABEL

        owners[evidence_id] = EvidenceOwner(
            evidence_id=evidence_id,
            tracking_id=row.id if row else None,
            is_tracked=bool(row.is_tracked) if row else False,
            tier=tier,
            user_ids=tuple(sorted(resolution.user_ids, key=str)),
            assigned_user_id=assigned,
            owner_user_id=owner_user,
            team_id=team_id,
            team_name=team_name,
            label=label,
            team_ids=tuple(team_ids_by_tracking.get(row.id, ())) if row else (),
        )
    return owners


async def resolve_evidence_owner(
    db: AsyncSession, organization_id: UUID, evidence_id: str,
) -> EvidenceOwner:
    owners = await resolve_evidence_owners(db, organization_id, [evidence_id])
    owner = owners.get(evidence_id)
    if owner is None:  # empty evidence_id
        return EvidenceOwner(
            evidence_id=evidence_id, tracking_id=None, is_tracked=False,
            tier=TIER_ORG_ADMIN, user_ids=(), assigned_user_id=None,
            owner_user_id=None, team_id=None, team_name=None, label=UNOWNED_LABEL,
        )
    return owner


async def evidence_ownership_flags(
    db: AsyncSession, organization_id: UUID, evidence_id: str, user_id,
) -> tuple:
    """``(owns_evidence, in_accountable_team)`` for the internal-auditor
    self-accept rule. ``in_accountable_team`` counts any membership role —
    a plain ``member`` of the accountable team is still on the team whose
    work is being accepted."""
    if user_id is None or not evidence_id:
        return (False, False)
    user_uuid = user_id if isinstance(user_id, UUID) else UUID(str(user_id))

    row = (await db.execute(
        select(EvidenceTracking).where(
            EvidenceTracking.organization_id == organization_id,
            EvidenceTracking.evidence_id == evidence_id,
        ).limit(1)
    )).scalars().first()
    if row is None:
        return (False, False)

    owns = user_uuid in {row.assigned_user_id, row.owner_user_id}

    membership = (await db.execute(
        select(TeamMember.id)
        .join(EvidenceTeamAssignment, EvidenceTeamAssignment.team_id == TeamMember.team_id)
        .where(
            EvidenceTeamAssignment.evidence_tracking_id == row.id,
            EvidenceTeamAssignment.organization_id == organization_id,
            EvidenceTeamAssignment.is_accountable.is_(True),
            TeamMember.organization_id == organization_id,
            TeamMember.user_id == user_uuid,
        ).limit(1)
    )).scalars().first()

    return (owns, membership is not None)
