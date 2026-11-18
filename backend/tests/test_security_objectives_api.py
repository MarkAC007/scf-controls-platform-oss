# SPDX-License-Identifier: AGPL-3.0-only
"""Security objectives and ISMS metrics: the HTTP routes and the nightly task.

These tests pin:

* the routes: twelve, mounted under ``/api`` with the methods and minimum
  roles of the contract, no DELETE for either segment, every request body
  closed to unknown keys, and the audit middleware's reading of each path;
* the role rules that depend on state: an editor sets the metric of a
  proposed objective but not a confirmed one; only an admin confirms;
* confirm: refused without a team (with the sentence), and on success the
  objective is active and the target is stamped with the admin;
* tenant isolation: another organisation's objective is a 404 on every
  route that takes an id;
* the register and the dashboard: starters seeded once, summary counts that
  add up, and the same reading on both pages;
* logged results and audit rows carrying the caller;
* the ISMS profile: objectives are no longer saved there, and its GET reads
  them from the table;
* the nightly task: one computed row per live metric per day, and one
  organisation failing does not stop the next.

Handlers are called directly with a hand-built membership, on a session
whose transaction is always rolled back (the ``test_capability_tests.py``
pattern).
"""
import inspect
import uuid as _uuid
from datetime import date
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

import tasks_automation
from api import isms_profile as profile_api
from api import security_objectives as api
import auth
from auth import OrgMembership
from middleware.audit_middleware import _action_for, _extract_entity_type, _should_skip
from models import (
    AuditLog,
    Function,
    IsmsMetricResult,
    IsmsProfile,
    Organization,
    OrganizationMember,
    SecurityObjective,
    Team,
    User,
)
from services.isms_metrics import service
from services.isms_metrics.schemas import (
    ConfirmBody,
    EvaluationCreate,
    MetricSpec,
    ObjectiveCreate,
    ObjectiveUpdate,
    ResultCreate,
    WordingRequest,
)

from test_journey_service import DATABASE_URL, requires_postgres  # noqa: F401

ORG = "/organizations/{org_id}"
OBJ = f"{ORG}/security-objectives"
MET = f"{ORG}/isms-metrics"

ROUTES = {
    (OBJ, "GET"): ("list_security_objectives", "viewer"),
    (OBJ, "POST"): ("create_security_objective", "editor"),
    (f"{OBJ}/{{objective_id}}", "GET"): ("get_security_objective", "viewer"),
    (f"{OBJ}/{{objective_id}}", "PATCH"): ("update_security_objective", "editor"),
    (f"{OBJ}/{{objective_id}}/metric", "PUT"): ("set_security_objective_metric", "editor"),
    (f"{OBJ}/{{objective_id}}/confirm", "POST"): ("confirm_security_objective", "admin"),
    (f"{OBJ}/{{objective_id}}/evaluation", "POST"): ("record_security_objective_evaluation", "editor"),
    (f"{MET}/measures", "GET"): ("list_isms_measures", "viewer"),
    (f"{MET}/wording", "POST"): ("isms_metric_wording", "viewer"),
    (MET, "GET"): ("list_isms_metrics", "viewer"),
    (f"{MET}/{{objective_id}}", "GET"): ("get_isms_metric", "viewer"),
    (f"{MET}/{{objective_id}}/results", "POST"): ("log_isms_metric_result", "editor"),
    # Phase 3 B4: a collector posts by the objective's ref.
    (f"{MET}/by-ref/{{ref}}/results", "POST"): ("log_isms_metric_result_by_ref", "editor"),
    # Phase 3 B2: derived measurement tasks.
    (f"{MET}/measurement-tasks", "GET"): ("list_isms_measurement_tasks", "viewer"),
    # Phase 3 B3: the cross-client view takes no organisation; any signed-in caller.
    ("/isms-metrics/portfolio", "GET"): ("isms_metrics_portfolio", "signed_in"),
}


# ---------------------------------------------------------------------------
# Structural: no database
# ---------------------------------------------------------------------------


