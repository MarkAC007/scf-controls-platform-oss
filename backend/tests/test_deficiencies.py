"""Control deficiency record (#1128, PIG R8).

A deficiency is an organisation's action item with no vendor. These tests pin:

* the record: raised by hand against a control the organisation scoped, a
  risk it assessed, or both; never another organisation's; owned by a member;
  written to the audit log; never deleted;
* closing: completing needs a retest note (the endpoint and the database both
  refuse one without); reopening clears the note, who closed it and when;
  cancelling needs none;
* the three automatic triggers (a control going at risk through any of the
  scoped-control writes, a composite going insufficient, a reviewer rejecting
  evidence) and that each raises once while one stands;
* the per-control and per-domain view;
* vendor action items, which carry the organisation now and are otherwise
  unchanged;
* the gates ``open_deficiencies_max`` and ``material_deficiencies_max``,
  which fail closed on bad params and on an unknown domain.

Control and evidence ids are read from the catalogue at run time; risk codes
are the organisation's own custom codes (R-ORG-n). Everything runs inside a
transaction that is rolled back.
"""
import inspect
import json
import uuid as _uuid
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

import tasks_vendor_assessment as tva
from api import custom_risks as custom_api
from api import deficiencies as deficiency_api
from api import scoped_controls as scoped_api
from api import vendors as vendor_api
from auth import OrgMembership
from catalog_models import SCFCatalogControl, SCFCatalogEvidence
from middleware.audit_middleware import _extract_entity_type
from models import (
    AuditLog,
    Organization,
    OrganizationMember,
    ScopedControl,
    User,
    Vendor,
    VendorActionItem,
)
from schemas import (
    BatchScopedControlOperation,
    BatchScopedControlRequest,
    CustomRiskDefinitionCreate,
    DeficiencyCreate,
    DeficiencyUpdate,
    ScopedControlCreate,
    ScopedControlUpdate,
    VendorActionItemCreate,
)
from services import composite_service, deficiencies, materiality
from services import journey as journey_service

from test_journey_service import DATABASE_URL, requires_postgres  # noqa: F401
from test_notification_triggers import (  # noqa: F401 — fixtures
    _make_evidence_file,
    membership,
    mock_db,
    org_id,
    user_id,
)

TODAY = date.today()


# ---------------------------------------------------------------------------
# Structural: no database
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status,note,refused", [
    ("completed", None, True),
    ("completed", "   ", True),
    ("completed", "Retested 12 Oct: rule now blocks the port", False),
    ("cancelled", None, False),
    ("open", None, False),
    ("in_progress", None, False),
])
def test_completing_needs_a_retest_note(status, note, refused):
    assert (deficiencies.closure_problem(status, note) is not None) is refused


def _item(status="open", **kwargs):
    fields = dict(status=status, closed_by_user_id=None, completed_date=None, retest_note=None)
    fields.update(kwargs)
    return SimpleNamespace(**fields)


def test_completing_records_who_and_when():
    closer = _uuid.uuid4()
    item = _item(retest_note="Retested")
    deficiencies.apply_status(item, "completed", closer, TODAY)
    assert (item.status, item.closed_by_user_id, item.completed_date) == ("completed", closer, TODAY)


def test_cancelling_records_who_but_no_completion_date():
    closer = _uuid.uuid4()
    item = _item()
    deficiencies.apply_status(item, "cancelled", closer, TODAY)
    assert (item.closed_by_user_id, item.completed_date) == (closer, None)


@pytest.mark.parametrize("closed", ["completed", "cancelled"])
def test_reopening_clears_the_closure_and_the_retest_note(closed):
    item = _item(closed, closed_by_user_id=_uuid.uuid4(), completed_date=TODAY, retest_note="Retested")
    deficiencies.apply_status(item, "in_progress", _uuid.uuid4(), TODAY)
    assert (item.closed_by_user_id, item.completed_date, item.retest_note) == (None, None, None)


def test_closing_again_keeps_the_original_closer():
    first = _uuid.uuid4()
    item = _item("completed", closed_by_user_id=first, completed_date=TODAY, retest_note="Retested")
    deficiencies.apply_status(item, "completed", _uuid.uuid4(), TODAY)
    assert item.closed_by_user_id == first


GATES = ("open_deficiencies_max", "material_deficiencies_max")


@pytest.mark.parametrize("gate", GATES)
@pytest.mark.parametrize("extra", [
    {"max_count": -1},
    {"max_count": True},
    {"max_count": 1.5},
    {"max_count": "0"},
    {"domains": []},
    {"domains": [""]},
    {"domains": "NET"},
    {"scf_ids": ["x"]},
    {"min_fraction": 1.0},
    {"statuses": ["at_risk"]},
    {"basis": "mcr"},
])
def test_the_gates_refuse_params_they_cannot_honour(gate, extra):
    check = {"type": gate, **extra}
    assert journey_service.check_param_problem(check) is not None


@pytest.mark.parametrize("check", [
    {"type": "open_deficiencies_max", "domains": ["NET", "CHG"], "max_count": 0},
    {"type": "material_deficiencies_max", "max_count": 0},
    {"type": "material_deficiencies_max", "label": "No material gaps", "max_count": 2, "domains": ["NET"]},
])
def test_the_gates_take_the_issue_examples(check):
    assert journey_service.check_param_problem(check) is None
    assert journey_service.unsupported_checks({"stages": [{"precondition_spec": [check]}]}) == set()


def _min_role(handler):
    dependency = inspect.signature(handler).parameters["membership"].default.dependency
    assert dependency.__qualname__.startswith("require_org_role")
    return dict(zip(dependency.__code__.co_freevars, (c.cell_contents for c in dependency.__closure__)))["min_role"]


