"""The one accessor for every credential the backend reads.

Resolution order, per credential, evaluated on EVERY call:

    1. database  — tier-3 integration secrets only, via a registered provider
    2. file      — the path in `{NAME}_FILE`, contents stripped
    3. environment — `os.getenv(name, default)`

Nothing is resolved at import. A module that does `API_KEY = os.getenv("API_KEY")`
freezes the value for the life of the process, which is why rotating a key used
to need a restart of the API and every Celery worker. Call `get_secret(name)`
where you used to call `os.getenv(name)` and rotation becomes a no-restart
operation.

An empty value at any tier means "unset at that tier" and falls through to the
next one, so an empty secret file behaves exactly like a missing one.

The database tier is deliberately unreachable for tier-1 and tier-2 credentials:
`get_secret` tests membership of `TIER3_NAMES` before it will so much as call the
provider. A row planted in `integration_secrets` naming `API_KEY` therefore
cannot become the master API key — the check is structural, not a filter someone
can forget to apply.

Only the database tier is cached (60s TTL). Files and environment variables are
cheap to read and must stay live. Because the cache is per process, a rotation
written by the API would otherwise take up to 60s to reach a Celery worker, so
writers call `bump_version()`, which INCRs a shared Redis key that readers check
at most once every 2s. Redis is best effort throughout: if it is unreachable the
TTL alone governs and nothing raises.
"""
import logging
import os
import time
from typing import Callable, Dict, Optional, Tuple
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

# Integration credentials an administrator may store in the database. Closed
# tuple: everything else is file or environment only, by construction.
TIER3_NAMES: Tuple[str, ...] = (
    "OIDC_CLIENT_SECRET",
    "RESEND_API_KEY",
    "ANTHROPIC_API_KEY",
    "AZURE_STORAGE_ACCOUNT_KEY",
    "HIBP_API_KEY",
    "NVD_API_KEY",
)

# Credentials the database tier must never be able to supply. Bootstrap secrets
# and anything that would let a database row escalate into control of the
# platform's own authentication or storage.
NEVER_DB_NAMES: Tuple[str, ...] = (
    "SCF_SECRET_KEY",
    "API_KEY",
    "DOWNLOAD_TOKEN_SECRET",
    "DB_PASSWORD",
    "MINIO_ROOT_USER",
    "MINIO_ROOT_PASSWORD",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "KC_ADMIN_PASSWORD",
)

REDIS_VERSION_KEY = "scf:secrets:version"
CACHE_TTL_SECONDS = 60.0
VERSION_CHECK_INTERVAL_SECONDS = 2.0
REDIS_SOCKET_TIMEOUT = 0.2

_provider: Optional[Callable[[], Dict[str, str]]] = None
_db_cache: Optional[Dict[str, str]] = None
_db_cache_at: float = 0.0
_db_version: Optional[str] = None
_version_checked_at: float = 0.0
_redis_client = None
_redis_unavailable = False


# ---------------------------------------------------------------------------
# Placeholders
# ---------------------------------------------------------------------------

def is_placeholder(value: Optional[str]) -> bool:
    """True when a value is missing or is one of the shipped stand-ins.

    The `.env.example` defaults exist so a developer can boot the stack; they
    must never reach production, and startup checks use this to say so.
    """
    if value is None:
        return True
    stripped = value.strip()
    if not stripped:
        return True
    lowered = stripped.lower()
    return (
        lowered.startswith("changeme")
        or lowered.startswith("change_me")
        or lowered == "minioadmin"
    )


# ---------------------------------------------------------------------------
# Redis version key (best effort, never fatal)
# ---------------------------------------------------------------------------

def _get_redis():
    """A lazily built sync Redis client, or None when Redis is unusable."""
    global _redis_client, _redis_unavailable
    if _redis_unavailable:
        return None
    if _redis_client is not None:
        return _redis_client
    try:
        import redis  # imported lazily so tests need no broker

        _redis_client = redis.Redis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            socket_timeout=REDIS_SOCKET_TIMEOUT,
            socket_connect_timeout=REDIS_SOCKET_TIMEOUT,
        )
    except Exception:  # pragma: no cover - import/URL problems are not fatal
        _redis_unavailable = True
        return None
    return _redis_client


