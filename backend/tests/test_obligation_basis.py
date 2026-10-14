"""Obligation basis on framework selection: MCR and DSR (#1122, PIG R2).

Why a control is in scope decides what taking it out needs. A minimum
compliance requirement (MCR) is owed to someone; a discretionary security
requirement (DSR) is the organisation's own choice. These tests defend four
properties:

* the basis is derived from classified framework selections, strongest
  wins, a per-control override replaces it, and nothing is guessed;
* the SQL and Python forms of that derivation agree;
* an MCR control leaves scope only with a reason and an approving admin,
  and the approver is the authenticated admin, never an id in the payload;
  every direct write path refuses (including an explicit null), inherit
  cannot drop an MCR override, and removing an MCR framework needs an admin
  and a reason;
* the basis reaches the summary, the list, the preview, the engagement
  snapshot, the audit log and the journey checks.

Framework and control ids are read from the seeded catalogue at run time.
Everything written runs inside a transaction that is rolled back.
"""
import json
import uuid as _uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from api import audit_engagements as engagements_api
from auth import OrgMembership
from api import scoped_controls as scoped_api
from catalog_models import SCFCatalogControl
from models import (
    AuditEngagement,
    AuditLog,
    EngagementControlScope,
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
    ControlBasisOverrideRequest,
    FrameworkBasisRequest,
    ScopedControlCreate,
    ScopedControlsPaginatedResponse,
    ScopedControlUpdate,
    ScopeOverrideRequest,
)
from services import journey as journey_service
from services import obligation_basis as basis_service
from services import reconciliation_service
from services.scoping_service import (
    INTERNAL_MAPPING_PREFIXES,
    framework_scope_summary,
    preview_framework_change,
    set_individual_scope_override,
)

from test_journey_service import DATABASE_URL, requires_postgres


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


# ---------------------------------------------------------------------------
# Derivation and validation — no database
# ---------------------------------------------------------------------------


def test_strongest_classified_selection_decides_the_basis():
    by_fw = {"fw_m": "mcr", "fw_d": "dsr"}
    assert basis_service.derive_basis(["fw_m", "fw_d"], by_fw) == ("mcr", "framework")
    assert basis_service.derive_basis(["fw_d"], by_fw) == ("dsr", "framework")
    assert basis_service.derive_basis(["fw_unclassified"], by_fw) == (None, None)
    assert basis_service.derive_basis([], by_fw) == (None, None)


def test_an_override_replaces_the_derived_basis_either_way():
    by_fw = {"fw_m": "mcr"}
    assert basis_service.derive_basis(["fw_m"], by_fw, "dsr") == ("dsr", "override")
    assert basis_service.derive_basis([], by_fw, "mcr") == ("mcr", "override")


def test_downgrade_order_is_mcr_then_dsr_then_unclassified():
    assert basis_service.rank("mcr") > basis_service.rank("dsr") > basis_service.rank(None)


@pytest.mark.parametrize("check, fragment", [
    ({"type": "frameworks_with_basis"}, "needs a basis"),
    ({"type": "frameworks_with_basis", "basis": "statutory"}, "'mcr' or 'dsr'"),
    ({"type": "controls_with_basis", "basis": "MCR"}, "'mcr' or 'dsr'"),
    ({"type": "controls_with_owner", "basis": "mcr"}, "does not read an obligation basis"),
    ({"type": "frameworks_scoped", "basis": "mcr"}, "does not read an obligation basis"),
])
def test_a_basis_the_engine_cannot_honour_is_named(check, fragment):
    problem = journey_service.check_param_problem(check)
    assert problem is not None and fragment in problem


@pytest.mark.parametrize("check", [
    {"type": "frameworks_with_basis", "basis": "mcr", "min_count": 1},
    {"type": "controls_with_basis", "min_fraction": 1},
    {"type": "controls_with_basis", "basis": "dsr"},
    {"type": "controls_with_basis", "scf_ids": ["ANY-ID"]},
])
def test_a_well_formed_basis_check_has_no_structural_problem(check):
    assert journey_service.check_param_problem(check) is None


def test_both_basis_checks_are_registered_and_controls_with_basis_takes_control_filters():
    assert {"frameworks_with_basis", "controls_with_basis"} <= journey_service.SUPPORTED_CHECKS
    assert "controls_with_basis" in journey_service.CONTROL_FILTERED_CHECKS
    assert "frameworks_with_basis" not in journey_service.CONTROL_FILTERED_CHECKS


