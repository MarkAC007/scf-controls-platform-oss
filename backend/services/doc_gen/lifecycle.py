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

import re
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

#: How each status is written for a human reader. The stored values are
#: snake_case machine states; "in_review" in a Document Control table read by an
#: auditor is a defect, not a status.
STATUS_LABELS: Dict[str, str] = {
    STATUS_DRAFT: "Draft",
    STATUS_IN_REVIEW: "In Review",
    STATUS_APPROVED: "Approved",
    STATUS_PUBLISHED: "Published",
}


def status_label(status: Optional[str]) -> str:
    """The reader-facing name for a lifecycle status.

    Falls back to title-casing an unknown value rather than raising: this is
    called on the export path, and a status the map has not caught up with must
    degrade to something readable rather than take the document down.
    """
    if not status:
        return ""
    return STATUS_LABELS.get(status) or status.replace("_", " ").title()


#: The ``Document Control`` heading, at any depth, numbered or not.
_DOC_CONTROL_HEADING_RE = re.compile(
    r"^#{1,6}\s*(?:\d+[.)]\s*)?document\s+control\s*$",
    re.IGNORECASE,
)

#: A table row whose *first* cell is "Status", with optional bold markers.
#: Anchoring to the first cell is deliberate: the Statement of Applicability and
#: the Control Status Report both carry a per-control "Status" column, and a
#: pattern matching the word anywhere in a row would rewrite a hundred control
#: rows into nonsense. Scoping to the Document Control block is the primary
#: guard; this is the second.
_STATUS_ROW_RE = re.compile(
    r"^(\|\s*\*{0,2}\s*Status\s*\*{0,2}\s*\|)([^|]*)(\|\s*)$",
    re.IGNORECASE,
)

_ANY_HEADING_RE = re.compile(r"^#{1,6}\s")


def apply_lifecycle_status(markdown: str, status: Optional[str]) -> str:
    """Write ``status`` into the document's own Document Control table.

    The Document Control block is what an auditor reads to decide whether a
    policy is in force, and until now it said whatever the generator wrote at
    generation time -- always the literal "Draft", frozen there even after the
    document had been approved and published. The platform record and the
    document's own front matter disagreed, and the document was the one people
    read.

    Regenerating to fix that would mean an LLM bill and a rewrite of prose
    somebody has already approved, so instead the single cell that encodes the
    claim is rewritten in place. This is applied at generation *and* on every
    lifecycle transition, so the two cannot drift apart again.

    **Why the prompt is not templated instead.** Interpolating the status into
    the generator prompt would move ``prompt_hash``, and with it every
    document's staleness verdict, on every approval. The prompt stays fixed and
    this owns the value.

    Scope rules, in order:

    1. Only inside the ``Document Control`` block, which ends at the next
       heading of any level. Tier 1 documents (Statement of Applicability,
       Control Status Report, Evidence Schedule) have no such block, so this is
       a no-op for them.
    2. Only a row whose first cell is "Status".

    A human who edited the Document Control section keeps that edit -- only the
    one cell moves. A human who deleted the Status row keeps it deleted: this
    never re-adds a row, because the absence is a choice the document's owner
    made, and silently reversing it would be the same overreach the generator
    has just stopped committing.

    Returns the markdown unchanged when there is nothing to rewrite, so callers
    may apply it unconditionally.
    """
    label = status_label(status)
    if not markdown or not label:
        return markdown

    lines = markdown.split("\n")
    start = next(
        (i for i, line in enumerate(lines)
         if _DOC_CONTROL_HEADING_RE.match(line.strip())),
        None,
    )
    if start is None:
        return markdown

    for i in range(start + 1, len(lines)):
        if _ANY_HEADING_RE.match(lines[i]):
            break  # left the Document Control block without finding the row
        match = _STATUS_ROW_RE.match(lines[i].strip())
        if match:
            lines[i] = f"{match.group(1)} {label} {match.group(3).strip()}"
            return "\n".join(lines)

    return markdown


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
