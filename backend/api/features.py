"""Runtime feature-flag reporting (#787, ISC-80).

The webclient compiles ``VITE_ENABLE_PER_WINDOW_REVIEW`` in at build time
while the backend reads ``ENABLE_PER_WINDOW_REVIEW`` from its environment at
request time. Nothing connected the two, so a deploy that set one and not
the other produced a UI and an API that disagreed about which review
workflow exists — and the failure was silent in the direction that matters:
the frontend showing per-file Approve buttons against a backend that
answers 410 Gone leaves a reviewer with no way to review anything.

This endpoint publishes what the backend actually believes, so the
divergence can be detected rather than discovered. It reports deployment
configuration, not org data: no organization scope, nothing tenant-specific,
and every value is already inferable from the API's own responses.
"""
import os

from fastapi import APIRouter

router = APIRouter(tags=["features"])


#: Deployment defaults for the assurance flags. All three default ON since the
#: window-assessment parity work (#569 follow-up): the windowed, record-level
#: assessment is the primary assessment surface and the per-file assessor is
#: the diagnostic layer beneath it. An operator turns one off by setting the
#: variable to ``false`` on BOTH the backend and celery-worker services.
FLAG_DEFAULTS = {
    "ENABLE_PER_WINDOW_REVIEW": "true",
    "ENABLE_WINDOW_ASSESSMENT_KSI": "true",
    "ENABLE_COMPOSITE_KSI": "true",
}


def flag_enabled(name: str) -> bool:
    """Read a deployment flag at call time.

    Unset and empty both mean "use the default"; anything other than the
    literal ``true`` (any case) is off. Shared by every reader of these flags
    so the API, the KSI dispatcher and the review cutover cannot drift.
    """
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        raw = FLAG_DEFAULTS.get(name, "false")
    return raw.strip().lower() == "true"


def _flag(name: str) -> bool:
    return flag_enabled(name)


@router.get(
    "/features",
    summary="Runtime feature flags as the backend sees them",
    description=(
        "Returns the backend's live view of the deployment feature flags. "
        "Clients that compile an equivalent build-time flag should compare "
        "against this and warn on mismatch."
    ),
)
async def get_features() -> dict:
    """Read the flags on every call — they are environment, not startup state.

    Reading at request time rather than caching at import means a container
    restarted with a changed value reports the new one immediately, and a
    test can set the variable without reloading the module.
    """
    return {
        "per_window_review": _flag("ENABLE_PER_WINDOW_REVIEW"),
        "window_assessment_ksi": _flag("ENABLE_WINDOW_ASSESSMENT_KSI"),
        "composite_ksi": _flag("ENABLE_COMPOSITE_KSI"),
    }
