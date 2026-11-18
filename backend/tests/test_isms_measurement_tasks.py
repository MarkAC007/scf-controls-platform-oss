# SPDX-License-Identifier: AGPL-3.0-only
"""Phase 3 B2: measurement tasks for logged metrics, derived when read.

These tests pin:

* which objectives have a task: active, unarchived, a logged metric with a
  calendar frequency; not proposed, archived, closed, live or milestone;
* open and done: open until the current period has an entry; a back-filled
  earlier period does not close it; done tasks only with ``include_done``;
* the period: one task for the current period only, gone when the period
  ends, the next one there on its first day;
* ``mine``: the reminder's own recipients (owner, else the team's primary
  and delegate, else the admins), and ``assigned_to_me`` keeps only those;
* isolation and side effects: another organisation's viewer is refused,
  another organisation's objectives never appear, and the call writes
  nothing, commits nothing and raises no notification.
"""
import uuid as _uuid
from datetime import date, datetime, time
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import event, func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import auth as auth_mod
from api import security_objectives as api
from models import (
    Function,
    IsmsMetricResult,
    Notification,
    ObjectiveMetric,
    Organization,
    OrganizationMember,
    SecurityObjective,
    Team,
    TeamMember,
    User,
)
from services.isms_metrics import measurement_tasks as mt
from services.isms_metrics import reminders, service
from services.isms_metrics.schemas import MeasurementTask, MetricSpec

from test_journey_service import DATABASE_URL, requires_postgres  # noqa: F401

TODAY = date(2026, 11, 18)          # Q4 2026: 1 Oct to 31 Dec
Q4, Q3 = date(2026, 10, 1), date(2026, 7, 1)


def test_titles_name_the_period():
    assert mt.task_title("OBJ-09", "Train staff", "quarterly") == 'Log this quarter\'s value for OBJ-09 "Train staff"'
    assert mt.task_title("OBJ-01", "x", "monthly").startswith("Log this month's value")
    assert mt.task_title("OBJ-01", "x", "annually").startswith("Log this year's value")
    oid = _uuid.uuid4()
    assert mt.task_id(oid, Q4) == f"measurement:{oid}:2026-10-01"


# ---------------------------------------------------------------------------
# Database-backed
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db(monkeypatch):
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


FROM_DAY_ONE = {reminders.MEASUREMENT_REMINDER_DAYS_KEY: 366}


async def _org(db):
    tag = _uuid.uuid4().hex[:10]
    org = Organization(name=f"isms-mt-{tag}", slug=f"isms-mt-{tag}", settings=dict(FROM_DAY_ONE))
    db.add(org)
    await db.flush()
    return org


async def _user(db, org, role="editor"):
    tag = _uuid.uuid4().hex[:10]
    user = User(google_sub=f"isms-mt-{tag}", email=f"isms-mt-{tag}@example.invalid", display_name=f"P {tag}")
    db.add(user)
    await db.flush()
    db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=role))
    await db.flush()
    return user


async def _team(db, org, **roles):
    function = (await db.execute(select(Function).limit(1))).scalar_one_or_none()
    if function is None:  # pragma: no cover - environment dependent
        pytest.skip("no function rows to hang a team on")
    team = Team(organization_id=org.id, function_id=function.id, name=f"Sec {_uuid.uuid4().hex[:6]}")
    db.add(team)
    await db.flush()
    for role, user in roles.items():
        db.add(TeamMember(team_id=team.id, organization_id=org.id, user_id=user.id, membership_role=role))
    await db.flush()
    return team


_REFS = iter(range(1, 100_000))


async def _objective(db, org, *, lifecycle="active", kind="logged", frequency="quarterly", results=(),
                     archived=False, **fields):
    objective = SecurityObjective(
        organization_id=org.id, ref=f"OBJ-{next(_REFS):02d}", name=fields.pop("name", "Train staff"),
        objective_type="maintain", lifecycle_status=lifecycle, source="custom",
        archived_at=datetime(2026, 1, 1) if archived else None, **fields,
    )
    db.add(objective)
    await db.flush()
    spec = {
        "logged": dict(kind="logged", name="Training completion (%)", unit="percent", direction="at_least",
                       target_value=95, warn_value=85, frequency=frequency),
        "milestone": dict(kind="milestone", name="Policy approved"),
        # A milestone row that carries a frequency all the same: still no task.
        "milestone_with_frequency": dict(kind="milestone", name="Policy approved", frequency=frequency),
        "live": dict(kind="live", measure_key="controls_implemented", unit="percent", direction="at_least",
                     target_value=80),
    }[kind]
    metric = ObjectiveMetric(organization_id=org.id, objective_id=objective.id, **spec)
    db.add(metric)
    await db.flush()
    for period in results:
        db.add(IsmsMetricResult(organization_id=org.id, objective_metric_id=metric.id, measured_on=period,
                                period_start=period, value=97, status="on_track", source="logged"))
    await db.flush()
    return objective