def _min_role(handler):
    params = inspect.signature(handler).parameters
    if "membership" not in params:
        assert params["user"].default.dependency is auth.require_auth
        return "signed_in"
    dependency = inspect.signature(handler).parameters["membership"].default.dependency
    assert dependency.__qualname__.startswith("require_org_role")
    return dict(zip(dependency.__code__.co_freevars, (c.cell_contents for c in dependency.__closure__)))["min_role"]


def test_the_thirteen_routes_are_registered_with_their_roles():
    routes = {(r.path, m): r.endpoint for r in api.router.routes for m in getattr(r, "methods", ())}
    assert set(routes) == set(ROUTES)
    for key, (name, role) in ROUTES.items():
        assert routes[key].__name__ == name, key
        assert _min_role(routes[key]) == role, key


def test_the_fixed_metric_paths_are_declared_before_the_id_path():
    order = [r.path for r in api.router.routes]
    detail = order.index(f"{MET}/{{objective_id}}")
    assert order.index(f"{MET}/measures") < detail
    assert order.index(f"{MET}/wording") < detail
    assert order.index(f"{MET}/measurement-tasks") < detail


def test_the_router_is_mounted_under_api_with_no_delete():
    import main

    paths = main.app.openapi()["paths"]
    for (path, method) in ROUTES:
        assert method.lower() in paths.get(f"/api{path}", {}), (path, method)
    ours = {p: ops for p, ops in paths.items() if "/security-objectives" in p or "/isms-metrics" in p}
    assert len(ours) == len({p for p, _ in ROUTES}) == 13
    assert not any("delete" in ops for ops in ours.values())


def test_every_request_body_refuses_unknown_keys():
    import main

    spec = main.app.openapi()
    schemas = spec["components"]["schemas"]
    bodies = 0
    for (path, method) in ROUTES:
        body = spec["paths"][f"/api{path}"][method.lower()].get("requestBody")
        if body is None:
            continue
        bodies += 1
        ref = body["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1]
        assert schemas[ref].get("additionalProperties") is False, (path, method, ref)
    assert bodies == 8
    for model, fields in (
        (ObjectiveCreate, {"name": "Keep it safe"}), (ObjectiveUpdate, {"name": "x"}),
        (MetricSpec, {"kind": "milestone"}), (ConfirmBody, {}), (EvaluationCreate, {"note": "ok"}),
        (ResultCreate, {"value": 1}), (WordingRequest, {"kind": "milestone"}),
    ):
        model(**fields)
        with pytest.raises(ValidationError):
            model(**fields, colour="red")


def test_the_audit_middleware_reads_the_new_paths():
    oid, xid = _uuid.uuid4(), _uuid.uuid4()
    base = f"/api/organizations/{oid}"
    assert _extract_entity_type(f"{base}/security-objectives") == "security_objective"
    assert _extract_entity_type(f"{base}/security-objectives/{xid}/confirm") == "security_objective"
    assert _extract_entity_type(f"{base}/isms-metrics/{xid}/results") == "isms_metric_result"
    assert _action_for("POST", f"{base}/security-objectives") == "create"
    assert _action_for("PATCH", f"{base}/security-objectives/{xid}") == "update"
    assert _action_for("PUT", f"{base}/security-objectives/{xid}/metric") == "update"
    assert _action_for("POST", f"{base}/security-objectives/{xid}/confirm") == "update"
    assert _action_for("POST", f"{base}/security-objectives/{xid}/evaluation") == "update"
    assert _action_for("POST", f"{base}/isms-metrics/{xid}/results") == "create"
    # Wording is a read sent as POST: no baseline row.
    assert _should_skip("POST", f"{base}/isms-metrics/wording")
    assert not _should_skip("POST", f"{base}/isms-metrics/{xid}/results")


def test_objectives_are_no_longer_a_saveable_profile_section():
    import main

    put = main.app.openapi()["paths"]["/api/organizations/{org_id}/isms-profile/{section}"]["put"]
    (section,) = [p for p in put["parameters"] if p["name"] == "section"]
    allowed = set(section["schema"]["enum"])
    assert "objectives" not in allowed
    assert allowed == set(profile_api.SECTIONS)


# ---------------------------------------------------------------------------
# Database-backed: the real handlers on a rolled-back transaction
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
    """Handlers commit; inside the rolled-back test transaction that is a flush."""
    monkeypatch.setattr(db, "commit", db.flush)
    monkeypatch.setattr(db, "rollback", db.flush)


async def _org(db):
    tag = _uuid.uuid4().hex[:10]
    org = Organization(name=f"isms-api-{tag}", slug=f"isms-api-{tag}")
    db.add(org)
    await db.flush()
    return org


async def _member(db, org, role):
    tag = _uuid.uuid4().hex[:10]
    user = User(google_sub=f"isms-api-{tag}", email=f"isms-api-{tag}@example.invalid",
                display_name=f"Person {tag}")
    db.add(user)
    await db.flush()
    db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=role))
    await db.flush()
    return _membership(org, user, role), user