def _read_version() -> Optional[str]:
    client = _get_redis()
    if client is None:
        return None
    try:
        raw = client.get(REDIS_VERSION_KEY)
    except Exception:
        return None
    if raw is None:
        return None
    if isinstance(raw, bytes):
        return raw.decode("utf-8", "replace")
    return str(raw)


def bump_version() -> None:
    """Announce that a stored secret changed, then drop this process's cache.

    Called by whoever writes a tier-3 secret. The INCR is what lets the other
    processes notice inside a couple of seconds instead of a minute.
    """
    client = _get_redis()
    if client is not None:
        try:
            client.incr(REDIS_VERSION_KEY)
        except Exception:
            logger.debug("Could not bump the secrets version key", exc_info=True)
    invalidate()


# ---------------------------------------------------------------------------
# Provider registration and the database tier
# ---------------------------------------------------------------------------

def register_db_provider(fn: Optional[Callable[[], Dict[str, str]]]) -> None:
    """Install the callable that returns `{name: plaintext}` for configured
    tier-3 rows. Passing None removes it. Registering always drops the cache."""
    global _provider
    _provider = fn
    invalidate()


def invalidate(name: Optional[str] = None) -> None:
    """Drop the cached database tier. `name` is accepted for call-site clarity;
    the provider returns every row at once, so there is nothing finer to drop."""
    global _db_cache, _db_cache_at, _version_checked_at
    _db_cache = None
    _db_cache_at = 0.0
    _version_checked_at = 0.0


def reset_caches() -> None:
    """Test hook: forget every cached value but keep the provider registered."""
    global _db_version
    _db_version = None
    invalidate()


def _db_values() -> Dict[str, str]:
    global _db_cache, _db_cache_at, _db_version, _version_checked_at

    if _provider is None:
        return {}

    now = time.monotonic()
    stale = _db_cache is None or (now - _db_cache_at) >= CACHE_TTL_SECONDS

    if not stale and (now - _version_checked_at) >= VERSION_CHECK_INTERVAL_SECONDS:
        _version_checked_at = now
        version = _read_version()
        if version is not None and version != _db_version:
            _db_version = version
            stale = True

    if not stale:
        return _db_cache or {}

    try:
        values = _provider() or {}
    except Exception:
        # A missing table, an undecryptable row, a database that is still
        # starting: the file and environment tiers must keep working.
        logger.warning("Database secret provider failed; using file/env tiers",
                       exc_info=True)
        values = _db_cache or {}

    _db_cache = dict(values)
    _db_cache_at = now
    _version_checked_at = now
    _db_version = _read_version()
    return _db_cache


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def _file_value(name: str) -> Optional[str]:
    path = os.environ.get(f"{name}_FILE")
    if not path or not path.strip():
        return None
    try:
        with open(path.strip(), "r", encoding="utf-8") as handle:
            value = handle.read().strip()
    except OSError:
        # Logs the variable NAME (e.g. DB_PASSWORD_FILE), never the path contents.
        # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
        logger.warning("Cannot read the secret file named by %s_FILE", name)
        return None
    return value or None


def _resolve(name: str, default: Optional[str] = None
             ) -> Tuple[Optional[str], Optional[str]]:
    """Return `(value, source)` where source is "db", "file", "env" or None."""
    if name in TIER3_NAMES:
        stored = _db_values().get(name)
        if stored is not None and stored.strip():
            return stored, "db"

    from_file = _file_value(name)
    if from_file is not None:
        return from_file, "file"

    from_env = os.environ.get(name)
    if from_env is not None and from_env.strip():
        return from_env, "env"

    return default, None


def get_secret(name: str, default: Optional[str] = None) -> Optional[str]:
    """Resolve a credential: database tier, then `{NAME}_FILE`, then the
    environment. Never cached at import; safe to call on every request."""
    value, _ = _resolve(name, default)
    return value


def source_of(name: str) -> Optional[str]:
    """Where the live value comes from — "db", "file", "env", or None when the
    credential is not configured. The Integrations UI shows this so an operator
    can see that a file on disk is overriding what they typed."""
    _, source = _resolve(name)
    return source


def integration_enabled(name: str) -> bool:
    """True when an optional integration has a usable credential.

    Generalises the old `RESEND_ENABLED` module constant, which froze at import
    and so could not be switched on without a restart.
    """
    value = get_secret(name)
    if not value:
        return False
    return not is_placeholder(value)


# ---------------------------------------------------------------------------
# Startup validation
# ---------------------------------------------------------------------------

