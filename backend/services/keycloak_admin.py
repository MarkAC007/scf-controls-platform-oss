"""Keycloak Admin REST client — provisioning users in the *bundled* realm (#984).

Inviting someone used to create an `organization_invites` row and send an email.
On a bundled-Keycloak install that left the invitee with a link, a redirect to
the OP, and no account to log in with. This module is the missing half: it
creates the identity at invite time, so the login the email promises exists.

**It runs only on the bundled profile.** `is_enabled()` is false unless all of
`KC_ADMIN_USER`, `KC_ADMIN_PASSWORD` and one of `OIDC_DISCOVERY_URL` /
`OIDC_ISSUER` resolve, and on a bring-your-own-OIDC install none of them do. The
platform does not own a customer's directory and must never write to it; the
gate is the whole of that guarantee, so nothing here should grow a bypass.

**Two URLs, and only one of them is right.** `OIDC_ISSUER` is what the *browser*
sees (in compose, the published `:8081`); `OIDC_DISCOVERY_URL` is what the
*backend* reaches over the container network (`http://keycloak:8080`). The admin
API is a server-to-server call, so it is rooted at the discovery URL whenever
one is set. Point it at the browser-facing origin and a hardened deployment that
only publishes Keycloak to the outside world will fail in a way that looks like
a credential problem.

**Nothing is read at import.** `oidc.py` freezes its configuration in module
constants, which is why a test cannot monkeypatch it and why rotating a secret
needs a restart. Every accessor here reads at call time, following
`oidc.get_client_secret()`; the admin password specifically goes through
`services.secrets.get_secret`, which resolves `KC_ADMIN_PASSWORD_FILE` first and
whose `NEVER_DB_NAMES` tuple already forbids the database tier from supplying
it. A row in `integration_secrets` therefore cannot become the Keycloak admin
password.

**No credential is ever logged.** The disabled-path log names the missing
variables and never their values; `KeycloakAdminError` renders the step and the
HTTP status and never a response body, a token, or a password. The only cached
state is the bearer token and its expiry — the password is re-resolved on every
token request, so a rotation takes effect without a restart.
"""
from __future__ import annotations

import logging
import os
import secrets as _secrets
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

import httpx

from services.secrets import get_secret

logger = logging.getLogger(__name__)

# Wall-clock timeout for every outbound call to the admin API.
HTTP_TIMEOUT_SECONDS = 10.0

# Renew the bearer token this many seconds before Keycloak expires it, so a
# request never sets out with a token that dies in flight.
_TOKEN_EXPIRY_SKEW_SECONDS = 30.0

# The realm that holds the admin account, and the public client that issues it a
# token. Both are Keycloak's own fixed names, not ours to configure.
_MASTER_REALM = "master"
_ADMIN_CLI_CLIENT_ID = "admin-cli"

# Either variable satisfies the URL requirement, so the two are reported as one
# name. See the module docstring for which is preferred and why.
_URL_CONFIG_NAME = "OIDC_DISCOVERY_URL or OIDC_ISSUER"

# Module state: the bearer token and when it stops being usable. Never the
# password, which is re-resolved per token request.
_token_cache: Optional[str] = None
_token_expires_at: float = 0.0


# ---------------------------------------------------------------------------
# Configuration — every value read at call time, never at import
# ---------------------------------------------------------------------------

def _admin_user() -> Optional[str]:
    """The Keycloak admin username. An identifier, not a secret, so plain env."""
    value = os.getenv("KC_ADMIN_USER")
    return value.strip() or None if value else None


def _admin_password() -> Optional[str]:
    """The Keycloak admin password, via the one credential accessor.

    Not stripped: whitespace can be part of a password. Empty means unset, which
    is what `get_secret` already returns for an empty file or variable.
    """
    return get_secret("KC_ADMIN_PASSWORD") or None


def _server_url() -> Optional[str]:
    """The URL the *backend* uses to reach Keycloak, discovery first."""
    value = os.getenv("OIDC_DISCOVERY_URL") or os.getenv("OIDC_ISSUER")
    return value.strip() or None if value else None


def missing_configuration() -> List[str]:
    """Names of the configuration this module needs and does not have.

    Names only — a caller may log the result, and does.
    """
    missing: List[str] = []
    if not _admin_user():
        missing.append("KC_ADMIN_USER")
    if not _admin_password():
        missing.append("KC_ADMIN_PASSWORD")
    if not _server_url():
        missing.append(_URL_CONFIG_NAME)
    return missing


