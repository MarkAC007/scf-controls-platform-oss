"""Control and evidence team assignments are policed by the DATABASE (#822 phase 3).

Six of the phase-3 acceptance criteria are claims about PostgreSQL, not about
Python:

* at most one **accountable** team per control, and per evidence item;
* a second *non*-accountable team is fine — the model is one accountable and
  N consulted, so the index has to be partial rather than a blanket unique;
* a team from another organisation cannot be assigned to this org's item;
* a spoofed ``organization_id`` naming a different tenant from the item is
  refused, which is the half of the pair a single composite key would miss;
* ``ON DELETE CASCADE`` really cascades, from the team side and the item side;
* zero assignments is legal, and so is an item with assignments but nobody
  accountable — that is a UI warning, never a constraint.

A mocked session proves none of that; it only proves a fake agreed with
itself. So this file is in two halves, following ``test_teams_rbac.py`` and
``test_team_schema_constraints.py`` from phase 1:

* structural assertions on the migration text, which run everywhere including
  a CI with no database, and catch a constraint quietly deleted from the file;
* behavioural round-trips against a real PostgreSQL, which are the only tests
  here that prove anything about what Postgres will actually reject.
  **The behavioural half SKIPS when no Postgres ``DATABASE_URL`` is reachable,
  which includes CI.** A green CI run is not evidence they passed.

Every behavioural test runs inside a transaction that is rolled back and never
committed, so nothing is left in the database and a test that deliberately
provokes an ``IntegrityError`` cannot poison its neighbour.

Run it against the dev stack with::

    docker compose exec -T backend python -m pytest \\
        tests/test_control_evidence_team_assignment_constraints.py -v

``DATABASE_URL`` is already correct inside that container. Check the summary
line: a run that says "skipped" for the behavioural classes has proved nothing
about the constraints, whatever its exit code.
"""
from __future__ import annotations

import os
import pathlib
import re
import sys
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The SQLAlchemy registry spans both modules: models.System has a relationship
# to SystemCatalogTemplate, which lives here. Import it or mapper configuration
# fails on the first query with an unresolvable class name.
import catalog_models  # noqa: E402,F401
from models import (  # noqa: E402
    Assignment,
    Base,
    ControlTeamAssignment,
    EvidenceTeamAssignment,
    EvidenceTracking,
    Function,
    Organization,
    OrganizationMember,
    ScopedControl,
    Team,
    User,
)

MIGRATION_FILE = (
    pathlib.Path(__file__).resolve().parents[1]
    / "alembic" / "versions" / "20260824_130000_control_evidence_team_assignments.py"
)

ALEMBIC_VERSIONS = MIGRATION_FILE.parent

DATABASE_URL = os.getenv("DATABASE_URL", "")

#: Applied to the behavioural classes only. Putting it at module scope would
#: skip the structural half too, and the structural half is the part that is
#: meant to run in a database-free CI.
requires_postgres = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason=(
        "needs a Postgres DATABASE_URL — these are SKIPPED, not passed, and "
        "they are the only tests here that prove the constraints bite"
    ),
)


# ---------------------------------------------------------------------------
# Structural — runs everywhere, including a CI with no database
# ---------------------------------------------------------------------------

