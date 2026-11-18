# SPDX-License-Identifier: AGPL-3.0-only
"""Phase 3 part A: ``reading.trend`` (A1) and ``reading.pace`` (A2).

The pace rule is a pure function and is tested as a table without a database.
The rest runs against a real Postgres on a transaction that is always rolled
back; those tests skip (never pass) when no database resolves.
"""
import uuid as _uuid
from datetime import date, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from models import (
    IsmsMetricResult,
    IsmsProfile,
    ObjectiveMetric,
    Organization,
    OrganizationMember,
    ScopedControl,
    SecurityObjective,
    User,
)
from services.isms_metrics import service, status as status_rules, wording
from services.isms_metrics.schemas import MetricDetail, MetricSpec, ObjectiveCreate, ObjectiveOut
from services.isms_metrics.service import Actor

from test_journey_service import DATABASE_URL, requires_postgres  # noqa: F401

TODAY = date(2026, 11, 18)
DUE = date(2027, 6, 30)
BASE_ON = date(2026, 9, 1)


# ---------------------------------------------------------------------------
# A2: the pace rule as a table (no database)
# ---------------------------------------------------------------------------

def _metric(unit="percent", direction="at_least", target=80.0):
    return {"unit": unit, "direction": direction, "target_value": target}


def _pace(value=60.0, baseline=40.0, *, kind="achieve", lifecycle="active", archived=False, due=DUE,
          metric=None, baseline_on=BASE_ON, today=TODAY):
    return status_rules.pace(kind, lifecycle, archived, due, metric or _metric(), value, baseline_on, baseline,
                             today)


# Expected by today on the default line: 40 + 40 * 78 / 302.
EXPECTED = 40 + 40 * (TODAY - BASE_ON).days / (DUE - BASE_ON).days


@pytest.mark.parametrize("case, kwargs, state", [
    ("at least, above the line", dict(value=EXPECTED + 1), "on_pace"),
    ("at least, below the line", dict(value=EXPECTED - 1), "behind"),
    ("at least, on the line exactly", dict(value=EXPECTED), "on_pace"),
    ("at most, below the line", dict(value=20.0, baseline=40.0, metric=_metric(direction="at_most", target=10)),
     "on_pace"),
    ("at most, above the line", dict(value=39.0, baseline=40.0, metric=_metric(direction="at_most", target=10)),
     "behind"),
    # Today's value meeting the target is on pace; a baseline that met it earns nothing.
    ("baseline meets target, today below it", dict(value=50.0, baseline=90.0), "behind"),
    ("baseline meets target, today meets it under the falling line", dict(value=85.0, baseline=90.0), "on_pace"),
    ("today meets target early in the span", dict(value=85.0, baseline=20.0, today=BASE_ON + timedelta(days=3)),
     "on_pace"),
    ("baseline just above target, today just below it", dict(value=79.0, baseline=85.0), "behind"),
    ("at most, baseline meets target, today above it", dict(value=50.0, baseline=10.0, metric=_metric(direction="at_most", target=20)), "behind"),
    ("at most, baseline meets target, today meets it above the rising line",
     dict(value=15.0, baseline=10.0, metric=_metric(direction="at_most", target=20)), "on_pace"),
    ("at most, today exactly on target above the rising line",
     dict(value=20.0, baseline=10.0, metric=_metric(direction="at_most", target=20)), "on_pace"),
    ("at least, today exactly on target under the falling line", dict(value=80.0, baseline=90.0), "on_pace"),
    ("count metric", dict(value=9.0, baseline=10.0, metric=_metric(unit="count", direction="at_most", target=0)),
     "behind"),
    ("due today", dict(value=79.0, today=DUE), "behind"),
])
def test_pace_states(case, kwargs, state):
    got = _pace(**kwargs)
    assert got is not None, case
    assert got["state"] == state, case
    assert got["label"] == {"on_pace": "On pace", "behind": "Behind pace"}[state]


