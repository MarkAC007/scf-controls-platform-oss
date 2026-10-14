"""Organisation compensating controls (#1129, PIG R9).

A compensating control with no vendor is recorded against one of the
organisation's controls. These tests pin:

* the record: only for a control the organisation has in scope, never another
  organisation's; written to the audit log on create, update and delete;
  invisible to (and untouchable by) another organisation;
* the guard: on a material control (by catalogue weighting or by override) it
  needs ``risk_code``, an accepted risk linked to the control (the
  catalogue's link or the organisation's custom one) with an approval in
  force; an expired, unapproved or unaccepted risk, or one not linked to the
  control, is refused; on any other control ``risk_code`` is optional but
  must still be linked and assessed;
* updates re-check a changed ``risk_code`` and refuse clearing it on a
  material control;
* vendor compensating controls, which carry the organisation now and are
  otherwise unchanged, and never appear among the organisation's.

Control ids and catalogue risk codes are read from the catalogue at run time;
custom risk codes are the organisation's own (R-ORG-n). Everything runs inside
a transaction that is rolled back.
"""
import inspect
import json
import uuid as _uuid
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from api import compensating_controls as cc_api
from api import custom_risks as custom_api
from api import vendors as vendor_api
from auth import OrgMembership
from catalog_models import SCFCatalogControl
from middleware.audit_middleware import _extract_entity_type
from models import (
    AuditLog,
    Organization,
    OrganizationMember,
    RiskAssessment,
    ScopedControl,
    User,
    Vendor,
    VendorCompensatingControl,
)
from schemas import (
    CompensatingControlCreate,
    CompensatingControlUpdate,
    CustomRiskControlMappingCreate,
    CustomRiskDefinitionCreate,
    VendorCompensatingControlCreate,
)
from services import compensating_controls as service
from services import materiality, risk_acceptance

from test_journey_service import DATABASE_URL, requires_postgres  # noqa: F401

TODAY = date.today()


# ---------------------------------------------------------------------------
# Structural: no database
# ---------------------------------------------------------------------------


def _min_role(handler):
    dependency = inspect.signature(handler).parameters["membership"].default.dependency
    assert dependency.__qualname__.startswith("require_org_role")
    return dict(zip(dependency.__code__.co_freevars, (c.cell_contents for c in dependency.__closure__)))["min_role"]


def test_the_routes_are_registered_with_their_roles():
    routes = {(r.path, m) for r in cc_api.router.routes for m in getattr(r, "methods", ())}
    base = "/organizations/{org_id}/compensating-controls"
    one = f"{base}/{{cc_id}}"
    assert routes == {(base, "GET"), (base, "POST"), (one, "GET"), (one, "PATCH"), (one, "DELETE")}
    assert _min_role(cc_api.list_compensating_controls) == "viewer"
    assert _min_role(cc_api.get_compensating_control) == "viewer"
    assert _min_role(cc_api.create_compensating_control) == "editor"
    assert _min_role(cc_api.update_compensating_control) == "editor"
    assert _min_role(cc_api.delete_compensating_control) == "editor"


def test_the_router_is_mounted_and_audited_as_compensating_control():
    import main

    paths = main.app.openapi()["paths"]
    base = "/api/organizations/{org_id}/compensating-controls"
    assert {"get", "post"} <= set(paths.get(base, {}))
    assert {"get", "patch", "delete"} <= set(paths.get(f"{base}/{{cc_id}}", {}))
    org = _uuid.uuid4()
    assert _extract_entity_type(f"/api/organizations/{org}/compensating-controls/x") == "compensating_control"
    # The vendor routes stay the vendor's.
    assert _extract_entity_type(f"/api/organizations/{org}/vendors/x/compensating-controls") == "vendor"


@pytest.mark.parametrize("field", ["gap_description", "compensating_control"])
@pytest.mark.parametrize("value", ["", "   "])
def test_blank_text_is_refused(field, value):
    fields = dict(scf_id="X", gap_description="gap", compensating_control="comp")
    fields[field] = value
    with pytest.raises(ValueError):
        CompensatingControlCreate(**fields)
    with pytest.raises(ValueError):
        CompensatingControlUpdate(**{field: value})


def test_text_is_stripped():
    data = CompensatingControlCreate(scf_id="X", gap_description=" gap ", compensating_control=" comp ")
    assert (data.gap_description, data.compensating_control) == ("gap", "comp")


