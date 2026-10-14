"""Capability test records (#1131, PIG R11).

These tests pin:

* the record: a test of a known type, run on or before today, with a
  scenario, a result and an outcome; audited; another organisation's is
  never read or written;
* findings as deficiencies: each finding is a deficiency linked to its test
  (source ``capability_test``), against an in-scope control, an assessed
  risk or neither, with an optional root cause; every finding is checked
  before anything is written;
* the result agreeing with the findings: ``passed`` with none, otherwise at
  least one, on create, on correction and when a finding is added (a
  cancelled finding does not count);
* the root cause on any deficiency, through the deficiency API;
* the journey check ``capability_tests_within_days``: counts tests run in
  the window whatever their result, and fails closed on a parameter it
  cannot honour.

Control ids are read from the catalogue at run time. Everything that touches
the database runs inside a transaction that is rolled back.
"""
import inspect
import json
import uuid as _uuid
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from api import capability_tests as api
from api import deficiencies as deficiencies_api
from auth import OrgMembership
from catalog_models import SCFCatalogControl
from middleware.audit_middleware import _action_for, _extract_entity_type
from models import (
    AuditLog,
    CapabilityTest,
    Organization,
    OrganizationMember,
    RiskAssessment,
    ScopedControl,
    User,
    VendorActionItem,
)
from schemas import (
    CapabilityTestCreate,
    CapabilityTestFinding,
    CapabilityTestUpdate,
    DeficiencyCreate,
    DeficiencyUpdate,
)
from services import capability_tests as service
from services import deficiencies
from services import journey as journey_service

from test_journey_service import DATABASE_URL, requires_postgres  # noqa: F401

TODAY = date.today()
GATE = "capability_tests_within_days"


# ---------------------------------------------------------------------------
# Structural: no database
# ---------------------------------------------------------------------------


def _min_role(handler):
    dependency = inspect.signature(handler).parameters["membership"].default.dependency
    assert dependency.__qualname__.startswith("require_org_role")
    return dict(zip(dependency.__code__.co_freevars, (c.cell_contents for c in dependency.__closure__)))["min_role"]


def test_the_routes_are_registered_with_their_roles():
    routes = {(r.path, m) for r in api.router.routes for m in getattr(r, "methods", ())}
    org = "/organizations/{org_id}"
    assert routes == {
        (f"{org}/capability-tests", "GET"),
        (f"{org}/capability-tests", "POST"),
        (f"{org}/capability-tests/{{test_id}}", "GET"),
        (f"{org}/capability-tests/{{test_id}}", "PATCH"),
        (f"{org}/capability-tests/{{test_id}}/findings", "POST"),
    }
    assert _min_role(api.list_capability_tests) == "viewer"
    assert _min_role(api.get_capability_test) == "viewer"
    assert _min_role(api.create_capability_test) == "editor"
    assert _min_role(api.update_capability_test) == "editor"
    assert _min_role(api.add_capability_test_finding) == "editor"


def test_the_router_is_mounted_and_audited():
    import main

    paths = main.app.openapi()["paths"]
    org = "/api/organizations/{org_id}"
    assert {"get", "post"} <= set(paths.get(f"{org}/capability-tests", {}))
    assert {"get", "patch"} <= set(paths.get(f"{org}/capability-tests/{{test_id}}", {}))
    assert "post" in paths.get(f"{org}/capability-tests/{{test_id}}/findings", {})
    listing = {p["name"]: p["in"] for p in paths[f"{org}/deficiencies"]["get"]["parameters"]}
    assert listing.get("capability_test_id") == "query"
    oid, tid = _uuid.uuid4(), _uuid.uuid4()
    assert _extract_entity_type(f"/api/organizations/{oid}/capability-tests") == "capability_test"
    assert _action_for("POST", f"/api/organizations/{oid}/capability-tests") == "create"
    findings = f"/api/organizations/{oid}/capability-tests/{tid}/findings"
    assert _extract_entity_type(findings) == "capability_test"
    assert _action_for("POST", findings) == "update"


