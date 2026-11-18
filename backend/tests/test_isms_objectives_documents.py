# SPDX-License-Identifier: AGPL-3.0-only
"""Security objectives in the generated ISMS documents (phase 1).

Two properties matter:

* the documents read objectives from ``security_objectives`` (active and
  closed ones only, never a proposed starter or an archived row, in reference
  order) in the legacy shape every renderer already understands;
* the regeneration fingerprint (``iso_core.inputs_digest``) moves when an
  objective is edited and does not move when a result is stored or starters
  are seeded. Results reach the documents through ``ctx.isms_results``, which
  the fingerprint never reads.

The renderer tests build a context by hand. The database tests run the real
context builder on a transaction that is always rolled back, through
``AsyncSession.run_sync`` so the sync loaders see the rows the async service
wrote. They skip (never pass) when no database resolves.
"""
import uuid as _uuid
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from models import Function, Organization, OrganizationMember, SecurityObjective, Team, User
from services.doc_gen import iso_core
from services.doc_gen.context import OrganisationContext, build_context
from services.isms_metrics import service
from services.isms_metrics.schemas import ConfirmBody, MetricSpec, ObjectiveCreate
from services.isms_metrics.service import Actor

from test_journey_service import DATABASE_URL, requires_postgres  # noqa: F401

TODAY = date(2026, 11, 18)
TBC = iso_core.TBC


# ---------------------------------------------------------------------------
# Renderers, on a hand-built context
# ---------------------------------------------------------------------------


def _ctx(objectives=None, results=None):
    profile = {} if objectives is None else {"objectives": objectives}
    return OrganisationContext(
        organization_id="org", name="Acme Ltd", generated_at="2026-11-18T12:00:00+00:00",
        isms_profile=profile, isms_results=results or [],
    )


def _objective(**over):
    row = {
        "id": "o1", "ref": "OBJ-01", "objective": "Keep supplier assurance current",
        "measure": "Supplier assurance current (%)", "target": "at least 90%", "owner": "Security Team",
        "resources": "", "due_date": None, "evaluation": "", "status": "in_progress",
        "planned_actions": "Assess every high and critical supplier each year", "type": "maintain",
        "review_date": "2027-06-30", "clauses": ["6.2"],
    }
    row.update(over)
    return row


def _result(**over):
    row = {
        "ref": "OBJ-01", "objective": "Keep supplier assurance current", "metric": "Supplier assurance current (%)",
        "target": "at least 90% · warning below 75%", "value_text": "67%", "status": "At risk",
        "measured_on": "2026-11-17", "population": "2 of 3 high and critical suppliers",
    }
    row.update(over)
    return row


NOT_MEASURED = {
    "ref": "OBJ-01", "objective": "Keep supplier assurance current", "metric": "Supplier assurance current (%)",
    "target": "at least 90%", "value_text": service.NOT_YET_MEASURED, "status": service.NOT_YET_MEASURED,
    "measured_on": None, "population": "",
}