def test_unknown_fields_are_refused_not_ignored():
    with pytest.raises(ValueError):
        CompensatingControlUpdate(scf_id="X")
    with pytest.raises(ValueError):
        CompensatingControlCreate(scf_id="X", gap_description="g", compensating_control="c",
                                  vendor_id=str(_uuid.uuid4()))


def test_the_effectiveness_rating_is_one_of_three():
    with pytest.raises(ValueError):
        CompensatingControlCreate(scf_id="X", gap_description="g", compensating_control="c",
                                  effectiveness_rating="total")


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


async def _controls(db, n, heavy=None, with_risks=False, distinct_domains=False):
    """``n`` active catalogue controls; heavy=True material by weighting, False not."""
    # The catalogue's risk links may sit only on deprecated controls in a test
    # database; the record does not look at catalogue status, so those serve.
    query = select(SCFCatalogControl.scf_id, SCFCatalogControl.risk_codes)
    if not with_risks:
        query = query.where(SCFCatalogControl.status == "active")
    weight = SCFCatalogControl.control_weighting
    if heavy is True:
        query = query.where(weight >= materiality.MATERIAL_WEIGHTING)
    elif heavy is False:
        query = query.where(weight.between(1, materiality.MATERIAL_WEIGHTING - 1))
    rows = (await db.execute(query.order_by(SCFCatalogControl.scf_id))).all()
    if with_risks:
        rows = [r for r in rows if r.risk_codes]
    if distinct_domains:
        seen, picked = set(), []
        for r in rows:
            if r.scf_id.split("-", 1)[0] not in seen:
                seen.add(r.scf_id.split("-", 1)[0])
                picked.append(r)
        rows = picked
    if len(rows) < n:  # pragma: no cover - environment dependent
        pytest.skip(f"catalogue has fewer than {n} suitable controls")
    return [r.scf_id for r in rows[:n]]


async def _catalog_risks(db, scf_id):
    return list(await db.scalar(
        select(SCFCatalogControl.risk_codes).where(SCFCatalogControl.scf_id == scf_id)
    ) or [])


async def _scope(db, org, scf_id, **kwargs):
    fields = dict(selected=True, implementation_status="not_started")
    fields.update(kwargs)
    row = ScopedControl(organization_id=org.id, scf_id=scf_id, **fields)
    db.add(row)
    await db.flush()
    return row


async def _custom_risk(db, org, user, scf_id=None):
    """A custom risk, linked to ``scf_id`` when given."""
    out = await custom_api.create_custom_risk(
        org.id, CustomRiskDefinitionCreate(title="Legacy VPN", description="Below the standard"),
        _request(), _membership(org, user), db,
    )
    if scf_id is not None:
        await custom_api.add_custom_risk_control(
            org.id, out.risk_code, CustomRiskControlMappingCreate(scf_id=scf_id),
            _request(), _membership(org, user), db,
        )
    return out.risk_code


async def _assess(db, org, risk_code):
    """An assessment for a catalogue risk code."""
    db.add(RiskAssessment(organization_id=org.id, risk_code=risk_code, treatment_status="analysed"))
    await db.flush()


async def _accept(db, org, risk_code, approver, expires_on=None):
    await risk_acceptance.accept_risk(
        db, org.id, risk_code, "Legacy kit retires next year", expires_on or TODAY + timedelta(days=90),
        approver.id,
    )
    await db.flush()


async def _assessment(db, org, risk_code):
    return (await db.execute(select(RiskAssessment).where(
        RiskAssessment.organization_id == org.id, RiskAssessment.risk_code == risk_code,
    ))).scalar_one()


async def _audited(db, org, entity_id):
    return list((await db.execute(select(AuditLog).where(
        AuditLog.organization_id == org.id, AuditLog.entity_id == entity_id,
    ))).scalars().all())


async def _create(db, org, user, scf_id, **kwargs):
    fields = dict(scf_id=scf_id, gap_description="No MFA on the legacy VPN",
                  compensating_control="Source-IP allow-list and session recording")
    fields.update(kwargs)
    return await cc_api.create_compensating_control(
        org.id, CompensatingControlCreate(**fields), _request(), _membership(org, user), db,
    )


