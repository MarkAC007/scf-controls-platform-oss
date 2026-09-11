"""Tests for the secret resolution chain (issue #947, Lane C).

Contract: `backend/services/secrets.py` is the ONLY accessor. Precedence is
DB tier (TIER3_NAMES only) -> `{NAME}_FILE` -> `os.getenv(name, default)`.
Nothing is cached at import; the DB tier alone is cached, with a 60s TTL and a
best-effort Redis version key that lets a process see another process's
rotation without waiting the TTL out.
"""
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import secrets  # noqa: E402


# --------------------------------------------------------------------------
# Inventory
# --------------------------------------------------------------------------

def test_tier3_names_is_the_closed_list_of_six():
    assert secrets.TIER3_NAMES == (
        "OIDC_CLIENT_SECRET",
        "RESEND_API_KEY",
        "ANTHROPIC_API_KEY",
        "AZURE_STORAGE_ACCOUNT_KEY",
        "HIBP_API_KEY",
        "NVD_API_KEY",
    )
    assert isinstance(secrets.TIER3_NAMES, tuple)


def test_never_db_names_covers_every_tier1_and_tier2_credential():
    assert secrets.NEVER_DB_NAMES == (
        "SCF_SECRET_KEY",
        "API_KEY",
        "DOWNLOAD_TOKEN_SECRET",
        "DB_PASSWORD",
        "MINIO_ROOT_USER",
        "MINIO_ROOT_PASSWORD",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "KC_ADMIN_PASSWORD",
    )


def test_tier3_and_never_db_are_disjoint():
    assert not set(secrets.TIER3_NAMES) & set(secrets.NEVER_DB_NAMES)


# --------------------------------------------------------------------------
# Precedence: DB -> file -> env
# --------------------------------------------------------------------------

def test_db_tier_wins_over_file_and_env_for_a_tier3_name(tmp_path, monkeypatch):
    secret_file = tmp_path / "RESEND_API_KEY"
    secret_file.write_text("from-file\n")
    monkeypatch.setenv("RESEND_API_KEY_FILE", str(secret_file))
    monkeypatch.setenv("RESEND_API_KEY", "from-env")
    secrets.register_db_provider(lambda: {"RESEND_API_KEY": "from-db"})
    secrets.invalidate()

    assert secrets.get_secret("RESEND_API_KEY") == "from-db"
    assert secrets.source_of("RESEND_API_KEY") == "db"


def test_file_tier_wins_over_env_when_db_has_nothing(tmp_path, monkeypatch):
    secret_file = tmp_path / "RESEND_API_KEY"
    secret_file.write_text("from-file\n")
    monkeypatch.setenv("RESEND_API_KEY_FILE", str(secret_file))
    monkeypatch.setenv("RESEND_API_KEY", "from-env")
    secrets.register_db_provider(lambda: {})
    secrets.invalidate()

    assert secrets.get_secret("RESEND_API_KEY") == "from-file"
    assert secrets.source_of("RESEND_API_KEY") == "file"


