# SPDX-License-Identifier: AGPL-3.0-only
"""The eight live ISMS measures against rows each test creates in a fresh organisation.

Runs on a transaction that is always rolled back; skips (never passes) when no
database resolves.
"""
import uuid as _uuid
from datetime import date, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from catalog_models import SCFCatalogRisk
from models import (
    CapabilityTest,
    CustomRiskDefinition,
    EvidenceCollectionTask,
    EvidenceTracking,
    IsmsProfile,
    Organization,
    RiskAssessment,
    ScopedControl,
    User,
    Vendor,
    VendorActionItem,
    VendorAssessment,
)
from services.doc_gen import context as doc_context
from services.isms_metrics import library

from test_journey_service import DATABASE_URL, requires_postgres  # noqa: F401

TODAY = date.today()


def test_registry_has_the_eight_outcome_and_seven_supporting_measures():
    by_group = {g: {m.key for m in library.MEASURES if m.group == g} for g in library.GROUPS}
    assert by_group["outcome"] == {
        "risk_within_appetite", "open_material_deficiencies", "deficiencies_corrected_in_time",
        "evidence_collected_on_time", "supplier_assurance_current", "controls_implemented",
        "response_tests_passed", "audit_programme_completed",
    }
    assert by_group["supporting"] == {
        "risks_assessed", "risk_treatments_on_time", "controls_with_team", "controls_at_target_maturity",
        "open_deficiencies_older_than", "open_deficiency_average_age", "ai_verdicts_awaiting_review",
    }
    assert len(library.MEASURES) == 15


def test_controls_implemented_uses_the_imported_statuses():
    assert library.IMPLEMENTED_STATUSES is doc_context.IMPLEMENTED_STATUSES


def test_minimum_populations():
    assert library.REGISTRY["supplier_assurance_current"].min_population == 1
    assert library.REGISTRY["audit_programme_completed"].min_population == 1
    for key in ("risk_within_appetite", "deficiencies_corrected_in_time", "evidence_collected_on_time",
                "controls_implemented"):
        assert library.REGISTRY[key].min_population == 5


def test_malformed_params_fall_back_to_defaults():
    m = library.REGISTRY["deficiencies_corrected_in_time"]
    assert library.resolve_params(m, {"days": "abc", "window_months": -3, "priority": "urgent", "x": 1}) == {
        "days": 90, "window_months": 12, "priority": "any",
    }
    assert library.resolve_params(m, {"days": 30, "priority": "high"})["days"] == 30
    assert library.resolve_params(m, None)["priority"] == "any"


def test_months_ago_clamps_to_month_end():
    assert library.months_ago(date(2026, 3, 31), 1) == date(2026, 2, 28)
    assert library.months_ago(date(2026, 11, 18), 12) == date(2025, 11, 18)


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


async def _org(db):
    tag = _uuid.uuid4().hex[:10]
    org = Organization(name=f"isms-{tag}", slug=f"isms-{tag}")
    db.add(org)
    await db.flush()
    return org


async def _user(db):
    tag = _uuid.uuid4().hex[:10]
    user = User(google_sub=f"isms-{tag}", email=f"isms-{tag}@example.invalid")
    db.add(user)
    await db.flush()
    return user


async def _run(db, org, key, params=None):
    return await library.measure(db, org.id, key, params, TODAY)


def _segments(m):
    return {s["label"]: s["count"] for s in m.segments}


