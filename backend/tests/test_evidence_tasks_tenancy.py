"""
Cross-tenant isolation tests for the Evidence Tasks API.

Evidence collection tasks carry no organization column of their own — they
inherit tenancy from the parent EvidenceTracking row. These tests pin that
every route resolves that parent organisation and refuses callers who are not
members, while leaving legitimate same-org callers untouched.

Uses unittest.mock — no database required (mirrors tests/test_evidence_files_api.py).
"""
import pytest
from unittest.mock import MagicMock, patch
from uuid import uuid4
from datetime import date

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException  # noqa: E402
from sqlalchemy.dialects import postgresql  # noqa: E402

import catalog_models  # noqa: E402,F401 — registers mappers referenced by models.System
from auth import OrgMembership  # noqa: E402
from schemas import EvidenceCollectionTaskCreate, EvidenceCollectionTaskUpdate  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeResult:
    """Stand-in for a SQLAlchemy Result over a single scripted value."""

    def __init__(self, value):
        self._value = value

    def first(self):
        return self._value

    def scalar_one_or_none(self):
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
    """Async session stub replaying scripted results and recording statements."""

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


def bind_values(statement):
    """Every bound parameter value in a statement, as strings.

    IN-clause parameters bind as a list, so those are flattened out.
    """
    compiled = statement.compile(dialect=postgresql.dialect())
    values = set()
    for value in compiled.params.values():
        if isinstance(value, (list, tuple)):
            values.update(str(item) for item in value)
        else:
            values.add(str(value))
    return values


def sql_text(statement):
    return str(statement.compile(dialect=postgresql.dialect()))


def accessible_orgs(*org_ids):
    """An async get_accessible_org_ids stub."""
    async def _accessible(user, db):
        return list(org_ids)

    return _accessible


def membership_gate(*member_org_ids, role="editor"):
    """A verify_org_membership stub that behaves like the real one.

    Non-members get a 403, exactly as auth.verify_org_membership raises.
    """
    async def _verify(org_id, user, db, min_role="viewer"):
        if org_id not in member_org_ids:
            raise HTTPException(status_code=403, detail="Access denied")
        return OrgMembership(
            user=user, organization_id=org_id, role=role, is_consultant=False
        )

    return _verify


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def org_a():
    return uuid4()


@pytest.fixture
def org_b():
    return uuid4()


@pytest.fixture
def caller():
    """Authenticated user who belongs to org B only."""
    user = MagicMock()
    user.db_id = str(uuid4())
    user.email = "attacker@org-b.example"
    user.auth_method = "oidc"
    return user


@pytest.fixture
def victim_task():
    """A task owned by org A."""
    task = MagicMock()
    task.id = uuid4()
    task.evidence_tracking_id = uuid4()
    task.task_type = "collection"
    task.title = "Org A quarterly access review"
    task.description = None
    task.priority = "medium"
    task.due_date = date(2026, 1, 1)
    task.status = "not_started"
    task.assigned_user_id = None
    task.completed_date = None
    task.completion_notes = None
    task.dependencies = []
    task.attachments = []
    task.auto_generated = True
    task.created_at = None
    return task


# ---------------------------------------------------------------------------
# PATCH /api/evidence-tasks/{task_id}
# ---------------------------------------------------------------------------

