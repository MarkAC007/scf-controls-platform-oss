"""Every Anthropic client is built with a key resolved through `services.secrets`.

Issue #1000. `anthropic.Anthropic()` with no `api_key=` does not fail — the SDK
falls back to `os.environ["ANTHROPIC_API_KEY"]`. So an omitted argument is
completely invisible on any machine that sets that variable, and breaks only on
an install whose key lives in the `integration_secrets` table. Two call sites
had shipped that way: `vendor_assessment_engine` and `recipe_generation_engine`.

That failure mode dictates the shape of these tests, and it is worth being
explicit about what each part is for, because a test written the obvious way
here passes against the broken code:

* **The environment tier is emptied.** `ANTHROPIC_API_KEY` and its `_FILE`
  companion are deleted. With the variable set, the SDK's own fallback supplies
  it and broken and fixed code are indistinguishable.

* **The database tier supplies a sentinel.** Merely unsetting the environment
  proves nothing either: with no key at *any* tier both versions fail, just in
  different words. The sentinel has to arrive somewhere only `get_secret` can
  reach it from, which is the registered database provider.

* **The assertion is on the `api_key` kwarg the SDK actually received.** Not on
  the source text, not on a log line, not on whether the call raised.

The patch point is the `Anthropic` attribute on the real `anthropic` module.
Every call site resolves it at call time — the two engines via
`anthropic.Anthropic`, the rest via a function-local `from anthropic import
Anthropic` — so one `setattr` intercepts all six both before and after the fix.
That equivalence is what makes the pre-fix negative control meaningful.
"""
import contextlib
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anthropic  # noqa: E402

from services import secrets  # noqa: E402

SENTINEL = "sk-ant-db-tier-sentinel-1000"


class _Spy:
    """Records the kwargs of every `Anthropic(...)` construction."""

    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append(kwargs)
        # A permissive stand-in: the call sites go on to use
        # `client.messages.create` / `.stream`, and we do not care what happens
        # after construction. The assertions read `self.calls`.
        import unittest.mock

        return unittest.mock.MagicMock()


@pytest.fixture
def db_tier_key(monkeypatch):
    """Key available ONLY from the database tier.

    Yields the spy that every call site's `Anthropic(...)` construction lands in.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY_FILE", raising=False)

    secrets.register_db_provider(lambda: {"ANTHROPIC_API_KEY": SENTINEL})
    secrets.reset_caches()

    spy = _Spy()
    monkeypatch.setattr(anthropic, "Anthropic", spy)
    try:
        yield spy
    finally:
        secrets.register_db_provider(None)
        secrets.reset_caches()


def _assert_key_reached_sdk(spy, *, expected_timeout=...):
    """Exactly one client was built, and it carries the database-tier key."""
    assert len(spy.calls) == 1, (
        f"expected exactly one Anthropic(...) construction, got {len(spy.calls)}. "
        "Zero means the call site returned before constructing a client — the "
        "test proved nothing."
    )
    kwargs = spy.calls[0]
    assert kwargs.get("api_key") == SENTINEL, (
        f"client was built with api_key={kwargs.get('api_key')!r}; the "
        f"database-tier key {SENTINEL!r} never reached the SDK. With no "
        "api_key= the SDK falls back to the environment, which is issue #1000."
    )
    if expected_timeout is not ...:
        assert kwargs.get("timeout") == expected_timeout, (
            f"timeout changed: expected {expected_timeout}, got {kwargs.get('timeout')}"
        )


# ---------------------------------------------------------------------------
# The two sites that shipped broken (#1000)
# ---------------------------------------------------------------------------

def test_vendor_assessment_engine_passes_the_db_tier_key(db_tier_key):
    from services import vendor_assessment_engine

    with contextlib.suppress(Exception):
        vendor_assessment_engine._call_anthropic_for_report("prompt", "model-x", [])

    _assert_key_reached_sdk(db_tier_key, expected_timeout=540.0)


def test_recipe_generation_engine_passes_the_db_tier_key(db_tier_key):
    from services import recipe_generation_engine

    with contextlib.suppress(Exception):
        recipe_generation_engine._call_anthropic_for_recipes("prompt", "model-x", [])

    _assert_key_reached_sdk(db_tier_key, expected_timeout=540.0)


# ---------------------------------------------------------------------------
# The four that were already correct — pinned so they stay that way
# ---------------------------------------------------------------------------

def test_window_assessment_service_passes_the_db_tier_key(db_tier_key):
    from services import window_assessment_service

    with contextlib.suppress(Exception):
        window_assessment_service._call_llm("system", "user")

    _assert_key_reached_sdk(db_tier_key)


def test_artifact_type_extraction_service_passes_the_db_tier_key(db_tier_key):
    from services import artifact_type_extraction_service

    with contextlib.suppress(Exception):
        artifact_type_extraction_service._call_llm("system", "user")

    _assert_key_reached_sdk(db_tier_key)


def test_tasks_assessment_passes_the_db_tier_key(db_tier_key):
    import tasks_assessment

    with contextlib.suppress(Exception):
        tasks_assessment._call_llm("system", "user")

    _assert_key_reached_sdk(db_tier_key)


def test_doc_gen_tier2_passes_the_db_tier_key(db_tier_key, monkeypatch):
    """Tier 2 document generation.

    Prompt assembly is stubbed out: `build_user_prompt` loads a template from
    disk and walks a full `OrganisationContext`/`DomainWithControls` pair, none
    of which this test is about. Neither stub is on the path under test — the
    `build_anthropic_client(timeout=...)` call executes for real, which is the
    whole point.

    `is_mock_mode()` is NOT stubbed, deliberately. It reads the same
    `get_secret("ANTHROPIC_API_KEY")`, so the database-tier sentinel is what
    takes this function off the mock path and into the live branch. If the
    database tier were not wired up, generation would return mock content and
    never construct a client — which `_assert_key_reached_sdk` reports as zero
    constructions rather than passing silently.
    """
    from services.doc_gen import tier2

    monkeypatch.setattr(tier2, "build_user_prompt", lambda *a, **k: "user prompt")
    monkeypatch.setattr(tier2, "resolve_model", lambda *a, **k: "model-x")

    with contextlib.suppress(Exception):
        tier2.generate_document(object(), object(), object())

    _assert_key_reached_sdk(db_tier_key, expected_timeout=tier2.MODEL_CALL_TIMEOUT_SECONDS)


# ---------------------------------------------------------------------------
# Class sweep: the assertion above is per-site, so pin the site list itself
# ---------------------------------------------------------------------------

def test_no_call_site_constructs_the_sdk_client_directly():
    """`services/llm_client.py` is the only place that names the constructor.

    The six tests above each pin one known site. This pins the *set*: a seventh
    call site added tomorrow with a bare `anthropic.Anthropic()` reintroduces
    #1000 and no per-site test would notice.
    """
    backend = Path(__file__).resolve().parents[1]
    offenders = []
    for path in backend.rglob("*.py"):
        rel = path.relative_to(backend)
        if rel.parts[0] in {"tests", "alembic", ".venv"}:
            continue
        if rel == Path("services/llm_client.py"):
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("*"):
                continue
            if "Anthropic(" in line and "AsyncAnthropic(" not in line:
                offenders.append(f"{rel}:{lineno}: {stripped}")

    assert not offenders, (
        "these construct the Anthropic SDK client outside services/llm_client.py, "
        "so nothing guarantees they pass api_key=:\n  " + "\n  ".join(offenders)
    )
