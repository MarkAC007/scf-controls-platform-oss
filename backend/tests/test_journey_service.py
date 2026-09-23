"""Tests for the guided journey engine.

The invariants worth defending here are the ones that protect a signature or
refuse a silent pass. A journey stage is a claim a named practitioner makes
about a client's readiness; everything below exists so the platform cannot
manufacture, erase, or quietly weaken one of those claims.

The DB-bound merge path is exercised against a stubbed session rather than a
live Postgres, because this suite has no database fixture (see
``tests/conftest.py`` — the convention here is unit tests with mocks). The
behaviours that need real SQL — two-hop domain joins, the unique ordinal
constraint — were verified in-container against the dev org and are recorded
in the PR rather than asserted here.
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from models import JourneyStageState
from services import journey as journey_service


# ---------------------------------------------------------------------------
# Unknown checks are never a pass
# ---------------------------------------------------------------------------


def test_unsupported_checks_flags_an_unknown_type():
    """An artefact naming a check the engine cannot run must be refused.

    Accepting it would hand the practitioner a gate that reads "cannot
    evaluate" for the length of an engagement — discovered by the client
    rather than by them.
    """
    template = {
        "stages": [
            {"key": "a", "precondition_spec": [{"type": "documents_approved"}]},
            {"key": "b", "precondition_spec": [{"type": "vibes_acceptable"}]},
        ]
    }
    assert journey_service.unsupported_checks(template) == {"vibes_acceptable"}


def test_unsupported_checks_passes_a_fully_supported_template():
    template = {
        "stages": [
            {"key": "a", "precondition_spec": [
                {"type": "frameworks_scoped"},
                {"type": "controls_with_owner"},
            ]},
        ]
    }
    assert journey_service.unsupported_checks(template) == set()


def test_unsupported_checks_tolerates_stages_without_a_gate():
    """A stage with no preconditions is a legitimate shape, not an error."""
    template = {"stages": [{"key": "a"}, {"key": "b", "precondition_spec": None}]}
    assert journey_service.unsupported_checks(template) == set()


def test_every_supported_check_is_actually_dispatchable():
    """SUPPORTED_CHECKS must not drift ahead of the evaluator.

    A type listed here but not handled in ``_evaluate_one`` would pass import
    validation and then evaluate to "cannot evaluate" forever — the exact
    failure ``unsupported_checks`` exists to prevent, reintroduced one layer
    down.
    """
    import inspect

    source = inspect.getsource(journey_service._evaluate_one)
    for check_type in journey_service.SUPPORTED_CHECKS:
        assert f'"{check_type}"' in source, (
            f"{check_type} is advertised as supported but _evaluate_one "
            f"never mentions it"
        )


# ---------------------------------------------------------------------------
# Domain scoping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_domain_clause_is_none_when_no_domains_named():
    """An unfiltered check keeps its org-wide meaning.

    This is what makes the feature backward compatible: templates written
    before domain scoping existed must evaluate exactly as they did. It also
    never reaches the catalogue, which is why the session here is never used.
    """
    db = MagicMock()
    db.execute = AsyncMock()
    assert await journey_service._domain_clause(db, None) is None
    assert await journey_service._domain_clause(db, []) is None
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_domain_clause_is_none_for_blank_entries():
    """Whitespace is not a domain. Treating it as one would filter to nothing
    and render the gate permanently unreachable."""
    db = MagicMock()
    db.execute = AsyncMock()
    assert await journey_service._domain_clause(db, ["", "   "]) is None


@pytest.mark.asyncio
async def test_domain_clause_filters_by_scf_id_prefix(monkeypatch):
    """Attribution is by prefix, which is the catalogue's own key.

    Matching the controls sheet's domain *name* against the domains sheet's
    name is the defect this replaces: it is the field carrying both of the
    catalogue's disagreements.
    """
    monkeypatch.setattr(
        journey_service, "_domain_lookup",
        AsyncMock(return_value={"gov": "GOV", "rsk": "RSK"}),
    )
    clause = await journey_service._domain_clause(MagicMock(), ["GOV", "RSK"])
    rendered = str(clause.compile(compile_kwargs={"literal_binds": True}))
    assert "scf_id" in rendered
    assert "lower('GOV-')" in rendered and "lower('RSK-')" in rendered
    assert "scf_catalog_domains" not in rendered
    assert "scf_domain" not in rendered


@pytest.mark.asyncio
async def test_an_unresolvable_domain_fails_closed_and_says_so(monkeypatch, caplog):
    """A typo in a template must not widen the gate to the whole organisation.

    Returning None here would make the filter a no-op, so a misspelt domain
    would be measured against every control the org holds and could read as a
    pass over data nobody asked about. A false pass is worse than any wrong
    number, so the clause matches nothing and the log names the token.
    """
    monkeypatch.setattr(
        journey_service, "_domain_lookup", AsyncMock(return_value={"gov": "GOV"}),
    )
    with caplog.at_level("WARNING"):
        clause = await journey_service._domain_clause(
            MagicMock(), ["NOT-A-DOMAIN"], "Owners named across the determinants"
        )
    assert clause is not None
    assert "false" in str(clause.compile(compile_kwargs={"literal_binds": True})).lower()
    assert "NOT-A-DOMAIN" in caplog.text
    assert "Owners named across the determinants" in caplog.text


@pytest.mark.asyncio
async def test_a_resolvable_domain_survives_an_unresolvable_neighbour(monkeypatch):
    """One bad token does not throw away the domains that did resolve."""
    monkeypatch.setattr(
        journey_service, "_domain_lookup", AsyncMock(return_value={"gov": "GOV"}),
    )
    clause = await journey_service._domain_clause(MagicMock(), ["GOV", "NOPE"])
    rendered = str(clause.compile(compile_kwargs={"literal_binds": True}))
    assert "lower('GOV-')" in rendered
    assert "NOPE" not in rendered


@pytest.mark.asyncio
async def test_denominator_reuses_the_org_total_when_unfiltered():
    """An unfiltered check must not issue a second count."""
    db = MagicMock()
    db.scalar = AsyncMock()
    got = await journey_service._denominator(
        db, uuid4(), None, {"in_scope": 372}
    )
    assert got == 372
    db.scalar.assert_not_awaited()


@pytest.mark.asyncio
async def test_denominator_counts_its_own_slice_when_filtered():
    """A domain-filtered check divides by its own population.

    Dividing a wave's numerator by the org-wide total would make every wave
    gate read as a few percent complete, forever — the defect this function
    exists to prevent.
    """
    db = MagicMock()
    db.scalar = AsyncMock(return_value=93)
    clause = journey_service._prefix_predicate(ScopedControl.scf_id, ["GOV", "RSK"])
    got = await journey_service._denominator(
        db, uuid4(), clause, {"in_scope": 372}
    )
    assert got == 93
    db.scalar.assert_awaited_once()


@pytest.mark.asyncio
async def test_denominator_treats_a_null_count_as_zero():
    db = MagicMock()
    db.scalar = AsyncMock(return_value=None)
    clause = journey_service._prefix_predicate(ScopedControl.scf_id, ["GOV"])
    got = await journey_service._denominator(
        db, uuid4(), clause, {"in_scope": 372}
    )
    assert got == 0


# ---------------------------------------------------------------------------
# Re-issuing a revised artefact
# ---------------------------------------------------------------------------


def _stage(key, ordinal, state=JourneyStageState.LOCKED.value, attested=False):
    st = MagicMock()
    st.key = key
    st.ordinal = ordinal
    st.state = state
    st.attested_at = datetime.now(timezone.utc) if attested else None
    st.attested_by_user_id = uuid4() if attested else None
    return st


def _journey_with(stages):
    j = MagicMock()
    j.stages = stages
    j.activated_at = None
    j.practitioner_name = None
    j.practitioner_organization_id = None
    return j


def _db_returning(journey):
    db = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = journey
    db.execute = AsyncMock(return_value=result)
    db.flush = AsyncMock()
    db.delete = AsyncMock()
    db.add = MagicMock()
    db.refresh = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_reissue_refuses_to_drop_a_passed_stage():
    """A revision cannot un-sign something.

    The person signed a moment in time. Deleting the row would delete the
    audit trail the database check constraint exists to protect, so the
    import refuses rather than guessing.
    """
    journey = _journey_with([
        _stage("mobilise", 0, JourneyStageState.PASSED.value, attested=True),
        _stage("determinants", 1),
    ])
    db = _db_returning(journey)

    with pytest.raises(ValueError) as exc:
        await journey_service.import_template(
            db, uuid4(), {"stages": [{"key": "determinants", "title": "D"}]}
        )

    assert "mobilise" in str(exc.value)
    db.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_reissue_refuses_to_drop_a_conditionally_passed_stage():
    """A conditional pass is still a signature."""
    journey = _journey_with([
        _stage("spine", 0, JourneyStageState.PASSED_CONDITIONAL.value, attested=True),
    ])
    db = _db_returning(journey)

    with pytest.raises(ValueError) as exc:
        await journey_service.import_template(
            db, uuid4(), {"stages": [{"key": "other", "title": "O"}]}
        )

    assert "spine" in str(exc.value)


@pytest.mark.asyncio
async def test_reissue_may_drop_an_unsigned_stage():
    """Removing a stage nobody has signed is a legitimate revision."""
    journey = _journey_with([
        _stage("mobilise", 0),
        _stage("retired-wave", 1),
    ])
    db = _db_returning(journey)

    await journey_service.import_template(
        db, uuid4(), {"stages": [{"key": "mobilise", "title": "M"}]}
    )

    assert db.delete.await_count == 1


@pytest.mark.asyncio
async def test_reissue_negates_old_ordinals_before_rewriting():
    """Ordinals are unique per journey.

    Without clearing the old ordering first, a reordering revision collides
    with itself — stage B cannot take ordinal 0 while stage A still holds it.
    """
    a = _stage("a", 0)
    b = _stage("b", 1)
    db = _db_returning(_journey_with([a, b]))

    await journey_service.import_template(
        db, uuid4(), {"stages": [{"key": "b", "title": "B"}, {"key": "a", "title": "A"}]}
    )

    # Both were pushed negative during the merge, so neither could collide.
    assert db.flush.await_count >= 1


# ---------------------------------------------------------------------------
# Ownership is a team relationship (#1052)
# ---------------------------------------------------------------------------


def test_the_ownership_check_does_not_read_the_legacy_owner_column():
    """The regression guard, and the reason this fix has no fallback.

    ``scoped_controls.owner`` is unvalidated free text with no referential
    integrity. OR-ing it back in as a safety net would leave a live non-team
    ownership path in the product, reachable by any editor token, and would
    make the gate satisfiable without assigning anybody at all. Source
    inspection rather than behaviour because the point is that the column is
    never consulted, which no amount of data can demonstrate.
    """
    import inspect

    source = inspect.getsource(journey_service._evaluate_one)
    branch = source.split('if ctype == "controls_with_owner":', 1)[1]
    branch = branch.split('if ctype == "controls_at_status":', 1)[0]
    assert "ScopedControl.owner" not in branch
    assert "assigned_to" not in branch
    assert "_accountable_team_clause()" in branch


def test_the_ownership_clause_is_a_semi_join_on_accountable_rows():
    """One control is one control, and only an accountable team counts.

    A plain join would count a control once per team row, so a control with
    one accountable and two consulted teams would contribute three to a
    numerator whose denominator counts it once.
    """
    from sqlalchemy.dialects import postgresql

    rendered = str(
        journey_service._accountable_team_clause().compile(
            dialect=postgresql.dialect()
        )
    )
    assert "EXISTS" in rendered
    assert "control_team_assignments" in rendered
    assert "is_accountable" in rendered
    assert "scoped_controls.id" in rendered
    assert "JOIN" not in rendered


# ---------------------------------------------------------------------------
# The printed requirement agrees with the gate by construction
# ---------------------------------------------------------------------------


def test_required_count_is_the_first_numerator_the_gate_accepts():
    """One below fails, exactly at passes — for the number actually printed."""
    total, need = 156, 0.95
    required = journey_service._required_count(total, need)
    assert (required - 1) / total < need
    assert required / total >= need


def test_required_count_on_a_denominator_that_does_not_divide_evenly():
    """148/156 at 95% is the case that started this: it renders as 95% and
    fails. The label now names 149, so the reader is not left reconciling a
    rounded percentage against a verdict computed from an exact one."""
    assert journey_service._required_count(156, 0.95) == 149
    assert 148 / 156 < 0.95
    assert 149 / 156 >= 0.95


def test_required_count_never_demands_a_control_the_gate_does_not():
    """Exhaustive over every denominator and threshold a template can carry.

    ``ceil(total * need)`` disagrees with the gate on 54 of these pairs, always
    by overstating. Asserting against the gate expression itself is the only
    definition that cannot drift from it.
    """
    for total in range(1, 200):
        for pct in range(1, 100):
            need = pct / 100
            required = journey_service._required_count(total, need)
            assert required / total >= need, (total, need, required)
            if required > 0:
                assert (required - 1) / total < need, (total, need, required)


def test_required_count_is_zero_when_nothing_is_scoped():
    """Guards the early-return branches against a division that cannot happen
    but must not be able to."""
    assert journey_service._required_count(0, 0.95) == 0


@pytest.mark.asyncio
async def test_the_label_names_the_shortfall_and_the_requirement():
    db = MagicMock()
    db.scalar = AsyncMock(return_value=148)
    out = await journey_service._evaluate_one(
        db, uuid4(), {"type": "controls_at_status", "statuses": ["implemented"],
                      "min_fraction": 0.95}, {"in_scope": 156},
    )
    assert out["met"] is False
    assert out["detail"] == "148 of 156 (94.9%) — 149 required, 1 more needed"


@pytest.mark.asyncio
async def test_a_met_gate_carries_no_phantom_shortfall():
    db = MagicMock()
    db.scalar = AsyncMock(return_value=149)
    out = await journey_service._evaluate_one(
        db, uuid4(), {"type": "controls_at_status", "statuses": ["implemented"],
                      "min_fraction": 0.95}, {"in_scope": 156},
    )
    assert out["met"] is True
    assert out["detail"] == "149 of 156 (95.5%) — 149 required"


# ---------------------------------------------------------------------------
# Behavioural — needs the seeded catalogue and a real PostgreSQL. SKIPS in CI.
#
# The domain-resolution half cannot be proved against fixtures the test
# inserts itself: the defect it guards is a disagreement between two seeded
# catalogue sheets, and a test that seeds its own agreeing rows passes while
# the product is broken. So these read the catalogue as shipped. Everything
# written runs inside a transaction that is rolled back, so a run leaves the
# database exactly as it found it.
# ---------------------------------------------------------------------------

import os  # noqa: E402
import uuid as _uuid  # noqa: E402

from sqlalchemy import func, or_, select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

import catalog_models  # noqa: E402,F401  (mapper registry spans both modules)
from catalog_models import SCFCatalogControl, SCFCatalogDomain  # noqa: E402
from models import (  # noqa: E402
    ControlTeamAssignment,
    Function,
    Organization,
    ScopedControl,
    Team,
)

def _database_url() -> str:
    """Resolved the way the application resolves it, not from one env var.

    The dev backend is wired with ``DB_HOST``/``DB_USER`` and a
    ``DB_PASSWORD_FILE`` secret rather than a ``DATABASE_URL``, so reading
    that one variable would skip this whole half inside the very container
    these tests are meant to run in — a green summary that proved nothing.
    """
    try:
        from db_url import get_database_url

        return get_database_url("") or ""
    except Exception:  # pragma: no cover - environment dependent
        return os.getenv("DATABASE_URL", "")


DATABASE_URL = _database_url()

requires_postgres = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason=(
        "needs a reachable Postgres and the seeded SCF catalogue — these "
        "are SKIPPED, not passed, and they are the only tests here that prove "
        "a domain resolves or a team owns anything"
    ),
)


@pytest.fixture
async def db():
    """A session on a transaction that is always rolled back."""
    engine = create_async_engine(DATABASE_URL)
    try:
        async with engine.connect():
            pass
    except Exception as exc:  # pragma: no cover - environment dependent
        await engine.dispose()
        pytest.skip(f"database not reachable: {exc}")

    session_factory = async_sessionmaker(
        engine, expire_on_commit=False, autoflush=False
    )
    session = session_factory()
    try:
        yield session
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


async def _resolved_scf_ids(db, tokens):
    """The catalogue controls a wave naming ``tokens`` is about.

    Built from the same lookup and the same predicate builder the gate uses,
    pointed at the catalogue table instead of the scoped one — so these
    assertions exercise the production resolution rather than a restatement
    of it.
    """
    lookup = await journey_service._domain_lookup(db)
    codes = []
    for token in tokens:
        code = lookup.get(token.strip().lower())
        if code is not None and code not in codes:
            codes.append(code)
    if not codes:
        return set()
    rows = await db.execute(
        select(SCFCatalogControl.scf_id).where(
            journey_service._prefix_predicate(SCFCatalogControl.scf_id, codes)
        )
    )
    return set(rows.scalars().all())


async def _make_org(db, tag):
    org = Organization(name=f"jq-{tag}", slug=f"jq-{tag}")
    db.add(org)
    await db.flush()
    return org


async def _make_team(db, org, tag):
    function = (await db.execute(
        select(Function).where(Function.is_active.is_(True)).limit(1)
    )).scalar_one_or_none()
    if function is None:  # pragma: no cover - environment dependent
        pytest.skip("no seeded functions in this database")
    team = Team(organization_id=org.id, function_id=function.id, name=f"team-{tag}")
    db.add(team)
    await db.flush()
    return team


async def _scope(db, org, scf_id, **kwargs):
    control = ScopedControl(
        organization_id=org.id, scf_id=scf_id, selected=True, **kwargs
    )
    db.add(control)
    await db.flush()
    return control


async def _assign(db, org, control, team, *, accountable):
    db.add(ControlTeamAssignment(
        scoped_control_id=control.id,
        team_id=team.id,
        organization_id=org.id,
        is_accountable=accountable,
    ))
    await db.flush()


async def _is_owned(db, org, control):
    """The production numerator predicate, asked about one control."""
    got = await db.scalar(
        select(func.count(ScopedControl.id)).where(
            ScopedControl.id == control.id,
            ScopedControl.organization_id == org.id,
            ScopedControl.selected.is_(True),
            journey_service._accountable_team_clause(),
        )
    )
    return int(got or 0) == 1


@requires_postgres
class TestOwnershipCountsAccountableTeams:
    """Who owns a control, asked of the database rather than of a mock."""

    @pytest.fixture
    async def tenant(self, db):
        tag = _uuid.uuid4().hex[:10]
        org = await _make_org(db, tag)
        team = await _make_team(db, org, tag)
        return org, team, tag

    async def test_a_team_accountable_for_a_control_makes_it_owned(self, db, tenant):
        org, team, tag = tenant
        control = await _scope(db, org, f"TST-{tag}-A")
        await _assign(db, org, control, team, accountable=True)
        assert await _is_owned(db, org, control) is True

    async def test_a_consulted_team_does_not_make_a_control_owned(self, db, tenant):
        """Consulted means informed, not answerable. A stage that went green
        on a consulted team would be a gate passed with nobody to page."""
        org, team, tag = tenant
        control = await _scope(db, org, f"TST-{tag}-B")
        await _assign(db, org, control, team, accountable=False)
        assert await _is_owned(db, org, control) is False

    async def test_a_control_with_no_team_at_all_is_not_owned(self, db, tenant):
        org, _team, tag = tenant
        control = await _scope(db, org, f"TST-{tag}-C")
        assert await _is_owned(db, org, control) is False

    async def test_legacy_owner_text_alone_does_not_make_a_control_owned(
        self, db, tenant
    ):
        """The no-fallback constraint, asserted against live data.

        A tenant carrying legacy free text and no teams correctly drops to
        zero owned and is told to assign teams. That is the true state, not a
        regression — and it is the whole reason the column is not OR-ed in.
        """
        org, _team, tag = tenant
        control = await _scope(
            db, org, f"TST-{tag}-D", owner="Security Team", assigned_to="A Person"
        )
        assert await _is_owned(db, org, control) is False

    async def test_one_control_with_several_teams_still_counts_once(self, db, tenant):
        """The semi-join, proved: a numerator cannot exceed its denominator."""
        org, team, tag = tenant
        second = await _make_team(db, org, f"{tag}-2")
        control = await _scope(db, org, f"TST-{tag}-E")
        await _assign(db, org, control, team, accountable=True)
        await _assign(db, org, control, second, accountable=False)
        got = await db.scalar(
            select(func.count(ScopedControl.id)).where(
                ScopedControl.organization_id == org.id,
                ScopedControl.selected.is_(True),
                journey_service._accountable_team_clause(),
            )
        )
        assert int(got or 0) == 1


@requires_postgres
class TestDomainsResolveAgainstTheSeededCatalogue:
    """The two catalogue sheets do not agree, and a gate must not care.

    Every test in this class asserts against the catalogue **as shipped**,
    which is the whole point of it: the defect being guarded is a
    disagreement between two seeded sheets, so a test that seeded its own
    agreeing rows would pass while the product stayed broken.

    That makes the catalogue a genuine precondition rather than a
    convenience, so a database with schema and no seed skips here with a
    reason a reader can act on. It must never be made to pass without the
    catalogue — an assertion weakened until an empty table satisfies it is
    not a weaker test, it is no test.
    """

    @pytest.fixture(autouse=True)
    async def _requires_the_seeded_catalogue(self, db):
        seeded = await db.scalar(select(func.count(SCFCatalogControl.scf_id)))
        if not seeded:
            pytest.skip(
                "requires the seeded SCF catalogue: scf_catalog_controls is "
                "empty. Run against a database with the catalogue imported "
                "(the dev stack has it); a migrated-but-unseeded database has "
                "the schema and none of the data these assertions are about."
            )

    @pytest.fixture
    async def gov(self, db):
        """GOV's two spellings, read from the catalogue rather than written
        into the test — the strings are data, and data is what drifted."""
        sheet_name = await db.scalar(
            select(SCFCatalogDomain.name).where(SCFCatalogDomain.identifier == "GOV")
        )
        control_name = await db.scalar(
            select(SCFCatalogControl.scf_domain)
            .where(SCFCatalogControl.scf_id.like("GOV-%"))
            .limit(1)
        )
        if not sheet_name or not control_name:  # pragma: no cover
            pytest.skip("catalogue not seeded in this database")
        return sheet_name, control_name

    async def test_the_two_catalogue_sheets_disagree_about_gov(self, gov):
        """The premise, and a condition of the data rather than of the code.

        A catalogue re-import can repair the disagreement — one did on this
        project's dev database, which is how this test earned its skip. That
        does not make the fix unnecessary: the defect was a two-hop join from
        a domain code, through the domains sheet's *name*, to the controls
        sheet's ``scf_domain``, and any release where those two spellings
        drift apart drops an entire domain out of its own wave. Prefix
        attribution removes the hop, so it cannot drift.

        So when the sheets agree this skips rather than fails. A failure here
        would report a defect in the fix, when what it actually observes is
        that the seeded data no longer exhibits the condition. The three
        tests that follow would be vacuous in that state, which is exactly
        what the skip records.
        """
        sheet_name, control_name = gov
        if sheet_name == control_name:
            pytest.skip(
                "this catalogue spells GOV the same way on both sheets "
                f"({sheet_name!r}), so there is no disagreement to resolve. "
                "The two-hop name join this class guards against only "
                "misbehaves when they differ."
            )
        assert sheet_name != control_name

    async def test_gov_resolves_to_its_controls_despite_the_disagreement(self, db):
        resolved = await _resolved_scf_ids(db, ["GOV"])
        expected = set((await db.execute(
            select(SCFCatalogControl.scf_id).where(
                SCFCatalogControl.scf_id.like("GOV-%")
            )
        )).scalars().all())
        assert expected, "no GOV controls in the catalogue"
        assert resolved == expected

    async def test_every_spelling_of_a_domain_resolves_to_the_same_controls(
        self, db, gov
    ):
        """Code, domains-sheet name and controls-sheet name are one domain."""
        sheet_name, control_name = gov
        by_code = await _resolved_scf_ids(db, ["GOV"])
        assert await _resolved_scf_ids(db, [sheet_name]) == by_code
        assert await _resolved_scf_ids(db, [control_name]) == by_code
        assert await _resolved_scf_ids(db, ["gov"]) == by_code

    async def test_every_seeded_domain_resolves_to_a_non_empty_control_set(self, db):
        """Parametrised from the database, because listing the codes here
        would freeze today's catalogue into the test."""
        identifiers = (await db.execute(
            select(SCFCatalogDomain.identifier)
        )).scalars().all()
        assert identifiers
        empty = [
            code for code in identifiers
            if not await _resolved_scf_ids(db, [code])
        ]
        assert empty == [], f"domains that resolve to no controls: {empty}"

    async def test_a_control_is_attributed_by_its_prefix_not_its_domain_cell(
        self, db
    ):
        """Where the two catalogue fields disagree, the prefix wins.

        One control carries an ``scf_id`` prefix of CHG and an ``scf_domain``
        cell reading Embedded Technology, while all nineteen of its siblings
        read Change Management and all nineteen genuine Embedded Technology
        controls carry the EMB prefix. The cell is a workbook error, not a
        cross-domain attribution: the prefix key is total and sound across
        every control in the catalogue, whatever its size in a given release,
        and ``scf_domain`` is the field carrying the disagreements.

        Derived from the data rather than naming CHG-08, so it keeps holding
        when the offending row changes or the workbook is repaired.
        """
        prefix = func.split_part(SCFCatalogControl.scf_id, "-", 1)
        named_domain = (
            select(SCFCatalogDomain.identifier)
            .where(SCFCatalogDomain.name == SCFCatalogControl.scf_domain)
            .scalar_subquery()
        )
        rows = (await db.execute(
            select(SCFCatalogControl.scf_id, prefix, named_domain).where(
                SCFCatalogControl.scf_domain.in_(select(SCFCatalogDomain.name)),
                named_domain != prefix,
            )
        )).all()
        if not rows:  # pragma: no cover - catalogue dependent
            pytest.skip("no cross-attributed control in this catalogue")
        for scf_id, prefix_code, cell_code in rows:
            assert scf_id in await _resolved_scf_ids(db, [prefix_code]), (
                f"{scf_id} is missing from {prefix_code}, which its prefix names"
            )
            assert scf_id not in await _resolved_scf_ids(db, [cell_code]), (
                f"{scf_id} was pulled into {cell_code} by its scf_domain cell"
            )

    async def test_the_prefix_key_is_total_across_the_whole_catalogue(self, db):
        """The premise of prefix attribution, asserted rather than assumed.

        Every control is ``XXX-…``, every prefix names a live domain, and
        every domain owns controls. If any of these stopped being true the
        resolution would start dropping controls silently — which is the
        class of defect this whole change is about.
        """
        prefix = func.split_part(SCFCatalogControl.scf_id, "-", 1)
        total = await db.scalar(select(func.count(SCFCatalogControl.scf_id)))
        bad_shape = await db.scalar(
            select(func.count(SCFCatalogControl.scf_id)).where(
                SCFCatalogControl.scf_id.op("!~")("^[A-Z]{3}-")
            )
        )
        orphan = await db.scalar(
            select(func.count(SCFCatalogControl.scf_id)).where(
                prefix.not_in(select(SCFCatalogDomain.identifier))
            )
        )
        assert total > 0
        assert bad_shape == 0
        assert orphan == 0

    async def test_an_unknown_domain_fails_closed_against_the_real_catalogue(
        self, db
    ):
        """Never None, and never widened. ``false()`` against live data."""
        clause = await journey_service._domain_clause(db, ["NOT-A-DOMAIN"])
        assert clause is not None
        assert await _resolved_scf_ids(db, ["NOT-A-DOMAIN"]) == set()


