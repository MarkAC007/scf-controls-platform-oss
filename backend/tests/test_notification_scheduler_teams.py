"""The daily schedulers, after #822 phase 4.

Four acceptance criteria are about the schedulers rather than about the
resolver, and each of them is a claim that only rows in a database can settle:

* ``if not task.assigned_user_id: continue`` is **gone from both schedulers**,
  and an unassigned task with an owning team **notifies that team**. This is a
  live production defect, not a hypothetical: an unassigned task was silently
  skipped by ``check_and_notify_due_tasks`` and ``check_and_notify_overdue_tasks``
  forever — no due warning, no overdue warning, no escalation. It is the
  highest-value test in this lane because the defect has **no symptom**: every
  screen renders correctly, nobody is told anything, and nothing looks wrong;
* the dedup key is ``type + reference_id + date``, **not per-user**, and the
  recipient rows are written in **one transaction**;
* **escalation fires on threshold crossings**, so five daily runs against a
  five-day-overdue task leave *one* notification, not five;
* an organisation with **no teams** receives **byte-for-byte** today's
  notifications. This is what protects every existing customer from a silent
  volume increase on upgrade, so the message text is asserted as a literal
  rather than as "some string";

plus bulk assignment, which must emit one aggregate notification per recipient
rather than one per item.

These tests **commit**. ``_emit`` commits the recipient set, which is the
behaviour under test, so a rollback-only fixture would test something else.
The ``world`` fixture therefore deletes its organisation and its users on the
way out — both cascade — and every test asserts on rows it can identify by its
own task or team id.

**They need PostgreSQL and SKIP in CI.** A green CI run is not evidence for
anything in this file.

Run with::

    docker compose exec -T backend python -m pytest \\
        tests/test_notification_scheduler_teams.py -v

Day-by-day escalation arithmetic (the +7 and +30 crossings) lives in
``test_notification_escalation_thresholds.py``, where ``created_at`` can be set
explicitly. Here the scheduler writes rows with the server's own clock, so what
is proved is the property that matters operationally: repeated runs do not
repeat the notification.
"""
from __future__ import annotations

import os
import pathlib
import re
import sys
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import catalog_models  # noqa: E402,F401
from models import (  # noqa: E402
    EvidenceCollectionTask,
    EvidenceTeamAssignment,
    EvidenceTracking,
    Function,
    Notification,
    Organization,
    OrganizationMember,
    ScopedControl,
    Team,
    TeamMember,
    User,
)
from services import notifications as notifications_module  # noqa: E402
from services.notifications import (  # noqa: E402
    check_and_notify_due_tasks,
    check_and_notify_overdue_tasks,
    create_bulk_team_assignment_notifications,
)

BACKEND = pathlib.Path(__file__).resolve().parents[1]
NOTIFICATIONS_SOURCE = BACKEND / "services" / "notifications.py"

DATABASE_URL = os.getenv("DATABASE_URL", "")

requires_postgres = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason=(
        "needs a Postgres DATABASE_URL — SKIPPED, not passed. Every claim in "
        "this file is about rows the scheduler wrote"
    ),
)


# ---------------------------------------------------------------------------
# The line that is gone. Runs everywhere.
# ---------------------------------------------------------------------------

