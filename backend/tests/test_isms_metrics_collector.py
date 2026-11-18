# SPDX-License-Identifier: AGPL-3.0-only
"""Phase 3 B4: logged values posted by a collector with a per-organisation API key.

These tests pin:

* who may post: an editor key writes (201); a viewer key and a key for
  another organisation are refused (403) by the real membership check;
* where a value came from: ``detail`` records ``origin`` and ``collector``,
  and the metric detail returns both on the result;
* the repeat rule: the same value for the same period from a key writes no
  row and no audit row and answers 200; a changed value writes; a person in
  the browser is never deduplicated;
* the by-ref route: same answer as by id, a malformed ref is refused by the
  path pattern (422), an unknown ref or another organisation's ref is 404.

Handlers are called directly on a session whose transaction is always
rolled back (the ``test_security_objectives_api.py`` pattern).
"""
import re
import uuid as _uuid
from datetime import date

import pytest
import pytest_asyncio
from fastapi import HTTPException
from fastapi.params import Path as PathParam
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request
from starlette.responses import Response

import auth as auth_mod
import catalog_models  # noqa: F401 — registers SystemCatalogTemplate for the ApiKey mapper
from api import security_objectives as api
from models import AuditLog, IsmsMetricResult, Organization, OrganizationMember, SecurityObjective, User
from services.isms_metrics import service
from services.isms_metrics.schemas import MetricDetail, MetricSpec, ObjectiveCreate, ResultCreate

from test_journey_service import DATABASE_URL, requires_postgres  # noqa: F401

LOGGED = MetricSpec(kind="logged", name="Patch SLA met (%)", unit="percent", direction="at_least",
                    target_value=95, warn_value=85, frequency="monthly")


# ---------------------------------------------------------------------------
# Structural: no database
# ---------------------------------------------------------------------------


def test_collector_name_is_optional_and_at_most_80_characters():
    assert ResultCreate(value=1).collector is None
    assert ResultCreate(value=1, collector="patch-sla").collector == "patch-sla"
    ResultCreate(value=1, collector="x" * 80)
    with pytest.raises(Exception):
        ResultCreate(value=1, collector="x" * 81)


@pytest.mark.parametrize("name", ["patch-sla", "nightly/patch_sla v2", "edr:defender.1", "A", "9" * 80])
def test_r2_plain_collector_names_are_accepted(name):
    assert ResultCreate(value=1, collector=name).collector == name


@pytest.mark.parametrize("name", [
    "[patch-sla](https://example.test/x)", "![x](https://example.test/p.png)", "<b>x</b>", "_patch",
    "-patch", "patch|sla", "patch\nsla", "Internal Audit, verified", "x" * 81, "",
])
def test_r2_a_collector_name_outside_the_pattern_is_refused(name):
    with pytest.raises(Exception):
        ResultCreate(value=1, collector=name)


@pytest.mark.parametrize("value", [1e10, -1e10, 1e25, float("inf"), float("-inf"), float("nan")])
def test_r8_a_value_out_of_range_is_refused(value):
    with pytest.raises(Exception):
        ResultCreate(value=value)


def test_r8_the_bounds_are_strict_but_wide():
    assert ResultCreate(value=9_999_999_999.9999).value == 9_999_999_999.9999
    assert ResultCreate(value=-9_999_999_999).value == -9_999_999_999


def test_by_ref_route_is_editor_with_the_ref_pattern():
    (route,) = [r for r in api.router.routes if r.path.endswith("/by-ref/{ref}/results")]
    assert route.methods == {"POST"} and route.status_code == 201
    import inspect
    default = inspect.signature(route.endpoint).parameters["ref"].default
    assert isinstance(default, PathParam)
    (pattern,) = [m.pattern for m in default.metadata if hasattr(m, "pattern")]
    rx = re.compile(pattern)
    for good in ("OBJ-1", "OBJ-09", "OBJ-123456"):
        assert rx.match(good), good
    for bad in ("OBJ-", "obj-09", "OBJ-1234567", "OBJ-09/../x", "9", "OBJ-1a"):
        assert not rx.match(bad), bad


