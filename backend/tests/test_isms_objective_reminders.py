# SPDX-License-Identifier: AGPL-3.0-only
"""Security objective reminders (clauses 6.2 and 9.1), and their bell target.

These tests pin:

* the three types and their dedup instants: a logged measurement is
  announced once per period and again in the next; a due or review date is
  announced once when it enters the 14 days and once when it is reached,
  and a changed date opens a fresh window;
* the recipients: the named owner, else the team's primary and delegate,
  else the admins; a person who has left the organisation is never one;
* only active, unarchived objectives are swept; one failing objective does
  not stop the others; two organisations stay apart;
* the events are registered on the ownership route;
* the notification target: a live objective resolves to its id, an archived
  or deleted one to nothing, and a failing lookup leaves the other kinds.

The sweep runs on a session whose transaction is always rolled back. The
dedup guard reads ``created_at``, which the database stamps with the real
clock, so each simulated day stamps the rows it wrote with that day before
the next sweep runs (:func:`_sweep`).
"""
import uuid as _uuid
from datetime import date, datetime, time
from types import SimpleNamespace
from typing import Any, List

import pytest
import pytest_asyncio
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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
from services.isms_metrics import reminders
from services.notification_targets import resolve_reference_keys
from services.owner_resolution import EVENTS, ROUTE_OWNERSHIP

from test_journey_service import DATABASE_URL, requires_postgres  # noqa: F401


# ---------------------------------------------------------------------------
# No database
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", reminders.NOTIFICATION_TYPES)
def test_the_three_events_route_through_the_ownership_chain(key):
    assert EVENTS[key].routing == ROUTE_OWNERSHIP
    assert not EVENTS[key].escalates


@pytest.mark.parametrize("day,expected", [
    (date(2027, 3, 1), (True, date(2027, 3, 1))),       # the date itself: reached
    (date(2027, 2, 20), (True, date(2027, 2, 20))),      # passed: keyed on the date
    (date(2027, 3, 2), (False, date(2027, 2, 16))),      # tomorrow: the window opened 14 days before
    (date(2027, 3, 15), (False, date(2027, 3, 1))),      # 14 days away: the window opens today
    (date(2027, 3, 16), None),                           # 15 days away: not yet
    (None, None),
])
def test_the_notice_window_and_its_dedup_instant(day, expected):
    assert reminders.date_notice(day, date(2027, 3, 1)) == expected


def test_the_messages_are_one_plain_sentence_naming_the_objective():
    o = SimpleNamespace(ref="OBJ-09", name="Train everyone in security awareness")
    assert reminders.measurement_message(o, "quarterly", first=False) == (
        'Objective OBJ-09 "Train everyone in security awareness": this quarter\'s value has not been logged.'
    )
    assert reminders.measurement_message(o, "monthly", first=False).endswith("this month's value has not been logged.")
    assert reminders.measurement_message(o, "quarterly", first=True) == (
        'Objective OBJ-09 "Train everyone in security awareness": no value has been logged yet.'
    )
    assert reminders.due_message(o, date(2027, 3, 1), reached=False) == (
        'Objective OBJ-09 "Train everyone in security awareness" is due on 2027-03-01.'
    )
    assert reminders.due_message(o, date(2027, 3, 1), reached=True) == (
        'Objective OBJ-09 "Train everyone in security awareness" reached its due date on 2027-03-01 '
        "and is still active."
    )
    assert reminders.review_message(o, date(2027, 3, 1), reached=False) == (
        'Objective OBJ-09 "Train everyone in security awareness" is due for review on 2027-03-01.'
    )
    assert reminders.review_message(o, date(2027, 3, 1), reached=True) == (
        'Objective OBJ-09 "Train everyone in security awareness" reached its review date on 2027-03-01.'
    )


def _notification(reference_type, reference_id):
    return SimpleNamespace(id=_uuid.uuid4(), reference_type=reference_type, reference_id=reference_id)


