"""Escalation fires on crossings, not on state (#822 phase 4).

The scheduler runs daily and asks "is this overdue?". That is a question about
**state**, and state is true again tomorrow. An item that escalates because it
*is* overdue escalates again the next day, and the day after, and by day thirty
one stalled task has produced thirty notifications and a team that has muted
the platform.

The acceptance criterion is a **count**, not a presence: *a task overdue for
five days must have produced exactly one escalation.* A test that asserts an
escalation happened passes just as happily against the broken behaviour, which
also escalates — five times. So the tests here run the scheduler's decision
once per simulated day and count what came out.

Two halves:

* the pure decision, :func:`escalation_threshold`, driven day by day with the
  previous days' output fed back in, which is exactly what the real scheduler
  does through the notifications table. No database;
* :func:`pending_escalation_threshold` against real ``notifications`` rows,
  which is the half that proves the derivation survives contact with
  PostgreSQL, ``created_at`` being a ``DateTime`` where the thresholds are
  whole days, and the index the schema lane added for it. **SKIPS in CI.**

There is deliberately no ``last_escalated_at`` column to assert on: the state
is derived from the rows already written, so a run that dies half way is
self-repairing rather than leaving a counter that is wrong forever.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import catalog_models  # noqa: E402,F401
from models import Notification, Organization, OrganizationMember, User  # noqa: E402
from services.notification_escalation import (  # noqa: E402
    ESCALATION_THRESHOLD_DAYS,
    escalation_threshold,
    pending_escalation_threshold,
)

DATABASE_URL = os.getenv("DATABASE_URL", "")

requires_postgres = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason="needs a Postgres DATABASE_URL — SKIPPED, not passed",
)

DUE = date(2026, 8, 1)


def _run_scheduler_for(days: int, *, due_date: date = DUE) -> list:
    """Run the daily decision for ``days`` consecutive days from the due date.

    Returns the list of thresholds that fired, one entry per firing. Each day
    sees the notifications the earlier days produced, which is what the real
    scheduler reads back out of the notifications table — so a decision that
    depends only on *state* rather than on the crossing shows up here as one
    entry per day.
    """
    written: list = []
    fired: list = []
    for offset in range(1, days + 1):
        today = due_date + timedelta(days=offset)
        threshold = escalation_threshold(
            due_date=due_date, today=today, prior_notification_dates=list(written),
        )
        if threshold is not None:
            fired.append(threshold)
            written.append(today)   # the notification this run writes
    return fired


class TestTheCountOverTime:
    """The criterion, and the shape of the defect it guards."""

    def test_five_days_overdue_produced_exactly_one_escalation(self):
        """The assertion #822 names. Not "an escalation happened" — one."""
        assert _run_scheduler_for(5) == [0]

    def test_thirty_days_overdue_produced_three_not_thirty(self):
        """The crossing at 0, then +7, then +30. Three notifications in a
        month rather than thirty, which is the difference between a platform
        people read and one they have muted."""
        assert _run_scheduler_for(30) == [0, 7, 30]

    def test_ninety_days_overdue_still_produced_only_three(self):
        """There is no fourth threshold. An item nobody ever acts on stops
        generating noise rather than generating it forever."""
        assert _run_scheduler_for(90) == [0, 7, 30]

    @pytest.mark.parametrize(
        "days,expected",
        [
            (1, [0]), (2, [0]), (3, [0]), (6, [0]),
            (7, [0, 7]), (8, [0, 7]), (29, [0, 7]),
            (30, [0, 7, 30]), (31, [0, 7, 30]),
        ],
    )
    def test_the_whole_first_month_day_by_day(self, days, expected):
        assert _run_scheduler_for(days) == expected

    def test_the_broken_behaviour_would_fail_these(self):
        """A note in executable form: state-based escalation fires every day.

        This spells out what the tests above are distinguishing between, so
        that "escalation happens" can never be mistaken for the criterion.
        """
        state_based = [0 for _ in range(5)]      # what "if overdue: notify" does
        assert _run_scheduler_for(5) != state_based
        assert len(_run_scheduler_for(5)) == 1


