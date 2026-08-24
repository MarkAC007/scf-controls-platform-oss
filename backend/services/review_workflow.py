"""Review-workflow rules: state transitions and segregation of duties.

Both rules are pure functions over plain values so they can be asserted
without a database, a request, or a Celery worker — the endpoint keeps the
I/O (loading the row, loading the uploader set) and this module keeps the
judgement.

Two separate concerns live here because they fail in the same place and
would otherwise be re-implemented per call site:

  * **Transitions** (D9) constrain which review states may follow which.
    They apply to every organization: an audit trail that records a
    rejection turning straight into an approval, with nothing in between,
    is not a record of a decision — it is a record of a missing one.

  * **Segregation of duties** (D8) stops a reviewer signing off evidence
    only they supplied. It is opt-in per organization
    (``require_reviewer_independence``) because for a one-person
    compliance team it is unsatisfiable, and a control nobody can pass is
    a control everybody disables.
"""
from __future__ import annotations

from typing import Iterable, Optional
from uuid import UUID

VALID_REVIEW_STATUSES = frozenset(
    {"approved", "rejected", "needs_revision", "not_reviewed"}
)

# D9. Same-state is always permitted (a re-PUT of the current status is
# idempotent, and callers retry). The edge this table exists to close is
# ``rejected -> approved`` in one step; the route through
# ``needs_revision`` is still open, it just has to be walked.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "not_reviewed": frozenset({"approved", "rejected", "needs_revision"}),
    "needs_revision": frozenset({"approved", "rejected", "needs_revision"}),
    "rejected": frozenset({"needs_revision", "not_reviewed"}),
    "approved": frozenset({"needs_revision", "rejected"}),
}


def transition_allowed(current: Optional[str], target: str) -> bool:
    """True if a review may move from ``current`` to ``target``.

    An unknown or missing current state is treated as ``not_reviewed`` —
    rows predating the review workflow have no status to reason from, and
    refusing every transition on them would strand them permanently.
    """
    if current == target:
        return True
    # One lookup handles both NULL (rows predating the workflow) and any
    # status the table does not know: both fall back to the fresh-row rules.
    allowed = ALLOWED_TRANSITIONS.get(current, ALLOWED_TRANSITIONS["not_reviewed"])
    return target in allowed


def transition_error(current: Optional[str], target: str) -> str:
    """Human-readable refusal naming the route that *is* open."""
    current = current or "not_reviewed"
    permitted = sorted(ALLOWED_TRANSITIONS.get(current, frozenset()))
    detail = (
        f"Cannot move a review from '{current}' to '{target}'. "
        f"From '{current}' the permitted next states are: "
        f"{', '.join(permitted) or 'none'}."
    )
    if current == "rejected" and target == "approved":
        detail += (
            " A rejection cannot become an approval in one step — send it back "
            "as 'needs_revision' so the re-assessment is on the record, then "
            "approve that."
        )
    return detail


def reviewer_is_sole_uploader(
    uploader_ids: Iterable[Optional[UUID]], reviewer_id: UUID
) -> bool:
    """True when every known uploader in the window is the reviewer.

    The window covers a set of files, so "the uploader" is a set. The rule
    is deliberately *sole* rather than *any*: if somebody else also put a
    file into this window, an independent pair of hands has touched the
    portfolio being signed off.

    Files whose uploader is unknown (``NULL`` after a user deletion) are
    ignored rather than counted as independent — an absent name cannot
    vouch for anything. An empty set is not a violation: there is nothing
    to be sole owner of.
    """
    known = {u for u in uploader_ids if u is not None}
    if not known:
        return False
    return known == {reviewer_id}


SOD_REFUSAL_DETAIL = (
    "Segregation of duties: you are the only person who supplied evidence in "
    "this window, so you cannot also review it. Ask another member of the "
    "organization to review it, or have an owner turn off "
    "'require_reviewer_independence' in the organization's assurance policy."
)
