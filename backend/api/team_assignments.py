"""Team assignment of controls and evidence (#822 phase 3).

Three endpoints, one per verb, all dispatching through
:data:`services.team_assignments.TEAM_ASSIGNMENT_TYPES`::

    GET    /api/organizations/{org_id}/team-assignments      ?type=control|evidence
    POST   /api/organizations/{org_id}/team-assignments      {type, item_id, team_id, is_accountable}
    DELETE /api/organizations/{org_id}/team-assignments/{id}

**The bulk GET is the reason this resource exists.** The controls list renders
hundreds of rows and the accountable-team badge sits on that hot path.
Fetching assignments per row is an N+1 measured in seconds, so the read returns
one map keyed by item id, in a single SQL statement, with the team and its
primary and delegate already embedded — a badge renders from the response with
no follow-up call. ``tests/test_team_assignments.py`` counts the statements
from the driver so that claim cannot quietly stop being true.

**Teams grant no permissions.** Exactly as in phase 1: authorisation is a
function of ``organization_members.role`` and nothing else. Reading is open to
any org member; every mutation is org **admin**. Being on a team — even being
the accountable team's primary — confers no authority at all, and nothing in
this module is consulted by any permission check.

**Purely additive.** ``scoped_controls.assigned_user_id``, ``owner_user_id``
and the polymorphic ``assignments`` table keep their exact current behaviour
and contracts. Nothing here changes an existing endpoint's response shape.
Team ownership is a second, durable axis alongside per-user assignment, not a
replacement for it; :mod:`services.owner_resolution` is where the two meet.

**Cross-tenant isolation is the database's.** Both join tables carry composite
foreign keys forcing the item, the team and the denormalised
``organization_id`` to agree, so a row naming another tenant's team simply
cannot be inserted. The application's job is narrower and absolute: derive
``organization_id`` from the path and the authenticated membership, and never
from the request body. The 404s below are for giving a caller a straight answer
instead of a 500 — they are not the isolation control, and must not be mistaken
for it.
"""
import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from auth import OrgMembership, require_org_role
from database import get_db
from models import Function, Team, TeamMember, User
from schemas import (
    SuccessResponse,
    TeamAssignmentCreate,
    TeamAssignmentMapResponse,
    TeamAssignmentResponse,
)
from services.audit_service import (
    detect_action_source,
    get_request_id,
    log_entity_changes,
)
from services.owner_resolution import ACCOUNTABLE_MEMBERSHIP_ROLES
from services.team_assignments import (
    TEAM_ASSIGNMENT_TYPE_KEYS,
    TEAM_ASSIGNMENT_TYPES,
    TeamAssignmentSpec,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["team-assignments"])

#: Cap on ``?item_ids=`` so one request cannot be turned into an unbounded
#: ``IN`` list. Comfortably above a page of controls; the unfiltered call is
#: the normal one anyway.
MAX_ITEM_IDS = 1000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _actor_id(membership: OrgMembership) -> Optional[UUID]:
    """The acting user's database id, or None for identities without one."""
    user = membership.user
    return UUID(user.db_id) if user and user.db_id else None


def _require_spec(type_key: str) -> TeamAssignmentSpec:
    """Dispatch on ``type``, or 422.

    Pydantic's pattern already rejects anything unknown on the POST body, so
    this fires only for the query-string form. The message names what *is*
    accepted, because "risk" and "vendor" appear in #822's API surface and land
    in phase 5 — a caller reading the issue will try them before they exist.
    """
    spec = TEAM_ASSIGNMENT_TYPES.get(type_key)
    if spec is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unsupported assignment type '{type_key}'. "
                f"Supported: {', '.join(TEAM_ASSIGNMENT_TYPE_KEYS)}."
            ),
        )
    return spec


async def _require_item(
    db: AsyncSession,
    spec: TeamAssignmentSpec,
    org_id: UUID,
    item_id: UUID,
    *,
    for_update: bool = False,
) -> None:
    """Check the item exists in this organisation, or 404.

    The ``organization_id`` predicate is why an item id from another tenant is
    indistinguishable from one that does not exist. It is *not* what stops the
    write — the composite foreign keys do that, and would reject the row even
    if this function were deleted. This exists so a client gets a 404 rather
    than an unexplained 500, and so the lock below can be taken.

    ``for_update`` takes a row lock on the item for the life of the
    transaction. Every accountable claim takes it, which serialises concurrent
    writers against the same control or evidence item and turns what would
    otherwise be a race on the partial unique index into an orderly queue. Note
    it locks the *item*, not the team: the index is
    ``unique(item_id) where is_accountable``, so the item is the row that
    contends.
    """
    query = select(spec.item_model.id).where(
        and_(
            spec.item_model.organization_id == org_id,
            spec.item_model.id == item_id,
        )
    )
    if for_update:
        query = query.with_for_update()

    if (await db.execute(query)).scalar_one_or_none() is None:
        raise HTTPException(
            status_code=404, detail=f"{spec.item_label} not found",
        )