def test_by_ref_bad_ref_is_422_through_the_app():
    """The path pattern is enforced by FastAPI: a malformed ref is a 422, with the caller let in."""
    import inspect

    from fastapi.testclient import TestClient

    import main
    from database import get_db

    endpoint = api.log_isms_metric_result_by_ref
    editor = inspect.signature(endpoint).parameters["membership"].default.dependency
    reached = []

    async def let_in():
        return None

    async def no_db():
        reached.append("db")
        yield None

    main.app.dependency_overrides[editor] = let_in
    main.app.dependency_overrides[get_db] = no_db
    try:
        client = TestClient(main.app, raise_server_exceptions=False)
        base = f"/api/organizations/{_uuid.uuid4()}/isms-metrics/by-ref"
        bad = [client.post(f"{base}/{ref}/results", json={"value": 1}) for ref in ("OBJ-x9", "obj-09", "OBJ-1234567")]
    finally:
        main.app.dependency_overrides.pop(editor, None)
        main.app.dependency_overrides.pop(get_db, None)
    assert [r.status_code for r in bad] == [422, 422, 422], [r.text for r in bad]


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
    monkeypatch.setattr(session, "commit", session.flush)
    try:
        yield session
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


async def _org(db):
    tag = _uuid.uuid4().hex[:10]
    org = Organization(name=f"isms-col-{tag}", slug=f"isms-col-{tag}")
    db.add(org)
    await db.flush()
    return org


async def _person(db, org, role="editor"):
    tag = _uuid.uuid4().hex[:10]
    user = User(google_sub=f"isms-col-{tag}", email=f"isms-col-{tag}@example.invalid", display_name="Editor")
    db.add(user)
    await db.flush()
    db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=role))
    await db.flush()
    return user


def _browser(user):
    return auth_mod.User(user_id=user.google_sub, email=user.email, auth_method="google", db_id=str(user.id))


def _key(user, org, role):
    caller = auth_mod.User(user_id="api_user", email=user.email, auth_method="user_api_key", db_id=str(user.id))
    caller._api_key_org_id = org.id
    caller._api_key_role = role
    return caller


def _request(caller, headers=()):
    raw = [(b"user-agent", b"patch-collector/1.0")] + [(k.encode(), v.encode()) for k, v in headers]
    req = Request({"type": "http", "headers": raw, "method": "POST", "path": "/"})
    req.state.user = caller
    return req


def _response():
    """As FastAPI hands a handler its ``Response``: no status set, so the route's 201 applies."""
    resp = Response()
    resp.status_code = None
    return resp


async def _post(db, org, caller, objective_id, value, *, by_ref=None, headers=(), **extra):
    membership = await auth_mod.verify_org_membership(org.id, caller, db, "editor")
    resp = _response()
    body = ResultCreate(value=value, **extra)
    if by_ref:
        out = await api.log_isms_metric_result_by_ref(
            org_id=org.id, ref=by_ref, data=body, request=_request(caller, headers), response=resp,
            membership=membership, db=db)
    else:
        out = await api.log_isms_metric_result(
            org_id=org.id, objective_id=objective_id, data=body, request=_request(caller, headers),
            response=resp, membership=membership, db=db)
    return out, (resp.status_code or 201)


async def _objective(db, org, user):
    actor = service.Actor(user_id=user.id, action_source="ui")
    out = await service.create_objective(db, org.id, ObjectiveCreate(
        name="Patch on time", planned_actions="Patch", metric=LOGGED), actor, date.today())
    return out["id"], out["ref"]


async def _rows(db, org):
    return (await db.execute(select(IsmsMetricResult).where(IsmsMetricResult.organization_id == org.id)
                             .order_by(IsmsMetricResult.created_at, IsmsMetricResult.id))).scalars().all()


async def _audits(db, org):
    """Result rows with field-level audit entries (one entry per tracked field)."""
    return int(await db.scalar(select(func.count(func.distinct(AuditLog.entity_id))).where(
        AuditLog.organization_id == org.id, AuditLog.entity_type == service.RESULT_ENTITY_TYPE)) or 0)


