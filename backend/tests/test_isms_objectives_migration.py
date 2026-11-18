# SPDX-License-Identifier: AGPL-3.0-only
"""ismsobj001: the backfill's row mapping, its DDL, and its constraints on a real database.

The pure half loads the revision with importlib and runs ``_row`` over
elements the old API could never have written. The database half runs on a
transaction that is always rolled back, against a database already at head:
the constraints behave as declared, and the backfill itself runs on a real
row through asyncpg, the way ``alembic/env.py`` runs migrations. Skips (never
passes) when no database resolves.
"""
import importlib.util
import json
import logging
import pathlib
import re
import uuid
from datetime import date
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

import models
from services.isms_metrics import status as status_rules

from test_journey_service import DATABASE_URL, requires_postgres  # noqa: F401

MIGRATION_FILE = (
    pathlib.Path(__file__).resolve().parent.parent
    / "alembic" / "versions" / "20261118_090000_security_objectives_isms_metrics.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("ismsobj001_migration", MIGRATION_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mig():
    return _load()


@pytest.fixture(scope="module")
def source():
    return MIGRATION_FILE.read_text()


ORG = uuid.uuid4()


# ---------------------------------------------------------------------------
# _row over odd elements (S3, C7)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [["planned"], {"v": 1}, 3, None, True, "achieved ", "unknown"])
def test_odd_status_maps_to_active(mig, status):
    row = mig._row(ORG, 1, {"objective": "X", "status": status})
    assert row["lifecycle"] == "active"


@pytest.mark.parametrize("status,expected", [
    ("planned", "active"), ("in_progress", "active"), ("achieved", "achieved"), ("missed", "missed"),
])
def test_known_status_maps(mig, status, expected):
    assert mig._row(ORG, 1, {"status": status})["lifecycle"] == expected


@pytest.mark.parametrize("due", ["2026-02-30", "2026-13-01", "soon", 20260101, ["2026-01-01"]])
def test_impossible_date_becomes_null_and_maintain(mig, due):
    row = mig._row(ORG, 1, {"objective": "X", "due_date": due})
    assert row["due"] is None and row["otype"] == "maintain"


def test_real_date_is_kept_and_achieve(mig):
    row = mig._row(ORG, 1, {"objective": "X", "due_date": " 2027-03-31 "})
    assert row["due"] == date(2027, 3, 31) and row["otype"] == "achieve"


@pytest.mark.parametrize("element,name", [
    ("A bare string", "A bare string"),
    (42, "42"),
    (None, "Untitled objective"),
    (["a", "b"], '["a", "b"]'),
    ("x" * 400, "x" * 300),
])
def test_scalar_element(mig, element, name):
    row = mig._row(ORG, 1, element)
    assert row["name"] == name and row["lifecycle"] == "active" and row["due"] is None
    assert row["measure"] == "" and row["target"] == "" and row["legacy"] is None


def test_object_fields_that_are_not_strings_are_kept_as_json(mig):
    row = mig._row(ORG, 1, {"objective": {"t": 1}, "measure": [1, 2], "owner": "  ", "id": 123})
    assert row["name"] == '{"t": 1}' and row["measure"] == "[1, 2]"
    assert row["owner"] is None and row["legacy"] == "123"


# ---------------------------------------------------------------------------
# Frozen copies agree with the code (C8, S5)
# ---------------------------------------------------------------------------


def test_result_status_copies_match_the_status_rules(mig):
    assert tuple(mig.RESULT_STATUSES) == tuple(status_rules.STATUSES) == tuple(models.METRIC_RESULT_STATUSES)


def test_source_check_copies_match(mig):
    assert mig.SOURCE_CHECK == models.OBJECTIVE_SOURCE_CHECK


# ---------------------------------------------------------------------------
# DDL text (S1, S4, S6, C3, C9)
# ---------------------------------------------------------------------------


def test_team_key_is_raw_ddl_that_nulls_only_the_team(source):
    ddl = re.search(r"ADD CONSTRAINT fk_security_objectives_team_org.*?\"\"\"", source, re.S).group(0)
    assert "FOREIGN KEY (organization_id, owning_team_id)" in ddl
    assert "REFERENCES teams (organization_id, id)" in ddl
    assert "ON DELETE SET NULL (owning_team_id)" in ddl
    assert "CASCADE" not in ddl
    assert 'sa.ForeignKey("teams.id"' not in source


def test_downgrade_drops_what_upgrade_adds(source):
    down = source[source.index("def downgrade"):]
    for name in ("fk_security_objectives_team_org", "ix_security_objectives_owning_team_id",
                 "ix_objective_metrics_objective_id"):
        assert name in down, name


def test_provenance_check_compares_the_element(mig):
    assert "new_value = :value" in str(mig._AUDIT_EXISTS)


def test_docstring_no_longer_says_computed_rows_come_back(mig):
    assert "worked out again" not in mig.__doc__
    assert "cannot be taken again" in mig.__doc__ or "no as-of query" in mig.__doc__


def test_refusal_says_sql_and_the_100_limit(source):
    down = source[source.index("def downgrade"):]
    assert "remove them with SQL" in down and "at most 100 results" in down


# ---------------------------------------------------------------------------
# On a real database at head, rolled back
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def conn():
    engine = create_async_engine(DATABASE_URL)
    try:
        connection = await engine.connect()
    except Exception as exc:  # pragma: no cover - environment dependent
        await engine.dispose()
        pytest.skip(f"database not reachable: {exc}")
    transaction = await connection.begin()
    try:
        yield connection
    finally:
        await transaction.rollback()
        await connection.close()
        await engine.dispose()


def _org(c):
    org = uuid.uuid4()
    c.execute(text("INSERT INTO organizations (id, name, slug) VALUES (:id, :n, :n)"),
              {"id": org, "n": f"ismsmig-{org.hex[:10]}"})
    return org


def _team(c, org):
    function_id = c.execute(text("SELECT id FROM functions LIMIT 1")).scalar()
    if function_id is None:
        pytest.skip("no function rows to hang a team on")
    return c.execute(text(
        "INSERT INTO teams (organization_id, function_id, name) VALUES (:o, :f, :n) RETURNING id"
    ), {"o": org, "f": function_id, "n": f"T {uuid.uuid4().hex[:6]}"}).scalar()


def _objective(c, org, team=None, source="custom", ref="OBJ-01"):
    return c.execute(text(
        "INSERT INTO security_objectives (organization_id, ref, name, source, owning_team_id) "
        "VALUES (:o, :r, 'n', :s, :t) RETURNING id"
    ), {"o": org, "r": ref, "s": source, "t": team}).scalar()


def _rejects(c, fn, constraint):
    savepoint = c.begin_nested()
    with pytest.raises(IntegrityError) as err:
        fn()
    savepoint.rollback()
    assert constraint in str(err.value)


@requires_postgres
@pytest.mark.asyncio
class TestOnTheDatabase:
    async def test_team_key_is_live_with_the_column_list(self, conn):
        definition = await conn.scalar(text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'fk_security_objectives_team_org'"
        ))
        assert definition == (
            "FOREIGN KEY (organization_id, owning_team_id) REFERENCES teams(organization_id, id) "
            "ON DELETE SET NULL (owning_team_id)"
        )
        indexes = set((await conn.execute(text(
            "SELECT indexname FROM pg_indexes WHERE tablename IN ('security_objectives', 'objective_metrics')"
        ))).scalars())
        assert {"ix_security_objectives_owning_team_id", "ix_objective_metrics_objective_id"} <= indexes

    async def test_another_organisations_team_is_refused_and_a_deleted_team_clears(self, conn):
        def run(c):
            org, other = _org(c), _org(c)
            foreign = _team(c, other)
            _rejects(c, lambda: _objective(c, org, foreign), "fk_security_objectives_team_org")
            own = _team(c, org)
            objective = _objective(c, org, own)
            c.execute(text("DELETE FROM teams WHERE id = :t"), {"t": own})
            row = c.execute(text("SELECT organization_id, owning_team_id FROM security_objectives WHERE id = :i"),
                            {"i": objective}).one()
            assert row == (org, None)
        await conn.run_sync(run)

    async def test_source_and_status_checks(self, conn):
        def run(c):
            org = _org(c)
            for good in ("custom", "migrated", "starter:x"):
                _objective(c, org, source=good, ref=f"OBJ-{good[:6]}")
            _rejects(c, lambda: _objective(c, org, source="import", ref="OBJ-99"), "ck_security_objectives_source")
            objective = _objective(c, org, ref="OBJ-77")
            metric = c.execute(text(
                "INSERT INTO objective_metrics (organization_id, objective_id, kind) VALUES (:o, :i, 'logged') "
                "RETURNING id"), {"o": org, "i": objective}).scalar()

            def result(value):
                c.execute(text(
                    "INSERT INTO isms_metric_results (organization_id, objective_metric_id, measured_on, status, source) "
                    "VALUES (:o, :m, CURRENT_DATE, :s, 'logged')"), {"o": org, "m": metric, "s": value})
            for good in status_rules.STATUSES:
                result(good)
            _rejects(c, lambda: result("error"), "ck_isms_metric_results_status")
        await conn.run_sync(run)

    async def test_backfill_on_real_rows(self, conn, mig, monkeypatch, caplog):
        """The backfill through asyncpg, odd elements included, then a re-run after a row moves."""
        elements = [
            {"id": "aaa", "objective": "First", "status": ["planned"], "due_date": "2026-02-30",
             "measure": "m1", "target": "t1"},
            {"id": "bbb", "objective": "Second", "status": {"v": 1}, "due_date": "2027-01-31"},
            "A bare string",
        ]
        state = {}

        def setup(c):
            # Rows already migrated (the dev organisation) would collide with a re-run's derived ids;
            # the transaction is rolled back, so this removal never lands.
            c.execute(text("DELETE FROM security_objectives WHERE source = 'migrated'"))
            state["org"] = org = _org(c)
            state["bad"] = bad = _org(c)
            c.execute(text("INSERT INTO isms_profiles (id, organization_id, objectives) VALUES (gen_random_uuid(), :o, "
                           "CAST(:j AS jsonb))"), {"o": org, "j": json.dumps(elements)})
            c.execute(text("INSERT INTO isms_profiles (id, organization_id, objectives) VALUES (gen_random_uuid(), :o, "
                           "CAST('{\"not\": \"a list\"}' AS jsonb))"), {"o": bad})

        def backfill(c):
            monkeypatch.setattr(mig, "op", SimpleNamespace(get_bind=lambda: c))
            mig._migrate_profile_objectives()

        await conn.run_sync(setup)
        with caplog.at_level(logging.WARNING, logger="alembic.runtime.migration"):
            await conn.run_sync(backfill)
        org, bad = state["org"], state["bad"]

        rows = (await conn.execute(text(
            "SELECT ref, legacy_row_id, name, lifecycle_status, objective_type, due_date FROM security_objectives "
            "WHERE organization_id = :o ORDER BY ref"), {"o": org})).all()
        assert [tuple(r) for r in rows] == [
            ("OBJ-01", "aaa", "First", "active", "maintain", None),
            ("OBJ-02", "bbb", "Second", "active", "achieve", date(2027, 1, 31)),
            ("OBJ-03", None, "A bare string", "active", "maintain", None),
        ]
        messages = " ".join(r.getMessage() for r in caplog.records)
        assert f"organisation {bad} has isms_profiles.objectives of type object" in messages
        assert "OBJ-01 has due_date '2026-02-30', which is not a date" in messages

        # Downgrade, the first element is deleted on the old release, upgrade again (S6).
        first_id = uuid.uuid5(mig._ID_NAMESPACE, f"{org}:objective:1")

        def move(c):
            c.execute(text("DELETE FROM security_objectives WHERE source = 'migrated'"))
            c.execute(text("UPDATE isms_profiles SET objectives = CAST(:j AS jsonb) WHERE organization_id = :o"),
                      {"o": org, "j": json.dumps(elements[1:])})
        await conn.run_sync(move)
        await conn.run_sync(backfill)
        provenance = (await conn.execute(text(
            "SELECT new_value FROM audit_log WHERE entity_id = :i AND field_name = 'migrated_from_isms_profile'"
        ), {"i": first_id})).scalars().all()
        assert sorted(json.loads(v)["objective"] for v in provenance) == ["First", "Second"]
