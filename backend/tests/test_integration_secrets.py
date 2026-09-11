"""Integration credential store, API and migration (Issue #947, contract §3d/§3e).

DB-backed. Requires a throwaway Postgres reachable at DATABASE_URL with the
migration applied; skipped otherwise so the file is harmless in the unit
harness.

Covers ISC-30..40, ISC-133, ISC-137, ISC-139, ISC-140.
"""
import asyncio
import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# models.py has relationships pointing at classes defined in catalog_models.py;
# without this import SQLAlchemy cannot configure ANY mapper. Pre-existing
# requirement of this codebase, not specific to #947.
import catalog_models  # noqa: F401

DATABASE_URL = os.getenv("DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason="requires a throwaway Postgres (DATABASE_URL)",
)

KEY_A = Fernet.generate_key().decode()
KEY_B = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    from services import crypto
    from services import secrets as secrets_mod

    monkeypatch.delenv("SCF_SECRET_KEY_FILE", raising=False)
    for name in ("RESEND_API_KEY", "ANTHROPIC_API_KEY", "HIBP_API_KEY",
                 "NVD_API_KEY", "AZURE_STORAGE_ACCOUNT_KEY", "OIDC_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(f"{name}_FILE", raising=False)
    monkeypatch.setenv("SCF_SECRET_KEY", KEY_A)
    crypto.reset()
    secrets_mod.reset_caches()
    yield
    crypto.reset()
    secrets_mod.reset_caches()


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(DATABASE_URL, poolclass=None)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        await session.execute(text("DELETE FROM integration_secrets"))
        await session.execute(
            text("DELETE FROM platform_audit_log WHERE entity_type = 'integration_secret'")
        )
        await session.commit()
        yield session
    await engine.dispose()


def _actor(label="admin@example.com", user_id=None):
    from services.integration_secrets import Actor

    return Actor(label=label, user_id=user_id, action_source="ui")


# ---------------------------------------------------------------------------
# Store semantics
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_set_then_list_never_returns_the_value(db):
    """ISC-30/ISC-36: the value goes in and never comes back out."""
    from services import integration_secrets as isec

    item = await isec.set_value(db, "RESEND_API_KEY", "re_live_SUPERSECRET", _actor())
    assert item["configured"] is True
    assert item["source"] == "db"
    assert "re_live_SUPERSECRET" not in json.dumps(item)

    items = await isec.list_items(db)
    assert "re_live_SUPERSECRET" not in json.dumps(items)
    row = next(i for i in items if i["name"] == "RESEND_API_KEY")
    assert row["configured"] is True
    assert row["updated_by"] == "admin@example.com"


@pytest.mark.asyncio
async def test_isc36_raw_row_holds_no_plaintext(db):
    """A pg_dump of the table must be useless without the key."""
    from services import integration_secrets as isec

    await isec.set_value(db, "NVD_API_KEY", "nvd-PLAINTEXT-CANARY", _actor())
    raw = (
        await db.execute(
            text("SELECT value_ciphertext FROM integration_secrets WHERE name = 'NVD_API_KEY'")
        )
    ).scalar()
    assert raw.startswith("enc:v1:")
    assert "nvd-PLAINTEXT-CANARY" not in raw


@pytest.mark.asyncio
async def test_unknown_name_and_empty_value_rejected(db):
    from services import integration_secrets as isec

    with pytest.raises(isec.UnknownIntegration):
        await isec.set_value(db, "DB_PASSWORD", "x", _actor())
    with pytest.raises(isec.UnknownIntegration):
        await isec.set_value(db, "NOT_A_REAL_NAME", "x", _actor())
    with pytest.raises(isec.EmptyValue):
        await isec.set_value(db, "HIBP_API_KEY", "   ", _actor())


@pytest.mark.asyncio
async def test_isc133_operator_managed_write_refused(db, monkeypatch):
    """A value in the environment must not be shadowed from a web form."""
    from services import integration_secrets as isec

    monkeypatch.setenv("HIBP_API_KEY", "set-by-the-operator")
    with pytest.raises(isec.OperatorManaged) as exc:
        await isec.set_value(db, "HIBP_API_KEY", "set-from-the-app", _actor())
    assert exc.value.args[0] == "env"

    items = await isec.list_items(db)
    row = next(i for i in items if i["name"] == "HIBP_API_KEY")
    assert row["managed_by_operator"] is True
    assert row["source"] == "env"


@pytest.mark.asyncio
async def test_isc40_no_key_means_not_configured_and_write_refused(db, monkeypatch):
    from services import crypto
    from services import integration_secrets as isec
    from services import secrets as secrets_mod

    monkeypatch.delenv("SCF_SECRET_KEY", raising=False)
    crypto.reset()
    secrets_mod.reset_caches()

    assert isec.encryption_key_configured() is False
    health = await isec.health(db)
    assert health["encryption_key_configured"] is False
    with pytest.raises(crypto.SecretKeyMissing):
        await isec.set_value(db, "RESEND_API_KEY", "x", _actor())


@pytest.mark.asyncio
async def test_clear_removes_the_row(db):
    from services import integration_secrets as isec

    await isec.set_value(db, "ANTHROPIC_API_KEY", "sk-ant-xyz", _actor())
    item = await isec.clear_value(db, "ANTHROPIC_API_KEY", _actor())
    assert item["configured"] is False
    assert item["source"] is None
    with pytest.raises(isec.UnknownIntegration):
        await isec.clear_value(db, "ANTHROPIC_API_KEY", _actor())


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_isc137_audit_rows_for_user_and_api_key_actors(db):
    """Both actor shapes are recorded, and neither row carries the value."""
    from services import integration_secrets as isec

    await isec.set_value(db, "RESEND_API_KEY", "value-one", _actor("human@example.com"))
    await isec.set_value(db, "RESEND_API_KEY", "value-two", _actor("api_key:master"))
    await isec.clear_value(db, "RESEND_API_KEY", _actor("human@example.com"))

    rows = (
        await db.execute(
            text(
                "SELECT action, actor, entity_id FROM platform_audit_log "
                "WHERE entity_type = 'integration_secret' ORDER BY created_at"
            )
        )
    ).fetchall()
    actions = [r[0] for r in rows]
    assert actions == [
        "integration.secret.set",
        "integration.secret.replaced",
        "integration.secret.cleared",
    ]
    assert [r[1] for r in rows] == [
        "human@example.com",
        "api_key:master",
        "human@example.com",
    ]
    assert all(r[2] == "RESEND_API_KEY" for r in rows)

    # The table has no value columns at all — verify structurally.
    cols = {
        r[0]
        for r in (
            await db.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'platform_audit_log'"
                )
            )
        ).fetchall()
    }
    assert "old_value" not in cols and "new_value" not in cols