class TestMigrationShape:
    """The constraint set, asserted against the migration source.

    Named literally rather than derived, so deleting one from the migration
    fails here instead of quietly redefining what phase 3 promised.
    """

    @pytest.fixture(scope="class")
    def source(self):
        return MIGRATION_FILE.read_text()

    def test_revision_chains_from_the_phase_one_migration(self, source):
        assert "revision: str = 'ctrlteamassign1'" in source
        assert "down_revision: Union[str, None] = 'teamsfunctions1'" in source

    @pytest.mark.parametrize(
        "constraint",
        [
            # The "what this row is about" edge, plus both halves of the
            # tenant check, on each of the two tables.
            "fk_control_team_assignments_control",
            "fk_control_team_assignments_control_org",
            "fk_control_team_assignments_team_org",
            "fk_evidence_team_assignments_evidence",
            "fk_evidence_team_assignments_evidence_org",
            "fk_evidence_team_assignments_team_org",
        ],
    )
    def test_every_isolation_key_is_named_and_present(self, source, constraint):
        # The names are load-bearing: the behavioural tests below assert on
        # them, and they are what an operator sees in a rejection.
        assert f"name='{constraint}'" in source

    def test_the_composite_keys_target_the_composite_uniques(self, source):
        assert "'scoped_controls.organization_id', 'scoped_controls.id'" in source
        assert "'evidence_tracking.organization_id', 'evidence_tracking.id'" in source
        # Phase 1 added the teams side; both tables reuse it.
        assert source.count("'teams.organization_id', 'teams.id'") == 2

    def test_the_foreign_key_targets_are_created_before_they_are_used(self, source):
        # A composite foreign key needs a composite unique on the far side, and
        # neither table had one. Missing either of these makes the migration
        # fail on deploy, not on review.
        for constraint, table in (
            ("uq_scoped_controls_org_id", "scoped_controls"),
            ("uq_evidence_tracking_org_id", "evidence_tracking"),
        ):
            assert f"'{constraint}', '{table}', ['organization_id', 'id']" in source

    @pytest.mark.parametrize(
        "index,table,column",
        [
            ("uq_control_accountable_team", "control_team_assignments",
             "scoped_control_id"),
            ("uq_evidence_accountable_team", "evidence_team_assignments",
             "evidence_tracking_id"),
        ],
    )
    def test_the_accountable_indexes_are_partial_and_unique(
        self, source, index, table, column
    ):
        # unique=True without the partial WHERE would mean one assignment per
        # item in total, so no consulted teams at all; the WHERE without
        # unique=True would mean nothing whatsoever.
        assert f"'{index}', '{table}',\n        ['{column}'], unique=True" in source
        assert "postgresql_where=sa.text('is_accountable')" in source

    def test_the_accountable_indexes_key_on_the_item_not_the_organisation(self, source):
        # Keyed on organization_id it would allow one accountable team per
        # tenant across every control — a constraint nobody asked for that
        # would make the second assignment in any org fail.
        assert "'uq_control_accountable_team'" in source
        assert re.search(
            r"'uq_control_accountable_team'.*?\['scoped_control_id'\]", source, re.S
        )
        assert re.search(
            r"'uq_evidence_accountable_team'.*?\['evidence_tracking_id'\]", source, re.S
        )

    @pytest.mark.parametrize(
        "constraint",
        [
            "fk_control_team_assignments_control",
            "fk_control_team_assignments_control_org",
            "fk_control_team_assignments_team_org",
            "fk_evidence_team_assignments_evidence",
            "fk_evidence_team_assignments_evidence_org",
            "fk_evidence_team_assignments_team_org",
        ],
    )
    def test_deleting_a_team_or_an_item_takes_its_assignments_with_it(
        self, source, constraint
    ):
        # RESTRICT here would make deleting a team impossible once it had been
        # assigned anything; SET NULL is not available on a NOT NULL column.
        assert f"name='{constraint}', ondelete='CASCADE'" in source

    def test_one_team_may_be_assigned_to_an_item_only_once(self, source):
        assert "name='uq_control_team_assignments_control_team'" in source
        assert "name='uq_evidence_team_assignments_evidence_team'" in source

    def test_downgrade_removes_every_object_it_created(self, source):
        for stmt in (
            "op.drop_index('ix_evidence_team_assignments_team_id'",
            "op.drop_index('uq_evidence_accountable_team'",
            "op.drop_table('evidence_team_assignments')",
            "op.drop_index('ix_control_team_assignments_team_id'",
            "op.drop_index('uq_control_accountable_team'",
            "op.drop_table('control_team_assignments')",
            "op.drop_constraint('uq_evidence_tracking_org_id'",
            "op.drop_constraint('uq_scoped_controls_org_id'",
        ):
            assert stmt in source

    def test_downgrade_drops_the_foreign_key_targets_last(self, source):
        # The two unique constraints are pointed at by the tables above them,
        # so dropping either first fails. Ordering is the whole test.
        downgrade = source.split("def downgrade()")[1]
        assert downgrade.index("drop_table('control_team_assignments')") < \
            downgrade.index("uq_scoped_controls_org_id")
        assert downgrade.index("drop_table('evidence_team_assignments')") < \
            downgrade.index("uq_evidence_tracking_org_id")

    def test_the_migration_creates_no_assignment_rows(self, source):
        # Phase 3 is schema only. Deriving assignments from the free-text
        # scoped_controls.owner / evidence_tracking.owner columns would write
        # unvalidated junk into every tenant at once, with no way back.
        assert "bulk_insert" not in source
        assert "op.execute" not in source