@pytest.mark.parametrize("case, kwargs", [
    ("past the due date", dict(today=DUE + timedelta(days=1))),
    ("proposed", dict(lifecycle="proposed")),
    ("closed achieved", dict(lifecycle="achieved")),
    ("closed missed", dict(lifecycle="missed")),
    ("archived", dict(archived=True)),
    ("maintain type", dict(kind="maintain")),
    ("no due date", dict(due=None)),
    ("no baseline", dict(baseline=None, baseline_on=None)),
    ("baseline on the due date", dict(baseline_on=DUE)),
    ("baseline after the due date", dict(baseline_on=DUE + timedelta(days=3))),
    ("no value today", dict(value=None)),
    ("no target", dict(metric=_metric(target=None))),
    ("no direction", dict(metric=_metric(direction=None))),
    ("milestone unit", dict(metric=_metric(unit=None))),
    ("no metric", dict(metric=None)),
])
def test_pace_is_null(case, kwargs):
    if case == "no metric":
        assert status_rules.pace("achieve", "active", False, DUE, None, 60.0, BASE_ON, 40.0, TODAY) is None
        return
    assert _pace(**kwargs) is None, case


def test_pace_shape_and_line():
    got = _pace(value=50.0)
    assert got["expected_value"] == pytest.approx(EXPECTED, abs=1e-4)
    assert (got["baseline_on"], got["baseline_value"], got["due_date"], got["target_value"]) == (
        BASE_ON, 40.0, DUE, 80.0)
    # Before the baseline the line has not started; on the due date it reaches the target.
    assert _pace(value=50.0, today=BASE_ON - timedelta(days=5))["expected_value"] == 40.0
    assert _pace(value=50.0, today=DUE)["expected_value"] == 80.0


# R5: today's value and the expected value are printed together, with the same
# number of decimals: the fewest of 0, 1, 2 at which the printed numbers compare
# the way the true ones do (stopping at 2 whatever the result).
@pytest.mark.parametrize("unit, value, expected, texts", [
    ("percent", 50.6, 50.9, ("50.6%", "50.9%")),      # whole numbers would both read 51%
    ("percent", 43.0, 52.4, ("43%", "52%")),
    ("percent", 50.2, 50.33, ("50.2%", "50.3%")),
    ("percent", 50.0, 49.96, ("50.00%", "49.96%")),   # equal at 0 and 1 decimals, apart at 2
    ("percent", 50.0, 49.999, ("50.00%", "50.00%")),  # stops at 2 decimals whatever the result
    ("percent", 61.0, 61.0, ("61%", "61%")),           # equal values print equal texts
    ("percent", 60.96, 60.96, ("61%", "61%")),
    ("count", 2.0, 3.0, ("2", "3")),                   # no unit suffix on a count
    ("count", 2.4, 2.6, ("2", "3")),                   # whole numbers already compare as the true ones do
    ("count", 2.4, 2.45, ("2.4", "2.5")),
])
def test_the_pace_line_words_both_numbers_together(unit, value, expected, texts):
    assert wording.pace_texts(unit, value, expected) == texts


def test_behind_pace_never_prints_two_equal_numbers():
    # Baseline 40 on 1 Sep, target 61.8 a hundred days later; fifty days in, expected is 50.9.
    due = BASE_ON + timedelta(days=100)
    got = _pace(value=50.6, metric=_metric(target=61.8), due=due, today=BASE_ON + timedelta(days=50))
    assert got["state"] == "behind"
    assert (got["value_text"], got["expected_text"]) == ("50.6%", "50.9%")
    on_line = _pace(value=got["expected_value"], metric=_metric(target=61.8), due=due,
                    today=BASE_ON + timedelta(days=50))
    assert on_line["state"] == "on_pace" and on_line["value_text"] == on_line["expected_text"]


# ---------------------------------------------------------------------------
# Database fixtures
# ---------------------------------------------------------------------------

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
    session.info["engine"] = engine
    try:
        yield session
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


async def _org(db):
    tag = _uuid.uuid4().hex[:10]
    org = Organization(name=f"isms-{tag}", slug=f"isms-{tag}")
    db.add(org)
    await db.flush()
    tag = _uuid.uuid4().hex[:10]
    user = User(google_sub=f"isms-{tag}", email=f"isms-{tag}@example.invalid", display_name="Admin")
    db.add(user)
    await db.flush()
    db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role="admin"))
    # Five controls, none implemented: controls implemented reads 0% today on a population it judges.
    for n in range(1, 6):
        db.add(ScopedControl(organization_id=org.id, scf_id=f"GOV-0{n}", selected=True, implementation_status=None))
    await db.flush()
    return org, Actor(user_id=user.id, action_source="ui")


