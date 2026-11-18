# SPDX-License-Identifier: AGPL-3.0-only
"""The security objectives service against a real Postgres, on a rolled-back transaction.

Covers seeding, create, update, archive, metric versioning, confirm,
evaluation, logged results, tenant isolation, audit rows, nightly recording
and the legacy shape for documents. The service never commits, so nothing
here outlives the test. Skips (never passes) when no database resolves.
"""
import uuid as _uuid
from datetime import date, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from models import (
    AuditLog,
    Function,
    IsmsMetricResult,
    ObjectiveMetric,
    Organization,
    OrganizationMember,
    SecurityObjective,
    Team,
    User,
)
from services.isms_metrics import library, service
from services.isms_metrics.schemas import ConfirmBody, MetricSpec, ObjectiveCreate, ObjectiveUpdate
from services.isms_metrics.service import Actor, ObjectiveError

from test_journey_service import DATABASE_URL, requires_postgres  # noqa: F401

TODAY = date(2026, 11, 18)

pytestmark = [requires_postgres, pytest.mark.asyncio]


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


async def _user(db, org, role="admin"):
    tag = _uuid.uuid4().hex[:10]
    user = User(google_sub=f"isms-{tag}", email=f"isms-{tag}@example.invalid", display_name=f"Person {tag}")
    db.add(user)
    await db.flush()
    db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=role))
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


def _actor(user):
    return Actor(user_id=user.id, action_source="ui")


async def _audit_count(db, entity_id, entity_type=service.ENTITY_TYPE):
    return int(await db.scalar(select(func.count()).select_from(AuditLog).where(
        AuditLog.entity_id == entity_id, AuditLog.entity_type == entity_type)) or 0)


async def _metrics(db, objective_id):
    return (await db.execute(select(ObjectiveMetric).where(ObjectiveMetric.objective_id == objective_id)
                             .order_by(ObjectiveMetric.active_from))).scalars().all()


LOGGED = MetricSpec(kind="logged", name="Training completion (%)", unit="percent", direction="at_least",
                    target_value=95, warn_value=85, frequency="quarterly")


async def _create(db, org, user, **kw):
    body = {"name": "Keep the lights on", "planned_actions": "Do the work"}
    body.update(kw)
    return await service.create_objective(db, org.id, ObjectiveCreate(**body), _actor(user), TODAY)


# ---------------------------------------------------------------------------
# Starters
# ---------------------------------------------------------------------------


async def test_seeding_is_idempotent_and_yields_twelve_proposed(db):
    org = await _org(db)
    assert await service.ensure_starters(db, org.id, TODAY, Actor.system()) == 12
    assert await service.ensure_starters(db, org.id, TODAY, Actor.system()) == 0
    rows = (await db.execute(select(SecurityObjective).where(SecurityObjective.organization_id == org.id))).scalars().all()
    assert len(rows) == 12
    assert {o.lifecycle_status for o in rows} == {"proposed"}
    assert sorted(o.ref for o in rows) == [f"OBJ-{n:02d}" for n in range(1, 13)]
    metrics = await service._active_metrics(db, org.id, [o.id for o in rows])
    kinds = [m.kind for m in metrics.values()]
    assert (kinds.count("live"), kinds.count("logged"), kinds.count("milestone")) == (8, 3, 1)
    assert all(m.target_set_by_user_id is None for m in metrics.values())
    for o in rows:
        assert await _audit_count(db, o.id) == 1
    risk = next(o for o in rows if o.source == "starter:risk_appetite")
    assert risk.ref == "OBJ-01"
    assert metrics[risk.id].name == "Risks within appetite (%)"
    assert float(metrics[risk.id].target_value) == 100 and float(metrics[risk.id].warn_value) == 90
    assert metrics[risk.id].frequency == "nightly"
    assert risk.evaluation_method.endswith("Measured nightly by the platform from Risk Register.")


async def test_archived_starter_counts_as_present(db):
    org = await _org(db)
    user = await _user(db, org)
    await service.ensure_starters(db, org.id, TODAY)
    starter = (await db.execute(select(SecurityObjective).where(
        SecurityObjective.organization_id == org.id, SecurityObjective.source == "starter:access_reviews"))).scalar_one()
    await service.update_objective(db, org.id, starter.id, {"archived": True}, _actor(user), TODAY)
    assert await service.ensure_starters(db, org.id, TODAY) == 0


