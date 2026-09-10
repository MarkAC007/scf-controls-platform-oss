"""Download-token signing secret is resolved per call (issue #947, Lane C).

Before this change `_get_secret()` memoised into a module global that nothing
ever cleared, so the first resolve froze the signing key for the life of the
process: rotating DOWNLOAD_TOKEN_SECRET needed a restart of both the API and
every Celery worker.

Semantics kept, deliberately: exactly ONE secret is live at a time. Verification
uses whatever is configured right now, so a rotation immediately invalidates
outstanding links. There is no grace window and none is added here; the existing
code has always had a single-secret contract and evidence links are short-lived
(900s default). The API_KEY fallback is kept (contract D7).
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import download_token  # noqa: E402

FILE_ID = "11111111-1111-1111-1111-111111111111"
ORG_ID = "22222222-2222-2222-2222-222222222222"
USER_ID = "33333333-3333-3333-3333-333333333333"


@pytest.fixture(autouse=True)
def _clean_secret_env(monkeypatch):
    for name in ("DOWNLOAD_TOKEN_SECRET", "API_KEY"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(f"{name}_FILE", raising=False)
    yield


def test_secret_is_not_memoised_in_a_module_global():
    """The never-invalidated `_SECRET` global is gone."""
    assert not hasattr(download_token, "_SECRET")


def test_rotation_takes_effect_without_a_restart(monkeypatch):
    monkeypatch.setenv("DOWNLOAD_TOKEN_SECRET", "secret-old")
    minted = download_token.generate_download_token(FILE_ID, ORG_ID, USER_ID)
    assert minted is not None
    token, expires = minted
    assert download_token.verify_download_token(FILE_ID, ORG_ID, token, expires) == USER_ID

    # Rotate in the same process — no reimport, no restart.
    monkeypatch.setenv("DOWNLOAD_TOKEN_SECRET", "secret-new")
    assert download_token.verify_download_token(FILE_ID, ORG_ID, token, expires) is None

    # A token minted under the new secret verifies under the new secret.
    minted_new = download_token.generate_download_token(FILE_ID, ORG_ID, USER_ID)
    assert minted_new is not None
    new_token, new_expires = minted_new
    assert new_token != token
    assert download_token.verify_download_token(
        FILE_ID, ORG_ID, new_token, new_expires
    ) == USER_ID

    # Rotating back makes the original token valid again — proof that
    # verification reads the live value rather than a frozen one.
    monkeypatch.setenv("DOWNLOAD_TOKEN_SECRET", "secret-old")
    assert download_token.verify_download_token(FILE_ID, ORG_ID, token, expires) == USER_ID


def test_signing_secret_reflects_the_live_value(monkeypatch):
    monkeypatch.setenv("DOWNLOAD_TOKEN_SECRET", "first")
    assert download_token.signing_secret() == "first"

    monkeypatch.setenv("DOWNLOAD_TOKEN_SECRET", "second")
    assert download_token.signing_secret() == "second"


def test_api_key_fallback_is_kept(monkeypatch):
    monkeypatch.setenv("API_KEY", "master-key")
    assert download_token.signing_secret() == "master-key"

    minted = download_token.generate_download_token(FILE_ID, ORG_ID, USER_ID)
    assert minted is not None
    token, expires = minted
    assert download_token.verify_download_token(FILE_ID, ORG_ID, token, expires) == USER_ID


def test_dedicated_secret_beats_the_api_key_fallback(monkeypatch):
    monkeypatch.setenv("API_KEY", "master-key")
    monkeypatch.setenv("DOWNLOAD_TOKEN_SECRET", "dedicated")
    assert download_token.signing_secret() == "dedicated"


def test_secret_can_come_from_a_file(tmp_path, monkeypatch):
    secret_file = tmp_path / "DOWNLOAD_TOKEN_SECRET"
    secret_file.write_text("from-a-0600-file\n")
    monkeypatch.setenv("DOWNLOAD_TOKEN_SECRET_FILE", str(secret_file))

    assert download_token.signing_secret() == "from-a-0600-file"
    minted = download_token.generate_download_token(FILE_ID, ORG_ID, USER_ID)
    assert minted is not None
    token, expires = minted
    assert download_token.verify_download_token(FILE_ID, ORG_ID, token, expires) == USER_ID


def test_no_secret_refuses_to_sign_or_verify(monkeypatch):
    assert download_token.signing_secret() is None
    assert download_token.generate_download_token(FILE_ID, ORG_ID, USER_ID) is None
    assert download_token.verify_download_token(FILE_ID, ORG_ID, "u.deadbeef", 1) is None


def test_a_download_token_secret_row_in_the_db_is_never_used(monkeypatch):
    """DOWNLOAD_TOKEN_SECRET is a NEVER_DB name: a poisoned provider row must
    not become the signing key."""
    from services import secrets

    secrets.register_db_provider(lambda: {"DOWNLOAD_TOKEN_SECRET": "db-poisoned"})
    secrets.invalidate()
    monkeypatch.setenv("DOWNLOAD_TOKEN_SECRET", "env-value")

    assert download_token.signing_secret() == "env-value"


def test_expired_token_is_rejected(monkeypatch):
    monkeypatch.setenv("DOWNLOAD_TOKEN_SECRET", "secret")
    minted = download_token.generate_download_token(FILE_ID, ORG_ID, USER_ID, ttl_seconds=1)
    assert minted is not None
    token, _ = minted
    past = int(time.time()) - 10

    assert download_token.verify_download_token(FILE_ID, ORG_ID, token, past) is None


def test_token_is_bound_to_file_and_org(monkeypatch):
    monkeypatch.setenv("DOWNLOAD_TOKEN_SECRET", "secret")
    minted = download_token.generate_download_token(FILE_ID, ORG_ID, USER_ID)
    assert minted is not None
    token, expires = minted

    assert download_token.verify_download_token("other-file", ORG_ID, token, expires) is None
    assert download_token.verify_download_token(FILE_ID, "other-org", token, expires) is None