@requires_postgres
@pytest.mark.asyncio
class TestWhoMayPost:
    async def test_editor_key_writes_201_with_origin_and_collector(self, db):
        org = await _org(db)
        owner = await _person(db, org)
        oid, _ = await _objective(db, org, owner)
        out, code = await _post(db, org, _key(owner, org, "editor"), oid, 97.5, collector="patch-sla",
                                note="from the patch report")
        assert code == 201
        (row,) = await _rows(db, org)
        assert row.source == "logged" and row.entered_by_user_id == owner.id
        assert row.detail == {"origin": "api_key", "collector": "patch-sla"}
        assert out["reading"]["value"] == 97.5
        detail = await service.metric_detail(db, org.id, oid, date.today())
        MetricDetail.model_validate(detail)
        assert (detail["results"][0]["origin"], detail["results"][0]["collector"]) == ("api_key", "patch-sla")
        assert await _audits(db, org) == 1

    async def test_viewer_key_is_refused(self, db):
        org = await _org(db)
        owner = await _person(db, org)
        oid, _ = await _objective(db, org, owner)
        with pytest.raises(HTTPException) as exc:
            await _post(db, org, _key(owner, org, "viewer"), oid, 90.0)
        assert exc.value.status_code == 403
        assert await _rows(db, org) == []

    async def test_key_for_another_organisation_is_refused(self, db):
        org, other = await _org(db), await _org(db)
        owner = await _person(db, org)
        oid, _ = await _objective(db, org, owner)
        with pytest.raises(HTTPException) as exc:
            await _post(db, org, _key(owner, other, "editor"), oid, 90.0)
        assert exc.value.status_code == 403
        assert await _rows(db, org) == []

    async def test_a_live_metric_still_refuses_a_value(self, db):
        org = await _org(db)
        owner = await _person(db, org)
        out = await service.create_objective(db, org.id, ObjectiveCreate(
            name="Live", planned_actions="x", metric=MetricSpec(kind="live", measure_key="controls_implemented",
                                                               target_value=80)),
            service.Actor(user_id=owner.id, action_source="ui"), date.today())
        with pytest.raises(HTTPException) as exc:
            await _post(db, org, _key(owner, org, "editor"), out["id"], 50.0)
        assert exc.value.status_code == 422


@requires_postgres
@pytest.mark.asyncio
class TestRepeats:
    async def test_same_value_twice_from_a_key_is_one_row_and_a_200(self, db):
        org = await _org(db)
        owner = await _person(db, org)
        oid, _ = await _objective(db, org, owner)
        key = _key(owner, org, "editor")
        first, c1 = await _post(db, org, key, oid, 97.5, collector="patch-sla")
        again, c2 = await _post(db, org, key, oid, 97.5, collector="patch-sla", note="re-sent")
        assert (c1, c2) == (201, 200)
        assert len(await _rows(db, org)) == 1
        assert await _audits(db, org) == 1           # none for the skipped post
        assert again["reading"] == first["reading"]  # the objective as it stands

    async def test_a_changed_value_writes_a_second_row_that_counts(self, db):
        org = await _org(db)
        owner = await _person(db, org)
        oid, _ = await _objective(db, org, owner)
        key = _key(owner, org, "editor")
        await _post(db, org, key, oid, 97.5)
        out, code = await _post(db, org, key, oid, 91.0)
        assert code == 201 and len(await _rows(db, org)) == 2
        assert out["reading"]["value"] == 91.0 and await _audits(db, org) == 2
        # Back to the first value: it differs from the newest, so it is stored again.
        out, code = await _post(db, org, key, oid, 97.5)
        assert code == 201 and len(await _rows(db, org)) == 3 and out["reading"]["value"] == 97.5

    async def test_same_value_for_another_period_is_stored(self, db):
        org = await _org(db)
        owner = await _person(db, org)
        oid, _ = await _objective(db, org, owner)
        key = _key(owner, org, "editor")
        today = date.today()
        last_month = date(today.year - (today.month == 1), (today.month - 2) % 12 + 1, 1)
        await _post(db, org, key, oid, 97.5)
        _, code = await _post(db, org, key, oid, 97.5, period_start=last_month)
        assert code == 201 and len(await _rows(db, org)) == 2

    async def test_the_same_value_from_a_browser_session_is_two_rows(self, db):
        org = await _org(db)
        owner = await _person(db, org)
        oid, _ = await _objective(db, org, owner)
        person = _browser(owner)
        _, c1 = await _post(db, org, person, oid, 97.5, note="first")
        _, c2 = await _post(db, org, person, oid, 97.5, note="re-entered with a new note")
        assert (c1, c2) == (201, 201)
        rows = await _rows(db, org)
        assert len(rows) == 2 and {r.detail["origin"] for r in rows} == {"ui"}
        assert await _audits(db, org) == 2

    async def test_a_key_repeating_a_value_a_person_typed_is_not_stored(self, db):
        org = await _org(db)
        owner = await _person(db, org)
        oid, _ = await _objective(db, org, owner)
        await _post(db, org, _browser(owner), oid, 97.5)
        _, code = await _post(db, org, _key(owner, org, "editor"), oid, 97.5, collector="patch-sla")
        assert code == 200 and len(await _rows(db, org)) == 1

    async def test_four_decimal_places_decide_equality(self, db):
        org = await _org(db)
        owner = await _person(db, org)
        oid, _ = await _objective(db, org, owner)
        key = _key(owner, org, "editor")
        await _post(db, org, key, oid, 97.12341)
        _, same = await _post(db, org, key, oid, 97.1234)      # stored as 97.1234 both times
        _, rounds_same = await _post(db, org, key, oid, 97.12344)
        _, other = await _post(db, org, key, oid, 97.1235)
        assert (same, rounds_same, other) == (200, 200, 201)