async def test_list_seeds_and_summarises(db):
    org = await _org(db)
    out = await service.list_objectives(db, org.id, TODAY)
    assert len(out["items"]) == 12
    assert [i["ref"] for i in out["items"]] == [f"OBJ-{n:02d}" for n in range(1, 13)]
    assert out["summary"]["objectives"] == 12
    assert out["summary"]["proposed"] == 12 and out["summary"]["needs_attention"] == 12
    item = out["items"][0]
    assert item["reading"]["status"] == "proposed"
    assert item["reading"]["population"].endswith("risks")
    assert item["metric"]["target_set_by_name"] is None


# ---------------------------------------------------------------------------
# Create, update, archive
# ---------------------------------------------------------------------------


async def test_create_is_proposed_and_audited(db):
    org = await _org(db)
    user = await _user(db, org, "editor")
    team = await _team(db, org)
    out = await _create(db, org, user, owning_team_id=team.id, iso_clauses=["6.2", "6.2", " 9.1 "], metric=LOGGED)
    assert out["lifecycle_status"] == "proposed" and out["source"] == "custom"
    assert out["ref"] == "OBJ-01"
    assert out["owning_team_name"] == team.name
    assert out["iso_clauses"] == ["6.2", "9.1"]
    assert out["metric"]["kind"] == "logged" and out["metric"]["target_text"] == "at least 95%"
    assert out["reading"]["status"] == "proposed"
    assert await _audit_count(db, out["id"]) >= 2


async def test_create_refuses_another_organisations_team(db):
    org, other = await _org(db), await _org(db)
    user = await _user(db, org)
    foreign = await _team(db, other)
    with pytest.raises(ObjectiveError) as err:
        await _create(db, org, user, owning_team_id=foreign.id)
    assert err.value.status_code == 422


async def test_refs_continue_after_starters(db):
    org = await _org(db)
    user = await _user(db, org)
    await service.ensure_starters(db, org.id, TODAY)
    out = await _create(db, org, user)
    assert out["ref"] == "OBJ-13"


async def test_update_fields_and_lifecycle_rules(db):
    org = await _org(db)
    user = await _user(db, org, "editor")
    out = await _create(db, org, user)
    before = await _audit_count(db, out["id"])
    updated = await service.update_objective(db, org.id, out["id"], ObjectiveUpdate(
        name="Renamed", resources="Two people", review_date=date(2027, 1, 1)), _actor(user), TODAY)
    assert updated["name"] == "Renamed" and updated["resources"] == "Two people"
    assert await _audit_count(db, out["id"]) > before
    with pytest.raises(ObjectiveError) as err:
        await service.update_objective(db, org.id, out["id"], {"lifecycle_status": "achieved"}, _actor(user), TODAY)
    assert err.value.message == "Confirm the objective first" and err.value.status_code == 422
    with pytest.raises(ObjectiveError):
        await service.update_objective(db, org.id, out["id"], {}, _actor(user), TODAY)


async def test_archive_and_restore(db):
    org = await _org(db)
    user = await _user(db, org, "editor")
    out = await _create(db, org, user)
    archived = await service.update_objective(db, org.id, out["id"], {"archived": True}, _actor(user), TODAY)
    assert archived["archived_at"] is not None
    listed = await service.list_objectives(db, org.id, TODAY, seed=False)
    assert listed["items"] == []
    with_archived = await service.list_objectives(db, org.id, TODAY, include_archived=True, seed=False)
    assert len(with_archived["items"]) == 1 and with_archived["summary"]["objectives"] == 0
    restored = await service.update_objective(db, org.id, out["id"], {"archived": False}, _actor(user), TODAY)
    assert restored["archived_at"] is None


async def test_another_organisations_objective_is_not_found(db):
    org, other = await _org(db), await _org(db)
    user = await _user(db, org)
    out = await _create(db, org, user)
    for call in (
        service.get_objective(db, other.id, out["id"], TODAY),
        service.update_objective(db, other.id, out["id"], {"name": "x"}, _actor(user), TODAY),
        service.metric_detail(db, other.id, out["id"], TODAY),
        service.log_result(db, other.id, out["id"], 1, _actor(user), TODAY),
    ):
        with pytest.raises(ObjectiveError) as err:
            await call
        assert err.value.status_code == 404


# ---------------------------------------------------------------------------
# Metric versioning and confirm
# ---------------------------------------------------------------------------


