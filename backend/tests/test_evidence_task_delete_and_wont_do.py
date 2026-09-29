"""Deleting an evidence task, and the `wont_do` status.

Two things are pinned:

1. ``DELETE /api/evidence-tasks/{task_id}`` is admin-only on the task's
   organisation, removes the task's comment thread in the same transaction,
   and writes an audit entry before the row goes. Non-members get the same
   404 every other task route gives them; editors get 403.
2. ``wont_do`` is a closed status. The schemas accept it, and every "still
   open" predicate reads ``CLOSED_TASK_STATUSES`` rather than its own copy of
   ``!= 'completed'`` — except the generator's duplicate window, which is the
   one place a won't-do task must NOT count as closed.

Mock-based, no database, mirroring tests/test_evidence_tasks_tenancy.py.
"""
import inspect
import json
import os
import sys
from datetime import date
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import catalog_models  # noqa: E402,F401 — registers mappers referenced by models
from auth import OrgMembership  # noqa: E402
from models import CLOSED_TASK_STATUSES  # noqa: E402
from schemas import EvidenceCollectionTaskCreate, EvidenceCollectionTaskUpdate  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeResult:
    def __init__(self, value):
        self._value = value

    def first(self):
        return self._value

    def scalar_one_or_none(self):
        return self._value

    def all(self):
        return list(self._value) if self._value else []


class FakeSession:
    def __init__(self, results=None):
        self._results = list(results or [])
        self.statements = []
        self.added = []
        self.deleted = []
        self.committed = False

    async def execute(self, statement, params=None):
        self.statements.append(statement)
        value = self._results.pop(0) if self._results else None
        return FakeResult(value)

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def commit(self):
        self.committed = True


def sql_text(statement):
    return str(statement.compile(dialect=postgresql.dialect()))


def membership_gate(*member_org_ids, role="editor"):
    async def _verify(org_id, user, db, min_role="viewer"):
        if org_id not in member_org_ids:
            raise HTTPException(status_code=403, detail="Access denied")
        return OrgMembership(user=user, organization_id=org_id, role=role, is_consultant=False)
    return _verify


@pytest.fixture
def org_a():
    return uuid4()


@pytest.fixture
def caller():
    user = MagicMock()
    user.db_id = str(uuid4())
    user.email = "admin@example.com"
    return user


@pytest.fixture
def task(org_a):
    t = MagicMock()
    t.id = uuid4()
    t.organization_id = org_a
    t.evidence_tracking_id = uuid4()
    t.title = "Collect the quarterly access review"
    t.status = "not_started"
    t.due_date = date(2026, 10, 1)
    t.auto_generated = True
    return t


# ---------------------------------------------------------------------------
# 1. DELETE /api/evidence-tasks/{task_id}
# ---------------------------------------------------------------------------

class TestDeleteEvidenceTask:

    @pytest.mark.asyncio
    async def test_admin_deletes_task_comments_and_audits(self, caller, task, org_a):
        from api.evidence_tasks import delete_evidence_task

        db = FakeSession([(task, org_a), [(uuid4(),), (uuid4(),)], [(uuid4(),)]])
        request = MagicMock()
        request.headers = {}

        with patch("api.evidence_tasks.verify_org_membership", membership_gate(org_a, role="admin")):
            response = await delete_evidence_task(task_id=task.id, request=request, db=db, current_user=caller)

        assert response.status_code == 204
        assert db.deleted == [task]
        assert db.committed is True

        # The comment thread went in the same transaction, keyed the way the
        # polymorphic comments table is keyed.
        comment_deletes = [s for s in db.statements if sql_text(s).startswith("DELETE FROM comments")]
        assert len(comment_deletes) == 1
        assert "commentable_type" in sql_text(comment_deletes[0])

        # An audit entry was added before the delete.
        assert len(db.added) == 1
        entry = db.added[0]
        assert entry.entity_type == "evidence_task"
        assert entry.action == "delete"
        assert entry.entity_id == task.id
        assert entry.organization_id == org_a
        # ...and it says how much of other people's conversation went with it.
        old_value = json.loads(entry.old_value)
        assert old_value["comments_deleted"] == 2
        assert len(old_value["comment_ids"]) == 2
        assert "RETURNING" in sql_text(comment_deletes[0]).upper()

        # Notifications pointing at the task went too, scoped to the
        # organisation as well as the id, and the trail counts them.
        notification_deletes = [s for s in db.statements if sql_text(s).startswith("DELETE FROM notifications")]
        assert len(notification_deletes) == 1
        notif_sql = sql_text(notification_deletes[0])
        assert "reference_type" in notif_sql and "reference_id" in notif_sql and "organization_id" in notif_sql
        assert old_value["notifications_deleted"] == 1

    @pytest.mark.asyncio
    async def test_platform_master_key_is_refused_not_crashed(self, task, org_a):
        """The master key has no user row; the audit entry needs one. 403, not 500."""
        from api.evidence_tasks import delete_evidence_task

        machine = MagicMock()
        machine.db_id = None
        machine.email = "master-key"
        db = FakeSession([(task, org_a)])
        with patch("api.evidence_tasks.verify_org_membership", membership_gate(org_a, role="admin")):
            with pytest.raises(HTTPException) as exc_info:
                await delete_evidence_task(task_id=task.id, request=MagicMock(), db=db, current_user=machine)

        assert exc_info.value.status_code == 403
        assert db.statements == []
        assert db.deleted == []
        assert db.committed is False

    @pytest.mark.asyncio
    async def test_editor_gets_403_and_nothing_is_deleted(self, caller, task, org_a):
        from api.evidence_tasks import delete_evidence_task

        db = FakeSession([(task, org_a)])
        with patch("api.evidence_tasks.verify_org_membership", membership_gate(org_a, role="editor")):
            with pytest.raises(HTTPException) as exc_info:
                await delete_evidence_task(task_id=task.id, request=MagicMock(), db=db, current_user=caller)

        assert exc_info.value.status_code == 403
        assert "admin" in exc_info.value.detail
        assert db.deleted == []
        assert db.added == []
        assert db.committed is False

    @pytest.mark.asyncio
    async def test_non_member_gets_404(self, caller, task, org_a):
        from api.evidence_tasks import delete_evidence_task

        db = FakeSession([(task, org_a)])
        with patch("api.evidence_tasks.verify_org_membership", membership_gate(uuid4(), role="admin")):
            with pytest.raises(HTTPException) as exc_info:
                await delete_evidence_task(task_id=task.id, request=MagicMock(), db=db, current_user=caller)

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Task not found"
        assert db.deleted == []

    @pytest.mark.asyncio
    async def test_unknown_task_is_404(self, caller):
        from api.evidence_tasks import delete_evidence_task

        db = FakeSession([None])
        with pytest.raises(HTTPException) as exc_info:
            await delete_evidence_task(task_id=uuid4(), request=MagicMock(), db=db, current_user=caller)
        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# 2. wont_do