def is_enabled() -> bool:
    """True when this install runs the bundled Keycloak and can provision.

    Logs the names of whatever is missing at INFO, because on a BYO-OIDC install
    that message is the expected steady state rather than a fault, and an
    operator who *expected* provisioning needs to see which variable to set.
    """
    missing = missing_configuration()
    if missing:
        logger.info(
            "Keycloak admin provisioning disabled: missing %s", ", ".join(missing)
        )
        return False
    return True


def admin_base_url() -> str:
    """Scheme and host of the Keycloak server, with no path.

    `http://keycloak:8080/realms/scf` -> `http://keycloak:8080`.
    """
    url = _server_url()
    if not url:
        raise KeycloakAdminError(
            f"Keycloak server URL is not configured (set {_URL_CONFIG_NAME})",
            step="token",
        )
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        raise KeycloakAdminError(
            "the configured Keycloak URL has no scheme and host", step="token"
        )
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def realm_name() -> str:
    """Realm from the last path segment of the configured URL.

    `http://keycloak:8080/realms/scf` and the same with a trailing slash both
    give `scf`. A URL that already points at the discovery *document* rather
    than the realm root is tolerated: the `.well-known/openid-configuration`
    tail is dropped before the last segment is taken, because getting `scf` from
    one spelling and `openid-configuration` from the other would send every
    admin call to a realm that does not exist.
    """
    url = _server_url()
    if not url:
        raise KeycloakAdminError(
            f"Keycloak realm is not configured (set {_URL_CONFIG_NAME})", step="token"
        )
    segments = [segment for segment in urlsplit(url).path.split("/") if segment]
    if (
        len(segments) >= 2
        and segments[-1] == "openid-configuration"
        and segments[-2] == ".well-known"
    ):
        segments = segments[:-2]
    if not segments:
        raise KeycloakAdminError(
            "the configured Keycloak URL carries no realm path segment", step="token"
        )
    return segments[-1]


def reset_caches() -> None:
    """Forget the cached bearer token. Test hook, and safe in production."""
    global _token_cache, _token_expires_at
    _token_cache = None
    _token_expires_at = 0.0


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class KeycloakAdminError(Exception):
    """An admin API call failed, named by the step that failed.

    `step` is one of `token`, `lookup`, `create`, `set_password`, `delete`. The
    rendered message carries the step and the HTTP status and nothing else — no
    response body, no token, no password — because this string reaches API error
    responses and logs.
    """

    def __init__(
        self, message: str, *, step: str, status_code: Optional[int] = None
    ) -> None:
        super().__init__(message)
        self.message = message
        self.step = step
        self.status_code = status_code

    def __str__(self) -> str:
        if self.status_code is not None:
            return (
                f"Keycloak admin {self.step} step failed "
                f"(HTTP {self.status_code}): {self.message}"
            )
        return f"Keycloak admin {self.step} step failed: {self.message}"


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

def _user_id_from_location(location: Optional[str]) -> Optional[str]:
    """Keycloak returns the new user's id only in the `Location` header."""
    if not location:
        return None
    segments = [segment for segment in urlsplit(location).path.split("/") if segment]
    return segments[-1] if segments else None


