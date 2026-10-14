"""Risk acceptance and standards exceptions (#1127, PIG R7).

Accepting a risk is a decision someone answers for. These tests pin:

* the acceptance record: only an admin, through the acceptance route, with a
  rationale and an expiry after today; the approver is the caller, never a
  payload field, and a platform key with no user gets a 403;
* the ordinary risk writes: they may still set 'accepted' (the risk page
  autosaves its whole form) but that records no approval; only an admin
  moves a risk with an approval in force out of 'accepted', which clears it;
  re-scoring an accepted risk clears it too;
* standards exceptions: a custom risk whose control link carries a document
  of the same organisation; changing the links of an accepted one clears its
  approval;
* the expiry notice: once per acceptance (a renewal is a new one), to the
  approver and the owner while they are members, or to the admins;
* the gate ``risks_accepted_without_approver_max``, which counts accepted
  risks with no approval in force and fails closed on bad params.

Risk codes are the organisation's own custom codes (R-ORG-n), created through
the real endpoint; no SCF identifier is written by the test. Everything runs
inside a transaction that is rolled back.
"""
import inspect
import json
import uuid as _uuid
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from api import custom_risks as custom_api
from api import risk_assessments as risk_api
from auth import OrgMembership
from catalog_models import SCFCatalogControl
from models import (
    AuditLog,
    CustomRiskControlMapping,
    GeneratedDocument,
    Notification,
    Organization,
    OrganizationMember,
    RiskAssessment,
    User,
)
from schemas import (
    CustomRiskControlMappingCreate,
    CustomRiskControlMappingUpdate,
    CustomRiskDefinitionCreate,
    NotificationBase,
    RiskAcceptanceRequest,
    RiskAssessmentCreate,
    RiskAssessmentUpdate,
)
from services import journey as journey_service
from services import notifications as notifications_service
from services import risk_acceptance
from services.owner_resolution import ROUTE_DIRECTED, event_for

from test_journey_service import DATABASE_URL, requires_postgres

GATE = "risks_accepted_without_approver_max"
TODAY = date.today()


# ---------------------------------------------------------------------------
# Structural: no database
# ---------------------------------------------------------------------------


def _risk(status="accepted", **kwargs):
    fields = dict(
        treatment_status=status, accepted_by=None, accepted_at=None,
        acceptance_rationale=None, acceptance_expires_on=None, owner_user_id=None,
        likelihood=2, impact=2, residual_likelihood=2, residual_impact=2,
    )
    fields.update(kwargs)
    return SimpleNamespace(**fields)


def _approved(**kwargs):
    fields = dict(
        accepted_by=_uuid.uuid4(), accepted_at=datetime.utcnow(),
        acceptance_rationale="Cost exceeds benefit", acceptance_expires_on=TODAY + timedelta(days=30),
    )
    fields.update(kwargs)
    return _risk(**fields)


@pytest.mark.parametrize("risk,state", [
    (_risk(status="treating"), None),
    (_risk(), "unapproved"),
    (_approved(accepted_by=None), "unapproved"),
    (_approved(acceptance_expires_on=TODAY - timedelta(days=1)), "expired"),
    (_approved(acceptance_expires_on=TODAY), "approved"),
    (_approved(), "approved"),
])
def test_acceptance_state(risk, state):
    assert risk_acceptance.acceptance_state(risk, TODAY) == state


def _before(risk, **changes):
    """The tracked values before a write; ``changes`` are what the write altered."""
    before = {f: getattr(risk, f) for f in (
        "treatment_status", *risk_acceptance.SCORE_FIELDS, *risk_acceptance.ACCEPTANCE_FIELDS)}
    before.update(changes)
    return before


def _cleared(risk):
    return all(getattr(risk, f) is None for f in risk_acceptance.ACCEPTANCE_FIELDS)


def test_staying_accepted_keeps_the_approval():
    stays = _approved()
    risk_acceptance.after_ordinary_write(stays, _before(stays), may_withdraw=False, today=TODAY)
    assert stays.treatment_status == "accepted" and not _cleared(stays)


def test_an_admin_leaving_accepted_clears_the_approval():
    leaves = _approved(status="treating")
    risk_acceptance.after_ordinary_write(
        leaves, _before(leaves, treatment_status="accepted"), may_withdraw=True, today=TODAY)
    assert leaves.treatment_status == "treating" and _cleared(leaves)


def test_below_admin_an_approval_in_force_keeps_the_risk_accepted():
    # Also what a stale autosave sends: the status from before the acceptance.
    stale = _approved(status="treating")
    risk_acceptance.after_ordinary_write(
        stale, _before(stale, treatment_status="accepted"), may_withdraw=False, today=TODAY)
    assert stale.treatment_status == "accepted" and not _cleared(stale)


