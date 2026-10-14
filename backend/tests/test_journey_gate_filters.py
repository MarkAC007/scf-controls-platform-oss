"""Gate vocabulary for imported journeys: ``scf_ids`` and ``generator`` (#1121).

A practitioner's playbook names its own slices — a handful of controls for one
step, one approved document for another. Both filters live in the journey
template, never the catalogue. What these tests defend is the one property
that makes a filter safe to add: a filter the engine cannot honour makes the
check unknown, never a pass over a wider slice than was asked for.

The behavioural half reads the SCF catalogue as seeded, and picks its control
ids from it at run time rather than writing any into the test, so nothing here
can drift from the workbook. Everything written runs inside a transaction that
is rolled back.
"""
import json
import uuid as _uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from api import journey as journey_api
from catalog_models import SCFCatalogControl
from models import AuditLog, GeneratedDocument, Organization, ScopedControl
from services import journey as journey_service

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
# Structural validation — no database
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("check, fragment", [
    ({"type": "evidence_files_uploaded", "scf_ids": ["X"]}, "does not count controls"),
    ({"type": "documents_approved", "scf_ids": ["X"]}, "does not count controls"),
    ({"type": "controls_with_owner", "scf_ids": "X"}, "non-empty list"),
    ({"type": "controls_with_owner", "scf_ids": []}, "non-empty list"),
    ({"type": "controls_with_owner", "scf_ids": ["  "]}, "non-blank"),
    ({"type": "controls_with_owner", "scf_ids": [7]}, "non-blank"),
    ({"type": "controls_at_status", "generator": "soa"}, "does not count documents"),
    ({"type": "documents_approved", "generator": ""}, "must name a document generator"),
    ({"type": "documents_approved", "generator": 3}, "must name a document generator"),
    ({"type": "documents_approved", "generator": "vibes"}, "no document generator named 'vibes'"),
    ({"type": "documents_approved", "generator": "soa:GOV"}, "not domain-scoped"),
    ({"type": "documents_approved", "generator": "policy:"}, "empty domain"),
])
def test_a_filter_the_engine_cannot_honour_is_named(check, fragment):
    problem = journey_service.check_param_problem(check)
    assert problem is not None and fragment in problem


@pytest.mark.parametrize("check", [
    {"type": "controls_with_owner"},
    {"type": "controls_with_owner", "scf_ids": ["ANY-ID"]},
    {"type": "controls_at_risk_max", "scf_ids": ["ANY-ID"], "domains": ["GOV"]},
    {"type": "documents_approved", "generator": "soa"},
    {"type": "documents_approved", "generator": "policy"},
    {"type": "documents_approved", "generator": " policy : gov "},
    {"type": "documents_approved"},
])
def test_a_well_formed_filter_has_no_structural_problem(check):
    assert journey_service.check_param_problem(check) is None


def test_generator_suffix_parses_to_an_upper_case_domain():
    assert journey_service._parse_generator("policy:gov") == ("policy", "GOV")
    assert journey_service._parse_generator("Policy:GOV") == ("policy", "GOV")
    assert journey_service._parse_generator(" soa ") == ("soa", None)


@pytest.mark.asyncio
async def test_a_malformed_filter_evaluates_to_unknown_without_touching_the_db():
    """Unknown, not false and not true — and decided before any query runs."""
    db = MagicMock()
    out = await journey_service._evaluate_one(
        db, _uuid.uuid4(),
        {"type": "evidence_items_tracked", "scf_ids": ["ANY-ID"]},
        {"in_scope": 0},
    )
    assert out["met"] is None
    assert "Cannot evaluate" in out["detail"]
    db.execute.assert_not_called()
    db.scalar.assert_not_called()


@pytest.mark.asyncio
async def test_an_unknown_check_type_is_still_unknown():
    out = await journey_service._evaluate_one(
        MagicMock(), _uuid.uuid4(), {"type": "vibes_acceptable"}, {"in_scope": 0}
    )
    assert out["met"] is None


# ---------------------------------------------------------------------------
# Behavioural — seeded catalogue, real PostgreSQL. SKIPS without them.
# ---------------------------------------------------------------------------


async def _catalogue_ids(db, prefix, n):
    rows = await db.execute(
        select(SCFCatalogControl.scf_id)
        .where(SCFCatalogControl.scf_id.like(f"{prefix}-%"))
        .order_by(SCFCatalogControl.scf_id)
        .limit(n)
    )
    ids = list(rows.scalars().all())
    if len(ids) < n:  # pragma: no cover - environment dependent
        pytest.skip(f"catalogue has fewer than {n} {prefix} controls")
    return ids