@requires_postgres
@pytest.mark.asyncio
class TestByRef:
    async def test_by_ref_equals_by_id(self, db):
        org = await _org(db)
        owner = await _person(db, org)
        oid, ref = await _objective(db, org, owner)
        key = _key(owner, org, "editor")
        by_ref, code = await _post(db, org, key, None, 88.0, by_ref=ref, collector="patch-sla")
        assert code == 201
        by_id = await service.get_objective(db, org.id, oid, date.today())
        assert by_ref == by_id and by_ref["id"] == oid
        (row,) = await _rows(db, org)
        assert row.detail == {"origin": "api_key", "collector": "patch-sla"}
        _, again = await _post(db, org, key, None, 88.0, by_ref=ref)
        assert again == 200

    async def test_unknown_ref_is_404(self, db):
        org = await _org(db)
        owner = await _person(db, org)
        await _objective(db, org, owner)
        with pytest.raises(HTTPException) as exc:
            await _post(db, org, _key(owner, org, "editor"), None, 1.0, by_ref="OBJ-999999")
        assert exc.value.status_code == 404

    async def test_another_organisations_ref_is_404(self, db):
        org, other = await _org(db), await _org(db)
        owner, stranger = await _person(db, org), await _person(db, other)
        _, ref = await _objective(db, other, stranger)  # this organisation has no objective at all
        with pytest.raises(HTTPException) as exc:
            await _post(db, org, _key(owner, org, "editor"), None, 1.0, by_ref=ref)
        assert exc.value.status_code == 404
        assert (await _rows(db, other)) == [] and (await _rows(db, org)) == []


@requires_postgres
@pytest.mark.asyncio
async def test_through_the_app_201_then_200_for_a_repeat(db):
    """FastAPI injects the Response: the route's 201, then 200 for an identical repeat from a key."""
    import inspect
    from types import SimpleNamespace

    import httpx

    import main
    from database import get_db

    org = await _org(db)
    owner = await _person(db, org)
    oid, ref = await _objective(db, org, owner)
    membership = auth_mod.OrgMembership(
        user=SimpleNamespace(db_id=str(owner.id), email=owner.email, auth_method="user_api_key"),
        organization_id=org.id, role="editor")
    deps = [inspect.signature(h).parameters["membership"].default.dependency
            for h in (api.log_isms_metric_result, api.log_isms_metric_result_by_ref)]

    async def as_editor():
        return membership

    async def same_session():
        yield db

    for dep in deps:
        main.app.dependency_overrides[dep] = as_editor
    main.app.dependency_overrides[get_db] = same_session
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://t") as client:
            url = f"/api/organizations/{org.id}/isms-metrics"
            key = {"x-audit-source": "api_key"}  # R1: ignored; the caller below signed in with a key
            codes = [
                (await client.post(f"{url}/{oid}/results", json={"value": 70, "collector": "c"}, headers=key)).status_code,
                (await client.post(f"{url}/{oid}/results", json={"value": 70}, headers=key)).status_code,
                (await client.post(f"{url}/by-ref/{ref}/results", json={"value": 70}, headers=key)).status_code,
                (await client.post(f"{url}/by-ref/{ref}/results", json={"value": 71}, headers=key)).status_code,
                (await client.post(f"{url}/by-ref/OBJ-999/results", json={"value": 71}, headers=key)).status_code,
            ]
    finally:
        for dep in deps:
            main.app.dependency_overrides.pop(dep, None)
        main.app.dependency_overrides.pop(get_db, None)
    assert codes == [201, 200, 200, 201, 404]
    assert len(await _rows(db, org)) == 2


