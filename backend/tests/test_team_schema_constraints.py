"""Tenant isolation for teams is enforced by the DATABASE, not by Python (#822 phase 1).

Four of the phase-1 acceptance criteria are claims about PostgreSQL:

* a user cannot be put on a team of an organisation they are not a member of;
* losing organisation membership removes the person from that org's teams;
* a team has **at most** one ``primary``;
* a team has **at most** one ``delegate``.

A mocked session cannot prove any of those — it would only prove that a fake
agreed with itself. So this file follows ``test_audit_log_append_only.py``:

* Structural assertions on the migration text, which run everywhere including
  a CI with no database, and catch a constraint silently dropped from the file.
* A functional round-trip against a live PostgreSQL in a throwaway schema,
  which is the only thing that actually proves the constraints bite.
  **These SKIP when no PostgreSQL is reachable, which includes CI.** A green
  CI run is not evidence they passed; the local transcript is.

Two further cases guard the design rather than a stated criterion:

* a team with zero members is legal (the indexes are partial, "at most one",
  never "exactly one"); and
* ``organization_id`` naming your own org while ``team_id`` names another
  tenant's team is refused by the *second* composite foreign key. One
  composite key is a half-open door, and this is the test that says so.

Run it against the dev stack with::

    docker compose exec -T backend python -m pytest \\
        tests/test_team_schema_constraints.py -v

``DATABASE_URL`` is already set inside that container and is picked up
automatically. Check the summary says 30 passed, not "17 passed, 13 skipped" --
the skipped thirteen are the only ones that touch a real database.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import sys
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MIGRATION_FILE = (
    pathlib.Path(__file__).resolve().parents[1]
    / "alembic" / "versions" / "20260824_120000_functions_teams_team_members.py"
)

def _default_dsn() -> str:
    """Prefer the DSN the application itself is configured with.

    These tests skip when they cannot reach PostgreSQL, so a DSN that is merely
    plausible is worse than no DSN at all: the run goes green with the thirteen
    tests that actually prove the constraints quietly skipped. Inside the dev
    stack ``DATABASE_URL`` is already correct and already has the password, so
    use it and swap the async driver for the sync one this file needs. The
    literal below is only for a bare checkout with nothing configured.
    """
    configured = os.getenv("DATABASE_URL")
    if configured:
        return configured.replace("+asyncpg", "+psycopg2")
    return (
        "postgresql+psycopg2://cg:"
        + os.getenv("DEV_DB_PASSWORD", "changeme_secure_password")
        + "@127.0.0.1:5432/cg_scf"
    )


DSN = os.getenv("TEST_MIGRATION_DATABASE_URL", _default_dsn())

SCHEMA = f"pytest_teams_{uuid.uuid4().hex[:8]}"

# The fourteen keys the platform ships. Written out here rather than derived
# from the migration, so that renaming one in the migration fails this test
# instead of quietly redefining "canonical".
CANONICAL_FUNCTION_KEYS = (
    "governance_risk_compliance",
    "security_operations",
    "security_engineering",
    "it_operations",
    "software_engineering",
    "identity_access_management",
    "data_privacy",
    "human_resources",
    "legal",
    "finance",
    "procurement_vendor_management",
    "facilities_physical_security",
    "business_continuity",
    "executive_leadership",
)


def _load_migration():
    """Import the migration module. Needs alembic on the path."""
    spec = importlib.util.spec_from_file_location("teams_functions_migration", MIGRATION_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _function_seed():
    """Read FUNCTION_SEED out of the migration *without* importing it.

    The migration does ``from alembic import op`` at module scope, so
    importing it needs alembic installed. The seed assertions are pure data
    checks that should run in every environment, including a bare CI with no
    migration tooling, so they parse the literal instead.
    """
    import ast

    tree = ast.parse(MIGRATION_FILE.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "FUNCTION_SEED" for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("FUNCTION_SEED not found in the migration")


# ---------------------------------------------------------------------------
# Structural — runs everywhere, including a CI with no database
# ---------------------------------------------------------------------------

class TestMigrationShape:
    @pytest.fixture(scope="class")
    def source(self):
        return MIGRATION_FILE.read_text()

    def test_revision_chains_from_the_audit_log_migration(self, source):
        assert "revision: str = 'teamsfunctions1'" in source
        assert "down_revision: Union[str, None] = 'auditappendonly1'" in source

    @pytest.mark.parametrize(
        "constraint",
        [
            "fk_team_members_org_member",
            "fk_team_members_team_org",
            "fk_team_members_team",
        ],
    )
    def test_composite_isolation_keys_are_named_and_present(self, source, constraint):
        # The names are load-bearing: the behavioural tests below assert on
        # them, and an operator reading a rejection sees them.
        assert f"name='{constraint}'" in source

    def test_org_member_key_targets_the_membership_table(self, source):
        assert "'organization_members.organization_id', 'organization_members.user_id'" in source

    def test_team_org_key_targets_the_teams_composite(self, source):
        # Needs uq_teams_org_id on the other side or the FK cannot be created.
        assert "'teams.organization_id', 'teams.id'" in source
        assert "name='uq_teams_org_id'" in source

    @pytest.mark.parametrize(
        "index,role",
        [("uq_team_primary", "primary"), ("uq_team_delegate", "delegate")],
    )
    def test_role_indexes_are_partial_and_unique(self, source, index, role):
        # unique=True without the partial WHERE would mean one member per team
        # in total; the WHERE without unique=True would mean nothing at all.
        assert f"'{index}', 'team_members', ['team_id'], unique=True" in source
        assert f"postgresql_where=sa.text(\"membership_role = '{role}'\")" in source

    def test_membership_revocation_cascades(self, source):
        # ON DELETE CASCADE on the membership key is what makes revoking an
        # org membership also remove the person from that org's teams.
        assert "name='fk_team_members_org_member', ondelete='CASCADE'" in source

    def test_functions_are_restrict_not_cascade(self, source):
        # Platform-static rows: deleting one must not silently destroy a
        # tenant's teams.
        assert "sa.ForeignKey('functions.id', ondelete='RESTRICT')" in source

    def test_downgrade_removes_every_object_it_created(self, source):
        for stmt in (
            "op.drop_index('ix_team_members_user_id', table_name='team_members')",
            "op.drop_index('uq_team_delegate', table_name='team_members')",
            "op.drop_index('uq_team_primary', table_name='team_members')",
            "op.drop_table('team_members')",
            "op.drop_index('ix_teams_function_id', table_name='teams')",
            "op.drop_table('teams')",
            "op.drop_table('functions')",
        ):
            assert stmt in source


class TestFunctionSeed:
    """Exactly fourteen functions, with the canonical keys and fixed ids."""

    @pytest.fixture(scope="class")
    def seed(self):
        return _function_seed()

    def test_exactly_fourteen_functions_are_seeded(self, seed):
        assert len(seed) == 14

    def test_the_keys_are_the_canonical_fourteen_in_order(self, seed):
        assert tuple(key for _id, key, _name, _desc in seed) == CANONICAL_FUNCTION_KEYS

    def test_ids_are_fixed_not_generated(self, seed):
        # Every tenant references the same fourteen rows. A gen_random_uuid()
        # here would give staging different ids from production and break any
        # later phase that ships a mapping.
        for function_id, key, _name, _desc in seed:
            assert uuid.UUID(function_id), key

    def test_ids_are_the_documented_uuid5_derivation(self, seed):
        # The migration's docstring claims the ids are uuid5(NS, key). If that
        # is only a comment, a regenerated id would go unnoticed; recomputing
        # it here makes the claim testable.
        namespace = uuid.uuid5(uuid.NAMESPACE_DNS, "functions.scf.compliancegenie.io")
        assert str(namespace) == "95b9c466-f07c-52d9-a134-0bb60bbe3797"
        for function_id, key, _name, _desc in seed:
            assert function_id == str(uuid.uuid5(namespace, key)), key

    def test_ids_and_keys_are_unique(self, seed):
        assert len({row[0] for row in seed}) == 14
        assert len({row[1] for row in seed}) == 14

    def test_every_function_has_a_name_and_a_description(self, seed):
        for _id, key, name, description in seed:
            assert name and name.strip(), key
            assert description and description.strip(), key


# ---------------------------------------------------------------------------
# Functional — needs PostgreSQL. SKIPS in CI.
# ---------------------------------------------------------------------------

ORG_A = str(uuid.uuid4())
ORG_B = str(uuid.uuid4())
USER_1 = str(uuid.uuid4())   # member of org A
USER_2 = str(uuid.uuid4())   # member of org A
USER_3 = str(uuid.uuid4())   # member of org B only
GRC_FUNCTION = "024f3da6-eb1d-5656-a81d-94a24b39abcf"


@pytest.fixture(scope="module")
def conn():
    try:
        engine = sa.create_engine(DSN, connect_args={"connect_timeout": 3})
        connection = engine.connect()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"No PostgreSQL reachable for team constraint tests: {exc}")

    connection.execute(text(f'CREATE SCHEMA "{SCHEMA}"'))
    connection.execute(text(f'SET search_path TO "{SCHEMA}"'))

    # Parent tables, cut down to the columns the migration actually needs.
    # NOT NULL is kept where production has it (organizations.slug,
    # users.google_sub) so a fixture that forgets them fails here rather than
    # letting the real migration meet a shape this test never exercised.
    connection.execute(text("""
        CREATE TABLE organizations (
            id uuid PRIMARY KEY,
            name varchar(255) NOT NULL,
            slug varchar(100) NOT NULL UNIQUE
        )
    """))
    connection.execute(text("""
        CREATE TABLE users (
            id uuid PRIMARY KEY,
            google_sub varchar(255) NOT NULL,
            email varchar(255) NOT NULL UNIQUE
        )
    """))
    connection.execute(text("""
        CREATE TABLE organization_members (
            id uuid PRIMARY KEY,
            organization_id uuid NOT NULL
                REFERENCES organizations(id) ON DELETE CASCADE,
            user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            role varchar(50) NOT NULL DEFAULT 'viewer',
            CONSTRAINT uq_organization_members_org_user
                UNIQUE (organization_id, user_id)
        )
    """))
    connection.commit()

    module = _load_migration()

    # Bind alembic's operations proxy to this connection so the migration's
    # own op.create_table / op.bulk_insert / op.create_index run verbatim
    # against the throwaway schema. Rewriting the DDL by hand here would test
    # the rewrite, not the migration.
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    module.op = Operations(MigrationContext.configure(connection))
    module.upgrade()
    connection.commit()

    try:
        yield connection
    finally:
        connection.rollback()
        connection.execute(text('SET search_path TO public'))
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE'))
        connection.commit()
        connection.close()
        engine.dispose()


@pytest.fixture(scope="module")
def tenants(conn):
    """Two organisations, three users, and one team in each organisation.

    USER_1 and USER_2 are members of org A; USER_3 is a member of org B only.
    Two distinct users in org A matter: the ``uq_team_primary`` and
    ``uq_team_delegate`` tests must not reuse one user, or they would trip
    ``uq_team_members_team_user`` and prove nothing about the role indexes.
    """
    for org_id, slug in ((ORG_A, "org-a"), (ORG_B, "org-b")):
        conn.execute(
            text("INSERT INTO organizations (id, name, slug) VALUES (:i, :n, :s)"),
            {"i": org_id, "n": slug, "s": slug},
        )
    for user_id in (USER_1, USER_2, USER_3):
        conn.execute(
            text("INSERT INTO users (id, google_sub, email) VALUES (:i, :g, :e)"),
            {"i": user_id, "g": f"sub-{user_id}", "e": f"{user_id}@example.com"},
        )
    for org_id, user_id in ((ORG_A, USER_1), (ORG_A, USER_2), (ORG_B, USER_3)):
        conn.execute(
            text("""
                INSERT INTO organization_members (id, organization_id, user_id, role)
                VALUES (:i, :o, :u, 'editor')
            """),
            {"i": str(uuid.uuid4()), "o": org_id, "u": user_id},
        )

    team_a = str(uuid.uuid4())
    team_b = str(uuid.uuid4())
    for team_id, org_id, name in ((team_a, ORG_A, "A Team"), (team_b, ORG_B, "B Team")):
        conn.execute(
            text("""
                INSERT INTO teams (id, organization_id, function_id, name)
                VALUES (:i, :o, :f, :n)
            """),
            {"i": team_id, "o": org_id, "f": GRC_FUNCTION, "n": name},
        )
    conn.commit()
    return {"team_a": team_a, "team_b": team_b}


def _add_member(conn, *, team_id, org_id, user_id, role="member"):
    row_id = str(uuid.uuid4())
    conn.execute(
        text("""
            INSERT INTO team_members
                (id, team_id, organization_id, user_id, membership_role)
            VALUES (:i, :t, :o, :u, :r)
        """),
        {"i": row_id, "t": team_id, "o": org_id, "u": user_id, "r": role},
    )
    return row_id


@pytest.fixture(autouse=True)
def clean_members(request):
    """Each functional test starts with no team_members rows.

    Resolved lazily: taking ``conn`` as a parameter would make every
    structural test in this file open a database connection, and skip when it
    could not — turning the CI-safe half of the file into skips too.
    """
    if "tenants" not in request.fixturenames:
        yield
        return
    request.getfixturevalue("tenants")
    connection = request.getfixturevalue("conn")
    connection.rollback()
    connection.execute(text("DELETE FROM team_members"))
    connection.commit()
    yield


class TestSeedLandsInTheDatabase:
    def test_fourteen_rows_with_the_canonical_keys(self, conn, tenants):
        rows = conn.execute(
            text("SELECT key FROM functions ORDER BY display_order")
        ).scalars().all()
        assert tuple(rows) == CANONICAL_FUNCTION_KEYS

    def test_the_seeded_ids_are_the_fixed_ones(self, conn, tenants):
        seeded = dict(conn.execute(text("SELECT key, id::text FROM functions")).all())
        for function_id, key, _name, _desc in _function_seed():
            assert seeded[key] == function_id

    def test_every_seeded_function_is_active(self, conn, tenants):
        inactive = conn.execute(
            text("SELECT count(*) FROM functions WHERE is_active IS NOT TRUE")
        ).scalar()
        assert inactive == 0


class TestCrossTenantIsolation:
    def test_a_user_from_another_org_cannot_join_this_orgs_team(self, conn, tenants):
        # USER_3 is a member of org B. Naming org A (which they are not a
        # member of) must be refused by the membership half of the pair.
        with pytest.raises(Exception) as exc:
            _add_member(
                conn,
                team_id=tenants["team_a"],
                org_id=ORG_A,
                user_id=USER_3,
            )
        conn.rollback()
        assert "fk_team_members_org_member" in str(exc.value)

    def test_naming_your_own_org_beside_another_tenants_team_is_refused(self, conn, tenants):
        # The attack the second composite key exists for: organization_id is
        # the caller's own org and user_id is genuinely a member of it, so the
        # membership half is satisfied. Only fk_team_members_team_org catches
        # that team_id belongs to a different tenant.
        with pytest.raises(Exception) as exc:
            _add_member(
                conn,
                team_id=tenants["team_b"],
                org_id=ORG_A,
                user_id=USER_1,
            )
        conn.rollback()
        assert "fk_team_members_team_org" in str(exc.value)

    def test_a_legitimate_member_is_accepted(self, conn, tenants):
        # The isolation tests above are only meaningful if the same statement
        # shape succeeds when all three columns agree.
        _add_member(conn, team_id=tenants["team_a"], org_id=ORG_A, user_id=USER_1)
        conn.commit()
        count = conn.execute(
            text("SELECT count(*) FROM team_members WHERE team_id = :t"),
            {"t": tenants["team_a"]},
        ).scalar()
        assert count == 1

    def test_revoking_org_membership_removes_them_from_that_orgs_teams(self, conn, tenants):
        _add_member(conn, team_id=tenants["team_a"], org_id=ORG_A, user_id=USER_2)
        conn.commit()
        assert conn.execute(
            text("SELECT count(*) FROM team_members WHERE user_id = :u"),
            {"u": USER_2},
        ).scalar() == 1

        conn.execute(
            text("""
                DELETE FROM organization_members
                 WHERE organization_id = :o AND user_id = :u
            """),
            {"o": ORG_A, "u": USER_2},
        )
        conn.commit()

        remaining = conn.execute(
            text("SELECT count(*) FROM team_members WHERE user_id = :u"),
            {"u": USER_2},
        ).scalar()
        assert remaining == 0

        # Put the membership back for the tests that follow.
        conn.execute(
            text("""
                INSERT INTO organization_members (id, organization_id, user_id, role)
                VALUES (:i, :o, :u, 'editor')
            """),
            {"i": str(uuid.uuid4()), "o": ORG_A, "u": USER_2},
        )
        conn.commit()


class TestAtMostOnePrimaryAndDelegate:
    def test_a_second_primary_is_refused(self, conn, tenants):
        # TWO DISTINCT users on purpose. The same user twice would trip
        # uq_team_members_team_user and say nothing about uq_team_primary.
        _add_member(
            conn, team_id=tenants["team_a"], org_id=ORG_A,
            user_id=USER_1, role="primary",
        )
        conn.commit()
        with pytest.raises(Exception) as exc:
            _add_member(
                conn, team_id=tenants["team_a"], org_id=ORG_A,
                user_id=USER_2, role="primary",
            )
        conn.rollback()
        assert "uq_team_primary" in str(exc.value)

    def test_a_second_delegate_is_refused(self, conn, tenants):
        _add_member(
            conn, team_id=tenants["team_a"], org_id=ORG_A,
            user_id=USER_1, role="delegate",
        )
        conn.commit()
        with pytest.raises(Exception) as exc:
            _add_member(
                conn, team_id=tenants["team_a"], org_id=ORG_A,
                user_id=USER_2, role="delegate",
            )
        conn.rollback()
        assert "uq_team_delegate" in str(exc.value)

    def test_one_primary_and_one_delegate_coexist(self, conn, tenants):
        # The indexes are per-role. If either were unique on team_id alone,
        # this would fail — which is what makes the two tests above about the
        # partial WHERE clause rather than about uniqueness in general.
        _add_member(
            conn, team_id=tenants["team_a"], org_id=ORG_A,
            user_id=USER_1, role="primary",
        )
        _add_member(
            conn, team_id=tenants["team_a"], org_id=ORG_A,
            user_id=USER_2, role="delegate",
        )
        conn.commit()
        roles = conn.execute(
            text("""
                SELECT membership_role FROM team_members
                 WHERE team_id = :t ORDER BY membership_role
            """),
            {"t": tenants["team_a"]},
        ).scalars().all()
        assert roles == ["delegate", "primary"]

    def test_another_team_may_have_its_own_primary(self, conn, tenants):
        # Partial *and* keyed on team_id: the constraint is per team, not
        # global.
        _add_member(
            conn, team_id=tenants["team_a"], org_id=ORG_A,
            user_id=USER_1, role="primary",
        )
        _add_member(
            conn, team_id=tenants["team_b"], org_id=ORG_B,
            user_id=USER_3, role="primary",
        )
        conn.commit()
        count = conn.execute(
            text("SELECT count(*) FROM team_members WHERE membership_role = 'primary'")
        ).scalar()
        assert count == 2

    def test_many_plain_members_are_allowed(self, conn, tenants):
        _add_member(conn, team_id=tenants["team_a"], org_id=ORG_A, user_id=USER_1)
        _add_member(conn, team_id=tenants["team_a"], org_id=ORG_A, user_id=USER_2)
        conn.commit()
        count = conn.execute(
            text("SELECT count(*) FROM team_members WHERE membership_role = 'member'")
        ).scalar()
        assert count == 2


class TestATeamWithNoMembersIsLegal:
    def test_creating_a_team_with_zero_members_succeeds(self, conn, tenants):
        # "At most one", never "exactly one": every team has no members for
        # the moment between its INSERT and the first membership row, and a
        # non-partial index would make that moment impossible.
        team_id = str(uuid.uuid4())
        conn.execute(
            text("""
                INSERT INTO teams (id, organization_id, function_id, name)
                VALUES (:i, :o, :f, 'Empty Team')
            """),
            {"i": team_id, "o": ORG_A, "f": GRC_FUNCTION},
        )
        conn.commit()

        assert conn.execute(
            text("SELECT count(*) FROM teams WHERE id = :i"), {"i": team_id}
        ).scalar() == 1
        assert conn.execute(
            text("SELECT count(*) FROM team_members WHERE team_id = :i"), {"i": team_id}
        ).scalar() == 0

        conn.execute(text("DELETE FROM teams WHERE id = :i"), {"i": team_id})
        conn.commit()
