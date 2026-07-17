"""Celery task for platform update discovery (upgrade design Part B, §2.2).

Polls the public GitHub Releases API for the OSS repo on a daily beat schedule,
validates the attached ``upgrade-manifest.json`` asset, and caches the result in
Redis (DB 0) under ``scf:update:latest``. The ``/api/version`` handler only ever
reads that key — no outbound call in the request path.

Design invariants:
  - Outbound-only from the operator's own backend; the project runs no server.
  - Opt-out for air-gapped installs via ``SCF_UPDATE_CHECK`` (default ON).
  - Fail closed on ambiguity: a release present but with a missing/invalid
    manifest never sets ``update_available`` — it records a ``manifest_missing``
    state, distinct from "GitHub unreachable".
  - Yanked-release hygiene: if GitHub reports no releases (404), the cached key
    is deleted rather than left pointing at a tag ``git checkout`` can't resolve.
"""
import json
import logging
import os
from datetime import datetime, timezone

import requests
from celery import shared_task

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TASK_PREFIX = "tasks_updates"

REQUEST_TIMEOUT = 20  # seconds per external HTTP call (matches tasks_research)

REDIS_KEY = "scf:update:latest"
REDIS_TTL_SECONDS = 26 * 60 * 60  # 26h — a little over the daily poll interval

GITHUB_REPO = "MarkAC007/scf-controls-platform-oss"
RELEASES_LATEST_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
MANIFEST_ASSET_NAME = "upgrade-manifest.json"
MANIFEST_SCHEMA_VERSION = 1

_GITHUB_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "SCF-Controls-Platform-UpdateCheck",
}

_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_sync_redis():
    """Return a synchronous Redis client for use inside the Celery task."""
    import redis as sync_redis

    return sync_redis.from_url(
        _REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
        socket_keepalive=True,
        retry_on_timeout=True,
        health_check_interval=30,
    )


def _update_check_enabled() -> bool:
    """Update check is ON unless SCF_UPDATE_CHECK is explicitly 'false'/'0'."""
    return os.getenv("SCF_UPDATE_CHECK", "").strip().lower() not in ("false", "0")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _strip_v(tag: str) -> str:
    return tag[1:] if tag[:1] in ("v", "V") else tag


def _write_state(redis_client, state: dict) -> None:
    """Persist the poller state to Redis, tolerating a Redis outage."""
    import redis as sync_redis

    try:
        redis_client.set(REDIS_KEY, json.dumps(state), ex=REDIS_TTL_SECONDS)
    except sync_redis.exceptions.RedisError as exc:
        logger.warning("Failed to write %s to Redis: %s", REDIS_KEY, exc)


def _delete_key(redis_client) -> None:
    import redis as sync_redis

    try:
        redis_client.delete(REDIS_KEY)
    except sync_redis.exceptions.RedisError as exc:
        logger.warning("Failed to delete %s from Redis: %s", REDIS_KEY, exc)


def _fetch_manifest(download_url: str) -> dict:
    """Fetch + validate the manifest asset. Raises ValueError if invalid."""
    resp = requests.get(download_url, headers=_GITHUB_HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    manifest = resp.json()
    if not isinstance(manifest, dict):
        raise ValueError("manifest is not a JSON object")
    if manifest.get("schema") != MANIFEST_SCHEMA_VERSION:
        raise ValueError(f"unexpected manifest schema {manifest.get('schema')!r}")
    if not manifest.get("version"):
        raise ValueError("manifest missing 'version'")
    return manifest


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------
@shared_task(
    bind=True,
    name=f"{TASK_PREFIX}.check_latest_release",
    autoretry_for=(requests.RequestException,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def check_latest_release(self):
    """Poll GitHub for the latest OSS release and cache the update state.

    Returns the state dict it wrote (or a small status dict for no-op paths).
    A 403 (rate limit) or 404 (no releases) is treated as a no-result and is
    NOT retried; other transport errors autoretry with backoff (bounded to 3).
    """
    checked_at = _now_iso()
    redis_client = _get_sync_redis()

    # Opt-out: record the disabled state so the endpoint can report it.
    if not _update_check_enabled():
        state = {"check_enabled": False, "checked_at": checked_at}
        _write_state(redis_client, state)
        logger.info("Update check disabled (SCF_UPDATE_CHECK); wrote disabled state.")
        return state

    resp = requests.get(RELEASES_LATEST_URL, headers=_GITHUB_HEADERS, timeout=REQUEST_TIMEOUT)

    # No releases at all: clear any stale pointer rather than leave it dangling.
    if resp.status_code == 404:
        _delete_key(redis_client)
        logger.info("GitHub reports no releases (404); cleared %s.", REDIS_KEY)
        return {"status": "no_releases", "checked_at": checked_at}

    # Rate limited / forbidden: no-result, do NOT retry, leave last-known value.
    if resp.status_code == 403:
        logger.warning("GitHub returned 403 (rate limited); leaving cached state untouched.")
        return {"status": "rate_limited", "checked_at": checked_at}

    resp.raise_for_status()
    release = resp.json()

    latest_tag = release.get("tag_name") or release.get("name")
    latest_version = _strip_v(latest_tag) if latest_tag else None

    # Locate the manifest asset.
    manifest_asset = next(
        (a for a in release.get("assets", []) if a.get("name") == MANIFEST_ASSET_NAME),
        None,
    )
    download_url = manifest_asset.get("browser_download_url") if manifest_asset else None

    if not latest_version or not download_url:
        state = {
            "status": "manifest_missing",
            "check_enabled": True,
            "latest_version": latest_version,
            "checked_at": checked_at,
        }
        _write_state(redis_client, state)
        logger.warning(
            "Release %s present but manifest asset missing — fail-closed (no update_available).",
            latest_tag,
        )
        return state

    # Fetch + validate the manifest. Any invalidity is fail-closed.
    try:
        manifest = _fetch_manifest(download_url)
    except (ValueError, requests.RequestException) as exc:
        state = {
            "status": "manifest_missing",
            "check_enabled": True,
            "latest_version": latest_version,
            "checked_at": checked_at,
        }
        _write_state(redis_client, state)
        logger.warning("Manifest for %s invalid/unfetchable (%s) — fail-closed.", latest_tag, exc)
        return state

    # Success: overwrite the cache with the fresh, validated state. Because we
    # always overwrite on a successful fetch, a yanked newer release (cached
    # version > fetched latest) is superseded by the correct current pointer.
    state = {
        "status": "ok",
        "check_enabled": True,
        "latest_version": manifest.get("version", latest_version),
        "breaking": bool(manifest.get("breaking", False)),
        "release_url": manifest.get("release_url") or release.get("html_url"),
        "summary": manifest.get("summary"),
        "min_upgradable_version": manifest.get("min_upgradable_version"),
        "required_stops": manifest.get("required_stops", []),
        "checked_at": checked_at,
    }
    _write_state(redis_client, state)
    logger.info("Update check OK: latest=%s (breaking=%s).", state["latest_version"], state["breaking"])
    return state
