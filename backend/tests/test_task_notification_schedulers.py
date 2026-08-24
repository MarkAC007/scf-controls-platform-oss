"""The two daily schedulers, end to end against a scripted session (#822 phase 4).

``tests/test_notification_recipients.py`` pins the recipient rule and
``tests/test_notification_escalation.py`` pins the threshold arithmetic. Both
are pure. This file is the one that runs the real
:func:`~services.notifications.check_and_notify_due_tasks` and
:func:`~services.notifications.check_and_notify_overdue_tasks` and asserts on
what they actually wrote, because the two defects #822 names are properties of
those functions rather than of the rules they call:

* ``if not task.assigned_user_id: continue`` — an unassigned task was silently
  skipped by this scheduler forever. No due warning, no overdue warning, no
  escalation, for the life of the task. This was live in production.
* escalation on **state** rather than on a crossing — a task that was overdue
  escalated again every single day.

The session below is scripted rather than mocked: it dispatches on the SQL each
statement compiles to and answers from an in-memory store, and rows written
through ``add`` become visible to later reads only after ``commit``. That is
what lets :class:`TestEscalationFiresOnCrossingsNotState` run thirty simulated
days through the real function and count what came out, which no amount of
``assert_called_once`` would establish.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Any, List, Optional

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Imported for its side effect: ``models`` declares relationships by name
# against classes that live here, and SQLAlchemy cannot configure its
# mappers until both modules have been imported.
import catalog_models  # noqa: E402,F401
from models import Notification  # noqa: E402
import services.notifications as notifications  # noqa: E402


# ---------------------------------------------------------------------------
# A scripted async session
# ---------------------------------------------------------------------------

class _Result:
    def __init__(self, rows: List[Any]):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def limit(self, _n):
        return self

    def scalars(self):
        # Every scalar-shaped read here selects a single column, so the row is
        # already the value.
        return _Result(self._rows)


class World:
    """The fixture data, and the session that serves it.

    One object rather than a fixture soup because the tests are about the
    relationship between what is in the world and what gets written — an
    unassigned task, a team with a primary, and nothing else.
    """

    def __init__(
        self,
        *,
        tasks: List[Any] = (),
        team_roster: List[Any] = (),
        org_admins: List[uuid.UUID] = (),
        users: List[Any] = None,
    ):
        self.tasks = list(tasks)
        self.team_roster = list(team_roster)
        self.org_admins = list(org_admins)
        self.notifications: List[Notification] = []
        self._pending: List[Notification] = []
        self.commits = 0
        self.rollbacks = 0
        self.today = date.today()
        #: Every user id anyone might resolve to, as User-shaped rows. Email is
        #: off by default so the tests exercise notification writes without
        #: reaching the mail service.
        self._users = {u.id: u for u in (users or [])}

    # -- fixture helpers ---------------------------------------------------

    def user(self, *, email: bool = False):
        row = SimpleNamespace(
            id=uuid.uuid4(),
            email=f"{uuid.uuid4().hex[:8]}@example.com",
            display_name="Someone",
            email_notifications_enabled=email,
            notification_frequency='immediate' if email else 'daily_digest',
        )
        self._users[row.id] = row
        return row.id

    def task(
        self,
        *,
        due_date: date,
        assigned_user_id: Optional[uuid.UUID] = None,
        owning_team_id: Optional[uuid.UUID] = None,
        organization_id: Optional[uuid.UUID] = None,
    ):
        task = SimpleNamespace(
            id=uuid.uuid4(),
            evidence_tracking_id=uuid.uuid4(),
            organization_id=organization_id or uuid.uuid4(),
            assigned_user_id=assigned_user_id,
            owning_team_id=owning_team_id,
            due_date=due_date,
            status='not_started',
        )
        evidence = SimpleNamespace(
            id=task.evidence_tracking_id, evidence_id="EVIDENCE-ONE",
        )
        self.tasks.append((task, evidence))
        return task

    # -- the session -------------------------------------------------------

    async def execute(self, stmt):
        sql = str(stmt)

        if sql.startswith("SELECT evidence_collection_tasks"):
            return _Result(self.tasks)

        if sql.startswith("SELECT notifications.created_at"):
            # The escalation read. Filtered the way the real predicate is:
            # this type, this reference, on or after the due date.
            params = stmt.compile().params
            return _Result([
                n.created_at for n in self.notifications
                if n.type == params.get('type_1')
                and n.reference_id == params.get('reference_id_1')
            ])

        if sql.startswith("SELECT notifications.id"):
            # The event-level dedup read: type + reference_id + date.
            params = stmt.compile().params
            return _Result([
                n.id for n in self.notifications
                if n.type == params.get('type_1')
                and n.reference_id == params.get('reference_id_1')
                and n.created_at.date() >= params.get('created_at_1')
            ])

        if "FROM team_members" in sql:
            return _Result(self.team_roster)

        if "FROM organization_members" in sql:
            return _Result(self.org_admins)

        if "FROM users" in sql:
            # ``IN`` compiles to a single expanding bind parameter, so the
            # value is a list of ids rather than one id per parameter.
            params = stmt.compile().params
            wanted = set()
            for key, value in params.items():
                if not key.startswith('id_'):
                    continue
                wanted.update(value if isinstance(value, list) else [value])
            return _Result([
                u for uid, u in self._users.items() if uid in wanted
            ])

        raise AssertionError(f"scripted session has no answer for:\n{sql}")

    def add(self, row):
        self._pending.append(row)

    async def commit(self):
        self.commits += 1
        for row in self._pending:
            row.id = uuid.uuid4()
            # The column has a server default, so a real INSERT stamps this.
            row.created_at = datetime.combine(
                self.today, datetime.min.time(),
            )
            self.notifications.append(row)
        self._pending = []

    async def rollback(self):
        self.rollbacks += 1
        self._pending = []

    # -- assertions helpers ------------------------------------------------

    def rows_of_type(self, notification_type: str):
        return [n for n in self.notifications if n.type == notification_type]

    def recipients_of(self, notification_type: str):
        return {n.user_id for n in self.rows_of_type(notification_type)}


@pytest.fixture
def world():
    return World()


# ---------------------------------------------------------------------------
# The removed `continue` — an unassigned task is no longer skipped forever
# ---------------------------------------------------------------------------

class TestUnassignedTasksAreNoLongerSkipped:

    @pytest.mark.asyncio
    async def test_unassigned_task_with_an_owning_team_notifies_that_team(self, world):
        """The regression #822 asks for by name.

        Before this change the loop opened with
        ``if not task.assigned_user_id: continue`` and this task produced
        nothing, ever.
        """
        primary, delegate = world.user(), world.user()
        team_id = uuid.uuid4()
        world.team_roster = [(primary, 'primary'), (delegate, 'delegate')]
        world.task(
            due_date=world.today + timedelta(days=1),
            assigned_user_id=None,
            owning_team_id=team_id,
        )

        created = await notifications.check_and_notify_due_tasks(world)

        assert created == 2
        assert world.recipients_of('task_due') == {primary, delegate}

    @pytest.mark.asyncio
    async def test_unassigned_task_with_no_team_falls_back_to_org_admins(self, world):
        admin = world.user()
        world.org_admins = [admin]
        world.task(due_date=world.today + timedelta(days=2), assigned_user_id=None)

        created = await notifications.check_and_notify_due_tasks(world)

        assert created == 1
        assert world.recipients_of('task_due') == {admin}

    @pytest.mark.asyncio
    async def test_a_task_nobody_owns_writes_nothing_and_does_not_raise(self, world):
        """An empty answer, not an exception. A raise here would take down the
        whole scheduler run over one orphaned item."""
        world.task(due_date=world.today, assigned_user_id=None)

        created = await notifications.check_and_notify_due_tasks(world)

        assert created == 0
        assert world.notifications == []

    @pytest.mark.asyncio
    async def test_an_unassigned_overdue_task_escalates_to_its_team(self, world):
        primary = world.user()
        world.team_roster = [(primary, 'primary')]
        world.task(
            due_date=world.today - timedelta(days=3),
            assigned_user_id=None,
            owning_team_id=uuid.uuid4(),
        )

        created = await notifications.check_and_notify_overdue_tasks(world)

        assert created == 1
        assert world.recipients_of('task_overdue') == {primary}


# ---------------------------------------------------------------------------
# Escalation
# ---------------------------------------------------------------------------

class TestEscalationFiresOnCrossingsNotState:

    async def _run_for_days(self, world, days: int):
        """Run the real scheduler once per simulated day."""
        for offset in range(1, days + 1):
            world.today = world.due + timedelta(days=offset)
            notifications.date = _FrozenDate(world.today)
            try:
                await notifications.check_and_notify_overdue_tasks(world)
            finally:
                notifications.date = date

    @pytest.mark.asyncio
    async def test_five_days_overdue_produces_exactly_one_escalation(self, world):
        """#822's acceptance criterion, through the real function.

        Five scheduler runs against one stalled task. Before this change that
        was five notifications; the old per-user dedup only suppressed a second
        run on the *same* day.
        """
        assignee = world.user()
        world.due = date.today() - timedelta(days=5)
        world.task(due_date=world.due, assigned_user_id=assignee)

        await self._run_for_days(world, 5)

        assert len(world.rows_of_type('task_overdue')) == 1

    @pytest.mark.asyncio
    async def test_thirty_days_produces_three_escalations_not_thirty(self, world):
        assignee = world.user()
        world.due = date.today() - timedelta(days=30)
        world.task(due_date=world.due, assigned_user_id=assignee)

        await self._run_for_days(world, 30)

        assert len(world.rows_of_type('task_overdue')) == 3

    @pytest.mark.asyncio
    async def test_escalation_reaches_the_team_on_top_of_the_assignee(self, world):
        """Escalation is additive, not a fourth tier. A stalled task surfaces
        to the owning team without waiting for the assignee to leave."""
        assignee, primary = world.user(), world.user()
        world.team_roster = [(primary, 'primary')]
        world.task(
            due_date=world.today - timedelta(days=1),
            assigned_user_id=assignee,
            owning_team_id=uuid.uuid4(),
        )

        await notifications.check_and_notify_overdue_tasks(world)

        assert world.recipients_of('task_overdue') == {assignee, primary}


# ---------------------------------------------------------------------------
# Volume control 1 — one transaction, one dedup key
# ---------------------------------------------------------------------------

class TestRecipientRowsAreWrittenAtomically:

    @pytest.mark.asyncio
    async def test_every_recipient_for_one_event_lands_in_one_commit(self, world):
        """Not one commit per recipient. A partially committed recipient set is
        what makes an event-level dedup key a lie."""
        world.team_roster = [(world.user(), 'primary'), (world.user(), 'delegate')]
        world.task(
            due_date=world.today + timedelta(days=1),
            assigned_user_id=None,
            owning_team_id=uuid.uuid4(),
        )

        await notifications.check_and_notify_due_tasks(world)

        assert len(world.rows_of_type('task_due')) == 2
        assert world.commits == 1

    @pytest.mark.asyncio
    async def test_a_second_run_on_the_same_day_re_notifies_nobody(self, world):
        """The dedup key is ``type + reference_id + date``. With the old
        per-user key, a set of recipients that had been half written would be
        half re-notified."""
        world.team_roster = [(world.user(), 'primary'), (world.user(), 'delegate')]
        world.task(
            due_date=world.today + timedelta(days=1),
            assigned_user_id=None,
            owning_team_id=uuid.uuid4(),
        )

        first = await notifications.check_and_notify_due_tasks(world)
        second = await notifications.check_and_notify_due_tasks(world)

        assert first == 2
        assert second == 0
        assert len(world.rows_of_type('task_due')) == 2


# ---------------------------------------------------------------------------
# Volume control 3 — an org with no teams is untouched
# ---------------------------------------------------------------------------

class TestOrganisationWithNoTeams:

    @pytest.mark.asyncio
    async def test_the_assignee_is_the_only_recipient(self, world):
        assignee = world.user()
        world.org_admins = [world.user()]
        world.task(due_date=world.today + timedelta(days=1), assigned_user_id=assignee)

        await notifications.check_and_notify_due_tasks(world)

        assert world.recipients_of('task_due') == {assignee}

    @pytest.mark.asyncio
    async def test_an_overdue_assigned_task_escalates_to_the_assignee_alone(self, world):
        assignee = world.user()
        world.org_admins = [world.user()]
        world.task(due_date=world.today - timedelta(days=2), assigned_user_id=assignee)

        await notifications.check_and_notify_overdue_tasks(world)

        assert world.recipients_of('task_overdue') == {assignee}


class _FrozenDate:
    """Stands in for the ``date`` module attribute so a scheduler run can be
    dated. Only ``today`` and the arithmetic the schedulers use are needed."""

    def __init__(self, today: date):
        self._today = today

    def today(self) -> date:
        return self._today

    def __getattr__(self, name):
        return getattr(date, name)
