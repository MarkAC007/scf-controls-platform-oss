"""API tests for the tiered /api/version endpoint (upgrade design Part C / §8.6).

Verifies the anonymous coarse response leaks no precise version, and that the
authenticated response (any logged-in user, not only platform admins) carries
the ``update`` object across its states (ok / unknown / disabled / redis-failure)
plus the image ``build`` stamp. The sensitive boundary is authenticated-vs-
anonymous (M3 fingerprinting), not admin-vs-member. Uses
FastAPI TestClient + ``app.dependency_overrides`` (repo convention) and
monkeypatches the version/count/redis helpers so the test isolates the new
gating and update logic rather than a live database.
"""
from __future__ import annotations

import json
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402 — imports the FastAPI app
from api import database_stats  # noqa: E402
from auth import User, optional_auth  # noqa: E402
from database import get_db  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class _FakeAsyncRedis:
    def __init__(self, value):
        self._value = value

    async def get(self, key):
        return self._value


async def _fake_get_db():
    yield object()  # never actually queried — counts helper is monkeypatched


@pytest.fixture(autouse=True)
def _isolate_helpers(monkeypatch):
    """Neutralise the DB/git/file helpers so the handler runs in-process."""
    async def _counts(_db):
        return {"controls": 10, "evidence_items": 20, "collection_interfaces": 30}

    monkeypatch.setattr(database_stats, "get_catalog_counts", _counts)
    monkeypatch.setattr(database_stats, "get_platform_version", lambda: "0.8.0")
    monkeypatch.setattr(database_stats, "get_catalog_version", lambda: "2025.4")
    monkeypatch.setattr(database_stats, "get_git_info", lambda _p: None)
    monkeypatch.setattr(database_stats, "get_build_info", lambda: None)
    monkeypatch.setenv("SCF_UPDATE_CHECK", "true")


@pytest.fixture
def client():
    c = TestClient(main.app)
    yield c
    main.app.dependency_overrides.pop(optional_auth, None)
    main.app.dependency_overrides.pop(get_db, None)


def _as_anonymous():
    main.app.dependency_overrides[optional_auth] = lambda: None
    main.app.dependency_overrides[get_db] = _fake_get_db


def _as_admin():
    # Static API-key user — one representative authenticated caller.
    main.app.dependency_overrides[optional_auth] = lambda: User(
        user_id="api_user", auth_method="api_key"
    )
    main.app.dependency_overrides[get_db] = _fake_get_db


def _as_member():
    # A non-admin, Google-authenticated org member. The endpoint gates on
    # authentication, not platform-admin, so this caller gets the full payload.
    main.app.dependency_overrides[optional_auth] = lambda: User(
        user_id="member@example.com", email="member@example.com",
        auth_method="google", db_id="00000000-0000-0000-0000-000000000001",
    )
    main.app.dependency_overrides[get_db] = _fake_get_db


def _set_redis(monkeypatch, state):
    value = json.dumps(state) if state is not None else None

    async def _get_redis():
        return _FakeAsyncRedis(value)

    monkeypatch.setattr(database_stats, "get_redis_client", _get_redis)


# ---------------------------------------------------------------------------
# Anonymous — coarse only
# ---------------------------------------------------------------------------
def test_anonymous_gets_coarse_response_only(client):
    _as_anonymous()
    resp = client.get("/api/version")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"platform": {"api_version": "1.0.0"}, "status": "ok"}
    # No precise version, git commit, catalog, update, or build leaked.
    assert "version" not in body["platform"]
    assert "catalog" not in body
    assert "update" not in body
    assert "build" not in body


# ---------------------------------------------------------------------------
# Non-admin authenticated member — also gets the full payload
# ---------------------------------------------------------------------------
def test_non_admin_member_gets_full_payload(client, monkeypatch):
    _as_member()
    _set_redis(monkeypatch, None)  # unknown state; shape is what matters here
    monkeypatch.setattr(
        database_stats, "get_build_info", lambda: {"build_stamp": "def5678", "version": "0.8.0"}
    )
    body = client.get("/api/version").json()
    # A logged-in member sees precise version, catalog, update object and build stamp.
    assert body["platform"]["version"] == "0.8.0"
    assert body["catalog"]["controls_count"] == 10
    assert "update" in body
    assert body["build"] == {"build_stamp": "def5678"}


