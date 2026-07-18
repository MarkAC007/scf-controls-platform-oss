"""Generic OpenID Connect (OIDC) Relying Party (RP) helper for CG SCF (issue #699).

This module is a *provider-agnostic* OIDC client. It performs discovery, JWKS
retrieval, id_token validation, and the PKCE/state/nonce primitives used by the
authorization-code flow. It deliberately imports nothing from ``auth.py`` so
that ``auth.py`` can import *this* module without any circular-import risk.

ISSUER vs DISCOVERY (reverse-proxy gotcha)
------------------------------------------
Two distinct URLs are configured because the browser and the backend often see
the OpenID Provider (OP) at different addresses:

* ``OIDC_ISSUER``  - the *public* issuer identifier. This is the value that a
  valid id_token's ``iss`` claim MUST byte-for-byte match, and it is also the
  origin the *browser* must be redirected to for the authorization endpoint.
* ``OIDC_DISCOVERY_URL`` - the URL the *backend* actually fetches over the wire
  (e.g. the in-cluster Docker DNS name ``http://keycloak:8080/realms/scf``).
  Defaults to ``OIDC_ISSUER`` when unset (i.e. no split proxy).

So: **validation and browser-facing URLs use ``OIDC_ISSUER``; server-side HTTP
fetches use ``OIDC_DISCOVERY_URL``.** The discovery document typically returns
endpoint URLs rooted at the public issuer; callers that need to fetch those
endpoints internally rebase their origin onto ``OIDC_DISCOVERY_URL`` via
``rebase_to_discovery_origin``, and callers that need to *redirect the browser*
rebase onto ``OIDC_ISSUER`` via ``rebase_to_issuer_origin``.

No token contents (id_token, access_token, refresh_token, code) are ever logged.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import HTTPException, status

from redis_client import get_redis_client

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (plain module-level env, matching backend/auth.py convention)
# ---------------------------------------------------------------------------
# Public issuer identifier: the exact string an id_token's `iss` must equal, and
# the origin the browser is redirected to. Presence of this var toggles OIDC on.
OIDC_ISSUER = os.getenv("OIDC_ISSUER")
# Internal URL the backend fetches (discovery/JWKS/token). Defaults to issuer.
OIDC_DISCOVERY_URL = os.getenv("OIDC_DISCOVERY_URL") or OIDC_ISSUER
OIDC_CLIENT_ID = os.getenv("OIDC_CLIENT_ID")
OIDC_CLIENT_SECRET = os.getenv("OIDC_CLIENT_SECRET")
OIDC_REDIRECT_URI = os.getenv("OIDC_REDIRECT_URI")
OIDC_SCOPES = os.getenv("OIDC_SCOPES", "openid email profile")

# Redis cache keys and TTLs.
_DISCOVERY_CACHE_KEY = "oidc:discovery"
_JWKS_CACHE_KEY = "oidc:jwks"
_DISCOVERY_TTL_SECONDS = 3600
_JWKS_TTL_SECONDS = 3600

# Allowed signing algorithms for id_token verification. Asymmetric only - a
# symmetric alg (HS*) would let anyone holding the client secret forge tokens,
# and "none" must never be accepted.
_ALLOWED_ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "PS256", "PS384", "PS512"]

# Clock-skew leeway (seconds) applied to exp/iat validation.
_CLOCK_LEEWAY_SECONDS = 30

# Wall-clock timeout for every outbound HTTP call to the OP.
_HTTP_TIMEOUT_SECONDS = 10.0


def oidc_enabled() -> bool:
    """Return True iff generic OIDC login is configured (OIDC_ISSUER is set)."""
    return bool(OIDC_ISSUER)


def _require_enabled() -> None:
    """Raise 501 if a helper is called while OIDC is disabled."""
    if not oidc_enabled():
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="OIDC login is not configured on this server",
        )


# ---------------------------------------------------------------------------
# URL origin rebasing (issuer vs discovery split)
# ---------------------------------------------------------------------------
def _rebase_origin(url: str, base: str) -> str:
    """Return ``url`` with its scheme+netloc replaced by those of ``base``.

    Path, query and fragment of ``url`` are preserved. When ``url`` and ``base``
    already share an origin the result is unchanged. Used to translate endpoint
    URLs from the discovery document between the public issuer origin and the
    internal discovery origin (the reverse-proxy split described in the module
    docstring).
    """
    src = urlsplit(url)
    dst = urlsplit(base)
    return urlunsplit((dst.scheme, dst.netloc, src.path, src.query, src.fragment))


def rebase_to_issuer_origin(url: str) -> str:
    """Rebase a discovery-document URL onto the public ``OIDC_ISSUER`` origin.

    Use for URLs the *browser* will contact (the authorization endpoint).
    """
    _require_enabled()
    # OIDC_ISSUER is guaranteed non-None by _require_enabled().
    return _rebase_origin(url, OIDC_ISSUER)  # type: ignore[arg-type]


def rebase_to_discovery_origin(url: str) -> str:
    """Rebase a discovery-document URL onto the internal ``OIDC_DISCOVERY_URL`` origin.

    Use for URLs the *backend* will contact server-side (token endpoint, etc.).
    When issuer and discovery origins are identical this is a no-op.
    """
    _require_enabled()
    base = OIDC_DISCOVERY_URL or OIDC_ISSUER
    return _rebase_origin(url, base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Discovery + JWKS (Redis-cached)
# ---------------------------------------------------------------------------
async def get_discovery() -> dict:
    """Return the OP's discovery document, Redis-cached for ~1h.

    Fetches ``{OIDC_DISCOVERY_URL}/.well-known/openid-configuration`` over the
    internal discovery origin. Raises 502 if the OP is unreachable or returns a
    malformed document.
    """
    _require_enabled()
    redis = await get_redis_client()

    cached = await redis.get(_DISCOVERY_CACHE_KEY)
    if cached:
        try:
            return json.loads(cached)
        except json.JSONDecodeError:
            # Corrupt cache entry - drop it and re-fetch below.
            logger.warning("Discarding corrupt cached OIDC discovery document")
            await redis.delete(_DISCOVERY_CACHE_KEY)

    base = (OIDC_DISCOVERY_URL or OIDC_ISSUER or "").rstrip("/")
    # Tolerate a base that already carries the well-known suffix (the shipped
    # .env.example does): strip it before re-appending so both a bare issuer
    # and a full discovery URL resolve to the single correct endpoint (F5a).
    _well_known = "/.well-known/openid-configuration"
    if base.endswith(_well_known):
        base = base[: -len(_well_known)].rstrip("/")
    discovery_url = f"{base}{_well_known}"
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            resp = await client.get(discovery_url)
            resp.raise_for_status()
            document = resp.json()
    except httpx.HTTPError as exc:
        logger.error("Failed to fetch OIDC discovery document: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to reach the identity provider (discovery)",
        )
    except json.JSONDecodeError:
        logger.error("OIDC discovery endpoint returned invalid JSON")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Identity provider returned a malformed discovery document",
        )

    if not isinstance(document, dict) or "jwks_uri" not in document:
        logger.error("OIDC discovery document missing required 'jwks_uri'")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Identity provider returned an incomplete discovery document",
        )

    await redis.set(_DISCOVERY_CACHE_KEY, json.dumps(document), ex=_DISCOVERY_TTL_SECONDS)
    return document


async def get_jwks(force_refresh: bool = False) -> dict:
    """Return the OP's JWKS document, Redis-cached for ~1h.

    ``force_refresh=True`` bypasses and repopulates the cache; used to recover
    from key rotation when an unknown ``kid`` is encountered during validation.
    Raises 502 if the JWKS cannot be fetched.
    """
    _require_enabled()
    redis = await get_redis_client()

    if not force_refresh:
        cached = await redis.get(_JWKS_CACHE_KEY)
        if cached:
            try:
                return json.loads(cached)
            except json.JSONDecodeError:
                logger.warning("Discarding corrupt cached OIDC JWKS")
                await redis.delete(_JWKS_CACHE_KEY)

    discovery = await get_discovery()
    jwks_uri = discovery.get("jwks_uri")
    if not jwks_uri:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Identity provider discovery document has no jwks_uri",
        )
    # The discovery doc advertises the public jwks_uri; fetch it internally.
    jwks_uri = rebase_to_discovery_origin(jwks_uri)

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            resp = await client.get(jwks_uri)
            resp.raise_for_status()
            jwks = resp.json()
    except httpx.HTTPError as exc:
        logger.error("Failed to fetch OIDC JWKS: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to reach the identity provider (JWKS)",
        )
    except json.JSONDecodeError:
        logger.error("OIDC JWKS endpoint returned invalid JSON")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Identity provider returned malformed JWKS",
        )

    if not isinstance(jwks, dict) or not jwks.get("keys"):
        logger.error("OIDC JWKS document contained no keys")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Identity provider returned an empty JWKS",
        )

    await redis.set(_JWKS_CACHE_KEY, json.dumps(jwks), ex=_JWKS_TTL_SECONDS)
    return jwks


# ---------------------------------------------------------------------------
# id_token validation
# ---------------------------------------------------------------------------
def _token_kid(token: str) -> Optional[str]:
    """Best-effort extraction of the JWS header ``kid`` without verifying.

    Returns None if the header cannot be parsed. Only the header (not the
    payload) is decoded, and nothing is logged.
    """
    try:
        header_segment = token.split(".", 1)[0]
        padded = header_segment + "=" * (-len(header_segment) % 4)
        header = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        kid = header.get("kid")
        return kid if isinstance(kid, str) else None
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _jwks_kids(jwks: dict) -> set:
    """Return the set of string ``kid`` values present in a JWKS document."""
    return {
        key.get("kid")
        for key in jwks.get("keys", [])
        if isinstance(key, dict) and isinstance(key.get("kid"), str)
    }


def _decode_with_jwks(token: str, jwks: dict) -> dict:
    """Verify ``token``'s signature against ``jwks`` and return raw claims.

    Signature + exp/iat (with leeway) are enforced here. ``iss``/``aud`` are
    validated by the caller (``validate_oidc_token``) so the azp fallback can be
    applied. Any signature/decode/claim failure raises an authlib ``JoseError``
    subclass up to the caller; kid/rotation handling is the caller's job.
    """
    from authlib.jose import JsonWebKey, JsonWebToken

    key_set = JsonWebKey.import_key_set(jwks)
    jwt = JsonWebToken(_ALLOWED_ALGORITHMS)

    # claims_options enforces iss byte-equality at decode time; aud handled by caller.
    claims_options = {"iss": {"essential": True, "value": OIDC_ISSUER}}
    claims = jwt.decode(token, key_set, claims_options=claims_options)

    # exp/iat/nbf validation with a small leeway for clock skew.
    claims.validate(leeway=_CLOCK_LEEWAY_SECONDS)
    return dict(claims)


async def validate_oidc_token(token: str) -> dict:
    """Validate an OIDC id_token and return its claims.

    Enforced:
      * signature against the OP JWKS (asymmetric algs only),
      * ``iss`` byte-equals ``OIDC_ISSUER``,
      * ``aud`` contains ``OIDC_CLIENT_ID`` (with the OIDC ``azp`` fallback: if
        ``aud`` equals the client id as a bare string that is accepted),
      * ``exp``/``iat`` within a small clock-skew leeway.

    A JWKS refetch is triggered ONLY for a genuine unknown ``kid`` (well-formed
    JWS whose key id is absent from the cached JWKS — i.e. OP key rotation).
    Non-JWT bearers (static/per-user API keys, opaque Google access tokens) have
    no parseable ``kid`` and are rejected as 401 *without* any network I/O, so
    ordinary API traffic can never hammer the IdP (F7). Any failure raises HTTP
    401. Token contents are never logged.
    """
    _require_enabled()
    from authlib.jose.errors import JoseError

    # Parse the JOSE header 'kid' up front with NO network I/O. A bearer that is
    # not a well-formed JWS (or carries no kid) is definitively not an id_token
    # for us: reject immediately and never force a JWKS refetch on its behalf.
    kid = _token_kid(token)
    if kid is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid identity token",
        )

    jwks = await get_jwks()

    # Only an ACTUAL unknown kid (well-formed JWS whose kid is missing from the
    # cached JWKS) justifies a single force-refresh + retry to recover from OP
    # key rotation. A known kid that later fails signature/claim checks is a real
    # invalid token and must NOT cause a refetch.
    if kid not in _jwks_kids(jwks):
        logger.info("OIDC token kid absent from cached JWKS; refreshing once")
        jwks = await get_jwks(force_refresh=True)
        if kid not in _jwks_kids(jwks):
            logger.warning("OIDC token kid unknown after JWKS refresh")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid identity token signature",
            )

    try:
        claims = _decode_with_jwks(token, jwks)
    except JoseError as exc:
        logger.warning("OIDC token validation failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid identity token",
        )

    _validate_audience(claims)
    return claims


def _validate_audience(claims: dict) -> None:
    """Enforce that ``aud`` admits ``OIDC_CLIENT_ID`` per the OIDC spec.

    ``aud`` may be a string or a list. When it is a list with more than one
    entry, the OIDC spec requires ``azp`` to be present and equal to our client
    id. Raises 401 on any mismatch.
    """
    aud = claims.get("aud")
    if isinstance(aud, str):
        audiences = [aud]
    elif isinstance(aud, list):
        audiences = [a for a in aud if isinstance(a, str)]
    else:
        audiences = []

    if OIDC_CLIENT_ID not in audiences:
        logger.warning("OIDC token audience does not include this client")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Identity token was not issued for this application",
        )

    # Multiple audiences => azp (authorized party) must be this client.
    if len(audiences) > 1:
        azp = claims.get("azp")
        if azp != OIDC_CLIENT_ID:
            logger.warning("OIDC token has multiple audiences without matching azp")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Identity token authorized party mismatch",
            )


# ---------------------------------------------------------------------------
# PKCE / state / nonce primitives (RFC 7636, OIDC core)
# ---------------------------------------------------------------------------
def _b64url_no_pad(data: bytes) -> str:
    """Base64url-encode without trailing '=' padding (RFC 7636)."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def generate_code_verifier() -> str:
    """Return a high-entropy PKCE ``code_verifier`` (43-128 url-safe chars)."""
    # 32 random bytes -> 43 base64url chars, within the RFC 7636 length bounds.
    return _b64url_no_pad(secrets.token_bytes(32))


def generate_code_challenge(code_verifier: str) -> str:
    """Return the S256 PKCE ``code_challenge`` for ``code_verifier``."""
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return _b64url_no_pad(digest)


def generate_state() -> str:
    """Return a random opaque CSRF ``state`` value."""
    return secrets.token_urlsafe(32)


def generate_nonce() -> str:
    """Return a random opaque ``nonce`` for id_token replay protection."""
    return secrets.token_urlsafe(32)


__all__ = [
    "OIDC_ISSUER",
    "OIDC_DISCOVERY_URL",
    "OIDC_CLIENT_ID",
    "OIDC_CLIENT_SECRET",
    "OIDC_REDIRECT_URI",
    "OIDC_SCOPES",
    "oidc_enabled",
    "get_discovery",
    "get_jwks",
    "validate_oidc_token",
    "rebase_to_issuer_origin",
    "rebase_to_discovery_origin",
    "generate_code_verifier",
    "generate_code_challenge",
    "generate_state",
    "generate_nonce",
]