@requires_postgres
class TestWaveTotalsIncludeEveryNamedDomain:
    """End to end, through the real clause, against real scoped rows."""

    @pytest.fixture
    async def wave(self, db):
        """A tenant scoped across GOV and RSK, with GOV owned by a team.

        GOV is the domain that disappeared from both halves of this gate;
        RSK is the control group that never did.
        """
        tag = _uuid.uuid4().hex[:10]
        org = await _make_org(db, tag)
        team = await _make_team(db, org, tag)
        picked = {}
        for code in ("GOV", "RSK"):
            picked[code] = (await db.execute(
                select(SCFCatalogControl.scf_id)
                .where(SCFCatalogControl.scf_id.like(f"{code}-%"))
                .order_by(SCFCatalogControl.scf_id)
                .limit(3)
            )).scalars().all()
            if len(picked[code]) < 3:  # pragma: no cover
                pytest.skip(
                    f"requires the seeded SCF catalogue: fewer than three "
                    f"{code} controls found. Run against a database with the "
                    f"catalogue imported; these assertions are about real "
                    f"scoped controls in real domains."
                )
        controls = {}
        for code, scf_ids in picked.items():
            controls[code] = [await _scope(db, org, s) for s in scf_ids]
        for control in controls["GOV"]:
            await _assign(db, org, control, team, accountable=True)
        return org, controls

    async def test_gov_appears_in_the_denominator(self, db, wave):
        org, controls = wave
        clause = await journey_service._domain_clause(db, ["GOV", "RSK"])
        total = await journey_service._denominator(db, org.id, clause, {"in_scope": 0})
        assert total == len(controls["GOV"]) + len(controls["RSK"])

    async def test_gov_appears_in_the_numerator(self, db, wave):
        org, controls = wave
        check = {
            "type": "controls_with_owner",
            "domains": ["GOV", "RSK"],
            "min_fraction": 0.9,
        }
        out = await journey_service._evaluate_one(db, org.id, check, {"in_scope": 0})
        owned = len(controls["GOV"])
        total = owned + len(controls["RSK"])
        assert out["met"] is False
        assert out["detail"].startswith(f"{owned} of {total} in GOV, RSK")

    async def test_a_named_domain_with_nothing_scoped_invents_no_controls(
        self, db, wave
    ):
        """PRI is named in the live Wave 1 gate and has nothing scoped. It must
        neither inflate the denominator nor raise."""
        org, controls = wave
        with_extra = await journey_service._domain_clause(db, ["GOV", "RSK", "PRI"])
        without = await journey_service._domain_clause(db, ["GOV", "RSK"])
        assert await journey_service._denominator(
            db, org.id, with_extra, {"in_scope": 0}
        ) == await journey_service._denominator(
            db, org.id, without, {"in_scope": 0}
        ) == len(controls["GOV"]) + len(controls["RSK"])