class _Result:
    def __init__(self, rows: List[Any]):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.statements: List[str] = []

    async def execute(self, stmt):
        self.statements.append(str(stmt))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return _Result(response)


@pytest.mark.asyncio
async def test_an_objective_notification_resolves_to_the_objective_id_and_asks_for_unarchived_rows():
    objective_id = _uuid.uuid4()
    n = _notification("security_objective", objective_id)
    session = _FakeSession([[SimpleNamespace(id=objective_id)]])
    assert await resolve_reference_keys(session, [n]) == {n.id: str(objective_id)}
    assert "security_objectives.archived_at IS NULL" in session.statements[0]


@pytest.mark.asyncio
async def test_a_failing_objective_lookup_keeps_the_other_kinds():
    tracking_id, scoped_id = _uuid.uuid4(), _uuid.uuid4()
    evidence = _notification("evidence", tracking_id)
    control = _notification("control", scoped_id)
    objective = _notification("security_objective", _uuid.uuid4())
    session = _FakeSession([
        [SimpleNamespace(id=tracking_id, evidence_id="E-HRS-16")],
        [SimpleNamespace(id=scoped_id, scf_id="AST-14")],
        RuntimeError("security_objectives is having a day"),
    ])
    resolved = await resolve_reference_keys(session, [evidence, control, objective])
    assert resolved == {evidence.id: "E-HRS-16", control.id: "AST-14"}


# ---------------------------------------------------------------------------
# Database-backed
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db():
    """A session on a transaction that is always rolled back."""
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
    """The sweep commits; inside the rolled-back test transaction that is a flush."""
    monkeypatch.setattr(db, "commit", db.flush)
    monkeypatch.setattr(db, "rollback", db.flush)


#: "From the first day of the period": what the tests that are not about timing run under.
FROM_DAY_ONE = {reminders.MEASUREMENT_REMINDER_DAYS_KEY: 366}


async def _org(db, settings=FROM_DAY_ONE):
    tag = _uuid.uuid4().hex[:10]
    org = Organization(name=f"isms-rem-{tag}", slug=f"isms-rem-{tag}", settings=dict(settings))
    db.add(org)
    await db.flush()
    return org


async def _user(db, org=None, role="editor"):
    tag = _uuid.uuid4().hex[:10]
    user = User(google_sub=f"isms-rem-{tag}", email=f"isms-rem-{tag}@example.invalid", display_name=f"P {tag}")
    db.add(user)
    await db.flush()
    if org is not None:
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=role))
        await db.flush()
    return user


async def _team(db, org, **roles):
    """A team with members by role: ``_team(db, org, primary=u1, member=u2)``."""
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


_REFS = iter(range(1, 10_000))


async def _objective(db, org, *, lifecycle="active", logged=False, results=(), archived=False, **fields):
    objective = SecurityObjective(
        organization_id=org.id, ref=f"OBJ-{next(_REFS):02d}", name=fields.pop("name", "Keep the lights on"),
        objective_type="maintain", lifecycle_status=lifecycle, source="custom",
        archived_at=datetime(2026, 1, 1) if archived else None, **fields,
    )
    db.add(objective)
    await db.flush()
    if logged:
        metric = ObjectiveMetric(
            organization_id=org.id, objective_id=objective.id, kind="logged", name="Training completion (%)",
            unit="percent", direction="at_least", target_value=95, warn_value=85, frequency="quarterly",
        )
        db.add(metric)
        await db.flush()
        for period in results:
            db.add(IsmsMetricResult(
                organization_id=org.id, objective_metric_id=metric.id, measured_on=period,
                period_start=period, value=97, status="on_track", source="logged",
            ))
        await db.flush()
    return objective