@requires_postgres
@pytest.mark.asyncio
class TestCalculators:
    async def test_risk_within_appetite(self, db):
        org = await _org(db)
        user = await _user(db)
        catalogue = int(await db.scalar(
            select(func.count()).select_from(SCFCatalogRisk).where(SCFCatalogRisk.status == "active")) or 0)
        for n in range(1, 7):
            db.add(CustomRiskDefinition(organization_id=org.id, risk_code=f"R-ORG-{n}", title=f"Risk {n}",
                                        description="test"))
        db.add_all([
            RiskAssessment(organization_id=org.id, risk_code="R-ORG-1", likelihood=1, impact=2),
            RiskAssessment(organization_id=org.id, risk_code="R-ORG-2", likelihood=4, impact=4,
                           residual_likelihood=1, residual_impact=3),
            RiskAssessment(organization_id=org.id, risk_code="R-ORG-3", likelihood=5, impact=5),
            RiskAssessment(organization_id=org.id, risk_code="R-ORG-4", likelihood=5, impact=5,
                           treatment_status="accepted", accepted_by=user.id, accepted_at=datetime.utcnow(),
                           acceptance_rationale="Board accepted", acceptance_expires_on=TODAY + timedelta(days=30)),
            RiskAssessment(organization_id=org.id, risk_code="R-ORG-6"),
        ])
        await db.flush()
        m = await _run(db, org, "risk_within_appetite")
        total = catalogue + 6
        assert (m.numerator, m.denominator) == (3, total)
        assert m.population == f"3 of {total} risks"
        # unassessed risks count against: R-ORG-5 (no row), R-ORG-6 (no score) and every catalogue risk
        assert _segments(m) == {"not assessed": catalogue + 2, "above appetite": 1, "accepted": 1}
        assert m.value == pytest.approx(300 / total)

    async def test_expired_acceptance_is_not_within_appetite(self, db):
        org = await _org(db)
        user = await _user(db)
        db.add(CustomRiskDefinition(organization_id=org.id, risk_code="R-ORG-1", title="Risk", description="t"))
        db.add(RiskAssessment(organization_id=org.id, risk_code="R-ORG-1", likelihood=5, impact=5,
                              treatment_status="accepted", accepted_by=user.id, accepted_at=datetime.utcnow(),
                              acceptance_rationale="Old", acceptance_expires_on=TODAY - timedelta(days=1)))
        await db.flush()
        m = await _run(db, org, "risk_within_appetite")
        assert _segments(m)["accepted"] == 0 and _segments(m)["above appetite"] == 1

    async def test_open_material_deficiencies(self, db):
        org = await _org(db)
        material = ScopedControl(organization_id=org.id, scf_id="GOV-01", selected=True,
                                 material_override=True, material_reason="Key control")
        ordinary = ScopedControl(organization_id=org.id, scf_id="GOV-02", selected=True,
                                 material_override=False, material_reason="Minor")
        db.add_all([material, ordinary])
        await db.flush()
        db.add_all([
            VendorActionItem(organization_id=org.id, title="m", scoped_control_id=material.id, scf_id="GOV-01"),
            VendorActionItem(organization_id=org.id, title="o", scoped_control_id=ordinary.id, scf_id="GOV-02"),
            VendorActionItem(organization_id=org.id, title="r", risk_code="R-ORG-1"),
            VendorActionItem(organization_id=org.id, title="done", scoped_control_id=material.id, scf_id="GOV-01",
                             status="completed", retest_note="retested", completed_date=TODAY),
        ])
        await db.flush()
        m = await _run(db, org, "open_material_deficiencies")
        assert (m.value, m.numerator, m.denominator) == (1.0, 1, 3)
        assert m.population == "1 of 3 open are material"

    async def test_deficiencies_corrected_in_time(self, db):
        org = await _org(db)
        vendor = Vendor(organization_id=org.id, name="V")
        db.add(vendor)
        await db.flush()

        def item(days_ago, status="open", done_after=None, vendor_id=None):
            raised = datetime.combine(TODAY - timedelta(days=days_ago), datetime.min.time())
            return VendorActionItem(
                organization_id=org.id, title=f"d{days_ago}", status=status, created_at=raised, vendor_id=vendor_id,
                completed_date=(raised.date() + timedelta(days=done_after)) if done_after is not None else None,
                retest_note="retested" if status == "completed" else None,
            )
        db.add_all([
            item(100, "completed", 50),   # good
            item(120, "completed", 100),  # late
            item(95),                     # still open
            item(100, "cancelled"),       # segment, excluded
            item(10),                     # too recent
            item(400, "completed", 1),    # outside the window
            item(100, "open", vendor_id=vendor.id),  # a vendor action item, not a deficiency
        ])
        await db.flush()
        m = await _run(db, org, "deficiencies_corrected_in_time")
        assert (m.numerator, m.denominator) == (1, 3)
        assert m.population == "1 of 3 raised 90+ days ago"
        assert _segments(m) == {"cancelled": 1}

    async def test_evidence_collected_on_time(self, db):
        org = await _org(db)
        tracking = EvidenceTracking(organization_id=org.id, evidence_id="E-ISMS-1")
        db.add(tracking)
        await db.flush()

        def task(due_ago, status="not_started", done_ago=None):
            return EvidenceCollectionTask(
                evidence_tracking_id=tracking.id, organization_id=org.id, title="t", status=status,
                due_date=TODAY - timedelta(days=due_ago),
                completed_date=(TODAY - timedelta(days=done_ago)) if done_ago is not None else None,
            )
        db.add_all([
            task(10, "completed", 11),  # good
            task(10, "completed"),      # completed with no date: counts against
            task(5, "wont_do", 5),      # excluded and listed
            task(20),                   # missed
            task(3, "completed", 1),    # completed late
            task(0),                    # due today: not yet in the window
            task(100, "completed", 100),  # outside the window
        ])
        await db.flush()
        m = await _run(db, org, "evidence_collected_on_time")
        assert (m.numerator, m.denominator) == (1, 4)
        assert m.population == "1 of 4 tasks due in the last 90 days"
        assert _segments(m) == {"won't do": 1, "completed with no date": 1}

    async def test_supplier_assurance_current(self, db):
        org = await _org(db)
        v1 = Vendor(organization_id=org.id, name="v1", criticality="high", status="active")
        v2 = Vendor(organization_id=org.id, name="v2", criticality="critical", status="approved")
        v3 = Vendor(organization_id=org.id, name="v3", criticality="high", status="under_review")
        v4 = Vendor(organization_id=org.id, name="v4", criticality="critical", status="offboarded")
        v5 = Vendor(organization_id=org.id, name="v5", criticality="low", status="active")
        db.add_all([v1, v2, v3, v4, v5])
        await db.flush()
        db.add_all([
            VendorAssessment(vendor_id=v1.id, status="completed", assessment_date=TODAY - timedelta(days=30)),
            VendorAssessment(vendor_id=v2.id, status="completed", assessment_date=TODAY - timedelta(days=400)),
            VendorAssessment(vendor_id=v3.id, status="failed", assessment_date=TODAY,
                             completed_at=datetime.utcnow()),
            VendorAssessment(vendor_id=v5.id, status="completed", assessment_date=TODAY),
        ])
        await db.flush()
        m = await _run(db, org, "supplier_assurance_current")
        assert (m.numerator, m.denominator) == (1, 3)
        assert m.population == "1 of 3 high and critical suppliers"
        assert _segments(m) == {"not in use": 1}

    async def test_controls_implemented(self, db):
        org = await _org(db)
        statuses = ["implemented", "monitored", "ready_for_review", "in_progress", "not_started", "not_applicable"]
        db.add_all([ScopedControl(organization_id=org.id, scf_id=f"TST-{i:02d}", selected=True,
                                  implementation_status=s) for i, s in enumerate(statuses)])
        db.add(ScopedControl(organization_id=org.id, scf_id="TST-99", selected=False,
                             implementation_status="implemented"))
        await db.flush()
        m = await _run(db, org, "controls_implemented")
        assert (m.numerator, m.denominator, m.value) == (3, 6, 50.0)
        assert m.population == "3 of 6 controls"

    async def test_a_not_applicable_control_counts_as_not_implemented_while_it_is_in_scope(self, db):
        """Scope decides the population, not the status: Not applicable in scope counts against, out of scope is absent."""
        org = await _org(db)
        db.add_all([
            ScopedControl(organization_id=org.id, scf_id="TST-01", selected=True, implementation_status="implemented"),
            ScopedControl(organization_id=org.id, scf_id="TST-02", selected=True, implementation_status="not_applicable"),
            ScopedControl(organization_id=org.id, scf_id="TST-03", selected=False, implementation_status="not_applicable"),
        ])
        await db.flush()
        m = await _run(db, org, "controls_implemented")
        assert (m.numerator, m.denominator, m.value) == (1, 2, 50.0)

    async def test_response_tests_passed(self, db):
        org = await _org(db)

        def test(kind, result, ago):
            return CapabilityTest(organization_id=org.id, test_type=kind, title="t", scenario="s", outcome="o",
                                  result=result, tested_on=TODAY - timedelta(days=ago))
        db.add_all([
            test("incident_response", "passed", 30),
            test("continuity", "passed_with_findings", 60),
            test("incident_response", "failed", 10),   # failed tests do not count
            test("other", "passed", 10),
            test("incident_response", "passed", 400),
        ])
        await db.flush()
        m = await _run(db, org, "response_tests_passed")
        assert m.value == 2.0 and m.numerator == 2
        assert m.population == "2 passed tests in the last 12 months"
        assert _segments(m) == {"failed": 1}

    async def test_response_tests_zero_is_a_reading(self, db):
        org = await _org(db)
        m = await _run(db, org, "response_tests_passed")
        assert m.value == 0.0

    async def test_audit_programme_completed(self, db):
        org = await _org(db)
        db.add(IsmsProfile(organization_id=org.id, audit_programme=[
            {"area": "A", "status": "completed"}, {"area": "B", "status": "planned"},
            {"area": "C", "status": "cancelled"}, "not an object", {"area": "D", "status": "completed"},
        ]))
        await db.flush()
        m = await _run(db, org, "audit_programme_completed")
        assert (m.numerator, m.denominator) == (2, 3)
        assert m.population == "2 of 3 audits due in the last 12 months"
        assert _segments(m) == {"cancelled": 1, "not yet due": 0}

    async def test_empty_organisation_percentages_are_none(self, db):
        org = await _org(db)
        for key in ("deficiencies_corrected_in_time", "evidence_collected_on_time", "supplier_assurance_current",
                    "controls_implemented", "audit_programme_completed"):
            m = await _run(db, org, key)
            assert m.value is None and m.denominator == 0, key