class TestPhaseThreeIsAdditive:
    """Nothing that already worked was changed.

    Phase 3 adds two tables and two foreign-key targets. Asserted here rather
    than left to code review, because an "improvement" to the existing
    per-user assignment path is exactly the kind of change that arrives with a
    migration like this one.
    """

    def test_the_polymorphic_assignments_table_is_untouched(self):
        columns = {c.name for c in Assignment.__table__.columns}
        assert columns == {
            "id", "assignable_type", "assignable_id", "user_id", "role",
            "assigned_at", "assigned_by_user_id",
        }

    def test_assignable_id_still_has_no_foreign_key_behind_it(self):
        # Not an endorsement — it is why the phase-3 tables are not
        # polymorphic. If this ever gains a key, the argument in the
        # migration's docstring needs rewriting, and this test is the prompt.
        column = Assignment.__table__.c.assignable_id
        assert list(column.foreign_keys) == []

    def test_the_per_user_control_assignment_columns_survive(self):
        columns = {c.name for c in ScopedControl.__table__.columns}
        assert {"assigned_user_id", "owner_user_id"} <= columns

    def test_the_new_tables_added_no_column_to_the_tables_they_point_at(self):
        # The team lives in its own row, not in a column on the control. A
        # scoped_controls.team_id would be a second, unconstrained source of
        # truth for the same fact.
        for table in (ScopedControl.__table__, EvidenceTracking.__table__):
            assert not [c.name for c in table.columns if "team" in c.name]

    def test_the_only_change_to_those_tables_is_a_foreign_key_target(self):
        for table, name in (
            (ScopedControl.__table__, "uq_scoped_controls_org_id"),
            (EvidenceTracking.__table__, "uq_evidence_tracking_org_id"),
        ):
            assert name in {c.name for c in table.constraints}


class TestPhaseFiveTablesDoNotExistYet:
    """Risk and vendor team assignment is phase 5, and is absent on purpose.

    Asserted explicitly rather than by omission. A table shipped early would
    reach production without the API, the UI or the tests that phase 5 owes
    it, and nobody reviewing phase 3 would be looking for it.
    """

    @pytest.mark.parametrize(
        "table", ["risk_team_assignments", "vendor_team_assignments"]
    )
    def test_no_model_declares_it(self, table):
        assert table not in Base.metadata.tables

    @pytest.mark.parametrize(
        "table", ["risk_team_assignments", "vendor_team_assignments"]
    )
    def test_no_migration_creates_it(self, table):
        creators = [
            path.name
            for path in ALEMBIC_VERSIONS.glob("*.py")
            if f"'{table}'" in path.read_text()
        ]
        assert creators == [], f"{table} is phase 5; created by {creators}"


# ---------------------------------------------------------------------------
# Behavioural — needs PostgreSQL. SKIPS in CI.
# ---------------------------------------------------------------------------

@pytest.fixture
async def db():
    """A session on a transaction that is always rolled back.

    Nothing here is ever committed, so the dev database is unchanged by a run
    and a test that provokes an IntegrityError cannot poison a neighbour.
    """
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