def test_env_tier_is_the_final_fallback(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY_FILE", raising=False)
    monkeypatch.setenv("RESEND_API_KEY", "from-env")

    assert secrets.get_secret("RESEND_API_KEY") == "from-env"
    assert secrets.source_of("RESEND_API_KEY") == "env"


def test_default_is_returned_when_no_tier_has_a_value(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY_FILE", raising=False)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)

    assert secrets.get_secret("RESEND_API_KEY") is None
    assert secrets.get_secret("RESEND_API_KEY", "fallback") == "fallback"
    assert secrets.source_of("RESEND_API_KEY") is None


def test_file_contents_are_stripped(tmp_path, monkeypatch):
    secret_file = tmp_path / "API_KEY"
    secret_file.write_text("  padded-value  \n")
    monkeypatch.setenv("API_KEY_FILE", str(secret_file))
    monkeypatch.delenv("API_KEY", raising=False)

    assert secrets.get_secret("API_KEY") == "padded-value"


def test_empty_file_means_unset_and_falls_through_to_env(tmp_path, monkeypatch):
    secret_file = tmp_path / "API_KEY"
    secret_file.write_text("\n")
    monkeypatch.setenv("API_KEY_FILE", str(secret_file))
    monkeypatch.setenv("API_KEY", "from-env")

    assert secrets.get_secret("API_KEY") == "from-env"
    assert secrets.source_of("API_KEY") == "env"


def test_missing_file_path_falls_through_to_env(tmp_path, monkeypatch):
    monkeypatch.setenv("API_KEY_FILE", str(tmp_path / "does-not-exist"))
    monkeypatch.setenv("API_KEY", "from-env")

    assert secrets.get_secret("API_KEY") == "from-env"


def test_empty_env_value_means_unset_and_falls_through_to_default(monkeypatch):
    monkeypatch.delenv("API_KEY_FILE", raising=False)
    monkeypatch.setenv("API_KEY", "")

    assert secrets.get_secret("API_KEY", "fallback") == "fallback"


def test_setenv_is_honoured_at_call_time_no_import_singleton(monkeypatch):
    monkeypatch.delenv("API_KEY_FILE", raising=False)
    monkeypatch.setenv("API_KEY", "first")
    assert secrets.get_secret("API_KEY") == "first"

    monkeypatch.setenv("API_KEY", "second")
    assert secrets.get_secret("API_KEY") == "second"


def test_setenv_is_honoured_after_an_earlier_resolve_warmed_the_db_cache(monkeypatch):
    """A warmed DB-tier cache must not freeze the env tier."""
    secrets.register_db_provider(lambda: {"ANTHROPIC_API_KEY": ""})
    monkeypatch.delenv("HIBP_API_KEY_FILE", raising=False)
    monkeypatch.setenv("HIBP_API_KEY", "warm")
    assert secrets.get_secret("HIBP_API_KEY") == "warm"

    monkeypatch.setenv("HIBP_API_KEY", "rotated")
    assert secrets.get_secret("HIBP_API_KEY") == "rotated"


# --------------------------------------------------------------------------
# The DB tier is structurally unreachable for NEVER_DB names
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", secrets.NEVER_DB_NAMES)
def test_db_tier_is_structurally_unreachable_for_never_db_names(name, monkeypatch):
    """A stored row for API_KEY (etc.) is never returned, even when the
    provider offers one: get_secret checks TIER3 membership before it so much
    as calls the provider."""
    calls = []

    def provider():
        calls.append(name)
        return {n: "db-poisoned" for n in secrets.NEVER_DB_NAMES}

    secrets.register_db_provider(provider)
    secrets.invalidate()
    monkeypatch.delenv(f"{name}_FILE", raising=False)
    monkeypatch.setenv(name, "env-value")

    assert secrets.get_secret(name) == "env-value"
    assert secrets.source_of(name) == "env"
    assert calls == [], f"provider was consulted for NEVER_DB name {name}"


def test_never_db_name_with_no_env_returns_default_not_the_db_row(monkeypatch):
    secrets.register_db_provider(lambda: {"API_KEY": "db-poisoned"})
    secrets.invalidate()
    monkeypatch.delenv("API_KEY_FILE", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)

    assert secrets.get_secret("API_KEY") is None


# --------------------------------------------------------------------------
# Cache: TTL, bump_version, Redis version key
# --------------------------------------------------------------------------

def test_db_provider_is_cached_between_calls():
    calls = []
    secrets.register_db_provider(lambda: (calls.append(1), {"NVD_API_KEY": "v1"})[1])
    secrets.invalidate()

    assert secrets.get_secret("NVD_API_KEY") == "v1"
    assert secrets.get_secret("NVD_API_KEY") == "v1"
    assert len(calls) == 1


def test_ttl_expiry_repolls_the_provider(monkeypatch):
    values = iter(["v1", "v2"])
    secrets.register_db_provider(lambda: {"NVD_API_KEY": next(values)})
    secrets.invalidate()
    assert secrets.get_secret("NVD_API_KEY") == "v1"

    monkeypatch.setattr(secrets, "CACHE_TTL_SECONDS", 0.0)
    assert secrets.get_secret("NVD_API_KEY") == "v2"


def test_invalidate_drops_the_cache_but_keeps_the_provider():
    values = iter(["v1", "v2"])
    secrets.register_db_provider(lambda: {"NVD_API_KEY": next(values)})
    secrets.invalidate()
    assert secrets.get_secret("NVD_API_KEY") == "v1"

    secrets.invalidate()
    assert secrets.get_secret("NVD_API_KEY") == "v2"


def test_reset_caches_drops_the_cache_and_keeps_the_provider():
    values = iter(["v1", "v2"])
    secrets.register_db_provider(lambda: {"NVD_API_KEY": next(values)})
    secrets.reset_caches()
    assert secrets.get_secret("NVD_API_KEY") == "v1"

    secrets.reset_caches()
    assert secrets.get_secret("NVD_API_KEY") == "v2"


class _FakeRedis:
    """Minimal stand-in for the sync redis client behind the version key."""

    def __init__(self, version=b"1"):
        self.version = version
        self.gets = 0
        self.incrs = 0

    def get(self, key):
        assert key == "scf:secrets:version"
        self.gets += 1
        return self.version

    def incr(self, key):
        assert key == "scf:secrets:version"
        self.incrs += 1
        self.version = str(int(self.version) + 1).encode()
        return int(self.version)


def test_redis_version_bump_makes_a_second_resolver_see_a_new_value(monkeypatch):
    """A rotation is observed via the version key without sleeping out the 60s
    TTL: the writer bumps, the reader notices on its next rate-limited check."""
    fake = _FakeRedis()
    monkeypatch.setattr(secrets, "_get_redis", lambda: fake)
    monkeypatch.setattr(secrets, "VERSION_CHECK_INTERVAL_SECONDS", 0.0)

    stored = {"NVD_API_KEY": "old"}
    secrets.register_db_provider(lambda: dict(stored))
    secrets.invalidate()
    assert secrets.get_secret("NVD_API_KEY") == "old"

    # A different process rotates the row and bumps the shared version key.
    stored["NVD_API_KEY"] = "new"
    fake.incr("scf:secrets:version")

    # This resolver still holds a warm, unexpired cache. The version key is
    # the only thing that can tell it to repoll.
    assert secrets.get_secret("NVD_API_KEY") == "new"


def test_unchanged_redis_version_leaves_the_cache_warm(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(secrets, "_get_redis", lambda: fake)
    monkeypatch.setattr(secrets, "VERSION_CHECK_INTERVAL_SECONDS", 0.0)

    calls = []
    secrets.register_db_provider(lambda: (calls.append(1), {"NVD_API_KEY": "v1"})[1])
    secrets.invalidate()
    assert secrets.get_secret("NVD_API_KEY") == "v1"
    assert secrets.get_secret("NVD_API_KEY") == "v1"
    assert len(calls) == 1


def test_version_get_is_rate_limited(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(secrets, "_get_redis", lambda: fake)
    # Production interval (2s): several resolves in a row must not each GET.
    secrets.register_db_provider(lambda: {"NVD_API_KEY": "v1"})
    secrets.invalidate()
    for _ in range(5):
        secrets.get_secret("NVD_API_KEY")
    assert fake.gets <= 1


def test_bump_version_incrs_and_invalidates(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(secrets, "_get_redis", lambda: fake)
    values = iter(["v1", "v2"])
    secrets.register_db_provider(lambda: {"NVD_API_KEY": next(values)})
    secrets.invalidate()
    assert secrets.get_secret("NVD_API_KEY") == "v1"

    secrets.bump_version()
    assert fake.incrs == 1
    assert secrets.get_secret("NVD_API_KEY") == "v2"


def test_redis_errors_are_ignored(monkeypatch):
    class _Broken:
        def get(self, key):
            raise RuntimeError("redis down")

        def incr(self, key):
            raise RuntimeError("redis down")

    monkeypatch.setattr(secrets, "_get_redis", lambda: _Broken())
    monkeypatch.setattr(secrets, "VERSION_CHECK_INTERVAL_SECONDS", 0.0)
    secrets.register_db_provider(lambda: {"NVD_API_KEY": "v1"})
    secrets.invalidate()

    assert secrets.get_secret("NVD_API_KEY") == "v1"
    secrets.bump_version()  # must not raise


def test_provider_exception_does_not_break_resolution(monkeypatch):
    def boom():
        raise RuntimeError("no table yet")

    secrets.register_db_provider(boom)
    secrets.invalidate()
    monkeypatch.setenv("NVD_API_KEY", "from-env")

    assert secrets.get_secret("NVD_API_KEY") == "from-env"


def test_no_provider_registered_falls_through_cleanly(monkeypatch):
    secrets.register_db_provider(None)
    secrets.invalidate()
    monkeypatch.setenv("NVD_API_KEY", "from-env")

    assert secrets.get_secret("NVD_API_KEY") == "from-env"


def test_empty_db_value_falls_through_to_env(monkeypatch):
    secrets.register_db_provider(lambda: {"NVD_API_KEY": "  "})
    secrets.invalidate()
    monkeypatch.setenv("NVD_API_KEY", "from-env")

    assert secrets.get_secret("NVD_API_KEY") == "from-env"


# --------------------------------------------------------------------------
# is_placeholder / integration_enabled
# --------------------------------------------------------------------------

@pytest.mark.parametrize("value", [
    None, "", "   ", "changeme", "CHANGEME", "changeme-postgres",
    "ChangeMe123", "minioadmin", "CHANGE_ME", "change_me_please",
])
def test_is_placeholder_true(value):
    assert secrets.is_placeholder(value) is True


@pytest.mark.parametrize("value", [
    "sk-ant-real", "a-real-password", "re_live_abc123",  # gitleaks:allow — fixtures, must look real for this assertion
])
def test_is_placeholder_false(value):
    assert secrets.is_placeholder(value) is False


def test_is_placeholder_minioadmin_is_exact_match_only():
    assert secrets.is_placeholder("minioadmin") is True
    assert secrets.is_placeholder("minioadminX") is False


def test_integration_enabled_reflects_value_and_placeholder(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY_FILE", raising=False)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    assert secrets.integration_enabled("RESEND_API_KEY") is False

    monkeypatch.setenv("RESEND_API_KEY", "changeme-please")
    assert secrets.integration_enabled("RESEND_API_KEY") is False

    monkeypatch.setenv("RESEND_API_KEY", "re_live_realkey")
    assert secrets.integration_enabled("RESEND_API_KEY") is True


# --------------------------------------------------------------------------
# check_startup_secrets
# --------------------------------------------------------------------------

def _clean_startup_env(monkeypatch):
    for name in ("API_KEY", "DB_PASSWORD", "DATABASE_URL", "AWS_ENDPOINT_URL",
                 "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "SCF_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(f"{name}_FILE", raising=False)


def test_startup_is_a_noop_in_development(monkeypatch):
    _clean_startup_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "development")
    secrets.check_startup_secrets()  # must not raise


def test_startup_is_a_noop_in_test(monkeypatch):
    _clean_startup_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "test")
    secrets.check_startup_secrets()


def test_startup_exits_3_when_api_key_unset_in_production(monkeypatch):
    _clean_startup_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DB_PASSWORD", "a-real-password")

    with pytest.raises(SystemExit) as exc:
        secrets.check_startup_secrets()
    assert exc.value.code == 3
    assert "API_KEY" in str(exc.value)


def test_startup_does_not_demand_api_key_for_worker_processes(monkeypatch):
    """A pre-#947 .env install only hands API_KEY to the backend service, so a
    Celery worker must boot without it (regression seen on the legacy stack)."""
    _clean_startup_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DB_PASSWORD", "a-real-password")

    secrets.check_startup_secrets(require_api_key=False)  # must not raise
    with pytest.raises(SystemExit):
        secrets.check_startup_secrets(require_api_key=True)


def test_startup_exits_when_api_key_is_a_placeholder(monkeypatch):
    _clean_startup_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("API_KEY", "changeme-generate-a-secure-key")
    monkeypatch.setenv("DB_PASSWORD", "a-real-password")

    with pytest.raises(SystemExit) as exc:
        secrets.check_startup_secrets()
    assert "API_KEY" in str(exc.value)
    assert "changeme-generate-a-secure-key" not in str(exc.value)


def test_startup_exits_when_db_password_is_a_placeholder(monkeypatch):
    _clean_startup_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("API_KEY", "a-real-api-key")
    monkeypatch.setenv("DB_PASSWORD", "changeme-postgres")

    with pytest.raises(SystemExit) as exc:
        secrets.check_startup_secrets()
    assert "DB_PASSWORD" in str(exc.value)
    assert "changeme-postgres" not in str(exc.value)


def test_startup_reads_the_db_password_out_of_a_legacy_dsn(monkeypatch):
    _clean_startup_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("API_KEY", "a-real-api-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://cg:changeme@postgres:5432/cg_scf")

    with pytest.raises(SystemExit) as exc:
        secrets.check_startup_secrets()
    assert "DATABASE_URL" in str(exc.value)
    assert "changeme" not in str(exc.value)


def test_startup_passes_with_a_real_dsn_password(monkeypatch):
    _clean_startup_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("API_KEY", "a-real-api-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://cg:s3cr3t-real@postgres:5432/cg_scf")

    secrets.check_startup_secrets()


def test_startup_exits_on_placeholder_aws_creds_when_endpoint_url_is_set(monkeypatch):
    _clean_startup_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("API_KEY", "a-real-api-key")
    monkeypatch.setenv("DB_PASSWORD", "a-real-password")
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://minio:9000")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "minioadmin")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "minioadmin")

    with pytest.raises(SystemExit) as exc:
        secrets.check_startup_secrets()
    assert "AWS_ACCESS_KEY_ID" in str(exc.value)


def test_startup_ignores_aws_creds_when_no_endpoint_url(monkeypatch):
    _clean_startup_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("API_KEY", "a-real-api-key")
    monkeypatch.setenv("DB_PASSWORD", "a-real-password")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "minioadmin")

    secrets.check_startup_secrets()


def test_startup_exits_on_a_non_fernet_secret_key_naming_the_variable(monkeypatch):
    """Exits non-zero naming SCF_SECRET_KEY, never printing the value."""
    _clean_startup_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("API_KEY", "a-real-api-key")
    monkeypatch.setenv("DB_PASSWORD", "a-real-password")
    monkeypatch.setenv("SCF_SECRET_KEY", "not-a-fernet-key-at-all")

    with pytest.raises(SystemExit) as exc:
        secrets.check_startup_secrets()
    assert exc.value.code == 3
    assert "SCF_SECRET_KEY" in str(exc.value)
    assert "not-a-fernet-key-at-all" not in str(exc.value)


def test_startup_accepts_a_valid_fernet_key(monkeypatch):
    fernet = pytest.importorskip("cryptography.fernet")
    _clean_startup_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("API_KEY", "a-real-api-key")
    monkeypatch.setenv("DB_PASSWORD", "a-real-password")
    monkeypatch.setenv("SCF_SECRET_KEY", fernet.Fernet.generate_key().decode())

    secrets.check_startup_secrets()


def test_startup_validates_every_comma_separated_rotation_key(monkeypatch):
    fernet = pytest.importorskip("cryptography.fernet")
    _clean_startup_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("API_KEY", "a-real-api-key")
    monkeypatch.setenv("DB_PASSWORD", "a-real-password")
    good = fernet.Fernet.generate_key().decode()
    monkeypatch.setenv("SCF_SECRET_KEY", f"{good},rubbish")

    with pytest.raises(SystemExit) as exc:
        secrets.check_startup_secrets()
    assert "SCF_SECRET_KEY" in str(exc.value)
    assert good not in str(exc.value)


def test_startup_accepts_two_valid_rotation_keys(monkeypatch):
    fernet = pytest.importorskip("cryptography.fernet")
    _clean_startup_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("API_KEY", "a-real-api-key")
    monkeypatch.setenv("DB_PASSWORD", "a-real-password")
    a = fernet.Fernet.generate_key().decode()
    b = fernet.Fernet.generate_key().decode()
    monkeypatch.setenv("SCF_SECRET_KEY", f"{a}, {b}")

    secrets.check_startup_secrets()


def test_startup_reads_secrets_from_files_too(tmp_path, monkeypatch):
    _clean_startup_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")
    api_file = tmp_path / "API_KEY"
    api_file.write_text("a-real-api-key\n")
    monkeypatch.setenv("API_KEY_FILE", str(api_file))
    db_file = tmp_path / "DB_PASSWORD"
    db_file.write_text("changeme-postgres\n")
    monkeypatch.setenv("DB_PASSWORD_FILE", str(db_file))

    with pytest.raises(SystemExit) as exc:
        secrets.check_startup_secrets()
    assert "DB_PASSWORD" in str(exc.value)


# --------------------------------------------------------------------------
# bootstrap_process
# --------------------------------------------------------------------------

def test_bootstrap_process_runs_checks_then_tolerates_a_missing_provider(monkeypatch):
    _clean_startup_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "test")
    secrets.bootstrap_process()  # ImportError on integration_secrets is swallowed


def test_bootstrap_process_propagates_a_startup_failure(monkeypatch):
    _clean_startup_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DB_PASSWORD", "a-real-password")

    with pytest.raises(SystemExit):
        secrets.bootstrap_process()


# --------------------------------------------------------------------------
# an ABSENT SCF_SECRET_KEY warns but never blocks (#956)
# --------------------------------------------------------------------------

def test_absent_secret_key_warns_in_development_but_does_not_raise(monkeypatch, caplog):
    """A fresh `docker compose up` that never ran the installer used to say
    nothing at all; the first signal was a 409 in the UI weeks later."""
    _clean_startup_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "development")
    with caplog.at_level(logging.WARNING):
        secrets.check_startup_secrets()  # must not raise
    assert any("SCF_SECRET_KEY is not configured" in r.message for r in caplog.records)


def test_absent_secret_key_warns_in_production_but_is_not_a_startup_problem(monkeypatch, caplog):
    _clean_startup_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("API_KEY", "a-real-api-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://cg:s3cr3t-real@postgres:5432/cg_scf")

    with caplog.at_level(logging.WARNING):
        secrets.check_startup_secrets()  # legacy .env installs must keep booting

    assert any("SCF_SECRET_KEY is not configured" in r.message for r in caplog.records)


def test_a_configured_secret_key_produces_no_warning(monkeypatch, caplog):
    fernet = pytest.importorskip("cryptography.fernet")
    _clean_startup_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("API_KEY", "a-real-api-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://cg:s3cr3t-real@postgres:5432/cg_scf")
    monkeypatch.setenv("SCF_SECRET_KEY", fernet.Fernet.generate_key().decode())

    with caplog.at_level(logging.WARNING):
        secrets.check_startup_secrets()

    assert not any("SCF_SECRET_KEY is not configured" in r.message for r in caplog.records)
