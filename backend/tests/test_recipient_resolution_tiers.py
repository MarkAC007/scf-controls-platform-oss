"""Who gets notified: ``resolve_recipients`` at every tier (#822 phase 4).

#822 replaces nine hand-rolled recipient expressions in
``services/notifications.py`` with one function. This file is the contract for
that function, and it is deliberately mostly database-free: a rule that can
only be exercised by standing up an organisation, a roster, a control and a
task is a rule nobody exercises.

The criteria covered here:

* ``resolve_recipients(item, event)`` unit-tested at **every tier including the
  all-empty case**;
* the team tier returns **both** primary and delegate;
* the result is a **set** — somebody who is both the explicit assignee and the
  team primary gets exactly one notification, not two;
* **consulted (non-accountable) teams are not on the routine path**;
* a task with ``owning_team_id IS NULL`` resolves to its **parent evidence
  item's** accountable team, and a task with ``owning_team_id`` set
  **overrides** the parent;
* an organisation with **no teams** resolves to exactly the people it resolves
  to today — the property that makes this upgrade-safe.

The last two are behavioural: inheritance is a decision about which SQL to
issue, and asserting it against a hand-built statement would prove only that
the test agrees with the code. Those classes need PostgreSQL and **SKIP in
CI**; a green CI run is not evidence for them.

Run with::

    docker compose exec -T backend python -m pytest \\
        tests/test_recipient_resolution_tiers.py -v
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import catalog_models  # noqa: E402,F401
from models import (  # noqa: E402
    EvidenceCollectionTask,
    EvidenceTeamAssignment,
    EvidenceTracking,
    Function,
    Organization,
    OrganizationMember,
    Team,
    TeamMember,
    User,
)
from services.owner_resolution import (  # noqa: E402
    ACCOUNTABLE_MEMBERSHIP_ROLES,
    EVENTS,
    OWNER_TIER_ACCOUNTABLE_TEAM,
    OWNER_TIER_EXPLICIT,
    OWNER_TIER_ORG_ADMIN,
    ROUTE_DIRECTED,
    ROUTE_ORG_ADMIN,
    ROUTE_OWNERSHIP,
    Item,
    NotificationEvent,
    event_for,
    resolve_recipients,
    resolve_recipients_for,
)

DATABASE_URL = os.getenv("DATABASE_URL", "")

requires_postgres = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason=(
        "needs a Postgres DATABASE_URL — SKIPPED, not passed. Task inheritance "
        "is a claim about which query runs, and only a database can settle it"
    ),
)


def _uid():
    return uuid.uuid4()


ORG = _uid()

#: The one event used by every tier test below. Ownership routing is the
#: interesting route — the other two are asserted separately.
OWNERSHIP = NotificationEvent("test_ownership", ROUTE_OWNERSHIP)
ESCALATING = NotificationEvent("test_escalating", ROUTE_OWNERSHIP, escalates=True)


def _item(**overrides) -> Item:
    fields = dict(
        item_type="evidence", item_id=_uid(), organization_id=ORG,
        explicit_user_ids=(),
    )
    fields.update(overrides)
    return Item(**fields)


# ---------------------------------------------------------------------------
# Every tier, including the one nobody writes a test for
# ---------------------------------------------------------------------------

class TestTierOneExplicitAssignment:
    def test_the_named_assignee_wins(self):
        assignee, primary, admin = _uid(), _uid(), _uid()

        result = resolve_recipients(
            _item(explicit_user_ids=(assignee,)), OWNERSHIP,
            accountable_team_members=[(primary, "primary")],
            organization_admin_user_ids=[admin],
        )

        assert result.tier == OWNER_TIER_EXPLICIT
        assert result.user_ids == frozenset({assignee})

    def test_every_named_user_is_taken_not_just_the_first(self):
        """A control names two: ``assigned_user_id`` and ``owner_user_id``."""
        assigned, owner = _uid(), _uid()

        result = resolve_recipients(
            _item(explicit_user_ids=(assigned, owner)), OWNERSHIP,
            organization_admin_user_ids=[_uid()],
        )

        assert result.user_ids == frozenset({assigned, owner})

    def test_a_none_does_not_count_as_a_populated_tier(self):
        """``SET NULL`` is the normal state of these columns, not an error.

        This is the whole reason #822 exists: the day somebody leaves, tier 1
        becomes ``(None, None)``. If that counted as populated, the chain
        would stop at an empty tier and nobody would be notified — the
        accountability evaporation, reproduced inside the fix.
        """
        primary = _uid()

        result = resolve_recipients(
            _item(explicit_user_ids=(None, None)), OWNERSHIP,
            accountable_team_members=[(primary, "primary")],
        )

        assert result.tier == OWNER_TIER_ACCOUNTABLE_TEAM
        assert result.user_ids == frozenset({primary})


class TestTierTwoTheAccountableTeam:
    def test_both_primary_and_delegate_are_returned(self):
        """Not delegate-on-escalation. Both, in parallel.

        A primary on annual leave would otherwise be a silent single point of
        failure, which is precisely the failure mode teams exist to remove.
        """
        primary, delegate = _uid(), _uid()

        result = resolve_recipients(
            _item(), OWNERSHIP,
            accountable_team_members=[(primary, "primary"), (delegate, "delegate")],
            organization_admin_user_ids=[_uid()],
        )

        assert result.tier == OWNER_TIER_ACCOUNTABLE_TEAM
        assert result.user_ids == frozenset({primary, delegate})

    def test_a_delegate_alone_is_a_populated_tier(self):
        """A team whose primary seat is vacant still owns its work."""
        delegate = _uid()

        result = resolve_recipients(
            _item(), OWNERSHIP,
            accountable_team_members=[(delegate, "delegate")],
            organization_admin_user_ids=[_uid()],
        )

        assert result.tier == OWNER_TIER_ACCOUNTABLE_TEAM
        assert result.user_ids == frozenset({delegate})

    def test_plain_members_are_not_paged(self):
        """Being on the accountable team is not being accountable for it.

        Note what this test would look like if it were wrong: the roster is
        three people and the answer is three people. Here the answer is two,
        and the third is named so the assertion cannot pass by coincidence.
        """
        primary, delegate, member = _uid(), _uid(), _uid()

        result = resolve_recipients(
            _item(), OWNERSHIP,
            accountable_team_members=[
                (primary, "primary"), (delegate, "delegate"), (member, "member"),
            ],
        )

        assert result.user_ids == frozenset({primary, delegate})
        assert member not in result.user_ids

    def test_a_roster_of_nothing_but_members_is_an_empty_tier(self):
        """And therefore falls through to the org admins, rather than
        resolving to a team that pages nobody."""
        member, admin = _uid(), _uid()

        result = resolve_recipients(
            _item(), OWNERSHIP,
            accountable_team_members=[(member, "member")],
            organization_admin_user_ids=[admin],
        )

        assert result.tier == OWNER_TIER_ORG_ADMIN
        assert result.user_ids == frozenset({admin})

    def test_the_paged_roles_are_exactly_primary_and_delegate(self):
        assert ACCOUNTABLE_MEMBERSHIP_ROLES == frozenset({"primary", "delegate"})


class TestTierThreeOrgAdmins:
    def test_the_admins_are_the_last_resort(self):
        first, second = _uid(), _uid()

        result = resolve_recipients(
            _item(), OWNERSHIP, organization_admin_user_ids=[first, second],
        )

        assert result.tier == OWNER_TIER_ORG_ADMIN
        assert result.user_ids == frozenset({first, second})


class TestTheAllEmptyCase:
    """The tier nobody writes a test for, and the one that takes a scheduler
    down when it raises."""

    def test_nothing_anywhere_resolves_to_nobody_without_raising(self):
        result = resolve_recipients(_item(), OWNERSHIP)

        assert result.user_ids == frozenset()
        assert result.tier is None
        assert bool(result) is False

    def test_an_item_with_no_organisation_still_resolves(self):
        """A directed event carries its own recipients and needs no org."""
        named = _uid()

        result = resolve_recipients(
            _item(organization_id=None, explicit_user_ids=(named,)),
            NotificationEvent("assignment", ROUTE_DIRECTED),
        )

        assert result.user_ids == frozenset({named})

    def test_every_tier_present_but_every_entry_none(self):
        result = resolve_recipients(
            _item(explicit_user_ids=(None,)), OWNERSHIP,
            accountable_team_members=[(None, "primary")],
            organization_admin_user_ids=[None],
        )

        assert result.user_ids == frozenset()
        assert result.tier is None


# ---------------------------------------------------------------------------
# The result is a set
# ---------------------------------------------------------------------------

class TestOnePersonGetsOneNotification:
    def test_the_assignee_who_is_also_the_team_primary_appears_once(self):
        """The criterion, stated exactly: *exactly one* notification.

        Tier 1 wins outright here, so the set never contains a duplicate to
        collapse. The test below is the one where both tiers really do
        contribute, which is where a list would show two rows and a set one.
        """
        person = _uid()

        result = resolve_recipients(
            _item(explicit_user_ids=(person,)), OWNERSHIP,
            accountable_team_members=[(person, "primary")],
        )

        assert result.user_ids == frozenset({person})
        assert len(result.user_ids) == 1

    def test_an_escalation_that_reaches_the_same_person_twice_still_sends_once(
        self,
    ):
        """Escalation is additive — tier 1's answer *plus* the accountable
        team. When they are the same person, the union is one recipient.

        Both halves are asserted to be non-empty first, so this cannot pass by
        one of them being empty and there being nothing to deduplicate.
        """
        person, delegate = _uid(), _uid()

        result = resolve_recipients(
            _item(explicit_user_ids=(person,)), ESCALATING,
            accountable_team_members=[(person, "primary"), (delegate, "delegate")],
        )

        assert result.tier == OWNER_TIER_EXPLICIT          # tier 1 contributed
        assert result.escalated_user_ids == frozenset({person, delegate})
        assert result.user_ids == frozenset({person, delegate})
        assert len(result.user_ids) == 2                   # not three

    def test_the_result_is_a_frozenset_not_a_sequence(self):
        """Structural, and cheap: a list would let a caller write two rows for
        one person without any test noticing until production."""
        result = resolve_recipients(
            _item(explicit_user_ids=(_uid(),)), OWNERSHIP,
        )
        assert isinstance(result.user_ids, frozenset)

    def test_the_same_user_named_twice_in_tier_one_appears_once(self):
        """A control whose ``assigned_user_id`` and ``owner_user_id`` are the
        same person — common, and the simplest duplicate there is."""
        person = _uid()

        result = resolve_recipients(
            _item(explicit_user_ids=(person, person)), OWNERSHIP,
        )

        assert result.user_ids == frozenset({person})


# ---------------------------------------------------------------------------
# Consulted teams
# ---------------------------------------------------------------------------

class TestConsultedTeamsAreNotOnTheRoutinePath:
    """A control with five consulted teams would page ten people for an
    ordinary event. Consulted means informed, not paged.

    The isolation is structural: ``accountable_team_members`` is fetched from
    the *accountable* assignment only, so a consulted team's roster is never
    handed to the resolver at all. Two tests, because the claim has two
    halves — the function ignores what it is not given (below), and the query
    does not give it (:class:`TestTheAccountableFilterIsInTheQuery`).
    """

    def test_a_consulted_roster_that_is_never_fetched_cannot_be_notified(self):
        primary, consulted_primary = _uid(), _uid()

        result = resolve_recipients(
            _item(), OWNERSHIP,
            accountable_team_members=[(primary, "primary")],
        )

        assert result.user_ids == frozenset({primary})
        assert consulted_primary not in result.user_ids

    def test_escalation_reaches_the_accountable_team_only(self):
        """Even on escalation, which is the widest routine path there is."""
        assignee, primary = _uid(), _uid()

        result = resolve_recipients(
            _item(explicit_user_ids=(assignee,)), ESCALATING,
            accountable_team_members=[(primary, "primary")],
        )

        assert result.escalated_user_ids == frozenset({primary})
        assert result.user_ids == frozenset({assignee, primary})


class TestTheAccountableFilterIsInTheQuery:
    """The other half: the statement that fills tier 2 asks for the
    accountable assignment, not for every assignment.

    Asserted on the compiled SQL rather than by round-trip, because the
    round-trip version passes just as happily against a query with no filter
    when the fixture happens to have created only one team.
    """

    def test_the_tier_two_statement_filters_on_is_accountable(self):
        from services.owner_resolution import _tier_two_stmt

        stmt = _tier_two_stmt(_item(item_type="evidence"))
        assert stmt is not None, "evidence must have a tier-2 statement"
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "is_accountable" in sql

    def test_the_tier_two_statement_filters_on_the_membership_role(self):
        from services.owner_resolution import _tier_two_stmt

        sql = str(_tier_two_stmt(_item(item_type="evidence")).compile(
            compile_kwargs={"literal_binds": True}
        ))
        assert "membership_role" in sql
        assert "primary" in sql and "delegate" in sql
        assert "member'" not in sql.replace("membership_role", "")


# ---------------------------------------------------------------------------
# An org with no teams
# ---------------------------------------------------------------------------

class TestAnOrganisationWithNoTeams:
    """No silent volume increase on upgrade.

    Team-tier routing activates only when a team is assigned to the item, and
    that is a property of the data rather than of a feature flag. With no
    accountable team the tier is empty, the chain falls through it, and the
    answer is the pre-#822 answer: the named assignee, or failing that the org
    admins.

    The scheduler-level version of this claim — byte-for-byte the same
    notification rows — is in ``test_notification_scheduler_teams.py``. This is
    the resolver-level version.
    """

    @pytest.mark.parametrize("event_key", sorted(EVENTS))
    def test_the_answer_matches_the_legacy_expression_for_every_event(
        self, event_key
    ):
        assignee, admin = _uid(), _uid()
        event = event_for(event_key)

        result = resolve_recipients(
            _item(explicit_user_ids=(assignee,)), event,
            accountable_team_members=[],          # no teams in this org
            organization_admin_user_ids=[admin],
        )

        if event.routing == ROUTE_ORG_ADMIN:
            legacy = frozenset({admin})
        else:
            legacy = frozenset({assignee})
        assert result.user_ids == legacy

    @pytest.mark.parametrize("event_key", sorted(EVENTS))
    def test_no_event_escalates_to_anybody_when_there_is_no_team(self, event_key):
        result = resolve_recipients(
            _item(explicit_user_ids=(_uid(),)), event_for(event_key),
            accountable_team_members=[],
            organization_admin_user_ids=[_uid()],
        )
        assert result.escalated_user_ids == frozenset()

    def test_an_unassigned_item_in_a_teamless_org_still_reaches_the_admins(self):
        """Today's last-resort behaviour, preserved."""
        admin = _uid()

        result = resolve_recipients(
            _item(), OWNERSHIP, organization_admin_user_ids=[admin],
        )

        assert result.user_ids == frozenset({admin})