class TestTheDecisionItself:
    """The pure function, branch by branch."""

    def test_an_item_due_today_is_not_overdue(self):
        assert escalation_threshold(
            due_date=DUE, today=DUE, prior_notification_dates=[],
        ) is None

    def test_an_item_due_tomorrow_is_not_overdue(self):
        assert escalation_threshold(
            due_date=DUE, today=DUE - timedelta(days=1),
            prior_notification_dates=[],
        ) is None

    def test_the_first_day_past_due_is_the_zero_crossing(self):
        assert escalation_threshold(
            due_date=DUE, today=DUE + timedelta(days=1),
            prior_notification_dates=[],
        ) == 0

    def test_a_notification_already_sent_suppresses_the_same_threshold(self):
        assert escalation_threshold(
            due_date=DUE, today=DUE + timedelta(days=3),
            prior_notification_dates=[DUE + timedelta(days=1)],
        ) is None

    def test_a_notification_from_before_the_threshold_does_not_suppress_it(self):
        """Day 1's notification must not suppress day 7's crossing — it was
        written before ``due + 7`` was reachable."""
        assert escalation_threshold(
            due_date=DUE, today=DUE + timedelta(days=7),
            prior_notification_dates=[DUE + timedelta(days=1)],
        ) == 7

    def test_only_the_highest_crossed_threshold_fires(self):
        """An item first looked at when it is already 40 days overdue — a
        scheduler that was switched off, or an import with a historic due
        date — escalates once, at 30, not three times for deadlines that all
        passed before anybody could act."""
        assert escalation_threshold(
            due_date=DUE, today=DUE + timedelta(days=40),
            prior_notification_dates=[],
        ) == 30

    def test_a_backlog_item_produces_one_notification_in_total(self):
        """The same claim as a count, which is how the criterion is worded."""
        fired = _run_scheduler_for(45, due_date=DUE)
        assert fired == [0, 7, 30]

        late = []
        written = []
        for offset in range(40, 60):
            today = DUE + timedelta(days=offset)
            threshold = escalation_threshold(
                due_date=DUE, today=today, prior_notification_dates=list(written),
            )
            if threshold is not None:
                late.append(threshold)
                written.append(today)
        assert late == [30]

    def test_order_of_prior_notifications_is_irrelevant(self):
        dates = [DUE + timedelta(days=d) for d in (1, 7)]
        forwards = escalation_threshold(
            due_date=DUE, today=DUE + timedelta(days=10),
            prior_notification_dates=dates,
        )
        backwards = escalation_threshold(
            due_date=DUE, today=DUE + timedelta(days=10),
            prior_notification_dates=list(reversed(dates)),
        )
        assert forwards == backwards is None

    def test_a_none_in_the_prior_dates_does_not_crash_the_run(self):
        """``created_at`` has a server default, so it is never NULL in
        practice — but a scheduler that raises takes every other item in the
        run down with it, and this function promises never to raise."""
        assert escalation_threshold(
            due_date=DUE, today=DUE + timedelta(days=1),
            prior_notification_dates=[None],
        ) == 0

    def test_the_thresholds_are_the_three_the_issue_names(self):
        assert tuple(ESCALATION_THRESHOLD_DAYS) == (0, 7, 30)

    def test_the_thresholds_are_in_ascending_order(self):
        """``max(crossed)`` does not care, but the docstring's table does, and
        so would anyone adding a fourth."""
        assert list(ESCALATION_THRESHOLD_DAYS) == sorted(ESCALATION_THRESHOLD_DAYS)


# ---------------------------------------------------------------------------
# Against real notification rows. SKIPS in CI.
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


@pytest.fixture
async def recipient(db):
    tag = uuid.uuid4().hex[:10]
    org = Organization(name=f"esc-{tag}", slug=f"esc-{tag}")
    db.add(org)
    await db.flush()
    user = User(email=f"esc-{tag}@example.invalid", google_sub=f"esc-{tag}")
    db.add(user)
    await db.flush()
    db.add(OrganizationMember(
        organization_id=org.id, user_id=user.id, role="admin",
    ))
    await db.flush()
    return user


