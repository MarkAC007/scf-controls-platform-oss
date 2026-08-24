"""The queue filter and the recipient rule are the same rule (#822 phase 4).

Invariant 7 of #822: **the read path ships with the write path.** A column
written and never read is the exact defect the issue exists to fix — evidence
assignment already shipped a column no queue query consumed, and the queue sat
permanently empty, which reads as "no work" rather than "broken".

So the rule that decides who gets *notified* about a task has to be the rule
that decides whose *queue* it lands in. It exists twice by necessity, because a
work queue cannot fetch every task in the organisation and resolve them one at
a time in Python:

* :func:`services.owner_resolution.resolve_recipients_for` — the notification
  path, resolving one item;
* :func:`services.responsibility.my_task_filter` — a WHERE clause, resolving
  every item at once.

Two implementations of one rule is a standing risk. **This file is the reason
it is safe to have two**: :class:`TestTheTwoImplementationsAgree` runs both
against the same rows in the same database and asserts they return the same
answer, task by task and user by user. A divergence introduced by editing one
and not the other fails here.

The fixtures are deliberately the awkward cases rather than the happy path — a
task that names a person *and* a team, a task whose parent has no accountable
team at all, a team member who is neither primary nor delegate — because the
happy path agrees under almost any implementation and proves correspondingly
little.

**Needs PostgreSQL.** These SKIP without a reachable ``DATABASE_URL``, and a
run whose summary says "skipped" has proved nothing. Run them against the dev
stack with::

    docker compose exec -T backend python -m pytest \\
        tests/test_responsibility_filters.py -v
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

# models.System declares a relationship by name against a class that lives
# here; without this import mapper configuration fails on the first query.
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
    OWNER_TIER_ACCOUNTABLE_TEAM,
    OWNER_TIER_EXPLICIT,
    Item,
    resolve_recipients_for,
)
from services.responsibility import my_task_filter  # noqa: E402

DATABASE_URL = os.getenv("DATABASE_URL", "")

requires_postgres = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason=(
        "needs a Postgres DATABASE_URL — these are SKIPPED, not passed, and "
        "they are the only tests that prove the two implementations agree"
    ),
)


@pytest.fixture
async def db():
    """A session on a transaction that is always rolled back.

    Nothing is committed, so a run leaves the dev database exactly as it found
    it. Same shape as ``test_control_evidence_team_assignment_constraints.py``.
    """
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
    """One organisation, two teams, five people and six tasks.

    Attribute names are the roles, not the identifiers, because every
    assertion below reads as a sentence about who can see what.
    """

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    @property
    def people(self) -> dict:
        return {
            "alice": self.alice,
            "primary_one": self.primary_one,
            "delegate_one": self.delegate_one,
            "member_one": self.member_one,
            "primary_two": self.primary_two,
            "admin": self.admin,
        }

    @property
    def tasks(self) -> dict:
        return {
            "assigned": self.task_assigned,
            "own_team": self.task_own_team,
            "inheriting": self.task_inheriting,
            "assigned_and_team": self.task_assigned_and_team,
            "orphan": self.task_orphan,
            "archived_team": self.task_archived_team,
        }


async def _user(db, org_id, tag, *, role="member"):
    user = User(
        email=f"resp-{tag}@example.invalid", google_sub=f"resp-sub-{tag}",
    )
    db.add(user)
    await db.flush()
    db.add(OrganizationMember(organization_id=org_id, user_id=user.id, role=role))
    await db.flush()
    return user


async def _task(db, *, org_id, evidence_id, assignee=None, team_id=None):
    task = EvidenceCollectionTask(
        organization_id=org_id,
        evidence_tracking_id=evidence_id,
        assigned_user_id=assignee.id if assignee is not None else None,
        owning_team_id=team_id,
        # NOT NULL on this table, and irrelevant to every assertion here.
        due_date=date.today() + timedelta(days=7),
        status="not_started",
    )
    db.add(task)
    await db.flush()
    return task


@pytest.fixture
async def world(db):
    function = (await db.execute(
        select(Function).where(Function.is_active.is_(True)).limit(1)
    )).scalar_one_or_none()
    if function is None:  # pragma: no cover - environment dependent
        pytest.skip("no seeded functions in this database")

    tag = uuid.uuid4().hex[:10]
    org = Organization(name=f"resp-{tag}", slug=f"resp-{tag}")
    db.add(org)
    await db.flush()

    team_one, team_two, archived = (
        Team(organization_id=org.id, function_id=function.id, name=f"{name}-{tag}")
        for name in ("team-one", "team-two", "team-archived")
    )
    archived.is_active = False
    db.add_all([team_one, team_two, archived])
    await db.flush()

    alice = await _user(db, org.id, f"alice-{tag}")
    primary_one = await _user(db, org.id, f"p1-{tag}")
    delegate_one = await _user(db, org.id, f"d1-{tag}")
    # On the accountable team, and deliberately NOT primary or delegate.
    member_one = await _user(db, org.id, f"m1-{tag}")
    primary_two = await _user(db, org.id, f"p2-{tag}")
    admin = await _user(db, org.id, f"admin-{tag}", role="admin")

    db.add_all([
        TeamMember(organization_id=org.id, team_id=team_one.id,
                   user_id=primary_one.id, membership_role="primary"),
        TeamMember(organization_id=org.id, team_id=team_one.id,
                   user_id=delegate_one.id, membership_role="delegate"),
        TeamMember(organization_id=org.id, team_id=team_one.id,
                   user_id=member_one.id, membership_role="member"),
        TeamMember(organization_id=org.id, team_id=team_two.id,
                   user_id=primary_two.id, membership_role="primary"),
        TeamMember(organization_id=org.id, team_id=archived.id,
                   user_id=primary_two.id, membership_role="delegate"),
    ])

    # The evidence item team one is accountable for, and one nobody owns.
    owned = EvidenceTracking(organization_id=org.id, evidence_id=f"owned-{tag}")
    orphaned = EvidenceTracking(organization_id=org.id, evidence_id=f"orphan-{tag}")
    db.add_all([owned, orphaned])
    await db.flush()

    db.add_all([
        EvidenceTeamAssignment(
            evidence_tracking_id=owned.id, team_id=team_one.id,
            organization_id=org.id, is_accountable=True,
            assigned_by_user_id=admin.id,
        ),
        # Consulted, not accountable. Must reach nobody's queue.
        EvidenceTeamAssignment(
            evidence_tracking_id=owned.id, team_id=team_two.id,
            organization_id=org.id, is_accountable=False,
            assigned_by_user_id=admin.id,
        ),
    ])
    await db.flush()

    return _World(
        org=org, function=function,
        team_one=team_one, team_two=team_two, archived_team=archived,
        alice=alice, primary_one=primary_one, delegate_one=delegate_one,
        member_one=member_one, primary_two=primary_two, admin=admin,
        owned=owned, orphaned=orphaned,
        task_assigned=await _task(
            db, org_id=org.id, evidence_id=owned.id, assignee=alice,
        ),
        task_own_team=await _task(
            db, org_id=org.id, evidence_id=owned.id, team_id=team_two.id,
        ),
        task_inheriting=await _task(
            db, org_id=org.id, evidence_id=owned.id,
        ),
        task_assigned_and_team=await _task(
            db, org_id=org.id, evidence_id=owned.id,
            assignee=alice, team_id=team_two.id,
        ),
        task_orphan=await _task(
            db, org_id=org.id, evidence_id=orphaned.id,
        ),
        task_archived_team=await _task(
            db, org_id=org.id, evidence_id=owned.id, team_id=archived.id,
        ),
    )


async def _queue(db, world, user) -> set:
    """The task names ``user``'s work queue shows, by the SQL filter."""
    rows = (await db.execute(
        select(EvidenceCollectionTask.id).where(
            EvidenceCollectionTask.organization_id == world.org.id,
            my_task_filter(user.id),
        )
    )).scalars().all()
    by_id = {task.id: name for name, task in world.tasks.items()}
    return {by_id[task_id] for task_id in rows}


