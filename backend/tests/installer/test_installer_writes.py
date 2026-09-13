"""Generation properties, the sentinel, file modes, and the exact `.env` key set."""

from __future__ import annotations

import json
import os
import re
import stat

import pytest
from cryptography.fernet import Fernet

from installer import writer
from installer.generate import (
    MINIO_USER_LENGTH,
    SECRET_FILE_NAMES,
    fernet_key,
    generate_secrets,
    minio_user,
    token,
)
from installer.writer import AlreadyProvisioned, SENTINEL_NAME

# The default storage choice is the bundled MinIO, so COMPOSE_PROFILES is now
# present on EVERY path — carrying `storage` alone, or `idp,storage` when the
# bundled identity provider is also selected. `test_installer_storage.py` covers
# the no-storage path, where the key can be absent again.
COMMON_KEYS = [
    "SCF_SECRETS_DIR",
    "COMPOSE_FILE",
    "COMPOSE_PROFILES",
    "ENVIRONMENT",
    "OSS_SINGLE_TENANT",
    "DB_HOST",
    "DB_PORT",
    "DB_NAME",
    "DB_USER",
    "DB_SSLMODE",
    "EVIDENCE_STORAGE_BOOTSTRAP",
]
OIDC_KEYS = [
    "OIDC_ISSUER",
    "OIDC_DISCOVERY_URL",
    "OIDC_CLIENT_ID",
    "OIDC_REDIRECT_URI",
    "VITE_OIDC_ENABLED",
]
EXPECTED_ENV_KEYS = {
    "bundled_keycloak": COMMON_KEYS
    + ["KC_ADMIN_USER", "BOOTSTRAP_ADMIN_EMAIL"]
    + OIDC_KEYS,
    "external_oidc": COMMON_KEYS + OIDC_KEYS,
    "none": COMMON_KEYS,
}


# ---------------------------------------------------------------- generation
def test_there_are_exactly_ten_secret_files():
    assert len(SECRET_FILE_NAMES) == 10
    assert len(set(SECRET_FILE_NAMES)) == 10
    assert "VITE_API_KEY" not in SECRET_FILE_NAMES


def test_generated_tokens_are_long_and_unique():
    values = {token() for _ in range(50)}
    assert len(values) == 50
    assert all(len(v) >= 40 for v in values)


def test_the_secret_key_is_a_valid_fernet_key_not_a_url_safe_token():
    key = fernet_key()
    assert len(key) == 44
    Fernet(key.encode())  # raises when the key is not a Fernet key


def test_the_minio_user_is_twenty_lowercase_alphanumerics():
    for _ in range(20):
        user = minio_user()
        assert len(user) == MINIO_USER_LENGTH
        assert re.fullmatch(r"[a-z0-9]{20}", user)


def test_the_application_credential_is_not_the_minio_root_credential():
    """This assertion is the inverse of the one it replaces (criterion 37).

    Until Phase 4 the installer set ``AWS_ACCESS_KEY_ID`` /
    ``AWS_SECRET_ACCESS_KEY`` **equal** to ``MINIO_ROOT_USER`` /
    ``MINIO_ROOT_PASSWORD``, and this test asserted that equality as the
    contract. The application therefore talked to the object store holding every
    evidence file as its root account. The pair is now independent and
    ``minio-init`` gives it a MinIO user whose policy names one bucket.

    Full storage coverage lives in ``test_installer_storage.py``; this one stays
    here so the file that once pinned the equality now pins its removal.
    """
    values = generate_secrets(db_type="bundled", idp_type="bundled_keycloak")
    assert values["AWS_ACCESS_KEY_ID"] != values["MINIO_ROOT_USER"]
    assert values["AWS_SECRET_ACCESS_KEY"] != values["MINIO_ROOT_PASSWORD"]


def test_every_name_is_generated_and_no_value_is_a_placeholder():
    values = generate_secrets(db_type="bundled", idp_type="bundled_keycloak")
    assert set(values) == set(SECRET_FILE_NAMES)
    assert not any(writer.is_placeholder(v) for v in values.values())


def test_an_external_database_keeps_the_operator_password():
    values = generate_secrets(
        db_type="external", idp_type="none", external_db_password="operator-choice"
    )
    assert values["DB_PASSWORD"] == "operator-choice"


def test_external_oidc_keeps_the_supplied_client_secret_and_still_makes_a_kc_password():
    values = generate_secrets(
        db_type="bundled", idp_type="external_oidc", external_oidc_client_secret="from-okta"
    )
    assert values["OIDC_CLIENT_SECRET"] == "from-okta"
    assert values["KC_ADMIN_PASSWORD"]


def test_idp_none_leaves_the_oidc_secret_empty():
    values = generate_secrets(db_type="bundled", idp_type="none")
    assert values["OIDC_CLIENT_SECRET"] == ""


def test_an_unknown_tier_is_rejected():
    with pytest.raises(ValueError):
        generate_secrets(db_type="mysql", idp_type="none")
    with pytest.raises(ValueError):
        generate_secrets(db_type="bundled", idp_type="ldap")


