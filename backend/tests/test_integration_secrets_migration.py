"""The migration must survive a database full of legacy plaintext (ISC-38/39).

An existing installation has plaintext invite tokens and plaintext webhook
secrets and has never had an encryption key. The upgrade has to run to
completion there, and the two things those columns are FOR — accepting an
invite by its token, and verifying a webhook signature — have to keep working
immediately afterwards, with no key and no backfill.

This test drives real alembic down and up, so it needs a throwaway database.
"""
import hashlib
import hmac
import os
import subprocess
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# models.py has relationships pointing at classes defined in catalog_models.py;
# without this import SQLAlchemy cannot configure ANY mapper. Pre-existing
# requirement of this codebase, not specific to #947.
import catalog_models  # noqa: F401

DATABASE_URL = os.getenv("DATABASE_URL", "")
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason="requires a throwaway Postgres (DATABASE_URL)",
)

PLAINTEXT_TOKEN = "legacy-invite-token-abcdefgh12345678"
PLAINTEXT_SECRET = "whsec_legacyplaintextsecret1234567890"


def _alembic(*args):
    result = subprocess.run(
        ["python", "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert result.returncode == 0, f"alembic {' '.join(args)} failed:\n{result.stderr}"
    return result


@pytest_asyncio.fixture
async def legacy_db(monkeypatch):
    """Downgrade one revision, seed plaintext rows, upgrade again."""
    from services import crypto
    from services import secrets as secrets_mod

    # No key at all: this is the pre-#947 installation.
    monkeypatch.delenv("SCF_SECRET_KEY", raising=False)
    monkeypatch.delenv("SCF_SECRET_KEY_FILE", raising=False)
    crypto.reset()
    secrets_mod.reset_caches()

    engine = create_async_engine(DATABASE_URL)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    _alembic("downgrade", "auditorgts1")

    org_id = uuid.uuid4()
    async with maker() as session:
        await session.execute(
            text(
                "INSERT INTO organizations (id, name, slug) "
                "VALUES (:id, 'Legacy Org', :slug)"
            ),
            {"id": org_id, "slug": f"legacy-{org_id.hex[:8]}"},
        )
        # Plaintext invite token, exactly as the old code wrote it.
        await session.execute(
            text(
                "INSERT INTO organization_invites "
                "(id, organization_id, email, role, invite_token, status, expires_at) "
                "VALUES (:id, :org, 'invitee@example.com', 'viewer', :tok, 'pending', "
                "now() + interval '7 days')"
            ),
            {"id": uuid.uuid4(), "org": org_id, "tok": PLAINTEXT_TOKEN},
        )
        # Plaintext webhook secret.
        await session.execute(
            text(
                "INSERT INTO webhook_endpoints "
                "(id, organization_id, name, secret, secret_prefix) "
                "VALUES (:id, :org, 'legacy hook', :sec, :pre)"
            ),
            {
                "id": uuid.uuid4(),
                "org": org_id,
                "sec": PLAINTEXT_SECRET,
                "pre": PLAINTEXT_SECRET[:12],
            },
        )
        await session.commit()

    # The upgrade under test: must need no key and touch no plaintext.
    _alembic("upgrade", "intsec947a1")

    async with maker() as session:
        yield session, org_id

    async with maker() as session:
        await session.execute(
            text("DELETE FROM webhook_endpoints WHERE organization_id = :o"), {"o": org_id}
        )
        await session.execute(
            text("DELETE FROM organization_invites WHERE organization_id = :o"), {"o": org_id}
        )
        await session.execute(text("DELETE FROM organizations WHERE id = :o"), {"o": org_id})
        await session.commit()
    await engine.dispose()


@pytest.mark.asyncio
async def test_migration_backfills_the_hash_for_legacy_rows(legacy_db):
    session, org_id = legacy_db
    expected = hashlib.sha256(PLAINTEXT_TOKEN.encode()).hexdigest()
    stored = (
        await session.execute(
            text(
                "SELECT invite_token, invite_token_hash FROM organization_invites "
                "WHERE organization_id = :o"
            ),
            {"o": org_id},
        )
    ).fetchone()
    assert stored[0] == PLAINTEXT_TOKEN, "the migration must not touch the token itself"
    assert stored[1] == expected, "invite_token_hash must be the sha256 hex of the token"


@pytest.mark.asyncio
async def test_invite_is_still_acceptable_by_token_after_the_upgrade(legacy_db):
    """ISC-38: the accept flow works on a legacy row with no key configured."""
    from models import OrganizationInvite
    from services.invite_tokens import hash_invite_token
    from sqlalchemy import select

    session, org_id = legacy_db
    result = await session.execute(
        select(OrganizationInvite).where(
            OrganizationInvite.invite_token_hash == hash_invite_token(PLAINTEXT_TOKEN)
        )
    )
    invite = result.scalar_one_or_none()
    assert invite is not None, "legacy invite became unfindable after the upgrade"
    assert invite.email == "invitee@example.com"
    # Read back through EncryptedString: legacy plaintext passes straight through.
    assert invite.invite_token == PLAINTEXT_TOKEN


@pytest.mark.asyncio
async def test_webhook_signature_still_verifies_after_the_upgrade(legacy_db):
    """ISC-39: HMAC verification needs the value back, and still gets it."""
    from models import WebhookEndpoint
    from sqlalchemy import select

    session, org_id = legacy_db
    result = await session.execute(
        select(WebhookEndpoint).where(WebhookEndpoint.organization_id == org_id)
    )
    endpoint = result.scalar_one()
    assert endpoint.secret == PLAINTEXT_SECRET

    payload = b'{"evidence_id":"E-1"}'
    expected = hmac.new(PLAINTEXT_SECRET.encode(), payload, hashlib.sha256).hexdigest()
    recomputed = hmac.new(endpoint.secret.encode(), payload, hashlib.sha256).hexdigest()
    assert hmac.compare_digest(expected, recomputed)
    assert endpoint.secret_prefix == PLAINTEXT_SECRET[:12]


@pytest.mark.asyncio
async def test_new_writes_encrypt_once_a_key_is_configured(legacy_db, monkeypatch):
    """The mixed state a rolling upgrade actually produces: some rows encrypted."""
    from cryptography.fernet import Fernet
    from models import WebhookEndpoint
    from services import crypto
    from services import secrets as secrets_mod
    from sqlalchemy import select

    session, org_id = legacy_db
    monkeypatch.setenv("SCF_SECRET_KEY", Fernet.generate_key().decode())
    crypto.reset()
    secrets_mod.reset_caches()

    new_id = uuid.uuid4()
    session.add(
        WebhookEndpoint(
            id=new_id,
            organization_id=org_id,
            name="new hook",
            secret="whsec_brandnewsecret",
            secret_prefix="whsec_brandn",
        )
    )
    await session.commit()

    raw = (
        await session.execute(
            text("SELECT secret FROM webhook_endpoints WHERE id = :i"), {"i": new_id}
        )
    ).scalar()
    assert raw.startswith("enc:v1:")
    assert "whsec_brandnewsecret" not in raw

    session.expire_all()
    endpoint = (
        await session.execute(select(WebhookEndpoint).where(WebhookEndpoint.id == new_id))
    ).scalar_one()
    assert endpoint.secret == "whsec_brandnewsecret"

    # The legacy row in the same column still reads fine alongside it.
    legacy = (
        await session.execute(
            select(WebhookEndpoint).where(WebhookEndpoint.name == "legacy hook")
        )
    ).scalar_one()
    assert legacy.secret == PLAINTEXT_SECRET
