"""Material controls, the approved procedure link and weighting order (#1126, PIG R6).

The playbook treats some controls as material: they cannot leave scope, or
be marked not applicable, without an approver. These tests pin:

* materiality itself: the catalogue weighting (10 is material) unless the
  control carries an override, which needs a reason and an admin;
* the guard on every write path that could exclude a material control, or
  mark it not applicable, and the one path (an admin's scope-override
  exclusion) that may;
* the link to an approved procedure document in the same organisation;
* the scoping list and the work queue carrying materiality and ordering by
  weighting;
* the gate ``material_controls_at_status``, which counts material in-scope
  controls, honours the domain and id filters, and fails closed.

Control ids, weightings and framework keys come from the seeded catalogue,
never from the test; structural tests use made-up ids of the same shape.
Everything written runs inside a transaction that is rolled back.
"""
import json
import uuid as _uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from api import dashboard as dashboard_api
from api import journey as journey_api
from api import scoped_controls as scoped_api
from auth import OrgMembership
from catalog_models import SCFCatalogControl
from models import (
    AuditLog,
    GeneratedDocument,
    Organization,
    OrganizationFrameworkSelection,
    OrganizationMember,
    ScopedControl,
    User,
)
from schemas import (
    BatchScopedControlOperation,
    BatchScopedControlRequest,
    BulkUnscopeFrameworkRequest,
    ControlMaterialityRequest,
    ControlProcedureDocumentRequest,
    ScopedControlCreate,
    ScopedControlsPaginatedResponse,
    ScopedControlUpdate,
    ScopeOverrideRequest,
)
from services import journey as journey_service
from services import materiality
from services.scoping_service import INTERNAL_MAPPING_PREFIXES, set_individual_scope_override

from test_journey_service import DATABASE_URL, requires_postgres

GATE = {"type": "material_controls_at_status", "statuses": ["implemented", "monitored"], "min_fraction": 1}


# ---------------------------------------------------------------------------
# The check's parameters: structural, no database
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("gate", [
    GATE,
    {**GATE, "domains": ["GOV", "RSK"], "min_fraction": 0.8},
    {**GATE, "scf_ids": ["any"], "label": "Step 3c"},
    {"type": "material_controls_at_status", "statuses": ["implemented"]},
])
def test_the_gate_is_well_formed_and_importable(gate):
    assert journey_service.check_param_problem(gate) is None
    template = {"stages": [{"precondition_spec": [gate]}]}
    assert journey_service.unsupported_checks(template) == set()
    assert gate["type"] in journey_service.CONTROL_FILTERED_CHECKS


@pytest.mark.parametrize("extra", [
    {"basis": "mcr"},
    {"min_count": 1},
    {"generator": "soa"},
    {"target": "L3"},
])
def test_a_parameter_the_gate_cannot_honour_is_refused(extra):
    problem = journey_service.check_param_problem({**GATE, **extra})
    assert problem and problem.startswith("'material_controls_at_status' takes no")


@pytest.mark.parametrize("statuses", [None, [], "implemented", ["done"], ["implemented", 3], [""]])
def test_statuses_must_be_a_non_empty_list_of_implementation_statuses(statuses):
    gate = {k: v for k, v in GATE.items() if k != "statuses"}
    if statuses is not None:
        gate["statuses"] = statuses
    problem = journey_service.check_param_problem(gate)
    assert problem and problem.startswith("statuses must")


@pytest.mark.parametrize("value", [0, -0.5, 1.5, "1", True, None, float("nan")])
def test_min_fraction_must_be_a_fraction(value):
    problem = journey_service.check_param_problem({**GATE, "min_fraction": value})
    assert problem == "min_fraction must be a number above 0 and at most 1"


@pytest.mark.parametrize("value", [[], "GOV", [""], [3]])
def test_domains_must_be_a_list_of_codes(value):
    problem = journey_service.check_param_problem({**GATE, "domains": value})
    assert problem and problem.startswith("domains must")


@pytest.mark.asyncio
async def test_a_refused_parameter_reads_as_unknown_not_met():
    out = await journey_service._evaluate_one(None, _uuid.uuid4(), {**GATE, "statuses": ["done"]}, {})
    assert out["met"] is None and out["detail"].startswith("Cannot evaluate:")


# ---------------------------------------------------------------------------
# Resolution and request shapes: structural, no database
# ---------------------------------------------------------------------------


def test_weighting_ten_is_material_unless_the_control_says_otherwise():
    assert materiality.resolve_material(None, 10) == (True, "weighting")
    assert materiality.resolve_material(None, 9) == (False, "weighting")
    assert materiality.resolve_material(None, None) == (False, "weighting")
    assert materiality.resolve_material(False, 10) == (False, "control")
    assert materiality.resolve_material(True, 1) == (True, "control")


def test_an_override_needs_a_reason():
    assert ControlMaterialityRequest(material=None).material is None
    assert ControlMaterialityRequest(material=False, reason="Compensated by design").material is False
    for bad in ({"material": True}, {"material": False, "reason": "  "}):
        with pytest.raises(ValidationError):
            ControlMaterialityRequest(**bad)


@pytest.mark.parametrize("material", [True, False])
def test_catalogue_migration_carries_materiality_and_the_procedure_to_the_successor(material):
    from unittest.mock import MagicMock

    from services import reconciliation_service

    procedure = _uuid.uuid4()

    def migrate(successor):
        scoped = SimpleNamespace(
            selected=True, material_override=material, material_reason="Holds card data",
            material_set_at=None, material_set_by=None, procedure_document_id=procedure,
        )
        by_id = {"ctl-old": scoped}
        if successor is not None:
            by_id["ctl-new"] = successor
        action = SimpleNamespace(
            action=reconciliation_service.PlannedActionType.MIGRATE,
            key="ctl-old", successor_scf_id="ctl-new", justification=None,
        )
        reconciliation_service._execute_control_action(
            MagicMock(), _uuid.uuid4(), action, by_id, "v-next", None,
            SimpleNamespace(migrated=0),
        )
        return scoped, by_id["ctl-new"]

    old, new = migrate(None)
    assert old.selected is False
    assert (new.material_override, new.material_reason) == (
        material, "Carried from ctl-old: Holds card data",
    )
    assert new.procedure_document_id == procedure
    # A successor with its own decision and procedure keeps them.
    own = _uuid.uuid4()
    _, kept = migrate(SimpleNamespace(
        selected=True, material_override=not material, material_reason="Own",
        procedure_document_id=own,
    ))
    assert (kept.material_override, kept.material_reason, kept.procedure_document_id) == (
        not material, "Own", own,
    )