async def _tasks(db, org, caller=None, day=TODAY, **kw):
    return await mt.measurement_tasks(db, org.id, caller.id if caller else None, day, **kw)


def _actor(user):
    return service.Actor(user_id=user.id, action_source="ui")


async def _notifications(db, org):
    return int(await db.scalar(select(func.count()).select_from(Notification).where(
        Notification.organization_id == org.id)) or 0)


@requires_postgres
@pytest.mark.asyncio
class TestWhichObjectives:
    async def test_first_measurement_gives_an_open_task(self, db):
        org = await _org(db)
        objective = await _objective(db, org)
        (task,) = await _tasks(db, org)
        MeasurementTask.model_validate(task)
        assert task["id"] == f"measurement:{objective.id}:2026-10-01"
        assert (task["status"], task["first"], task["period_start"], task["due_date"]) == (
            "not_started", True, Q4, date(2026, 12, 31))
        assert task["ref"] == objective.ref and task["frequency"] == "quarterly" and task["unit"] == "percent"
        assert task["title"] == f'Log this quarter\'s value for {objective.ref} "Train staff"'
        assert task["metric_name"] == "Training completion (%)"
        assert task["completed_date"] is None and task["value_text"] is None

    async def test_an_unlogged_current_period_gives_an_open_task(self, db):
        org = await _org(db)
        await _objective(db, org, results=(Q3,))
        (task,) = await _tasks(db, org)
        assert (task["status"], task["first"]) == ("not_started", False)

    @pytest.mark.parametrize("kw", [
        dict(lifecycle="proposed"), dict(lifecycle="achieved"), dict(lifecycle="missed"), dict(archived=True),
        dict(kind="live"), dict(kind="milestone"), dict(kind="milestone_with_frequency"),
    ])
    async def test_no_task(self, db, kw):
        org = await _org(db)
        await _objective(db, org, **kw)
        assert await _tasks(db, org) == []
        assert await _tasks(db, org, include_done=True) == []

    async def test_a_retired_logged_version_gives_no_task(self, db):
        org = await _org(db)
        objective = await _objective(db, org)
        await db.execute(update(ObjectiveMetric).where(ObjectiveMetric.objective_id == objective.id)
                         .values(active_to=datetime(2026, 11, 1)))
        await db.flush()
        assert await _tasks(db, org) == []


