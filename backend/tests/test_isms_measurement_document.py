# SPDX-License-Identifier: AGPL-3.0-only
"""The clause 9.1 document: Monitoring, Measurement, Analysis and Evaluation (phase 3, B1).

What an auditor checks against clause 9.1: what is measured, how, how often,
who measures, and when and by whom results are evaluated. So the tests pin:

* the generator is registered as a Tier 1 core-pack document;
* proposed and archived objectives are never printed;
* who measures and who evaluated print the to-be-completed marker when
  nothing is recorded, never an invented name;
* each stored result prints the target of the metric version it was measured
  under, not today's;
* the history caps (12 months for a live metric, 12 periods for a logged one)
  and a fixed number of statements whatever the number of objectives;
* neither a new value nor a new evaluation moves the regeneration fingerprint;
* the management review counts recorded evaluations.

Database tests run the real context builder on a transaction that is always
rolled back, and skip (never pass) when no database resolves.
"""
import uuid as _uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from models import (
    Function, IsmsMetricResult, ObjectiveMetric, Organization, OrganizationMember, SecurityObjective, Team, User,
)
from services.doc_gen import iso_core
from services.doc_gen.context import OrganisationContext, build_context
from services.doc_gen.registry import get_generator
from services.isms_metrics import service
from services.isms_metrics.measurement_document import _months_back, _result_entry, document_measurement_sync
from services.isms_metrics.schemas import ConfirmBody, MetricSpec, ObjectiveCreate
from services.isms_metrics.service import Actor

from test_journey_service import DATABASE_URL, requires_postgres  # noqa: F401

TODAY = date.today()
TBC = iso_core.TBC


# ---------------------------------------------------------------------------
# Registry and renderer, on a hand-built context
# ---------------------------------------------------------------------------


def test_the_generator_is_a_tier_1_core_document():
    gen = get_generator("monitoring-measurement")
    assert gen.tier == 1 and not gen.is_derivative and not gen.domain_scoped
    assert gen.requires.isms
    assert gen.renderer == "iso_core.render_monitoring_measurement"
    assert gen.resolve_renderer() is iso_core.render_monitoring_measurement
    assert ("monitoring-measurement", "Monitoring, Measurement, Analysis and Evaluation") in iso_core._MANUAL_REFERENCES


def _entry(**over):
    row = {
        "ref": "OBJ-03", "objective": "Keep controls implemented", "metric": "Controls implemented (%)",
        "what_is_measured": "Share of selected controls that are implemented.",
        "method": "Implemented controls as a share of selected controls. Calculated by the platform.",
        "kind": "live", "frequency_text": "Every night, by the platform", "measured_by": "Security Team",
        "evaluation_method": "Reviewed every quarter.", "review_date": "2027-03-31",
        "evaluated_by": None, "evaluated_on": None, "evaluation_note": None,
        "status": "On track", "status_as_of": "2026-11-18",
        "results": [{"measured_on": "2026-11-18", "period": None, "value_text": "81%",
                     "target": "at least 80%", "status": "On track", "source": "Computed by the platform",
                     "entered_by": None, "note": None}],
    }
    row.update(over)
    return row


def _ctx(entries=None):
    return OrganisationContext(organization_id="org", name="Acme Ltd", generated_at="2026-11-18T12:00:00+00:00",
                               isms_measurement=entries or [])


