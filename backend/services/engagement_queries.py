"""Status lifecycle for engagement auditor queries.

Increment 4 of the framework-native audit-engagement design
(docs/superpowers/specs/2026-08-19-audit-engagements-design.md).

Pure lifecycle helpers, kept free of DB access so the transition rules are
unit-testable in isolation. See models.EngagementQueryStatus for the states.
"""
from __future__ import annotations

OPEN = "open"
ANSWERED = "answered"
CLOSED = "closed"

# Manual (PATCH-driven) transitions the API permits.
_ALLOWED_TRANSITIONS = {
    OPEN: {ANSWERED, CLOSED},
    ANSWERED: {OPEN, CLOSED},
    CLOSED: {OPEN},  # a closed query can only be reopened
}


def status_after_response(current: str) -> str:
    """New status once a response is posted.

    A response answers an open/answered query; a closed query stays closed until
    it is explicitly reopened.
    """
    if current == CLOSED:
        return CLOSED
    return ANSWERED


def is_valid_query_transition(current: str, new: str) -> bool:
    """Whether a manual status change from ``current`` to ``new`` is allowed."""
    return new in _ALLOWED_TRANSITIONS.get(current, set())
