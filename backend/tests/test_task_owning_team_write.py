"""A task's owning team: what a write means, and what an omission means (#822 §6).

``owning_team_id`` is the override. ``NULL`` inherits the parent evidence
item's accountable team, and inheriting is the default a task is created with
-- setup, collection and review on one evidence item are routinely three
different functions, so the override has to exist, and going back to the
parent has to be expressible too.

That last part is the whole reason this file exists. Every other optional
field on ``EvidenceCollectionTaskUpdate`` uses ``if x is not None``, which
cannot tell an explicit ``null`` from an absent key. Under that idiom an
override is a one-way door: you can hand a task to GRC and never hand it back,
and the bug passes review because the happy path works. The endpoint uses
``model_fields_set`` for this field alone, and these tests are what stop
somebody tidying it back into the house style.

Mock-based, no database.
"""
import os
import sys
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import catalog_models  # noqa: E402,F401 — registers mappers referenced by models
from api.evidence_tasks import update_evidence_task  # noqa: E402
from schemas import (  # noqa: E402
    EvidenceCollectionTaskCreate,
    EvidenceCollectionTaskUpdate,
)


ORG = uuid4()
TEAM = uuid4()
OTHER_TEAM = uuid4()
#: A real uuid, not a MagicMock attribute. The endpoint audits every ownership
#: change (invariant 6) and does `UUID(current_user.db_id)` to name the actor,
#: which a bare MagicMock cannot survive. Nothing here asserts on the actor --
#: the fixture just has to be able to reach the code under test.
ACTOR = uuid4()


class TestTheSchemaKeepsOmittedAndNullApart:
    def test_an_omitted_team_is_not_in_fields_set(self):
        update = EvidenceCollectionTaskUpdate(status="in_progress")

        assert "owning_team_id" not in update.model_fields_set
        # It still reads as None, which is exactly the trap: the value alone
        # cannot tell you the caller said nothing.
        assert update.owning_team_id is None

    def test_an_explicit_null_IS_in_fields_set(self):
        update = EvidenceCollectionTaskUpdate.model_validate({"owning_team_id": None})

        assert "owning_team_id" in update.model_fields_set
        assert update.owning_team_id is None

    def test_a_set_team_is_in_fields_set(self):
        update = EvidenceCollectionTaskUpdate.model_validate({"owning_team_id": str(TEAM)})

        assert "owning_team_id" in update.model_fields_set
        assert update.owning_team_id == TEAM

    def test_a_task_is_created_inheriting_by_default(self):
        create = EvidenceCollectionTaskCreate(
            evidence_tracking_id=uuid4(),
            title="Collect the export",
            due_date=date(2026, 12, 31),
        )

        # Not "unowned" -- inheriting. A task created without a team belongs to
        # whoever owns its evidence item.
        assert create.owning_team_id is None

    def test_create_accepts_an_override_in_the_same_write(self):
        # One write, not create-then-PATCH: a half-failed pair leaves a task
        # with the WRONG owner, which is the accountability gap this phase
        # exists to close.
        create = EvidenceCollectionTaskCreate.model_validate(
            {
                "evidence_tracking_id": str(uuid4()),
                "title": "Review the export",
                "due_date": "2026-12-31",
                "owning_team_id": str(TEAM),
            }
        )

        assert create.owning_team_id == TEAM


def _task(owning_team_id=None):
    task = MagicMock()
    task.id = uuid4()
    task.evidence_tracking_id = uuid4()
    task.owning_team_id = owning_team_id
    task.assigned_user_id = None
    task.dependencies = []
    task.attachments = []
    return task


def _db(org_id=ORG, team_found=True):
    """A session that answers the team-in-org check, then the assignee check.

    One team lookup, not an org lookup followed by a team lookup: the endpoint
    scopes the team query by ``task.organization_id``, which is denormalised
    from the parent evidence item and therefore already known. Asking the
    database for the org first would be a round trip for a value in hand.
    """
    db = MagicMock()
    team_result = MagicMock()
    team = MagicMock()
    team.id = TEAM
    team.organization_id = org_id
    team.is_active = True
    team_result.scalar_one_or_none.return_value = team if team_found else None
    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(side_effect=[team_result, user_result])
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


def _actor():
    """A caller the audit path can name. See ACTOR."""
    user = MagicMock()
    user.db_id = str(ACTOR)
    return user


async def _patch(task, update, db):
    with patch(
        "api.evidence_tasks._resolve_task_access", AsyncMock(return_value=task)
    ):
        return await update_evidence_task(
            task_id=task.id,
            task_update=update,
            db=db,
            current_user=_actor(),
        )


class TestTheEndpointHonoursTheDistinction:
    @pytest.mark.asyncio
    async def test_an_explicit_null_clears_the_override(self):
        task = _task(owning_team_id=TEAM)
        db = _db()

        await _patch(
            task,
            EvidenceCollectionTaskUpdate.model_validate({"owning_team_id": None}),
            db,
        )

        # Back to inheriting. Without this the override is a one-way door.
        assert task.owning_team_id is None

    @pytest.mark.asyncio
    async def test_an_omitted_key_leaves_the_team_alone(self):
        task = _task(owning_team_id=TEAM)
        db = _db()

        await _patch(task, EvidenceCollectionTaskUpdate(status="completed"), db)

        # TaskEditModal is not the only caller. A partial PATCH that never
        # mentions the team must not silently reassign the work.
        assert task.owning_team_id == TEAM

    @pytest.mark.asyncio
    async def test_setting_a_team_writes_it(self):
        task = _task(owning_team_id=None)
        db = _db()

        await _patch(
            task,
            EvidenceCollectionTaskUpdate.model_validate({"owning_team_id": str(TEAM)}),
            db,
        )

        assert task.owning_team_id == TEAM

    @pytest.mark.asyncio
    async def test_a_team_from_another_tenant_is_a_400_not_a_500(self):
        task = _task(owning_team_id=None)
        db = _db(team_found=False)

        with pytest.raises(HTTPException) as exc:
            await _patch(
                task,
                EvidenceCollectionTaskUpdate.model_validate(
                    {"owning_team_id": str(OTHER_TEAM)}
                ),
                db,
            )

        # The composite foreign key is the backstop that makes this a bug
        # rather than a breach. The service check is what makes it a message
        # somebody can act on instead of an integrity error.
        assert exc.value.status_code == 400
        assert task.owning_team_id is None

    @pytest.mark.asyncio
    async def test_clearing_does_not_cost_a_team_lookup(self):
        """Nothing to validate when the answer is "inherit"."""
        task = _task(owning_team_id=TEAM)
        db = _db()

        await _patch(
            task,
            EvidenceCollectionTaskUpdate.model_validate({"owning_team_id": None}),
            db,
        )

        # Zero queries: no org lookup and no team check, because "inherit" is
        # not a value that needs validating. (Nothing else reads either -- the
        # trailing assigned-user lookup is skipped when nobody is assigned.)
        assert db.execute.await_count == 0