@pytest.mark.asyncio
async def test_a_malformed_basis_evaluates_to_unknown_without_touching_the_db():
    db = MagicMock()
    out = await journey_service._evaluate_one(
        db, _uuid.uuid4(), {"type": "frameworks_with_basis", "basis": "maybe"}, {"in_scope": 0}
    )
    assert out["met"] is None
    assert "Cannot evaluate" in out["detail"]
    db.execute.assert_not_called()
    db.scalar.assert_not_called()


def test_request_schemas_refuse_what_the_columns_would():
    with pytest.raises(ValidationError):
        ControlBasisOverrideRequest(basis="mcr", reason="  ")
    with pytest.raises(ValidationError):
        ControlBasisOverrideRequest(basis="must")
    with pytest.raises(ValidationError):
        FrameworkBasisRequest(basis="mcr", obligation_type="moral")
    assert ControlBasisOverrideRequest(basis=None).basis is None
    assert FrameworkBasisRequest().basis is None


# ---------------------------------------------------------------------------
# Behavioural — seeded catalogue, real PostgreSQL. SKIPS without them.
# ---------------------------------------------------------------------------


def test_catalogue_migration_carries_a_basis_override_to_the_successor():
    def migrate(successor):
        scoped = SimpleNamespace(
            selected=True, basis_override="mcr", basis_override_reason="Statute",
            basis_override_set_at=None, basis_override_set_by=None,
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
    assert (new.basis_override, new.basis_override_reason) == ("mcr", "Carried from ctl-old: Statute")
    # A successor with its own override keeps it.
    _, kept = migrate(SimpleNamespace(
        selected=True, basis_override="dsr", basis_override_reason="Own",
    ))
    assert (kept.basis_override, kept.basis_override_reason) == ("dsr", "Own")


async def _pick(db):
    """Two frameworks and three controls from the seeded catalogue.

    ``both`` maps fw_a and fw_b, ``b_only`` maps fw_b but not fw_a, and
    ``neither`` maps neither. fw_a is chosen so that some control mapped by fw_b
    is not mapped by fw_a.
    """
    rows = (await db.execute(
        select(SCFCatalogControl.scf_id, SCFCatalogControl.framework_mappings)
        .where(SCFCatalogControl.status == "active")
        .order_by(SCFCatalogControl.scf_id)
    )).fetchall()
    keys = {
        scf_id: {k for k in (m or {}) if not k.startswith(INTERNAL_MAPPING_PREFIXES)}
        for scf_id, m in rows
    }
    for scf_id, fws in keys.items():
        for fw_a in sorted(fws):
            for fw_b in sorted(fws - {fw_a}):
                b_only = next((c for c, f in keys.items() if fw_b in f and fw_a not in f), None)
                neither = next(
                    (c for c, f in keys.items() if fw_a not in f and fw_b not in f and c != b_only),
                    None,
                )
                if b_only and neither:
                    return SimpleNamespace(
                        fw_a=fw_a, fw_b=fw_b, both=scf_id, b_only=b_only, neither=neither
                    )
    pytest.skip("catalogue has no suitable framework pair")  # pragma: no cover


async def _org(db):
    tag = _uuid.uuid4().hex[:10]
    org = Organization(name=f"pig-{tag}", slug=f"pig-{tag}")
    db.add(org)
    await db.flush()
    return org


async def _user(db, org, role):
    tag = _uuid.uuid4().hex[:10]
    user = User(google_sub=f"pig-{tag}", email=f"pig-{tag}@example.invalid")
    db.add(user)
    await db.flush()
    db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=role))
    await db.flush()
    return user


async def _select(db, org, fw, basis, active=True):
    db.add(OrganizationFrameworkSelection(
        organization_id=org.id, framework_id=fw, source="bulk_scope", active=active, basis=basis,
    ))
    await db.flush()


async def _scope(db, org, scf_id, **kwargs):
    kwargs.setdefault("selected", True)
    row = ScopedControl(organization_id=org.id, scf_id=scf_id, **kwargs)
    db.add(row)
    await db.flush()
    return row


