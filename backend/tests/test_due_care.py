"""Due-care pack (#1130, PIG R10).

These tests pin:

* the validation record: only on a control the organisation has in scope,
  never another organisation's; audited as an update of the control; the
  next date is the last validation plus the control's cadence (its own, else
  the catalogue's), and a control never validated or with no known cadence
  never counts as validated;
* the schedule: counts over every in-scope control, a state filter that
  refuses an unknown state;
* posture snapshots: one per organisation per month, refused (409) rather
  than replaced; audited; the monthly sweep takes each organisation's once;
* the validation-due notice: once per validation cycle, to the control's
  owner (or the admins when it has none), never for a control not validated;
* the journey checks: ``controls_validated_within_cadence`` and
  ``documents_approved`` with ``within_days``, both failing closed on a
  parameter they cannot honour;
* the Governance Oversight Report: its figures, its red flags, and a prompt
  key that moves when a figure does.

Control ids are read from the catalogue at run time. Everything that touches
the database runs inside a transaction that is rolled back.
"""
import inspect
import json
import uuid as _uuid
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio
from dateutil.relativedelta import relativedelta
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from api import due_care as api
from auth import OrgMembership
from catalog_models import SCFCatalogControl
from middleware.audit_middleware import _action_for, _extract_entity_type
from models import (
    AuditLog,
    DocGenSettings,
    DocumentTransition,
    GeneratedDocument,
    Notification,
    Organization,
    OrganizationMember,
    PostureSnapshot,
    RiskAssessment,
    ScopedControl,
    User,
    Vendor,
    VendorActionItem,
    VendorCompensatingControl,
)
from schemas import ControlValidationRequest
from services import due_care
from services import journey as journey_service
from services import materiality
from services import notifications as notifications_service
from services.doc_gen.context import OrganisationContext
from services.doc_gen.registry import get_generator
from services.doc_gen.tier1 import governance_inputs_digest, render_governance_report

from test_journey_service import DATABASE_URL, requires_postgres  # noqa: F401

TODAY = date.today()


def _ago(**kwargs) -> datetime:
    return datetime.combine(TODAY - relativedelta(**kwargs), datetime.min.time())


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
        (f"{org}/scoped-controls/{{scf_id}}/validation", "POST"),
        (f"{org}/control-validations", "GET"),
        (f"{org}/posture-snapshots", "GET"),
        (f"{org}/posture-snapshots", "POST"),
        (f"{org}/posture-snapshots/{{period}}", "GET"),
    }
    assert _min_role(api.record_control_validation) == "editor"
    assert _min_role(api.list_control_validations) == "viewer"
    assert _min_role(api.list_posture_snapshots) == "viewer"
    assert _min_role(api.get_posture_snapshot) == "viewer"
    assert _min_role(api.create_posture_snapshot) == "editor"


def test_the_router_is_mounted_and_audited():
    import main

    paths = main.app.openapi()["paths"]
    org = "/api/organizations/{org_id}"
    assert "post" in paths.get(f"{org}/scoped-controls/{{scf_id}}/validation", {})
    assert "get" in paths.get(f"{org}/control-validations", {})
    assert {"get", "post"} <= set(paths.get(f"{org}/posture-snapshots", {}))
    assert "get" in paths.get(f"{org}/posture-snapshots/{{period}}", {})
    oid = _uuid.uuid4()
    validation = f"/api/organizations/{oid}/scoped-controls/X/validation"
    assert _extract_entity_type(validation) == "scoped_control"
    assert _action_for("POST", validation) == "update"
    assert _extract_entity_type(f"/api/organizations/{oid}/posture-snapshots") == "posture_snapshot"
    assert _action_for("POST", f"/api/organizations/{oid}/posture-snapshots") == "create"
    # Other POSTs under scoped-controls keep their own action.
    assert _action_for("POST", f"/api/organizations/{oid}/scoped-controls/X") == "create"


def test_the_jobs_and_the_notice_are_registered():
    from services.owner_resolution import EVENTS, ROUTE_OWNERSHIP
    from schemas import NotificationBase

    assert EVENTS["control_validation_due"].routing == ROUTE_OWNERSHIP
    NotificationBase(type="control_validation_due", reference_type="control",
                     reference_id=_uuid.uuid4(), message="m")


def test_the_report_generator_is_registered_reading_governance_only():
    spec = get_generator("governance-report")
    assert (spec.tier, spec.document_type, spec.is_derivative, spec.domain_scoped) == (1, "report", False, False)
    assert spec.renderer == "tier1.render_governance_report"
    assert spec.requires.governance and spec.requires.controls
    assert not (spec.requires.evidence or spec.requires.risks or spec.requires.systems)
    # No other generator pays for the governance queries.
    from services.doc_gen.registry import all_generators

    assert [g.name for g in all_generators() if g.requires.governance] == ["governance-report"]


@pytest.mark.parametrize("note", ["", "   "])
def test_a_blank_note_is_refused(note):
    with pytest.raises(ValueError):
        ControlValidationRequest(note=note)


