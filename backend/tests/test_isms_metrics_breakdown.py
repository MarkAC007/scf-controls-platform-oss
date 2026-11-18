# SPDX-License-Identifier: AGPL-3.0-only
"""Phase 2: breakdowns, supporting measures, the audit rule, and the detail additions.

Against a real Postgres on a transaction that is always rolled back; the
database tests skip (never pass) when no database resolves.
"""
import uuid as _uuid
from datetime import date, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from models import (
    CapabilityTest,
    ControlTeamAssignment,
    CustomRiskDefinition,
    EvidenceAssessment,
    EvidenceCollectionTask,
    EvidenceFile,
    EvidenceTracking,
    Function,
    IsmsProfile,
    ObjectiveMetric,
    Organization,
    OrganizationMember,
    RiskAssessment,
    ScopedControl,
    SecurityObjective,
    Team,
    User,
    Vendor,
    VendorActionItem,
    VendorAssessment,
)
from services.isms_metrics import library, service, starters, status as status_rules, wording
from services.isms_metrics.schemas import MetricDetail, MetricSpec, ObjectiveCreate
from services.isms_metrics.service import Actor

from test_journey_service import DATABASE_URL, requires_postgres  # noqa: F401

TODAY = date(2026, 11, 18)
OUTCOME = ("risk_within_appetite", "open_material_deficiencies", "deficiencies_corrected_in_time",
           "evidence_collected_on_time", "supplier_assurance_current", "controls_implemented",
           "response_tests_passed", "audit_programme_completed")


# ---------------------------------------------------------------------------
# Pure rules
# ---------------------------------------------------------------------------

def test_every_outcome_measure_has_a_breakdown_and_supporting_ones_are_never_starters():
    for key in OUTCOME:
        assert library.REGISTRY[key].breakdown is not None, key
    supporting = {m.key for m in library.MEASURES if m.group == library.SUPPORTING}
    assert len(supporting) == 7
    seeded = {s.measure_key for s in starters.STARTERS if s.measure_key}
    assert seeded and not supporting & seeded


def test_reviewable_statuses_match_the_review_queue():
    from api.evidence_assessment import REVIEWABLE_STATUSES
    assert library.REVIEWABLE_STATUSES == REVIEWABLE_STATUSES


def test_planned_date_is_read_defensively():
    assert library.planned_date({"planned_date": "2026-03-01"}) == date(2026, 3, 1)
    for bad in ("2026-02-30", "01/03/2026", "20260301", "", None, 20260301, "2026-3-1"):
        assert library.planned_date({"planned_date": bad}) is None, bad
    assert library.planned_date({}) is None


def test_change_text():
    assert wording.change_text("percent", 2.0, 60) == "up 2 points in the last 60 days, to the last stored measurement"
    assert wording.change_text("count", -3.0, 60) == "down 3 in the last 60 days, to the last stored measurement"
    assert wording.change_text("percent", 1.0, 60) == "up 1 point in the last 60 days, to the last stored measurement"
    assert wording.change_text("percent", 0.04, 60) == "no change in the last 60 days, to the last stored measurement"
    assert wording.change_text("percent", -2.45, 60) == "down 2.5 points in the last 60 days, to the last stored measurement"


def _result(on, value, source="computed", unit="percent", created=None):
    return {"measured_on": on, "value": value, "source": source, "unit": unit,
            "created_at": created or datetime.combine(on, datetime.min.time())}


def test_change_compares_newest_with_oldest_in_sixty_days_of_the_same_unit_and_source():
    live = ObjectiveMetric(kind="live", unit="percent")
    results = [
        _result(TODAY, 70), _result(TODAY - timedelta(days=30), 65), _result(TODAY - timedelta(days=55), 60),
        _result(TODAY - timedelta(days=61), 10),                  # outside the window
        _result(TODAY - timedelta(days=60), 99, unit="count"),    # another unit, oldest in the window
        _result(TODAY - timedelta(days=58), 1, source="logged"),  # another source
    ]
    change = service.change_over(results, live, TODAY)
    assert change == {"delta": 10.0, "days": 60, "text": "up 10 points in the last 60 days, to the last stored measurement"}
    assert service.change_over(results[:1], live, TODAY) is None
    logged = ObjectiveMetric(kind="logged", unit="percent")
    assert service.change_over(results, logged, TODAY) is None  # one logged result only
    assert service.change_over(results, ObjectiveMetric(kind="milestone", unit="percent"), TODAY) is None
    assert service.change_over(results, None, TODAY) is None


# ---------------------------------------------------------------------------
# Database fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db():
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


async def _org(db, settings=None):
    tag = _uuid.uuid4().hex[:10]
    org = Organization(name=f"isms-{tag}", slug=f"isms-{tag}", settings=settings or {})
    db.add(org)
    await db.flush()
    return org


async def _user(db, org=None, name=None):
    tag = _uuid.uuid4().hex[:10]
    user = User(google_sub=f"isms-{tag}", email=f"isms-{tag}@example.invalid", display_name=name or f"Person {tag}")
    db.add(user)
    await db.flush()
    if org is not None:
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role="admin"))
        await db.flush()
    return user


async def _team(db, org, name="Security"):
    function = (await db.execute(select(Function).limit(1))).scalar_one_or_none()
    if function is None:
        pytest.skip("no function rows to hang a team on")
    team = Team(organization_id=org.id, function_id=function.id, name=f"{name} {_uuid.uuid4().hex[:6]}")
    db.add(team)
    await db.flush()
    return team