class TestClause62Document:
    def test_what_will_be_done_comes_from_planned_actions_not_the_objective_text(self):
        out = iso_core.render_security_objectives(_ctx([_objective()]))
        assert "- **What will be done:** Assess every high and critical supplier each year" in out
        assert "- **What will be done:** Keep supplier assurance current" not in out
        # the objective text appears once in the planning entry, as the objective
        planning = out.split("## 3. Planning", 1)[1].split("## 4.", 1)[0]
        assert planning.count("Keep supplier assurance current") == 1

    def test_missing_planned_actions_are_marked_not_filled_from_the_name(self):
        out = iso_core.render_security_objectives(_ctx([_objective(planned_actions="")]))
        assert f"- **What will be done:** {TBC}" in out

    def test_text_typed_on_several_lines_stays_inside_its_list_item(self):
        out = iso_core.render_security_objectives(_ctx([_objective(
            planned_actions="Assess suppliers\n\n  Chase renewals \nReport quarterly", resources="Budget\nTwo people")]))
        assert "- **What will be done:** Assess suppliers; Chase renewals; Report quarterly\n" in out
        assert "- **Resources:** Budget; Two people\n" in out

    def test_planning_prints_team_type_and_review_date(self):
        out = iso_core.render_security_objectives(_ctx([_objective()]))
        assert "### OBJ-01" in out
        assert "- **Responsible:** Security Team" in out
        assert "- **Type:** Maintain" in out
        assert "- **Completion:** Ongoing" in out
        assert "- **Review date:** 2027-06-30" in out

    def test_an_achieve_objective_shows_its_due_date_and_a_missing_one_is_marked(self):
        out = iso_core.render_security_objectives(_ctx([
            _objective(type="achieve", due_date="2027-03-31"),
            _objective(ref="OBJ-02", type="achieve", due_date=None, review_date=None),
        ]))
        assert "- **Type:** Achieve by a date" in out
        assert "- **Completion:** 2027-03-31" in out
        assert f"- **Completion:** {TBC}" in out
        assert f"- **Review date:** {TBC}" in out

    def test_the_reference_replaces_the_row_number(self):
        out = iso_core.render_security_objectives(_ctx([_objective(), _objective(ref="OBJ-07")]))
        assert "| Ref | Objective | Measure | Target | Owner | Due | Status |" in out
        assert "| OBJ-01 | Keep supplier assurance current |" in out
        assert "| OBJ-07 |" in out
        assert "| 1 | Keep supplier" not in out

    def test_a_row_without_a_reference_keeps_its_number(self):
        out = iso_core.render_security_objectives(_ctx([_objective(ref=None)]))
        assert "| 1 | Keep supplier assurance current |" in out
        assert "### Objective 1" in out

    def test_the_results_table_prints_value_status_and_date(self):
        out = iso_core.render_security_objectives(_ctx([_objective()], [_result()]))
        results = out.split("## 4. Results", 1)[1].split("## 5.", 1)[0]
        assert "| Ref | Objective | Supporting metric | Target | Latest result | Status | Measured on |" in results
        assert ("| OBJ-01 | Keep supplier assurance current | Supplier assurance current (%) | "
                "at least 90% · warning below 75% | 67% (2 of 3 high and critical suppliers) | At risk | "
                "2026-11-17 |") in results
        assert "## 5. Supporting Indicators" in out

    def test_nothing_measured_gives_a_plain_sentence(self):
        out = iso_core.render_security_objectives(_ctx([_objective()], [NOT_MEASURED]))
        results = out.split("## 4. Results", 1)[1].split("## 5.", 1)[0]
        assert "No results have been recorded for these objectives yet." in results
        assert "| Latest result |" not in results

    def test_an_unmeasured_row_beside_a_measured_one_says_so(self):
        out = iso_core.render_security_objectives(_ctx(
            [_objective(), _objective(ref="OBJ-02", objective="Train every new starter")],
            [_result(), {**NOT_MEASURED, "ref": "OBJ-02", "objective": "Train every new starter",
                         "metric": "", "target": ""}],
        ))
        assert f"| OBJ-02 | Train every new starter | {TBC} | {TBC} | Not yet measured | Not yet measured | — |" in out

    def test_no_objectives_renders_with_both_gaps_stated(self):
        out = iso_core.render_security_objectives(_ctx())
        assert out.startswith("# ")
        assert "record measurable information security objectives" in out
        assert "No information security objectives have been set, so there are no results to report." in out
        assert "## 3. Planning" not in out


class TestManagementReview:
    def test_keeps_the_tally_and_adds_the_results(self):
        out = iso_core.render_management_review(_ctx(
            [_objective(), _objective(ref="OBJ-02", status="achieved")], [_result()],
        ))
        assert "| Objectives | 1 achieved, 1 in progress |" in out
        section = out.split("### Fulfilment of Information Security Objectives", 1)[1].split("### ", 1)[0]
        assert "| OBJ-01 | Keep supplier assurance current |" in section
        assert "| At risk |" in section

    def test_no_objectives_still_renders(self):
        out = iso_core.render_management_review(_ctx())
        assert "| Objectives | None recorded |" in out
        assert "No information security objectives have been set, so there are no results to report." in out


class TestFingerprintIgnoresResults:
    def test_results_are_not_hashed(self):
        a = iso_core.inputs_digest(_ctx([_objective()], [NOT_MEASURED]))
        b = iso_core.inputs_digest(_ctx([_objective()], [_result()]))
        assert a == b

    def test_an_objective_edit_is_hashed(self):
        a = iso_core.inputs_digest(_ctx([_objective()]))
        assert iso_core.inputs_digest(_ctx([_objective(planned_actions="Something else")])) != a


# ---------------------------------------------------------------------------
# The context builder against the database
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


async def _org(db):
    tag = _uuid.uuid4().hex[:10]
    org = Organization(name=f"isms-doc-{tag}", slug=f"isms-doc-{tag}")
    db.add(org)
    await db.flush()
    return org


async def _admin(db, org):
    tag = _uuid.uuid4().hex[:10]
    user = User(google_sub=f"isms-doc-{tag}", email=f"isms-doc-{tag}@example.invalid", display_name=f"P {tag}")
    db.add(user)
    await db.flush()
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


LOGGED = MetricSpec(kind="logged", name="Training completion (%)", unit="percent", direction="at_least",
                    target_value=95, warn_value=85, frequency="quarterly")


async def _active(db, org, admin, team, name, **kw):
    body = {"name": name, "planned_actions": f"Plan for {name}", "owning_team_id": team.id, "metric": LOGGED}
    body.update(kw)
    out = await service.create_objective(db, org.id, ObjectiveCreate(**body), _actor(admin), TODAY)
    await service.confirm_objective(db, org.id, out["id"], ConfirmBody(), _actor(admin), "admin", TODAY)
    return out


def _actor(user):
    return Actor(user_id=user.id, action_source="ui")


async def _context(db, org):
    return await db.run_sync(lambda s: build_context(
        s, str(org.id), include_evidence=False, include_risks=False, include_systems=False, include_isms=True,
    ))