# ---------------------------------------------------------------------------
# Authenticated caller — full shape + update states
# ---------------------------------------------------------------------------
def test_admin_ok_state_reports_update_available(client, monkeypatch):
    _as_admin()
    _set_redis(
        monkeypatch,
        {
            "status": "ok",
            "check_enabled": True,
            "latest_version": "0.9.0",
            "breaking": True,
            "release_url": "https://github.com/x/releases/tag/v0.9.0",
            "summary": "Breaking change; read notes.",
            "min_upgradable_version": "0.6.0",
            "required_stops": [],
            "checked_at": "2026-07-17T02:00:00+00:00",
        },
    )
    monkeypatch.setattr(
        database_stats, "get_build_info", lambda: {"build_stamp": "abc1234", "version": "0.8.0"}
    )

    body = client.get("/api/version").json()
    assert body["platform"]["version"] == "0.8.0"
    assert body["catalog"]["controls_count"] == 10
    update = body["update"]
    assert update["check_enabled"] is True
    assert update["installed_version"] == "0.8.0"
    assert update["latest_version"] == "0.9.0"
    assert update["update_available"] is True
    assert update["breaking"] is True
    assert update["skip_blocked"] is False  # 0.8.0 >= 0.6.0 floor
    assert body["build"] == {"build_stamp": "abc1234"}


def test_admin_no_update_when_installed_is_latest(client, monkeypatch):
    _as_admin()
    _set_redis(
        monkeypatch,
        {
            "status": "ok",
            "check_enabled": True,
            "latest_version": "0.8.0",
            "breaking": False,
            "min_upgradable_version": "0.6.0",
            "checked_at": "2026-07-17T02:00:00+00:00",
        },
    )
    update = client.get("/api/version").json()["update"]
    assert update["update_available"] is False


def test_admin_skip_blocked_when_below_floor(client, monkeypatch):
    _as_admin()
    monkeypatch.setattr(database_stats, "get_platform_version", lambda: "0.5.0")
    _set_redis(
        monkeypatch,
        {
            "status": "ok",
            "check_enabled": True,
            "latest_version": "0.9.0",
            "breaking": False,
            "min_upgradable_version": "0.6.0",
            "checked_at": "2026-07-17T02:00:00+00:00",
        },
    )
    update = client.get("/api/version").json()["update"]
    assert update["update_available"] is True
    assert update["skip_blocked"] is True  # 0.5.0 < 0.6.0 floor


def test_admin_unknown_state_when_no_cache(client, monkeypatch):
    _as_admin()
    _set_redis(monkeypatch, None)  # empty key
    update = client.get("/api/version").json()["update"]
    assert update == {"check_enabled": True, "update_available": None, "last_checked": None}


def test_admin_manifest_missing_state(client, monkeypatch):
    _as_admin()
    _set_redis(
        monkeypatch,
        {"status": "manifest_missing", "check_enabled": True, "latest_version": "0.9.0",
         "checked_at": "2026-07-17T02:00:00+00:00"},
    )
    update = client.get("/api/version").json()["update"]
    assert update["update_available"] is None
    assert update["status"] == "manifest_missing"


def test_admin_disabled_state(client, monkeypatch):
    _as_admin()
    monkeypatch.setenv("SCF_UPDATE_CHECK", "false")
    # Redis should not even be consulted, but provide a stub anyway.
    _set_redis(monkeypatch, None)
    update = client.get("/api/version").json()["update"]
    assert update == {"check_enabled": False}


def test_admin_redis_failure_degrades_gracefully(client, monkeypatch):
    _as_admin()

    async def _boom():
        raise RuntimeError("redis down")

    monkeypatch.setattr(database_stats, "get_redis_client", _boom)

    resp = client.get("/api/version")
    assert resp.status_code == 200  # endpoint must not error
    update = resp.json()["update"]
    assert update == {"check_enabled": True, "update_available": None, "last_checked": None}
