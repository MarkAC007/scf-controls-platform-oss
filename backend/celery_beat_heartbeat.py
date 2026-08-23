"""Beat liveness that observes the scheduler ticking (#784).

The healthcheck this replaces was::

    test: ["CMD-SHELL", "grep -q beat /proc/1/cmdline || exit 1"]

which asserts that PID 1's command line contains the string "beat". A command
line does not change when the process behind it stops working, so a beat that
had wedged or lost its broker reported healthy indefinitely while scheduled
evidence collection had silently stopped. Paired with #784's other half — all
INFO logging discarded when App Insights is unset, i.e. every self-hosted
deployment — there was no signal left through which anyone would notice.

How this works
--------------
``HeartbeatScheduler`` (in ``celery_beat_scheduler``) stamps a Redis key on
every scheduler tick, with a TTL. This module answers the probe's question and
holds no Celery imports, so ``python -m celery_beat_heartbeat`` starts in
milliseconds rather than paying for the whole task tree.

What the probe reports
----------------------
Three outcomes, not two:

* key present  -> healthy.
* key absent, Redis answered -> **unhealthy**. This is the case the probe
  exists for: the broker is fine and the scheduler is not.
* Redis unreachable -> **inconclusive, reported healthy**, loudly.

That last one is a deliberate reversal of the obvious "fail closed" instinct,
because of where this is wired. It is an ECS container health check on an
``essential = true`` container, and `celery_app` documents that the managed
Redis force-reboots and leaves clients holding dead sockets. Failing closed
would turn a 90-second broker blip into ECS stopping and replacing a beat that
was never at fault — amplifying an outage instead of reporting one. Restarting
beat cannot fix Redis, and beat cannot dispatch anything until Redis returns
either way. Broker death is already covered by the Redis service healthcheck
and by the worker's `celery inspect ping`; this probe is scoped to the one
thing only it can see.
"""
from __future__ import annotations

import logging
import os
import socket

from redis_url import fix_rediss_url

logger = logging.getLogger(__name__)

#: Prefix for the key stamped on every tick. Namespaced so it is obvious what
#: wrote it when someone is staring at a KEYS dump.
HEARTBEAT_KEY_PREFIX = "scf:celery-beat:heartbeat"

#: Floor for how long the key survives without a further tick.
#:
#: ``PersistentScheduler`` sleeps at most ``max_interval`` between ticks even
#: when nothing is due, so the healthy worst-case gap is that interval. The
#: default is 300s, and 900s gives two missed ticks of slack — enough that a
#: slow tick or a brief Redis blip does not restart a working scheduler, short
#: enough that a genuinely dead beat is caught inside a quarter of an hour.
#:
#: This is a FLOOR, not the whole story: ``max_interval`` is settable at
#: runtime (``celery beat --max-interval``, ``beat_max_loop_interval``), so
#: ``ttl_for_interval`` scales the TTL up when it is raised. A fixed 900s
#: would make a healthy beat fail its own probe above ~450s.
HEARTBEAT_TTL_SECONDS = 900

#: Multiple of max_interval the TTL must cover: two missed ticks plus headroom.
TTL_INTERVAL_MULTIPLIER = 3

#: Socket timeouts, in seconds.
#:
#: Deliberately short. ``write_heartbeat`` runs INLINE in beat's tick loop and
#: beat has no other thread, so every second spent waiting on Redis is a second
#: every due task is late. A best-effort stamp that gives up quickly is worth
#: more than one that blocks the scheduler. It also keeps the probe inside the
#: container healthcheck's own timeout, so it exits 1 with a diagnostic line
#: instead of being SIGKILLed silently.
SOCKET_TIMEOUT_SECONDS = 2


def heartbeat_key() -> str:
    """Per-deployment, per-instance key.

    A bare shared key is a false-healthy waiting to happen: two beats on one
    Redis DB — staging and production pointed at the same cache, the two sides
    of a blue/green cutover, a second `docker compose up` against the same
    broker — would each keep the other's probe green. That is exactly the
    "dead beat reports healthy" this whole change removes, reintroduced by the
    fix. The healthcheck runs inside the beat container, so it sees the same
    hostname and the same ENVIRONMENT the scheduler stamped with.
    """
    environment = os.getenv("ENVIRONMENT", "unknown")
    return f"{HEARTBEAT_KEY_PREFIX}:{environment}:{socket.gethostname()}"