@pytest.mark.parametrize("risk", [
    _risk(status="treating"),
    _approved(status="treating", acceptance_expires_on=TODAY - timedelta(days=1)),
    _approved(status="treating", accepted_by=None),
], ids=["unapproved", "expired", "approver-deleted"])
def test_below_admin_an_acceptance_not_in_force_may_be_left(risk):
    risk_acceptance.after_ordinary_write(
        risk, _before(risk, treatment_status="accepted"), may_withdraw=False, today=TODAY)
    assert risk.treatment_status == "treating" and _cleared(risk)


@pytest.mark.parametrize("field", risk_acceptance.SCORE_FIELDS)
@pytest.mark.parametrize("may_withdraw", [True, False])
def test_rescoring_an_accepted_risk_clears_the_approval(field, may_withdraw):
    risk = _approved(**{field: 5})
    risk_acceptance.after_ordinary_write(risk, _before(risk, **{field: 2}), may_withdraw, today=TODAY)
    assert risk.treatment_status == "accepted" and _cleared(risk)
    assert risk_acceptance.acceptance_state(risk, TODAY) == "unapproved"


def test_arriving_at_accepted_the_ordinary_way_has_no_approval():
    arrives = _risk()
    risk_acceptance.after_ordinary_write(
        arrives, _before(arrives, treatment_status="analysed"), may_withdraw=True, today=TODAY)
    assert risk_acceptance.acceptance_state(arrives, TODAY) == "unapproved"


@pytest.mark.parametrize("check", [
    {"type": GATE, "scf_ids": ["X-1"]},
    {"type": GATE, "domains": ["GOV"]},
    {"type": GATE, "min_fraction": 1},
    {"type": GATE, "max_count": -1},
    {"type": GATE, "max_count": True},
    {"type": GATE, "max_count": 1.5},
    {"type": GATE, "max_count": "0"},
])
def test_the_gate_refuses_params_it_cannot_honour(check):
    assert journey_service.check_param_problem(check)


@pytest.mark.parametrize("check", [
    {"type": GATE},
    {"type": GATE, "max_count": 0, "label": "No unapproved acceptances"},
    {"type": GATE, "max_count": 3},
])
def test_the_gate_accepts_its_own_params(check):
    assert GATE in journey_service.SUPPORTED_CHECKS
    assert journey_service.check_param_problem(check) is None


def test_an_acceptance_needs_a_rationale_and_an_expiry():
    with pytest.raises(ValidationError):
        RiskAcceptanceRequest(rationale="   ", expires_on=TODAY + timedelta(days=1))
    with pytest.raises(ValidationError):
        RiskAcceptanceRequest(rationale="Cost exceeds benefit")
    body = RiskAcceptanceRequest(rationale="  Cost exceeds benefit ", expires_on=TODAY + timedelta(days=1))
    assert body.rationale == "Cost exceeds benefit"


def test_the_expiry_notice_is_a_known_notification():
    # Marking a notification read serialises it through this schema; a type
    # missing from it made that endpoint 500 (#852 review).
    NotificationBase(
        type="risk_acceptance_expired", reference_type="risk",
        reference_id=_uuid.uuid4(), message="expired",
    )
    assert event_for("risk_acceptance_expired").routing == ROUTE_DIRECTED


def test_the_expiry_notice_goes_to_the_approver_and_owner_else_the_admins():
    approver, owner, admin = _uuid.uuid4(), _uuid.uuid4(), _uuid.uuid4()
    members = [approver, owner, admin]
    recipients = risk_acceptance.expiry_recipients
    assert recipients(approver, owner, members, [admin]) == {approver, owner}
    assert recipients(None, None, members, [admin]) == {admin}
    # Someone who has left the organisation would never see it.
    assert recipients(approver, owner, [owner, admin], [admin]) == {owner}
    assert recipients(approver, None, [admin], [admin]) == {admin}


def _min_role(handler):
    dependency = inspect.signature(handler).parameters["membership"].default.dependency
    assert dependency.__qualname__.startswith("require_org_role")
    return dict(zip(dependency.__code__.co_freevars, (c.cell_contents for c in dependency.__closure__)))["min_role"]


def test_acceptance_routes_are_registered_and_admin_only():
    routes = {
        (route.path, method)
        for route in risk_api.router.routes
        for method in getattr(route, "methods", ())
    }
    path = "/organizations/{org_id}/risk-assessments/{risk_code}/acceptance"
    assert {(path, "PUT"), (path, "DELETE")} <= routes
    assert _min_role(risk_api.put_risk_acceptance) == "admin"
    assert _min_role(risk_api.delete_risk_acceptance) == "admin"
    # The document link on an exception is the control owner's, like the link itself.
    assert _min_role(custom_api.update_custom_risk_control) == "editor"
    custom_routes = {(r.path, m) for r in custom_api.router.routes for m in getattr(r, "methods", ())}
    assert ("/organizations/{org_id}/custom-risks/{risk_code}/controls/{scf_id}", "PATCH") in custom_routes


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
        user=SimpleNamespace(db_id=str(user.id) if user else None, email=getattr(user, "email", None)),
        organization_id=org.id,
        role=role,
    )