def _membership(org, user, role):
    return OrgMembership(
        user=SimpleNamespace(db_id=str(user.id), email=user.email), organization_id=org.id, role=role,
    )


async def _team(db, org):
    function = (await db.execute(select(Function).limit(1))).scalar_one_or_none()
    if function is None:  # pragma: no cover - environment dependent
        pytest.skip("no function rows to hang a team on")
    team = Team(organization_id=org.id, function_id=function.id, name=f"Security {_uuid.uuid4().hex[:6]}")
    db.add(team)
    await db.flush()
    return team


def _request(method="POST"):
    return Request({"type": "http", "headers": [], "method": method, "path": "/"})


LIVE = {"kind": "live", "measure_key": "controls_implemented", "target_value": 80, "warn_value": 60}
LOGGED = {"kind": "logged", "name": "Training completion (%)", "unit": "percent", "direction": "at_least",
          "target_value": 95, "warn_value": 85, "frequency": "quarterly"}


async def _create(db, org, membership, **body):
    fields = {"name": "Keep the lights on", "planned_actions": "Do the work"}
    fields.update(body)
    return await api.create_security_objective(
        org_id=org.id, data=ObjectiveCreate(**fields), request=_request(), membership=membership, db=db,
    )


async def _confirm(db, org, membership, objective_id, **body):
    return await api.confirm_security_objective(
        org_id=org.id, objective_id=objective_id, data=ConfirmBody(**body), request=_request(),
        membership=membership, db=db,
    )


async def _set_metric(db, org, membership, objective_id, spec):
    return await api.set_security_objective_metric(
        org_id=org.id, objective_id=objective_id, data=MetricSpec(**spec), request=_request("PUT"),
        membership=membership, db=db,
    )


async def _audit_rows(db, entity_id, user_id):
    return int(await db.scalar(select(func.count()).select_from(AuditLog).where(
        AuditLog.entity_id == entity_id, AuditLog.changed_by_user_id == user_id)) or 0)


async def _computed_rows(db, org):
    return int(await db.scalar(select(func.count()).select_from(IsmsMetricResult).where(
        IsmsMetricResult.organization_id == org.id, IsmsMetricResult.source == "computed")) or 0)