async def _objective(db, org, actor, spec=None, **kw):
    spec = spec or MetricSpec(kind="live", measure_key="controls_implemented", target_value=80)
    body = dict(name=f"Objective {_uuid.uuid4().hex[:6]}", planned_actions="Do the work", metric=spec)
    body.update(kw)
    out = await service.create_objective(db, org.id, ObjectiveCreate(**body), actor, TODAY)
    return out["id"]


async def _active(db, objective_id):
    return (await db.execute(select(ObjectiveMetric).where(
        ObjectiveMetric.objective_id == objective_id, ObjectiveMetric.active_to.is_(None)))).scalar_one()


async def _store(db, org, metric, values, start=TODAY - timedelta(days=100), step=7, source="computed"):
    """Results for ``metric``: one per ``step`` days from ``start``, in the order given."""
    for n, value in enumerate(values):
        db.add(IsmsMetricResult(organization_id=org.id, objective_metric_id=metric.id,
                                measured_on=start + timedelta(days=n * step), value=value,
                                status="in_progress", source=source))
    await db.flush()


def _logged_spec():
    return MetricSpec(kind="logged", name="Staff trained", unit="percent", target_value=80, frequency="quarterly")


async def _log(db, org, metric, value, period, written, at):
    """A logged entry for ``period``, written on ``written`` at ``at``."""
    db.add(IsmsMetricResult(organization_id=org.id, objective_metric_id=metric.id, measured_on=written,
                            period_start=period, value=value, status="in_progress", source="logged",
                            created_at=at))
    await db.flush()


async def _set(db, objective_id, **fields):
    objective = await db.get(SecurityObjective, objective_id)
    for k, v in fields.items():
        setattr(objective, k, v)
    await db.flush()


async def _one(db, org, objective_id):
    return await service.get_objective(db, org.id, objective_id, TODAY)


# ---------------------------------------------------------------------------
# A1: trend
# ---------------------------------------------------------------------------