async def test_metric_versioning_keeps_one_active_and_the_old_row(db):
    org = await _org(db)
    user = await _user(db, org, "editor")
    out = await _create(db, org, user, metric=LOGGED)
    first = out["metric"]["id"]
    again = await service.set_metric(db, org.id, out["id"], MetricSpec(
        kind="live", measure_key="controls_implemented", target_value=80, warn_value=60), _actor(user), "editor", TODAY)
    rows = await _metrics(db, out["id"])
    assert len(rows) == 2
    assert [r.active_to is None for r in rows] == [False, True]
    assert rows[0].id == first
    assert again["metric"]["kind"] == "live" and again["metric"]["unit"] == "percent"
    assert again["metric"]["name"] == "Controls implemented (%)"
    assert again["metric"]["target_set_by_user_id"] is None  # still proposed


async def test_metric_rules(db):
    org = await _org(db)
    user = await _user(db, org)
    out = await _create(db, org, user)
    bad = [
        MetricSpec(kind="live", measure_key="nope"),
        MetricSpec(kind="live", measure_key="controls_implemented", target_value=120),
        MetricSpec(kind="live", measure_key="controls_implemented", target_value=80, warn_value=90),
        MetricSpec(kind="live", measure_key="open_material_deficiencies", target_value=0, warn_value=0),
        MetricSpec(kind="live", measure_key="open_material_deficiencies", target_value=-1),
        MetricSpec(kind="logged", unit="percent", frequency="monthly"),
        MetricSpec(kind="logged", name="x", frequency="monthly"),
        MetricSpec(kind="live", measure_key="controls_implemented", warn_value=50),
    ]
    for spec in bad:
        with pytest.raises(ObjectiveError) as err:
            await service.set_metric(db, org.id, out["id"], spec, _actor(user), "admin", TODAY)
        assert err.value.status_code == 422, spec


async def test_editor_may_set_metric_on_proposed_but_not_on_confirmed(db):
    org = await _org(db)
    admin = await _user(db, org, "admin")
    editor = await _user(db, org, "editor")
    team = await _team(db, org)
    out = await _create(db, org, editor, owning_team_id=team.id, metric=LOGGED)
    await service.set_metric(db, org.id, out["id"], LOGGED, _actor(editor), "editor", TODAY)
    await service.confirm_objective(db, org.id, out["id"], ConfirmBody(), _actor(admin), "admin", TODAY)
    with pytest.raises(ObjectiveError) as err:
        await service.set_metric(db, org.id, out["id"], LOGGED, _actor(editor), "editor", TODAY)
    assert err.value.status_code == 403
    changed = await service.set_metric(db, org.id, out["id"], LOGGED, _actor(admin), "admin", TODAY)
    assert changed["metric"]["target_set_by_user_id"] == admin.id


async def test_confirm_refuses_without_team_and_without_target(db):
    org = await _org(db)
    admin = await _user(db, org)
    team = await _team(db, org)
    no_target = MetricSpec(kind="live", measure_key="controls_implemented")
    out = await _create(db, org, admin, metric=no_target)
    with pytest.raises(ObjectiveError) as err:
        await service.confirm_objective(db, org.id, out["id"], ConfirmBody(), _actor(admin), "admin", TODAY)
    assert err.value.message == "Choose a responsible team before confirming"
    with pytest.raises(ObjectiveError) as err:
        await service.confirm_objective(db, org.id, out["id"], ConfirmBody(owning_team_id=team.id), _actor(admin),
                                        "admin", TODAY)
    assert err.value.message == "Set a target before confirming"
    with pytest.raises(ObjectiveError) as err:
        await service.confirm_objective(db, org.id, out["id"], ConfirmBody(owning_team_id=team.id, target_value=80),
                                        _actor(admin), "editor", TODAY)
    assert err.value.status_code == 403


async def test_confirm_milestone_needs_due_date(db):
    org = await _org(db)
    admin = await _user(db, org)
    team = await _team(db, org)
    out = await _create(db, org, admin, owning_team_id=team.id, metric=MetricSpec(kind="milestone", name="Cert"))
    with pytest.raises(ObjectiveError) as err:
        await service.confirm_objective(db, org.id, out["id"], ConfirmBody(), _actor(admin), "admin", TODAY)
    assert err.value.message == "Set a due date before confirming"
    done = await service.confirm_objective(db, org.id, out["id"], ConfirmBody(due_date=date(2027, 6, 30)),
                                           _actor(admin), "admin", TODAY)
    assert done["lifecycle_status"] == "active" and done["reading"]["status"] == "in_progress"