def _task_item(task) -> Item:
    return Item(
        item_type="task",
        item_id=task.id,
        organization_id=task.organization_id,
        explicit_user_ids=(task.assigned_user_id,),
        owning_team_id=task.owning_team_id,
        parent=Item(
            item_type="evidence",
            item_id=task.evidence_tracking_id,
            organization_id=task.organization_id,
        ),
    )


async def _resolved(db, world, task) -> tuple:
    """Who the notification path resolves ``task`` to, and at which tier."""
    resolution = await resolve_recipients_for(
        db,
        Item(
            item_type="task",
            item_id=task.id,
            organization_id=task.organization_id,
            explicit_user_ids=(task.assigned_user_id,),
            owning_team_id=task.owning_team_id,
            parent=Item(
                item_type="evidence",
                item_id=task.evidence_tracking_id,
                organization_id=task.organization_id,
            ),
        ),
        "task_due",
    )
    return resolution.user_ids, resolution.tier


# ---------------------------------------------------------------------------
# The queue, read through the SQL filter
# ---------------------------------------------------------------------------

@requires_postgres
class TestWhoSeesWhat:

    async def test_an_assignee_sees_the_task_assigned_to_them(self, db, world):
        assert await _queue(db, world, world.alice) == {
            "assigned", "assigned_and_team",
        }

    async def test_a_team_primary_sees_an_unassigned_task_owned_by_their_team(
        self, db, world
    ):
        """``own_team`` names team two directly; ``archived_team`` names the
        archived team, which primary two is also on. Archiving a team stops it
        collecting *new* work — it does not hide work it already holds, or an
        org tidying up its structure would lose sight of it."""
        assert await _queue(db, world, world.primary_two) == {
            "own_team", "archived_team",
        }

    async def test_the_accountable_teams_primary_sees_the_inheriting_task(
        self, db, world
    ):
        """Nothing was copied onto the task. It resolves through its parent."""
        assert await _queue(db, world, world.primary_one) == {"inheriting"}

    async def test_the_delegate_sees_exactly_what_the_primary_sees(self, db, world):
        """Primary and delegate together, not delegate-on-escalation. A primary
        on annual leave is otherwise a silent single point of failure."""
        assert await _queue(db, world, world.delegate_one) == \
            await _queue(db, world, world.primary_one)

    async def test_a_plain_team_member_sees_nothing(self, db, world):
        """Being on the accountable team is not being accountable for it."""
        assert await _queue(db, world, world.member_one) == set()

    async def test_an_org_admin_sees_nothing_they_do_not_own(self, db, world):
        """Tier 3 is deliberately absent from queues.

        Falling through to "every administrator" would put every unowned task
        in the organisation on the queue of everyone able to fix that, which is
        not a queue — it is the unfiltered list with extra steps. Tier 3
        remains the last resort for *notifying* somebody that an item has no
        owner.
        """
        assert await _queue(db, world, world.admin) == set()

    async def test_nobody_sees_the_orphan(self, db, world):
        """A task whose parent has no accountable team is on no queue at all.

        It is not lost: the notification path still reaches the org's admins,
        which is how somebody learns it needs an owner.
        """
        for person in world.people.values():
            assert "orphan" not in await _queue(db, world, person)