class TestTheSkipIsGoneFromBothSchedulers:
    """``if not task.assigned_user_id: continue`` — ``notifications.py:296``
    and ``:372`` before this phase.

    A source assertion as well as the behavioural tests below, because the two
    fail differently and both are worth having. The behavioural test says "an
    unassigned task is notified"; this one says "and it is not notified by a
    special case bolted on beside the skip", which is what the criterion asks
    for — the resolution chain *replaces* the guard rather than sitting behind
    it.
    """

    @pytest.fixture(scope="class")
    def source(self):
        return NOTIFICATIONS_SOURCE.read_text()

    def test_the_source_file_is_the_one_we_think_it_is(self, source):
        """Closes the vacuous pass: a search over an empty or missing file
        finds no ``continue`` either."""
        assert "async def check_and_notify_due_tasks" in source
        assert "async def check_and_notify_overdue_tasks" in source

    @staticmethod
    def _code_of(source, scheduler):
        """The scheduler's executable body, with its docstring removed.

        Both docstrings quote the line that was deleted, in order to explain
        why — so a naive substring search over the whole function finds the
        very text whose absence is being asserted, and fails on the
        documentation rather than on the code.
        """
        body = source.split(f"async def {scheduler}")[1].split("\nasync def ")[0]
        parts = body.split('"""')
        assert len(parts) >= 3, f"{scheduler} has no docstring to strip"
        return '"""'.join(parts[2:])

    @pytest.mark.parametrize(
        "scheduler",
        ["check_and_notify_due_tasks", "check_and_notify_overdue_tasks"],
    )
    def test_neither_scheduler_skips_an_unassigned_task(self, source, scheduler):
        code = self._code_of(source, scheduler)
        assert "continue" in source, (
            "no `continue` anywhere in the file would make the assertion below "
            "pass against an empty read"
        )
        assert not re.search(
            r"if not task\.assigned_user_id\s*:", code
        ), f"{scheduler} still skips unassigned tasks"
        assert "assigned_user_id" not in code, (
            f"{scheduler} still reasons about the assignee directly; the "
            "resolution chain is meant to replace that, not sit behind it"
        )

    @pytest.mark.parametrize(
        "scheduler",
        ["check_and_notify_due_tasks", "check_and_notify_overdue_tasks"],
    )
    def test_both_schedulers_resolve_through_the_chain(self, source, scheduler):
        assert "resolve_recipients_for" in self._code_of(source, scheduler)

    def test_the_dedup_guard_is_not_keyed_on_a_user(self, source):
        """``type + reference_id + date``. A ``user_id`` in this predicate is
        the old key, and with a recipient set it re-notifies whoever already
        got the message after a partially-failed run."""
        guard = source.split("async def _already_notified")[1].split("\nasync def ")[0]
        assert "Notification.type ==" in guard
        assert "Notification.reference_id ==" in guard
        assert "Notification.created_at >=" in guard
        assert "user_id" not in guard.split('"""')[2]

    def test_the_recipient_rows_are_committed_once(self, source):
        """One commit for the whole set — all of them are notified or none
        are, which is what makes the event-level dedup key truthful."""
        emit = source.split("async def _emit")[1].split("\nasync def ")[0]
        assert emit.count("await db.commit()") == 1
        assert "await db.rollback()" in emit

    def test_the_overdue_scheduler_gates_on_the_threshold_not_on_a_date(
        self, source
    ):
        """The anti-storm property has exactly one owner.

        This is what stops the behavioural count test below from passing for
        the wrong reason: if the overdue site also passed ``dedup_on=today``,
        five same-day runs would produce one row whether or not the threshold
        gate worked at all.
        """
        code = self._code_of(source, "check_and_notify_overdue_tasks")
        assert "pending_escalation_threshold" in code
        assert "dedup_on" not in code
        # And the due scheduler does use it, so the assertion above is about
        # this site rather than about a keyword nobody passes anywhere.
        assert "dedup_on" in self._code_of(source, "check_and_notify_due_tasks")


# ---------------------------------------------------------------------------
# Behavioural. Commits. SKIPS in CI.
# ---------------------------------------------------------------------------

@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    try:
        async with engine.connect():
            pass
    except Exception as exc:  # pragma: no cover - environment dependent
        await engine.dispose()
        pytest.skip(f"database not reachable: {exc}")
    yield engine
    await engine.dispose()


class _World:
    def __init__(self, **kw):
        self.__dict__.update(kw)