@requires_postgres
@pytest.mark.asyncio
class TestRolesAndConfirm:
    async def test_editor_sets_the_metric_while_proposed_and_is_refused_once_confirmed(self, db, no_commit):
        org = await _org(db)
        editor, _ = await _member(db, org, "editor")
        admin, admin_user = await _member(db, org, "admin")
        team = await _team(db, org)
        out = await _create(db, org, editor, owning_team_id=team.id)
        assert out["lifecycle_status"] == "proposed" and out["metric"] is None

        out = await _set_metric(db, org, editor, out["id"], LIVE)
        assert out["metric"]["measure_key"] == "controls_implemented"
        assert out["metric"]["target_set_by_user_id"] is None

        await _confirm(db, org, admin, out["id"])
        with pytest.raises(HTTPException) as exc:
            await _set_metric(db, org, editor, out["id"], {**LIVE, "target_value": 70, "warn_value": 50})
        assert exc.value.status_code == 403

        changed = await _set_metric(db, org, admin, out["id"], {**LIVE, "target_value": 70, "warn_value": 50})
        assert changed["metric"]["target_value"] == 70
        assert changed["metric"]["target_set_by_user_id"] == admin_user.id

    async def test_type_and_due_date_of_a_confirmed_objective_take_an_admin(self, db, no_commit):
        """An editor must not turn an off-track objective into "in progress" by re-typing it."""
        org = await _org(db)
        editor, _ = await _member(db, org, "editor")
        admin, _ = await _member(db, org, "admin")
        team = await _team(db, org)
        out = await _create(db, org, editor, owning_team_id=team.id, metric=LIVE)

        async def patch(membership, **changes):
            return await api.update_security_objective(
                org_id=org.id, objective_id=out["id"], data=ObjectiveUpdate(**changes),
                request=_request("PATCH"), membership=membership, db=db,
            )

        # While proposed an editor may set both.
        due = date(2099, 12, 31)
        changed = await patch(editor, objective_type="achieve", due_date=due)
        assert changed["objective_type"] == "achieve" and changed["due_date"] == due
        await patch(editor, objective_type="maintain", due_date=None)

        await _confirm(db, org, admin, out["id"])
        for changes in ({"objective_type": "achieve"}, {"due_date": due}):
            with pytest.raises(HTTPException) as exc:
                await patch(editor, **changes)
            assert exc.value.status_code == 403
        row = await db.get(SecurityObjective, out["id"])
        await db.refresh(row)
        assert row.objective_type == "maintain" and row.due_date is None

        # Sending the value it already has is not a change, and other fields stay open to an editor.
        kept = await patch(editor, objective_type="maintain", name="Renamed by an editor")
        assert kept["name"] == "Renamed by an editor"

        changed = await patch(admin, objective_type="achieve", due_date=due)
        assert changed["objective_type"] == "achieve" and changed["due_date"] == due

    async def test_archiving_and_closing_a_confirmed_objective_take_an_admin(self, db, no_commit):
        """An editor must not take an off-track objective off the dashboard by archiving or closing it."""
        org = await _org(db)
        editor, _ = await _member(db, org, "editor")
        admin, _ = await _member(db, org, "admin")
        team = await _team(db, org)
        out = await _create(db, org, editor, owning_team_id=team.id, metric=LIVE)

        async def patch(membership, **changes):
            return await api.update_security_objective(
                org_id=org.id, objective_id=out["id"], data=ObjectiveUpdate(**changes),
                request=_request("PATCH"), membership=membership, db=db,
            )

        # While proposed an editor may dismiss a suggestion and bring it back.
        assert (await patch(editor, archived=True))["archived_at"] is not None
        assert (await patch(editor, archived=False))["archived_at"] is None

        await _confirm(db, org, admin, out["id"])
        for changes in ({"archived": True}, {"lifecycle_status": "achieved"}, {"lifecycle_status": "missed"}):
            with pytest.raises(HTTPException) as exc:
                await patch(editor, **changes)
            assert exc.value.status_code == 403, changes
            assert exc.value.detail == "Only an admin can archive, restore or close a confirmed objective"
        row = await db.get(SecurityObjective, out["id"])
        await db.refresh(row)
        assert row.lifecycle_status == "active" and row.archived_at is None

        # Sending what it already is changes nothing, and the other fields stay open to an editor.
        kept = await patch(editor, archived=False, lifecycle_status="active", name="Renamed by an editor")
        assert kept["name"] == "Renamed by an editor"

        closed = await patch(admin, lifecycle_status="missed")
        assert closed["lifecycle_status"] == "missed" and closed["reading"]["status"] == "missed"
        with pytest.raises(HTTPException) as exc:
            await patch(editor, lifecycle_status="active")
        assert exc.value.status_code == 403
        assert (await patch(admin, lifecycle_status="active"))["lifecycle_status"] == "active"

        assert (await patch(admin, archived=True))["archived_at"] is not None
        with pytest.raises(HTTPException) as exc:
            await patch(editor, archived=False)
        assert exc.value.status_code == 403
        assert (await patch(admin, archived=False))["archived_at"] is None

    async def test_confirm_is_refused_for_an_editor(self, db, no_commit):
        org = await _org(db)
        editor, _ = await _member(db, org, "editor")
        team = await _team(db, org)
        out = await _create(db, org, editor, owning_team_id=team.id, metric=LIVE)
        # The route needs admin; the service refuses an editor too, should it be reached.
        assert _min_role(api.confirm_security_objective) == "admin"
        with pytest.raises(HTTPException) as exc:
            await _confirm(db, org, editor, out["id"])
        assert exc.value.status_code == 403
        assert (await db.get(SecurityObjective, out["id"])).lifecycle_status == "proposed"

    async def test_confirm_without_a_team_is_a_422_with_the_sentence(self, db, no_commit):
        org = await _org(db)
        admin, _ = await _member(db, org, "admin")
        out = await _create(db, org, admin, metric=LIVE)
        with pytest.raises(HTTPException) as exc:
            await _confirm(db, org, admin, out["id"])
        assert exc.value.status_code == 422
        assert exc.value.detail == "Choose a responsible team before confirming"

    async def test_confirm_makes_the_objective_active_and_stamps_the_target_setter(self, db, no_commit):
        org = await _org(db)
        editor, _ = await _member(db, org, "editor")
        admin, admin_user = await _member(db, org, "admin")
        team = await _team(db, org)
        out = await _create(db, org, editor, metric={**LIVE, "target_value": None, "warn_value": None})
        confirmed = await _confirm(db, org, admin, out["id"], owning_team_id=str(team.id), target_value=75)
        assert confirmed["lifecycle_status"] == "active"
        assert confirmed["owning_team_id"] == team.id and confirmed["owning_team_name"] == team.name
        metric = confirmed["metric"]
        assert metric["target_value"] == 75
        assert metric["target_set_by_user_id"] == admin_user.id
        assert metric["target_set_by_name"] == admin_user.display_name
        assert metric["target_set_at"] is not None
        assert metric["id"] != out["metric"]["id"]  # a new version was opened

        with pytest.raises(HTTPException) as exc:
            await _confirm(db, org, admin, out["id"])
        assert exc.value.status_code == 422

    async def test_a_new_target_rewrites_the_generated_evaluation_text_and_leaves_a_persons_own(self, db, no_commit):
        """The generated text names the target; the register and the 6.2 document print it."""
        org = await _org(db)
        admin, _ = await _member(db, org, "admin")
        team = await _team(db, org)
        listed = await api.list_security_objectives(org_id=org.id, include_archived=False, membership=admin, db=db)
        starters = {i["source"]: i for i in listed["items"]}
        risk, evidence, controls = (
            starters["starter:risk_appetite"], starters["starter:evidence_on_time"],
            starters["starter:controls_implemented"],
        )
        assert "Target: at least 100%; warning below 90%." in risk["evaluation_method"]

        # Confirm with a different target: the sentence follows.
        confirmed = await _confirm(
            db, org, admin, risk["id"], owning_team_id=str(team.id), target_value=90, warn_value=80)
        assert confirmed["metric"]["target_value"] == 90
        assert "Target: at least 90%; warning below 80%." in confirmed["evaluation_method"]
        assert "100%" not in confirmed["evaluation_method"]

        # A metric change with no text sent: the sentence follows the new metric.
        changed = await _set_metric(db, org, admin, evidence["id"], {
            "kind": "live", "measure_key": "evidence_collected_on_time", "target_value": 70, "warn_value": 50})
        assert "Target: at least 70%; warning below 50%." in changed["evaluation_method"]
        assert "90%" not in changed["evaluation_method"]

        # Text a person wrote is left alone by both routes.
        own = "We look at the control list at each management review."
        await api.update_security_objective(
            org_id=org.id, objective_id=controls["id"], data=ObjectiveUpdate(evaluation_method=own),
            request=_request("PATCH"), membership=admin, db=db,
        )
        kept = await _set_metric(db, org, admin, controls["id"], {**LIVE, "target_value": 85, "warn_value": 70})
        assert kept["evaluation_method"] == own
        kept = await _confirm(db, org, admin, controls["id"], owning_team_id=str(team.id), target_value=95)
        assert kept["evaluation_method"] == own and kept["metric"]["target_value"] == 95


