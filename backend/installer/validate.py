"""External-database validation.

Two jobs, both security-relevant:

1. **Never pass an operator string through to the driver.**  A DSN is parsed into
   discrete parts and the connection is REBUILT from them, so libpq keywords the
   operator smuggled in (``options``, a unix-socket ``host=``, ``passfile``…)
   cannot reach asyncpg.  Unix-socket hosts and link-local / in-container-loopback
   addresses are refused outright.

2. **Never let a credential reach a response body or a log line.**  Every
   ``detail`` string is built from a fixed vocabulary and then passed through the
   package redaction pass; the password is never formatted into anything.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import secrets as _secrets
import socket
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

from . import get_logger, redact

logger = get_logger()

CONNECT_TIMEOUT_SECONDS = 5.0

# libpq keywords that change WHERE or HOW we connect.  Rejected, never honoured.
FORBIDDEN_DSN_PARAMS: frozenset[str] = frozenset(
    {
        "options",
        "service",
        "passfile",
        "sslkey",
        "sslcert",
        "sslrootcert",
        "krbsrvname",
        "gssencmode",
    }
)

ALLOWED_SSLMODES: tuple[str, ...] = ("require", "verify-ca", "verify-full", "disable")
DEFAULT_SSLMODE = "require"

CHECK_NAMES: tuple[str, ...] = (
    "dns",
    "connect",
    "auth",
    "tls",
    "version",
    "create_table",
    "database",
)

MIN_SERVER_VERSION_NUM = 150000  # PostgreSQL 15

CLOUD_SQL_HINT = (
    "TLS negotiation failed. Managed PostgreSQL (Cloud SQL, RDS, Azure Database) "
    "usually requires TLS and either a public IP or a proxy sidecar reachable "
    "from the Docker network — check that the instance accepts connections from "
    "this host and that you are not pointing at a socket path."
)


class ValidationRejected(Exception):
    """The request is malformed or points somewhere we refuse to go: HTTP 400."""

    def __init__(self, error: str, detail: str) -> None:
        super().__init__(detail)
        self.error = error
        self.detail = detail


@dataclass
class DbParts:
    """The only connection description that ever reaches the driver."""

    host: str
    port: int = 5432
    dbname: str = "cg_scf"
    user: str = "cg"
    sslmode: str = DEFAULT_SSLMODE
    password: str = field(default="", repr=False)

    def safe_dict(self) -> dict[str, Any]:
        """Everything except the password — safe for logs and responses."""
        return {
            "host": self.host,
            "port": self.port,
            "dbname": self.dbname,
            "user": self.user,
            "sslmode": self.sslmode,
        }

    def __repr__(self) -> str:  # never let a repr leak the password
        return f"DbParts({self.safe_dict()!r})"


def _require_str(value: Any, name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, (str, int)):
        raise ValidationRejected("invalid_field", f"{name} must be a string")
    return str(value).strip()


def _check_host(host: str) -> str:
    if not host:
        raise ValidationRejected("invalid_field", "a database host is required")
    if host.startswith("/"):
        raise ValidationRejected(
            "unsupported connection parameter",
            "unix-socket hosts are not supported; give a hostname or IP address",
        )
    if any(c in host for c in " \t\r\n"):
        raise ValidationRejected("invalid_field", "the database host contains whitespace")
    return host


def _check_sslmode(sslmode: str, allow_plaintext: bool) -> str:
    mode = (sslmode or DEFAULT_SSLMODE).strip().lower()
    if mode not in ALLOWED_SSLMODES:
        raise ValidationRejected(
            "unsupported connection parameter",
            f"sslmode must be one of {', '.join(ALLOWED_SSLMODES)}",
        )
    if mode == "disable" and not allow_plaintext:
        raise ValidationRejected(
            "plaintext_refused",
            "sslmode=disable sends the database password in cleartext on every "
            "boot; re-submit with allow_plaintext to accept that",
        )
    return mode


def parse_dsn(dsn: str) -> DbParts:
    """Parse a DSN into discrete parts.  The string itself is then discarded."""
    dsn = (dsn or "").strip()
    if not dsn:
        raise ValidationRejected("invalid_field", "the connection string is empty")

    split = urlsplit(dsn)
    scheme = split.scheme.lower().split("+")[0]
    if scheme not in ("postgres", "postgresql"):
        raise ValidationRejected(
            "invalid_field", "the connection string must start with postgresql://"
        )

    params = {k.lower(): v for k, v in parse_qsl(split.query, keep_blank_values=True)}
    forbidden = sorted(FORBIDDEN_DSN_PARAMS & set(params))
    if forbidden:
        raise ValidationRejected(
            "unsupported connection parameter",
            f"unsupported connection parameter: {forbidden[0]}",
        )

    # A `host=` query parameter is how a unix socket is smuggled past a
    # hostname allow-list; it is honoured only after the same checks.
    host = params.get("host") or (split.hostname or "")
    host = _check_host(unquote(host))

    try:
        port = int(params.get("port") or split.port or 5432)
    except (TypeError, ValueError):
        raise ValidationRejected("invalid_field", "the database port must be a number") from None

    dbname = unquote(split.path.lstrip("/")) or params.get("dbname") or ""
    user = unquote(split.username or "") or params.get("user") or ""
    password = unquote(split.password or "") or params.get("password") or ""
    sslmode = (params.get("sslmode") or "").strip().lower()

    return DbParts(
        host=host,
        port=port,
        dbname=dbname or "cg_scf",
        user=user or "cg",
        sslmode=sslmode or "",
        password=password,
    )


def parts_from_payload(payload: dict[str, Any]) -> tuple[DbParts, bool]:
    """Build DbParts from either ``{"dsn": ...}`` or discrete fields.

    Returns ``(parts, allow_plaintext)``.  Raises ValidationRejected (HTTP 400)
    for anything we will not connect to.
    """
    if not isinstance(payload, dict):
        raise ValidationRejected("invalid_field", "expected a JSON object")

    allow_plaintext = bool(payload.get("allow_plaintext"))

    for key in FORBIDDEN_DSN_PARAMS:
        if key in payload:
            raise ValidationRejected(
                "unsupported connection parameter",
                f"unsupported connection parameter: {key}",
            )

    dsn = _require_str(payload.get("dsn"), "dsn")
    if dsn:
        parts = parse_dsn(dsn)
        # Discrete fields override a DSN's, so a form can correct one field.
        override_password = _require_str(payload.get("password"), "password")
        if override_password:
            parts.password = override_password
        override_sslmode = _require_str(payload.get("sslmode"), "sslmode")
        if override_sslmode:
            parts.sslmode = override_sslmode.lower()
    else:
        host = _check_host(_require_str(payload.get("host"), "host"))
        raw_port = _require_str(payload.get("port"), "port") or "5432"
        try:
            port = int(raw_port)
        except ValueError:
            raise ValidationRejected("invalid_field", "the database port must be a number") from None
        parts = DbParts(
            host=host,
            port=port,
            dbname=_require_str(payload.get("dbname"), "dbname") or "cg_scf",
            user=_require_str(payload.get("user"), "user") or "cg",
            sslmode=_require_str(payload.get("sslmode"), "sslmode").lower(),
            password=_require_str(payload.get("password"), "password"),
        )

    if not 1 <= parts.port <= 65535:
        raise ValidationRejected("invalid_field", "the database port is out of range")

    parts.sslmode = _check_sslmode(parts.sslmode, allow_plaintext)
    return parts, allow_plaintext


def resolve_addresses(host: str) -> list[str]:
    """Resolve a host to literal addresses.  Raises OSError on DNS failure."""
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    seen: list[str] = []
    for info in infos:
        addr = info[4][0]
        if addr not in seen:
            seen.append(addr)
    return seen


def guard_addresses(addresses: list[str]) -> None:
    """Refuse the cloud metadata range and this container's own loopback.

    ``127.0.0.1`` inside the wizard container is the wizard, not the operator's
    host — connecting there is never what the operator meant, and the distinction
    between refused and filtered turns the form into a port scanner.
    """
    for raw in addresses:
        try:
            addr = ipaddress.ip_address(raw)
        except ValueError:
            continue
        if addr.is_link_local:
            raise ValidationRejected(
                "address_refused",
                "link-local addresses (169.254.0.0/16) are refused: that range "
                "carries cloud instance metadata, not databases",
            )
        if addr.is_loopback:
            raise ValidationRejected(
                "address_refused",
                "loopback resolves to the installer container itself, not to your "
                "machine; use the LAN address or the Docker host gateway name",
            )


def _looks_like_tls_failure(exc: BaseException) -> bool:
    """True when a connection error is really a refused/failed TLS negotiation."""
    import ssl

    if isinstance(exc, ssl.SSLError) or isinstance(exc, ssl.CertificateError):
        return True
    text = str(exc).lower()
    return "ssl" in text or "tls" in text or "certificate" in text


def _check(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": ok, "detail": sanitise_detail(detail)}


def _ssl_context(sslmode: str) -> Any:
    if sslmode == "disable":
        return False
    if sslmode == "require":
        import ssl

        ctx = ssl.create_default_context()
        # `require` in libpq means encrypt, do not verify.
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    if sslmode == "verify-ca":
        import ssl

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        return ctx
    return True  # verify-full: asyncpg's strictest default context


async def run_checks(parts: DbParts) -> dict[str, Any]:
    """Probe the external database.  Never raises for a connection problem."""
    import asyncpg

    checks: list[dict[str, Any]] = []
    hint: str | None = None

    try:
        addresses = await asyncio.get_running_loop().run_in_executor(
            None, resolve_addresses, parts.host
        )
    except OSError:
        checks.append(_check("dns", False, f"could not resolve host {parts.host}"))
        return {"ok": False, "checks": checks, "hint": None}

    guard_addresses(addresses)
    checks.append(_check("dns", True, f"{parts.host} resolves to {', '.join(addresses)}"))

    conn = None
    database_exists = True
    try:
        conn = await asyncio.wait_for(
            asyncpg.connect(
                host=parts.host,
                port=parts.port,
                database=parts.dbname,
                user=parts.user,
                password=parts.password,
                ssl=_ssl_context(parts.sslmode),
                timeout=CONNECT_TIMEOUT_SECONDS,
                server_settings={"application_name": "scf-installer"},
            ),
            timeout=CONNECT_TIMEOUT_SECONDS,
        )
    except asyncpg.InvalidCatalogNameError:
        # Server reachable and credentials good; the database is simply absent.
        database_exists = False
        try:
            conn = await asyncio.wait_for(
                asyncpg.connect(
                    host=parts.host,
                    port=parts.port,
                    database="postgres",
                    user=parts.user,
                    password=parts.password,
                    ssl=_ssl_context(parts.sslmode),
                    timeout=CONNECT_TIMEOUT_SECONDS,
                ),
                timeout=CONNECT_TIMEOUT_SECONDS,
            )
        except Exception:
            checks.append(_check("connect", True, f"reached {parts.host}:{parts.port}"))
            checks.append(_check("auth", True, f"authenticated as {parts.user}"))
            checks.append(
                _check(
                    "database",
                    False,
                    f"database {parts.dbname} does not exist and the maintenance "
                    "database could not be opened to create it",
                )
            )
            return {"ok": False, "checks": checks, "hint": None}
    except (
        asyncpg.InvalidPasswordError,
        asyncpg.InvalidAuthorizationSpecificationError,
    ):
        checks.append(_check("connect", True, f"reached {parts.host}:{parts.port}"))
        checks.append(
            _check("auth", False, f"the server rejected the credentials for user {parts.user}")
        )
        return {"ok": False, "checks": checks, "hint": None}
    except asyncpg.PostgresError as exc:
        # Any other server-side refusal, e.g. pg_hba rejecting a non-TLS client.
        message = redact(str(exc)).upper()
        tls_related = "SSL" in message or "ENCRYPT" in message
        checks.append(_check("connect", True, f"reached {parts.host}:{parts.port}"))
        checks.append(_check("auth", False, "the server refused the connection"))
        if tls_related:
            checks.append(_check("tls", False, "the server requires a different TLS mode"))
            hint = CLOUD_SQL_HINT
        return {"ok": False, "checks": checks, "hint": hint}
    except (asyncio.TimeoutError, OSError) as exc:
        # ssl.SSLError IS an OSError, and asyncpg reports "server does not
        # support SSL, but SSL was required" as a plain ConnectionError — so a
        # TLS problem arrives here, not in the generic branch below.  Reaching
        # the port and then failing to negotiate is a TLS failure, not a
        # connect failure, and it is the case the Cloud SQL hint exists for.
        if _looks_like_tls_failure(exc):
            checks.append(_check("connect", True, f"reached {parts.host}:{parts.port}"))
            checks.append(
                _check(
                    "tls",
                    False,
                    f"the server refused TLS at sslmode={parts.sslmode} "
                    f"({type(exc).__name__})",
                )
            )
            return {"ok": False, "checks": checks, "hint": CLOUD_SQL_HINT}
        checks.append(
            _check(
                "connect",
                False,
                f"could not open a connection to {parts.host}:{parts.port} "
                f"within {int(CONNECT_TIMEOUT_SECONDS)}s ({type(exc).__name__})",
            )
        )
        return {"ok": False, "checks": checks, "hint": None}
    except Exception as exc:  # anything else during the handshake
        checks.append(_check("connect", True, f"reached {parts.host}:{parts.port}"))
        checks.append(_check("tls", False, f"TLS negotiation failed ({type(exc).__name__})"))
        return {"ok": False, "checks": checks, "hint": CLOUD_SQL_HINT}

    try:
        checks.append(_check("connect", True, f"reached {parts.host}:{parts.port}"))
        checks.append(_check("auth", True, f"authenticated as {parts.user}"))

        try:  # asyncpg does not expose this publicly
            ssl_object = conn._transport.get_extra_info("ssl_object")  # noqa: SLF001
        except Exception:  # pragma: no cover - transport shape varies
            ssl_object = None
        if parts.sslmode == "disable":
            checks.append(_check("tls", True, "TLS disabled by request (allow_plaintext was set)"))
        elif ssl_object is not None:
            cipher = ssl_object.cipher()
            checks.append(_check("tls", True, f"encrypted with {cipher[0] if cipher else 'TLS'}"))
        else:
            checks.append(_check("tls", False, "the connection was not encrypted despite sslmode"))
            hint = CLOUD_SQL_HINT

        version_num = int(await conn.fetchval("SELECT current_setting('server_version_num')"))
        version_text = await conn.fetchval("SELECT current_setting('server_version')")
        checks.append(
            _check(
                "version",
                version_num >= MIN_SERVER_VERSION_NUM,
                f"PostgreSQL {version_text} (the platform requires 15 or newer)",
            )
        )

        if database_exists:
            probe = f"scf_installer_probe_{_secrets.token_hex(6)}"
            try:
                # The only interpolated value is the probe name generated two lines
                # up (scf_installer_probe_ + token_hex); nothing user-supplied.
                # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query, python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
                await conn.execute(f'CREATE TABLE "{probe}" (id integer)')
                # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query, python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
                await conn.execute(f'DROP TABLE "{probe}"')
                checks.append(
                    _check("create_table", True, f"created and dropped a probe table in {parts.dbname}")
                )
            except Exception as exc:
                checks.append(
                    _check(
                        "create_table",
                        False,
                        f"the user {parts.user} cannot create tables in {parts.dbname} "
                        f"({type(exc).__name__})",
                    )
                )
            checks.append(_check("database", True, f"database {parts.dbname} exists"))
        else:
            can_create = bool(
                await conn.fetchval(
                    "SELECT rolcreatedb OR rolsuper FROM pg_roles WHERE rolname = current_user"
                )
            )
            checks.append(
                _check(
                    "create_table",
                    can_create,
                    "skipped: the target database does not exist yet"
                    if can_create
                    else f"the user {parts.user} cannot create databases",
                )
            )
            checks.append(
                _check(
                    "database",
                    can_create,
                    f"database {parts.dbname} does not exist; {parts.user} "
                    + ("can create it" if can_create else "cannot create it"),
                )
            )
    finally:
        if conn is not None:
            try:
                await conn.close(timeout=2)
            except Exception:  # pragma: no cover
                pass

    ok = all(c["ok"] for c in checks)
    logger.info("validate-db %s -> %s", parts.safe_dict(), "ok" if ok else "failed")
    return {"ok": ok, "checks": checks, "hint": hint}


_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def sanitise_detail(detail: str) -> str:
    """Last line of defence: redact, strip control characters, bound the length."""
    return _CONTROL_CHARS.sub(" ", redact(str(detail)))[:400]