# ---------------------------------------------------------------------------
# Review fixes R1 to R4 (review-b.md M1, M2, M3, L5)
# ---------------------------------------------------------------------------


def _oidc(user):
    return auth_mod.User(user_id=user.google_sub, email=user.email, auth_method="oidc", db_id=str(user.id))


MILESTONE = MetricSpec(kind="milestone", name="MFA enforced")


async def _activate(db, oid):
    """Confirmed and in force, so the stored status is a judgement rather than ``proposed``."""
    from sqlalchemy import update

    await db.execute(update(SecurityObjective).where(SecurityObjective.id == oid).values(lifecycle_status="active"))
    await db.flush()


@requires_postgres
@pytest.mark.asyncio
class TestR1OriginFromHowTheCallerSignedIn:
    async def test_an_oidc_person_stores_ui(self, db):
        org = await _org(db)
        owner = await _person(db, org)
        oid, _ = await _objective(db, org, owner)
        await _post(db, org, _oidc(owner), oid, 96)
        _, again = await _post(db, org, _oidc(owner), oid, 96)
        assert again == 201  # a person is never deduplicated
        assert [r.detail["origin"] for r in await _rows(db, org)] == ["ui", "ui"]

    async def test_a_key_saying_ui_is_still_a_key_and_its_repeat_is_skipped(self, db):
        org = await _org(db)
        owner = await _person(db, org)
        oid, _ = await _objective(db, org, owner)
        key, ui = _key(owner, org, "editor"), [("x-audit-source", "ui")]
        _, c1 = await _post(db, org, key, oid, 96, headers=ui)
        _, c2 = await _post(db, org, key, oid, 96, headers=ui)
        assert (c1, c2) == (201, 200)
        assert [r.detail["origin"] for r in await _rows(db, org)] == ["api_key"]

    async def test_a_key_saying_mcp_stores_mcp(self, db):
        org = await _org(db)
        owner = await _person(db, org)
        oid, _ = await _objective(db, org, owner)
        await _post(db, org, _key(owner, org, "editor"), oid, 96, headers=[("x-audit-source", "mcp")])
        assert [r.detail["origin"] for r in await _rows(db, org)] == ["mcp"]

    async def test_a_browser_saying_api_key_is_still_a_person(self, db):
        org = await _org(db)
        owner = await _person(db, org)
        oid, _ = await _objective(db, org, owner)
        say_key = [("x-audit-source", "api_key")]
        _, c1 = await _post(db, org, _browser(owner), oid, 96, headers=say_key)
        _, c2 = await _post(db, org, _browser(owner), oid, 96, headers=say_key)
        assert (c1, c2) == (201, 201)
        assert {r.detail["origin"] for r in await _rows(db, org)} == {"ui"}

    async def test_a_call_with_no_request_stores_system(self, db):
        org = await _org(db)
        owner = await _person(db, org)
        oid, _ = await _objective(db, org, owner)
        await service.log_result(db, org.id, oid, 96, service.Actor(user_id=owner.id, action_source="ui"),
                                 date.today())
        assert [r.detail["origin"] for r in await _rows(db, org)] == ["system"]


