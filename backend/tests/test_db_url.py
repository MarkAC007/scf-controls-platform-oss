"""Tests for in-process DSN composition (issue #947, Lane C).

`DATABASE_URL` still wins byte-for-byte when it is set. When it is not, the DSN
is composed from DB_HOST/DB_PORT/DB_NAME/DB_USER plus the resolved DB_PASSWORD,
so a password can live in a 0600 file instead of an environment variable.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db_url  # noqa: E402

_DB_VARS = ("DATABASE_URL", "DB_HOST", "DB_PORT", "DB_NAME", "DB_USER",
            "DB_PASSWORD", "DB_SSLMODE")


@pytest.fixture(autouse=True)
def _clean_db_env(monkeypatch):
    for name in _DB_VARS:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(f"{name}_FILE", raising=False)
    yield


# --------------------------------------------------------------------------
# DATABASE_URL wins, unchanged
# --------------------------------------------------------------------------

def test_database_url_is_returned_byte_for_byte(monkeypatch):
    dsn = "postgresql+asyncpg://cg:p%40ss@postgres:5432/cg_scf?ssl=require"
    monkeypatch.setenv("DATABASE_URL", dsn)

    assert db_url.get_database_url() == dsn


def test_database_url_wins_over_components(monkeypatch):
    dsn = "postgresql+asyncpg://legacy:legacy@legacy-host:5432/legacy_db"
    monkeypatch.setenv("DATABASE_URL", dsn)
    monkeypatch.setenv("DB_HOST", "ignored")
    monkeypatch.setenv("DB_PASSWORD", "ignored")

    assert db_url.get_database_url() == dsn


def test_empty_database_url_is_treated_as_unset(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("DB_PASSWORD", "s3cret")

    assert db_url.get_database_url() == (
        "postgresql+asyncpg://cg:s3cret@localhost:5432/cg_scf"
    )


# --------------------------------------------------------------------------
# Composition from components
# --------------------------------------------------------------------------

def test_composes_from_components_with_defaults(monkeypatch):
    monkeypatch.setenv("DB_PASSWORD", "s3cret")

    assert db_url.get_database_url() == (
        "postgresql+asyncpg://cg:s3cret@localhost:5432/cg_scf"
    )


def test_composes_from_explicit_components(monkeypatch):
    monkeypatch.setenv("DB_HOST", "postgres")
    monkeypatch.setenv("DB_PORT", "6543")
    monkeypatch.setenv("DB_NAME", "scf")
    monkeypatch.setenv("DB_USER", "scfuser")
    monkeypatch.setenv("DB_PASSWORD", "s3cret")

    assert db_url.get_database_url() == (
        "postgresql+asyncpg://scfuser:s3cret@postgres:6543/scf"
    )


def test_password_from_a_file_is_used(tmp_path, monkeypatch):
    pw_file = tmp_path / "DB_PASSWORD"
    pw_file.write_text("file-password\n")
    monkeypatch.setenv("DB_PASSWORD_FILE", str(pw_file))

    assert db_url.get_database_url() == (
        "postgresql+asyncpg://cg:file-password@localhost:5432/cg_scf"
    )


@pytest.mark.parametrize("raw,quoted", [
    ("p@ss", "p%40ss"),
    ("a/b", "a%2Fb"),
    ("a:b", "a%3Ab"),
    ("a b", "a%20b"),
    ("100%pure", "100%25pure"),
    ("p#ss?q", "p%23ss%3Fq"),
])
def test_special_characters_in_the_password_are_url_quoted(raw, quoted, monkeypatch):
    monkeypatch.setenv("DB_PASSWORD", raw)

    assert db_url.get_database_url() == (
        f"postgresql+asyncpg://cg:{quoted}@localhost:5432/cg_scf"
    )


def test_username_is_url_quoted_too(monkeypatch):
    monkeypatch.setenv("DB_USER", "cg@tenant")
    monkeypatch.setenv("DB_PASSWORD", "s3cret")

    assert db_url.get_database_url() == (
        "postgresql+asyncpg://cg%40tenant:s3cret@localhost:5432/cg_scf"
    )


# --------------------------------------------------------------------------
# sslmode mapping
# --------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["", "disable"])
def test_no_ssl_param_when_sslmode_is_unset_or_disable(mode, monkeypatch):
    monkeypatch.setenv("DB_PASSWORD", "s3cret")
    monkeypatch.setenv("DB_SSLMODE", mode)

    assert db_url.get_database_url() == (
        "postgresql+asyncpg://cg:s3cret@localhost:5432/cg_scf"
    )


def test_asyncpg_uses_ssl_param(monkeypatch):
    monkeypatch.setenv("DB_PASSWORD", "s3cret")
    monkeypatch.setenv("DB_SSLMODE", "require")

    assert db_url.get_database_url() == (
        "postgresql+asyncpg://cg:s3cret@localhost:5432/cg_scf?ssl=require"
    )


def test_psycopg2_uses_sslmode_param(monkeypatch):
    monkeypatch.setenv("DB_PASSWORD", "s3cret")
    monkeypatch.setenv("DB_SSLMODE", "require")

    assert db_url.get_sync_database_url() == (
        "postgresql+psycopg2://cg:s3cret@localhost:5432/cg_scf?sslmode=require"
    )


def test_sslmode_verify_full_is_passed_through(monkeypatch):
    monkeypatch.setenv("DB_PASSWORD", "s3cret")
    monkeypatch.setenv("DB_SSLMODE", "verify-full")

    assert db_url.get_database_url().endswith("?ssl=verify-full")
    assert db_url.get_sync_database_url().endswith("?sslmode=verify-full")


# --------------------------------------------------------------------------
# The `default` argument preserves today's dev/test behaviour
# --------------------------------------------------------------------------

def test_default_is_returned_when_no_password_is_resolvable():
    legacy = "postgresql+asyncpg://odin:changeme@localhost:5432/odin_scf"

    assert db_url.get_database_url(legacy) == legacy


def test_default_is_returned_when_the_password_is_empty(monkeypatch):
    monkeypatch.setenv("DB_PASSWORD", "")
    legacy = "postgresql+asyncpg://cg:cg@localhost:5432/cg_scf"

    assert db_url.get_database_url(legacy) == legacy


def test_no_default_and_no_password_still_composes(monkeypatch):
    """Without a default the caller wants a DSN regardless; an empty password
    is legal in a trust-auth deployment."""
    result = db_url.get_database_url()

    assert result.startswith("postgresql+asyncpg://cg:@localhost:5432/cg_scf")


# --------------------------------------------------------------------------
# Sync variant matches today's transformation exactly
# --------------------------------------------------------------------------

def test_sync_url_converts_the_legacy_default_the_way_the_tasks_did():
    legacy = "postgresql+asyncpg://cg:cg@localhost:5432/cg_scf"

    assert db_url.get_sync_database_url(legacy) == (
        "postgresql+psycopg2://cg:cg@localhost:5432/cg_scf"
    )


def test_sync_url_rewrites_ssl_require_to_sslmode_require(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://cg:cg@postgres:5432/cg_scf?ssl=require",
    )

    assert db_url.get_sync_database_url() == (
        "postgresql+psycopg2://cg:cg@postgres:5432/cg_scf?sslmode=require"
    )


def test_sync_url_from_components_uses_psycopg2(monkeypatch):
    monkeypatch.setenv("DB_PASSWORD", "s3cret")

    assert db_url.get_sync_database_url() == (
        "postgresql+psycopg2://cg:s3cret@localhost:5432/cg_scf"
    )


# --------------------------------------------------------------------------
# Rotation: the DSN is resolved per call, not frozen at import
# --------------------------------------------------------------------------

def test_password_rotation_is_visible_on_the_next_call(monkeypatch):
    monkeypatch.setenv("DB_PASSWORD", "old")
    assert "old" in db_url.get_database_url()

    monkeypatch.setenv("DB_PASSWORD", "new")
    assert "new" in db_url.get_database_url()
