#!/usr/bin/env python3
"""Fail if any Anthropic model in the registry no longer resolves (#782).

The bug this exists to prevent
------------------------------
Four services shipped pinned to ``claude-sonnet-4-20250514``. It was retired,
the API answered 404, and AI evidence review was entirely non-functional in
production — discovered only by triggering an assessment by hand and reading
``celery-worker`` logs. It failed soft: an assessment row with ``status=error``
and a low-severity "Error" chip in the UI.

Repointing the ids fixes today. Only a check that runs on a schedule fixes the
next retirement, which is already scheduled for whatever is current now.

Usage
-----
    ANTHROPIC_API_KEY=... python scripts/check_model_ids.py

Exit codes:
    0  every Anthropic id in the registry resolves, or no API key was available
       on a run where that is tolerated (see below)
    1  at least one id did not resolve, or no API key was available on a run
       where one is required
    2  the models endpoint could not be reached at all

**Without a key this check is inert.** Set ``MODEL_LIVENESS_REQUIRE_KEY=1`` and
it exits 1 instead of 0 in that case — the scheduled workflow run does exactly
that, so an unarmed check shows up as a failing job rather than a green tick
with a warning annotation nobody opens. Pull-request runs leave it unset,
because a fork PR can never see a repository secret.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.model_registry import MODELS, ROLES, anthropic_model_ids  # noqa: E402

MODELS_URL = "https://api.anthropic.com/v1/models?limit=1000"
API_VERSION = "2023-06-01"

IN_GITHUB = bool(os.getenv("GITHUB_ACTIONS"))


def _annotate(level: str, message: str) -> None:
    """Print a GitHub annotation as well as plain text, so CI surfaces it."""
    print(message)
    if IN_GITHUB:
        print(f"::{level}::{message}")


def is_live(model_id: str, live_ids) -> bool:
    """True when ``model_id`` is listed, or listed as a dated snapshot of itself.

    An undated id may be an alias the list endpoint reports only in its
    ``-YYYYMMDD`` form, so an exact-membership test alone would call a working
    alias dead. The snapshot match is anchored and digits-only on purpose: a
    plain ``startswith`` would let ``claude-haiku-4-5-20260101`` vouch for a
    retired ``claude-haiku-4``. That is a different model, and reporting "all
    ids resolve" for one that 404s is precisely the failure this job exists to
    catch.
    """
    if model_id in live_ids:
        return True
    snapshot = re.compile(rf"^{re.escape(model_id)}-\d{{8}}$")
    return any(snapshot.match(entry) for entry in live_ids)


def fetch_live_ids(api_key: str) -> set:
    request = urllib.request.Request(
        MODELS_URL,
        headers={"x-api-key": api_key, "anthropic-version": API_VERSION},
    )
    # False positive: the URL is the module constant MODELS_URL, baked into the
    # Request object two lines above. Nothing about it is caller- or
    # environment-derived — the registry supplies model ids, which are compared
    # against the response, never interpolated into the request.
    # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return {entry["id"] for entry in payload.get("data", []) if entry.get("id")}


def main() -> int:
    registry_ids = anthropic_model_ids()
    print(f"Registry declares {len(MODELS)} model(s) across {len(ROLES)} role(s).")
    print(f"Anthropic ids to check: {', '.join(registry_ids)}")

    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        # A green tick nobody is notified about is not disclosure. On the
        # scheduled run — the one that exists to catch a retirement between pull
        # requests — an unarmed check FAILS, so the gap has a forcing function
        # instead of sitting inert forever. On a pull request it only warns:
        # fork PRs can never see a repository secret, and failing every
        # contributor's CI for a secret they cannot supply would just get the
        # workflow deleted.
        unarmed_is_fatal = os.getenv("MODEL_LIVENESS_REQUIRE_KEY", "").strip() == "1"
        _annotate(
            "error" if unarmed_is_fatal else "warning",
            "ANTHROPIC_API_KEY is not set — model liveness was NOT checked. "
            "This job cannot catch a retired model id until that secret exists "
            "on the repository. See #782.",
        )
        return 1 if unarmed_is_fatal else 0

    try:
        live = fetch_live_ids(api_key)
    except urllib.error.HTTPError as exc:
        _annotate("error", f"GET /v1/models failed: HTTP {exc.code} {exc.reason}")
        return 2
    except Exception as exc:  # noqa: BLE001 — any transport failure is the same outcome
        _annotate("error", f"GET /v1/models could not be reached: {exc}")
        return 2

    print(f"Provider lists {len(live)} model(s).")

    dead = [model_id for model_id in registry_ids if not is_live(model_id, live)]

    if dead:
        for model_id in dead:
            roles = [r for r, (_, default) in ROLES.items() if default == model_id]
            _annotate(
                "error",
                f"Model {model_id!r} does not resolve against the Anthropic API. "
                f"Role(s) affected: {', '.join(roles) or 'none (unreferenced entry)'}. "
                "Repoint it in backend/services/model_registry.py.",
            )
        return 1

    print("All registry model ids resolve.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
