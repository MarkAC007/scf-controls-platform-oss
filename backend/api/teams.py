"""
Functions and Teams API endpoints (#822 phase 1).

Two resources live here:

* ``/api/functions`` — the platform-static business functions. Read only, and
  deliberately so: the rows are fixed literals seeded by migration
  ``teamsfunctions1``, which is what lets "this team is aligned to Engineering"
  mean the same thing in every deployment. A tenant able to rename or delete a
  function could break that for its own data, so no write endpoint exists.

* ``/api/organizations/{org_id}/teams`` — organisation-scoped teams and their
  membership roster.

**Teams grant no permissions.** Authorisation is, and remains, entirely a
function of ``organization_members.role``. A person's place on a team records
who is *accountable* for work, never what they are *allowed* to do, and
nothing in this module is consulted by any permission check. There is no such
thing as a team admin.
"""
import logging
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import and_, case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from auth import OrgMembership, User, require_auth, require_org_role
from database import get_db
from models import Function, OrganizationMember, Team, TeamMember
from schemas import (
    FunctionResponse,
    SuccessResponse,
    TeamCreate,
    TeamDetailResponse,
    TeamMemberCreate,
    TeamMemberResponse,
    TeamMemberUpdate,
    TeamResponse,
    TeamUpdate,
)
from services.audit_service import (
    TEAM_MEMBER_TRACKED_FIELDS,
    TEAM_TRACKED_FIELDS,
    detect_action_source,
    get_request_id,
    log_entity_changes,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["teams"])


#: Roles at most one member of a team may hold at a time, each backed by its
#: own partial unique index (``uq_team_primary`` / ``uq_team_delegate``).
EXCLUSIVE_MEMBERSHIP_ROLES = ("primary", "delegate")

