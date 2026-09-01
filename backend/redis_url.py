"""Redis URL normalisation shared by the app and the beat healthcheck.

This lived as `_fix_rediss_url` inside `celery_app.py`, which meant it applied
to the processes that import `celery_app` and to nothing else. The beat
liveness probe (#784) is a bare `python -m celery_beat_heartbeat` — it
deliberately does not import the Celery app — so it would have read the raw
`rediss://` URL and applied redis-py's defaults (`ssl_cert_reqs="required"`,
hostname verification) while beat itself was connected with verification off.

That disagreement is not academic for any install whose `REDIS_URL` is
`rediss://` (an operator pointing the stack at a TLS-terminated Redis): the
probe would fail the TLS handshake against a perfectly healthy beat, and a
supervisor restarting on failed healthchecks would stop and replace a working
scheduler every couple of minutes, forever.
"""
from __future__ import annotations


def fix_rediss_url(url: str) -> str:
    """Append ssl_cert_reqs=CERT_NONE for rediss:// TLS connections."""
    if url.startswith("rediss://") and "ssl_cert_reqs" not in url:
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}ssl_cert_reqs=CERT_NONE"
    return url