class TestTheRoutingTable:
    """Which tiers an event may use is data, not an ``if`` at nine call sites."""

    def test_directed_events_never_widen_beyond_the_people_they_name(self):
        """A mistyped ``@`` handle must not page an organisation."""
        for key in ("assignment", "mention"):
            assert event_for(key).routing == ROUTE_DIRECTED

        result = resolve_recipients(
            _item(explicit_user_ids=()), event_for("mention"),
            accountable_team_members=[(_uid(), "primary")],
            organization_admin_user_ids=[_uid()],
        )
        assert result.user_ids == frozenset()

    def test_the_task_schedulers_route_through_ownership(self):
        """The criterion that matters most: an unassigned task must not stop
        at an empty tier 1."""
        assert event_for("task_due").routing == ROUTE_OWNERSHIP
        assert event_for("task_overdue").routing == ROUTE_OWNERSHIP

    def test_the_three_escalation_events_are_the_three_named_in_the_issue(self):
        escalating = {key for key, e in EVENTS.items() if e.escalates}
        assert escalating == {
            "task_overdue", "evidence_rejected", "composite_insufficient",
        }

    def test_an_unregistered_type_stays_narrow_rather_than_widening(self):
        """The safe default. Defaulting to the ownership chain would make a
        new notification type page an organisation the first time anybody
        forgot to add a row."""
        assert event_for("some_type_nobody_registered").routing == ROUTE_DIRECTED