def test_a_note_is_required_and_nothing_else_is_accepted():
    with pytest.raises(ValueError):
        ControlValidationRequest()
    with pytest.raises(ValueError):
        ControlValidationRequest(note="Checked", last_validated_at="2020-01-01T00:00:00")
    assert ControlValidationRequest(note=" Sampled ten joiners ").note == "Sampled ten joiners"


@pytest.mark.parametrize(
    ("cadence", "months"),
    [("Annual", 12), ("Semi-Annual", 6), ("Quarterly", 3), (" monthly ", 1),
     (None, None), ("", None), ("Biennial", None)],
)
def test_cadence_months_reads_the_catalogue_wording(cadence, months):
    assert due_care.cadence_months(cadence) == months


def test_the_next_date_is_the_last_validation_plus_the_cadence():
    assert due_care.next_validation_due(datetime(2026, 1, 31, 15), "Quarterly") == date(2026, 4, 30)
    assert due_care.next_validation_due(datetime(2024, 2, 29), "Annual") == date(2025, 2, 28)
    assert due_care.next_validation_due(None, "Annual") is None
    assert due_care.next_validation_due(datetime(2026, 1, 1), None) is None


def test_validation_state_boundaries():
    state = due_care.validation_state
    today = date(2026, 6, 15)
    assert state(None, "Annual", today) == "never"
    assert state(datetime(2026, 6, 1), "Biennial", today) == "unknown_cadence"
    assert state(datetime(2025, 6, 14), "Annual", today) == "overdue"
    assert state(datetime(2025, 6, 15), "Annual", today) == "due_soon"
    assert state(datetime(2025, 7, 15), "Annual", today) == "due_soon"
    assert state(datetime(2025, 7, 16), "Annual", today) == "current"
    assert set(due_care.WITHIN_CADENCE) == {"current", "due_soon"}


def test_the_notice_horizon_is_fourteen_days_and_includes_past_due():
    today = date(2026, 6, 15)
    rows = [
        SimpleNamespace(name="past", last_validated_at=datetime(2025, 1, 1), cadence="Annual"),
        SimpleNamespace(name="edge", last_validated_at=datetime(2025, 6, 29), cadence="Annual"),
        SimpleNamespace(name="beyond", last_validated_at=datetime(2025, 6, 30), cadence="Annual"),
        SimpleNamespace(name="never", last_validated_at=None, cadence="Annual"),
        SimpleNamespace(name="unknown", last_validated_at=datetime(2020, 1, 1), cadence=None),
    ]
    assert [(r.name, d) for r, d in due_care.due_for_notice(rows, today)] == [
        ("past", date(2026, 1, 1)), ("edge", date(2026, 6, 29)),
    ]


def test_summarise_posture_counts_each_row_once():
    today = date(2026, 6, 15)
    out = due_care.summarise_posture(
        [("implemented", "mcr", True), ("not_started", None, False), ("implemented", "odd", True)],
        [(None, True), (date(2026, 6, 1), False), (date(2026, 7, 1), True)],
        today,
    )
    assert out == {
        "in_scope": 3,
        "status": {"implemented": 2, "not_started": 1},
        "basis": {"mcr": 1, "dsr": 0, "none": 2},
        "materiality": {"material": 2, "not_material": 1},
        "deficiencies": {"open": 3, "material_open": 2, "overdue": 1, "without_due_date": 1},
    }


def _governance(**overrides):
    data = {
        "as_of": "2026-06-15",
        "recent_days": 90,
        "posture": {
            "in_scope": 2,
            "status": {"implemented": 2},
            "basis": {"mcr": 1, "dsr": 1, "none": 0},
            "materiality": {"material": 1, "not_material": 1},
            "deficiencies": {"open": 0, "material_open": 0, "overdue": 0, "without_due_date": 0},
        },
        "implemented": 2,
        "material_implemented": 1,
        "validation": {"current": 1, "due_soon": 0, "overdue": 0, "never": 1, "unknown_cadence": 0},
        "validated_recently": 1,
        "material_open_deficiencies": [],
        "deficiencies_without_owner": 0,
        "deficiencies_raised_recently": {},
        "deficiencies_completed_recently": 0,
        "risk_treatment": {},
        "risk_acceptance": {"approved": 0, "expired": 0, "unapproved": 0},
        "expired_acceptances": [],
        "vendors_by_criticality": {},
        "vendors_added_recently": 0,
        "compensating_controls": 0,
        "frameworks": [],
        "previous_snapshot": None,
    }
    data.update(overrides)
    return data


def test_everything_green_with_no_exceptions_is_a_red_flag():
    flags = due_care.red_flags(_governance())
    assert len(flags) == 1 and "no documented exceptions" in flags[0]


@pytest.mark.parametrize(
    "exception",
    [
        {"implemented": 1},
        {"risk_acceptance": {"approved": 1, "expired": 0, "unapproved": 0}},
        {"compensating_controls": 1},
    ],
)
def test_a_documented_exception_clears_the_all_green_flag(exception):
    assert due_care.red_flags(_governance(**exception)) == []


def test_an_open_deficiency_clears_the_all_green_flag():
    data = _governance()
    data["posture"]["deficiencies"]["open"] = 1
    assert due_care.red_flags(data) == []