@requires_postgres
@pytest.mark.asyncio
class TestTenantIsolation:
    async def test_another_organisations_objective_is_a_404_on_every_id_route(self, db, no_commit):
        org, other = await _org(db), await _org(db)
        admin, _ = await _member(db, org, "admin")
        theirs_admin, _ = await _member(db, other, "admin")
        theirs = await _create(db, other, theirs_admin, metric=LOGGED)
        oid = theirs["id"]
        common = dict(org_id=org.id, objective_id=oid, membership=admin, db=db)
        calls = {
            "get": api.get_security_objective(**common),
            "patch": api.update_security_objective(
                data=ObjectiveUpdate(name="Mine now"), request=_request("PATCH"), **common),
            "metric": api.set_security_objective_metric(
                data=MetricSpec(**LOGGED), request=_request("PUT"), **common),
            "confirm": api.confirm_security_objective(data=ConfirmBody(), request=_request(), **common),
            "evaluation": api.record_security_objective_evaluation(
                data=EvaluationCreate(note="Looked at it"), request=_request(), **common),
            "results": api.log_isms_metric_result(data=ResultCreate(value=50), request=_request(), **common),
            "detail": api.get_isms_metric(**common),
        }
        for name, call in calls.items():
            with pytest.raises(HTTPException) as exc:
                await call
            assert exc.value.status_code == 404, name
        untouched = await db.get(SecurityObjective, oid)
        assert untouched.name == "Keep the lights on" and untouched.organization_id == other.id


