# SPDX-License-Identifier: AGPL-3.0-only
"""Phase 3 B6: a logged entry's stored status is judged as of its own period.

A value back-filled for an earlier period must store the status its value
earns (on track, at risk, off track), not "measurement overdue" because
today's period has no entry yet. The clause 9.1 document prints that stored
status. The tile reading is unchanged: an objective whose newest entry is
for an earlier period still reads "measurement overdue" today. Rows already
stored are not rewritten.
"""
import uuid as _uuid
from datetime import date, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from models import IsmsMetricResult, ObjectiveMetric, Organization, OrganizationMember, SecurityObjective, User
from services.isms_metrics import service

from test_journey_service import DATABASE_URL, requires_postgres  # noqa: F401

TODAY = date(2026, 11, 18)           # Q4 2026
Q3, Q4 = date(2026, 7, 1), date(2026, 10, 1)


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


async def _setup(db, frequency="quarterly", **fields):
    tag = _uuid.uuid4().hex[:10]
    org = Organization(name=f"isms-b6-{tag}", slug=f"isms-b6-{tag}")
    db.add(org)
    await db.flush()
    user = User(google_sub=f"isms-b6-{tag}", email=f"isms-b6-{tag}@example.invalid")
    db.add(user)
    await db.flush()
    db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role="admin"))
    objective = SecurityObjective(
        organization_id=org.id, ref="OBJ-01", name="Train staff", objective_type=fields.pop("objective_type", "maintain"),
        lifecycle_status="active", source="custom", **fields,
    )
    db.add(objective)
    await db.flush()
    db.add(ObjectiveMetric(organization_id=org.id, objective_id=objective.id, kind="logged",
                           name="Training completion (%)", unit="percent", direction="at_least",
                           target_value=95, warn_value=85, frequency=frequency))
    await db.flush()
    return org, objective, service.Actor(user_id=user.id, action_source="ui")


async def _stored(db, org):
    return [(r.period_start, r.status) for r in (await db.execute(
        select(IsmsMetricResult).where(IsmsMetricResult.organization_id == org.id)
        .order_by(IsmsMetricResult.created_at, IsmsMetricResult.id))).scalars().all()]


@requires_postgres
@pytest.mark.asyncio
class TestStoredStatus:
    @pytest.mark.parametrize("value, status", [(97, "on_track"), (88, "at_risk"), (50, "off_track")])
    async def test_a_back_dated_entry_stores_the_status_its_value_earns(self, db, value, status):
        org, objective, actor = await _setup(db)
        await service.log_result(db, org.id, objective.id, value, actor, TODAY, period_start=date(2026, 9, 1))
        assert await _stored(db, org) == [(Q3, status)]

    async def test_a_back_dated_monthly_entry_is_judged_at_the_end_of_its_month(self, db):
        org, objective, actor = await _setup(db, frequency="monthly")
        await service.log_result(db, org.id, objective.id, 88, actor, TODAY, period_start=date(2026, 8, 15))
        assert await _stored(db, org) == [(date(2026, 8, 1), "at_risk")]

    @pytest.mark.parametrize("value, status", [(97, "on_track"), (88, "at_risk"), (50, "off_track")])
    async def test_an_entry_for_the_current_period_is_unchanged(self, db, value, status):
        org, objective, actor = await _setup(db)
        out = await service.log_result(db, org.id, objective.id, value, actor, TODAY)
        assert await _stored(db, org) == [(Q4, status)]
        assert out["reading"]["status"] == status

    async def test_the_judgement_date_is_never_after_today(self, db):
        # An "achieve by 30 Nov" objective is in progress until its date; judged
        # at the end of the quarter instead of today, 50% would read off track.
        org, objective, actor = await _setup(db, objective_type="achieve", due_date=date(2026, 11, 30))
        await service.log_result(db, org.id, objective.id, 50, actor, TODAY)
        assert await _stored(db, org) == [(Q4, "in_progress")]

    async def test_a_back_dated_entry_is_judged_at_the_end_of_its_period(self, db):
        # Due 15 Aug: a Q3 entry is judged on 30 Sep, after the date, so 50% is off track,
        # not "in progress" as it would be on 1 Jul.
        org, objective, actor = await _setup(db, objective_type="achieve", due_date=date(2026, 8, 15))
        await service.log_result(db, org.id, objective.id, 50, actor, TODAY, period_start=Q3)
        assert await _stored(db, org) == [(Q3, "off_track")]

    async def test_the_tile_still_reads_measurement_overdue(self, db):
        org, objective, actor = await _setup(db)
        out = await service.log_result(db, org.id, objective.id, 97, actor, TODAY, period_start=Q3)
        assert await _stored(db, org) == [(Q3, "on_track")]
        assert out["reading"]["status"] == "measurement_overdue"
        assert (await service.get_objective(db, org.id, objective.id, TODAY))["reading"]["status"] == (
            "measurement_overdue")

    async def test_rows_already_stored_are_not_rewritten(self, db):
        org, objective, actor = await _setup(db)
        metric = await service.active_metric(db, org.id, objective.id)
        db.add(IsmsMetricResult(organization_id=org.id, objective_metric_id=metric.id, measured_on=TODAY,
                                period_start=Q3, value=97, status="measurement_overdue", source="logged",
                                created_at=datetime(2026, 11, 1)))
        await db.flush()
        await service.log_result(db, org.id, objective.id, 88, actor, TODAY, period_start=date(2026, 4, 1))
        await service.log_result(db, org.id, objective.id, 97, actor, TODAY)
        assert await _stored(db, org) == [
            (Q3, "measurement_overdue"), (date(2026, 4, 1), "at_risk"), (Q4, "on_track")]