def _ago(days):
    return datetime.combine(TODAY - timedelta(days=days), datetime.min.time())


async def _populate(db, org, owner, tag="A"):
    """Rows that make every measure with a breakdown fail at least once."""
    for n in range(1, 5):
        db.add(CustomRiskDefinition(organization_id=org.id, risk_code=f"R-ORG-{tag}{n}", title=f"Risk {tag}{n}",
                                    description="t"))
    db.add_all([
        RiskAssessment(organization_id=org.id, risk_code=f"R-ORG-{tag}1", likelihood=5, impact=5,
                       owner_user_id=owner.id, treatment_status="treating", treatment_due_date=TODAY - timedelta(days=3)),
        RiskAssessment(organization_id=org.id, risk_code=f"R-ORG-{tag}2", likelihood=4, impact=4,
                       treatment_status="treating"),
        RiskAssessment(organization_id=org.id, risk_code=f"R-ORG-{tag}3", likelihood=1, impact=1),
    ])
    material = ScopedControl(organization_id=org.id, scf_id="GOV-01", selected=True, material_override=True,
                             material_reason="k", implementation_status="in_progress",
                             target_maturity_override="L3", target_maturity_reason="t", maturity_level="L1")
    ordinary = ScopedControl(organization_id=org.id, scf_id="GOV-02", selected=True, material_override=False,
                             material_reason="m", implementation_status="implemented",
                             target_maturity_override="L2", target_maturity_reason="t", maturity_level="L2")
    db.add_all([material, ordinary])
    await db.flush()
    db.add_all([
        VendorActionItem(organization_id=org.id, title=f"Material {tag}", scoped_control_id=material.id,
                         scf_id="GOV-01", created_at=_ago(120), owner_user_id=owner.id),
        VendorActionItem(organization_id=org.id, title=f"Ordinary {tag}", scoped_control_id=ordinary.id,
                         scf_id="GOV-02", created_at=_ago(5)),
        VendorActionItem(organization_id=org.id, title=f"Late {tag}", status="completed", retest_note="r",
                         created_at=_ago(200), completed_date=(TODAY - timedelta(days=10))),
    ])
    tracking = EvidenceTracking(organization_id=org.id, evidence_id=f"E-{tag}-1")
    db.add(tracking)
    await db.flush()
    db.add_all([
        EvidenceCollectionTask(evidence_tracking_id=tracking.id, organization_id=org.id, title="t",
                               status="not_started", due_date=TODAY - timedelta(days=4), assigned_user_id=owner.id),
        EvidenceCollectionTask(evidence_tracking_id=tracking.id, organization_id=org.id, title="t",
                               status="completed", due_date=TODAY - timedelta(days=4),
                               completed_date=TODAY - timedelta(days=5)),
    ])
    db.add(Vendor(organization_id=org.id, name=f"Supplier {tag}", criticality="critical", status="active"))
    db.add_all([
        CapabilityTest(organization_id=org.id, test_type="incident_response", title=f"Drill {tag}", scenario="s",
                       outcome="o", result="failed", tested_on=TODAY - timedelta(days=9)),
        CapabilityTest(organization_id=org.id, test_type="continuity", title="ok", scenario="s", outcome="o",
                       result="passed", tested_on=TODAY - timedelta(days=9)),
    ])
    db.add(IsmsProfile(organization_id=org.id, audit_programme=[
        {"area": f"Access {tag}", "status": "planned", "planned_date": "2026-01-10"},
        {"area": "Done", "status": "completed", "planned_date": "2026-02-10"},
    ]))
    file = EvidenceFile(organization_id=org.id, evidence_id=f"E-{tag}-1", filename=f"f{tag}.pdf",
                        s3_key=f"isms/{_uuid.uuid4().hex}", content_type="application/pdf", file_size_bytes=1)
    db.add(file)
    await db.flush()
    db.add(EvidenceAssessment(evidence_file_id=file.id, organization_id=org.id, evidence_id=f"E-{tag}-1",
                              status="partial"))
    await db.flush()


def _failing(key, m):
    """What the calculator counts against: the breakdown must list exactly this many."""
    if key == "response_tests_passed":
        return {s["label"]: s["count"] for s in m.segments}["failed"]
    if library.REGISTRY[key].unit == "count":
        return m.numerator
    return m.denominator - m.numerator


# ---------------------------------------------------------------------------
# Breakdowns
# ---------------------------------------------------------------------------