async def _sweep(db, day: date) -> None:
    """Run the sweep as if on ``day``: rows it writes are stamped with that day."""
    await reminders.check_and_notify_objectives(db, today=day)
    await db.execute(
        update(Notification).where(Notification.created_at == func.now())
        .values(created_at=datetime.combine(day, time(7, 45)))
    )
    await db.flush()


async def _rows(db, objective, kind=None):
    query = select(Notification).where(Notification.reference_id == objective.id)
    if kind:
        query = query.where(Notification.type == kind)
    return list((await db.execute(query.order_by(Notification.created_at))).scalars().all())


async def _recipients(db, objective, kind=None):
    return {n.user_id for n in await _rows(db, objective, kind)}


@requires_postgres
@pytest.mark.asyncio
class TestWhenAndHowOften:
    async def test_an_overdue_measurement_notifies_once_per_period_and_again_in_the_next(self, db, no_commit):
        org = await _org(db)
        owner = await _user(db, org)
        # Last logged for the July quarter; on 18 November the October quarter is overdue.
        objective = await _objective(db, org, logged=True, results=[date(2026, 7, 1)], owner_user_id=owner.id)

        await _sweep(db, date(2026, 11, 18))
        await _sweep(db, date(2026, 11, 19))
        await _sweep(db, date(2026, 12, 31))
        rows = await _rows(db, objective, reminders.MEASUREMENT_DUE)
        assert len(rows) == 1
        assert rows[0].message == f'Objective {objective.ref} "Keep the lights on": this quarter\'s value has not been logged.'
        assert rows[0].reference_type == "security_objective" and rows[0].organization_id == org.id

        await _sweep(db, date(2027, 1, 4))
        await _sweep(db, date(2027, 1, 5))
        assert len(await _rows(db, objective, reminders.MEASUREMENT_DUE)) == 2

    async def test_a_measurement_logged_for_this_period_is_not_announced(self, db, no_commit):
        org = await _org(db)
        objective = await _objective(db, org, logged=True, results=[date(2026, 10, 1)])
        await _sweep(db, date(2026, 11, 18))
        assert await _rows(db, objective) == []

    async def test_a_first_measurement_due_is_announced_once_this_period(self, db, no_commit):
        org = await _org(db)
        await _user(db, org, "admin")
        objective = await _objective(db, org, logged=True)
        await _sweep(db, date(2026, 11, 18))
        await _sweep(db, date(2026, 11, 19))
        rows = await _rows(db, objective, reminders.MEASUREMENT_DUE)
        assert [r.message for r in rows] == [f'Objective {objective.ref} "Keep the lights on": no value has been logged yet.']

    @pytest.mark.parametrize("field,kind,coming,reached", [
        ("due_date", reminders.OBJECTIVE_DUE, "is due on 2027-03-01.",
         "reached its due date on 2027-03-01 and is still active."),
        ("review_date", reminders.REVIEW_DUE, "is due for review on 2027-03-01.",
         "reached its review date on 2027-03-01."),
    ])
    async def test_a_date_notifies_once_inside_the_window_and_once_when_reached(
        self, db, no_commit, field, kind, coming, reached,
    ):
        org = await _org(db)
        await _user(db, org, "admin")
        objective = await _objective(db, org, **{field: date(2027, 3, 1)})

        await _sweep(db, date(2027, 2, 14))    # 15 days away: not yet
        assert await _rows(db, objective, kind) == []
        await _sweep(db, date(2027, 2, 19))    # inside the 14 days
        await _sweep(db, date(2027, 2, 20))    # the next day adds nothing
        assert [r.message.split('" ', 1)[1] for r in await _rows(db, objective, kind)] == [coming]

        await _sweep(db, date(2027, 3, 2))     # the day after the date: once more
        await _sweep(db, date(2027, 3, 3))
        await _sweep(db, date(2027, 4, 1))
        assert [r.message.split('" ', 1)[1] for r in await _rows(db, objective, kind)] == [coming, reached]

    async def test_a_changed_due_date_opens_a_fresh_window(self, db, no_commit):
        org = await _org(db)
        await _user(db, org, "admin")
        objective = await _objective(db, org, due_date=date(2027, 3, 1))
        await _sweep(db, date(2027, 2, 19))
        assert len(await _rows(db, objective, reminders.OBJECTIVE_DUE)) == 1

        objective.due_date = date(2027, 6, 1)
        await db.flush()
        await _sweep(db, date(2027, 2, 20))    # far off now: nothing
        await _sweep(db, date(2027, 5, 20))    # inside the new window
        rows = await _rows(db, objective, reminders.OBJECTIVE_DUE)
        assert [r.message.rsplit(" ", 1)[1] for r in rows] == ["2027-03-01.", "2027-06-01."]

    @pytest.mark.parametrize("state", [
        {"lifecycle": "proposed"}, {"lifecycle": "achieved"}, {"lifecycle": "missed"}, {"archived": True},
    ])
    async def test_nothing_for_proposed_closed_or_archived_objectives(self, db, no_commit, state):
        org = await _org(db)
        await _user(db, org, "admin")
        objective = await _objective(
            db, org, logged=True, due_date=date(2026, 11, 1), review_date=date(2026, 11, 1), **state,
        )
        await _sweep(db, date(2026, 11, 18))
        assert await _rows(db, objective) == []

    async def test_the_sweep_returns_the_rows_it_wrote(self, db, no_commit):
        org = await _org(db)
        await _user(db, org, "admin")
        await _user(db, org, "admin")
        await _objective(db, org, due_date=date(2026, 11, 20))
        before = await db.scalar(select(func.count()).select_from(Notification).where(
            Notification.organization_id == org.id))
        written = await reminders.check_and_notify_objectives(db, today=date(2026, 11, 18))
        after = await db.scalar(select(func.count()).select_from(Notification).where(
            Notification.organization_id == org.id))
        assert after - before == 2 and written >= 2


