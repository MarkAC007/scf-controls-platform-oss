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

from .fingerprint import sha256
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

    parsed = flatten_sections(parse_markdown_sections(merged_content))
    current = {section.section_id: section for section in parsed}
    # Same identity rule as the merge: a section retired on an earlier run
    # re-parses under whichever heading now precedes it, so its stored id is
    # absent from the map above and an out-of-band edit to it would go
    # undetected. Position pairs the two sequences, guarded on length for the
    # case where a pasted markdown heading makes the parser see extra sections.
    ordered = sorted(stored_sections, key=lambda r: r.get("ordinal") or 0)
    if len(ordered) == len(parsed):
        for row, section in zip(ordered, parsed):
            current[row["section_id"]] = section

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


def _body_lines(section: _MergedSection) -> List[str]:
    """The lines :func:`_render` emits *after* a section's heading.

    Split out so that :func:`_rendered_body` hashes exactly what was written.
    Two functions deriving the same text independently is how the merged
    document and its stored hashes drift apart; deriving both from here means
    they cannot.
    """
    lines = [""]
    if section.marker:
        lines.append(section.marker)
        lines.append("")
    if section.content:
        lines.append(section.content)
    lines.append("")
    return lines


def _rendered_body(section: _MergedSection) -> str:
    """What :func:`parse_markdown_sections` will read back as this section's body.

    The parser takes every line between one heading and the next and strips it
    (``section_parser.parse_markdown_sections``), so joining the same lines and
    stripping reproduces its answer without re-parsing the document.
    """
    return "\n".join(_body_lines(section)).strip()


def _render(preamble: str, sections: Sequence[_MergedSection]) -> str:
    parts: List[str] = []
    if preamble.rstrip():
        parts.append(preamble.rstrip())
        parts.append("")

    for section in sections:
        parts.append("#" * section.heading_level + " " + section.heading_text)
        parts.extend(_body_lines(section))

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

    # The stored rows -- not a re-parse -- are the identity system of record.
    # A section retired on an earlier run sits at the end of the document at
    # its original depth, so re-parsing renames it after whatever heading now
    # precedes it ("roles.scope" reads back as "review.scope"). Looking the
    # prior content up by that re-derived id misses, and the retirement branch
    # below then drops the section entirely -- the silent deletion
    # ``pending_retirement`` exists to prevent.
    #
    # Both sequences are in document order, so position pairs them. The
    # length check is the guard: a human edit containing a markdown heading
    # line makes the parser see more sections than there are rows, and a
    # mismatched pairing would hand every section its neighbour's identity.
    # In that case the id-keyed map alone stands, which is the previous
    # behaviour rather than a corrupted one.
    existing_parsed = flatten_sections(parse_markdown_sections(existing_merged_content))
    existing_content_by_id: Dict[str, ParsedSection] = {
        section.section_id: section for section in existing_parsed
    }
    ordered_rows = sorted(existing_sections, key=lambda r: r.get("ordinal") or 0)
    if len(ordered_rows) == len(existing_parsed):
        for row, parsed in zip(ordered_rows, existing_parsed):
            existing_content_by_id[row["section_id"]] = parsed
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
                # Stripped because this content already carries the marker from
                # the run that retired it, and ``marker=`` below writes another.
                # Left alone, a document regenerated five times accumulates five
                # copies of the same comment and its content hash never settles.
                content=strip_markers(prior_content.content),
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

    # --- Section rows, built from the merge itself ---------------------------
    # Identity belongs to the merge, not to the parser. Every entry in
    # ``merged`` already carries the ``section_id`` this section is stored
    # under -- taken from the new generation, or from the row that retired it.
    #
    # This block used to re-parse ``merged_content`` and re-derive each id from
    # the rendered tree. Section ids are content-and-position derived
    # (``section_parser.parse_markdown_sections``), and rendering is not
    # identity-preserving: a retired ``###`` is appended at the end of the
    # document at its original depth, so it re-parses under whichever ``##``
    # now precedes it. The lookup then missed, every retiree was written back
    # as ``unchanged`` with ``human_edited`` cleared, and the next run could no
    # longer find it at all.
    #
    # The re-parse existed to keep ``content_hash`` describing what is actually
    # in the operative document, markers included, so the next run's edit
    # detection does not misfire. :func:`_rendered_body` preserves that
    # property without the re-parse: it joins the very lines :func:`_render`
    # wrote and strips them, which is precisely what the parser reads back.
    rows: List[Dict[str, Any]] = []
    for ordinal, section in enumerate(merged):
        body_hash = sha256(_rendered_body(section))
        row: Dict[str, Any] = {
            "section_id": section.section_id,
            "heading_text": section.heading_text,
            "heading_level": section.heading_level,
            "ordinal": ordinal,
            "content_hash": body_hash,
            # Tracks the GENERATED layer, so it is the hash of the newly
            # generated section, not of what the merge chose to keep.
            "last_generated_hash": new_generated_hash_by_id.get(
                section.section_id, body_hash
            ),
            "human_edited": False,
            "edited_content": None,
            "status": section.status,
            "control_ids": list(section.control_ids),
        }
        if section.status in (STATUS_HUMAN_PRESERVED, STATUS_CONFLICT):
            row["human_edited"] = True
            row["edited_content"] = human_edits.get(section.section_id)
        elif section.status == STATUS_PENDING_RETIREMENT:
            # A retiring section has no new generated content, so its
            # generated-layer hash and human layer both carry forward.
            prior = existing_by_id.get(section.section_id, {})
            row["human_edited"] = bool(prior.get("human_edited"))
            row["edited_content"] = prior.get("edited_content")
            row["last_generated_hash"] = (
                prior.get("last_generated_hash") or row["last_generated_hash"]
            )
        rows.append(row)

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