class _Tenant:
    """One organisation with a team, a scoped control and an evidence record."""

    def __init__(self, org, team, other_team, control, other_control, evidence):
        self.org = org
        self.team = team
        self.other_team = other_team
        self.control = control
        self.other_control = other_control
        self.evidence = evidence


class _Scenario:
    def __init__(self, a, b, actor):
        self.a = a          # the tenant under test
        self.b = b          # the neighbour whose data must stay out of reach
        self.actor = actor  # a user to attribute assignments to


async def _make_tenant(db, function, tag):
    org = Organization(name=f"ta-{tag}", slug=f"ta-{tag}")
    db.add(org)
    await db.flush()

    teams = []
    for suffix in ("primary", "second"):
        team = Team(
            organization_id=org.id,
            function_id=function.id,
            name=f"team-{suffix}-{tag}",
        )
        db.add(team)
        teams.append(team)

    controls = []
    for suffix in ("A", "B"):
        control = ScopedControl(organization_id=org.id, scf_id=f"TST-{tag}-{suffix}")
        db.add(control)
        controls.append(control)

    evidence = EvidenceTracking(organization_id=org.id, evidence_id=f"E-{tag}")
    db.add(evidence)
    await db.flush()

    return _Tenant(org, teams[0], teams[1], controls[0], controls[1], evidence)


@pytest.fixture
async def scenario(db):
    """Two tenants, each fully furnished, plus a user to attribute writes to.

    Two teams and two controls *per tenant* on purpose: proving the accountable
    index is per item rather than per organisation needs a second control in
    the same org, and proving it is partial rather than a blanket unique needs
    a second team on the same control.
    """
    function = (await db.execute(
        select(Function).where(Function.is_active.is_(True)).limit(1)
    )).scalar_one_or_none()
    if function is None:  # pragma: no cover - environment dependent
        pytest.skip("no seeded functions in this database")

    tag = uuid.uuid4().hex[:10]
    tenant_a = await _make_tenant(db, function, f"a{tag}")
    tenant_b = await _make_tenant(db, function, f"b{tag}")

    actor = User(email=f"ta-{tag}@example.invalid", google_sub=f"sub-{tag}")
    db.add(actor)
    await db.flush()
    db.add(OrganizationMember(
        organization_id=tenant_a.org.id, user_id=actor.id, role="admin",
    ))
    await db.flush()

    return _Scenario(tenant_a, tenant_b, actor)


def _control_row(scenario, *, team=None, control=None, org=None, accountable=False):
    tenant = scenario.a
    return ControlTeamAssignment(
        scoped_control_id=(control or tenant.control).id,
        team_id=(team or tenant.team).id,
        organization_id=(org or tenant.org).id,
        is_accountable=accountable,
        assigned_by_user_id=scenario.actor.id,
    )


def _evidence_row(scenario, *, team=None, evidence=None, org=None, accountable=False):
    tenant = scenario.a
    return EvidenceTeamAssignment(
        evidence_tracking_id=(evidence or tenant.evidence).id,
        team_id=(team or tenant.team).id,
        organization_id=(org or tenant.org).id,
        is_accountable=accountable,
        assigned_by_user_id=scenario.actor.id,
    )


async def _count(db, model, **filters):
    stmt = select(func.count()).select_from(model)
    for column, value in filters.items():
        stmt = stmt.where(getattr(model, column) == value)
    return (await db.execute(stmt)).scalar_one()


