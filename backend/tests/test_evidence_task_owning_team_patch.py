"""``PATCH /api/evidence-tasks/{task_id}`` and ``owning_team_id`` (#822 phase 4).

The field is additive, but it is not an ordinary field, because **null is a
value here rather than an absence**:

* omitted — leave the override as it stands;
* ``null`` — clear it, so the task inherits its parent evidence item's
  accountable team;
* a team id — override it.

Every other field on this model is applied with ``if x is not None``, an idiom
that cannot tell the second case from the first. This one is read from
Pydantic's ``model_fields_set`` instead, and that difference is what the first
class below exists to pin — including, crucially, that the *other* fields still
behave the old way, because changing them would be a contract break for every
client generated from the existing schema.

The rest covers what the endpoint refuses (a team from another tenant, a team
that has been archived), what it records (an audit row for every ownership
change, and none for a PATCH that changed no ownership), and what it declines
to consult (team membership, in any authorisation decision).

Mock-based, following ``tests/test_evidence_tasks_tenancy.py``, whose fakes are
reused. The database-level half of the same guarantee — that a cross-tenant
``owning_team_id`` is *unrepresentable* rather than merely refused — is
``fk_evidence_collection_tasks_team_org``, added by the migration
``20260824_160000_evidence_task_org_and_owning_team.py``. The 400 asserted
below is the operator-facing half of that pair, not a substitute for it.
"""
import os
import sys
from datetime import date
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException  # noqa: E402

import catalog_models  # noqa: E402,F401 — registers mappers referenced by models.System
from models import AuditLog  # noqa: E402
from schemas import EvidenceCollectionTaskUpdate  # noqa: E402
from tests.test_evidence_tasks_tenancy import (  # noqa: E402
    FakeSession,
    membership_gate,
)


@pytest.fixture
def org_id():
    return uuid4()


@pytest.fixture
def team_id():
    return uuid4()


@pytest.fixture
def caller():
    user = MagicMock()
    user.db_id = str(uuid4())
    user.email = "editor@example.invalid"
    user.auth_method = "oidc"
    return user


@pytest.fixture
def task(org_id, team_id):
    """A task that already has an owning team, so clearing it is observable."""
    task = MagicMock()
    task.id = uuid4()
    task.evidence_tracking_id = uuid4()
    task.organization_id = org_id
    task.owning_team_id = team_id
    task.assigned_user_id = None
    task.task_type = "collection"
    task.title = "Quarterly access review"
    task.description = None
    task.priority = "medium"
    task.due_date = date(2026, 1, 1)
    task.status = "not_started"
    task.completed_date = None
    task.completion_notes = None
    task.dependencies = []
    task.attachments = []
    task.auto_generated = True
    task.created_at = None
    return task


def _team(org_id, *, active=True):
    team = MagicMock()
    team.id = uuid4()
    team.organization_id = org_id
    team.name = "Security Operations"
    team.is_active = active
    return team


async def _patch(task, update, caller, org_id, *, results):
    """Call the endpoint as a same-org editor, with a scripted session."""
    from api.evidence_tasks import update_evidence_task

    db = FakeSession([(task, org_id)] + list(results))
    with patch("api.evidence_tasks.verify_org_membership", membership_gate(org_id)):
        result = await update_evidence_task(
            task_id=task.id,
            task_update=update,
            db=db,
            current_user=caller,
        )
    return db, result


def _ownership_audit(db) -> dict:
    """``{field_name: (old, new)}`` for the ownership audit rows written."""
    return {
        row.field_name: (row.old_value, row.new_value)
        for row in db.added
        if isinstance(row, AuditLog)
    }


# ---------------------------------------------------------------------------
# Omitted, null, and set are three different things
# ---------------------------------------------------------------------------

class TestNullIsAValueNotAnAbsence:

    @pytest.mark.asyncio
    async def test_omitting_the_field_leaves_the_override_alone(
        self, task, caller, org_id, team_id
    ):
        """The compatibility case. Every client that exists today omits it."""
        update = EvidenceCollectionTaskUpdate(status="in_progress")

        db, _ = await _patch(task, update, caller, org_id, results=[None])

        assert task.owning_team_id == team_id
        assert task.status == "in_progress"
        assert db.committed is True

    @pytest.mark.asyncio
    async def test_an_explicit_null_clears_the_override(self, task, caller, org_id):
        """Back to inheriting the parent evidence item's accountable team.

        This is the case ``if x is not None`` cannot express, and the reason
        this one field is read from ``model_fields_set``.
        """
        update = EvidenceCollectionTaskUpdate(owning_team_id=None)

        db, _ = await _patch(task, update, caller, org_id, results=[None])

        assert task.owning_team_id is None
        assert db.committed is True

    @pytest.mark.asyncio
    async def test_a_team_id_sets_the_override(self, task, caller, org_id):
        team = _team(org_id)
        update = EvidenceCollectionTaskUpdate(owning_team_id=team.id)

        db, _ = await _patch(task, update, caller, org_id, results=[team, None])

        assert task.owning_team_id == team.id
        assert db.committed is True

    @pytest.mark.asyncio
    async def test_clearing_it_runs_no_team_lookup(self, task, caller, org_id):
        """There is no team to validate, so there is no query to make. A
        lookup here would 400 on ``null`` — the opposite of what it means."""
        update = EvidenceCollectionTaskUpdate(owning_team_id=None)

        db, _ = await _patch(task, update, caller, org_id, results=[None])

        # One statement only: the access check in _resolve_task_access.
        assert len(db.statements) == 1

    @pytest.mark.asyncio
    async def test_the_other_fields_keep_the_none_means_omitted_contract(
        self, task, caller, org_id
    ):
        """Explicitly sending ``null`` for any other field is still a no-op.

        Changing that would silently turn every partial update sent by an
        existing client into a wipe of the fields it did not mention.
        """
        task.status = "in_progress"
        task.priority = "high"
        update = EvidenceCollectionTaskUpdate(status=None, priority=None)

        await _patch(task, update, caller, org_id, results=[None])

        assert task.status == "in_progress"
        assert task.priority == "high"


