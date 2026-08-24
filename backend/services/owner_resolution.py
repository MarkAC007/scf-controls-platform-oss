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
Being consulted means being informed, not being paged.

**They are notified on escalation, and only there** (#822's Notifications note).
That is the one place ``is_accountable = false`` changes who hears about an
item — without it the flag would mean nothing for notifications at all, and a
consulted team would be indistinguishable from a team that was never assigned.
The volume argument still holds, because escalations fire at most three times
in an item's whole life (0 / +7 / +30 days): five consulted teams is ten people
three times, not ten people every day, which is the "informed" the note
describes. Escalation uses its own query rather than a relaxed tier 2 — see
:func:`_consulted_teams_stmt`.

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


# ===========================================================================
# Phase 4 — the one function the nine notification sites call
# ===========================================================================
#
# Everything above is phase 3: the pure tier decision, and a convenience that
# fetches the tiers for a control or an evidence item. What follows is what
# ``services/notifications.py`` actually calls, and it adds the three things
# the chain alone does not say:
#
#   * **which tiers an event is even allowed to use.** A mention names people;
#     falling a mention through to "the accountable team, then every org
#     admin" would page an organisation because somebody typed an ``@`` and
#     got the handle wrong. A due date, by contrast, is exactly the question
#     the chain exists to answer. The policy is data in :data:`EVENTS`, one
#     row per notification type, rather than an ``if`` at nine call sites.
#   * **tasks**, which are not in the team-assignment registry at all and
#     never will be — a task inherits ownership from its evidence item
#     (#822 §6), so its tier 2 is its own ``owning_team_id`` when set and its
#     parent's accountable team when not.
#   * **escalation**, which is additive rather than a tier: an overdue item
#     reaches the owning team *on top of* whoever the chain resolved, so a
#     stalled task with a named assignee still surfaces to the team.

#: Tier 1 only — the event names its recipients and the platform must not
#: invent more. Assignment and @mention.
ROUTE_DIRECTED = "directed"

#: The full 1-2-3 chain. "Who is responsible for this item?"
ROUTE_OWNERSHIP = "ownership"

#: Tier 3 only. The audience *is* the organisation's admins, because they are
#: the people who do the thing the notification asks for (review a control,
#: answer an auditor's query, check a catalogue reconciliation). Routing these
#: through the chain would hand a control's assignee a review request that
#: their role may not even permit them to action.
ROUTE_ORG_ADMIN = "org_admin"


@dataclass(frozen=True)
class NotificationEvent:
    """One notification type's routing policy.

    ``escalates`` is deliberately separate from ``routing``: escalation is not
    a fourth tier, it is an *addition* to whatever tier won. #822 names three
    escalation events — overdue, at risk, and evidence rejected — and for each
    of them the accountable team's primary and delegate are notified on top of
    the resolved set, so a stalled item reaches the owning team even when an
    individual is assigned to it.
    """

    key: str
    routing: str
    escalates: bool = False

    @property
    def uses_team_tier(self) -> bool:
        return self.routing == ROUTE_OWNERSHIP or self.escalates


#: The routing table. Adding a notification type means adding a row here, not
#: writing a new recipient expression — which is the whole point of #822's
#: notifications section. Phase 6's four risk and vendor types are rows in
#: this dict and nothing else.
EVENTS: dict = {
    event.key: event
    for event in (
        # --- directed: the caller already named the humans -----------------
        NotificationEvent("assignment", ROUTE_DIRECTED),
        NotificationEvent("mention", ROUTE_DIRECTED),
        # --- ownership: the chain answers "whose is this?" ------------------
        NotificationEvent("task_due", ROUTE_OWNERSHIP),
        NotificationEvent("task_overdue", ROUTE_OWNERSHIP, escalates=True),
        NotificationEvent("evidence_rejected", ROUTE_OWNERSHIP, escalates=True),
        NotificationEvent("composite_insufficient", ROUTE_OWNERSHIP, escalates=True),
        # --- org admin: the admins are the audience, not the fallback ------
        NotificationEvent("control_ready_for_review", ROUTE_ORG_ADMIN),
        NotificationEvent("engagement_query_raised", ROUTE_ORG_ADMIN),
        NotificationEvent("catalog_reconciliation_applied", ROUTE_ORG_ADMIN),
        NotificationEvent("catalog_reconciliation_rolled_back", ROUTE_ORG_ADMIN),
    )
}


def event_for(key: str) -> NotificationEvent:
    """The policy for a notification type.

    An unregistered type falls back to :data:`ROUTE_DIRECTED` rather than
    raising. A new notification type is then delivered to exactly the people
    its caller named — quiet and correct — instead of taking down the code
    path that creates it, and instead of silently defaulting to the widest
    audience in the building.
    """
    return EVENTS.get(key) or NotificationEvent(key, ROUTE_DIRECTED)


@dataclass(frozen=True)
class Item:
    """The bit of an item the resolver needs, decoupled from its ORM class.

    Callers build one of these from a row they already hold. Passing the row
    itself would make this module import — and stay in step with — six model
    classes whose only shared property is "somebody owns me".

    Args:
        item_type: ``control``, ``evidence``, ``task``, and in phase 5
            ``risk`` and ``vendor``. Anything in
            :data:`~services.team_assignments.TEAM_ASSIGNMENT_TYPES` resolves
            its accountable team through that registry; ``task`` is handled
            separately because a task has no join table by design.
        explicit_user_ids: tier 1 — every user the item itself names.
        owning_team_id: tasks only. ``None`` means inherit, which is the
            common case and the reason the column is nullable.
        parent: the evidence item a task hangs off, so an inheriting task can
            reach its accountable team. ``None`` on a top-level item.
    """

    item_type: str
    item_id: Optional[UUID]
    #: ``None`` only for a directed event, which resolves no tier that needs
    #: an organisation. Every other route requires it.
    organization_id: Optional[UUID]
    explicit_user_ids: Tuple = ()
    owning_team_id: Optional[UUID] = None
    parent: Optional["Item"] = None


@dataclass(frozen=True)
class RecipientResolution:
    """Who to notify, and enough detail to explain why.

    :attr:`user_ids` is the answer. The rest exists so a test can assert
    *which* rule produced it — a set that happens to be right for the wrong
    reason is the failure mode a resolution chain is most prone to.
    """

    user_ids: frozenset
    tier: Optional[str]
    escalated_user_ids: frozenset = frozenset()

    def __bool__(self) -> bool:
        return bool(self.user_ids)


def resolve_recipients(
    item: Item,
    event: NotificationEvent,
    *,
    accountable_team_members: Iterable[Tuple[Optional[UUID], Optional[str]]] = (),
    consulted_team_members: Iterable[Tuple[Optional[UUID], Optional[str]]] = (),
    organization_admin_user_ids: Iterable[Optional[UUID]] = (),
    exclude_user_ids: Iterable[Optional[UUID]] = (),
) -> RecipientResolution:
    """Who should be notified about ``item`` for ``event``. Pure; never raises.

    The tiers are supplied rather than fetched, exactly as
    :func:`resolve_owners` supplies them, so the decision is testable without
    a database and the two async wrappers below cannot drift from it.

    **The result is a set.** Somebody who is both the explicit assignee and
    the accountable team's primary is in it once, and therefore receives one
    notification rather than two.

    **Consulted teams never appear on the routine path.**
    ``accountable_team_members`` is fetched from the accountable assignment
    only. A control with five consulted teams pages nobody extra for an
    ordinary event; consulted means informed, which is the team view, not
    paged.

    **On an escalation they do appear**, through the separate
    ``consulted_team_members`` argument — see the escalation block below. That
    is the one place ``is_accountable = false`` changes who hears about an
    item, and without it the flag would mean nothing at all for notifications.
    The volume stays bounded because escalations fire at most three times in an
    item's whole life (0 / +7 / +30 days), so five consulted teams is ten
    people three times, not ten people daily.

    **Team-tier routing activates only when a team is assigned.** With no
    accountable team the tier is empty, the chain falls through it, and an
    organisation that has never created a team resolves to precisely the
    people it resolves to today. That is the property that makes this
    upgrade-safe, and it is a property of the data rather than of a feature
    flag.
    """
    excluded = _clean(exclude_user_ids)

    if event.routing == ROUTE_DIRECTED:
        resolved = OwnerResolution(
            user_ids=frozenset(_clean(item.explicit_user_ids) - excluded),
            tier=OWNER_TIER_EXPLICIT if _clean(item.explicit_user_ids) else None,
        )
    elif event.routing == ROUTE_ORG_ADMIN:
        admins = _clean(organization_admin_user_ids)
        resolved = OwnerResolution(
            user_ids=frozenset(admins - excluded),
            tier=OWNER_TIER_ORG_ADMIN if admins else None,
        )
    else:
        resolved = resolve_owners(
            explicit_user_ids=item.explicit_user_ids,
            accountable_team_members=accountable_team_members,
            organization_admin_user_ids=organization_admin_user_ids,
            exclude_user_ids=exclude_user_ids,
        )

    escalated: frozenset = frozenset()
    if event.escalates:
        # Additive, not a tier, and deliberately not routed through the chain
        # above: the chain stops at the first non-empty tier, so a stalled item
        # that tier 1 answered would never reach a team through it. Both team
        # sets are unioned in flat, then deduped against the resolved set by
        # the frozenset union at the end.
        #
        # Accountable AND consulted, per #822's Notifications note: a consulted
        # team is informed rather than paged, and an escalation is precisely
        # the moment "informed" is the point. Plain `member` is excluded from
        # both, exactly as on the routine path -- being on a team is not being
        # on point for its work.
        escalated = frozenset(_clean(
            user_id
            for user_id, role in (
                list(accountable_team_members) + list(consulted_team_members)
            )
            if role in ACCOUNTABLE_MEMBERSHIP_ROLES
        ) - excluded)

    return RecipientResolution(
        user_ids=resolved.user_ids | escalated,
        tier=resolved.tier,
        escalated_user_ids=escalated,
    )


# --- tier fetching ---------------------------------------------------------
#
# Statement *builders*, not queries. Both the async path (the API and the
# schedulers) and the sync path (composite_service, which runs under Celery on
# a synchronous Session) execute the same SQLAlchemy objects, so there is one
# definition of "the accountable team's primary and delegate" rather than two
# that agree until somebody edits one of them.


def _team_roster_stmt(organization_id: UUID, team_id: UUID):
    """The primary and delegate of one named team."""
    return (
        select(TeamMember.user_id, TeamMember.membership_role)
        .where(
            TeamMember.team_id == team_id,
            TeamMember.organization_id == organization_id,
            TeamMember.membership_role.in_(ACCOUNTABLE_MEMBERSHIP_ROLES),
        )
    )


def _assigned_team_stmt(
    organization_id: UUID, item_type: str, item_id: UUID, *, accountable: bool,
):
    """Primary and delegate of the teams assigned to an item, by RACI role.

    ``accountable=True`` gives the one accountable team — tier 2 of the routine
    chain. ``accountable=False`` gives the N consulted teams, which are used
    **only** to widen an escalation and never appear on the routine path.

    One builder rather than two because the two queries differ by a single
    boolean, and a copy would be the obvious place for the membership-role
    filter to drift between them.

    Returns ``None`` for a type with no join table — ``task``, and in the
    current phase ``risk`` and ``vendor``, whose tables arrive in phase 5.
    A caller treats ``None`` as an empty set and the chain falls through it,
    so registering those types later is a registry entry and no change here.
    """
    from services.team_assignments import TEAM_ASSIGNMENT_TYPES

    spec = TEAM_ASSIGNMENT_TYPES.get(item_type)
    if spec is None:
        return None

    return (
        select(TeamMember.user_id, TeamMember.membership_role)
        .join(Team, Team.id == TeamMember.team_id)
        .join(spec.model, spec.model.team_id == Team.id)
        .where(
            spec.item_column == item_id,
            spec.model.is_accountable.is_(accountable),
            spec.model.organization_id == organization_id,
            TeamMember.organization_id == organization_id,
            TeamMember.membership_role.in_(ACCOUNTABLE_MEMBERSHIP_ROLES),
        )
    )


def _accountable_team_stmt(organization_id: UUID, item_type: str, item_id: UUID):
    """Tier 2: the accountable team's primary and delegate."""
    return _assigned_team_stmt(
        organization_id, item_type, item_id, accountable=True,
    )


def _consulted_teams_stmt(item: Item):
    """Escalation only: primary and delegate of every CONSULTED team.

    Deliberately a separate statement from :func:`_tier_two_stmt` rather than a
    relaxation of it. The routine path stops at the first non-empty tier, and
    threading consulted teams through that short-circuit is how the tier
    ordering gets broken by accident — a consulted team would start answering
    for an item whose accountable team is perfectly well defined.

    Consulted assignments hang off the item that *has* assignments. A task has
    none by design (it inherits), so a task's consulted teams are its parent
    evidence item's.
    """
    source = item.parent if item.item_type == "task" else item
    if source is None or source.item_id is None:
        return None
    return _assigned_team_stmt(
        source.organization_id, source.item_type, source.item_id,
        accountable=False,
    )


def _tier_two_stmt(item: Item):
    """The statement that fills tier 2 for any item, task or not.

    A task's ownership is inherited (#822 §6):

    * ``owning_team_id`` set — that team. The setup/collect/review split on
      one evidence item is routinely three different functions, and this is
      the column that says so.
    * ``owning_team_id`` NULL — the parent evidence item's accountable team.
      Nothing is copied down onto the task, so the two cannot drift.

    Returns ``None`` when there is no team to reach, which is the state of
    every item in an organisation that has not created any.
    """
    if item.owning_team_id is not None:
        return _team_roster_stmt(item.organization_id, item.owning_team_id)

    if item.item_type == "task":
        if item.parent is None or item.parent.item_id is None:
            return None
        return _accountable_team_stmt(
            item.parent.organization_id, item.parent.item_type, item.parent.item_id,
        )

    if item.item_id is None:
        return None
    return _accountable_team_stmt(
        item.organization_id, item.item_type, item.item_id,
    )


def _org_admin_stmt(organization_id: UUID):
    return select(OrganizationMember.user_id).where(
        OrganizationMember.organization_id == organization_id,
        OrganizationMember.role == "admin",
    )


async def resolve_recipients_for(
    db: AsyncSession,
    item: Item,
    event_key: str,
    *,
    exclude_user_ids: Iterable[Optional[UUID]] = (),
) -> RecipientResolution:
    """Fetch the tiers ``event_key`` is allowed to use, and resolve them.

    Only the tiers the policy can actually reach are queried. A directed event
    runs **no** queries at all, which matters: ``create_mention_notifications``
    is on the comment-post path and must not acquire two extra round trips to
    reach the same answer it reaches today.
    """
    event = event_for(event_key)

    team_members: list = []
    if event.uses_team_tier:
        stmt = _tier_two_stmt(item)
        if stmt is not None:
            team_members = list((await db.execute(stmt)).all())

    # Only an escalating event pays for this second query. An ordinary
    # task_due runs exactly what it ran before.
    consulted: list = []
    if event.escalates:
        stmt = _consulted_teams_stmt(item)
        if stmt is not None:
            consulted = list((await db.execute(stmt)).all())

    admins: list = []
    if event.routing in (ROUTE_OWNERSHIP, ROUTE_ORG_ADMIN):
        admins = list((await db.execute(
            _org_admin_stmt(item.organization_id)
        )).scalars().all())

    return resolve_recipients(
        item, event,
        accountable_team_members=team_members,
        consulted_team_members=consulted,
        organization_admin_user_ids=admins,
        exclude_user_ids=exclude_user_ids,
    )


def resolve_recipients_for_sync(
    session,
    item: Item,
    event_key: str,
    *,
    exclude_user_ids: Iterable[Optional[UUID]] = (),
) -> RecipientResolution:
    """:func:`resolve_recipients_for` against a synchronous ``Session``.

    ``services/composite_service.py`` runs under Celery on a sync session, so
    one of the nine sites cannot await. It executes the same statement
    builders and calls the same pure resolver; only the two ``execute`` lines
    differ.
    """
    event = event_for(event_key)

    team_members: list = []
    if event.uses_team_tier:
        stmt = _tier_two_stmt(item)
        if stmt is not None:
            team_members = list(session.execute(stmt).all())

    consulted: list = []
    if event.escalates:
        stmt = _consulted_teams_stmt(item)
        if stmt is not None:
            consulted = list(session.execute(stmt).all())

    admins: list = []
    if event.routing in (ROUTE_OWNERSHIP, ROUTE_ORG_ADMIN):
        admins = list(session.execute(
            _org_admin_stmt(item.organization_id)
        ).scalars().all())

    return resolve_recipients(
        item, event,
        accountable_team_members=team_members,
        consulted_team_members=consulted,
        organization_admin_user_ids=admins,
        exclude_user_ids=exclude_user_ids,
    )