# ---------------------------------------------------------------------------
# Task inheritance — needs PostgreSQL. SKIPS in CI.
# ---------------------------------------------------------------------------

@pytest.fixture
async def db():
    engine = create_async_engine(DATABASE_URL)
    try:
        async with engine.connect():
            pass
    except Exception as exc:  # pragma: no cover - environment dependent
        await engine.dispose()
        pytest.skip(f"database not reachable: {exc}")

    session_factory = async_sessionmaker(
        engine, expire_on_commit=False, autoflush=False
    )
    session = session_factory()
    try:
        yield session
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


class _World:
    """One org, two teams with full rosters, an evidence item, and a task."""

    def __init__(self, **kw):
        self.__dict__.update(kw)

    def task_item(self, task):
        return Item(
            item_type="task", item_id=task.id,
            organization_id=task.organization_id,
            explicit_user_ids=(task.assigned_user_id,),
            owning_team_id=task.owning_team_id,
            parent=Item(
                item_type="evidence", item_id=self.evidence.id,
                organization_id=self.org.id,
            ),
        )


@pytest.fixture
async def world(db):
    function = (await db.execute(
        select(Function).where(Function.is_active.is_(True)).limit(1)
    )).scalar_one_or_none()
    if function is None:  # pragma: no cover - environment dependent
        pytest.skip("no seeded functions in this database")

    tag = uuid.uuid4().hex[:10]
    org = Organization(name=f"nr-{tag}", slug=f"nr-{tag}")
    db.add(org)
    await db.flush()

    people = {}
    for role in ("parent_primary", "parent_delegate", "parent_member",
                 "override_primary", "override_delegate",
                 "consulted_primary", "admin", "assignee"):
        user = User(
            email=f"{role}-{tag}@example.invalid", google_sub=f"{role}-{tag}",
        )
        db.add(user)
        people[role] = user
    await db.flush()

    for user in people.values():
        db.add(OrganizationMember(
            organization_id=org.id, user_id=user.id,
            role="admin" if user is people["admin"] else "editor",
        ))
    await db.flush()

    teams = {}
    for label in ("parent", "override", "consulted"):
        team = Team(
            organization_id=org.id, function_id=function.id,
            name=f"{label}-{tag}",
        )
        db.add(team)
        teams[label] = team
    await db.flush()

    roster = (
        ("parent", "parent_primary", "primary"),
        ("parent", "parent_delegate", "delegate"),
        ("parent", "parent_member", "member"),
        ("override", "override_primary", "primary"),
        ("override", "override_delegate", "delegate"),
        ("consulted", "consulted_primary", "primary"),
    )
    for team_label, person, membership_role in roster:
        db.add(TeamMember(
            team_id=teams[team_label].id, organization_id=org.id,
            user_id=people[person].id, membership_role=membership_role,
        ))

    evidence = EvidenceTracking(organization_id=org.id, evidence_id=f"EV-{tag}")
    db.add(evidence)
    await db.flush()

    # The parent team is ACCOUNTABLE; the consulted team is assigned but not.
    db.add(EvidenceTeamAssignment(
        evidence_tracking_id=evidence.id, team_id=teams["parent"].id,
        organization_id=org.id, is_accountable=True,
    ))
    db.add(EvidenceTeamAssignment(
        evidence_tracking_id=evidence.id, team_id=teams["consulted"].id,
        organization_id=org.id, is_accountable=False,
    ))
    await db.flush()

    return _World(org=org, teams=teams, people=people, evidence=evidence)