@pytest.fixture
async def world(engine):
    """One organisation, committed, and torn down afterwards.

    Deleting the organisation cascades its members, teams, evidence items and
    tasks; deleting the users cascades their notifications. Nothing this
    fixture creates outlives the test.
    """
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    tag = uuid.uuid4().hex[:10]

    async with session_factory() as db:
        function = (await db.execute(
            select(Function).where(Function.is_active.is_(True)).limit(1)
        )).scalar_one_or_none()
        if function is None:  # pragma: no cover - environment dependent
            pytest.skip("no seeded functions in this database")

        org = Organization(name=f"sch-{tag}", slug=f"sch-{tag}")
        db.add(org)
        await db.flush()

        people = {}
        for role in ("primary", "delegate", "member", "admin", "assignee"):
            user = User(
                email=f"{role}-{tag}@example.invalid", google_sub=f"{role}-{tag}",
                # Email delivery is not what any of these tests are about, and
                # a scheduler that tries to send one reaches a mail provider.
                email_notifications_enabled=False,
                notification_frequency="daily",
            )
            db.add(user)
            people[role] = user
        await db.flush()

        for role, user in people.items():
            db.add(OrganizationMember(
                organization_id=org.id, user_id=user.id,
                role="admin" if role == "admin" else "editor",
            ))

        team = Team(
            organization_id=org.id, function_id=function.id, name=f"owners-{tag}",
        )
        db.add(team)
        await db.flush()
        for role in ("primary", "delegate", "member"):
            db.add(TeamMember(
                team_id=team.id, organization_id=org.id,
                user_id=people[role].id, membership_role=role,
            ))

        evidence = EvidenceTracking(
            organization_id=org.id, evidence_id=f"EV-{tag}",
        )
        db.add(evidence)
        await db.flush()
        db.add(EvidenceTeamAssignment(
            evidence_tracking_id=evidence.id, team_id=team.id,
            organization_id=org.id, is_accountable=True,
        ))

        control = ScopedControl(organization_id=org.id, scf_id=f"TST-{tag}")
        db.add(control)
        await db.commit()

        built = _World(
            org_id=org.id, team_id=team.id, team_name=team.name,
            evidence_id=evidence.id, evidence_key=evidence.evidence_id,
            control_id=control.id, tag=tag,
            people={role: user.id for role, user in people.items()},
            session_factory=session_factory,
        )

    yield built

    async with session_factory() as db:
        await db.execute(delete(Organization).where(Organization.id == built.org_id))
        await db.execute(
            delete(User).where(User.id.in_(list(built.people.values())))
        )
        await db.commit()


async def _add_task(world, *, due_in_days, assigned=None, owning_team=None,
                    task_type="collection"):
    async with world.session_factory() as db:
        task = EvidenceCollectionTask(
            evidence_tracking_id=world.evidence_id,
            organization_id=world.org_id,
            owning_team_id=owning_team,
            assigned_user_id=assigned,
            task_type=task_type,
            title="collect the thing",
            due_date=date.today() + timedelta(days=due_in_days),
            status="not_started",
        )
        db.add(task)
        await db.commit()
        return task.id


async def _drop_the_teams(world):
    """Turn the fixture's organisation into one that has never used teams."""
    async with world.session_factory() as db:
        await db.execute(delete(EvidenceTeamAssignment).where(
            EvidenceTeamAssignment.organization_id == world.org_id
        ))
        await db.execute(delete(TeamMember).where(
            TeamMember.organization_id == world.org_id
        ))
        await db.execute(delete(Team).where(Team.organization_id == world.org_id))
        await db.commit()


async def _notifications_for(world, reference_id, notification_type=None):
    async with world.session_factory() as db:
        stmt = select(Notification).where(Notification.reference_id == reference_id)
        if notification_type:
            stmt = stmt.where(Notification.type == notification_type)
        return list((await db.execute(stmt)).scalars().all())


async def _run(world, scheduler):
    """One scheduler run, on its own session, as the cron job does."""
    async with world.session_factory() as db:
        return await scheduler(db)


@requires_postgres
class TestAnUnassignedTaskIsNoLongerSilentlySkipped:
    """The live defect, and the reason this lane exists.

    An unassigned task produced no due warning, no overdue warning and no
    escalation, for its entire life. Nothing on any screen said so.
    """

    async def test_an_unassigned_task_with_an_owning_team_notifies_that_team(
        self, world
    ):
        task_id = await _add_task(
            world, due_in_days=1, assigned=None, owning_team=world.team_id,
        )

        await _run(world, check_and_notify_due_tasks)

        recipients = {n.user_id for n in await _notifications_for(world, task_id)}
        assert recipients == {world.people["primary"], world.people["delegate"]}, (
            "an unassigned task with an owning team must reach that team"
        )

    async def test_the_plain_member_of_the_owning_team_is_not_notified(self, world):
        task_id = await _add_task(
            world, due_in_days=1, assigned=None, owning_team=world.team_id,
        )

        await _run(world, check_and_notify_due_tasks)

        recipients = {n.user_id for n in await _notifications_for(world, task_id)}
        assert world.people["member"] not in recipients

    async def test_an_unassigned_task_inheriting_from_its_evidence_item_notifies(
        self, world
    ):
        """No assignee **and** no owning team — the most common shape there
        is, and the one that was most completely invisible. It reaches the
        evidence item's accountable team through inheritance."""
        task_id = await _add_task(
            world, due_in_days=2, assigned=None, owning_team=None,
        )

        await _run(world, check_and_notify_due_tasks)

        recipients = {n.user_id for n in await _notifications_for(world, task_id)}
        assert recipients == {world.people["primary"], world.people["delegate"]}

    async def test_an_unassigned_overdue_task_notifies_too(self, world):
        """The second scheduler. Both carried the same skip."""
        task_id = await _add_task(
            world, due_in_days=-2, assigned=None, owning_team=None,
        )

        await _run(world, check_and_notify_overdue_tasks)

        rows = await _notifications_for(world, task_id, "task_overdue")
        assert {n.user_id for n in rows} == {
            world.people["primary"], world.people["delegate"],
        }

    async def test_an_unassigned_task_in_a_teamless_org_reaches_the_admins(
        self, world
    ):
        """Tier 3, which is today's last-resort behaviour, preserved."""
        await _drop_the_teams(world)
        task_id = await _add_task(world, due_in_days=1, assigned=None)

        await _run(world, check_and_notify_due_tasks)

        rows = await _notifications_for(world, task_id)
        assert {n.user_id for n in rows} == {world.people["admin"]}