@requires_postgres
class TestAtMostOneAccountableTeamPerControl:
    async def test_a_second_accountable_team_is_refused(self, db, scenario):
        db.add(_control_row(scenario, accountable=True))
        await db.flush()

        # A DIFFERENT team on purpose: the same team twice would trip
        # uq_control_team_assignments_control_team and say nothing at all
        # about the accountable index.
        db.add(_control_row(scenario, team=scenario.a.other_team, accountable=True))
        with pytest.raises(IntegrityError) as caught:
            await db.flush()

        assert "uq_control_accountable_team" in str(caught.value)

    async def test_a_second_non_accountable_team_is_allowed(self, db, scenario):
        """One accountable, N consulted — the index must be partial.

        This is the test that distinguishes a partial index from a blanket
        unique on scoped_control_id. Without it, the test above would pass
        just as happily against a schema that allowed one team per control
        full stop, which is not the model phase 3 describes.
        """
        db.add(_control_row(scenario, accountable=True))
        db.add(_control_row(scenario, team=scenario.a.other_team, accountable=False))
        await db.flush()

        assert await _count(
            db, ControlTeamAssignment, scoped_control_id=scenario.a.control.id
        ) == 2

    async def test_two_controls_may_each_have_their_own_accountable_team(
        self, db, scenario
    ):
        """Keyed on the control, not on the organisation.

        An index over organization_id would allow one accountable team per
        tenant in total and break the second control anybody assigned.
        """
        db.add(_control_row(scenario, accountable=True))
        db.add(_control_row(
            scenario, control=scenario.a.other_control,
            team=scenario.a.other_team, accountable=True,
        ))
        await db.flush()

        assert await _count(
            db, ControlTeamAssignment, organization_id=scenario.a.org.id,
            is_accountable=True,
        ) == 2

    async def test_the_same_team_cannot_be_assigned_to_one_control_twice(
        self, db, scenario
    ):
        db.add(_control_row(scenario))
        await db.flush()

        db.add(_control_row(scenario))
        with pytest.raises(IntegrityError) as caught:
            await db.flush()

        assert "uq_control_team_assignments_control_team" in str(caught.value)


@requires_postgres
class TestAtMostOneAccountableTeamPerEvidenceItem:
    async def test_a_second_accountable_team_is_refused(self, db, scenario):
        db.add(_evidence_row(scenario, accountable=True))
        await db.flush()

        db.add(_evidence_row(scenario, team=scenario.a.other_team, accountable=True))
        with pytest.raises(IntegrityError) as caught:
            await db.flush()

        assert "uq_evidence_accountable_team" in str(caught.value)

    async def test_a_second_non_accountable_team_is_allowed(self, db, scenario):
        db.add(_evidence_row(scenario, accountable=True))
        db.add(_evidence_row(scenario, team=scenario.a.other_team, accountable=False))
        await db.flush()

        assert await _count(
            db, EvidenceTeamAssignment,
            evidence_tracking_id=scenario.a.evidence.id,
        ) == 2

    async def test_the_same_team_cannot_be_assigned_to_one_item_twice(
        self, db, scenario
    ):
        db.add(_evidence_row(scenario))
        await db.flush()

        db.add(_evidence_row(scenario))
        with pytest.raises(IntegrityError) as caught:
            await db.flush()

        assert "uq_evidence_team_assignments_evidence_team" in str(caught.value)