# ---------------------------------------------------------------------------
# Database-backed: the real endpoints and gates on a rolled-back transaction
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
        if not await session.scalar(select(func.count(SCFCatalogControl.scf_id))):
            pytest.skip("SCF catalogue not seeded")
        yield session
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


@pytest.fixture
def no_commit(db, monkeypatch):
    """Endpoints commit; inside the rolled-back test transaction that is a flush."""
    monkeypatch.setattr(db, "commit", db.flush)


async def _weighted(db, heavy, n, prefix=None):
    """``n`` active controls the catalogue weights 10 (heavy) or 1 to 9 (light)."""
    weight = SCFCatalogControl.control_weighting
    query = select(SCFCatalogControl.scf_id).where(
        SCFCatalogControl.status == "active",
        weight >= materiality.MATERIAL_WEIGHTING if heavy else weight.between(1, materiality.MATERIAL_WEIGHTING - 1),
    )
    if prefix:
        query = query.where(SCFCatalogControl.scf_id.like(f"{prefix}-%"))
    ids = list((await db.execute(query.order_by(SCFCatalogControl.scf_id).limit(n))).scalars().all())
    if len(ids) < n:  # pragma: no cover - environment dependent
        pytest.skip(f"catalogue has fewer than {n} {'heavy' if heavy else 'light'} controls")
    return ids


async def _weight_of(db, scf_id):
    return await db.scalar(
        select(SCFCatalogControl.control_weighting).where(SCFCatalogControl.scf_id == scf_id)
    )


async def _framework_of(db, scf_id):
    mappings = await db.scalar(
        select(SCFCatalogControl.framework_mappings).where(SCFCatalogControl.scf_id == scf_id)
    )
    keys = sorted(k for k in (mappings or {}) if not k.startswith(INTERNAL_MAPPING_PREFIXES))
    if not keys:  # pragma: no cover - environment dependent
        pytest.skip(f"{scf_id} maps no framework")
    return keys[0]


async def _org(db):
    tag = _uuid.uuid4().hex[:10]
    org = Organization(name=f"pig-{tag}", slug=f"pig-{tag}")
    db.add(org)
    await db.flush()
    return org


async def _user(db, org=None, role="admin"):
    tag = _uuid.uuid4().hex[:10]
    user = User(google_sub=f"pig-{tag}", email=f"pig-{tag}@example.invalid")
    db.add(user)
    await db.flush()
    if org is not None:
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=role))
        await db.flush()
    return user


def _membership(org, user, role):
    return OrgMembership(
        user=SimpleNamespace(db_id=str(user.id), email=user.email),
        organization_id=org.id,
        role=role,
    )


def _request():
    return Request({"type": "http", "headers": [], "method": "PUT", "path": "/"})


async def _scope(db, org, scf_id, **kwargs):
    kwargs.setdefault("selected", True)
    row = ScopedControl(organization_id=org.id, scf_id=scf_id, **kwargs)
    db.add(row)
    await db.flush()
    return row


async def _row(db, org, scf_id):
    return await db.scalar(select(ScopedControl).where(
        ScopedControl.organization_id == org.id, ScopedControl.scf_id == scf_id))


async def _document(db, org, document_type="procedure", lifecycle_status="approved"):
    doc = GeneratedDocument(
        organization_id=org.id, generator_name="test", document_type=document_type,
        title="Operating procedure", filename="procedure.md", lifecycle_status=lifecycle_status,
    )
    db.add(doc)
    await db.flush()
    return doc


async def _gate(db, org, check):
    totals = await journey_service._in_scope_counts(db, org.id)
    return await journey_service._evaluate_one(db, org.id, check, totals)


async def _audited(db, org, entity_id):
    rows = (await db.execute(select(AuditLog.field_name, AuditLog.old_value, AuditLog.new_value).where(
        AuditLog.organization_id == org.id,
        AuditLog.entity_id == entity_id,
    ))).all()
    return {r.field_name: (r.old_value, r.new_value) for r in rows}


def _list(db, org, user, **kwargs):
    params = dict(
        scope_status="all", domain=None, csf_function=None, framework=None,
        control_weighting=None, search=None, team_id=None, my_teams=False,
        function_id=None, accountable_owner_type=None, basis=None, material=None,
        sort="scf_id", limit=200, offset=0,
    )
    params.update(kwargs)
    return scoped_api.list_scoped_controls_paginated(
        org.id, membership=_membership(org, user, "viewer"), db=db, **params
    )