async def _require_team(db: AsyncSession, org_id: UUID, team_id: UUID) -> Team:
    """Resolve a team inside this organisation, or 404.

    Checked in the application rather than left to
    ``fk_..._team_org`` because an unknown or foreign team id would otherwise
    surface as a raw IntegrityError — a 500 for what is plainly a client
    mistake.

    An *archived* team is accepted, matching phase 1, which lets an archived
    team keep its roster. Archiving records that a team is no longer in use; it
    does not retroactively invalidate what the team is on the hook for, and
    refusing the write would strand any item whose owning team was archived
    before a new one was picked. ``team.is_active`` rides along in the read
    payload so the UI can mark it.
    """
    team = (await db.execute(
        select(Team).where(
            and_(Team.organization_id == org_id, Team.id == team_id)
        )
    )).scalar_one_or_none()
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found")
    return team


async def _claim_accountable(
    db: AsyncSession,
    spec: TeamAssignmentSpec,
    item_id: UUID,
    incoming_team_id: UUID,
):
    """Clear the way for an accountable claim, in this transaction.

    ``uq_control_accountable_team`` and ``uq_evidence_accountable_team`` are
    non-deferrable partial unique indexes, so Postgres evaluates them at the
    end of each *statement*, not at commit. The incumbent is therefore demoted
    and that UPDATE flushed **before** the caller issues the promoting
    UPDATE/INSERT — the index never sees two accountable rows, and both
    statements land in the one transaction the caller commits, so from every
    other session's point of view the item never has two owners and never has
    none.

    This is what makes promotion a single request. A client-side "clear the old
    one, then set the new one" sequence is two transactions: it races under
    concurrent edits and leaves the item ownerless if the second call is lost.

    Returns the demoted row (still in the session, flag already cleared) so the
    caller can audit the demotion it caused, or None if nothing moved —
    including the no-op case where the incoming team is already the accountable
    one, which must not demote the incumbent to make way for itself.
    """
    incumbent = (await db.execute(
        select(spec.model).where(
            and_(
                spec.item_column == item_id,
                spec.model.is_accountable.is_(True),
                spec.model.team_id != incoming_team_id,
            )
        )
    )).scalar_one_or_none()

    if incumbent is None:
        return None

    incumbent.is_accountable = False
    await db.flush()
    return incumbent


async def _flush_mapping_conflicts(db: AsyncSession, spec: TeamAssignmentSpec) -> None:
    """Flush, translating this type's known constraint violations into 4xx.

    Anything not in ``spec.conflicts`` is re-raised and becomes a 500, which is
    the correct outcome for a violation nobody anticipated.
    """
    try:
        await db.flush()
    except IntegrityError as exc:
        message = str(getattr(exc, "orig", exc))
        await db.rollback()
        for constraint, status_code, detail in spec.conflicts:
            if constraint in message:
                raise HTTPException(status_code=status_code, detail=detail) from exc
        logger.exception("Unmapped integrity error on team assignment write")
        raise


def _member_payload(row) -> Optional[dict]:
    """One accountable member, from the labelled columns of a joined row.

    The user half is nullable independently of the membership half:
    ``team_members.user_id`` carries no direct foreign key to ``users`` — it is
    reachable only through ``organization_members`` — so a roster row whose
    user has gone is representable, and the badge renders the role without a
    name rather than dropping the assignment.
    """
    if row.member_user_id is None:
        return None
    return {
        "user_id": row.member_user_id,
        "membership_role": row.member_role,
        "user": None if row.member_user_pk is None else {
            "id": row.member_user_pk,
            "email": row.member_email,
            "display_name": row.member_display_name,
        },
    }