async def _world(db):
    """fw_a is MCR, fw_b is DSR; all three controls scoped."""
    p = await _pick(db)
    org = await _org(db)
    await _select(db, org, p.fw_a, "mcr")
    await _select(db, org, p.fw_b, "dsr")
    for scf_id in (p.both, p.b_only, p.neither):
        await _scope(db, org, scf_id)
    return p, org


def _membership(user, role="admin"):
    return OrgMembership(
        user=SimpleNamespace(db_id=str(user.id)), organization_id=None, role=role
    )


def _request():
    return Request({"type": "http", "headers": [], "method": "POST", "path": "/"})


async def _evaluate(db, org, check):
    totals = await journey_service._in_scope_counts(db, org.id)
    return await journey_service._evaluate_one(db, org.id, check, totals)


@pytest.fixture
def no_commit(db, monkeypatch):
    """Endpoints commit; inside the rolled-back test transaction that is a flush."""
    monkeypatch.setattr(db, "commit", db.flush)


@requires_postgres
@pytest.mark.asyncio
class TestDerivation:

    @pytest_asyncio.fixture(autouse=True)
    async def _seeded(self, db):
        if not await db.scalar(select(func.count(SCFCatalogControl.scf_id))):
            pytest.skip("requires the seeded SCF catalogue")

    async def test_sql_and_python_forms_agree(self, db):
        p, org = await _world(db)
        row = (await db.execute(select(ScopedControl).where(
            ScopedControl.organization_id == org.id, ScopedControl.scf_id == p.neither
        ))).scalar_one()
        row.basis_override, row.basis_override_reason = "dsr", "Board decision"
        await db.flush()

        python = await basis_service.effective_bases(db, org.id, [p.both, p.b_only, p.neither])
        sql = dict((await db.execute(
            select(ScopedControl.scf_id, basis_service.scoped_control_basis_expr()).where(
                ScopedControl.organization_id == org.id
            )
        )).fetchall())
        assert python == sql == {p.both: "mcr", p.b_only: "dsr", p.neither: "dsr"}

    async def test_inactive_and_unclassified_selections_contribute_nothing(self, db):
        p = await _pick(db)
        org = await _org(db)
        await _select(db, org, p.fw_a, "mcr", active=False)
        await _select(db, org, p.fw_b, None)
        await _scope(db, org, p.both)
        assert await basis_service.effective_bases(db, org.id, [p.both]) == {p.both: None}

    async def test_another_orgs_selection_never_classifies_this_orgs_control(self, db):
        p = await _pick(db)
        mine, theirs = await _org(db), await _org(db)
        await _select(db, theirs, p.fw_a, "mcr")
        await _scope(db, mine, p.both)
        sql = await db.scalar(
            select(basis_service.scoped_control_basis_expr()).where(
                ScopedControl.organization_id == mine.id
            )
        )
        assert sql is None
        assert (await basis_service.effective_bases(db, mine.id, [p.both]))[p.both] is None


@requires_postgres
@pytest.mark.asyncio
class TestJourneyChecks:

    @pytest_asyncio.fixture(autouse=True)
    async def _seeded(self, db):
        if not await db.scalar(select(func.count(SCFCatalogControl.scf_id))):
            pytest.skip("requires the seeded SCF catalogue")

    async def test_frameworks_with_basis_counts_active_selections_of_that_basis(self, db):
        p, org = await _world(db)
        await _select(db, org, "inactive_mcr_fw", "mcr", active=False)
        out = await _evaluate(db, org, {"type": "frameworks_with_basis", "basis": "mcr", "min_count": 1})
        assert out["met"] is True and out["detail"] == "1 MCR framework(s) scoped (need 1)"
        out = await _evaluate(db, org, {"type": "frameworks_with_basis", "basis": "dsr", "min_count": 2})
        assert out["met"] is False and out["detail"] == "1 DSR framework(s) scoped (need 2)"

    async def test_controls_with_basis_counts_classified_in_scope_controls(self, db):
        p, org = await _world(db)
        out = await _evaluate(db, org, {"type": "controls_with_basis", "min_fraction": 1})
        assert out["met"] is False
        assert out["detail"].startswith("2 of 3 with an obligation basis")
        out = await _evaluate(db, org, {"type": "controls_with_basis", "basis": "mcr", "min_fraction": 0.3})
        assert out["met"] is True and out["detail"].startswith("1 of 3 with MCR basis")
        # The strongest basis wins, so the control both frameworks map is MCR, not DSR.
        out = await _evaluate(db, org, {"type": "controls_with_basis", "basis": "dsr"})
        assert out["detail"].startswith("1 of 3 with DSR basis")

    async def test_an_override_classifies_and_scf_ids_narrows(self, db):
        p, org = await _world(db)
        row = (await db.execute(select(ScopedControl).where(
            ScopedControl.organization_id == org.id, ScopedControl.scf_id == p.neither
        ))).scalar_one()
        row.basis_override, row.basis_override_reason = "dsr", "Board decision"
        await db.flush()
        out = await _evaluate(db, org, {"type": "controls_with_basis", "min_fraction": 1})
        assert out["met"] is True and out["detail"].startswith("3 of 3")
        out = await _evaluate(db, org, {
            "type": "controls_with_basis", "basis": "mcr", "scf_ids": [p.both, p.b_only],
        })
        assert out["detail"].startswith("1 of 2 across 2 named controls with MCR basis")

    async def test_no_scope_is_not_a_pass(self, db):
        org = await _org(db)
        out = await _evaluate(db, org, {"type": "controls_with_basis", "min_fraction": 0})
        assert out["met"] is False