@requires_postgres
class TestTierOneStopsTheChain:

    async def test_a_task_that_names_a_person_is_not_on_the_teams_queue(
        self, db, world
    ):
        """``assigned_and_team`` names alice *and* team two. Alice has it.

        Without this, marking a team accountable would silently add every one
        of its items to two more people's queues — the volume increase #822
        forbids, arriving through the read path instead of the write path.
        """
        assert "assigned_and_team" in await _queue(db, world, world.alice)
        assert "assigned_and_team" not in await _queue(db, world, world.primary_two)

    async def test_clearing_the_assignee_hands_the_task_to_the_team(self, db, world):
        """The durability tier 2 exists for.

        ``assigned_user_id`` is ``ON DELETE SET NULL``, so this is exactly what
        happens the day an assignee leaves: the task does not vanish from
        everyone's queue, it falls to the team.
        """
        world.task_assigned_and_team.assigned_user_id = None
        await db.flush()

        assert "assigned_and_team" not in await _queue(db, world, world.alice)
        assert "assigned_and_team" in await _queue(db, world, world.primary_two)


@requires_postgres
class TestConsultedTeamsAreNotOnTheHook:

    async def test_a_consulted_teams_primary_gets_no_inherited_work(self, db, world):
        """Team two is consulted on the owned evidence item. Its primary sees
        ``own_team`` — which names it directly — and not ``inheriting``."""
        assert "inheriting" not in await _queue(db, world, world.primary_two)