def test_nothing_in_scope_is_not_all_green():
    data = _governance(implemented=0)
    data["posture"]["in_scope"] = 0
    assert due_care.red_flags(data) == []


def test_expired_acceptances_and_undated_material_deficiencies_are_flagged():
    data = _governance(
        implemented=1,
        expired_acceptances=[{"risk_code": "R-ORG-1", "expires_on": "2026-05-01"}],
        material_open_deficiencies=[
            {"title": "t", "scf_id": "X", "status": "open", "due_date": None, "owner": None},
            {"title": "u", "scf_id": "X", "status": "open", "due_date": "2026-07-01", "owner": None},
        ],
    )
    flags = due_care.red_flags(data)
    assert len(flags) == 2
    assert "R-ORG-1" in flags[0]
    assert flags[1].startswith("1 material deficiency")


def _report_ctx(governance):
    return OrganisationContext(
        organization_id=str(_uuid.uuid4()), name="Example Org", generated_at="2026-06-15T00:00:00",
        governance=governance,
    )


def test_the_report_answers_each_oversight_question():
    out = render_governance_report(_report_ctx(_governance(
        frameworks=[{"framework_id": "fw-a", "basis": "mcr"}, {"framework_id": "fw-b", "basis": None}],
    )))
    for heading in (
        "## 1. Implementation and Known Deficiencies",
        "## 2. Change in the Risk Profile",
        "## 3. Validation: What Failed and What Was Corrected",
        "## 4. Obligations in Scope",
        "## 5. Red Flags",
        "## 6. Review Record",
    ):
        assert heading in out
    assert out.count("**For the board:**") == 4
    assert "| fw-a | MCR |" in out and "| fw-b | NOT SET |" in out
    assert "no documented exceptions" in out
    assert "No posture snapshot from three or more months ago" in out


def test_the_report_compares_with_the_snapshot_a_quarter_ago():
    data = _governance(previous_snapshot={"period": "2026-03-01", "counts": {
        "in_scope": 1, "status": {"implemented": 1, "not_started": 0},
        "materiality": {"material": 1},
        "deficiencies": {"open": 2, "material_open": 1, "overdue": 0},
    }})
    out = render_governance_report(_report_ctx(data))
    assert "Compared with the posture snapshot of 2026-03-01." in out
    assert "| Controls in scope | 1 | 2 | +1 |" in out
    assert "| Open deficiencies | 2 | 0 | -2 |" in out
    assert "| Material controls | 1 | 1 | no change |" in out


def test_the_report_lists_open_material_deficiencies_and_flags():
    data = _governance(implemented=1, material_open_deficiencies=[
        {"title": "Patch | backlog", "scf_id": "X", "status": "in_progress", "due_date": None, "owner": "Ops"},
    ])
    out = render_governance_report(_report_ctx(data))
    assert "### Open material deficiencies (1)" in out
    assert "| Patch / backlog | X | In Progress | — | Ops |" in out
    assert "- 1 material deficiency(ies) without a due date." in out


def test_the_prompt_key_moves_with_a_figure_but_not_with_the_date():
    from services.doc_gen.pipeline import _render_tier1

    spec = get_generator("governance-report")
    base = _render_tier1(spec, _report_ctx(_governance()))[2]
    assert base.startswith("tier1:governance-report:governance:")
    assert _render_tier1(spec, _report_ctx(_governance(as_of="2026-06-16")))[2] == base
    assert _render_tier1(spec, _report_ctx(_governance(compensating_controls=1)))[2] != base
    assert governance_inputs_digest(_report_ctx(_governance(vendors_added_recently=3))) != base.rsplit(":", 1)[1]


# -- journey check parameters ------------------------------------------------


VALIDATION_GATE = "controls_validated_within_cadence"


@pytest.mark.parametrize(
    "check",
    [
        {"type": VALIDATION_GATE, "max_count": 0},
        {"type": VALIDATION_GATE, "statuses": ["implemented"]},
        {"type": VALIDATION_GATE, "min_fraction": 0},
        {"type": VALIDATION_GATE, "min_fraction": 1.5},
        {"type": VALIDATION_GATE, "min_fraction": True},
        {"type": VALIDATION_GATE, "domains": []},
        {"type": VALIDATION_GATE, "domains": [" "]},
        {"type": VALIDATION_GATE, "scf_ids": []},
        {"type": VALIDATION_GATE, "within_days": 30},
        {"type": "documents_approved", "within_days": 0},
        {"type": "documents_approved", "within_days": -5},
        {"type": "documents_approved", "within_days": True},
        {"type": "documents_approved", "within_days": "30"},
        {"type": "documents_approved", "within_days": 1.5},
        {"type": "documents_approved", "within_days": 3661},
        {"type": "documents_approved", "within_days": 3650000},
        {"type": "controls_with_owner", "within_days": 30},
    ],
)
def test_a_parameter_the_check_cannot_honour_is_refused(check):
    assert journey_service.check_param_problem(check) is not None