async def test_confirm_moves_to_active_and_stamps_target_setter(db):
    org = await _org(db)
    admin = await _user(db, org)
    team = await _team(db, org)
    await service.ensure_starters(db, org.id, TODAY)
    starter = (await db.execute(select(SecurityObjective).where(
        SecurityObjective.organization_id == org.id,
        SecurityObjective.source == "starter:controls_implemented"))).scalar_one()
    out = await service.confirm_objective(db, org.id, starter.id, ConfirmBody(
        owning_team_id=team.id, target_value=90, warn_value=70), _actor(admin), "admin", TODAY)
    assert out["lifecycle_status"] == "active"
    assert out["metric"]["target_set_by_user_id"] == admin.id
    assert out["metric"]["target_set_by_name"] == admin.display_name
    assert out["metric"]["target_value"] == 90 and out["metric"]["warn_value"] == 70
    assert len(await _metrics(db, starter.id)) == 2
    with pytest.raises(ObjectiveError) as err:
        await service.confirm_objective(db, org.id, starter.id, ConfirmBody(), _actor(admin), "admin", TODAY)
    assert err.value.status_code == 422
    # now confirmed, the lifecycle can move
    achieved = await service.update_objective(db, org.id, starter.id, {"lifecycle_status": "achieved"},
                                              _actor(admin), TODAY)
    assert achieved["lifecycle_status"] == "achieved"


# ---------------------------------------------------------------------------
# Evaluation and results
# ---------------------------------------------------------------------------


async def test_record_evaluation(db):
    org = await _org(db)
    user = await _user(db, org, "editor")
    out = await _create(db, org, user)
    with pytest.raises(ObjectiveError):
        await service.record_evaluation(db, org.id, out["id"], "   ", _actor(user), TODAY)
    before = await _audit_count(db, out["id"])
    done = await service.record_evaluation(db, org.id, out["id"], "Reviewed at management review", _actor(user), TODAY)
    assert done["last_evaluation_note"] == "Reviewed at management review"
    assert done["last_evaluated_by_name"] == user.display_name and done["last_evaluated_at"] is not None
    assert await _audit_count(db, out["id"]) > before


async def test_log_result_validation_default_period_and_correction(db):
    org = await _org(db)
    admin = await _user(db, org)
    team = await _team(db, org)
    out = await _create(db, org, admin, owning_team_id=team.id, metric=LOGGED)
    await service.confirm_objective(db, org.id, out["id"], ConfirmBody(), _actor(admin), "admin", TODAY)
    fresh = await service.get_objective(db, org.id, out["id"], TODAY)
    assert fresh["reading"]["status"] == "first_measurement_due"
    for bad in (101, -1):
        with pytest.raises(ObjectiveError):
            await service.log_result(db, org.id, out["id"], bad, _actor(admin), TODAY)
    with pytest.raises(ObjectiveError):
        await service.log_result(db, org.id, out["id"], 50, _actor(admin), TODAY, period_start=date(2027, 1, 1))

    first = await service.log_result(db, org.id, out["id"], 88, _actor(admin), TODAY, note="First pass")
    assert first["reading"]["status"] == "at_risk" and first["reading"]["value_text"] == "88%"
    assert first["reading"]["current_period_start"] == date(2026, 10, 1)
    corrected = await service.log_result(db, org.id, out["id"], 96, _actor(admin), TODAY)
    assert corrected["reading"]["status"] == "on_track"
    assert corrected["reading"]["logged_by_name"] == admin.display_name

    rows = (await db.execute(select(IsmsMetricResult).where(
        IsmsMetricResult.objective_metric_id == corrected["metric"]["id"]))).scalars().all()
    assert len(rows) == 2 and {r.period_start for r in rows} == {date(2026, 10, 1)}
    for r in rows:
        assert await _audit_count(db, r.id, service.RESULT_ENTITY_TYPE) >= 1

    stale = await service.get_objective(db, org.id, out["id"], date(2027, 1, 5))
    assert stale["reading"]["status"] == "measurement_overdue"


async def test_results_point_at_the_metric_version_in_force(db):
    org = await _org(db)
    user = await _user(db, org, "editor")
    out = await _create(db, org, user, metric=LOGGED)
    await service.log_result(db, org.id, out["id"], 50, _actor(user), TODAY)
    second = await service.set_metric(db, org.id, out["id"], LOGGED.model_copy(update={"target_value": 90}),
                                      _actor(user), "editor", TODAY)
    await service.log_result(db, org.id, out["id"], 60, _actor(user), TODAY)
    detail = await service.metric_detail(db, org.id, out["id"], TODAY)
    assert len(detail["metric_versions"]) == 2
    assert [r["value"] for r in detail["results"]] == [60.0, 50.0]
    rows = (await db.execute(select(IsmsMetricResult).join(
        ObjectiveMetric, ObjectiveMetric.id == IsmsMetricResult.objective_metric_id).where(
        ObjectiveMetric.objective_id == out["id"]).order_by(IsmsMetricResult.created_at))).scalars().all()
    assert rows[0].objective_metric_id == out["metric"]["id"]
    assert rows[1].objective_metric_id == second["metric"]["id"]
    assert detail["results"][0]["target_label"] == "at least 90% · warning below 85%"