def test_the_routes_are_registered_with_their_roles_and_no_delete():
    routes = {(r.path, m) for r in deficiency_api.router.routes for m in getattr(r, "methods", ())}
    base = "/organizations/{org_id}/deficiencies"
    assert routes == {
        (base, "GET"), (base, "POST"), (f"{base}/summary", "GET"),
        (f"{base}/{{deficiency_id}}", "GET"), (f"{base}/{{deficiency_id}}", "PATCH"),
    }
    assert _min_role(deficiency_api.list_deficiencies) == "viewer"
    assert _min_role(deficiency_api.deficiency_summary) == "viewer"
    assert _min_role(deficiency_api.get_deficiency) == "viewer"
    assert _min_role(deficiency_api.create_deficiency) == "editor"
    assert _min_role(deficiency_api.update_deficiency) == "editor"


def test_the_router_is_mounted_and_audited_as_deficiency():
    import main

    paths = main.app.openapi()["paths"]
    base = "/api/organizations/{org_id}/deficiencies"
    assert {"get", "post"} <= set(paths.get(base, {}))
    assert "get" in paths.get(f"{base}/summary", {})
    assert {"get", "patch"} <= set(paths.get(f"{base}/{{deficiency_id}}", {}))
    assert _extract_entity_type(f"/api/organizations/{_uuid.uuid4()}/deficiencies/x") == "deficiency"


# ---------------------------------------------------------------------------
# Trigger wiring on the review endpoints and the composite worker (mocked)
# ---------------------------------------------------------------------------


class TestReviewWiring:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("status,raised", [("rejected", True), ("approved", False), ("needs_revision", False)])
    @patch("api.evidence_files.get_assurance_policy", new_callable=AsyncMock)
    @patch("api.evidence_files.on_evidence_rejected", new_callable=AsyncMock)
    @patch("api.evidence_files.create_evidence_rejected_notifications", new_callable=AsyncMock)
    @patch("api.evidence_files.log_entity_changes", new_callable=AsyncMock)
    async def test_a_file_rejection_raises_deficiencies(
        self, mock_audit, mock_notify, mock_raise, mock_policy, status, raised,
        membership, mock_db, org_id, user_id, monkeypatch,  # noqa: F811
    ):
        from api.evidence_files import review_evidence_file
        from schemas import EvidenceFileReviewRequest

        monkeypatch.setenv("ENABLE_PER_WINDOW_REVIEW", "false")
        file_id = _uuid.uuid4()
        ef = _make_evidence_file(file_id, org_id, _uuid.uuid4(), membership.user)
        result = MagicMock()
        result.unique.return_value.scalar_one_or_none.return_value = ef
        mock_db.execute.return_value = result
        mock_db.refresh = AsyncMock()
        order = []
        mock_raise.side_effect = lambda *a, **k: order.append("raise")
        mock_db.commit.side_effect = lambda: order.append("commit")

        await review_evidence_file(
            org_id=org_id, evidence_id="EVID0001", file_id=file_id,
            body=EvidenceFileReviewRequest(review_status=status, review_notes="n"),
            request=MagicMock(), membership=membership, db=mock_db,
        )
        if raised:
            mock_raise.assert_awaited_once()
            args = mock_raise.call_args.args
            assert args[:4] == (mock_db, org_id, "EVID0001", user_id)
            assert order == ["raise", "commit"], "raised inside the review's own transaction"
        else:
            mock_raise.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status,raised", [("rejected", True), ("approved", False), ("not_reviewed", False)])
    @patch("api.evidence_window_assessment.on_evidence_rejected", new_callable=AsyncMock)
    @patch("api.evidence_window_assessment.create_evidence_rejected_notifications", new_callable=AsyncMock)
    @patch("api.evidence_window_assessment.log_entity_changes", new_callable=AsyncMock)
    @patch("api.evidence_window_assessment.assess_window_task")
    async def test_a_window_rejection_raises_deficiencies(
        self, mock_task, mock_audit, mock_notify, mock_raise, status, raised,
        membership, mock_db, org_id, user_id,  # noqa: F811
    ):
        from api.evidence_window_assessment import review_window_assessment
        from schemas import WindowAssessmentReviewRequest

        ewa = MagicMock()
        ewa.id, ewa.organization_id, ewa.evidence_id = _uuid.uuid4(), org_id, "EVID0002"
        ewa.review_status, ewa.file_ids = "not_reviewed", []
        result = MagicMock()
        result.scalar_one_or_none.return_value = ewa
        mock_db.execute.return_value = result
        order = []
        mock_raise.side_effect = lambda *a, **k: order.append("raise")
        mock_db.commit.side_effect = lambda: order.append("commit")

        await review_window_assessment(
            org_id=org_id, ewa_id=ewa.id, body=WindowAssessmentReviewRequest(review_status=status),
            request=MagicMock(), membership=membership, db=mock_db,
        )
        if raised:
            mock_raise.assert_awaited_once()
            assert mock_raise.call_args.args[:4] == (mock_db, org_id, "EVID0002", user_id)
            assert order == ["raise", "commit"]
        else:
            mock_raise.assert_not_called()


