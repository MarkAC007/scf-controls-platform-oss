"""Generic OIDC authorization-code login endpoints for CG SCF (issue #699).

Router mounts under ``/api/auth`` and drives the browser-facing OIDC flow:

    GET  /api/auth/login     -> 302 to the OP authorization endpoint
    GET  /api/auth/callback  -> exchange code, mint session, 302 back to the SPA
    POST /api/auth/refresh   -> rotate refresh handle, return a fresh id_token
    POST /api/auth/logout    -> best-effort revoke of the server-side refresh handle

Design notes
------------
* This backend mints no JWTs of its own; the OP's id_token IS the session token.
  Tokens are handed to the SPA in the URL *fragment* (never the query string) so
  they never land in server/proxy access logs.
* The raw ``refresh_token`` never reaches the browser. It is stored server-side
  in Redis under an opaque ``refresh_handle``; the SPA only ever holds the handle.
* ``state``/``nonce``/PKCE are enforced. ``state`` entries are single-use
  (GETDEL on callback).
* ``_persist_oidc_user`` lives in ``auth.py`` and may be added by a parallel
  change; it is imported lazily inside the handlers so importing this module
  never fails even before that function exists.
"""
from __future__ import annotations

import hmac
import json
import logging
import secrets
import time
from typing import Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from redis_client import get_redis_client
import oidc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["oidc"])

# Redis key prefixes / TTLs for the login state and refresh-handle stores.
_STATE_KEY_PREFIX = "oidc:state:"
_REFRESH_KEY_PREFIX = "oidc:refresh:"
_STATE_TTL_SECONDS = 600  # login round-trip window
_DEFAULT_REFRESH_TTL_SECONDS = 30 * 24 * 3600  # fallback when OP omits lifetime

# Cookie that binds a login flow to the browser that started it (session-fixation
# defense). Scoped to the auth path; SameSite=Lax so it survives the IdP redirect.
_FLOW_COOKIE_NAME = "scf_oidc_flow"

# Fragment landing route in the SPA after a successful login.
_SPA_CALLBACK_PATH = "/auth/callback"
# Where provisioning failures are surfaced to the SPA (query, not fragment).
_SPA_ERROR_PATH = "/?auth_error=account_not_provisioned"

# Outbound token-endpoint timeout.
_HTTP_TIMEOUT_SECONDS = 10.0


class RefreshRequest(BaseModel):
    """Body for POST /refresh and POST /logout."""
    refresh_handle: str


class RefreshResponse(BaseModel):
    """Body returned by POST /refresh after a successful rotation."""
    id_token: str
    expires_in: int
    refresh_handle: str


def _guard_enabled() -> None:
    """Reject requests when OIDC is not configured (parallels oidc._require_enabled)."""
    if not oidc.oidc_enabled():
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="OIDC login is not configured on this server",
        )


async def _token_endpoint() -> str:
    """Resolve the OP token endpoint, rebased onto the internal discovery origin."""
    discovery = await oidc.get_discovery()
    token_endpoint = discovery.get("token_endpoint")
    if not token_endpoint:
        logger.error("OIDC discovery document has no token_endpoint")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Identity provider does not advertise a token endpoint",
        )
    return oidc.rebase_to_discovery_origin(token_endpoint)


async def _post_token_request(form: dict) -> dict:
    """POST an application/x-www-form-urlencoded grant to the OP token endpoint.

    Returns the parsed JSON token response. Maps failures so the SPA can tell a
    terminal grant rejection from a transient outage: OP 4xx -> 401 (grant
    genuinely rejected, clear the session); OP 5xx or a network/timeout error ->
    503 (identity provider temporarily unavailable, retryable). Malformed but
    2xx responses remain 502. Never logs the form (it carries the code, client
    secret and refresh token).
    """
    token_endpoint = await _token_endpoint()
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                token_endpoint,
                data=form,
                headers={"Accept": "application/json"},
            )
    except httpx.HTTPError as exc:
        # Transport failure/timeout is transient, not an invalid grant. Surface a
        # retryable 503 so the frontend does not treat it as a terminal 401.
        logger.error("OIDC token endpoint unreachable: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Identity provider temporarily unavailable",
        )

    if resp.status_code >= 500:
        # Transient OP-side failure: retryable, not a rejected grant.
        logger.warning("OIDC token endpoint returned %s (server error)", resp.status_code)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Identity provider temporarily unavailable",
        )

    if resp.status_code >= 400:
        # OP rejected the grant (bad/expired code, bad refresh token, etc.).
        # The error body may name the reason but must not be echoed verbatim.
        logger.warning("OIDC token endpoint returned %s for grant", resp.status_code)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Identity provider rejected the authentication grant",
        )

    try:
        payload = resp.json()
    except json.JSONDecodeError:
        logger.error("OIDC token endpoint returned non-JSON body")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Identity provider returned a malformed token response",
        )

    if not isinstance(payload, dict) or "id_token" not in payload:
        logger.error("OIDC token response missing id_token")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Identity provider omitted the id_token",
        )
    return payload


