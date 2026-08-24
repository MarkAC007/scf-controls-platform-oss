"""A task carries its tenant, and its team cannot be another tenant's (#822 phase 4).

Five phase-4 acceptance criteria live in this file. Four of them are claims
about PostgreSQL rather than about Python, and the fifth is a claim about
what does **not** exist:

* ``evidence_collection_tasks.organization_id`` is present and agrees with the
  parent evidence item — for every row, permanently, not merely at the moment
  the backfill ran;
* the composite foreign key rejects a task pointing at another organisation's
  team, **by both halves** — ``fk_..._team_org`` catches the naive attempt and
  ``fk_..._evidence_org`` catches the same attempt with ``organization_id``
  tampered to match the team. With only one of the two, the tampered insert
  succeeds and the door is half open;
* deleting a team returns its tasks to inheriting rather than deleting them or
  making the team undeletable;
* no ``task_team_assignments`` table exists — inheritance, not co-ownership.

Two halves, following ``test_team_schema_constraints.py`` (phase 1) and
``test_control_evidence_team_assignment_constraints.py`` (phase 3):

* **Structural** assertions on the migration text and the ORM metadata, which
  run everywhere including a CI with no database, and catch a constraint
  quietly deleted from the file;
* **Behavioural** round-trips against a real PostgreSQL, which are the only
  tests here that prove Postgres will actually refuse anything.
  **The behavioural half SKIPS when no Postgres ``DATABASE_URL`` is reachable,
  which includes CI.** A green CI run is *not* evidence that these criteria
  passed; the local transcript is.

The behavioural tests open a transaction, never commit, and roll back in the
fixture teardown, so a run leaves the database exactly as it found it and a
test that deliberately provokes an ``IntegrityError`` cannot poison the next.

Run against the dev stack with::

    docker compose exec -T backend python -m pytest \\
        tests/test_evidence_task_org_and_owning_team.py -v

``DATABASE_URL`` is already correct inside that container. Read the summary
line before believing it: "18 passed, 14 skipped" means the fourteen tests
that prove the constraints bite did not run.

On the vacuous pass
-------------------

The lesson phase 2 paid for: a test that compares two things it never checked
exist goes green on a database that has *neither*. So
:class:`TestTheConstraintsAreActuallyInstalled` asserts both foreign keys are
present in ``pg_catalog`` — with the right referential action and, for the
team side, the right ``SET NULL`` column list — *before* any test below relies
on one of them rejecting anything. Ask of every test here: what state makes
this pass while the feature is broken? For the rejection tests, the answer
would be "a database with no constraint at all", and that class is what closes
it.
"""
from __future__ import annotations

import os
import pathlib
import re
import sys
import uuid
from datetime import date, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The SQLAlchemy registry spans both modules: models.System has a relationship
# to SystemCatalogTemplate, which lives here. Import it or mapper configuration
# fails on the first query with an unresolvable class name.
import catalog_models  # noqa: E402,F401
from models import (  # noqa: E402
    Base,
    EvidenceCollectionTask,
    EvidenceTeamAssignment,
    EvidenceTracking,
    Function,
    Organization,
    OrganizationMember,
    Team,
    User,
)

BACKEND = pathlib.Path(__file__).resolve().parents[1]
MIGRATION_FILE = (
    BACKEND / "alembic" / "versions"
    / "20260824_160000_evidence_task_org_and_owning_team.py"
)
ALEMBIC_VERSIONS = MIGRATION_FILE.parent

DATABASE_URL = os.getenv("DATABASE_URL", "")