@requires_postgres
@pytest.mark.asyncio
class TestFromTheTable:
    async def test_active_objectives_only_in_reference_order(self, db):
        org = await _org(db)
        admin = await _admin(db, org)
        team = await _team(db, org)
        await service.ensure_starters(db, org.id, TODAY)            # proposed: never in a document
        first = await _active(db, org, admin, team, "First active")
        second = await _active(db, org, admin, team, "Second active")
        archived = await _active(db, org, admin, team, "Archived one")
        await service.update_objective(db, org.id, archived["id"], {"archived": True}, _actor(admin), TODAY)
        await service.create_objective(db, org.id, ObjectiveCreate(name="Still proposed"), _actor(admin), TODAY)
        # numeric, not text, order: OBJ-100 after OBJ-90
        for out, ref in ((first, "OBJ-100"), (second, "OBJ-90")):
            row = await db.get(SecurityObjective, out["id"])
            row.ref = ref
        await db.flush()

        ctx = await _context(db, org)
        objectives = ctx.isms_profile["objectives"]
        assert [o["ref"] for o in objectives] == ["OBJ-90", "OBJ-100"]
        assert [o["objective"] for o in objectives] == ["Second active", "First active"]
        legacy = objectives[0]
        for key in ("objective", "measure", "target", "owner", "due_date", "status", "resources", "evaluation"):
            assert key in legacy
        assert legacy["owner"] == team.name and legacy["status"] == "in_progress"
        assert legacy["measure"] == "Training completion (%)" and legacy["planned_actions"] == "Plan for Second active"
        # the organisation has no isms_profiles row, and its objectives still reach the documents
        assert ctx.isms_profile["scope"] == {}
        assert [r["ref"] for r in ctx.isms_results] == ["OBJ-90", "OBJ-100"]

    async def test_no_objectives_and_no_profile_is_an_empty_profile(self, db):
        org = await _org(db)
        await service.ensure_starters(db, org.id, TODAY)
        ctx = await _context(db, org)
        assert ctx.isms_profile == {} and ctx.isms_results == []
        for render in (iso_core.render_security_objectives, iso_core.render_management_review):
            out = render(ctx)
            assert out.startswith("# ")
            assert "No information security objectives have been set" in out


@requires_postgres
@pytest.mark.asyncio
class TestFingerprint:
    async def test_results_and_starters_do_not_move_it_and_edits_do(self, db):
        org = await _org(db)
        admin = await _admin(db, org)
        team = await _team(db, org)
        out = await _active(db, org, admin, team, "Train every member of staff")
        before = iso_core.inputs_digest(await _context(db, org))

        await service.log_result(db, org.id, out["id"], 88, _actor(admin), TODAY)
        ctx = await _context(db, org)
        assert ctx.isms_results[0]["value_text"] == "88%" and ctx.isms_results[0]["status"] == "At risk"
        assert iso_core.inputs_digest(ctx) == before, "a stored result must not mark the document stale"

        assert await service.ensure_starters(db, org.id, TODAY) > 0
        assert iso_core.inputs_digest(await _context(db, org)) == before, "seeded starters are proposed"

        await service.update_objective(db, org.id, out["id"], {"name": "Train every person"}, _actor(admin), TODAY)
        renamed = iso_core.inputs_digest(await _context(db, org))
        assert renamed != before

        await service.set_metric(db, org.id, out["id"], LOGGED.model_copy(update={"target_value": 99}),
                                 _actor(admin), "admin", TODAY)
        retargeted = iso_core.inputs_digest(await _context(db, org))
        assert retargeted != renamed

        other = await _team(db, org, "Operations")
        await service.update_objective(db, org.id, out["id"], {"owning_team_id": other.id}, _actor(admin), TODAY)
        assert iso_core.inputs_digest(await _context(db, org)) != retargeted


@requires_postgres
@pytest.mark.asyncio
class TestRenderedFromTheTable:
    async def test_clause_62_and_management_review(self, db):
        org = await _org(db)
        admin = await _admin(db, org)
        team = await _team(db, org)
        measured = await _active(db, org, admin, team, "Train every member of staff")
        await _active(db, org, admin, team, "Patch within fourteen days")
        ctx = await _context(db, org)

        unmeasured = iso_core.render_security_objectives(ctx)
        assert "- **What will be done:** Plan for Train every member of staff" in unmeasured
        assert "- **What will be done:** Train every member of staff" not in unmeasured
        assert f"- **Responsible:** {team.name}" in unmeasured
        assert "No results have been recorded for these objectives yet." in unmeasured

        await service.log_result(db, org.id, measured["id"], 96, _actor(admin), TODAY)
        ctx = await _context(db, org)
        ref = measured["ref"]
        doc = iso_core.render_security_objectives(ctx)
        assert f"| {ref} | Train every member of staff | Training completion (%) |" in doc
        assert "| 96% | On track |" in doc
        assert "| Not yet measured | Not yet measured | — |" in doc

        review = iso_core.render_management_review(ctx)
        assert "| Objectives | 2 in progress |" in review
        assert "### Fulfilment of Information Security Objectives" in review
        assert "| 96% | On track |" in review