def _request():
    return Request({"type": "http", "headers": [], "method": "PUT", "path": "/"})


async def _custom_risk(db, org, user):
    out = await custom_api.create_custom_risk(
        org.id, CustomRiskDefinitionCreate(title="Legacy VPN", description="Below the standard"),
        _request(), _membership(org, user, "editor"), db,
    )
    return out.risk_code


async def _assessment(db, org, code):
    return await db.scalar(select(RiskAssessment).where(
        RiskAssessment.organization_id == org.id, RiskAssessment.risk_code == code))


async def _audit_trail(db, org, entity_id, field):
    """(old, new) for each audit row of one field, decoded."""
    rows = (await db.execute(select(AuditLog.old_value, AuditLog.new_value).where(
        AuditLog.organization_id == org.id, AuditLog.entity_id == entity_id, AuditLog.field_name == field,
    ))).all()
    decode = lambda v: json.loads(v) if v else None  # noqa: E731
    return [(decode(r.old_value), decode(r.new_value)) for r in rows]


async def _audited(db, org, entity_id):
    rows = (await db.execute(select(AuditLog.field_name, AuditLog.new_value).where(
        AuditLog.organization_id == org.id, AuditLog.entity_id == entity_id,
    ))).all()
    return {r.field_name: json.loads(r.new_value) if r.new_value else None for r in rows}


async def _accept(db, org, user, code, days=30, rationale="Cost exceeds benefit"):
    return await risk_api.put_risk_acceptance(
        org.id, code, RiskAcceptanceRequest(rationale=rationale, expires_on=TODAY + timedelta(days=days)),
        _request(), _membership(org, user, "admin"), db,
    )


async def _gate(db, org, max_count=0):
    totals = await journey_service._in_scope_counts(db, org.id)
    return await journey_service._evaluate_one(db, org.id, {"type": GATE, "max_count": max_count}, totals)


async def _list(db, org, user, **kwargs):
    params = dict(treatment_status=None, risk_level=None, category=None, acceptance_state=None)
    params.update(kwargs)
    return await risk_api.list_risk_assessments(
        org.id, membership=_membership(org, user, "viewer"), db=db, **params,
    )


@requires_postgres
@pytest.mark.asyncio
class TestAcceptance:
    async def test_an_admin_accepts_with_the_record_and_it_is_audited(self, db, no_commit):
        org = await _org(db)
        admin = await _user(db, org, "admin")
        code = await _custom_risk(db, org, admin)
        out = await _accept(db, org, admin, code)
        assert out["treatment_status"] == "accepted"
        assert out["accepted_by"] == admin.id
        assert out["acceptance_rationale"] == "Cost exceeds benefit"
        assert out["acceptance_expires_on"] == TODAY + timedelta(days=30)
        assert out["accepted_at"] is not None and out["acceptance_state"] == "approved"
        audited = await _audited(db, org, out["id"])
        assert audited["accepted_by"] == str(admin.id)
        assert audited["acceptance_rationale"] == "Cost exceeds benefit"
        assert audited["treatment_status"] == "accepted"

    @pytest.mark.parametrize("days,rationale", [(0, "Fine"), (-1, "Fine")])
    async def test_an_expiry_must_be_after_today(self, db, no_commit, days, rationale):
        org = await _org(db)
        admin = await _user(db, org, "admin")
        code = await _custom_risk(db, org, admin)
        with pytest.raises(HTTPException) as exc:
            await _accept(db, org, admin, code, days=days, rationale=rationale)
        assert exc.value.status_code == 400 and "after today" in exc.value.detail
        assert (await _assessment(db, org, code)).treatment_status == "identified"

    @pytest.mark.parametrize("rationale", [None, "", "   "])
    async def test_the_service_refuses_a_blank_rationale(self, db, no_commit, rationale):
        # The request schema refuses it too; this is the path MCP and future callers share.
        org = await _org(db)
        admin = await _user(db, org, "admin")
        code = await _custom_risk(db, org, admin)
        with pytest.raises(ValueError, match="rationale"):
            await risk_acceptance.accept_risk(
                db, org.id, code, rationale, TODAY + timedelta(days=5), admin.id,
            )
        assert (await _assessment(db, org, code)).treatment_status == "identified"

    async def test_a_platform_key_cannot_accept_or_withdraw(self, db, no_commit):
        org = await _org(db)
        admin = await _user(db, org, "admin")
        code = await _custom_risk(db, org, admin)
        keyed = _membership(org, None, "admin")
        with pytest.raises(HTTPException) as exc:
            await risk_api.put_risk_acceptance(
                org.id, code, RiskAcceptanceRequest(rationale="x", expires_on=TODAY + timedelta(days=5)),
                _request(), keyed, db,
            )
        assert exc.value.status_code == 403
        await _accept(db, org, admin, code)
        with pytest.raises(HTTPException) as exc:
            await risk_api.delete_risk_acceptance(org.id, code, _request(), keyed, db)
        assert exc.value.status_code == 403
        assert (await _assessment(db, org, code)).accepted_by == admin.id

    async def test_another_organisations_risk_is_not_found(self, db, no_commit):
        org, other = await _org(db), await _org(db)
        admin = await _user(db, org, "admin")
        code = await _custom_risk(db, other, await _user(db, other, "admin"))
        with pytest.raises(HTTPException) as exc:
            await _accept(db, org, admin, code)
        assert exc.value.status_code == 404
        assert (await _assessment(db, other, code)).treatment_status == "identified"

    async def test_withdrawing_returns_the_risk_to_analysed_and_clears_the_record(self, db, no_commit):
        org = await _org(db)
        admin = await _user(db, org, "admin")
        code = await _custom_risk(db, org, admin)
        await _accept(db, org, admin, code)
        out = await risk_api.delete_risk_acceptance(
            org.id, code, _request(), _membership(org, admin, "admin"), db,
        )
        assert out["treatment_status"] == "analysed"
        assert all(out[f] is None for f in risk_acceptance.ACCEPTANCE_FIELDS)
        with pytest.raises(HTTPException) as exc:
            await risk_api.delete_risk_acceptance(
                org.id, code, _request(), _membership(org, admin, "admin"), db,
            )
        assert exc.value.status_code == 400

    async def test_the_db_refuses_a_record_without_a_rationale_or_expiry(self, db, no_commit):
        org = await _org(db)
        admin = await _user(db, org, "admin")
        code = await _custom_risk(db, org, admin)
        row = await _assessment(db, org, code)
        row.accepted_at, row.accepted_by = datetime.utcnow(), admin.id
        row.acceptance_rationale, row.acceptance_expires_on = " ", TODAY + timedelta(days=1)
        with pytest.raises(IntegrityError):
            async with db.begin_nested():
                await db.flush()


