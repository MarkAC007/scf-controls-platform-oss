"""The one constructor for the Anthropic client.

Every call site that talks to the Anthropic API builds its client here, so that
the key reaches the SDK by exactly one route: `services.secrets.get_secret`,
which resolves database -> `{NAME}_FILE` -> environment on every call.

Why a helper that *builds the client* rather than a helper that returns a key:
`anthropic.Anthropic()` with no `api_key=` does not fail. The SDK silently falls
back to `os.environ["ANTHROPIC_API_KEY"]`, so an omitted argument is invisible
wherever that variable happens to be set — which is every developer machine and
every install that configures the key in the environment. It breaks only on an
install whose key lives in the database, i.e. exactly the installs nobody tests
on. That was issue #1000: `vendor_assessment_engine` and
`recipe_generation_engine` both constructed `anthropic.Anthropic(timeout=540.0)`
and neither vendor assessment nor recipe generation could run against a
database-supplied key. A convention ("remember to pass api_key=") has no failing
state when you forget it; a constructor that always passes it cannot be
forgotten.

This lives here rather than in `services.secrets` on purpose. `secrets.py` is
the accessor for *every* credential the backend reads and has no vendor SDK
imports at all; making it import `anthropic` would put one vendor's client
library behind every credential lookup in the platform, including those on the
API process that never call an LLM. The dependency runs one way — this module
imports `secrets`, never the reverse.

The `anthropic` import stays inside the function for the same reason the call
sites had it inside theirs: the SDK is needed in the Celery worker, not on the
API process, and paying the import at module scope would load it everywhere
`services` is touched.
"""
import logging
from typing import TYPE_CHECKING, Any

from services.secrets import get_secret

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from anthropic import Anthropic

logger = logging.getLogger(__name__)

# The environment-variable NAME the key is read under (the same shape other
# modules use for this: `GLOBAL_MODEL_ENV`, `BOOTSTRAP_ENV`, `crypto._KEY_NAME`).
#
# The identifier is load-bearing. CodeQL's sensitive-data heuristic classifies
# a value by the NAME of the variable holding it, and this value flows into
# `services.secrets._file_value`'s warning log. Under the name SECRET_NAME it
# was reported as py/clear-text-logging-sensitive-data (high) on the public
# repo and blocked the v0.35.0 OSS release PR — although the value is an
# env-var name, never a credential. The matching families are: secret /
# trusted / confidential; pass(wd|word|code|phrase), auth.?key, oauth,
# api.?(key|tok), mfa; cert; account, user.?(name|id), session.?(id|key).
# tests/test_codeql_sensitive_names.py fails the suite if any identifier
# passed into the secret accessors matches one of them.
KEY_ENV = "ANTHROPIC_API_KEY"


def get_anthropic_key() -> str:
    """The Anthropic key, resolved per call.

    Raises `KeyError("ANTHROPIC_API_KEY")` when no tier supplies one. That shape
    is inherited from `doc_gen.tier2._anthropic_key`, which this replaces, and
    is what `doc_gen` already handles; call sites that would rather degrade than
    raise keep their own `if not get_secret(...)` guard and never reach here.
    """
    key = get_secret(KEY_ENV)
    if not key:
        raise KeyError(KEY_ENV)
    return key


def build_anthropic_client(**kwargs: Any) -> "Anthropic":
    """Construct an `anthropic.Anthropic` with the resolved key.

    `**kwargs` is passed through untouched so each caller keeps its own timeout
    (540s in the two research engines, `MODEL_CALL_TIMEOUT_SECONDS` in doc_gen,
    the SDK default in the three assessment callers). Nothing about the model,
    retries, or base URL is decided here — this helper owns the credential and
    nothing else.
    """
    from anthropic import Anthropic

    return Anthropic(api_key=get_anthropic_key(), **kwargs)