@requires_postgres
@pytest.mark.asyncio
class TestMeasurementTiming:
    """The organisation's ``isms_measurement_reminder_days`` decides when in the period the reminder goes out."""

    async def _overdue(self, db, settings):
        org = await _org(db, settings)
        owner = await _user(db, org)
        # Quarterly, last logged for the July quarter: the October quarter (ends 31 December) is overdue.
        return await _objective(db, org, logged=True, results=[date(2026, 7, 1)], owner_user_id=owner.id)

    async def test_with_no_setting_the_reminder_waits_for_the_last_fourteen_days(self, db, no_commit):
        objective = await self._overdue(db, {})
        for day in (date(2026, 10, 1), date(2026, 11, 18), date(2026, 12, 17)):
            await _sweep(db, day)
        assert await _rows(db, objective, reminders.MEASUREMENT_DUE) == []

        await _sweep(db, date(2026, 12, 18))
        await _sweep(db, date(2026, 12, 19))
        await _sweep(db, date(2026, 12, 31))
        assert len(await _rows(db, objective, reminders.MEASUREMENT_DUE)) == 1

        # The next quarter is a new period: quiet until 18 March, then once.
        await _sweep(db, date(2027, 1, 4))
        assert len(await _rows(db, objective, reminders.MEASUREMENT_DUE)) == 1
        await _sweep(db, date(2027, 3, 18))
        assert len(await _rows(db, objective, reminders.MEASUREMENT_DUE)) == 2

    async def test_a_longer_lead_brings_it_forward_and_from_day_one_is_the_first_day(self, db, no_commit):
        thirty = await self._overdue(db, {reminders.MEASUREMENT_REMINDER_DAYS_KEY: 30})
        day_one = await self._overdue(db, FROM_DAY_ONE)
        await _sweep(db, date(2026, 10, 1))
        assert len(await _rows(db, day_one, reminders.MEASUREMENT_DUE)) == 1
        assert await _rows(db, thirty, reminders.MEASUREMENT_DUE) == []
        await _sweep(db, date(2026, 12, 1))
        assert await _rows(db, thirty, reminders.MEASUREMENT_DUE) == []
        await _sweep(db, date(2026, 12, 2))
        assert len(await _rows(db, thirty, reminders.MEASUREMENT_DUE)) == 1
        assert len(await _rows(db, day_one, reminders.MEASUREMENT_DUE)) == 1

    async def test_two_organisations_each_follow_their_own_setting(self, db, no_commit):
        early = await self._overdue(db, {reminders.MEASUREMENT_REMINDER_DAYS_KEY: 60})
        late = await self._overdue(db, {reminders.MEASUREMENT_REMINDER_DAYS_KEY: 7})
        await _sweep(db, date(2026, 11, 18))  # 44 days of the quarter remain
        assert len(await _rows(db, early, reminders.MEASUREMENT_DUE)) == 1
        assert await _rows(db, late, reminders.MEASUREMENT_DUE) == []
        await _sweep(db, date(2026, 12, 25))
        assert len(await _rows(db, early, reminders.MEASUREMENT_DUE)) == 1
        assert len(await _rows(db, late, reminders.MEASUREMENT_DUE)) == 1

    async def test_a_first_measurement_waits_for_the_window_too(self, db, no_commit):
        org = await _org(db, {})
        await _user(db, org, "admin")
        objective = await _objective(db, org, logged=True)
        await _sweep(db, date(2026, 11, 18))
        assert await _rows(db, objective, reminders.MEASUREMENT_DUE) == []
        await _sweep(db, date(2026, 12, 20))
        assert len(await _rows(db, objective, reminders.MEASUREMENT_DUE)) == 1

    async def test_a_metric_with_no_calendar_period_is_reminded_straight_away(self, db, no_commit):
        org = await _org(db, {})
        await _user(db, org, "admin")
        objective = await _objective(db, org, logged=True)
        await db.execute(
            update(ObjectiveMetric).where(ObjectiveMetric.objective_id == objective.id).values(frequency=None)
        )
        await db.flush()
        # Days well after the metric row was made (now): the reminder is deduplicated from that instant.
        # 10 February is 49 days from the end of a quarter, so only "no period" can open the window.
        await _sweep(db, date(2027, 2, 10))
        await _sweep(db, date(2027, 2, 11))
        assert len(await _rows(db, objective, reminders.MEASUREMENT_DUE)) == 1

    async def test_the_due_date_reminder_does_not_follow_the_setting(self, db, no_commit):
        org = await _org(db, {reminders.MEASUREMENT_REMINDER_DAYS_KEY: 1})
        await _user(db, org, "admin")
        objective = await _objective(db, org, due_date=date(2026, 11, 25))
        await _sweep(db, date(2026, 11, 18))
        assert len(await _rows(db, objective, reminders.OBJECTIVE_DUE)) == 1


