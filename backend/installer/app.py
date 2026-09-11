"""The wizard's HTTP surface.

Threat model, in the order the middleware runs:

1. **DNS rebinding.**  A hostile page can rebind ``evil.test`` to ``127.0.0.1``
   and then read our responses under the same-origin policy — CORS is no defence.
   Any ``Host`` that is not ``127.0.0.1:<port>`` / ``localhost:<port>`` gets 421.
2. **Cross-site form posts.**  ``Sec-Fetch-Site: cross-site`` gets 403.
3. **Tokens in URLs.**  A ``token`` query parameter gets 400 so the shape never
   appears in an access log, in scrollback or in a ``Referer``.
4. **Response headers** that stop the page loading anything off-origin.

The token itself travels in ``X-SCF-Provision-Token``, is compared with
``hmac.compare_digest``, and five wrong ones kill the process *before* the
sentinel is written, so the operator must relaunch and re-read a fresh token.
"""

from __future__ import annotations

import hmac
import os
import threading
from pathlib import Path
from typing import Any, Callable

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware

from . import get_logger, host_secrets_dir, out_dir, secrets_dir
from . import validate as validate_mod
from .generate import SECRET_FILE_NAMES, generate_secrets
from .validate import ValidationRejected
from . import writer

logger = get_logger()

STATIC_DIR = Path(__file__).parent / "static"

TOKEN_HEADER = "X-SCF-Provision-Token"
MAX_BAD_TOKENS = 5
EXIT_DELAY_SECONDS = 1.0
LOCKOUT_EXIT_CODE = 5

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; "
        "connect-src 'self'; img-src 'self'"
    ),
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
}

# Fixed error vocabulary.  A response body never carries str(exc).
ERR_ALREADY_PROVISIONED = "already_provisioned"
ERR_VALIDATION_FAILED = "validation_failed"
ERR_INVALID_REQUEST = "invalid_request"
ERR_INTERNAL = "internal_error"


def _json(status: int, payload: dict[str, Any]) -> JSONResponse:
    response = JSONResponse(status_code=status, content=payload)
    for key, value in SECURITY_HEADERS.items():
        response.headers[key] = value
    return response


class HostCheckMiddleware(BaseHTTPMiddleware):
    """421 unless the Host header names our own loopback listener."""

    def __init__(self, app: Any, allowed: set[str]) -> None:
        super().__init__(app)
        self.allowed = allowed

    async def dispatch(self, request: Request, call_next):
        host = (request.headers.get("host") or "").strip().lower()
        if host not in self.allowed:
            logger.warning("refused a request with Host %r", host)
            return _json(
                421,
                {
                    "ok": False,
                    "error": "misdirected_request",
                    "detail": "this installer answers only on its own loopback address",
                },
            )
        return await call_next(request)


class SecFetchMiddleware(BaseHTTPMiddleware):
    """403 for anything a cross-site context initiated, except opening the page.

    A top-level GET navigation (a link from the docs, a browser extension, a
    typed address that Chrome attributes to another site) carries no token and
    reads nothing the initiator can see, so refusing it only turns the landing
    page into a JSON error.  Every other cross-site request — a fetch, a form
    POST, a subresource — is refused: the API needs the custom token header,
    which a cross-site context cannot send without a preflight we never answer.
    """

    @staticmethod
    def _is_page_navigation(request: Request) -> bool:
        headers = request.headers
        return (
            request.method in ("GET", "HEAD")
            and (headers.get("sec-fetch-mode") or "").lower() == "navigate"
            and (headers.get("sec-fetch-dest") or "").lower() == "document"
        )

    async def dispatch(self, request: Request, call_next):
        if (
            request.headers.get("sec-fetch-site") or ""
        ).lower() == "cross-site" and not self._is_page_navigation(request):
            logger.warning("refused a cross-site request to %s", request.url.path)
            return _json(
                403,
                {
                    "ok": False,
                    "error": "cross_site_refused",
                    "detail": "cross-site requests are refused",
                },
            )
        return await call_next(request)


class NoQueryTokenMiddleware(BaseHTTPMiddleware):
    """400 for a `token` query parameter, so the token never lands in a log."""

    async def dispatch(self, request: Request, call_next):
        if "token" in request.query_params:
            return _json(
                400,
                {
                    "ok": False,
                    "error": ERR_INVALID_REQUEST,
                    "detail": (
                        "the provisioning token must be sent in the "
                        f"{TOKEN_HEADER} header, never in the URL"
                    ),
                },
            )
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        for key, value in SECURITY_HEADERS.items():
            response.headers[key] = value
        return response