#: Roster ordering: the accountable people first, then everyone else by the
#: order they joined. Stable, and it puts the answer to "who owns this?" at the
#: top of the list without the client having to sort.
_ROLE_RANK = case(
    (TeamMember.membership_role == "primary", 0),
    (TeamMember.membership_role == "delegate", 1),
    else_=2,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _actor_id(membership: OrgMembership) -> Optional[UUID]:
    """The acting user's database id, or None for identities without one."""
    user = membership.user
    return UUID(user.db_id) if user and user.db_id else None


def _function_snapshot(fn: Function) -> dict:
    return {
        "id": fn.id,
        "key": fn.key,
        "name": fn.name,
        "is_active": fn.is_active,
    }


def _team_payload(team: Team, member_count: int) -> dict:
    return {
        "id": team.id,
        "organization_id": team.organization_id,
        "function_id": team.function_id,
        "name": team.name,
        "description": team.description,
        "is_active": team.is_active,
        "created_at": team.created_at,
        "updated_at": team.updated_at,
        "created_by_user_id": team.created_by_user_id,
        "updated_by_user_id": team.updated_by_user_id,
        "function": _function_snapshot(team.function) if team.function else None,
        "member_count": member_count,
    }


def _team_health(team: Team, members: List[TeamMember]) -> dict:
    """Advisory health for a team. Every condition here is a warning, never a gate.

    A team with no members and no primary is precisely what every team looks
    like the moment it is created, so none of this may block a write. The UI
    renders badges from it; the API only reports.
    """
    has_primary = any(m.membership_role == "primary" for m in members)
    has_delegate = any(m.membership_role == "delegate" for m in members)
    function_is_active = bool(team.function and team.function.is_active)

    warnings: List[str] = []
    if not members:
        warnings.append("Team has no members.")
    if not has_primary:
        warnings.append("Team has no primary owner.")
    if not function_is_active:
        warnings.append("Team is aligned to a function that is no longer active.")
    if not team.is_active:
        warnings.append("Team is archived.")

    return {
        "has_primary": has_primary,
        "has_delegate": has_delegate,
        "has_members": bool(members),
        "member_count": len(members),
        "function_is_active": function_is_active,
        "warnings": warnings,
    }


async def _load_team(
    db: AsyncSession,
    org_id: UUID,
    team_id: UUID,
    *,
    for_update: bool = False,
) -> Team:
    """Fetch one team inside its organisation, or 404.

    The ``organization_id`` predicate is the tenant boundary: a team id from
    another organisation is indistinguishable from one that does not exist.

    ``for_update`` takes a row lock on the team for the life of the
    transaction. Every membership mutation takes it, which serialises
    concurrent writers against the same team and turns what would otherwise be
    a unique-index race on ``uq_team_primary`` into an orderly queue.
    """
    query = select(Team).where(
        and_(Team.organization_id == org_id, Team.id == team_id)
    )
    if for_update:
        # of=Team: lock the teams row only. Without it the FOR UPDATE would try
        # to take row locks through the joined-in eager load as well.
        query = query.with_for_update(of=Team)
    else:
        query = query.options(selectinload(Team.function))

    team = (await db.execute(query)).scalar_one_or_none()
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found")
    return team


async def _load_roster(db: AsyncSession, team_id: UUID) -> List[TeamMember]:
    """The team's members, accountable roles first, each with its user loaded."""
    result = await db.execute(
        select(TeamMember)
        .where(TeamMember.team_id == team_id)
        .options(selectinload(TeamMember.user))
        .order_by(_ROLE_RANK, TeamMember.added_at)
    )
    return list(result.scalars().all())


async def _load_team_detail(db: AsyncSession, org_id: UUID, team_id: UUID) -> dict:
    """Team + roster + health, as the detail response body."""
    team = await _load_team(db, org_id, team_id)
    members = await _load_roster(db, team_id)
    payload = _team_payload(team, len(members))
    payload["members"] = members
    payload["health"] = _team_health(team, members)
    return payload


async def _require_function(db: AsyncSession, function_id: UUID) -> Function:
    """Resolve a function id, or 404.

    Checked in the application rather than left to the foreign key because
    ``teams.function_id`` is ON DELETE RESTRICT: an unknown id would surface as
    a raw IntegrityError, i.e. a 500 for what is plainly a client mistake.

    An *inactive* function is accepted. Deactivation is a platform decision
    that can happen long after a team is created, so refusing the write would
    make a tenant's teams uncreatable because of a switch they cannot see. The
    condition is reported instead, via ``health.function_is_active``.
    """
    fn = (await db.execute(
        select(Function).where(Function.id == function_id)
    )).scalar_one_or_none()
    if fn is None:
        raise HTTPException(status_code=404, detail="Function not found")
    return fn


async def _require_org_member(db: AsyncSession, org_id: UUID, user_id: UUID) -> None:
    """Refuse team membership for anyone without an organisation_members row.

    ``team_members`` carries a composite foreign key to
    ``organization_members``, so this is the database's rule, not a policy
    invented here — the row physically cannot be inserted otherwise.

    It bites in one non-obvious case. ``verify_org_membership`` admits a user
    to an organisation by either of two paths: an ``OrganizationMember`` row,
    or an active ``ConsultantClientRelationship``. A consultant reaching the
    org by the second path can read and write everything else in it while
    having no ``OrganizationMember`` row at all, so adding them to a team would
    fail at the foreign key. That is caught here and returned as a 400 that
    explains the remedy, rather than escaping as a 500.
    """
    exists = (await db.execute(
        select(OrganizationMember.id).where(
            and_(
                OrganizationMember.organization_id == org_id,
                OrganizationMember.user_id == user_id,
            )
        )
    )).scalar_one_or_none()

    if exists is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "User is not a member of this organisation and cannot be added "
                "to a team. Team membership is backed by organisation "
                "membership; a consultant whose access comes from a "
                "consultant-client relationship must be added as an "
                "organisation member first."
            ),
        )


