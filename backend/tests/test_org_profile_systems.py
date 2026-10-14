"""Organisation profile, system boundary record and vendor notes (#1124, PIG R4).

The playbook asks which systems sit inside the boundary and how critical each
one is, how fast it must come back and how much data it may lose, where it
runs and what it holds; for each vendor, where our data sits, what flows down
by contract and who is responsible for what; and for the organisation, its
size and mission. These tests pin that record and the journey gate that reads
it:

    {"type": "systems_with_criticality", "min_fraction": 1}

* the new fields are written through the real endpoints, stay in their own
  organisation and land in audit_log;
* the gate counts only systems someone put inside the boundary, fails on an
  empty boundary, and is unknown, never met, on a parameter it cannot honour.

Everything written runs inside a transaction that is rolled back.
"""
import uuid as _uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from api import journey as journey_api
from api import organizations as organizations_api
from api import systems as systems_api
from api import vendors as vendors_api
from auth import OrgMembership
from models import AuditLog, Organization, System, User, Vendor
from schemas import (
    OrganizationSettingsUpdate,
    SystemCreate,
    SystemUpdate,
    VendorUpdate,
)
from services import journey as journey_service

from test_journey_service import DATABASE_URL, requires_postgres

GATE = {"type": "systems_with_criticality", "min_fraction": 1}


# ---------------------------------------------------------------------------
# The check's parameters: structural, no database
# ---------------------------------------------------------------------------


def test_the_gate_is_well_formed_and_importable():
    assert journey_service.check_param_problem(GATE) is None
    assert journey_service.check_param_problem({"type": "systems_with_criticality"}) is None
    template = {"stages": [{"precondition_spec": [GATE]}]}
    assert journey_service.unsupported_checks(template) == set()


@pytest.mark.parametrize("extra", [
    {"domains": ["any"]},
    {"scf_ids": ["any"]},
    {"basis": "mcr"},
    {"min_count": 1},
    {"in_scope_only": True},
])
def test_a_parameter_the_gate_cannot_honour_is_refused(extra):
    problem = journey_service.check_param_problem({**GATE, **extra})
    assert problem and "counts systems" in problem


@pytest.mark.parametrize("value", [0, -0.5, 1.5, "1", True, None, float("nan"), float("inf")])
def test_min_fraction_must_be_a_fraction(value):
    problem = journey_service.check_param_problem({**GATE, "min_fraction": value})
    assert problem == "min_fraction must be a number above 0 and at most 1"


@pytest.mark.asyncio
async def test_a_refused_parameter_reads_as_unknown_not_met():
    # check_param_problem runs before any query, so no database is touched.
    out = await journey_service._evaluate_one(None, _uuid.uuid4(), {**GATE, "domains": ["x"]}, {})
    assert out["met"] is None and out["detail"].startswith("Cannot evaluate:")


# ---------------------------------------------------------------------------
# Field validation: structural, no database
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field,value", [
    ("criticality", "extreme"),
    ("data_classification", "secret"),
    ("rto_hours", -1),
    ("rpo_hours", -0.1),
    ("rto_hours", float("inf")),
    ("rpo_hours", float("nan")),
    ("hosting_location", "x" * 256),
])
def test_system_fields_reject_values_outside_their_vocabulary(field, value):
    with pytest.raises(ValidationError):
        SystemCreate(name="s", system_type="custom", **{field: value})
    with pytest.raises(ValidationError):
        SystemUpdate(**{field: value})


def test_business_size_uses_the_guidance_keys():
    for size in ("micro_small", "small", "medium", "large", "enterprise"):
        assert OrganizationSettingsUpdate(business_size=size).business_size == size
    with pytest.raises(ValidationError):
        OrganizationSettingsUpdate(business_size="huge")


def test_a_blank_mission_statement_clears_it():
    assert OrganizationSettingsUpdate(mission_statement="   ").mission_statement is None
    assert OrganizationSettingsUpdate(mission_statement=" Keep it safe ").mission_statement == "Keep it safe"


def test_a_stray_stored_mission_statement_reads_as_not_recorded():
    assert organizations_api._mission_statement({"mission_statement": 5}) is None
    assert organizations_api._mission_statement({"mission_statement": "Serve"}) == "Serve"


def test_a_stray_stored_business_size_reads_as_not_recorded():
    assert organizations_api._business_size({"business_size": "huge"}) is None
    assert organizations_api._business_size({}) is None
    assert organizations_api._business_size({"business_size": "large"}) == "large"


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


async def _org(db):
    tag = _uuid.uuid4().hex[:10]
    org = Organization(name=f"pig-{tag}", slug=f"pig-{tag}")
    db.add(org)
    await db.flush()
    return org


async def _user(db):
    tag = _uuid.uuid4().hex[:10]
    user = User(google_sub=f"pig-{tag}", email=f"pig-{tag}@example.invalid")
    db.add(user)
    await db.flush()
    return user