@requires_postgres
@pytest.mark.asyncio
class TestOpenAndDone:
    async def test_a_logged_value_closes_the_task(self, db):
        org = await _org(db)
        admin = await _user(db, org, "admin")
        objective = await _objective(db, org)
        await service.log_result(db, org.id, objective.id, 97, _actor(admin), TODAY)
        assert await _tasks(db, org) == []
        (done,) = await _tasks(db, org, include_done=True)
        assert (done["status"], done["completed_date"], done["value_text"]) == ("completed", TODAY, "97%")
        assert done["first"] is False

    async def test_a_back_filled_earlier_period_does_not_close_it(self, db):
        org = await _org(db)
        admin = await _user(db, org, "admin")
        objective = await _objective(db, org)
        await service.log_result(db, org.id, objective.id, 97, _actor(admin), TODAY, period_start=date(2026, 8, 15))
        (task,) = await _tasks(db, org)
        assert task["status"] == "not_started" and task["first"] is False

    async def test_a_value_logged_before_a_target_change_still_counts(self, db):
        org = await _org(db)
        admin = await _user(db, org, "admin")
        objective = await service.create_objective(db, org.id, service.ObjectiveCreate(
            name="Train", planned_actions="x", metric=MetricSpec(
                kind="logged", name="Training (%)", unit="percent", direction="at_least", target_value=90,
                frequency="quarterly")), _actor(admin), TODAY)
        await db.execute(update(SecurityObjective).where(SecurityObjective.id == objective["id"])
                         .values(lifecycle_status="active"))
        await service.log_result(db, org.id, objective["id"], 92, _actor(admin), TODAY)
        await db.execute(update(SecurityObjective).where(SecurityObjective.id == objective["id"])
                         .values(lifecycle_status="proposed"))
        await service.set_metric(db, org.id, objective["id"], MetricSpec(
            kind="logged", name="Training (%)", unit="percent", direction="at_least", target_value=95,
            frequency="quarterly"), _actor(admin), "admin", TODAY)
        await db.execute(update(SecurityObjective).where(SecurityObjective.id == objective["id"])
                         .values(lifecycle_status="active"))
        await db.flush()
        assert await _tasks(db, org) == []
        (done,) = await _tasks(db, org, include_done=True)
        assert done["status"] == "completed"

    async def test_an_entry_under_another_unit_does_not_close_it(self, db):
        org = await _org(db)
        objective = await _objective(db, org)
        old = ObjectiveMetric(organization_id=org.id, objective_id=objective.id, kind="logged", name="Old count",
                              unit="count", direction="at_most", target_value=0, frequency="quarterly",
                              active_from=datetime(2026, 1, 1), active_to=datetime(2026, 9, 1))
        db.add(old)
        await db.flush()
        db.add(IsmsMetricResult(organization_id=org.id, objective_metric_id=old.id, measured_on=Q4,
                                period_start=Q4, value=3, status="off_track", source="logged"))
        await db.flush()
        (task,) = await _tasks(db, org)
        assert task["status"] == "not_started" and task["first"] is True

    async def test_the_period_rolls_over(self, db):
        org = await _org(db)
        objective = await _objective(db, org, results=(Q4,))
        assert await _tasks(db, org, day=date(2026, 12, 31)) == []
        (task,) = await _tasks(db, org, day=date(2027, 1, 1))
        assert task["id"] == f"measurement:{objective.id}:2027-01-01" and task["due_date"] == date(2027, 3, 31)

    async def test_monthly_and_annual_periods(self, db):
        org = await _org(db)
        await _objective(db, org, frequency="annually", name="Yearly")   # the lower ref, due later
        await _objective(db, org, frequency="monthly", name="Monthly")
        tasks = await _tasks(db, org)
        assert [(t["frequency"], t["period_start"], t["due_date"]) for t in tasks] == [
            ("monthly", date(2026, 11, 1), date(2026, 11, 30)),
            ("annually", date(2026, 1, 1), date(2026, 12, 31)),
        ]  # due date first


@requires_postgres
@pytest.mark.asyncio
class TestMine:
    async def test_mine_follows_owner_then_team_then_admins(self, db):
        org = await _org(db)
        admin = await _user(db, org, "admin")
        owner, primary, delegate, member = [await _user(db, org) for _ in range(4)]
        team = await _team(db, org, primary=primary, delegate=delegate, member=member)
        owned = await _objective(db, org, name="Owned", owner_user_id=owner.id, owning_team_id=team.id)
        teamed = await _objective(db, org, name="Team", owning_team_id=team.id)
        orphan = await _objective(db, org, name="Nobody")

        async def mine(user):
            return {t["objective_id"] for t in await _tasks(db, org, user) if t["mine"]}

        assert await mine(owner) == {owned.id}
        assert await mine(primary) == await mine(delegate) == {teamed.id}
        assert await mine(member) == set()
        assert await mine(admin) == {orphan.id}
        for user, objective in ((owner, owned), (primary, teamed), (admin, orphan)):
            got = await _tasks(db, org, user, assigned_to_me=True)
            assert [t["objective_id"] for t in got] == [objective.id]
            assert set(await reminders.recipients_for(db, objective, reminders.MEASUREMENT_DUE)) >= {user.id}
        (row,) = [t for t in await _tasks(db, org) if t["objective_id"] == owned.id]
        assert row["owning_team_name"] == team.name and row["owner_name"] == owner.display_name

    async def test_an_owner_who_turned_notifications_off_is_still_assigned(self, db):
        # B7: who a task belongs to does not depend on whether they want the email.
        org = await _org(db)
        await _user(db, org, "admin")
        owner = await _user(db, org)
        owner.email_notifications_enabled, owner.notification_frequency = False, "never"
        await db.flush()
        objective = await _objective(db, org, owner_user_id=owner.id)
        got = await _tasks(db, org, owner, assigned_to_me=True)
        assert [(t["objective_id"], t["mine"]) for t in got] == [(objective.id, True)]

    async def test_an_owner_who_left_hands_the_task_to_the_team(self, db):
        org = await _org(db)
        primary = await _user(db, org)
        team = await _team(db, org, primary=primary)
        stranger = User(google_sub=f"x-{_uuid.uuid4().hex}", email=f"x-{_uuid.uuid4().hex}@example.invalid")
        db.add(stranger)
        await db.flush()
        await _objective(db, org, owner_user_id=stranger.id, owning_team_id=team.id)
        assert [t["mine"] for t in await _tasks(db, org, primary)] == [True]
        assert [t["mine"] for t in await _tasks(db, org, stranger)] == [False]

    async def test_no_caller_is_never_mine(self, db):
        org = await _org(db)
        await _user(db, org, "admin")
        await _objective(db, org)
        assert [t["mine"] for t in await _tasks(db, org)] == [False]
        assert await _tasks(db, org, assigned_to_me=True) == []