async def test_results_refused_for_live_metric(db):
    org = await _org(db)
    user = await _user(db, org, "editor")
    out = await _create(db, org, user, metric=MetricSpec(kind="live", measure_key="controls_implemented"))
    with pytest.raises(ObjectiveError) as err:
        await service.log_result(db, org.id, out["id"], 50, _actor(user), TODAY)
    assert err.value.status_code == 422


async def test_milestone_result_is_zero_or_one(db):
    org = await _org(db)
    user = await _user(db, org, "editor")
    out = await _create(db, org, user, metric=MetricSpec(kind="milestone", name="Cert"))
    with pytest.raises(ObjectiveError):
        await service.log_result(db, org.id, out["id"], 0.5, _actor(user), TODAY)
    done = await service.log_result(db, org.id, out["id"], 1, _actor(user), TODAY)
    assert done["reading"]["value_text"] == "Done"


async def test_a_logged_value_survives_a_new_metric_version(db):
    """A target change, a rename or the confirm step opens a new version; the value people typed stays."""
    org = await _org(db)
    admin = await _user(db, org)
    out = await _create(db, org, admin, metric=LOGGED)
    logged = await service.log_result(db, org.id, out["id"], 96, _actor(admin), TODAY)
    assert logged["reading"]["value"] == 96

    raised = await service.set_metric(db, org.id, out["id"], LOGGED.model_copy(update={"target_value": 97}),
                                      _actor(admin), "admin", TODAY)
    assert raised["metric"]["id"] != out["metric"]["id"]
    assert raised["reading"]["value"] == 96 and raised["reading"]["value_text"] == "96%"
    assert raised["reading"]["status"] != "first_measurement_due"
    listed = await service.list_objectives(db, org.id, TODAY)
    row = next(i for i in listed["items"] if i["id"] == out["id"])
    assert row["reading"]["value"] == 96

    # A different unit measures a different thing: the old percentage is not carried over.
    count = MetricSpec(kind="logged", name="Open findings", unit="count", direction="at_most",
                       target_value=0, frequency="quarterly")
    changed = await service.set_metric(db, org.id, out["id"], count, _actor(admin), "admin", TODAY)
    assert changed["reading"]["value"] is None and changed["reading"]["status"] in (
        "first_measurement_due", "proposed")

    milestone = await _create(db, org, admin, name="Certify", metric=MetricSpec(kind="milestone", name="Cert"))
    await service.log_result(db, org.id, milestone["id"], 1, _actor(admin), TODAY)
    renamed = await service.set_metric(db, org.id, milestone["id"], MetricSpec(kind="milestone", name="ISO cert"),
                                       _actor(admin), "admin", TODAY)
    assert renamed["reading"]["value_text"] == "Done"


async def test_a_milestone_entry_cannot_be_dated_ahead(db):
    org = await _org(db)
    user = await _user(db, org, "editor")
    out = await _create(db, org, user, metric=MetricSpec(kind="milestone", name="Cert"))
    with pytest.raises(ObjectiveError) as err:
        await service.log_result(db, org.id, out["id"], 0, _actor(user), TODAY,
                                 period_start=TODAY + timedelta(days=400))
    assert err.value.status_code == 422


def test_numbers_that_are_not_numbers_are_refused():
    from pydantic import ValidationError

    from services.isms_metrics.schemas import ResultCreate
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValidationError):
            ResultCreate(value=bad)
        with pytest.raises(ValidationError):
            MetricSpec(kind="live", measure_key="controls_implemented", target_value=bad)
        with pytest.raises(ValidationError):
            ConfirmBody(warn_value=bad)