async def _store_refresh_token(refresh_token: str, expires_in: Optional[int]) -> str:
    """Persist a refresh_token under a fresh opaque handle; return the handle.

    TTL is the OP-supplied ``refresh_expires_in`` when present, else a 30d default.
    """
    handle = secrets.token_urlsafe(32)
    ttl = expires_in if (isinstance(expires_in, int) and expires_in > 0) else _DEFAULT_REFRESH_TTL_SECONDS
    redis = await get_redis_client()
    entry = json.dumps({"refresh_token": refresh_token, "created": int(time.time())})
    await redis.set(f"{_REFRESH_KEY_PREFIX}{handle}", entry, ex=ttl)
    return handle


def _expire_flow_cookie(response: RedirectResponse) -> None:
    """Clear the single-use flow-binding cookie (path/attrs must match /login)."""
    response.delete_cookie(
        key=_FLOW_COOKIE_NAME,
        path="/api/auth",
        httponly=True,
        secure=True,
        samesite="lax",
    )


@router.get("/login")
async def login() -> RedirectResponse:
    """Begin the OIDC authorization-code + PKCE flow.

    Generates state/nonce/PKCE, persists them single-use in Redis, and 302s the
    browser to the OP authorization endpoint (rebased onto the *public* issuer
    origin so the browser can actually reach it).
    """
    _guard_enabled()

    discovery = await oidc.get_discovery()
    authorization_endpoint = discovery.get("authorization_endpoint")
    if not authorization_endpoint:
        logger.error("OIDC discovery document has no authorization_endpoint")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Identity provider does not advertise an authorization endpoint",
        )
    # The browser must hit the public issuer origin, not the internal one.
    authorization_endpoint = oidc.rebase_to_issuer_origin(authorization_endpoint)

    state = oidc.generate_state()
    nonce = oidc.generate_nonce()
    code_verifier = oidc.generate_code_verifier()
    code_challenge = oidc.generate_code_challenge(code_verifier)
    # High-entropy value tying this flow to the initiating browser via a cookie.
    flow_binding = secrets.token_urlsafe(32)

    redis = await get_redis_client()
    state_entry = json.dumps(
        {
            "nonce": nonce,
            "code_verifier": code_verifier,
            "flow_binding": flow_binding,
            "created": int(time.time()),
        }
    )
    await redis.set(f"{_STATE_KEY_PREFIX}{state}", state_entry, ex=_STATE_TTL_SECONDS)

    params = {
        "response_type": "code",
        "client_id": oidc.OIDC_CLIENT_ID,
        "redirect_uri": oidc.OIDC_REDIRECT_URI,
        "scope": oidc.OIDC_SCOPES,
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    separator = "&" if "?" in authorization_endpoint else "?"
    redirect_url = f"{authorization_endpoint}{separator}{urlencode(params)}"
    response = RedirectResponse(url=redirect_url, status_code=status.HTTP_302_FOUND)
    # Bind the callback to the browser that started here: /callback rejects any
    # request that cannot present this cookie value. secure=True is correct for
    # prod (HTTPS); local http dev may need secure toggled off to see the cookie.
    response.set_cookie(
        key=_FLOW_COOKIE_NAME,
        value=flow_binding,
        max_age=_STATE_TTL_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",  # must survive the top-level redirect back from the IdP
        path="/api/auth",
    )
    return response


@router.get("/callback")
async def callback(
    request: Request,
    code: Optional[str] = Query(default=None),
    state: Optional[str] = Query(default=None),
    error: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Complete the flow: validate state, exchange code, provision user, hand off.

    On success 302s the SPA to ``/auth/callback`` with the id_token, expiry and
    refresh handle in the URL *fragment*. Provisioning failure (403) is surfaced
    as a redirect to the SPA error route rather than a raw JSON error.
    """
    _guard_enabled()

    # OP-reported authorization errors (user denied consent, etc.).
    if error:
        logger.info("OIDC provider returned authorization error")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Authorization was not granted",
        )
    if not code or not state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing authorization code or state",
        )

    # Single-use state: delete-before-use. Unknown/reused state is rejected.
    redis = await get_redis_client()
    raw_state = await redis.getdel(f"{_STATE_KEY_PREFIX}{state}")
    if not raw_state:
        logger.warning("OIDC callback with unknown or already-used state")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired login state",
        )
    try:
        state_data = json.loads(raw_state)
        expected_nonce = state_data["nonce"]
        code_verifier = state_data["code_verifier"]
    except (json.JSONDecodeError, KeyError):
        logger.error("Corrupt OIDC state entry in Redis")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid login state",
        )

    # Session-fixation defense: this callback must come from the same browser that
    # started /login. Compare the stored flow binding to the cookie in constant
    # time; a missing or mismatched cookie means the flow was started elsewhere.
    stored_binding = state_data.get("flow_binding")
    flow_cookie = request.cookies.get(_FLOW_COOKIE_NAME)
    if not stored_binding or not flow_cookie or not hmac.compare_digest(
        stored_binding, flow_cookie
    ):
        logger.warning("OIDC callback flow-binding cookie missing or mismatched")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Login session mismatch — restart sign-in",
        )

    token_response = await _post_token_request(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": oidc.OIDC_REDIRECT_URI,
            "client_id": oidc.OIDC_CLIENT_ID,
            "client_secret": oidc.OIDC_CLIENT_SECRET,
            "code_verifier": code_verifier,
        }
    )

    id_token = token_response["id_token"]
    claims = await oidc.validate_oidc_token(id_token)

    # Bind the id_token to this specific login attempt (replay protection).
    if claims.get("nonce") != expected_nonce:
        logger.warning("OIDC id_token nonce mismatch")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Identity token nonce mismatch",
        )

    # Provision/lookup the local user. Imported lazily so this module loads even
    # before auth._persist_oidc_user exists (parallel change).
    from auth import _persist_oidc_user

    try:
        await _persist_oidc_user(
            db,
            sub=claims["sub"],
            email=claims.get("email"),
            display_name=claims.get("name") or claims.get("preferred_username"),
            issuer=claims["iss"],
            email_verified=bool(claims.get("email_verified", False)),
        )
    except HTTPException as exc:
        # A 403 means the account is not provisioned; the browser should land on
        # a friendly SPA error page, not a raw JSON 403.
        if exc.status_code == status.HTTP_403_FORBIDDEN:
            logger.info("OIDC login rejected: account not provisioned")
            error_response = RedirectResponse(
                url=_SPA_ERROR_PATH, status_code=status.HTTP_302_FOUND
            )
            _expire_flow_cookie(error_response)
            return error_response
        raise

    expires_in = token_response.get("expires_in")
    if not isinstance(expires_in, int):
        expires_in = 0

    fragment_params = {"id_token": id_token, "expires_in": expires_in}
    refresh_token = token_response.get("refresh_token")
    if refresh_token:
        handle = await _store_refresh_token(
            refresh_token, token_response.get("refresh_expires_in")
        )
        fragment_params["refresh_handle"] = handle

    redirect_url = f"{_SPA_CALLBACK_PATH}#{urlencode(fragment_params)}"
    response = RedirectResponse(url=redirect_url, status_code=status.HTTP_302_FOUND)
    _expire_flow_cookie(response)
    return response


@router.post("/refresh", response_model=RefreshResponse)
async def refresh(body: RefreshRequest, db: AsyncSession = Depends(get_db)) -> RefreshResponse:
    """Exchange a refresh handle for a fresh id_token, rotating the handle.

    Rotation: the new refresh_token is stored under a NEW handle and the old key
    is deleted, so a leaked handle is single-use. Unknown handle => 401.
    """
    _guard_enabled()

    redis = await get_redis_client()
    old_key = f"{_REFRESH_KEY_PREFIX}{body.refresh_handle}"
    # Atomic read-and-delete serializes concurrent refreshes for the same handle:
    # exactly one caller receives the token and reaches the OP; any racing caller
    # (e.g. a second browser tab) gets None here and a clean 401 without a second
    # submit of the same refresh_token to the OP (which would 4xx and log it out).
    raw = await redis.getdel(old_key)
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unknown or expired refresh handle",
        )
    try:
        stored_refresh_token = json.loads(raw)["refresh_token"]
    except (json.JSONDecodeError, KeyError):
        logger.error("Corrupt OIDC refresh entry in Redis")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh handle",
        )

    token_response = await _post_token_request(
        {
            "grant_type": "refresh_token",
            "refresh_token": stored_refresh_token,
            "client_id": oidc.OIDC_CLIENT_ID,
            "client_secret": oidc.OIDC_CLIENT_SECRET,
        }
    )

    id_token = token_response["id_token"]
    claims = await oidc.validate_oidc_token(id_token)

    # Re-affirm the user is still provisioned on every refresh.
    from auth import _persist_oidc_user

    await _persist_oidc_user(
        db,
        sub=claims["sub"],
        email=claims.get("email"),
        display_name=claims.get("name") or claims.get("preferred_username"),
        issuer=claims["iss"],
        email_verified=bool(claims.get("email_verified", False)),
    )

    expires_in = token_response.get("expires_in")
    if not isinstance(expires_in, int):
        expires_in = 0

    # Rotate: mint a new handle for the new refresh_token. The old key was already
    # consumed by the getdel above, so no explicit delete is needed here.
    new_refresh_token = token_response.get("refresh_token") or stored_refresh_token
    new_handle = await _store_refresh_token(
        new_refresh_token, token_response.get("refresh_expires_in")
    )

    return RefreshResponse(
        id_token=id_token, expires_in=expires_in, refresh_handle=new_handle
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshRequest) -> None:
    """Best-effort server-side logout: delete the refresh handle if present.

    Always returns 204 regardless of whether the handle existed (idempotent).
    """
    _guard_enabled()
    redis = await get_redis_client()
    await redis.delete(f"{_REFRESH_KEY_PREFIX}{body.refresh_handle}")
    return None