@pytest.mark.parametrize(
    "check",
    [
        {"type": VALIDATION_GATE},
        {"type": VALIDATION_GATE, "label": "l", "min_fraction": 0.8, "domains": ["GOV"], "scf_ids": ["X"]},
        {"type": "documents_approved", "within_days": 365},
        {"type": "documents_approved", "within_days": 3660},
        {"type": "documents_approved", "within_days": 1, "generator": "governance-report"},
    ],
)
def test_well_formed_parameters_are_accepted(check):
    assert journey_service.check_param_problem(check) is None


def test_the_validation_gate_is_a_supported_control_filtered_check():
    assert VALIDATION_GATE in journey_service.SUPPORTED_CHECKS
    assert VALIDATION_GATE in journey_service.CONTROL_FILTERED_CHECKS


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


async def _controls(db, n, cadence="Annual", heavy=None, distinct_domains=False):
    """``n`` active catalogue controls with this catalogue cadence."""
    query = select(SCFCatalogControl.scf_id).where(
        SCFCatalogControl.status == "active", SCFCatalogControl.validation_cadence == cadence,
    )
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


async def _deficiency(db, org, control, **kwargs):
    fields = dict(organization_id=org.id, vendor_id=None, scoped_control_id=control.id,
                  scf_id=control.scf_id, title="Gap found in testing", priority="high", status="open")
    fields.update(kwargs)
    item = VendorActionItem(**fields)
    db.add(item)
    await db.flush()
    return item


async def _audited(db, org, entity_id):
    return list((await db.execute(select(AuditLog).where(
        AuditLog.organization_id == org.id, AuditLog.entity_id == entity_id,
    ))).scalars().all())


async def _validate(db, org, user, scf_id, note="Sampled ten joiners against HR records"):
    return await api.record_control_validation(
        org.id, scf_id, ControlValidationRequest(note=note), _request(), _membership(org, user), db,
    )


@requires_postgres
@pytest.mark.asyncio
class TestValidation:
    async def test_a_validation_is_recorded_audited_and_dated_by_the_catalogue_cadence(self, db, no_commit):
        org = await _org(db)
        user = await _user(db, org)
        (scf,) = await _controls(db, 1, cadence="Quarterly")
        control = await _scope(db, org, scf)

        out = await _validate(db, org, user, scf.lower())
        await db.refresh(control)
        assert control.last_validated_by == user.id and control.last_validated_at is not None
        assert control.last_validation_note == "Sampled ten joiners against HR records"
        assert out.scf_id == scf and out.validation_cadence == "Quarterly"
        assert out.next_validation_due == control.last_validated_at.date() + relativedelta(months=3)
        assert out.validation_state == "current"

        entries = await _audited(db, org, control.id)
        assert {e.field_name for e in entries} == {"last_validated_at", "last_validated_by", "last_validation_note"}
        assert {(e.entity_type, e.action, e.scf_id, e.changed_by_user_id) for e in entries} == {
            ("scoped_control", "update", scf, user.id)}
        assert {e.new_value for e in entries if e.field_name == "last_validation_note"} == {
            json.dumps("Sampled ten joiners against HR records")}

    async def test_the_controls_own_cadence_wins_over_the_catalogues(self, db, no_commit):
        org = await _org(db)
        (scf,) = await _controls(db, 1, cadence="Annual")
        await _scope(db, org, scf, validation_cadence="Monthly")
        out = await _validate(db, org, await _user(db, org), scf)
        assert out.validation_cadence == "Monthly"
        assert out.next_validation_due == out.last_validated_at.date() + relativedelta(months=1)

    async def test_a_blank_cadence_override_falls_back_to_the_catalogues(self, db, no_commit):
        org = await _org(db)
        a, b = await _controls(db, 2, cadence="Quarterly")
        await _scope(db, org, a, validation_cadence="", last_validated_at=_ago(months=1))
        await _scope(db, org, b, validation_cadence="  ", last_validated_at=_ago(months=4))
        out = await api.list_control_validations(
            org.id, None, None, None, _membership(org, await _user(db, org, "viewer"), "viewer"), db,
        )
        assert {(c.scf_id, c.validation_cadence, c.validation_state) for c in out.controls} == {
            (a, "Quarterly", "current"), (b, "Quarterly", "overdue")}
        assert (await _gate(db, org, type=VALIDATION_GATE, min_fraction=0.5))["met"] is True

        org, other = await _org(db), await _org(db)
        a, b, c = await _controls(db, 3)
        await _scope(db, other, a)
        await _scope(db, org, b, selected=False)
        user = await _user(db, org)
        for scf in (a, b, c):
            with pytest.raises(HTTPException) as exc:
                await _validate(db, org, user, scf)
            assert exc.value.status_code == 422
        unknown = "X" + _uuid.uuid4().hex[:8]
        with pytest.raises(HTTPException):
            await _validate(db, org, user, unknown)
        foreign = (await db.execute(select(ScopedControl).where(
            ScopedControl.organization_id == other.id, ScopedControl.scf_id == a,
        ))).scalar_one()
        assert foreign.last_validated_at is None

    async def test_the_schedule_counts_every_control_and_filters_by_state(self, db, no_commit):
        org = await _org(db)
        a, b, c, d = await _controls(db, 4, cadence="Annual", distinct_domains=True)
        await _scope(db, org, a, last_validated_at=_ago(months=2))
        await _scope(db, org, b, last_validated_at=_ago(months=13))
        await _scope(db, org, c)
        await _scope(db, org, d, selected=False, last_validated_at=_ago(months=13))
        member = _membership(org, await _user(db, org, "viewer"), "viewer")

        out = await api.list_control_validations(org.id, None, None, None, member, db)
        assert out.counts == {"current": 1, "due_soon": 0, "overdue": 1, "never": 1, "unknown_cadence": 0}
        assert [c.scf_id for c in out.controls] == sorted([a, b, c])

        overdue = await api.list_control_validations(org.id, "overdue", None, None, member, db)
        assert [c.scf_id for c in overdue.controls] == [b]
        assert overdue.counts == out.counts

        by_domain = await api.list_control_validations(org.id, None, a.split("-")[0], None, member, db)
        assert [c.scf_id for c in by_domain.controls] == [a]
        by_id = await api.list_control_validations(org.id, None, None, c.lower(), member, db)
        assert [c.scf_id for c in by_id.controls] == [c]

        with pytest.raises(HTTPException) as exc:
            await api.list_control_validations(org.id, "stale", None, None, member, db)
        assert exc.value.status_code == 422

    async def test_another_organisations_controls_are_not_listed(self, db, no_commit):
        org, other = await _org(db), await _org(db)
        (a,) = await _controls(db, 1)
        await _scope(db, other, a, last_validated_at=_ago(months=1))
        out = await api.list_control_validations(
            org.id, None, None, None, _membership(org, await _user(db, org, "viewer"), "viewer"), db,
        )
        assert out.controls == [] and sum(out.counts.values()) == 0