class TestCompositeWiring:
    def _run(self, monkeypatch, prior, new="insufficient", raise_error=False):
        calls = []
        session = MagicMock()
        session.query.return_value.filter.return_value.scalar.return_value = prior
        session.commit.side_effect = lambda: calls.append("commit")
        session.rollback.side_effect = lambda: calls.append("rollback")
        monkeypatch.setattr(composite_service, "_claim_idempotency", lambda *a, **k: True)
        monkeypatch.setattr(composite_service, "_get_sync_session", lambda: session)
        monkeypatch.setattr(
            composite_service, "_compute_composite",
            lambda *a: {"composite_status": new, "included_window_ids": []},
        )
        monkeypatch.setattr(
            composite_service, "_upsert_composite",
            lambda *a: SimpleNamespace(id=_uuid.uuid4(), composite_status=new, composite_score=None),
        )

        def _raise(s, org, scf):
            calls.append(("raise", org, scf))
            if raise_error:
                raise RuntimeError("boom")
        monkeypatch.setattr(composite_service, "on_composite_insufficient_sync", _raise)
        monkeypatch.setattr(
            composite_service, "create_composite_insufficient_notifications_sync",
            lambda *a: calls.append("notify"),
        )
        org = _uuid.uuid4()
        out = composite_service.recompute_control_composite_task.apply(
            kwargs={"organization_id": str(org), "scf_id": "CTRL-X", "trigger_event": "after_commit"},
        ).get()
        return out, calls, org

    def test_going_insufficient_raises_one_and_commits_it(self, monkeypatch):
        out, calls, org = self._run(monkeypatch, prior="sufficient")
        assert out["composite_status"] == "insufficient"
        assert calls == ["commit", ("raise", org, "CTRL-X"), "commit", "notify"]

    @pytest.mark.parametrize("prior,new", [("insufficient", "insufficient"), ("sufficient", "partial")])
    def test_no_transition_raises_nothing(self, monkeypatch, prior, new):
        _, calls, _ = self._run(monkeypatch, prior=prior, new=new)
        assert not any(isinstance(c, tuple) for c in calls)

    def test_a_failure_rolls_back_and_the_composite_stands(self, monkeypatch):
        out, calls, org = self._run(monkeypatch, prior=None, raise_error=True)
        assert out["composite_status"] == "insufficient"
        assert calls == ["commit", ("raise", org, "CTRL-X"), "rollback", "notify"]


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


async def _controls(db, n, heavy=None, distinct_domains=False):
    """``n`` active catalogue controls; heavy=True material by weighting, False not."""
    query = select(SCFCatalogControl.scf_id).where(SCFCatalogControl.status == "active")
    weight = SCFCatalogControl.control_weighting
    if heavy is True:
        query = query.where(weight >= materiality.MATERIAL_WEIGHTING)
    elif heavy is False:
        query = query.where(weight.between(1, materiality.MATERIAL_WEIGHTING - 1))
    ids = list((await db.execute(query.order_by(SCFCatalogControl.scf_id))).scalars().all())
    if distinct_domains:
        seen, picked = set(), []
        for v in ids:
            if v.split("-", 1)[0] not in seen:
                seen.add(v.split("-", 1)[0])
                picked.append(v)
        ids = picked
    if len(ids) < n:  # pragma: no cover - environment dependent
        pytest.skip(f"catalogue has fewer than {n} suitable controls")
    return ids[:n]


async def _scope(db, org, scf_id, **kwargs):
    fields = dict(selected=True, implementation_status="not_started")
    fields.update(kwargs)
    row = ScopedControl(organization_id=org.id, scf_id=scf_id, **fields)
    db.add(row)
    await db.flush()
    return row


async def _rows(db, org):
    return list((await db.execute(
        select(VendorActionItem).where(
            VendorActionItem.organization_id == org.id, VendorActionItem.vendor_id.is_(None),
        ).order_by(VendorActionItem.created_at, VendorActionItem.title)
    )).scalars().all())


async def _audited(db, org, entity_id):
    rows = (await db.execute(select(AuditLog).where(
        AuditLog.organization_id == org.id, AuditLog.entity_id == entity_id,
    ))).scalars().all()
    return rows


async def _create(db, org, user, **kwargs):
    fields = dict(title="Firewall rule review overdue")
    fields.update(kwargs)
    return await deficiency_api.create_deficiency(
        org.id, DeficiencyCreate(**fields), _request(), _membership(org, user), db,
    )


async def _update(db, org, user, deficiency_id, **kwargs):
    return await deficiency_api.update_deficiency(
        org.id, deficiency_id, DeficiencyUpdate(**kwargs), _request(), _membership(org, user), db,
    )


async def _custom_risk(db, org, user):
    out = await custom_api.create_custom_risk(
        org.id, CustomRiskDefinitionCreate(title="Legacy VPN", description="Below the standard"),
        _request(), _membership(org, user), db,
    )
    return out.risk_code


async def _noise(db, org, scf):
    """What must not count for ``org``: another org's deficiency, and a vendor item."""
    other = await _org(db)
    await _scope(db, other, scf)
    await _create(db, other, await _user(db, other), scf_id=scf)
    vendor = Vendor(organization_id=org.id, name=f"pig-vendor-{_uuid.uuid4().hex[:6]}")
    db.add(vendor)
    await db.flush()
    db.add(VendorActionItem(organization_id=org.id, vendor_id=vendor.id, title="Sign the DPA",
                            priority="high", status="open"))
    await db.flush()


async def _gate(db, org, **check):
    totals = await journey_service._in_scope_counts(db, org.id)
    return await journey_service._evaluate_one(db, org.id, check, totals)


@pytest.mark.parametrize("title", ["", "   "])
def test_a_blank_title_is_refused(title):
    with pytest.raises(ValueError):
        DeficiencyCreate(title=title, risk_code="R-ORG-1")
    with pytest.raises(ValueError):
        DeficiencyUpdate(title=title)


