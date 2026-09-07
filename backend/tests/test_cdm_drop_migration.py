"""Offline tests for the CDM retirement data step (#907).

Covers migration ``cdmdrop001`` (behaviour against a recording ``op`` and a
fake connection: ack refusal, ack pass, zero-row pass, drop order, downgrade
order), the purge script's safety defaults and driver logic for both
backends, the probe's pure helpers, the model/registry removals and the ack
plumbing. The real upgrade → refuse → ack → drop → downgrade → upgrade
round-trip against PostgreSQL is run in a throwaway container in the PR
verification.

The migration loader and the recording ``op`` come from
``test_catalog_upgrade_migrations`` (the repo's offline-migration harness);
only the per-table ``count(*)`` answering is specific to these tests.
"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

import pytest

from test_catalog_upgrade_migrations import RecordingOp, _load_migration

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

MIGRATIONS_DIR = BACKEND / "alembic" / "versions"
MIGRATION_FILENAME = "20260910_120000_drop_cdm_tables.py"
PURGE_FILE = BACKEND / "scripts" / "cdm_retirement_purge.py"
PROBE_FILE = BACKEND / "scripts" / "cdm_retirement_probe.py"

CDM_TABLES = {
    "cdm_documents",
    "cdm_document_chunks",
    "cdm_document_intents",
    "cdm_control_proposals",
    "cdm_mappings",
}


def _load_script(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fakes: only the connection is CDM-specific (per-table row counts)
# ---------------------------------------------------------------------------
class _Result:
    def __init__(self, scalar: Any = None, rows: Optional[List[Tuple]] = None):
        self._scalar = scalar
        self._rows = rows or []

    def scalar(self):
        return self._scalar

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    """Answers the migration's SELECTs from a dict of row counts; a table
    absent from the dict does not exist."""

    def __init__(self, counts: Dict[str, int], docs: Optional[List[Tuple[str, str]]] = None):
        self.counts = counts
        self.docs = docs or []
        self.statements: List[str] = []

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.statements.append(sql)
        if "to_regclass" in sql:
            table = (params or {}).get("t") or re.search(r"to_regclass\('(\w+)'\)", sql).group(1)
            return _Result(scalar=table in self.counts)
        m = re.search(r"SELECT count\(\*\) FROM (\w+)", sql)
        if m:
            return _Result(scalar=self.counts.get(m.group(1), 0))
        if "SELECT organization_id, id FROM cdm_documents" in sql:
            return _Result(rows=self.docs)
        raise AssertionError(f"unexpected query in upgrade(): {sql}")


class CdmOp(RecordingOp):
    """The shared recording ``op`` with a per-table-count connection."""

    def __init__(self, conn: FakeConn):
        super().__init__()
        self._bind = conn
        self.conn = conn


@pytest.fixture()
def migration(monkeypatch):
    mod = _load_migration(MIGRATION_FILENAME)
    monkeypatch.delenv(mod.ACK_ENV, raising=False)
    return mod


def _wire(monkeypatch, mod, counts, docs=None) -> CdmOp:
    fake = CdmOp(FakeConn(counts, docs))
    monkeypatch.setattr(mod, "op", fake)
    return fake


def _drops(op: CdmOp) -> List[str]:
    return [s for s in op.executed_sql if s.startswith("DROP TABLE")]


# ---------------------------------------------------------------------------
# Chain shape
# ---------------------------------------------------------------------------
def test_revision_chain(migration):
    assert migration.revision == "cdmdrop001"
    assert migration.down_revision == "evassessver1"


def test_single_head_after_this_migration():
    """No other migration may also revise ``evassessver1`` (that would fork the chain)."""
    parents = []
    for path in MIGRATIONS_DIR.glob("*.py"):
        src = path.read_text()
        # both styles in this repo: `down_revision = ...` and `down_revision: Union[str, None] = ...`
        for m in re.finditer(r"^down_revision(?:\s*:[^=\n]*)?\s*=\s*(.+)$", src, re.M):
            if "evassessver1" in m.group(1):
                parents.append(path.name)
    assert parents == [MIGRATION_FILENAME], parents


def test_fork_regex_sees_every_migration_file():
    """Guard for the guard: the regex above must match each file's down_revision line."""
    pattern = re.compile(r"^down_revision(?:\s*:[^=\n]*)?\s*=\s*(.+)$", re.M)
    unmatched = [p.name for p in MIGRATIONS_DIR.glob("*.py") if not pattern.search(p.read_text())]
    assert unmatched == [], unmatched


