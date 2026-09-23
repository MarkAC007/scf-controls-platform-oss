"""Individual assignment is withdrawn; team assignment is the only assignment.

Three write paths could still name a person as the owner of a control or an
evidence item. Each is pinned here, and each is pinned at a different strength,
because they are not the same kind of surface:

1. ``POST /api/assignments`` — the polymorphic per-user table. **410 Gone.**
   Not a silent no-op: a caller that posted here and got a 201 would believe it
   had assigned somebody, and nothing downstream would have changed hands.
2. ``EvidenceCollectionTaskCreate.assigned_user_id`` — **field removed.** A new
   task has no history to preserve, so there is nothing to be gentle about.
3. ``EvidenceCollectionTaskUpdate.assigned_user_id`` — **kept, clear-only.**
   This is the one that looks like a contradiction and is not. A stored assignee
   is tier 1 of the owner-resolution chain, so a task held by someone who has
   left would notify that account forever if there were no way to blank it.
   Removing the field closes the exit; leaving it settable leaves the door the
   requirement shuts. Clear-only is the single shape that is neither, so both
   halves are asserted: ``null`` succeeds, a uuid is refused.

Mock-based, no database, following tests/test_evidence_assignment.py. Nothing
here opens a connection, so nothing here can leave a row behind.
"""
import os
import sys
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException  # noqa: E402

import catalog_models  # noqa: E402,F401 — registers mappers referenced by models.System
from schemas import (  # noqa: E402
    AssignmentCreate,
    BatchScopedControlOperation,
    EvidenceCollectionTaskCreate,
    EvidenceCollectionTaskResponse,
    EvidenceCollectionTaskUpdate,
    ScopedControlResponse,
    ScopedControlUpdate,
)


class FakeSession:
    """Async session stub. Records whether anything was written."""

    def __init__(self, results=None):
        self._results = list(results or [])
        self.added = []
        self.committed = False

    async def execute(self, statement, params=None):
        value = self._results.pop(0) if self._results else None

        class _Result:
            def scalar_one_or_none(self_inner):
                return value

            def scalar_one(self_inner):
                return value

        return _Result()

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True

    async def flush(self):
        pass

    async def refresh(self, obj):
        pass


@pytest.fixture
def caller():
    user = MagicMock()
    user.db_id = str(uuid4())
    user.email = "editor@example.test"
    user.auth_method = "oidc"
    return user


# ---------------------------------------------------------------------------
# 1. POST /api/assignments
# ---------------------------------------------------------------------------

class TestAssignmentCreateIsGone:

    @pytest.mark.asyncio
    async def test_create_assignment_returns_410(self, caller):
        from api.assignments import create_assignment

        with pytest.raises(HTTPException) as exc:
            await create_assignment(
                assignment_data=AssignmentCreate(
                    assignable_type="control",
                    assignable_id=uuid4(),
                    user_id=uuid4(),
                ),
                current_user=caller,
            )

        assert exc.value.status_code == 410

    @pytest.mark.asyncio
    async def test_the_410_names_the_replacement(self, caller):
        """A bare 410 tells an integrator they are wrong, not what to do instead."""
        from api.assignments import create_assignment

        with pytest.raises(HTTPException) as exc:
            await create_assignment(
                assignment_data=AssignmentCreate(
                    assignable_type="evidence",
                    assignable_id=uuid4(),
                    user_id=uuid4(),
                ),
                current_user=caller,
            )

        assert "team-assignments" in exc.value.detail

    @pytest.mark.asyncio
    async def test_it_refuses_before_touching_the_database(self, caller):
        """The refusal is unconditional, not the tail of a validation path.

        If any lookup ran first, a request could be refused for the wrong reason
        -- 404 on an id the caller mistyped -- and the withdrawal would read as
        an intermittent bug rather than a decision.
        """
        from api.assignments import create_assignment
        import inspect

        params = inspect.signature(create_assignment).parameters
        assert "db" not in params

    def test_read_and_delete_survive(self):
        """The rows stay reachable, and that is deliberate.

        They are the only record of who held what before the cutover, and the
        DELETE is how an organisation clears an inherited individual assignment
        on purpose rather than having it vanish underneath them.
        """
        import api.assignments as assignments_api

        assert hasattr(assignments_api, "list_assignments")
        assert hasattr(assignments_api, "get_my_assignments")
        assert hasattr(assignments_api, "delete_assignment")


# ---------------------------------------------------------------------------
# 2. Task create no longer accepts a person
# ---------------------------------------------------------------------------