@requires_postgres
class TestHandingAccountabilityOverIsOneTransaction:
    """``uq_control_accountable_team`` is a NON-DEFERRABLE partial unique index.

    Postgres therefore checks it at the end of every statement, not at commit.
    That single fact is what any "make this team accountable" endpoint has to
    be built around: the incumbent must be cleared and flushed *before* the
    challenger is promoted, both inside one transaction, so that either both
    halves land or neither does and the item is never left with two
    accountable teams — nor, on a failure, with none.

    The API-level version of this criterion lives in
    ``test_team_assignments_api.py`` and skips until that branch merges. What
    is proved here is the mechanism underneath it, which exists now.
    """

    async def test_clearing_then_promoting_leaves_exactly_one_accountable(
        self, db, scenario
    ):
        incumbent = _control_row(scenario, accountable=True)
        challenger = _control_row(scenario, team=scenario.a.other_team)
        db.add_all([incumbent, challenger])
        await db.flush()

        incumbent.is_accountable = False
        await db.flush()
        challenger.is_accountable = True
        await db.flush()

        assert await _count(
            db, ControlTeamAssignment, scoped_control_id=scenario.a.control.id,
            is_accountable=True,
        ) == 1
        # And the loser is still assigned — demotion is not removal. A team
        # that was accountable yesterday stays consulted today unless somebody
        # says otherwise.
        assert await _count(
            db, ControlTeamAssignment, scoped_control_id=scenario.a.control.id
        ) == 2

    async def test_promoting_without_clearing_first_fails_at_the_statement(
        self, db, scenario
    ):
        """The negative control that gives the test above its teeth.

        If this passed, the index would be enforcing nothing and the ordering
        in the endpoint would be decoration. It fails at the statement rather
        than at commit, which is exactly why the clear has to be flushed
        before the promote is issued.
        """
        db.add(_control_row(scenario, accountable=True))
        challenger = _control_row(scenario, team=scenario.a.other_team)
        db.add(challenger)
        await db.flush()

        challenger.is_accountable = True
        with pytest.raises(IntegrityError) as caught:
            await db.flush()

        assert "uq_control_accountable_team" in str(caught.value)

    async def test_the_same_swap_works_for_evidence(self, db, scenario):
        incumbent = _evidence_row(scenario, accountable=True)
        challenger = _evidence_row(scenario, team=scenario.a.other_team)
        db.add_all([incumbent, challenger])
        await db.flush()

        incumbent.is_accountable = False
        await db.flush()
        challenger.is_accountable = True
        await db.flush()

        assert await _count(
            db, EvidenceTeamAssignment,
            evidence_tracking_id=scenario.a.evidence.id, is_accountable=True,
        ) == 1


@requires_postgres
class TestCrossTenantIsolation:
    """Both composite foreign keys, and the attack each one exists for."""

    async def test_a_legitimate_assignment_is_accepted(self, db, scenario):
        # The refusals below mean nothing unless the same statement shape
        # succeeds when the organisation, the item and the team all agree.
        db.add(_control_row(scenario, accountable=True))
        db.add(_evidence_row(scenario, accountable=True))
        await db.flush()

        assert await _count(
            db, ControlTeamAssignment, organization_id=scenario.a.org.id
        ) == 1
        assert await _count(
            db, EvidenceTeamAssignment, organization_id=scenario.a.org.id
        ) == 1

    async def test_another_organisations_team_cannot_be_put_on_this_control(
        self, db, scenario
    ):
        # organization_id and scoped_control_id are honestly the caller's own,
        # so the control half of the pair is satisfied. Only the team half
        # notices that team_id belongs to somebody else.
        db.add(_control_row(scenario, team=scenario.b.team))
        with pytest.raises(IntegrityError) as caught:
            await db.flush()

        assert "fk_control_team_assignments_team_org" in str(caught.value)

    async def test_a_spoofed_organization_id_cannot_reach_another_tenants_control(
        self, db, scenario
    ):
        """The attack the *second* composite key exists for.

        organization_id names tenant B and team_id is genuinely one of tenant
        B's teams, so the team half of the pair is perfectly happy. Only
        ``fk_control_team_assignments_control_org`` notices that the control
        being assigned belongs to tenant A. One composite key here would be a
        half-open door: a caller could publish their own team onto a victim's
        compliance record.
        """
        db.add(_control_row(
            scenario, team=scenario.b.team, org=scenario.b.org,
            control=scenario.a.control,
        ))
        with pytest.raises(IntegrityError) as caught:
            await db.flush()

        assert "fk_control_team_assignments_control_org" in str(caught.value)

    async def test_another_organisations_team_cannot_be_put_on_this_evidence(
        self, db, scenario
    ):
        db.add(_evidence_row(scenario, team=scenario.b.team))
        with pytest.raises(IntegrityError) as caught:
            await db.flush()

        assert "fk_evidence_team_assignments_team_org" in str(caught.value)

    async def test_a_spoofed_organization_id_cannot_reach_another_tenants_evidence(
        self, db, scenario
    ):
        db.add(_evidence_row(
            scenario, team=scenario.b.team, org=scenario.b.org,
            evidence=scenario.a.evidence,
        ))
        with pytest.raises(IntegrityError) as caught:
            await db.flush()

        assert "fk_evidence_team_assignments_evidence_org" in str(caught.value)

    async def test_an_assignment_cannot_point_at_a_control_that_does_not_exist(
        self, db, scenario
    ):
        # The reason these tables are not polymorphic. The existing
        # `assignments` table would accept this row without comment.
        db.add(ControlTeamAssignment(
            scoped_control_id=uuid.uuid4(),
            team_id=scenario.a.team.id,
            organization_id=scenario.a.org.id,
        ))
        with pytest.raises(IntegrityError) as caught:
            await db.flush()

        assert "fk_control_team_assignments_control" in str(caught.value)