@requires_postgres
@pytest.mark.asyncio
class TestRecord:
    async def test_raised_against_a_scoped_control_and_audited(self, db, no_commit):
        org = await _org(db)
        user = await _user(db, org)
        (scf,) = await _controls(db, 1)
        control = await _scope(db, org, scf)
        out = await _create(db, org, user, scf_id=scf.lower(), priority="high", owner_user_id=user.id,
                            due_date=TODAY)
        assert (out.scoped_control_id, out.scf_id, out.domain) == (control.id, scf, scf.split("-")[0])
        assert (out.status, out.auto_generated, out.source) == ("open", False, None)
        audit = {r.field_name: r for r in await _audited(db, org, out.id)}
        assert {r.action for r in audit.values()} == {"create"}
        assert json.loads(audit["title"].new_value) == "Firewall rule review overdue"
        assert json.loads(audit["scoped_control_id"].new_value) == str(control.id)
        assert audit["title"].changed_by_user_id == user.id
        assert audit["title"].entity_type == "deficiency"

    async def test_raised_against_a_risk_alone(self, db, no_commit):
        org = await _org(db)
        user = await _user(db, org)
        code = await _custom_risk(db, org, user)
        out = await _create(db, org, user, risk_code=code)
        assert (out.risk_code, out.scoped_control_id, out.scf_id, out.domain) == (code, None, None, None)

    @pytest.mark.parametrize("bad", [
        {},
        {"scf_id": "  ", "risk_code": " "},
        {"scf_id": "unscoped"},
        {"risk_code": "R-ORG-999"},
        {"scf_id": "other-org"},
        {"scf_id": "descoped"},
        {"risk_code": "other-risk"},
    ])
    async def test_refused_without_something_of_this_org_to_stand_against(self, db, no_commit, bad):
        org, other = await _org(db), await _org(db)
        user = await _user(db, org)
        scoped, unscoped, foreign, descoped = await _controls(db, 4)
        await _scope(db, org, scoped)
        await _scope(db, org, descoped, selected=False)
        await _scope(db, other, foreign)
        outsider = await _user(db, other)
        names = {"unscoped": unscoped, "other-org": foreign, "descoped": descoped,
                 "other-risk": await _custom_risk(db, other, outsider)}
        fields = {k: names.get(v, v) for k, v in bad.items()}
        with pytest.raises(HTTPException) as err:
            await _create(db, org, user, **fields)
        assert err.value.status_code == 422
        assert await _rows(db, org) == []

    async def test_an_owner_from_another_org_is_not_found(self, db, no_commit):
        org, other = await _org(db), await _org(db)
        user = await _user(db, org)
        (scf,) = await _controls(db, 1)
        await _scope(db, org, scf)
        with pytest.raises(HTTPException) as err:
            await _create(db, org, user, scf_id=scf, owner_user_id=(await _user(db, other)).id)
        assert err.value.status_code == 404
        assert await _rows(db, org) == []

    async def test_another_orgs_deficiency_is_not_found(self, db, no_commit):
        org, other = await _org(db), await _org(db)
        (scf,) = await _controls(db, 1)
        await _scope(db, org, scf)
        out = await _create(db, org, await _user(db, org), scf_id=scf)
        intruder = await _user(db, other)
        for call in (
            deficiency_api.get_deficiency(other.id, out.id, _membership(other, intruder), db),
            _update(db, other, intruder, out.id, title="mine now"),
        ):
            with pytest.raises(HTTPException) as err:
                await call
            assert err.value.status_code == 404

    async def test_vendor_items_and_deficiencies_stay_apart(self, db, no_commit):
        org = await _org(db)
        user = await _user(db, org)
        vendor = Vendor(organization_id=org.id, name=f"pig-vendor-{_uuid.uuid4().hex[:6]}")
        db.add(vendor)
        await db.flush()
        item = await vendor_api.create_action_item(
            org.id, vendor.id, VendorActionItemCreate(title="Sign the DPA"), _membership(org, user), db,
        )
        assert item.organization_id == org.id
        with pytest.raises(HTTPException) as err:
            await deficiency_api.get_deficiency(org.id, item.id, _membership(org, user), db)
        assert err.value.status_code == 404
        assert await deficiency_api.list_deficiencies(
            org.id, None, False, None, None, None, _membership(org, user), db) == []
        # A vendor item still completes without a retest note.
        item.status = "completed"
        await db.flush()

        (scf,) = await _controls(db, 1)
        await _scope(db, org, scf)
        own = await _create(db, org, user, scf_id=scf)
        listed = await vendor_api.list_all_vendor_action_items(org.id, None, None, _membership(org, user), db)
        assert [i.id for i in listed] == [item.id]
        assert own.id not in {i.id for i in listed}

    async def test_the_vendor_assessment_insert_carries_the_vendor_org(self, db, no_commit, monkeypatch):
        org = await _org(db)
        vendor = Vendor(organization_id=org.id, name=f"pig-vendor-{_uuid.uuid4().hex[:6]}")
        db.add(vendor)
        await db.flush()
        from test_tasks_vendor_assessment import FakeSession, _sample_result

        fake = FakeSession()
        monkeypatch.setattr(tva, "_get_sync_session", lambda: fake)
        from datetime import datetime
        tva._persist_completed(job_id="j", vendor_id=str(vendor.id), result=_sample_result(),
                               completed_at=datetime(2026, 7, 11))
        sql, params = next(c for c in fake.calls if "INSERT INTO vendor_action_items" in c[0])
        await db.execute(text(sql), {**params, "assessment_id": None})
        row = (await db.execute(
            select(VendorActionItem).where(VendorActionItem.vendor_id == vendor.id)
        )).scalar_one()
        assert (row.organization_id, row.auto_generated, row.status) == (org.id, True, "open")


