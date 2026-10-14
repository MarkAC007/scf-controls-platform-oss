"""Target maturity per domain, with a per-control override (#1125, PIG R5).

The playbook sets a target maturity for each domain, lets a control depart
from it with a reason, and gates on both having targets and reaching them:

    {"type": "controls_with_target", "min_fraction": 1}
    {"type": "controls_at_target", "domains": ["GOV", "RSK"], "min_fraction": 0.8}

These tests pin:

* the domain targets in the organisation settings, refused for a domain the
  catalogue does not hold, and audited;
* the per-control override, which needs a reason, stays in its organisation
  and lands in audit_log;
* both gates, which count in-scope controls, honour the domain and id filters,
  and are unknown, never met, on a parameter they cannot honour;
* the maturity report's gap section.

Domain codes and control ids come from the seeded catalogue, never from the
test; the structural tests use made-up ids of the same shape. Everything
written runs inside a transaction that is rolled back.
"""
import uuid as _uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from api import journey as journey_api
from api import organizations as organizations_api
from api import scoped_controls as scoped_api
from auth import OrgMembership
from catalog_models import SCFCatalogControl, SCFCatalogDomain
from models import AuditLog, Organization, ScopedControl, User
from schemas import (
    ControlTargetMaturityRequest,
    OrganizationSettingsUpdate,
    ScopedControlsPaginatedResponse,
)
from services import journey as journey_service
from services import target_maturity as tm
from services.doc_gen.context import (
    Domain,
    DomainWithControls,
    EnrichedControl,
    OrganisationContext,
)
from services.doc_gen.tier1 import render_maturity_report

from test_journey_service import DATABASE_URL, requires_postgres

WITH = {"type": "controls_with_target", "min_fraction": 1}
AT = {"type": "controls_at_target", "min_fraction": 1}


# ---------------------------------------------------------------------------
# The checks' parameters: structural, no database
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("gate", [
    WITH,
    AT,
    {"type": "controls_at_target", "domains": ["GOV", "RSK"], "min_fraction": 0.8},
    {"type": "controls_with_target", "scf_ids": ["any"], "label": "Step 30"},
])
def test_the_gates_are_well_formed_and_importable(gate):
    assert journey_service.check_param_problem(gate) is None
    template = {"stages": [{"precondition_spec": [gate]}]}
    assert journey_service.unsupported_checks(template) == set()
    assert gate["type"] in journey_service.CONTROL_FILTERED_CHECKS


@pytest.mark.parametrize("extra", [
    {"basis": "mcr"},
    {"min_count": 1},
    {"statuses": ["implemented"]},
    {"generator": "soa"},
    {"target": "L3"},
])
def test_a_parameter_the_gates_cannot_honour_is_refused(extra):
    for gate in (WITH, AT):
        problem = journey_service.check_param_problem({**gate, **extra})
        assert problem and problem.startswith(f"'{gate['type']}'")


@pytest.mark.parametrize("value", [0, -0.5, 1.5, "1", True, None, float("nan"), float("inf")])
def test_min_fraction_must_be_a_fraction(value):
    for gate in (WITH, AT):
        problem = journey_service.check_param_problem({**gate, "min_fraction": value})
        assert problem == "min_fraction must be a number above 0 and at most 1"


@pytest.mark.parametrize("value", [[], "GOV", [""], ["  "], [3], None])
def test_domains_must_be_a_list_of_codes(value):
    problem = journey_service.check_param_problem({**AT, "domains": value})
    assert problem and problem.startswith("domains must")


@pytest.mark.asyncio
async def test_a_refused_parameter_reads_as_unknown_not_met():
    # check_param_problem runs before any query, so no database is touched.
    out = await journey_service._evaluate_one(None, _uuid.uuid4(), {**AT, "basis": "mcr"}, {})
    assert out["met"] is None and out["detail"].startswith("Cannot evaluate:")


# ---------------------------------------------------------------------------
# Resolution and validation: structural, no database
# ---------------------------------------------------------------------------