async def test_one_failing_measure_does_not_blank_the_register_or_the_night(db, monkeypatch):
    """A calculator that raises, in Python or in SQL, costs its own reading and nothing else."""
    from sqlalchemy import text

    org = await _org(db)
    admin = await _user(db, org)
    team = await _team(db, org)
    real = service.run_measure

    async def flaky(session, org_id, key, params, today):
        if key == "risk_within_appetite":
            raise ZeroDivisionError("a data shape nobody expected")
        if key == "controls_implemented":
            await session.execute(text("SELECT 1/0"))  # aborts the transaction unless contained
        return await real(session, org_id, key, params, today)

    monkeypatch.setattr(service, "run_measure", flaky)
    listed = await service.list_objectives(db, org.id, TODAY)
    by_source = {i["source"]: i for i in listed["items"]}
    assert len(listed["items"]) == 12
    for broken in ("starter:risk_appetite", "starter:controls_implemented"):
        reading = by_source[broken]["reading"]
        assert reading["value"] is None and reading["measured_on"] is None
        assert reading["population"] == service.COULD_NOT_MEASURE
    assert by_source["starter:evidence_on_time"]["reading"]["measured_on"] == TODAY

    # Confirmed, it reads Insufficient data: never a value, never on track.
    confirmed = await service.confirm_objective(db, org.id, by_source["starter:risk_appetite"]["id"], ConfirmBody(
        owning_team_id=team.id), _actor(admin), "admin", TODAY)
    assert confirmed["reading"]["status"] == "insufficient_data"

    measures = {m["key"]: m for m in await service.measures_today(db, org.id, None, TODAY)}
    assert len(measures) == len(library.MEASURES)
    assert measures["risk_within_appetite"]["today"]["value"] is None
    assert measures["risk_within_appetite"]["today"]["population"] == service.COULD_NOT_MEASURE
    assert measures["evidence_collected_on_time"]["today"]["population"] != service.COULD_NOT_MEASURE

    # The night records the six that worked and leaves a gap for the two that did not.
    assert await service.record_nightly_results(db, org.id, TODAY) == 6


async def test_documents_print_the_entry_the_register_reads(db):
    """A back-filled correction to an old period does not replace the current figure."""
    org = await _org(db)
    admin = await _user(db, org)
    team = await _team(db, org)
    out = await _create(db, org, admin, metric=LOGGED)
    await service.confirm_objective(db, org.id, out["id"], ConfirmBody(owning_team_id=team.id),
                                    _actor(admin), "admin", TODAY)
    await service.log_result(db, org.id, out["id"], 96, _actor(admin), TODAY)
    await service.log_result(db, org.id, out["id"], 60, _actor(admin), TODAY, period_start=date(2026, 7, 1))
    register = (await service.list_objectives(db, org.id, TODAY, seed=False))["items"][0]["reading"]
    assert register["value"] == 96 and register["status"] == "on_track"
    printed = await db.run_sync(lambda s: service.document_results_sync(s, org.id, TODAY))
    assert [(r["value_text"], r["status"]) for r in printed] == [("96%", "On track")]


# ---------------------------------------------------------------------------
# Nightly recording, wording and the legacy shape
# ---------------------------------------------------------------------------


async def test_nightly_recording_twice_leaves_one_row_per_live_metric(db):
    org = await _org(db)
    first = await service.record_nightly_results(db, org.id, TODAY)
    second = await service.record_nightly_results(db, org.id, TODAY)
    assert (first, second) == (8, 0)
    count = await db.scalar(select(func.count()).select_from(IsmsMetricResult).where(
        IsmsMetricResult.organization_id == org.id, IsmsMetricResult.source == "computed",
        IsmsMetricResult.measured_on == TODAY))
    assert count == 8
    tomorrow = await service.record_nightly_results(db, org.id, TODAY + timedelta(days=1))
    assert tomorrow == 8


async def test_measures_today_orders_by_word_match(db):
    org = await _org(db)
    out = await service.measures_today(db, org.id, "supplier assurance", TODAY)
    assert len(out) == 15 and out[0]["key"] == "supplier_assurance_current" and out[0]["match"] >= 2
    assert all("value_text" in m["today"] for m in out)
    plain = await service.measures_today(db, org.id, None, TODAY)
    assert [m["match"] for m in plain] == [0] * 15 and plain[0]["key"] == "risk_within_appetite"
    # Outcome measures first, then supporting, each in library order (or best match first).
    assert [m["group"] for m in plain] == ["outcome"] * 8 + ["supporting"] * 7
    assert [m["key"] for m in plain] == [m.key for m in library.MEASURES]
    risky = await service.measures_today(db, org.id, "risks assessed", TODAY)
    assert [m["group"] for m in risky] == ["outcome"] * 8 + ["supporting"] * 7
    assert risky[8]["key"] == "risks_assessed"


