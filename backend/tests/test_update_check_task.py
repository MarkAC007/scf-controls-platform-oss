"""Tests for the update-discovery Celery task (upgrade design Part B).

The task is exercised locally via ``.apply()`` (synchronous, no broker) with
``requests`` and Redis mocked at the module boundary — mirroring the repo
convention of testing Celery tasks by calling them directly with hand-rolled
fakes rather than a live worker.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tasks_updates  # noqa: E402
from tasks_updates import REDIS_KEY, check_latest_release  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class _FakeRedis:
    """Minimal sync Redis stand-in (decode_responses semantics: str values)."""

    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None):
        self.store[key] = value

    def delete(self, key):
        self.store.pop(key, None)


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        import requests

        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")


def _run():
    """Execute the task synchronously and return its result value."""
    return check_latest_release.apply(args=()).get()


@pytest.fixture
def fake_redis(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(tasks_updates, "_get_sync_redis", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def _enable_check(monkeypatch):
    # Default the tests to "check enabled" unless a test overrides it.
    monkeypatch.setenv("SCF_UPDATE_CHECK", "true")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_success_writes_ok_state(fake_redis, monkeypatch):
    release = {
        "tag_name": "v0.9.0",
        "html_url": "https://github.com/x/releases/tag/v0.9.0",
        "assets": [
            {"name": "upgrade-manifest.json", "browser_download_url": "https://x/m.json"}
        ],
    }
    manifest = {
        "schema": 1,
        "version": "0.9.0",
        "breaking": False,
        "release_url": "https://github.com/x/releases/tag/v0.9.0",
        "summary": "Adds webhook retries; safe additive migration.",
        "min_upgradable_version": "0.6.0",
        "required_stops": [],
    }

    def _get(url, **kwargs):
        if url == tasks_updates.RELEASES_LATEST_URL:
            return _FakeResponse(200, release)
        assert url == "https://x/m.json"
        return _FakeResponse(200, manifest)

    monkeypatch.setattr(tasks_updates.requests, "get", _get)

    result = _run()
    assert result["status"] == "ok"

    stored = json.loads(fake_redis.store[REDIS_KEY])
    assert stored["status"] == "ok"
    assert stored["check_enabled"] is True
    assert stored["latest_version"] == "0.9.0"
    assert stored["breaking"] is False
    assert stored["min_upgradable_version"] == "0.6.0"
    assert stored["summary"].startswith("Adds webhook")
    assert "checked_at" in stored


def test_manifest_missing_is_fail_closed(fake_redis, monkeypatch):
    release = {"tag_name": "v0.9.0", "html_url": "https://x", "assets": []}
    monkeypatch.setattr(
        tasks_updates.requests, "get", lambda url, **kw: _FakeResponse(200, release)
    )

    result = _run()
    assert result["status"] == "manifest_missing"

    stored = json.loads(fake_redis.store[REDIS_KEY])
    assert stored["status"] == "manifest_missing"
    # Fail-closed: never advertises an available update.
    assert "update_available" not in stored
    assert stored.get("latest_version") == "0.9.0"


def test_invalid_manifest_schema_is_fail_closed(fake_redis, monkeypatch):
    release = {
        "tag_name": "v0.9.0",
        "html_url": "https://x",
        "assets": [
            {"name": "upgrade-manifest.json", "browser_download_url": "https://x/m.json"}
        ],
    }
    bad_manifest = {"schema": 99, "version": "0.9.0"}  # wrong schema

    def _get(url, **kwargs):
        if url == tasks_updates.RELEASES_LATEST_URL:
            return _FakeResponse(200, release)
        return _FakeResponse(200, bad_manifest)

    monkeypatch.setattr(tasks_updates.requests, "get", _get)

    result = _run()
    assert result["status"] == "manifest_missing"


def test_disabled_writes_disabled_state_and_skips_http(fake_redis, monkeypatch):
    monkeypatch.setenv("SCF_UPDATE_CHECK", "false")

    def _boom(*a, **k):
        raise AssertionError("HTTP must not be called when update check is disabled")

    monkeypatch.setattr(tasks_updates.requests, "get", _boom)

    result = _run()
    assert result["check_enabled"] is False

    stored = json.loads(fake_redis.store[REDIS_KEY])
    assert stored["check_enabled"] is False
    assert "checked_at" in stored


def test_deleted_release_clears_stale_key(fake_redis, monkeypatch):
    # A previously-cached "update available" pointer...
    fake_redis.store[REDIS_KEY] = json.dumps(
        {"status": "ok", "latest_version": "0.9.0", "check_enabled": True}
    )
    # ...but GitHub now reports no releases (404).
    monkeypatch.setattr(
        tasks_updates.requests, "get", lambda url, **kw: _FakeResponse(404, {})
    )

    result = _run()
    assert result["status"] == "no_releases"
    assert REDIS_KEY not in fake_redis.store


def test_rate_limited_leaves_cache_untouched(fake_redis, monkeypatch):
    fake_redis.store[REDIS_KEY] = json.dumps(
        {"status": "ok", "latest_version": "0.9.0", "check_enabled": True}
    )
    monkeypatch.setattr(
        tasks_updates.requests, "get", lambda url, **kw: _FakeResponse(403, {})
    )

    result = _run()
    assert result["status"] == "rate_limited"
    # Last-known value preserved, not overwritten or deleted.
    assert json.loads(fake_redis.store[REDIS_KEY])["latest_version"] == "0.9.0"