@requires_postgres
@pytest.mark.asyncio
class TestBreakdowns:
    async def test_breakdown_total_equals_the_calculators_failing_count_for_every_measure(self, db):
        org = await _org(db)
        await _populate(db, org, await _user(db, org))
        for key, rows_fn in library.ROWS.items():
            m = await library.measure(db, org.id, key, None, TODAY)
            b = await library.breakdown(db, org.id, key, None, TODAY, 50)
            failing = _failing(key, m)
            assert failing > 0, key  # the test is only worth something if each measure fails here
            assert b is not None and b["total"] == failing, key
            assert len(b["rows"]) == min(failing, 50), key
            assert b["title"] and b["register"]["key"] is None and b["register_label"], key
            assert {c["key"] for c in b["columns"]} == set(b["rows"][0]["cells"]), key

    async def test_no_row_or_name_crosses_between_organisations(self, db):
        a, b = await _org(db), await _org(db)
        alpha = await _user(db, a, name="Alpha Owner")
        bravo = await _user(db, b, name="Bravo Owner")
        await _populate(db, a, alpha, "A")
        await _populate(db, b, bravo, "B")
        # A row in A pointing at someone who is a member of B only (a direct database edit): no name.
        db.add(CustomRiskDefinition(organization_id=a.id, risk_code="R-ORG-AX", title="Stray", description="t"))
        db.add(RiskAssessment(organization_id=a.id, risk_code="R-ORG-AX", likelihood=5, impact=5,
                              owner_user_id=bravo.id))
        await db.flush()
        b_keys = set()
        for key in library.ROWS:
            got = await library.breakdown(db, b.id, key, None, TODAY, 50)
            b_keys |= {r["key"] for r in got["rows"]} if got else set()
        for key in library.ROWS:
            got = await library.breakdown(db, a.id, key, None, TODAY, 50)
            text = repr(got)
            assert "Bravo" not in text and "Supplier B" not in text and "Material B" not in text, key
            assert "Drill B" not in text and "E-B-1" not in text and "Access B" not in text, key
            # Record ids and B's own risk codes; catalogue codes, SCF ids and audit positions are shared names.
            own = {r["key"] for r in got["rows"]} if got else set()
            assert not own & {k for k in b_keys if len(k) == 36 or k.startswith("R-ORG-B")}, key
        risks = await library.breakdown(db, a.id, "risk_within_appetite", None, TODAY, 50)
        owners = {r["key"]: r["cells"]["owner"] for r in risks["rows"]}
        assert owners["R-ORG-A1"] == "Alpha Owner" and owners["R-ORG-AX"] == "—"

    async def test_limit_and_worst_first(self, db):
        org = await _org(db)
        for n, (lik, imp) in enumerate([(4, 4), (5, 5), (5, 4), (1, 1)], start=1):
            db.add(CustomRiskDefinition(organization_id=org.id, risk_code=f"R-ORG-{n}", title=f"R{n}", description="t"))
            db.add(RiskAssessment(organization_id=org.id, risk_code=f"R-ORG-{n}", likelihood=lik, impact=imp))
        db.add(CustomRiskDefinition(organization_id=org.id, risk_code="R-ORG-9", title="R9", description="t"))
        await db.flush()
        full = await library.breakdown(db, org.id, "risk_within_appetite", None, TODAY, 50)
        above = [r["key"] for r in full["rows"] if r["group"] == "above appetite"]
        assert above == ["R-ORG-2", "R-ORG-3", "R-ORG-1"]  # 25, 20, 16: score high to low
        assert full["rows"][:3] == [r for r in full["rows"] if r["group"] == "above appetite"]
        assert full["rows"][0]["cells"]["level"] == "Critical · 25"
        assert full["title"].startswith("3 risks above appetite · ")
        two = await library.breakdown(db, org.id, "risk_within_appetite", None, TODAY, 2)
        assert [r["key"] for r in two["rows"]] == ["R-ORG-2", "R-ORG-3"] and two["total"] == full["total"]

    async def test_nothing_holding_back_is_null(self, db):
        org = await _org(db)
        db.add(ScopedControl(organization_id=org.id, scf_id="GOV-01", selected=True,
                             implementation_status="implemented"))
        await db.flush()
        assert await library.breakdown(db, org.id, "controls_implemented", None, TODAY, 10) is None
        assert await library.breakdown(db, org.id, "response_tests_passed", None, TODAY, 10) is None
        assert await library.breakdown(db, org.id, "open_deficiency_average_age", None, TODAY, 10) is None