def _membership(org, user, role):
    return OrgMembership(
        user=SimpleNamespace(db_id=str(user.id), email=user.email),
        organization_id=org.id,
        role=role,
    )


def _request():
    return Request({"type": "http", "headers": [], "method": "POST", "path": "/"})


async def _system(db, org, **fields):
    system = System(
        organization_id=org.id, name=f"sys-{_uuid.uuid4().hex[:8]}", system_type="custom", **fields
    )
    db.add(system)
    await db.flush()
    return system


async def _gate(db, org, check=GATE):
    totals = await journey_service._in_scope_counts(db, org.id)
    return await journey_service._evaluate_one(db, org.id, check, totals)


async def _audited(db, org, entity_id):
    rows = (await db.execute(select(AuditLog.field_name, AuditLog.old_value, AuditLog.new_value).where(
        AuditLog.organization_id == org.id,
        AuditLog.entity_id == entity_id,
    ))).all()
    return {r.field_name: (r.old_value, r.new_value) for r in rows}


@requires_postgres
@pytest.mark.asyncio
class TestSystemBoundaryRecord:

    @pytest_asyncio.fixture
    async def world(self, db, no_commit):
        org = await _org(db)
        editor = _membership(org, await _user(db), "editor")
        return SimpleNamespace(org=org, editor=editor)

    async def test_create_records_and_audits_the_boundary_fields(self, db, world):
        out = await systems_api.create_system(
            world.org.id,
            SystemCreate(
                name="Ledger", system_type="custom", in_scope=True, criticality="high",
                rto_hours=4, rpo_hours=0.5, data_classification="confidential",
                hosting_location="eu-west-2",
            ),
            _request(), world.editor, db,
        )
        assert (out.in_scope, out.criticality, out.rto_hours, out.rpo_hours) == (True, "high", 4, 0.5)
        assert (out.data_classification, out.hosting_location) == ("confidential", "eu-west-2")

        audited = await _audited(db, world.org, out.id)
        for field in ("in_scope", "criticality", "rto_hours", "rpo_hours",
                      "data_classification", "hosting_location"):
            assert field in audited, field
        assert audited["criticality"] == (None, '"high"')

    async def test_update_audits_the_change(self, db, world):
        system = await _system(db, world.org, in_scope=True)
        await systems_api.update_system(
            world.org.id, system.id, SystemUpdate(criticality="critical", in_scope=False),
            _request(), world.editor, db,
        )
        audited = await _audited(db, world.org, system.id)
        assert audited["criticality"] == ("null", '"critical"')
        assert audited["in_scope"] == ("true", "false")

    async def test_the_in_scope_filter_stays_inside_the_organisation(self, db, world):
        inside = await _system(db, world.org, in_scope=True)
        outside = await _system(db, world.org, in_scope=False)
        undecided = await _system(db, world.org)
        other = await _org(db)
        await _system(db, other, in_scope=True)

        async def listed(flag):
            rows = await systems_api.list_systems(
                world.org.id, membership=world.editor, system_type=None, status=None,
                vendor_id=None, in_scope=flag, db=db,
            )
            return {r.id for r in rows}

        assert await listed("true") == {inside.id}
        assert await listed("false") == {outside.id}
        assert await listed("undecided") == {undecided.id}
        assert await listed(None) == {inside.id, outside.id, undecided.id}

    async def test_the_database_refuses_a_value_outside_the_vocabulary(self, db, world):
        for fields in ({"criticality": "extreme"}, {"data_classification": "secret"}, {"rto_hours": -1}):
            with pytest.raises(IntegrityError):
                async with db.begin_nested():
                    await _system(db, world.org, **fields)


@requires_postgres
@pytest.mark.asyncio
class TestSystemsWithCriticalityGate:

    @pytest_asyncio.fixture
    async def org(self, db):
        return await _org(db)

    async def test_an_empty_boundary_does_not_pass(self, db, org):
        out = await _gate(db, org)
        assert out["met"] is False and out["detail"] == "No systems marked in scope yet"
        # A rated system nobody has put inside the boundary is still no boundary.
        await _system(db, org, criticality="high")
        await _system(db, org, in_scope=False, criticality="high")
        assert (await _gate(db, org))["detail"] == "No systems marked in scope yet"

    async def test_every_in_scope_system_needs_a_criticality(self, db, org):
        await _system(db, org, in_scope=True, criticality="medium")
        unrated = await _system(db, org, in_scope=True)
        out = await _gate(db, org)
        assert out["met"] is False
        assert out["detail"] == "1 of 2 in-scope systems with a criticality (50.0%) — 2 required, 1 more needed"

        unrated.criticality = "low"
        await db.flush()
        out = await _gate(db, org)
        assert out["met"] is True
        assert out["detail"] == "2 of 2 in-scope systems with a criticality (100.0%) — 2 required"

    async def test_systems_marked_out_of_scope_are_not_counted(self, db, org):
        await _system(db, org, in_scope=True, criticality="high")
        await _system(db, org, in_scope=False)
        assert (await _gate(db, org))["met"] is True

    async def test_an_undecided_system_is_a_shortfall_until_rated_or_placed(self, db, org):
        # One rated system among undecided ones is not a complete boundary.
        await _system(db, org, in_scope=True, criticality="high")
        undecided = await _system(db, org)
        out = await _gate(db, org)
        assert out["met"] is False
        assert out["detail"] == (
            "1 of 2 systems in scope or undecided (1 undecided) with a criticality (50.0%)"
            " — 2 required, 1 more needed"
        )
        undecided.in_scope = False
        await db.flush()
        assert (await _gate(db, org))["met"] is True
        undecided.in_scope = None
        undecided.criticality = "low"
        await db.flush()
        assert (await _gate(db, org))["met"] is True

    async def test_min_fraction_lowers_the_bar(self, db, org):
        await _system(db, org, in_scope=True, criticality="high")
        await _system(db, org, in_scope=True)
        assert (await _gate(db, org, {**GATE, "min_fraction": 0.5}))["met"] is True

    async def test_another_organisations_systems_do_not_count(self, db, org):
        other = await _org(db)
        await _system(db, other, in_scope=True, criticality="high")
        assert (await _gate(db, org))["met"] is False
        await _system(db, org, in_scope=True)
        out = await _gate(db, org)
        assert out["detail"].startswith("0 of 1 ")


