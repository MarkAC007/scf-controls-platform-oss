# SPDX-License-Identifier: AGPL-3.0-only
"""Phase 3 B3: the cross-client view, ``GET /api/isms-metrics/portfolio``.

Tenant isolation is the main risk, so most of these tests are about who
sees which organisation:

* a direct member sees exactly the organisations they belong to;
* a consultant sees a client only while the relationship is ``active``
  (pending and suspended give nothing);
* a user API key sees only the organisation the key is for, even when its
  owner belongs to more;
* the static key sees nothing outside single-tenant mode;
* a platform admin sees only organisations they belong to or consult for;
* the route takes no organisation from the caller.

Then the numbers: the counts are the dashboard's own (``summarise`` over
``derive_status``) read from stored rows; a live metric with no stored row is
``not_measured`` and makes the row ``stale``; the cost is a fixed number of
statements whatever the number of organisations; nothing is written, seeded
or committed.
"""
import hashlib
import inspect
import uuid as _uuid
from datetime import date, datetime, timedelta

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import delete, event, func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import auth as auth_mod
import catalog_models  # noqa: F401 — registers SystemCatalogTemplate for the ApiKey mapper
from api import security_objectives as api
from models import (
    ApiKey,
    ConsultantClientRelationship,
    ConsultantProfile,
    IsmsMetricResult,
    ObjectiveMetric,
    Organization,
    OrganizationMember,
    ScopedControl,
    SecurityObjective,
    User,
)
from services.isms_metrics import portfolio, service
from services.isms_metrics.schemas import MetricSpec, ObjectiveCreate, Portfolio

from test_journey_service import DATABASE_URL, requires_postgres  # noqa: F401

TODAY = date.today()
YESTERDAY = TODAY - timedelta(days=1)


def test_the_route_takes_no_organisation_from_the_caller():
    (route,) = [r for r in api.router.routes if r.path == "/isms-metrics/portfolio"]
    assert route.methods == {"GET"}
    params = set(inspect.signature(route.endpoint).parameters)
    assert params == {"user", "db"}, params
    assert inspect.signature(route.endpoint).parameters["user"].default.dependency is auth_mod.require_auth


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
    session.info["engine"] = engine
    try:
        yield session
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


@pytest.fixture
def no_commit(db, monkeypatch):
    async def refuse():
        raise AssertionError("the portfolio must not commit")

    monkeypatch.setattr(db, "commit", refuse)


async def _org(db, name=None):
    tag = _uuid.uuid4().hex[:10]
    org = Organization(name=name or f"isms-pf-{tag}", slug=f"isms-pf-{tag}")
    db.add(org)
    await db.flush()
    return org


async def _user(db, *orgs, role="viewer", platform_admin=False):
    tag = _uuid.uuid4().hex[:10]
    user = User(google_sub=f"isms-pf-{tag}", email=f"isms-pf-{tag}@example.invalid", display_name=f"P {tag}",
                is_platform_admin=platform_admin)
    db.add(user)
    await db.flush()
    for org in orgs:
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=role))
    await db.flush()
    return user


def _browser(user):
    return auth_mod.User(user_id=user.google_sub, email=user.email, auth_method="google", db_id=str(user.id))


def _key(user, org, role="viewer"):
    caller = auth_mod.User(user_id=user.google_sub, email=user.email, auth_method="user_api_key",
                           db_id=str(user.id))
    caller._api_key_org_id, caller._api_key_role = org.id, role
    return caller


async def _consult(db, user, org, status):
    profile = (await db.execute(select(ConsultantProfile).where(ConsultantProfile.user_id == user.id))
               ).scalar_one_or_none()
    if profile is None:
        profile = ConsultantProfile(user_id=user.id)
        db.add(profile)
        await db.flush()
    db.add(ConsultantClientRelationship(consultant_id=profile.id, organization_id=org.id, status=status))
    await db.flush()


_REFS = iter(range(1, 100_000))
LIVE = dict(kind="live", measure_key="controls_implemented", unit="percent", direction="at_least",
            target_value=80, warn_value=60)