async def _claim_exclusive_role(
    db: AsyncSession,
    team_id: UUID,
    membership_role: str,
    incoming_user_id: UUID,
) -> Optional[TeamMember]:
    """Clear the way for a primary/delegate claim, in this transaction.

    ``uq_team_primary`` and ``uq_team_delegate`` are non-deferrable partial
    unique indexes, so Postgres evaluates them at the end of each *statement*,
    not at commit. The incumbent is therefore demoted and that UPDATE flushed
    *before* the caller issues the promoting UPDATE/INSERT — the index never
    sees two holders, and both statements land in the one transaction the
    caller commits, so there is no window in which the team has no primary from
    any other session's point of view.

    Returns the demoted member (still in the session, role already changed) so
    the caller can audit the demotion it caused, or None if nothing moved.
    """
    if membership_role not in EXCLUSIVE_MEMBERSHIP_ROLES:
        return None

    incumbent = (await db.execute(
        select(TeamMember).where(
            and_(
                TeamMember.team_id == team_id,
                TeamMember.membership_role == membership_role,
                TeamMember.user_id != incoming_user_id,
            )
        )
    )).scalar_one_or_none()

    if incumbent is None:
        return None

    incumbent.membership_role = "member"
    await db.flush()
    return incumbent


async def _flush_mapping_conflicts(db: AsyncSession, conflicts: dict) -> None:
    """Flush, translating known constraint violations into 4xx responses.

    *conflicts* maps a constraint name to a ``(status_code, detail)`` pair.
    Anything not listed is re-raised and becomes a 500, which is the correct
    outcome for a violation nobody anticipated.
    """
    try:
        await db.flush()
    except IntegrityError as exc:
        message = str(getattr(exc, "orig", exc))
        await db.rollback()
        for constraint, (status_code, detail) in conflicts.items():
            if constraint in message:
                raise HTTPException(status_code=status_code, detail=detail) from exc
        logger.exception("Unmapped integrity error on team write")
        raise


# ---------------------------------------------------------------------------
# Functions (platform-static, read only)
# ---------------------------------------------------------------------------