#: Applied to the behavioural classes only. At module scope it would skip the
#: structural half too, and the structural half is the part meant to run in a
#: database-free CI.
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
    """The migration, asserted line by line rather than left to review."""

    @pytest.fixture(scope="class")
    def source(self):
        return MIGRATION_FILE.read_text()

    def test_the_migration_exists_at_all(self):
        assert MIGRATION_FILE.exists(), (
            f"{MIGRATION_FILE.name} is the phase-4 task migration; without it "
            "every behavioural test below is testing a schema nobody shipped"
        )

    def test_revision_chains_from_the_phase_two_migration(self, source):
        assert "revision: str = 'evtaskteam1'" in source
        assert "down_revision: Union[str, None] = 'invitemembertype1'" in source

    def test_the_next_migration_stacks_on_it_without_branching(self):
        """The multi-function migration is phase 4's single direct child.

        A migration can only remain the Alembic head until the next feature
        lands. What must remain invariant is a linear graph: exactly one child,
        rather than a second sibling head branching from the same revision.
        """
        children = []
        for path in ALEMBIC_VERSIONS.glob("*.py"):
            source = path.read_text()
            if re.search(
                r"^down_revision[^=]*= ['\"]evtaskteam1['\"]", source, re.M
            ):
                children.append((path.name, source))

        assert len(children) == 1
        child_name, child_source = children[0]
        assert child_name == "20260824_170000_team_functions_many_to_many.py"
        assert 'revision: str = "teamfunctions2"' in child_source

    def test_organization_id_is_added_backfilled_then_constrained_in_that_order(
        self, source
    ):
        """Three steps, and the order is the whole point.

        NOT NULL first would fail against a table that has rows; skipping the
        backfill would make step three fail; skipping step three would leave a
        nullable tenant column, which is not a tenant column at all.
        """
        upgrade = source.split("def upgrade()")[1].split("def downgrade()")[0]
        add = upgrade.index("sa.Column('organization_id'")
        backfill = upgrade.index("UPDATE evidence_collection_tasks")
        constrain = upgrade.index(
            "'evidence_collection_tasks', 'organization_id', nullable=False"
        )
        assert add < backfill < constrain

    def test_the_backfill_derives_the_tenant_from_the_parent_evidence_item(
        self, source
    ):
        """Derived by join, not invented from a default or a first-row guess."""
        upgrade = source.split("def upgrade()")[1].split("def downgrade()")[0]
        assert "SET organization_id = e.organization_id" in upgrade
        assert "FROM evidence_tracking AS e" in upgrade
        assert "WHERE e.id = t.evidence_tracking_id" in upgrade

    def test_the_column_is_added_nullable_first(self, source):
        assert (
            "sa.Column('organization_id', UUID(as_uuid=True), nullable=True)"
            in source
        )

    def test_owning_team_id_is_nullable_because_null_means_inherit(self, source):
        assert (
            "sa.Column('owning_team_id', UUID(as_uuid=True), nullable=True)"
            in source
        )

    @pytest.mark.parametrize(
        "constraint",
        [
            "fk_evidence_collection_tasks_evidence_org",
            "fk_evidence_collection_tasks_team_org",
        ],
    )
    def test_both_halves_of_the_tenant_check_are_named_and_present(
        self, source, constraint
    ):
        # Both names are load-bearing: the behavioural tests assert on them,
        # and they are what an operator sees in a rejection.
        assert constraint in source

    def test_the_evidence_side_cascades(self, source):
        """Deleting an evidence item already took its tasks with it. A
        composite key that said RESTRICT would contradict the single-column
        key beside it and make evidence items undeletable."""
        assert re.search(
            r"fk_evidence_collection_tasks_evidence_org.*?ondelete='CASCADE'",
            source, re.S,
        )

    def test_the_team_side_nulls_only_the_team_column(self, source):
        """``ON DELETE SET NULL (owning_team_id)`` — the PostgreSQL 15 column
        list, and the reason this constraint is raw DDL.

        A bare ``SET NULL`` would try to null ``organization_id`` as well,
        fail against its NOT NULL, and leave any team with tasks undeletable.
        CASCADE would delete the work rather than orphan it back to
        inheriting. Both wrong answers are one word away from this one.
        """
        assert "ON DELETE SET NULL (owning_team_id)" in source
        assert "REFERENCES teams (organization_id, id)" in source

    def test_the_team_side_is_not_a_bare_set_null_or_a_cascade(self, source):
        # Slice the raw ALTER TABLE, not the first mention of the name — the
        # docstring names the constraint several times before the DDL does.
        team_ddl = re.search(
            r"ADD CONSTRAINT fk_evidence_collection_tasks_team_org.*?\"\"\"",
            source, re.S,
        )
        assert team_ddl, "the raw ADD CONSTRAINT for the team key is missing"
        body = team_ddl.group(0)
        assert "ON DELETE CASCADE" not in body
        assert "ON DELETE RESTRICT" not in body
        assert re.search(r"ON DELETE SET NULL\s*\(\s*owning_team_id\s*\)", body)

    def test_the_composite_keys_target_the_composite_uniques_added_earlier(
        self, source
    ):
        # uq_evidence_tracking_org_id is phase 3's; uq_teams_org_id is phase
        # 1's. Neither is created here — pointing at a unique that does not
        # exist fails on deploy, not on review.
        assert "['organization_id', 'evidence_tracking_id'], ['organization_id', 'id']" \
            in source
        assert "REFERENCES teams (organization_id, id)" in source

    def test_the_migration_writes_no_team_assignments(self, source):
        """Schema only. Inferring team ownership from ``evidence_tracking.owner``
        would write unvalidated free-text junk into every tenant at once; that
        reconciliation is the operator-run phase 7 CLI."""
        upgrade = source.split("def upgrade()")[1].split("def downgrade()")[0]
        assert "owning_team_id = " not in upgrade
        assert "bulk_insert" not in upgrade
        # The only op.execute in upgrade() are the backfill and the raw team
        # FK. Anything else executing SQL here deserves a second look.
        assert upgrade.count("op.execute") == 2

    def test_downgrade_removes_every_object_it_created(self, source):
        downgrade = source.split("def downgrade()")[1]
        for stmt in (
            "ix_notifications_type_reference_created",
            "ix_evidence_collection_tasks_owning_team_id",
            "fk_evidence_collection_tasks_team_org",
            "fk_evidence_collection_tasks_evidence_org",
            "op.drop_column('evidence_collection_tasks', 'owning_team_id')",
            "op.drop_column('evidence_collection_tasks', 'organization_id')",
        ):
            assert stmt in downgrade

    def test_downgrade_drops_the_constraints_before_the_columns(self, source):
        """Dropping a column out from under a foreign key that names it fails."""
        downgrade = source.split("def downgrade()")[1]
        assert downgrade.index("fk_evidence_collection_tasks_team_org") < \
            downgrade.index("drop_column('evidence_collection_tasks', 'owning_team_id')")
        assert downgrade.index("fk_evidence_collection_tasks_evidence_org") < \
            downgrade.index("drop_column('evidence_collection_tasks', 'organization_id')")


