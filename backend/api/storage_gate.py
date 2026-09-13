"""One refusal for "this organisation has no evidence store".

Phase 7, ISC 53. Before this module the same condition was answered three
different ways: a 400 with a bare string from the presign route, a 503 with a
bare string from the download route, and an unhandled ``ValueError`` from the
inbox writer that surfaced as a 500. None of the three named the screen that
fixes it, so the only way to learn what to do was to read the source.

**Why 409 and not 503.** 503 is a statement about the *platform*: load
balancers take a service out of rotation on it, uptime monitors page on it, and
its contract invites a retry. None of that is true here — the platform is
healthy, every other feature of the organisation works, and no amount of
retrying will produce an object store. The condition is a property of *this
organisation's* configuration, and the caller (or their administrator) is the
one who can clear it. 409 Conflict is the status this surface already uses for
"the resource is in a state that conflicts with what you asked" — the
activation route answers 409 for an undecryptable secret and for a copy already
in flight — and Phase 5 already taught the client to render a 409 whose detail
carries ``message`` (ISC 45).

The platform-wide statement about storage lives on ``GET /health``
(``evidence_storage`` component, ISC 54), which is where a monitor should look.
"""
from __future__ import annotations

from fastapi import HTTPException, status

#: The screen an organisation administrator goes to. Named in the message
#: rather than linked, because the API has no knowledge of the client's routing
#: and a stale path is worse than a name.
EVIDENCE_STORAGE_SCREEN = "Settings → Evidence storage"

#: Machine-readable discriminator, so a client can branch without matching prose.
EVIDENCE_STORAGE_NOT_CONFIGURED = "evidence_storage_not_configured"

STORAGE_NOT_CONFIGURED_MESSAGE = (
    "No evidence store is configured for this organisation, so evidence files "
    f"cannot be uploaded or read. An organisation administrator sets one up "
    f"under {EVIDENCE_STORAGE_SCREEN}."
)


def storage_not_configured() -> HTTPException:
    """The single refusal every unconfigured-storage path raises.

    Returned rather than raised so the call site reads ``raise
    storage_not_configured()`` and keeps its own traceback.
    """
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "error": EVIDENCE_STORAGE_NOT_CONFIGURED,
            "message": STORAGE_NOT_CONFIGURED_MESSAGE,
        },
    )
