"""audit_log is append-only at the database level (#789, ISC-82..87).

Two layers:

* Structural assertions on the migration text — these run everywhere,
  including CI, and catch a trigger silently dropped from the file.
* A functional round-trip against a live PostgreSQL in a throwaway schema:
  INSERT succeeds, UPDATE is refused, DELETE is refused, TRUNCATE is
  refused, the two legitimate cascade paths still work, and downgrade
  removes everything cleanly. Skipped automatically when no PostgreSQL is
  reachable (CI has none), so the transcript from a local run is quoted in
  the PR body rather than being the only evidence that exists.

Follows the shape of ``test_vendor_assessment_migration.py``.
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
    / "alembic" / "versions" / "20260824_010000_audit_log_append_only.py"
)

DEFAULT_DSN = (
    "postgresql+psycopg2://cg:"
    + os.getenv("DEV_DB_PASSWORD", "changeme_secure_password")
    + "@127.0.0.1:5432/cg_scf"
)
DSN = os.getenv("TEST_MIGRATION_DATABASE_URL", DEFAULT_DSN)

SCHEMA = f"pytest_auditlog_{uuid.uuid4().hex[:8]}"

ORG_ID = str(uuid.uuid4())
OTHER_ORG_ID = str(uuid.uuid4())
USER_ID = str(uuid.uuid4())


def _load_migration():
    spec = importlib.util.spec_from_file_location("audit_append_only", MIGRATION_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Structural — runs everywhere, including a CI with no database
# ---------------------------------------------------------------------------

class TestMigrationShape:
    @pytest.fixture(scope="class")
    def source(self):
        return MIGRATION_FILE.read_text()

    @pytest.mark.parametrize(
        "trigger,event",
        [
            ("audit_log_no_update", "BEFORE UPDATE ON audit_log"),
            ("audit_log_no_delete", "BEFORE DELETE ON audit_log"),
            ("audit_log_no_truncate", "BEFORE TRUNCATE ON audit_log"),
        ],
    )
    def test_trigger_is_installed(self, source, trigger, event):
        assert f"CREATE TRIGGER {trigger}" in source
        assert event in source

    @pytest.mark.parametrize(
        "trigger",
        ["audit_log_no_update", "audit_log_no_delete", "audit_log_no_truncate"],
    )
    def test_downgrade_removes_the_trigger(self, source, trigger):
        assert f"DROP TRIGGER IF EXISTS {trigger} ON audit_log;" in source

    @pytest.mark.parametrize(
        "fn",
        [
            "audit_log_refuse_update",
            "audit_log_refuse_delete",
            "audit_log_refuse_truncate",
        ],
    )
    def test_downgrade_removes_the_function_too(self, source, fn):
        # A dropped trigger with an orphaned function left behind makes the
        # next upgrade's CREATE OR REPLACE silently reuse stale code.
        assert f"DROP FUNCTION IF EXISTS {fn}();" in source

    def test_truncate_trigger_is_statement_level(self, source):
        # TRUNCATE does not fire row-level triggers at all, so a FOR EACH ROW
        # truncate trigger would be accepted by Postgres and never run.
        assert "FOR EACH STATEMENT EXECUTE FUNCTION audit_log_refuse_truncate()" in source

    def test_the_update_exception_is_column_drift_proof(self, source):
        # Comparing named columns would silently stop covering any column
        # added later. The jsonb form covers the whole row minus one key.
        assert "(to_jsonb(NEW) - 'changed_by_user_id')" in source
        assert "= (to_jsonb(OLD) - 'changed_by_user_id')" in source

    def test_anonymisation_is_one_way(self, source):
        # NULL -> value would let a caller re-attribute a change to somebody.
        # One-wayness rests entirely on this condition, which is why the
        # behavioural tests below probe both directions.
        assert "NEW.changed_by_user_id IS NULL" in source

    def test_delete_exception_is_tied_to_the_tenant_going_away(self, source):
        assert "SELECT 1 FROM organizations WHERE id = OLD.organization_id" in source

    def test_refusals_say_what_to_do_instead(self, source):
        assert "append a new audit row" in source


# ---------------------------------------------------------------------------
# Functional — needs PostgreSQL
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def conn():
    try:
        engine = sa.create_engine(DSN, connect_args={"connect_timeout": 3})
        connection = engine.connect()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"No PostgreSQL reachable for audit_log trigger test: {exc}")

    connection.execute(text(f'CREATE SCHEMA "{SCHEMA}"'))
    connection.execute(text(f'SET search_path TO "{SCHEMA}"'))
    connection.execute(text("""
        CREATE TABLE organizations (id uuid PRIMARY KEY)
    """))
    connection.execute(text("""
        CREATE TABLE users (id uuid PRIMARY KEY)
    """))
    connection.execute(text("""
        CREATE TABLE audit_log (
            id uuid PRIMARY KEY,
            organization_id uuid NOT NULL
                REFERENCES organizations(id) ON DELETE CASCADE,
            entity_type varchar(50) NOT NULL,
            entity_id uuid NOT NULL,
            action varchar(20) NOT NULL,
            field_name varchar(100),
            old_value text,
            new_value text,
            changed_by_user_id uuid
                REFERENCES users(id) ON DELETE SET NULL,
            changed_at timestamptz DEFAULT now()
        )
    """))
    connection.commit()

    module = _load_migration()

    class _Op:
        @staticmethod
        def execute(sql):
            connection.execute(text(sql))

    module.op = _Op
    module.upgrade()
    connection.commit()

    try:
        yield connection, module
    finally:
        connection.rollback()
        connection.execute(text('SET search_path TO public'))
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE'))
        connection.commit()
        connection.close()
        engine.dispose()


def _seed(connection, *, org_id=ORG_ID, user_id=USER_ID):
    row_id = str(uuid.uuid4())
    connection.execute(
        text("INSERT INTO organizations (id) VALUES (:i) ON CONFLICT DO NOTHING"),
        {"i": org_id},
    )
    connection.execute(
        text("INSERT INTO users (id) VALUES (:i) ON CONFLICT DO NOTHING"),
        {"i": user_id},
    )
    connection.execute(
        text("""
            INSERT INTO audit_log
                (id, organization_id, entity_type, entity_id, action,
                 field_name, old_value, new_value, changed_by_user_id)
            VALUES (:id, :org, 'scoped_control', :ent, 'update',
                    'status', 'draft', 'approved', :usr)
        """),
        {"id": row_id, "org": org_id, "ent": str(uuid.uuid4()), "usr": user_id},
    )
    connection.commit()
    return row_id


class TestAppendOnlyEnforcement:
    def test_insert_still_works(self, conn):
        connection, _ = conn
        row_id = _seed(connection)
        count = connection.execute(
            text("SELECT count(*) FROM audit_log WHERE id = :i"), {"i": row_id}
        ).scalar()
        assert count == 1

    def test_update_is_refused(self, conn):
        connection, _ = conn
        row_id = _seed(connection)
        with pytest.raises(Exception) as exc:
            connection.execute(
                text("UPDATE audit_log SET new_value = 'tampered' WHERE id = :i"),
                {"i": row_id},
            )
        connection.rollback()
        assert "append-only" in str(exc.value)

    def test_the_original_value_survives_the_attempt(self, conn):
        connection, _ = conn
        row_id = _seed(connection)
        try:
            connection.execute(
                text("UPDATE audit_log SET new_value = 'tampered' WHERE id = :i"),
                {"i": row_id},
            )
        except Exception:
            connection.rollback()
        value = connection.execute(
            text("SELECT new_value FROM audit_log WHERE id = :i"), {"i": row_id}
        ).scalar()
        assert value == "approved"

    def test_delete_is_refused(self, conn):
        connection, _ = conn
        row_id = _seed(connection)
        with pytest.raises(Exception) as exc:
            connection.execute(
                text("DELETE FROM audit_log WHERE id = :i"), {"i": row_id}
            )
        connection.rollback()
        assert "append-only" in str(exc.value)

    def test_truncate_is_refused(self, conn):
        connection, _ = conn
        _seed(connection)
        with pytest.raises(Exception) as exc:
            connection.execute(text("TRUNCATE audit_log"))
        connection.rollback()
        assert "append-only" in str(exc.value)

    def test_attributing_an_unattributed_row_is_refused(self, conn):
        # The mirror image: a row already anonymised cannot be given an
        # actor. Without this, "who did this" becomes writable after the
        # fact, which is the whole property the table exists to hold.
        connection, _ = conn
        user_id = str(uuid.uuid4())
        row_id = _seed(connection, user_id=user_id)
        connection.execute(text("DELETE FROM users WHERE id = :i"), {"i": user_id})
        connection.commit()

        someone = str(uuid.uuid4())
        connection.execute(text("INSERT INTO users (id) VALUES (:i)"), {"i": someone})
        connection.commit()
        with pytest.raises(Exception) as exc:
            connection.execute(
                text("UPDATE audit_log SET changed_by_user_id = :u WHERE id = :i"),
                {"u": someone, "i": row_id},
            )
        connection.rollback()
        assert "append-only" in str(exc.value)

    def test_nulling_the_actor_cannot_smuggle_another_change(self, conn):
        # The anonymisation exception is a shape, not a keyword. An update
        # that clears changed_by_user_id *and* edits the record is still an
        # edit of the record, and the jsonb comparison is what says so.
        connection, _ = conn
        row_id = _seed(connection)
        with pytest.raises(Exception) as exc:
            connection.execute(
                text("""
                    UPDATE audit_log
                       SET changed_by_user_id = NULL, new_value = 'tampered'
                     WHERE id = :i
                """),
                {"i": row_id},
            )
        connection.rollback()
        assert "append-only" in str(exc.value)

    def test_re_attributing_a_change_to_someone_is_refused(self, conn):
        connection, _ = conn
        row_id = _seed(connection)
        other_user = str(uuid.uuid4())
        connection.execute(
            text("INSERT INTO users (id) VALUES (:i)"), {"i": other_user}
        )
        connection.commit()
        with pytest.raises(Exception) as exc:
            connection.execute(
                text("UPDATE audit_log SET changed_by_user_id = :u WHERE id = :i"),
                {"u": other_user, "i": row_id},
            )
        connection.rollback()
        assert "append-only" in str(exc.value)


class TestLegitimateCascadesStillWork:
    def test_deleting_a_user_anonymises_rather_than_failing(self, conn):
        # ON DELETE SET NULL. Revision auditlognull01 exists precisely so a
        # user can be deleted; refusing this update would re-break the
        # database restore endpoint and the admin CLI.
        connection, _ = conn
        user_id = str(uuid.uuid4())
        row_id = _seed(connection, user_id=user_id)
        connection.execute(text("DELETE FROM users WHERE id = :i"), {"i": user_id})
        connection.commit()
        remaining = connection.execute(
            text("SELECT changed_by_user_id FROM audit_log WHERE id = :i"),
            {"i": row_id},
        ).scalar()
        assert remaining is None

    def test_the_rest_of_the_row_survives_anonymisation(self, conn):
        connection, _ = conn
        user_id = str(uuid.uuid4())
        row_id = _seed(connection, user_id=user_id)
        connection.execute(text("DELETE FROM users WHERE id = :i"), {"i": user_id})
        connection.commit()
        old, new = connection.execute(
            text("SELECT old_value, new_value FROM audit_log WHERE id = :i"),
            {"i": row_id},
        ).one()
        assert (old, new) == ("draft", "approved")

    def test_deleting_an_organization_takes_its_audit_rows(self, conn):
        # ON DELETE CASCADE, relied on by api/admin.py, api/organizations.py
        # and api/provisioning.py.
        connection, _ = conn
        org_id = str(uuid.uuid4())
        row_id = _seed(connection, org_id=org_id)
        connection.execute(
            text("DELETE FROM organizations WHERE id = :i"), {"i": org_id}
        )
        connection.commit()
        count = connection.execute(
            text("SELECT count(*) FROM audit_log WHERE id = :i"), {"i": row_id}
        ).scalar()
        assert count == 0

    def test_another_tenants_rows_are_untouched_by_that_delete(self, conn):
        connection, _ = conn
        doomed = str(uuid.uuid4())
        survivor_row = _seed(connection, org_id=OTHER_ORG_ID)
        _seed(connection, org_id=doomed)
        connection.execute(
            text("DELETE FROM organizations WHERE id = :i"), {"i": doomed}
        )
        connection.commit()
        count = connection.execute(
            text("SELECT count(*) FROM audit_log WHERE id = :i"), {"i": survivor_row}
        ).scalar()
        assert count == 1


class TestDowngrade:
    def test_downgrade_then_upgrade_leaves_the_guard_in_place(self, conn):
        # Run last: downgrade removes the protection, so the assertions
        # either side of it prove the round trip rather than a one-way door.
        connection, module = conn
        row_id = _seed(connection)

        module.downgrade()
        connection.commit()

        triggers = connection.execute(text("""
            SELECT count(*) FROM pg_trigger t
            JOIN pg_class c ON c.oid = t.tgrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relname = 'audit_log' AND n.nspname = :s
              AND NOT t.tgisinternal
        """), {"s": SCHEMA}).scalar()
        assert triggers == 0

        # With the guard gone, the tamper the migration exists to stop works.
        connection.execute(
            text("UPDATE audit_log SET new_value = 'tampered' WHERE id = :i"),
            {"i": row_id},
        )
        connection.commit()

        module.upgrade()
        connection.commit()
        with pytest.raises(Exception):
            connection.execute(
                text("UPDATE audit_log SET new_value = 'again' WHERE id = :i"),
                {"i": row_id},
            )
        connection.rollback()


class TestTheORMDeletePathSurvives:
    """The trigger's DELETE exception keys on the parent already being gone.

    SQLAlchemy, left to itself, does the opposite: for a relationship with a
    delete cascade and no ``passive_deletes``, it loads the children and
    DELETEs them *first*, while the parent is still there. That is precisely
    the shape the trigger refuses, so without ``passive_deletes=True`` on
    ``Organization.audit_logs`` this migration would break every delete-org
    path in the product (``api/admin.py``, ``api/organizations.py``,
    ``api/provisioning.py``, ``cli/admin.py``).

    Rather than assert that in a comment, these tests mirror both
    relationship configurations against the real triggers and show which one
    survives.
    """

    @staticmethod
    def _mapping(passive: bool):
        from sqlalchemy.orm import declarative_base, relationship

        base = declarative_base()

        class Org(base):
            __tablename__ = "organizations"
            id = sa.Column(sa.dialects.postgresql.UUID(as_uuid=False),
                           primary_key=True)
            audit_logs = relationship(
                "Audit", back_populates="organization",
                cascade="all, delete-orphan", passive_deletes=passive,
            )

        class Audit(base):
            __tablename__ = "audit_log"
            id = sa.Column(sa.dialects.postgresql.UUID(as_uuid=False),
                           primary_key=True)
            organization_id = sa.Column(
                sa.dialects.postgresql.UUID(as_uuid=False),
                sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                nullable=False,
            )
            entity_type = sa.Column(sa.String(50), nullable=False)
            entity_id = sa.Column(sa.dialects.postgresql.UUID(as_uuid=False),
                                  nullable=False)
            action = sa.Column(sa.String(20), nullable=False)
            organization = relationship("Org", back_populates="audit_logs")

        return Org, Audit

    def _delete_org_through_the_orm(self, conn, passive: bool):
        from sqlalchemy.orm import Session

        connection, _ = conn
        Org, Audit = self._mapping(passive)
        org_id = str(uuid.uuid4())
        _seed(connection, org_id=org_id)

        engine = sa.create_engine(
            DSN, connect_args={"options": f"-csearch_path={SCHEMA}"}
        )
        try:
            with Session(engine) as session:
                org = session.get(Org, org_id)
                assert org is not None
                session.delete(org)
                session.commit()
        finally:
            engine.dispose()
        return org_id

    def test_without_passive_deletes_the_orm_trips_the_trigger(self, conn):
        with pytest.raises(Exception) as exc:
            self._delete_org_through_the_orm(conn, passive=False)
        assert "append-only" in str(exc.value)

    def test_with_passive_deletes_the_org_and_its_rows_go_cleanly(self, conn):
        connection, _ = conn
        org_id = self._delete_org_through_the_orm(conn, passive=True)
        left = connection.execute(
            text("SELECT count(*) FROM audit_log WHERE organization_id = :i"),
            {"i": org_id},
        ).scalar()
        assert left == 0

    def test_the_real_relationship_is_configured_that_way(self):
        # Structural, so CI without a database still catches the regression.
        from sqlalchemy import inspect as sa_inspect

        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        import catalog_models  # noqa: F401 — completes the mapper registry
        from models import Organization

        rel = sa_inspect(Organization).relationships["audit_logs"]
        assert rel.passive_deletes is True, (
            "Organization.audit_logs must defer to the DB cascade; without it "
            "the ORM deletes audit rows while the org still exists and the "
            "append-only trigger refuses them"
        )


def test_the_model_agrees_with_the_database_about_nullability():
    """The UPDATE exception only exists because this column can be NULL.

    Revision ``auditlognull01`` made it nullable in the database; the model
    still said ``nullable=False``. Nothing broke at runtime -- SQLAlchemy
    does not enforce it on read -- but ``alembic revision --autogenerate``
    compares the two and would have emitted an ``alter_column`` putting the
    NOT NULL back. That would re-break user deletion *and* leave the
    append-only trigger permitting an update the schema forbids.
    """
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import catalog_models  # noqa: F401 — completes the mapper registry
    from models import AuditLog

    column = AuditLog.__table__.columns["changed_by_user_id"]
    assert column.nullable is True