def ttl_for_interval(max_interval: float | None) -> int:
    """TTL that always clears the scheduler's real sleep interval."""
    if not max_interval or max_interval <= 0:
        return HEARTBEAT_TTL_SECONDS
    return max(HEARTBEAT_TTL_SECONDS, int(max_interval * TTL_INTERVAL_MULTIPLIER))


def _redis_url() -> str:
    """Where to stamp: the broker, because beat is useless without it anyway.

    Normalised through the same `fix_rediss_url` the app applies, so the probe
    and the writer agree about TLS verification on `rediss://` endpoints.
    """
    raw = (
        os.getenv("CELERY_BROKER_URL")
        or os.getenv("REDIS_URL")
        or "redis://localhost:6379/1"
    )
    return fix_rediss_url(raw)


_client_cache = None
_client_cache_url = None


def _client(reset: bool = False):
    """A cached Redis client, or ``None`` when one cannot be built.

    Cached because ``write_heartbeat`` runs on every tick: building a fresh
    ``redis.Redis`` each time means a new pool and a new TCP/TLS handshake per
    tick, none of them ever closed. Keyed on the URL so a test or a re-exec
    that changes the broker is not served a stale client.

    ``redis`` is imported lazily — this module is imported at Celery
    configuration time, including in test and CLI contexts with no broker.
    """
    global _client_cache, _client_cache_url

    url = _redis_url()
    if reset or _client_cache_url != url:
        _client_cache, _client_cache_url = None, None

    if _client_cache is not None:
        return _client_cache

    try:
        import redis  # noqa: PLC0415 — lazy on purpose, see docstring
    except ImportError:  # pragma: no cover — redis is a hard worker dependency
        logger.warning("redis package unavailable — beat heartbeat disabled")
        return None
    try:
        _client_cache = redis.Redis.from_url(
            url,
            socket_connect_timeout=SOCKET_TIMEOUT_SECONDS,
            socket_timeout=SOCKET_TIMEOUT_SECONDS,
        )
        _client_cache_url = url
        return _client_cache
    except Exception as exc:  # noqa: BLE001 — a bad URL must not stop beat
        logger.warning("Could not build a Redis client for the beat heartbeat: %s", exc)
        return None


def write_heartbeat(max_interval: float | None = None) -> bool:
    """Stamp the heartbeat key. Returns whether it landed. Never raises.

    Swallowing is the point: a scheduler that stopped scheduling because its
    liveness probe raised would be a worse bug than the one being fixed.
    """
    client = _client()
    if client is None:
        return False
    try:
        client.set(heartbeat_key(), "1", ex=ttl_for_interval(max_interval))
        return True
    except Exception as exc:  # noqa: BLE001 — see docstring
        logger.warning("Beat heartbeat write failed: %s", exc)
        _client(reset=True)  # drop a client holding a dead socket
        return False


#: Probe verdicts.
FRESH = "fresh"          # beat ticked recently
STALE = "stale"          # Redis answered and the key is gone — beat is dead
UNKNOWN = "unknown"      # cannot ask; see the module docstring


def heartbeat_status() -> str:
    """One of FRESH / STALE / UNKNOWN. Never raises."""
    client = _client()
    if client is None:
        return UNKNOWN
    try:
        return FRESH if client.exists(heartbeat_key()) else STALE
    except Exception as exc:  # noqa: BLE001
        logger.warning("Beat heartbeat read failed: %s", exc)
        _client(reset=True)
        return UNKNOWN


def main() -> int:
    """Entry point for the container healthcheck. 0 = healthy, 1 = not."""
    status = heartbeat_status()
    key = heartbeat_key()
    if status == FRESH:
        print(f"beat heartbeat present ({key})")
        return 0
    if status == STALE:
        print(f"beat heartbeat MISSING — scheduler is not ticking ({key})")
        return 1
    print(
        f"beat heartbeat INDETERMINATE — Redis unreachable, cannot judge beat ({key}). "
        "Reporting healthy so a broker outage does not churn a working scheduler; "
        "broker liveness is the redis service's own healthcheck."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