async def _snapshots(db, org):
    return list((await db.execute(
        select(PostureSnapshot).where(PostureSnapshot.organization_id == org.id)
    )).scalars().all())


@requires_postgres
@pytest.mark.asyncio
class TestSnapshots:
    async def test_a_manual_snapshot_counts_the_posture_and_is_audited(self, db, no_commit):
        org = await _org(db)
        user = await _user(db, org)
        heavy, (light, descoped) = (await _controls(db, 1, heavy=True))[0], await _controls(db, 2, heavy=False)
        material = await _scope(db, org, heavy, implementation_status="implemented")
        await _scope(db, org, light)
        await _scope(db, org, descoped, selected=False, implementation_status="implemented")
        await _deficiency(db, org, material, due_date=TODAY - timedelta(days=1))
        await _deficiency(db, org, material, status="completed", retest_note="Retested: fixed")

        out = await api.create_posture_snapshot(org.id, _request(), _membership(org, user), db)
        assert out.period == TODAY.replace(day=1) and out.trigger == "manual"
        assert out.taken_by_user_id == user.id
        assert out.counts["in_scope"] == 2
        assert out.counts["status"] == {"implemented": 1, "not_started": 1}
        assert out.counts["materiality"] == {"material": 1, "not_material": 1}
        assert out.counts["deficiencies"] == {"open": 1, "material_open": 1, "overdue": 1, "without_due_date": 0}
        entries = await _audited(db, org, out.id)
        assert {e.field_name for e in entries} == {"period", "trigger", "taken_by_user_id", "counts"}
        assert {(e.entity_type, e.action) for e in entries} == {("posture_snapshot", "create")}

    async def test_a_second_snapshot_in_the_month_is_refused_not_replaced(self, db, no_commit):
        org = await _org(db)
        user = await _user(db, org)
        first = await api.create_posture_snapshot(org.id, _request(), _membership(org, user), db)
        with pytest.raises(HTTPException) as exc:
            await api.create_posture_snapshot(org.id, _request(), _membership(org, user), db)
        assert exc.value.status_code == 409
        assert [s.id for s in await _snapshots(db, org)] == [first.id]

    async def test_snapshots_are_listed_newest_first_and_read_by_month(self, db, no_commit):
        org, other = await _org(db), await _org(db)
        viewer = _membership(org, await _user(db, org, "viewer"), "viewer")
        for months in (2, 0, 1):
            await due_care.take_snapshot(db, org.id, "scheduled", today=TODAY - relativedelta(months=months))
        await due_care.take_snapshot(db, other.id, "scheduled")
        await db.flush()

        listed = await api.list_posture_snapshots(org.id, 24, viewer, db)
        assert [s.period for s in listed] == [
            (TODAY - relativedelta(months=m)).replace(day=1) for m in (0, 1, 2)
        ]
        assert len(await api.list_posture_snapshots(org.id, 1, viewer, db)) == 1

        for months in (0, 1):
            month = (TODAY - relativedelta(months=months)).strftime("%Y-%m")
            got = await api.get_posture_snapshot(org.id, month, viewer, db)
            assert got.organization_id == org.id and got.period.strftime("%Y-%m") == month
        for bad, code in (("2026-13", 422), ("June", 422), ("1999-01", 404)):
            with pytest.raises(HTTPException) as exc:
                await api.get_posture_snapshot(org.id, bad, viewer, db)
            assert exc.value.status_code == code

    async def test_a_listed_period_reads_back_its_snapshot_over_http(self, db, no_commit, monkeypatch):
        """The list serialises ``period`` as the ``YYYY-MM`` the read by month takes.

        Over HTTP, because the defect was in the serialisation: the handlers
        return ORM rows and only the response model turns them into JSON.
        """
        import httpx
        from fastapi import FastAPI

        import auth as auth_mod
        from database import get_db

        org = await _org(db)
        viewer = _membership(org, await _user(db, org, "viewer"), "viewer")
        month = (TODAY - relativedelta(months=1)).strftime("%Y-%m")
        await due_care.take_snapshot(db, org.id, "scheduled", today=TODAY - relativedelta(months=1))
        await db.flush()

        async def _require_auth(*args, **kwargs):
            return viewer.user

        async def _verify(*args, **kwargs):
            return viewer

        async def _db():
            yield db

        monkeypatch.setattr(auth_mod, "require_auth", _require_auth)
        monkeypatch.setattr(auth_mod, "verify_org_membership", _verify)
        app = FastAPI()
        app.include_router(api.router, prefix="/api")
        app.dependency_overrides[get_db] = _db
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://due-care",
            headers={"Authorization": "Bearer stub"},
        ) as client:
            listed = await client.get(f"/api/organizations/{org.id}/posture-snapshots")
            assert listed.status_code == 200, listed.text
            (row,) = listed.json()
            assert row["period"] == month
            got = await client.get(f"/api/organizations/{org.id}/posture-snapshots/{row['period']}")
        assert got.status_code == 200, got.text
        assert got.json()["id"] == row["id"] and got.json()["period"] == month

    async def test_the_monthly_sweep_takes_each_organisation_once(self, db, no_commit):
        org = await _org(db)
        await due_care.take_monthly_snapshots(db, TODAY)
        mine = await _snapshots(db, org)
        assert [(s.period, s.trigger, s.taken_by_user_id) for s in mine] == [(TODAY.replace(day=1), "scheduled", None)]
        entries = await _audited(db, org, mine[0].id)
        assert entries and {(e.action, e.changed_by_user_id) for e in entries} == {("create", None)}

        await due_care.take_monthly_snapshots(db, TODAY)
        assert len(await _snapshots(db, org)) == 1
        with pytest.raises(HTTPException) as exc:
            await api.create_posture_snapshot(org.id, _request(), _membership(org, await _user(db, org)), db)
        assert exc.value.status_code == 409