def test_a_deficiency_tracks_its_test_and_root_cause():
    assert {"capability_test_id", "root_cause"} <= deficiencies.DEFICIENCY_TRACKED_FIELDS
    assert service.TRACKED_FIELDS == {
        "test_type", "title", "tested_on", "scenario", "result", "outcome", "recorded_by_user_id",
    }


def _body(**overrides):
    fields = dict(test_type="incident_response", title="Ransomware tabletop", tested_on=TODAY,
                  scenario="Encrypted file server at 09:00", result="passed",
                  outcome="Contained within the hour")
    fields.update(overrides)
    return fields


@pytest.mark.parametrize(
    "overrides",
    [
        {"test_type": "tabletop"},
        {"result": "ok"},
        {"scenario": "   "},
        {"outcome": ""},
        {"title": " "},
        {"severity": "high"},
        {"findings": [{"title": " "}]},
        {"findings": [{"title": "t", "status": "completed"}]},
    ],
)
def test_a_malformed_test_is_refused(overrides):
    with pytest.raises(ValidationError):
        CapabilityTestCreate(**_body(**overrides))


def test_an_update_takes_no_unknown_field():
    with pytest.raises(ValidationError):
        CapabilityTestUpdate(severity="high")
    with pytest.raises(ValidationError):
        CapabilityTestUpdate(outcome="  ")


@pytest.mark.parametrize(
    "result,count,ok",
    [("passed", 0, True), ("passed", 1, False), ("passed_with_findings", 0, False),
     ("passed_with_findings", 2, True), ("failed", 0, False), ("failed", 1, True)],
)
def test_the_result_must_agree_with_the_findings(result, count, ok):
    assert (service.result_problem(result, count) is None) is ok


def test_a_test_is_recorded_once_it_has_run():
    assert service.tested_on_problem(TODAY, TODAY) is None
    assert service.tested_on_problem(TODAY + timedelta(days=1), TODAY) is not None


@pytest.mark.parametrize(
    "check",
    [
        {"type": GATE},
        {"type": GATE, "days": 0},
        {"type": GATE, "days": 3661},
        {"type": GATE, "days": True},
        {"type": GATE, "days": "365"},
        {"type": GATE, "days": 365, "min_count": 0},
        {"type": GATE, "days": 365, "min_count": True},
        {"type": GATE, "days": 365, "min_count": 1.5},
        {"type": GATE, "days": 365, "test_types": []},
        {"type": GATE, "days": 365, "test_types": ["tabletop"]},
        {"type": GATE, "days": 365, "test_types": "continuity"},
        {"type": GATE, "days": 365, "domains": ["GOV"]},
        {"type": GATE, "days": 365, "scf_ids": ["X"]},
        {"type": GATE, "days": 365, "max_count": 1},
        {"type": GATE, "days": 365, "within_days": 30},
    ],
)
def test_a_parameter_the_check_cannot_honour_is_refused(check):
    assert journey_service.check_param_problem(check) is not None


@pytest.mark.parametrize(
    "check",
    [
        {"type": GATE, "days": 365, "min_count": 2},
        {"type": GATE, "days": 1},
        {"type": GATE, "days": 3660, "label": "l", "test_types": ["continuity", "incident_response"]},
    ],
)
def test_well_formed_parameters_are_accepted(check):
    assert journey_service.check_param_problem(check) is None


def test_the_check_is_supported_and_evaluated():
    assert GATE in journey_service.SUPPORTED_CHECKS
    assert GATE not in journey_service.CONTROL_FILTERED_CHECKS
    assert f'"{GATE}"' in inspect.getsource(journey_service._evaluate_one)