@requires_postgres
@pytest.mark.asyncio
class TestDetail:
    async def _live_objective(self, db, org):
        await service.list_objectives(db, org.id, TODAY)  # seeds the starters
        objective = (await db.execute(select(SecurityObjective).where(
            SecurityObjective.organization_id == org.id, SecurityObjective.source == "starter:controls_implemented",
        ))).scalar_one()
        return objective

    async def test_breakdown_null_for_logged_milestone_unset_closed_and_archived(self, db):
        org = await _org(db)
        db.add(ScopedControl(organization_id=org.id, scf_id="GOV-01", selected=True, implementation_status=None))
        await db.flush()
        live = ObjectiveMetric(kind="live", measure_key="controls_implemented", params={})
        open_ = SecurityObjective(lifecycle_status="active", archived_at=None)
        assert await service._breakdown_safely(db, org.id, open_, live, TODAY, 10) is not None  # the control
        for metric in (ObjectiveMetric(kind="logged", measure_key="controls_implemented"),
                       ObjectiveMetric(kind="milestone", measure_key="controls_implemented"),
                       ObjectiveMetric(kind="unset"), ObjectiveMetric(kind="logged"), None):
            assert await service._breakdown_safely(db, org.id, open_, metric, TODAY, 10) is None
        for closed in ("achieved", "missed"):
            assert await service._breakdown_safely(
                db, org.id, SecurityObjective(lifecycle_status=closed), live, TODAY, 10) is None
        archived = SecurityObjective(lifecycle_status="active", archived_at=datetime.utcnow())
        assert await service._breakdown_safely(db, org.id, archived, live, TODAY, 10) is None

    async def test_detail_carries_breakdown_targets_and_raised_deficiencies(self, db):
        org = await _org(db)
        db.add(ScopedControl(organization_id=org.id, scf_id="GOV-01", selected=True, implementation_status=None))
        await db.flush()
        objective = await self._live_objective(db, org)
        other_org = await _org(db)
        vendor = Vendor(organization_id=org.id, name="V")
        db.add(vendor)
        await db.flush()
        ref = str(objective.id)
        # Inserted directly: the count must not depend on the create route.
        db.add_all([
            VendorActionItem(organization_id=org.id, title="Off track", source="security_objective", source_ref=ref),
            VendorActionItem(organization_id=org.id, title="Working on it", source="security_objective",
                             source_ref=ref, status="in_progress"),
            VendorActionItem(organization_id=org.id, title="closed", source="security_objective",
                             source_ref=ref, status="cancelled"),
            VendorActionItem(organization_id=org.id, title="done", source="security_objective",
                             source_ref=ref, status="completed", retest_note="r"),
            VendorActionItem(organization_id=org.id, title="vendor item", source="security_objective",
                             source_ref=ref, vendor_id=vendor.id),
            VendorActionItem(organization_id=org.id, title="other source", source="manual", source_ref=ref),
            VendorActionItem(organization_id=org.id, title="other objective", source="security_objective",
                             source_ref=str(_uuid.uuid4())),
            VendorActionItem(organization_id=other_org.id, title="same id text, other organisation",
                             source="security_objective", source_ref=ref),
        ])
        await db.flush()
        await service.record_nightly_results(db, org.id, TODAY)
        detail = await service.metric_detail(db, org.id, objective.id, TODAY, breakdown_limit=1)
        MetricDetail.model_validate(detail)
        assert detail["breakdown"]["total"] == 1 and len(detail["breakdown"]["rows"]) == 1
        assert detail["raised_deficiencies"] == 2  # open and in progress, here, from this objective
        metric = next(m for m in detail["metric_versions"])
        for r in detail["results"]:
            assert (r["target_value"], r["warn_value"], r["direction"], r["unit"]) == (
                metric["target_value"], metric["warn_value"], metric["direction"], metric["unit"])
        assert detail["change"] is None  # one result so far

    async def test_each_result_carries_the_target_in_force_then_and_the_change(self, db):
        org = await _org(db)
        admin = await _user(db, org)
        spec = MetricSpec(kind="logged", name="Training completion (%)", unit="percent", direction="at_least",
                          target_value=95, warn_value=85, frequency="quarterly")
        actor = Actor(user_id=admin.id, action_source="ui")
        out = await service.create_objective(db, org.id, ObjectiveCreate(
            name="Train people", planned_actions="Run the course", metric=spec), actor, TODAY)
        await service.log_result(db, org.id, out["id"], 90, actor, TODAY - timedelta(days=20))
        await service.set_metric(db, org.id, out["id"], spec.model_copy(update={"target_value": 97}),
                                 actor, "admin", TODAY)
        await service.log_result(db, org.id, out["id"], 93, actor, TODAY)
        detail = await service.metric_detail(db, org.id, out["id"], TODAY)
        MetricDetail.model_validate(detail)
        assert [(r["value"], r["target_value"], r["warn_value"]) for r in detail["results"]] == [
            (93.0, 97.0, 85.0), (90.0, 95.0, 85.0)]
        assert {(r["direction"], r["unit"]) for r in detail["results"]} == {("at_least", "percent")}
        assert detail["change"] == {"delta": 3.0, "days": 60, "text": "up 3 points in the last 60 days, to the last stored measurement"}
        assert detail["breakdown"] is None and detail["raised_deficiencies"] == 0

    @pytest.mark.parametrize("how", ["python", "sql"])
    async def test_a_failing_breakdown_leaves_the_call_at_200_with_null(self, db, monkeypatch, how):
        from sqlalchemy import text

        from api import security_objectives as api
        org = await _org(db)
        admin = await _user(db, org)
        db.add(ScopedControl(organization_id=org.id, scf_id="GOV-01", selected=True, implementation_status=None))
        await db.flush()
        objective = await self._live_objective(db, org)

        async def broken(session, org_id, key, params, today, limit):
            if how == "sql":
                await session.execute(text("SELECT 1/0"))
            raise ZeroDivisionError("a shape nobody expected")

        monkeypatch.setattr(service, "run_breakdown", broken)
        membership = type("M", (), {"user_id": admin.id, "role": "admin", "organization_id": org.id})()
        detail = await api.get_isms_metric(org_id=org.id, objective_id=objective.id, breakdown_limit=10,
                                           membership=membership, db=db)
        assert detail["breakdown"] is None
        assert detail["objective"]["reading"]["value"] == 0.0  # the reading itself still works
        MetricDetail.model_validate(detail)


# ---------------------------------------------------------------------------
# Supporting measures, on real rows
# ---------------------------------------------------------------------------

async def _run(db, org, key, params=None):
    return await library.measure(db, org.id, key, params, TODAY)


def _segs(m):
    return {s["label"]: s["count"] for s in m.segments}