@requires_postgres
@pytest.mark.asyncio
class TestTrend:
    async def test_oldest_first_capped_at_twelve_numbers_only(self, db):
        org, actor = await _org(db)
        oid = await _objective(db, org, actor)
        metric = await _active(db, oid)
        await _store(db, org, metric, [float(v) for v in range(1, 16)])          # 15 results, values 1..15
        await _store(db, org, metric, [None], start=TODAY - timedelta(days=1))  # no number: left out
        trend = (await _one(db, org, oid))["reading"]["trend"]
        assert [p["value"] for p in trend] == [float(v) for v in range(4, 16)]
        dates = [p["measured_on"] for p in trend]
        assert dates == sorted(dates) and len(trend) == 12

    async def test_each_point_is_worded_by_the_server(self, db):
        # 2 of 13 is 15.384615...%: the tile tooltip prints this text, so it must not be the raw number.
        org, actor = await _org(db)
        oid = await _objective(db, org, actor)
        metric = await _active(db, oid)
        await _store(db, org, metric, [200 / 13, 79.6, 50.0])
        trend = (await _one(db, org, oid))["reading"]["trend"]
        assert [p["value_text"] for p in trend] == ["15%", "79.6%", "50%"]   # 79.6 never prints as the 80% target
        assert ObjectiveOut.model_validate(await _one(db, org, oid)).reading.trend[0].value_text == "15%"

    async def test_empty_list_never_null_and_on_the_wire(self, db):
        org, actor = await _org(db)
        oid = await _objective(db, org, actor)
        item = await _one(db, org, oid)
        assert item["reading"]["trend"] == [] and item["reading"]["pace"] is None
        assert ObjectiveOut.model_validate(item).reading.trend == []
        bare = (await service.create_objective(db, org.id, ObjectiveCreate(
            name="No metric yet", planned_actions="Decide"), actor, TODAY))["id"]
        assert (await _one(db, org, bare))["reading"]["trend"] == []

    async def test_only_results_of_the_same_measure(self, db):
        org, actor = await _org(db)
        oid = await _objective(db, org, actor, spec=MetricSpec(
            kind="live", measure_key="audit_programme_completed", target_value=100))
        old = await _active(db, oid)
        await _store(db, org, old, [10.0, 20.0], start=TODAY - timedelta(days=60))
        # A target change keeps the measure: its results stay on the line.
        await service.set_metric(db, org.id, oid, MetricSpec(
            kind="live", measure_key="audit_programme_completed", target_value=90), actor, "admin", TODAY)
        retargeted = await _active(db, oid)
        await _store(db, org, retargeted, [30.0], start=TODAY - timedelta(days=20))
        assert [p["value"] for p in (await _one(db, org, oid))["reading"]["trend"]] == [10.0, 20.0, 30.0]
        # A change of settings is another measure: the earlier results leave the trend.
        await service.set_metric(db, org.id, oid, MetricSpec(
            kind="live", measure_key="audit_programme_completed", params={"months": 24}, target_value=90),
            actor, "admin", TODAY)
        changed = await _active(db, oid)
        await _store(db, org, changed, [50.0], start=TODAY - timedelta(days=5))
        assert [p["value"] for p in (await _one(db, org, oid))["reading"]["trend"]] == [50.0]

    async def test_closed_and_proposed_objectives_keep_their_stored_trend(self, db):
        org, actor = await _org(db)
        oid = await _objective(db, org, actor)
        await _store(db, org, await _active(db, oid), [1.0, 2.0])
        for lifecycle in ("achieved", "missed", "proposed"):
            await _set(db, oid, lifecycle_status=lifecycle)
            assert [p["value"] for p in (await _one(db, org, oid))["reading"]["trend"]] == [1.0, 2.0], lifecycle

    async def test_trend_does_not_cross_objectives_or_organisations(self, db):
        org, actor = await _org(db)
        other_org, other_actor = await _org(db)
        a = await _objective(db, org, actor)
        b = await _objective(db, org, actor)
        c = await _objective(db, other_org, other_actor)
        await _store(db, org, await _active(db, a), [1.0])
        await _store(db, org, await _active(db, b), [2.0, 3.0])
        await _store(db, other_org, await _active(db, c), [9.0])
        listed = await service.list_objectives(db, org.id, TODAY)
        by_id = {i["id"]: i["reading"]["trend"] for i in listed["items"]}
        assert [p["value"] for p in by_id[a]] == [1.0]
        assert [p["value"] for p in by_id[b]] == [2.0, 3.0]
        assert c not in by_id

    @pytest.mark.parametrize("count", [1, 6])
    async def test_one_query_for_the_whole_list(self, db, count):
        org, actor = await _org(db)
        ids = [await _objective(db, org, actor) for _ in range(count)]
        for oid in ids:
            await _store(db, org, await _active(db, oid), [1.0, 2.0, 3.0])
        objectives = (await db.execute(select(SecurityObjective).where(
            SecurityObjective.id.in_(ids)))).scalars().all()
        statements = []

        def seen(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        sync_engine = db.info["engine"].sync_engine
        event.listen(sync_engine, "before_cursor_execute", seen)
        try:
            items = await service._build(db, org.id, objectives, TODAY)
        finally:
            event.remove(sync_engine, "before_cursor_execute", seen)
        assert all(len(i["reading"]["trend"]) == 3 for i in items)
        on_results = [s for s in statements if "isms_metric_results" in s]
        assert len(on_results) == 1, on_results


# ---------------------------------------------------------------------------
# A2: pace on the list, single and detail calls
# ---------------------------------------------------------------------------

@requires_postgres
@pytest.mark.asyncio
class TestPaceOnCalls:
    async def _achieve(self, db, org, actor, baseline=(BASE_ON, 50.0)):
        oid = await _objective(db, org, actor, objective_type="achieve", due_date=DUE)
        await _set(db, oid, lifecycle_status="active")
        if baseline:
            await _store(db, org, await _active(db, oid), [baseline[1]], start=baseline[0])
        return oid

    async def test_behind_pace_everywhere_and_status_unchanged(self, db):
        org, actor = await _org(db)
        oid = await self._achieve(db, org, actor)   # baseline 50% on 1 Sep, today 0%: behind
        single = await _one(db, org, oid)
        pace = single["reading"]["pace"]
        assert pace["state"] == "behind" and pace["label"] == "Behind pace"
        assert pace["baseline_on"] == BASE_ON and pace["baseline_value"] == 50.0
        assert pace["due_date"] == DUE and pace["target_value"] == 80.0
        assert (pace["value_text"], pace["expected_text"]) == ("0%", "58%")   # today 0%, expected 57.75%
        assert ObjectiveOut.model_validate(single).reading.pace.value_text == "0%"
        assert single["reading"]["status"] == "in_progress"  # pace never changes the status
        listed = await service.list_objectives(db, org.id, TODAY)
        assert next(i for i in listed["items"] if i["id"] == oid)["reading"]["pace"] == pace
        detail = await service.metric_detail(db, org.id, oid, TODAY)
        MetricDetail.model_validate(detail)
        assert detail["objective"]["reading"]["pace"] == pace

    async def test_status_is_the_same_with_and_without_pace(self, db, monkeypatch):
        org, actor = await _org(db)
        oid = await self._achieve(db, org, actor)
        with_pace = await _one(db, org, oid)
        monkeypatch.setattr(service, "reading_pace", lambda *a, **k: None)
        without = await _one(db, org, oid)
        assert with_pace["reading"]["pace"] is not None and without["reading"]["pace"] is None
        assert with_pace["reading"]["status"] == without["reading"]["status"]

    async def test_on_pace_when_the_value_is_ahead_of_the_line(self, db):
        org, actor = await _org(db)
        oid = await self._achieve(db, org, actor, baseline=(BASE_ON, 0.0))
        for n in range(1, 6):
            db.add(ScopedControl(organization_id=org.id, scf_id=f"TST-0{n}", selected=True,
                                 implementation_status="implemented"))
        await db.flush()
        pace = (await _one(db, org, oid))["reading"]["pace"]   # today 50%, expected about 21%
        assert pace["state"] == "on_pace" and pace["label"] == "On pace"

    async def test_null_without_a_baseline_or_when_proposed_or_maintain(self, db):
        org, actor = await _org(db)
        no_baseline = await self._achieve(db, org, actor, baseline=None)
        assert (await _one(db, org, no_baseline))["reading"]["pace"] is None
        proposed = await self._achieve(db, org, actor)
        await _set(db, proposed, lifecycle_status="proposed")
        assert (await _one(db, org, proposed))["reading"]["pace"] is None
        maintain = await self._achieve(db, org, actor)
        await _set(db, maintain, objective_type="maintain")
        assert (await _one(db, org, maintain))["reading"]["pace"] is None

    async def test_baseline_is_the_earliest_same_measure_result(self, db):
        org, actor = await _org(db)
        oid = await self._achieve(db, org, actor, baseline=None)
        metric = await _active(db, oid)
        # Thirteen results: the baseline is the first even though the trend keeps only the last twelve.
        await _store(db, org, metric, [float(v) for v in range(30, 43)], start=BASE_ON, step=5)
        reading = (await _one(db, org, oid))["reading"]
        assert reading["pace"]["baseline_on"] == BASE_ON and reading["pace"]["baseline_value"] == 30.0
        assert reading["trend"][0]["value"] == 31.0 and len(reading["trend"]) == 12

    async def test_audits_measure_with_a_profile_reads_pace_too(self, db):
        org, actor = await _org(db)
        db.add(IsmsProfile(organization_id=org.id, audit_programme=[
            {"area": "A", "status": "completed", "planned_date": "2026-03-01"}]))
        await db.flush()
        oid = await _objective(db, org, actor, objective_type="achieve", due_date=DUE, spec=MetricSpec(
            kind="live", measure_key="audit_programme_completed", target_value=100))
        await _set(db, oid, lifecycle_status="active")
        await _store(db, org, await _active(db, oid), [20.0], start=BASE_ON)
        pace = (await _one(db, org, oid))["reading"]["pace"]
        assert pace["state"] == "on_pace"   # today 100%

    async def test_no_pace_beside_insufficient_data(self, db):
        # R4: a live percentage under its minimum population still has a value, but it is not judged.
        org, actor = await _org(db)
        oid = await self._achieve(db, org, actor)
        metric = await _active(db, oid)
        metric.min_population = 10   # the org has five controls
        await db.flush()
        reading = (await _one(db, org, oid))["reading"]
        assert (reading["status"], reading["value"]) == ("insufficient_data", 0.0)
        assert reading["pace"] is None

    async def test_no_pace_beside_measurement_overdue(self, db):
        # R4: a logged objective last logged for an earlier quarter shows a stale value, not judged.
        org, actor = await _org(db)
        oid = await _objective(db, org, actor, spec=_logged_spec(), objective_type="achieve", due_date=DUE)
        await _set(db, oid, lifecycle_status="active")
        await _log(db, org, await _active(db, oid), 4.0, date(2026, 7, 1), date(2026, 8, 10),
                   datetime(2026, 8, 10, 9))
        reading = (await _one(db, org, oid))["reading"]
        assert (reading["status"], reading["value"]) == ("measurement_overdue", 4.0)
        assert reading["pace"] is None


# ---------------------------------------------------------------------------
# R3: a logged entry is one point per period, placed on its period
# ---------------------------------------------------------------------------

@requires_postgres
@pytest.mark.asyncio
class TestLoggedSeries:
    Q2, Q3, Q4 = date(2026, 4, 1), date(2026, 7, 1), date(2026, 10, 1)

    async def _logged(self, db):
        org, actor = await _org(db)
        oid = await _objective(db, org, actor, spec=_logged_spec(), objective_type="achieve", due_date=DUE)
        await _set(db, oid, lifecycle_status="active")
        return org, oid, await _active(db, oid)

    async def test_a_correction_replaces_the_entry_it_corrects(self, db):
        org, oid, metric = await self._logged(db)
        # 40 typed by mistake for Q3, corrected to 4 an hour later; then Q4.
        await _log(db, org, metric, 40.0, self.Q3, date(2026, 9, 10), datetime(2026, 9, 10, 9))
        await _log(db, org, metric, 4.0, self.Q3, date(2026, 9, 10), datetime(2026, 9, 10, 10))
        await _log(db, org, metric, 30.0, self.Q4, date(2026, 10, 15), datetime(2026, 10, 15, 9))
        reading = (await _one(db, org, oid))["reading"]
        assert [(p["measured_on"], p["value"]) for p in reading["trend"]] == [(self.Q3, 4.0), (self.Q4, 30.0)]
        assert (reading["pace"]["baseline_on"], reading["pace"]["baseline_value"]) == (self.Q3, 4.0)

    async def test_an_earlier_period_logged_later_sorts_first(self, db):
        org, oid, metric = await self._logged(db)
        await _log(db, org, metric, 30.0, self.Q4, date(2026, 10, 15), datetime(2026, 10, 15, 9))
        await _log(db, org, metric, 10.0, self.Q2, TODAY, datetime(2026, 11, 18, 9))   # backfilled today
        reading = (await _one(db, org, oid))["reading"]
        assert [(p["measured_on"], p["value"]) for p in reading["trend"]] == [(self.Q2, 10.0), (self.Q4, 30.0)]
        assert reading["trend"][-1]["value"] == reading["value"] == 30.0   # the line ends on the big number
        assert (reading["pace"]["baseline_on"], reading["pace"]["baseline_value"]) == (self.Q2, 10.0)

    async def test_the_newest_twelve_periods_by_period_not_by_day_written(self, db):
        org, oid, metric = await self._logged(db)
        periods = [date(2023 + (n + 3) // 4, 3 * ((n + 3) % 4) + 1, 1) for n in range(14)]   # 2023 Q4 .. 2027 Q1
        periods = [p for p in periods if p <= self.Q4]                                        # 13 quarters
        # Written newest period first, so the day written runs the opposite way to the period.
        for n, period in enumerate(reversed(periods)):
            await _log(db, org, metric, float(n), period, date(2026, 10, 2) + timedelta(days=n),
                       datetime(2026, 10, 2, 9) + timedelta(days=n))
        trend = (await _one(db, org, oid))["reading"]["trend"]
        assert [p["measured_on"] for p in trend] == periods[-12:]
