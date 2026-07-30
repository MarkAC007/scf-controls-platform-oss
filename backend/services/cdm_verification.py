"""CDM v2 — Tier 2 phrase verification and the offset rule (epic #709).

Tier 1 (Postgres FTS) answers *"what might address this control?"* and returns
leads. Tier 2 — this module — answers *"which document says this, exactly, and
where?"* Nothing becomes a ``cdm_mapping`` until Tier 2 has located the exact
wording and its offsets.

The governing structural rule (issue #709, HTV-2):

    A retrieval backend that cannot return verifiable character offsets into
    stored document text MAY surface exploratory hits, but MAY NEVER create a
    ``cdm_mapping``.

Two design decisions make that rule real rather than aspirational:

* **Enforced at construction, not by the type checker.** A Python ``Protocol``
  is structural and erased at runtime, so ``char_start: int | None`` satisfies
  a checker while emitting ``None``. :class:`VerifiedMatch` therefore validates
  in ``__post_init__`` and raises. The rule then holds whether or not anything
  runs mypy.

* **Both passes run here, in Python, against the stored chunk body.** Whitespace
  normalisation is *length-changing*: collapsing ``"control   is\\n\\n  maintained"``
  (28 chars) to ``"control is maintained"`` (21) means an index into the
  normalised text is **not** an index into the original. Using one for the other
  offsets every flexible citation by the number of collapsed characters before
  it — landing in the right neighbourhood with the wrong start, which is the
  worst failure mode on an audit-grade table. We build an explicit
  normalised→original index map instead.

  Running pass 2 in SQL would additionally require Postgres ``regexp_replace``
  and Python's normaliser to agree byte-for-byte forever. ``body_norm`` in the
  database is a *retrieval* aid only; it is never the thing we measure against.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

_WHITESPACE_RUN = re.compile(r"\s+")


class MatchType(str, Enum):
    """How a phrase was located. Surfaced to the reviewer, never inferred."""

    EXACT = "exact"
    WHITESPACE_FLEXIBLE = "whitespace_flexible"
    FUZZY = "fuzzy"


@dataclass(frozen=True)
class VerifiedMatch:
    """A phrase located in stored text, with offsets that are known to resolve.

    Construction fails loudly on any state that would produce a wrong citation.
    This is the only type the mapping writer accepts, so an unverifiable hit
    cannot reach ``cdm_mappings`` even by accident.
    """

    char_start: int
    char_end: int
    match_type: MatchType
    matched_text: str

    def __post_init__(self) -> None:
        if self.char_start is None or self.char_end is None:
            raise ValueError("VerifiedMatch requires non-null offsets")
        if self.char_start < 0:
            raise ValueError(f"char_start must be non-negative, got {self.char_start}")
        if self.char_end <= self.char_start:
            raise ValueError(
                f"char_end ({self.char_end}) must exceed char_start ({self.char_start})"
            )
        if not self.matched_text:
            raise ValueError("VerifiedMatch requires non-empty matched_text")


def build_normalisation_map(text: str) -> tuple[str, list[int]]:
    """Normalise whitespace and return ``(normalised, index_map)``.

    ``index_map[i]`` is the index in ``text`` of the character that produced
    ``normalised[i]``. For a collapsed whitespace run, that is the index of the
    run's **first** character — so a match starting on a collapsed space maps
    back to where the whitespace began, never past it.

    ``len(index_map) == len(normalised)`` always, which is what makes the
    reverse mapping total rather than best-effort.
    """
    normalised_chars: list[str] = []
    index_map: list[int] = []

    position = 0
    length = len(text)
    # Skip leading whitespace so the result matches ``str.strip()`` semantics.
    while position < length and text[position].isspace():
        position += 1

    while position < length:
        char = text[position]
        if char.isspace():
            run_start = position
            while position < length and text[position].isspace():
                position += 1
            if position >= length:
                break  # trailing whitespace is stripped, not emitted
            normalised_chars.append(" ")
            index_map.append(run_start)
            continue
        normalised_chars.append(char)
        index_map.append(position)
        position += 1

    return "".join(normalised_chars), index_map


def normalise_whitespace(text: str) -> str:
    """Whitespace-collapsed copy of ``text``.

    Kept byte-identical to :func:`services.cdm_chunking.normalise_whitespace`;
    the two exist separately only so this module does not import the chunker.
    """
    return _WHITESPACE_RUN.sub(" ", text).strip()


def locate_phrase(
    body: str,
    phrase: str,
    *,
    allow_fuzzy: bool = False,
) -> VerifiedMatch | None:
    """Locate ``phrase`` within ``body``, returning offsets **into ``body``**.

    Two passes, in order:

    1. **Exact** — a direct substring find. Offsets are the find result.
    2. **Whitespace-flexible** — both sides normalised, matched, then the hit
       mapped back to original coordinates through the index map. Labelled
       ``whitespace_flexible`` so the reviewer sees it was not a literal match.

    A third fuzzy pass is reserved for a ``pg_trgm``-backed caller and is only
    reachable via ``allow_fuzzy``; it is never a dependency (issue #709 HTV-4).

    Returns ``None`` when neither pass resolves — the caller must then create
    no mapping. First occurrence wins within the chunk; at chunk scope that
    ambiguity is bounded and the surrounding chunk is cited alongside it.
    """
    if not body or not phrase or not phrase.strip():
        return None

    # ── Pass 1: exact ────────────────────────────────────────────────────
    exact_start = body.find(phrase)
    if exact_start >= 0:
        return VerifiedMatch(
            char_start=exact_start,
            char_end=exact_start + len(phrase),
            match_type=MatchType.EXACT,
            matched_text=body[exact_start:exact_start + len(phrase)],
        )

    # ── Pass 2: whitespace-flexible, via an explicit index map ───────────
    normalised_body, index_map = build_normalisation_map(body)
    normalised_phrase = normalise_whitespace(phrase)
    if not normalised_phrase:
        return None

    norm_start = normalised_body.find(normalised_phrase)
    if norm_start >= 0:
        norm_end = norm_start + len(normalised_phrase)
        # Map the normalised span back to original coordinates. The start maps
        # directly; the end maps from the *last matched character* plus its own
        # length in the original, so a trailing collapsed run is not swallowed.
        orig_start = index_map[norm_start]
        last_index = index_map[norm_end - 1]
        last_char_is_space = normalised_body[norm_end - 1] == " "
        if last_char_is_space:
            # The match ends on a collapsed run; end at the run's first char.
            orig_end = last_index
        else:
            orig_end = last_index + 1
        if orig_end <= orig_start:
            return None
        return VerifiedMatch(
            char_start=orig_start,
            char_end=orig_end,
            match_type=MatchType.WHITESPACE_FLEXIBLE,
            matched_text=body[orig_start:orig_end],
        )

    if not allow_fuzzy:
        return None

    return _locate_fuzzy(body, phrase, normalised_body, index_map)


def _locate_fuzzy(
    body: str,
    phrase: str,
    normalised_body: str,
    index_map: list[int],
) -> VerifiedMatch | None:
    """Optional third pass — longest common token run.

    Deliberately conservative: it locates the longest contiguous run of the
    phrase's tokens that appears in the body, and only accepts it when that run
    covers at least 60% of the phrase's tokens. Anything weaker is not evidence
    an auditor would accept, so it returns ``None`` rather than a citation.
    """
    tokens = normalise_whitespace(phrase).split()
    if len(tokens) < 3:
        return None

    minimum = max(3, int(len(tokens) * 0.6))
    for window in range(len(tokens), minimum - 1, -1):
        for start in range(0, len(tokens) - window + 1):
            candidate = " ".join(tokens[start:start + window])
            found = normalised_body.find(candidate)
            if found < 0:
                continue
            end = found + len(candidate)
            orig_start = index_map[found]
            last_index = index_map[end - 1]
            orig_end = last_index if normalised_body[end - 1] == " " else last_index + 1
            if orig_end <= orig_start:
                continue
            return VerifiedMatch(
                char_start=orig_start,
                char_end=orig_end,
                match_type=MatchType.FUZZY,
                matched_text=body[orig_start:orig_end],
            )
    return None


def locate_phrase_in_document(
    document_text: str,
    chunk_char_start: int,
    body: str,
    phrase: str,
    *,
    allow_fuzzy: bool = False,
) -> VerifiedMatch | None:
    """Locate ``phrase`` and return offsets in **document** coordinates.

    ``cdm_mappings`` stores offsets against the document's extracted text, not
    against the chunk, so the chunk-relative result is shifted by the chunk's
    own start. The shifted offsets are re-validated against ``document_text``
    before being returned — a mismatch means the stored chunk and the current
    extracted text have diverged, and we return ``None`` rather than write a
    citation that points somewhere plausible and wrong.
    """
    local = locate_phrase(body, phrase, allow_fuzzy=allow_fuzzy)
    if local is None:
        return None

    absolute_start = chunk_char_start + local.char_start
    absolute_end = chunk_char_start + local.char_end

    if document_text[absolute_start:absolute_end] != local.matched_text:
        return None

    return VerifiedMatch(
        char_start=absolute_start,
        char_end=absolute_end,
        match_type=local.match_type,
        matched_text=local.matched_text,
    )