@requires_postgres
@pytest.mark.asyncio
class TestMaterialityOverride:

    async def test_an_override_is_set_audited_and_cleared(self, db, no_commit):
        org = await _org(db)
        admin = await _user(db, org, "admin")
        (heavy,) = await _weighted(db, True, 1)
        row = await _scope(db, org, heavy)

        out = await scoped_api.put_control_materiality(
            org.id, heavy, ControlMaterialityRequest(material=False, reason="Compensated by design"),
            _request(), _membership(org, admin, "admin"), db,
        )
        assert (out.material_override, out.material_reason) == (False, "Compensated by design")
        assert out.material_set_by == admin.id and out.material_set_at is not None
        assert await materiality.is_material(db, org.id, heavy) is False
        audited = await _audited(db, org, row.id)
        assert audited["material_override"][1] == "false"
        assert audited["material_reason"][1] == '"Compensated by design"'

        cleared = await scoped_api.put_control_materiality(
            org.id, heavy, ControlMaterialityRequest(material=None),
            _request(), _membership(org, admin, "admin"), db,
        )
        assert cleared.material_override is None and cleared.material_reason is None
        assert cleared.material_set_by is None and cleared.material_set_at is None
        assert await materiality.is_material(db, org.id, heavy) is True

    async def test_clearing_a_material_override_on_a_light_control_needs_a_reason(self, db, no_commit):
        org = await _org(db)
        admin = await _user(db, org, "admin")
        (light,) = await _weighted(db, False, 1)
        await _scope(db, org, light, material_override=True, material_reason="Holds card data")
        with pytest.raises(HTTPException) as exc:
            await scoped_api.put_control_materiality(
                org.id, light, ControlMaterialityRequest(material=None),
                _request(), _membership(org, admin, "admin"), db,
            )
        assert exc.value.status_code == 400 and "reason" in exc.value.detail
        assert await materiality.is_material(db, org.id, light) is True

        out = await scoped_api.put_control_materiality(
            org.id, light, ControlMaterialityRequest(material=None, reason="Card data moved out"),
            _request(), _membership(org, admin, "admin"), db,
        )
        # The override is gone; why, and who decided, stay on the row.
        assert out.material_override is None
        assert (out.material_reason, out.material_set_by) == ("Card data moved out", admin.id)
        assert await materiality.is_material(db, org.id, light) is False

    async def test_a_platform_key_cannot_clear_an_override_either(self, db, no_commit):
        org = await _org(db)
        (heavy,) = await _weighted(db, True, 1)
        await _scope(db, org, heavy, material_override=False, material_reason="Compensated")
        keyed = OrgMembership(user=SimpleNamespace(db_id=None), organization_id=org.id, role="admin")
        with pytest.raises(HTTPException) as exc:
            await scoped_api.put_control_materiality(
                org.id, heavy, ControlMaterialityRequest(material=None), _request(), keyed, db,
            )
        assert exc.value.status_code == 403
        assert (await _row(db, org, heavy)).material_override is False

    async def test_another_organisation_cannot_override_the_control(self, db, no_commit):
        org, other = await _org(db), await _org(db)
        (heavy,) = await _weighted(db, True, 1)
        await _scope(db, org, heavy)
        with pytest.raises(HTTPException) as exc:
            await scoped_api.put_control_materiality(
                other.id, heavy, ControlMaterialityRequest(material=False, reason="x"),
                _request(), _membership(other, await _user(db, other), "admin"), db,
            )
        assert exc.value.status_code == 404
        assert (await _row(db, org, heavy)).material_override is None

    async def test_a_platform_key_with_no_user_cannot_be_the_approver(self, db, no_commit):
        org = await _org(db)
        (heavy,) = await _weighted(db, True, 1)
        await _scope(db, org, heavy)
        keyed = OrgMembership(user=SimpleNamespace(db_id=None), organization_id=org.id, role="admin")
        with pytest.raises(HTTPException) as exc:
            await scoped_api.put_control_materiality(
                org.id, heavy, ControlMaterialityRequest(material=False, reason="x"),
                _request(), keyed, db,
            )
        assert exc.value.status_code == 403
        assert (await _row(db, org, heavy)).material_override is None

    async def test_the_service_refuses_an_override_without_a_reason(self, db):
        org = await _org(db)
        (heavy,) = await _weighted(db, True, 1)
        await _scope(db, org, heavy)
        with pytest.raises(ValueError):
            await materiality.set_material_override(db, org.id, heavy, False, "  ", None)

    @pytest.mark.parametrize("fields", [
        {"material_override": False},
        {"material_override": True, "material_reason": " "},
    ])
    async def test_the_database_refuses_what_the_api_would(self, db, fields):
        org = await _org(db)
        (heavy,) = await _weighted(db, True, 1)
        with pytest.raises(IntegrityError):
            async with db.begin_nested():
                await _scope(db, org, heavy, **fields)

    async def test_a_control_with_no_scope_record_follows_its_weighting(self, db):
        org = await _org(db)
        (heavy,) = await _weighted(db, True, 1)
        (light,) = await _weighted(db, False, 1)
        assert await materiality.is_material(db, org.id, heavy) is True
        assert await materiality.is_material(db, org.id, light) is False

    async def test_sql_and_python_agree(self, db):
        org = await _org(db)
        heavy = await _weighted(db, True, 2)
        light = await _weighted(db, False, 2)
        await _scope(db, org, heavy[0])
        await _scope(db, org, heavy[1], material_override=False, material_reason="x")
        await _scope(db, org, light[0])
        await _scope(db, org, light[1], material_override=True, material_reason="x")
        rows = (await db.execute(
            select(ScopedControl.scf_id, ScopedControl.material_override, materiality.material_expr())
            .where(ScopedControl.organization_id == org.id)
        )).all()
        assert len(rows) == 4
        for scf_id, override, sql_value in rows:
            assert sql_value == materiality.resolve_material(override, await _weight_of(db, scf_id))[0]
        assert {r[0]: r[2] for r in rows} == {
            heavy[0]: True, heavy[1]: False, light[0]: False, light[1]: True,
        }