@requires_postgres
@pytest.mark.asyncio
class TestOrdinaryWrites:
    async def test_patch_to_accepted_records_no_approval(self, db, no_commit):
        org = await _org(db)
        editor = await _user(db, org, "editor")
        code = await _custom_risk(db, org, editor)
        out = await risk_api.update_risk_assessment(
            org.id, code, RiskAssessmentUpdate(treatment_status="accepted"),
            _request(), _membership(org, editor, "editor"), db,
        )
        assert out["treatment_status"] == "accepted"
        assert out["accepted_by"] is None and out["acceptance_state"] == "unapproved"

    async def test_an_autosave_that_keeps_accepted_keeps_the_approval(self, db, no_commit):
        org = await _org(db)
        admin = await _user(db, org, "admin")
        code = await _custom_risk(db, org, admin)
        await _accept(db, org, admin, code)
        # The risk page sends its whole form, status included, on every edit.
        out = await risk_api.update_risk_assessment(
            org.id, code, RiskAssessmentUpdate(treatment_status="accepted", notes="Reviewed"),
            _request(), _membership(org, admin, "editor"), db,
        )
        assert out["acceptance_state"] == "approved" and out["accepted_by"] == admin.id

    async def _write(self, db, org, user, role, code, via, **fields):
        if via == "patch":
            return await risk_api.update_risk_assessment(
                org.id, code, RiskAssessmentUpdate(**fields),
                _request(), _membership(org, user, role), db,
            )
        return await risk_api.create_or_update_risk_assessment(
            org.id, RiskAssessmentCreate(risk_code=code, **fields),
            _request(), _membership(org, user, role), db,
        )

    @pytest.mark.parametrize("via", ["patch", "post"])
    async def test_an_admin_leaving_accepted_clears_the_approval(self, db, no_commit, via):
        org = await _org(db)
        admin = await _user(db, org, "admin")
        code = await _custom_risk(db, org, admin)
        await _accept(db, org, admin, code)
        out = await self._write(db, org, admin, "admin", code, via, treatment_status="treating")
        assert out["treatment_status"] == "treating"
        assert all(out[f] is None for f in risk_acceptance.ACCEPTANCE_FIELDS)
        # Back to accepted the ordinary way: no approval until an admin gives one.
        out = await risk_api.update_risk_assessment(
            org.id, code, RiskAssessmentUpdate(treatment_status="accepted"),
            _request(), _membership(org, admin, "editor"), db,
        )
        assert out["acceptance_state"] == "unapproved"
        audited = await _audited(db, org, out["id"])
        assert "accepted_by" in audited

    @pytest.mark.parametrize("via", ["patch", "post"])
    async def test_an_editor_cannot_withdraw_an_approval_by_an_ordinary_write(self, db, no_commit, via):
        # Also a stale autosave: a page opened before the admin accepted
        # sends the old status back with the next edit.
        org = await _org(db)
        admin = await _user(db, org, "admin")
        editor = await _user(db, org, "editor")
        code = await _custom_risk(db, org, admin)
        await _accept(db, org, admin, code)
        out = await self._write(
            db, org, editor, "editor", code, via, treatment_status="treating", notes="Typed later")
        assert out["treatment_status"] == "accepted" and out["notes"] == "Typed later"
        assert out["acceptance_state"] == "approved" and out["accepted_by"] == admin.id

    async def test_an_editor_may_move_an_expired_acceptance_on(self, db, no_commit):
        org = await _org(db)
        admin = await _user(db, org, "admin")
        editor = await _user(db, org, "editor")
        code = await _custom_risk(db, org, admin)
        await _accept(db, org, admin, code)
        (await _assessment(db, org, code)).acceptance_expires_on = TODAY - timedelta(days=1)
        await db.flush()
        out = await self._write(db, org, editor, "editor", code, "patch", treatment_status="treating")
        assert out["treatment_status"] == "treating"
        assert all(out[f] is None for f in risk_acceptance.ACCEPTANCE_FIELDS)

    @pytest.mark.parametrize("role", ["editor", "admin"])
    async def test_rescoring_an_accepted_risk_withdraws_its_approval(self, db, no_commit, role):
        org = await _org(db)
        admin = await _user(db, org, "admin")
        writer = admin if role == "admin" else await _user(db, org, "editor")
        code = await _custom_risk(db, org, admin)
        await self._write(db, org, admin, "admin", code, "patch", residual_likelihood=2, residual_impact=2)
        await _accept(db, org, admin, code)
        out = await self._write(
            db, org, writer, role, code, "patch",
            treatment_status="accepted", residual_likelihood=5, residual_impact=5,
        )
        assert out["treatment_status"] == "accepted" and out["acceptance_state"] == "unapproved"
        assert (str(admin.id), None) in await _audit_trail(db, org, out["id"], "accepted_by")
        # An unchanged score in the autosave does not.
        await _accept(db, org, admin, code)
        out = await self._write(
            db, org, writer, role, code, "patch",
            treatment_status="accepted", residual_likelihood=5, residual_impact=5, notes="n",
        )
        assert out["acceptance_state"] == "approved"

    async def test_the_list_filters_by_acceptance_state(self, db, no_commit):
        org = await _org(db)
        admin = await _user(db, org, "admin")
        approved, unapproved, expired, open_ = [await _custom_risk(db, org, admin) for _ in range(4)]
        await _accept(db, org, admin, approved)
        await _accept(db, org, admin, expired)
        await risk_api.update_risk_assessment(
            org.id, unapproved, RiskAssessmentUpdate(treatment_status="accepted"),
            _request(), _membership(org, admin, "editor"), db,
        )
        (await _assessment(db, org, expired)).acceptance_expires_on = TODAY - timedelta(days=1)
        await db.flush()
        for state, code in (("approved", approved), ("unapproved", unapproved), ("expired", expired)):
            rows = await _list(db, org, admin, acceptance_state=state)
            assert [r["risk_code"] for r in rows] == [code]
            assert rows[0]["acceptance_state"] == state
        assert {r["risk_code"] for r in await _list(db, org, admin)} == {approved, unapproved, expired, open_}


