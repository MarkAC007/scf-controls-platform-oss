"""CDM v2 — Tier 1 discovery via Postgres FTS (epic #709).

Two tiers, one database. This module is Tier 1: *"what in my docs might address
this control?"* It ranks stored chunks with ``ts_rank_cd`` over a GIN-indexed
generated ``tsvector`` and returns **typed rows that carry character offsets**.
Tier 2 (``cdm_verification``) then locates the exact wording inside the winning
chunk. Nothing becomes a mapping until it has.

Three things here are load-bearing:

* **The typed row carries offsets, so the offset rule is structural.** A backend
  that cannot populate ``char_start``/``char_end`` cannot produce a
  :class:`RetrievedChunk`, and the mapping writer accepts nothing else.

* **``ts_rank_cd`` is normalised absolutely, not against the result set.**
  ``ts_rank_cd`` is unbounded and length-dependent. Dividing by the maximum of
  the current result set would make the top hit score exactly 1.0 whatever its
  quality — which is precisely the "score is really list position" defect v1
  died of, rebuilt in more code. We pass normalisation bitmask ``32``, which
  applies ``rank/(rank+1)`` inside Postgres: bounded to [0,1) independently of
  what else was returned, so one weak hit and one strong hit score differently.

* **One scan per control, with per-objective attribution.** A control is not one
  question — it is N assessment objectives, each a distinct thing to look for.
  Merging them into a single blob makes any retriever optimise for their
  centroid and match none of them well (issue #709 D3). We build one ``tsquery``
  per objective, combine them in a single statement, and report *which*
  objective matched. That is one scan, not N round trips.

Every ``tsquery`` is built with ``plainto_tsquery(:bound_param)``. Control and
objective text is catalogue data rather than end-user input, but it reaches SQL
all the same, and string-interpolating it would be an injection surface for the
sake of nothing.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ts_rank_cd normalisation bitmask. 32 => rank / (rank + 1), an absolute
# transform bounded to [0,1). See module docstring — this is not a tuning knob.
_TS_RANK_NORMALISATION = 32

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")

# Terms too generic to count as evidence of overlap. Deliberately short: the
# tsvector already applies English stemming and stopword removal, and this set
# only guards the *term-overlap component*, which is computed in Python.
_OVERLAP_STOPWORDS = frozenset({
    "the", "and", "for", "are", "with", "that", "this", "shall", "must",
    "should", "has", "have", "its", "any", "all", "such", "from", "which",
    "will", "been", "was", "were", "not", "but", "may", "can", "each",
    "organisation", "organization", "control", "controls",
})


@dataclass(frozen=True)
class RetrievedChunk:
    """One Tier-1 lead. Offsets are mandatory — that is the whole point.

    ``char_start``/``char_end`` are character offsets into the *document's*
    extracted text (not into the chunk), matching ``cdm_document_chunks``.
    """

    chunk_id: UUID
    cdm_document_id: UUID
    ordinal: int
    heading: str | None
    body: str
    body_norm: str
    char_start: int
    char_end: int
    ts_rank: float
    matched_objectives: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        if self.char_start is None or self.char_end is None:
            raise ValueError("RetrievedChunk requires non-null offsets")
        # A negative start is worse than an error: Python slices it from the
        # end of the string, so the citation would quietly point at the wrong
        # passage instead of failing.
        if self.char_start < 0:
            raise ValueError("RetrievedChunk requires char_start >= 0")
        if self.char_end <= self.char_start:
            raise ValueError("RetrievedChunk requires char_end > char_start")


@dataclass(frozen=True)
class ControlQuery:
    """The query seed for one control, kept decomposed rather than blobbed."""

    scf_id: str | None
    control_name: str | None
    control_question: str | None
    objectives: tuple[str, ...] = field(default=())

    def query_texts(self) -> list[str]:
        """One query string per objective, or a name+question fallback.

        A control with no assessment objectives still has to be searchable, so
        it falls back to name + question. That fallback is a single text, which
        is exactly the v1 behaviour — acceptable only because there is nothing
        finer-grained to decompose into.
        """
        objectives = [o.strip() for o in self.objectives if o and o.strip()]
        if objectives:
            return objectives
        fallback_parts = [
            part.strip()
            for part in (self.control_name, self.control_question)
            if part and part.strip()
        ]
        if not fallback_parts:
            return []
        return [" ".join(fallback_parts)]

    def all_terms(self) -> frozenset[str]:
        """Distinct meaningful terms across the whole control, lowercased."""
        blob = " ".join(
            part for part in (
                self.control_name,
                self.control_question,
                *self.objectives,
            ) if part
        )
        tokens = {t.lower() for t in _TOKEN_RE.findall(blob) if len(t) > 2}
        return frozenset(tokens - _OVERLAP_STOPWORDS)


class RetrievalBackend(Protocol):
    """Tier-1 discovery contract.

    A backend that cannot return offset-bearing rows cannot satisfy this
    signature, because :class:`RetrievedChunk` refuses to be constructed
    without them. That is what makes the offset rule structural rather than a
    convention someone remembers to follow.
    """

    name: str
    can_produce_mappings: bool

    def search(
        self,
        session: Session,
        org_id: UUID,
        query: ControlQuery,
        *,
        limit: int,
    ) -> tuple[list[RetrievedChunk], int]:
        """Return ``(rows, total_candidates_before_truncation)``."""
        ...


class PostgresFTSBackend:
    """Default backend. Postgres FTS over ``cdm_document_chunks``.

    Zero new services: this runs inside the database the platform already
    ships, backs up, and scopes by tenant.
    """

    name = "postgres_fts"
    can_produce_mappings = True

    def search(
        self,
        session: Session,
        org_id: UUID,
        query: ControlQuery,
        *,
        limit: int,
    ) -> tuple[list[RetrievedChunk], int]:
        built = self.build_statement(org_id, query, limit=limit)
        if built is None:
            return [], 0
        sql, params = built
        rows = session.execute(sql, params).mappings().all()
        return self.rows_to_chunks(rows)

    def build_statement(
        self,
        org_id: UUID,
        query: ControlQuery,
        *,
        limit: int,
    ) -> tuple[Any, dict[str, object]] | None:
        """Build the search statement without executing it.

        Split out from :meth:`search` so the sync mapping path and the async
        API path issue byte-identical SQL. Two hand-maintained copies of this
        query would be the obvious way to serve both, and the obvious way for
        the review queue and the search box to quietly disagree about what a
        document says.

        Returns ``None`` when the control yields no queryable text.
        """
        query_texts = query.query_texts()
        if not query_texts:
            return None

        # Build one tsquery per objective, all bound parameters. The combined
        # predicate is a single OR'd scan; per-objective attribution comes from
        # positionally-aligned CASE arrays so we learn *which* objective
        # matched without issuing one statement per objective.
        params: dict[str, object] = {"org_id": str(org_id), "limit": limit}
        match_clauses: list[str] = []
        attribution_clauses: list[str] = []
        attribution_rank_clauses: list[str] = []
        rank_terms: list[str] = []

        for index, query_text in enumerate(query_texts):
            key = f"q{index}"
            params[key] = query_text
            # plainto_tsquery ANDs every lexeme, so a full objective sentence
            # ("Supplier access credentials are revoked promptly on
            # termination") matches almost nothing — one absent word kills the
            # whole clause. Behind a human accept/dismiss gate recall is worth
            # more than precision: a missed passage is invisible, a weak
            # proposal costs one click (issue #709 R3). We therefore rewrite
            # the parsed query's AND operators to OR.
            #
            # The user text still goes through plainto_tsquery, so it is
            # parsed and escaped by Postgres; the replace() operates on that
            # already-sanitised lexeme output, not on raw input. Ranking still
            # rewards chunks matching more of the query, so OR widens the net
            # without flattening the order.
            tsq = (
                f"replace(plainto_tsquery('english', :{key})::text, '&', '|')::tsquery"
            )
            match_clauses.append(f"c.search_vector @@ {tsq}")
            attribution_clauses.append(
                f"CASE WHEN c.search_vector @@ {tsq} THEN :{key} ELSE NULL END"
            )
            # Bitmask 32 => rank/(rank+1): absolute, bounded, result-set
            # independent. See module docstring.
            objective_rank = (
                f"ts_rank_cd(c.search_vector, {tsq}, {_TS_RANK_NORMALISATION})"
            )
            rank_terms.append(objective_rank)
            attribution_rank_clauses.append(objective_rank)

        where_match = " OR ".join(match_clauses)
        # NULLs are deliberately retained in both arrays so the two stay
        # positionally aligned; the pairing is filtered in Python. Stripping
        # NULLs from the text array alone would silently shift every objective
        # against the wrong rank, and a mapping would then cite an objective
        # the passage does not answer.
        attribution_array = ", ".join(attribution_clauses)
        attribution_rank_array = ", ".join(attribution_rank_clauses)
        # Greatest per-objective rank, so a chunk that answers one objective
        # extremely well is not diluted by the objectives it does not answer.
        rank_expression = f"GREATEST({', '.join(rank_terms)})" if len(rank_terms) > 1 else rank_terms[0]

        sql = text(
            f"""
            WITH matched AS (
                SELECT
                    c.id,
                    c.cdm_document_id,
                    c.ordinal,
                    c.heading,
                    c.body,
                    c.body_norm,
                    c.char_start,
                    c.char_end,
                    {rank_expression} AS ts_rank,
                    ARRAY[{attribution_array}] AS matched_objectives,
                    ARRAY[{attribution_rank_array}] AS matched_objective_ranks
                FROM cdm_document_chunks c
                JOIN cdm_documents d ON d.id = c.cdm_document_id
                WHERE c.organization_id = CAST(:org_id AS uuid)
                  AND d.organization_id = CAST(:org_id AS uuid)
                  AND ({where_match})
            )
            SELECT *, COUNT(*) OVER () AS total_candidates
            FROM matched
            ORDER BY ts_rank DESC, cdm_document_id, ordinal
            LIMIT :limit
            """
        )

        return sql, params

    @staticmethod
    def rows_to_chunks(rows) -> tuple[list[RetrievedChunk], int]:
        """Map result rows to typed chunks plus the pre-truncation total."""
        if not rows:
            return [], 0

        total = int(rows[0]["total_candidates"])

        def _ordered_objectives(row) -> tuple[str, ...]:
            """Matched objectives, strongest first.

            Order carries meaning downstream: the mapping records
            ``matched_objectives[0]`` as the objective the passage answers, and
            a reviewer reads that as a claim about the evidence. Array order
            straight from the CASE list is query order, not relevance, so the
            first entry would frequently name an objective the passage only
            grazes.
            """
            texts = row["matched_objectives"] or ()
            ranks = row["matched_objective_ranks"] or ()
            paired = [
                (float(rank or 0.0), text_value)
                for text_value, rank in zip(texts, ranks)
                if text_value is not None
            ]
            paired.sort(key=lambda pair: pair[0], reverse=True)
            return tuple(text_value for _, text_value in paired)

        chunks = [
            RetrievedChunk(
                chunk_id=row["id"],
                cdm_document_id=row["cdm_document_id"],
                ordinal=row["ordinal"],
                heading=row["heading"],
                body=row["body"],
                body_norm=row["body_norm"],
                char_start=row["char_start"],
                char_end=row["char_end"],
                ts_rank=float(row["ts_rank"]),
                matched_objectives=_ordered_objectives(row),
            )
            for row in rows
        ]
        return chunks, total


class LightRAGBackend:
    """Demoted semantic backend — exploratory hits only (issue #709 HTV-3).

    LightRAG returns chunk *text*, not verifiable offsets into our stored
    documents. Under the offset rule that means it may surface leads for a
    human to look at, but may never write to ``cdm_mappings``. The flag below
    is what the mapping writer checks; it is not advisory.

    Default-off. A default-off plugin is only honest if the default path is
    sufficient on its own, which is what the Postgres tier is for.
    """

    name = "lightrag"
    can_produce_mappings = False

    def search(
        self,
        session: Session,
        org_id: UUID,
        query: ControlQuery,
        *,
        limit: int,
    ) -> tuple[list[RetrievedChunk], int]:
        raise NotImplementedError(
            "LightRAG is an exploratory backend and cannot return verifiable "
            "offsets; it must not be used on the mapping path."
        )


def is_lightrag_retrieval_enabled() -> bool:
    """LightRAG retrieval is opt-in and defaults to off."""
    return os.getenv("ENABLE_CDM_LIGHTRAG", "false").strip().lower() == "true"


def get_retrieval_backend(name: str | None = None) -> RetrievalBackend:
    """Single selection point for the retrieval backend.

    Defaults to Postgres FTS. Selecting ``lightrag`` yields a backend whose
    ``can_produce_mappings`` is False, so the mapping path refuses it rather
    than silently writing unverifiable citations.
    """
    resolved = (name or os.getenv("CDM_RETRIEVAL_BACKEND", "postgres_fts")).strip().lower()
    if resolved == "lightrag":
        if not is_lightrag_retrieval_enabled():
            logger.warning(
                "CDM_RETRIEVAL_BACKEND=lightrag but ENABLE_CDM_LIGHTRAG is not "
                "true; falling back to postgres_fts"
            )
            return PostgresFTSBackend()
        return LightRAGBackend()
    return PostgresFTSBackend()


def compute_term_overlap(chunk_body_norm: str, control_terms: frozenset[str]) -> float:
    """Fraction of the control's distinct terms present in the chunk.

    Tokens are lowercased and compared by prefix so that the component broadly
    agrees with the ``tsvector`` that produced the hit — an exact string
    comparison would count "maintains" as a miss where the stemmer counted it
    as a match, and the score would then contradict its own retrieval.
    """
    if not control_terms:
        return 0.0
    chunk_tokens = {t.lower() for t in _TOKEN_RE.findall(chunk_body_norm)}
    if not chunk_tokens:
        return 0.0

    hits = 0
    for term in control_terms:
        stem = term[:6]
        if any(token.startswith(stem) or term.startswith(token[:6]) for token in chunk_tokens):
            hits += 1
    return hits / len(control_terms)


def compute_objective_coverage(
    matched_objectives: Sequence[str],
    all_objectives: Sequence[str],
) -> float:
    """Fraction of the control's objectives this chunk matched.

    Returns 0.0 rather than 1.0 when the control has no objectives at all: a
    control with nothing to cover has not had anything covered, and returning
    1.0 would hand every fallback-query hit a free full-marks component.
    """
    total = len([o for o in all_objectives if o and o.strip()])
    if total == 0:
        return 0.0
    matched = len({o for o in matched_objectives if o and o.strip()})
    return min(1.0, matched / total)