@requires_postgres
@pytest.mark.asyncio
class TestExclusionGuard:

    async def test_every_other_write_path_refuses_to_deselect_a_material_control(self, db, no_commit):
        org = await _org(db)
        (heavy,) = await _weighted(db, True, 1)
        (light,) = await _weighted(db, False, 1)
        await _scope(db, org, heavy)
        await _scope(db, org, light)
        editor = await _user(db, org, "editor")
        for m in (_membership(org, editor, "editor"), _membership(org, await _user(db, org), "admin")):
            with pytest.raises(HTTPException) as exc:
                await scoped_api.update_scoped_control(
                    org.id, heavy, ScopedControlUpdate(selected=False), _request(), m, db
                )
            assert exc.value.status_code == 409 and "material" in exc.value.detail
            with pytest.raises(HTTPException) as exc:
                await scoped_api.create_or_update_scoped_control(
                    org.id, ScopedControlCreate(scf_id=heavy, selected=False), _request(), m, db
                )
            assert exc.value.status_code == 409
            with pytest.raises(HTTPException) as exc:
                await scoped_api.delete_scoped_control(org.id, heavy, _request(), m, db)
            assert exc.value.status_code == 409

        m = _membership(org, editor, "editor")
        out = await scoped_api.batch_update_scoped_controls(
            org.id,
            BatchScopedControlRequest(operations=[
                BatchScopedControlOperation(scf_id=heavy, selected=False),
                BatchScopedControlOperation(scf_id=light, selected=False),
            ]),
            _request(), m, db,
        )
        assert (out.updated, out.failed) == (1, 1)
        assert out.errors and out.errors[0].startswith(f"{heavy}:") and "material" in out.errors[0]
        assert (await _row(db, org, heavy)).selected is True
        assert (await _row(db, org, light)).selected is False

    async def test_recording_a_material_control_out_of_scope_first_time_is_refused(self, db, no_commit):
        org = await _org(db)
        (heavy,) = await _weighted(db, True, 1)
        editor = await _user(db, org, "editor")
        with pytest.raises(HTTPException) as exc:
            await scoped_api.create_or_update_scoped_control(
                org.id, ScopedControlCreate(scf_id=heavy, selected=False), _request(),
                _membership(org, editor, "editor"), db,
            )
        assert exc.value.status_code == 409
        assert await _row(db, org, heavy) is None

    async def test_an_override_decides_which_controls_are_guarded(self, db, no_commit):
        org = await _org(db)
        (heavy,) = await _weighted(db, True, 1)
        (light,) = await _weighted(db, False, 1)
        await _scope(db, org, heavy, material_override=False, material_reason="Compensated")
        await _scope(db, org, light, material_override=True, material_reason="Holds card data")
        m = _membership(org, await _user(db, org, "editor"), "editor")
        out = await scoped_api.update_scoped_control(
            org.id, heavy, ScopedControlUpdate(selected=False), _request(), m, db
        )
        assert out.selected is False
        with pytest.raises(HTTPException) as exc:
            await scoped_api.update_scoped_control(
                org.id, light, ScopedControlUpdate(selected=False), _request(), m, db
            )
        assert exc.value.status_code == 409

    async def test_not_applicable_on_a_material_control_needs_an_admin(self, db, no_commit):
        org = await _org(db)
        heavy = await _weighted(db, True, 2)
        (light,) = await _weighted(db, False, 1)
        for scf_id in (*heavy, light):
            await _scope(db, org, scf_id, implementation_status="not_started")
        editor = _membership(org, await _user(db, org, "editor"), "editor")
        admin_user = await _user(db, org, "admin")
        admin = _membership(org, admin_user, "admin")

        with pytest.raises(HTTPException) as exc:
            await scoped_api.update_scoped_control(
                org.id, heavy[0], ScopedControlUpdate(implementation_status="not_applicable"),
                _request(), editor, db,
            )
        assert exc.value.status_code == 403 and "material" in exc.value.detail
        with pytest.raises(HTTPException) as exc:
            await scoped_api.create_or_update_scoped_control(
                org.id, ScopedControlCreate(scf_id=heavy[0], implementation_status="not_applicable"),
                _request(), editor, db,
            )
        assert exc.value.status_code == 403
        out = await scoped_api.batch_update_scoped_controls(
            org.id,
            BatchScopedControlRequest(operations=[
                BatchScopedControlOperation(scf_id=heavy[1], implementation_status="not_applicable"),
                BatchScopedControlOperation(scf_id=light, implementation_status="not_applicable"),
            ]),
            _request(), editor, db,
        )
        assert (out.updated, out.failed) == (1, 1)
        assert (await _row(db, org, heavy[1])).implementation_status == "not_started"
        assert (await _row(db, org, light)).implementation_status == "not_applicable"

        # The admin's own write is the approval, and the audit log names them.
        row = await _row(db, org, heavy[0])
        done = await scoped_api.update_scoped_control(
            org.id, heavy[0], ScopedControlUpdate(implementation_status="not_applicable"),
            _request(), admin, db,
        )
        assert done.implementation_status == "not_applicable"
        logged = (await db.execute(select(AuditLog.changed_by_user_id).where(
            AuditLog.organization_id == org.id,
            AuditLog.entity_id == row.id,
            AuditLog.field_name == "implementation_status",
        ))).scalars().all()
        assert logged == [admin_user.id]

        # A control already not applicable is not being marked so again.
        again = await scoped_api.update_scoped_control(
            org.id, heavy[0], ScopedControlUpdate(implementation_status="not_applicable", priority="low"),
            _request(), editor, db,
        )
        assert again.implementation_status == "not_applicable"

    async def test_writes_that_leave_scope_and_status_alone_pass(self, db, no_commit):
        org = await _org(db)
        (heavy,) = await _weighted(db, True, 1)
        await _scope(db, org, heavy)
        editor = _membership(org, await _user(db, org, "editor"), "editor")
        out = await scoped_api.update_scoped_control(
            org.id, heavy, ScopedControlUpdate(implementation_status="in_progress"), _request(), editor, db,
        )
        assert out.selected is True and out.implementation_status == "in_progress"

    async def test_an_editor_cannot_exclude_a_material_control_but_an_admin_can(self, db, no_commit):
        org = await _org(db)
        (heavy,) = await _weighted(db, True, 1)
        await _scope(db, org, heavy)
        editor = await _user(db, org, "editor")
        admin = await _user(db, org, "admin")
        with pytest.raises(HTTPException) as exc:
            await scoped_api.put_scope_override(
                org.id, heavy, ScopeOverrideRequest(action="exclude", reason="Out"),
                _request(), _membership(org, editor, "editor"), db,
            )
        assert exc.value.status_code == 403
        assert (await _row(db, org, heavy)).selected is True

        out = await scoped_api.put_scope_override(
            org.id, heavy, ScopeOverrideRequest(action="exclude", reason="Out"),
            _request(), _membership(org, admin, "admin"), db,
        )
        assert out.selected is False and out.scope_override_approved_by == admin.id
        logged = (await db.execute(select(AuditLog.new_value).where(
            AuditLog.organization_id == org.id,
            AuditLog.scf_id == heavy,
            AuditLog.field_name == "scope_override_approved_by",
        ))).scalars().all()
        assert [json.loads(v) for v in logged] == [str(admin.id)]

    async def test_an_editor_may_still_exclude_a_control_that_is_not_material(self, db, no_commit):
        org = await _org(db)
        (light,) = await _weighted(db, False, 1)
        await _scope(db, org, light)
        editor = await _user(db, org, "editor")
        out = await scoped_api.put_scope_override(
            org.id, light, ScopeOverrideRequest(action="exclude", reason="Out"),
            _request(), _membership(org, editor, "editor"), db,
        )
        assert out.selected is False and out.scope_override_approved_by is None

    async def test_inherit_cannot_drop_a_material_control(self, db):
        org = await _org(db)
        (heavy,) = await _weighted(db, True, 1)
        (light,) = await _weighted(db, False, 1)
        await _scope(db, org, heavy)
        await _scope(db, org, light)
        editor = await _user(db, org, "editor")
        with pytest.raises(ValueError, match="material"):
            await set_individual_scope_override(db, org.id, heavy, "inherit", None, editor.id, commit=False)
        assert (await _row(db, org, heavy)).selected is True
        # No framework keeps either in scope; the light one simply follows that.
        row = await set_individual_scope_override(db, org.id, light, "inherit", None, editor.id, commit=False)
        assert row.selected is False

    async def test_removing_a_framework_that_maps_a_material_control_needs_an_admin_and_a_reason(
        self, db, no_commit
    ):
        org = await _org(db)
        (heavy,) = await _weighted(db, True, 1)
        (light,) = await _weighted(db, False, 1)
        fw = await _framework_of(db, heavy)
        fw_light = await _framework_of(db, light)
        await _scope(db, org, heavy)
        editor = await _user(db, org, "editor")
        admin = await _user(db, org, "admin")
        assert await materiality.unscope_touches_material(db, org.id, [fw]) is True

        with pytest.raises(HTTPException) as exc:
            await scoped_api.bulk_unscope_by_framework(
                org.id, BulkUnscopeFrameworkRequest(frameworks=[fw], removal_reason="Done"),
                _membership(org, editor, "editor"), db,
            )
        assert exc.value.status_code == 403 and "material" in exc.value.detail
        with pytest.raises(HTTPException) as exc:
            await scoped_api.bulk_unscope_by_framework(
                org.id, BulkUnscopeFrameworkRequest(frameworks=[fw]),
                _membership(org, admin, "admin"), db,
            )
        assert exc.value.status_code == 400
        assert (await _row(db, org, heavy)).selected is True

        # Another organisation's material control is not this one's concern,
        # and a light control counts only once it is overridden to material.
        other = await _org(db)
        await _scope(db, other, light)
        assert await materiality.unscope_touches_material(db, other.id, [fw]) is False
        assert await materiality.unscope_touches_material(db, other.id, [fw_light]) is False
        row = await _row(db, other, light)
        row.material_override, row.material_reason = True, "Holds card data"
        await db.flush()
        assert await materiality.unscope_touches_material(db, other.id, [fw_light]) is True
        assert await materiality.unscope_touches_material(db, other.id, []) is False

    async def test_a_material_control_another_framework_keeps_is_not_at_stake(self, db, no_commit):
        heavy, fw, fw_other = None, None, None
        rows = (await db.execute(
            select(SCFCatalogControl.scf_id, SCFCatalogControl.framework_mappings)
            .where(
                SCFCatalogControl.status == "active",
                SCFCatalogControl.control_weighting >= materiality.MATERIAL_WEIGHTING,
            )
            .order_by(SCFCatalogControl.scf_id)
        )).all()
        for scf_id, mappings in rows:
            keys = sorted(k for k in (mappings or {}) if not k.startswith(INTERNAL_MAPPING_PREFIXES))
            if len(keys) >= 2:
                heavy, fw, fw_other = scf_id, keys[0], keys[1]
                break
        if heavy is None:  # pragma: no cover - environment dependent
            pytest.skip("no material control maps two frameworks")
        org = await _org(db)
        await _scope(db, org, heavy)
        for framework in (fw, fw_other):
            db.add(OrganizationFrameworkSelection(
                organization_id=org.id, framework_id=framework, source="bulk_scope", active=True,
            ))
        await db.flush()
        # Removing one framework leaves the control in scope through the other.
        assert await materiality.unscope_touches_material(db, org.id, [fw]) is False
        # Removing both takes it out.
        assert await materiality.unscope_touches_material(db, org.id, [fw, fw_other]) is True
        # An individual inclusion keeps it in scope whatever is removed.
        row = await _row(db, org, heavy)
        row.scope_override, row.scope_override_reason = "include", "Kept"
        await db.flush()
        assert await materiality.unscope_touches_material(db, org.id, [fw, fw_other]) is False
        # And an editor's removal then goes through.
        editor = await _user(db, org, "editor")
        out = await scoped_api.bulk_unscope_by_framework(
            org.id, BulkUnscopeFrameworkRequest(frameworks=[fw]), _membership(org, editor, "editor"), db,
        )
        assert out.removed == 0 and (await _row(db, org, heavy)).selected is True


