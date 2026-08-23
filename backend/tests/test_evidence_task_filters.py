"""
Server-side filtering on ``GET /api/evidence-tasks`` (#788).

Three callers used to fetch every evidence task in the organisation and narrow
the list in JavaScript. The response is unpaginated, so each of those views
grew with the tenant rather than with what it displayed — and one of them,
the "My Tasks" page, applied no user filter at all: it showed everybody's
tasks under a heading claiming they were yours.

These tests pin the filters into SQL, and pin "mine" to the authenticated
caller rather than to a client-supplied id.

Mock-based — no database (mirrors tests/test_evidence_tasks_tenancy.py).
"""
import pytest
from unittest.mock import MagicMock
from uuid import uuid4

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import catalog_models  # noqa: E402,F401 — registers mappers referenced by models
from sqlalchemy.dialects import postgresql  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _Result:
    def scalars(self):
        class _Scalars:
            def all(self_inner):
                return []

        return _Scalars()

    def fetchall(self):
        return []

    def all(self):
        return []

    def scalar_one_or_none(self):
        return None


class FakeSession:
    """Records every statement; every query comes back empty."""

    def __init__(self):
        self.statements = []

    async def execute(self, statement, params=None):
        self.statements.append(statement)
        return _Result()


def sql_text(statement):
    return str(statement.compile(dialect=postgresql.dialect()))


def bind_values(statement):
    compiled = statement.compile(dialect=postgresql.dialect())
    values = set()
    for value in compiled.params.values():
        if isinstance(value, (list, tuple)):
            values.update(str(item) for item in value)
        else:
            values.add(str(value))
    return values


def accessible_orgs(*org_ids):
    async def _accessible(user, db):
        return list(org_ids)

    return _accessible


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def org_id():
    return uuid4()


@pytest.fixture
def caller():
    user = MagicMock()
    user.db_id = str(uuid4())
    user.email = "collector@example.test"
    user.auth_method = "oidc"
    return user


async def call_list(db, caller, org_id, **kwargs):
    from unittest.mock import patch
    from api.evidence_tasks import list_evidence_tasks

    # Every parameter is passed explicitly, exactly as FastAPI resolves them at
    # request time. Calling the endpoint function directly leaves any omitted
    # argument as its `Query(...)` default object, which is truthy — a filter
    # nobody asked for would silently apply.
    params = dict(
        status_filter=None,
        assigned_user_id=None,
        overdue_only=False,
        frameworks=None,
        evidence_tracking_id=None,
        task_type=None,
        assigned_to_me=False,
    )
    params.update(kwargs)
    with patch(
        "api.evidence_tasks.get_accessible_org_ids", accessible_orgs(org_id)
    ):
        return await list_evidence_tasks(db=db, current_user=caller, **params)


# ---------------------------------------------------------------------------
# Filters reach SQL
# ---------------------------------------------------------------------------

class TestFiltersAreAppliedInSql:

    @pytest.mark.asyncio
    async def test_evidence_tracking_id_narrows_the_query(self, caller, org_id):
        db = FakeSession()
        tracking_id = uuid4()

        await call_list(db, caller, org_id, evidence_tracking_id=tracking_id)

        statement = db.statements[0]
        assert "evidence_tracking_id" in sql_text(statement)
        assert str(tracking_id) in bind_values(statement)

    @pytest.mark.asyncio
    async def test_task_type_narrows_the_query(self, caller, org_id):
        db = FakeSession()

        await call_list(db, caller, org_id, task_type="review")

        statement = db.statements[0]
        assert "task_type" in sql_text(statement)
        assert "review" in bind_values(statement)

    @pytest.mark.asyncio
    async def test_filters_compose(self, caller, org_id):
        """Status + type + tracking id in one query, not three passes in JS."""
        db = FakeSession()
        tracking_id = uuid4()

        await call_list(
            db,
            caller,
            org_id,
            evidence_tracking_id=tracking_id,
            task_type="collection",
            status_filter="in_progress",
        )

        values = bind_values(db.statements[0])
        assert str(tracking_id) in values
        assert "collection" in values
        assert "in_progress" in values

    @pytest.mark.asyncio
    async def test_no_filters_still_lists(self, caller, org_id):
        """The unfiltered call is unchanged — existing callers keep working."""
        db = FakeSession()

        result = await call_list(db, caller, org_id)

        assert result == []
        assert len(db.statements) == 1
        assert "task_type" not in bind_values(db.statements[0])


# ---------------------------------------------------------------------------
# "My tasks" is resolved server-side
# ---------------------------------------------------------------------------

class TestAssignedToMe:

    @pytest.mark.asyncio
    async def test_binds_the_callers_own_id(self, caller, org_id):
        db = FakeSession()

        await call_list(db, caller, org_id, assigned_to_me=True)

        statement = db.statements[0]
        assert "assigned_user_id" in sql_text(statement)
        assert caller.db_id in bind_values(statement)

    @pytest.mark.asyncio
    async def test_ignores_a_client_supplied_id_in_favour_of_the_token(
        self, caller, org_id
    ):
        """Both filters apply; the caller's own id is always one of them."""
        db = FakeSession()
        someone_else = uuid4()

        await call_list(
            db,
            caller,
            org_id,
            assigned_to_me=True,
            assigned_user_id=someone_else,
        )

        values = bind_values(db.statements[0])
        assert caller.db_id in values

    @pytest.mark.asyncio
    async def test_unpersisted_caller_gets_nothing_rather_than_everything(
        self, org_id
    ):
        """No DB row → no assignments. Never fall back to "all tasks"."""
        db = FakeSession()
        service_identity = MagicMock()
        service_identity.db_id = None

        result = await call_list(
            db, service_identity, org_id, assigned_to_me=True
        )

        assert result == []
        assert db.statements == []

    @pytest.mark.asyncio
    async def test_default_is_off(self, caller, org_id):
        """Omitting the flag must not silently scope the list to the caller."""
        db = FakeSession()

        await call_list(db, caller, org_id)

        assert caller.db_id not in bind_values(db.statements[0])
