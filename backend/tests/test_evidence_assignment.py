"""Evidence assignment reaches the columns the schedulers read (#781).

Two things are pinned here, and they are different failures with the same cause.

1. **The bug.** `evidence_tracking.owner` was free text and the only assignment
   control the UI offered. The task generator, the due-date notifier and the work
   queue all read `assigned_user_id` / `owner_user_id`, which no endpoint wrote.
   Every generated task was therefore unassigned, unnotified and absent from every
   user's queue. The tests below assign *through the API contract* and assert the
   generated task carries an assignee.

2. **The hazard the fix introduces.** Exposing a user id on a write path means the
   caller now chooses which user id is stored. Verifying only that the user
   *exists* — which is all three pre-existing sites did — lets an editor bind
   another tenant's account to their evidence, whereupon that account receives
   notifications and work-queue rows naming this org's evidence IDs. Every write
   path that accepts a user id is checked for org membership here.

Mock-based, no database, mirroring tests/test_evidence_tasks_tenancy.py.
"""
import inspect
import os
import sys
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException  # noqa: E402

import catalog_models  # noqa: E402,F401 — registers mappers referenced by models.System
from auth import OrgMembership  # noqa: E402
from schemas import (  # noqa: E402
    BatchEvidenceTrackingOperation,
    BatchEvidenceTrackingRequest,
    EvidenceCollectionTaskCreate,
    EvidenceCollectionTaskUpdate,
    EvidenceTrackingUpdate,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeResult:
    def __init__(self, value):
        self._value = value

    @property
    def rowcount(self):
        """Rows an UPDATE touched. Scripted as an int; anything else means 0."""
        return self._value if isinstance(self._value, int) else 0

    def first(self):
        return self._value

    def scalar_one_or_none(self):
        return self._value

    def scalar_one(self):
        return self._value

    def fetchall(self):
        return list(self._value or [])

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

    async def execute(self, statement, params=None):
        self.statements.append(statement)
        value = self._results.pop(0) if self._results else None
        return FakeResult(value)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True

    async def flush(self):
        pass

    async def refresh(self, obj):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def membership(org_id, user, role="editor"):
    return OrgMembership(
        user=user, organization_id=org_id, role=role, is_consultant=False
    )


@pytest.fixture
def org_a():
    return uuid4()


@pytest.fixture
def org_b():
    return uuid4()


@pytest.fixture
def caller():
    user = MagicMock()
    user.db_id = str(uuid4())
    user.email = "editor@org-a.example"
    user.auth_method = "oidc"
    return user


# ---------------------------------------------------------------------------
# 1. The guard itself
# ---------------------------------------------------------------------------

class TestAssertUserInOrg:

    @pytest.mark.asyncio
    async def test_direct_member_passes(self, org_a):
        from auth import assert_user_in_org

        db = FakeSession([uuid4()])  # membership row found
        await assert_user_in_org(uuid4(), org_a, db)  # must not raise

    @pytest.mark.asyncio
    async def test_active_consultant_passes(self, org_a):
        """A consultant is not in GET /members but can legitimately own work."""
        from auth import assert_user_in_org

        db = FakeSession([None, uuid4()])  # no member row, active relationship
        await assert_user_in_org(uuid4(), org_a, db)

    @pytest.mark.asyncio
    async def test_stranger_is_404(self, org_a):
        from auth import assert_user_in_org

        db = FakeSession([None, None])
        with pytest.raises(HTTPException) as exc:
            await assert_user_in_org(uuid4(), org_a, db)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_message_does_not_reveal_whether_the_user_exists(self, org_a):
        """404 with a constant message — otherwise this is an account oracle."""
        from auth import assert_user_in_org

        db = FakeSession([None, None])
        with pytest.raises(HTTPException) as exc:
            await assert_user_in_org(uuid4(), org_a, db)
        detail = exc.value.detail.lower()
        assert "not found in this organisation" in detail
        assert "exists" not in detail


# ---------------------------------------------------------------------------
# 2. Evidence tracking write paths
# ---------------------------------------------------------------------------

class TestEvidenceTrackingPatch:

    @pytest.mark.asyncio
    async def test_assigning_a_member_is_accepted(self, org_a, caller):
        from api.evidence_tracking import update_evidence_tracking

        assignee = uuid4()
        tracking = MagicMock()
        tracking.id = uuid4()
        tracking.assigned_user_id = None
        tracking.owner_user_id = None

        # 1: load tracking, 2: membership lookup, 3: propagate-to-open-tasks
        # UPDATE (2 rows), 4: reload with relationships
        db = FakeSession([tracking, uuid4(), 2, tracking])

        result = await update_evidence_tracking(
            org_id=org_a,
            evidence_id="E-HRS-16",
            tracking_update=EvidenceTrackingUpdate(assigned_user_id=assignee),
            membership=membership(org_a, caller),
            db=db,
        )

        assert tracking.assigned_user_id == assignee
        assert db.committed is True
        assert result is tracking

    @pytest.mark.asyncio
    async def test_assigning_a_stranger_is_refused_and_nothing_is_written(
        self, org_a, caller
    ):
        from api.evidence_tracking import update_evidence_tracking

        tracking = MagicMock()
        tracking.id = uuid4()
        tracking.assigned_user_id = None
        tracking.owner_user_id = None
        # 1: load tracking, 2+3: membership lookups both empty
        db = FakeSession([tracking, None, None])

        with pytest.raises(HTTPException) as exc:
            await update_evidence_tracking(
                org_id=org_a,
                evidence_id="E-HRS-16",
                tracking_update=EvidenceTrackingUpdate(assigned_user_id=uuid4()),
                membership=membership(org_a, caller),
                db=db,
            )

        assert exc.value.status_code == 404
        assert db.committed is False

    @pytest.mark.asyncio
    async def test_unassigning_needs_no_lookup(self, org_a, caller):
        """Explicit null clears the field; it must not be treated as a stranger."""
        from api.evidence_tracking import update_evidence_tracking

        tracking = MagicMock()
        tracking.id = uuid4()
        # No membership lookup consumed. Unassigning still runs the
        # propagation call, which short-circuits to 0 without a statement only if
        # BOTH assignment columns are None — owner_user_id is, here.
        db = FakeSession([tracking, tracking])

        await update_evidence_tracking(
            org_id=org_a,
            evidence_id="E-HRS-16",
            tracking_update=EvidenceTrackingUpdate(assigned_user_id=None),
            membership=membership(org_a, caller),
            db=db,
        )

        assert tracking.assigned_user_id is None
        assert db.committed is True

    @pytest.mark.asyncio
    async def test_untouched_assignment_is_not_looked_up_or_cleared(
        self, org_a, caller
    ):
        """A PATCH of an unrelated field must leave the assignee entirely alone.

        This endpoint is called with the whole tracking object on every field
        edit, so 'field absent' and 'field null' have to stay distinguishable.
        """
        from api.evidence_tracking import update_evidence_tracking

        tracking = MagicMock()
        tracking.id = uuid4()
        db = FakeSession([tracking, tracking])

        await update_evidence_tracking(
            org_id=org_a,
            evidence_id="E-HRS-16",
            tracking_update=EvidenceTrackingUpdate(comments="unrelated edit"),
            membership=membership(org_a, caller),
            db=db,
        )

        assert "assigned_user_id" not in EvidenceTrackingUpdate(
            comments="unrelated edit"
        ).model_dump(exclude_unset=True)
        assert db.committed is True


def _serialisable_tracking(org_id):
    """A tracking row real enough for EvidenceTrackingResponse to validate.

    A MagicMock cannot be used here: the batch endpoint serialises what it
    returns, and every attribute of a mock is another mock, which fails every
    field type.
    """
    from types import SimpleNamespace

    return SimpleNamespace(
        id=uuid4(),
        organization_id=org_id,
        evidence_id="E-OK-01",
        is_tracked=True,
        method_of_collection=None,
        collecting_system=None,
        owner=None,
        assigned_user_id=None,
        owner_user_id=None,
        assigned_user=None,
        owner_user=None,
        frequency="quarterly",
        comments="fine",
        maturity_level=None,
        system_id=None,
        system=None,
        file_count=0,
        created_at=datetime(2026, 8, 23, 12, 0, 0),
        updated_at=datetime(2026, 8, 23, 12, 0, 0),
    )


class TestStaleAssigneeDoesNotBlockEdits:
    """The trap this guard could have walked into.

    The web client re-sends the WHOLE tracking object on every field edit. If the
    guard validated the stored assignee rather than the changed one, then the
    moment that person left the org, editing the comments on their evidence would
    fail on a field the operator never touched — and the failure surfaces as a
    silently reverted save, because the client logs the error and keeps its
    optimistic state.
    """

    @pytest.mark.asyncio
    async def test_resending_an_unchanged_assignee_is_not_revalidated(
        self, org_a, caller
    ):
        from api.evidence_tracking import update_evidence_tracking

        departed = uuid4()
        tracking = MagicMock()
        tracking.id = uuid4()
        tracking.assigned_user_id = departed  # already stored, no longer a member
        tracking.owner_user_id = None

        # load, propagate UPDATE (0 rows), reload. A membership lookup would
        # consume a slot and return None, producing a 404 — there is none.
        db = FakeSession([tracking, 0, tracking])

        await update_evidence_tracking(
            org_id=org_a,
            evidence_id="E-HRS-16",
            tracking_update=EvidenceTrackingUpdate(
                comments="edited something else", assigned_user_id=departed
            ),
            membership=membership(org_a, caller),
            db=db,
        )

        assert tracking.comments == "edited something else"
        assert tracking.assigned_user_id == departed
        assert db.committed is True

    @pytest.mark.asyncio
    async def test_changing_to_a_stranger_is_still_refused(self, org_a, caller):
        from api.evidence_tracking import update_evidence_tracking

        tracking = MagicMock()
        tracking.id = uuid4()
        tracking.assigned_user_id = uuid4()
        tracking.owner_user_id = None

        db = FakeSession([tracking, None, None])

        with pytest.raises(HTTPException) as exc:
            await update_evidence_tracking(
                org_id=org_a,
                evidence_id="E-HRS-16",
                tracking_update=EvidenceTrackingUpdate(assigned_user_id=uuid4()),
                membership=membership(org_a, caller),
                db=db,
            )

        assert exc.value.status_code == 404
        assert db.committed is False


class TestPropagationToOpenTasks:
    """Assigning evidence has to reach tasks that ALREADY exist (#781).

    task_generator stamps an assignee once, at creation, and its duplicate-window
    check stops it revisiting a task it already made. Without propagation the fix
    would only help a future collection period, and an org that assigned all its
    evidence today would still open an empty work queue tomorrow.
    """

    @pytest.mark.asyncio
    async def test_open_unassigned_tasks_are_stamped(self, org_a, caller):
        from api.evidence_tracking import update_evidence_tracking
        from sqlalchemy.dialects import postgresql

        assignee = uuid4()
        tracking = MagicMock()
        tracking.id = uuid4()
        tracking.evidence_id = "E-HRS-16"
        tracking.assigned_user_id = None
        tracking.owner_user_id = None

        db = FakeSession([tracking, uuid4(), 3, tracking])

        await update_evidence_tracking(
            org_id=org_a,
            evidence_id="E-HRS-16",
            tracking_update=EvidenceTrackingUpdate(assigned_user_id=assignee),
            membership=membership(org_a, caller),
            db=db,
        )

        updates = [
            st
            for st in db.statements
            if "UPDATE evidence_collection_tasks" in str(
                st.compile(dialect=postgresql.dialect())
            )
        ]
        assert len(updates) == 1
        sql = str(updates[0].compile(dialect=postgresql.dialect()))
        # Never clobber a per-task assignee a person set deliberately, and never
        # touch history.
        assert "assigned_user_id IS NULL" in sql
        assert "status !=" in sql

    @pytest.mark.asyncio
    async def test_no_update_is_issued_when_there_is_no_assignee(
        self, org_a, caller
    ):
        """Unassigning must not fire a pointless UPDATE ... SET NULL over tasks."""
        from api.evidence_tracking import update_evidence_tracking
        from sqlalchemy.dialects import postgresql

        tracking = MagicMock()
        tracking.id = uuid4()
        tracking.evidence_id = "E-HRS-16"
        tracking.assigned_user_id = None
        tracking.owner_user_id = None

        db = FakeSession([tracking, tracking])

        await update_evidence_tracking(
            org_id=org_a,
            evidence_id="E-HRS-16",
            tracking_update=EvidenceTrackingUpdate(assigned_user_id=None),
            membership=membership(org_a, caller),
            db=db,
        )

        assert not [
            st
            for st in db.statements
            if "UPDATE evidence_collection_tasks" in str(
                st.compile(dialect=postgresql.dialect())
            )
        ]

    @pytest.mark.asyncio
    async def test_unrelated_edit_does_not_touch_tasks(self, org_a, caller):
        from api.evidence_tracking import update_evidence_tracking
        from sqlalchemy.dialects import postgresql

        tracking = MagicMock()
        tracking.id = uuid4()
        tracking.evidence_id = "E-HRS-16"
        tracking.assigned_user_id = uuid4()
        tracking.owner_user_id = None

        db = FakeSession([tracking, tracking])

        await update_evidence_tracking(
            org_id=org_a,
            evidence_id="E-HRS-16",
            tracking_update=EvidenceTrackingUpdate(comments="just a note"),
            membership=membership(org_a, caller),
            db=db,
        )

        assert not [
            st
            for st in db.statements
            if "UPDATE evidence_collection_tasks" in str(
                st.compile(dialect=postgresql.dialect())
            )
        ]


class TestEvidenceTrackingBatch:

    @pytest.mark.asyncio
    async def test_one_bad_assignee_fails_only_its_own_operation(
        self, org_a, caller
    ):
        """A 500-row import must not be lost to one bad user id."""
        from api.evidence_tracking import batch_update_evidence_tracking

        good = _serialisable_tracking(org_a)
        http_request = MagicMock()
        caller.db_id = str(uuid4())

        # op1: existing-row lookup -> None, then 2 empty membership lookups
        #      -> ValueError, so the op fails on its own
        # op2: existing-row lookup -> good, no assignee to validate
        # then: final reload of result_evidence
        db = FakeSession([None, None, None, good, [good]])

        request = BatchEvidenceTrackingRequest(
            operations=[
                BatchEvidenceTrackingOperation(
                    evidence_id="E-BAD-01", assigned_user_id=uuid4()
                ),
                BatchEvidenceTrackingOperation(
                    evidence_id="E-OK-01", comments="fine"
                ),
            ]
        )

        with patch("api.evidence_tracking.log_entity_changes", new=_noop_audit):
            response = await batch_update_evidence_tracking(
                org_id=org_a,
                request=request,
                http_request=http_request,
                membership=membership(org_a, caller),
                db=db,
            )

        assert response.failed == 1
        assert response.updated == 1
        assert any("E-BAD-01" in e for e in response.errors)


async def _noop_audit(**kwargs):
    return None


# ---------------------------------------------------------------------------
# 3. The sibling write paths that had the same hole
# ---------------------------------------------------------------------------

class TestSiblingWritePaths:

    @pytest.mark.asyncio
    async def test_task_create_refuses_a_stranger(self, org_a, caller):
        from api.evidence_tasks import create_evidence_task

        evidence = MagicMock()
        evidence.id = uuid4()
        evidence.organization_id = org_a

        db = FakeSession([None, None])  # both membership lookups empty

        async def _resolve(evidence_tracking_id, current_user, db_, min_role="viewer"):
            return evidence

        with patch("api.evidence_tasks._resolve_evidence_access", new=_resolve):
            with pytest.raises(HTTPException) as exc:
                await create_evidence_task(
                    task_data=EvidenceCollectionTaskCreate(
                        evidence_tracking_id=evidence.id,
                        due_date=date(2026, 9, 1),
                        title="Collect Evidence: E-HRS-16",
                        assigned_user_id=uuid4(),
                    ),
                    db=db,
                    current_user=caller,
                )

        assert exc.value.status_code == 404
        assert db.committed is False

    @pytest.mark.asyncio
    async def test_task_update_refuses_a_stranger(self, org_a, caller):
        """Reassignment had no validation of any kind before #781."""
        from api.evidence_tasks import update_evidence_task

        task = MagicMock()
        task.id = uuid4()
        task.evidence_tracking_id = uuid4()
        original_assignee = uuid4()
        task.assigned_user_id = original_assignee

        # org lookup, then two empty membership lookups
        db = FakeSession([org_a, None, None])

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

        assert exc.value.status_code == 404
        assert task.assigned_user_id == original_assignee
        assert db.committed is False

    @pytest.mark.asyncio
    async def test_task_update_does_not_revalidate_an_unchanged_assignee(
        self, org_a, caller
    ):
        """TaskEditModal re-sends assigned_user_id on every save (#781)."""
        from api.evidence_tasks import update_evidence_task

        departed = uuid4()
        task = MagicMock()
        task.id = uuid4()
        task.evidence_tracking_id = uuid4()
        task.assigned_user_id = departed

        # Nothing scripted: any membership lookup would return None -> 404.
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
    async def test_assignment_create_refuses_a_stranger(self, org_a, caller):
        from api.assignments import create_assignment
        from schemas import AssignmentCreate

        evidence = MagicMock()
        evidence.id = uuid4()
        evidence.organization_id = org_a

        # user exists, assignable found, then two empty membership lookups
        db = FakeSession([MagicMock(), evidence, None, None])

        async def _accessible(user, db_):
            return [org_a]

        with patch("api.assignments.get_accessible_org_ids", new=_accessible):
            with pytest.raises(HTTPException) as exc:
                await create_assignment(
                    request=MagicMock(),
                    assignment_data=AssignmentCreate(
                        assignable_type="evidence",
                        assignable_id=evidence.id,
                        user_id=uuid4(),
                    ),
                    db=db,
                    current_user=caller,
                )

        assert exc.value.status_code == 404
        assert db.committed is False


# ---------------------------------------------------------------------------
# 4. The reported symptom: does the generated task carry an assignee?
# ---------------------------------------------------------------------------

def _tracked_evidence(**overrides):
    evidence = MagicMock()
    evidence.id = uuid4()
    evidence.evidence_id = "E-HRS-16"
    evidence.frequency = "quarterly"
    evidence.is_tracked = True
    evidence.last_collection_date = None
    evidence.assigned_user_id = None
    evidence.owner_user_id = None
    evidence.owner = None
    for key, value in overrides.items():
        setattr(evidence, key, value)
    return evidence


async def _run_generator(evidence):
    """Run the generator over exactly one evidence record."""
    from services import task_generator

    # 1: the evidence query, 2: the duplicate-task lookup
    db = FakeSession([[evidence], None])

    class _SessionFactory:
        def __call__(self):
            return db

    with patch.object(task_generator, "AsyncSessionLocal", _SessionFactory()):
        await task_generator.generate_evidence_tasks()

    return db


class TestGeneratedTaskAssignment:

    @pytest.mark.asyncio
    async def test_assigned_user_id_reaches_the_task(self):
        """The regression the issue asked for: assign, generate, assert assignee."""
        assignee = uuid4()
        db = await _run_generator(_tracked_evidence(assigned_user_id=assignee))

        tasks = [t for t in db.added if hasattr(t, "assigned_user_id")]
        assert len(tasks) == 1
        assert tasks[0].assigned_user_id == assignee

    @pytest.mark.asyncio
    async def test_owner_user_id_is_used_when_no_explicit_assignee(self):
        owner = uuid4()
        db = await _run_generator(_tracked_evidence(owner_user_id=owner))

        tasks = [t for t in db.added if hasattr(t, "assigned_user_id")]
        assert len(tasks) == 1
        assert tasks[0].assigned_user_id == owner

    @pytest.mark.asyncio
    async def test_assigned_user_id_wins_over_owner_user_id(self):
        assignee, owner = uuid4(), uuid4()
        db = await _run_generator(
            _tracked_evidence(assigned_user_id=assignee, owner_user_id=owner)
        )

        tasks = [t for t in db.added if hasattr(t, "assigned_user_id")]
        assert tasks[0].assigned_user_id == assignee

    @pytest.mark.asyncio
    async def test_unassigned_still_generates_and_says_so(self, caplog):
        """An unassigned task is inert, so the log has to name it (#781).

        Before this, every task was unassigned and nothing said so — the only
        symptom was an empty work queue, which reads as 'no work', not 'broken'.
        """
        import logging

        with caplog.at_level(logging.INFO, logger="services.task_generator"):
            db = await _run_generator(_tracked_evidence())

        tasks = [t for t in db.added if hasattr(t, "assigned_user_id")]
        assert len(tasks) == 1
        assert tasks[0].assigned_user_id is None
        assert any(
            "UNASSIGNED" in r.message and "E-HRS-16" in r.message
            for r in caplog.records
        )


# ---------------------------------------------------------------------------
# 5. The write contract exposes what the readers read
# ---------------------------------------------------------------------------

class TestSchemaContract:

    def test_update_schema_exposes_both_columns(self):
        fields = set(EvidenceTrackingUpdate.model_fields)
        assert {"assigned_user_id", "owner_user_id"} <= fields

    def test_batch_schema_exposes_both_columns(self):
        fields = set(BatchEvidenceTrackingOperation.model_fields)
        assert {"assigned_user_id", "owner_user_id"} <= fields

    def test_response_exposes_both_columns_and_resolved_users(self):
        from schemas import EvidenceTrackingResponse

        fields = set(EvidenceTrackingResponse.model_fields)
        assert {
            "assigned_user_id",
            "owner_user_id",
            "assigned_user",
            "owner_user",
        } <= fields

    def test_reassignment_is_audited(self):
        """Who owns a piece of evidence is a governance fact, not a UI preference."""
        from services.audit_service import EVIDENCE_TRACKING_TRACKED_FIELDS

        assert {"assigned_user_id", "owner_user_id"} <= EVIDENCE_TRACKING_TRACKED_FIELDS


# ---------------------------------------------------------------------------
# 6. The free-text owner is gone from the contract
#
# It was kept for one revision as a "team or external party" label and that was
# overruled: a second, unstructured way to answer "who owns this" is what let
# the original defect survive — people filled in the box, believed they had
# assigned the work, and nothing downstream ever read it. The COLUMN stays
# (dropping it would destroy labels that never resolved to a user, and that is
# not reversible); the API contract does not.
# ---------------------------------------------------------------------------

class TestFreeTextOwnerIsGone:

    def test_write_schemas_do_not_accept_it(self):
        from schemas import (
            BatchEvidenceTrackingOperation,
            EvidenceTrackingBase,
            EvidenceTrackingUpdate,
        )

        for schema in (
            EvidenceTrackingBase,
            EvidenceTrackingUpdate,
            BatchEvidenceTrackingOperation,
        ):
            assert "owner" not in schema.model_fields, schema.__name__

    def test_response_does_not_return_it(self):
        from schemas import EvidenceTrackingResponse

        assert "owner" not in EvidenceTrackingResponse.model_fields

    def test_the_column_survives_so_legacy_labels_are_not_destroyed(self):
        from models import EvidenceTracking

        assert hasattr(EvidenceTracking, "owner")

    def test_reconciliation_still_carries_it_across_a_catalog_upgrade(self):
        """A label nobody can edit any more is still not ours to bin."""
        from services.reconciliation_service import _MIGRATED_EVIDENCE_STATE_FIELDS

        assert "owner" in _MIGRATED_EVIDENCE_STATE_FIELDS


class TestOwnerRendersAsAPerson:

    def test_user_label_prefers_display_name(self):
        from user_display import user_label

        assert user_label(SimpleNamespace(display_name="Ada L", email="a@b.com")) == "Ada L"

    def test_user_label_falls_back_to_email_when_blank(self):
        from user_display import user_label

        assert user_label(SimpleNamespace(display_name="   ", email="a@b.com")) == "a@b.com"
        assert user_label(SimpleNamespace(display_name=None, email="a@b.com")) == "a@b.com"

    def test_user_label_of_nobody_is_nobody(self):
        from user_display import user_label

        assert user_label(None) is None

    def test_task_payloads_resolve_the_owner_user(self):
        """The `owner` key in the task payloads is a resolved person now."""
        import api.evidence_tasks as evidence_tasks

        src = inspect.getsource(evidence_tasks)
        assert "_user_label(evidence.owner_user)" in src
        assert "evidence.owner," not in src

    def test_generated_schedule_resolves_the_owner_user(self):
        import services.doc_gen.context as context

        src = inspect.getsource(context)
        assert 'user_label(et.owner_user)' in src
        assert '"owner": et.owner' not in src