async def _update(db, org, user, cc_id, **kwargs):
    return await cc_api.update_compensating_control(
        org.id, cc_id, CompensatingControlUpdate(**kwargs), _request(), _membership(org, user), db,
    )


async def _list(db, org, **filters):
    params = dict(scf_id=None, domain=None, risk_code=None)
    params.update(filters)
    return await cc_api.list_compensating_controls(org.id, **params, membership=None, db=db)


async def _vendor_row(db, org):
    vendor = Vendor(organization_id=org.id, name=f"pig-vendor-{_uuid.uuid4().hex[:6]}")
    db.add(vendor)
    await db.flush()
    row = VendorCompensatingControl(organization_id=org.id, vendor_id=vendor.id, gap_description="No SOC 2",
                                    compensating_control="Annual on-site audit")
    db.add(row)
    await db.flush()
    return vendor, row


@requires_postgres
@pytest.mark.asyncio
class TestRecord:
    async def test_recorded_against_an_in_scope_control_and_audited(self, db, no_commit):
        org = await _org(db)
        user = await _user(db, org)
        (scf,) = await _controls(db, 1, heavy=False)
        control = await _scope(db, org, scf)
        out = await _create(db, org, user, scf.lower(), effectiveness_rating="full",
                            risk_reduction_notes="Covers remote admin only")
        assert (out.scoped_control_id, out.scf_id, out.domain) == (control.id, scf, scf.split("-")[0])
        assert (out.organization_id, out.effectiveness_rating, out.risk_code, out.risk_acceptance_state) == (
            org.id, "full", None, None)
        audit = {r.field_name: r for r in await _audited(db, org, out.id)}
        assert set(audit) == service.COMPENSATING_TRACKED_FIELDS
        assert {r.action for r in audit.values()} == {"create"}
        assert {r.entity_type for r in audit.values()} == {"compensating_control"}
        assert {r.changed_by_user_id for r in audit.values()} == {user.id}
        assert {r.scf_id for r in audit.values()} == {scf}
        assert json.loads(audit["compensating_control"].new_value) == "Source-IP allow-list and session recording"
        assert json.loads(audit["scoped_control_id"].new_value) == str(control.id)

    @pytest.mark.parametrize("which", ["unscoped", "descoped", "other-org"])
    async def test_refused_for_a_control_this_org_does_not_have_in_scope(self, db, no_commit, which):
        org, other = await _org(db), await _org(db)
        user = await _user(db, org)
        unscoped, descoped, foreign = await _controls(db, 3, heavy=False)
        await _scope(db, org, descoped, selected=False)
        await _scope(db, other, foreign)
        scf = {"unscoped": unscoped, "descoped": descoped, "other-org": foreign}[which]
        with pytest.raises(HTTPException) as exc:
            await _create(db, org, user, scf)
        assert exc.value.status_code == 422
        assert (await _list(db, org)) == []

    async def test_a_linked_assessed_risk_is_kept_on_a_control_that_is_not_material(self, db, no_commit):
        org = await _org(db)
        user = await _user(db, org)
        (scf,) = await _controls(db, 1, heavy=False)
        await _scope(db, org, scf)
        code = await _custom_risk(db, org, user, scf)
        out = await _create(db, org, user, scf, risk_code=f" {code} ")
        assert (out.risk_code, out.risk_acceptance_state) == (code, None)

    @pytest.mark.parametrize("which", ["unlinked", "unassessed-catalogue", "unknown"])
    async def test_a_risk_code_must_be_linked_and_assessed_even_when_not_material(self, db, no_commit, which):
        org = await _org(db)
        user = await _user(db, org)
        scf, elsewhere = await _controls(db, 2, heavy=False)
        await _scope(db, org, scf)
        if which == "unlinked":
            code = await _custom_risk(db, org, user, elsewhere)
        elif which == "unassessed-catalogue":
            (scf,) = await _controls(db, 1, heavy=False, with_risks=True)
            await _scope(db, org, scf)
            code = (await _catalog_risks(db, scf))[0]
        else:
            code = "R-ORG-999"
        with pytest.raises(HTTPException) as exc:
            await _create(db, org, user, scf, risk_code=code)
        assert exc.value.status_code == 409

    async def test_another_org_cannot_see_or_touch_it(self, db, no_commit):
        org, other = await _org(db), await _org(db)
        user, outsider = await _user(db, org), await _user(db, other)
        (scf,) = await _controls(db, 1, heavy=False)
        await _scope(db, org, scf)
        await _scope(db, other, scf)
        out = await _create(db, org, user, scf)
        assert (await _list(db, other)) == []
        for call in (
            lambda: cc_api.get_compensating_control(other.id, out.id, membership=None, db=db),
            lambda: _update(db, other, outsider, out.id, compensating_control="Hijacked"),
            lambda: cc_api.delete_compensating_control(other.id, out.id, _request(),
                                                       _membership(other, outsider), db),
        ):
            with pytest.raises(HTTPException) as exc:
                await call()
            assert exc.value.status_code == 404
        assert (await _list(db, org))[0].compensating_control == "Source-IP allow-list and session recording"

    async def test_the_database_refuses_a_row_for_neither_a_vendor_nor_a_control(self, db):
        org = await _org(db)
        db.add(VendorCompensatingControl(organization_id=org.id, gap_description="g", compensating_control="c"))
        with pytest.raises(IntegrityError):
            await db.flush()