# ---------------------------------------------------------------------------
# Database-backed: the real endpoints on a rolled-back transaction
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
    """Endpoints commit; inside the rolled-back test transaction that is a flush."""
    monkeypatch.setattr(db, "commit", db.flush)
    monkeypatch.setattr(db, "rollback", db.flush)


async def _org(db):
    tag = _uuid.uuid4().hex[:10]
    org = Organization(name=f"pig-{tag}", slug=f"pig-{tag}")
    db.add(org)
    await db.flush()
    return org


async def _user(db, org=None, role="editor"):
    tag = _uuid.uuid4().hex[:10]
    user = User(google_sub=f"pig-{tag}", email=f"pig-{tag}@example.invalid")
    db.add(user)
    await db.flush()
    if org is not None:
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=role))
        await db.flush()
    return user


def _membership(org, user, role="editor"):
    return OrgMembership(
        user=SimpleNamespace(db_id=str(user.id) if user else None, email=getattr(user, "email", None)),
        organization_id=org.id,
        role=role,
    )


def _request():
    return Request({"type": "http", "headers": [], "method": "POST", "path": "/"})


async def _controls(db, n):
    ids = list((await db.execute(
        select(SCFCatalogControl.scf_id).where(SCFCatalogControl.status == "active")
        .order_by(SCFCatalogControl.scf_id).limit(n)
    )).scalars().all())
    if len(ids) < n:  # pragma: no cover - environment dependent
        pytest.skip(f"catalogue has fewer than {n} active controls")
    return ids


async def _scope(db, org, scf_id, **kwargs):
    fields = dict(selected=True, implementation_status="not_started")
    fields.update(kwargs)
    row = ScopedControl(organization_id=org.id, scf_id=scf_id, **fields)
    db.add(row)
    await db.flush()
    return row


async def _risk(db, org, code="R-ORG-1"):
    db.add(RiskAssessment(organization_id=org.id, risk_code=code))
    await db.flush()
    return code


async def _create(db, org, user, **overrides):
    return await api.create_capability_test(
        org.id, CapabilityTestCreate(**_body(**overrides)), _request(), _membership(org, user), db,
    )


async def _tests_of(db, org):
    return int(await db.scalar(
        select(func.count(CapabilityTest.id)).where(CapabilityTest.organization_id == org.id)
    ) or 0)


async def _findings_of(db, org):
    return list((await db.execute(
        select(VendorActionItem).where(
            VendorActionItem.organization_id == org.id, VendorActionItem.capability_test_id.isnot(None),
        )
    )).scalars().all())


async def _audited(db, org, entity_id):
    return list((await db.execute(select(AuditLog).where(
        AuditLog.organization_id == org.id, AuditLog.entity_id == entity_id,
    ))).scalars().all())