# ---------------------------------------------------------------------------
# What it refuses
# ---------------------------------------------------------------------------

class TestTeamValidation:

    @pytest.mark.asyncio
    async def test_a_team_from_another_organisation_is_refused(
        self, task, caller, org_id, team_id
    ):
        """Reported as not found, not as forbidden.

        The other tenant's team existing is not this caller's to learn. The
        composite foreign key would refuse the write anyway; this lookup is the
        difference between a 400 an operator can act on and a 500 from a
        constraint violation.
        """
        update = EvidenceCollectionTaskUpdate(owning_team_id=uuid4())

        # The lookup is scoped to the task's organisation, so a foreign team
        # simply is not found.
        with pytest.raises(HTTPException) as exc:
            await _patch(task, update, caller, org_id, results=[None, None])

        assert exc.value.status_code == 400
        assert exc.value.detail == "Owning team not found in this organisation"
        assert task.owning_team_id == team_id

    @pytest.mark.asyncio
    async def test_an_archived_team_cannot_take_new_work(
        self, task, caller, org_id, team_id
    ):
        """Archiving is not deleting: the team keeps what it already holds and
        stops collecting more. Without this, an organisation tidying up its
        structure would route fresh work to a team it had stood down."""
        update = EvidenceCollectionTaskUpdate(owning_team_id=_team(org_id, active=False).id)

        with pytest.raises(HTTPException) as exc:
            await _patch(
                task, update, caller, org_id,
                results=[_team(org_id, active=False), None],
            )

        assert exc.value.status_code == 400
        assert exc.value.detail == "Owning team is archived and cannot take new work"
        assert task.owning_team_id == team_id

    @pytest.mark.asyncio
    async def test_a_refusal_writes_nothing(self, task, caller, org_id):
        update = EvidenceCollectionTaskUpdate(
            owning_team_id=uuid4(), status="completed",
        )

        with pytest.raises(HTTPException):
            db, _ = await _patch(task, update, caller, org_id, results=[None, None])

        # The status change rides in the same request and must not land either.
        assert task.status == "not_started"


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

class TestEveryOwnershipChangeIsAudited:
    """#822 invariant 6.

    Team membership is mutable and unversioned, so without an audit row the
    question "who owned this task in March?" has no answer — today's membership
    silently overwrites March's.
    """

    @pytest.mark.asyncio
    async def test_clearing_the_team_is_audited(self, task, caller, org_id, team_id):
        update = EvidenceCollectionTaskUpdate(owning_team_id=None)

        db, _ = await _patch(task, update, caller, org_id, results=[None])

        audit = _ownership_audit(db)
        assert "owning_team_id" in audit
        old, new = audit["owning_team_id"]
        assert str(team_id) in old
        assert new == "null"

    @pytest.mark.asyncio
    async def test_reassigning_a_person_is_audited(self, task, caller, org_id):
        assignee = uuid4()
        update = EvidenceCollectionTaskUpdate(assigned_user_id=assignee)

        with patch("api.evidence_tasks.assert_user_in_org", _noop_membership()):
            db, _ = await _patch(
                task, update, caller, org_id, results=[org_id, None],
            )

        audit = _ownership_audit(db)
        assert "assigned_user_id" in audit
        assert str(assignee) in audit["assigned_user_id"][1]

    @pytest.mark.asyncio
    async def test_a_patch_that_changes_no_ownership_writes_no_ownership_rows(
        self, task, caller, org_id
    ):
        """Editing a title is not an ownership event.

        ``log_entity_changes`` emits nothing when nothing changed, so this is
        free — but it is the property that keeps the audit trail readable.
        """
        update = EvidenceCollectionTaskUpdate(title="Renamed")

        db, _ = await _patch(task, update, caller, org_id, results=[None])

        assert _ownership_audit(db) == {}

    @pytest.mark.asyncio
    async def test_the_audit_row_is_written_before_the_commit(
        self, task, caller, org_id
    ):
        """``log_entity_changes`` adds to the session and does not commit, so
        the change and the row describing it are one transaction or neither."""
        import inspect

        from api.evidence_tasks import update_evidence_task

        source = inspect.getsource(update_evidence_task)
        assert source.index("log_entity_changes(") < source.index("db.commit()")


def _noop_membership():
    async def _assert(user_id, org_id, db):
        return None

    return _assert


# ---------------------------------------------------------------------------
# Teams grant no permissions
# ---------------------------------------------------------------------------

class TestTeamsGrantNoPermissions:
    """#822 invariant 4. RBAC stays on ``organization_members.role``.

    Being somebody's team primary must not grant a capability their
    organisation role denies, and the way that invariant fails in practice is
    not a deliberate decision — it is a membership lookup drifting into an
    authorisation branch. So this reads the handler's source.
    """

    def test_the_handler_consults_no_team_membership(self):
        import inspect

        from api.evidence_tasks import update_evidence_task

        source = inspect.getsource(update_evidence_task)
        assert "TeamMember" not in source
        assert "membership_role" not in source

    def test_the_role_it_requires_is_unchanged(self):
        """Editor, as it already was. Assigning a task to a team is an edit of
        the task, not a new privilege tier."""
        import inspect

        from api.evidence_tasks import update_evidence_task

        source = inspect.getsource(update_evidence_task)
        assert '_resolve_task_access(task_id, current_user, db, "editor")' in source