def test_docstring_carries_operator_facts(migration):
    doc = migration.__doc__
    for needle in ("SCF_CDM_DROP_ACK", "cdm_retirement_purge.py", "upgrade.sh --rollback",
                   "Downgrade restores an empty schema only"):
        assert needle in doc, needle


# ---------------------------------------------------------------------------
# upgrade()
# ---------------------------------------------------------------------------
def test_refuses_with_rows_and_no_ack(migration, monkeypatch):
    fake = _wire(monkeypatch, migration, {t: 0 for t in CDM_TABLES} | {"cdm_documents": 19, "cdm_mappings": 191})
    with pytest.raises(RuntimeError) as exc:
        migration.upgrade()
    msg = str(exc.value)
    assert "SCF_CDM_DROP_ACK" in msg
    assert "cdm_documents: 19" in msg and "cdm_mappings: 191" in msg
    assert "upgrade.sh" in msg and "backup" in msg
    assert "rolls the whole upgrade back" in msg, "operator must learn the refusal triggers upgrade.sh's rollback"
    assert fake.executed_sql == [], "nothing may be dropped or updated on refusal"


def test_refuses_when_ack_has_wrong_value(migration, monkeypatch):
    monkeypatch.setenv(migration.ACK_ENV, "yes")
    fake = _wire(monkeypatch, migration, {t: 1 for t in CDM_TABLES})
    with pytest.raises(RuntimeError):
        migration.upgrade()
    assert fake.executed_sql == []


def test_drops_in_fk_order_with_ack(migration, monkeypatch):
    monkeypatch.setenv(migration.ACK_ENV, "1")
    docs = [("org-a", "doc-1"), ("org-a", "doc-2")]
    fake = _wire(monkeypatch, migration, {t: 3 for t in CDM_TABLES}, docs)
    migration.upgrade()
    drops = _drops(fake)
    assert drops == [f"DROP TABLE IF EXISTS {t}" for t in migration.TABLES_IN_DROP_ORDER]
    # children before parents: mappings first, documents last
    assert drops[0].endswith("cdm_mappings") and drops[-1].endswith("cdm_documents")
    assert "CASCADE" not in " ".join(drops)
    update = [s for s in fake.executed_sql if s.startswith("UPDATE organizations")]
    assert len(update) == 1
    # organizations.settings is json, not jsonb — the key operators need the cast
    assert "settings::jsonb - 'cdm_enabled'" in update[0]
    assert "::json" in update[0].split("- 'cdm_enabled')")[1]
    assert "jsonb_typeof(settings::jsonb) = 'object'" in update[0], "non-object settings must be skipped, not fatal"
    assert fake.executed_sql.index(update[0]) > fake.executed_sql.index(drops[-1])


def test_manifest_lists_one_prefix_per_document(migration, monkeypatch, caplog):
    monkeypatch.setenv(migration.ACK_ENV, "1")
    docs = [("org-a", "doc-1"), ("org-b", "doc-9")]
    _wire(monkeypatch, migration, {t: 1 for t in CDM_TABLES}, docs)
    assert migration._manifest(FakeConn({t: 1 for t in CDM_TABLES}, docs)) == [
        "cdm/org-a/doc-1/", "cdm/org-b/doc-9/",
    ]
    with caplog.at_level("INFO", logger="alembic.runtime.migration"):
        migration.upgrade()
    text = caplog.text
    assert "cdm/org-a/doc-1/" in text and "cdm/org-b/doc-9/" in text
    assert "cdm_retirement_purge.py" in text


def test_zero_rows_needs_no_ack(migration, monkeypatch):
    fake = _wire(monkeypatch, migration, {t: 0 for t in CDM_TABLES})
    migration.upgrade()
    assert len(_drops(fake)) == 5


def test_missing_tables_count_as_zero(migration, monkeypatch):
    """A database where the tables are already gone (re-run after a partial
    failure) must not crash on ``count(*)``; ``to_regclass`` guards it."""
    fake = _wire(monkeypatch, migration, {})  # no cdm table exists
    migration.upgrade()
    assert all("count(*)" not in s for s in fake.conn.statements)
    assert len(_drops(fake)) == 5