@requires_postgres
@pytest.mark.asyncio
class TestRecord:
    async def test_a_test_and_its_findings_are_recorded_and_audited(self, db, no_commit):
        org = await _org(db)
        user = await _user(db, org)
        (scf,) = await _controls(db, 1)
        control = await _scope(db, org, scf)
        code = await _risk(db, org)

        out = await _create(db, org, user, result="failed", findings=[
            {"title": "Backups untested", "scf_id": scf.lower(), "priority": "high",
             "root_cause": "  No restore drill in the plan  ", "due_date": str(TODAY + timedelta(days=30))},
            {"title": "Call tree out of date", "risk_code": code},
            {"title": "Nobody knew who declares an incident"},
        ])
        assert (out.result, out.test_type, out.tested_on) == ("failed", "incident_response", TODAY)
        assert out.recorded_by_user_id == user.id
        assert (out.finding_count, out.open_finding_count) == (3, 3)
        assert [f.title for f in out.findings] == [
            "Backups untested", "Call tree out of date", "Nobody knew who declares an incident"]
        first, second, third = out.findings
        assert (first.scf_id, first.scoped_control_id, first.root_cause) == (scf, control.id,
                                                                              "No restore drill in the plan")
        assert (second.scf_id, second.risk_code, second.root_cause) == (None, code, None)
        assert (third.scf_id, third.risk_code) == (None, None)
        assert all(f.capability_test_id == out.id and f.source == "capability_test"
                   and f.auto_generated is False and f.status == "open" for f in out.findings)

        entries = await _audited(db, org, out.id)
        assert {e.field_name for e in entries} == service.TRACKED_FIELDS
        assert {(e.entity_type, e.action, e.changed_by_user_id) for e in entries} == {
            ("capability_test", "create", user.id)}
        cause = await _audited(db, org, first.id)
        assert {(e.entity_type, e.action) for e in cause} == {("deficiency", "create")}
        assert {e.new_value for e in cause if e.field_name == "root_cause"} == {
            json.dumps("No restore drill in the plan")}
        assert {e.new_value for e in cause if e.field_name == "capability_test_id"} == {json.dumps(str(out.id))}

    async def test_a_result_that_disagrees_with_the_findings_writes_nothing(self, db, no_commit):
        org = await _org(db)
        user = await _user(db, org)
        for overrides in ({"result": "passed", "findings": [{"title": "Gap"}]},
                          {"result": "failed"},
                          {"result": "passed_with_findings"},
                          {"tested_on": TODAY + timedelta(days=1)}):
            with pytest.raises(HTTPException) as exc:
                await _create(db, org, user, **overrides)
            assert exc.value.status_code == 422
        assert await _tests_of(db, org) == 0

    async def test_every_finding_is_checked_before_anything_is_written(self, db, no_commit):
        org, other = await _org(db), await _org(db)
        user = await _user(db, org)
        a, b = await _controls(db, 2)
        await _scope(db, org, a)
        await _scope(db, org, b, selected=False)
        await _scope(db, other, a)
        theirs = await _risk(db, other, "R-ORG-9")
        stranger = await _user(db, other)
        bad = [
            ({"scf_id": b}, 422),                              # descoped
            ({"scf_id": "X" + _uuid.uuid4().hex[:8]}, 422),    # unknown
            ({"risk_code": theirs}, 422),                       # another organisation's risk
            ({"owner_user_id": str(stranger.id)}, 404),         # not a member
        ]
        for finding, status in bad:
            with pytest.raises(HTTPException) as exc:
                await _create(db, org, user, result="failed",
                              findings=[{"title": "Fine", "scf_id": a}, {"title": "Bad", **finding}])
            assert exc.value.status_code == status
        assert await _tests_of(db, org) == 0
        assert await _findings_of(db, org) == []

    async def test_tests_are_listed_newest_first_filtered_and_never_another_organisations(self, db, no_commit):
        org, other = await _org(db), await _org(db)
        user = await _user(db, org)
        old = await _create(db, org, user, test_type="continuity", tested_on=TODAY - timedelta(days=200))
        new = await _create(db, org, user, tested_on=TODAY - timedelta(days=3), result="passed_with_findings",
                            findings=[{"title": "A"}, {"title": "B"}])
        await _create(db, other, await _user(db, other))
        # A cancelled finding (raised in error) is not counted.
        cancelled = new.findings[1]
        await deficiencies_api.update_deficiency(
            org.id, cancelled.id, DeficiencyUpdate(status="cancelled"), _request(), _membership(org, user), db,
        )
        viewer = _membership(org, await _user(db, org, "viewer"), "viewer")

        listed = await api.list_capability_tests(org.id, None, None, viewer, db)
        assert [t.id for t in listed] == [new.id, old.id]
        assert [(t.finding_count, t.open_finding_count) for t in listed] == [(1, 1), (0, 0)]
        assert all(t.findings is None for t in listed)
        assert [t.id for t in await api.list_capability_tests(org.id, "continuity", None, viewer, db)] == [old.id]
        since = TODAY - timedelta(days=10)
        assert [t.id for t in await api.list_capability_tests(org.id, None, since, viewer, db)] == [new.id]

        one = await api.get_capability_test(org.id, new.id, viewer, db)
        assert [f.title for f in one.findings] == ["A", "B"] and one.finding_count == 1
        theirs = await api.list_capability_tests(other.id, None, None, viewer, db)
        with pytest.raises(HTTPException) as exc:
            await api.get_capability_test(org.id, theirs[0].id, viewer, db)
        assert exc.value.status_code == 404

    async def test_a_closed_finding_counts_but_is_not_open_and_another_organisations_never_counts(
        self, db, no_commit,
    ):
        org, other = await _org(db), await _org(db)
        user = await _user(db, org)
        test = await _create(db, org, user, result="failed", findings=[{"title": "A"}, {"title": "B"}])
        await deficiencies_api.update_deficiency(
            org.id, test.findings[0].id,
            DeficiencyUpdate(status="completed", retest_note="Restore drill rerun and passed"),
            _request(), _membership(org, user), db,
        )
        # A row the API would never write: another organisation's item
        # pointing at this test. It must not count towards it.
        db.add(VendorActionItem(
            organization_id=other.id, vendor_id=None, title="Stray", status="open",
            source="capability_test", capability_test_id=test.id, auto_generated=False,
        ))
        await db.flush()
        viewer = _membership(org, await _user(db, org, "viewer"), "viewer")
        (listed,) = await api.list_capability_tests(org.id, None, None, viewer, db)
        assert (listed.finding_count, listed.open_finding_count) == (2, 1)
        one = await api.get_capability_test(org.id, test.id, viewer, db)
        assert (one.finding_count, one.open_finding_count) == (2, 1)
        assert [f.title for f in one.findings] == ["A", "B"]