async def _org(db):
    tag = _uuid.uuid4().hex[:10]
    org = Organization(name=f"pig-{tag}", slug=f"pig-{tag}")
    db.add(org)
    await db.flush()
    return org


async def _scope(db, org, scf_id, **kwargs):
    db.add(ScopedControl(organization_id=org.id, scf_id=scf_id, selected=True, **kwargs))
    await db.flush()


async def _evaluate(db, org, check):
    totals = await journey_service._in_scope_counts(db, org.id)
    return await journey_service._evaluate_one(db, org.id, check, totals)


@requires_postgres
@pytest.mark.asyncio
class TestScfIdsFilter:

    @pytest_asyncio.fixture(autouse=True)
    async def _seeded(self, db):
        if not await db.scalar(select(func.count(SCFCatalogControl.scf_id))):
            pytest.skip("requires the seeded SCF catalogue")

    async def test_numerator_and_denominator_are_the_named_controls(self, db):
        gov = await _catalogue_ids(db, "GOV", 3)
        org = await _org(db)
        await _scope(db, org, gov[0], implementation_status="implemented")
        await _scope(db, org, gov[1])
        # Scoped and decided, but not named: must count on neither side.
        await _scope(db, org, gov[2], implementation_status="implemented")

        out = await _evaluate(db, org, {
            "type": "controls_with_decision", "scf_ids": gov[:2], "min_fraction": 1,
        })
        assert out["met"] is False
        assert out["detail"].startswith("1 of 2 across 2 named controls")

    async def test_the_gate_passes_when_every_named_control_qualifies(self, db):
        gov = await _catalogue_ids(db, "GOV", 3)
        org = await _org(db)
        await _scope(db, org, gov[0], implementation_status="implemented")
        await _scope(db, org, gov[1], implementation_status="implemented")
        await _scope(db, org, gov[2])  # undecided, but not named

        out = await _evaluate(db, org, {
            "type": "controls_with_decision", "scf_ids": gov[:2], "min_fraction": 1,
        })
        assert out["met"] is True

    async def test_ids_are_matched_in_catalogue_form(self, db):
        gov = await _catalogue_ids(db, "GOV", 1)
        org = await _org(db)
        await _scope(db, org, gov[0], implementation_status="implemented")

        out = await _evaluate(db, org, {
            "type": "controls_with_decision", "scf_ids": [f"  {gov[0].lower()} "],
        })
        assert out["met"] is True
        assert out["detail"].startswith("1 of 1")

    async def test_an_id_the_catalogue_does_not_carry_fails_closed_and_is_named(self, db):
        gov = await _catalogue_ids(db, "GOV", 1)
        org = await _org(db)
        await _scope(db, org, gov[0], implementation_status="implemented")
        bogus = f"ZZZ-{_uuid.uuid4().hex[:6].upper()}"

        out = await _evaluate(db, org, {
            "type": "controls_with_decision", "scf_ids": [gov[0], bogus],
        })
        # Not True on the one real control: the typo must not shrink the gate.
        assert out["met"] is None
        assert bogus in out["detail"]

    async def test_scf_ids_and_domains_intersect(self, db):
        gov = await _catalogue_ids(db, "GOV", 2)
        cpl = await _catalogue_ids(db, "CPL", 1)
        org = await _org(db)
        await _scope(db, org, gov[0], implementation_status="implemented")
        await _scope(db, org, cpl[0])
        # In the domain and decided, but not named: must not reach the numerator.
        await _scope(db, org, gov[1], implementation_status="implemented")

        out = await _evaluate(db, org, {
            "type": "controls_with_decision",
            "scf_ids": [gov[0], cpl[0]],
            "domains": ["GOV"],
        })
        # Only gov[0] is both named and in GOV.
        assert out["met"] is True
        assert out["detail"].startswith("1 of 1 in GOV across 1 named control ")

    async def test_a_named_control_nobody_scoped_is_a_shortfall(self, db):
        gov = await _catalogue_ids(db, "GOV", 2)
        org = await _org(db)
        await _scope(db, org, gov[0], implementation_status="implemented")

        out = await _evaluate(db, org, {
            "type": "controls_with_decision", "scf_ids": [gov[1]],
        })
        assert out["met"] is False
        assert out["detail"].startswith("0 of 1 across 1 named control ")

    async def test_unscoped_named_controls_stay_in_the_denominator(self, db):
        """Three named, one scoped and done: one of three, not one of one.

        Dividing by what happens to be scoped would let a gate pass on the
        single control that is finished, and let an organisation turn a gate
        green by never scoping the rest.
        """
        gov = await _catalogue_ids(db, "GOV", 3)
        org = await _org(db)
        await _scope(db, org, gov[0], implementation_status="implemented")

        out = await _evaluate(db, org, {
            "type": "controls_with_decision", "scf_ids": gov, "min_fraction": 1,
        })
        assert out["met"] is False
        assert out["detail"].startswith("1 of 3 across 3 named controls")

    async def test_a_recorded_exclusion_leaves_the_denominator(self, db):
        """An exclusion carries a reason and an audit trail; silence does not."""
        gov = await _catalogue_ids(db, "GOV", 2)
        org = await _org(db)
        await _scope(db, org, gov[0], implementation_status="implemented")
        db.add(ScopedControl(
            organization_id=org.id, scf_id=gov[1], selected=False,
            scope_override="exclude", scope_override_reason="Not applicable to this estate",
        ))
        await db.flush()

        out = await _evaluate(db, org, {
            "type": "controls_with_decision", "scf_ids": gov, "min_fraction": 1,
        })
        assert out["met"] is True
        assert out["detail"].startswith("1 of 1 across 2 named controls (1 excluded)")

    async def test_an_unfiltered_check_still_counts_the_whole_organisation(self, db):
        gov = await _catalogue_ids(db, "GOV", 3)
        org = await _org(db)
        for scf_id in gov:
            await _scope(db, org, scf_id, implementation_status="implemented")

        out = await _evaluate(db, org, {"type": "controls_with_decision"})
        assert out["met"] is True
        assert out["detail"].startswith("3 of 3")

    async def test_unknown_scf_ids_lists_only_the_unknown(self, db):
        gov = await _catalogue_ids(db, "GOV", 1)
        bogus = f"ZZZ-{_uuid.uuid4().hex[:6].upper()}"
        assert await journey_service.unknown_scf_ids(db, [gov[0], bogus.lower()]) == [bogus]