async def _objective(db, org, *, lifecycle="active", spec=None, rows=(), archived=False, **fields):
    """An objective with its metric in force and stored rows ``(measured_on, value, source[, period])``."""
    objective = SecurityObjective(
        organization_id=org.id, ref=f"OBJ-{next(_REFS):02d}", name=fields.pop("name", "Keep it safe"),
        objective_type=fields.pop("objective_type", "maintain"), lifecycle_status=lifecycle, source="custom",
        archived_at=datetime(2026, 1, 1) if archived else None, **fields,
    )
    db.add(objective)
    await db.flush()
    metric = ObjectiveMetric(organization_id=org.id, objective_id=objective.id, **(spec or LIVE))
    db.add(metric)
    await db.flush()
    for n, row in enumerate(rows):  # created in list order, a minute apart
        measured_on, value, source = row[:3]
        db.add(IsmsMetricResult(
            created_at=datetime(2026, 1, 1) + timedelta(minutes=n),
            organization_id=org.id, objective_metric_id=metric.id, measured_on=measured_on,
            period_start=row[3] if len(row) > 3 else (measured_on if source == "logged" else None),
            value=value, denominator=10 if source == "computed" else None, status="on_track", source=source,
        ))
    await db.flush()
    return objective


async def _read(db, caller):
    out = await api.isms_metrics_portfolio(user=caller, db=db)
    Portfolio.model_validate(out)
    return out


def _ids(out):
    return {i["organization_id"] for i in out["items"]}


@requires_postgres
@pytest.mark.asyncio
class TestWhoSeesWhat:
    async def test_a_member_of_two_of_three_organisations_gets_two_rows(self, db, no_commit):
        a, b, c = await _org(db), await _org(db), await _org(db)
        user = await _user(db, a, b)
        out = await _read(db, _browser(user))
        assert _ids(out) == {a.id, b.id}
        assert {i["organization_name"] for i in out["items"]} == {a.name, b.name}
        assert c.id not in _ids(out)

    @pytest.mark.parametrize("status", ["pending", "suspended"])  # the CHECK allows these and active
    async def test_a_consultant_relationship_that_is_not_active_gives_no_row(self, db, no_commit, status):
        mine, client = await _org(db), await _org(db)
        consultant = await _user(db, mine)
        await _consult(db, consultant, client, status)
        assert _ids(await _read(db, _browser(consultant))) == {mine.id}

    async def test_an_active_consultant_relationship_gives_a_row(self, db, no_commit):
        mine, client = await _org(db), await _org(db)
        consultant = await _user(db, mine)
        await _consult(db, consultant, client, "active")
        assert _ids(await _read(db, _browser(consultant))) == {mine.id, client.id}

    async def test_a_user_api_key_gets_only_its_own_organisation(self, db, no_commit):
        a, b = await _org(db), await _org(db)
        owner = await _user(db, a, b, role="admin")
        assert _ids(await _read(db, _key(owner, b))) == {b.id}

    async def test_a_key_whose_owner_lost_access_cannot_reach_the_portfolio(self, db, monkeypatch):
        """B8: such a key is refused when it authenticates, before the route runs.

        ``validate_user_api_key`` (the ``scf_`` branch of ``require_auth``)
        refuses a key whose owner has no current access to the key's
        organisation (``auth._current_org_role``: a direct membership, or an
        *active* consultant relationship). This test removes the membership
        row directly, not through the member-removal route that also
        deactivates the key, so it is the authentication check that stops it.
        """
        monkeypatch.setattr(db, "commit", db.flush)  # validate_user_api_key commits last_used_at
        a, b, client = await _org(db), await _org(db), await _org(db)
        owner = await _user(db, a, b)
        consultant = await _user(db, a)
        await _consult(db, consultant, client, "active")

        async def issue(user, org):
            token = f"scf_{_uuid.uuid4().hex}"
            db.add(ApiKey(user_id=user.id, organization_id=org.id, name="collector", key_prefix=token[:8],
                          key_hash=hashlib.sha256(token.encode()).hexdigest(), role="viewer"))
            await db.flush()
            return token

        member_token, consultant_token = await issue(owner, b), await issue(consultant, client)
        # Positive control: while access holds, each key authenticates and sees its one organisation.
        assert _ids(await _read(db, await auth_mod.validate_user_api_key(member_token, db))) == {b.id}
        assert _ids(await _read(db, await auth_mod.validate_user_api_key(consultant_token, db))) == {client.id}

        await db.execute(delete(OrganizationMember).where(
            OrganizationMember.organization_id == b.id, OrganizationMember.user_id == owner.id))
        await db.execute(update(ConsultantClientRelationship).where(
            ConsultantClientRelationship.organization_id == client.id).values(status="suspended"))
        await db.flush()
        for token in (member_token, consultant_token):
            with pytest.raises(HTTPException) as exc:
                await auth_mod.validate_user_api_key(token, db)
            assert exc.value.status_code == 401
            assert "no longer has access" in exc.value.detail

    async def test_the_static_key_sees_nothing_outside_single_tenant_mode(self, db, no_commit, monkeypatch):
        await _org(db)
        monkeypatch.setattr(auth_mod, "is_single_tenant_active", lambda: False)
        static = auth_mod.User(user_id="api_user", email=None, auth_method="api_key")
        assert (await _read(db, static))["items"] == []

    async def test_r7_a_pinned_static_key_sees_only_its_organisation(self, db, no_commit, monkeypatch):
        from services import single_tenant
        pinned, later = await _org(db), await _org(db)
        monkeypatch.setattr(auth_mod, "is_single_tenant_active", lambda: True)
        monkeypatch.setattr(single_tenant, "single_tenant_org_id", lambda: str(pinned.id))
        static = auth_mod.User(user_id="api_user", email=None, auth_method="api_key")
        assert _ids(await _read(db, static)) == {pinned.id}
        assert later.id not in _ids(await _read(db, static))

    async def test_r7_an_unpinned_static_key_in_single_tenant_mode_is_unchanged(self, db, no_commit, monkeypatch):
        from services import single_tenant
        one, two = await _org(db), await _org(db)
        monkeypatch.setattr(auth_mod, "is_single_tenant_active", lambda: True)
        monkeypatch.setattr(single_tenant, "single_tenant_org_id", lambda: None)
        static = auth_mod.User(user_id="api_user", email=None, auth_method="api_key")
        assert {one.id, two.id} <= _ids(await _read(db, static))

    async def test_a_platform_admin_sees_only_their_own_organisations(self, db, no_commit):
        mine, other = await _org(db), await _org(db)
        admin = await _user(db, mine, platform_admin=True)
        assert _ids(await _read(db, _browser(admin))) == {mine.id}
        assert other.id not in _ids(await _read(db, _browser(admin)))

    async def test_a_user_with_no_organisation_gets_no_rows(self, db, no_commit):
        loner = await _user(db)
        assert (await _read(db, _browser(loner)))["items"] == []
        nobody = auth_mod.User(user_id="x", email=None, auth_method="google", db_id=None)
        assert (await _read(db, nobody))["items"] == []

    async def test_counts_never_include_another_organisations_objectives(self, db, no_commit):
        a, b = await _org(db), await _org(db)
        user = await _user(db, a)
        await _objective(db, a, rows=[(TODAY, 90, "computed")])
        for _ in range(3):
            await _objective(db, b, rows=[(TODAY, 10, "computed")])
        (row,) = (await _read(db, _browser(user)))["items"]
        assert (row["objectives"], row["on_track"], row["off_track"]) == (1, 1, 0)


