"""The organisation-level automatic task generation switch.

`settings.auto_task_generation_enabled` (default on) is honoured by the one
eligibility rule both automatic creators share — `generate_task_for_tracking`
— so the nightly sweep and the tracking write paths cannot disagree about it.
Manual task creation (`POST /api/evidence-tasks`) never consults it.

Pinned:

- the resolver's default-on semantics,
- a switched-off organisation gets a distinct skip reason and no task,
- the write path looks the switch up; the sweep excludes switched-off
  organisations from its SELECT and does not look it up per row,
- the settings endpoint echoes and persists the flag.

Mock-based, no database, mirroring tests/test_task_generation_on_write.py.
"""
import inspect
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import catalog_models  # noqa: E402,F401 — registers mappers referenced by models
from schemas import OrganizationSettingsResponse, OrganizationSettingsUpdate  # noqa: E402
from services import task_generator as tg  # noqa: E402
from services.task_generator import (  # noqa: E402
    AUTO_TASK_GENERATION_SETTING_KEY,
    CREATED,
    SKIP_AUTO_GENERATION_DISABLED,
    generate_task_for_tracking,
    resolve_auto_task_generation,
)


def sql_text(statement):
    return str(statement.compile(dialect=postgresql.dialect()))


class FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def all(self):
        return list(self._value or [])

    def scalars(self):
        rows = self._value

        class _S:
            def all(self_inner):
                return list(rows or [])

        return _S()