@requires_postgres
@pytest.mark.asyncio
class TestSupportingMeasures:
    async def test_risks_assessed(self, db):
        org = await _org(db)
        user = await _user(db, org)
        for n in range(1, 5):
            db.add(CustomRiskDefinition(organization_id=org.id, risk_code=f"R-ORG-{n}", title=f"R{n}", description="t"))
        db.add_all([
            RiskAssessment(organization_id=org.id, risk_code="R-ORG-1", likelihood=2, impact=2),
            RiskAssessment(organization_id=org.id, risk_code="R-ORG-2", likelihood=2),  # half scored
            # accepted but never scored: still counts against
            RiskAssessment(organization_id=org.id, risk_code="R-ORG-4", treatment_status="accepted",
                           accepted_by=user.id, accepted_at=datetime.utcnow(), acceptance_rationale="x",
                           acceptance_expires_on=TODAY + timedelta(days=9)),
        ])  # R-ORG-3 has no assessment at all: a missing record counts against
        await db.flush()
        m = await _run(db, org, "risks_assessed")
        appetite = await _run(db, org, "risk_within_appetite")
        assert m.denominator == appetite.denominator  # the same register
        assert m.numerator == 1 and _segs(m)["not assessed"] == m.denominator - 1

    async def test_risk_treatments_on_time(self, db):
        org = await _org(db)
        rows = [
            dict(treatment_status="treating", treatment_due_date=TODAY + timedelta(days=5)),   # good
            dict(treatment_status="treating", treatment_due_date=TODAY),                       # good: due today
            dict(treatment_status="treating", treatment_due_date=TODAY - timedelta(days=1)),   # overdue
            dict(treatment_status="treating"),                                                  # no due date
            dict(treatment_status="analysed", treatment_plan="Buy a firewall"),                 # planned, no date
            dict(treatment_status="treated", treatment_due_date=TODAY - timedelta(days=90)),   # done: out
            dict(treatment_status="identified"),                                                # nothing yet: out
        ]
        for n, kw in enumerate(rows, start=1):
            db.add(CustomRiskDefinition(organization_id=org.id, risk_code=f"R-ORG-{n}", title="r", description="t"))
            db.add(RiskAssessment(organization_id=org.id, risk_code=f"R-ORG-{n}", likelihood=3, impact=3, **kw))
        await db.flush()
        m = await _run(db, org, "risk_treatments_on_time")
        assert (m.numerator, m.denominator) == (2, 5)
        assert _segs(m) == {"overdue": 1, "no due date": 2}
        b = await library.breakdown(db, org.id, "risk_treatments_on_time", None, TODAY, 10)
        assert [r["key"] for r in b["rows"]][0] == "R-ORG-3" and b["total"] == 3

    async def test_controls_with_team(self, db):
        org = await _org(db)
        team = await _team(db, org)
        owned = ScopedControl(organization_id=org.id, scf_id="TST-01", selected=True)
        consulted = ScopedControl(organization_id=org.id, scf_id="TST-02", selected=True)
        bare = ScopedControl(organization_id=org.id, scf_id="TST-03", selected=True)
        out = ScopedControl(organization_id=org.id, scf_id="TST-04", selected=False)
        db.add_all([owned, consulted, bare, out])
        await db.flush()
        db.add_all([
            ControlTeamAssignment(scoped_control_id=owned.id, team_id=team.id, organization_id=org.id,
                                  is_accountable=True),
            ControlTeamAssignment(scoped_control_id=consulted.id, team_id=team.id, organization_id=org.id,
                                  is_accountable=False),
            ControlTeamAssignment(scoped_control_id=out.id, team_id=team.id, organization_id=org.id,
                                  is_accountable=True),
        ])
        await db.flush()
        m = await _run(db, org, "controls_with_team")
        assert (m.numerator, m.denominator) == (1, 3)
        b = await library.breakdown(db, org.id, "controls_with_team", None, TODAY, 10)
        assert [r["key"] for r in b["rows"]] == ["TST-02", "TST-03"]
        implemented = await library.breakdown(db, org.id, "controls_implemented", None, TODAY, 10)
        assert {r["key"]: r["cells"]["team"] for r in implemented["rows"]}["TST-01"] == team.name

    async def test_controls_at_target_maturity(self, db):
        org = await _org(db)
        db.add_all([
            ScopedControl(organization_id=org.id, scf_id="TST-01", selected=True,
                          target_maturity_override="L3", target_maturity_reason="t", maturity_level="L3"),     # at target
            ScopedControl(organization_id=org.id, scf_id="TST-02", selected=True,
                          target_maturity_override="L3", target_maturity_reason="t", maturity_level="L2"),     # below
            ScopedControl(organization_id=org.id, scf_id="TST-03", selected=True,
                          target_maturity_override="L2", target_maturity_reason="t"),                          # no current level: against
            ScopedControl(organization_id=org.id, scf_id="TST-04", selected=True, maturity_level="L5"),  # no target
            ScopedControl(organization_id=org.id, scf_id="TST-05", selected=True,
                          target_maturity_override="L2", target_maturity_reason="t", maturity_level=" l4 "),   # above, normalised
            ScopedControl(organization_id=org.id, scf_id="TST-06", selected=True,
                          target_maturity_override="L2", target_maturity_reason="t", maturity_level="Level 4"),  # not a level: unrated
        ])
        await db.flush()
        m = await _run(db, org, "controls_at_target_maturity")
        assert (m.numerator, m.denominator) == (2, 5)
        assert _segs(m) == {"no target set": 1}

    async def test_controls_at_target_maturity_inherits_the_domain_target(self, db):
        org = await _org(db, settings={"maturity_targets": {"TST": "L3"}})
        db.add_all([
            ScopedControl(organization_id=org.id, scf_id="TST-01", selected=True, maturity_level="L3"),
            ScopedControl(organization_id=org.id, scf_id="TST-02", selected=True, maturity_level="L1"),
        ])
        await db.flush()
        m = await _run(db, org, "controls_at_target_maturity")
        stored = library.target_maturity.stored_domain_targets(org.settings)
        assert stored.get("TST") == "L3", "domain target not accepted by stored_domain_targets"
        assert (m.numerator, m.denominator) == (1, 2)

    async def test_open_deficiencies_older_than_and_average_age(self, db):
        org = await _org(db)
        vendor = Vendor(organization_id=org.id, name="V")
        db.add(vendor)
        await db.flush()
        db.add_all([
            VendorActionItem(organization_id=org.id, title="old", created_at=_ago(100)),
            VendorActionItem(organization_id=org.id, title="new", created_at=_ago(10), status="in_progress"),
            VendorActionItem(organization_id=org.id, title="closed", created_at=_ago(300), status="cancelled"),
            VendorActionItem(organization_id=org.id, title="vendor item", created_at=_ago(300), vendor_id=vendor.id),
        ])
        await db.flush()
        m = await _run(db, org, "open_deficiencies_older_than")
        assert (m.value, m.numerator, m.denominator) == (1.0, 1, 2)
        assert (await _run(db, org, "open_deficiencies_older_than", {"days": 5})).value == 2.0
        assert (await _run(db, org, "open_deficiency_average_age")).value == 55.0
        assert (await _run(db, await _org(db), "open_deficiency_average_age")).value == 0.0

    async def test_ai_verdicts_awaiting_review(self, db):
        org = await _org(db)
        reviewer = await _user(db, org)

        async def verdict(status, decision=None, deleted=False):
            f = EvidenceFile(organization_id=org.id, evidence_id="E-1", filename="f", s3_key=_uuid.uuid4().hex,
                             content_type="text/plain", file_size_bytes=1, is_deleted=deleted)
            db.add(f)
            await db.flush()
            db.add(EvidenceAssessment(evidence_file_id=f.id, organization_id=org.id, evidence_id="E-1",
                                      status=status, review_decision=decision,
                                      reviewed_by_user_id=reviewer.id if decision else None))
        for args in (("partial",), ("insufficient",), ("sufficient", "confirmed"), ("pending",), ("error",),
                     ("partial", None, True)):
            await verdict(*args)
        await db.flush()
        m = await _run(db, org, "ai_verdicts_awaiting_review")
        assert m.value == 2.0


