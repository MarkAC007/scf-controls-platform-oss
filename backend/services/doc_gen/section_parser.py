"""
Markdown section parser for generated ISMS documents.

Python port of ``scf-doc-gen`` ``src/meta/section-parser.ts``.

Decomposes generated markdown into an addressable section tree with content
hashes, control-ID extraction and normalised section IDs. This is the
foundation the three-layer merge stands on: every ``section_id`` produced here
becomes a row in ``document_sections`` and the key a human edit is stored under.

**Known behaviour, preserved deliberately from the TypeScript original:**
two sections whose heading text normalises to the same string *at the same
level under the same parent* collide, and the later one wins in any
``section_id``-keyed map. Hierarchical IDs make this rare in practice
(``policy-statements.scope`` and ``roles.scope`` do not collide). Changing the
collision rule here would change every existing ``section_id`` and orphan every
stored human edit -- so it is documented rather than "fixed".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .fingerprint import sha256

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

#: Bracketed SCF control IDs, e.g. two-to-five uppercase letters, a hyphen,
#: digits, and an optional dotted sub-number.
_CONTROL_ID_RE = re.compile(r"\[([A-Z]{2,5}-\d+(?:\.\d+)?)\]")

#: An ATX heading line: one to six hashes, whitespace, then text.
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")

#: A fenced code block delimiter (backticks or tildes), allowing indentation.
_FENCE_RE = re.compile(r"^(`{3,}|~{3,})")

#: A ``**Generated:**`` stamp is a wall clock, not document substance. Every
#: Tier 1 generator opens with one (``tier1._header``), so hashing it made the
#: first section of every Tier 1 document report ``updated`` on every single
#: regeneration — a permanent false "the generator changed this" badge on the
#: one section nothing ever changes, which is exactly the signal the reviewer
#: needs to be able to trust. Verified against two consecutive forced SoA runs
#: whose only difference was this line.
#:
#: The rendered document is untouched: the auditor still sees the stamp. Only
#: change detection ignores it. The stamp is replaced rather than removed so
#: the line count is stable and nothing downstream shifts.
_VOLATILE_STAMP_RE = re.compile(r"^\*\*Generated:\*\*.*$", re.MULTILINE)


def hashable_body(body: str) -> str:
    """The part of ``body`` that change detection should compare.

    Two bodies differing only in their generated-at stamp collapse to the same
    string, so they hash equal and merge as ``unchanged``.
    """
    return _VOLATILE_STAMP_RE.sub("**Generated:**", body)


#: Leading section numbering: "1.", "4.1", "4.1.2 "
_LEADING_NUMBER_RE = re.compile(r"^\d+(?:\.\d+)*\.?\s*")

#: A trailing parenthetical whose content *starts with a digit*: "(12 controls)",
#: "(7)", "(3 items)". Deliberately narrow -- see :func:`normalise_section_id`
#: for why a qualifier like "(Policy)" or "(Draft)" must keep affecting the slug.
_TRAILING_COUNT_RE = re.compile(r"\(\s*\d+[^)]*\)\s*$")


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------


def extract_control_ids(content: str) -> List[str]:
    """Return the sorted, de-duplicated SCF control IDs referenced in ``content``.

    Only *bracketed* IDs count. Prose that happens to name a control without
    brackets is not a citation, and treating it as one would attach controls to
    sections that merely mention them in passing.
    """
    return sorted({m.group(1) for m in _CONTROL_ID_RE.finditer(content or "")})


def normalise_section_id(heading_text: str) -> str:
    """Normalise heading text into a section-ID component.

    Strips leading numbering, bold markers, trailing colons, bracketed SCF IDs
    and a trailing count parenthetical; lowercases; collapses everything
    non-alphanumeric to single hyphens.

    Numbering is stripped on purpose: renumbering a document (inserting a new
    section 3) must not orphan the human edits attached to what was section 4.

    **A trailing count parenthetical is stripped for the same reason, and it is
    the more damaging of the two.** Generated headings carry tallies -- the SoA
    writes ``### 3. GOV — Governance & Risk Management (12 controls)`` -- and a
    tally is a property of today's scope, not an identity. Left in the slug,
    scoping one more control renames the section: the old id has no counterpart
    in the new generation, so the three-way merge retires it and creates a
    fresh one, and every human edit attached to it is stranded on a ghost. One
    scope change did that to *every* domain section of a Statement of
    Applicability at once (40 sections became 71, 33 of them retired).

    The rule is narrow on purpose: only a parenthetical whose content begins
    with a digit is dropped. ``(12 controls)``, ``(7)`` and ``(3 items)`` are
    volatile counts; ``(Policy)``, ``(Draft)`` and ``(Annex A)`` are part of
    what the section *is* and must keep affecting the slug, because two
    sibling headings differing only by such a qualifier are genuinely two
    different sections. The count stays in the display heading -- only the
    identity loses it.

    Examples:
        "1. Document Control"          -> "document-control"
        "4.1 Access Management"        -> "access-management"
        "**Evidence Produced:**"       -> "evidence-produced"
        "3. GOV — Governance (12 controls)" -> "gov-governance"
        "Acceptable Use (Policy)"      -> "acceptable-use-policy"
    """
    text = heading_text or ""
    text = _LEADING_NUMBER_RE.sub("", text)
    text = text.replace("**", "")
    text = re.sub(r":+$", "", text)
    text = _CONTROL_ID_RE.sub("", text)
    text = _TRAILING_COUNT_RE.sub("", text)
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


# ---------------------------------------------------------------------------
# Parsed section tree
# ---------------------------------------------------------------------------


@dataclass
class ParsedSection:
    """One section of a parsed markdown document."""

    section_id: str
    heading_text: str
    heading_level: int
    content: str
    content_hash: str
    control_ids: List[str] = field(default_factory=list)
    children: List["ParsedSection"] = field(default_factory=list)


@dataclass
class _Heading:
    level: int
    text: str
    line_index: int


def _extract_headings(lines: List[str]) -> List[_Heading]:
    """Find heading lines, skipping anything inside a fenced code block.

    A shell comment inside a fence is not a heading; treating it as one would
    fabricate sections that vanish the moment the fence content changes.
    """
    headings: List[_Heading] = []
    in_fence = False

    for index, line in enumerate(lines):
        if _FENCE_RE.match(line.lstrip()):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _HEADING_RE.match(line)
        if match:
            headings.append(
                _Heading(level=len(match.group(1)), text=match.group(2).strip(), line_index=index)
            )

    return headings


def parse_markdown_sections(markdown: str) -> List[ParsedSection]:
    """Parse markdown into a tree of sections.

    The returned tree starts at the first heading level found -- typically the
    second level for generated documents, since the first is the document
    title. Content before the first heading (the preamble) belongs to no
    section and is preserved separately by the merge engine.
    """
    lines = (markdown or "").split("\n")
    headings = _extract_headings(lines)
    if not headings:
        return []

    root: List[ParsedSection] = []
    stack: List[ParsedSection] = []

    for i, heading in enumerate(headings):
        start = heading.line_index + 1
        end = headings[i + 1].line_index if i + 1 < len(headings) else len(lines)
        body = "\n".join(lines[start:end]).strip()

        # Pop back to the nearest ancestor with a strictly shallower level.
        while stack and stack[-1].heading_level >= heading.level:
            stack.pop()

        normalised = normalise_section_id(heading.text)
        parent_path = stack[-1].section_id if stack else ""
        section_id = f"{parent_path}.{normalised}" if parent_path else normalised

        section = ParsedSection(
            section_id=section_id,
            heading_text=heading.text,
            heading_level=heading.level,
            content=body,
            content_hash=sha256(hashable_body(body)),
            control_ids=extract_control_ids(f"{heading.text}\n{body}"),
        )

        if stack:
            stack[-1].children.append(section)
        else:
            root.append(section)

        stack.append(section)

    return root


def flatten_sections(sections: List[ParsedSection]) -> List[ParsedSection]:
    """Depth-first flatten of a parsed section tree."""
    out: List[ParsedSection] = []

    def walk(nodes: List[ParsedSection]) -> None:
        for node in nodes:
            out.append(node)
            walk(node.children)

    walk(sections)
    return out


@dataclass
class SectionPairing:
    """Which heading in a document each stored section row belongs to.

    ``heading_index`` maps a stored ``section_id`` to its index in
    ``flatten_sections(parse_markdown_sections(markdown))`` -- which is also
    its index among the document's heading lines, because a depth-first walk of
    the section tree visits headings in line order. ``unmatched`` lists rows no
    heading could be found for, and ``unclaimed`` the heading indices no row
    claimed. Both are reported rather than papered over: a caller that needs to
    log or refuse is entitled to know, and silently guessing is the failure
    this whole structure exists to prevent.
    """

    heading_index: Dict[str, int] = field(default_factory=dict)
    unmatched: List[str] = field(default_factory=list)
    unclaimed: List[int] = field(default_factory=list)


def _row_field(row: Any, name: str, default: Any = None) -> Any:
    """Read a field from a stored section row given as a mapping or an object.

    Callers arrive with both shapes -- the merge engine passes plain dicts, the
    API passes ``DocumentSection`` instances -- and neither should have to
    convert just to ask which heading a row belongs to.
    """
    if isinstance(row, Mapping):
        return row.get(name, default)
    return getattr(row, name, default)


def pair_sections_to_headings(
    markdown: str,
    rows: Sequence[Any],
    parsed: Optional[Sequence[ParsedSection]] = None,
) -> SectionPairing:
    """Pair stored section rows to the headings of the operative document.

    Stored rows -- not a re-parse -- are the system of record for section
    identity, because the id a fresh parse derives is *wrong* for a retired
    section: it is re-rendered at the end of the document at its original
    depth, so it reads back as a child of whichever heading now precedes it
    (``roles.scope`` re-parses as ``review.scope``). Any code that needs to
    connect a row to the text under its heading therefore needs this mapping,
    and getting it wrong is not cosmetic -- the reader portals per-section
    decision controls onto the ids emitted from this pairing, so a wrong answer
    resolves the wrong section.

    Pairing runs in three passes, each looser than the one above it, and a
    heading is consumed the moment it is claimed so two rows can never share
    one body:

    1. exact ``section_id``, which is what the parser derived in the first
       place and is right for every section that has not moved;
    2. heading text *and* level, for a row whose ancestor heading was renamed
       so that only its stored parent path is stale;
    3. heading text alone, for a row whose depth an editor changed.

    Pass 1 runs for every row before pass 2 begins for any of them. That
    ordering is what makes a retiree resolve correctly: the live
    ``roles.scope`` claims its own heading by exact id first, so the ghost that
    shares its heading text cannot steal it and falls through to the copy at
    the end of the document, which is the one it actually owns.

    Position is deliberately *not* a fallback pass. Pairing the Nth row with
    the Nth heading is precisely the assumption that fails here, and it fails
    silently and cumulatively: one divergence -- a retiree moved to the end, a
    human edit that introduces a ``#`` line, a document title with no row --
    and every subsequent section is off by one, wearing its neighbour's status
    and answering to its neighbour's id. A row that matches nothing is reported
    in ``unmatched`` instead, because no answer is recoverable and a confident
    wrong answer is not.

    Args:
        markdown: The operative document.
        rows: Stored ``document_sections`` rows, as mappings or ORM objects.
        parsed: The already-flattened parse of the same ``markdown``, when the
            caller has one. Supplied only to avoid parsing twice; omitting it
            changes nothing but the cost.
    """
    flat = list(parsed) if parsed is not None else flatten_sections(
        parse_markdown_sections(markdown or "")
    )
    pairing = SectionPairing()
    taken: set = set()

    def claim(predicate) -> Optional[int]:
        for index, heading in enumerate(flat):
            if index in taken:
                continue
            if predicate(heading):
                taken.add(index)
                return index
        return None

    ordered = sorted(rows, key=lambda r: _row_field(r, "ordinal") or 0)
    pending: List[Any] = []

    for row in ordered:
        section_id = _row_field(row, "section_id") or ""
        index = claim(lambda h, wanted=section_id: h.section_id == wanted)
        if index is None:
            pending.append(row)
        else:
            pairing.heading_index[section_id] = index

    for row in pending:
        section_id = _row_field(row, "section_id") or ""
        text = _row_field(row, "heading_text") or ""
        level = _row_field(row, "heading_level")
        index = claim(
            lambda h, t=text, lv=level: h.heading_text == t and h.heading_level == lv
        )
        if index is None:
            index = claim(lambda h, t=text: h.heading_text == t)
        if index is None:
            pairing.unmatched.append(section_id)
        else:
            pairing.heading_index[section_id] = index

    pairing.unclaimed = [i for i in range(len(flat)) if i not in taken]
    return pairing


def section_body_from_markdown(markdown: str, section_id: str) -> Optional[ParsedSection]:
    """Slice one section out of a whole-document markdown snapshot.

    ``document_versions`` stores the generated layer as one markdown blob, so
    "show me what the generator wrote for *this* section" is a slicing problem,
    not a storage one. This is that slice, named and pure so the version-diff
    endpoint and its tests can both reach it.

    Returns ``None`` when the section is not in the snapshot. That is not an
    error case -- it is the defining case for a ``pending_retirement`` section,
    whose *absence* from the newest generation is precisely what retired it. A
    caller that treats "not found" as a failure renders an empty pane where it
    should be saying "the generator no longer produces this section".
    """
    for section in flatten_sections(parse_markdown_sections(markdown)):
        if section.section_id == section_id:
            return section
    return None


def heading_line_indices(markdown: str) -> List[int]:
    """Line numbers of the heading lines, in document order, fences excluded.

    Exposed so callers that need to address a section *by position* -- excising
    a block, stripping a marker inside one -- use the same notion of "the Nth
    heading" that :func:`parse_markdown_sections` does. Deriving it a second
    way is how a document and its section rows drift apart.
    """
    return [h.line_index for h in _extract_headings((markdown or "").split("\n"))]


def excise_section_block(markdown: str, position: int) -> str:
    """Remove the ``position``-th heading and the body attributed to it.

    The block removed is exactly the span
    :func:`parse_markdown_sections` attributes to that heading: the heading
    line through to the line before the *next* heading of any level. A
    subsection nested under the removed heading is therefore left in place, not
    swept away with it -- it is a section in its own right, with its own row
    and its own retirement decision, and deleting it as a side effect of its
    parent's retirement would be the silent deletion the whole
    ``pending_retirement`` mechanism exists to prevent.

    Out-of-range positions return the markdown unchanged rather than raising:
    the caller has already decided this section should go, and failing the
    whole request because the document has since been reparsed differently
    would leave the row and the document disagreeing.
    """
    lines = (markdown or "").split("\n")
    indices = heading_line_indices(markdown)
    if position < 0 or position >= len(indices):
        return markdown or ""

    start = indices[position]
    end = indices[position + 1] if position + 1 < len(indices) else len(lines)
    remaining = lines[:start] + lines[end:]
    return "\n".join(remaining).rstrip() + "\n" if remaining else ""


def split_preamble(markdown: str) -> str:
    """Return the content before the first heading line."""
    lines = (markdown or "").split("\n")
    for index, line in enumerate(lines):
        if _HEADING_RE.match(line):
            return "\n".join(lines[:index])
    return markdown or ""


# ---------------------------------------------------------------------------
# Section metadata rows
# ---------------------------------------------------------------------------


def to_section_rows(
    sections: List[ParsedSection],
    status: str = "new",
) -> List[Dict[str, Any]]:
    """Flatten a parsed tree into ``document_sections``-shaped dicts.

    ``last_generated_hash`` is seeded to the current content hash, which is
    correct for a first generation: nothing has diverged yet.
    """
    rows: List[Dict[str, Any]] = []
    for ordinal, section in enumerate(flatten_sections(sections)):
        rows.append(
            {
                "section_id": section.section_id,
                "heading_text": section.heading_text,
                "heading_level": section.heading_level,
                "ordinal": ordinal,
                "content_hash": section.content_hash,
                "last_generated_hash": section.content_hash,
                "human_edited": False,
                "edited_content": None,
                "status": status,
                "control_ids": section.control_ids,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Section-ID remapping (migration support)
# ---------------------------------------------------------------------------


@dataclass
class SectionIdRemap:
    """The outcome of recomputing one document's section ids.

    ``changes`` maps old id to new id and contains only rows that actually
    move. ``collisions`` names the rows that *would* have moved onto an id
    already spoken for in the same document and were therefore left alone.
    """

    changes: Dict[str, str] = field(default_factory=dict)
    collisions: List[str] = field(default_factory=list)


def recompute_section_ids(rows: Sequence[Dict[str, Any]]) -> SectionIdRemap:
    """Rebuild a document's section ids from its stored headings.

    ``document_sections`` rows keep ``heading_text``, ``heading_level`` and
    ``ordinal``, which is everything :func:`parse_markdown_sections` uses to
    derive an id -- so the ids can be recomputed from the rows alone, without
    reparsing the document. That matters for a data migration: the operative
    markdown may since have been edited, but the rows are the identity system
    of record, and walking them in ``ordinal`` order with a level stack
    reproduces the parser's ``parent.child`` paths exactly.

    Args:
        rows: One document's rows. Each needs ``section_id``, ``heading_text``
            and ``heading_level``; ``ordinal`` orders them (missing ordinals
            sort as 0, preserving the caller's order among ties).

    Returns:
        A :class:`SectionIdRemap`.

    **The collision guard.** ``uq_document_sections_doc_section`` makes
    ``(document_id, section_id)`` unique, so a rename onto an id another row
    already holds -- or will still hold after the pass -- would abort the
    migration for the whole estate. Two headings that differed only by their
    count parenthetical normalise to the same slug once the count is dropped,
    which is exactly how such a pair arises. A colliding row keeps its existing
    id and is reported: one stranded edit is a bounded, visible problem; a
    failed migration is not. Its descendants are then built on the id it kept,
    so the tree stays internally consistent either way.

    **Retired rows.** ``three_way_merge`` appends ``pending_retirement``
    sections after the live ones and numbers them from there, so their
    ordinals order the *document*, not the heading tree. They are therefore
    re-slugged against the parent path already stored in their id rather than
    against the level stack, and never parent a following row.
    """
    ordered = sorted(rows, key=lambda r: (r.get("ordinal") or 0))
    remap = SectionIdRemap()

    #: Ids still held by rows this pass has not reached yet. A candidate that
    #: matches one of these collides just as surely as one already assigned.
    pending = {r.get("section_id") for r in ordered}
    assigned: set = set()
    stack: List[tuple] = []  # (heading_level, chosen_id)

    for row in ordered:
        old_id = row.get("section_id") or ""
        level = int(row.get("heading_level") or 2)
        retiring = row.get("status") == "pending_retirement"
        pending.discard(old_id)

        normalised = normalise_section_id(row.get("heading_text") or "")

        if retiring:
            # A retiree's ordinal is not a position in the heading tree.
            # ``three_way_merge`` appends retired sections after every live one
            # and numbers them there, so walking one with the level stack
            # parents it under whichever live heading happens to sort last --
            # handing a retired section somebody else's identity, which is the
            # exact failure this function exists to prevent. Its real parent is
            # already recorded in its stored id, so re-normalise its own leaf
            # and keep the stored path. Following that path through ``changes``
            # picks up a rename this same pass made: retirees sort after their
            # ancestors, so an ancestor's entry is already recorded.
            stored_parent = old_id.rsplit(".", 1)[0] if "." in old_id else ""
            parent_path = remap.changes.get(stored_parent, stored_parent)
        else:
            while stack and stack[-1][0] >= level:
                stack.pop()
            parent_path = stack[-1][1] if stack else ""

        candidate = f"{parent_path}.{normalised}" if parent_path else normalised

        if not normalised or candidate == old_id:
            chosen = old_id
        elif candidate in assigned or candidate in pending:
            chosen = old_id
            remap.collisions.append(old_id)
        else:
            chosen = candidate
            remap.changes[old_id] = candidate

        assigned.add(chosen)
        if not retiring:
            # A retiree is not in the live tree, so it must never become the
            # parent of a row that follows it.
            stack.append((level, chosen))

    return remap