@requires_postgres
@pytest.mark.asyncio
class TestExceptions:
    async def _linkable(self, db):
        scf_id = await db.scalar(
            select(SCFCatalogControl.scf_id).where(SCFCatalogControl.status == "active")
            .order_by(SCFCatalogControl.scf_id).limit(1)
        )
        if scf_id is None:  # pragma: no cover - environment dependent
            pytest.skip("SCF catalogue not seeded")
        return scf_id

    async def _document(self, db, org, generator="test"):
        doc = GeneratedDocument(
            organization_id=org.id, generator_name=generator, document_type="standard",
            title="Remote access standard", filename="standard.md", lifecycle_status="approved",
        )
        db.add(doc)
        await db.flush()
        return doc

    async def test_an_exception_links_the_control_and_the_standard_and_is_accepted_the_same_way(
        self, db, no_commit,
    ):
        org = await _org(db)
        admin = await _user(db, org, "admin")
        code = await _custom_risk(db, org, admin)
        scf_id = await self._linkable(db)
        doc = await self._document(db, org)
        await custom_api.add_custom_risk_control(
            org.id, code, CustomRiskControlMappingCreate(scf_id=scf_id, document_id=doc.id),
            _request(), _membership(org, admin, "editor"), db,
        )
        listed = await custom_api.list_custom_risk_controls(org.id, code, _membership(org, admin, "viewer"), db)
        assert listed["links"] == [{"scf_id": scf_id, "document_id": doc.id}]
        mapping = await db.scalar(select(CustomRiskControlMapping).where(
            CustomRiskControlMapping.organization_id == org.id, CustomRiskControlMapping.risk_code == code))
        assert (await _audited(db, org, mapping.id))["document_id"] == str(doc.id)
        out = await _accept(db, org, admin, code)
        assert out["acceptance_state"] == "approved"

    async def test_the_standard_can_be_set_and_cleared_on_an_existing_link(self, db, no_commit):
        org = await _org(db)
        editor = await _user(db, org, "editor")
        code = await _custom_risk(db, org, editor)
        scf_id = await self._linkable(db)
        doc = await self._document(db, org)
        await custom_api.add_custom_risk_control(
            org.id, code, CustomRiskControlMappingCreate(scf_id=scf_id),
            _request(), _membership(org, editor, "editor"), db,
        )
        await custom_api.update_custom_risk_control(
            org.id, code, scf_id, CustomRiskControlMappingUpdate(document_id=doc.id),
            _request(), _membership(org, editor, "editor"), db,
        )
        mapping = await db.scalar(select(CustomRiskControlMapping).where(
            CustomRiskControlMapping.organization_id == org.id, CustomRiskControlMapping.risk_code == code))
        assert mapping.document_id == doc.id
        assert (await _audited(db, org, mapping.id))["document_id"] == str(doc.id)
        await custom_api.update_custom_risk_control(
            org.id, code, scf_id, CustomRiskControlMappingUpdate(document_id=None),
            _request(), _membership(org, editor, "editor"), db,
        )
        assert mapping.document_id is None

    async def test_the_document_must_be_named_to_change_it(self, db, no_commit):
        org = await _org(db)
        editor = await _user(db, org, "editor")
        code = await _custom_risk(db, org, editor)
        scf_id = await self._linkable(db)
        doc = await self._document(db, org)
        await custom_api.add_custom_risk_control(
            org.id, code, CustomRiskControlMappingCreate(scf_id=scf_id, document_id=doc.id),
            _request(), _membership(org, editor, "editor"), db,
        )
        with pytest.raises(HTTPException) as exc:
            await custom_api.update_custom_risk_control(
                org.id, code, scf_id, CustomRiskControlMappingUpdate(),
                _request(), _membership(org, editor, "editor"), db,
            )
        assert exc.value.status_code == 422
        mapping = await db.scalar(select(CustomRiskControlMapping).where(
            CustomRiskControlMapping.organization_id == org.id, CustomRiskControlMapping.risk_code == code))
        assert mapping.document_id == doc.id

    async def test_changing_the_links_of_an_accepted_exception_withdraws_its_approval(self, db, no_commit):
        org = await _org(db)
        admin = await _user(db, org, "admin")
        editor = await _user(db, org, "editor")
        code = await _custom_risk(db, org, admin)
        scf_ids = (await db.execute(
            select(SCFCatalogControl.scf_id).where(SCFCatalogControl.status == "active")
            .order_by(SCFCatalogControl.scf_id).limit(2)
        )).scalars().all()
        if len(scf_ids) < 2:  # pragma: no cover - environment dependent
            pytest.skip("SCF catalogue not seeded")
        first, second = scf_ids
        doc, other_doc = await self._document(db, org), await self._document(db, org, "test-other")
        as_editor = _membership(org, editor, "editor")
        await custom_api.add_custom_risk_control(
            org.id, code, CustomRiskControlMappingCreate(scf_id=first, document_id=doc.id), _request(), as_editor, db)

        async def state():
            return risk_acceptance.acceptance_state(await _assessment(db, org, code), TODAY)

        await _accept(db, org, admin, code)
        # Naming the same document again changes nothing.
        await custom_api.update_custom_risk_control(
            org.id, code, first, CustomRiskControlMappingUpdate(document_id=doc.id), _request(), as_editor, db)
        assert await state() == "approved"

        await custom_api.update_custom_risk_control(
            org.id, code, first, CustomRiskControlMappingUpdate(document_id=other_doc.id), _request(), as_editor, db)
        assert await state() == "unapproved"
        row = await _assessment(db, org, code)
        assert (str(admin.id), None) in await _audit_trail(db, org, row.id, "accepted_by")

        await _accept(db, org, admin, code)
        await custom_api.add_custom_risk_control(
            org.id, code, CustomRiskControlMappingCreate(scf_id=second), _request(), as_editor, db)
        assert await state() == "unapproved"

        await _accept(db, org, admin, code)
        await custom_api.remove_custom_risk_control(org.id, code, second, _request(), as_editor, db)
        assert await state() == "unapproved"

    async def test_another_organisations_document_is_refused(self, db, no_commit):
        org, other = await _org(db), await _org(db)
        editor = await _user(db, org, "editor")
        code = await _custom_risk(db, org, editor)
        scf_id = await self._linkable(db)
        foreign = await self._document(db, other)
        with pytest.raises(HTTPException) as exc:
            await custom_api.add_custom_risk_control(
                org.id, code, CustomRiskControlMappingCreate(scf_id=scf_id, document_id=foreign.id),
                _request(), _membership(org, editor, "editor"), db,
            )
        assert exc.value.status_code == 404
        await custom_api.add_custom_risk_control(
            org.id, code, CustomRiskControlMappingCreate(scf_id=scf_id),
            _request(), _membership(org, editor, "editor"), db,
        )
        with pytest.raises(HTTPException) as exc:
            await custom_api.update_custom_risk_control(
                org.id, code, scf_id, CustomRiskControlMappingUpdate(document_id=foreign.id),
                _request(), _membership(org, editor, "editor"), db,
            )
        assert exc.value.status_code == 404

    async def test_another_organisations_link_is_not_found(self, db, no_commit):
        org, other = await _org(db), await _org(db)
        editor = await _user(db, other, "editor")
        code = await _custom_risk(db, other, editor)
        scf_id = await self._linkable(db)
        await custom_api.add_custom_risk_control(
            other.id, code, CustomRiskControlMappingCreate(scf_id=scf_id),
            _request(), _membership(other, editor, "editor"), db,
        )
        with pytest.raises(HTTPException) as exc:
            await custom_api.update_custom_risk_control(
                org.id, code, scf_id, CustomRiskControlMappingUpdate(document_id=None),
                _request(), _membership(org, await _user(db, org, "editor"), "editor"), db,
            )
        assert exc.value.status_code == 404