class StartupSecretError(SystemExit):
    """SystemExit(3) that still carries an operator-readable explanation.

    Plain `SystemExit(3)` prints nothing, and `SystemExit("message")` exits 1.
    This is a real SystemExit with a real exit code of 3, and `str()` names the
    offending variables — never their values.
    """

    def __init__(self, message: str):
        super().__init__(3)
        self.code = 3
        self.message = message

    def __str__(self) -> str:
        return self.message


def _password_from_dsn(dsn: str) -> Optional[str]:
    try:
        return urlsplit(dsn).password
    except ValueError:
        return None


def _fernet_keys_are_valid(raw: str) -> bool:
    try:
        from cryptography.fernet import Fernet
    except ImportError:  # pragma: no cover - cryptography is a pinned dependency
        logger.warning("cryptography is unavailable; cannot validate SCF_SECRET_KEY")
        return True
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            Fernet(part.encode())
        except Exception:
            return False
    return True


def check_startup_secrets(*, require_api_key: bool = True) -> None:
    """Refuse to start a non-development process on placeholder credentials.

    `require_api_key=False` is for processes that never authenticate a request
    (the Celery workers): a pre-#947 `.env` install only ever handed API_KEY to
    the `backend` service, and demanding it from a worker would refuse to start
    an install that has run unchanged for months.

    Booting a production stack on `changeme-postgres` is a silent compromise;
    booting on a malformed `SCF_SECRET_KEY` means every integration secret
    written that day is unreadable tomorrow. Both are worth a hard stop.
    Messages name the variable and never print its value.
    """
    environment = (os.getenv("ENVIRONMENT") or "").strip().lower()

    # An ABSENT key is not a hard stop — a legacy `.env` install that never
    # adopted in-app credential storage is a supported configuration, and
    # refusing to boot would break it. But it must not be silent either: until
    # #956 the only signal was a 409 the first time someone tried to save a
    # credential in the UI, which can be weeks after the install. Warn in every
    # environment, including development, because the symptom is identical there.
    if not (get_secret("SCF_SECRET_KEY") or "").strip():
        logger.warning(
            "SCF_SECRET_KEY is not configured: the platform cannot encrypt "
            "credentials, so storing one in the application will be refused. "
            "Credentials supplied through a secrets file or an environment "
            "variable are unaffected. Run scripts/install.sh to provision one."
        )

    if environment in ("development", "test"):
        return

    problems = []

    if require_api_key and is_placeholder(get_secret("API_KEY")):
        problems.append("API_KEY is unset or still a shipped placeholder")

    dsn = (os.getenv("DATABASE_URL") or "").strip()
    if dsn:
        if is_placeholder(_password_from_dsn(dsn)):
            problems.append("DATABASE_URL carries an unset or placeholder password")
    elif is_placeholder(get_secret("DB_PASSWORD")):
        problems.append("DB_PASSWORD is unset or still a shipped placeholder")

    if (os.getenv("AWS_ENDPOINT_URL") or "").strip():
        for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
            if is_placeholder(get_secret(name)):
                problems.append(f"{name} is unset or still a shipped placeholder")

    secret_key = get_secret("SCF_SECRET_KEY")
    if secret_key and secret_key.strip() and not _fernet_keys_are_valid(secret_key):
        problems.append(
            "SCF_SECRET_KEY is not a valid Fernet key "
            "(generate one with Fernet.generate_key())"
        )

    if not problems:
        return

    message = (
        f"Refusing to start with ENVIRONMENT={environment or 'unset'}: "
        + "; ".join(problems)
    )
    # logging's lastResort handler puts WARNING and above on stderr even when
    # nothing configured logging, so this reaches an operator either way.
    logger.critical(message)
    raise StartupSecretError(message)


def bootstrap_process(*, require_api_key: bool = True) -> None:
    """Validate credentials, then wire up the database tier if it exists.

    Called once per process: from the API lifespan after the migration guard
    (the provider needs its table), and from each Celery worker child — the
    latter with `require_api_key=False`, see `check_startup_secrets`.
    """
    check_startup_secrets(require_api_key=require_api_key)
    try:
        from services.integration_secrets import register
        register()
    except ImportError:
        # Lane D's provider is optional: without it the file and environment
        # tiers still resolve every credential.
        logger.debug("No database secret provider available")
