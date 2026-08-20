"""Migration sanity tests for the six catalog-upgrade migrations (WP0).

Follows the repo's migration-test pattern (test_composite_migration.py,
test_m4_migration.py): no live Postgres. Static checks are complemented by
executing each migration's upgrade()/downgrade() against a recording fake
``op``, which lets us test M1's duplicate-abort behavior, the M3/M6 backfill
statements, and full up/down symmetry. The real full-DB round trip
(alembic upgrade head / downgrade -6) is exercised by the WP0 gate in the
dev stack.
"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import sys
import types

import pytest
import sqlalchemy as sa

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"

# filename -> (revision, down_revision), in chain order
CHAIN = [
    ("20260820_100001_catalog_org_key_uniques.py", "catupg001", "eng4query001"),
    ("20260820_100002_catalog_lifecycle_columns.py", "catupg002", "catupg001"),
    ("20260820_100003_org_catalog_state_selections.py", "catupg003", "catupg002"),
    ("20260820_100004_catalog_import_runs.py", "catupg004", "catupg003"),
    ("20260820_100005_org_reconciliation_runs.py", "catupg005", "catupg004"),
    ("20260820_100006_engagement_catalog_version.py", "catupg006", "catupg005"),
]

CATALOG_TABLES = (
    "scf_catalog_controls",
    "scf_catalog_evidence",
    "scf_catalog_assessment_objectives",
    "scf_catalog_domains",
)


def _load_migration(filename: str):
    """Import a migration module fresh.

    The repo's alembic/ directory shadows the installed alembic package when
    backend/ is on sys.path, so ``from alembic import op`` cannot resolve here.
    Every test replaces ``module.op`` with a RecordingOp before calling
    upgrade()/downgrade(), so a stub satisfies the import; it is installed
    only for the duration of the module exec and then restored.
    """
    path = MIGRATIONS_DIR / filename
    assert path.exists(), f"Migration file missing: {path}"
    module_name = f"catalog_upgrade_migration_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get("alembic")
    stub = types.ModuleType("alembic")
    stub.op = None  # replaced per-test with a RecordingOp
    sys.modules["alembic"] = stub
    try:
        spec.loader.exec_module(module)
    finally:
        if previous is not None:
            sys.modules["alembic"] = previous
        else:
            del sys.modules["alembic"]
    return module


# ---------------------------------------------------------------------------
# Fake alembic op: records DDL calls, answers get_bind() with a stub
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, scalar_value):
        self._scalar = scalar_value

    def scalar(self):
        return self._scalar

    def fetchall(self):
        return []


class _FakeBind:
    def __init__(self, scalar_value):
        self._scalar = scalar_value

    def execute(self, *args, **kwargs):
        return _FakeResult(self._scalar)


class RecordingOp:
    """Stand-in for alembic.op that records the DDL a migration issues."""

    def __init__(self, dupe_group_count: int = 0):
        self._bind = _FakeBind(dupe_group_count)
        self.tables_created: dict[str, list[str]] = {}
        self.tables_dropped: list[str] = []
        self.columns_added: set[tuple[str, str]] = set()
        self.columns_dropped: set[tuple[str, str]] = set()
        self.indexes_created: dict[str, str] = {}
        self.indexes_dropped: set[str] = set()
        self.constraints_created: dict[str, str] = {}
        self.constraints_dropped: set[str] = set()
        self.executed_sql: list[str] = []

    def get_bind(self):
        return self._bind

    def create_table(self, name, *args, **kwargs):
        self.tables_created[name] = [a.name for a in args if isinstance(a, sa.Column)]

    def drop_table(self, name, **kwargs):
        self.tables_dropped.append(name)

    def add_column(self, table_name, column, **kwargs):
        self.columns_added.add((table_name, column.name))

    def drop_column(self, table_name, column_name, **kwargs):
        self.columns_dropped.add((table_name, column_name))

    def create_index(self, index_name, table_name, columns, **kwargs):
        self.indexes_created[index_name] = table_name

    def drop_index(self, index_name, table_name=None, **kwargs):
        self.indexes_dropped.add(index_name)

    def create_check_constraint(self, name, table_name, condition, **kwargs):
        self.constraints_created[name] = table_name

    def create_unique_constraint(self, name, table_name, columns, **kwargs):
        self.constraints_created[name] = table_name

    def create_foreign_key(self, name, source_table, referent_table, *args, **kwargs):
        self.constraints_created[name] = source_table

    def drop_constraint(self, name, table_name, type_=None, **kwargs):
        self.constraints_dropped.add(name)

    def execute(self, sql, **kwargs):
        self.executed_sql.append(str(sql))


def _run(module, direction: str, dupe_group_count: int = 0) -> RecordingOp:
    recorder = RecordingOp(dupe_group_count)
    module.op = recorder
    getattr(module, direction)()
    return recorder


# ---------------------------------------------------------------------------
# Revision chain
# ---------------------------------------------------------------------------


class TestRevisionChain:
    def test_chain_is_linear_from_eng4query001(self):
        for filename, revision, down_revision in CHAIN:
            module = _load_migration(filename)
            assert module.revision == revision, filename
            assert module.down_revision == down_revision, filename

    def test_catupg006_is_the_single_alembic_head(self):
        revisions: set[str] = set()
        referenced: set[str] = set()
        for path in MIGRATIONS_DIR.glob("*.py"):
            source = path.read_text()
            rev_match = re.search(
                r"^revision(?:\s*:\s*str)?\s*=\s*['\"]([^'\"]+)['\"]", source, re.M
            )
            if not rev_match:
                continue
            revisions.add(rev_match.group(1))
            down_match = re.search(r"^down_revision[^=]*=\s*(.+)$", source, re.M)
            if down_match:
                referenced.update(re.findall(r"['\"]([^'\"]+)['\"]", down_match.group(1)))
        heads = revisions - referenced
        assert heads == {"catupg006"}, f"expected single head catupg006, got {heads}"


# ---------------------------------------------------------------------------
# M1 — unique keys + duplicate abort
# ---------------------------------------------------------------------------


class TestM1UniqueKeys:
    def test_aborts_on_duplicates_pointing_at_report_script(self):
        module = _load_migration(CHAIN[0][0])
        recorder = RecordingOp(dupe_group_count=3)
        module.op = recorder
        with pytest.raises(RuntimeError, match=r"report_catalog_key_dupes\.py"):
            module.upgrade()
        assert not recorder.constraints_created, "must abort before creating constraints"

    def test_clean_database_creates_both_unique_constraints(self):
        module = _load_migration(CHAIN[0][0])
        recorder = _run(module, "upgrade", dupe_group_count=0)
        assert recorder.constraints_created == {
            "uq_scoped_controls_org_scf": "scoped_controls",
            "uq_evidence_tracking_org_evidence": "evidence_tracking",
        }

    def test_precheck_counts_duplicate_key_groups(self):
        module = _load_migration(CHAIN[0][0])
        assert set(module.DUPLICATE_GROUP_COUNT_SQL) == {
            "scoped_controls", "evidence_tracking",
        }
        sc_sql = module.DUPLICATE_GROUP_COUNT_SQL["scoped_controls"]
        assert "GROUP BY organization_id, scf_id" in sc_sql
        assert "HAVING count(*) > 1" in sc_sql
        et_sql = module.DUPLICATE_GROUP_COUNT_SQL["evidence_tracking"]
        assert "GROUP BY organization_id, evidence_id" in et_sql


# ---------------------------------------------------------------------------
# M2 — lifecycle columns
# ---------------------------------------------------------------------------


class TestM2LifecycleColumns:
    def test_adds_three_columns_check_and_index_per_catalog_table(self):
        module = _load_migration(CHAIN[1][0])
        assert tuple(module.CATALOG_TABLES) == CATALOG_TABLES
        recorder = _run(module, "upgrade")
        for table in CATALOG_TABLES:
            for column in ("status", "retired_in_version", "superseded_by"):
                assert (table, column) in recorder.columns_added
            assert recorder.constraints_created[f"ck_{table}_status"] == table
            assert recorder.indexes_created[f"ix_{table}_status"] == table

    def test_status_default_is_active_and_check_allows_deprecated(self):
        source = (MIGRATIONS_DIR / CHAIN[1][0]).read_text()
        assert "server_default='active'" in source
        assert "status IN ('active', 'deprecated')" in source
        assert "nullable=False" in source


# ---------------------------------------------------------------------------
# M3 — org catalog state + framework selections + backfill
# ---------------------------------------------------------------------------


class TestM3OrgState:
    def test_creates_both_tables_with_specified_columns(self):
        module = _load_migration(CHAIN[2][0])
        recorder = _run(module, "upgrade")
        assert set(recorder.tables_created["organization_catalog_state"]) >= {
            "organization_id", "reconciled_catalog_version",
            "last_reconciled_at", "last_reconciliation_run_id",
        }
        assert set(recorder.tables_created["organization_framework_selections"]) >= {
            "id", "organization_id", "framework_id",
            "selected_at", "selected_by", "source", "active",
        }

    def test_backfill_inserts_one_state_row_per_organization(self):
        module = _load_migration(CHAIN[2][0])
        backfill = module.STATE_BACKFILL_SQL
        assert "INSERT INTO organization_catalog_state" in backfill
        # Selects every org, unfiltered: exactly one state row per organisation.
        assert "FROM organizations" in backfill
        assert "WHERE" not in backfill

    def test_backfill_samples_live_version_with_2026_1_fallback(self):
        module = _load_migration(CHAIN[2][0])
        # Same sampling the codebase uses today (api/database_stats.get_catalog_version).
        assert "SELECT catalog_version FROM scf_catalog_controls LIMIT 1" in module.STATE_BACKFILL_SQL
        assert "COALESCE" in module.STATE_BACKFILL_SQL
        assert "'2026.1'" in module.STATE_BACKFILL_SQL

    def test_upgrade_executes_the_backfill(self):
        module = _load_migration(CHAIN[2][0])
        recorder = _run(module, "upgrade")
        assert module.STATE_BACKFILL_SQL in recorder.executed_sql

    def test_last_reconciliation_run_id_has_no_fk_yet(self):
        # The FK is deferred to M5, once organization_reconciliation_runs exists.
        source = (MIGRATIONS_DIR / CHAIN[2][0]).read_text()
        assert "organization_reconciliation_runs.id" not in source


# ---------------------------------------------------------------------------
# M4 / M5 — run ledgers + partial unique indexes
# ---------------------------------------------------------------------------


class TestM4ImportRuns:
    def test_creates_ledger_with_specified_columns(self):
        module = _load_migration(CHAIN[3][0])
        recorder = _run(module, "upgrade")
        assert set(recorder.tables_created["catalog_import_runs"]) >= {
            "id", "from_version", "to_version", "status",
            "workbook_object_key", "diff_detail_object_key",
            "diff_summary", "sanity_report", "superseded_pairings",
            "started_by", "created_at", "updated_at", "completed_at",
        }

    def test_status_check_covers_all_eight_states(self):
        module = _load_migration(CHAIN[3][0])
        assert set(module.STATUSES) == {
            "staging", "staged", "blocked", "applying",
            "applied", "failed", "cancelled", "reverted",
        }
        source = (MIGRATIONS_DIR / CHAIN[3][0]).read_text()
        for status in module.STATUSES:
            assert f"'{status}'" in source

    def test_partial_unique_index_allows_one_in_flight_run(self):
        module = _load_migration(CHAIN[3][0])
        recorder = _run(module, "upgrade")
        assert "uq_catalog_import_runs_in_flight" in recorder.indexes_created
        source = (MIGRATIONS_DIR / CHAIN[3][0]).read_text()
        assert "unique=True" in source
        assert "status IN ('staging', 'staged', 'applying')" in source
        assert "postgresql_where" in source


class TestM5ReconciliationRuns:
    def test_creates_ledger_with_specified_columns(self):
        module = _load_migration(CHAIN[4][0])
        recorder = _run(module, "upgrade")
        assert set(recorder.tables_created["organization_reconciliation_runs"]) >= {
            "id", "organization_id", "from_version", "to_version",
            "catalog_import_run_id", "status", "diff_summary",
            "planned_actions", "org_snapshot", "actions_log",
        }

    def test_status_check_covers_all_seven_states(self):
        module = _load_migration(CHAIN[4][0])
        assert set(module.STATUSES) == {
            "previewed", "applying", "applied", "failed",
            "rolling_back", "rolled_back", "cancelled",
        }

    def test_partial_unique_index_allows_one_active_run_per_org(self):
        module = _load_migration(CHAIN[4][0])
        recorder = _run(module, "upgrade")
        assert recorder.indexes_created["uq_org_reconciliation_runs_active"] == (
            "organization_reconciliation_runs"
        )
        source = (MIGRATIONS_DIR / CHAIN[4][0]).read_text()
        assert "status IN ('previewed', 'applying', 'rolling_back')" in source
        assert "postgresql_where" in source

    def test_adds_the_deferred_fk_from_m3(self):
        module = _load_migration(CHAIN[4][0])
        recorder = _run(module, "upgrade")
        assert recorder.constraints_created["fk_org_catalog_state_last_run"] == (
            "organization_catalog_state"
        )
        downgrade = _run(module, "downgrade")
        assert "fk_org_catalog_state_last_run" in downgrade.constraints_dropped


# ---------------------------------------------------------------------------
# M6 — engagement catalog_version + backfill
# ---------------------------------------------------------------------------


class TestM6EngagementVersion:
    def test_adds_nullable_catalog_version_column(self):
        module = _load_migration(CHAIN[5][0])
        recorder = _run(module, "upgrade")
        assert ("audit_engagements", "catalog_version") in recorder.columns_added
        source = (MIGRATIONS_DIR / CHAIN[5][0]).read_text()
        assert "nullable=True" in source

    def test_backfill_stamps_existing_engagements_with_live_version(self):
        module = _load_migration(CHAIN[5][0])
        backfill = module.ENGAGEMENT_BACKFILL_SQL
        assert "UPDATE audit_engagements" in backfill
        assert "SELECT catalog_version FROM scf_catalog_controls LIMIT 1" in backfill
        assert "COALESCE" in backfill
        assert "'2026.1'" in backfill
        assert "WHERE catalog_version IS NULL" in backfill
        recorder = _run(module, "upgrade")
        assert backfill in recorder.executed_sql


# ---------------------------------------------------------------------------
# Up/down round-trip symmetry (all six)
# ---------------------------------------------------------------------------


class TestUpDownRoundTrip:
    @pytest.mark.parametrize("filename", [entry[0] for entry in CHAIN])
    def test_downgrade_mirrors_upgrade(self, filename):
        module = _load_migration(filename)
        up = _run(module, "upgrade")
        down = _run(module, "downgrade")

        assert set(up.tables_created) == set(down.tables_dropped)
        assert up.columns_added == down.columns_dropped

        dropped_tables = set(down.tables_dropped)
        # Indexes on surviving tables must be dropped explicitly; indexes on
        # dropped tables go down with the table.
        surviving_indexes = {
            name for name, table in up.indexes_created.items()
            if table not in dropped_tables
        }
        assert surviving_indexes <= down.indexes_dropped
        assert down.indexes_dropped <= set(up.indexes_created)

        # Same rule for constraints created outside create_table.
        surviving_constraints = {
            name for name, table in up.constraints_created.items()
            if table not in dropped_tables
        }
        assert surviving_constraints == down.constraints_dropped


# ---------------------------------------------------------------------------
# ORM parity
# ---------------------------------------------------------------------------


class TestModelParity:
    def test_catalog_models_expose_lifecycle_columns(self):
        import catalog_models

        for cls in (
            catalog_models.SCFCatalogControl,
            catalog_models.SCFCatalogEvidence,
            catalog_models.SCFCatalogAssessmentObjective,
            catalog_models.SCFCatalogDomain,
        ):
            for column in ("status", "retired_in_version", "superseded_by"):
                assert hasattr(cls, column), f"{cls.__name__}.{column} missing"

    def test_new_ledger_models_exist_with_expected_tables(self):
        import models

        assert models.OrganizationCatalogState.__tablename__ == "organization_catalog_state"
        assert models.OrganizationFrameworkSelection.__tablename__ == (
            "organization_framework_selections"
        )
        assert models.CatalogImportRun.__tablename__ == "catalog_import_runs"
        assert models.OrganizationReconciliationRun.__tablename__ == (
            "organization_reconciliation_runs"
        )

    def test_audit_engagement_has_catalog_version(self):
        import models

        assert hasattr(models.AuditEngagement, "catalog_version")


# ---------------------------------------------------------------------------
# Dupe report script
# ---------------------------------------------------------------------------


class TestDupeReportScript:
    SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "report_catalog_key_dupes.py"

    def test_script_exists_and_compiles(self):
        assert self.SCRIPT.exists()
        compile(self.SCRIPT.read_text(), str(self.SCRIPT), "exec")

    def _queries(self):
        spec = importlib.util.spec_from_file_location("report_catalog_key_dupes", self.SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.DUPLICATE_KEY_QUERIES

    def test_script_queries_are_read_only_selects(self):
        for label, query in self._queries().items():
            statement = query.strip().upper()
            assert statement.startswith("SELECT"), label
            for verb in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE"):
                assert verb not in statement, f"{label}: report script must be read-only"

    def test_script_reports_both_key_pairs(self):
        queries = "\n".join(self._queries().values())
        assert "GROUP BY organization_id, scf_id" in queries
        assert "GROUP BY organization_id, evidence_id" in queries
