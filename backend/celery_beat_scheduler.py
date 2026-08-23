"""The beat scheduler that stamps a liveness heartbeat (#784).

Split from ``celery_beat_heartbeat`` so that module stays free of Celery
imports: the container healthcheck runs ``python -m celery_beat_heartbeat``
every 30 seconds, and ``from celery.beat import PersistentScheduler`` drags in
enough of Celery to matter at that cadence — and to risk the probe being
SIGKILLed at its own timeout rather than exiting with a diagnostic line.
"""
from __future__ import annotations

from celery.beat import PersistentScheduler

from celery_beat_heartbeat import write_heartbeat


class HeartbeatScheduler(PersistentScheduler):
    """``PersistentScheduler`` that stamps the heartbeat key on every tick.

    A subclass, not a replacement: ``--schedule /tmp/celerybeat-schedule`` (the
    uid-1001 bind-mount workaround in docker-compose) and shelve storage keep
    working untouched.

    ``tick()`` is the scheduler's loop body — it runs whether or not anything
    is due, so the stamp tracks the loop itself rather than the tasks it
    happens to dispatch. That distinction matters: a heartbeat driven by a
    periodic task would prove *a worker* ran something, and would go stale when
    the worker died even though beat was fine.
    """

    def tick(self, *args, **kwargs):
        result = super().tick(*args, **kwargs)
        # Pass the scheduler's ACTUAL sleep bound, not the class default:
        # `celery beat --max-interval` and `beat_max_loop_interval` both raise
        # it at runtime, and a fixed TTL would make a healthy beat fail its own
        # probe once the interval exceeded a third of it.
        write_heartbeat(max_interval=getattr(self, "max_interval", None))
        return result