class TestRenderer:
    def test_the_what_and_who_table(self):
        out = iso_core.render_monitoring_measurement(_ctx([_entry()]))
        assert "| Ref | Objective | Metric | Method | Frequency | Measured by |" in out
        assert "| OBJ-03 | Keep controls implemented | Controls implemented (%) |" in out
        assert "| Every night, by the platform | Security Team |" in out
        assert "- **Current status:** On track (as of 2026-11-18)" in out
        assert "| Date | Period | Result | Target at the time | Status at the time | Source |" in out
        assert "| 2026-11-18 | — | 81% | at least 80% | On track | Computed by the platform |" in out

    def test_no_evaluation_and_no_owner_print_the_marker_not_a_name(self):
        out = iso_core.render_monitoring_measurement(_ctx([_entry(measured_by=None)]))
        assert "- **Last evaluation:** *[To be completed: record who evaluated these results" in out
        assert f"| Every night, by the platform | {TBC} |" in out
        assert "Evaluation note" not in out

    def test_a_recorded_evaluation_prints_who_when_and_the_note(self):
        out = iso_core.render_monitoring_measurement(_ctx([_entry(
            evaluated_by="Jane Doe", evaluated_on="2026-11-10", evaluation_note="Above target all quarter.")]))
        assert "- **Last evaluation:** 2026-11-10 by Jane Doe" in out
        assert "- **Evaluation note:** Above target all quarter." in out

    def test_an_evaluation_whose_person_is_gone_keeps_the_date_and_marks_the_name(self):
        out = iso_core.render_monitoring_measurement(_ctx([_entry(evaluated_on="2026-11-10")]))
        assert f"- **Last evaluation:** 2026-11-10 by {TBC}" in out
        assert f"- **Evaluation note:** {TBC}" in out

    def test_a_logged_entry_names_who_entered_it(self):
        out = iso_core.render_monitoring_measurement(_ctx([_entry(kind="logged", results=[{
            "measured_on": "2026-11-02", "period": "Q4 2026", "value_text": "88%", "target": "at least 95%",
            "status": "At risk", "source": "Logged", "entered_by": "Sam", "note": None}])]))
        assert "| 2026-11-02 | Q4 2026 | 88% | at least 95% | At risk | Logged by Sam |" in out

    def test_no_metric_and_no_results(self):
        out = iso_core.render_monitoring_measurement(_ctx([
            _entry(kind="unset", results=[], status="Not yet measured", status_as_of=None),
            _entry(ref="OBJ-04", results=[]),
        ]))
        assert "*[To be completed: choose a metric for this objective]*" in out
        assert "No results have been stored for this objective yet." in out

    def test_an_organisation_with_nothing_recorded(self):
        out = iso_core.render_monitoring_measurement(_ctx())
        assert out.startswith("# Monitoring, Measurement, Analysis and Evaluation")
        assert "record measurable information security objectives" in out
        assert "## 4. How Results Are Used" in out

    def test_the_management_review_counts_evaluations(self):
        base = dict(organization_id="org", name="Acme", generated_at="2026-11-18T12:00:00+00:00",
                    isms_profile={"objectives": [{"ref": "OBJ-01", "objective": "x", "status": "in_progress"}]})
        none = iso_core.render_management_review(OrganisationContext(**base, isms_measurement=[_entry()]))
        assert "The objective has no recorded evaluation yet." in none
        two = iso_core.render_management_review(OrganisationContext(**base, isms_measurement=[
            _entry(evaluated_on="2026-10-01"), _entry(ref="OBJ-04", evaluated_on="2026-11-10"), _entry(ref="OBJ-05"),
        ]))
        assert "2 of 3 objectives have a recorded evaluation; the most recent was on 2026-11-10." in two


def test_months_back():
    assert _months_back(date(2026, 11, 18), 11) == date(2025, 12, 1)
    assert _months_back(date(2026, 1, 5), 1) == date(2025, 12, 1)


# ---------------------------------------------------------------------------
# Against the database
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
    org = Organization(name=f"isms-91-{tag}", slug=f"isms-91-{tag}")
    db.add(org)
    await db.flush()
    return org


async def _admin(db, org, name=None):
    tag = _uuid.uuid4().hex[:10]
    user = User(google_sub=f"isms-91-{tag}", email=f"isms-91-{tag}@example.invalid",
                display_name=name or f"Person {tag}")
    db.add(user)
    await db.flush()
    db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role="admin"))
    await db.flush()
    return user


async def _team(db, org):
    function = (await db.execute(select(Function).limit(1))).scalar_one_or_none()
    if function is None:
        pytest.skip("no function rows to hang a team on")
    team = Team(organization_id=org.id, function_id=function.id, name=f"Security {_uuid.uuid4().hex[:6]}")
    db.add(team)
    await db.flush()
    return team


def _actor(user):
    return Actor(user_id=user.id, action_source="ui")


MONTHLY = MetricSpec(kind="logged", name="Patched in time (%)", unit="percent", direction="at_least",
                     target_value=95, warn_value=85, frequency="monthly")
LIVE = MetricSpec(kind="live", measure_key="controls_implemented", target_value=90, warn_value=70)


