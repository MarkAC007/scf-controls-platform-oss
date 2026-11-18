# SPDX-License-Identifier: AGPL-3.0-only
"""The deficiency route and the objective detail agree on the link.

Two modules meet here: ``api.deficiencies.create_deficiency`` writes
``source`` and ``source_ref``, and ``services.isms_metrics.service`` counts
the open deficiencies raised from an objective by reading them back. Each is
tested on its own; this pins that what one writes is what the other reads,
including an id sent in upper case.
"""
import uuid as _uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from api import deficiencies as api
from api.deficiencies import DeficiencyRaise
from auth import OrgMembership
from models import Organization, OrganizationMember, SecurityObjective, User
from schemas import DeficiencyUpdate
from services.isms_metrics import service

from test_journey_service import DATABASE_URL, requires_postgres  # noqa: F401


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


def _request():
    return Request({"type": "http", "headers": [], "method": "POST", "path": "/"})


async def _org_objective_editor(db):
    tag = _uuid.uuid4().hex[:10]
    org = Organization(name=f"obj-link-{tag}", slug=f"obj-link-{tag}")
    user = User(google_sub=f"obj-link-{tag}", email=f"obj-link-{tag}@example.invalid", display_name="P")
    db.add_all([org, user])
    await db.flush()
    db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role="editor"))
    objective = SecurityObjective(
        organization_id=org.id, ref="OBJ-01", name="Patch on time",
        objective_type="maintain", lifecycle_status="active", source="custom",
    )
    db.add(objective)
    await db.flush()
    member = OrgMembership(
        user=SimpleNamespace(db_id=str(user.id), email=user.email), organization_id=org.id, role="editor",
    )
    return org, objective, member


@requires_postgres
@pytest.mark.asyncio
async def test_a_deficiency_raised_through_the_route_is_counted_on_the_objective(db, no_commit):
    org, objective, member = await _org_objective_editor(db)
    assert await service.raised_deficiency_count(db, org.id, objective.id) == 0

    first = await api.create_deficiency(
        org.id,
        DeficiencyRaise(title="Off track", source="security_objective", source_ref=str(objective.id)),
        _request(), member, db,
    )
    assert await service.raised_deficiency_count(db, org.id, objective.id) == 1

    # An id sent in upper case is the same objective.
    await api.create_deficiency(
        org.id,
        DeficiencyRaise(title="Off track again", source="security_objective", source_ref=str(objective.id).upper()),
        _request(), member, db,
    )
    assert await service.raised_deficiency_count(db, org.id, objective.id) == 2

    # Completing one takes it off the count: only open ones are "already raised".
    await api.update_deficiency(org.id, first.id, DeficiencyUpdate(status="completed", retest_note="Re-measured: back on target"), _request(), member, db)
    assert await service.raised_deficiency_count(db, org.id, objective.id) == 1