@requires_postgres
@pytest.mark.asyncio
class TestValidationNotice:
    """Assertions are on this organisation's notices, never the sweep's
    global count: the sweep covers every organisation in the database."""

    async def _notices(self, db, org):
        return (await db.execute(select(
            Notification.user_id, Notification.type, Notification.reference_type, Notification.reference_id,
        ).where(Notification.organization_id == org.id))).all()

    async def _sweep(self, db, day=TODAY):
        return await notifications_service.check_and_notify_due_control_validations(db, day)

    async def test_a_validation_coming_due_is_announced_once_to_the_owner(self, db, no_commit):
        org = await _org(db)
        await _user(db, org, "admin")
        owner = await _user(db, org, "editor")
        (scf,) = await _controls(db, 1, cadence="Annual")
        control = await _scope(db, org, scf, owner_user_id=owner.id,
                               last_validated_at=_ago(months=12) + timedelta(days=7))

        await self._sweep(db)
        assert [tuple(n) for n in await self._notices(db, org)] == [
            (owner.id, "control_validation_due", "control", control.id)]
        await self._sweep(db, TODAY + timedelta(days=1))
        assert len(await self._notices(db, org)) == 1

    async def test_a_new_validation_starts_a_new_cycle(self, db, no_commit):
        org = await _org(db)
        owner = await _user(db, org, "editor")
        (scf,) = await _controls(db, 1, cadence="Quarterly")
        control = await _scope(db, org, scf, owner_user_id=owner.id, last_validated_at=_ago(months=4))
        await self._sweep(db)
        assert len(await self._notices(db, org)) == 1
        # Validated again; that validation in turn falls due.
        control.last_validated_at = datetime.utcnow() + timedelta(minutes=1)
        await db.flush()
        await self._sweep(db, TODAY + relativedelta(months=3))
        assert len(await self._notices(db, org)) == 2

    async def test_a_control_without_an_owner_goes_to_the_admins(self, db, no_commit):
        org = await _org(db)
        admin = await _user(db, org, "admin")
        (scf,) = await _controls(db, 1, cadence="Annual")
        await _scope(db, org, scf, last_validated_at=_ago(months=13))
        await self._sweep(db)
        assert {n.user_id for n in await self._notices(db, org)} == {admin.id}

    async def test_nothing_is_announced_early_never_validated_or_out_of_scope(self, db, no_commit):
        org = await _org(db)
        owner = await _user(db, org, "admin")
        a, b, c, d = await _controls(db, 4, cadence="Annual")
        await _scope(db, org, a, owner_user_id=owner.id, last_validated_at=_ago(months=11))
        await _scope(db, org, b, owner_user_id=owner.id)
        await _scope(db, org, c, owner_user_id=owner.id, selected=False, last_validated_at=_ago(months=13))
        await _scope(db, org, d, owner_user_id=owner.id, validation_cadence="Whenever",
                     last_validated_at=_ago(months=30))
        await self._sweep(db)
        assert await self._notices(db, org) == []