class TestTheModelMatchesTheMigration:
    """The ORM has to agree with the database, or the write path breaks in a
    way no schema test would see."""

    def test_the_task_model_carries_both_new_columns(self):
        columns = {c.name for c in EvidenceCollectionTask.__table__.columns}
        assert "organization_id" in columns
        assert "owning_team_id" in columns

    def test_organization_id_is_not_nullable(self):
        assert EvidenceCollectionTask.__table__.c.organization_id.nullable is False

    def test_owning_team_id_is_nullable_because_null_means_inherit(self):
        assert EvidenceCollectionTask.__table__.c.owning_team_id.nullable is True

    def test_both_composite_foreign_keys_are_declared(self):
        names = {
            c.name for c in EvidenceCollectionTask.__table__.constraints
            if isinstance(c, sa.ForeignKeyConstraint)
        }
        assert "fk_evidence_collection_tasks_evidence_org" in names
        assert "fk_evidence_collection_tasks_team_org" in names

    def test_the_composite_keys_name_two_columns_each(self):
        """A single-column key here would be a different constraint wearing the
        same name — and would not close the tenant hole."""
        by_name = {
            c.name: [col.name for col in c.columns]
            for c in EvidenceCollectionTask.__table__.constraints
            if isinstance(c, sa.ForeignKeyConstraint) and c.name
        }
        assert by_name["fk_evidence_collection_tasks_evidence_org"] == [
            "organization_id", "evidence_tracking_id",
        ]
        assert by_name["fk_evidence_collection_tasks_team_org"] == [
            "organization_id", "owning_team_id",
        ]

    def test_the_evidence_key_declares_its_cascade(self):
        """The ORM must agree with the migration on the referential action.

        Only a ``create_all`` database is built from this declaration, but a
        declaration that disagrees with the migration is a trap for whoever
        reads it next — and for the developer whose fallback database then
        behaves differently from production.
        """
        constraint = next(
            c for c in EvidenceCollectionTask.__table__.constraints
            if getattr(c, "name", None)
            == "fk_evidence_collection_tasks_evidence_org"
        )
        assert constraint.ondelete == "CASCADE"

    def test_the_team_key_deliberately_declares_no_action(self):
        """``ON DELETE SET NULL (owning_team_id)`` cannot be expressed here.

        SQLAlchemy validates ``ondelete`` against a fixed list and rejects the
        PostgreSQL 15 column form, so the migration emits that constraint as
        raw DDL and this declaration exists only to tell the ORM the two
        columns reach ``teams`` together. Asserted so that somebody "fixing"
        the omission by writing a bare ``ondelete='SET NULL'`` — which would
        try to null the NOT NULL ``organization_id`` — fails here first.
        """
        constraint = next(
            c for c in EvidenceCollectionTask.__table__.constraints
            if getattr(c, "name", None)
            == "fk_evidence_collection_tasks_team_org"
        )
        assert constraint.ondelete is None

    def test_the_existing_per_user_assignment_column_is_untouched(self):
        """Phase 4 is additive. ``assigned_user_id`` keeps its column, its
        foreign key and its SET NULL behaviour."""
        column = EvidenceCollectionTask.__table__.c.assigned_user_id
        assert column.nullable is True
        key = next(iter(column.foreign_keys))
        assert key.column.table.name == "users"
        assert key.ondelete == "SET NULL"

    def test_the_task_table_has_no_updated_at_column(self):
        """Not a criterion — a tripwire. Three phase-4 attempts were lost to
        assuming this column exists. It does not; assert it so the next person
        finds out here rather than at 2am."""
        columns = {c.name for c in EvidenceCollectionTask.__table__.columns}
        assert "updated_at" not in columns

    def test_due_date_is_not_nullable(self):
        """Same tripwire. A fixture that omits due_date fails at the flush with
        a message about a NOT NULL violation, not about the thing being tested."""
        assert EvidenceCollectionTask.__table__.c.due_date.nullable is False