# ---------------------------------------------------------------------------
# The audit programme rule (contract section 3)
# ---------------------------------------------------------------------------

@requires_postgres
@pytest.mark.asyncio
class TestAuditRule:
    async def _audits(self, db, entries):
        org = await _org(db)
        db.add(IsmsProfile(organization_id=org.id, audit_programme=entries))
        await db.flush()
        return org, await _run(db, org, "audit_programme_completed")

    async def test_all_dated_in_the_future_reads_insufficient_data(self, db):
        _, m = await self._audits(db, [
            {"area": "A", "status": "planned", "planned_date": "2027-03-01"},
            {"area": "B", "status": "in_progress", "planned_date": "2026-12-01"},
        ])
        assert (m.value, m.numerator, m.denominator) == (None, 0, 0)
        assert _segs(m)["not yet due"] == 2
        metric = ObjectiveMetric(kind="live", unit="percent", direction="at_least", target_value=100, min_population=1)
        assert status_rules.derive_status("active", "maintain", None, metric, m.value, m.denominator,
                                          None, False, TODAY) == "insufficient_data"

    async def test_an_audit_planned_today_and_not_done_counts_against(self, db):
        org, m = await self._audits(db, [{"area": "Today", "status": "planned", "planned_date": TODAY.isoformat()}])
        assert (m.numerator, m.denominator) == (0, 1)
        assert _segs(m)["not yet due"] == 0
        b = await library.breakdown(db, org.id, "audit_programme_completed", None, TODAY, 10)
        assert [(r["group"], r["cells"]["area"]) for r in b["rows"]] == [("not completed", "Today")]

    async def test_overdue_unfinished_audit_three_years_old_stays_in(self, db):
        _, m = await self._audits(db, [{"area": "A", "status": "planned", "planned_date": "2023-11-01"}])
        assert (m.numerator, m.denominator) == (0, 1)

    async def test_undated_and_unparseable_audits_stay_in(self, db):
        _, m = await self._audits(db, [
            {"area": "A", "status": "planned"},
            {"area": "B", "status": "completed", "planned_date": "not a date"},
            {"area": "C", "status": "planned", "planned_date": "2026-02-31"},
        ])
        assert (m.numerator, m.denominator) == (1, 3)

    async def test_completed_audit_older_than_the_window_leaves(self, db):
        _, m = await self._audits(db, [
            {"area": "old", "status": "completed", "planned_date": "2025-01-01"},
            {"area": "recent", "status": "completed", "planned_date": "2026-01-01"},
            {"area": "edge", "status": "completed", "planned_date": "2025-11-18"},  # exactly 12 months: in
            {"area": "future done", "status": "completed", "planned_date": "2027-01-01"},
            {"area": "x", "status": "cancelled", "planned_date": "2026-05-01"},
        ])
        assert (m.numerator, m.denominator) == (3, 3)
        assert m.population == "3 of 3 audits due in the last 12 months"
        assert _segs(m) == {"cancelled": 1, "not yet due": 0}
        _, wide = await self._audits(db, [{"area": "old", "status": "completed", "planned_date": "2025-01-01"}])
        assert wide.denominator == 0
        org = await _org(db)
        db.add(IsmsProfile(organization_id=org.id, audit_programme=[
            {"area": "old", "status": "completed", "planned_date": "2025-01-01"}]))
        await db.flush()
        assert (await _run(db, org, "audit_programme_completed", {"months": 24})).denominator == 1

    async def test_audit_breakdown_order_and_planned_text(self, db):
        org, _ = await self._audits(db, [
            {"area": "undated", "status": "planned", "planned_period": "Q3 2026"},
            {"area": "late", "status": "planned", "planned_date": "2026-06-01", "auditor": "Sam"},
            {"area": "later", "status": "in_progress", "planned_date": "2026-09-01"},
        ])
        b = await library.breakdown(db, org.id, "audit_programme_completed", None, TODAY, 10)
        assert [r["cells"]["area"] for r in b["rows"]] == ["late", "later", "undated"]
        assert [r["cells"]["planned"] for r in b["rows"]] == ["1 Jun 2026", "1 Sep 2026", "Q3 2026"]
        # An undated audit counts as due, under a group of its own that says why it is listed.
        assert [r["group"] for r in b["rows"]] == ["not completed", "not completed", "no planned date"]
        assert b["title"] == "2 audits not completed · 1 with no planned date"
        assert b["rows"][0]["link"] == {"kind": "audit", "key": None}