class TokenGate:
    """Constant-time token check with a fail-closed counter."""

    def __init__(
        self,
        secrets_path: Path,
        *,
        on_lockout: Callable[[], None] | None = None,
        max_bad: int = MAX_BAD_TOKENS,
    ) -> None:
        self.secrets_path = secrets_path
        self.max_bad = max_bad
        self.bad_attempts = 0
        self._lock = threading.Lock()
        self.on_lockout = on_lockout or self._default_lockout

    @staticmethod
    def _default_lockout() -> None:  # pragma: no cover - kills the test runner
        os._exit(LOCKOUT_EXIT_CODE)

    def expected(self) -> str | None:
        return writer.read_provision_token(self.secrets_path)

    def check(self, presented: str | None) -> bool:
        expected = self.expected()
        if not expected:
            self._register_failure("no provisioning token file is present")
            return False
        if presented and hmac.compare_digest(presented, expected):
            return True
        self._register_failure("invalid provisioning token")
        return False

    def _register_failure(self, why: str) -> None:
        with self._lock:
            self.bad_attempts += 1
            attempts = self.bad_attempts
        logger.warning("%s (%d/%d)", why, attempts, self.max_bad)
        if attempts >= self.max_bad:
            logger.error("too many invalid provisioning tokens — exiting")
            self.on_lockout()


def schedule_exit(delay: float = EXIT_DELAY_SECONDS, code: int = 0) -> None:
    """Let the response flush, then stop the wizard: it is single-use."""

    def _bye() -> None:  # pragma: no cover - process teardown
        os._exit(code)

    timer = threading.Timer(delay, _bye)
    timer.daemon = True
    timer.start()


def _provision(
    *,
    payload: dict[str, Any],
    secrets_path: Path,
    out_path: Path,
    host_dir: str,
) -> dict[str, Any]:
    """Shared by the HTTP route and unattended mode: identical writes."""
    db = payload.get("db") or {}
    idp = payload.get("idp") or {}
    db_type = str(db.get("type") or "bundled")
    idp_type = str(idp.get("type") or "none")

    # Validate BEFORE the sentinel: a rejected config must leave the secrets dir
    # untouched, so the operator can fix the file and re-run. The browser wizard
    # makes this field mandatory client-side; unattended mode used to accept an
    # empty one silently, which produced a healthy stack with no organisation
    # membership and an unusable UI (#956).
    if idp_type == "bundled_keycloak" and not str(idp.get("bootstrap_admin_email") or "").strip():
        raise ValidationRejected(
            ERR_INVALID_REQUEST,
            "idp.bootstrap_admin_email is required when idp.type is 'bundled_keycloak': "
            "it is the account promoted to platform administrator",
        )

    writer.ensure_secrets_dir(secrets_path)
    # Sentinel FIRST: atomic, symlink-proof, race-proof.
    writer.create_sentinel(secrets_path, db=db_type, idp=idp_type)

    values = generate_secrets(
        db_type=db_type,
        idp_type=idp_type,
        external_db_password=db.get("password"),
        external_oidc_client_secret=idp.get("oidc_client_secret"),
    )
    created = writer.write_secret_files(secrets_path, values)

    env_db = dict(db)
    env_db.pop("password", None)
    env_db["type"] = db_type
    if db_type == "bundled":
        env_db.update({"host": "postgres", "port": 5432, "dbname": "cg_scf", "user": "cg"})
    content = writer.build_env(host_secrets_dir=host_dir, db=env_db, idp=idp)
    env_path = writer.write_env(out_path, content)

    writer.delete_provision_token(secrets_path)

    next_steps = [
        "Review .env — it holds non-secret configuration only.",
        "Start the stack: docker compose up -d",
    ]
    if idp_type == "bundled_keycloak":
        next_steps += [
            "Read the one-time Keycloak password: docker compose logs idp-init",
            "Sign in with that password; Keycloak will make you set your own.",
        ]
        email = str(idp.get("bootstrap_admin_email") or "").strip().lower()
        if email:
            next_steps.append(
                "Create the platform admin: docker compose exec backend "
                f"python -m cli.admin setup --admin-email {email}"
            )
    return {
        "ok": True,
        "secrets_dir": host_dir,
        "env_path": str(env_path),
        "files_created": [n for n in SECRET_FILE_NAMES if created.get(n)],
        "files_kept": [n for n in SECRET_FILE_NAMES if not created.get(n)],
        "next_steps": next_steps,
    }


class _Unauthorized(Exception):
    """Raised by the token dependency; mapped to 401 by its own handler."""