# ---------------------------------------------------------------------------
# downgrade()
# ---------------------------------------------------------------------------
def test_downgrade_recreates_all_five_parents_first(migration, monkeypatch):
    fake = _wire(monkeypatch, migration, {})
    migration.downgrade()
    assert list(fake.tables_created) == [
        "cdm_documents", "cdm_document_chunks", "cdm_document_intents",
        "cdm_control_proposals", "cdm_mappings",
    ]
    assert list(fake.tables_created) == list(reversed(migration.TABLES_IN_DROP_ORDER))
    # the three named FKs the historical migrations added to cdm_mappings
    fks = {name for name in fake.constraints_created if name.startswith("fk_")}
    assert fks == {
        "fk_cdm_mappings_last_reviewed_by_user_id_users",
        "fk_cdm_mappings_chunk",
        "fk_cdm_mappings_control_proposal",
    }
    assert fake.indexes_created["ix_cdm_chunks_search_vector"] == "cdm_document_chunks"


# ---------------------------------------------------------------------------
# Purge script
# ---------------------------------------------------------------------------
class _FakePaginator:
    def __init__(self, keys):
        self.keys = keys

    def paginate(self, Bucket, Prefix):
        assert Prefix == "cdm/"
        matching = [k for k in self.keys if k.startswith(Prefix)]
        yield {"Contents": [{"Key": k} for k in matching]}


class FakeS3:
    def __init__(self, keys):
        self.keys = list(keys)
        self.deleted: List[str] = []

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return _FakePaginator(self.keys)

    def get_bucket_versioning(self, Bucket):
        return {"Status": "Suspended"}

    def delete_objects(self, Bucket, Delete):
        for obj in Delete["Objects"]:
            self.deleted.append(obj["Key"])
            self.keys.remove(obj["Key"])
        return {}


class _Blob:
    def __init__(self, name):
        self.name = name


class FakeContainer:
    container_name = "evidence"

    def __init__(self, names):
        self.names = list(names)
        self.deleted: List[str] = []

    def list_blobs(self, name_starts_with):
        return [_Blob(n) for n in self.names if n.startswith(name_starts_with)]

    def delete_blob(self, name):
        self.deleted.append(name)
        self.names.remove(name)


SEED = ["cdm/o/d/f.pdf", "cdm/o/d2/g.docx", "cdmx/decoy", "evidence/keep"]


@pytest.fixture()
def purge():
    return _load_script(PURGE_FILE, "cdm_retirement_purge_under_test")


@pytest.fixture()
def s3(purge, monkeypatch):
    from services import s3_service

    client = FakeS3(SEED)
    monkeypatch.setattr(purge.storage_service, "get_backend", lambda: "s3")
    monkeypatch.setattr(s3_service, "_get_s3_client", lambda: client)
    monkeypatch.setattr(s3_service, "EVIDENCE_BUCKET", "evidence")
    return client


@pytest.fixture()
def azure(purge, monkeypatch):
    from services import azure_blob_service

    container = FakeContainer(SEED)
    monkeypatch.setattr(purge.storage_service, "get_backend", lambda: "azure")
    monkeypatch.setattr(azure_blob_service, "_get_container_client", lambda: container)
    monkeypatch.setattr(azure_blob_service, "_get_blob_service_client", lambda: None)  # retention → "unknown"
    return container


def test_purge_prefix_is_fixed(purge):
    assert purge.CDM_PREFIX == "cdm/"
    assert purge.S3_DELETE_BATCH == 1000


def test_purge_backend_none_exits_zero(purge, monkeypatch):
    monkeypatch.setattr(purge.storage_service, "get_backend", lambda: "none")
    assert purge.main([]) == 0
    assert purge.main(["--apply"]) == 0


def test_purge_dry_run_is_default_and_deletes_nothing(purge, s3):
    assert purge.main([]) == 0
    assert purge.main(["--dry-run"]) == 0
    assert s3.deleted == []


def test_purge_apply_removes_only_cdm_prefix_and_is_idempotent(purge, s3):
    assert purge.main(["--apply"]) == 0
    assert sorted(s3.deleted) == ["cdm/o/d/f.pdf", "cdm/o/d2/g.docx"]
    assert sorted(s3.keys) == ["cdmx/decoy", "evidence/keep"]
    assert purge.main(["--apply"]) == 0  # second run finds nothing


def test_purge_batches_at_api_limit(purge, s3):
    s3.keys = [f"cdm/o/d/{i}.bin" for i in range(2500)]
    calls: List[int] = []
    orig = s3.delete_objects

    def counting(Bucket, Delete):
        calls.append(len(Delete["Objects"]))
        return orig(Bucket=Bucket, Delete=Delete)

    s3.delete_objects = counting
    assert purge.main(["--apply"]) == 0
    assert calls == [1000, 1000, 500]