@requires_postgres
@pytest.mark.asyncio
class TestExpiryNotice:
    """Assertions are on this organisation's notices, never the sweep's
    global count: the sweep covers every organisation in the database."""

    async def _notices(self, db, org):
        return (await db.execute(select(
            Notification.user_id, Notification.type, Notification.reference_type, Notification.reference_id,
        ).where(Notification.organization_id == org.id))).all()

    async def _lapse(self, db, org, code, accepted_days_ago=40):
        # An acceptance that has lapsed was recorded well before today's sweep.
        row = await _assessment(db, org, code)
        row.accepted_at = datetime.utcnow() - timedelta(days=accepted_days_ago)
        row.acceptance_expires_on = TODAY - timedelta(days=1)
        await db.flush()
        return row

    async def _sweep(self, db, day=TODAY):
        return await notifications_service.check_and_notify_expired_risk_acceptances(db, day)

    async def test_an_expired_acceptance_is_announced_once_to_approver_and_owner(self, db, no_commit):
        org = await _org(db)
        admin = await _user(db, org, "admin")
        owner = await _user(db, org, "editor")
        code = await _custom_risk(db, org, admin)
        await _accept(db, org, admin, code)
        row = await self._lapse(db, org, code)
        row.owner_user_id = owner.id
        await db.flush()

        await self._sweep(db)
        notices = await self._notices(db, org)
        assert {n.user_id for n in notices} == {admin.id, owner.id}
        assert {(n.type, n.reference_type, n.reference_id) for n in notices} == {
            ("risk_acceptance_expired", "risk", row.id)}
        # Tomorrow's run stays quiet.
        await self._sweep(db, TODAY + timedelta(days=1))
        assert len(await self._notices(db, org)) == 2

    async def test_an_approver_who_chose_immediate_email_is_emailed(self, db, no_commit, monkeypatch):
        org = await _org(db)
        admin = await _user(db, org, "admin")
        admin.email_notifications_enabled, admin.notification_frequency = True, "immediate"
        code = await _custom_risk(db, org, admin)
        await _accept(db, org, admin, code)
        await self._lapse(db, org, code)
        sent = []

        async def fake_send(**kwargs):
            sent.append(kwargs)

        monkeypatch.setattr(notifications_service, "send_event_notification_email", fake_send)
        await self._sweep(db)
        mine = [m for m in sent if m["to_email"] == admin.email]
        assert len(mine) == 1 and code in mine[0]["subject"]
        assert mine[0]["event_type"] == "risk_acceptance_expired"

    async def test_a_renewal_made_the_day_of_the_notice_is_announced_when_it_lapses(self, db, no_commit):
        org = await _org(db)
        admin = await _user(db, org, "admin")
        code = await _custom_risk(db, org, admin)
        await _accept(db, org, admin, code)
        await self._lapse(db, org, code)
        await self._sweep(db)
        assert len(await self._notices(db, org)) == 1
        # The admin renews the same day, the reaction the notice asks for.
        await _accept(db, org, admin, code, days=10)
        await self._sweep(db)
        assert len(await self._notices(db, org)) == 1
        # The renewal lapses in its turn: a new notice.
        (await _assessment(db, org, code)).acceptance_expires_on = TODAY - timedelta(days=1)
        await db.flush()
        await self._sweep(db, TODAY + timedelta(days=11))
        assert len(await self._notices(db, org)) == 2

    async def test_an_acceptance_in_force_or_an_unapproved_one_is_not_announced(self, db, no_commit):
        org = await _org(db)
        admin = await _user(db, org, "admin")
        live, loose = await _custom_risk(db, org, admin), await _custom_risk(db, org, admin)
        await _accept(db, org, admin, live)
        await risk_api.update_risk_assessment(
            org.id, loose, RiskAssessmentUpdate(treatment_status="accepted"),
            _request(), _membership(org, admin, "editor"), db,
        )
        await self._sweep(db)
        assert await self._notices(db, org) == []

    async def test_a_stray_expiry_without_an_acceptance_record_is_skipped(self, db, no_commit, caplog):
        # No write path leaves an expiry without accepted_at; a hand-edited row
        # must neither be announced nor stop the run for everyone else.
        org = await _org(db)
        admin = await _user(db, org, "admin")
        stray, real = await _custom_risk(db, org, admin), await _custom_risk(db, org, admin)
        row = await _assessment(db, org, stray)
        row.treatment_status, row.acceptance_expires_on = "accepted", TODAY - timedelta(days=1)
        await _accept(db, org, admin, real)
        lapsed = await self._lapse(db, org, real)
        with caplog.at_level("ERROR"):
            await self._sweep(db)
        assert "Failed to check" not in caplog.text
        assert {n.reference_id for n in await self._notices(db, org)} == {lapsed.id}

    async def test_with_no_approver_or_owner_left_the_admins_hear_it(self, db, no_commit):
        org = await _org(db)
        approver = await _user(db, org, "admin")
        other_admin = await _user(db, org, "admin")
        code = await _custom_risk(db, org, approver)
        await _accept(db, org, approver, code)
        row = await self._lapse(db, org, code)
        row.accepted_by = None  # the approver's account was deleted
        await db.flush()
        await self._sweep(db)
        assert {n.user_id for n in await self._notices(db, org)} == {approver.id, other_admin.id}

    async def test_an_approver_who_left_the_organisation_is_replaced_by_the_admins(self, db, no_commit):
        org = await _org(db)
        approver = await _user(db, org, "admin")
        admin = await _user(db, org, "admin")
        code = await _custom_risk(db, org, approver)
        await _accept(db, org, approver, code)
        await self._lapse(db, org, code)
        member = await db.scalar(select(OrganizationMember).where(
            OrganizationMember.organization_id == org.id, OrganizationMember.user_id == approver.id))
        await db.delete(member)  # removed from the organisation, account kept
        await db.flush()
        await self._sweep(db)
        assert {n.user_id for n in await self._notices(db, org)} == {admin.id}