async def _objective(db, org, admin, team, name, metric=MONTHLY, confirm=True, **extra):
    out = await service.create_objective(db, org.id, ObjectiveCreate(
        name=name, owning_team_id=team.id, metric=metric, **extra), _actor(admin), TODAY)
    if confirm:
        await service.confirm_objective(db, org.id, out["id"], ConfirmBody(), _actor(admin), "admin", TODAY)
    return out


async def _context(db, org):
    return await db.run_sync(lambda s: build_context(
        s, str(org.id), include_evidence=False, include_risks=False, include_systems=False, include_isms=True,
    ))


def _section(doc, ref):
    return doc.split(f"### {ref}\n", 1)[1].split("\n### ", 1)[0]


@requires_postgres
@pytest.mark.asyncio
class TestFromTheDatabase:
    async def test_proposed_and_archived_are_not_printed(self, db):
        org = await _org(db)
        admin = await _admin(db, org)
        team = await _team(db, org)
        await service.ensure_starters(db, org.id, TODAY)
        kept = await _objective(db, org, admin, team, "Kept")
        archived = await _objective(db, org, admin, team, "Archived")
        await service.update_objective(db, org.id, archived["id"], {"archived": True}, _actor(admin), TODAY)
        proposed = await _objective(db, org, admin, team, "Still proposed", confirm=False)

        ctx = await _context(db, org)
        assert [e["ref"] for e in ctx.isms_measurement] == [kept["ref"]]
        doc = iso_core.render_monitoring_measurement(ctx)
        assert f"### {kept['ref']}" in doc
        for ref in (archived["ref"], proposed["ref"]):
            assert f"| {ref} |" not in doc and f"### {ref}\n" not in doc
        assert ctx.isms_measurement[0]["measured_by"] == team.name

    async def test_each_result_prints_the_target_in_force_when_it_was_measured(self, db):
        org = await _org(db)
        admin = await _admin(db, org)
        team = await _team(db, org)
        out = await _objective(db, org, admin, team, "Patch on time")
        last_month = _months_back(TODAY, 1)
        await service.log_result(db, org.id, out["id"], 90, _actor(admin), TODAY, period_start=last_month)
        await service.set_metric(db, org.id, out["id"], MONTHLY.model_copy(update={"target_value": 99}),
                                 _actor(admin), "admin", TODAY)
        await service.log_result(db, org.id, out["id"], 97, _actor(admin), TODAY)

        ctx = await _context(db, org)
        entry = ctx.isms_measurement[0]
        # A value back-filled for last month is judged as of that month (B6): 90 against
        # at least 95 with a warning below 85 is At risk, never "Measurement overdue".
        assert [(r["value_text"], r["target"], r["status"]) for r in entry["results"]] == [
            ("90%", "at least 95% · warning below 85%", "At risk"),
            ("97%", "at least 99% · warning below 85%", "At risk"),
        ]
        rows = _table_rows(iso_core.render_monitoring_measurement(ctx), out["ref"])
        assert f"| {last_month.strftime('%B %Y')} | 90% | at least 95% · warning below 85% | At risk |" in rows[2]
        assert "Measurement overdue" not in "\n".join(rows)
        assert entry["results"][0]["source"] == "Logged" and entry["results"][0]["entered_by"] == admin.display_name
        assert entry["results"][0]["period"] == last_month.strftime("%B %Y")

    async def test_logged_history_prints_in_period_order_not_entry_order(self, db):
        org = await _org(db)
        admin = await _admin(db, org)
        team = await _team(db, org)
        quarterly = MONTHLY.model_copy(update={"frequency": "quarterly"})
        out = await _objective(db, org, admin, team, "Train staff", metric=quarterly)
        await service.log_result(db, org.id, out["id"], 96, _actor(admin), date(2026, 10, 15))   # Q4, in October
        await service.log_result(db, org.id, out["id"], 91, _actor(admin), date(2026, 11, 18),
                                 period_start=date(2026, 4, 1))                                  # Q2, back-filled
        ctx = await _context(db, org)
        assert [r["period"] for r in ctx.isms_measurement[0]["results"]] == ["Q2 2026", "Q4 2026"]
        rows = _table_rows(iso_core.render_monitoring_measurement(ctx), out["ref"])
        assert "| Q2 2026 |" in rows[2] and "| Q4 2026 |" in rows[3]

    async def test_milestone_history_keeps_the_newest_12_entries(self, db):
        org = await _org(db)
        admin = await _admin(db, org)
        team = await _team(db, org)
        out = await _objective(db, org, admin, team, "Policy approved",
                               metric=MetricSpec(kind="milestone", name="Policy approved"),
                               objective_type="achieve", due_date=TODAY + timedelta(days=30))
        metric = await service.active_metric(db, org.id, out["id"])
        days = [TODAY - timedelta(days=n) for n in range(14)]
        for day in days:
            db.add(IsmsMetricResult(organization_id=org.id, objective_metric_id=metric.id, measured_on=day,
                                    period_start=day, value=Decimal(1), status="on_track", detail={},
                                    source="logged"))
        await db.flush()
        results = (await _context(db, org)).isms_measurement[0]["results"]
        assert [r["measured_on"] for r in results] == [d.isoformat() for d in sorted(days[:12])]

    async def test_a_multi_paragraph_evaluation_note_adds_no_heading(self, db):
        org = await _org(db)
        admin = await _admin(db, org)
        team = await _team(db, org)
        out = await _objective(db, org, admin, team, "Patch on time")
        await service.record_evaluation(db, org.id, out["id"], "On target.\n\n# Actions\nKeep going.",
                                        _actor(admin), TODAY)
        doc = iso_core.render_monitoring_measurement(await _context(db, org))
        assert "# Actions" not in [line.strip() for line in doc.splitlines()]
        assert "- **Evaluation note:** On target.; # Actions; Keep going." in _section(doc, out["ref"])

    async def test_logged_history_keeps_the_newest_entry_of_the_last_12_periods(self, db):
        org = await _org(db)
        admin = await _admin(db, org)
        team = await _team(db, org)
        out = await _objective(db, org, admin, team, "Patch on time")
        for back in range(14):
            await service.log_result(db, org.id, out["id"], 50 + back, _actor(admin), TODAY,
                                     period_start=_months_back(TODAY, back))
        # a correction to the current period: only the newer entry counts
        await service.log_result(db, org.id, out["id"], 99, _actor(admin), TODAY)

        results = (await _context(db, org)).isms_measurement[0]["results"]
        assert len(results) == 12
        periods = [r["period"] for r in results]
        assert _months_back(TODAY, 0).strftime("%B %Y") in periods
        assert _months_back(TODAY, 11).strftime("%B %Y") in periods
        assert _months_back(TODAY, 12).strftime("%B %Y") not in periods
        current = [r for r in results if r["period"] == TODAY.strftime("%B %Y")]
        assert [r["value_text"] for r in current] == ["99%"]

    async def test_live_history_keeps_the_newest_row_of_each_of_the_last_12_months(self, db):
        org = await _org(db)
        admin = await _admin(db, org)
        team = await _team(db, org)
        out = await _objective(db, org, admin, team, "Controls implemented", metric=LIVE)
        metric = await service.active_metric(db, org.id, out["id"])
        days = [_months_back(TODAY, back) + timedelta(days=2) for back in range(14)]
        days.append(_months_back(TODAY, 3) + timedelta(days=5))     # a newer row in the same month
        for n, day in enumerate(days):
            db.add(IsmsMetricResult(organization_id=org.id, objective_metric_id=metric.id, measured_on=day,
                                    value=Decimal(40 + n), status="off_track", detail={}, source="computed"))
        await db.flush()

        results = (await _context(db, org)).isms_measurement[0]["results"]
        assert len(results) == 12
        dates = [r["measured_on"] for r in results]
        assert dates == sorted(dates)
        assert (_months_back(TODAY, 3) + timedelta(days=5)).isoformat() in dates
        assert (_months_back(TODAY, 3) + timedelta(days=2)).isoformat() not in dates
        assert (_months_back(TODAY, 12) + timedelta(days=2)).isoformat() not in dates
        assert all(r["target"] == "at least 90% · warning below 70%" and r["status"] == "Off track" for r in results)

    async def test_the_statement_count_does_not_grow_with_objectives(self, db):
        org = await _org(db)
        admin = await _admin(db, org)
        team = await _team(db, org)

        def statements(session):
            count = [0]

            def tick(*_a, **_k):
                count[0] += 1
            bind = session.get_bind()
            event.listen(bind, "before_cursor_execute", tick)
            try:
                document_measurement_sync(session, org.id, TODAY, current=[])
            finally:
                event.remove(bind, "before_cursor_execute", tick)
            return count[0]

        first = await _objective(db, org, admin, team, "One")
        await service.log_result(db, org.id, first["id"], 90, _actor(admin), TODAY)
        await service.record_evaluation(db, org.id, first["id"], "Checked", _actor(admin), TODAY)
        one = await db.run_sync(statements)
        for name in ("Two", "Three", "Four"):
            more = await _objective(db, org, admin, team, name)
            await service.log_result(db, org.id, more["id"], 80, _actor(admin), TODAY)
            await service.record_evaluation(db, org.id, more["id"], "Checked", _actor(admin), TODAY)
        four = await db.run_sync(statements)
        assert one == four <= 6

    async def test_a_new_value_or_evaluation_does_not_move_the_fingerprint(self, db):
        org = await _org(db)
        admin = await _admin(db, org, name="Jane Evaluator")
        team = await _team(db, org)
        out = await _objective(db, org, admin, team, "Patch on time")
        before = await _context(db, org)
        digest = iso_core.inputs_digest(before, exclude_generator="monitoring-measurement")
        assert f"- **Last evaluation:** {iso_core._tbc('record who evaluated')}"[:60] in \
            iso_core.render_monitoring_measurement(before)

        await service.log_result(db, org.id, out["id"], 96, _actor(admin), TODAY)
        await service.record_evaluation(db, org.id, out["id"], "On target", _actor(admin), TODAY)
        after = await _context(db, org)
        assert after.isms_measurement[0]["results"] and after.isms_measurement[0]["evaluated_by"] == "Jane Evaluator"
        assert iso_core.inputs_digest(after, exclude_generator="monitoring-measurement") == digest

        doc = _section(iso_core.render_monitoring_measurement(after), out["ref"])
        assert f"- **Last evaluation:** {TODAY.isoformat()} by Jane Evaluator" in doc
        assert "- **Evaluation note:** On target" in doc
        review = iso_core.render_management_review(after)
        assert f"1 of 1 objective has a recorded evaluation; the most recent was on {TODAY.isoformat()}." in review

    async def test_an_evaluator_who_left_is_not_named(self, db):
        org = await _org(db)
        admin = await _admin(db, org, name="Jane Evaluator")
        team = await _team(db, org)
        out = await _objective(db, org, admin, team, "Patch on time")
        await service.record_evaluation(db, org.id, out["id"], "On target", _actor(admin), TODAY)
        row = await db.get(SecurityObjective, out["id"])
        row.last_evaluated_by_user_id = None              # what ON DELETE SET NULL leaves behind
        await db.flush()
        doc = iso_core.render_monitoring_measurement(await _context(db, org))
        assert f"- **Last evaluation:** {TODAY.isoformat()} by {TBC}" in doc
        assert "Jane Evaluator" not in doc