@requires_postgres
class TestDeletesCascade:
    """Deleting a team, or the item, takes the assignment rows with it.

    Core DELETEs rather than ``session.delete``, so Postgres does the
    cascading and the test observes the database's behaviour rather than
    SQLAlchemy's. ``expunge_all`` afterwards because the session still holds
    rows the database has just removed underneath it.
    """

    async def test_deleting_a_team_removes_its_control_assignments(
        self, db, scenario
    ):
        db.add(_control_row(scenario, accountable=True))
        await db.flush()
        assert await _count(
            db, ControlTeamAssignment, team_id=scenario.a.team.id
        ) == 1

        await db.execute(sa.delete(Team).where(Team.id == scenario.a.team.id))
        db.expunge_all()

        assert await _count(db, ControlTeamAssignment,
                            scoped_control_id=scenario.a.control.id) == 0

    async def test_deleting_a_team_removes_its_evidence_assignments(
        self, db, scenario
    ):
        db.add(_evidence_row(scenario, accountable=True))
        await db.flush()

        await db.execute(sa.delete(Team).where(Team.id == scenario.a.team.id))
        db.expunge_all()

        assert await _count(db, EvidenceTeamAssignment,
                            evidence_tracking_id=scenario.a.evidence.id) == 0

    async def test_deleting_the_control_removes_its_assignments(self, db, scenario):
        db.add(_control_row(scenario, accountable=True))
        db.add(_control_row(scenario, team=scenario.a.other_team))
        await db.flush()

        await db.execute(
            sa.delete(ScopedControl).where(ScopedControl.id == scenario.a.control.id)
        )
        db.expunge_all()

        assert await _count(db, ControlTeamAssignment,
                            scoped_control_id=scenario.a.control.id) == 0
        # The team itself is untouched: a control going away does not delete
        # the people who owned it.
        assert await _count(db, Team, id=scenario.a.team.id) == 1

    async def test_deleting_the_evidence_record_removes_its_assignments(
        self, db, scenario
    ):
        db.add(_evidence_row(scenario, accountable=True))
        await db.flush()

        await db.execute(
            sa.delete(EvidenceTracking)
            .where(EvidenceTracking.id == scenario.a.evidence.id)
        )
        db.expunge_all()

        assert await _count(db, EvidenceTeamAssignment,
                            evidence_tracking_id=scenario.a.evidence.id) == 0
        assert await _count(db, Team, id=scenario.a.team.id) == 1