def test_breakdown_limit_is_bounded_one_to_fifty_with_ten_by_default():
    import inspect
    from typing import get_args

    from api import security_objectives as api
    param = inspect.signature(api.get_isms_metric).parameters["breakdown_limit"]
    assert param.default == 10
    query = next(a for a in get_args(param.annotation)[1:])
    bounds = {type(m).__name__: m for m in query.metadata}
    assert bounds["Ge"].ge == 1 and bounds["Le"].le == 50


# ---------------------------------------------------------------------------
# Contract A1: a result says whether it measures the same thing as the metric in force
# ---------------------------------------------------------------------------

def _version(kind, **kw):
    return ObjectiveMetric(kind=kind, **kw)


def test_same_measure_rules():
    live = _version("live", measure_key="deficiencies_corrected_in_time", params={"days": 90})
    same = service.same_measure
    assert same(_version("live", measure_key="deficiencies_corrected_in_time", params={}), live)  # 90 is the default
    assert not same(_version("live", measure_key="deficiencies_corrected_in_time", params={"days": 30}), live)
    assert not same(_version("live", measure_key="controls_implemented", params={}), live)
    assert same(_version("live", measure_key="deficiencies_corrected_in_time", params={"days": 90},
                         target_value=50, warn_value=10, direction="at_most", frequency="monthly"), live)
    logged = _version("logged", name="Training completion (%)", unit="percent")
    assert same(_version("logged", name="  training COMPLETION (%) ", unit="percent", target_value=1), logged)
    assert not same(_version("logged", name="Training completion (%)", unit="count"), logged)
    assert not same(_version("logged", name="Phishing clicks", unit="percent"), logged)
    assert same(_version("milestone", name="Cert"), _version("milestone", name="ISO certificate"))
    assert not same(_version("logged", name="x", unit="percent"), _version("live", measure_key="x"))
    assert not same(live, None)


def test_change_leaves_out_results_under_another_measure():
    live = ObjectiveMetric(kind="live", unit="percent")
    rows = [_result(TODAY, 90), _result(TODAY - timedelta(days=30), 40)]
    rows[1]["same_measure"] = False
    assert service.change_over(rows, live, TODAY) is None


@requires_postgres
@pytest.mark.asyncio
class TestSameMeasure:
    async def _objective(self, db, measure_key, params=None, target=100):
        org = await _org(db)
        admin = await _user(db, org)
        actor = Actor(user_id=admin.id, action_source="ui")
        db.add(IsmsProfile(organization_id=org.id, audit_programme=[
            {"area": "Old", "status": "completed", "planned_date": "2025-03-01"},
            {"area": "New", "status": "completed", "planned_date": "2026-03-01"},
        ]))
        db.add(ScopedControl(organization_id=org.id, scf_id="GOV-01", selected=True, implementation_status=None))
        await db.flush()
        spec = MetricSpec(kind="live", measure_key=measure_key, params=params, target_value=target)
        out = await service.create_objective(db, org.id, ObjectiveCreate(
            name="Audit", planned_actions="Audit things", metric=spec), actor, TODAY)
        return org, actor, out["id"]

    async def _switch_and_read(self, db, measure_key, params, new_spec):
        org, actor, objective_id = await self._objective(db, measure_key, params)
        await service.record_nightly_results(db, org.id, TODAY - timedelta(days=30))
        await service.set_metric(db, org.id, objective_id, new_spec, actor, "admin", TODAY)
        await service.record_nightly_results(db, org.id, TODAY)
        detail = await service.metric_detail(db, org.id, objective_id, TODAY)
        MetricDetail.model_validate(detail)
        return detail

    async def test_a_change_of_measure_breaks_the_change_figure(self, db):
        detail = await self._switch_and_read(db, "audit_programme_completed", None, MetricSpec(
            kind="live", measure_key="controls_implemented", target_value=100))
        assert [(r["value"], r["same_measure"]) for r in detail["results"]] == [(0.0, True), (100.0, False)]
        assert detail["change"] is None

    async def test_a_change_of_settings_alone_breaks_it_too(self, db):
        detail = await self._switch_and_read(db, "audit_programme_completed", {"months": 12}, MetricSpec(
            kind="live", measure_key="audit_programme_completed", params={"months": 24}, target_value=100))
        assert [r["same_measure"] for r in detail["results"]] == [True, False]
        assert detail["change"] is None

    async def test_a_change_of_target_alone_keeps_the_change_figure(self, db):
        detail = await self._switch_and_read(db, "audit_programme_completed", None, MetricSpec(
            kind="live", measure_key="audit_programme_completed", target_value=90, warn_value=70))
        assert [r["same_measure"] for r in detail["results"]] == [True, True]
        assert [r["target_value"] for r in detail["results"]] == [90.0, 100.0]
        assert detail["change"] == {"delta": 0.0, "days": 60,
                                    "text": "no change in the last 60 days, to the last stored measurement"}