# ---------------------------------------------------------------------------
# Rotation / backfill / health
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_isc139_rotate_reencrypts_under_primary_key(db, monkeypatch):
    from services import crypto
    from services import integration_secrets as isec
    from services import secrets as secrets_mod

    await isec.set_value(db, "NVD_API_KEY", "rotate-me", _actor())
    before = (
        await db.execute(text("SELECT value_ciphertext FROM integration_secrets WHERE name='NVD_API_KEY'"))
    ).scalar()

    # New key first, old key retained — the two-step rotation.
    monkeypatch.setenv("SCF_SECRET_KEY", f"{KEY_B},{KEY_A}")
    crypto.reset()
    secrets_mod.reset_caches()

    plaintext = crypto.decrypt(before)
    assert plaintext == "rotate-me"
    rotated = crypto.encrypt(plaintext)
    await db.execute(
        text("UPDATE integration_secrets SET value_ciphertext = :v WHERE name='NVD_API_KEY'"),
        {"v": rotated},
    )
    await db.commit()

    # Old key dropped: the rotated row must still open.
    monkeypatch.setenv("SCF_SECRET_KEY", KEY_B)
    crypto.reset()
    secrets_mod.reset_caches()
    after = (
        await db.execute(text("SELECT value_ciphertext FROM integration_secrets WHERE name='NVD_API_KEY'"))
    ).scalar()
    assert crypto.decrypt(after) == "rotate-me"
    with pytest.raises(crypto.DecryptError):
        crypto.decrypt(before)


@pytest.mark.asyncio
async def test_isc140_legacy_plaintext_rows_are_counted(db):
    from services import integration_secrets as isec

    assert await isec.legacy_plaintext_rows(db) == 0
    await db.execute(
        text(
            "INSERT INTO integration_secrets (name, value_ciphertext) "
            "VALUES ('HIBP_API_KEY', 'legacy-plaintext')"
        )
    )
    await db.commit()
    assert await isec.legacy_plaintext_rows(db) == 1


@pytest.mark.asyncio
async def test_health_shape(db):
    from services import integration_secrets as isec

    await isec.set_value(db, "RESEND_API_KEY", "x", _actor())
    health = await isec.health(db)
    assert health["encryption_key_configured"] is True
    assert "RESEND_API_KEY" in health["configured"]
    assert "NVD_API_KEY" in health["unconfigured"]
    assert len(health["items"]) == 6
    assert health["secrets_dir_mode"] in {"file", "env", "mixed"}


@pytest.mark.asyncio
async def test_isc37_write_is_visible_to_get_secret_without_restart(db):
    """The whole point: set it in the app, and the reader sees it now."""
    from services import integration_secrets as isec
    from services import secrets as secrets_mod

    isec.register()
    secrets_mod.reset_caches()
    assert secrets_mod.get_secret("RESEND_API_KEY") is None

    await isec.set_value(db, "RESEND_API_KEY", "re_live_NEWVALUE", _actor())
    assert secrets_mod.get_secret("RESEND_API_KEY") == "re_live_NEWVALUE"

    await isec.clear_value(db, "RESEND_API_KEY", _actor())
    assert secrets_mod.get_secret("RESEND_API_KEY") is None


