"""Phase 1 of bring-your-own evidence storage: the configuration model.

ISA 20260912-0930, criteria 9 to 15.

These tests need no database. The schema claims are checked against the
migration's own emitted operations — the migration module is executed with a
recording stand-in for ``alembic.op`` — and against the ORM metadata, which is
what every query is built from. The migration was additionally run for real
against the development stack; that is recorded in the phase handoff note, not
here, because a test that silently skips without a database proves nothing.

The resolver tests build their own
:class:`~services.storage_config.StorageConfigResolver` instances rather than
reaching into the shared one, because the thing worth proving about a two-second
convergence window is that *two separate resolvers* agree, which module state
cannot demonstrate.
"""
from __future__ import annotations

import importlib.util
import pathlib
import uuid

import pytest
from cryptography.fernet import Fernet

from services import storage_config
from services.storage_config import (
    SOURCE_LEGACY_ENV,
    SOURCE_ORG,
    SOURCE_PLATFORM,
    SSE_AES256,
    SSE_NONE,
    StorageConfigError,
    StorageConfigResolver,
    StoredConfigRow,
)

BACKEND_ROOT = pathlib.Path(__file__).resolve().parent.parent
MIGRATION_PATH = (
    BACKEND_ROOT / "alembic" / "versions" / "20260912_090000_evidence_storage_configs.py"
)

KEY_A = Fernet.generate_key().decode()
KEY_B = Fernet.generate_key().decode()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row(**overrides) -> StoredConfigRow:
    base = dict(
        config_id=str(uuid.uuid4()),
        organization_id=None,
        provider="s3_compatible",
        bucket="row-bucket",
        region="eu-west-2",
        endpoint_url="https://objects.example.net",
        public_endpoint="",
        path_style=True,
        sse_mode=SSE_NONE,
        access_key_id="AKIAROW",
        secret_access_key="row-secret",
        key_version="1",
        is_bundled=False,
    )
    base.update(overrides)
    return StoredConfigRow(**base)