# ---------------------------------------------------------------------------

class TestWontDoStatus:

    def test_closed_statuses_are_completed_and_wont_do(self):
        assert set(CLOSED_TASK_STATUSES) == {"completed", "wont_do"}

    def test_schemas_accept_wont_do(self):
        assert EvidenceCollectionTaskUpdate(status="wont_do").status == "wont_do"
        assert EvidenceCollectionTaskCreate(
            evidence_tracking_id=uuid4(), due_date=date(2026, 10, 1), status="wont_do"
        ).status == "wont_do"

    def test_schemas_still_reject_unknown_status(self):
        with pytest.raises(ValidationError):
            EvidenceCollectionTaskUpdate(status="cancelled")

    def test_list_filter_accepts_wont_do(self):
        import re
        from api.evidence_tasks import list_evidence_tasks

        source = inspect.getsource(list_evidence_tasks)
        match = re.search(r'status_filter: Optional\[str\] = Query\(None, pattern="([^"]+)"\)', source)
        assert match, "status_filter regex not found"
        pattern = match.group(1)
        assert re.match(pattern, "wont_do")
        assert not re.match(pattern, "cancelled")

    def test_no_backend_module_carries_its_own_closed_predicate(self):
        """One declaration of "closed": no route or service compares a task
        status to the literal 'completed' with != or notin_. The generator's
        duplicate window is the one deliberate exception (next test)."""
        import re as _re
        from pathlib import Path

        backend = Path(__file__).resolve().parent.parent
        allowed = {backend / "services" / "task_generator.py"}
        offenders = []
        pattern = _re.compile(r"""status\s*(?:!=|<>)\s*['"]completed['"]""")
        for path in list((backend / "api").rglob("*.py")) + list((backend / "services").rglob("*.py")):
            if path in allowed:
                continue
            for lineno, line in enumerate(path.read_text().splitlines(), 1):
                if pattern.search(line):
                    offenders.append(f"{path.relative_to(backend)}:{lineno}: {line.strip()}")
        assert not offenders, "Own copy of 'closed' instead of CLOSED_TASK_STATUSES:\n" + "\n".join(offenders)

    @pytest.mark.asyncio
    async def test_list_payload_carries_created_at(self, task, org_a):
        """The Tasks page sorts on CREATED; the list route's serializer has to emit it."""
        from datetime import datetime, timezone

        from api.evidence_tasks import _serialize_task_with_evidence, list_evidence_tasks

        assert "_serialize_task_with_evidence" in inspect.getsource(list_evidence_tasks)
        task.created_at = datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc)
        task.updated_at = task.created_at
        task.priority = "high"
        task.task_type = "collection"
        task.description = None
        task.completed_date = None
        task.completion_notes = None
        task.assigned_user_id = None
        task.owning_team_id = None
        task.evidence_tracking = None
        payload = await _serialize_task_with_evidence(task, FakeSession([None, None]))
        assert payload["created_at"] == task.created_at

    def test_generator_duplicate_window_deliberately_ignores_wont_do(self):
        """Closing a task as won't-do must not stop the next collection being minted."""
        from services import task_generator

        source = inspect.getsource(task_generator.generate_task_for_tracking)
        assert "EvidenceCollectionTask.status != 'completed'" in source
        assert "CLOSED_TASK_STATUSES" not in source

    @pytest.mark.asyncio
    async def test_dashboard_counts_wont_do_as_closed_not_overdue(self, caller, org_a):
        from api.evidence_tasks import get_my_dashboard

        overdue_open = MagicMock(status="in_progress", due_date=date(2000, 1, 1))
        overdue_wont_do = MagicMock(status="wont_do", due_date=date(2000, 1, 1))
        done = MagicMock(status="completed", due_date=date(2000, 1, 1))

        class _Scalars:
            def __init__(self, rows):
                self.rows = rows

            def all(self):
                return self.rows

        class _Result:
            def __init__(self, rows):
                self.rows = rows

            def scalars(self):
                return _Scalars(self.rows)

        class _Db:
            def __init__(self):
                self.calls = 0

            async def execute(self, statement, params=None):
                self.calls += 1
                return _Result([overdue_open, overdue_wont_do, done] if self.calls == 1 else [])

        async def _accessible(user, db):
            return [org_a]

        with patch("api.evidence_tasks.get_accessible_org_ids", _accessible):
            out = await get_my_dashboard(db=_Db(), current_user=caller)

        assert out["total_tasks"] == 3
        assert out["completed"] == 1
        assert out["wont_do"] == 1
        assert out["overdue"] == 1, "a won't-do task is closed, not overdue"