@pytest.mark.asyncio
async def test_file_and_env_beat_the_database(db, monkeypatch, tmp_path):
    """Precedence is db -> file -> env for reads, but operator tiers win here."""
    from services import integration_secrets as isec
    from services import secrets as secrets_mod

    isec.register()
    await isec.set_value(db, "NVD_API_KEY", "from-database", _actor())
    secrets_mod.reset_caches()
    assert secrets_mod.get_secret("NVD_API_KEY") == "from-database"

    # An operator-managed value is reported as such and blocks further writes.
    monkeypatch.setenv("NVD_API_KEY", "from-environment")
    items = await isec.list_items(db)
    row = next(i for i in items if i["name"] == "NVD_API_KEY")
    assert row["managed_by_operator"] is True
    with pytest.raises(isec.OperatorManaged):
        await isec.set_value(db, "NVD_API_KEY", "from-the-app", _actor())


# ---------------------------------------------------------------------------
# Router: status codes and leak-freedom (ISC-31..35)
# ---------------------------------------------------------------------------

class _FakeUser:
    def __init__(self, email="admin@example.com", auth_method="google", db_id=None):
        self.email = email
        self.auth_method = auth_method
        self.db_id = db_id


class _FakeRequest:
    def __init__(self):
        self.headers = {}
        self.client = None
        self.url = type("U", (), {"path": "/api/admin/integrations"})()
        self.state = type("S", (), {})()
        self.method = "PUT"


@pytest.mark.asyncio
async def test_router_status_codes_and_no_value_in_any_body(db, monkeypatch):
    from fastapi import HTTPException

    from api import integrations as api_integrations

    request, user = _FakeRequest(), _FakeUser()
    canary = "CANARY-VALUE-9f3a"

    body = api_integrations.IntegrationValueRequest(value=canary)
    item = await api_integrations.set_integration("RESEND_API_KEY", body, request, user, db)
    assert canary not in json.dumps(item)

    listing = await api_integrations.list_integrations(request, user, db)
    assert canary not in json.dumps(listing)
    assert listing["encryption_key_configured"] is True
    assert listing["legacy_plaintext_rows"] == 0

    health = await api_integrations.integrations_health(request, user, db)
    assert canary not in json.dumps(health)

    audit = await api_integrations.integrations_audit(request, 50, user, db)
    assert canary not in json.dumps(audit)
    assert audit["items"][0]["entity_id"] == "RESEND_API_KEY"

    # 404 unknown name
    with pytest.raises(HTTPException) as exc:
        await api_integrations.set_integration("NOPE", body, request, user, db)
    assert exc.value.status_code == 404
    assert canary not in json.dumps(exc.value.detail)

    # 422 empty value
    with pytest.raises(HTTPException) as exc:
        await api_integrations.set_integration(
            "NVD_API_KEY",
            api_integrations.IntegrationValueRequest(value="  "),
            request, user, db,
        )
    assert exc.value.status_code == 422

    # 409 operator-managed (ISC-133 at the HTTP layer)
    monkeypatch.setenv("NVD_API_KEY", "operator-value")
    with pytest.raises(HTTPException) as exc:
        await api_integrations.set_integration("NVD_API_KEY", body, request, user, db)
    assert exc.value.status_code == 409
    assert exc.value.detail["managed_by_operator"] is True
    assert "operator-value" not in json.dumps(exc.value.detail)
    monkeypatch.delenv("NVD_API_KEY", raising=False)

    # 404 on delete of an unset name
    with pytest.raises(HTTPException) as exc:
        await api_integrations.clear_integration("ANTHROPIC_API_KEY", request, user, db)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_isc40_router_returns_409_without_a_key(db, monkeypatch):
    from fastapi import HTTPException

    from api import integrations as api_integrations
    from services import crypto
    from services import secrets as secrets_mod

    monkeypatch.delenv("SCF_SECRET_KEY", raising=False)
    crypto.reset()
    secrets_mod.reset_caches()

    with pytest.raises(HTTPException) as exc:
        await api_integrations.set_integration(
            "RESEND_API_KEY",
            api_integrations.IntegrationValueRequest(value="x"),
            _FakeRequest(), _FakeUser(), db,
        )
    assert exc.value.status_code == 409
    assert exc.value.detail["encryption_key_configured"] is False

    listing = await api_integrations.list_integrations(_FakeRequest(), _FakeUser(), db)
    assert listing["encryption_key_configured"] is False


@pytest.mark.asyncio
async def test_isc137_api_key_actor_recorded_without_a_user_id(db):
    from api import integrations as api_integrations

    user = _FakeUser(email="ignored@example.com", auth_method="api_key")
    await api_integrations.set_integration(
        "HIBP_API_KEY",
        api_integrations.IntegrationValueRequest(value="v"),
        _FakeRequest(), user, db,
    )
    row = (
        await db.execute(
            text(
                "SELECT actor, actor_user_id FROM platform_audit_log "
                "WHERE entity_type='integration_secret' ORDER BY created_at DESC LIMIT 1"
            )
        )
    ).fetchone()
    assert row[0] == "api_key:master"
    assert row[1] is None
