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

# The third tier, alongside the database and the identity provider: does this
# install ship its own object store, or does the operator bring one?
#
# ``none`` does not mean "evidence storage is unconfigured and broken". It means
# no object store is *bundled*: the `storage` compose profile stays inactive, no
# MinIO credential is generated, and an organisation administrator points the
# platform at their own S3-compatible store from the Settings screen. That is
# the whole point of the bring-your-own work — the bundled MinIO becomes one
# option rather than the only one.
STORAGE_BUNDLED_MINIO = "bundled_minio"
STORAGE_NONE = "none"
STORAGE_TYPES = (STORAGE_BUNDLED_MINIO, STORAGE_NONE)
#: What an install that says nothing about storage gets — the bundled MinIO,
#: which is what every install got before this choice existed.
DEFAULT_STORAGE_TYPE = STORAGE_BUNDLED_MINIO


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
    storage_type: str = DEFAULT_STORAGE_TYPE,
    external_db_password: str | None = None,
    external_oidc_client_secret: str | None = None,
) -> dict[str, str]:
    """Return the full ten-name mapping written to SCF_SECRETS_DIR.

    Values may be empty strings — an empty file means "unset" to every reader.

    On the bundled-storage path this returns **two** independent MinIO
    credentials, which is a change of contract worth stating plainly. Until now
    ``AWS_ACCESS_KEY_ID`` / ``AWS_SECRET_ACCESS_KEY`` were set *equal* to
    ``MINIO_ROOT_USER`` / ``MINIO_ROOT_PASSWORD``, so the application talked to
    the object store that holds every evidence file as its **root account**: a
    credential leaked from the application was root of every bucket, could
    create and delete users, and could read the console. The pair is now
    generated on its own and ``minio-init`` creates a MinIO user for it with a
    policy naming one bucket and no administrative action at all.

    Nothing about ``MINIO_ROOT_*`` changes: it stays host-only, stays out of the
    database tier (``services/secrets.py`` ``NEVER_DB_NAMES``), and remains the
    account ``minio-init`` uses to do the provisioning.
    """
    if db_type not in DB_TYPES:
        raise ValueError(f"unknown db type: {db_type!r}")
    if idp_type not in IDP_TYPES:
        raise ValueError(f"unknown idp type: {idp_type!r}")
    if storage_type not in STORAGE_TYPES:
        raise ValueError(
            f"unknown storage type: {storage_type!r} "
            f"(expected one of: {', '.join(STORAGE_TYPES)})"
        )

    if storage_type == STORAGE_BUNDLED_MINIO:
        minio_root_user = minio_user()
        minio_root_password = token()
        # Distinct from the root pair, deliberately. See the docstring.
        app_access_key_id = minio_user()
        app_secret_access_key = token()
    else:
        # No bundled object store: the `storage` profile is inactive, so neither
        # `minio` nor `minio-init` starts and neither credential has a consumer.
        # All four names are still WRITTEN, as empty files — the secrets overlay
        # declares ten sources and `docker compose config` fails on a missing
        # one (docker-compose.secrets.yml, "All ten files must exist").
        minio_root_user = ""
        minio_root_password = ""
        app_access_key_id = ""
        app_secret_access_key = ""

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
        # The object store's OWN root account. Host-only, never in the database
        # tier, and used by `minio-init` alone — not by the application.
        "MINIO_ROOT_USER": minio_root_user,
        "MINIO_ROOT_PASSWORD": minio_root_password,
        # The application's scoped credential. `minio-init` creates a MinIO user
        # for this pair whose policy is confined to the evidence bucket.
        "AWS_ACCESS_KEY_ID": app_access_key_id,
        "AWS_SECRET_ACCESS_KEY": app_secret_access_key,
        # Always generated: the Keycloak master admin is an infrastructure
        # account no human ever uses, and an unused value is harmless.
        "KC_ADMIN_PASSWORD": token(),
        "OIDC_CLIENT_SECRET": oidc_client_secret,
    }