@requires_postgres
@pytest.mark.asyncio
class TestRegisterAndDashboard:
    async def test_the_list_seeds_twelve_starters_once_and_the_summary_adds_up(self, db, no_commit):
        org = await _org(db)
        viewer, _ = await _member(db, org, "viewer")
        first = await api.list_security_objectives(org_id=org.id, include_archived=False, membership=viewer, db=db)
        assert len(first["items"]) == 12
        assert [i["ref"] for i in first["items"]] == [f"OBJ-{n:02d}" for n in range(1, 13)]
        assert {i["lifecycle_status"] for i in first["items"]} == {"proposed"}

        second = await api.list_security_objectives(org_id=org.id, include_archived=False, membership=viewer, db=db)
        assert [i["id"] for i in second["items"]] == [i["id"] for i in first["items"]]
        stored = int(await db.scalar(select(func.count()).select_from(SecurityObjective).where(
            SecurityObjective.organization_id == org.id)))
        assert stored == 12

        s = second["summary"]
        assert s["objectives"] == len(second["items"])
        assert s["on_track"] + s["at_risk"] + s["off_track"] + s["in_progress"] + s["needs_attention"] \
            == s["objectives"]
        assert s["proposed"] + s["insufficient_data"] + s["overdue"] + s["not_measured"] == s["needs_attention"]

    async def test_the_register_and_the_dashboard_give_the_same_reading(self, db, no_commit):
        org = await _org(db)
        admin, _ = await _member(db, org, "admin")
        team = await _team(db, org)
        logged = await _create(db, org, admin, owning_team_id=team.id, metric=LOGGED)
        await _confirm(db, org, admin, logged["id"])
        await api.log_isms_metric_result(
            org_id=org.id, objective_id=logged["id"], data=ResultCreate(value=88), request=_request(),
            membership=admin, db=db,
        )
        await api.update_security_objective(
            org_id=org.id, objective_id=(await _create(db, org, admin, name="Gone"))["id"],
            data=ObjectiveUpdate(archived=True), request=_request("PATCH"), membership=admin, db=db,
        )

        register = await api.list_security_objectives(org_id=org.id, include_archived=False, membership=admin, db=db)
        dashboard = await api.list_isms_metrics(org_id=org.id, membership=admin, db=db)
        assert [i["id"] for i in register["items"]] == [i["id"] for i in dashboard["items"]]
        assert len(dashboard["items"]) == 13  # twelve starters and the logged one; the archived one is left out
        for a, b in zip(register["items"], dashboard["items"]):
            assert a["reading"] == b["reading"], a["ref"]
        assert register["summary"] == dashboard["summary"]
        mine = next(i for i in dashboard["items"] if i["id"] == logged["id"])
        assert (mine["reading"]["status"], mine["reading"]["value"]) == ("at_risk", 88)

        detail = await api.get_isms_metric(org_id=org.id, objective_id=logged["id"], membership=admin, db=db)
        assert detail["objective"]["reading"] == mine["reading"]
        assert [r["value"] for r in detail["results"]] == [88]

    async def test_measures_and_wording(self, db, no_commit):
        org = await _org(db)
        viewer, _ = await _member(db, org, "viewer")
        measures = await api.list_isms_measures(org_id=org.id, text="supplier", membership=viewer, db=db)
        assert len(measures) == 15 and measures[0]["key"] == "supplier_assurance_current"
        assert measures[0]["match"] > 0
        wording = await api.isms_metric_wording(
            org_id=org.id, data=WordingRequest(kind="live", measure_key="controls_implemented", target_value=80),
            membership=viewer,
        )
        assert wording["target_text"] and wording["metric_name"]
        with pytest.raises(HTTPException) as exc:
            await api.isms_metric_wording(
                org_id=org.id, data=WordingRequest(kind="live", measure_key="nope"), membership=viewer)
        assert exc.value.status_code == 422