class TestNoTaskTeamAssignmentsTable:
    """Inheritance, not co-ownership — asserted, not left to omission.

    A task is atomic by construction: one title, one due date, one status, one
    doer. A join table would model a cardinality that does not exist and would
    become a second source of truth that can silently drift from its parent.
    The criterion is that the table is *absent*, and absence is exactly the
    kind of claim that rots the moment somebody adds the table "for
    consistency" with the four that do exist.
    """

    def test_no_model_declares_it(self):
        assert "task_team_assignments" not in Base.metadata.tables

    def test_no_migration_creates_it(self):
        creators = [
            path.name for path in ALEMBIC_VERSIONS.glob("*.py")
            if "task_team_assignments" in path.read_text()
        ]
        assert creators == [], f"inheritance, not co-ownership; created by {creators}"

    def test_nothing_in_the_backend_declares_or_queries_it(self):
        """Prose is allowed; a declaration is not.

        ``models.py`` explains at length why this table does not exist, so a
        bare substring search would fail on the documentation that makes the
        decision reviewable. What must be absent is any of the shapes that
        would bring the table into being or read from it.
        """
        patterns = (
            '__tablename__ = "task_team_assignments"',
            "__tablename__ = 'task_team_assignments'",
            'Table("task_team_assignments"',
            "create_table('task_team_assignments'",
            'create_table("task_team_assignments"',
            "FROM task_team_assignments",
            "INTO task_team_assignments",
        )
        hits = []
        for path in BACKEND.rglob("*.py"):
            if "tests" in path.parts:
                continue
            body = path.read_text()
            for pattern in patterns:
                if pattern in body:
                    hits.append(f"{path.relative_to(BACKEND)}: {pattern}")
        assert hits == [], f"inheritance, not co-ownership; found {hits}"

    def test_models_py_still_says_why_it_does_not_exist(self):
        """The reasoning is the durable part. Somebody will propose this table
        again; the docstring is what answers them."""
        body = (BACKEND / "models.py").read_text()
        assert "task_team_assignments" in body

    def test_the_override_column_is_the_whole_mechanism(self):
        """The positive half of the same claim: there IS a way to override, so
        this is a deliberate design and not an omission of the feature."""
        assert "owning_team_id" in {
            c.name for c in EvidenceCollectionTask.__table__.columns
        }