@requires_postgres
@pytest.mark.asyncio
class TestProcedureLink:

    async def test_an_approved_procedure_is_linked_audited_and_unlinked(self, db, no_commit):
        org = await _org(db)
        editor = await _user(db, org, "editor")
        (scf_id,) = await _weighted(db, True, 1)
        row = await _scope(db, org, scf_id)
        doc = await _document(db, org)

        out = await scoped_api.put_control_procedure_document(
            org.id, scf_id, ControlProcedureDocumentRequest(document_id=doc.id),
            _request(), _membership(org, editor, "editor"), db,
        )
        assert out.procedure_document_id == doc.id
        assert (await _audited(db, org, row.id))["procedure_document_id"][1] == json.dumps(str(doc.id))

        page = ScopedControlsPaginatedResponse.model_validate(await _list(db, org, editor))
        item = next(c for c in page.controls if c.scf_id == scf_id)
        assert (item.procedure_document_id, item.procedure_document_status) == (doc.id, "approved")

        # Edited after linking, it loses its approval; the link stays and the
        # list shows the drift.
        doc.lifecycle_status = "draft"
        await db.flush()
        page = ScopedControlsPaginatedResponse.model_validate(await _list(db, org, editor))
        item = next(c for c in page.controls if c.scf_id == scf_id)
        assert (item.procedure_document_id, item.procedure_document_status) == (doc.id, "draft")

        out = await scoped_api.put_control_procedure_document(
            org.id, scf_id, ControlProcedureDocumentRequest(document_id=None),
            _request(), _membership(org, editor, "editor"), db,
        )
        assert out.procedure_document_id is None

    async def test_a_published_procedure_is_signed_off_too(self, db, no_commit):
        org = await _org(db)
        editor = await _user(db, org, "editor")
        (scf_id,) = await _weighted(db, True, 1)
        await _scope(db, org, scf_id)
        doc = await _document(db, org, lifecycle_status="published")
        out = await scoped_api.put_control_procedure_document(
            org.id, scf_id, ControlProcedureDocumentRequest(document_id=doc.id),
            _request(), _membership(org, editor, "editor"), db,
        )
        assert out.procedure_document_id == doc.id

    @pytest.mark.parametrize("document_type,status,fragment", [
        ("policy", "approved", "Only a procedure"),
        ("procedure", "draft", "Only an approved or published procedure"),
        ("procedure", "in_review", "Only an approved or published procedure"),
    ])
    async def test_only_an_approved_procedure_can_be_linked(self, db, no_commit, document_type, status, fragment):
        org = await _org(db)
        editor = await _user(db, org, "editor")
        (scf_id,) = await _weighted(db, True, 1)
        await _scope(db, org, scf_id)
        doc = await _document(db, org, document_type, status)
        with pytest.raises(HTTPException) as exc:
            await scoped_api.put_control_procedure_document(
                org.id, scf_id, ControlProcedureDocumentRequest(document_id=doc.id),
                _request(), _membership(org, editor, "editor"), db,
            )
        assert exc.value.status_code == 400 and fragment in exc.value.detail
        assert (await _row(db, org, scf_id)).procedure_document_id is None

    async def test_another_organisations_document_cannot_be_linked(self, db, no_commit):
        org, other = await _org(db), await _org(db)
        editor = await _user(db, org, "editor")
        (scf_id,) = await _weighted(db, True, 1)
        await _scope(db, org, scf_id)
        theirs = await _document(db, other)
        with pytest.raises(HTTPException) as exc:
            await scoped_api.put_control_procedure_document(
                org.id, scf_id, ControlProcedureDocumentRequest(document_id=theirs.id),
                _request(), _membership(org, editor, "editor"), db,
            )
        assert exc.value.status_code == 404
        assert (await _row(db, org, scf_id)).procedure_document_id is None

    async def test_a_control_with_no_scope_record_cannot_be_linked(self, db, no_commit):
        org = await _org(db)
        (scf_id,) = await _weighted(db, True, 1)
        doc = await _document(db, org)
        with pytest.raises(HTTPException) as exc:
            await scoped_api.put_control_procedure_document(
                org.id, scf_id, ControlProcedureDocumentRequest(document_id=doc.id),
                _request(), _membership(org, await _user(db, org, "editor"), "editor"), db,
            )
        assert exc.value.status_code == 404