@requires_postgres
@pytest.mark.asyncio
class TestCorrection:
    async def _patch(self, db, org, user, test_id, **fields):
        return await api.update_capability_test(
            org.id, test_id, CapabilityTestUpdate(**fields), _request(), _membership(org, user), db,
        )

    async def test_a_correction_is_audited_field_by_field(self, db, no_commit):
        org = await _org(db)
        user = await _user(db, org)
        test = await _create(db, org, user)
        out = await self._patch(db, org, user, test.id, title="Ransomware tabletop, Q4")
        assert out.title == "Ransomware tabletop, Q4"
        updates = [e for e in await _audited(db, org, test.id) if e.action == "update"]
        assert [(e.field_name, e.new_value) for e in updates] == [("title", json.dumps("Ransomware tabletop, Q4"))]
        for fields in ({}, {"findings": []}, {"title": None}, {"tested_on": TODAY + timedelta(days=2)}):
            with pytest.raises(HTTPException) as exc:
                await self._patch(db, org, user, test.id, **fields)
            assert exc.value.status_code == 422

    async def test_a_passed_test_is_corrected_to_failed_with_its_finding_in_one_request(self, db, no_commit):
        org = await _org(db)
        user = await _user(db, org)
        test = await _create(db, org, user)
        with pytest.raises(HTTPException):
            await self._patch(db, org, user, test.id, result="failed")
        with pytest.raises(HTTPException):
            await api.add_capability_test_finding(
                org.id, test.id, CapabilityTestFinding(title="Late"), _request(), _membership(org, user), db,
            )
        with pytest.raises(HTTPException):
            await self._patch(db, org, user, test.id, findings=[{"title": "Late"}])
        assert await _findings_of(db, org) == []

        out = await self._patch(db, org, user, test.id, result="failed",
                                findings=[{"title": "Late", "root_cause": "Pager rota lapsed"}])
        assert (out.result, out.finding_count) == ("failed", 1)
        assert out.findings[0].root_cause == "Pager rota lapsed"

    async def test_a_failed_test_reads_as_passed_only_once_its_findings_are_cancelled(self, db, no_commit):
        org = await _org(db)
        user = await _user(db, org)
        test = await _create(db, org, user, result="failed", findings=[{"title": "Raised in error"}])
        with pytest.raises(HTTPException):
            await self._patch(db, org, user, test.id, result="passed")
        await deficiencies_api.update_deficiency(
            org.id, test.findings[0].id, DeficiencyUpdate(status="cancelled"), _request(),
            _membership(org, user), db,
        )
        assert (await self._patch(db, org, user, test.id, result="passed")).result == "passed"
        # Restoring the finding would leave a passed test with a finding.
        with pytest.raises(HTTPException) as exc:
            await deficiencies_api.update_deficiency(
                org.id, test.findings[0].id, DeficiencyUpdate(status="open"), _request(),
                _membership(org, user), db,
            )
        assert exc.value.status_code == 422 and "Correct the capability test's result" in exc.value.detail
        assert [f.status for f in await _findings_of(db, org)] == ["cancelled"]

    async def test_a_cancelled_finding_of_a_test_that_did_not_pass_is_restored(self, db, no_commit):
        org = await _org(db)
        user = await _user(db, org)
        member = _membership(org, user)
        test = await _create(db, org, user, result="failed", findings=[{"title": "A"}, {"title": "B"}])
        first = test.findings[0].id
        await deficiencies_api.update_deficiency(
            org.id, first, DeficiencyUpdate(status="cancelled"), _request(), member, db)
        restored = await deficiencies_api.update_deficiency(
            org.id, first, DeficiencyUpdate(status="open"), _request(), member, db)
        assert restored.status == "open"
        assert (await api.get_capability_test(org.id, test.id, member, db)).open_finding_count == 2

    async def test_a_finding_found_later_is_added_to_its_test(self, db, no_commit):
        org, other = await _org(db), await _org(db)
        user = await _user(db, org)
        test = await _create(db, org, user, result="passed_with_findings", findings=[{"title": "First"}])
        added = await api.add_capability_test_finding(
            org.id, test.id, CapabilityTestFinding(title="Second", root_cause="Unclear handover"),
            _request(), _membership(org, user), db,
        )
        assert (added.capability_test_id, added.source, added.root_cause) == (
            test.id, "capability_test", "Unclear handover")
        theirs = await _create(db, other, await _user(db, other), result="failed", findings=[{"title": "T"}])
        with pytest.raises(HTTPException) as exc:
            await api.add_capability_test_finding(
                org.id, theirs.id, CapabilityTestFinding(title="X"), _request(), _membership(org, user), db,
            )
        assert exc.value.status_code == 404
        with pytest.raises(HTTPException) as exc:
            await self._patch(db, org, user, theirs.id, title="Mine now")
        assert exc.value.status_code == 404