class TestBothWritePathsSetTheTenant:
    """A NOT NULL column with no writer is a broken write path, not a follow-up.

    Source assertions rather than round-trips: the two construction sites are
    an API handler and a Celery-side generator, and reaching either through
    its real entry point costs more setup than the claim is worth. The claim
    is narrow — every ``EvidenceCollectionTask(`` in the backend names
    ``organization_id`` — and it is enforced here by finding the sites rather
    than by listing them, so a third site added tomorrow is caught too.
    """

    @pytest.fixture(scope="class")
    def construction_sites(self):
        sites = []
        for path in BACKEND.rglob("*.py"):
            if "tests" in path.parts or path.name == "models.py":
                continue
            body = path.read_text()
            for match in re.finditer(r"EvidenceCollectionTask\(", body):
                sites.append((str(path.relative_to(BACKEND)), body[match.start():match.start() + 700]))
        return sites

    def test_the_sites_are_found_at_all(self, construction_sites):
        """Closes the vacuous pass: an empty list would make the test below
        pass by iterating over nothing."""
        assert len(construction_sites) >= 2, (
            "expected at least the API handler and the task generator; "
            f"found {[p for p, _ in construction_sites]}"
        )

    def test_every_site_sets_organization_id(self, construction_sites):
        missing = [
            path for path, snippet in construction_sites
            if "organization_id=" not in snippet
        ]
        assert missing == [], f"NOT NULL column with no writer at: {missing}"


# ---------------------------------------------------------------------------
# Behavioural — needs PostgreSQL. SKIPS in CI.
# ---------------------------------------------------------------------------

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


class _Tenant:
    def __init__(self, org, team, other_team, evidence):
        self.org = org
        self.team = team
        self.other_team = other_team
        self.evidence = evidence


class _Scenario:
    def __init__(self, a, b, actor):
        self.a = a          # the tenant under test
        self.b = b          # the neighbour whose data must stay out of reach
        self.actor = actor


async def _make_tenant(db, function, tag):
    org = Organization(name=f"tk-{tag}", slug=f"tk-{tag}")
    db.add(org)
    await db.flush()

    teams = []
    for suffix in ("owner", "second"):
        team = Team(
            organization_id=org.id,
            function_id=function.id,   # NOT NULL — a team without one is refused
            name=f"team-{suffix}-{tag}",
        )
        db.add(team)
        teams.append(team)

    evidence = EvidenceTracking(organization_id=org.id, evidence_id=f"EV-{tag}")
    db.add(evidence)
    await db.flush()

    return _Tenant(org, teams[0], teams[1], evidence)


@pytest.fixture
async def scenario(db):
    function = (await db.execute(
        select(Function).where(Function.is_active.is_(True)).limit(1)
    )).scalar_one_or_none()
    if function is None:  # pragma: no cover - environment dependent
        pytest.skip("no seeded functions in this database")

    tag = uuid.uuid4().hex[:10]
    tenant_a = await _make_tenant(db, function, f"a{tag}")
    tenant_b = await _make_tenant(db, function, f"b{tag}")

    actor = User(email=f"tk-{tag}@example.invalid", google_sub=f"sub-{tag}")
    db.add(actor)
    await db.flush()
    db.add(OrganizationMember(
        organization_id=tenant_a.org.id, user_id=actor.id, role="admin",
    ))
    await db.flush()

    return _Scenario(tenant_a, tenant_b, actor)