@requires_postgres
@pytest.mark.asyncio
class TestClosing:
    async def _open(self, db):
        org = await _org(db)
        user = await _user(db, org)
        (scf,) = await _controls(db, 1)
        await _scope(db, org, scf)
        return org, user, await _create(db, org, user, scf_id=scf)

    @pytest.mark.parametrize("note", [None, "  "])
    async def test_completing_without_a_retest_note_is_refused_and_changes_nothing(self, db, no_commit, note):
        org, user, out = await self._open(db)
        before = len(await _audited(db, org, out.id))
        fields = {"status": "completed", "title": "renamed"}
        if note is not None:
            fields["retest_note"] = note
        with pytest.raises(HTTPException) as err:
            await _update(db, org, user, out.id, **fields)
        assert err.value.status_code == 422
        row = (await _rows(db, org))[0]
        assert (row.status, row.title, row.closed_by_user_id) == ("open", "Firewall rule review overdue", None)
        assert len(await _audited(db, org, out.id)) == before

    async def test_completing_with_a_note_records_the_closure(self, db, no_commit):
        org, user, out = await self._open(db)
        closer = await _user(db, org)
        done = await _update(db, org, closer, out.id, status="completed",
                             retest_note="  Retested: rule blocks the port  ")
        assert (done.status, done.retest_note) == ("completed", "Retested: rule blocks the port")
        assert (done.closed_by_user_id, done.completed_date) == (closer.id, TODAY)
        audit = {(r.field_name, r.action): r for r in await _audited(db, org, out.id)}
        assert json.loads(audit[("status", "update")].new_value) == "completed"
        assert json.loads(audit[("retest_note", "update")].new_value) == "Retested: rule blocks the port"
        assert audit[("status", "update")].changed_by_user_id == closer.id

    async def test_a_note_given_earlier_is_enough_to_complete(self, db, no_commit):
        org, user, out = await self._open(db)
        await _update(db, org, user, out.id, retest_note="Retested")
        assert (await _update(db, org, user, out.id, status="completed")).status == "completed"

    async def test_reopening_needs_a_fresh_retest_to_close_again(self, db, no_commit):
        org, user, out = await self._open(db)
        await _update(db, org, user, out.id, status="completed", retest_note="Retested")
        reopened = await _update(db, org, user, out.id, status="open")
        assert (reopened.retest_note, reopened.closed_by_user_id, reopened.completed_date) == (None, None, None)
        with pytest.raises(HTTPException):
            await _update(db, org, user, out.id, status="completed")

    async def test_a_note_sent_with_the_reopening_is_not_kept(self, db, no_commit):
        org, user, out = await self._open(db)
        await _update(db, org, user, out.id, status="completed", retest_note="Retested")
        reopened = await _update(db, org, user, out.id, status="in_progress", retest_note="Retested")
        assert (reopened.status, reopened.retest_note) == ("in_progress", None)
        with pytest.raises(HTTPException) as err:
            await _update(db, org, user, out.id, status="completed")
        assert err.value.status_code == 422
        assert (await _update(db, org, user, out.id, status="completed",
                              retest_note="Retested again")).retest_note == "Retested again"

    async def test_completed_then_cancelled(self, db, no_commit):
        org, user, out = await self._open(db)
        canceller = await _user(db, org)
        await _update(db, org, user, out.id, status="completed", retest_note="Retested")
        done = await _update(db, org, canceller, out.id, status="cancelled")
        assert (done.status, done.completed_date, done.closed_by_user_id, done.retest_note) == (
            "cancelled", None, canceller.id, "Retested")

    async def test_a_completed_one_cannot_lose_its_note(self, db, no_commit):
        org, user, out = await self._open(db)
        await _update(db, org, user, out.id, status="completed", retest_note="Retested")
        with pytest.raises(HTTPException) as err:
            await _update(db, org, user, out.id, retest_note="")
        assert err.value.status_code == 422

    async def test_cancelling_needs_no_note(self, db, no_commit):
        org, user, out = await self._open(db)
        done = await _update(db, org, user, out.id, status="cancelled")
        assert (done.status, done.closed_by_user_id, done.completed_date) == ("cancelled", user.id, None)

    @pytest.mark.parametrize("fields", [{}, {"status": None}, {"title": None}, {"priority": None}])
    async def test_refused_updates(self, db, no_commit, fields):
        org, user, out = await self._open(db)
        with pytest.raises(HTTPException) as err:
            await _update(db, org, user, out.id, **fields)
        assert err.value.status_code == 422

    async def test_the_owner_must_be_a_member(self, db, no_commit):
        org, user, out = await self._open(db)
        outsider = await _user(db, await _org(db))
        with pytest.raises(HTTPException) as err:
            await _update(db, org, user, out.id, owner_user_id=outsider.id)
        assert err.value.status_code == 404
        member = await _user(db, org, "viewer")
        assert (await _update(db, org, user, out.id, owner_user_id=member.id)).owner_user_id == member.id

    async def test_the_database_refuses_a_completed_deficiency_without_a_note(self, db):
        org = await _org(db)
        nested = await db.begin_nested()
        db.add(VendorActionItem(organization_id=org.id, title="x", priority="medium", status="completed"))
        with pytest.raises(IntegrityError, match="ck_vendor_action_items_deficiency_retest"):
            await db.flush()
        await nested.rollback()