@requires_postgres
class TestAgainstTheNotificationsTable:
    """The derivation, read back out of the rows the scheduler wrote.

    ``created_at`` has a server default of ``now()``, so a row written today
    carries today's timestamp whatever the simulation wants. Every row here
    therefore sets ``created_at`` explicitly — otherwise the test would only
    ever exercise the one-day-old case, and would pass against a function that
    ignored its input entirely.
    """

    async def _write(self, db, user, reference_id, when, notification_type):
        db.add(Notification(
            user_id=user.id, type=notification_type, reference_type="task",
            reference_id=reference_id, message="overdue",
            created_at=datetime.combine(when, datetime.min.time()),
        ))
        await db.flush()

    async def test_the_first_crossing_fires_when_nothing_has_been_written(
        self, db, recipient
    ):
        reference_id = uuid.uuid4()

        threshold = await pending_escalation_threshold(
            db, notification_type="task_overdue", reference_id=reference_id,
            due_date=DUE, today=DUE + timedelta(days=1),
        )

        assert threshold == 0

    async def test_a_row_already_written_suppresses_the_next_four_days(
        self, db, recipient
    ):
        reference_id = uuid.uuid4()
        await self._write(
            db, recipient, reference_id, DUE + timedelta(days=1), "task_overdue")

        for offset in range(2, 7):
            threshold = await pending_escalation_threshold(
                db, notification_type="task_overdue", reference_id=reference_id,
                due_date=DUE, today=DUE + timedelta(days=offset),
            )
            assert threshold is None, f"day {offset} should be silent"

    async def test_five_days_overdue_leaves_exactly_one_row(self, db, recipient):
        """The criterion, end to end against the table it is derived from.

        The loop is the scheduler: decide, and if it fires, write the row the
        next day's decision will read.
        """
        reference_id = uuid.uuid4()

        for offset in range(1, 6):
            threshold = await pending_escalation_threshold(
                db, notification_type="task_overdue", reference_id=reference_id,
                due_date=DUE, today=DUE + timedelta(days=offset),
            )
            if threshold is not None:
                await self._write(
                    db, recipient, reference_id,
                    DUE + timedelta(days=offset), "task_overdue",
                )

        written = (await db.execute(
            select(func.count()).select_from(Notification)
            .where(Notification.reference_id == reference_id)
        )).scalar_one()
        assert written == 1

    async def test_thirty_days_overdue_leaves_exactly_three_rows(
        self, db, recipient
    ):
        reference_id = uuid.uuid4()

        for offset in range(1, 31):
            threshold = await pending_escalation_threshold(
                db, notification_type="task_overdue", reference_id=reference_id,
                due_date=DUE, today=DUE + timedelta(days=offset),
            )
            if threshold is not None:
                await self._write(
                    db, recipient, reference_id,
                    DUE + timedelta(days=offset), "task_overdue",
                )

        written = (await db.execute(
            select(func.count()).select_from(Notification)
            .where(Notification.reference_id == reference_id)
        )).scalar_one()
        assert written == 3

    async def test_another_items_notifications_do_not_suppress_this_one(
        self, db, recipient
    ):
        """Keyed on ``reference_id``. Without that predicate every item in the
        deployment would share one escalation budget, and the test above would
        still pass."""
        mine, theirs = uuid.uuid4(), uuid.uuid4()
        await self._write(
            db, recipient, theirs, DUE + timedelta(days=1), "task_overdue")

        threshold = await pending_escalation_threshold(
            db, notification_type="task_overdue", reference_id=mine,
            due_date=DUE, today=DUE + timedelta(days=2),
        )
        assert threshold == 0

    async def test_a_different_notification_type_does_not_suppress_this_one(
        self, db, recipient
    ):
        """A ``task_due`` warning written three days before the deadline must
        not stand in for the overdue escalation."""
        reference_id = uuid.uuid4()
        await self._write(
            db, recipient, reference_id, DUE + timedelta(days=1), "task_due")

        threshold = await pending_escalation_threshold(
            db, notification_type="task_overdue", reference_id=reference_id,
            due_date=DUE, today=DUE + timedelta(days=2),
        )
        assert threshold == 0

    async def test_one_recipients_row_suppresses_the_threshold_for_everyone(
        self, db, recipient
    ):
        """Dedup is per item, not per user.

        With a recipient *set*, a per-user check re-notifies whoever already
        got the message after a partially-failed run — and, worse, a team of
        three would escalate three times because each recipient's own history
        is empty. The row written for one recipient closes the threshold.
        """
        reference_id = uuid.uuid4()
        await self._write(
            db, recipient, reference_id, DUE + timedelta(days=1), "task_overdue")

        # A second person on the same team, with no notification history.
        tag = uuid.uuid4().hex[:8]
        other = User(email=f"o-{tag}@example.invalid", google_sub=f"o-{tag}")
        db.add(other)
        await db.flush()

        threshold = await pending_escalation_threshold(
            db, notification_type="task_overdue", reference_id=reference_id,
            due_date=DUE, today=DUE + timedelta(days=2),
        )
        assert threshold is None

    async def test_the_query_is_served_by_the_index_the_schema_lane_added(
        self, db
    ):
        """``ix_notifications_type_reference_created`` exists and leads with
        ``type``.

        Not a performance test — a presence test. #822 moves dedup off the
        ``user_id``-leading predicate that ``ix_notifications_user`` served,
        and without a replacement the daily scheduler does one sequential scan
        of a table that is never pruned, per item, per run.
        """
        definition = (await db.execute(text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE indexname = 'ix_notifications_type_reference_created'"
        ))).scalar_one_or_none()
        assert definition is not None, (
            "ix_notifications_type_reference_created is missing; dedup falls "
            "back to a sequential scan"
        )
        assert "type" in definition
        assert "reference_id" in definition
        assert "created_at" in definition