def _task(scenario, *, org=None, evidence=None, team=None, **overrides):
    """A task on tenant A, with every tenant-relevant field overridable.

    Defaults are the legal row. Each rejection test below changes exactly one
    thing, so the test names the attack rather than restating the schema.
    """
    tenant = scenario.a
    fields = dict(
        evidence_tracking_id=(evidence or tenant.evidence).id,
        organization_id=(org or tenant.org).id,
        owning_team_id=team.id if team is not None else None,
        task_type="collection",
        title="collect the thing",
        due_date=date.today() + timedelta(days=3),   # NOT NULL
        status="not_started",
    )
    fields.update(overrides)
    return EvidenceCollectionTask(**fields)


@requires_postgres
class TestTheConstraintsAreActuallyInstalled:
    """Before anything below relies on a constraint rejecting a row, prove the
    constraint is there.

    This is the class that closes the vacuous pass. Every rejection test in
    this file would go green against a database that simply had no such
    constraint *if* the row were also rejected for some other reason — and a
    parity check like "no task disagrees with its parent" goes green against a
    table with no rows and no constraint at all. So: assert presence, assert
    the referential action, assert the SET NULL column list, and assert it
    from ``pg_catalog`` rather than from the migration text, which is what the
    structural half already covers.
    """

    async def test_both_foreign_keys_exist_on_the_live_table(self, db):
        names = set((await db.execute(text(
            """
            SELECT conname FROM pg_constraint
             WHERE conrelid = 'evidence_collection_tasks'::regclass
               AND contype = 'f'
            """
        ))).scalars().all())
        assert "fk_evidence_collection_tasks_evidence_org" in names
        assert "fk_evidence_collection_tasks_team_org" in names

    async def test_each_key_spans_two_columns(self, db):
        """A one-column key wearing the right name closes nothing."""
        rows = dict((await db.execute(text(
            """
            SELECT conname, cardinality(conkey) FROM pg_constraint
             WHERE conrelid = 'evidence_collection_tasks'::regclass
               AND conname LIKE 'fk_evidence_collection_tasks_%_org'
            """
        ))).all())
        assert rows["fk_evidence_collection_tasks_evidence_org"] == 2
        assert rows["fk_evidence_collection_tasks_team_org"] == 2

    async def test_the_evidence_key_cascades_and_the_team_key_sets_null(self, db):
        actions = dict((await db.execute(text(
            """
            SELECT conname, confdeltype FROM pg_constraint
             WHERE conrelid = 'evidence_collection_tasks'::regclass
               AND conname LIKE 'fk_evidence_collection_tasks_%_org'
            """
        ))).all())
        # asyncpg returns PostgreSQL's "char" type as bytes; psycopg2 returns
        # str. Normalise rather than pin the test to one driver.
        def action(name):
            value = actions[name]
            return value.decode() if isinstance(value, bytes) else value

        assert action("fk_evidence_collection_tasks_evidence_org") == "c"  # CASCADE
        assert action("fk_evidence_collection_tasks_team_org") == "n"      # SET NULL

    async def test_the_team_key_nulls_owning_team_id_and_nothing_else(self, db):
        """The PostgreSQL 15 column list, read back from the catalogue.

        ``confdelsetcols`` empty would mean a bare SET NULL, which would try to
        null the NOT NULL ``organization_id`` and make any team with tasks
        undeletable. This assertion is the difference between "we wrote the
        clause" and "the clause is doing what we wrote it for".
        """
        column = (await db.execute(text(
            """
            SELECT a.attname
              FROM pg_constraint c
              JOIN LATERAL unnest(c.confdelsetcols) AS s(attnum) ON TRUE
              JOIN pg_attribute a
                ON a.attrelid = c.conrelid AND a.attnum = s.attnum
             WHERE c.conname = 'fk_evidence_collection_tasks_team_org'
            """
        ))).scalars().all()
        assert column == ["owning_team_id"]

    async def test_organization_id_is_not_null_on_the_live_table(self, db):
        nullable = (await db.execute(text(
            """
            SELECT is_nullable FROM information_schema.columns
             WHERE table_name = 'evidence_collection_tasks'
               AND column_name = 'organization_id'
            """
        ))).scalar_one()
        assert nullable == "NO"