def test_purge_azure_driver_same_contract(purge, azure):
    """The Azure adapter shares the driver, so dry-run gate, prefix scoping,
    idempotency and exit code are the tested S3 behaviour, not a copy."""
    assert purge.main([]) == 0
    assert azure.deleted == []
    assert purge.main(["--apply"]) == 0
    assert sorted(azure.deleted) == ["cdm/o/d/f.pdf", "cdm/o/d2/g.docx"]
    assert sorted(azure.names) == ["cdmx/decoy", "evidence/keep"]
    assert purge.main(["--apply"]) == 0


def test_purge_exit_1_when_objects_remain(purge, s3):
    def failing(Bucket, Delete):
        return {"Errors": [{"Key": o["Key"], "Code": "AccessDenied", "Message": "nope"} for o in Delete["Objects"]]}

    s3.delete_objects = failing
    assert purge.main(["--apply"]) == 1


def test_purge_flags_are_exclusive(purge):
    with pytest.raises(SystemExit):
        purge.main(["--dry-run", "--apply"])


# ---------------------------------------------------------------------------
# Probe script — pure helpers only (the DB path runs in the throwaway acceptance)
# ---------------------------------------------------------------------------
def test_probe_effective_flag_matches_pre_retirement_semantics(monkeypatch):
    mod = _load_script(PROBE_FILE, "cdm_retirement_probe_under_test")
    monkeypatch.delenv("ENABLE_CDM", raising=False)
    assert mod._effective(None) is False          # env default false
    assert mod._effective(True) is True           # tenant JSON boolean wins
    assert mod._effective(False) is False
    monkeypatch.setenv("ENABLE_CDM", "true")
    assert mod._effective(None) is True
    assert mod._effective(False) is False         # tenant override still wins
    src = PROBE_FILE.read_text()
    assert not re.search(r"\b(UPDATE|DELETE|INSERT|DROP|ALTER|TRUNCATE)\b\s", src), "probe must be SELECT-only"
    # only a JSON boolean is an override: the SQL narrows on jsonb_typeof before casting
    assert "jsonb_typeof(settings::jsonb -> 'cdm_enabled') = 'boolean'" in src


def test_probe_dump_omits_derived_chunk_columns():
    mod = _load_script(PROBE_FILE, "cdm_retirement_probe_under_test2")
    assert mod.DUMP_SKIP_COLUMNS["cdm_document_chunks"] == {"search_vector", "body_norm"}


# ---------------------------------------------------------------------------
# Removals
# ---------------------------------------------------------------------------
def test_models_no_longer_declare_cdm():
    import models

    for name in ("CDMDocument", "CDMDocumentChunk", "CDMDocumentIntent", "CDMControlProposal", "CDMMapping"):
        assert not hasattr(models, name), name
    assert not (set(models.Base.metadata.tables) & CDM_TABLES)


def test_database_stats_excluded_tables_no_longer_name_cdm():
    from api import database_stats

    assert not (set(database_stats.TENANT_SCOPED_EXCLUDED_TABLES) & CDM_TABLES)


# ---------------------------------------------------------------------------
# Ack plumbing — mirrors tests/test_assurance_flags_reach_containers.py:
# docker-compose.yml is an explicit allow-list with no env_file, so a variable
# missing from a service block is silently ignored there.
# ---------------------------------------------------------------------------
REPO_ROOT = BACKEND.parent
ACK = "SCF_CDM_DROP_ACK"


@pytest.fixture(scope="module")
def compose() -> dict:
    yaml = pytest.importorskip("yaml")
    path = REPO_ROOT / "docker-compose.yml"
    if not path.exists():
        pytest.skip("docker-compose.yml not present at the repo root (container run mounts backend/ only)")
    return yaml.safe_load(path.read_text())


@pytest.mark.parametrize("service", ["backend", "celery-worker"])
def test_ack_is_forwarded_to_the_service(compose, service):
    environment = compose["services"][service]["environment"]
    assert ACK in environment, f"{ACK} is not forwarded to {service}; setting it in .env would do nothing there"
    assert environment[ACK] == "${SCF_CDM_DROP_ACK:-}", "unset must mean 'no ack', never a default"


def test_ack_is_documented_as_a_commented_assignment():
    path = REPO_ROOT / ".env.example"
    if not path.exists():
        pytest.skip(".env.example not present at the repo root")
    text = path.read_text()
    assert f"# {ACK}=" in text
    assert f"\n{ACK}=" not in text, "must stay commented: an uncommented key would enter the release env_added delta"