@router.get("/functions", response_model=List[FunctionResponse])
async def list_functions(
    include_inactive: bool = Query(False, description="Include deactivated functions"),
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    List the platform's business functions.

    Requires: any authenticated user. The list is identical for every tenant,
    so there is nothing here to scope to an organisation.

    Read only by design — see the module docstring. No create, update or delete
    endpoint for functions exists, or should.
    """
    query = select(Function)
    if not include_inactive:
        query = query.where(Function.is_active.is_(True))

    result = await db.execute(
        query.order_by(Function.display_order.nulls_last(), Function.key)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------

@router.get(
    "/organizations/{org_id}/teams",
    response_model=List[TeamResponse],
)
async def list_teams(
    org_id: UUID,
    function_id: Optional[UUID] = Query(None, description="Filter to one function"),
    include_inactive: bool = Query(False, description="Include archived teams"),
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """
    List the organisation's teams.

    Requires: viewer role or higher.

    Archived teams are hidden unless ``include_inactive`` is set — they are
    kept, never deleted, so that history referring to them still resolves.
    """
    query = select(Team).where(Team.organization_id == org_id)
    if function_id is not None:
        query = query.where(Team.function_id == function_id)
    if not include_inactive:
        query = query.where(Team.is_active.is_(True))

    result = await db.execute(
        query.options(selectinload(Team.function)).order_by(Team.name)
    )
    teams = list(result.scalars().all())
    if not teams:
        return []

    # One grouped count for the whole page rather than a query per team.
    counts = dict(
        (await db.execute(
            select(TeamMember.team_id, func.count(TeamMember.id))
            .where(TeamMember.team_id.in_([t.id for t in teams]))
            .group_by(TeamMember.team_id)
        )).all()
    )

    return [_team_payload(team, counts.get(team.id, 0)) for team in teams]


@router.post(
    "/organizations/{org_id}/teams",
    response_model=TeamDetailResponse,
    status_code=201,
)
async def create_team(
    org_id: UUID,
    team_data: TeamCreate,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """
    Create a team.

    Requires: admin role.

    The team is born empty — no members, no primary. That is a legal state and
    is reported through ``health.warnings``, not refused.
    """
    await _require_function(db, team_data.function_id)

    actor_id = _actor_id(membership)
    team = Team(
        organization_id=org_id,
        created_by_user_id=actor_id,
        updated_by_user_id=actor_id,
        **team_data.model_dump(),
    )
    db.add(team)
    await _flush_mapping_conflicts(db, {
        "uq_teams_org_name": (
            409,
            f"A team named '{team_data.name}' already exists in this organisation",
        ),
    })

    new_values = {f: getattr(team, f) for f in TEAM_TRACKED_FIELDS if hasattr(team, f)}
    await log_entity_changes(
        db=db, organization_id=org_id, entity_type="team",
        entity_id=team.id, action="create", changed_by_user_id=actor_id,
        old_values={}, new_values=new_values,
        tracked_fields=TEAM_TRACKED_FIELDS,
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )

    await db.commit()
    return await _load_team_detail(db, org_id, team.id)


@router.get(
    "/organizations/{org_id}/teams/{team_id}",
    response_model=TeamDetailResponse,
)
async def get_team(
    org_id: UUID,
    team_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """
    Get one team with its full roster and advisory health.

    Requires: viewer role or higher.
    """
    return await _load_team_detail(db, org_id, team_id)


@router.patch(
    "/organizations/{org_id}/teams/{team_id}",
    response_model=TeamDetailResponse,
)
async def update_team(
    org_id: UUID,
    team_id: UUID,
    team_data: TeamUpdate,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """
    Update a team's name, description, function alignment or active flag.

    Requires: admin role.
    """
    team = await _load_team(db, org_id, team_id)
    updates = team_data.model_dump(exclude_unset=True)

    if "function_id" in updates and updates["function_id"] is not None:
        await _require_function(db, updates["function_id"])

    old_values = {f: getattr(team, f) for f in TEAM_TRACKED_FIELDS if hasattr(team, f)}

    for field, value in updates.items():
        setattr(team, field, value)
    team.updated_by_user_id = _actor_id(membership)

    await _flush_mapping_conflicts(db, {
        "uq_teams_org_name": (
            409,
            f"A team named '{updates.get('name')}' already exists in this organisation",
        ),
    })

    new_values = {f: getattr(team, f) for f in TEAM_TRACKED_FIELDS if hasattr(team, f)}
    await log_entity_changes(
        db=db, organization_id=org_id, entity_type="team",
        entity_id=team.id, action="update", changed_by_user_id=_actor_id(membership),
        old_values=old_values, new_values=new_values,
        tracked_fields=TEAM_TRACKED_FIELDS,
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )

    await db.commit()
    return await _load_team_detail(db, org_id, team_id)


@router.delete(
    "/organizations/{org_id}/teams/{team_id}",
    response_model=SuccessResponse,
)
async def archive_team(
    org_id: UUID,
    team_id: UUID,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """
    Archive a team.

    Requires: admin role.

    This sets ``is_active = false``. The row is never deleted: preserving who
    was accountable for what, and when, is the entire point of the feature, and
    a hard delete would take the roster and every audit reference with it. The
    team stops appearing in the default listing and can be brought back with a
    PATCH setting ``is_active`` true.
    """
    team = await _load_team(db, org_id, team_id)

    if not team.is_active:
        return SuccessResponse(message="Team is already archived")

    old_values = {f: getattr(team, f) for f in TEAM_TRACKED_FIELDS if hasattr(team, f)}
    team.is_active = False
    team.updated_by_user_id = _actor_id(membership)
    await db.flush()

    new_values = {f: getattr(team, f) for f in TEAM_TRACKED_FIELDS if hasattr(team, f)}
    await log_entity_changes(
        db=db, organization_id=org_id, entity_type="team",
        entity_id=team.id, action="update", changed_by_user_id=_actor_id(membership),
        old_values=old_values, new_values=new_values,
        tracked_fields=TEAM_TRACKED_FIELDS,
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )

    await db.commit()
    return SuccessResponse(message=f"Team '{team.name}' archived")


# ---------------------------------------------------------------------------
# Team membership
# ---------------------------------------------------------------------------

@router.get(
    "/organizations/{org_id}/teams/{team_id}/members",
    response_model=List[TeamMemberResponse],
)
async def list_team_members(
    org_id: UUID,
    team_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """
    List a team's members, primary first.

    Requires: viewer role or higher.
    """
    await _load_team(db, org_id, team_id)
    return await _load_roster(db, team_id)


@router.post(
    "/organizations/{org_id}/teams/{team_id}/members",
    response_model=TeamMemberResponse,
    status_code=201,
)
async def add_team_member(
    org_id: UUID,
    team_id: UUID,
    member_data: TeamMemberCreate,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """
    Add a user to a team.

    Requires: admin role — meaning admin of the *organisation*. Team membership
    confers no authority of its own, so there is no team-level equivalent to
    check.

    Adding someone as ``primary`` (or ``delegate``) when the seat is taken
    demotes the incumbent to ``member`` in the same transaction; see
    :func:`_claim_exclusive_role`.
    """
    actor_id = _actor_id(membership)
    # Row lock first: it orders concurrent writers against this team, so two
    # simultaneous primary claims resolve one after the other instead of racing
    # each other into a unique-index violation.
    await _load_team(db, org_id, team_id, for_update=True)
    await _require_org_member(db, org_id, member_data.user_id)

    existing = (await db.execute(
        select(TeamMember).where(
            and_(
                TeamMember.team_id == team_id,
                TeamMember.user_id == member_data.user_id,
            )
        )
    )).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail="User is already a member of this team",
        )

    demoted = await _claim_exclusive_role(
        db, team_id, member_data.membership_role, member_data.user_id
    )

    member = TeamMember(
        team_id=team_id,
        organization_id=org_id,
        user_id=member_data.user_id,
        membership_role=member_data.membership_role,
        added_by_user_id=actor_id,
    )
    db.add(member)
    await _flush_mapping_conflicts(db, {
        "uq_team_members_team_user": (409, "User is already a member of this team"),
        "uq_team_primary": (409, "This team already has a primary owner"),
        "uq_team_delegate": (409, "This team already has a delegate"),
        "fk_team_members_org_member": (
            400,
            "User is not a member of this organisation and cannot be added to a team",
        ),
    })

    action_source = detect_action_source(request)
    request_id = get_request_id(request)

    if demoted is not None:
        await log_entity_changes(
            db=db, organization_id=org_id, entity_type="team_member",
            entity_id=demoted.id, action="update", changed_by_user_id=actor_id,
            old_values={"membership_role": member_data.membership_role},
            new_values={"membership_role": "member"},
            tracked_fields=TEAM_MEMBER_TRACKED_FIELDS,
            action_source=action_source, request_id=request_id,
        )

    new_values = {
        f: getattr(member, f) for f in TEAM_MEMBER_TRACKED_FIELDS if hasattr(member, f)
    }
    await log_entity_changes(
        db=db, organization_id=org_id, entity_type="team_member",
        entity_id=member.id, action="create", changed_by_user_id=actor_id,
        old_values={}, new_values=new_values,
        tracked_fields=TEAM_MEMBER_TRACKED_FIELDS,
        action_source=action_source, request_id=request_id,
    )

    await db.commit()

    result = await db.execute(
        select(TeamMember)
        .where(TeamMember.id == member.id)
        .options(selectinload(TeamMember.user))
    )
    return result.scalar_one()


@router.patch(
    "/organizations/{org_id}/teams/{team_id}/members/{user_id}",
    response_model=TeamMemberResponse,
)
async def update_team_member(
    org_id: UUID,
    team_id: UUID,
    user_id: UUID,
    member_data: TeamMemberUpdate,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """
    Change a member's role on a team.

    Requires: admin role.

    Promotion to ``primary`` or ``delegate`` demotes the incumbent to
    ``member`` atomically — one transaction, one commit, ordered so the partial
    unique index is never transiently violated. Callers do not, and must not,
    have to demote first themselves.
    """
    actor_id = _actor_id(membership)
    await _load_team(db, org_id, team_id, for_update=True)

    member = (await db.execute(
        select(TeamMember).where(
            and_(TeamMember.team_id == team_id, TeamMember.user_id == user_id)
        )
    )).scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=404, detail="Team member not found")

    previous_role = member.membership_role
    if previous_role == member_data.membership_role:
        result = await db.execute(
            select(TeamMember)
            .where(TeamMember.id == member.id)
            .options(selectinload(TeamMember.user))
        )
        return result.scalar_one()

    demoted = await _claim_exclusive_role(
        db, team_id, member_data.membership_role, user_id
    )

    member.membership_role = member_data.membership_role
    await _flush_mapping_conflicts(db, {
        "uq_team_primary": (409, "This team already has a primary owner"),
        "uq_team_delegate": (409, "This team already has a delegate"),
    })

    action_source = detect_action_source(request)
    request_id = get_request_id(request)

    if demoted is not None:
        await log_entity_changes(
            db=db, organization_id=org_id, entity_type="team_member",
            entity_id=demoted.id, action="update", changed_by_user_id=actor_id,
            old_values={"membership_role": member_data.membership_role},
            new_values={"membership_role": "member"},
            tracked_fields=TEAM_MEMBER_TRACKED_FIELDS,
            action_source=action_source, request_id=request_id,
        )

    await log_entity_changes(
        db=db, organization_id=org_id, entity_type="team_member",
        entity_id=member.id, action="update", changed_by_user_id=actor_id,
        old_values={"membership_role": previous_role},
        new_values={"membership_role": member.membership_role},
        tracked_fields=TEAM_MEMBER_TRACKED_FIELDS,
        action_source=action_source, request_id=request_id,
    )

    await db.commit()

    result = await db.execute(
        select(TeamMember)
        .where(TeamMember.id == member.id)
        .options(selectinload(TeamMember.user))
    )
    return result.scalar_one()


@router.delete(
    "/organizations/{org_id}/teams/{team_id}/members/{user_id}",
    response_model=SuccessResponse,
)
async def remove_team_member(
    org_id: UUID,
    team_id: UUID,
    user_id: UUID,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """
    Remove a user from a team.

    Requires: admin role.

    The membership row really is deleted here, unlike a team, which archives.
    A membership is a statement about the present — "this person is on this
    team" — and the audit trail carries the history of it. Nothing else points
    at a ``team_members`` row.
    """
    actor_id = _actor_id(membership)
    await _load_team(db, org_id, team_id, for_update=True)

    member = (await db.execute(
        select(TeamMember).where(
            and_(TeamMember.team_id == team_id, TeamMember.user_id == user_id)
        )
    )).scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=404, detail="Team member not found")

    member_id = member.id
    old_values = {
        f: getattr(member, f) for f in TEAM_MEMBER_TRACKED_FIELDS if hasattr(member, f)
    }

    await db.delete(member)
    await db.flush()

    await log_entity_changes(
        db=db, organization_id=org_id, entity_type="team_member",
        entity_id=member_id, action="delete", changed_by_user_id=actor_id,
        old_values=old_values, new_values={},
        tracked_fields=TEAM_MEMBER_TRACKED_FIELDS,
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )

    await db.commit()
    return SuccessResponse(message="Team member removed")