class TestTaskCreateHasNoAssignee:

    def test_create_schema_has_no_assigned_user_id(self):
        assert "assigned_user_id" not in EvidenceCollectionTaskCreate.model_fields

    def test_an_assignee_in_the_body_is_dropped_rather_than_stored(self):
        """An old client keeps working; it just stops having an effect."""
        payload = EvidenceCollectionTaskCreate(
            evidence_tracking_id=uuid4(),
            due_date="2030-01-01",
            assigned_user_id=str(uuid4()),
        )
        assert not hasattr(payload, "assigned_user_id")
        assert "assigned_user_id" not in payload.model_dump()

    def test_the_owning_team_is_still_how_a_new_task_gets_an_owner(self):
        team = uuid4()
        payload = EvidenceCollectionTaskCreate(
            evidence_tracking_id=uuid4(),
            due_date="2030-01-01",
            owning_team_id=team,
        )
        assert payload.owning_team_id == team

    @pytest.mark.asyncio
    async def test_the_create_endpoint_writes_no_assignee(self, caller):
        """The endpoint, not only the schema. A schema field can be removed
        while the handler still reads it off a dict and writes the column."""
        from api.evidence_tasks import create_evidence_task

        org_id = uuid4()
        evidence = MagicMock()
        evidence.id = uuid4()
        evidence.organization_id = org_id
        evidence.evidence_id = "E-TEST"
        evidence.owning_team_id = None

        db = FakeSession([])

        async def _resolve(evidence_id, current_user, db_, min_role="viewer"):
            return evidence

        async def _noop(*args, **kwargs):
            return None

        payload = EvidenceCollectionTaskCreate(
            evidence_tracking_id=evidence.id,
            due_date="2030-01-01",
            title="Collect something",
        )
        # Smuggled past the schema so the HANDLER is what is under test. Without
        # this the assertion below passes for the wrong reason -- there is no
        # assignee to write, so a handler that still wrote one would look clean.
        payload.__dict__["assigned_user_id"] = uuid4()

        with patch("api.evidence_tasks._resolve_evidence_access", new=_resolve), \
                patch("api.evidence_tasks.log_entity_changes", new=_noop):
            try:
                await create_evidence_task(
                    task_data=payload,
                    db=db,
                    current_user=caller,
                )
            except Exception:
                # The handler serialises the row afterwards, which needs more of
                # the session than is stubbed here. What matters is what it put
                # on the row before it got there.
                pass

        assert db.added, "no task row was constructed"
        assert getattr(db.added[0], "assigned_user_id", None) is None


# ---------------------------------------------------------------------------
# 3. Task update is clear-only
# ---------------------------------------------------------------------------

def _task_with_assignee(assignee):
    task = MagicMock()
    task.id = uuid4()
    task.evidence_tracking_id = uuid4()
    task.assigned_user_id = assignee
    task.owning_team_id = None
    task.organization_id = uuid4()
    return task