@requires_postgres
@pytest.mark.asyncio
class TestR2CollectorOnlyFromAKey:
    async def test_a_browser_session_sending_a_collector_name_gets_422(self, db):
        org = await _org(db)
        owner = await _person(db, org)
        oid, _ = await _objective(db, org, owner)
        for caller in (_browser(owner), _oidc(owner)):
            with pytest.raises(HTTPException) as exc:
                await _post(db, org, caller, oid, 99, collector="Internal-Audit")
            assert exc.value.status_code == 422
            assert "API key" in exc.value.detail
        assert await _rows(db, org) == []


@requires_postgres
@pytest.mark.asyncio
class TestR3MilestoneRepeats:
    async def test_a_nightly_repeat_of_a_milestone_is_skipped_and_a_change_writes(self, db):
        org = await _org(db)
        owner = await _person(db, org)
        actor = service.Actor(user_id=owner.id, action_source="ui")
        oid = (await service.create_objective(db, org.id, ObjectiveCreate(
            name="MFA", planned_actions="Enforce", metric=MILESTONE), actor, date.today()))["id"]
        key = _key(owner, org, "editor")
        days = [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3), date(2026, 1, 4), date(2026, 1, 5)]
        codes = [(await _post(db, org, key, oid, v, period_start=d))[1]
                 for v, d in zip((1, 1, 0, 0, 1), days)]
        assert codes == [201, 200, 201, 200, 201]
        assert [float(r.value) for r in await _rows(db, org)] == [1.0, 0.0, 1.0]


@requires_postgres
@pytest.mark.asyncio
class TestR4OnlyTheVersionInForce:
    async def _change(self, db, org, oid, owner, **spec):
        await service.set_metric(db, org.id, oid, LOGGED.model_copy(update=spec),
                                 service.Actor(user_id=owner.id, action_source="ui"), "admin", date.today())

    async def test_the_first_post_after_a_target_change_is_stored_once(self, db):
        org = await _org(db)
        owner = await _person(db, org)
        oid, _ = await _objective(db, org, owner)
        await _activate(db, oid)
        key = _key(owner, org, "editor")
        await _post(db, org, key, oid, 92)
        await self._change(db, org, oid, owner, target_value=90)
        _, after = await _post(db, org, key, oid, 92)
        _, again = await _post(db, org, key, oid, 92)
        assert (after, again) == (201, 200)
        rows = await _rows(db, org)
        assert [r.status for r in rows] == ["at_risk", "on_track"]  # each judged by its own version
        assert len({r.objective_metric_id for r in rows}) == 2

    async def test_the_first_post_after_a_rename_is_stored(self, db):
        org = await _org(db)
        owner = await _person(db, org)
        oid, _ = await _objective(db, org, owner)
        key = _key(owner, org, "editor")
        await _post(db, org, key, oid, 95)
        await self._change(db, org, oid, owner, name="Phishing click rate (%)")
        _, code = await _post(db, org, key, oid, 95)
        assert code == 201 and len(await _rows(db, org)) == 2

    async def test_the_status_is_judged_from_the_value_as_stored(self, db):
        org = await _org(db)
        owner = await _person(db, org)
        oid, _ = await _objective(db, org, owner)
        await _activate(db, oid)
        out, _ = await _post(db, org, _key(owner, org, "editor"), oid, 94.99996)
        (row,) = await _rows(db, org)
        assert (float(row.value), row.status) == (95.0, "on_track")
        assert out["reading"]["status"] == row.status


@requires_postgres
@pytest.mark.asyncio
async def test_r8_a_value_that_rounds_past_ten_digits_is_a_422_not_a_500(db):
    org = await _org(db)
    owner = await _person(db, org)
    actor = service.Actor(user_id=owner.id, action_source="ui")
    oid = (await service.create_objective(db, org.id, ObjectiveCreate(
        name="Count", planned_actions="x", metric=MetricSpec(
            kind="logged", name="Open issues", unit="count", direction="at_most", target_value=0,
            frequency="monthly")), actor, date.today()))["id"]
    for value in (9_999_999_999.99999, 1e25):
        with pytest.raises(service.ObjectiveError) as exc:
            await service.log_result(db, org.id, oid, value, actor, date.today())
        assert exc.value.status_code == 422
    await service.log_result(db, org.id, oid, 9_999_999_999.9999, actor, date.today())
    assert [float(r.value) for r in await _rows(db, org)] == [9_999_999_999.9999]