async def _doc(db, org, generator, status, domain=""):
    db.add(GeneratedDocument(
        organization_id=org.id, generator_name=generator, document_type="report",
        domain_id=domain, title=f"{generator} {domain}", filename=f"{generator}-{domain}.md",
        lifecycle_status=status,
    ))
    await db.flush()


@requires_postgres
@pytest.mark.asyncio
class TestGeneratorFilter:

    @pytest_asyncio.fixture
    async def org(self, db):
        org = await _org(db)
        await _doc(db, org, "soa", "approved")
        await _doc(db, org, "risk-treatment", "published")
        await _doc(db, org, "policy", "approved", "GOV")
        await _doc(db, org, "policy", "draft", "CPL")
        return org

    async def test_unfiltered_counts_every_approved_document(self, db, org):
        out = await _evaluate(db, org, {"type": "documents_approved", "min_count": 3})
        assert out["met"] is True

    async def test_a_generator_counts_only_its_own_documents(self, db, org):
        out = await _evaluate(db, org, {"type": "documents_approved", "generator": "soa", "min_count": 2})
        assert out["met"] is False
        assert out["detail"] == "1 soa approved (need 2)"

    async def test_a_domain_suffix_counts_only_that_domain(self, db, org):
        gov = await _evaluate(db, org, {"type": "documents_approved", "generator": "policy:GOV"})
        cpl = await _evaluate(db, org, {"type": "documents_approved", "generator": "policy:cpl"})
        assert gov["met"] is True
        assert cpl["met"] is False  # CPL's policy is only a draft

    async def test_a_domain_scoped_generator_without_suffix_counts_any_domain(self, db, org):
        out = await _evaluate(db, org, {"type": "documents_approved", "generator": "policy", "min_count": 1})
        assert out["met"] is True
        assert out["detail"].startswith("1 policy approved")

    async def test_an_unknown_domain_suffix_fails_closed(self, db, org):
        out = await _evaluate(db, org, {"type": "documents_approved", "generator": "policy:QQQZ"})
        assert out["met"] is None

    async def test_an_unknown_generator_fails_closed(self, db, org):
        out = await _evaluate(db, org, {"type": "documents_approved", "generator": "vibes"})
        assert out["met"] is None

    async def test_another_organisations_documents_never_count(self, db, org):
        other = await _org(db)
        out = await _evaluate(db, other, {"type": "documents_approved", "generator": "soa"})
        assert out["met"] is False
        assert out["detail"].startswith("0 soa approved")