@requires_postgres
@pytest.mark.asyncio
class TestRootCause:
    async def test_any_deficiency_carries_a_root_cause(self, db, no_commit):
        org = await _org(db)
        user = await _user(db, org)
        member = _membership(org, user)
        code = await _risk(db, org)
        item = await deficiencies_api.create_deficiency(
            org.id, DeficiencyCreate(title="Weak passwords", risk_code=code, root_cause="  Legacy policy "),
            _request(), member, db,
        )
        assert (item.root_cause, item.capability_test_id) == ("Legacy policy", None)
        blank = await deficiencies_api.create_deficiency(
            org.id, DeficiencyCreate(title="Other", risk_code=code, root_cause="   "), _request(), member, db,
        )
        assert blank.root_cause is None
        out = await deficiencies_api.update_deficiency(
            org.id, item.id, DeficiencyUpdate(root_cause="Policy never reviewed"), _request(), member, db,
        )
        assert out.root_cause == "Policy never reviewed"
        changed = [e for e in await _audited(db, org, item.id) if e.action == "update"]
        assert [(e.field_name, e.new_value) for e in changed] == [("root_cause", json.dumps("Policy never reviewed"))]
        trimmed = await deficiencies_api.update_deficiency(
            org.id, item.id, DeficiencyUpdate(root_cause="  Policy owner left  "), _request(), member, db,
        )
        assert trimmed.root_cause == "Policy owner left"
        blanked = await deficiencies_api.update_deficiency(
            org.id, item.id, DeficiencyUpdate(root_cause="   "), _request(), member, db,
        )
        assert blanked.root_cause is None
        await deficiencies_api.update_deficiency(
            org.id, item.id, DeficiencyUpdate(root_cause="Policy never reviewed"), _request(), member, db,
        )
        cleared = await deficiencies_api.update_deficiency(
            org.id, item.id, DeficiencyUpdate(root_cause=None), _request(), member, db,
        )
        assert cleared.root_cause is None

    async def test_the_deficiency_list_filters_by_test(self, db, no_commit):
        org = await _org(db)
        user = await _user(db, org)
        code = await _risk(db, org)
        test = await _create(db, org, user, result="failed", findings=[{"title": "Found"}])
        await deficiencies_api.create_deficiency(
            org.id, DeficiencyCreate(title="Unrelated", risk_code=code), _request(), _membership(org, user), db,
        )
        viewer = _membership(org, await _user(db, org, "viewer"), "viewer")
        listed = await deficiencies_api.list_deficiencies(
            org.id, None, False, None, None, None, viewer, db, capability_test_id=test.id,
        )
        assert [d.title for d in listed] == ["Found"]
        everything = await deficiencies_api.list_deficiencies(org.id, None, False, None, None, None, viewer, db)
        assert {d.title for d in everything} == {"Found", "Unrelated"}