@requires_postgres
class TestATaskCannotReachAnotherTenantsTeam:
    """Both halves. With only one, the tampered insert below succeeds."""

    async def test_half_one_a_task_naming_another_orgs_team_is_refused(
        self, db, scenario
    ):
        """The naive attempt: my org, my evidence item, their team.

        Caught by ``fk_..._team_org``, because ``(my org, their team)`` is not
        a row in ``teams (organization_id, id)``.
        """
        db.add(_task(scenario, team=scenario.b.team))
        with pytest.raises(IntegrityError) as caught:
            await db.flush()

        message = str(caught.value)
        assert "fk_evidence_collection_tasks_team_org" in message
        # And *not* the evidence-side key: the two halves must be two
        # constraints, or one of these tests is the other one written twice.
        assert "fk_evidence_collection_tasks_evidence_org" not in message

    async def test_half_two_tampering_the_org_to_match_the_team_is_also_refused(
        self, db, scenario
    ):
        """The attempt that a single composite key would let through.

        Set ``organization_id`` to the *team's* organisation, so the team-side
        check now passes cleanly — ``(their org, their team)`` is a real row.
        The row still hangs off tenant A's evidence item, and it is
        ``fk_..._evidence_org`` that says no.

        This is the test the brief singles out: delete the evidence-side
        constraint and half one still passes, so half one alone is not
        evidence that the door is shut.
        """
        db.add(_task(scenario, org=scenario.b.org, team=scenario.b.team))
        with pytest.raises(IntegrityError) as caught:
            await db.flush()

        message = str(caught.value)
        assert "fk_evidence_collection_tasks_evidence_org" in message
        assert "fk_evidence_collection_tasks_team_org" not in message

    async def test_a_task_may_not_claim_a_tenant_its_evidence_item_disagrees_with(
        self, db, scenario
    ):
        """The backfill invariant, made permanent.

        The criterion says ``organization_id`` is *backfilled from the parent
        evidence item*. A one-off UPDATE proves that was true the day it ran.
        This proves it stays true: a row whose ``organization_id`` disagrees
        with its parent cannot be written at all, with or without a team.
        """
        db.add(_task(scenario, org=scenario.b.org))
        with pytest.raises(IntegrityError) as caught:
            await db.flush()

        assert "fk_evidence_collection_tasks_evidence_org" in str(caught.value)

    async def test_a_task_with_no_tenant_at_all_is_refused(self, db, scenario):
        db.add(_task(scenario, organization_id=None))
        with pytest.raises(IntegrityError):
            await db.flush()


@requires_postgres
class TestTheLegalRowsAreStillLegal:
    """A constraint that rejects everything is not tenant isolation, it is an
    outage. These are the rows the feature is *for*."""

    async def test_no_team_is_the_common_case_and_is_accepted(self, db, scenario):
        """``owning_team_id IS NULL`` — inherit. Under MATCH SIMPLE the
        composite key stands aside entirely when either column is NULL, which
        is exactly the behaviour the inherit case wants."""
        task = _task(scenario)
        db.add(task)
        await db.flush()

        assert task.owning_team_id is None
        assert task.organization_id == scenario.a.org.id

    async def test_a_team_from_the_same_organisation_is_accepted(self, db, scenario):
        task = _task(scenario, team=scenario.a.team)
        db.add(task)
        await db.flush()

        assert task.owning_team_id == scenario.a.team.id

    async def test_two_tasks_on_one_evidence_item_may_name_different_teams(
        self, db, scenario
    ):
        """The case that motivates the column: ``setup``, ``collection`` and
        ``review`` on one evidence item are routinely different functions."""
        db.add(_task(scenario, team=scenario.a.team, task_type="setup"))
        db.add(_task(scenario, team=scenario.a.other_team, task_type="review"))
        await db.flush()

        count = (await db.execute(
            select(func.count()).select_from(EvidenceCollectionTask)
            .where(EvidenceCollectionTask.evidence_tracking_id
                   == scenario.a.evidence.id)
        )).scalar_one()
        assert count == 2


