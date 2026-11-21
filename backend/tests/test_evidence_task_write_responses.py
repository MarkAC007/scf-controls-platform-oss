# SPDX-License-Identifier: AGPL-3.0-only
"""The task write endpoints answer with the stored task, not schema defaults (#1283).

``POST /api/evidence-tasks``, ``PATCH /api/evidence-tasks/{id}`` and
``POST /api/evidence-tasks/{id}/complete`` each built their own response dict
and all three left out ``task_type``, ``priority``, ``title``, ``description``,
``dependencies`` and ``attachments``. ``EvidenceCollectionTaskResponse`` has a
default for every one of those, so the answer validated and reported
``priority: "medium"``, ``task_type: "collection"`` and ``title: null``
whatever was stored. A client (the MCP server's ``scf_evidence_task_write``)
was told its update had not been applied, or had wiped the title.

The tests run each handler's return value through the response model, as
FastAPI does, and compare it with the stored row. The last class pins the
general rule: every field of the response model is supplied, so none can fall
back to a default.

Mock-based, following ``tests/test_evidence_task_owning_team_patch.py``.
"""
import os
import sys
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import catalog_models  # noqa: E402,F401 — registers mappers referenced by models.System
from schemas import (  # noqa: E402
    EvidenceCollectionTaskCreate,
    EvidenceCollectionTaskResponse,
    EvidenceCollectionTaskUpdate,
)
from tests.test_evidence_tasks_tenancy import (  # noqa: E402
    FakeSession,
    membership_gate,
)

# What the three write handlers left out before #1283.
DROPPED_FIELDS = ("task_type", "priority", "title", "description", "dependencies", "attachments")


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
    """A task whose every defaulted field holds something other than the default."""
    task = MagicMock()
    task.id = uuid4()
    task.evidence_tracking_id = uuid4()
    task.organization_id = org_id
    task.owning_team_id = uuid4()
    task.assigned_user_id = None
    task.task_type = "review"
    task.title = "Quarterly access review"
    task.description = "Review the joiners, movers and leavers list."
    task.priority = "low"
    task.due_date = date(2027, 1, 15)
    task.status = "not_started"
    task.completed_date = None
    task.completion_notes = None
    task.dependencies = [uuid4()]
    task.attachments = [{"url": "https://example.invalid/a.pdf", "name": "a.pdf", "type": "pdf"}]
    task.auto_generated = False
    task.created_at = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)
    return task


def _as_served(result) -> EvidenceCollectionTaskResponse:
    """What FastAPI makes of the handler's return value."""
    return EvidenceCollectionTaskResponse.model_validate(result)


def _assert_matches_stored(served, task):
    for field in EvidenceCollectionTaskResponse.model_fields:
        if field == "assigned_user":
            continue
        assert getattr(served, field) == getattr(task, field), field


class TestUpdateResponse:

    @pytest.mark.asyncio
    async def test_answer_shows_the_stored_task(self, task, caller, org_id):
        """MCP-10: update priority and status on a titled task."""
        from api.evidence_tasks import update_evidence_task

        update = EvidenceCollectionTaskUpdate(priority="high", status="in_progress")
        db = FakeSession([(task, org_id)])
        with patch("api.evidence_tasks.verify_org_membership", membership_gate(org_id)):
            result = await update_evidence_task(
                task_id=task.id, task_update=update, db=db, current_user=caller
            )

        served = _as_served(result)
        assert served.priority == "high"
        assert served.status == "in_progress"
        assert served.title == "Quarterly access review"
        assert served.description == "Review the joiners, movers and leavers list."
        assert served.task_type == "review"
        _assert_matches_stored(served, task)


class TestCreateResponse:

    @pytest.mark.asyncio
    async def test_answer_shows_what_was_created(self, caller, org_id):
        from api.evidence_tasks import create_evidence_task

        evidence = MagicMock()
        evidence.id = uuid4()
        evidence.organization_id = org_id
        dependency = uuid4()
        payload = EvidenceCollectionTaskCreate(
            evidence_tracking_id=evidence.id,
            due_date=date(2027, 1, 15),
            task_type="review",
            priority="critical",
            title="Quarterly access review",
            description="Review the joiners, movers and leavers list.",
            dependencies=[dependency],
            attachments=[{"name": "a.pdf"}],
        )

        class _Session(FakeSession):
            async def refresh(self, obj):
                # The columns the database fills in on insert.
                obj.id = uuid4()
                obj.created_at = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)

        db = _Session([evidence])
        with patch("api.evidence_tasks.verify_org_membership", membership_gate(org_id)):
            result = await create_evidence_task(task_data=payload, db=db, current_user=caller)

        served = _as_served(result)
        assert served.task_type == "review"
        assert served.priority == "critical"
        assert served.title == "Quarterly access review"
        assert served.description == "Review the joiners, movers and leavers list."
        assert served.dependencies == [dependency]
        assert served.attachments == [{"name": "a.pdf"}]
        _assert_matches_stored(served, db.added[0])


class TestCompleteResponse:

    @pytest.mark.asyncio
    async def test_answer_shows_the_stored_task(self, task, caller, org_id):
        from api.evidence_tasks import complete_evidence_task

        db = FakeSession([(task, org_id)])
        with patch("api.evidence_tasks.verify_org_membership", membership_gate(org_id)):
            result = await complete_evidence_task(
                task_id=task.id, completion_notes="done", db=db, current_user=caller
            )

        served = _as_served(result)
        assert served.status == "completed"
        assert served.priority == "low"
        assert served.title == "Quarterly access review"
        assert served.task_type == "review"
        _assert_matches_stored(served, task)


class TestNoFieldCanFallBackToADefault:

    @pytest.mark.asyncio
    async def test_the_shared_builder_supplies_every_response_field(self, task):
        from api.evidence_tasks import _task_response

        result = await _task_response(task, FakeSession())

        assert set(result) == set(EvidenceCollectionTaskResponse.model_fields)

    @pytest.mark.asyncio
    async def test_the_assignee_is_resolved_when_there_is_one(self, task):
        from api.evidence_tasks import _task_response

        user = MagicMock()
        user.id = uuid4()
        user.email = "holder@example.invalid"
        user.display_name = "Task Holder"
        task.assigned_user_id = user.id

        result = await _task_response(task, FakeSession([user]))

        assert result["assigned_user"] == {
            "id": user.id, "email": user.email, "display_name": user.display_name,
        }

    def test_every_handler_with_this_response_model_uses_the_builder(self):
        """A fourth hand-built dict would bring the defect back for its endpoint."""
        import inspect

        from api import evidence_tasks

        for handler in (
            evidence_tasks.create_evidence_task,
            evidence_tasks.update_evidence_task,
            evidence_tasks.complete_evidence_task,
        ):
            assert "return await _task_response(task, db)" in inspect.getsource(handler), handler.__name__