@requires_postgres
@pytest.mark.asyncio
class TestListingAndQueues:

    async def test_the_scoping_list_carries_materiality_and_filters_on_it(self, db):
        org = await _org(db)
        user = await _user(db, org, "viewer")
        heavy = await _weighted(db, True, 2)
        (light,) = await _weighted(db, False, 1)
        await _scope(db, org, heavy[0])
        await _scope(db, org, heavy[1], material_override=False, material_reason="Compensated")
        await _scope(db, org, light)

        page = ScopedControlsPaginatedResponse.model_validate(
            await _list(db, org, user, scope_status="in_scope")
        )
        got = {c.scf_id: (c.material, c.material_source, c.material_reason) for c in page.controls}
        assert got == {
            heavy[0]: (True, "weighting", None),
            heavy[1]: (False, "control", "Compensated"),
            light: (False, "weighting", None),
        }
        only = ScopedControlsPaginatedResponse.model_validate(
            await _list(db, org, user, scope_status="in_scope", material=True)
        )
        assert [c.scf_id for c in only.controls] == [heavy[0]] and only.total == 1
        rest = ScopedControlsPaginatedResponse.model_validate(
            await _list(db, org, user, scope_status="in_scope", material=False)
        )
        assert {c.scf_id for c in rest.controls} == {heavy[1], light} and rest.total == 2

    async def test_the_scoping_list_sorts_by_weighting_when_asked(self, db):
        org = await _org(db)
        user = await _user(db, org, "viewer")
        (heavy,) = await _weighted(db, True, 1)
        light = (await _weighted(db, False, 9))
        await _scope(db, org, heavy)
        for scf_id in light:
            await _scope(db, org, scf_id)

        by_weight = ScopedControlsPaginatedResponse.model_validate(
            await _list(db, org, user, scope_status="in_scope", sort="weighting")
        )
        weights = [c.control_weighting for c in by_weight.controls]
        assert weights == sorted(weights, reverse=True) and weights[0] >= materiality.MATERIAL_WEIGHTING
        # Material first: an override lifts the lightest control to second.
        lightest = min(light, key=lambda v: next(c.control_weighting for c in by_weight.controls if c.scf_id == v))
        row = await _row(db, org, lightest)
        row.material_override, row.material_reason = True, "Holds card data"
        await db.flush()
        by_weight = ScopedControlsPaginatedResponse.model_validate(
            await _list(db, org, user, scope_status="in_scope", sort="weighting")
        )
        assert [c.scf_id for c in by_weight.controls][:2] == [heavy, lightest]
        by_id = ScopedControlsPaginatedResponse.model_validate(
            await _list(db, org, user, scope_status="in_scope")
        )
        assert [c.scf_id for c in by_id.controls] == sorted(c.scf_id for c in by_id.controls)

    async def test_the_work_queue_puts_the_heaviest_blocking_controls_first(self, db):
        org = await _org(db)
        user = await _user(db, org, "viewer")
        (heavy,) = await _weighted(db, True, 1)
        # Two light controls of different weights; the lighter is the stalest,
        # so staleness alone would put it ahead of the heavier one.
        weight = SCFCatalogControl.control_weighting
        lighter = await db.scalar(
            select(SCFCatalogControl.scf_id)
            .where(SCFCatalogControl.status == "active", weight.between(1, materiality.MATERIAL_WEIGHTING - 2))
            .order_by(weight, SCFCatalogControl.scf_id).limit(1)
        )
        heavier = lighter and await db.scalar(
            select(SCFCatalogControl.scf_id)
            .where(
                SCFCatalogControl.status == "active",
                weight > select(weight).where(SCFCatalogControl.scf_id == lighter).scalar_subquery(),
                weight < materiality.MATERIAL_WEIGHTING,
            )
            .order_by(weight.desc(), SCFCatalogControl.scf_id).limit(1)
        )
        if not heavier:  # pragma: no cover - environment dependent
            pytest.skip("catalogue has no two light controls of different weights")
        light = [heavier, lighter]
        await _scope(db, org, lighter, implementation_status="not_started")
        await db.execute(
            ScopedControl.__table__.update()
            .where(ScopedControl.organization_id == org.id)
            .values(updated_at=func.now() - func.make_interval(0, 0, 0, 30))
        )
        await _scope(db, org, heavier, implementation_status="not_started")
        await db.execute(
            ScopedControl.__table__.update()
            .where(ScopedControl.organization_id == org.id, ScopedControl.scf_id == heavier)
            .values(updated_at=func.now() - func.make_interval(0, 0, 0, 20))
        )
        await _scope(db, org, heavy, implementation_status="at_risk")

        queue = await dashboard_api.get_work_queue(
            org.id, assigned_to_me=False, membership=_membership(org, user, "viewer"), db=db,
        )
        got = [(b.scf_id, b.material) for b in queue.blocking_controls]
        assert got[0] == (heavy, True)
        assert [s for s, _ in got[1:]] == light and not any(m for _, m in got[1:])
        weights = [b.weighting for b in queue.blocking_controls]
        assert weights == sorted(weights, reverse=True) and weights[1] > weights[2]

        # A control made material by override comes ahead of heavier ones that
        # are not.
        row = await _row(db, org, light[1])
        row.material_override, row.material_reason = True, "Holds card data"
        await db.flush()
        queue = await dashboard_api.get_work_queue(
            org.id, assigned_to_me=False, membership=_membership(org, user, "viewer"), db=db,
        )
        assert [b.scf_id for b in queue.blocking_controls][:2] == [heavy, light[1]]
        assert all(b.material for b in queue.blocking_controls[:2])
        assert not queue.blocking_controls[2].material