@requires_postgres
@pytest.mark.asyncio
class TestIsolationAndSideEffects:
    async def test_another_organisations_viewer_is_refused(self, db):
        org, other = await _org(db), await _org(db)
        outsider = await _user(db, other, "admin")
        caller = auth_mod.User(user_id=outsider.google_sub, email=outsider.email, auth_method="google",
                               db_id=str(outsider.id))
        with pytest.raises(HTTPException) as exc:
            await auth_mod.verify_org_membership(org.id, caller, db, "viewer")
        assert exc.value.status_code == 403
        key = auth_mod.User(user_id="api_user", email=outsider.email, auth_method="user_api_key",
                            db_id=str(outsider.id))
        key._api_key_org_id, key._api_key_role = other.id, "admin"
        with pytest.raises(HTTPException) as exc:
            await auth_mod.verify_org_membership(org.id, key, db, "viewer")
        assert exc.value.status_code == 403

    async def test_another_organisations_objectives_never_appear(self, db):
        org, other = await _org(db), await _org(db)
        mine = await _objective(db, org)
        await _objective(db, other)
        await _objective(db, other)
        assert [t["objective_id"] for t in await _tasks(db, org)] == [mine.id]

    async def test_the_route_writes_nothing_and_raises_no_notification(self, db, monkeypatch):
        org = await _org(db)
        admin = await _user(db, org, "admin")
        await _objective(db, org, owner_user_id=admin.id)

        async def refuse():
            raise AssertionError("the measurement-tasks call must not commit")

        monkeypatch.setattr(db, "commit", refuse)
        monkeypatch.setattr(api, "_today", lambda: TODAY)
        membership = auth_mod.OrgMembership(user=SimpleNamespace(db_id=str(admin.id), email=admin.email),
                                            organization_id=org.id, role="viewer")
        before = await _notifications(db, org)
        results_before = int(await db.scalar(select(func.count()).select_from(IsmsMetricResult).where(
            IsmsMetricResult.organization_id == org.id)))
        got = await api.list_isms_measurement_tasks(org_id=org.id, assigned_to_me=True, include_done=False,
                                                    membership=membership, db=db)
        assert [t["mine"] for t in got] == [True]
        assert await _notifications(db, org) == before == 0
        assert not db.new and not db.dirty and not db.deleted
        assert int(await db.scalar(select(func.count()).select_from(IsmsMetricResult).where(
            IsmsMetricResult.organization_id == org.id))) == results_before

        # The morning sweep stays the notifier: it writes its own rows, and the call adds none after it.
        monkeypatch.setattr(db, "commit", db.flush)
        monkeypatch.setattr(db, "rollback", db.flush)
        await reminders.check_and_notify_objectives(db, today=TODAY)
        await db.execute(update(Notification).where(Notification.created_at == func.now())
                         .values(created_at=datetime.combine(TODAY, time(7, 45))))
        after_sweep = await _notifications(db, org)
        assert after_sweep >= 1
        await api.list_isms_measurement_tasks(org_id=org.id, assigned_to_me=False, include_done=True,
                                              membership=membership, db=db)
        assert await _notifications(db, org) == after_sweep


# ---------------------------------------------------------------------------
# Review fixes R5 and R6 (review-b.md L1, L3)
# ---------------------------------------------------------------------------


