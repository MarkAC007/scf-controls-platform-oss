"""Build the database DSN in-process instead of on the host.

`DATABASE_URL` used to be assembled by docker compose, which meant the password
appeared in `docker inspect`, in every process's environment, and in any crash
dump that echoed the environment. Composing it here lets the password live in a
0600 file that only the resolver reads.

`DATABASE_URL` still wins when it is set, byte for byte, so existing
deployments and managed-Postgres connection strings are untouched.
"""
import os
from typing import Optional
from urllib.parse import quote

from services.secrets import get_secret

DEFAULT_HOST = "localhost"
DEFAULT_PORT = "5432"
DEFAULT_NAME = "cg_scf"
DEFAULT_USER = "cg"


def _to_sync(dsn: str) -> str:
    """The asyncpg-to-psycopg2 rewrite the sync task modules have always done."""
    return dsn.replace("+asyncpg", "+psycopg2").replace("?ssl=require", "?sslmode=require")


def _env(name: str, fallback: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        return fallback
    return value.strip()


def _ssl_suffix(param: str) -> str:
    mode = (os.getenv("DB_SSLMODE") or "").strip()
    if not mode or mode == "disable":
        return ""
    return f"?{param}={mode}"


def _compose(driver: str, ssl_param: str, default: Optional[str]) -> Optional[str]:
    password = get_secret("DB_PASSWORD")
    if password is None or not password.strip():
        if default is not None:
            return None  # caller falls back to its historical default
        password = ""

    user = quote(_env("DB_USER", DEFAULT_USER), safe="")
    secret = quote(password, safe="")
    host = _env("DB_HOST", DEFAULT_HOST)
    port = _env("DB_PORT", DEFAULT_PORT)
    name = _env("DB_NAME", DEFAULT_NAME)
    return f"{driver}://{user}:{secret}@{host}:{port}/{name}{_ssl_suffix(ssl_param)}"


def get_database_url(default: Optional[str] = None) -> str:
    """The async (asyncpg) DSN.

    `DATABASE_URL` wins unchanged. Otherwise the DSN is composed from
    DB_HOST/DB_PORT/DB_NAME/DB_USER and the resolved DB_PASSWORD. When no
    password resolves and a `default` is supplied, the default is returned so
    development and test behaviour is exactly what it was.
    """
    dsn = os.getenv("DATABASE_URL")
    if dsn and dsn.strip():
        return dsn

    composed = _compose("postgresql+asyncpg", "ssl", default)
    if composed is not None:
        return composed
    return default  # type: ignore[return-value]


def get_sync_database_url(default: Optional[str] = None) -> str:
    """The sync (psycopg2) DSN, for Celery tasks and the composite service."""
    dsn = os.getenv("DATABASE_URL")
    if dsn and dsn.strip():
        return _to_sync(dsn)

    composed = _compose("postgresql+psycopg2", "sslmode", default)
    if composed is not None:
        return composed
    return _to_sync(default)  # type: ignore[arg-type]