@requires_postgres
@pytest.mark.asyncio
class TestResultsAndAudit:
    async def test_a_logged_value_changes_the_reading_and_a_live_metric_refuses_one(self, db, no_commit):
        org = await _org(db)
        editor, _ = await _member(db, org, "editor")
        logged = await _create(db, org, editor, metric=LOGGED)
        assert logged["reading"]["value"] is None
        after = await api.log_isms_metric_result(
            org_id=org.id, objective_id=logged["id"], data=ResultCreate(value=72, note="Q4 count"),
            request=_request(), membership=editor, db=db,
        )
        assert after["reading"]["value"] == 72 and after["reading"]["value_text"] != logged["reading"]["value_text"]
        assert after["reading"]["measured_on"] == date.today()

        live = await _create(db, org, editor, metric=LIVE)
        with pytest.raises(HTTPException) as exc:
            await api.log_isms_metric_result(
                org_id=org.id, objective_id=live["id"], data=ResultCreate(value=50), request=_request(),
                membership=editor, db=db,
            )
        assert exc.value.status_code == 422
        assert int(await db.scalar(select(func.count()).select_from(IsmsMetricResult).where(
            IsmsMetricResult.organization_id == org.id))) == 1

    async def test_every_mutation_leaves_an_audit_row_with_the_caller(self, db, no_commit):
        org = await _org(db)
        editor, editor_user = await _member(db, org, "editor")
        admin, admin_user = await _member(db, org, "admin")
        team = await _team(db, org)

        out = await _create(db, org, editor, metric=LOGGED)
        oid = out["id"]
        seen = {editor_user.id: await _audit_rows(db, oid, editor_user.id), admin_user.id: 0}
        assert seen[editor_user.id] >= 1, "create"

        async def grew(user, step):
            now = await _audit_rows(db, oid, user.id)
            assert now > seen[user.id], step
            seen[user.id] = now

        await api.update_security_objective(
            org_id=org.id, objective_id=oid, data=ObjectiveUpdate(owning_team_id=team.id), request=_request("PATCH"),
            membership=editor, db=db)
        await grew(editor_user, "patch")
        await _set_metric(db, org, editor, oid, {**LOGGED, "target_value": 90, "warn_value": 80})
        await grew(editor_user, "metric")
        await _confirm(db, org, admin, oid)
        await grew(admin_user, "confirm")
        await api.record_security_objective_evaluation(
            org_id=org.id, objective_id=oid, data=EvaluationCreate(note="Reviewed at the ISMS meeting"),
            request=_request(), membership=editor, db=db)
        await grew(editor_user, "evaluation")
        await api.update_security_objective(
            org_id=org.id, objective_id=oid, data=ObjectiveUpdate(archived=True), request=_request("PATCH"),
            membership=admin, db=db)
        await grew(admin_user, "archive")
        archived_at = (await db.get(SecurityObjective, oid)).archived_at
        assert archived_at is not None

        await api.update_security_objective(
            org_id=org.id, objective_id=oid, data=ObjectiveUpdate(archived=False), request=_request("PATCH"),
            membership=admin, db=db)
        await api.log_isms_metric_result(
            org_id=org.id, objective_id=oid, data=ResultCreate(value=91), request=_request(),
            membership=editor, db=db)
        (result_id,) = (await db.execute(select(IsmsMetricResult.id).where(
            IsmsMetricResult.organization_id == org.id))).scalars().all()
        rows = (await db.execute(select(AuditLog).where(AuditLog.entity_id == result_id))).scalars().all()
        assert rows and {(r.entity_type, r.action, r.changed_by_user_id) for r in rows} == {
            ("isms_metric_result", "create", editor_user.id)}