class KeycloakAdminClient:
    """Thin async wrapper over the handful of admin endpoints invites need.

    Construct with no arguments in production; the configuration is read then,
    at call time. Tests pass `transport=httpx.MockTransport(...)` and explicit
    credentials so nothing depends on the environment.
    """

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        realm: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        timeout: float = HTTP_TIMEOUT_SECONDS,
    ) -> None:
        self.base_url = (base_url or admin_base_url()).rstrip("/")
        self.realm = realm or realm_name()
        self._username = username if username is not None else _admin_user()
        self._password = password if password is not None else _admin_password()
        self._transport = transport
        self._timeout = timeout

    # -- plumbing ----------------------------------------------------------

    def _client(self) -> httpx.AsyncClient:
        kwargs: Dict[str, Any] = {"timeout": self._timeout}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.AsyncClient(**kwargs)

    def _users_url(self) -> str:
        return f"{self.base_url}/admin/realms/{self.realm}/users"

    @staticmethod
    def _auth_headers(token: str) -> Dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    @staticmethod
    def generate_temporary_password() -> str:
        """A single-use password for a freshly created account.

        `token_urlsafe(12)` is 96 bits of entropy. It is handed to the inviter
        once and never stored: the account carries `UPDATE_PASSWORD` as a
        required action, so it is spent on first login.
        """
        return _secrets.token_urlsafe(12)

    # -- calls -------------------------------------------------------------

    async def get_token(self) -> str:
        """A bearer token for the admin API, cached until shortly before expiry."""
        global _token_cache, _token_expires_at

        if _token_cache and time.monotonic() < _token_expires_at:
            return _token_cache

        url = (
            f"{self.base_url}/realms/{_MASTER_REALM}"
            "/protocol/openid-connect/token"
        )
        form = {
            "grant_type": "password",
            "client_id": _ADMIN_CLI_CLIENT_ID,
            "username": self._username or "",
            "password": self._password or "",
        }
        try:
            async with self._client() as client:
                response = await client.post(url, data=form)
        except httpx.HTTPError as exc:
            raise KeycloakAdminError(
                f"could not reach the Keycloak admin API at {self.base_url}",
                step="token",
            ) from exc

        if response.status_code != 200:
            raise KeycloakAdminError(
                "the admin-cli token request was rejected",
                step="token",
                status_code=response.status_code,
            )

        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise KeycloakAdminError(
                "the token response carried no access_token",
                step="token",
                status_code=response.status_code,
            )

        try:
            expires_in = float(payload.get("expires_in") or 60.0)
        except (TypeError, ValueError):
            expires_in = 60.0
        _token_cache = str(token)
        _token_expires_at = time.monotonic() + max(
            expires_in - _TOKEN_EXPIRY_SKEW_SECONDS, 0.0
        )
        return _token_cache

    async def find_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """The realm user with exactly this email, or None.

        `exact=true` matters: without it Keycloak substring-matches, and an
        invite for `sam@example.com` would find `sam@example.com.au`. The
        returned rows are re-checked case-insensitively anyway, because the
        filter's own case handling is not something to depend on.
        """
        token = await self.get_token()
        try:
            async with self._client() as client:
                response = await client.get(
                    self._users_url(),
                    params={"email": email, "exact": "true"},
                    headers=self._auth_headers(token),
                )
        except httpx.HTTPError as exc:
            raise KeycloakAdminError(
                "could not reach the Keycloak admin API for a user lookup",
                step="lookup",
            ) from exc

        if response.status_code != 200:
            raise KeycloakAdminError(
                "the user lookup was rejected",
                step="lookup",
                status_code=response.status_code,
            )

        wanted = email.strip().lower()
        for entry in response.json() or []:
            if str(entry.get("email") or "").strip().lower() == wanted:
                return entry
        return None

    async def ensure_user(self, email: str) -> Tuple[str, bool]:
        """The realm user id for this email, creating the account if absent.

        Returns `(user_id, created)`. `created` is False for an account that was
        already there — the caller must leave such an account completely alone,
        because it belongs to a person who may already be using it, possibly in
        another organisation.

        Lookup-then-create races: two invites for the same address at the same
        moment both see nothing and both POST. Keycloak answers the loser with
        409, which is resolved by looking the user up again rather than failing,
        so the operation is idempotent on retry.
        """
        existing = await self.find_user_by_email(email)
        if existing is not None:
            user_id = existing.get("id")
            if not user_id:
                raise KeycloakAdminError(
                    "the existing Keycloak user carried no id", step="lookup"
                )
            return str(user_id), False

        token = await self.get_token()
        payload = {
            "username": email,
            "email": email,
            "enabled": True,
            "emailVerified": True,
            "requiredActions": ["UPDATE_PASSWORD"],
        }
        try:
            async with self._client() as client:
                response = await client.post(
                    self._users_url(),
                    json=payload,
                    headers=self._auth_headers(token),
                )
        except httpx.HTTPError as exc:
            raise KeycloakAdminError(
                "could not reach the Keycloak admin API to create the user",
                step="create",
            ) from exc

        if response.status_code == 409:
            raced = await self.find_user_by_email(email)
            if raced is not None and raced.get("id"):
                return str(raced["id"]), False
            raise KeycloakAdminError(
                "Keycloak reported the user already exists but it cannot be found",
                step="create",
                status_code=409,
            )

        if response.status_code not in (200, 201):
            raise KeycloakAdminError(
                "the user create was rejected",
                step="create",
                status_code=response.status_code,
            )

        user_id = _user_id_from_location(response.headers.get("location"))
        if user_id:
            return user_id, True

        # Keycloak always sets Location on a 201. A proxy that strips it should
        # not cost us the account we just made.
        created = await self.find_user_by_email(email)
        if created is not None and created.get("id"):
            return str(created["id"]), True
        raise KeycloakAdminError(
            "the created user has no id in the Location header and cannot be found",
            step="create",
            status_code=response.status_code,
        )

    async def set_temporary_password(self, user_id: str, password: str) -> None:
        """Set a one-shot password on an account this code just created.

        `temporary: true` pairs with the `UPDATE_PASSWORD` required action: the
        person must replace it at first login, so the value in the invite email
        stops working the moment it is used.
        """
        token = await self.get_token()
        url = f"{self._users_url()}/{user_id}/reset-password"
        body = {"type": "password", "value": password, "temporary": True}
        try:
            async with self._client() as client:
                response = await client.put(
                    url, json=body, headers=self._auth_headers(token)
                )
        except httpx.HTTPError as exc:
            raise KeycloakAdminError(
                "could not reach the Keycloak admin API to set the password",
                step="set_password",
            ) from exc

        if response.status_code not in (200, 204):
            raise KeycloakAdminError(
                "the password reset was rejected",
                step="set_password",
                status_code=response.status_code,
            )

    async def delete_user(self, user_id: str) -> None:
        """Remove an account. A 404 is success — the end state is what matters.

        This is called to undo a provisioning whose database commit failed, and
        on invite cancellation. Both are cleanup paths that may run twice, so
        "already gone" must not raise.
        """
        token = await self.get_token()
        url = f"{self._users_url()}/{user_id}"
        try:
            async with self._client() as client:
                response = await client.delete(url, headers=self._auth_headers(token))
        except httpx.HTTPError as exc:
            raise KeycloakAdminError(
                "could not reach the Keycloak admin API to delete the user",
                step="delete",
            ) from exc

        if response.status_code == 404:
            return
        if response.status_code not in (200, 204):
            raise KeycloakAdminError(
                "the user delete was rejected",
                step="delete",
                status_code=response.status_code,
            )