@requires_postgres
@pytest.mark.asyncio
class TestAtRisk:
    async def test_a_patch_to_at_risk_raises_one_against_the_control(self, db, no_commit):
        org = await _org(db)
        user, owner = await _user(db, org), await _user(db, org)
        (scf,) = await _controls(db, 1)
        control = await _scope(db, org, scf, owner_user_id=owner.id)
        await scoped_api.update_scoped_control(
            org.id, scf, ScopedControlUpdate(implementation_status="at_risk"),
            _request(), _membership(org, user), db,
        )
        (row,) = await _rows(db, org)
        assert (row.scoped_control_id, row.source, row.source_ref, row.auto_generated) == (
            control.id, "control_at_risk", None, True)
        assert (row.status, row.owner_user_id) == ("open", owner.id)
        assert scf in row.title
        audit = await _audited(db, org, row.id)
        assert audit and {(a.action, a.entity_type, a.changed_by_user_id, a.scf_id) for a in audit} == {
            ("create", "deficiency", user.id, scf)}

    async def test_it_is_raised_once_while_one_stands(self, db, no_commit):
        org = await _org(db)
        user = await _user(db, org)
        (scf,) = await _controls(db, 1)
        await _scope(db, org, scf)

        async def set_status(status):
            await scoped_api.update_scoped_control(
                org.id, scf, ScopedControlUpdate(implementation_status=status),
                _request(), _membership(org, user), db,
            )

        await set_status("at_risk")
        await set_status("at_risk")  # no transition
        await set_status("in_progress")
        await set_status("at_risk")  # a transition, but one stands
        (first,) = await _rows(db, org)
        await _update(db, org, user, first.id, status="cancelled")
        await set_status("at_risk")  # still at risk: closed is closed
        assert [r.status for r in await _rows(db, org)] == ["cancelled"]
        await set_status("implemented")
        await set_status("at_risk")
        rows = await _rows(db, org)
        assert [r.status for r in rows] == ["cancelled", "open"]

    async def test_other_statuses_raise_nothing(self, db, no_commit):
        org = await _org(db)
        user = await _user(db, org)
        (scf,) = await _controls(db, 1)
        await _scope(db, org, scf)
        for status in ("in_progress", "implemented", "not_started"):
            await scoped_api.update_scoped_control(
                org.id, scf, ScopedControlUpdate(implementation_status=status),
                _request(), _membership(org, user), db,
            )
        assert await _rows(db, org) == []

    @pytest.mark.parametrize("path", ["patch", "upsert", "batch"])
    async def test_an_at_risk_control_coming_into_scope_raises_it(self, db, no_commit, path):
        org = await _org(db)
        user = await _user(db, org, "admin")
        (scf,) = await _controls(db, 1, heavy=False)
        await _scope(db, org, scf, selected=False, implementation_status="at_risk")
        as_admin = _membership(org, user, "admin")
        if path == "patch":
            await scoped_api.update_scoped_control(
                org.id, scf, ScopedControlUpdate(selected=True), _request(), as_admin, db)
        elif path == "upsert":
            await scoped_api.create_or_update_scoped_control(
                org.id, ScopedControlCreate(scf_id=scf, selected=True, implementation_status="at_risk"),
                _request(), as_admin, db)
        else:
            out = await scoped_api.batch_update_scoped_controls(
                org.id, BatchScopedControlRequest(operations=[BatchScopedControlOperation(scf_id=scf, selected=True)]),
                _request(), as_admin, db)
            assert out.failed == 0, out.errors
        (row,) = await _rows(db, org)
        assert (row.source, row.scf_id) == ("control_at_risk", scf)

    @pytest.mark.parametrize("path", ["upsert-update", "upsert-create", "batch-update", "batch-create"])
    async def test_every_scoped_control_write_raises_it(self, db, no_commit, path):
        org = await _org(db)
        user = await _user(db, org)
        (scf,) = await _controls(db, 1)
        if path.endswith("update"):
            await _scope(db, org, scf)
        if path.startswith("upsert"):
            await scoped_api.create_or_update_scoped_control(
                org.id, ScopedControlCreate(scf_id=scf, selected=True, implementation_status="at_risk"),
                _request(), _membership(org, user), db,
            )
        else:
            out = await scoped_api.batch_update_scoped_controls(
                org.id,
                BatchScopedControlRequest(operations=[
                    BatchScopedControlOperation(scf_id=scf, selected=True, implementation_status="at_risk")]),
                _request(), _membership(org, user), db,
            )
            assert out.failed == 0, out.errors
        (row,) = await _rows(db, org)
        assert row.source == "control_at_risk"

    async def test_a_control_out_of_scope_raises_nothing(self, db, no_commit):
        org = await _org(db)
        user = await _user(db, org)
        (scf,) = await _controls(db, 1)
        await _scope(db, org, scf, selected=False)
        await scoped_api.batch_update_scoped_controls(
            org.id,
            BatchScopedControlRequest(operations=[BatchScopedControlOperation(scf_id=scf, implementation_status="at_risk")]),
            _request(), _membership(org, user), db,
        )
        assert await _rows(db, org) == []


async def _evidence_with_controls(db, n=2):
    """An evidence item the catalogue maps to at least ``n`` catalogue controls."""
    rows = (await db.execute(
        select(SCFCatalogEvidence.evidence_id, SCFCatalogEvidence.control_mappings)
        .where(func.jsonb_typeof(SCFCatalogEvidence.control_mappings) == "array")
        .order_by(SCFCatalogEvidence.evidence_id)
    )).all()
    known = set((await db.execute(select(SCFCatalogControl.scf_id))).scalars().all())
    for evidence_id, mapped in rows:
        controls = sorted({c for c in mapped or [] if c in known})
        if len(controls) >= n:
            return evidence_id, controls
    pytest.skip(f"no catalogue evidence maps {n} controls")  # pragma: no cover