@requires_postgres
@pytest.mark.asyncio
class TestMcrExclusion:

    @pytest_asyncio.fixture(autouse=True)
    async def _seeded(self, db):
        if not await db.scalar(select(func.count(SCFCatalogControl.scf_id))):
            pytest.skip("requires the seeded SCF catalogue")

    async def test_excluding_an_mcr_control_needs_an_admin_approver(self, db):
        p, org = await _world(db)
        editor = await _user(db, org, "editor")
        admin = await _user(db, org, "admin")
        with pytest.raises(PermissionError, match="needs an admin"):
            await set_individual_scope_override(
                db, org.id, p.both, "exclude", "Out of scope", editor.id, commit=False
            )
        row = await set_individual_scope_override(
            db, org.id, p.both, "exclude", "Out of scope", admin.id,
            commit=False, approver_user_id=admin.id,
        )
        assert (row.selected, row.scope_override, row.scope_override_approved_by) == (
            False, "exclude", admin.id
        )
        # Restoring scope clears the approval with the exclusion it approved.
        row = await set_individual_scope_override(
            db, org.id, p.both, "include", None, editor.id, commit=False
        )
        assert row.scope_override_approved_by is None

    async def test_excluding_a_dsr_control_needs_only_a_reason(self, db):
        p, org = await _world(db)
        editor = await _user(db, org, "editor")
        row = await set_individual_scope_override(
            db, org.id, p.b_only, "exclude", "Not needed", editor.id, commit=False
        )
        assert row.selected is False and row.scope_override_approved_by is None

    async def test_an_editor_cannot_exclude_an_mcr_control_in_an_admins_name(
        self, db, no_commit
    ):
        p, org = await _world(db)
        editor = await _user(db, org, "editor")
        admin = await _user(db, org, "admin")
        # The admin's id in the payload is not an approval: the field is gone
        # and the approver is whoever is authenticated.
        with pytest.raises(HTTPException) as exc:
            await scoped_api.put_scope_override(
                org.id, p.both,
                ScopeOverrideRequest.model_validate(
                    {"action": "exclude", "reason": "Out", "approver_user_id": str(admin.id)}
                ),
                _request(), _membership(editor, "editor"), db,
            )
        assert exc.value.status_code == 403
        still = (await db.execute(select(ScopedControl.selected).where(
            ScopedControl.organization_id == org.id, ScopedControl.scf_id == p.both
        ))).scalar_one()
        assert still is True

    async def test_an_admin_exclusion_records_and_audits_the_admin_as_approver(
        self, db, no_commit
    ):
        p, org = await _world(db)
        admin = await _user(db, org, "admin")
        await scoped_api.put_scope_override(
            org.id, p.both, ScopeOverrideRequest(action="exclude", reason="Out"),
            _request(), _membership(admin), db,
        )
        logged = (await db.execute(select(AuditLog.new_value).where(
            AuditLog.organization_id == org.id,
            AuditLog.scf_id == p.both,
            AuditLog.field_name == "scope_override_approved_by",
        ))).scalars().all()
        assert [json.loads(v) for v in logged] == [str(admin.id)]

    async def test_inherit_cannot_drop_a_control_that_is_mcr_by_override(self, db):
        p, org = await _world(db)
        editor = await _user(db, org, "editor")
        row = (await db.execute(select(ScopedControl).where(
            ScopedControl.organization_id == org.id, ScopedControl.scf_id == p.neither
        ))).scalar_one()
        row.basis_override, row.basis_override_reason = "mcr", "Contract schedule"
        await db.flush()
        with pytest.raises(ValueError, match="MCR by override"):
            await set_individual_scope_override(
                db, org.id, p.neither, "inherit", None, editor.id, commit=False
            )
        # Without the override the same inherit simply follows the frameworks.
        # The control may be weighted 10, and so material (PIG R6), which would
        # refuse the inherit for its own reason; take that out of the picture.
        row.basis_override, row.basis_override_reason = None, None
        row.material_override, row.material_reason = False, "Not material here"
        await db.flush()
        row = await set_individual_scope_override(
            db, org.id, p.neither, "inherit", None, editor.id, commit=False
        )
        assert row.selected is False

    async def test_removing_a_framework_that_carries_mcr_needs_an_admin_and_a_reason(
        self, db, no_commit
    ):
        p, org = await _world(db)
        editor = await _user(db, org, "editor")
        admin = await _user(db, org, "admin")
        assert await basis_service.unscope_touches_mcr(db, org.id, [p.fw_a]) is True
        assert await basis_service.unscope_touches_mcr(db, org.id, [p.fw_b]) is False

        with pytest.raises(HTTPException) as exc:
            await scoped_api.bulk_unscope_by_framework(
                org.id, BulkUnscopeFrameworkRequest(frameworks=[p.fw_a], removal_reason="Done"),
                _membership(editor, "editor"), db,
            )
        assert exc.value.status_code == 403
        with pytest.raises(HTTPException) as exc:
            await scoped_api.bulk_unscope_by_framework(
                org.id, BulkUnscopeFrameworkRequest(frameworks=[p.fw_a]),
                _membership(admin), db,
            )
        assert exc.value.status_code == 400

        # A DSR framework still counts once it maps an in-scope MCR override.
        row = (await db.execute(select(ScopedControl).where(
            ScopedControl.organization_id == org.id, ScopedControl.scf_id == p.b_only
        ))).scalar_one()
        row.basis_override, row.basis_override_reason = "mcr", "Regulator letter"
        await db.flush()
        assert await basis_service.unscope_touches_mcr(db, org.id, [p.fw_b]) is True
        other = await _org(db)
        assert await basis_service.unscope_touches_mcr(db, other.id, [p.fw_a, p.fw_b]) is False

    async def test_every_other_write_path_refuses_to_deselect_an_mcr_control(self, db, no_commit):
        p, org = await _world(db)
        editor = await _user(db, org, "editor")
        m = _membership(editor, "editor")

        with pytest.raises(HTTPException) as exc:
            await scoped_api.update_scoped_control(
                org.id, p.both, ScopedControlUpdate(selected=False), _request(), m, db
            )
        assert exc.value.status_code == 409 and "MCR" in exc.value.detail

        with pytest.raises(HTTPException) as exc:
            await scoped_api.create_or_update_scoped_control(
                org.id, ScopedControlCreate(scf_id=p.both, selected=False), _request(), m, db
            )
        assert exc.value.status_code == 409

        with pytest.raises(HTTPException) as exc:
            await scoped_api.delete_scoped_control(org.id, p.both, _request(), m, db)
        assert exc.value.status_code == 409

        out = await scoped_api.batch_update_scoped_controls(
            org.id,
            BatchScopedControlRequest(operations=[
                BatchScopedControlOperation(scf_id=p.both, selected=False),
                BatchScopedControlOperation(scf_id=p.b_only, selected=False),
            ]),
            _request(), m, db,
        )
        assert (out.updated, out.failed) == (1, 1)
        assert out.errors and out.errors[0].startswith(f"{p.both}:")

        # An explicit null deselects (the row would store NULL, which every
        # reader treats as out of scope), so it meets the same guard.
        with pytest.raises(HTTPException) as exc:
            await scoped_api.update_scoped_control(
                org.id, p.both, ScopedControlUpdate(selected=None), _request(), m, db
            )
        assert exc.value.status_code == 409
        with pytest.raises(HTTPException) as exc:
            await scoped_api.create_or_update_scoped_control(
                org.id, ScopedControlCreate(scf_id=p.both, selected=None), _request(), m, db
            )
        assert exc.value.status_code == 409
        out = await scoped_api.batch_update_scoped_controls(
            org.id,
            BatchScopedControlRequest(operations=[
                BatchScopedControlOperation(scf_id=p.both, selected=None),
            ]),
            _request(), m, db,
        )
        assert (out.updated, out.failed) == (0, 1)

        row = (await db.execute(select(ScopedControl.selected).where(
            ScopedControl.organization_id == org.id, ScopedControl.scf_id == p.both
        ))).scalar_one()
        assert row is True

    async def test_writes_that_leave_scope_alone_are_not_exclusions(self, db, no_commit):
        p, org = await _world(db)
        editor = await _user(db, org, "editor")
        out = await scoped_api.update_scoped_control(
            org.id, p.both, ScopedControlUpdate(implementation_status="in_progress"),
            _request(), _membership(editor), db,
        )
        assert out.selected is True and out.implementation_status == "in_progress"