async def _load_assignment_map(
    db: AsyncSession,
    spec: TeamAssignmentSpec,
    org_id: UUID,
    *,
    item_ids: Optional[List[UUID]] = None,
    team_id: Optional[UUID] = None,
    accountable_only: bool = False,
) -> Dict[UUID, List[dict]]:
    """Every matching assignment for the organisation, keyed by item id.

    **One SQL statement, whatever the number of items.** Assignments, their
    team, that team's function, and that team's primary and delegate all come
    back together, so a page of five hundred controls costs the same read as
    one and the badge needs no follow-up query. The member join is restricted
    to the two accountable roles, which bounds row multiplication at two rows
    per assignment rather than the size of the roster.

    Explicit columns rather than ORM entities, on purpose: selecting a mapped
    object and then reading ``.team`` off it would lazily emit a query per row,
    which is the exact N+1 this endpoint exists to remove. Columns cannot do
    that.

    The team join is inner: ``fk_..._team_org`` guarantees a team for every
    assignment, and its ``ON DELETE CASCADE`` means a deleted team takes its
    assignments with it. Every other join is outer, because a team legitimately
    has no primary and no delegate — that is what every team looks like the
    moment it is created.
    """
    member = TeamMember.__table__.alias("accountable_member")
    user = User.__table__.alias("accountable_user")

    query = (
        select(
            spec.model.id,
            spec.item_column.label("item_id"),
            spec.model.team_id,
            spec.model.organization_id,
            spec.model.is_accountable,
            spec.model.assigned_at,
            spec.model.assigned_by_user_id,
            Team.name.label("team_name"),
            Team.is_active.label("team_is_active"),
            Team.function_id,
            Function.id.label("function_pk"),
            Function.key.label("function_key"),
            Function.name.label("function_name"),
            Function.is_active.label("function_is_active"),
            member.c.user_id.label("member_user_id"),
            member.c.membership_role.label("member_role"),
            user.c.id.label("member_user_pk"),
            user.c.email.label("member_email"),
            user.c.display_name.label("member_display_name"),
        )
        .join(
            Team,
            and_(
                Team.id == spec.model.team_id,
                # Both halves, mirroring the composite foreign key. Joining on
                # id alone would still be correct today; joining on the pair
                # means this read cannot be the thing that surfaces a row whose
                # tenancy has somehow diverged.
                Team.organization_id == spec.model.organization_id,
            ),
        )
        .outerjoin(Function, Function.id == Team.function_id)
        .outerjoin(
            member,
            and_(
                member.c.team_id == Team.id,
                member.c.membership_role.in_(ACCOUNTABLE_MEMBERSHIP_ROLES),
            ),
        )
        .outerjoin(user, user.c.id == member.c.user_id)
        .where(spec.model.organization_id == org_id)
    )

    if accountable_only:
        query = query.where(spec.model.is_accountable.is_(True))
    if team_id is not None:
        query = query.where(spec.model.team_id == team_id)
    if item_ids:
        query = query.where(spec.item_column.in_(item_ids))

    rows = (await db.execute(query)).all()

    # Row multiplication is at most two per assignment (primary and delegate),
    # so the assignments are folded back together here rather than in SQL.
    assignments: Dict[UUID, dict] = {}
    for row in rows:
        payload = assignments.get(row.id)
        if payload is None:
            payload = {
                "id": row.id,
                "type": spec.type_key,
                "item_id": row.item_id,
                "team_id": row.team_id,
                "organization_id": row.organization_id,
                "is_accountable": row.is_accountable,
                "assigned_at": row.assigned_at,
                "assigned_by_user_id": row.assigned_by_user_id,
                "team": {
                    "id": row.team_id,
                    "name": row.team_name,
                    "is_active": row.team_is_active,
                    "function_id": row.function_id,
                    "function": None if row.function_pk is None else {
                        "id": row.function_pk,
                        "key": row.function_key,
                        "name": row.function_name,
                        "is_active": row.function_is_active,
                    },
                    "primary": None,
                    "delegate": None,
                },
            }
            assignments[row.id] = payload

        if row.member_role in ACCOUNTABLE_MEMBERSHIP_ROLES:
            payload["team"][row.member_role] = _member_payload(row)

    keyed: Dict[UUID, List[dict]] = {}
    for payload in assignments.values():
        keyed.setdefault(payload["item_id"], []).append(payload)

    # The accountable team first in every bucket: the badge reads [0] without
    # scanning, and the ordering is stable rather than whatever the join
    # happened to emit.
    for bucket in keyed.values():
        bucket.sort(key=lambda a: (not a["is_accountable"], a["team"]["name"] or ""))

    return keyed