class FakeSession:
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

    async def rollback(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def tracking_row(**overrides):
    row = MagicMock()
    row.id = uuid4()
    row.organization_id = overrides.pop("organization_id", uuid4())
    row.evidence_id = "quarterly-access-review"
    row.is_tracked = True
    row.frequency = "monthly"
    row.last_collection_date = None
    row.assigned_user_id = None
    row.owner_user_id = None
    row.next_collection_date = None
    for k, v in overrides.items():
        setattr(row, k, v)
    return row


class TestResolver:

    @pytest.mark.parametrize("settings", [None, {}, {"other": 1}, "not-a-dict", {AUTO_TASK_GENERATION_SETTING_KEY: True}])
    def test_defaults_to_on(self, settings):
        assert resolve_auto_task_generation(settings) is True

    def test_explicit_false_is_off(self):
        assert resolve_auto_task_generation({AUTO_TASK_GENERATION_SETTING_KEY: False}) is False

    @pytest.mark.parametrize("value", ["false", "0", 0, None, "off"])
    def test_only_a_real_boolean_turns_it_off(self, value):
        # Free JSON column, more than one writer: a stringly "false" must not
        # silently starve an organisation of tasks.
        assert resolve_auto_task_generation({AUTO_TASK_GENERATION_SETTING_KEY: value}) is True


class TestGenerateTaskForTracking:

    @pytest.mark.asyncio
    async def test_switched_off_org_gets_no_task_and_a_distinct_reason(self):
        db = FakeSession([{AUTO_TASK_GENERATION_SETTING_KEY: False}])
        outcome = await generate_task_for_tracking(db, tracking_row())

        assert outcome.created is False
        assert outcome.reason == SKIP_AUTO_GENERATION_DISABLED
        assert outcome.due_date is not None, "the due date is still reported for the log"
        assert db.added == []
        # Exactly one query: the settings lookup. The duplicate check never ran.
        assert len(db.statements) == 1
        assert "organizations" in sql_text(db.statements[0])

    @pytest.mark.asyncio
    async def test_write_path_looks_the_switch_up_for_the_rows_org(self):
        org_id = uuid4()
        db = FakeSession([{AUTO_TASK_GENERATION_SETTING_KEY: True}, None])
        outcome = await generate_task_for_tracking(db, tracking_row(organization_id=org_id))

        assert outcome.reason == CREATED
        lookup = db.statements[0].compile(dialect=postgresql.dialect())
        assert str(org_id) in {str(v) for v in lookup.params.values()}

    @pytest.mark.asyncio
    async def test_explicit_true_skips_the_lookup(self):
        db = FakeSession([None])
        outcome = await generate_task_for_tracking(db, tracking_row(), auto_generation_enabled=True)
        assert outcome.reason == CREATED
        assert len(db.statements) == 1, "only the duplicate check"
        assert "organizations" not in sql_text(db.statements[0])

    @pytest.mark.asyncio
    async def test_explicit_false_never_touches_the_database(self):
        db = FakeSession()
        outcome = await generate_task_for_tracking(db, tracking_row(), auto_generation_enabled=False)
        assert outcome.reason == SKIP_AUTO_GENERATION_DISABLED
        assert db.statements == []

    @pytest.mark.asyncio
    async def test_untracked_rows_are_still_refused_before_the_lookup(self):
        db = FakeSession()
        outcome = await generate_task_for_tracking(db, tracking_row(is_tracked=False))
        assert outcome.reason == tg.SKIP_NOT_TRACKED
        assert db.statements == []


class TestSweep:

    @pytest.mark.asyncio
    async def test_sweep_excludes_switched_off_orgs_and_passes_true_per_row(self):
        on_org, off_org = uuid4(), uuid4()
        row = tracking_row(organization_id=on_org)
        db = FakeSession([
            [(on_org, {}), (off_org, {AUTO_TASK_GENERATION_SETTING_KEY: False})],  # settings prefetch
            [row],                                                                  # tracking rows
        ])
        seen = []

        async def _fake_generate(session, evidence, *, auto_generation_enabled=None):
            seen.append(auto_generation_enabled)
            return tg.TaskGenerationOutcome(True, CREATED)

        with patch.object(tg, "AsyncSessionLocal", return_value=db), \
             patch.object(tg, "generate_task_for_tracking", _fake_generate):
            result = await tg.generate_evidence_tasks()

        assert result == {"tasks_created": 1, "tasks_skipped": 0}
        assert seen == [True], "the sweep must not look the switch up once per row"

        tracking_select = db.statements[1]
        text = sql_text(tracking_select)
        assert "NOT IN" in text.upper()
        bound = {str(v) for vals in tracking_select.compile(dialect=postgresql.dialect()).params.values()
                 for v in (vals if isinstance(vals, (list, tuple)) else [vals])}
        assert str(off_org) in bound
        assert str(on_org) not in bound

    @pytest.mark.asyncio
    async def test_sweep_with_no_disabled_orgs_has_no_not_in_clause(self):
        db = FakeSession([[(uuid4(), {})], []])
        with patch.object(tg, "AsyncSessionLocal", return_value=db):
            await tg.generate_evidence_tasks()
        assert "NOT IN" not in sql_text(db.statements[1]).upper()

    def test_sweep_starting_log_is_debug(self):
        """The 'starting' line was noise at INFO once a night for every install."""
        source = inspect.getsource(tg.generate_evidence_tasks)
        assert 'logger.debug("Starting evidence task generation...")' in source
        assert 'logger.info("Starting' not in source


class TestSettingsEndpoint:

    def _db(self, org):
        db = AsyncMock()
        db.add = MagicMock()
        r = MagicMock()
        r.scalar_one_or_none = MagicMock(return_value=org)
        db.execute = AsyncMock(return_value=r)
        return db

    def _org(self, settings):
        return SimpleNamespace(id=uuid4(), name="Acme Widgets", settings=settings)

    def test_response_defaults_on_and_update_is_optional(self):
        assert OrganizationSettingsResponse().auto_task_generation_enabled is True
        assert "auto_task_generation_enabled" not in OrganizationSettingsUpdate(industry="x").model_dump(exclude_unset=True)

    @pytest.mark.asyncio
    async def test_get_echoes_the_stored_flag(self):
        from api.organizations import get_organization_settings

        org = self._org({AUTO_TASK_GENERATION_SETTING_KEY: False})
        out = await get_organization_settings(org_id=org.id, membership=MagicMock(), db=self._db(org))
        assert out.auto_task_generation_enabled is False

        org = self._org({})
        out = await get_organization_settings(org_id=org.id, membership=MagicMock(), db=self._db(org))
        assert out.auto_task_generation_enabled is True

    @pytest.mark.asyncio
    async def test_patch_persists_the_flag_and_leaves_other_settings_alone(self):
        from api.organizations import update_organization_settings

        org = self._org({"industry": "Fintech", "owner_teams": ["GRC"]})
        membership = MagicMock()
        membership.user.db_id = str(uuid4())
        with patch("api.organizations.log_entity_changes", new=AsyncMock()):
            out = await update_organization_settings(
                org_id=org.id,
                settings_data=OrganizationSettingsUpdate(auto_task_generation_enabled=False),
                request=MagicMock(),
                membership=membership,
                db=self._db(org),
            )
        assert out.auto_task_generation_enabled is False
        assert org.settings[AUTO_TASK_GENERATION_SETTING_KEY] is False
        assert org.settings["industry"] == "Fintech"
        assert org.settings["owner_teams"] == ["GRC"]
