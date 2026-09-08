"""Change cursor endpoint + middleware attribution, against a real Postgres.

Epic #921 (data refresh), defect #922 (dead audit middleware).

Skips — never passes — without a ``postgresql`` ``DATABASE_URL``. Run in a
throwaway container on the compose network::

    docker run --rm --network cg-scf-network -v $PWD/backend:/app -w /app \
      -e DATABASE_URL=... ghcr.io/markac007/scf-backend:latest \
      python -m pytest tests/test_change_cursor.py -v

Design notes:

* ``auth._authenticate`` is stubbed, not ``require_auth``. The point of #922
  is that the *real* ``require_auth`` sets ``request.state.user``; stubbing
  the outer function would test the stub.
* The middleware writes in its own ``AsyncSessionLocal`` and commits, so the
  test session reads rows back with a plain query after the request returns.
* Teardown deletes the organisations; ``audit_log`` cascades.
"""
from __future__ import annotations

import os
import sys
import uuid

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auth as auth_mod  # noqa: E402
import main  # noqa: E402
from database import get_db  # noqa: E402
from models import AuditLog, Organization, OrganizationMember, User  # noqa: E402

DATABASE_URL = os.getenv("DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason="needs a Postgres DATABASE_URL — SKIPPED, not passed",
)

# Explicit asyncio marker for the async suites: CI runs pytest from the
# repository root, where backend/pytest.ini (asyncio_mode=auto) is not the
# config. TestTheCursorIsGuarded holds one synchronous test and applies the
# mark to its async members individually.
asyncio_test = pytest.mark.asyncio

CURSOR_PATH = "/api/organizations/{org_id}/changes/cursor"
SYSTEMS_PATH = "/api/organizations/{org_id}/systems"
ZERO_UUID = uuid.UUID("00000000-0000-0000-0000-000000000000")


# ---------------------------------------------------------------------------
# The cursor
# ---------------------------------------------------------------------------

@asyncio_test
class TestTheCursorReflectsTheAuditLog:
    async def test_a_member_reads_max_changed_at_and_count(self, api, session, estate):
        api.as_("viewer")
        response = await api.client.get(CURSOR_PATH.format(org_id=estate.org_id))
        assert response.status_code == 200, response.text
        body = response.json()
        newest, count = (await session.execute(
            sa.select(sa.func.max(AuditLog.changed_at), sa.func.count(AuditLog.id))
            .where(AuditLog.organization_id == estate.org_id)
        )).one()
        assert body["count"] == count
        assert (body["cursor"] is None) == (newest is None)

    async def test_a_fresh_organisation_has_a_null_cursor(self, api, estate):
        api.as_("viewer")
        response = await api.client.get(CURSOR_PATH.format(org_id=estate.org_id))
        assert response.status_code == 200, response.text
        assert response.json() == {"cursor": None, "count": 0}

    async def test_the_cursor_advances_after_a_mutation(self, api, estate):
        api.as_("viewer")
        before = (await api.client.get(CURSOR_PATH.format(org_id=estate.org_id))).json()
        await api.create_system()
        api.as_("viewer")
        after = (await api.client.get(CURSOR_PATH.format(org_id=estate.org_id))).json()
        assert after["count"] > before["count"]
        assert after["cursor"] is not None
        assert before["cursor"] is None or after["cursor"] >= before["cursor"]

    async def test_reading_the_cursor_does_not_itself_write_audit_rows(self, api, estate):
        """A 20-second poll that wrote a row every 20 seconds would never settle."""
        api.as_("viewer")
        first = (await api.client.get(CURSOR_PATH.format(org_id=estate.org_id))).json()
        for _ in range(3):
            await api.client.get(CURSOR_PATH.format(org_id=estate.org_id))
        again = (await api.client.get(CURSOR_PATH.format(org_id=estate.org_id))).json()
        assert again == first

    async def test_another_organisations_changes_do_not_move_this_cursor(self, api, session, estate):
        session.add(AuditLog(
            organization_id=estate.other_org_id, entity_type="system",
            entity_id=uuid.uuid4(), action="create", action_source="ui",
        ))
        await session.commit()
        api.as_("viewer")
        body = (await api.client.get(CURSOR_PATH.format(org_id=estate.org_id))).json()
        assert body == {"cursor": None, "count": 0}


