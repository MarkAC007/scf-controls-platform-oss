"""CDM v2 — deterministic document chunking (epic #709, Part 2).

Chunking is a **stored artefact**, not an ephemeral step. ``ts_rank_cd`` ranks
lexeme positions inside a ``tsvector`` and character offsets cannot be recovered
from it, so offsets must be captured at chunk-creation time. Ranking therefore
happens over whole chunks; phrase location happens *within* the winning chunk.

Normative rules (issue #709 Part 2 "Chunking is a stored artefact"), stated
precisely enough that two implementations agree:

1. A heading always flushes the current chunk and becomes the *next* chunk's
   ``heading``. Headings are **not** part of ``body``.
2. A blank line flushes only once the accumulated chunk exceeds
   ``FLUSH_MIN_CHARS`` (400).
3. ``HARD_CAP_CHARS`` (1800) is a hard cap that flushes mid-paragraph.

Determinism is load-bearing: re-ingest of unchanged text must produce identical
offsets, or every stored citation silently moves (issue risk R4).

Design decisions carried from the epic's THINK phase:

* **D-11** — a markdown heading is only one of the heading shapes we see. The
  default extraction path is PyMuPDF over a PDF, whose output contains no
  markdown at all, so a markdown-only rule would leave ``heading`` NULL for
  every document on the path this epic exists to unblock. We therefore reuse
  the numbered / ``Section N`` / markdown detector that already ships in
  ``cdm_mapping.derive_section``'s pattern family rather than writing a second
  one that can drift from it.
* **D-13** — chunks do not overlap. A phrase spanning a hard-cap boundary
  cannot be verified and is dropped. ``HARD_CAP_CHARS`` flushing at a word
  boundary (rule 3 below) reduces the loss; it does not remove it.
* **D-10** — ``body_norm`` is produced *here*, in Python, and stored. It is
  never recomputed in SQL, because a Postgres ``regexp_replace`` and this
  function would have to agree byte-for-byte forever.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator

# Rule thresholds. Changing either is a breaking change requiring a re-chunk
# plus a stale sweep (issue risk R4) — they are not tuning knobs.
FLUSH_MIN_CHARS = 400
HARD_CAP_CHARS = 1800

# Look back at most this far for a word boundary when the hard cap fires.
# Bounded so a chunk of one enormous "word" (a base64 blob, a minified table)
# still flushes at the cap instead of degenerating.
_WORD_BOUNDARY_LOOKBACK = 120


@dataclass(frozen=True)
class DocumentChunk:
    """One stored chunk. ``char_start``/``char_end`` index the *extracted text*.

    Invariant, asserted by :func:`chunk_document_text`:
        ``extracted_text[chunk.char_start:chunk.char_end] == chunk.body``
    """

    ordinal: int
    heading: str | None
    body: str
    char_start: int
    char_end: int
    body_norm: str


# Heading shapes, most specific first. Mirrors the pattern family in
# ``services.cdm_mapping._SECTION_PATTERNS`` (D-11) — numbered headings are
# more specific than markdown, markdown more specific than Section/Chapter.
_HEADING_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Numbered: "4.2.1 Access Control". The clause number is retained in the
    # title because it *is* the locator an auditor cites ("§4.2.1"); dropping
    # it would leave two identically-named subsections indistinguishable.
    re.compile(r"^(?P<title>\d+(?:\.\d+)+\s+\S[^\r\n]{0,250})$"),
    re.compile(r"^#{1,6}\s+(?P<title>\S[^\r\n]{0,250})$"),
    # Appendices are lettered at least as often as numbered ("Appendix B"),
    # so the designator class covers digits, roman numerals and letters.
    re.compile(
        r"^(?P<kind>Section|Chapter|Appendix|Annex|Part)\s+[A-Z0-9]{1,4}"
        r"(?:[:.\)\s-]+(?P<title>\S[^\r\n]{0,250}))?$",
        re.IGNORECASE,
    ),
)

_HEADING_MAX_LEN = 255
_WHITESPACE_RUN = re.compile(r"\s+")


def normalise_whitespace(text: str) -> str:
    """Collapse every whitespace run to a single space and strip the ends.

    The single normalisation implementation for CDM (D-10). ``body_norm`` in
    the database is produced by this function and by nothing else, so the
    Python phrase-verification pass can rely on it byte-for-byte.
    """
    return _WHITESPACE_RUN.sub(" ", text).strip()


def _match_heading(line: str) -> str | None:
    """Return the heading title carried by ``line``, or None.

    A heading must be a whole line. Fenced-code content is excluded by the
    caller, not here — this predicate is deliberately pure so it can be unit
    tested against a line in isolation.
    """
    stripped = line.strip()
    if not stripped or len(stripped) > 300:
        return None
    for pattern in _HEADING_PATTERNS:
        match = pattern.match(stripped)
        if match is None:
            continue
        title = (match.groupdict().get("title") or "").strip()
        if not title:
            # "Section 4" with no trailing title is still a heading; fall back
            # to the whole line so the locator is not silently dropped.
            title = stripped
        cleaned = normalise_whitespace(title).strip(" :.-")
        if cleaned:
            return cleaned[:_HEADING_MAX_LEN]
    return None


def _is_blank(line: str) -> bool:
    """True for an empty line *or* a whitespace-only line.

    PDF extraction emits ``"   "`` and ``"\\t"`` lines routinely, so treating
    only ``""`` as blank would make the 400-char flush rule fire on some
    documents and not others (RedTeam C2).
    """
    return not line.strip()


def _word_boundary_cut(body: str, limit: int) -> int:
    """Return the cut index at or before ``limit``, preferring a word boundary.

    Scans back up to ``_WORD_BOUNDARY_LOOKBACK`` characters for whitespace so
    the hard cap does not split a token in half. Falls back to ``limit`` when
    no boundary is within reach.
    """
    if limit >= len(body):
        return len(body)
    floor = max(1, limit - _WORD_BOUNDARY_LOOKBACK)
    for index in range(limit, floor - 1, -1):
        if body[index - 1].isspace():
            return index
    return limit


def _iter_lines_with_offsets(text: str) -> Iterator[tuple[str, int, int]]:
    """Yield ``(line_without_terminator, start_offset, end_offset_incl_terminator)``.

    Offsets index ``text`` directly, so a chunk assembled from consecutive
    lines can report exact ``char_start``/``char_end`` without re-searching.
    Handles ``\\n`` and ``\\r\\n``; a trailing line with no terminator is
    yielded too.
    """
    position = 0
    length = len(text)
    while position < length:
        newline = text.find("\n", position)
        if newline == -1:
            yield text[position:length], position, length
            return
        line_end = newline
        if line_end > position and text[line_end - 1] == "\r":
            line_end -= 1
        yield text[position:line_end], position, newline + 1
        position = newline + 1


def chunk_document_text(extracted_text: str) -> list[DocumentChunk]:
    """Split ``extracted_text`` into deterministic, offset-bearing chunks.

    Returns chunks in document order with ``ordinal`` starting at 0 and no
    gaps. Whitespace-only input yields an empty list.

    The returned offsets are **character** indices into ``extracted_text``
    (Python string indices), not byte indices — see ISC-127 and the
    ``cdm_verification`` module, which preserves the same convention.
    """
    if not extracted_text or not extracted_text.strip():
        return []

    chunks: list[DocumentChunk] = []
    pending_heading: str | None = None
    current_heading: str | None = None
    buffer_start: int | None = None
    buffer_end: int | None = None
    in_fence = False

    def flush() -> None:
        """Emit the buffered span as a chunk, trimming surrounding whitespace."""
        nonlocal buffer_start, buffer_end, current_heading, pending_heading
        if buffer_start is None or buffer_end is None:
            # Nothing buffered — but a pending heading must still be promoted,
            # or back-to-back headings ("# Policy" immediately followed by
            # "## 4.2 Onboarding") silently drop the earlier one and the next
            # chunk is attributed to the wrong section.
            if pending_heading is not None:
                current_heading = pending_heading
                pending_heading = None
            return
        raw = extracted_text[buffer_start:buffer_end]
        if not raw.strip():
            buffer_start = buffer_end = None
            current_heading = pending_heading
            pending_heading = None
            return

        # Trim leading/trailing whitespace while keeping offsets exact, so the
        # stored invariant extracted_text[start:end] == body always holds.
        lead = len(raw) - len(raw.lstrip())
        trail = len(raw) - len(raw.rstrip())
        start = buffer_start + lead
        end = buffer_end - trail
        body = extracted_text[start:end]

        chunks.append(
            DocumentChunk(
                ordinal=len(chunks),
                heading=current_heading,
                body=body,
                char_start=start,
                char_end=end,
                body_norm=normalise_whitespace(body),
            )
        )
        buffer_start = buffer_end = None
        current_heading = pending_heading
        pending_heading = None

    def buffered_length() -> int:
        if buffer_start is None or buffer_end is None:
            return 0
        return buffer_end - buffer_start

    for line, line_start, line_end in _iter_lines_with_offsets(extracted_text):
        stripped = line.strip()

        # Fenced code blocks: a '#' inside one is not a heading (RedTeam C2).
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            if buffer_start is None:
                buffer_start = line_start
            buffer_end = line_end
            continue

        if not in_fence:
            heading = _match_heading(line)
            if heading is not None:
                # Rule 1: a heading flushes the current chunk and becomes the
                # next chunk's heading. The heading line itself is excluded
                # from every body — it is a locator, not content.
                pending_heading = heading
                flush()
                continue

        if _is_blank(line):
            # Rule 2: a blank line flushes only past the soft minimum.
            if buffered_length() > FLUSH_MIN_CHARS:
                flush()
            elif buffer_start is not None:
                buffer_end = line_end
            continue

        if buffer_start is None:
            buffer_start = line_start
        buffer_end = line_end

        # Rule 3: hard cap flushes mid-paragraph, cut at a word boundary.
        while buffered_length() >= HARD_CAP_CHARS:
            assert buffer_start is not None and buffer_end is not None
            span = extracted_text[buffer_start:buffer_end]
            cut = _word_boundary_cut(span, HARD_CAP_CHARS)
            split_at = buffer_start + cut
            remainder_end = buffer_end
            buffer_end = split_at
            flush()
            # The remainder keeps the same heading — it is the same section.
            current_heading = chunks[-1].heading if chunks else current_heading
            if split_at < remainder_end:
                buffer_start = split_at
                buffer_end = remainder_end
            else:
                break

    flush()

    # Invariant guard. A violation here means offsets would be silently wrong
    # in the database, which is the exact failure class this module exists to
    # prevent — fail loudly at ingest instead.
    for chunk in chunks:
        if extracted_text[chunk.char_start:chunk.char_end] != chunk.body:
            raise AssertionError(
                f"chunk {chunk.ordinal} offsets do not round-trip "
                f"({chunk.char_start}:{chunk.char_end})"
            )

    return chunks