@requires_postgres
@pytest.mark.asyncio
class TestMaterial:
    async def test_refused_without_a_risk_code_naming_the_acceptances_in_force(self, db, no_commit):
        org = await _org(db)
        user, admin = await _user(db, org), await _user(db, org, "admin")
        (scf,) = await _controls(db, 1, heavy=True)
        await _scope(db, org, scf)
        code = await _custom_risk(db, org, user, scf)
        with pytest.raises(HTTPException) as exc:
            await _create(db, org, user, scf)
        assert exc.value.status_code == 409
        assert "material" in exc.value.detail and "None of the risks" in exc.value.detail
        assert code not in exc.value.detail
        await _accept(db, org, code, admin)
        with pytest.raises(HTTPException) as exc:
            await _create(db, org, user, scf)
        assert exc.value.status_code == 409
        assert code in exc.value.detail
        assert (await _list(db, org)) == []

    async def test_an_accepted_custom_risk_linked_to_it_justifies_it(self, db, no_commit):
        org = await _org(db)
        user, admin = await _user(db, org), await _user(db, org, "admin")
        (scf,) = await _controls(db, 1, heavy=True)
        await _scope(db, org, scf)
        code = await _custom_risk(db, org, user, scf)
        await _accept(db, org, code, admin)
        out = await _create(db, org, user, scf, risk_code=code)
        assert (out.risk_code, out.risk_acceptance_state) == (code, "approved")
        audit = {r.field_name: r for r in await _audited(db, org, out.id)}
        assert json.loads(audit["risk_code"].new_value) == code

    async def test_an_accepted_catalogue_risk_of_the_control_justifies_it(self, db, no_commit):
        org = await _org(db)
        user, admin = await _user(db, org), await _user(db, org, "admin")
        (scf,) = await _controls(db, 1, heavy=True, with_risks=True)
        await _scope(db, org, scf)
        code = (await _catalog_risks(db, scf))[0]
        await _assess(db, org, code)
        await _accept(db, org, code, admin)
        out = await _create(db, org, user, scf, risk_code=code)
        assert out.risk_acceptance_state == "approved"

    @pytest.mark.parametrize("state", ["not-accepted", "expired", "unapproved", "withdrawn", "unlinked", "relinked"])
    async def test_refused_without_an_approval_in_force_on_a_linked_risk(self, db, no_commit, state):
        org = await _org(db)
        user, admin = await _user(db, org), await _user(db, org, "admin")
        scf, elsewhere = await _controls(db, 2, heavy=True)
        await _scope(db, org, scf)
        code = await _custom_risk(db, org, user, elsewhere if state == "unlinked" else scf)
        if state != "not-accepted":
            await _accept(db, org, code, admin)
        assessment = await _assessment(db, org, code)
        if state == "expired":
            assessment.acceptance_expires_on = TODAY - timedelta(days=1)
        elif state == "unapproved":
            assessment.accepted_by = None
        elif state == "withdrawn":
            await risk_acceptance.revoke_acceptance(db, org.id, code, admin.id)
        elif state == "relinked":
            # Linking the accepted exception to another control withdraws its approval (R7).
            await custom_api.add_custom_risk_control(
                org.id, code, CustomRiskControlMappingCreate(scf_id=elsewhere),
                _request(), _membership(org, user), db,
            )
        await db.flush()
        with pytest.raises(HTTPException) as exc:
            await _create(db, org, user, scf, risk_code=code)
        assert exc.value.status_code == 409
        assert (await _list(db, org)) == []

    async def test_an_acceptance_expiring_today_is_still_in_force(self, db, no_commit):
        org = await _org(db)
        user, admin = await _user(db, org), await _user(db, org, "admin")
        (scf,) = await _controls(db, 1, heavy=True)
        await _scope(db, org, scf)
        code = await _custom_risk(db, org, user, scf)
        await _accept(db, org, code, admin)
        (await _assessment(db, org, code)).acceptance_expires_on = TODAY
        await db.flush()
        assert (await _create(db, org, user, scf, risk_code=code)).risk_acceptance_state == "approved"

    async def test_the_override_decides_materiality(self, db, no_commit):
        org = await _org(db)
        user = await _user(db, org)
        (light,) = await _controls(db, 1, heavy=False)
        (heavy,) = await _controls(db, 1, heavy=True)
        await _scope(db, org, light, material_override=True, material_reason="Holds payment data")
        await _scope(db, org, heavy, material_override=False, material_reason="Lab network only")
        with pytest.raises(HTTPException) as exc:
            await _create(db, org, user, light)
        assert exc.value.status_code == 409
        assert (await _create(db, org, user, heavy)).scf_id == heavy

    async def test_another_orgs_link_does_not_count(self, db, no_commit):
        """Custom codes are numbered per organisation, so two can share one."""
        org, other = await _org(db), await _org(db)
        user, admin = await _user(db, org), await _user(db, org, "admin")
        other_user = await _user(db, other)
        scf, elsewhere = await _controls(db, 2, heavy=True)
        await _scope(db, org, scf)
        code = await _custom_risk(db, org, user, elsewhere)
        assert await _custom_risk(db, other, other_user, scf) == code
        await _accept(db, org, code, admin)
        with pytest.raises(HTTPException) as exc:
            await _create(db, org, user, scf, risk_code=code)
        assert exc.value.status_code == 409

    async def test_the_hint_names_only_this_orgs_acceptances(self, db, no_commit):
        org, other = await _org(db), await _org(db)
        user = await _user(db, org)
        other_user, other_admin = await _user(db, other), await _user(db, other, "admin")
        (scf,) = await _controls(db, 1, heavy=True)
        await _scope(db, org, scf)
        code = await _custom_risk(db, org, user, scf)
        assert await _custom_risk(db, other, other_user, scf) == code
        await _accept(db, other, code, other_admin)
        with pytest.raises(HTTPException) as exc:
            await _create(db, org, user, scf)
        assert "None of the risks" in exc.value.detail and code not in exc.value.detail

    async def test_a_link_saved_in_lower_case_counts(self, db, no_commit):
        org = await _org(db)
        user, admin = await _user(db, org), await _user(db, org, "admin")
        (scf,) = await _controls(db, 1, heavy=True)
        await _scope(db, org, scf)
        code = await _custom_risk(db, org, user, scf.lower())
        await _accept(db, org, code, admin)
        assert (await _create(db, org, user, scf, risk_code=code)).justified is True

    async def test_a_catalogue_risk_of_another_control_does_not_count(self, db, no_commit):
        org = await _org(db)
        user, admin = await _user(db, org), await _user(db, org, "admin")
        candidates = await _controls(db, 20, heavy=True, with_risks=True)
        scf = candidates[0]
        own = set(await _catalog_risks(db, scf))
        foreign = None
        for other in candidates[1:]:
            foreign = next((c for c in await _catalog_risks(db, other) if c not in own), None)
            if foreign:
                break
        if foreign is None:  # pragma: no cover - environment dependent
            pytest.skip("no catalogue risk outside the control's own")
        await _scope(db, org, scf)
        await _assess(db, org, foreign)
        await _accept(db, org, foreign, admin)
        with pytest.raises(HTTPException) as exc:
            await _create(db, org, user, scf, risk_code=foreign)
        assert exc.value.status_code == 409

    async def test_another_orgs_acceptance_does_not_count(self, db, no_commit):
        org, other = await _org(db), await _org(db)
        user = await _user(db, org)
        other_user, other_admin = await _user(db, other), await _user(db, other, "admin")
        (scf,) = await _controls(db, 1, heavy=True, with_risks=True)
        await _scope(db, org, scf)
        code = (await _catalog_risks(db, scf))[0]
        await _assess(db, other, code)
        await _accept(db, other, code, other_admin)
        await _assess(db, org, code)
        with pytest.raises(HTTPException) as exc:
            await _create(db, org, user, scf, risk_code=code)
        assert exc.value.status_code == 409
        del other_user


