"""
Three-layer content model engine -- the asset this whole integration exists for.

Python port of ``scf-doc-gen`` ``src/meta/three-layer.ts``.

    Generated (immutable snapshots) + Human (section edits) = Merged (operative document)

Without this, regenerating a policy after a control changes destroys every
human edit made to it, and the feature is a one-shot draft generator rather
than a living ISMS. With it, a document can be regenerated indefinitely and
only the sections where *both* layers moved need a human decision.

Decision matrix, per section:

    | Generated changed | Human edited | Status            | Content kept |
    |-------------------|--------------|-------------------|--------------|
    | no                | no           | unchanged         | generated    |
    | no                | yes          | human_preserved   | human        |
    | yes               | no           | updated           | generated    |
    | yes               | yes          | conflict          | human        |

Plus two structural outcomes:
    - section absent from the previous run          -> ``new``
    - section present before, absent now            -> ``pending_retirement``

``pending_retirement`` never deletes. A policy clause silently disappearing
because a control left scope is an audit finding, so the section is preserved,
marked, and left for a human to retire deliberately.

On conflict the **human** text is the one that survives into the merged
document. The generated alternative is not discarded -- it lives in the
immutable ``document_versions`` snapshot, which is what the section-diff UI
reads to show both sides.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .section_parser import (
    ParsedSection,
    flatten_sections,
    parse_markdown_sections,
    split_preamble,
    to_section_rows,
)

# ---------------------------------------------------------------------------
# Section statuses
# ---------------------------------------------------------------------------

STATUS_UNCHANGED = "unchanged"
STATUS_UPDATED = "updated"
STATUS_HUMAN_PRESERVED = "human_preserved"
STATUS_CONFLICT = "conflict"
STATUS_NEW = "new"
STATUS_PENDING_RETIREMENT = "pending_retirement"

SECTION_STATUSES = (
    STATUS_UNCHANGED,
    STATUS_UPDATED,
    STATUS_HUMAN_PRESERVED,
    STATUS_CONFLICT,
    STATUS_NEW,
    STATUS_PENDING_RETIREMENT,
)

# Inline markers written into the merged markdown. They are HTML comments so
# they render as nothing in the preview and in exported PDF, but survive a
# round-trip through the editor as plain text.
CONFLICT_MARKER = (
    "<!-- CONFLICT: regenerated from updated controls. Your edit was kept. "
    "The generated alternative is in this document's version history. -->"
)
NEW_SECTION_MARKER = "<!-- NEW: section added from newly scoped controls. -->"
PENDING_RETIREMENT_MARKER = (
    "<!-- PENDING RETIREMENT: the controls behind this section left scope. "
    "Nothing has been deleted -- review and retire deliberately. -->"
)

_MARKERS = (CONFLICT_MARKER, NEW_SECTION_MARKER, PENDING_RETIREMENT_MARKER)

#: Matches any marker comment by its leading keyword rather than by its exact
#: text. Exact-string matching alone is not enough: a human editing a
#: conflicted section can reflow the comment, and the marker wording changes
#: between releases. Either way the stale text would survive into an exported
#: PDF handed to an auditor, which is the one place a merge annotation must
#: never appear.
_MARKER_RE = re.compile(
    r"<!--\s*(?:CONFLICT|NEW|PENDING\s+RETIREMENT)\b.*?-->",
    re.IGNORECASE | re.DOTALL,
)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class ManifestEntry:
    """One line of the change manifest shown in the regeneration review UI."""

    section_id: str
    status: str
    heading_text: str
    control_ids: List[str] = field(default_factory=list)
    detail: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "section_id": self.section_id,
            "status": self.status,
            "heading_text": self.heading_text,
            "control_ids": list(self.control_ids),
            "detail": self.detail,
        }


@dataclass
class MergeResult:
    """Everything a caller needs to persist one regeneration."""

    merged_content: str
    manifest: List[ManifestEntry] = field(default_factory=list)
    sections: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def counts(self) -> Dict[str, int]:
        """Status tallies, for the review banner ("7 of your edits kept")."""
        out = {status: 0 for status in SECTION_STATUSES}
        for entry in self.manifest:
            out[entry.status] = out.get(entry.status, 0) + 1
        return out

    @property
    def conflict_count(self) -> int:
        return self.counts.get(STATUS_CONFLICT, 0)


@dataclass
class _MergedSection:
    section_id: str
    heading_text: str
    heading_level: int
    content: str
    control_ids: List[str]
    status: str
    marker: Optional[str] = None


# ---------------------------------------------------------------------------
# Human-edit detection
# ---------------------------------------------------------------------------


def strip_markers(content: str) -> str:
    """Remove merge markers from section content.

    A human editing a conflicted section usually leaves the marker comment in
    place. Without stripping it, the marker becomes part of the content hash
    and the section reads as edited forever.
    """
    text = content or ""
    for marker in _MARKERS:
        text = text.replace(marker, "")
    # Catch reflowed or older-wording markers the literal pass missed.
    text = _MARKER_RE.sub("", text)
    return text.strip()


def detect_human_edits(
    merged_content: str,
    stored_sections: Sequence[Dict[str, Any]],
) -> Dict[str, str]:
    """Find sections whose current content diverges from what was generated.

    Compares the live merged document against each section's
    ``last_generated_hash``. Returns ``{section_id: edited_content}``.

    Used when a document was edited outside the per-section save path (a bulk
    paste, an import). The normal editing flow records edits explicitly, so
    this is a reconciliation pass, not the primary mechanism.
    """
    if not stored_sections:
        return {}

    current = {
        section.section_id: section
        for section in flatten_sections(parse_markdown_sections(merged_content))
    }

    edits: Dict[str, str] = {}
    for stored in stored_sections:
        section_id = stored.get("section_id")
        live = current.get(section_id)
        if live is None:
            continue
        if live.content_hash != stored.get("last_generated_hash"):
            edits[section_id] = live.content

    return edits


def collect_human_edits(stored_sections: Sequence[Dict[str, Any]]) -> Dict[str, str]:
    """Read the human layer out of stored ``document_sections`` rows."""
    return {
        row["section_id"]: row["edited_content"]
        for row in stored_sections
        if row.get("human_edited") and row.get("edited_content") is not None
    }


# ---------------------------------------------------------------------------
# Document reconstruction
# ---------------------------------------------------------------------------


def _render(preamble: str, sections: Sequence[_MergedSection]) -> str:
    parts: List[str] = []
    if preamble.rstrip():
        parts.append(preamble.rstrip())
        parts.append("")

    for section in sections:
        parts.append("#" * section.heading_level + " " + section.heading_text)
        parts.append("")
        if section.marker:
            parts.append(section.marker)
            parts.append("")
        if section.content:
            parts.append(section.content)
        parts.append("")

    return "\n".join(parts)


def build_merged_document(generated_content: str, human_edits: Dict[str, str]) -> str:
    """Apply human edits to generated content, section by section.

    Headings always come from the new generation; only section *bodies* are
    substituted. That keeps renumbering and heading rewording under the
    generator's control while the human owns the prose.
    """
    if not human_edits:
        return generated_content

    flat = flatten_sections(parse_markdown_sections(generated_content))
    if not any(section.section_id in human_edits for section in flat):
        return generated_content

    merged = [
        _MergedSection(
            section_id=section.section_id,
            heading_text=section.heading_text,
            heading_level=section.heading_level,
            content=human_edits.get(section.section_id, section.content),
            control_ids=section.control_ids,
            status=STATUS_HUMAN_PRESERVED
            if section.section_id in human_edits
            else STATUS_UNCHANGED,
        )
        for section in flat
    ]
    return _render(split_preamble(generated_content), merged)


# ---------------------------------------------------------------------------
# The three-way merge
# ---------------------------------------------------------------------------


def three_way_merge(
    new_generated_content: str,
    existing_merged_content: Optional[str],
    existing_sections: Optional[Sequence[Dict[str, Any]]],
    human_edits: Optional[Dict[str, str]] = None,
) -> MergeResult:
    """Merge a fresh generation against the stored human layer.

    Args:
        new_generated_content: Markdown just produced by the generator.
        existing_merged_content: The operative document before this run, or
            ``None`` on first generation.
        existing_sections: Stored ``document_sections`` rows, or ``None``.
        human_edits: ``{section_id: content}``. Defaults to the edits recorded
            on ``existing_sections``.

    Returns:
        A :class:`MergeResult` carrying the new operative document, a
        per-section manifest, and section rows ready to persist.
    """
    human_edits = (
        human_edits
        if human_edits is not None
        else collect_human_edits(existing_sections or [])
    )

    new_tree = parse_markdown_sections(new_generated_content)
    new_flat = flatten_sections(new_tree)

    # --- First generation: nothing to merge against -------------------------
    if not existing_sections or not existing_merged_content:
        return MergeResult(
            merged_content=new_generated_content,
            manifest=[
                ManifestEntry(
                    section_id=section.section_id,
                    status=STATUS_NEW,
                    heading_text=section.heading_text,
                    control_ids=section.control_ids,
                )
                for section in new_flat
            ],
            sections=to_section_rows(new_tree, status=STATUS_NEW),
        )

    existing_by_id = {row["section_id"]: row for row in existing_sections}
    existing_content_by_id: Dict[str, ParsedSection] = {
        section.section_id: section
        for section in flatten_sections(parse_markdown_sections(existing_merged_content))
    }
    new_generated_hash_by_id = {
        section.section_id: section.content_hash for section in new_flat
    }

    merged: List[_MergedSection] = []
    manifest: List[ManifestEntry] = []
    seen: set[str] = set()

    for section in new_flat:
        seen.add(section.section_id)
        prior = existing_by_id.get(section.section_id)

        if prior is None:
            merged.append(
                _MergedSection(
                    section_id=section.section_id,
                    heading_text=section.heading_text,
                    heading_level=section.heading_level,
                    content=section.content,
                    control_ids=section.control_ids,
                    status=STATUS_NEW,
                    marker=NEW_SECTION_MARKER,
                )
            )
            manifest.append(
                ManifestEntry(
                    section_id=section.section_id,
                    status=STATUS_NEW,
                    heading_text=section.heading_text,
                    control_ids=section.control_ids,
                    detail="New section from newly scoped controls",
                )
            )
            continue

        generated_changed = section.content_hash != prior.get("last_generated_hash")
        edited_content = human_edits.get(section.section_id)
        has_human_edit = edited_content is not None

        if not generated_changed and not has_human_edit:
            status, content, marker, detail = (
                STATUS_UNCHANGED,
                section.content,
                None,
                None,
            )
        elif not generated_changed and has_human_edit:
            status, content, marker, detail = (
                STATUS_HUMAN_PRESERVED,
                edited_content,
                None,
                "Your edit kept; generated content did not change",
            )
        elif generated_changed and not has_human_edit:
            status, content, marker, detail = (
                STATUS_UPDATED,
                section.content,
                None,
                "Accepted the regenerated content",
            )
        else:
            status, content, marker, detail = (
                STATUS_CONFLICT,
                edited_content,
                CONFLICT_MARKER,
                "Both the generated content and your edit changed -- your "
                "edit was kept, review required",
            )

        merged.append(
            _MergedSection(
                section_id=section.section_id,
                heading_text=section.heading_text,
                heading_level=section.heading_level,
                content=content,
                control_ids=section.control_ids,
                status=status,
                marker=marker,
            )
        )
        manifest.append(
            ManifestEntry(
                section_id=section.section_id,
                status=status,
                heading_text=section.heading_text,
                control_ids=section.control_ids,
                detail=detail,
            )
        )

    # --- Sections that fell out of the new generation ------------------------
    for row in existing_sections:
        section_id = row["section_id"]
        if section_id in seen:
            continue
        prior_content = existing_content_by_id.get(section_id)
        if prior_content is None:
            continue
        merged.append(
            _MergedSection(
                section_id=section_id,
                heading_text=prior_content.heading_text,
                heading_level=prior_content.heading_level,
                content=prior_content.content,
                control_ids=prior_content.control_ids,
                status=STATUS_PENDING_RETIREMENT,
                marker=PENDING_RETIREMENT_MARKER,
            )
        )
        manifest.append(
            ManifestEntry(
                section_id=section_id,
                status=STATUS_PENDING_RETIREMENT,
                heading_text=row.get("heading_text") or prior_content.heading_text,
                control_ids=row.get("control_ids") or [],
                detail="Controls left scope -- section preserved pending review",
            )
        )

    merged_content = _render(split_preamble(new_generated_content), merged)

    # --- Rebuild section rows from the merged document -----------------------
    # Re-parsing (rather than mapping the in-memory list) keeps content hashes
    # honest: they must describe what is actually in the operative document,
    # markers included, or the next run's edit detection misfires.
    rows = to_section_rows(parse_markdown_sections(merged_content))
    status_by_id = {section.section_id: section.status for section in merged}

    for row in rows:
        section_id = row["section_id"]
        status = status_by_id.get(section_id, STATUS_UNCHANGED)
        row["status"] = status
        generated_hash = new_generated_hash_by_id.get(section_id)
        if generated_hash:
            row["last_generated_hash"] = generated_hash
        if status in (STATUS_HUMAN_PRESERVED, STATUS_CONFLICT):
            row["human_edited"] = True
            row["edited_content"] = human_edits.get(section_id)
        elif status == STATUS_PENDING_RETIREMENT:
            prior = existing_by_id.get(section_id, {})
            row["human_edited"] = bool(prior.get("human_edited"))
            row["edited_content"] = prior.get("edited_content")
            row["last_generated_hash"] = prior.get("last_generated_hash") or row["last_generated_hash"]

    return MergeResult(merged_content=merged_content, manifest=manifest, sections=rows)


def resolve_section(
    row: Dict[str, Any],
    choice: str,
    generated_content: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve one conflicted section.

    Args:
        row: The stored ``document_sections`` row.
        choice: ``"keep_mine"``, ``"take_generated"``, or ``"retire"``.
        generated_content: Required for ``"take_generated"`` -- the section body
            from the immutable version snapshot.

    Returns:
        The mutated row. Resolution always clears the conflict status; a
        section cannot remain a conflict after someone has decided.
    """
    if choice == "keep_mine":
        row["status"] = STATUS_HUMAN_PRESERVED
        row["human_edited"] = True
    elif choice == "take_generated":
        if generated_content is None:
            raise ValueError("take_generated requires the generated section content")
        row["status"] = STATUS_UPDATED
        row["human_edited"] = False
        row["edited_content"] = None
        row["content_hash"] = row["last_generated_hash"]
    elif choice == "retire":
        row["status"] = STATUS_PENDING_RETIREMENT
    else:
        raise ValueError(f"Unknown resolution choice: {choice!r}")
    return row