def test_the_override_wins_then_the_domain_then_nothing():
    targets = {"GOV": "L3"}
    assert tm.resolve_target("GOV-a", "L4", targets) == ("L4", "control")
    assert tm.resolve_target("GOV-a", None, targets) == ("L3", "domain")
    assert tm.resolve_target("gov-a.b", None, targets) == ("L3", "domain")
    assert tm.resolve_target("RSK-a", None, targets) == (None, None)


def test_stored_targets_drop_what_is_not_a_level():
    stored = {"maturity_targets": {"gov": "l3", "RSK": "L9", "": "L1", "CPL": 2, "PRI": " L2 "}}
    assert tm.stored_domain_targets(stored) == {"GOV": "L3", "PRI": "L2"}
    assert tm.stored_domain_targets({"maturity_targets": ["GOV"]}) == {}
    assert tm.stored_domain_targets(None) == {}


def test_an_override_needs_a_reason_and_a_level():
    assert ControlTargetMaturityRequest(level=None).level is None
    assert ControlTargetMaturityRequest(level="L2", reason="Low exposure").level == "L2"
    for bad in ({"level": "L2"}, {"level": "L2", "reason": "  "}, {"level": "L6", "reason": "x"}):
        with pytest.raises(ValidationError):
            ControlTargetMaturityRequest(**bad)


def test_settings_accept_only_cmm_levels():
    assert OrganizationSettingsUpdate(maturity_targets={"GOV": "L3"}).maturity_targets == {"GOV": "L3"}
    with pytest.raises(ValidationError):
        OrganizationSettingsUpdate(maturity_targets={"GOV": "L9"})
    with pytest.raises(ValidationError):
        OrganizationSettingsUpdate(maturity_targets={"GOV": "high"})


# ---------------------------------------------------------------------------
# The maturity report's gap section: no database
# ---------------------------------------------------------------------------


def _ctl(scf_id, current, target, source="domain", reason=None):
    return EnrichedControl(
        scf_id=scf_id, control_name=f"name {scf_id}", control_description="",
        domain_identifier=scf_id.split("-")[0], implementation_status="in_progress",
        maturity_level=current, target_maturity=target, target_source=source if target else None,
        target_maturity_reason=reason,
    )


def _ctx(controls):
    bundles = {}
    for c in controls:
        bundles.setdefault(c.domain_identifier, []).append(c)
    return OrganisationContext(
        organization_id="o", name="Example", generated_at="now",
        domains=[DomainWithControls(domain=Domain(identifier=k, name=f"{k} domain"), controls=v)
                 for k, v in sorted(bundles.items())],
        all_controls=controls, total_scoped_controls=len(controls), total_domains=len(bundles),
    )


def test_the_report_lists_the_gap_to_target():
    out = render_maturity_report(_ctx([
        _ctl("AAA-a", "L3", "L3"),
        _ctl("AAA-b", "L1", "L3"),
        _ctl("AAA-c", None, "L2"),
        _ctl("BBB-a", "L2", "L4", source="control", reason="Holds payment data"),
        _ctl("BBB-b", "L2", None),
    ]))
    section = out.split("## 4. Gap to Target", 1)[1]
    assert "1 of 4 controls with a target have reached it. 1 control has no target." in section
    assert "| AAA — AAA domain | 3 | 3 | 1 | 2 |" in section
    assert "| BBB — BBB domain | 2 | 1 | 0 | 1 |" in section
    assert "### Controls Below Target (3)" in section
    assert "| AAA-b | name AAA-b | AAA | L1 | L3 | 2 levels | domain |" in section
    assert "| AAA-c | name AAA-c | AAA | Unrated | L2 | unrated | domain |" in section
    assert "| BBB-a | name BBB-a | BBB | L2 | L4 | 2 levels | control: Holds payment data |" in section
    assert "AAA-a |" not in section.split("### Controls Below Target", 1)[1]