@requires_postgres
@pytest.mark.asyncio
class TestImportValidationAndAudit:

    @pytest_asyncio.fixture(autouse=True)
    async def _seeded(self, db):
        if not await db.scalar(select(func.count(SCFCatalogControl.scf_id))):
            pytest.skip("requires the seeded SCF catalogue")

    async def test_template_param_problems_names_each_unhonourable_parameter(self, db):
        gov = await _catalogue_ids(db, "GOV", 1)
        bogus = f"ZZZ-{_uuid.uuid4().hex[:6].upper()}"
        template = {"stages": [{"key": "step-1c", "precondition_spec": [
            {"type": "controls_with_owner", "scf_ids": [gov[0]]},
            {"type": "controls_with_owner", "scf_ids": [bogus]},
            {"type": "documents_approved", "generator": "policy:QQQZ"},
            {"type": "documents_approved", "generator": "vibes"},
            {"type": "evidence_files_uploaded", "scf_ids": [gov[0]]},
        ]}]}
        problems = await journey_service.template_param_problems(db, template)
        assert len(problems) == 4
        joined = " | ".join(problems)
        assert bogus in joined
        assert "QQQZ" in joined
        assert "vibes" in joined
        assert "does not count controls" in joined

    async def test_a_valid_template_has_no_problems(self, db):
        gov = await _catalogue_ids(db, "GOV", 2)
        template = {"stages": [{"key": "a", "precondition_spec": [
            {"type": "controls_with_owner", "scf_ids": gov, "min_fraction": 1},
            {"type": "documents_approved", "generator": "policy:GOV"},
            {"type": "documents_approved", "generator": "soa"},
        ]}]}
        assert await journey_service.template_param_problems(db, template) == []

    async def test_the_endpoint_refuses_an_unhonourable_template(self, db, monkeypatch):
        org = await _org(db)
        # Should validation ever regress, the endpoint would reach its commit;
        # keep that a flush so a failing run still leaves no rows behind.
        monkeypatch.setattr(db, "commit", db.flush)
        payload = journey_api.ImportRequest(template={"stages": [{
            "key": "step-1c", "title": "Step 1c",
            "precondition_spec": [{"type": "documents_approved", "generator": "vibes"}],
        }]})
        with pytest.raises(journey_api.HTTPException) as exc:
            await journey_api.import_journey(org.id, _request(), payload, _membership(), db)
        assert exc.value.status_code == 422
        assert "vibes" in exc.value.detail

    async def test_the_endpoint_writes_each_stage_gate_to_the_audit_log(self, db, monkeypatch):
        gov = await _catalogue_ids(db, "GOV", 2)
        org = await _org(db)
        # The endpoint commits; inside this test that must stay a flush so the
        # fixture's rollback still leaves the database as it found it.
        monkeypatch.setattr(db, "commit", db.flush)

        async def import_(stages):
            payload = journey_api.ImportRequest(template={"stages": stages})
            await journey_api.import_journey(org.id, _request(), payload, _membership(), db)

        await import_([
            {"key": "step-1c", "title": "Step 1c",
             "precondition_spec": [{"type": "controls_with_owner", "scf_ids": [gov[0]]}]},
            {"key": "gone", "title": "Dropped later"},
        ])
        await import_([
            {"key": "step-1c", "title": "Step 1c",
             "precondition_spec": [{"type": "controls_with_owner", "scf_ids": gov}]},
        ])

        rows = (await db.execute(
            select(AuditLog).where(
                AuditLog.organization_id == org.id,
                AuditLog.entity_type == "journey_stage",
                AuditLog.field_name == "precondition_spec",
            )
        )).scalars().all()
        by_action = {}
        for row in rows:
            by_action.setdefault(row.action, []).append(row)

        # First import: both stages created, the named gate on record.
        assert len(by_action["create"]) == 2
        created = [json.loads(r.new_value) for r in by_action["create"]]
        assert [{"type": "controls_with_owner", "scf_ids": [gov[0]]}] in created
        # Re-issue: the widened gate is an update carrying old and new.
        assert len(by_action["update"]) == 1
        update = by_action["update"][0]
        assert json.loads(update.old_value) == [{"type": "controls_with_owner", "scf_ids": [gov[0]]}]
        assert json.loads(update.new_value) == [{"type": "controls_with_owner", "scf_ids": gov}]
        # The dropped stage is a delete, not a silent disappearance.
        assert len(by_action["delete"]) == 1


def _membership():
    return SimpleNamespace(user=None)


def _request():
    return Request({"type": "http", "headers": [], "method": "POST", "path": "/"})