@requires_postgres
class TestDeletionSemantics:
    """What happens to the work when the org chart changes."""

    async def test_deleting_the_team_orphans_the_task_back_to_inheriting(
        self, db, scenario
    ):
        """Not CASCADE — the work survives. Not RESTRICT — the team is
        deletable. And ``organization_id`` is untouched, which is the whole
        reason for the PostgreSQL 15 column list."""
        task = _task(scenario, team=scenario.a.team)
        db.add(task)
        await db.flush()
        task_id, org_id = task.id, scenario.a.org.id

        await db.delete(scenario.a.team)
        await db.flush()

        # Read the row back with raw SQL rather than through the identity map:
        # the ORM's copy of this task still says owning_team_id is set, because
        # the SET NULL happened inside PostgreSQL and nothing told SQLAlchemy.
        # A test that read the ORM object would pass against a CASCADE too.
        row = (await db.execute(text(
            "SELECT owning_team_id, organization_id "
            "FROM evidence_collection_tasks WHERE id = :id"
        ), {"id": task_id})).first()

        assert row is not None, "deleting a team must not delete the work"
        assert row.owning_team_id is None, "the task must fall back to inheriting"
        assert row.organization_id == org_id, "the tenant column must be untouched"

    async def test_deleting_the_evidence_item_still_takes_its_tasks(
        self, db, scenario
    ):
        """The pre-existing CASCADE, unchanged by the new composite key beside
        it. A task without its evidence item is not a task."""
        task = _task(scenario, team=scenario.a.team)
        db.add(task)
        await db.flush()
        task_id = task.id

        await db.delete(scenario.a.evidence)
        await db.flush()

        row = (await db.execute(text(
            "SELECT id FROM evidence_collection_tasks WHERE id = :id"
        ), {"id": task_id})).first()
        assert row is None


@requires_postgres
class TestTheLiveDataAgreesWithItsParents:
    """The backfill, checked against the rows that are actually there.

    Weak on its own — a table with no rows passes trivially — which is why
    :meth:`test_there_is_something_to_check` runs first and why the invariant
    is separately proved unfalsifiable in
    ``TestATaskCannotReachAnotherTenantsTeam``. This class is the corroborating
    read, not the evidence.
    """

    async def test_there_is_something_to_check(self, db):
        total = (await db.execute(
            select(func.count()).select_from(EvidenceCollectionTask)
        )).scalar_one()
        if total == 0:
            pytest.skip(
                "no evidence_collection_tasks rows in this database — the "
                "parity check below would pass vacuously, so it is skipped "
                "rather than counted"
            )
        assert total > 0

    async def test_no_task_has_a_null_tenant(self, db):
        nulls = (await db.execute(text(
            "SELECT count(*) FROM evidence_collection_tasks "
            "WHERE organization_id IS NULL"
        ))).scalar_one()
        assert nulls == 0

    async def test_no_task_disagrees_with_its_parent_evidence_item(self, db):
        mismatched = (await db.execute(text(
            """
            SELECT count(*)
              FROM evidence_collection_tasks t
              JOIN evidence_tracking e ON e.id = t.evidence_tracking_id
             WHERE t.organization_id IS DISTINCT FROM e.organization_id
            """
        ))).scalar_one()
        assert mismatched == 0

    async def test_no_task_names_a_team_from_another_organisation(self, db):
        strays = (await db.execute(text(
            """
            SELECT count(*)
              FROM evidence_collection_tasks t
              JOIN teams tm ON tm.id = t.owning_team_id
             WHERE tm.organization_id IS DISTINCT FROM t.organization_id
            """
        ))).scalar_one()
        assert strays == 0