def test_the_report_says_when_no_target_is_set():
    out = render_maturity_report(_ctx([_ctl("AAA-a", "L1", None)]))
    assert "No target maturity is set." in out.split("## 4. Gap to Target", 1)[1]


def test_the_report_says_when_every_target_is_met():
    out = render_maturity_report(_ctx([_ctl("AAA-a", "L4", "L3")]))
    assert "Every control with a target has reached it." in out


def test_the_gap_table_groups_by_scf_id_prefix_not_catalogue_domain_name():
    """A control files under the domain whose target it inherits.

    The catalogue can place a control under a domain whose code is not its
    prefix; the gap table must still count it in its prefix's row.
    """
    stray = _ctl("BBB-c", "L1", "L3")
    stray.domain_identifier = "AAA"
    ctx = _ctx([_ctl("AAA-a", "L3", "L3"), stray])
    section = render_maturity_report(ctx).split("## 4. Gap to Target", 1)[1]
    assert "| AAA — AAA domain | 2 | 1 | 1 | 0 |" not in section
    assert "| AAA — AAA domain | 1 | 1 | 1 | 0 |" in section
    assert "| BBB | 1 | 1 | 0 | 1 |" in section
    assert "| BBB-c | name BBB-c | BBB | L1 | L3 | 2 levels | domain |" in section


def test_the_report_and_the_resolver_read_the_domain_the_same_way():
    from services.doc_gen.tier1 import _domain_code

    for scf_id in ("GOV-a", "gov-a.b", " rsk-a", "AAA", ""):
        assert _domain_code(scf_id) == tm.domain_of(scf_id)


def test_a_domain_given_twice_in_different_case_is_refused():
    with pytest.raises(ValidationError, match="more than once"):
        OrganizationSettingsUpdate(maturity_targets={"GOV": "L3", "gov": "L1"})


def test_a_target_change_changes_the_reports_prompt_key_and_no_target_keeps_the_old_one():
    """A changed target must regenerate the report, not be skipped as unchanged.

    The fingerprint's controls hash leaves targets out, so the maturity
    report's prompt key carries a digest of them; with no targets the key is
    exactly the one it had before this change, so nothing goes stale on deploy.
    """
    from services.doc_gen.pipeline import _render_tier1
    from services.doc_gen.registry import get_generator

    spec = get_generator("maturity-report")
    _, _, untargeted, _, _ = _render_tier1(spec, _ctx([_ctl("AAA-a", "L1", None)]))
    assert untargeted == "tier1:maturity-report"

    _, _, at_l3, _, _ = _render_tier1(spec, _ctx([_ctl("AAA-a", "L1", "L3")]))
    _, _, at_l4, _, _ = _render_tier1(spec, _ctx([_ctl("AAA-a", "L1", "L4")]))
    _, _, at_l4_again, _, _ = _render_tier1(spec, _ctx([_ctl("AAA-a", "L1", "L4")]))
    _, _, reasoned, _, _ = _render_tier1(
        spec, _ctx([_ctl("AAA-a", "L1", "L4", source="control", reason="Holds payment data")])
    )
    assert at_l3.startswith("tier1:maturity-report:targets:")
    assert len({untargeted, at_l3, at_l4, reasoned}) == 4
    assert at_l4 == at_l4_again


def test_other_tier1_reports_keep_their_prompt_key_when_targets_are_set():
    from services.doc_gen.pipeline import _render_tier1
    from services.doc_gen.registry import all_generators

    others = [s for s in all_generators()
              if s.tier == 1 and s.renderer != "tier1.render_maturity_report"]
    ctx = _ctx([_ctl("AAA-a", "L1", "L3")])
    checked = 0
    for spec in others:
        try:
            _, _, key, _, _ = _render_tier1(spec, ctx)
        except Exception:  # a renderer that needs inputs this bare context lacks
            continue
        # The governance report folds its own figures into its key (PIG R10);
        # what this pins is that targets never reach any other report's key.
        if spec.renderer == "tier1.render_governance_report":
            assert key == _render_tier1(spec, _ctx([]))[2]
        else:
            assert key == f"tier1:{spec.name}"
        checked += 1
    assert checked, "expected at least one other tier-1 report to compare against"