class _RecordingOp:
    """Stand-in for ``alembic.op`` that records what a migration asked for."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def _record(*args, **kwargs):
            self.calls.append((name, args, kwargs))

        return _record

    def of(self, name):
        return [c for c in self.calls if c[0] == name]

    @property
    def sql(self):
        return "\n".join(str(a) for _, args, _ in self.of("execute") for a in args)


def _run_migration(direction: str) -> _RecordingOp:
    spec = importlib.util.spec_from_file_location("_phase1_migration", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    recorder = _RecordingOp()
    module.op = recorder
    getattr(module, direction)()
    return recorder


def _migration_module():
    spec = importlib.util.spec_from_file_location("_phase1_migration_meta", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeRedis:
    """Minimal stand-in for the sync redis client behind the version key.

    Shared between two resolvers, which is exactly what the real key is: one
    value two processes can both see.
    """

    def __init__(self, version=b"1"):
        self.version = version
        self.gets = 0
        self.incrs = 0

    def get(self, key):
        assert key == storage_config.REDIS_VERSION_KEY
        self.gets += 1
        return self.version

    def incr(self, key):
        assert key == storage_config.REDIS_VERSION_KEY
        self.incrs += 1
        self.version = str(int(self.version) + 1).encode()
        return int(self.version)


# ---------------------------------------------------------------------------
# Criterion 9 — the table, with a nullable organisation
# ---------------------------------------------------------------------------


def test_the_migration_chains_onto_the_current_head():
    module = _migration_module()
    assert module.revision == "evstorcfg1a1"
    assert module.down_revision == "intsec947a1"


def test_the_migration_creates_the_table_with_a_nullable_organisation():
    """Criterion 9. Null organisation is the platform scope, so the column has
    to be nullable — a NOT NULL column would leave the catalogue workbook and
    the reconciliation blobs with nothing to resolve."""
    recorder = _run_migration("upgrade")
    creates = recorder.of("create_table")
    assert len(creates) == 1
    name, args, _ = creates[0]
    assert args[0] == "evidence_storage_configs"

    columns = {c.name: c for c in args[1:] if hasattr(c, "name")}
    assert columns["organization_id"].nullable is True

    for required in (
        "id",
        "organization_id",
        "provider",
        "bucket",
        "region",
        "endpoint_url",
        "path_style",
        "sse_mode",
        "access_key_id",
        "secret_ciphertext",
        "key_version",
        "status",
        "is_bundled",
        "created_at",
        "updated_at",
        "updated_by_user_id",
        "updated_by_label",
    ):
        assert required in columns, f"missing column {required}"


def test_the_orm_model_agrees_with_the_migration():
    """The query layer and the schema must not disagree about nullability."""
    import catalog_models  # noqa: F401 — mappers cannot configure without it
    from models import EvidenceStorageConfig

    table = EvidenceStorageConfig.__table__
    assert table.name == "evidence_storage_configs"
    assert table.columns["organization_id"].nullable is True
    assert table.columns["bucket"].nullable is False
    assert table.columns["secret_ciphertext"].nullable is True


def test_the_migration_is_reversible():
    recorder = _run_migration("downgrade")
    dropped_tables = [args[0] for _, args, _ in recorder.of("drop_table")]
    assert dropped_tables == ["evidence_storage_configs"]
    assert "uq_evidence_storage_configs_active_platform" in recorder.sql
    assert "uq_evidence_storage_configs_active_org" in recorder.sql
    dropped_columns = [args for _, args, _ in recorder.of("drop_column")]
    assert ("evidence_files", "storage_config_id") in dropped_columns


# ---------------------------------------------------------------------------
# Criterion 10 — one active config per scope
# ---------------------------------------------------------------------------


def test_one_active_config_per_organisation_is_a_partial_unique_index():
    """Criterion 10, org half."""
    sql = _run_migration("upgrade").sql
    assert "CREATE UNIQUE INDEX uq_evidence_storage_configs_active_org" in sql
    assert "ON evidence_storage_configs (organization_id)" in sql
    assert "WHERE status = 'active' AND organization_id IS NOT NULL" in sql


def test_one_active_platform_config_needs_an_expression_index():
    """Criterion 10, platform half — and the reason it is not the same index.

    Postgres treats NULLs as distinct, so a unique index on `organization_id`
    constrains nothing at all for platform rows. The index is therefore over
    the constant expression `(organization_id IS NULL)`, restricted to rows the
    predicate admits; for every such row the expression is `true`, so at most
    one of them can exist.
    """
    sql = _run_migration("upgrade").sql
    assert "CREATE UNIQUE INDEX uq_evidence_storage_configs_active_platform" in sql
    assert "((organization_id IS NULL))" in sql
    assert "WHERE status = 'active' AND organization_id IS NULL" in sql


def test_draft_and_retired_rows_are_not_constrained():
    """Both predicates name `active` explicitly, which is what lets a
    replacement config be created and tested next to the live one."""
    sql = _run_migration("upgrade").sql
    assert sql.count("CREATE UNIQUE INDEX") == 2
    assert sql.count("WHERE status = 'active'") == 2
    assert "draft" not in sql and "retired" not in sql


# ---------------------------------------------------------------------------
# Criterion 11 — EvidenceFile.storage_config_id
# ---------------------------------------------------------------------------


def test_evidence_file_carries_a_nullable_storage_config_id():
    """Criterion 11. Null means legacy: resolve via the organisation, then the
    environment. Set means authoritative."""
    import catalog_models  # noqa: F401
    from models import EvidenceFile

    column = EvidenceFile.__table__.columns["storage_config_id"]
    assert column.nullable is True
    target = list(column.foreign_keys)[0].target_fullname
    assert target == "evidence_storage_configs.id"


def test_the_file_foreign_key_restricts_rather_than_nulling():
    """A config a file still points at must not be deletable. SET NULL would
    silently re-point those bytes at whatever the organisation uses now, which
    is a different store."""
    recorder = _run_migration("upgrade")
    fks = recorder.of("create_foreign_key")
    assert any(kwargs.get("ondelete") == "RESTRICT" for _, _, kwargs in fks)
    added = [args for _, args, _ in recorder.of("add_column")]
    assert any(a[0] == "evidence_files" for a in added)


# ---------------------------------------------------------------------------
# Criterion 12 — secrets encrypted with the existing MultiFernet helper
# ---------------------------------------------------------------------------


@pytest.fixture
def _key(monkeypatch):
    from services import crypto

    monkeypatch.delenv("SCF_SECRET_KEY_FILE", raising=False)
    monkeypatch.setenv("SCF_SECRET_KEY", KEY_A)
    crypto.reset()
    yield
    crypto.reset()


def _record(ciphertext, **overrides):
    """A database record tuple in the order `load_active_rows` selects."""
    base = [
        uuid.uuid4(),          # id
        None,                  # organization_id
        "s3_compatible",       # provider
        "enc-bucket",          # bucket
        "eu-west-1",           # region
        "https://objects.example.net",
        "",                    # public_endpoint
        True,                  # path_style
        SSE_NONE,
        "AKIAENC",
        ciphertext,
        1,                     # key_version
        False,                 # is_bundled
    ]
    for index, value in overrides.items():
        base[index] = value
    return tuple(base)


def test_a_stored_secret_round_trips_through_the_existing_helper(_key):
    """Criterion 12. The same `services.crypto` MultiFernet helper that
    `IntegrationSecret` uses — not a second encryption scheme."""
    from services import crypto

    ciphertext = crypto.encrypt("row-secret")
    assert ciphertext.startswith(crypto.PREFIX)

    row = storage_config._row_from_record(_record(ciphertext))
    assert row.secret_access_key == "row-secret"
    assert row.secret_undecryptable is False


def test_a_secret_written_under_a_rotated_key_still_decrypts(monkeypatch):
    """MultiFernet's whole point: the old key stays in the list as
    decrypt-only, so a rotation is not a flag day."""
    from services import crypto

    monkeypatch.setenv("SCF_SECRET_KEY", KEY_A)
    crypto.reset()
    ciphertext = crypto.encrypt("written-under-a")

    monkeypatch.setenv("SCF_SECRET_KEY", f"{KEY_B},{KEY_A}")
    crypto.reset()
    row = storage_config._row_from_record(_record(ciphertext))
    assert row.secret_access_key == "written-under-a"


def test_an_undecryptable_secret_refuses_rather_than_falling_back(monkeypatch):
    """The configuration plainly exists. Silently resolving to the platform or
    legacy store would write that organisation's evidence into somebody else's
    bucket, which is worse than an error."""
    from services import crypto

    monkeypatch.setenv("SCF_SECRET_KEY", KEY_A)
    crypto.reset()
    ciphertext = crypto.encrypt("unreadable")

    monkeypatch.setenv("SCF_SECRET_KEY", KEY_B)
    crypto.reset()
    row = storage_config._row_from_record(_record(ciphertext))
    assert row.secret_undecryptable is True
    assert row.secret_access_key == ""

    with pytest.raises(StorageConfigError):
        storage_config.config_from_row(row)


def test_one_undecryptable_row_does_not_break_the_others(monkeypatch):
    good = _row(organization_id="org-good")
    bad = _row(organization_id="org-bad", secret_undecryptable=True)
    resolver = StorageConfigResolver(loader=lambda: [good, bad])

    assert resolver.resolve("org-good").bucket == "row-bucket"
    with pytest.raises(StorageConfigError):
        resolver.resolve("org-bad")


def test_the_resolved_config_keeps_the_secret_out_of_its_repr():
    config = storage_config.config_from_row(_row(secret_access_key="top-secret"))
    assert "top-secret" not in repr(config)


# ---------------------------------------------------------------------------
# Criteria 13 and 14 — resolution order, and platform scope
# ---------------------------------------------------------------------------


def test_an_organisation_row_wins(monkeypatch):
    """Criterion 13, first step."""
    monkeypatch.setenv("EVIDENCE_BUCKET", "env-bucket")
    resolver = StorageConfigResolver(
        loader=lambda: [
            _row(organization_id=None, bucket="platform-bucket"),
            _row(organization_id="org-1", bucket="org-bucket"),
        ]
    )
    config = resolver.resolve("org-1")
    assert config.bucket == "org-bucket"
    assert config.source == SOURCE_ORG
    assert config.organization_id == "org-1"


def test_the_platform_row_is_next(monkeypatch):
    """Criterion 13, second step. An organisation with no store of its own
    keeps using whatever the platform uses — which is what makes this phase
    invisible to an existing installation."""
    monkeypatch.setenv("EVIDENCE_BUCKET", "env-bucket")
    resolver = StorageConfigResolver(
        loader=lambda: [_row(organization_id=None, bucket="platform-bucket")]
    )
    config = resolver.resolve("org-unconfigured")
    assert config.bucket == "platform-bucket"
    assert config.source == SOURCE_PLATFORM
    # The organisation is still on the value object: it is the key prefix and
    # the object metadata tag, and losing it would mis-tag every write.
    assert config.organization_id == "org-unconfigured"


def test_the_environment_is_the_last_resort(monkeypatch):
    """Criterion 13, third step. An operator who has never opened the Settings
    screen must keep working unchanged."""
    monkeypatch.setenv("EVIDENCE_BUCKET", "env-bucket")
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://minio:9000")
    resolver = StorageConfigResolver(loader=lambda: [])
    config = resolver.resolve("org-1")
    assert config.bucket == "env-bucket"
    assert config.source == SOURCE_LEGACY_ENV
    assert config.config_id == storage_config.LEGACY_ENV_CONFIG_ID


def test_platform_scope_resolves_without_an_organisation(monkeypatch):
    """Criterion 14. The catalogue workbook and reconciliation blobs have no
    tenant, and must not be forced to invent one."""
    monkeypatch.setenv("EVIDENCE_BUCKET", "env-bucket")
    resolver = StorageConfigResolver(
        loader=lambda: [
            _row(organization_id=None, bucket="platform-bucket"),
            _row(organization_id="org-1", bucket="org-bucket"),
        ]
    )
    config = resolver.resolve_platform()
    assert config.bucket == "platform-bucket"
    assert config.organization_id is None
    assert config.source == SOURCE_PLATFORM


def test_platform_scope_falls_back_to_the_environment_too(monkeypatch):
    monkeypatch.setenv("EVIDENCE_BUCKET", "env-bucket")
    resolver = StorageConfigResolver(loader=lambda: [_row(organization_id="org-1")])
    assert resolver.resolve_platform().bucket == "env-bucket"


def test_an_unreadable_database_resolves_from_the_environment(monkeypatch):
    """Every installation has zero rows until Phase 4 seeds one, and a
    pre-migration database has no table at all. Neither may stop the platform
    serving evidence."""
    monkeypatch.setenv("EVIDENCE_BUCKET", "env-bucket")

    def boom():
        raise RuntimeError("relation does not exist")

    resolver = StorageConfigResolver(loader=boom)
    assert resolver.resolve("org-1").bucket == "env-bucket"
    assert resolver.resolve_platform().bucket == "env-bucket"


def test_a_load_failure_is_not_retried_on_every_operation(monkeypatch):
    """One outage must not become a connection storm: `resolve` is called on
    every single storage operation."""
    calls = []

    def boom():
        calls.append(1)
        raise RuntimeError("database is starting")

    resolver = StorageConfigResolver(loader=boom)
    for _ in range(10):
        resolver.resolve("org-1")
    assert len(calls) == 1


def test_only_active_rows_are_selected():
    """Draft and retired rows exist precisely so they are not resolved."""
    assert "status = 'active'" in storage_config._SELECT_ACTIVE_CONFIGS


# ---------------------------------------------------------------------------
# Endpoint safety carried over from Phase 0
# ---------------------------------------------------------------------------


def test_a_bundled_row_is_operator_supplied_and_passes():
    """The installer's own MinIO is not a tenant typing a URL into a form, so
    it goes through — which is what keeps the platform working after this
    phase."""
    config = storage_config.config_from_row(
        _row(is_bundled=True, endpoint_url="http://minio:9000", provider="minio")
    )
    assert config.endpoint_is_operator_supplied is True
    storage_config.assert_endpoint_allowed(config)  # must not raise


def test_a_row_an_administrator_typed_is_not_operator_supplied():
    """The flag Phase 2's address guard keys off.

    ``is_bundled`` is what separates the installer's own store from an endpoint
    a tenant administrator typed, and only the former gets the narrow operator
    exemption. Updated in Phase 2: this test used to assert that *any* tenant
    row was refused, which was the correct interim state while the address
    checks were a stub. Now the refusal depends on the address, and the cases
    live in ``test_storage_phase2_driver_and_test.py``. What Phase 1 still owns
    is the claim that the flag does not default to the permissive end.
    """
    config = storage_config.config_from_row(
        _row(is_bundled=False, endpoint_url="https://objects.example.net")
    )
    assert config.endpoint_is_operator_supplied is False

    bundled = storage_config.config_from_row(
        _row(is_bundled=True, endpoint_url="http://minio:9000")
    )
    assert bundled.endpoint_is_operator_supplied is True

    # A tenant row naming a private address is refused; the same row naming a
    # public one is not. Both halves, so this cannot pass by refusing
    # everything.
    storage_config.use_address_resolver(lambda host: ["10.1.2.3"])
    try:
        with pytest.raises(StorageConfigError):
            storage_config.assert_endpoint_allowed(config)
        storage_config.use_address_resolver(lambda host: ["93.184.216.34"])
        storage_config.assert_endpoint_allowed(config)
    finally:
        storage_config.use_address_resolver(None)


def test_the_legacy_environment_path_still_passes(monkeypatch):
    monkeypatch.setenv("EVIDENCE_BUCKET", "env-bucket")
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://minio:9000")
    config = StorageConfigResolver(loader=lambda: []).resolve_platform()
    storage_config.assert_endpoint_allowed(config)  # must not raise


def test_a_row_does_not_inherit_the_bundled_public_endpoint(monkeypatch):
    """EVIDENCE_PUBLIC_ENDPOINT describes the bundled MinIO. Inheriting it onto
    an organisation's own cloud bucket would sign browser-facing URLs against
    the wrong host."""
    monkeypatch.setenv("EVIDENCE_PUBLIC_ENDPOINT", "http://localhost:9000")
    config = storage_config.config_from_row(_row(public_endpoint=""))
    assert config.public_endpoint == ""
    assert config.endpoint_for(storage_config.ROLE_PRESIGN) == config.endpoint_url


def test_the_sse_mode_column_is_validated():
    assert storage_config.config_from_row(_row(sse_mode=SSE_AES256)).sse_enabled is True
    record = _record(None)
    record = record[:8] + ("nonsense",) + record[9:]
    assert storage_config._row_from_record(record).sse_mode == SSE_NONE


# ---------------------------------------------------------------------------
# Criterion 15 — a change reaches another process in about two seconds
# ---------------------------------------------------------------------------


def test_two_resolvers_converge_through_the_shared_version_key(monkeypatch):
    """Criterion 15. Two resolvers stand in for the API process and a Celery
    worker. One writes and announces; the other is holding a warm, unexpired
    cache, so the version key is the only thing that can tell it to re-read.
    """
    fake = _FakeRedis()
    monkeypatch.setattr(storage_config, "_get_redis", lambda: fake)
    monkeypatch.setattr(storage_config, "VERSION_CHECK_INTERVAL_SECONDS", 0.0)

    stored = [_row(organization_id="org-1", bucket="before")]
    writer = StorageConfigResolver(loader=lambda: list(stored))
    reader = StorageConfigResolver(loader=lambda: list(stored))

    assert writer.resolve("org-1").bucket == "before"
    assert reader.resolve("org-1").bucket == "before"

    # The writing process changes the row and announces it.
    stored[0] = _row(organization_id="org-1", bucket="after")
    writer.bump_version()

    assert fake.incrs == 1
    assert writer.resolve("org-1").bucket == "after"
    # The reader never called invalidate(); it converges purely on the key.
    assert reader.resolve("org-1").bucket == "after"


def test_convergence_is_seconds_not_the_full_ttl():
    """The numbers are the point of criterion 15: a worker must not wait out a
    sixty-second cache to see a rotation."""
    assert storage_config.CACHE_TTL_SECONDS == 60.0
    assert storage_config.VERSION_CHECK_INTERVAL_SECONDS == 2.0
    assert storage_config.REDIS_VERSION_KEY == "scf:storage:version"


def test_the_version_key_is_not_the_secrets_key():
    """Two independent caches. Bumping one must not invalidate the other, or a
    credential rotation would needlessly reload every storage config."""
    from services import secrets

    assert storage_config.REDIS_VERSION_KEY != secrets.REDIS_VERSION_KEY


def test_the_version_get_is_rate_limited(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(storage_config, "_get_redis", lambda: fake)
    resolver = StorageConfigResolver(loader=lambda: [_row(organization_id="org-1")])
    for _ in range(5):
        resolver.resolve("org-1")
    assert fake.gets <= 1


def test_an_unchanged_version_leaves_the_cache_warm(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(storage_config, "_get_redis", lambda: fake)
    monkeypatch.setattr(storage_config, "VERSION_CHECK_INTERVAL_SECONDS", 0.0)

    calls = []

    def loader():
        calls.append(1)
        return [_row(organization_id="org-1")]

    resolver = StorageConfigResolver(loader=loader)
    resolver.resolve("org-1")
    resolver.resolve("org-1")
    assert len(calls) == 1


def test_redis_being_down_is_not_fatal(monkeypatch):
    """Best effort throughout: the TTL alone governs, and nothing raises."""

    class _Broken:
        def get(self, key):
            raise RuntimeError("redis down")

        def incr(self, key):
            raise RuntimeError("redis down")

    monkeypatch.setattr(storage_config, "_get_redis", lambda: _Broken())
    monkeypatch.setattr(storage_config, "VERSION_CHECK_INTERVAL_SECONDS", 0.0)

    resolver = StorageConfigResolver(loader=lambda: [_row(organization_id="org-1")])
    assert resolver.resolve("org-1").bucket == "row-bucket"
    resolver.bump_version()  # must not raise
    assert resolver.resolve("org-1").bucket == "row-bucket"


def test_no_redis_at_all_is_not_fatal(monkeypatch):
    monkeypatch.setattr(storage_config, "_get_redis", lambda: None)
    resolver = StorageConfigResolver(loader=lambda: [_row(organization_id="org-1")])
    assert resolver.resolve("org-1").bucket == "row-bucket"
    resolver.bump_version()


def test_the_ttl_alone_eventually_repolls(monkeypatch):
    values = iter(
        [
            [_row(organization_id="org-1", bucket="first")],
            [_row(organization_id="org-1", bucket="second")],
        ]
    )
    monkeypatch.setattr(storage_config, "_get_redis", lambda: None)
    resolver = StorageConfigResolver(loader=lambda: next(values))
    assert resolver.resolve("org-1").bucket == "first"
    monkeypatch.setattr(storage_config, "CACHE_TTL_SECONDS", 0.0)
    assert resolver.resolve("org-1").bucket == "second"


# ---------------------------------------------------------------------------
# The shared resolver, and the invalidation the facade performs
# ---------------------------------------------------------------------------


def test_the_module_level_helpers_delegate_to_the_shared_resolver(monkeypatch):
    monkeypatch.setenv("EVIDENCE_BUCKET", "env-bucket")
    storage_config.use_loader(lambda: [_row(organization_id="org-1", bucket="shared")])
    try:
        assert storage_config.resolve("org-1").bucket == "shared"
        assert storage_config.resolve_platform().bucket == "env-bucket"
    finally:
        storage_config.use_loader(lambda: [])


def test_the_facade_invalidates_the_configuration_snapshot(monkeypatch):
    """`storage_service.invalidate()` has to drop the configuration snapshot as
    well as the client cache, or the next operation rebuilds a client from a
    configuration this process has already been told is stale."""
    from services import storage_service

    values = iter(
        [
            [_row(organization_id="org-1", bucket="before")],
            [_row(organization_id="org-1", bucket="after")],
        ]
    )
    monkeypatch.setattr(storage_config, "_get_redis", lambda: None)
    storage_config.use_loader(lambda: next(values))
    try:
        assert storage_config.resolve("org-1").bucket == "before"
        storage_service.invalidate()
        assert storage_config.resolve("org-1").bucket == "after"
    finally:
        storage_config.use_loader(lambda: [])