@requires_postgres
@pytest.mark.asyncio
class TestRecipients:
    async def test_the_named_owner_gets_it_and_the_admins_and_team_do_not(self, db, no_commit):
        org = await _org(db)
        owner = await _user(db, org, "editor")
        await _user(db, org, "admin")
        team = await _team(db, org, primary=await _user(db, org))
        objective = await _objective(db, org, due_date=date(2026, 11, 20), owner_user_id=owner.id,
                                     owning_team_id=team.id)
        await _sweep(db, date(2026, 11, 18))
        assert await _recipients(db, objective) == {owner.id}

    async def test_no_owner_falls_to_the_teams_primary_and_delegate(self, db, no_commit):
        org = await _org(db)
        await _user(db, org, "admin")
        primary, delegate, member = await _user(db, org), await _user(db, org), await _user(db, org)
        team = await _team(db, org, primary=primary, delegate=delegate, member=member)
        objective = await _objective(db, org, due_date=date(2026, 11, 20), owning_team_id=team.id)
        await _sweep(db, date(2026, 11, 18))
        assert await _recipients(db, objective) == {primary.id, delegate.id}

    async def test_no_owner_and_no_team_falls_to_the_admins(self, db, no_commit):
        org = await _org(db)
        a1, a2 = await _user(db, org, "admin"), await _user(db, org, "admin")
        await _user(db, org, "editor")
        objective = await _objective(db, org, due_date=date(2026, 11, 20))
        await _sweep(db, date(2026, 11, 18))
        assert await _recipients(db, objective) == {a1.id, a2.id}

    async def test_an_owner_who_has_left_the_organisation_is_not_a_recipient(self, db, no_commit):
        org = await _org(db)
        admin = await _user(db, org, "admin")
        # A real account that left this organisation and is still a member of
        # another: the membership check must be for this organisation.
        gone = await _user(db, await _org(db))
        primary = await _user(db, org)
        team = await _team(db, org, primary=primary)
        with_team = await _objective(db, org, due_date=date(2026, 11, 20), owner_user_id=gone.id,
                                     owning_team_id=team.id)
        without_team = await _objective(db, org, due_date=date(2026, 11, 20), owner_user_id=gone.id)
        await _sweep(db, date(2026, 11, 18))
        assert await _recipients(db, with_team) == {primary.id}
        assert await _recipients(db, without_team) == {admin.id}

    async def test_two_organisations_stay_apart(self, db, no_commit):
        org_a, org_b = await _org(db), await _org(db)
        admin_a, admin_b = await _user(db, org_a, "admin"), await _user(db, org_b, "admin")
        a = await _objective(db, org_a, due_date=date(2026, 11, 20))
        b = await _objective(db, org_b, due_date=date(2026, 11, 20))
        await _sweep(db, date(2026, 11, 18))
        assert [(n.user_id, n.organization_id) for n in await _rows(db, a)] == [(admin_a.id, org_a.id)]
        assert [(n.user_id, n.organization_id) for n in await _rows(db, b)] == [(admin_b.id, org_b.id)]