class TestTaskUpdateIsClearOnly:

    @pytest.mark.asyncio
    async def test_naming_a_person_is_refused_with_422(self, caller):
        from api.evidence_tasks import update_evidence_task

        task = _task_with_assignee(None)
        db = FakeSession([])

        async def _resolve(task_id, current_user, db_, min_role="viewer"):
            return task

        with patch("api.evidence_tasks._resolve_task_access", new=_resolve):
            with pytest.raises(HTTPException) as exc:
                await update_evidence_task(
                    task_id=task.id,
                    task_update=EvidenceCollectionTaskUpdate(
                        assigned_user_id=uuid4()
                    ),
                    db=db,
                    current_user=caller,
                )

        assert exc.value.status_code == 422
        assert task.assigned_user_id is None
        assert db.committed is False

    @pytest.mark.asyncio
    async def test_re_sending_the_stored_assignee_is_allowed(self, caller):
        """Unchanged IS exempt, and it has to be.

        This case previously asserted the opposite, on the reasoning that a
        "same value" carve-out would let a client keep an assignee alive by
        echoing it back on every save. That reasoning was wrong: nothing expires
        a stored assignee, so an echo keeps nothing alive that was not already
        there, and it creates no assignment a caller did not already have.

        What refusing it did do was make every task carrying an assignee from
        before this rule uneditable. A client that PATCHes an object it fetched
        re-sends the fields it read, so the refusal would land on a value nobody
        chose, and the only way out would be to clear an assignee the operator
        may not have known about -- to change the due date. That is a migration
        trap, not an enforcement.

        The guard belongs on an attempt to SET an individual, which is asserted
        by its neighbours: a different id is refused whether the field was empty
        or held somebody else.
        """
        from api.evidence_tasks import update_evidence_task

        departed = uuid4()
        task = _task_with_assignee(departed)
        db = FakeSession([])

        async def _resolve(task_id, current_user, db_, min_role="viewer"):
            return task

        with patch("api.evidence_tasks._resolve_task_access", new=_resolve):
            await update_evidence_task(
                task_id=task.id,
                task_update=EvidenceCollectionTaskUpdate(
                    title="renamed", assigned_user_id=departed
                ),
                db=db,
                current_user=caller,
            )

        assert task.title == "renamed"
        assert task.assigned_user_id == departed
        assert db.committed is True

    @pytest.mark.asyncio
    async def test_naming_a_different_person_is_refused_even_when_one_is_stored(
        self, caller
    ):
        """The echo exemption must not widen into "any value once one is set".

        A handler that checked only whether the field was already populated
        would pass the echo case above and still let a caller hand the task to
        somebody else -- the exact capability JQA-001 withdrew.
        """
        from api.evidence_tasks import update_evidence_task

        departed = uuid4()
        task = _task_with_assignee(departed)
        db = FakeSession([])

        async def _resolve(task_id, current_user, db_, min_role="viewer"):
            return task

        with patch("api.evidence_tasks._resolve_task_access", new=_resolve):
            with pytest.raises(HTTPException) as exc:
                await update_evidence_task(
                    task_id=task.id,
                    task_update=EvidenceCollectionTaskUpdate(
                        assigned_user_id=uuid4()
                    ),
                    db=db,
                    current_user=caller,
                )

        assert exc.value.status_code == 422
        assert task.assigned_user_id == departed
        assert db.committed is False

    @pytest.mark.asyncio
    async def test_an_explicit_null_clears_the_assignee(self, caller):
        from api.evidence_tasks import update_evidence_task

        departed = uuid4()
        task = _task_with_assignee(departed)
        db = FakeSession([])

        async def _resolve(task_id, current_user, db_, min_role="viewer"):
            return task

        async def _noop(*args, **kwargs):
            return None

        with patch("api.evidence_tasks._resolve_task_access", new=_resolve), \
                patch("api.evidence_tasks.log_entity_changes", new=_noop):
            await update_evidence_task(
                task_id=task.id,
                task_update=EvidenceCollectionTaskUpdate(assigned_user_id=None),
                db=db,
                current_user=caller,
            )

        assert task.assigned_user_id is None
        assert db.committed is True

    @pytest.mark.asyncio
    async def test_omitting_the_key_leaves_an_existing_assignee_alone(self, caller):
        """An edit to an unrelated field must not silently un-assign the task."""
        from api.evidence_tasks import update_evidence_task

        departed = uuid4()
        task = _task_with_assignee(departed)
        db = FakeSession([])

        async def _resolve(task_id, current_user, db_, min_role="viewer"):
            return task

        async def _noop(*args, **kwargs):
            return None

        with patch("api.evidence_tasks._resolve_task_access", new=_resolve), \
                patch("api.evidence_tasks.log_entity_changes", new=_noop):
            await update_evidence_task(
                task_id=task.id,
                task_update=EvidenceCollectionTaskUpdate(title="renamed"),
                db=db,
                current_user=caller,
            )

        assert task.title == "renamed"
        assert task.assigned_user_id == departed


# ---------------------------------------------------------------------------
# 4. What must NOT have been taken away
# ---------------------------------------------------------------------------

class TestHistorySurvives:
    """Every column in this change is kept. The tombstones are the fix.

    A data migration that blanked them would be the destructive option and is
    not reversible -- and one of them is still counted by a live metric.
    """

    def test_the_task_read_schema_still_returns_an_assignee(self):
        assert "assigned_user_id" in EvidenceCollectionTaskResponse.model_fields
        assert "assigned_user" in EvidenceCollectionTaskResponse.model_fields

    def test_the_task_column_and_relationship_are_untouched(self):
        from models import EvidenceCollectionTask

        assert hasattr(EvidenceCollectionTask, "assigned_user_id")

    def test_the_assignments_table_is_untouched(self):
        from models import Assignment

        assert hasattr(Assignment, "user_id")

    def test_scoped_control_write_schemas_no_longer_carry_free_text_ownership(self):
        assert "owner" not in ScopedControlUpdate.model_fields
        assert "assigned_to" not in ScopedControlUpdate.model_fields
        assert "owner" not in BatchScopedControlOperation.model_fields
        assert "assigned_to" not in BatchScopedControlOperation.model_fields

    def test_a_populated_control_owner_still_reads_back(self):
        """Load-bearing, and the reason is not sentimental about history.

        The column is counted by a live ownership metric. Removing it from the
        read schema as well would take that metric to zero for every
        organisation -- turning a wrong number into an unfixable one.
        """
        assert "owner" in ScopedControlResponse.model_fields

        control = ScopedControlResponse(
            id=uuid4(),
            organization_id=uuid4(),
            scf_id="XXX-00",
            owner="Recorded before the rule changed",
            created_at="2020-01-01T00:00:00Z",
            updated_at="2020-01-01T00:00:00Z",
        )
        assert control.owner == "Recorded before the rule changed"

    def test_the_control_column_survives(self):
        from models import ScopedControl

        assert hasattr(ScopedControl, "owner")
        assert hasattr(ScopedControl, "assigned_to")

    def test_a_control_update_can_no_longer_carry_an_owner(self):
        update = ScopedControlUpdate(owner="someone", assigned_to="someone")
        assert "owner" not in update.model_dump(exclude_unset=True)
        assert "assigned_to" not in update.model_dump(exclude_unset=True)