def test_catalogue_migration_carries_a_target_override_to_the_successor():
    from unittest.mock import MagicMock

    from services import reconciliation_service

    def migrate(successor):
        scoped = SimpleNamespace(
            selected=True, target_maturity_override="L4", target_maturity_reason="Crown jewel",
            target_maturity_set_at=None, target_maturity_set_by=None,
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
    assert (new.target_maturity_override, new.target_maturity_reason) == (
        "L4", "Carried from ctl-old: Crown jewel",
    )
    # A successor with its own target keeps it.
    _, kept = migrate(SimpleNamespace(
        selected=True, target_maturity_override="L2", target_maturity_reason="Own",
    ))
    assert (kept.target_maturity_override, kept.target_maturity_reason) == ("L2", "Own")


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


async def _catalogue_ids(db, prefix, n):
    ids = list((await db.execute(
        select(SCFCatalogControl.scf_id)
        .where(SCFCatalogControl.scf_id.like(f"{prefix}-%"))
        .order_by(SCFCatalogControl.scf_id)
        .limit(n)
    )).scalars().all())
    if len(ids) < n:  # pragma: no cover - environment dependent
        pytest.skip(f"catalogue has fewer than {n} {prefix} controls")
    return ids


async def _two_domains(db):
    """Two domain codes the catalogue holds, read from it rather than assumed."""
    codes = list((await db.execute(
        select(SCFCatalogDomain.identifier).order_by(SCFCatalogDomain.identifier).limit(2)
    )).scalars().all())
    if len(codes) < 2:  # pragma: no cover - environment dependent
        pytest.skip("catalogue has fewer than two domains")
    return codes


async def _org(db, targets=None):
    tag = _uuid.uuid4().hex[:10]
    settings = {"maturity_targets": targets} if targets is not None else {}
    org = Organization(name=f"pig-{tag}", slug=f"pig-{tag}", settings=settings)
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
    return Request({"type": "http", "headers": [], "method": "PUT", "path": "/"})


async def _scope(db, org, scf_id, **kwargs):
    kwargs.setdefault("selected", True)
    row = ScopedControl(organization_id=org.id, scf_id=scf_id, **kwargs)
    db.add(row)
    await db.flush()
    return row


async def _gate(db, org, check):
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
class TestDomainTargets:

    async def test_targets_round_trip_in_catalogue_form_and_are_audited(self, db, no_commit):
        org = await _org(db)
        admin = _membership(org, await _user(db), "admin")
        a, b = await _two_domains(db)

        saved = await organizations_api.update_organization_settings(
            org.id, OrganizationSettingsUpdate(maturity_targets={a.lower(): "L3", b: "L2"}),
            _request(), admin, db,
        )
        assert saved.maturity_targets == {a: "L3", b: "L2"}
        read = await organizations_api.get_organization_settings(org.id, admin, db)
        assert read.maturity_targets == {a: "L3", b: "L2"}
        audited = await _audited(db, org, org.id)
        assert '"maturity_targets"' in audited["settings"][1]

        # Leaving the field out leaves it alone; an empty map clears it.
        await organizations_api.update_organization_settings(
            org.id, OrganizationSettingsUpdate(industry="Health"), _request(), admin, db,
        )
        assert (await organizations_api.get_organization_settings(org.id, admin, db)).maturity_targets == {a: "L3", b: "L2"}
        await organizations_api.update_organization_settings(
            org.id, OrganizationSettingsUpdate(maturity_targets={}), _request(), admin, db,
        )
        assert (await organizations_api.get_organization_settings(org.id, admin, db)).maturity_targets == {}

    async def test_a_domain_the_catalogue_does_not_hold_is_refused(self, db, no_commit):
        org = await _org(db)
        admin = _membership(org, await _user(db), "admin")
        a, _ = await _two_domains(db)
        with pytest.raises(organizations_api.HTTPException) as exc:
            await organizations_api.update_organization_settings(
                org.id, OrganizationSettingsUpdate(maturity_targets={a: "L3", "NOPE": "L2"}),
                _request(), admin, db,
            )
        assert exc.value.status_code == 422 and "NOPE" in exc.value.detail
        assert (await organizations_api.get_organization_settings(org.id, admin, db)).maturity_targets == {}


@requires_postgres
@pytest.mark.asyncio
class TestControlOverride:

    async def test_an_override_is_set_audited_and_cleared(self, db, no_commit):
        org = await _org(db)
        user = await _user(db)
        (scf_id,) = await _catalogue_ids(db, (await _two_domains(db))[0], 1)
        row = await _scope(db, org, scf_id)

        out = await scoped_api.put_control_target_maturity(
            org.id, scf_id, ControlTargetMaturityRequest(level="L4", reason="Holds payment data"),
            _request(), _membership(org, user, "admin"), db,
        )
        assert (out.target_maturity_override, out.target_maturity_reason) == ("L4", "Holds payment data")
        assert out.target_maturity_set_by == user.id and out.target_maturity_set_at is not None
        audited = await _audited(db, org, row.id)
        assert audited["target_maturity_override"][1] == '"L4"'
        assert audited["target_maturity_reason"][1] == '"Holds payment data"'

        cleared = await scoped_api.put_control_target_maturity(
            org.id, scf_id, ControlTargetMaturityRequest(level=None),
            _request(), _membership(org, user, "admin"), db,
        )
        assert cleared.target_maturity_override is None and cleared.target_maturity_reason is None
        assert cleared.target_maturity_set_by is None

    async def test_another_organisation_cannot_override_the_control(self, db, no_commit):
        org, other = await _org(db), await _org(db)
        (scf_id,) = await _catalogue_ids(db, (await _two_domains(db))[0], 1)
        await _scope(db, org, scf_id)
        with pytest.raises(scoped_api.HTTPException) as exc:
            await scoped_api.put_control_target_maturity(
                other.id, scf_id, ControlTargetMaturityRequest(level="L1", reason="x"),
                _request(), _membership(other, await _user(db), "admin"), db,
            )
        assert exc.value.status_code == 404
        row = await db.scalar(select(ScopedControl).where(
            ScopedControl.organization_id == org.id, ScopedControl.scf_id == scf_id))
        assert row.target_maturity_override is None

    async def test_the_service_refuses_an_override_without_a_reason(self, db):
        org = await _org(db)
        (scf_id,) = await _catalogue_ids(db, (await _two_domains(db))[0], 1)
        await _scope(db, org, scf_id)
        with pytest.raises(ValueError):
            await tm.set_control_target_override(db, org.id, scf_id, "L2", "  ", None)

    @pytest.mark.parametrize("fields", [
        {"target_maturity_override": "L2"},
        {"target_maturity_override": "L2", "target_maturity_reason": " "},
        {"target_maturity_override": "L7", "target_maturity_reason": "x"},
    ])
    async def test_the_database_refuses_what_the_api_would(self, db, fields):
        org = await _org(db)
        (scf_id,) = await _catalogue_ids(db, (await _two_domains(db))[0], 1)
        with pytest.raises(IntegrityError):
            async with db.begin_nested():
                await _scope(db, org, scf_id, **fields)

    async def test_the_scoping_list_carries_the_effective_target(self, db):
        a, b = await _two_domains(db)
        org = await _org(db, {a: "L3"})
        admin = await _user(db)
        in_a = await _catalogue_ids(db, a, 3)
        (in_b,) = await _catalogue_ids(db, b, 1)
        await _scope(db, org, in_a[0])
        await _scope(db, org, in_a[1], target_maturity_override="L5", target_maturity_reason="Crown jewel")
        await _scope(db, org, in_b)
        await _scope(db, org, in_a[2], selected=False)

        page = ScopedControlsPaginatedResponse.model_validate(await scoped_api.list_scoped_controls_paginated(
            org.id, membership=_membership(org, admin, "viewer"), db=db, scope_status="all",
            domain=None, csf_function=None, framework=None, control_weighting=None,
            search=None, team_id=None, my_teams=False, function_id=None,
            accountable_owner_type=None, basis=None, material=None, sort="scf_id", limit=200, offset=0,
        ))
        got = {c.scf_id: (c.target_maturity, c.target_source, c.target_maturity_reason)
               for c in page.controls if c.scf_id in {*in_a, in_b}}
        assert got[in_a[0]] == ("L3", "domain", None)
        assert got[in_a[1]] == ("L5", "control", "Crown jewel")
        assert got[in_b] == (None, None, None)
        # Recorded but not selected: still in the programme's records, so it
        # still shows the target it would be held to.
        assert got[in_a[2]] == ("L3", "domain", None)


@requires_postgres
@pytest.mark.asyncio
class TestTargetGates:

    async def test_an_empty_scope_does_not_pass(self, db):
        org = await _org(db)
        for gate in (WITH, AT):
            out = await _gate(db, org, gate)
            assert out["met"] is False and out["detail"] == "No controls scoped yet"

    async def test_every_in_scope_control_needs_a_target(self, db):
        a, b = await _two_domains(db)
        org = await _org(db, {a: "L2"})
        (in_a,) = await _catalogue_ids(db, a, 1)
        in_b = await _catalogue_ids(db, b, 2)
        await _scope(db, org, in_a)
        await _scope(db, org, in_b[0])
        out = await _gate(db, org, WITH)
        assert out["met"] is False
        assert out["detail"] == "1 of 2 with a target maturity (50.0%) — 2 required, 1 more needed"

        # An override counts; an unselected control is not in the count.
        await _scope(db, org, in_b[1], selected=False)
        row = await db.scalar(select(ScopedControl).where(
            ScopedControl.organization_id == org.id, ScopedControl.scf_id == in_b[0]))
        row.target_maturity_override, row.target_maturity_reason = "L1", "Low exposure"
        await db.flush()
        assert (await _gate(db, org, WITH))["met"] is True

    async def test_at_target_means_reached_and_unrated_never_is(self, db):
        a, _ = await _two_domains(db)
        org = await _org(db, {a: "L3"})
        ids = await _catalogue_ids(db, a, 5)
        await _scope(db, org, ids[0], maturity_level="L3")
        await _scope(db, org, ids[1], maturity_level="l4 ")   # free text, canonicalised
        await _scope(db, org, ids[2], maturity_level="L2")
        await _scope(db, org, ids[3])                          # unrated
        await _scope(db, org, ids[4], maturity_level="Level 5")  # not a CMM level
        out = await _gate(db, org, AT)
        assert out["met"] is False
        assert out["detail"].startswith("2 of 5 at target maturity (40.0%)")

        assert (await _gate(db, org, {**AT, "min_fraction": 0.4}))["met"] is True

    async def test_a_control_with_no_target_is_not_at_target(self, db):
        org = await _org(db)
        (scf_id,) = await _catalogue_ids(db, (await _two_domains(db))[0], 1)
        await _scope(db, org, scf_id, maturity_level="L5")
        assert (await _gate(db, org, AT))["met"] is False

    async def test_a_lower_override_is_what_the_control_is_held_to(self, db):
        a, _ = await _two_domains(db)
        org = await _org(db, {a: "L4"})
        (scf_id,) = await _catalogue_ids(db, a, 1)
        await _scope(db, org, scf_id, maturity_level="L2",
                     target_maturity_override="L2", target_maturity_reason="Low exposure")
        assert (await _gate(db, org, AT))["met"] is True

    async def test_the_domain_filter_narrows_both_halves(self, db):
        a, b = await _two_domains(db)
        org = await _org(db, {a: "L1", b: "L3"})
        (in_a,) = await _catalogue_ids(db, a, 1)
        (in_b,) = await _catalogue_ids(db, b, 1)
        await _scope(db, org, in_a, maturity_level="L1")
        await _scope(db, org, in_b, maturity_level="L1")
        assert (await _gate(db, org, AT))["met"] is False
        only_a = await _gate(db, org, {**AT, "domains": [a]})
        assert only_a["met"] is True and only_a["detail"].startswith(f"1 of 1 in {a} at target")
        assert (await _gate(db, org, {**AT, "domains": [b]}))["met"] is False

    async def test_the_scf_ids_filter_narrows_both_halves(self, db):
        a, _ = await _two_domains(db)
        org = await _org(db, {a: "L2"})
        ids = await _catalogue_ids(db, a, 2)
        await _scope(db, org, ids[0], maturity_level="L2")
        await _scope(db, org, ids[1], maturity_level="L0")
        out = await _gate(db, org, {**AT, "scf_ids": [ids[0].lower()]})
        assert out["met"] is True and "across 1 named control" in out["detail"]

    async def test_an_unknown_domain_fails_closed(self, db):
        a, _ = await _two_domains(db)
        org = await _org(db, {a: "L1"})
        (in_a,) = await _catalogue_ids(db, a, 1)
        await _scope(db, org, in_a, maturity_level="L5")
        out = await _gate(db, org, {**AT, "domains": ["NOPE"]})
        assert out["met"] is False

    async def test_another_organisations_controls_and_targets_do_not_count(self, db):
        a, _ = await _two_domains(db)
        org, other = await _org(db), await _org(db, {a: "L1"})
        (scf_id,) = await _catalogue_ids(db, a, 1)
        await _scope(db, other, scf_id, maturity_level="L5")
        for gate in (WITH, AT):
            assert (await _gate(db, org, gate))["detail"] == "No controls scoped yet"
        await _scope(db, org, scf_id, maturity_level="L5")
        # The other organisation's domain target is not this one's.
        assert (await _gate(db, org, WITH))["met"] is False
        assert (await _gate(db, other, AT))["met"] is True

    async def test_sql_and_python_resolution_agree(self, db):
        a, b = await _two_domains(db)
        targets = {a: "L2", b: "L4"}
        org = await _org(db, targets)
        ids = await _catalogue_ids(db, a, 2) + await _catalogue_ids(db, b, 1)
        await _scope(db, org, ids[0])
        await _scope(db, org, ids[1], target_maturity_override="L5", target_maturity_reason="x")
        await _scope(db, org, ids[2])
        rows = (await db.execute(
            select(ScopedControl.scf_id, ScopedControl.target_maturity_override,
                   tm.effective_target_expr(targets))
            .where(ScopedControl.organization_id == org.id)
        )).all()
        assert len(rows) == 3
        for scf_id, override, sql_level in rows:
            assert sql_level == tm.resolve_target(scf_id, override, targets)[0]


@requires_postgres
@pytest.mark.asyncio
@pytest.mark.parametrize("check,fragment", [
    ({**AT, "domains": ["NOPE"]}, "no SCF domain 'NOPE'"),
    ({**WITH, "basis": "mcr"}, "takes no basis"),
])
async def test_the_import_endpoint_refuses_a_gate_it_cannot_honour(db, monkeypatch, check, fragment):
    org = await _org(db)
    # Should validation ever regress, the endpoint would reach its commit;
    # keep that a flush so a failing run still leaves no rows behind.
    monkeypatch.setattr(db, "commit", db.flush)
    payload = journey_api.ImportRequest(template={"stages": [{
        "key": "targets", "title": "Targets", "precondition_spec": [check],
    }]})
    with pytest.raises(journey_api.HTTPException) as exc:
        await journey_api.import_journey(org.id, _request(), payload, SimpleNamespace(user=None), db)
    assert exc.value.status_code == 422
    assert fragment in str(exc.value.detail)