# ---------------------------------------------------------------------------
# Module-level conveniences — what callers outside this file should use
# ---------------------------------------------------------------------------

@dataclass
class ProvisionResult:
    """What provisioning did.

    `temporary_password` is set only when `created` is True, and only ever
    travels outward — into the invite response once and into the invite email.
    It is never persisted and never returned by a list endpoint.
    """

    user_id: str
    created: bool
    temporary_password: Optional[str]


async def provision_user(
    email: str, *, client: Optional[KeycloakAdminClient] = None
) -> ProvisionResult:
    """Ensure an account exists for this email, with a temp password if new.

    An account that already existed is returned untouched and with no password:
    resetting the credentials of a person who already uses this realm would lock
    them out of every organisation they belong to.
    """
    client = client or KeycloakAdminClient()
    user_id, created = await client.ensure_user(email)
    if not created:
        return ProvisionResult(user_id=user_id, created=False, temporary_password=None)

    password = KeycloakAdminClient.generate_temporary_password()
    await client.set_temporary_password(user_id, password)
    return ProvisionResult(user_id=user_id, created=True, temporary_password=password)


async def find_user_id(
    email: str, *, client: Optional[KeycloakAdminClient] = None
) -> Optional[str]:
    """The realm user id for this email, or None. Used for `idp_status`."""
    client = client or KeycloakAdminClient()
    entry = await client.find_user_by_email(email)
    if entry is None:
        return None
    user_id = entry.get("id")
    return str(user_id) if user_id else None


async def delete_user(
    user_id: str, *, client: Optional[KeycloakAdminClient] = None
) -> None:
    """Remove an account this code provisioned. Idempotent."""
    client = client or KeycloakAdminClient()
    await client.delete_user(user_id)


__all__ = [
    "HTTP_TIMEOUT_SECONDS",
    "KeycloakAdminClient",
    "KeycloakAdminError",
    "ProvisionResult",
    "admin_base_url",
    "delete_user",
    "find_user_id",
    "is_enabled",
    "missing_configuration",
    "provision_user",
    "realm_name",
    "reset_caches",
]