async def test_legacy_shape_excludes_proposed_and_archived_and_has_no_live_value(db):
    org = await _org(db)
    admin = await _user(db, org)
    team = await _team(db, org)
    await service.ensure_starters(db, org.id, TODAY)
    assert await service.legacy_objectives(db, org.id) == []  # seeding leaves the legacy shape unchanged
    kept = await _create(db, org, admin, owning_team_id=team.id, metric=LOGGED, due_date=date(2027, 3, 1))
    await service.confirm_objective(db, org.id, kept["id"], ConfirmBody(), _actor(admin), "admin", TODAY)
    gone = await _create(db, org, admin, owning_team_id=team.id, metric=LOGGED)
    await service.confirm_objective(db, org.id, gone["id"], ConfirmBody(), _actor(admin), "admin", TODAY)
    await service.update_objective(db, org.id, gone["id"], {"archived": True}, _actor(admin), TODAY)
    await service.log_result(db, org.id, kept["id"], 42.37, _actor(admin), TODAY)

    rows = await service.legacy_objectives(db, org.id)
    assert [r["ref"] for r in rows] == [kept["ref"]]
    row = rows[0]
    assert set(row) == {"id", "ref", "objective", "measure", "target", "owner", "resources", "due_date",
                        "evaluation", "status", "planned_actions", "type", "review_date", "clauses"}
    assert row["owner"] == team.name and row["status"] == "in_progress" and row["due_date"] == "2027-03-01"
    assert row["measure"] == "Training completion (%)" and row["target"] == "at least 95%"
    assert not any("42.37" in str(v) or "42%" in str(v) for v in row.values())


async def test_legacy_shape_for_unset_metric_uses_old_text(db):
    org = await _org(db)
    now = service._now()
    objective = SecurityObjective(organization_id=org.id, ref="OBJ-01", name="Old one", lifecycle_status="active",
                                  source="migrated", owner_text="Somebody", legacy_row_id="abcdef123456",
                                  created_at=now, updated_at=now)
    db.add(objective)
    await db.flush()
    db.add(ObjectiveMetric(organization_id=org.id, objective_id=objective.id, kind="unset",
                           description="Old measure", target_text="Old target"))
    await db.flush()
    rows = await service.legacy_objectives(db, org.id)
    assert rows[0]["id"] == "abcdef123456" and rows[0]["measure"] == "Old measure"
    assert rows[0]["target"] == "Old target" and rows[0]["owner"] == "Somebody"
    reading = (await service.get_objective(db, org.id, objective.id, TODAY))["reading"]
    assert reading["status"] == "not_measured"


async def test_wording_for_live_request(db):
    from services.isms_metrics.schemas import WordingRequest
    out = service.wording_for(WordingRequest(kind="live", measure_key="risk_within_appetite", target_value=100,
                                             warn_value=90))
    assert out["metric_name"] == "Risks within appetite (%)"
    assert out["target_label"] == "at least 100% · warning below 90%"
    with pytest.raises(ObjectiveError):
        service.wording_for(WordingRequest(kind="live", measure_key="nope"))


# ---------------------------------------------------------------------------
# Closed objectives: achieved or missed is the final state
# ---------------------------------------------------------------------------


async def _confirmed_logged(db, org, admin, value=None):
    team = await _team(db, org, f"Team {_uuid.uuid4().hex[:6]}")
    out = await _create(db, org, admin, metric=LOGGED)
    await service.confirm_objective(db, org.id, out["id"], ConfirmBody(owning_team_id=team.id),
                                    _actor(admin), "admin", TODAY)
    if value is not None:
        await service.log_result(db, org.id, out["id"], value, _actor(admin), TODAY)
    return out