@requires_postgres
@pytest.mark.asyncio
class TestBasisWrites:

    @pytest_asyncio.fixture(autouse=True)
    async def _seeded(self, db):
        if not await db.scalar(select(func.count(SCFCatalogControl.scf_id))):
            pytest.skip("requires the seeded SCF catalogue")

    async def test_framework_basis_is_written_and_audited(self, db, no_commit):
        p, org = await _world(db)
        admin = await _user(db, org, "admin")
        item = await scoped_api.put_framework_basis(
            org.id, p.fw_b,
            FrameworkBasisRequest(basis="mcr", obligation_type="contractual", obligation_source="MSA schedule 4"),
            _request(), _membership(admin), db,
        )
        assert (item["basis"], item["obligation_type"], item["obligation_source"]) == (
            "mcr", "contractual", "MSA schedule 4"
        )
        rows = dict((await db.execute(select(AuditLog.field_name, AuditLog.new_value).where(
            AuditLog.organization_id == org.id,
            AuditLog.entity_type == "organization_framework_selection",
        ))).fetchall())
        assert {k: json.loads(v) for k, v in rows.items()} == {
            "basis": "mcr", "obligation_type": "contractual", "obligation_source": "MSA schedule 4",
        }

    async def test_an_unscoped_framework_cannot_be_classified(self, db, no_commit):
        p, org = await _world(db)
        admin = await _user(db, org, "admin")
        with pytest.raises(HTTPException) as exc:
            await scoped_api.put_framework_basis(
                org.id, "not_a_selected_framework", FrameworkBasisRequest(basis="mcr"),
                _request(), _membership(admin), db,
            )
        assert exc.value.status_code == 404

    async def test_control_override_is_written_audited_and_cleared(self, db, no_commit):
        p, org = await _world(db)
        admin = await _user(db, org, "admin")
        row = await scoped_api.put_control_basis_override(
            org.id, p.both, ControlBasisOverrideRequest(basis="dsr", reason="Inherited from parent"),
            _request(), _membership(admin), db,
        )
        assert (row.basis_override, row.basis_override_reason, row.basis_override_set_by) == (
            "dsr", "Inherited from parent", admin.id
        )
        assert (await basis_service.effective_bases(db, org.id, [p.both]))[p.both] == "dsr"
        logged = (await db.execute(select(AuditLog.new_value).where(
            AuditLog.organization_id == org.id,
            AuditLog.scf_id == p.both,
            AuditLog.field_name == "basis_override",
        ))).scalars().all()
        assert [json.loads(v) for v in logged] == ["dsr"]

        row = await scoped_api.put_control_basis_override(
            org.id, p.both, ControlBasisOverrideRequest(basis=None), _request(), _membership(admin), db,
        )
        assert row.basis_override is None and row.basis_override_reason is None
        assert (await basis_service.effective_bases(db, org.id, [p.both]))[p.both] == "mcr"

    async def test_the_database_refuses_an_override_without_a_reason(self, db):
        p, org = await _world(db)
        for reason in (None, "   "):
            with pytest.raises(IntegrityError):
                async with db.begin_nested():
                    await db.execute(
                        ScopedControl.__table__.update()
                        .where(ScopedControl.organization_id == org.id, ScopedControl.scf_id == p.both)
                        .values(basis_override="mcr", basis_override_reason=reason)
                    )

    async def test_override_on_a_control_with_no_scope_record_is_404(self, db, no_commit):
        p = await _pick(db)
        org = await _org(db)
        admin = await _user(db, org, "admin")
        with pytest.raises(HTTPException) as exc:
            await scoped_api.put_control_basis_override(
                org.id, p.both, ControlBasisOverrideRequest(basis="mcr", reason="x"),
                _request(), _membership(admin), db,
            )
        assert exc.value.status_code == 404