@requires_postgres
@pytest.mark.asyncio
class TestMaterialGate:

    async def test_no_material_control_in_scope_does_not_pass(self, db):
        org = await _org(db)
        out = await _gate(db, org, GATE)
        assert out["met"] is False and out["detail"] == "No material controls in scope"
        (light,) = await _weighted(db, False, 1)
        await _scope(db, org, light, implementation_status="implemented")
        assert (await _gate(db, org, GATE))["detail"] == "No material controls in scope"

    async def test_it_counts_material_in_scope_controls_at_the_statuses(self, db):
        org = await _org(db)
        heavy = await _weighted(db, True, 4)
        (light,) = await _weighted(db, False, 1)
        await _scope(db, org, heavy[0], implementation_status="implemented")
        await _scope(db, org, heavy[1], implementation_status="not_started")
        await _scope(db, org, heavy[2], implementation_status="monitored", selected=False)
        await _scope(db, org, heavy[3], implementation_status="not_started",
                     material_override=False, material_reason="Compensated")
        await _scope(db, org, light, implementation_status="not_started")
        out = await _gate(db, org, GATE)
        assert out["met"] is False
        assert out["detail"] == (
            "1 of 2 material controls at implemented or monitored (50.0%) — 2 required, 1 more needed"
        )
        assert (await _gate(db, org, {**GATE, "min_fraction": 0.5}))["met"] is True

        # A light control overridden to material joins the count.
        row = await _row(db, org, light)
        row.material_override, row.material_reason = True, "Holds card data"
        await db.flush()
        assert (await _gate(db, org, GATE))["detail"].startswith("1 of 3 material controls")

        row = await _row(db, org, heavy[1])
        row.implementation_status = "monitored"
        row = await _row(db, org, light)
        row.implementation_status = "implemented"
        await db.flush()
        assert (await _gate(db, org, GATE))["met"] is True

    async def test_the_domain_filter_narrows_both_halves(self, db):
        (first,) = await _weighted(db, True, 1)
        prefix = first.split("-", 1)[0]
        rows = (await db.execute(
            select(SCFCatalogControl.scf_id).where(
                SCFCatalogControl.status == "active",
                SCFCatalogControl.control_weighting >= materiality.MATERIAL_WEIGHTING,
                ~SCFCatalogControl.scf_id.like(f"{prefix}-%"),
            ).order_by(SCFCatalogControl.scf_id).limit(1)
        )).scalars().all()
        if not rows:  # pragma: no cover - environment dependent
            pytest.skip("material controls in only one domain")
        org = await _org(db)
        await _scope(db, org, first, implementation_status="implemented")
        await _scope(db, org, rows[0], implementation_status="not_started")
        assert (await _gate(db, org, GATE))["met"] is False
        only = await _gate(db, org, {**GATE, "domains": [prefix]})
        assert only["met"] is True and only["detail"].startswith(f"1 of 1 material control in {prefix} at")
        other_prefix = rows[0].split("-", 1)[0]
        assert (await _gate(db, org, {**GATE, "domains": [other_prefix]}))["met"] is False

    async def test_a_named_material_control_nobody_scoped_is_a_shortfall(self, db):
        org = await _org(db)
        heavy = await _weighted(db, True, 2)
        (light,) = await _weighted(db, False, 1)
        await _scope(db, org, heavy[0], implementation_status="implemented")
        out = await _gate(db, org, {**GATE, "scf_ids": [heavy[0], heavy[1].lower(), light]})
        assert out["met"] is False
        assert out["detail"].startswith("1 of 2 material controls across 3 named controls")
        # The light named control is not material and not counted; an excluded
        # material one leaves the count with its recorded reason.
        await _scope(db, org, heavy[1], selected=False, scope_override="exclude",
                     scope_override_reason="Out")
        out = await _gate(db, org, {**GATE, "scf_ids": [heavy[0], heavy[1], light]})
        assert out["met"] is True and "(1 excluded)" in out["detail"]

    async def test_a_named_control_follows_its_override_and_must_be_in_scope(self, db):
        org = await _org(db)
        heavy = await _weighted(db, True, 2)
        light = await _weighted(db, False, 2)
        await _scope(db, org, heavy[0], implementation_status="implemented")
        # Overridden out of materiality: named, but not counted.
        await _scope(db, org, heavy[1], implementation_status="not_started",
                     material_override=False, material_reason="Compensated")
        # Overridden into materiality: named and counted, and done.
        await _scope(db, org, light[0], implementation_status="monitored",
                     material_override=True, material_reason="Holds card data")
        out = await _gate(db, org, {**GATE, "scf_ids": [*heavy, light[0]]})
        assert out["met"] is True and out["detail"].startswith("2 of 2 material controls")

        # Recorded out of scope without an exclusion: still counted, and its
        # status does not make it done.
        row = await _row(db, org, light[0])
        row.selected = False
        await db.flush()
        out = await _gate(db, org, {**GATE, "scf_ids": [*heavy, light[0]]})
        assert out["met"] is False and out["detail"].startswith("1 of 2 material controls")

    async def test_another_organisations_override_does_not_make_a_named_control_material(self, db):
        org, other = await _org(db), await _org(db)
        (heavy,) = await _weighted(db, True, 1)
        (light,) = await _weighted(db, False, 1)
        await _scope(db, other, light, material_override=True, material_reason="Theirs")
        await _scope(db, org, heavy, implementation_status="implemented")
        out = await _gate(db, org, {**GATE, "scf_ids": [heavy, light]})
        assert out["met"] is True and out["detail"].startswith("1 of 1 material control across 2")

    async def test_a_named_set_with_no_material_control_does_not_pass(self, db):
        org = await _org(db)
        (light,) = await _weighted(db, False, 1)
        await _scope(db, org, light, implementation_status="implemented")
        out = await _gate(db, org, {**GATE, "scf_ids": [light]})
        assert out["met"] is False and out["detail"].startswith("No material controls in scope")

    async def test_an_unknown_domain_fails_closed(self, db):
        org = await _org(db)
        (heavy,) = await _weighted(db, True, 1)
        await _scope(db, org, heavy, implementation_status="implemented")
        assert (await _gate(db, org, {**GATE, "domains": ["NOPE"]}))["met"] is False

    async def test_another_organisations_controls_and_overrides_do_not_count(self, db):
        org, other = await _org(db), await _org(db)
        (heavy,) = await _weighted(db, True, 1)
        (light,) = await _weighted(db, False, 1)
        await _scope(db, other, heavy, implementation_status="implemented")
        await _scope(db, other, light, implementation_status="implemented",
                     material_override=True, material_reason="Theirs")
        assert (await _gate(db, org, GATE))["detail"] == "No material controls in scope"
        await _scope(db, org, light, implementation_status="implemented")
        assert (await _gate(db, org, GATE))["detail"] == "No material controls in scope"
        await _scope(db, org, heavy, implementation_status="not_started")
        assert (await _gate(db, org, GATE))["met"] is False
        assert (await _gate(db, other, GATE))["met"] is True
        out = await _gate(db, org, {**GATE, "scf_ids": [heavy, light]})
        assert out["detail"].startswith("0 of 1 material control across 2 named controls")


@requires_postgres
@pytest.mark.asyncio
@pytest.mark.parametrize("check,fragment", [
    ({**GATE, "domains": ["NOPE"]}, "no SCF domain 'NOPE'"),
    ({**GATE, "statuses": ["done"]}, "statuses must be implementation statuses"),
    ({**GATE, "basis": "mcr"}, "takes no basis"),
])
async def test_the_import_endpoint_refuses_a_gate_it_cannot_honour(db, monkeypatch, check, fragment):
    org = await _org(db)
    monkeypatch.setattr(db, "commit", db.flush)
    payload = journey_api.ImportRequest(template={"stages": [{
        "key": "material", "title": "Material", "precondition_spec": [check],
    }]})
    with pytest.raises(journey_api.HTTPException) as exc:
        await journey_api.import_journey(org.id, _request(), payload, SimpleNamespace(user=None), db)
    assert exc.value.status_code == 422
    assert fragment in str(exc.value.detail)