# ---------------------------------------------------------------------------
# Diff review M8: the order of each outcome measure's breakdown
# ---------------------------------------------------------------------------

async def _keys(db, org, key):
    b = await library.breakdown(db, org.id, key, None, TODAY, 50)
    return [r["cells"] for r in b["rows"]], [r["group"] for r in b["rows"]]


@requires_postgres
@pytest.mark.asyncio
class TestBreakdownOrder:
    async def test_open_material_deficiencies_oldest_first(self, db):
        org = await _org(db)
        control = ScopedControl(organization_id=org.id, scf_id="GOV-01", selected=True, material_override=True,
                                material_reason="k")
        db.add(control)
        await db.flush()
        for title, age in (("middle", 50), ("newest", 5), ("oldest", 300)):
            db.add(VendorActionItem(organization_id=org.id, title=title, scoped_control_id=control.id,
                                    scf_id="GOV-01", created_at=_ago(age)))
        await db.flush()
        cells, _ = await _keys(db, org, "open_material_deficiencies")
        assert [c["deficiency"] for c in cells] == ["oldest", "middle", "newest"]

    async def test_corrected_in_time_still_open_longest_first_then_completed_late(self, db):
        org = await _org(db)
        db.add_all([
            VendorActionItem(organization_id=org.id, title="late, quick", status="completed", retest_note="r",
                             created_at=_ago(200), completed_date=TODAY - timedelta(days=100)),
            VendorActionItem(organization_id=org.id, title="open, recent", created_at=_ago(95)),
            VendorActionItem(organization_id=org.id, title="late, slow", status="completed", retest_note="r",
                             created_at=_ago(300), completed_date=TODAY - timedelta(days=10)),
            VendorActionItem(organization_id=org.id, title="open, longest", created_at=_ago(250)),
        ])
        await db.flush()
        cells, groups = await _keys(db, org, "deficiencies_corrected_in_time")
        assert [c["deficiency"] for c in cells] == ["open, longest", "open, recent", "late, slow", "late, quick"]
        assert groups == ["still open", "still open", "completed late", "completed late"]

    async def test_evidence_most_overdue_first(self, db):
        org = await _org(db)
        tracking = EvidenceTracking(organization_id=org.id, evidence_id="E-1")
        db.add(tracking)
        await db.flush()
        for title, days in (("a week late", 7), ("a month late", 30), ("a day late", 1)):
            db.add(EvidenceCollectionTask(evidence_tracking_id=tracking.id, organization_id=org.id, title=title,
                                          status="not_started", due_date=TODAY - timedelta(days=days)))
        await db.flush()
        b = await library.breakdown(db, org.id, "evidence_collected_on_time", None, TODAY, 50)
        assert [r["cells"]["due"] for r in b["rows"]] == [
            library.day_text(TODAY - timedelta(days=d)) for d in (30, 7, 1)]

    async def test_suppliers_critical_first_then_by_name(self, db):
        org = await _org(db)
        db.add_all([
            Vendor(organization_id=org.id, name="Beta", criticality="high", status="active"),
            Vendor(organization_id=org.id, name="Zulu", criticality="critical", status="active"),
            Vendor(organization_id=org.id, name="Alpha", criticality="high", status="active"),
        ])
        await db.flush()
        cells, _ = await _keys(db, org, "supplier_assurance_current")
        assert [c["supplier"] for c in cells] == ["Zulu", "Alpha", "Beta"]

    async def test_response_tests_newest_first(self, db):
        org = await _org(db)
        for title, days in (("middle", 40), ("newest", 2), ("oldest", 200)):
            db.add(CapabilityTest(organization_id=org.id, test_type="incident_response", title=title, scenario="s",
                                  outcome="o", result="failed", tested_on=TODAY - timedelta(days=days)))
        await db.flush()
        cells, _ = await _keys(db, org, "response_tests_passed")
        assert [c["test"] for c in cells] == ["newest", "middle", "oldest"]

    async def test_controls_by_scf_id(self, db):
        org = await _org(db)
        for scf_id in ("TST-03", "GOV-02", "AST-10"):
            db.add(ScopedControl(organization_id=org.id, scf_id=scf_id, selected=True, implementation_status=None))
        await db.flush()
        b = await library.breakdown(db, org.id, "controls_implemented", None, TODAY, 50)
        assert [r["key"] for r in b["rows"]] == ["AST-10", "GOV-02", "TST-03"]