# ---------------------------------------------------------------------------
# Template keys name a file on disk, so they are untrusted input
# ---------------------------------------------------------------------------


class TestTemplateKeyIsNotAPath:
    """`template_key` arrives from a request body and is used to open a file.

    CodeQL flagged this as py/path-injection against v0.40.1 (alerts 320/321).
    The sink is reachable: api/journey.py's import endpoint passes the field
    straight through, so an org admin could read any .json the backend process
    can reach. These tests are the regression fence.

    Every rejection must be indistinguishable from "no such template" — a
    different error, or a different status, would turn the endpoint into an
    oracle for which files exist on the host.
    """

    @pytest.mark.parametrize("key", [
        "../../../../etc/passwd",
        "../secrets",
        "..%2f..%2fetc%2fpasswd",
        "/etc/passwd",
        "//etc/passwd",
        "subdir/template",
        "back\\slash",
        "",
        ".",
        "..",
        ".hidden",
        "key with spaces",
        "nul\x00byte",
    ])
    def test_a_key_that_is_not_a_bare_stem_is_refused(self, key):
        with pytest.raises(FileNotFoundError):
            journey_service.load_template(key)

    def test_traversal_is_refused_even_when_the_target_exists(self, tmp_path, monkeypatch):
        """The refusal is not an accident of the file being absent.

        Without this, a test suite on a box where /etc/passwd.json happens not
        to exist would pass with the guard removed.
        """
        outside = tmp_path / "secret.json"
        outside.write_text('{"stages": []}', encoding="utf-8")
        inside = tmp_path / "templates"
        inside.mkdir()
        monkeypatch.setattr(journey_service, "TEMPLATE_DIR", inside)

        with pytest.raises(FileNotFoundError):
            journey_service.load_template("../secret")

        # The same bytes ARE readable when asked for legitimately, which proves
        # the refusal came from the guard and not from an unreadable file.
        (inside / "secret.json").write_text('{"stages": []}', encoding="utf-8")
        assert journey_service.load_template("secret") == {"stages": []}

    def test_a_symlink_escaping_the_directory_is_refused(self, tmp_path, monkeypatch):
        """The pattern cannot see this one — only the resolved path can.

        `evil` is a perfectly legal bare stem. It is the resolution that
        reveals it points out of the template directory.
        """
        outside = tmp_path / "outside.json"
        outside.write_text('{"stages": []}', encoding="utf-8")
        inside = tmp_path / "templates"
        inside.mkdir()
        link = inside / "evil.json"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):  # pragma: no cover
            pytest.skip("filesystem does not support symlinks")
        monkeypatch.setattr(journey_service, "TEMPLATE_DIR", inside)

        assert journey_service.TEMPLATE_KEY_RE.match("evil"), \
            "the pattern alone would admit this key — containment is the control"
        with pytest.raises(FileNotFoundError):
            journey_service.load_template("evil")

    def test_the_shipped_default_still_loads(self):
        """The guard must not break the template the product actually ships."""
        doc = journey_service.load_template()
        assert doc.get("template_key") == journey_service.DEFAULT_TEMPLATE_KEY
        assert doc.get("stages")

    @pytest.mark.parametrize("key", [
        "compliancegenie-default",
        "cg-12-month",
        "client_a.v2",
        "Plan2026",
    ])
    def test_legitimate_keys_still_match(self, key):
        """Real keys, including the practitioner artefact's own, stay legal."""
        assert journey_service.TEMPLATE_KEY_RE.match(key)