@requires_postgres
@pytest.mark.asyncio
class TestRejection:
    async def test_a_rejection_raises_one_per_in_scope_control_it_supports(self, db, no_commit):
        org, other = await _org(db), await _org(db)
        user = await _user(db, org)
        evidence_id, mapped = await _evidence_with_controls(db, 2)
        kept = [await _scope(db, org, c) for c in mapped[:-1]]
        await _scope(db, org, mapped[-1], selected=False)
        await _scope(db, other, mapped[0])

        raised = await deficiencies.on_evidence_rejected(db, org.id, evidence_id, user.id)
        assert sorted(r.scoped_control_id for r in raised) == sorted(c.id for c in kept)
        rows = await _rows(db, org)
        assert {(r.source, r.source_ref) for r in rows} == {("evidence_rejected", evidence_id)}
        assert await _rows(db, other) == []
        # The same evidence rejected again (its window, say) adds nothing.
        assert await deficiencies.on_evidence_rejected(db, org.id, evidence_id, user.id) == []
        assert len(await _rows(db, org)) == len(kept)

    async def test_each_rejected_evidence_raises_its_own(self, db, no_commit):
        org = await _org(db)
        rows = (await db.execute(
            select(SCFCatalogEvidence.evidence_id, SCFCatalogEvidence.control_mappings)
            .where(SCFCatalogEvidence.status == "active",
                   func.jsonb_typeof(SCFCatalogEvidence.control_mappings) == "array")
            .order_by(SCFCatalogEvidence.evidence_id)
        )).all()
        known = set((await db.execute(select(SCFCatalogControl.scf_id))).scalars().all())
        by_control = {}
        for evidence_id, mapped in rows:
            for c in mapped or []:
                if c in known:
                    by_control.setdefault(c, []).append(evidence_id)
        shared = next(((c, ev) for c, ev in sorted(by_control.items()) if len(ev) >= 2), None)
        if shared is None:  # pragma: no cover - environment dependent
            pytest.skip("no control is supported by two active evidence items")
        scf, (first, second) = shared[0], shared[1][:2]
        await _scope(db, org, scf)
        await deficiencies.on_evidence_rejected(db, org.id, first)
        await deficiencies.on_evidence_rejected(db, org.id, second)
        assert sorted(r.source_ref for r in await _rows(db, org) if r.scf_id == scf) == sorted([first, second])

    async def test_retired_evidence_raises_nothing(self, db, no_commit):
        org = await _org(db)
        row = (await db.execute(
            select(SCFCatalogEvidence.evidence_id, SCFCatalogEvidence.control_mappings)
            .where(SCFCatalogEvidence.status != "active",
                   func.jsonb_typeof(SCFCatalogEvidence.control_mappings) == "array",
                   func.jsonb_array_length(SCFCatalogEvidence.control_mappings) > 0)
            .order_by(SCFCatalogEvidence.evidence_id).limit(1)
        )).first()
        if row is None:  # pragma: no cover - environment dependent
            pytest.skip("the catalogue holds no retired evidence")
        for scf in set(row[1]):
            await _scope(db, org, scf)
        assert await deficiencies.on_evidence_rejected(db, org.id, row[0]) == []

    async def test_unknown_evidence_raises_nothing(self, db, no_commit):
        org = await _org(db)
        assert await deficiencies.on_evidence_rejected(db, org.id, "no-such-evidence") == []


@requires_postgres
@pytest.mark.asyncio
class TestComposite:
    async def test_an_insufficient_composite_raises_one_attributed_to_the_platform(self, db, no_commit):
        org = await _org(db)
        (scf, unscoped) = await _controls(db, 2)
        control = await _scope(db, org, scf)
        first = await db.run_sync(lambda s: deficiencies.on_composite_insufficient_sync(s, org.id, scf))
        again = await db.run_sync(lambda s: deficiencies.on_composite_insufficient_sync(s, org.id, scf))
        none = await db.run_sync(lambda s: deficiencies.on_composite_insufficient_sync(s, org.id, unscoped))
        assert first is not None and again is None and none is None
        await db.flush()  # the worker's caller commits
        (row,) = await _rows(db, org)
        assert (row.scoped_control_id, row.source) == (control.id, "composite_insufficient")
        audit = await _audited(db, org, row.id)
        assert {(a.action, a.changed_by_user_id, a.action_source, a.scf_id) for a in audit} == {
            ("create", None, "system", scf)}
        assert {a.field_name for a in audit} == set(deficiencies.DEFICIENCY_TRACKED_FIELDS)

    async def test_only_this_orgs_in_scope_control(self, db, no_commit):
        org, other = await _org(db), await _org(db)
        (theirs, descoped) = await _controls(db, 2)
        await _scope(db, other, theirs)
        await _scope(db, org, descoped, selected=False)
        for scf in (theirs, descoped):
            assert await db.run_sync(lambda s, v=scf: deficiencies.on_composite_insufficient_sync(s, org.id, v)) is None
        await db.flush()
        assert await _rows(db, org) == [] and await _rows(db, other) == []


@requires_postgres
@pytest.mark.asyncio
class TestSummaryAndList:
    async def test_open_counts_per_control_and_domain(self, db, no_commit):
        org = await _org(db)
        user = await _user(db, org)
        (heavy,) = await _controls(db, 1, heavy=True)
        light = await _controls(db, 2, heavy=False, distinct_domains=True)
        for scf in (heavy, *light):
            await _scope(db, org, scf)
        for scf in (heavy, heavy, light[0], light[1]):
            await _create(db, org, user, scf_id=scf)
        closed = await _create(db, org, user, scf_id=light[1])
        await _update(db, org, user, closed.id, status="cancelled")
        await _create(db, org, user, risk_code=await _custom_risk(db, org, user))
        await _noise(db, org, heavy)

        out = await deficiency_api.deficiency_summary(org.id, _membership(org, user), db)
        assert out["open_total"] == 5 and out["unlinked_open"] == 1 and out["material_open_total"] == 2
        by_control = {e["scf_id"]: (e["open_count"], e["material"]) for e in out["by_control"]}
        assert by_control == {heavy: (2, True), light[0]: (1, False), light[1]: (1, False)}
        expected = {}
        for scf, n in ((heavy, 2), (light[0], 1), (light[1], 1)):
            expected[scf.split("-")[0]] = expected.get(scf.split("-")[0], 0) + n
        assert {e["domain"]: e["open_count"] for e in out["by_domain"]} == expected

    async def test_a_material_override_is_honoured(self, db, no_commit):
        org = await _org(db)
        user = await _user(db, org)
        (heavy,) = await _controls(db, 1, heavy=True)
        (light,) = await _controls(db, 1, heavy=False)
        await _scope(db, org, heavy, material_override=False, material_reason="Compensated by design")
        await _scope(db, org, light, material_override=True, material_reason="Holds customer data")
        await _create(db, org, user, scf_id=heavy)
        await _create(db, org, user, scf_id=light)
        out = await deficiency_api.deficiency_summary(org.id, _membership(org, user), db)
        assert {e["scf_id"]: e["material"] for e in out["by_control"]} == {heavy: False, light: True}

    async def test_list_filters(self, db, no_commit):
        org, other = await _org(db), await _org(db)
        user = await _user(db, org)
        a, b = await _controls(db, 2, distinct_domains=True)
        for scf in (a, b):
            await _scope(db, org, scf)
            await _scope(db, other, scf)
        da = await _create(db, org, user, scf_id=a)
        db_ = await _create(db, org, user, scf_id=b)
        code = await _custom_risk(db, org, user)
        dr = await _create(db, org, user, risk_code=code)
        await _update(db, org, user, db_.id, status="cancelled")
        await _create(db, other, await _user(db, other), scf_id=a)

        async def ids(**kw):
            params = dict(status=None, open_only=False, scf_id=None, domain=None, risk_code=None)
            params.update(kw)
            out = await deficiency_api.list_deficiencies(org.id, **params, membership=_membership(org, user), db=db)
            return {d.id for d in out}

        assert await ids() == {da.id, db_.id, dr.id}
        assert await ids(open_only=True) == {da.id, dr.id}
        assert await ids(status="cancelled") == {db_.id}
        assert await ids(scf_id=a.lower()) == {da.id}
        assert await ids(domain=b.split("-")[0].lower()) == {db_.id}
        assert await ids(risk_code=code) == {dr.id}