async def _gate(db, org, **check):
    totals = await journey_service._in_scope_counts(db, org.id)
    return await journey_service._evaluate_one(db, org.id, check, totals)


@requires_postgres
@pytest.mark.asyncio
class TestJourneyChecks:
    async def test_no_controls_in_scope_fails(self, db, no_commit):
        org = await _org(db)
        out = await _gate(db, org, type=VALIDATION_GATE)
        assert out["met"] is False and "No controls scoped" in out["detail"]

    async def test_only_controls_validated_within_their_cadence_count(self, db, no_commit):
        org = await _org(db)
        a, b, c, d = await _controls(db, 4, cadence="Annual")
        await _scope(db, org, a, last_validated_at=_ago(months=1))
        await _scope(db, org, b, last_validated_at=_ago(months=11, days=20))  # due soon: still within
        await _scope(db, org, c, last_validated_at=_ago(months=13))           # overdue
        await _scope(db, org, d)                                               # never
        out = await _gate(db, org, type=VALIDATION_GATE)
        assert out["met"] is False
        assert out["detail"].startswith("2 of 4 validated within their cadence (50.0%)")
        assert (await _gate(db, org, type=VALIDATION_GATE, min_fraction=0.5))["met"] is True

    async def test_an_unknown_cadence_counts_against(self, db, no_commit):
        org = await _org(db)
        (a,) = await _controls(db, 1, cadence="Annual")
        await _scope(db, org, a, validation_cadence="Whenever", last_validated_at=_ago(days=1))
        assert (await _gate(db, org, type=VALIDATION_GATE))["met"] is False

    async def test_a_named_control_nobody_scoped_counts_against(self, db, no_commit):
        org = await _org(db)
        a, b, c = await _controls(db, 3, cadence="Annual")
        await _scope(db, org, a, last_validated_at=_ago(months=1))
        await _scope(db, org, c, last_validated_at=_ago(months=1))  # validated, but not named
        out = await _gate(db, org, type=VALIDATION_GATE, scf_ids=[a, b])
        assert out["met"] is False and out["detail"].startswith("1 of 2 across 2 named controls")
        assert (await _gate(db, org, type=VALIDATION_GATE, scf_ids=[a]))["met"] is True

    async def test_another_organisations_validations_do_not_count(self, db, no_commit):
        org, other = await _org(db), await _org(db)
        (a,) = await _controls(db, 1, cadence="Annual")
        await _scope(db, org, a)
        await _scope(db, other, a, last_validated_at=_ago(months=1))
        assert (await _gate(db, org, type=VALIDATION_GATE))["met"] is False

    async def test_an_unknown_domain_is_refused_at_import_and_never_passes(self, db, no_commit):
        org = await _org(db)
        (a,) = await _controls(db, 1, cadence="Annual")
        await _scope(db, org, a, last_validated_at=_ago(months=1))
        template = {"stages": [{"key": "s", "precondition_spec": [
            {"type": VALIDATION_GATE, "domains": ["NOSUCHDOMAIN"]}]}]}
        problems = await journey_service.template_param_problems(db, template)
        assert len(problems) == 1 and "NOSUCHDOMAIN" in problems[0]
        out = await _gate(db, org, type=VALIDATION_GATE, domains=["NOSUCHDOMAIN"])
        assert out["met"] is False and "No controls scoped in NOSUCHDOMAIN" in out["detail"]

    async def test_a_document_must_have_been_approved_within_the_window(self, db, no_commit):
        async def doc(status, approved_days_ago, published_days_ago=None):
            org = await _org(db)
            d = GeneratedDocument(
                organization_id=org.id, generator_name="governance-report", document_type="report",
                title="Governance", filename=f"g-{_uuid.uuid4().hex[:6]}.md", lifecycle_status=status,
            )
            db.add(d)
            await db.flush()
            if approved_days_ago is not None:
                db.add(DocumentTransition(
                    document_id=d.id, from_status="in_review", to_status="approved",
                    created_at=datetime.utcnow() - timedelta(days=approved_days_ago),
                ))
            if published_days_ago is not None:
                db.add(DocumentTransition(
                    document_id=d.id, from_status="approved", to_status="published",
                    created_at=datetime.utcnow() - timedelta(days=published_days_ago),
                ))
            await db.flush()
            return org

        check = dict(type="documents_approved", generator="governance-report")
        old = await doc("approved", 100)
        assert (await _gate(db, old, **check))["met"] is True
        stale = await _gate(db, old, within_days=90, **check)
        assert stale["met"] is False and "in the last 90 days" in stale["detail"]
        # Approved recently but since edited back to review: not approved now.
        edited = await doc("in_review", 5)
        assert (await _gate(db, edited, within_days=90, **check))["met"] is False
        recent = await doc("published", 10)
        assert (await _gate(db, recent, within_days=90, **check))["met"] is True
        assert (await _gate(db, recent, within_days=5, **check))["met"] is False
        # Publishing an old approval is not a fresh one.
        republished = await doc("published", 100, published_days_ago=5)
        assert (await _gate(db, republished, within_days=90, **check))["met"] is False
        # Another organisation's recent approval does not count.
        assert (await _gate(db, await _org(db), within_days=90, **check))["met"] is False


