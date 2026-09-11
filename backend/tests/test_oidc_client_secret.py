"""Regression: oidc.get_client_secret() must resolve through services.secrets.

The first live Keycloak login on a wizard-provisioned stack (#947) died with
``NameError: name 'get_secret' is not defined`` at the token exchange — the
accessor was rewritten to resolve on every call but the import was never
added, and no test exercised the callback path.
"""

import pytest

import oidc


def test_get_client_secret_resolves_env_value(monkeypatch):
    monkeypatch.delenv("OIDC_CLIENT_SECRET_FILE", raising=False)
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "env-value-for-test")
    assert oidc.get_client_secret() == "env-value-for-test"


def test_get_client_secret_prefers_file_tier(monkeypatch, tmp_path):
    f = tmp_path / "OIDC_CLIENT_SECRET"
    f.write_text("file-value-for-test\n")
    monkeypatch.setenv("OIDC_CLIENT_SECRET_FILE", str(f))
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "env-value-for-test")
    assert oidc.get_client_secret() == "file-value-for-test"


def test_get_client_secret_is_resolved_per_call(monkeypatch):
    monkeypatch.delenv("OIDC_CLIENT_SECRET_FILE", raising=False)
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "first")
    assert oidc.get_client_secret() == "first"
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "second")
    assert oidc.get_client_secret() == "second"


def test_get_client_secret_unset(monkeypatch):
    monkeypatch.delenv("OIDC_CLIENT_SECRET_FILE", raising=False)
    monkeypatch.delenv("OIDC_CLIENT_SECRET", raising=False)
    assert oidc.get_client_secret() in (None, "")