def create_app(
    *,
    secrets_path: Path | None = None,
    out_path: Path | None = None,
    host_dir: str | None = None,
    port: int = 8765,
    public_port: int | None = None,
    on_lockout: Callable[[], None] | None = None,
    exit_after_provision: bool = True,
) -> FastAPI:
    secrets_path = Path(secrets_path) if secrets_path else secrets_dir()
    out_path = Path(out_path) if out_path else out_dir()
    host_dir = host_dir or host_secrets_dir()

    # The browser only ever sees the port Docker published on the host loopback
    # (install.sh: -p 127.0.0.1:${PORT}:8765), which is not necessarily the port
    # uvicorn listens on inside the container.  The Host check must use the
    # published one, or every request 421s the moment --port is not 8765.
    public_port = port if public_port is None else public_port
    allowed_hosts = {f"127.0.0.1:{public_port}", f"localhost:{public_port}"}
    if public_port == 80:
        allowed_hosts |= {"127.0.0.1", "localhost"}

    app = FastAPI(
        title="SCF Controls Platform installer",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        middleware=[
            # List order is outermost-first, i.e. exactly the contract order.
            Middleware(HostCheckMiddleware, allowed=allowed_hosts),
            Middleware(SecFetchMiddleware),
            Middleware(NoQueryTokenMiddleware),
            Middleware(SecurityHeadersMiddleware),
        ],
    )
    gate = TokenGate(secrets_path, on_lockout=on_lockout)
    app.state.gate = gate
    app.state.secrets_path = secrets_path
    app.state.out_path = out_path
    app.state.host_dir = host_dir

    async def require_token(request: Request) -> None:
        presented = request.headers.get(TOKEN_HEADER)
        if not gate.check(presented):
            raise _Unauthorized()

    # Every handler returns a FIXED enum.  str(exc) is never echoed: asyncpg and
    # SQLAlchemy exceptions carry the full DSN, password included.
    @app.exception_handler(_Unauthorized)
    async def _unauthorized(request: Request, exc: _Unauthorized) -> JSONResponse:
        return _json(
            401,
            {"ok": False, "error": "unauthorized", "detail": "invalid provisioning token"},
        )

    @app.exception_handler(ValidationRejected)
    async def _rejected(request: Request, exc: ValidationRejected) -> JSONResponse:
        return _json(
            400,
            {"ok": False, "error": exc.error, "detail": validate_mod.sanitise_detail(exc.detail)},
        )

    @app.exception_handler(writer.AlreadyProvisioned)
    async def _provisioned(request: Request, exc: writer.AlreadyProvisioned) -> JSONResponse:
        return _json(
            409,
            {
                "ok": False,
                "error": ERR_ALREADY_PROVISIONED,
                "detail": "this secrets directory has already been provisioned",
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled %s in %s", type(exc).__name__, request.url.path)
        return _json(
            500,
            {"ok": False, "error": ERR_INTERNAL, "detail": "the installer hit an internal error"},
        )

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", headers=SECURITY_HEADERS)

    @app.get("/app.js")
    async def app_js() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "app.js", media_type="text/javascript", headers=SECURITY_HEADERS
        )

    @app.get("/app.css")
    async def app_css() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "app.css", media_type="text/css", headers=SECURITY_HEADERS
        )

    @app.get("/api/status", dependencies=[Depends(require_token)])
    async def status() -> JSONResponse:
        return _json(
            200,
            {
                "provisioned": writer.is_provisioned(secrets_path),
                "mode": "serve",
                "secrets_dir": host_dir,
            },
        )

    @app.post("/api/validate-db", dependencies=[Depends(require_token)])
    async def validate_db(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except Exception:
            raise ValidationRejected(ERR_INVALID_REQUEST, "the request body is not JSON") from None
        parts, _ = validate_mod.parts_from_payload(payload)
        result = await validate_mod.run_checks(parts)
        return _json(200, result)

    @app.post("/api/provision", dependencies=[Depends(require_token)])
    async def provision(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except Exception:
            raise ValidationRejected(ERR_INVALID_REQUEST, "the request body is not JSON") from None

        db = payload.get("db") or {}
        if str(db.get("type") or "bundled") == "external":
            parts, _ = validate_mod.parts_from_payload(db)
            result = await validate_mod.run_checks(parts)
            if not result.get("ok"):
                # Nothing has been written yet: the sentinel comes later.
                return _json(
                    400,
                    {
                        "ok": False,
                        "error": ERR_VALIDATION_FAILED,
                        "detail": "the external database did not pass validation",
                        "checks": result.get("checks", []),
                        "hint": result.get("hint"),
                    },
                )

        result = _provision(
            payload=payload, secrets_path=secrets_path, out_path=out_path, host_dir=host_dir
        )
        if exit_after_provision:
            schedule_exit()
        return _json(200, result)

    return app