# ---------------------------------------------------------------------------
# Collector source label (B4 writes detail = {"origin", "collector"})
# ---------------------------------------------------------------------------


async def _key_row(db, org, out, value, period, collector, owner):
    """A logged row as an API-key caller leaves it (B4): origin and collector in ``detail``."""
    metric = await service.active_metric(db, org.id, out["id"])
    db.add(IsmsMetricResult(organization_id=org.id, objective_metric_id=metric.id, measured_on=TODAY,
                            period_start=period, value=Decimal(value), status="on_track",
                            detail={"origin": "api_key", "collector": collector}, source="logged",
                            entered_by_user_id=owner.id if owner else None))
    await db.flush()


def _table_rows(doc, ref):
    """The results-table lines of one objective's section, header included."""
    return [line for line in _section(doc, ref).splitlines() if line.startswith("|")]


@requires_postgres
@pytest.mark.asyncio
class TestCollectorSource:
    async def test_a_collector_row_names_the_collector_not_the_key_owner(self, db):
        org = await _org(db)
        admin = await _admin(db, org, name="Key Owner")
        team = await _team(db, org)
        out = await _objective(db, org, admin, team, "Patch on time")
        await service.log_result(db, org.id, out["id"], 91, _actor(admin), TODAY, period_start=_months_back(TODAY, 2))
        await _key_row(db, org, out, 96, _months_back(TODAY, 1), "patch-sla", admin)
        await _key_row(db, org, out, 97, _months_back(TODAY, 0), "patch-sla", None)

        doc = iso_core.render_monitoring_measurement(await _context(db, org))
        rows = _table_rows(doc, out["ref"])
        # the collector and the accountable person are both named (review M2)
        assert [r for r in rows if "96%" in r][0].endswith("| Logged by collector patch-sla (key of Key Owner) |")
        assert [r for r in rows if "97%" in r][0].endswith("| Logged by collector patch-sla |")
        assert [r for r in rows if "91%" in r][0].endswith("| Logged by Key Owner |")

    async def test_a_collector_name_with_a_pipe_and_newline_keeps_the_columns(self, db):
        org = await _org(db)
        admin = await _admin(db, org)
        team = await _team(db, org)
        out = await _objective(db, org, admin, team, "Patch on time")
        await _key_row(db, org, out, 96, _months_back(TODAY, 0), "a|b\nc", None)

        doc = iso_core.render_monitoring_measurement(await _context(db, org))
        rows = _table_rows(doc, out["ref"])
        assert len(rows) == 3                                    # header, rule, one result
        assert {r.count("|") for r in rows} == {7}               # six columns in every line
        assert rows[2].endswith("| Logged by collector a/b c |")

    async def test_a_row_from_before_the_collector_feed_still_renders(self, db):
        org = await _org(db)
        admin = await _admin(db, org, name="Early Bird")
        team = await _team(db, org)
        out = await _objective(db, org, admin, team, "Patch on time")
        metric = await service.active_metric(db, org.id, out["id"])
        db.add(IsmsMetricResult(organization_id=org.id, objective_metric_id=metric.id, measured_on=TODAY,
                                period_start=_months_back(TODAY, 0), value=Decimal(96), status="on_track",
                                detail={}, source="logged", entered_by_user_id=admin.id))
        await db.flush()

        rows = _table_rows(iso_core.render_monitoring_measurement(await _context(db, org)), out["ref"])
        assert len(rows) == 3 and rows[2].endswith("| Logged by Early Bird |")