async def _gate(db, org, **check):
    totals = await journey_service._in_scope_counts(db, org.id)
    return await journey_service._evaluate_one(db, org.id, {"type": GATE, **check}, totals)


def _test_row(org, days_ago, test_type="incident_response", result="passed"):
    return CapabilityTest(organization_id=org.id, test_type=test_type, title="t",
                          tested_on=TODAY - timedelta(days=days_ago), scenario="s", result=result, outcome="o")


@requires_postgres
@pytest.mark.asyncio
class TestJourneyCheck:
    async def test_tests_in_the_window_count_whatever_their_result(self, db, no_commit):
        org, other = await _org(db), await _org(db)
        db.add_all([
            _test_row(org, 0, result="failed"),
            _test_row(org, 364, test_type="continuity"),
            _test_row(org, 365),             # a year ago today: still counts
            _test_row(org, 366),             # a day before that: outside
            _test_row(org, -1),              # dated tomorrow: not run yet
            _test_row(other, 10), _test_row(other, 20),
        ])
        await db.flush()
        out = await _gate(db, org, days=365, min_count=3)
        assert out["met"] is True
        assert out["detail"] == "3 capability test(s) in the last 365 days — 3 required"
        assert (await _gate(db, org, days=365, min_count=4))["met"] is False
        assert (await _gate(db, org, days=366, min_count=4))["met"] is True
        assert (await _gate(db, org, days=1))["met"] is True
        only = await _gate(db, org, days=365, test_types=["continuity"])
        assert only["met"] is True and only["detail"].startswith("1 capability test(s) (continuity)")
        assert (await _gate(db, org, days=365, min_count=2, test_types=["continuity"]))["met"] is False

    async def test_no_tests_fails_and_a_bad_parameter_is_unknown(self, db, no_commit):
        org = await _org(db)
        assert (await _gate(db, org, days=365))["met"] is False
        db.add(_test_row(org, 1))
        await db.flush()
        for bad in ({}, {"days": 365, "domains": ["GOV"]}, {"days": 365, "test_types": ["drill"]}):
            out = await _gate(db, org, **bad)
            assert out["met"] is None and out["detail"].startswith("Cannot evaluate")