async def test_a_closed_objective_reads_its_final_state_and_keeps_its_value(db):
    """Missed at 96% against 95% must not read On track; achieved at 60% must not read Off track."""
    org = await _org(db)
    admin = await _user(db, org)
    good = await _confirmed_logged(db, org, admin, 96)
    poor = await _confirmed_logged(db, org, admin, 60)
    bare = await _confirmed_logged(db, org, admin)
    await service.update_objective(db, org.id, good["id"], {"lifecycle_status": "missed"}, _actor(admin), TODAY)
    await service.update_objective(db, org.id, poor["id"], {"lifecycle_status": "achieved"}, _actor(admin), TODAY)
    await service.update_objective(db, org.id, bare["id"], {"lifecycle_status": "achieved"}, _actor(admin), TODAY)

    listed = await service.list_objectives(db, org.id, TODAY, seed=False)
    readings = {o["id"]: o["reading"] for o in listed["items"]}
    assert (readings[good["id"]]["status"], readings[good["id"]]["value_text"]) == ("missed", "96%")
    assert (readings[poor["id"]]["status"], readings[poor["id"]]["value_text"]) == ("achieved", "60%")
    assert readings[bare["id"]]["status"] == "achieved" and readings[bare["id"]]["value"] is None

    summary = listed["summary"]
    assert (summary["objectives"], summary["achieved"], summary["missed"]) == (3, 2, 1)
    for judged in ("on_track", "at_risk", "off_track", "in_progress", "needs_attention", "overdue",
                   "not_measured", "insufficient_data", "proposed"):
        assert summary[judged] == 0, judged

    # Set back to active, each is judged again on the value it kept.
    await service.update_objective(db, org.id, good["id"], {"lifecycle_status": "active"}, _actor(admin), TODAY)
    reopened = (await service.get_objective(db, org.id, good["id"], TODAY))["reading"]
    assert reopened["status"] == "on_track"


async def test_a_closed_objective_takes_no_new_value_and_stores_no_final_state(db):
    org = await _org(db)
    admin = await _user(db, org)
    out = await _confirmed_logged(db, org, admin, 96)
    await service.update_objective(db, org.id, out["id"], {"lifecycle_status": "missed"}, _actor(admin), TODAY)
    with pytest.raises(ObjectiveError) as err:
        await service.log_result(db, org.id, out["id"], 99, _actor(admin), TODAY)
    assert err.value.status_code == 422 and "closed" in err.value.message
    stored = (await db.execute(select(IsmsMetricResult.status).where(
        IsmsMetricResult.organization_id == org.id))).scalars().all()
    assert stored == ["on_track"]


async def test_the_nightly_task_skips_a_closed_objective(db):
    org = await _org(db)
    admin = await _user(db, org)
    team = await _team(db, org)
    live = MetricSpec(kind="live", measure_key="controls_implemented", target_value=80, warn_value=60)
    out = await _create(db, org, admin, metric=live)
    await service.confirm_objective(db, org.id, out["id"], ConfirmBody(owning_team_id=team.id),
                                    _actor(admin), "admin", TODAY)
    await service.update_objective(db, org.id, out["id"], {"lifecycle_status": "achieved"}, _actor(admin), TODAY)
    before = await service.record_nightly_results(db, org.id, TODAY)
    assert before == 8, "the eight starters, not the closed objective"
    rows = (await db.execute(select(IsmsMetricResult.status).join(
        ObjectiveMetric, ObjectiveMetric.id == IsmsMetricResult.objective_metric_id,
    ).where(ObjectiveMetric.objective_id == out["id"]))).scalars().all()
    assert rows == []
    statuses = set((await db.execute(select(IsmsMetricResult.status).where(
        IsmsMetricResult.organization_id == org.id))).scalars().all())
    assert not statuses & {"achieved", "missed"}

    await service.update_objective(db, org.id, out["id"], {"lifecycle_status": "active"}, _actor(admin), TODAY)
    assert await service.record_nightly_results(db, org.id, TODAY) == 1


async def test_documents_print_the_final_state_of_a_closed_objective(db):
    org = await _org(db)
    admin = await _user(db, org)
    measured = await _confirmed_logged(db, org, admin, 96)
    unmeasured = await _confirmed_logged(db, org, admin)
    await service.update_objective(db, org.id, measured["id"], {"lifecycle_status": "missed"}, _actor(admin), TODAY)
    await service.update_objective(db, org.id, unmeasured["id"], {"lifecycle_status": "achieved"},
                                   _actor(admin), TODAY)
    printed = await db.run_sync(lambda s: service.document_results_sync(s, org.id, TODAY))
    assert [(r["value_text"], r["status"]) for r in printed] == [
        ("96%", "Missed"), ("Not yet measured", "Achieved"),
    ]


async def test_a_reading_prints_a_decimal_place_when_a_whole_number_would_cross_the_target(db):
    org = await _org(db)
    admin = await _user(db, org)
    out = await _confirmed_logged(db, org, admin, 94.5)
    reading = (await service.get_objective(db, org.id, out["id"], TODAY))["reading"]
    assert (reading["status"], reading["value_text"]) == ("at_risk", "94.5%")
    printed = await db.run_sync(lambda s: service.document_results_sync(s, org.id, TODAY))
    assert [(r["value_text"], r["status"]) for r in printed] == [("94.5%", "At risk")]
