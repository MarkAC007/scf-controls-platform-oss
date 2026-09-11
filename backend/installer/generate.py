"""Credential generation.

Pure functions, no I/O.  Nothing here accepts an operator-chosen password for a
credential the platform owns: every machine credential comes from ``secrets``
and is never displayed.  The only operator-supplied values that survive are the
external database password and an external OIDC client secret, which belong to
systems the platform does not own.
"""

from __future__ import annotations

import secrets as _secrets
import string

from cryptography.fernet import Fernet

# The ten files the installer always creates in SCF_SECRETS_DIR.  Unused ones are
# created EMPTY so `docker compose config` never fails on a missing secret path.
SECRET_FILE_NAMES: tuple[str, ...] = (
    "DB_PASSWORD",
    "SCF_SECRET_KEY",
    "API_KEY",
    "DOWNLOAD_TOKEN_SECRET",
    "MINIO_ROOT_USER",
    "MINIO_ROOT_PASSWORD",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "KC_ADMIN_PASSWORD",
    "OIDC_CLIENT_SECRET",
)

# MinIO access keys must be alphanumeric; lower-case + digits keeps them
# copy-pasteable and shell-safe.
_MINIO_USER_ALPHABET = string.ascii_lowercase + string.digits
MINIO_USER_LENGTH = 20
TOKEN_BYTES = 32

DB_TYPES = ("bundled", "external")
IDP_TYPES = ("bundled_keycloak", "external_oidc", "none")


def token() -> str:
    """A generated credential: 32 random bytes, url-safe base64."""
    return _secrets.token_urlsafe(TOKEN_BYTES)


def fernet_key() -> str:
    """SCF_SECRET_KEY must be a valid Fernet key, never a url-safe token."""
    return Fernet.generate_key().decode()


def minio_user() -> str:
    return "".join(_secrets.choice(_MINIO_USER_ALPHABET) for _ in range(MINIO_USER_LENGTH))


def provision_token() -> str:
    """Token install.sh hands the operator; the wizard compares it constant-time."""
    return _secrets.token_urlsafe(30)[:40]


def generate_secrets(
    *,
    db_type: str,
    idp_type: str,
    external_db_password: str | None = None,
    external_oidc_client_secret: str | None = None,
) -> dict[str, str]:
    """Return the full ten-name mapping written to SCF_SECRETS_DIR.

    Values may be empty strings — an empty file means "unset" to every reader.
    """
    if db_type not in DB_TYPES:
        raise ValueError(f"unknown db type: {db_type!r}")
    if idp_type not in IDP_TYPES:
        raise ValueError(f"unknown idp type: {idp_type!r}")

    minio_root_user = minio_user()
    minio_root_password = token()

    if db_type == "external":
        db_password = external_db_password or ""
    else:
        db_password = token()

    if idp_type == "bundled_keycloak":
        # The bundled IdP is ours, so its client secret is generated too.
        oidc_client_secret = token()
    elif idp_type == "external_oidc":
        oidc_client_secret = external_oidc_client_secret or ""
    else:
        oidc_client_secret = ""

    return {
        "DB_PASSWORD": db_password,
        "SCF_SECRET_KEY": fernet_key(),
        "API_KEY": token(),
        "DOWNLOAD_TOKEN_SECRET": token(),
        "MINIO_ROOT_USER": minio_root_user,
        "MINIO_ROOT_PASSWORD": minio_root_password,
        # boto3 talks to MinIO with the root credentials on the bundled path.
        "AWS_ACCESS_KEY_ID": minio_root_user,
        "AWS_SECRET_ACCESS_KEY": minio_root_password,
        # Always generated: the Keycloak master admin is an infrastructure
        # account no human ever uses, and an unused value is harmless.
        "KC_ADMIN_PASSWORD": token(),
        "OIDC_CLIENT_SECRET": oidc_client_secret,
    }
