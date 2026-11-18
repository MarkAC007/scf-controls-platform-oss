# SPDX-License-Identifier: AGPL-3.0-only
"""Objective reminders can be marked read.

``PATCH /api/notifications/{id}/read`` serialises through
``NotificationResponse``, whose ``type`` and ``reference_type`` are
restricted by regex (``schemas.NotificationBase``). A type the writers emit
but the pattern omits makes marking that notification read a 500 after the
row is already committed. These tests pin:

* every notification type and reference type the reminders module can emit
  validates against ``NotificationBase``; the list of types is read from the
  module's own ``_notify`` calls, so a fourth type added later is checked too;
* an ``objective_due`` notification on a ``security_objective`` is marked read
  through the real route and comes back 200, read, with its bell target.
"""
import ast
import inspect
import uuid as _uuid
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import catalog_models  # noqa: F401 - registers mappers referenced by models
from api import notifications as notifications_api
from auth import User as AuthUser, require_auth
from database import get_db
from models import Notification, Organization, OrganizationMember, SecurityObjective, User
from schemas import NotificationBase
from services.isms_metrics import reminders

from test_journey_service import DATABASE_URL, requires_postgres  # noqa: F401


def _emitted_types():
    """The notification types named in reminders' ``_notify`` calls."""
    tree = ast.parse(inspect.getsource(reminders))
    names = [
        node.args[2].id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_notify"
        and len(node.args) >= 3 and isinstance(node.args[2], ast.Name)
    ]
    assert names, "no _notify calls found: the walk would check nothing"
    return {getattr(reminders, n) for n in names}


def _emitted_reference_types():
    """The reference types passed to ``_emit`` in the reminders module."""
    tree = ast.parse(inspect.getsource(reminders))
    refs = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_emit":
            for kw in node.keywords:
                if kw.arg == "reference_type":
                    refs.append(getattr(reminders, kw.value.id))
    assert refs, "no _emit call found: the walk would check nothing"
    return set(refs)


def test_the_walk_finds_every_declared_reminder_type():
    assert _emitted_types() == set(reminders.NOTIFICATION_TYPES)
    assert _emitted_reference_types() == {reminders.REFERENCE_TYPE}


@pytest.mark.parametrize("notification_type", sorted(_emitted_types()))
@pytest.mark.parametrize("reference_type", sorted(_emitted_reference_types()))
def test_every_reminder_the_module_can_emit_validates(notification_type, reference_type):
    NotificationBase(type=notification_type, reference_type=reference_type,
                     reference_id=uuid4(), message="m")


@pytest.mark.parametrize("field, value", [
    ("type", "objective_made_up"),
    ("type", "objective_due_soon"),
    ("reference_type", "security_objectives"),
    ("reference_type", "objective"),
])
def test_a_type_the_module_does_not_emit_is_still_refused(field, value):
    """Widening the patterns for the reminders must not let near-misses through."""
    row = {"type": reminders.OBJECTIVE_DUE, "reference_type": reminders.REFERENCE_TYPE,
           "reference_id": uuid4(), "message": "m", field: value}
    with pytest.raises(ValueError):
        NotificationBase(**row)


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(DATABASE_URL)
    try:
        async with engine.connect():
            pass
    except Exception as exc:  # pragma: no cover - environment dependent
        await engine.dispose()
        pytest.skip(f"database not reachable: {exc}")
    session = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)()
    try:
        yield session
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


@pytest.fixture
def no_commit(db, monkeypatch):
    monkeypatch.setattr(db, "commit", db.flush)
    monkeypatch.setattr(db, "rollback", db.flush)


@requires_postgres
@pytest.mark.asyncio
async def test_an_objective_reminder_is_marked_read_through_the_route(db, no_commit):
    tag = _uuid.uuid4().hex[:10]
    org = Organization(name=f"obj-read-{tag}", slug=f"obj-read-{tag}")
    user = User(google_sub=f"obj-read-{tag}", email=f"obj-read-{tag}@example.invalid", display_name="P")
    db.add_all([org, user])
    await db.flush()
    db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role="editor"))
    objective = SecurityObjective(organization_id=org.id, ref="OBJ-01", name="Patch on time",
                                  objective_type="maintain", lifecycle_status="active", source="custom")
    db.add(objective)
    await db.flush()
    note = Notification(user_id=user.id, organization_id=org.id, type=reminders.OBJECTIVE_DUE,
                        reference_type=reminders.REFERENCE_TYPE, reference_id=objective.id,
                        message='Objective OBJ-01 "Patch on time" is due on 2027-03-01.')
    db.add(note)
    await db.flush()

    app = FastAPI()
    app.include_router(notifications_api.router)

    async def _db():
        yield db

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[require_auth] = lambda: AuthUser(
        user_id=f"obj-read-{tag}", email=user.email, auth_method="google", db_id=str(user.id),
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        response = await client.patch(f"/api/notifications/{note.id}/read")

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["id"], body["is_read"]) == (str(note.id), True)
    assert (body["type"], body["reference_type"]) == ("objective_due", "security_objective")
    assert body["reference_key"] == str(objective.id)
    await db.refresh(note)
    assert note.is_read is True and note.read_at is not None
