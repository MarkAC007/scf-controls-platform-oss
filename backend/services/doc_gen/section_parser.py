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
from typing import Any, Dict, List

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

#: Leading section numbering: "1.", "4.1", "4.1.2 "
_LEADING_NUMBER_RE = re.compile(r"^\d+(?:\.\d+)*\.?\s*")


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

    Strips leading numbering, bold markers, trailing colons and bracketed SCF
    IDs; lowercases; collapses everything non-alphanumeric to single hyphens.

    Numbering is stripped on purpose: renumbering a document (inserting a new
    section 3) must not orphan the human edits attached to what was section 4.

    Examples:
        "1. Document Control"    -> "document-control"
        "4.1 Access Management"  -> "access-management"
        "**Evidence Produced:**" -> "evidence-produced"
    """
    text = heading_text or ""
    text = _LEADING_NUMBER_RE.sub("", text)
    text = text.replace("**", "")
    text = re.sub(r":+$", "", text)
    text = _CONTROL_ID_RE.sub("", text)
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
            content_hash=sha256(body),
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