@requires_postgres
@pytest.mark.asyncio
class TestIsmsProfile:
    async def test_the_profile_reads_objectives_from_the_table(self, db, no_commit):
        org = await _org(db)
        admin, _ = await _member(db, org, "admin")
        team = await _team(db, org)
        db.add(IsmsProfile(organization_id=org.id, objectives=[{"id": "stale", "objective": "Old JSON row"}]))
        await db.flush()
        kept = await _create(db, org, admin, name="Protect client data", owning_team_id=team.id, metric=LOGGED)
        await _confirm(db, org, admin, kept["id"])
        await _create(db, org, admin, name="Still proposed", metric=LOGGED)

        out = await profile_api.get_isms_profile(org_id=org.id, membership=admin, db=db)
        assert [(o.id, o.objective, o.owner, o.status, o.measure, o.target) for o in out.objectives] == [
            (str(kept["id"]), "Protect client data", team.name, "in_progress", "Training completion (%)",
             "at least 95%")]

        saved = await profile_api.update_isms_profile_section(
            org_id=org.id, section="audit_programme", payload=profile_api.SectionUpdate(data=[{"area": "HR"}]),
            request=_request("PUT"), membership=admin, db=db,
        )
        assert [o.id for o in saved.objectives] == [str(kept["id"])]
        assert [a.area for a in saved.audit_programme] == ["HR"]


@requires_postgres
@pytest.mark.asyncio
class TestNightly:
    async def test_two_runs_on_one_day_write_one_row_per_live_metric(self, db, no_commit, monkeypatch):
        org = await _org(db)

        async def only_ours(_db):
            return [org.id]

        monkeypatch.setattr(tasks_automation, "_organization_ids", only_ours)
        today = date.today()
        first = await tasks_automation.record_isms_metric_results(db, today)
        live = [s for s in service.STARTERS if s.kind == "live"]
        assert first == {"results_written": len(live), "organisations": 1, "organisations_failed": 0}
        second = await tasks_automation.record_isms_metric_results(db, today)
        assert second == {"results_written": 0, "organisations": 1, "organisations_failed": 0}
        assert await _computed_rows(db, org) == len(live) == 8
        days = (await db.execute(select(IsmsMetricResult.measured_on).where(
            IsmsMetricResult.organization_id == org.id))).scalars().all()
        assert set(days) == {today}

    async def test_one_organisation_failing_does_not_stop_the_next(self, db, no_commit, monkeypatch):
        broken, fine = await _org(db), await _org(db)
        real = service.record_nightly_results

        async def flaky(session, org_id, today):
            if org_id == broken.id:
                raise RuntimeError("calculator fell over")
            return await real(session, org_id, today)

        async def both(_db):
            return [broken.id, fine.id]

        monkeypatch.setattr(service, "record_nightly_results", flaky)
        monkeypatch.setattr(tasks_automation, "_organization_ids", both)
        out = await tasks_automation.record_isms_metric_results(db, date.today())
        assert out == {"results_written": 8, "organisations": 1, "organisations_failed": 1}
        assert await _computed_rows(db, broken) == 0
        assert await _computed_rows(db, fine) == 8
