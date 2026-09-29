"""Google access-token validation must not sign users out on a request burst.

Prod runs Google auth, where every API request was validated with two live
Google calls. Opening an evidence task fans out ~40 requests; one transient
Google failure in that burst returned 401, and the SPA treats any 401 as a
dead session — clearing the token and bouncing the user to sign-in.

These tests pin the fix: the resolved identity is cached per token, concurrent
requests share one lookup, and Google-side failures surface as a retryable 503
(never 401) all the way out of ``_authenticate``.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

import httpx
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auth as auth_mod  # noqa: E402

pytestmark = pytest.mark.asyncio

IDENTITY = {"sub": "g-123", "email": "user@example.com", "name": "User"}


class FakeRedis:
    def __init__(self, fail: bool = False):
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.fail = fail

    async def get(self, key):
        if self.fail:
            raise ConnectionError("redis down")
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        if self.fail:
            raise ConnectionError("redis down")
        self.store[key] = value
        self.ttls[key] = ex


@pytest.fixture
def fake_redis(monkeypatch):
    redis = FakeRedis()

    async def _get():
        return redis

    monkeypatch.setattr(auth_mod, "get_redis_client", _get)
    return redis


@pytest.fixture
def counting_fetch(monkeypatch):
    calls = {"n": 0}

    async def _fetch(token):
        calls["n"] += 1
        await asyncio.sleep(0.01)  # let concurrent callers pile up
        return dict(IDENTITY), 3599

    monkeypatch.setattr(auth_mod, "_fetch_google_identity", _fetch)
    return calls


async def test_burst_of_requests_shares_one_google_lookup(fake_redis, counting_fetch):
    results = await asyncio.gather(
        *(auth_mod._resolve_google_identity("tok") for _ in range(40))
    )
    assert counting_fetch["n"] == 1
    assert all(r == IDENTITY for r in results)


async def test_cached_identity_skips_google(fake_redis, counting_fetch):
    await auth_mod._resolve_google_identity("tok")
    await auth_mod._resolve_google_identity("tok")
    assert counting_fetch["n"] == 1


async def test_cache_is_keyed_by_hash_not_raw_token(fake_redis, counting_fetch):
    await auth_mod._resolve_google_identity("secret-token-value")
    assert fake_redis.store
    assert all("secret-token-value" not in k for k in fake_redis.store)
    assert json.loads(next(iter(fake_redis.store.values()))) == IDENTITY


async def test_cache_ttl_capped_and_never_outlives_token(fake_redis, monkeypatch):
    async def _fetch(token):
        return dict(IDENTITY), 3599 if token == "long" else 42

    monkeypatch.setattr(auth_mod, "_fetch_google_identity", _fetch)
    await auth_mod._resolve_google_identity("long")
    await auth_mod._resolve_google_identity("short")
    assert fake_redis.ttls[auth_mod._google_identity_cache_key("long")] == 300
    assert fake_redis.ttls[auth_mod._google_identity_cache_key("short")] == 42


async def test_redis_outage_does_not_fail_auth(monkeypatch, counting_fetch):
    redis = FakeRedis(fail=True)

    async def _get():
        return redis

    monkeypatch.setattr(auth_mod, "get_redis_client", _get)
    assert await auth_mod._resolve_google_identity("tok") == IDENTITY


async def test_failed_lookup_is_not_cached_and_propagates_to_waiters(fake_redis, monkeypatch):
    async def _fetch(token):
        await asyncio.sleep(0.01)
        raise auth_mod.GoogleIdentityUnavailable("tokeninfo returned 503")

    monkeypatch.setattr(auth_mod, "_fetch_google_identity", _fetch)
    results = await asyncio.gather(
        *(auth_mod._resolve_google_identity("tok") for _ in range(5)),
        return_exceptions=True,
    )
    assert all(isinstance(r, auth_mod.GoogleIdentityUnavailable) for r in results)
    assert fake_redis.store == {}
    assert auth_mod._google_identity_inflight == {}


def _patch_google_http(monkeypatch, handler):
    real_client = httpx.AsyncClient

    def _client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _client)
    monkeypatch.setattr(auth_mod, "GOOGLE_CLIENT_ID", "client-id")


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_google_server_errors_are_transient(monkeypatch, status):
    _patch_google_http(monkeypatch, lambda req: httpx.Response(status, text="busy"))
    with pytest.raises(auth_mod.GoogleIdentityUnavailable):
        await auth_mod._fetch_google_identity("tok")


async def test_google_timeout_is_transient(monkeypatch):
    def _handler(req):
        raise httpx.ReadTimeout("slow", request=req)

    _patch_google_http(monkeypatch, _handler)
    with pytest.raises(auth_mod.GoogleIdentityUnavailable):
        await auth_mod._fetch_google_identity("tok")


async def test_userinfo_server_error_is_transient(monkeypatch):
    def _handler(req):
        if "tokeninfo" in str(req.url):
            return httpx.Response(200, json={"aud": "client-id", "expires_in": "3000"})
        return httpx.Response(503, text="busy")

    _patch_google_http(monkeypatch, _handler)
    with pytest.raises(auth_mod.GoogleIdentityUnavailable):
        await auth_mod._fetch_google_identity("tok")


async def test_invalid_token_is_still_rejected(monkeypatch):
    _patch_google_http(
        monkeypatch,
        lambda req: httpx.Response(400, json={"error_description": "Invalid Value", "error": "invalid_token"}),
    )
    with pytest.raises(Exception) as exc_info:
        await auth_mod._fetch_google_identity("tok")
    assert not isinstance(exc_info.value, auth_mod.GoogleIdentityUnavailable)


async def test_valid_token_returns_identity_and_expiry(monkeypatch):
    def _handler(req):
        if "tokeninfo" in str(req.url):
            return httpx.Response(200, json={"aud": "client-id", "expires_in": "1234"})
        return httpx.Response(200, json=IDENTITY)

    _patch_google_http(monkeypatch, _handler)
    identity, expires_in = await auth_mod._fetch_google_identity("tok")
    assert identity == IDENTITY
    assert expires_in == 1234


async def test_validate_google_token_maps_unavailable_to_503(monkeypatch):
    async def _resolve(token):
        raise auth_mod.GoogleIdentityUnavailable("Google unreachable: ReadTimeout")

    monkeypatch.setattr(auth_mod, "GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setattr(auth_mod, "_resolve_google_identity", _resolve)
    with pytest.raises(HTTPException) as exc_info:
        await auth_mod.validate_google_token("tok", db=None)
    assert exc_info.value.status_code == 503


async def test_validate_google_token_rejected_token_is_401(monkeypatch):
    async def _resolve(token):
        raise Exception("Token expired. Please sign in again.")

    monkeypatch.setattr(auth_mod, "GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setattr(auth_mod, "_resolve_google_identity", _resolve)
    with pytest.raises(HTTPException) as exc_info:
        await auth_mod.validate_google_token("tok", db=None)
    assert exc_info.value.status_code == 401


async def test_authenticate_surfaces_503_instead_of_falling_through_to_401(monkeypatch):
    import oidc

    async def _google(token, db):
        raise HTTPException(status_code=503, detail="Google sign-in is temporarily unavailable. Please retry.")

    monkeypatch.setattr(oidc, "oidc_enabled", lambda: False)
    monkeypatch.setattr(auth_mod, "GOOGLE_AUTH_ENABLED", True)
    monkeypatch.setattr(auth_mod, "validate_google_token", _google)
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="tok")
    with pytest.raises(HTTPException) as exc_info:
        await auth_mod._authenticate(creds, db=None)
    assert exc_info.value.status_code == 503
