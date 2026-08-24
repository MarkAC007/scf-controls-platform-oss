"""The team-assignment type registry (#822 phase 3).

One table, one entry per assignable thing. Everything that differs between
assigning a team to a *control* and assigning one to a piece of *evidence* —
the join table, the column naming the item, the audit entity type — is data in
:data:`TEAM_ASSIGNMENT_TYPES`, and everything that does not differ is written
once against :class:`TeamAssignmentSpec`.

That is deliberate and it is the reason this module exists rather than a pair
of near-identical routers. #822 specifies four assignable types: controls and
evidence (phase 3, here), risks and vendors (phase 5). Adding the second pair
must be an entry in this dict, not a rewrite of the endpoints, the bulk read,
or the resolution chain.

**Phase 3 registers controls and evidence only.** ``risk`` and ``vendor`` are
absent on purpose: their tables do not exist yet, and a registry entry naming a
model that has not been created is not a head start, it is an import error
waiting for whoever runs the migration. The shape below is what those two
entries will look like; adding them is a table entry and a migration.

Why four boring tables rather than one polymorphic one: the existing
``assignments`` table resolves its target through ``assignable_type`` plus a
bare ``assignable_id`` with no foreign key behind it, so the database cannot
tell a live control from a deleted one, or from a typo. Each table here names
its target and lets Postgres enforce it. The registry recovers the one thing
the polymorphic design was actually good at — uniform dispatch — without
giving up referential integrity to get it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Tuple

from sqlalchemy import and_, literal, or_, select

from models import (
    ControlTeamAssignment,
    EvidenceTeamAssignment,
    EvidenceTracking,
    ScopedControl,
)
from services.audit_service import (
    CONTROL_TEAM_ASSIGNMENT_TRACKED_FIELDS,
    EVIDENCE_TEAM_ASSIGNMENT_TRACKED_FIELDS,
)


@dataclass(frozen=True)
class TeamAssignmentSpec:
    """Everything that differs between one assignable type and another.

    Frozen: this is configuration read on every request, and a spec that could
    be mutated at runtime would be a cross-request footgun for no benefit.
    """

    #: The ``?type=`` value, and the discriminator in a POST body.
    type_key: str
    #: The join table mapping teams to items of this type.
    model: Any
    #: The model of the thing being assigned (``ScopedControl``, ...).
    item_model: Any
    #: Name of the column on :attr:`model` that names the item.
    item_id_field: str
    #: ``entity_type`` written to ``audit_log`` for rows of this type.
    entity_type: str
    #: Fields audited on create/update/delete.
    tracked_fields: frozenset
    #: Human name of the item, for error messages a user has to act on.
    item_label: str
    #: Constraint names Postgres reports, mapped to the 4xx they deserve. The
    #: strings are matched against the driver's message, so they must stay
    #: identical to the migration's.
    conflicts: Tuple[Tuple[str, int, str], ...]

    @property
    def item_column(self):
        """The mapped attribute naming the item, e.g. ``.scoped_control_id``."""
        return getattr(self.model, self.item_id_field)


CONTROL_ASSIGNMENT_SPEC = TeamAssignmentSpec(
    type_key="control",
    model=ControlTeamAssignment,
    item_model=ScopedControl,
    item_id_field="scoped_control_id",
    entity_type="control_team_assignment",
    tracked_fields=CONTROL_TEAM_ASSIGNMENT_TRACKED_FIELDS,
    item_label="Control",
    conflicts=(
        ("uq_control_team_assignments_control_team", 409,
         "This team is already assigned to this control"),
        ("uq_control_accountable_team", 409,
         "This control already has an accountable team"),
        ("fk_control_team_assignments_team_org", 400,
         "Team does not belong to this organisation"),
        ("fk_control_team_assignments_control_org", 400,
         "Control does not belong to this organisation"),
    ),
)

EVIDENCE_ASSIGNMENT_SPEC = TeamAssignmentSpec(
    type_key="evidence",
    model=EvidenceTeamAssignment,
    item_model=EvidenceTracking,
    item_id_field="evidence_tracking_id",
    entity_type="evidence_team_assignment",
    tracked_fields=EVIDENCE_TEAM_ASSIGNMENT_TRACKED_FIELDS,
    item_label="Evidence item",
    conflicts=(
        ("uq_evidence_team_assignments_evidence_team", 409,
         "This team is already assigned to this evidence item"),
        ("uq_evidence_accountable_team", 409,
         "This evidence item already has an accountable team"),
        ("fk_evidence_team_assignments_team_org", 400,
         "Team does not belong to this organisation"),
        ("fk_evidence_team_assignments_evidence_org", 400,
         "Evidence item does not belong to this organisation"),
    ),
)


#: The dispatch table. Phase 5 adds ``"risk"`` and ``"vendor"`` here.
TEAM_ASSIGNMENT_TYPES: Dict[str, TeamAssignmentSpec] = {
    spec.type_key: spec
    for spec in (CONTROL_ASSIGNMENT_SPEC, EVIDENCE_ASSIGNMENT_SPEC)
}

#: Rendered into the Pydantic pattern and the OpenAPI enum, so the accepted
#: ``type`` values cannot drift from what is actually dispatchable.
TEAM_ASSIGNMENT_TYPE_KEYS: Tuple[str, ...] = tuple(sorted(TEAM_ASSIGNMENT_TYPES))


def team_assignment_filter(
    spec: TeamAssignmentSpec,
    item_id_column: Any,
    *,
    organization_id: Any,
    team_id: Any = None,
    function_id: Any = None,
):
    """An EXISTS clause restricting a list to items a team (or function) works on.

    ``item_id_column`` is the outer query's column holding the item's primary
    key — ``ScopedControl.id`` for the controls list, ``EvidenceTracking.id``
    for evidence. The returned clause is correlated against it.

    **Why EXISTS and not a JOIN.** The semantics are "any assigned team", so a
    control with three assigned teams matches three rows in the assignment
    table. A JOIN would emit that control three times and, because the controls
    endpoint counts a subquery of its own filtered query, count it three times
    in ``total`` as well — a page with duplicates and a pagination footer that
    disagrees with it. EXISTS is a semi-join: it stops at the first match, so
    one item is one row no matter how many teams touch it. Postgres also gets
    to plan it as a hashed subplan against the assignment table's index rather
    than materialising the join.

    **Why the organisation is repeated inside the subquery.** The outer query
    is already scoped to the org, and the composite foreign keys make a
    cross-tenant assignment row unrepresentable, so this predicate is
    defence-in-depth rather than the isolation control (invariant 5 puts that
    in the database, where it is now). It also lets the planner use the
    ``(organization_id, team_id)`` index instead of scanning by item alone.

    Passing neither ``team_id`` nor ``function_id`` returns ``None``, so
    callers can hand the result straight to a conditional ``where`` and the
    no-filter path adds no SQL whatsoever.
    """
    if team_id is None and function_id is None:
        return None

    from models import Function, Team  # local: avoids a circular import at module load

    stmt = select(literal(1)).select_from(spec.model)
    conditions = [
        spec.item_column == item_id_column,
        spec.model.organization_id == organization_id,
    ]

    if team_id is not None:
        conditions.append(spec.model.team_id == team_id)

    if function_id is not None:
        # The function lives on the team, not the assignment, so reach it
        # through the team. Joined on the org as well as the id so a team can
        # only ever qualify its own tenant's rows.
        stmt = stmt.join(
            Team,
            and_(
                Team.id == spec.model.team_id,
                Team.organization_id == spec.model.organization_id,
            ),
        )
        conditions.append(or_(
            Team.function_id == function_id,
            Team.functions.any(Function.id == function_id),
        ))

    return stmt.where(and_(*conditions)).exists()


def accountable_owner_filter(
    spec: TeamAssignmentSpec,
    item_id_column: Any,
    *,
    organization_id: Any,
    accountable_owner_type: Any = None,
):
    """An EXISTS clause restricting a list by who owns the item (#822 phase 2).

    "Owns" here is one specific person: the **primary** member of the item's
    **accountable** team. #822 asks for the items an external contractor is on
    the hook for, and that is the chain the model already encodes --
    accountable team (``is_accountable``), then that team's primary
    (``team_members.membership_role = 'primary'``), then that person's
    employment type at this organisation
    (``organization_members.member_type``).

    Deliberately not "any team member is a contractor", and not "any assigned
    team": a consulted team's contractor is not accountable for the item, and
    reporting them as such would overstate contractor exposure in exactly the
    report someone reaches for to size it.

    ``item_id_column`` is the outer query's column holding the item's primary
    key, as in :func:`team_assignment_filter`, and this is an EXISTS for the
    same reason -- one item stays one row, so a paginated ``total`` computed
    from the filtered query still agrees with the page beneath it. (The chain
    is one-to-one at every hop here, since a team has at most one primary and
    a control at most one accountable team, but the semi-join costs nothing
    and keeps both filters answering in the same shape.)

    ``organization_id`` is pinned on the assignment row, and both joins carry
    the organisation with them, so no hop can wander into another tenant even
    though ``team_members`` and ``organization_members`` are reachable by id
    alone.

    Passing ``None`` returns ``None``, so the no-filter path adds no SQL.

    This lives beside :func:`team_assignment_filter` and is called by both list
    endpoints for the reason that module exists: the controls list and the
    evidence list must not be able to answer "whose contractors?" differently.
    """
    if accountable_owner_type is None:
        return None

    # local: same idiom as team_assignment_filter above.
    from models import OrganizationMember, TeamMember

    return (
        select(literal(1))
        .select_from(spec.model)
        .join(
            TeamMember,
            and_(
                TeamMember.team_id == spec.model.team_id,
                # The organisation travels with the join rather than being
                # trusted from team_id alone.
                TeamMember.organization_id == spec.model.organization_id,
                TeamMember.membership_role == 'primary',
            ),
        )
        .join(
            OrganizationMember,
            and_(
                OrganizationMember.organization_id == TeamMember.organization_id,
                OrganizationMember.user_id == TeamMember.user_id,
            ),
        )
        .where(
            and_(
                spec.item_column == item_id_column,
                spec.model.organization_id == organization_id,
                spec.model.is_accountable.is_(True),
                OrganizationMember.member_type == accountable_owner_type,
            )
        )
        .exists()
    )