@requires_postgres
@pytest.mark.asyncio
class TestFailureIsolation:
    async def test_one_reminder_that_raises_stops_neither_the_others_of_that_objective_nor_the_sweep(
        self, db, no_commit, monkeypatch,
    ):
        org = await _org(db)
        await _user(db, org, "admin")
        broken = await _objective(db, org, due_date=date(2026, 11, 20), name="Broken")
        fine = await _objective(db, org, due_date=date(2026, 11, 20), name="Fine")
        real = reminders._measurement_notice

        async def explode_on_broken(db_, objective, today):
            if objective.id == broken.id:
                raise RuntimeError("this objective's measurement is having a day")
            return await real(db_, objective, today)

        rollbacks = []
        flush = db.flush

        async def counted_rollback():
            rollbacks.append(1)
            await flush()

        monkeypatch.setattr(reminders, "_measurement_notice", explode_on_broken)
        monkeypatch.setattr(db, "rollback", counted_rollback)
        await _sweep(db, date(2026, 11, 18))
        # The failed reminder was rolled back once; the due-date reminder of
        # the same objective, and the other objective's, were still written.
        assert len(rollbacks) == 1
        assert [n.type for n in await _rows(db, broken)] == [reminders.OBJECTIVE_DUE]
        assert [n.type for n in await _rows(db, fine)] == [reminders.OBJECTIVE_DUE]


@requires_postgres
@pytest.mark.asyncio
class TestTarget:
    async def test_a_live_objective_resolves_and_an_archived_or_deleted_one_does_not(self, db):
        org = await _org(db)
        live = await _objective(db, org)
        archived = await _objective(db, org, archived=True)
        notes = [_notification("security_objective", i) for i in (live.id, archived.id, _uuid.uuid4())]
        assert await resolve_reference_keys(db, notes) == {notes[0].id: str(live.id)}