async def _new_version(db, org, objective, **spec):
    """Close the metric in force and open the next one, as ``set_metric`` does."""
    old = (await db.execute(select(ObjectiveMetric).where(
        ObjectiveMetric.objective_id == objective.id, ObjectiveMetric.active_to.is_(None)))).scalar_one()
    old.active_to = datetime(2026, 11, 18, 9)
    await db.flush()
    fields = dict(kind=old.kind, name=old.name, unit=old.unit, direction=old.direction,
                  target_value=old.target_value, warn_value=old.warn_value, frequency=old.frequency)
    fields.update(spec)
    db.add(ObjectiveMetric(organization_id=org.id, objective_id=objective.id,
                           active_from=datetime(2026, 11, 18, 9), **fields))
    await db.flush()


@requires_postgres
@pytest.mark.asyncio
class TestR5AgreesWithTheReading:
    async def test_a_monthly_entry_then_a_change_to_quarterly_leaves_no_open_task(self, db):
        org = await _org(db)
        objective = await _objective(db, org, frequency="monthly", results=(date(2026, 11, 1),))
        await _new_version(db, org, objective, frequency="quarterly")
        assert await _tasks(db, org) == []
        (done,) = await _tasks(db, org, include_done=True)
        assert (done["status"], done["period_start"], done["value_text"]) == ("completed", Q4, "97%")
        metric = await service.active_metric(db, org.id, objective.id)
        reading = await service.read_objective(db, org.id, objective, metric, TODAY)
        assert reading["status"] not in (service.status_rules.MEASUREMENT_OVERDUE,
                                         service.status_rules.FIRST_MEASUREMENT_DUE)

    async def test_the_newest_period_is_the_one_shown_not_the_newest_written(self, db):
        org = await _org(db)
        objective = await _objective(db, org, frequency="monthly")
        (metric_id,) = (await db.execute(select(ObjectiveMetric.id).where(
            ObjectiveMetric.objective_id == objective.id))).scalars().all()
        db.add(IsmsMetricResult(organization_id=org.id, objective_metric_id=metric_id, measured_on=TODAY,
                                period_start=date(2026, 11, 1), value=97, status="on_track", source="logged",
                                created_at=datetime(2026, 11, 18, 8)))
        db.add(IsmsMetricResult(organization_id=org.id, objective_metric_id=metric_id, measured_on=TODAY,
                                period_start=date(2026, 10, 1), value=50, status="off_track", source="logged",
                                created_at=datetime(2026, 11, 18, 9)))   # back-filled later, older period
        await db.flush()
        await _new_version(db, org, objective, frequency="quarterly")
        (done,) = await _tasks(db, org, include_done=True)
        assert (done["status"], done["value_text"]) == ("completed", "97%")


@requires_postgres
@pytest.mark.asyncio
class TestR6Cost:
    @pytest.mark.parametrize("count", [2, 10])
    async def test_a_fixed_number_of_statements_for_one_owner(self, db, count):
        org = await _org(db)
        owner = await _user(db, org)
        team = await _team(db, org, primary=owner)
        for n in range(count):
            await _objective(db, org, owner_user_id=owner.id, owning_team_id=team.id,
                             results=(Q3,) if n % 2 else ())
        statements = []

        def seen(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        sync_engine = db.bind.sync_engine
        event.listen(sync_engine, "before_cursor_execute", seen)
        try:
            got = await _tasks(db, org, owner, assigned_to_me=True)
        finally:
            event.remove(sync_engine, "before_cursor_execute", seen)
        assert len(got) == count and all(t["mine"] for t in got)
        assert len(statements) == mt.STATEMENTS_ONE_OWNER, statements

    async def test_the_cache_keys_on_the_team_as_well_as_the_owner(self, db):
        org = await _org(db)
        caller = await _user(db, org)
        other = await _user(db, org)
        gone = await _user(db, org)
        await db.execute(OrganizationMember.__table__.delete().where(
            OrganizationMember.organization_id == org.id, OrganizationMember.user_id == gone.id))
        mine_team = await _team(db, org, primary=caller)
        their_team = await _team(db, org, primary=other)
        a = await _objective(db, org, owner_user_id=gone.id, owning_team_id=mine_team.id)
        b = await _objective(db, org, owner_user_id=gone.id, owning_team_id=their_team.id)
        c = await _objective(db, org, owner_user_id=other.id, owning_team_id=mine_team.id)
        got = {t["objective_id"]: t["mine"] for t in await _tasks(db, org, caller)}
        assert got == {a.id: True, b.id: False, c.id: False}