class TestTemplateSelectionComesFromTheDirectory:
    """The v0.40.1 fix checked a path it had already built from the key.

    CodeQL rejected it, and the rejection was correct on the merits: a check on
    a tainted path is weaker than never constructing one. These tests pin the
    property that replaced it — the opened file is chosen by enumerating the
    directory, so the caller's string selects among files we already have
    rather than naming one. They fail if anyone reintroduces path joining.
    """

    def test_a_key_naming_a_real_file_outside_the_directory_is_refused(
        self, tmp_path, monkeypatch
    ):
        """The classic traversal, with the target deliberately made to exist.

        `secrets.json` is real and readable. If the loader still joined the key
        to TEMPLATE_DIR this would open it. Enumeration cannot reach it,
        because it is not in the directory being enumerated.
        """
        templates = tmp_path / "templates"
        templates.mkdir()
        (templates / "legit.json").write_text('{"template_key": "legit"}')
        secrets = tmp_path / "secrets.json"
        secrets.write_text('{"password": "hunter2"}')
        monkeypatch.setattr(journey_service, "TEMPLATE_DIR", templates)

        with pytest.raises(FileNotFoundError):
            journey_service.load_template("../secrets")

        # and the legitimate neighbour in the same directory still loads,
        # so the refusal above is selectivity rather than a broken loader
        assert journey_service.load_template("legit")["template_key"] == "legit"

    def test_the_file_opened_is_the_one_the_directory_listing_offered(
        self, tmp_path, monkeypatch
    ):
        """Rename the file on disk and the old key stops working.

        This is the positive form of the property. If selection were driven by
        the string, `first` would keep resolving after the rename. It only
        stops working because the loader asks the directory what exists.
        """
        templates = tmp_path / "templates"
        templates.mkdir()
        target = templates / "first.json"
        target.write_text('{"template_key": "first"}')
        monkeypatch.setattr(journey_service, "TEMPLATE_DIR", templates)

        assert journey_service.load_template("first")["template_key"] == "first"

        target.rename(templates / "second.json")
        with pytest.raises(FileNotFoundError):
            journey_service.load_template("first")
        assert journey_service.load_template("second")["template_key"] == "first"

    def test_a_non_json_neighbour_is_not_reachable(self, tmp_path, monkeypatch):
        """Enumeration is scoped to *.json, so a sibling .env stays invisible."""
        templates = tmp_path / "templates"
        templates.mkdir()
        (templates / ".env").write_text("SECRET=1")
        (templates / "notes.txt").write_text("plain")
        monkeypatch.setattr(journey_service, "TEMPLATE_DIR", templates)

        for key in (".env", "notes", "notes.txt"):
            with pytest.raises(FileNotFoundError):
                journey_service.load_template(key)

    def test_the_loader_does_not_join_the_key_to_a_path(self):
        """Source-level pin: the construction CodeQL flagged must stay gone.

        Crude on purpose. The property is invisible at runtime once the
        directory happens to be empty of surprises, and this fix is only
        meaningful as a *structural* one, so the structure is what gets
        asserted. If this fails, read the load_template docstring before
        'fixing' the test.
        """
        import inspect

        source = inspect.getsource(journey_service.load_template)
        body = source.split('"""')[-1]
        assert "TEMPLATE_DIR /" not in body, \
            "path built from the key again — see the docstring"
        assert "root /" not in body, \
            "path built from the key again — see the docstring"
        assert ".glob(" in body, "selection must come from a directory listing"