class TestUpdateEvidenceTask:

    @pytest.mark.asyncio
    async def test_cross_tenant_update_is_404_and_does_not_mutate(
        self, caller, victim_task, org_a, org_b
    ):
        """A caller in org B cannot PATCH an org A task — and nothing is written."""
        from api.evidence_tasks import update_evidence_task

        db = FakeSession([(victim_task, org_a)])
        update = EvidenceCollectionTaskUpdate(status="completed", assigned_user_id=uuid4())

        with patch("api.evidence_tasks.verify_org_membership", membership_gate(org_b)):
            with pytest.raises(HTTPException) as exc_info:
                await update_evidence_task(
                    task_id=victim_task.id,
                    task_update=update,
                    db=db,
                    current_user=caller,
                )

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Task not found"
        assert db.committed is False
        assert victim_task.status == "not_started"
        assert victim_task.assigned_user_id is None

    @pytest.mark.asyncio
    async def test_same_org_update_applies(self, caller, victim_task, org_a):
        """A member of the owning org can still update the task."""
        from api.evidence_tasks import update_evidence_task

        db = FakeSession([(victim_task, org_a), None])
        update = EvidenceCollectionTaskUpdate(status="in_progress", priority="high")

        with patch("api.evidence_tasks.verify_org_membership", membership_gate(org_a)):
            result = await update_evidence_task(
                task_id=victim_task.id,
                task_update=update,
                db=db,
                current_user=caller,
            )

        assert db.committed is True
        assert victim_task.status == "in_progress"
        assert victim_task.priority == "high"
        assert result["id"] == victim_task.id

    @pytest.mark.asyncio
    async def test_viewer_cannot_update(self, caller, victim_task, org_a):
        """Read-only members of the owning org get 403, not a silent write."""
        from api.evidence_tasks import update_evidence_task

        db = FakeSession([(victim_task, org_a)])
        update = EvidenceCollectionTaskUpdate(status="completed")

        with patch(
            "api.evidence_tasks.verify_org_membership",
            membership_gate(org_a, role="viewer"),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await update_evidence_task(
                    task_id=victim_task.id,
                    task_update=update,
                    db=db,
                    current_user=caller,
                )

        assert exc_info.value.status_code == 403
        assert db.committed is False
        assert victim_task.status == "not_started"

    @pytest.mark.asyncio
    async def test_due_date_is_updatable(self, caller, victim_task, org_a):
        """The edit modal exposes a due date — the PATCH must honour it."""
        from api.evidence_tasks import update_evidence_task

        db = FakeSession([(victim_task, org_a), None])
        update = EvidenceCollectionTaskUpdate(due_date=date(2026, 6, 30))

        with patch("api.evidence_tasks.verify_org_membership", membership_gate(org_a)):
            await update_evidence_task(
                task_id=victim_task.id,
                task_update=update,
                db=db,
                current_user=caller,
            )

        assert victim_task.due_date == date(2026, 6, 30)

    @pytest.mark.asyncio
    async def test_unknown_task_is_404(self, caller, org_b):
        from api.evidence_tasks import update_evidence_task

        db = FakeSession([None])

        with patch("api.evidence_tasks.verify_org_membership", membership_gate(org_b)):
            with pytest.raises(HTTPException) as exc_info:
                await update_evidence_task(
                    task_id=uuid4(),
                    task_update=EvidenceCollectionTaskUpdate(status="completed"),
                    db=db,
                    current_user=caller,
                )

        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/evidence-tasks/{task_id}/complete
# ---------------------------------------------------------------------------

class TestCompleteEvidenceTask:

    @pytest.mark.asyncio
    async def test_cross_tenant_complete_is_404(self, caller, victim_task, org_a, org_b):
        from api.evidence_tasks import complete_evidence_task

        db = FakeSession([(victim_task, org_a)])

        with patch("api.evidence_tasks.verify_org_membership", membership_gate(org_b)):
            with pytest.raises(HTTPException) as exc_info:
                await complete_evidence_task(
                    task_id=victim_task.id,
                    completion_notes="pwned",
                    db=db,
                    current_user=caller,
                )

        assert exc_info.value.status_code == 404
        assert db.committed is False
        assert victim_task.status == "not_started"

    @pytest.mark.asyncio
    async def test_same_org_complete_succeeds(self, caller, victim_task, org_a):
        from api.evidence_tasks import complete_evidence_task

        evidence = MagicMock()
        evidence.last_collection_date = None
        db = FakeSession([(victim_task, org_a), evidence, None])

        with patch("api.evidence_tasks.verify_org_membership", membership_gate(org_a)):
            result = await complete_evidence_task(
                task_id=victim_task.id,
                completion_notes="done",
                db=db,
                current_user=caller,
            )

        assert db.committed is True
        assert victim_task.status == "completed"
        assert result["completion_notes"] == "done"


# ---------------------------------------------------------------------------
# POST /api/evidence-tasks
# ---------------------------------------------------------------------------

class TestCreateEvidenceTask:

    @pytest.mark.asyncio
    async def test_cannot_create_task_against_other_org_evidence(
        self, caller, org_a, org_b
    ):
        """Creating a task on another org's evidence record is a 404."""
        from api.evidence_tasks import create_evidence_task

        evidence = MagicMock()
        evidence.id = uuid4()
        evidence.organization_id = org_a
        db = FakeSession([evidence])

        payload = EvidenceCollectionTaskCreate(
            evidence_tracking_id=evidence.id, due_date=date(2026, 1, 1)
        )

        with patch("api.evidence_tasks.verify_org_membership", membership_gate(org_b)):
            with pytest.raises(HTTPException) as exc_info:
                await create_evidence_task(
                    task_data=payload, db=db, current_user=caller
                )

        assert exc_info.value.status_code == 404
        assert db.added == []
        assert db.committed is False

    @pytest.mark.asyncio
    async def test_same_org_create_succeeds(self, caller, org_a):
        from api.evidence_tasks import create_evidence_task

        evidence = MagicMock()
        evidence.id = uuid4()
        evidence.organization_id = org_a
        db = FakeSession([evidence])

        payload = EvidenceCollectionTaskCreate(
            evidence_tracking_id=evidence.id, due_date=date(2026, 1, 1)
        )

        with patch("api.evidence_tasks.verify_org_membership", membership_gate(org_a)):
            await create_evidence_task(task_data=payload, db=db, current_user=caller)

        assert len(db.added) == 1
        assert db.committed is True


# ---------------------------------------------------------------------------
# GET /api/evidence-tasks
# ---------------------------------------------------------------------------

class TestListEvidenceTasks:

    @pytest.mark.asyncio
    async def test_query_is_scoped_to_accessible_orgs(self, caller, org_b):
        """The listing joins EvidenceTracking and filters on the caller's orgs."""
        from api.evidence_tasks import list_evidence_tasks

        db = FakeSession([[]])

        with patch(
            "api.evidence_tasks.get_accessible_org_ids", accessible_orgs(org_b)
        ):
            await list_evidence_tasks(
                status_filter=None,
                assigned_user_id=None,
                overdue_only=False,
                frameworks=None,
                db=db,
                current_user=caller,
            )

        assert len(db.statements) == 1
        statement = db.statements[0]
        assert "evidence_tracking" in sql_text(statement)
        assert "organization_id" in sql_text(statement)
        assert str(org_b) in bind_values(statement)

    @pytest.mark.asyncio
    async def test_no_accessible_orgs_returns_empty(self, caller):
        from api.evidence_tasks import list_evidence_tasks

        db = FakeSession()

        with patch("api.evidence_tasks.get_accessible_org_ids", accessible_orgs()):
            result = await list_evidence_tasks(
                status_filter=None,
                assigned_user_id=None,
                overdue_only=False,
                frameworks=None,
                db=db,
                current_user=caller,
            )

        assert result == []
        assert db.statements == []


# ---------------------------------------------------------------------------
# GET /api/users/me/dashboard
# ---------------------------------------------------------------------------

class TestMyDashboard:

    @pytest.mark.asyncio
    async def test_dashboard_queries_are_org_scoped(self, caller, org_b):
        """Stale assignments in orgs the user has left must not surface."""
        from api.evidence_tasks import get_my_dashboard

        db = FakeSession([[], []])

        with patch(
            "api.evidence_tasks.get_accessible_org_ids", accessible_orgs(org_b)
        ):
            result = await get_my_dashboard(db=db, current_user=caller)

        assert result["total_tasks"] == 0
        assert len(db.statements) == 2
        for statement in db.statements:
            assert "evidence_tracking" in sql_text(statement)
            assert str(org_b) in bind_values(statement)

    @pytest.mark.asyncio
    async def test_no_accessible_orgs_returns_empty_dashboard(self, caller):
        from api.evidence_tasks import get_my_dashboard

        db = FakeSession()

        with patch("api.evidence_tasks.get_accessible_org_ids", accessible_orgs()):
            result = await get_my_dashboard(db=db, current_user=caller)

        assert result["total_tasks"] == 0
        assert result["upcoming_tasks"] == []
        assert db.statements == []
