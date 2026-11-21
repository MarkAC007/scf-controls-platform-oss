# SPDX-License-Identifier: AGPL-3.0-only
"""``PATCH /api/evidence-tasks/{task_id}`` keeps ``completed_date`` in step with ``status`` (#1268).

"Mark completed" on the task detail page patches ``status=completed`` and
sends no date. Only ``POST …/complete`` stamped one, so a task closed from the
page was completed with no completion date.

The PATCH handler now owns the rule, so it holds for every client that patches
the status (the page, the API, the MCP server):

* moving **into** ``completed`` stamps today, unless the caller supplied a date;
* moving **out of** ``completed`` clears the date, the same way reopening a
  deficiency does (``services.deficiencies.apply_status``);
* a PATCH that does not change the status leaves the date alone.

Mock-based, following ``tests/test_evidence_task_owning_team_patch.py``.
"""
import os
import sys
from datetime import date
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import catalog_models  # noqa: E402,F401 — registers mappers referenced by models.System
from schemas import EvidenceCollectionTaskUpdate  # noqa: E402
from tests.test_evidence_tasks_tenancy import (  # noqa: E402
    FakeSession,
    membership_gate,
)


@pytest.fixture
def org_id():
    return uuid4()


@pytest.fixture
def caller():
    user = MagicMock()
    user.db_id = str(uuid4())
    user.email = "editor@example.invalid"
    user.auth_method = "oidc"
    return user


@pytest.fixture
def task(org_id):
    task = MagicMock()
    task.id = uuid4()
    task.evidence_tracking_id = uuid4()
    task.organization_id = org_id
    task.owning_team_id = None
    task.assigned_user_id = None
    task.task_type = "documentation"
    task.title = "Write the access review procedure"
    task.description = None
    task.priority = "medium"
    task.due_date = date(2026, 1, 1)
    task.status = "not_started"
    task.completed_date = None
    task.completion_notes = None
    task.dependencies = []
    task.attachments = []
    task.auto_generated = False
    task.created_at = None
    return task


async def _patch(task, update, caller, org_id):
    """Call the endpoint as a same-org editor, with a scripted session."""
    from api.evidence_tasks import update_evidence_task

    db = FakeSession([(task, org_id)])
    with patch("api.evidence_tasks.verify_org_membership", membership_gate(org_id)):
        result = await update_evidence_task(
            task_id=task.id,
            task_update=update,
            db=db,
            current_user=caller,
        )
    return db, result


class TestMovingIntoCompleted:

    @pytest.mark.asyncio
    async def test_status_completed_with_no_date_stamps_today(self, task, caller, org_id):
        """What "Mark completed" on the task detail page sends."""
        update = EvidenceCollectionTaskUpdate(status="completed", completion_notes=None)

        db, result = await _patch(task, update, caller, org_id)

        assert db.committed is True
        assert task.status == "completed"
        assert task.completed_date == date.today()
        assert result["completed_date"] == date.today()

    @pytest.mark.asyncio
    async def test_a_supplied_date_is_kept(self, task, caller, org_id):
        """A caller recording work finished earlier says when; that is not overwritten."""
        update = EvidenceCollectionTaskUpdate(status="completed", completed_date=date(2026, 3, 4))

        await _patch(task, update, caller, org_id)

        assert task.completed_date == date(2026, 3, 4)

    @pytest.mark.asyncio
    async def test_wont_do_to_completed_stamps_today(self, task, caller, org_id):
        task.status = "wont_do"
        update = EvidenceCollectionTaskUpdate(status="completed")

        await _patch(task, update, caller, org_id)

        assert task.completed_date == date.today()


class TestStatusNotChanging:

    @pytest.mark.asyncio
    async def test_resaving_a_completed_task_keeps_its_date(self, task, caller, org_id):
        """"Save changes" on a completed task re-sends status=completed."""
        task.status = "completed"
        task.completed_date = date(2026, 3, 4)
        update = EvidenceCollectionTaskUpdate(status="completed", completion_notes="amended")

        await _patch(task, update, caller, org_id)

        assert task.completed_date == date(2026, 3, 4)

    @pytest.mark.asyncio
    async def test_editing_another_field_of_a_completed_task_keeps_its_date(self, task, caller, org_id):
        task.status = "completed"
        task.completed_date = date(2026, 3, 4)
        update = EvidenceCollectionTaskUpdate(priority="high")

        await _patch(task, update, caller, org_id)

        assert task.status == "completed"
        assert task.completed_date == date(2026, 3, 4)

    @pytest.mark.asyncio
    async def test_an_open_task_gets_no_date(self, task, caller, org_id):
        update = EvidenceCollectionTaskUpdate(status="in_progress")

        await _patch(task, update, caller, org_id)

        assert task.completed_date is None

    @pytest.mark.asyncio
    async def test_wont_do_gets_no_date(self, task, caller, org_id):
        """Won't do is a resolution, not a completion."""
        update = EvidenceCollectionTaskUpdate(status="wont_do")

        await _patch(task, update, caller, org_id)

        assert task.completed_date is None


class TestMovingOutOfCompleted:

    @pytest.mark.parametrize("reopened_as", ["not_started", "in_progress", "wont_do"])
    @pytest.mark.asyncio
    async def test_leaving_completed_clears_the_date(self, task, caller, org_id, reopened_as):
        """A task that is no longer completed must not still claim a completion date."""
        task.status = "completed"
        task.completed_date = date(2026, 3, 4)
        update = EvidenceCollectionTaskUpdate(status=reopened_as)

        db, result = await _patch(task, update, caller, org_id)

        assert task.status == reopened_as
        assert task.completed_date is None
        assert result["completed_date"] is None

    @pytest.mark.asyncio
    async def test_completing_again_after_a_reopen_stamps_a_fresh_date(self, task, caller, org_id):
        task.status = "completed"
        task.completed_date = date(2026, 3, 4)

        await _patch(task, EvidenceCollectionTaskUpdate(status="in_progress"), caller, org_id)
        await _patch(task, EvidenceCollectionTaskUpdate(status="completed"), caller, org_id)

        assert task.completed_date == date.today()