@requires_postgres
@pytest.mark.asyncio
class TestReadSurfaces:

    @pytest_asyncio.fixture(autouse=True)
    async def _seeded(self, db):
        if not await db.scalar(select(func.count(SCFCatalogControl.scf_id))):
            pytest.skip("requires the seeded SCF catalogue")

    async def test_summary_splits_in_scope_controls_and_carries_selection_basis(self, db):
        p, org = await _world(db)
        summary = await framework_scope_summary(db, org.id)
        assert summary["basis_counts"] == {"mcr": 1, "dsr": 1, "unclassified": 1}
        by_id = {f["id"]: f for f in summary["frameworks"]}
        assert by_id[p.fw_a]["basis"] == "mcr" and by_id[p.fw_b]["basis"] == "dsr"

    async def test_remove_preview_lists_basis_downgrades(self, db):
        p, org = await _world(db)
        preview = await preview_framework_change(db, org.id, [p.fw_a], "remove")
        # Still in scope through fw_b, but only as DSR now.
        assert {"scf_id": p.both, "from_basis": "mcr", "to_basis": "dsr"} in preview["basis_downgrades"]
        assert all(d["scf_id"] != p.b_only for d in preview["basis_downgrades"])
        add = await preview_framework_change(db, org.id, [p.fw_a], "add")
        assert add["basis_downgrades"] == []

    async def test_scoping_list_reports_and_filters_by_basis(self, db):
        p, org = await _world(db)
        admin = await _user(db, org, "admin")

        async def listing(basis):
            # Through the response model: a field it does not declare is
            # dropped from the payload, so this proves the API carries it.
            return ScopedControlsPaginatedResponse.model_validate(await scoped_api.list_scoped_controls_paginated(
                org.id, membership=_membership(admin), db=db, scope_status="in_scope",
                domain=None, csf_function=None, framework=None, control_weighting=None,
                search=None, team_id=None, my_teams=False, function_id=None,
                accountable_owner_type=None, basis=basis, material=None, sort="scf_id", limit=200, offset=0,
            ))

        page = await listing(None)
        got = {c.scf_id: (c.obligation_basis, c.basis_source) for c in page.controls}
        assert got == {
            p.both: ("mcr", "framework"),
            p.b_only: ("dsr", "framework"),
            p.neither: (None, None),
        }
        assert [c.scf_id for c in (await listing("mcr")).controls] == [p.both]
        assert [c.scf_id for c in (await listing("dsr")).controls] == [p.b_only]
        assert [c.scf_id for c in (await listing("unclassified")).controls] == [p.neither]

    async def test_engagement_snapshot_freezes_the_basis(self, db):
        p, org = await _world(db)
        eng = AuditEngagement(organization_id=org.id, name="pig", frameworks=[p.fw_a])
        db.add(eng)
        await db.flush()
        await engagements_api._materialise_scope(
            db=db, engagement_id=eng.id, org_id=org.id, frameworks=[p.fw_a]
        )
        await db.flush()
        frozen = await db.scalar(select(EngagementControlScope.obligation_basis).where(
            EngagementControlScope.engagement_id == eng.id,
            EngagementControlScope.scf_id == p.both,
        ))
        assert frozen == "mcr"

        # Reclassifying afterwards changes the live basis, never the snapshot.
        sel = (await db.execute(select(OrganizationFrameworkSelection).where(
            OrganizationFrameworkSelection.organization_id == org.id,
            OrganizationFrameworkSelection.framework_id == p.fw_a,
        ))).scalar_one()
        sel.basis = "dsr"
        await db.flush()
        items = await engagements_api.get_engagement_scope(org.id, eng.id, db=db)
        assert {i.scf_id: i.obligation_basis for i in items}[p.both] == "mcr"