@requires_postgres
@pytest.mark.asyncio
class TestGates:
    async def test_open_deficiencies_max_by_domain(self, db, no_commit):
        org = await _org(db)
        user = await _user(db, org)
        a, b = await _controls(db, 2, distinct_domains=True)
        await _scope(db, org, a)
        await _scope(db, org, b)
        dom_a, dom_b = a.split("-")[0], b.split("-")[0]
        gate = dict(type="open_deficiencies_max", max_count=0)
        assert (await _gate(db, org, **gate))["met"] is True

        await _noise(db, org, b)
        assert (await _gate(db, org, **gate))["met"] is True
        await _create(db, org, user, scf_id=a)
        await _create(db, org, user, risk_code=await _custom_risk(db, org, user))
        out = await _gate(db, org, **gate)
        assert out["met"] is False and out["detail"].startswith("2 open deficiencies")
        assert (await _gate(db, org, **gate, domains=[dom_b]))["met"] is True
        out = await _gate(db, org, **gate, domains=[dom_a, dom_b])
        assert out["met"] is False and out["detail"].startswith("1 open deficiencies in ")
        assert (await _gate(db, org, type="open_deficiencies_max", max_count=1, domains=[dom_a]))["met"] is True

        closed = await _create(db, org, user, scf_id=b)
        await _update(db, org, user, closed.id, status="completed", retest_note="Retested")
        assert (await _gate(db, org, **gate, domains=[dom_b]))["met"] is True

    async def test_material_deficiencies_max_counts_only_material_controls(self, db, no_commit):
        org = await _org(db)
        user = await _user(db, org)
        (heavy,) = await _controls(db, 1, heavy=True)
        (light,) = await _controls(db, 1, heavy=False)
        await _scope(db, org, heavy)
        await _scope(db, org, light)
        gate = dict(type="material_deficiencies_max", max_count=0)
        await _create(db, org, user, scf_id=light)
        await _create(db, org, user, risk_code=await _custom_risk(db, org, user))
        assert (await _gate(db, org, **gate))["met"] is True
        await _create(db, org, user, scf_id=heavy)
        out = await _gate(db, org, **gate)
        assert out["met"] is False and out["detail"].startswith("1 open material deficiencies")

    async def test_material_gate_ignores_other_orgs_and_vendor_items(self, db, no_commit):
        org = await _org(db)
        (heavy,) = await _controls(db, 1, heavy=True)
        await _scope(db, org, heavy)
        await _noise(db, org, heavy)
        assert (await _gate(db, org, type="material_deficiencies_max", max_count=0))["met"] is True

    async def test_deleting_or_descoping_the_control_does_not_hide_it(self, db, no_commit):
        org = await _org(db)
        user = await _user(db, org)
        (heavy,) = await _controls(db, 1, heavy=True)
        (light,) = await _controls(db, 1, heavy=False)
        heavy_row = await _scope(db, org, heavy)
        light_row = await _scope(db, org, light)
        made = await _create(db, org, user, scf_id=heavy)
        await _create(db, org, user, scf_id=light)
        await db.delete(heavy_row)
        light_row.selected = False
        await db.flush()
        db.expire_all()  # the FK's SET NULL happened in the database
        await db.refresh(org)
        await db.refresh(user)

        got = await deficiency_api.get_deficiency(org.id, made.id, _membership(org, user), db)
        assert (got.scoped_control_id, got.scf_id, got.domain) == (None, heavy, heavy.split("-")[0])
        material = await _gate(db, org, type="material_deficiencies_max", max_count=0)
        assert material["met"] is False and material["detail"].startswith("1 open material")
        for scf in (heavy, light):
            out = await _gate(db, org, type="open_deficiencies_max", max_count=0, domains=[scf.split("-")[0]])
            assert out["met"] is False
        summary = await deficiency_api.deficiency_summary(org.id, _membership(org, user), db)
        assert {e["scf_id"]: e["material"] for e in summary["by_control"]} == {heavy: True, light: False}
        assert (summary["open_total"], summary["unlinked_open"], summary["material_open_total"]) == (2, 0, 1)

    @pytest.mark.parametrize("gate", GATES)
    async def test_an_unknown_domain_cannot_be_evaluated_rather_than_passing(self, db, no_commit, gate):
        org = await _org(db)
        (a,) = await _controls(db, 1)
        out = await _gate(db, org, type=gate, max_count=0, domains=[a.split("-")[0], "NOSUCHDOMAIN"])
        assert out["met"] is None and "NOSUCHDOMAIN" in out["detail"]

    @pytest.mark.parametrize("gate", GATES)
    async def test_an_unknown_domain_is_refused_at_import(self, db, gate):
        template = {"stages": [{"key": "s", "precondition_spec": [
            {"type": gate, "max_count": 0, "domains": ["NOSUCHDOMAIN"]}]}]}
        problems = await journey_service.template_param_problems(db, template)
        assert len(problems) == 1 and "NOSUCHDOMAIN" in problems[0]