# ---------------------------------------------------------------------------
# The point of the file
# ---------------------------------------------------------------------------

@requires_postgres
class TestTheTwoImplementationsAgree:
    """The SQL filter and the Python resolver, on the same rows.

    The correspondence is exact for tiers 1 and 2, which are the only tiers a
    queue uses: a task is on ``user``'s queue **iff** the notification path
    resolves that task to ``user`` at the explicit or accountable-team tier.
    Tier 3 is where they part company, and they part company on purpose — see
    :func:`services.responsibility.my_task_filter`.
    """

    async def test_every_task_and_every_person_agree(self, db, world):
        queues = {
            name: await _queue(db, world, person)
            for name, person in world.people.items()
        }

        disagreements = []
        for task_name, task in world.tasks.items():
            recipients, tier = await _resolved(db, world, task)
            queue_tiers = (OWNER_TIER_EXPLICIT, OWNER_TIER_ACCOUNTABLE_TEAM)
            for person_name, person in world.people.items():
                notified = person.id in recipients and tier in queue_tiers
                queued = task_name in queues[person_name]
                if notified != queued:
                    disagreements.append(
                        f"{task_name}/{person_name}: "
                        f"notified={notified} (tier={tier}) queued={queued}"
                    )

        assert not disagreements, (
            "the queue filter and the recipient rule disagree:\n  "
            + "\n  ".join(disagreements)
        )

    async def test_the_orphan_resolves_to_admins_and_to_no_queue(self, db, world):
        """The one case where the two are meant to differ, asserted so that
        the agreement test above is not passing by both sides being empty."""
        recipients, tier = await _resolved(db, world, world.task_orphan)

        assert world.admin.id in recipients
        assert tier == "organization_admin"
        assert "orphan" not in await _queue(db, world, world.admin)


@requires_postgres
class TestConsultedTeamsReachTheEscalationQuery:
    """The consulted half of the escalation set, through real SQL.

    ``tests/test_notification_recipients.py`` pins the *rule* — escalation
    recipients are ``resolved ∪ accountable{p,d} ∪ consulted{p,d}`` — by
    handing the resolver both rosters directly. That proves the arithmetic and
    nothing about the query, and the query is the half that can silently return
    nothing: ``_consulted_teams_stmt`` has to find a task's consulted teams
    through its **parent evidence item**, because a task carries no assignments
    of its own by design.

    The fixture is already shaped for it: team one is accountable on the owned
    evidence item and team two is consulted on the same item.
    """

    async def test_an_escalation_reaches_the_consulted_teams_primary(
        self, db, world
    ):
        recipients, _ = await _resolved_for(
            db, world.task_inheriting, "task_overdue",
        )

        assert world.primary_one.id in recipients, "accountable team"
        assert world.primary_two.id in recipients, "consulted team"

    async def test_the_same_task_on_a_routine_event_does_not(self, db, world):
        """The identical row, one event key different. This is the assertion
        that would fail if the consulted query were wired into tier 2."""
        recipients, _ = await _resolved_for(
            db, world.task_inheriting, "task_due",
        )

        assert world.primary_one.id in recipients
        assert world.primary_two.id not in recipients

    async def test_a_plain_member_of_neither_team_is_reached(self, db, world):
        """``member_one`` is on the accountable team as a plain member. The
        role filter applies to the escalation exactly as to the routine path.
        """
        recipients, _ = await _resolved_for(
            db, world.task_inheriting, "task_overdue",
        )

        assert world.member_one.id not in recipients

    async def test_an_escalation_on_an_item_with_no_consulted_team_adds_nobody(
        self, db, world
    ):
        """The orphan's parent has no assignments at all. The extra query
        returns empty and the escalation is a no-op, rather than an error."""
        recipients, tier = await _resolved_for(
            db, world.task_orphan, "task_overdue",
        )

        assert recipients == frozenset({world.admin.id})
        assert tier == "organization_admin"


async def _resolved_for(db, task, event_key) -> tuple:
    resolution = await resolve_recipients_for(db, _task_item(task), event_key)
    return resolution.user_ids, resolution.tier
