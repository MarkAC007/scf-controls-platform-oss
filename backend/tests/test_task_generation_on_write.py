"""A tracked evidence item gets its first collection task on write (#789).

The gap this closes: #781 made assignment reach the columns the schedulers read,
and `_propagate_assignee_to_open_tasks` made it reach tasks that already exist.
Neither helps an item that has just become tracked, because it has no task at
all. Its first one came from the 01:00 UTC sweep — up to twenty-four hours after
the person did the work, with nothing in the product saying so.

Two claims are pinned:

1. **One declaration of eligibility.** `generate_task_for_tracking` decides, and
   both the sweep and the five write paths ask it. A second copy of the rule on
   the write path is the failure shape #783 was: one concept per subsystem,
   disagreeing silently.
2. **The write is never collateral damage.** Generation is a consequence of
   saving a tracking record, not part of it. It must not commit, and it must not
   be able to fail the request.

Mock-based, no database, mirroring tests/test_evidence_assignment.py.
"""
import os
import sys
from datetime import date, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import catalog_models  # noqa: E402,F401 — registers mappers referenced by models
from services.task_generator import (  # noqa: E402
    CREATED,
    SKIP_DUPLICATE,
    SKIP_NON_SCHEDULING,
    SKIP_NOT_TRACKED,
    SKIP_NO_FREQUENCY,
    SKIP_UNRECOGNISED_FREQUENCY,
    generate_task_for_tracking,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeResult:
    def __init__(self, value):
        self._value = value

    @property
    def rowcount(self):
        return self._value if isinstance(self._value, int) else 0

    def first(self):
        return self._value

    def scalar_one_or_none(self):
        return self._value

    def scalar_one(self):
        return self._value

    def scalars(self):
        rows = self._value

        class _Scalars:
            def all(self_inner):
                return list(rows or [])

        return _Scalars()


class FakeSession:
    """Async session stub replaying scripted results in order."""

    def __init__(self, results=None):
        self._results = list(results or [])
        self.statements = []
        self.added = []
        self.committed = False
        self.flushes = 0

    async def execute(self, statement, params=None):
        self.statements.append(statement)
        value = self._results.pop(0) if self._results else None
        return FakeResult(value)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True

    async def flush(self):
        self.flushes += 1

    async def refresh(self, obj):
        pass


def tracking_row(**overrides):
    """A tracking row that WOULD generate a task, unless an override stops it."""
    row = MagicMock()
    row.id = overrides.pop("id", uuid4())
    row.evidence_id = overrides.pop("evidence_id", "E-IAM-01")
    row.is_tracked = overrides.pop("is_tracked", True)
    row.frequency = overrides.pop("frequency", "monthly")
    row.last_collection_date = overrides.pop("last_collection_date", None)
    row.assigned_user_id = overrides.pop("assigned_user_id", None)
    row.owner_user_id = overrides.pop("owner_user_id", None)
    row.next_collection_date = None
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


# ---------------------------------------------------------------------------
# 1. The decision itself
# ---------------------------------------------------------------------------

class TestGenerateTaskForTracking:

    @pytest.mark.asyncio
    async def test_eligible_row_produces_a_task(self):
        db = FakeSession([None])  # duplicate check finds nothing
        outcome = await generate_task_for_tracking(db, tracking_row())

        assert outcome.created is True
        assert outcome.reason == CREATED
        assert len(db.added) == 1
        assert db.added[0].title == "Collect Evidence: E-IAM-01"
        assert db.added[0].auto_generated is True

    @pytest.mark.asyncio
    async def test_it_never_commits(self):
        """The caller's transaction decides. A tracking write that rolls back
        must not leave the task it would have implied behind it."""
        db = FakeSession([None])
        await generate_task_for_tracking(db, tracking_row())
        assert db.committed is False

    @pytest.mark.asyncio
    async def test_untracked_row_produces_nothing(self):
        db = FakeSession([None])
        outcome = await generate_task_for_tracking(db, tracking_row(is_tracked=False))

        assert outcome.created is False
        assert outcome.reason == SKIP_NOT_TRACKED
        assert db.added == []
        # The duplicate query is never even issued — declining is free.
        assert db.statements == []

    @pytest.mark.asyncio
    async def test_row_without_a_frequency_produces_nothing(self):
        db = FakeSession([None])
        outcome = await generate_task_for_tracking(db, tracking_row(frequency=None))

        assert outcome.reason == SKIP_NO_FREQUENCY
        assert db.added == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("frequency", ["real_time", "on_demand"])
    async def test_non_scheduling_cadence_produces_nothing(self, frequency):
        """`real_time` and `on_demand` are recognised and schedule nothing. Before
        #783 `real_time` had no key at all and was skipped as an *unrecognised*
        value — same outcome, wrong diagnosis, and a warning nobody could see."""
        db = FakeSession([None])
        outcome = await generate_task_for_tracking(db, tracking_row(frequency=frequency))

        assert outcome.reason == SKIP_NON_SCHEDULING
        assert db.added == []

    @pytest.mark.asyncio
    async def test_unrecognised_cadence_is_a_distinct_reason(self):
        """Distinct from non-scheduling: this one IS a data defect."""
        db = FakeSession([None])
        outcome = await generate_task_for_tracking(db, tracking_row(frequency="fortnightly-ish"))

        assert outcome.reason == SKIP_UNRECOGNISED_FREQUENCY
        assert db.added == []

    @pytest.mark.asyncio
    async def test_a_second_write_does_not_duplicate(self):
        """The web client re-saves the whole tracking object on every debounced
        field edit, so this runs on keystrokes. Without the duplicate window a
        person typing a comment would mint a task per pause."""
        db = FakeSession([MagicMock()])  # duplicate check finds an open task
        outcome = await generate_task_for_tracking(db, tracking_row())

        assert outcome.created is False
        assert outcome.reason == SKIP_DUPLICATE
        assert db.added == []

    @pytest.mark.asyncio
    async def test_assignee_prefers_assigned_over_owner(self):
        assignee, owner = uuid4(), uuid4()
        db = FakeSession([None])
        outcome = await generate_task_for_tracking(
            db, tracking_row(assigned_user_id=assignee, owner_user_id=owner)
        )
        assert outcome.assigned_user_id == assignee
        assert db.added[0].assigned_user_id == assignee

    @pytest.mark.asyncio
    async def test_owner_is_the_fallback_assignee(self):
        owner = uuid4()
        db = FakeSession([None])
        outcome = await generate_task_for_tracking(db, tracking_row(owner_user_id=owner))
        assert outcome.assigned_user_id == owner

    @pytest.mark.asyncio
    async def test_first_due_date_for_a_long_cadence_is_thirty_days(self):
        """An annual item's first task is due in 30 days, not 370. A task 370
        days out is indistinguishable from no task for the person trying to
        start collecting today."""
        db = FakeSession([None])
        outcome = await generate_task_for_tracking(db, tracking_row(frequency="annual"))
        assert outcome.due_date == date.today() + timedelta(days=30)

    @pytest.mark.asyncio
    async def test_subsequent_due_date_follows_the_last_collection(self):
        last = date.today() - timedelta(days=5)
        db = FakeSession([None])
        outcome = await generate_task_for_tracking(
            db, tracking_row(frequency="weekly", last_collection_date=last)
        )
        assert outcome.due_date == last + timedelta(days=7)

    @pytest.mark.asyncio
    async def test_next_collection_date_is_stamped_on_the_row(self):
        row = tracking_row()
        db = FakeSession([None])
        outcome = await generate_task_for_tracking(db, row)
        assert row.next_collection_date == outcome.due_date


# ---------------------------------------------------------------------------
# 2. The sweep asks the same question
# ---------------------------------------------------------------------------

class TestSweepDelegates:

    def test_sweep_calls_the_shared_decision(self):
        """Read the source rather than the behaviour. A sweep that re-implemented
        eligibility would still pass a behavioural test on the day it was written
        and drift the week after — which is exactly how the frequency vocabulary
        ended up declared five times (#783)."""
        import inspect

        from services import task_generator

        source = inspect.getsource(task_generator.generate_evidence_tasks)
        assert "generate_task_for_tracking(db, evidence)" in source
        # ...and does not carry its own copy of the rule.
        assert "normalize_frequency" not in source
        assert "EvidenceCollectionTask(" not in source

    def test_sweep_still_reports_created_and_skipped(self):
        """Callers (tasks_automation, the __main__ block) read these two keys."""
        import inspect

        from services import task_generator

        source = inspect.getsource(task_generator.generate_evidence_tasks)
        assert '"tasks_created": tasks_created' in source
        assert '"tasks_skipped": tasks_skipped' in source


# ---------------------------------------------------------------------------
# 3. Every tracking write path asks it
# ---------------------------------------------------------------------------

WRITE_PATHS = [
    "create_or_update_evidence_tracking",
    "batch_update_evidence_tracking",
    "update_evidence_tracking",
]


class TestWritePathsGenerate:

    @pytest.mark.parametrize("handler_name", WRITE_PATHS)
    def test_handler_generates_a_first_task(self, handler_name):
        import inspect

        from api import evidence_tracking

        source = inspect.getsource(getattr(evidence_tracking, handler_name))
        assert "_generate_first_task(" in source, (
            f"{handler_name} writes a tracking row without asking whether it now "
            "owes a collection task — the 24-hour dead zone #789 filed"
        )

    def test_upsert_covers_both_of_its_branches(self):
        """`create_or_update_evidence_tracking` is two write paths in one
        function. A single call site would cover only one of them."""
        import inspect

        from api import evidence_tracking

        source = inspect.getsource(
            evidence_tracking.create_or_update_evidence_tracking
        )
        assert source.count("_generate_first_task(") == 2

    def test_batch_covers_both_of_its_branches(self):
        import inspect

        from api import evidence_tracking

        source = inspect.getsource(evidence_tracking.batch_update_evidence_tracking)
        assert source.count("_generate_first_task(") == 2

    @pytest.mark.asyncio
    async def test_generation_failure_does_not_fail_the_write(self):
        """Saving the tracking record is what the operator asked for. Scheduling
        its first collection is a consequence, and a consequence that throws must
        not take the request with it — the sweep retries tonight regardless."""
        from api.evidence_tracking import _generate_first_task

        class ExplodingSession(FakeSession):
            async def execute(self, statement, params=None):
                raise RuntimeError("database went away")

        # Must not raise.
        await _generate_first_task(tracking_row(), ExplodingSession())

    @pytest.mark.asyncio
    async def test_generation_flushes_before_asking(self):
        """A row created in this request has no id yet, and the duplicate check
        joins on it. Without the flush every call would match nothing and mint
        another task."""
        from api.evidence_tracking import _generate_first_task

        db = FakeSession([None])
        await _generate_first_task(tracking_row(), db)
        assert db.flushes == 1