# ------------------------------------------------------------------ sentinel
def test_the_sentinel_is_created_once(tmp_path):
    writer.create_sentinel(tmp_path, db="bundled", idp="none")
    payload = json.loads((tmp_path / SENTINEL_NAME).read_text())
    # 2 since the storage choice joined db and idp in the payload (Phase 4).
    assert payload["version"] == 2
    assert payload["db"] == "bundled"
    with pytest.raises(AlreadyProvisioned):
        writer.create_sentinel(tmp_path, db="bundled", idp="none")


def test_a_dangling_symlink_sentinel_is_a_refusal_not_an_absence(tmp_path):
    """`os.path.exists` follows symlinks; O_EXCL|O_NOFOLLOW does not."""
    os.symlink(tmp_path / "nowhere", tmp_path / SENTINEL_NAME)
    assert writer.is_provisioned(tmp_path) is True
    with pytest.raises(AlreadyProvisioned):
        writer.create_sentinel(tmp_path, db="bundled", idp="none")


def test_the_sentinel_is_0600(tmp_path):
    path = writer.create_sentinel(tmp_path, db="bundled", idp="none")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


# --------------------------------------------------------------- secret files
def test_all_ten_files_are_written_0600_even_when_empty(tmp_path):
    values = generate_secrets(db_type="bundled", idp_type="none")
    writer.write_secret_files(tmp_path, values)
    for name in SECRET_FILE_NAMES:
        path = tmp_path / name
        assert path.exists(), name
        assert stat.S_IMODE(path.stat().st_mode) == 0o600, name


def test_an_existing_secret_file_is_never_overwritten(tmp_path):
    writer.write_secret_file(tmp_path, "SCF_SECRET_KEY", "the-original-key")
    values = generate_secrets(db_type="bundled", idp_type="none")
    created = writer.write_secret_files(tmp_path, values)
    assert created["SCF_SECRET_KEY"] is False
    assert (tmp_path / "SCF_SECRET_KEY").read_text() == "the-original-key"
    assert created["API_KEY"] is True


def test_the_secrets_directory_is_0700(tmp_path):
    target = tmp_path / "secrets"
    writer.ensure_secrets_dir(target)
    assert stat.S_IMODE(target.stat().st_mode) == 0o700


def test_the_provisioning_token_is_read_and_deleted(tmp_path):
    (tmp_path / writer.TOKEN_NAME).write_text("tok\n")
    assert writer.read_provision_token(tmp_path) == "tok"
    assert writer.delete_provision_token(tmp_path) is True
    assert writer.read_provision_token(tmp_path) is None


# ------------------------------------------------------------------ .env keys
@pytest.mark.parametrize("idp_type", sorted(EXPECTED_ENV_KEYS))
def test_the_env_key_set_is_exactly_the_contract_set(idp_type):
    content = writer.build_env(
        host_secrets_dir="/home/op/.scf/secrets",
        db={"type": "bundled"},
        idp={
            "type": idp_type,
            "kc_admin_user": "admin",
            "bootstrap_admin_email": "OP@Example.test",
            "oidc_issuer": "https://issuer.example.test/oauth2",
            "oidc_client_id": "scf",
        },
    )
    assert writer.env_keys(content) == EXPECTED_ENV_KEYS[idp_type]


def test_the_env_never_contains_a_generated_credential():
    values = generate_secrets(db_type="bundled", idp_type="bundled_keycloak")
    content = writer.build_env(
        host_secrets_dir="/home/op/.scf/secrets",
        db={"type": "bundled"},
        idp={"type": "bundled_keycloak", "bootstrap_admin_email": "op@example.test"},
    )
    for name, value in values.items():
        assert name not in writer.env_keys(content), name
        if value:
            assert value not in content, name


def test_the_bootstrap_email_is_lower_cased():
    content = writer.build_env(
        host_secrets_dir="/s",
        db={"type": "bundled"},
        idp={"type": "bundled_keycloak", "bootstrap_admin_email": "OP@Example.TEST"},
    )
    assert "BOOTSTRAP_ADMIN_EMAIL=op@example.test" in content


def test_an_external_database_lands_in_the_env_without_its_password():
    content = writer.build_env(
        host_secrets_dir="/s",
        db={
            "type": "external",
            "host": "db.example.test",
            "port": 6543,
            "dbname": "scf",
            "user": "scfuser",
            "sslmode": "verify-full",
        },
        idp={"type": "none"},
    )
    assert "DB_HOST=db.example.test" in content
    assert "DB_SSLMODE=verify-full" in content
    assert "password" not in content.lower()


def test_the_env_header_names_the_installer_and_the_secrets_directory():
    content = writer.build_env(host_secrets_dir="/s", db={"type": "bundled"}, idp={"type": "none"})
    assert content.startswith("# Generated by scripts/install.sh on ")
    assert "Secrets live in SCF_SECRETS_DIR, not here." in content


def test_the_env_file_is_written_0600(tmp_path):
    path = writer.write_env(tmp_path, "A=1\n")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_placeholder_detection_matches_the_contract():
    for value in (None, "", "   ", "changeme", "CHANGEME-really", "minioadmin", "CHANGE_ME_NOW"):
        assert writer.is_placeholder(value) is True, value
    for value in ("a-real-value", "MinioAdminister"):
        assert writer.is_placeholder(value) is False, value