@requires_postgres
@pytest.mark.asyncio
class TestGate:
    async def test_no_accepted_risks_meets_the_gate(self, db, no_commit):
        org = await _org(db)
        await _custom_risk(db, org, await _user(db, org, "admin"))
        out = await _gate(db, org)
        assert out["met"] is True and out["detail"].startswith("0 accepted")

    async def test_it_counts_unapproved_and_expired_acceptances_but_not_approved_ones(self, db, no_commit):
        org = await _org(db)
        admin = await _user(db, org, "admin")
        approved, unapproved, expired = [await _custom_risk(db, org, admin) for _ in range(3)]
        await _accept(db, org, admin, approved)
        assert (await _gate(db, org))["met"] is True

        await risk_api.update_risk_assessment(
            org.id, unapproved, RiskAssessmentUpdate(treatment_status="accepted"),
            _request(), _membership(org, admin, "editor"), db,
        )
        out = await _gate(db, org)
        assert out["met"] is False and out["detail"].startswith("1 accepted")
        assert (await _gate(db, org, max_count=1))["met"] is True

        await _accept(db, org, admin, expired)
        (await _assessment(db, org, expired)).acceptance_expires_on = TODAY - timedelta(days=1)
        await db.flush()
        assert (await _gate(db, org, max_count=1))["detail"].startswith("2 accepted")

        # An approver whose account was deleted no longer answers for it.
        (await _assessment(db, org, approved)).accepted_by = None
        await db.flush()
        assert (await _gate(db, org, max_count=2))["met"] is False

    async def test_an_approver_without_a_recorded_acceptance_still_counts(self, db, no_commit):
        # A hand-edited row naming an approver but no accepted_at is not an approval.
        org = await _org(db)
        admin = await _user(db, org, "admin")
        code = await _custom_risk(db, org, admin)
        row = await _assessment(db, org, code)
        row.treatment_status, row.accepted_by = "accepted", admin.id
        row.acceptance_expires_on = TODAY + timedelta(days=30)
        await db.flush()
        assert (await _gate(db, org))["detail"].startswith("1 accepted")

    async def test_another_organisations_risks_do_not_count(self, db, no_commit):
        org, other = await _org(db), await _org(db)
        admin = await _user(db, other, "admin")
        code = await _custom_risk(db, other, admin)
        await risk_api.update_risk_assessment(
            other.id, code, RiskAssessmentUpdate(treatment_status="accepted"),
            _request(), _membership(other, admin, "editor"), db,
        )
        assert (await _gate(db, other))["met"] is False
        assert (await _gate(db, org))["met"] is True

    async def test_a_bad_param_reads_unknown(self, db, no_commit):
        org = await _org(db)
        totals = await journey_service._in_scope_counts(db, org.id)
        out = await journey_service._evaluate_one(db, org.id, {"type": GATE, "max_count": -1}, totals)
        assert out["met"] is None