def test_a_result_with_no_detail_at_all_still_renders():
    """``detail`` is NOT NULL in the table; an unflushed row can still carry None."""
    version = ObjectiveMetric(kind="logged", unit="percent", direction="at_least", target_value=95, warn_value=85,
                              frequency="monthly", name="Patched (%)")
    row = IsmsMetricResult(measured_on=date(2026, 11, 18), period_start=date(2026, 11, 1), value=Decimal(96),
                           status="on_track", detail=None, source="logged", entered_by_user_id=None, note=None)
    entry = _result_entry(row, version, {})
    assert entry["source"] == "Logged" and entry["entered_by"] is None and entry["period"] == "November 2026"
    assert entry["collector"] is None


def test_the_source_label_comes_from_fields_not_from_the_label_text():
    """D1: who and what are separate fields; no word in a label changes what is printed."""
    def row(**kw):
        return {"source": "Logged", "entered_by": None, "collector": None, **kw}
    assert iso_core._source_text(row(entered_by="Alice Example")) == "Logged by Alice Example"
    assert iso_core._source_text(row(collector="patch-sla")) == "Logged by collector patch-sla"
    assert iso_core._source_text(row(collector="patch-sla", entered_by="Alice Example")) == \
        "Logged by collector patch-sla (key of Alice Example)"
    # a person whose name holds the word is still named
    assert iso_core._source_text(row(entered_by="Collector Smith")) == "Logged by Collector Smith"
    assert iso_core._source_text({"source": "Computed by the platform"}) == "Computed by the platform"


def test_free_text_stays_inside_its_bullet():
    """D3: a note, method, name or measure typed on several lines adds no heading or section."""
    note = "Above target all quarter.\n\n# Actions\nPatch the last two servers."
    out = iso_core.render_monitoring_measurement(_ctx([_entry(
        evaluated_on="2026-11-10", evaluated_by="Jane\n# Doe", evaluation_note=note,
        what_is_measured="Share of servers\n## patched", evaluation_method="Quarterly\n### review")]))
    assert not any(line.startswith(("# Actions", "# Doe", "## patched", "### review")) for line in out.splitlines())
    assert "- **Evaluation note:** Above target all quarter.; # Actions; Patch the last two servers." in out
    assert "- **Last evaluation:** 2026-11-10 by Jane; # Doe" in out
    assert "- **What is measured:** Share of servers; ## patched" in out
    assert "- **How results are evaluated:** Quarterly; ### review" in out