@requires_postgres
@pytest.mark.asyncio
class TestNumbers:
    async def test_counts_equal_the_dashboard_summary_when_tonights_row_exists(self, db, monkeypatch):
        monkeypatch.setattr(db, "commit", db.flush)
        org = await _org(db)
        admin = await _user(db, org, role="admin")
        actor = service.Actor(user_id=admin.id, action_source="ui")
        for n in range(1, 6):
            db.add(ScopedControl(organization_id=org.id, scf_id=f"GOV-0{n}", selected=True,
                                 implementation_status="implemented" if n <= 4 else None))
        await db.flush()
        for target in (70, 85, 95):
            out = await service.create_objective(db, org.id, ObjectiveCreate(
                name=f"Live {target}", planned_actions="x", metric=MetricSpec(
                    kind="live", measure_key="controls_implemented", target_value=target, warn_value=60)),
                actor, TODAY)
            await db.execute(update(SecurityObjective).where(SecurityObjective.id == out["id"])
                             .values(lifecycle_status="active"))
        logged = await service.create_objective(db, org.id, ObjectiveCreate(
            name="Training", planned_actions="x", metric=MetricSpec(
                kind="logged", name="Training (%)", unit="percent", direction="at_least", target_value=95,
                frequency="quarterly")), actor, TODAY)
        await db.execute(update(SecurityObjective).where(SecurityObjective.id == logged["id"])
                         .values(lifecycle_status="active"))
        await db.flush()
        await service.log_result(db, org.id, logged["id"], 90, actor, TODAY)
        await service.record_nightly_results(db, org.id, TODAY)
        summary = (await service.list_objectives(db, org.id, TODAY, seed=False))["summary"]
        (row,) = (await _read(db, _browser(admin)))["items"]
        for key in ("objectives", "on_track", "at_risk", "off_track", "needs_attention", "in_progress",
                    "proposed", "not_measured", "insufficient_data", "overdue", "achieved", "missed"):
            assert row[key] == summary[key], key
        # 80% implemented against 70, 85 and 95 (warn 60); 90% logged against 95.
        assert (row["on_track"], row["at_risk"], row["off_track"]) == (1, 2, 1)
        assert row["stale"] is False and row["last_measured_on"] == TODAY

    async def test_a_live_metric_with_no_stored_row_is_not_measured_and_stale(self, db, no_commit):
        org = await _org(db)
        user = await _user(db, org)
        await _objective(db, org)
        (row,) = (await _read(db, _browser(user)))["items"]
        assert (row["not_measured"], row["needs_attention"], row["stale"]) == (1, 1, True)
        assert row["last_measured_on"] is None

    @pytest.mark.parametrize("age, stale", [(0, False), (1, False), (2, True)])
    async def test_stale_when_the_newest_row_is_older_than_yesterday(self, db, no_commit, age, stale):
        org = await _org(db)
        user = await _user(db, org)
        day = TODAY - timedelta(days=age)
        await _objective(db, org, rows=[(day, 90, "computed")])
        (row,) = (await _read(db, _browser(user)))["items"]
        assert (row["stale"], row["last_measured_on"], row["on_track"]) == (stale, day, 1)

    async def test_the_newest_stored_row_is_the_one_judged(self, db, no_commit):
        org = await _org(db)
        user = await _user(db, org)
        await _objective(db, org, rows=[(TODAY - timedelta(days=3), 90, "computed"), (YESTERDAY, 10, "computed")])
        (row,) = (await _read(db, _browser(user)))["items"]
        assert (row["off_track"], row["on_track"], row["last_measured_on"]) == (1, 0, YESTERDAY)

    async def test_todays_target_judges_yesterdays_value(self, db, no_commit):
        org = await _org(db)
        user = await _user(db, org)
        objective = await _objective(db, org, rows=[(YESTERDAY, 70, "computed")])
        (row,) = (await _read(db, _browser(user)))["items"]
        assert row["at_risk"] == 1
        # A new version with a lower target, opened today: the old row is the same measure and is judged by it.
        await db.execute(update(ObjectiveMetric).where(ObjectiveMetric.objective_id == objective.id)
                         .values(active_to=datetime.now()))
        db.add(ObjectiveMetric(organization_id=org.id, objective_id=objective.id, **{**LIVE, "target_value": 65}))
        await db.flush()
        (row,) = (await _read(db, _browser(user)))["items"]
        assert row["on_track"] == 1 and row["stale"] is False

    async def test_a_row_of_another_measure_is_not_used(self, db, no_commit):
        org = await _org(db)
        user = await _user(db, org)
        objective = await _objective(db, org, rows=[(TODAY, 95, "computed")])
        await db.execute(update(ObjectiveMetric).where(ObjectiveMetric.objective_id == objective.id)
                         .values(active_to=datetime.now()))
        db.add(ObjectiveMetric(organization_id=org.id, objective_id=objective.id,
                               **{**LIVE, "measure_key": "audit_programme_completed"}))
        await db.flush()
        (row,) = (await _read(db, _browser(user)))["items"]
        assert (row["not_measured"], row["on_track"], row["stale"]) == (1, 0, True)

    async def test_logged_and_milestone_use_the_newest_entry_by_period(self, db, no_commit):
        org = await _org(db)
        user = await _user(db, org)
        q = date(TODAY.year, 3 * ((TODAY.month - 1) // 3) + 1, 1)
        await _objective(db, org, spec=dict(kind="logged", name="T", unit="percent", direction="at_least",
                                             target_value=95, warn_value=85, frequency="quarterly"),
                         # The earlier period's entry was typed last: the period decides, not the typing.
                         rows=[(YESTERDAY, 50, "logged", q), (TODAY, 99, "logged", q - timedelta(days=92))])
        await _objective(db, org, spec=dict(kind="milestone", name="Policy"), rows=[(YESTERDAY, 1, "logged")])
        (row,) = (await _read(db, _browser(user)))["items"]
        assert (row["off_track"], row["on_track"]) == (1, 1)
        assert row["stale"] is False  # stale is about live metrics only
        assert row["last_measured_on"] == YESTERDAY  # the rows used, not the newest row stored

    async def test_a_logged_entry_under_another_unit_is_not_used(self, db, no_commit):
        org = await _org(db)
        user = await _user(db, org)
        spec = dict(kind="logged", name="T", unit="percent", direction="at_least", target_value=95,
                    frequency="quarterly")
        objective = await _objective(db, org, spec=spec)
        old = ObjectiveMetric(organization_id=org.id, objective_id=objective.id, kind="logged", name="Old",
                              unit="count", direction="at_most", target_value=0, frequency="quarterly",
                              active_from=datetime(2026, 1, 1), active_to=datetime(2026, 2, 1))
        db.add(old)
        await db.flush()
        db.add(IsmsMetricResult(organization_id=org.id, objective_metric_id=old.id, measured_on=TODAY,
                                period_start=TODAY, value=0, status="on_track", source="logged"))
        await db.flush()
        (row,) = (await _read(db, _browser(user)))["items"]
        assert (row["overdue"], row["on_track"], row["last_measured_on"]) == (1, 0, None)

    async def test_a_proposed_live_objective_with_no_row_is_proposed(self, db, no_commit):
        org = await _org(db)
        user = await _user(db, org)
        await _objective(db, org, lifecycle="proposed")
        (row,) = (await _read(db, _browser(user)))["items"]
        assert (row["proposed"], row["not_measured"]) == (1, 0)

    async def test_closed_archived_and_proposed(self, db, no_commit):
        org = await _org(db)
        user = await _user(db, org)
        await _objective(db, org, lifecycle="achieved")              # no row needed, not stale
        await _objective(db, org, lifecycle="missed")
        await _objective(db, org, archived=True)                      # not counted at all
        await _objective(db, org, lifecycle="proposed", rows=[(TODAY, 99, "computed")])
        (row,) = (await _read(db, _browser(user)))["items"]
        assert (row["objectives"], row["achieved"], row["missed"], row["proposed"]) == (3, 1, 1, 1)
        assert row["stale"] is False

    async def test_an_organisation_with_no_objectives_shows_zeros_and_is_not_seeded(self, db, no_commit):
        org = await _org(db)
        user = await _user(db, org)
        (row,) = (await _read(db, _browser(user)))["items"]
        assert (row["objectives"], row["on_track"], row["needs_attention"], row["stale"]) == (0, 0, 0, False)
        assert row["last_measured_on"] is None
        assert int(await db.scalar(select(func.count()).select_from(SecurityObjective).where(
            SecurityObjective.organization_id == org.id))) == 0

    async def test_nothing_is_written(self, db, no_commit):
        org = await _org(db)
        user = await _user(db, org)
        await _objective(db, org, rows=[(YESTERDAY, 90, "computed")])
        await _objective(db, org)
        before = int(await db.scalar(select(func.count()).select_from(IsmsMetricResult).where(
            IsmsMetricResult.organization_id == org.id)))
        await _read(db, _browser(user))
        assert not db.new and not db.dirty and not db.deleted
        assert int(await db.scalar(select(func.count()).select_from(IsmsMetricResult).where(
            IsmsMetricResult.organization_id == org.id))) == before

    async def test_no_calculator_runs(self, db, no_commit, monkeypatch):
        org = await _org(db)
        user = await _user(db, org)
        await _objective(db, org, rows=[(TODAY, 90, "computed")])

        def boom(*a, **k):
            raise AssertionError("no calculator may run")

        monkeypatch.setattr(service, "_measure_safely", boom)
        monkeypatch.setattr(service, "read_objective", boom)
        (row,) = (await _read(db, _browser(user)))["items"]
        assert row["on_track"] == 1

    @pytest.mark.parametrize("count", [2, 10])
    async def test_a_fixed_number_of_statements(self, db, no_commit, count):
        user = await _user(db)
        for _ in range(count):
            org = await _org(db)
            db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role="viewer"))
            await _objective(db, org, rows=[(TODAY, 90, "computed")])
            await _objective(db, org, spec=dict(kind="logged", name="T", unit="percent", direction="at_least",
                                                 target_value=95, frequency="quarterly"),
                             rows=[(TODAY, 99, "logged")])
        await db.flush()
        statements = []

        def seen(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        sync_engine = db.info["engine"].sync_engine
        event.listen(sync_engine, "before_cursor_execute", seen)
        try:
            out = await _read(db, _browser(user))
        finally:
            event.remove(sync_engine, "before_cursor_execute", seen)
        assert len(out["items"]) == count and all(i["objectives"] == 2 for i in out["items"])
        assert len(statements) == portfolio.STATEMENTS, statements