@requires_postgres
@pytest.mark.asyncio
class TestUpdate:
    async def test_changes_are_audited_field_by_field(self, db, no_commit):
        org = await _org(db)
        user = await _user(db, org)
        (scf,) = await _controls(db, 1, heavy=False)
        await _scope(db, org, scf)
        out = await _create(db, org, user, scf)
        editor = await _user(db, org)
        updated = await _update(db, org, editor, out.id, effectiveness_rating="minimal",
                                compensating_control="  Allow-list only  ", gap_description=out.gap_description)
        assert (updated.effectiveness_rating, updated.compensating_control) == ("minimal", "Allow-list only")
        rows = [r for r in await _audited(db, org, out.id) if r.action == "update"]
        assert {r.field_name for r in rows} == {"effectiveness_rating", "compensating_control"}
        changed = {r.field_name: r for r in rows}
        assert json.loads(changed["effectiveness_rating"].old_value) == "partial"
        assert json.loads(changed["effectiveness_rating"].new_value) == "minimal"
        assert {r.changed_by_user_id for r in rows} == {editor.id}

    @pytest.mark.parametrize("changes", [
        {},
        {"gap_description": None},
        {"compensating_control": None},
        {"effectiveness_rating": None},
    ])
    async def test_nothing_or_clearing_a_required_field_is_refused(self, db, no_commit, changes):
        org = await _org(db)
        user = await _user(db, org)
        (scf,) = await _controls(db, 1, heavy=False)
        await _scope(db, org, scf)
        out = await _create(db, org, user, scf)
        with pytest.raises(HTTPException) as exc:
            await _update(db, org, user, out.id, **changes)
        assert exc.value.status_code == 422

    async def test_a_material_control_keeps_its_justification(self, db, no_commit):
        org = await _org(db)
        user, admin = await _user(db, org), await _user(db, org, "admin")
        (scf,) = await _controls(db, 1, heavy=True)
        await _scope(db, org, scf)
        code = await _custom_risk(db, org, user, scf)
        unaccepted = await _custom_risk(db, org, user, scf)
        await _accept(db, org, code, admin)
        out = await _create(db, org, user, scf, risk_code=code)
        for bad in (None, "  ", unaccepted):
            with pytest.raises(HTTPException) as exc:
                await _update(db, org, user, out.id, risk_code=bad, effectiveness_rating="full")
            assert exc.value.status_code == 409
        row = await cc_api.get_compensating_control(org.id, out.id, membership=None, db=db)
        assert (row.risk_code, row.effectiveness_rating) == (code, "partial")
        assert not [r for r in await _audited(db, org, out.id) if r.action == "update"]

    async def test_a_changed_risk_code_is_rechecked_and_audited(self, db, no_commit):
        org = await _org(db)
        user, admin = await _user(db, org), await _user(db, org, "admin")
        (scf,) = await _controls(db, 1, heavy=True)
        await _scope(db, org, scf)
        first = await _custom_risk(db, org, user, scf)
        second = await _custom_risk(db, org, user, scf)
        await _accept(db, org, first, admin)
        await _accept(db, org, second, admin)
        out = await _create(db, org, user, scf, risk_code=first)
        updated = await _update(db, org, user, out.id, risk_code=second)
        assert (updated.risk_code, updated.risk_acceptance_state) == (second, "approved")
        (row,) = [r for r in await _audited(db, org, out.id) if r.action == "update"]
        assert (row.field_name, json.loads(row.old_value), json.loads(row.new_value)) == ("risk_code", first, second)

    async def test_an_unchanged_risk_code_is_not_rechecked(self, db, no_commit):
        """A lapsed acceptance does not block editing the record; the response shows it lapsed."""
        org = await _org(db)
        user, admin = await _user(db, org), await _user(db, org, "admin")
        (scf,) = await _controls(db, 1, heavy=True)
        await _scope(db, org, scf)
        code = await _custom_risk(db, org, user, scf)
        await _accept(db, org, code, admin)
        out = await _create(db, org, user, scf, risk_code=code)
        (await _assessment(db, org, code)).acceptance_expires_on = TODAY - timedelta(days=1)
        await db.flush()
        updated = await _update(db, org, user, out.id, risk_code=code, risk_reduction_notes="Reviewed")
        assert (updated.risk_code, updated.risk_acceptance_state) == (code, "expired")

    @pytest.mark.parametrize("cleared", [None, "  "])
    async def test_a_risk_code_can_be_cleared_on_a_control_that_is_not_material(self, db, no_commit, cleared):
        org = await _org(db)
        user = await _user(db, org)
        (scf,) = await _controls(db, 1, heavy=False)
        await _scope(db, org, scf)
        code = await _custom_risk(db, org, user, scf)
        out = await _create(db, org, user, scf, risk_code=code)
        assert (await _update(db, org, user, out.id, risk_code=cleared)).risk_code is None


