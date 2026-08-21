"""
Document lifecycle state machine.

Python port of ``scf-doc-gen`` ``src/meta/lifecycle.ts``, with the RBAC layer
the standalone tool had no concept of.

    draft -> in_review -> approved -> published
               ^   |         ^  |         |
               |   v         |  v         v
               +---+         +--+---------+

Every transition is validated here, recorded in ``document_transitions``
(append-only) and mirrored into ``AuditLog``. A document's status is never set
by direct assignment anywhere in the codebase -- ``validate_transition`` is the
only door, so an invalid state cannot be reached by a caller who forgot the
rules.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

STATUS_DRAFT = "draft"
STATUS_IN_REVIEW = "in_review"
STATUS_APPROVED = "approved"
STATUS_PUBLISHED = "published"

LIFECYCLE_STATUSES = (STATUS_DRAFT, STATUS_IN_REVIEW, STATUS_APPROVED, STATUS_PUBLISHED)

#: The complete transition map. Anything not listed here is refused.
VALID_TRANSITIONS: Dict[str, List[str]] = {
    STATUS_DRAFT: [STATUS_IN_REVIEW],
    STATUS_IN_REVIEW: [STATUS_APPROVED, STATUS_DRAFT],
    STATUS_APPROVED: [STATUS_PUBLISHED, STATUS_IN_REVIEW],
    STATUS_PUBLISHED: [STATUS_IN_REVIEW],
}

#: Minimum org role for each transition. Approving and publishing are admin
#: acts because they are the two that make a document externally citable.
TRANSITION_ROLES: Dict[Tuple[str, str], str] = {
    (STATUS_DRAFT, STATUS_IN_REVIEW): "editor",
    (STATUS_IN_REVIEW, STATUS_APPROVED): "admin",
    (STATUS_IN_REVIEW, STATUS_DRAFT): "editor",
    (STATUS_APPROVED, STATUS_PUBLISHED): "admin",
    (STATUS_APPROVED, STATUS_IN_REVIEW): "editor",
    (STATUS_PUBLISHED, STATUS_IN_REVIEW): "editor",
}

TRANSITION_LABELS: Dict[Tuple[str, str], str] = {
    (STATUS_DRAFT, STATUS_IN_REVIEW): "Submit for Review",
    (STATUS_IN_REVIEW, STATUS_APPROVED): "Approve Document",
    (STATUS_IN_REVIEW, STATUS_DRAFT): "Return to Draft",
    (STATUS_APPROVED, STATUS_PUBLISHED): "Publish",
    (STATUS_APPROVED, STATUS_IN_REVIEW): "Request Changes",
    (STATUS_PUBLISHED, STATUS_IN_REVIEW): "Request Re-review",
}

ROLE_RANK = {"viewer": 1, "editor": 2, "admin": 3}


class TransitionError(ValueError):
    """Raised when a lifecycle transition is not permitted."""


def can_transition(from_status: str, to_status: str) -> bool:
    """True when the state machine permits this move (ignoring role)."""
    return to_status in VALID_TRANSITIONS.get(from_status, [])


def available_transitions(from_status: str, role: str = "admin") -> List[Dict[str, str]]:
    """Transitions the given role may perform from ``from_status``.

    Shaped for direct use by the UI's action bar -- each entry carries the
    target status, its button label, and the role it needs, so the frontend
    never has to re-derive the rules.
    """
    rank = ROLE_RANK.get(role, 0)
    out: List[Dict[str, str]] = []
    for target in VALID_TRANSITIONS.get(from_status, []):
        required = TRANSITION_ROLES.get((from_status, target), "admin")
        if rank >= ROLE_RANK.get(required, 3):
            out.append(
                {
                    "to_status": target,
                    "label": transition_label(from_status, target),
                    "required_role": required,
                }
            )
    return out


def transition_label(from_status: str, to_status: str) -> str:
    """Human label for a transition, for buttons and audit entries."""
    return TRANSITION_LABELS.get((from_status, to_status), f"{from_status} to {to_status}")


def required_role(from_status: str, to_status: str) -> str:
    """Minimum org role for a transition. Unknown transitions demand admin."""
    return TRANSITION_ROLES.get((from_status, to_status), "admin")


def validate_transition(from_status: str, to_status: str, role: str) -> None:
    """Raise :class:`TransitionError` unless this move is permitted for ``role``.

    The only sanctioned way to change a document's lifecycle status.
    """
    if to_status not in LIFECYCLE_STATUSES:
        raise TransitionError(f"Unknown document status: {to_status!r}")
    if from_status == to_status:
        raise TransitionError(f"Document is already {to_status!r}")
    if not can_transition(from_status, to_status):
        allowed = ", ".join(VALID_TRANSITIONS.get(from_status, [])) or "none"
        raise TransitionError(
            f"Cannot move a document from {from_status!r} to {to_status!r}. "
            f"Allowed from {from_status!r}: {allowed}."
        )
    needed = required_role(from_status, to_status)
    if ROLE_RANK.get(role, 0) < ROLE_RANK.get(needed, 3):
        raise TransitionError(
            f"{transition_label(from_status, to_status)} requires the "
            f"{needed} role."
        )


def transition_on_edit(current_status: str) -> Optional[str]:
    """The status a document should fall back to when its content is edited.

    Editing an approved or published document invalidates the approval that
    was granted against different text. Returning it to ``in_review`` is the
    honest outcome; leaving it "approved" would let an edit launder itself
    through a stale sign-off.
    """
    if current_status in (STATUS_APPROVED, STATUS_PUBLISHED):
        return STATUS_IN_REVIEW
    return None