@requires_postgres
class TestUnassignedAndUnaccountableAreBothLegal:
    """"At most one", never "exactly one".

    Every control and every evidence item starts with no assignments at all,
    and a schema that made either of these states impossible would refuse the
    state the whole estate is in on the day phase 3 deploys.
    """

    async def test_an_item_with_no_assignments_is_legal(self, db, scenario):
        assert await _count(db, ControlTeamAssignment,
                            scoped_control_id=scenario.a.control.id) == 0
        assert await _count(db, EvidenceTeamAssignment,
                            evidence_tracking_id=scenario.a.evidence.id) == 0

    async def test_assignments_with_nobody_accountable_are_legal(self, db, scenario):
        """Two consulted teams and no owner.

        The UI warns about this; the database does not refuse it. Making it a
        constraint would mean every assignment had to arrive with an
        accountability decision already made, which is not how anyone works.
        """
        db.add(_control_row(scenario, accountable=False))
        db.add(_control_row(scenario, team=scenario.a.other_team, accountable=False))
        db.add(_evidence_row(scenario, accountable=False))
        db.add(_evidence_row(scenario, team=scenario.a.other_team, accountable=False))
        await db.flush()

        assert await _count(
            db, ControlTeamAssignment, scoped_control_id=scenario.a.control.id,
            is_accountable=True,
        ) == 0
        assert await _count(
            db, EvidenceTeamAssignment,
            evidence_tracking_id=scenario.a.evidence.id, is_accountable=True,
        ) == 0

    async def test_is_accountable_defaults_to_false(self, db, scenario):
        """An insert that says nothing about accountability claims nothing.

        Defaulting to true would make the first assignment on every control an
        ownership decision the caller never made.
        """
        row = ControlTeamAssignment(
            scoped_control_id=scenario.a.control.id,
            team_id=scenario.a.team.id,
            organization_id=scenario.a.org.id,
        )
        db.add(row)
        await db.flush()
        await db.refresh(row)

        assert row.is_accountable is False


@requires_postgres
class TestTheExistingAssignmentPathStillWorks:
    """Regression: phase 3 is additive, and the per-user path is unaffected."""

    async def test_a_user_can_still_be_assigned_to_a_control_directly(
        self, db, scenario
    ):
        db.add(Assignment(
            assignable_type="control",
            assignable_id=scenario.a.control.id,
            user_id=scenario.actor.id,
            role="primary",
        ))
        await db.flush()

        assert await _count(
            db, Assignment, assignable_id=scenario.a.control.id
        ) == 1

    async def test_the_two_paths_coexist_on_the_same_control(self, db, scenario):
        """A team owns it and a person owns it, at the same time.

        Phase 3 adds a second axis of ownership; it does not replace the
        first, and nothing in the schema makes them exclusive.
        """
        scenario.a.control.assigned_user_id = scenario.actor.id
        db.add(Assignment(
            assignable_type="control",
            assignable_id=scenario.a.control.id,
            user_id=scenario.actor.id,
        ))
        db.add(_control_row(scenario, accountable=True))
        await db.flush()

        assert await _count(
            db, Assignment, assignable_id=scenario.a.control.id
        ) == 1
        assert await _count(
            db, ControlTeamAssignment, scoped_control_id=scenario.a.control.id
        ) == 1

    async def test_the_polymorphic_table_still_accepts_a_dangling_target(
        self, db, scenario
    ):
        """Not a feature — the documented reason phase 3 did not reuse it.

        If this ever starts failing, `assignments.assignable_id` has grown a
        foreign key, and the migration's argument for two dedicated tables
        should be revisited rather than left as a stale comment.
        """
        db.add(Assignment(
            assignable_type="control",
            assignable_id=uuid.uuid4(),
            user_id=scenario.actor.id,
        ))
        await db.flush()  # accepted, with no such control anywhere


@requires_postgres
class TestPhaseFiveTablesAreAbsentFromTheDatabase:
    """The structural claim above, checked against the deployed schema too.

    A migration file is a claim about what should exist; this is what does.
    """

    @pytest.mark.parametrize(
        "table", ["risk_team_assignments", "vendor_team_assignments"]
    )
    async def test_the_table_has_not_been_created(self, db, table):
        exists = (await db.execute(
            sa.text(
                "SELECT to_regclass('public.' || :name) IS NOT NULL"
            ),
            {"name": table},
        )).scalar_one()
        assert exists is False, f"{table} is phase 5 and should not exist yet"