@requires_postgres
@pytest.mark.asyncio
class TestGovernanceReport:
    async def test_the_figures_are_this_organisations(self, db, no_commit):
        org, other = await _org(db), await _org(db)
        heavy = (await _controls(db, 1, heavy=True))[0]
        light = (await _controls(db, 1, heavy=False))[0]
        material = await _scope(db, org, heavy, implementation_status="implemented",
                                last_validated_at=_ago(months=1))
        await _scope(db, org, light, last_validated_at=_ago(months=6))  # validated, not recently
        await _deficiency(db, org, material, title="Undated gap")
        await _deficiency(db, org, material, title="Dated gap", due_date=TODAY + timedelta(days=9))
        # Owned by a user or by a named person: neither counts as without an owner.
        await _deficiency(db, org, material, title="Owned gap", due_date=TODAY + timedelta(days=20),
                          owner_user_id=(await _user(db, org)).id)
        await _deficiency(db, org, material, title="Named gap", due_date=TODAY + timedelta(days=30),
                          owner_name="Ops lead")
        await _deficiency(db, org, material, status="completed", completed_date=TODAY,
                          retest_note="Retested: fixed")
        db.add(RiskAssessment(organization_id=org.id, risk_code="R-ORG-1", treatment_status="treating"))
        # Another organisation's figures must not leak in.
        theirs = await _scope(db, other, heavy, implementation_status="implemented")
        await _deficiency(db, other, theirs)
        # The quarter-ago comparison: the latest snapshot at least three months back.
        for months, n in ((4, 0), (3, 1), (2, 99)):
            db.add(PostureSnapshot(organization_id=org.id, trigger="scheduled", counts={"in_scope": n},
                                   period=TODAY.replace(day=1) - relativedelta(months=months)))
        # A vendor's compensating control is not one of the organisation's own.
        vendor = Vendor(organization_id=org.id, name=f"pig-vendor-{_uuid.uuid4().hex[:6]}")
        db.add(vendor)
        await db.flush()
        db.add(VendorCompensatingControl(organization_id=org.id, vendor_id=vendor.id, gap_description="g",
                                         compensating_control="c", effectiveness_rating="partial"))
        await db.flush()

        data = await db.run_sync(lambda s: due_care.governance_data(s, org.id, TODAY))
        assert data["posture"]["in_scope"] == 2
        assert (data["implemented"], data["material_implemented"]) == (1, 1)
        assert data["posture"]["deficiencies"]["open"] == 4
        assert [d["title"] for d in data["material_open_deficiencies"]] == [
            "Dated gap", "Owned gap", "Named gap", "Undated gap"]
        assert data["deficiencies_completed_recently"] == 1
        assert data["deficiencies_without_owner"] == 2
        assert data["risk_treatment"] == {"treating": 1}
        assert data["validated_recently"] == 1
        assert data["previous_snapshot"]["counts"] == {"in_scope": 1}
        assert data["compensating_controls"] == 0
        assert due_care.red_flags(data) == ["1 material deficiency(ies) without a due date."]

    async def test_build_context_reads_governance_only_when_asked(self, db, no_commit):
        from services.doc_gen.context import build_context

        org = await _org(db)
        (a,) = await _controls(db, 1)
        await _scope(db, org, a)
        plain = await db.run_sync(lambda s: build_context(s, org.id))
        assert plain.governance == {}
        ctx = await db.run_sync(lambda s: build_context(s, org.id, include_governance=True))
        assert ctx.governance["posture"]["in_scope"] == 1
        out = render_governance_report(ctx)
        assert "| Controls in scope | 1 |" in out

    async def test_the_generator_runs_end_to_end_with_the_governance_figures(self, db, no_commit):
        from services.doc_gen.pipeline import run_generation

        org = await _org(db)
        db.add(DocGenSettings(organization_id=org.id, enabled=True, licence_acknowledged_at=datetime.utcnow()))
        (a,) = await _controls(db, 1)
        await _scope(db, org, a, implementation_status="implemented")
        result = await db.run_sync(lambda s: run_generation(
            s, organization_id=str(org.id), generator_name="governance-report",
        ))
        assert result.action == "created"
        doc = (await db.execute(select(GeneratedDocument).where(
            GeneratedDocument.organization_id == org.id,
            GeneratedDocument.generator_name == "governance-report",
        ))).scalar_one()
        assert doc.lifecycle_status == "draft"
        assert "| Controls in scope | 1 |" in doc.merged_content
        assert "no documented exceptions" in doc.merged_content