@requires_postgres
class TestDedupIsPerEventNotPerUser:
    async def test_a_second_run_on_the_same_day_writes_nothing(self, world):
        task_id = await _add_task(
            world, due_in_days=1, assigned=None, owning_team=world.team_id,
        )

        first = await _run(world, check_and_notify_due_tasks)
        second = await _run(world, check_and_notify_due_tasks)

        assert first == 2, "the team's primary and delegate"
        assert second == 0, "the event has already been announced today"
        assert len(await _notifications_for(world, task_id)) == 2

    async def test_a_row_for_one_recipient_suppresses_the_whole_event(self, world):
        """The per-user key would not.

        One recipient's row is planted by hand; the scheduler then runs for a
        task whose team has two. Under the old ``user_id``-leading key the
        delegate has no history and would be notified, producing a third row.
        Under the event key the event is already announced and nothing is
        written.
        """
        task_id = await _add_task(
            world, due_in_days=1, assigned=None, owning_team=world.team_id,
        )
        async with world.session_factory() as db:
            db.add(Notification(
                user_id=world.people["primary"], type="task_due",
                reference_type="task", reference_id=task_id,
                message="planted by the test",
            ))
            await db.commit()

        written = await _run(world, check_and_notify_due_tasks)

        assert written == 0
        rows = await _notifications_for(world, task_id)
        assert len(rows) == 1
        assert world.people["delegate"] not in {n.user_id for n in rows}

    async def test_two_different_tasks_do_not_share_a_dedup_budget(self, world):
        """Keyed on ``reference_id``. Without it the first task in a run would
        silence every other task in the deployment for the day, and the test
        above would still pass."""
        first = await _add_task(
            world, due_in_days=1, assigned=None, owning_team=world.team_id)
        second = await _add_task(
            world, due_in_days=2, assigned=None, owning_team=world.team_id)

        await _run(world, check_and_notify_due_tasks)

        assert len(await _notifications_for(world, first)) == 2
        assert len(await _notifications_for(world, second)) == 2

    async def test_the_whole_recipient_set_is_written_or_none_of_it_is(
        self, world, monkeypatch
    ):
        """Atomicity, provoked rather than asserted from the source.

        The second ``Notification(...)`` in the set raises. If the rows were
        committed one at a time, the first recipient's row would survive and
        the event would be recorded as announced while half the team was never
        told — the exact state the event-level dedup key would then make
        permanent.
        """
        task_id = await _add_task(
            world, due_in_days=1, assigned=None, owning_team=world.team_id,
        )

        # A proxy rather than a plain function: the module also uses
        # ``Notification`` as a mapped class — ``select(Notification.id)`` in
        # the dedup guard — so attribute access has to keep working and only
        # *construction* may explode.
        class _ExplodesOnTheSecondRow:
            def __init__(self, real):
                self._real = real
                self.calls = 0

            def __getattr__(self, name):
                return getattr(self._real, name)

            def __call__(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 2:
                    raise RuntimeError("boom, half way through the set")
                return self._real(*args, **kwargs)

        proxy = _ExplodesOnTheSecondRow(notifications_module.Notification)
        monkeypatch.setattr(notifications_module, "Notification", proxy)
        written = await _run(world, check_and_notify_due_tasks)
        monkeypatch.undo()

        assert proxy.calls == 2, "the fixture must have produced a set of two"
        assert written == 0
        assert await _notifications_for(world, task_id) == []


@requires_postgres
class TestEscalationDoesNotRepeat:
    async def test_five_runs_against_an_overdue_task_leave_one_notification(
        self, world
    ):
        """The criterion, counted.

        Five scheduler runs, a task five days overdue, one notification per
        recipient. The old behaviour wrote one per run — and note that the
        overdue site passes no ``dedup_on``, asserted separately above, so the
        only thing suppressing runs two to five is the threshold gate.
        """
        task_id = await _add_task(
            world, due_in_days=-5, assigned=None, owning_team=world.team_id,
        )

        for _ in range(5):
            await _run(world, check_and_notify_overdue_tasks)

        rows = await _notifications_for(world, task_id, "task_overdue")
        assert len({n.user_id for n in rows}) == 2, "primary and delegate"
        assert len(rows) == 2, (
            f"one escalation for the whole 5 days, not one per run; got "
            f"{[n.message for n in rows]}"
        )

    async def test_escalation_reaches_the_team_on_top_of_a_named_assignee(
        self, world
    ):
        """Additive, not a tier. A stalled task reaches the owning team even
        when an individual is assigned — which is the whole point of
        escalating rather than merely notifying."""
        task_id = await _add_task(
            world, due_in_days=-1,
            assigned=world.people["assignee"], owning_team=world.team_id,
        )

        await _run(world, check_and_notify_overdue_tasks)

        recipients = {
            n.user_id for n in await _notifications_for(world, task_id, "task_overdue")
        }
        assert recipients == {
            world.people["assignee"],
            world.people["primary"],
            world.people["delegate"],
        }

    async def test_a_task_that_is_not_yet_overdue_escalates_to_nobody(self, world):
        task_id = await _add_task(
            world, due_in_days=0, assigned=None, owning_team=world.team_id,
        )

        await _run(world, check_and_notify_overdue_tasks)

        assert await _notifications_for(world, task_id, "task_overdue") == []


@requires_postgres
class TestAnOrganisationWithNoTeamsSeesTodaysBehaviour:
    """Byte-for-byte. The regression test that protects every existing
    customer from a silent volume increase on upgrade.

    The message strings below are the pre-#822 literals, written out rather
    than derived from the code under test — a test that builds its expectation
    from the same function it is checking proves only that the function is
    consistent with itself.
    """

    async def test_one_assignee_gets_exactly_one_due_notification(self, world):
        await _drop_the_teams(world)
        task_id = await _add_task(
            world, due_in_days=2, assigned=world.people["assignee"],
        )

        written = await _run(world, check_and_notify_due_tasks)

        rows = await _notifications_for(world, task_id)
        assert written == 1
        assert len(rows) == 1
        assert rows[0].user_id == world.people["assignee"]

    async def test_the_due_message_is_unchanged(self, world):
        await _drop_the_teams(world)
        task_id = await _add_task(
            world, due_in_days=2, assigned=world.people["assignee"],
        )

        await _run(world, check_and_notify_due_tasks)

        row = (await _notifications_for(world, task_id))[0]
        assert row.message == (
            f"Evidence collection task for {world.evidence_key} "
            f"is due in 2 day(s)"
        )
        assert row.type == "task_due"
        assert row.reference_type == "task"
        assert row.reference_id == task_id

    async def test_the_due_today_wording_is_unchanged(self, world):
        await _drop_the_teams(world)
        task_id = await _add_task(
            world, due_in_days=0, assigned=world.people["assignee"],
        )

        await _run(world, check_and_notify_due_tasks)

        row = (await _notifications_for(world, task_id))[0]
        assert row.message == (
            f"Evidence collection task for {world.evidence_key} is due today!"
        )

    async def test_the_overdue_message_is_unchanged(self, world):
        await _drop_the_teams(world)
        task_id = await _add_task(
            world, due_in_days=-3, assigned=world.people["assignee"],
        )

        await _run(world, check_and_notify_overdue_tasks)

        rows = await _notifications_for(world, task_id, "task_overdue")
        assert len(rows) == 1
        assert rows[0].message == (
            f"Evidence collection task for {world.evidence_key} "
            f"is overdue by 3 day(s)"
        )

    async def test_nobody_beyond_the_assignee_is_reached(self, world):
        """The volume claim, stated as a count over the whole organisation.

        Five people are org members. Before teams, one notification was
        written. After teams — in an org that has none — one notification is
        written.
        """
        await _drop_the_teams(world)
        task_id = await _add_task(
            world, due_in_days=1, assigned=world.people["assignee"],
        )

        await _run(world, check_and_notify_due_tasks)

        async with world.session_factory() as db:
            total = (await db.execute(
                select(func.count()).select_from(Notification)
                .where(Notification.reference_id == task_id)
            )).scalar_one()
        assert total == 1


@requires_postgres
class TestBulkAssignmentAggregates:
    """One notification per recipient, never one per item.

    #800 shipped bulk evidence actions, so assigning fifty controls to a team
    is a live path. The naive implementation produces a hundred notifications
    and a team that mutes the platform.
    """

    async def test_twelve_items_produce_one_notification_per_recipient(self, world):
        item_ids = [uuid.uuid4() for _ in range(12)]

        async with world.session_factory() as db:
            written = await create_bulk_team_assignment_notifications(
                db, organization_id=world.org_id, team_id=world.team_id,
                team_name=world.team_name, item_type="control", item_ids=item_ids,
            )

        rows = await _notifications_for(world, world.team_id)
        assert written == 2, "the primary and the delegate, once each"
        assert len(rows) == 2, f"one per recipient, not one per item; got {len(rows)}"
        assert {n.user_id for n in rows} == {
            world.people["primary"], world.people["delegate"],
        }

    async def test_the_message_counts_the_items(self, world):
        item_ids = [uuid.uuid4() for _ in range(12)]

        async with world.session_factory() as db:
            await create_bulk_team_assignment_notifications(
                db, organization_id=world.org_id, team_id=world.team_id,
                team_name=world.team_name, item_type="control", item_ids=item_ids,
            )

        messages = {n.message for n in await _notifications_for(world, world.team_id)}
        assert messages == {f"12 controls assigned to {world.team_name}"}

    async def test_a_single_item_is_not_pluralised(self, world):
        async with world.session_factory() as db:
            await create_bulk_team_assignment_notifications(
                db, organization_id=world.org_id, team_id=world.team_id,
                team_name=world.team_name, item_type="control",
                item_ids=[uuid.uuid4()],
            )

        messages = {n.message for n in await _notifications_for(world, world.team_id)}
        assert messages == {f"1 control assigned to {world.team_name}"}

    async def test_the_reference_is_the_team_not_an_arbitrary_item(self, world):
        """A deep link to one member of a set of fifty is a link to the wrong
        place."""
        item_ids = [uuid.uuid4() for _ in range(5)]

        async with world.session_factory() as db:
            await create_bulk_team_assignment_notifications(
                db, organization_id=world.org_id, team_id=world.team_id,
                team_name=world.team_name, item_type="evidence", item_ids=item_ids,
            )

        rows = await _notifications_for(world, world.team_id)
        assert rows, "nothing was written"
        for row in rows:
            assert row.reference_type == "team"
            assert row.reference_id == world.team_id
            assert row.reference_id not in item_ids

    async def test_the_actor_is_not_notified_of_their_own_action(self, world):
        item_ids = [uuid.uuid4() for _ in range(3)]

        async with world.session_factory() as db:
            written = await create_bulk_team_assignment_notifications(
                db, organization_id=world.org_id, team_id=world.team_id,
                team_name=world.team_name, item_type="control", item_ids=item_ids,
                actor_user_id=world.people["primary"],
            )

        rows = await _notifications_for(world, world.team_id)
        assert written == 1
        assert {n.user_id for n in rows} == {world.people["delegate"]}

    async def test_an_empty_assignment_notifies_nobody(self, world):
        async with world.session_factory() as db:
            written = await create_bulk_team_assignment_notifications(
                db, organization_id=world.org_id, team_id=world.team_id,
                team_name=world.team_name, item_type="control", item_ids=[],
            )

        assert written == 0
        assert await _notifications_for(world, world.team_id) == []

    async def test_the_plain_member_is_not_notified(self, world):
        """Consistent with the routine path: primary and delegate, not the
        roster."""
        async with world.session_factory() as db:
            await create_bulk_team_assignment_notifications(
                db, organization_id=world.org_id, team_id=world.team_id,
                team_name=world.team_name, item_type="control",
                item_ids=[uuid.uuid4()],
            )

        recipients = {
            n.user_id for n in await _notifications_for(world, world.team_id)
        }
        assert world.people["member"] not in recipients