@requires_postgres
@pytest.mark.asyncio
class TestDeleteAndList:
    async def test_delete_is_audited_with_what_it_said(self, db, no_commit):
        org = await _org(db)
        user = await _user(db, org)
        (scf,) = await _controls(db, 1, heavy=False)
        await _scope(db, org, scf)
        out = await _create(db, org, user, scf)
        await cc_api.delete_compensating_control(org.id, out.id, _request(), _membership(org, user), db)
        assert (await _list(db, org)) == []
        deleted = {r.field_name: r for r in await _audited(db, org, out.id) if r.action == "delete"}
        assert set(deleted) == service.COMPENSATING_TRACKED_FIELDS
        assert json.loads(deleted["gap_description"].old_value) == "No MFA on the legacy VPN"
        assert deleted["gap_description"].new_value is None
        assert deleted["gap_description"].changed_by_user_id == user.id

    async def test_filters_and_acceptance_state_per_row(self, db, no_commit):
        org = await _org(db)
        user, admin = await _user(db, org), await _user(db, org, "admin")
        a, b = await _controls(db, 2, heavy=False, distinct_domains=True)
        await _scope(db, org, a)
        await _scope(db, org, b)
        code = await _custom_risk(db, org, user, a)
        await _accept(db, org, code, admin)
        first = await _create(db, org, user, a, risk_code=code)
        second = await _create(db, org, user, b)
        await _vendor_row(db, org)
        other = await _org(db)
        await _scope(db, other, a)
        await _create(db, other, await _user(db, other), a)
        everything = await _list(db, org)
        assert {r.id for r in everything} == {first.id, second.id}
        assert {r.id: r.risk_acceptance_state for r in everything} == {first.id: "approved", second.id: None}
        assert [r.id for r in await _list(db, org, scf_id=a.lower())] == [first.id]
        assert [r.id for r in await _list(db, org, risk_code=code)] == [first.id]
        assert [r.id for r in await _list(db, org, domain=a.split("-")[0].lower())] == [first.id]

    async def test_the_acceptance_state_shown_is_this_orgs(self, db, no_commit):
        org, other = await _org(db), await _org(db)
        user = await _user(db, org)
        other_user, other_admin = await _user(db, other), await _user(db, other, "admin")
        (scf,) = await _controls(db, 1, heavy=False)
        await _scope(db, org, scf)
        code = await _custom_risk(db, org, user, scf)
        assert await _custom_risk(db, other, other_user, scf) == code
        await _accept(db, other, code, other_admin)
        out = await _create(db, org, user, scf, risk_code=code)
        assert out.risk_acceptance_state is None
        (listed,) = await _list(db, org)
        assert listed.risk_acceptance_state is None

    async def test_material_and_justified_as_things_stand(self, db, no_commit):
        org = await _org(db)
        user, admin = await _user(db, org), await _user(db, org, "admin")
        (heavy,) = await _controls(db, 1, heavy=True)
        light, flipped = await _controls(db, 2, heavy=False)
        await _scope(db, org, heavy)
        await _scope(db, org, light)
        flipped_control = await _scope(db, org, flipped)
        code = await _custom_risk(db, org, user, heavy)
        await _accept(db, org, code, admin)
        made = {
            "heavy": await _create(db, org, user, heavy, risk_code=code),
            "light": await _create(db, org, user, light),
            "flipped": await _create(db, org, user, flipped),
        }
        assert {k: (v.material, v.justified) for k, v in made.items()} == {
            "heavy": (True, True), "light": (False, True), "flipped": (False, True)}
        # The acceptance lapses; the other control becomes material by override.
        (await _assessment(db, org, code)).acceptance_expires_on = TODAY - timedelta(days=1)
        flipped_control.material_override, flipped_control.material_reason = True, "Now holds payment data"
        await db.flush()
        now = {r.id: (r.material, r.risk_acceptance_state, r.justified) for r in await _list(db, org)}
        assert now == {
            made["heavy"].id: (True, "expired", False),
            made["light"].id: (False, None, True),
            made["flipped"].id: (True, None, False),
        }

    async def test_a_risk_no_longer_linked_does_not_speak_for_it(self, db, no_commit):
        """Deleting a custom risk frees its code; a new risk with that code is not this one."""
        org = await _org(db)
        user, admin = await _user(db, org), await _user(db, org, "admin")
        scf, elsewhere = await _controls(db, 2, heavy=True)
        await _scope(db, org, scf)
        code = await _custom_risk(db, org, user, scf)
        await _accept(db, org, code, admin)
        out = await _create(db, org, user, scf, risk_code=code)
        await custom_api.delete_custom_risk(org.id, code, _request(), _membership(org, user), db)
        assert await _custom_risk(db, org, user, elsewhere) == code
        await _accept(db, org, code, admin)
        row = await cc_api.get_compensating_control(org.id, out.id, membership=None, db=db)
        assert (row.risk_code, row.risk_acceptance_state, row.justified) == (code, None, False)

    async def test_a_control_the_catalogue_does_not_know_is_material(self, db, no_commit):
        org = await _org(db)
        user = await _user(db, org)
        unknown = "X" + _uuid.uuid4().hex[:8].upper()
        row = VendorCompensatingControl(organization_id=org.id, scf_id=unknown, gap_description="g",
                                        compensating_control="c", effectiveness_rating="partial")
        db.add(row)
        await db.flush()
        got = await cc_api.get_compensating_control(org.id, row.id, membership=None, db=db)
        assert (got.material, got.justified) == (True, False)
        with pytest.raises(HTTPException) as exc:
            await _update(db, org, user, row.id, risk_code="R-ORG-1")
        assert exc.value.status_code == 409

    async def test_deleting_the_control_keeps_the_record(self, db, no_commit):
        org = await _org(db)
        user = await _user(db, org)
        (scf,) = await _controls(db, 1, heavy=False)
        control = await _scope(db, org, scf)
        out = await _create(db, org, user, scf)
        org_id = org.id
        await db.delete(control)
        await db.flush()
        db.expire_all()
        row = await cc_api.get_compensating_control(org_id, out.id, membership=None, db=db)
        assert (row.scoped_control_id, row.scf_id) == (None, scf)

    async def test_a_vendor_row_is_not_the_orgs(self, db, no_commit):
        org = await _org(db)
        user = await _user(db, org)
        _, row = await _vendor_row(db, org)
        for call in (
            lambda: cc_api.get_compensating_control(org.id, row.id, membership=None, db=db),
            lambda: _update(db, org, user, row.id, compensating_control="Changed"),
            lambda: cc_api.delete_compensating_control(org.id, row.id, _request(), _membership(org, user), db),
        ):
            with pytest.raises(HTTPException) as exc:
                await call()
            assert exc.value.status_code == 404


@requires_postgres
@pytest.mark.asyncio
class TestVendor:
    async def test_vendor_rows_carry_the_org_and_list_as_before(self, db, no_commit):
        org = await _org(db)
        user = await _user(db, org)
        vendor = Vendor(organization_id=org.id, name=f"pig-vendor-{_uuid.uuid4().hex[:6]}")
        db.add(vendor)
        await db.flush()
        (scf,) = await _controls(db, 1, heavy=False)
        await _scope(db, org, scf)
        await _create(db, org, user, scf)
        made = await vendor_api.create_compensating_control(
            org.id, vendor.id,
            VendorCompensatingControlCreate(gap_description="No SOC 2", compensating_control="On-site audit"),
            membership=_membership(org, user), db=db,
        )
        assert (made.organization_id, made.vendor_id, made.scf_id) == (org.id, vendor.id, None)
        listed = await vendor_api.list_compensating_controls(org.id, vendor.id, membership=None, db=db)
        assert [r.id for r in listed] == [made.id]