@requires_postgres
@pytest.mark.asyncio
class TestVendorAndOrganisationProfile:

    @pytest_asyncio.fixture
    async def world(self, db, no_commit):
        org = await _org(db)
        user = await _user(db)
        return SimpleNamespace(
            org=org,
            editor=_membership(org, user, "editor"),
            admin=_membership(org, user, "admin"),
        )

    async def test_vendor_notes_are_written_and_audited(self, db, world):
        vendor = Vendor(organization_id=world.org.id, name=f"v-{_uuid.uuid4().hex[:8]}")
        db.add(vendor)
        await db.flush()
        out = await vendors_api.update_vendor(
            world.org.id, vendor.id,
            VendorUpdate(
                data_location="EU (Frankfurt)",
                flow_down_requirements="Breach notice within 24 hours",
                shared_responsibility_note="They patch the platform; we manage accounts",
            ),
            _request(), world.editor, db,
        )
        assert out.data_location == "EU (Frankfurt)"
        assert out.flow_down_requirements == "Breach notice within 24 hours"
        audited = await _audited(db, world.org, vendor.id)
        assert {"data_location", "flow_down_requirements", "shared_responsibility_note"} <= set(audited)

    async def test_another_organisation_cannot_write_the_vendor(self, db, world):
        vendor = Vendor(organization_id=world.org.id, name=f"v-{_uuid.uuid4().hex[:8]}")
        db.add(vendor)
        await db.flush()
        other = await _org(db)
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            await vendors_api.update_vendor(
                other.id, vendor.id, VendorUpdate(data_location="anywhere"),
                _request(), _membership(other, await _user(db), "editor"), db,
            )
        assert exc.value.status_code == 404

    async def test_business_size_and_mission_round_trip_and_are_audited(self, db, world):
        saved = await organizations_api.update_organization_settings(
            world.org.id,
            OrganizationSettingsUpdate(business_size="small", mission_statement="Keep patients safe"),
            _request(), world.admin, db,
        )
        assert (saved.business_size, saved.mission_statement) == ("small", "Keep patients safe")

        read = await organizations_api.get_organization_settings(world.org.id, world.admin, db)
        assert (read.business_size, read.mission_statement) == ("small", "Keep patients safe")

        audited = await _audited(db, world.org, world.org.id)
        assert '"business_size": "small"' in audited["settings"][1]

        # An explicit null clears it; leaving it out leaves it alone.
        await organizations_api.update_organization_settings(
            world.org.id, OrganizationSettingsUpdate(industry="Health"), _request(), world.admin, db,
        )
        assert (await organizations_api.get_organization_settings(world.org.id, world.admin, db)).business_size == "small"
        await organizations_api.update_organization_settings(
            world.org.id, OrganizationSettingsUpdate(business_size=None), _request(), world.admin, db,
        )
        assert (await organizations_api.get_organization_settings(world.org.id, world.admin, db)).business_size is None


@requires_postgres
@pytest.mark.asyncio
async def test_the_import_endpoint_refuses_a_gate_it_cannot_honour(db, monkeypatch):
    org = await _org(db)
    # Should validation ever regress, the endpoint would reach its commit;
    # keep that a flush so a failing run still leaves no rows behind.
    monkeypatch.setattr(db, "commit", db.flush)
    payload = journey_api.ImportRequest(template={"stages": [{
        "key": "boundary", "title": "Boundary",
        "precondition_spec": [{**GATE, "domains": ["any"]}],
    }]})
    with pytest.raises(journey_api.HTTPException) as exc:
        await journey_api.import_journey(org.id, _request(), payload, SimpleNamespace(user=None), db)
    assert exc.value.status_code == 422
    assert "counts systems" in str(exc.value.detail)