async def _add_task(db, world, *, owning_team=None, assigned_user=None):
    task = EvidenceCollectionTask(
        evidence_tracking_id=world.evidence.id,
        organization_id=world.org.id,
        owning_team_id=owning_team.id if owning_team is not None else None,
        assigned_user_id=assigned_user.id if assigned_user is not None else None,
        task_type="collection",
        title="collect the thing",
        due_date=date.today() + timedelta(days=2),
        status="not_started",
    )
    db.add(task)
    await db.flush()
    return task


@requires_postgres
class TestATaskInheritsItsParentsTeam:
    """``owning_team_id IS NULL`` means inherit — the common case."""

    async def test_an_unassigned_task_reaches_the_parents_accountable_team(
        self, db, world
    ):
        """The whole point of phase 4, in one test.

        No assignee, no owning team. Before this feature the daily scheduler
        skipped this task forever. Now tier 1 is empty, tier 2 reaches through
        the parent evidence item, and the accountable team's primary and
        delegate are notified.

        Deliberately a *routine* event, so the set below is the tier's answer
        and nothing else. The escalating case is the test after this one --
        keeping them apart is what makes each set exact.
        """
        task = await _add_task(db, world)

        result = await resolve_recipients_for(db, world.task_item(task), "task_due")

        assert result.tier == OWNER_TIER_ACCOUNTABLE_TEAM
        assert result.user_ids == frozenset({
            world.people["parent_primary"].id,
            world.people["parent_delegate"].id,
        })

    async def test_the_same_task_overdue_also_reaches_the_consulted_team(
        self, db, world
    ):
        """The inherited tier is unchanged by escalation; the consulted team is
        added to it.

        This is the #822 escalation ruling end to end: the chain still answers
        `accountable team`, and the consulted primary joins additively rather
        than by relaxing the tier. Asserting the union *and* the tier together
        is what distinguishes "escalation added someone" from "the chain fell
        through to a wider tier", which would look identical on user_ids alone.
        """
        task = await _add_task(db, world)

        result = await resolve_recipients_for(db, world.task_item(task), "task_overdue")

        assert result.tier == OWNER_TIER_ACCOUNTABLE_TEAM
        assert result.user_ids == frozenset({
            world.people["parent_primary"].id,
            world.people["parent_delegate"].id,
            world.people["consulted_primary"].id,
        })
        assert world.people["consulted_primary"].id in result.escalated_user_ids

    async def test_the_parents_plain_members_are_not_reached(self, db, world):
        task = await _add_task(db, world)

        result = await resolve_recipients_for(db, world.task_item(task), "task_due")

        assert world.people["parent_member"].id not in result.user_ids

    async def test_the_parents_consulted_team_is_not_reached(self, db, world):
        """The consulted team is really assigned to this evidence item — it is
        a row in ``evidence_team_assignments`` — and is still not paged. That
        is what makes this test about ``is_accountable`` rather than about an
        empty table."""
        assignments = (await db.execute(
            select(EvidenceTeamAssignment).where(
                EvidenceTeamAssignment.evidence_tracking_id == world.evidence.id
            )
        )).scalars().all()
        assert len(assignments) == 2, "the consulted assignment must exist"

        task = await _add_task(db, world)
        result = await resolve_recipients_for(db, world.task_item(task), "task_due")

        assert world.people["consulted_primary"].id not in result.user_ids

    async def test_an_assigned_task_still_prefers_its_assignee(self, db, world):
        """Tier 1 first. Existing behaviour, unchanged."""
        task = await _add_task(db, world, assigned_user=world.people["assignee"])

        result = await resolve_recipients_for(db, world.task_item(task), "task_due")

        assert result.tier == OWNER_TIER_EXPLICIT
        assert result.user_ids == frozenset({world.people["assignee"].id})