def _tracked(spec: TeamAssignmentSpec, row) -> dict:
    return {f: getattr(row, f) for f in spec.tracked_fields if hasattr(row, f)}


async def _audit(
    db: AsyncSession,
    spec: TeamAssignmentSpec,
    request: Request,
    *,
    org_id: UUID,
    entity_id: UUID,
    action: str,
    actor_id: Optional[UUID],
    old_values: dict,
    new_values: dict,
) -> None:
    await log_entity_changes(
        db=db,
        organization_id=org_id,
        entity_type=spec.entity_type,
        entity_id=entity_id,
        action=action,
        changed_by_user_id=actor_id,
        old_values=old_values,
        new_values=new_values,
        tracked_fields=set(spec.tracked_fields),
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/organizations/{org_id}/team-assignments",
    response_model=TeamAssignmentMapResponse,
)
async def list_team_assignments(
    org_id: UUID,
    type: str = Query(
        ...,
        pattern=f"^({'|'.join(TEAM_ASSIGNMENT_TYPE_KEYS)})$",
        description="Which kind of item to return assignments for",
    ),
    item_ids: Optional[List[UUID]] = Query(
        None,
        description=(
            "Restrict to these item ids. Optional: the unfiltered call returns "
            "the organisation's whole map, which is what a page load wants."
        ),
    ),
    team_id: Optional[UUID] = Query(
        None, description="Restrict to assignments held by one team",
    ),
    accountable_only: bool = Query(
        False, description="Return only the accountable team for each item",
    ),
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """
    Team assignments for the organisation, keyed by item id.

    Requires: viewer role or higher. Reading who owns what is open to any
    member of the organisation; only mutation is admin.

    **Bulk by design, and not optional.** The controls list renders hundreds of
    rows with an accountable-team badge on each. This returns the whole page's
    assignments in one request and one SQL statement, already indexed by item
    id, with each team's primary and delegate embedded. Clients must not fetch
    per row.

    ``item_ids`` and ``team_id`` are narrowing filters, not required
    parameters — a page load calls this once with ``?type=control`` and keys
    the result. ``team_id`` answers "which controls does this team own", which
    is what ``ix_..._team_id`` was created for.

    **Items with no assignments are OMITTED from the map, not returned as an
    empty list.** Read ``map[item_id] ?? []``; the absence of a key means "no
    team owns this", never "not fetched yet".

    That omission keeps the payload proportional to what is actually assigned,
    which matters when most of a catalogue is unowned. But it is the one way a
    caller gets this endpoint wrong without noticing, so it is worth being
    explicit about the trap:

    A client that caches "which ids do I already have" by reading the *keys of
    the response* will never record the unowned ones. Every subsequent call --
    every scroll, every page -- re-requests exactly the items that were never
    going to come back, and the set never shrinks. Nothing errors, the badges
    are all correct, and the request count grows with the catalogue. That is
    the N+1 this endpoint exists to prevent, reintroduced by the caller.

    Cache the ids you **asked about**, not the ids you got back.

    When paging with ``item_ids``, chunk requests below ``MAX_ITEM_IDS``
    (currently 1000); over that the call is rejected with 422 rather than
    silently truncated, so an unbounded accumulation fails at page load.
    """
    spec = _require_spec(type)

    if item_ids and len(item_ids) > MAX_ITEM_IDS:
        raise HTTPException(
            status_code=422,
            detail=f"At most {MAX_ITEM_IDS} item_ids may be requested at once",
        )

    keyed = await _load_assignment_map(
        db, spec, org_id,
        item_ids=item_ids,
        team_id=team_id,
        accountable_only=accountable_only,
    )

    return TeamAssignmentMapResponse(
        type=spec.type_key,
        total=sum(len(v) for v in keyed.values()),
        accountable_only=accountable_only,
        assignments=keyed,
    )


@router.post(
    "/organizations/{org_id}/team-assignments",
    response_model=TeamAssignmentResponse,
    status_code=201,
)
async def create_team_assignment(
    org_id: UUID,
    payload: TeamAssignmentCreate,
    request: Request,
    response: Response,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """
    Assign a team to a control or evidence item.

    Requires: admin role — admin of the *organisation*. Team membership confers
    no authority of its own, so there is no team-level equivalent to check.

    ``is_accountable`` makes this the accountable team and demotes any
    incumbent to consulted **in the same transaction**; see
    :func:`_claim_accountable`. Callers do not, and must not, have to demote
    first themselves.

    Re-posting a team that is already assigned to the item updates its
    ``is_accountable`` flag and returns **200** rather than 409. That is what
    makes "promote this consulted team to accountable" a single atomic request;
    the alternative — DELETE then POST — is the two-call race this endpoint
    exists to remove. A genuinely new assignment returns 201.

    ``organization_id`` comes from the path and the verified membership. It is
    absent from the request body on purpose: a caller able to name it could
    file one tenant's team against another tenant's control.
    """
    spec = _require_spec(payload.type)
    actor_id = _actor_id(membership)

    # Lock the item first: it orders concurrent writers against this control or
    # evidence record, so two simultaneous accountable claims resolve one after
    # the other instead of racing each other into the partial unique index.
    await _require_item(db, spec, org_id, payload.item_id, for_update=True)
    await _require_team(db, org_id, payload.team_id)

    existing = (await db.execute(
        select(spec.model).where(
            and_(
                spec.item_column == payload.item_id,
                spec.model.team_id == payload.team_id,
            )
        )
    )).scalar_one_or_none()

    demoted = None
    if payload.is_accountable:
        demoted = await _claim_accountable(
            db, spec, payload.item_id, payload.team_id,
        )

    if existing is not None:
        old_values = _tracked(spec, existing)
        existing.is_accountable = payload.is_accountable
        row, action, status_code = existing, "update", 200
    else:
        old_values = {}
        row = spec.model(**{
            spec.item_id_field: payload.item_id,
            "team_id": payload.team_id,
            # Derived, never taken from the body.
            "organization_id": org_id,
            "is_accountable": payload.is_accountable,
            "assigned_by_user_id": actor_id,
        })
        db.add(row)
        action, status_code = "create", 201

    await _flush_mapping_conflicts(db, spec)

    if demoted is not None:
        await _audit(
            db, spec, request, org_id=org_id, entity_id=demoted.id,
            action="update", actor_id=actor_id,
            old_values={"is_accountable": True},
            new_values={"is_accountable": False},
        )

    await _audit(
        db, spec, request, org_id=org_id, entity_id=row.id,
        action=action, actor_id=actor_id,
        old_values=old_values, new_values=_tracked(spec, row),
    )

    await db.commit()

    response.status_code = status_code
    keyed = await _load_assignment_map(
        db, spec, org_id, item_ids=[payload.item_id],
    )
    for assignment in keyed.get(payload.item_id, []):
        if assignment["id"] == row.id:
            return assignment
    # Unreachable short of the row vanishing between commit and read.
    raise HTTPException(status_code=500, detail="Assignment could not be read back")


@router.delete(
    "/organizations/{org_id}/team-assignments/{assignment_id}",
    response_model=SuccessResponse,
)
async def delete_team_assignment(
    org_id: UUID,
    assignment_id: UUID,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """
    Remove a team's assignment from a control or evidence item.

    Requires: admin role.

    The path carries no ``type``, so each registered table is looked up in turn
    — bounded by the size of the registry, and driven by it, so phase 5's risk
    and vendor tables are searched by adding a registry entry and nothing else.
    Assignment ids are UUIDs, so at most one table can match.

    The row really is deleted, unlike a team, which archives. An assignment is
    a statement about the present — "this team owns this control" — and the
    audit trail carries the history of it. Nothing else points at one.

    Deleting the accountable assignment leaves the item with no accountable
    team, which is legal: it is the state every item is in until somebody picks
    one. Ownership does not silently transfer to a consulted team.
    """
    actor_id = _actor_id(membership)

    for spec in TEAM_ASSIGNMENT_TYPES.values():
        row = (await db.execute(
            select(spec.model).where(
                and_(
                    spec.model.id == assignment_id,
                    # The tenant predicate. An assignment id belonging to
                    # another organisation is indistinguishable from one that
                    # does not exist.
                    spec.model.organization_id == org_id,
                )
            )
        )).scalar_one_or_none()

        if row is None:
            continue

        old_values = _tracked(spec, row)
        await db.delete(row)
        await db.flush()

        await _audit(
            db, spec, request, org_id=org_id, entity_id=assignment_id,
            action="delete", actor_id=actor_id,
            old_values=old_values, new_values={},
        )

        await db.commit()
        return SuccessResponse(message="Team assignment removed")

    raise HTTPException(status_code=404, detail="Team assignment not found")
