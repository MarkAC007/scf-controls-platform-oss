"""Crypto contract tests (Issue #947, contract §3a).

Covers ISC-20..25, ISC-40 (no key configured) and ISC-23 (the encryption key is
never derived from another credential).
"""
import os
import re

import pytest
from cryptography.fernet import Fernet

from services import crypto
from services import secrets as secrets_mod

KEY_A = Fernet.generate_key().decode()
KEY_B = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("SCF_SECRET_KEY", "SCF_SECRET_KEY_FILE"):
        monkeypatch.delenv(name, raising=False)
    crypto.reset()
    secrets_mod.reset_caches()
    yield
    crypto.reset()
    secrets_mod.reset_caches()


def test_no_key_configured_returns_none(monkeypatch):
    assert crypto.get_fernet() is None


def test_encrypt_without_key_raises_secret_key_missing():
    with pytest.raises(crypto.SecretKeyMissing):
        crypto.encrypt("hunter2")


def test_round_trip_is_prefixed_and_hides_the_plaintext(monkeypatch):
    monkeypatch.setenv("SCF_SECRET_KEY", KEY_A)
    token = crypto.encrypt("s3cret-value")
    assert token.startswith(crypto.PREFIX)
    assert "s3cret-value" not in token
    assert crypto.is_encrypted(token)
    assert crypto.decrypt(token) == "s3cret-value"


def test_ciphertext_is_non_deterministic(monkeypatch):
    """Why invite lookups moved to a hash: the same input encrypts differently."""
    monkeypatch.setenv("SCF_SECRET_KEY", KEY_A)
    assert crypto.encrypt("same") != crypto.encrypt("same")


def test_first_key_is_primary_and_others_still_decrypt(monkeypatch):
    monkeypatch.setenv("SCF_SECRET_KEY", KEY_A)
    old = crypto.encrypt("rotate-me")

    # New key first, old key retained: the old ciphertext must still open.
    monkeypatch.setenv("SCF_SECRET_KEY", f"{KEY_B},{KEY_A}")
    crypto.reset()
    secrets_mod.reset_caches()
    assert crypto.decrypt(old) == "rotate-me"

    new = crypto.encrypt("rotate-me")
    # Dropping the old key must leave the NEW ciphertext readable.
    monkeypatch.setenv("SCF_SECRET_KEY", KEY_B)
    crypto.reset()
    secrets_mod.reset_caches()
    assert crypto.decrypt(new) == "rotate-me"
    with pytest.raises(crypto.DecryptError):
        crypto.decrypt(old)


def test_instance_is_cached_per_key_string(monkeypatch):
    monkeypatch.setenv("SCF_SECRET_KEY", KEY_A)
    first = crypto.get_fernet()
    assert crypto.get_fernet() is first

    monkeypatch.setenv("SCF_SECRET_KEY", KEY_B)
    secrets_mod.reset_caches()
    assert crypto.get_fernet() is not first


def test_invalid_key_part_raises_secret_key_invalid(monkeypatch):
    monkeypatch.setenv("SCF_SECRET_KEY", f"{KEY_A},not-a-fernet-key")
    with pytest.raises(crypto.SecretKeyInvalid) as exc:
        crypto.get_fernet()
    assert "not-a-fernet-key" not in str(exc.value), "error must name the variable, not the value"


def test_unknown_ciphertext_raises_decrypt_error(monkeypatch):
    monkeypatch.setenv("SCF_SECRET_KEY", KEY_A)
    foreign = crypto.PREFIX + Fernet(Fernet.generate_key()).encrypt(b"x").decode()
    with pytest.raises(crypto.DecryptError):
        crypto.decrypt(foreign)


def test_legacy_plaintext_is_returned_and_counted(monkeypatch):
    monkeypatch.setenv("SCF_SECRET_KEY", KEY_A)
    assert crypto.legacy_read_count() == 0
    assert crypto.decrypt("plain-legacy-value") == "plain-legacy-value"
    assert crypto.decrypt("another") == "another"
    assert crypto.legacy_read_count() == 2


def test_legacy_read_works_with_no_key_at_all():
    assert crypto.decrypt("plain-legacy-value") == "plain-legacy-value"
    assert crypto.legacy_read_count() == 1


class _Dialect:
    pass


def test_encrypted_string_bind_and_result(monkeypatch):
    monkeypatch.setenv("SCF_SECRET_KEY", KEY_A)
    col = crypto.EncryptedString()
    assert col.process_bind_param(None, _Dialect()) is None

    stored = col.process_bind_param("whsec_abc123", _Dialect())
    assert stored.startswith(crypto.PREFIX)
    assert "whsec_abc123" not in stored
    assert col.process_result_value(stored, _Dialect()) == "whsec_abc123"


def test_encrypted_string_does_not_double_encrypt(monkeypatch):
    monkeypatch.setenv("SCF_SECRET_KEY", KEY_A)
    col = crypto.EncryptedString()
    once = col.process_bind_param("value", _Dialect())
    twice = col.process_bind_param(once, _Dialect())
    assert twice == once
    assert col.process_result_value(twice, _Dialect()) == "value"


def test_encrypted_string_stores_plaintext_when_no_key():
    """A keyless legacy install must keep working, not start raising."""
    col = crypto.EncryptedString()
    stored = col.process_bind_param("value", _Dialect())
    assert stored == "value"
    assert col.process_result_value(stored, _Dialect()) == "value"


def test_isc23_key_is_never_derived_from_another_credential():
    """The encryption key must not fall back to any other secret."""
    source = open(crypto.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
    for forbidden in ("API_KEY", "DB_PASSWORD", "DOWNLOAD_TOKEN_SECRET", "token_urlsafe"):
        assert forbidden not in source, f"crypto.py must not reference {forbidden}"
    assert 'get_secret("SCF_SECRET_KEY")' in source or '_KEY_NAME = "SCF_SECRET_KEY"' in source