@requires_postgres
class TestOwningTeamOverridesTheParent:
    """``owning_team_id`` set means override — the setup/collect/review split."""

    async def test_the_override_team_is_reached_instead_of_the_parents(
        self, db, world
    ):
        task = await _add_task(db, world, owning_team=world.teams["override"])

        # Routine event: the set is the override team and nobody else. On an
        # escalating event the parent's consulted team would join this set,
        # which would say nothing about whether the override worked.
        result = await resolve_recipients_for(db, world.task_item(task), "task_due")

        assert result.user_ids == frozenset({
            world.people["override_primary"].id,
            world.people["override_delegate"].id,
        })

    async def test_the_parents_team_is_not_also_reached(self, db, world):
        """Override, not union. Notifying both would make the column a way to
        add recipients rather than to redirect them."""
        task = await _add_task(db, world, owning_team=world.teams["override"])

        result = await resolve_recipients_for(db, world.task_item(task), "task_overdue")

        assert world.people["parent_primary"].id not in result.user_ids
        assert world.people["parent_delegate"].id not in result.user_ids

    async def test_two_tasks_on_one_evidence_item_reach_different_teams(
        self, db, world
    ):
        """The case the column exists for: engineering wires up the export,
        GRC signs it off, and they are notified about their own task."""
        setup = await _add_task(db, world, owning_team=world.teams["override"])
        review = await _add_task(db, world)

        setup_result = await resolve_recipients_for(
            db, world.task_item(setup), "task_due")
        review_result = await resolve_recipients_for(
            db, world.task_item(review), "task_due")

        assert setup_result.user_ids != review_result.user_ids
        assert setup_result.user_ids == frozenset({
            world.people["override_primary"].id,
            world.people["override_delegate"].id,
        })
        assert review_result.user_ids == frozenset({
            world.people["parent_primary"].id,
            world.people["parent_delegate"].id,
        })

    async def test_nothing_is_copied_down_onto_the_task(self, db, world):
        """Inheritance is resolved at read time, so parent and child cannot
        drift. Reassigning the evidence item's accountable team changes what
        an inheriting task resolves to, with no write to the task at all.
        """
        task = await _add_task(db, world)

        before = await resolve_recipients_for(db, world.task_item(task), "task_due")
        assert before.user_ids == frozenset({
            world.people["parent_primary"].id,
            world.people["parent_delegate"].id,
        })

        # Hand accountability to the other team. The task is untouched.
        for assignment in (await db.execute(
            select(EvidenceTeamAssignment).where(
                EvidenceTeamAssignment.evidence_tracking_id == world.evidence.id
            )
        )).scalars().all():
            assignment.is_accountable = False
        await db.flush()
        db.add(EvidenceTeamAssignment(
            evidence_tracking_id=world.evidence.id,
            team_id=world.teams["override"].id,
            organization_id=world.org.id, is_accountable=True,
        ))
        await db.flush()

        after = await resolve_recipients_for(db, world.task_item(task), "task_due")
        assert after.user_ids == frozenset({
            world.people["override_primary"].id,
            world.people["override_delegate"].id,
        })


@requires_postgres
class TestATaskInAnOrganisationWithNoTeams:
    """The upgrade-safety property, proved against real rows."""

    async def test_an_unassigned_task_falls_through_to_the_org_admins(
        self, db, world
    ):
        for assignment in (await db.execute(
            select(EvidenceTeamAssignment).where(
                EvidenceTeamAssignment.evidence_tracking_id == world.evidence.id
            )
        )).scalars().all():
            await db.delete(assignment)
        await db.flush()

        task = await _add_task(db, world)
        result = await resolve_recipients_for(db, world.task_item(task), "task_due")

        assert result.tier == OWNER_TIER_ORG_ADMIN
        assert result.user_ids == frozenset({world.people["admin"].id})

    async def test_an_assigned_task_reaches_exactly_its_assignee(self, db, world):
        for assignment in (await db.execute(
            select(EvidenceTeamAssignment).where(
                EvidenceTeamAssignment.evidence_tracking_id == world.evidence.id
            )
        )).scalars().all():
            await db.delete(assignment)
        await db.flush()

        task = await _add_task(db, world, assigned_user=world.people["assignee"])
        result = await resolve_recipients_for(db, world.task_item(task), "task_due")

        assert result.user_ids == frozenset({world.people["assignee"].id})