class TestTheCursorIsGuarded:
    @asyncio_test
    async def test_a_non_member_is_refused(self, api, estate):
        api.as_("outsider")
        response = await api.client.get(CURSOR_PATH.format(org_id=estate.org_id))
        assert response.status_code == 403, response.text

    @asyncio_test
    async def test_an_unauthenticated_caller_is_refused(self, api, estate):
        response = await api.client.get(CURSOR_PATH.format(org_id=estate.org_id))
        assert response.status_code == 401, response.text

    async def test_the_route_is_documented(self):
        assert "get" in main.app.openapi()["paths"].get(CURSOR_PATH, {})


# ---------------------------------------------------------------------------
# #922 — the middleware now writes, and says who
# ---------------------------------------------------------------------------

@asyncio_test
class TestTheMiddlewareAttributesMutations:
    async def _baseline_rows(self, session, org_id):
        """Middleware rows for this org, oldest first.

        Ordered explicitly: the middleware commits in its own session, so
        without an ORDER BY the "most recent" row is whatever the planner
        hands back.
        """
        return (await session.execute(
            sa.select(AuditLog)
            .where((AuditLog.organization_id == org_id) & (AuditLog.entity_id == ZERO_UUID))
            .order_by(AuditLog.changed_at)
        )).scalars().all()

    async def test_a_ui_write_produces_a_baseline_row_marked_ui(self, api, session, estate):
        await api.create_system()
        rows = await self._baseline_rows(session, estate.org_id)
        assert rows, (
            "the mutation succeeded and AuditMiddleware wrote nothing — "
            "request.state.user is not being set by require_auth (#922)"
        )
        row = rows[-1]
        assert row.action_source == "ui"
        assert row.action == "create"
        assert row.entity_type == "system"
        assert row.changed_by_user_id == estate.editor
        assert row.request_id is not None

    async def test_an_mcp_write_is_marked_mcp(self, api, session, estate):
        await api.create_system(auth_method="user_api_key",
                                headers={"User-Agent": "mcp-server-scf/3.0.0 (model context protocol)"})
        rows = await self._baseline_rows(session, estate.org_id)
        assert rows and rows[-1].action_source == "mcp"

    async def test_a_plain_api_key_write_is_marked_api_key(self, api, session, estate):
        await api.create_system(auth_method="user_api_key",
                                headers={"User-Agent": "python-httpx/0.27"})
        rows = await self._baseline_rows(session, estate.org_id)
        assert rows and rows[-1].action_source == "api_key"

    async def test_the_field_level_rows_share_the_request_id(self, api, session, estate):
        """Middleware and explicit audit calls correlate on one request_id."""
        await api.create_system()
        rows = (await session.execute(
            sa.select(AuditLog).where(AuditLog.organization_id == estate.org_id)
        )).scalars().all()
        baseline = [r for r in rows if r.entity_id == ZERO_UUID]
        detailed = [r for r in rows if r.entity_id != ZERO_UUID]
        assert baseline and detailed
        assert {r.request_id for r in detailed} == {baseline[-1].request_id}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def engine():
    eng = create_async_engine(DATABASE_URL)
    try:
        async with eng.connect():
            pass
    except Exception as exc:  # pragma: no cover - environment dependent
        await eng.dispose()
        pytest.skip(f"database not reachable: {exc}")
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest.fixture
async def session(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        try:
            yield s
        finally:
            await s.rollback()


class _Estate:
    def __init__(self, org_id, other_org_id, viewer, editor, outsider):
        self.org_id = org_id
        self.other_org_id = other_org_id
        self.viewer = viewer
        self.editor = editor
        self.outsider = outsider


@pytest.fixture
async def estate(session):
    tag = uuid.uuid4().hex[:10]
    org = Organization(name=f"cur-{tag}", slug=f"cur-{tag}")
    other = Organization(name=f"cur-other-{tag}", slug=f"cur-other-{tag}")
    session.add_all([org, other])
    await session.flush()

    async def _user(key):
        user = User(email=f"cur-{key}-{tag}@example.invalid", google_sub=f"cur-{key}-{tag}")
        session.add(user)
        await session.flush()
        return user

    viewer = await _user("viewer")
    editor = await _user("editor")
    outsider = await _user("outsider")
    session.add_all([
        OrganizationMember(organization_id=org.id, user_id=viewer.id, role="viewer"),
        OrganizationMember(organization_id=org.id, user_id=editor.id, role="editor"),
        OrganizationMember(organization_id=other.id, user_id=outsider.id, role="admin"),
    ])
    await session.commit()

    built = _Estate(org.id, other.id, viewer.id, editor.id, outsider.id)
    user_ids = [viewer.id, editor.id, outsider.id]
    try:
        yield built
    finally:
        await session.rollback()
        await session.execute(sa.delete(Organization).where(
            Organization.id.in_([built.org_id, built.other_org_id])))
        await session.execute(sa.delete(User).where(User.id.in_(user_ids)))
        await session.commit()


class _Api:
    def __init__(self, client, current, estate):
        self.client = client
        self._current = current
        self._estate = estate

    def as_(self, key: str, auth_method: str = "google") -> None:
        user_id = {"viewer": self._estate.viewer, "editor": self._estate.editor,
                   "outsider": self._estate.outsider}[key]
        user = auth_mod.User(
            user_id=f"stub-{key}", email=f"{key}@example.invalid",
            auth_method=auth_method, db_id=str(user_id),
        )
        if auth_method == "user_api_key":
            # verify_org_membership reads the org and role frozen into the key
            # itself for this auth method; a user object without them is
            # refused before the route is reached.
            user._api_key_org_id = self._estate.org_id
            user._api_key_role = "editor"
        self._current["user"] = user

    async def create_system(self, auth_method: str = "google", headers=None):
        self.as_("editor", auth_method=auth_method)
        response = await self.client.post(
            SYSTEMS_PATH.format(org_id=self._estate.org_id),
            json={"name": f"sys-{uuid.uuid4().hex[:6]}", "system_type": "custom"},
            headers=headers or {},
        )
        assert response.status_code in (200, 201), response.text
        return response


@pytest.fixture
async def app_engine():
    """Give the middleware's module-level engine this test's event loop.

    ``AuditMiddleware`` writes through ``database.AsyncSessionLocal``, whose
    engine is created once at import and pools connections bound to whichever
    loop first used them. pytest-asyncio runs each test on a fresh loop, so
    the second test onwards the middleware picks up a pooled connection from a
    dead loop, raises "attached to a different loop", and — because the
    middleware swallows every exception rather than break the request — the
    test sees an empty audit table and reads it as the #922 defect returning.
    Disposing the pool either side of the test keeps that noise out.
    """
    import database

    await database.engine.dispose()
    try:
        yield database.engine
    finally:
        await database.engine.dispose()


@pytest.fixture
async def api(app_engine, session, estate, monkeypatch):
    """The real app, the real ``require_auth``, a stubbed token check."""
    from fastapi import HTTPException

    current = {"user": None}

    async def _authenticate(credentials, db):
        if current["user"] is None:
            raise HTTPException(status_code=401, detail="no caller chosen")
        return current["user"]

    async def _db():
        yield session

    monkeypatch.setattr(auth_mod, "_authenticate", _authenticate)
    main.app.dependency_overrides[get_db] = _db
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app),
            base_url="http://cursor",
            headers={"Authorization": "Bearer stub"},
        ) as client:
            yield _Api(client, current, estate)
    finally:
        main.app.dependency_overrides.pop(get_db, None)
