"""CDM mapping computation — pure helper consumed by the cdm.compute_mappings task.

The helper is testable without Celery, Redis, or HTTPX live calls; callers inject
a sync SQLAlchemy ``Session``, a LightRAG query callable, and an extracted-text
resolver. The Celery task in ``tasks_cdm.py`` is a thin wrapper that wires the
production session + real ``CDMLightRAGClient`` + real ``cdm_storage`` reader.

CDM v2 (epic #709) adds ``compute_mappings_v2``, which supersedes
``compute_mappings_for_org`` on the mapping path. The v1 helper is retained
only so the LightRAG-backed deployment and its tests keep working; new callers
must use v2. The two differ in the one respect that matters for an audit-grade
table: v2 refuses any retrieval backend that cannot return character offsets,
so a mapping cannot exist without provenance that resolves.

D-1 (v1, superseded): scores were rank-derived (``1.0 - 0.05*rank``) because
LightRAG's ``/query/data`` returns no per-chunk scores. That made the score a
restatement of list position, and made the default 0.7 threshold unreachable
for anything past rank 6. v2 composes the score from observable components and
persists them.

D-5: dedup is over ``(scoped_control_id, cdm_document_id, byte_offset_start)``
because chunk_id regenerates on re-ingest but byte_offset_start is stable.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Iterable, Optional, Protocol, Sequence
from uuid import UUID

from sqlalchemy import or_, select, text, update
from sqlalchemy.orm import Session

from catalog_models import SCFCatalogControl
from models import AuditLog, CDMDocument, CDMMapping, ScopedControl

if TYPE_CHECKING:
    # Annotation-only: compute_mappings_v2 lazy-imports cdm_retrieval at call
    # time to avoid a circular import; this guard exists so the string
    # annotation resolves for type checkers and pyflakes (F821).
    from services import cdm_retrieval

_QUERY_TEXT_MAX_CHARS = 2000


logger = logging.getLogger(__name__)


_SYSTEM_ACTOR_FALLBACK = UUID("00000000-0000-0000-0000-000000000001")


def _get_system_actor_user_id() -> UUID:
    """Resolve the audit-log actor used for unattended writes (D-1, slice 6)."""
    raw = os.getenv("CDM_SYSTEM_ACTOR_USER_ID")
    if raw:
        try:
            return UUID(raw)
        except ValueError:
            pass
    return _SYSTEM_ACTOR_FALLBACK

# Matches the file_source LightRAG receives from tasks_cdm.ingest_cdm_document:
#   f"cdm-{document.id}.txt"
_FILE_SOURCE_DOC_ID_RE = re.compile(
    r"^cdm-([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.txt$"
)


@dataclass(frozen=True)
class ComputeMappingsSummary:
    """Return shape from compute_mappings_for_org / compute_mappings_v2.

    The #712 fields default to 0 so the v1 path (which has no gate, cap, or
    cutoff) and existing positional constructions keep working unchanged.
    Reconciliation invariant on the v2 path::

        hits_evaluated == mappings_created
                        + mappings_skipped_below_threshold
                        + mappings_skipped_duplicate
                        + mappings_skipped_unresolved_offset
                        + mappings_skipped_by_intent_gate
                        + mappings_skipped_by_cap
    """

    controls_processed: int
    hits_evaluated: int
    mappings_created: int
    mappings_skipped_below_threshold: int
    mappings_skipped_duplicate: int
    mappings_skipped_unresolved_offset: int
    mappings_skipped_by_intent_gate: int = 0
    mappings_skipped_by_cap: int = 0
    documents_excluded_awaiting_intent: int = 0
    documents_excluded_unclassified: int = 0


def _rank_derived_score(rank_index: int) -> float:
    """Score = 1.0 - 0.05 * rank_index, clamped to [0.0, 1.0]."""
    value = 1.0 - 0.05 * rank_index
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def _parse_doc_id_from_file_source(file_source: str) -> UUID | None:
    match = _FILE_SOURCE_DOC_ID_RE.match(file_source)
    if match is None:
        return None
    try:
        return UUID(match.group(1))
    except ValueError:
        return None


# Heading patterns for derive_section. Each pattern matches the START of a
# line. The capture group ``(title)`` carries the human label that's persisted
# to cdm_mappings.section. Order in this list is the tie-breaker priority
# when two patterns match on the same line — numbered headings are more
# specific than markdown, markdown is more specific than Section/Chapter.
_SECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(?P<num>\d+(?:\.\d+)+)\s+(?P<title>[^\r\n]{1,250})", re.MULTILINE),
    re.compile(r"^#{1,3}\s+(?P<title>[^\r\n]{1,250})", re.MULTILINE),
    re.compile(
        r"^(?P<kind>Section|Chapter)\s+\d+(?:[:.\)\s-]+(?P<title>[^\r\n]{1,250}))?",
        re.MULTILINE | re.IGNORECASE,
    ),
)

_SECTION_BACK_WINDOW_CHARS = 2000
_SECTION_MAX_LEN = 255


def _resolve_from_sections(
    sections: Sequence[Any],
    byte_offset_start: int,
) -> str | None:
    """Slice 13 — Docling-sections lookup by byte offset.

    Walk the sections list, collecting every section whose half-open
    range ``[byte_start, byte_end)`` contains ``byte_offset_start``.
    When two ranges nest (e.g. a section header sits inside a
    title-level scope), pick the deepest ``level`` — that is the most
    specific scope for the matched chunk. Within equal levels, the
    later (later-listed = textually-closer) section wins.
    """
    best_level = -1
    best_title: str | None = None
    for sec in sections:
        try:
            start = int(getattr(sec, "byte_start"))
            end = int(getattr(sec, "byte_end"))
            level = int(getattr(sec, "level", 1))
            title = str(getattr(sec, "title", "")).strip()
        except (AttributeError, TypeError, ValueError):
            continue
        if not title:
            continue
        if start <= byte_offset_start < end and level >= best_level:
            best_level = level
            best_title = title
    return best_title


class _SectionLike(Protocol):
    """Structural type for sections passed in by the Docling ingest path.

    The cdm_docling_service.Section dataclass satisfies this Protocol;
    tests can also pass dicts via SimpleNamespace or any object with these
    attributes. Kept loose so cdm_mapping does not have to import from
    cdm_docling_service (which pulls the docling dep on import).
    """

    level: int
    title: str
    byte_start: int
    byte_end: int


def derive_section(
    extracted_text: str,
    byte_offset_start: int,
    *,
    sections: Optional[Sequence[Any]] = None,
) -> str | None:
    """Return the nearest preceding heading label, or None.

    Two derivation paths, in priority order:

    1. **Docling sections (slice 13):** if ``sections`` is supplied (the
       Docling ingest path passes ``DoclingResult.sections``), find the
       section whose ``[byte_start, byte_end)`` range contains
       ``byte_offset_start``. When multiple ranges nest, the deepest
       ``level`` wins — that is the most specific scope for the chunk.

    2. **Regex fallback (slice 12):** when ``sections`` is None / empty
       (plain-text formats: .txt / .csv / .json / .yaml), backward-scan
       up to 2000 chars from ``byte_offset_start`` looking for the
       nearest heading (markdown H1-H3, numbered ``1.2.3 Title``, or
       ``Section N`` / ``Chapter N``).

    Tie-breaker on the regex path (same line): numbered > markdown >
    Section/Chapter. Across lines, the heading with the highest start
    position (closest preceding) wins regardless of pattern.
    """
    if sections:
        title = _resolve_from_sections(sections, byte_offset_start)
        if title is not None:
            cleaned = re.sub(r"\s+", " ", title).strip(" :.-")
            return cleaned[:_SECTION_MAX_LEN] if cleaned else None

    if not extracted_text or byte_offset_start <= 0:
        return None

    window_start = max(0, byte_offset_start - _SECTION_BACK_WINDOW_CHARS)
    window = extracted_text[window_start:byte_offset_start]
    if not window:
        return None

    best_start = -1
    best_title: str | None = None
    best_priority = len(_SECTION_PATTERNS)

    for priority, pattern in enumerate(_SECTION_PATTERNS):
        for match in pattern.finditer(window):
            start = match.start()
            title = (match.group("title") or "").strip()
            if not title:
                continue
            if start > best_start or (start == best_start and priority < best_priority):
                best_start = start
                best_title = title
                best_priority = priority

    if best_title is None:
        return None

    cleaned = re.sub(r"\s+", " ", best_title).strip(" :.-")
    if not cleaned:
        return None
    return cleaned[:_SECTION_MAX_LEN]


def _derive_query_text_for_control(
    control_name: str | None,
    control_description: str | None,
    control_question: str | None = None,
    required_artifact_types: list | None = None,
    objective_texts: list[str] | None = None,
) -> str | None:
    """Build a discriminating LightRAG query seed for one SCF control.

    Parts are ordered most-discriminating first so that truncation at
    ``_QUERY_TEXT_MAX_CHARS`` drops the generic ``control_description`` last.

    Order:
      1. control_name          — shortest, uniquely identifies the control
      2. control_question      — framing question specific to this control
      3. required_artifact_types — artifact type + description per entry
      4. objective_texts       — assessment objectives specific to this control
      5. control_description   — kept but placed last; often generic boilerplate
    """
    parts: list[str] = []

    if control_name and control_name.strip():
        parts.append(control_name.strip())

    if control_question and control_question.strip():
        parts.append(control_question.strip())

    # required_artifact_types is JSONB — guard against None, non-list, non-dict entries.
    if required_artifact_types is not None and isinstance(required_artifact_types, list):
        for entry in required_artifact_types:
            if not isinstance(entry, dict):
                continue
            artifact_type = entry.get("type")
            artifact_desc = entry.get("description")
            artifact_parts: list[str] = []
            if artifact_type and isinstance(artifact_type, str):
                artifact_parts.append(artifact_type.replace("_", " ").strip())
            if artifact_desc and isinstance(artifact_desc, str) and artifact_desc.strip():
                artifact_parts.append(artifact_desc.strip())
            if artifact_parts:
                parts.append(" ".join(artifact_parts))

    if objective_texts:
        for obj in objective_texts:
            if obj and isinstance(obj, str) and obj.strip():
                parts.append(obj.strip())

    if control_description and control_description.strip():
        parts.append(control_description.strip())

    if not parts:
        return None
    return ". ".join(parts)[:_QUERY_TEXT_MAX_CHARS]


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _best_citation_sentence(body: str, control_terms: frozenset[str]) -> str:
    """Pick the sentence in ``body`` that best answers the control.

    A whole chunk can be 1800 characters; citing all of it makes a reviewer
    hunt for the relevant line. Narrowing to the highest-overlap sentence
    produces a citation an auditor can read at a glance, while the chunk
    remains available as surrounding context.

    Falls back to the whole body when the chunk has no sentence structure —
    a table or a bullet list — rather than returning a fragment.
    """
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(body) if s.strip()]
    if len(sentences) <= 1:
        return body.strip()

    best = body.strip()
    best_hits = -1
    for sentence in sentences:
        if len(sentence) < 20:
            continue
        tokens = {t.lower() for t in re.findall(r"[A-Za-z0-9]+", sentence)}
        hits = sum(
            1 for term in control_terms
            if any(tok.startswith(term[:6]) or term.startswith(tok[:6]) for tok in tokens)
        )
        if hits > best_hits:
            best_hits = hits
            best = sentence
    return best


class DocumentIntentGate:
    """Narrows a control's candidate documents using model-claimed domains.

    THE INVARIANT on the default (provider-disabled) path:

        A document whose ``intent_status`` is anything other than ``classified``
        is **allowed**, not excluded. Missing intent yields ``None`` — no
        filtering at all, exactly v2 behaviour — and never ``set()``.

    A classification outage must degrade to "we propose as much as v2 did",
    never to "we found nothing", because the second is indistinguishable to the
    user from "your documents cover nothing". Classification will never arrive
    when no provider is configured, so fail-closed there would permanently
    disable mapping for self-hosted/keyless deployments.

    ``require_defined_intent=True`` (#712) inverts that for deployments where a
    provider IS enabled: there, classification is expected, so a document still
    ``pending`` or ``failed`` is **excluded from compute** — no proposals,
    rather than ungated proposals. Fail-open under an enabled provider turned a
    visible classification failure into 34,704 silently ungated proposals (92%
    off-domain); the awaiting-classification affordance in the UI is the honest
    signal, and this mode keeps it that way. Per-document eligibility in this
    mode:

    * ``pending`` / ``failed`` — excluded (counted, surfaced in the summary)
    * ``classified`` — participates, filtered by claimed domains (as today)
    * ``unclassified`` — **excluded** (counted separately). An abstention
      means the platform cannot say where the document belongs, so it
      proposes nowhere rather than everywhere. This reverses the original
      #712 allow: in practice one abstained SOP produced 2,516 cross-domain
      proposals (75% of a compute run). Re-classification is the recovery
      path; the exclusion is surfaced in the summary so it is never silent.
    * ``stale`` — participates using its existing (stale) intents: some gate
      beats no gate; revisit when re-classification-on-update ships
    * ``classified``/``stale`` with no surviving intent rows — excluded on
      this path (unplaceable is indistinguishable from an abstention here);
      still allowed-everywhere on the fail-open path below

    The org-level "zero classifications → no filtering" branch does not apply
    in this mode — per-document eligibility covers it, so an all-pending corpus
    yields zero proposals, not a flood.

    The gate only ever *filters* candidates. It never creates, scores or cites
    anything, and it makes no model call: one preload query per run, then pure
    set membership. A model call in the per-control path would be both a latency
    disaster and a route by which a model output could reach a mapping.
    """

    def __init__(
        self,
        session: Session,
        org_id: UUID,
        *,
        require_defined_intent: bool = False,
    ):
        rows = session.execute(
            text(
                "SELECT d.id AS document_id, d.intent_status, i.domain "
                "FROM cdm_documents d "
                "LEFT JOIN cdm_document_intents i ON i.cdm_document_id = d.id "
                "WHERE d.organization_id = :org_id"
            ),
            {"org_id": str(org_id)},
        ).fetchall()

        self._require_defined_intent = require_defined_intent
        self._by_domain: dict[str, set[UUID]] = {}
        # Documents that pass every domain's gate. On the fail-open path this
        # is every non-classified status; on the fail-closed path it is the
        # statuses whose lack of domains is a *defined* outcome.
        self._always_allowed: set[UUID] = set()
        # Fail-closed only: documents awaiting a definition that never came.
        self._excluded_awaiting_intent: set[UUID] = set()
        # Fail-closed only: abstentions and unplaceable documents.
        self._excluded_unclassified: set[UUID] = set()
        self._any_classified = False

        for row in rows:
            document_id = row.document_id
            status = row.intent_status
            if require_defined_intent and status in ("pending", "failed"):
                self._excluded_awaiting_intent.add(document_id)
                continue
            if require_defined_intent and status == "unclassified":
                self._excluded_unclassified.add(document_id)
                continue
            domain_scoped = status == "classified" or (
                require_defined_intent and status == "stale"
            )
            if not domain_scoped:
                self._always_allowed.add(document_id)
                continue
            if not row.domain:
                # Domain-scoped status with no surviving intent row. The task
                # cannot produce this state today, but the left join can (a
                # deleted intent row, a partial restore). Fail-open: a document
                # we cannot place is allowed everywhere, not excluded
                # everywhere. Fail-closed: unplaceable is an abstention, and
                # abstentions do not propose.
                if require_defined_intent:
                    self._excluded_unclassified.add(document_id)
                else:
                    self._always_allowed.add(document_id)
                continue
            if status == "classified":
                self._any_classified = True
            self._by_domain.setdefault(row.domain, set()).add(document_id)

    @property
    def documents_excluded_awaiting_intent(self) -> int:
        """How many documents fail-closed eligibility excluded (#712)."""
        return len(self._excluded_awaiting_intent)

    @property
    def documents_excluded_unclassified(self) -> int:
        """How many abstained/unplaceable documents fail-closed excluded."""
        return len(self._excluded_unclassified)

    def allowed_documents(self, domain: str | None) -> set[UUID] | None:
        """Documents permitted for ``domain``, or ``None`` for no filtering."""
        if self._require_defined_intent:
            if domain is None:
                # No domain to match against, but per-document eligibility
                # still applies: everything except awaiting-intent documents.
                eligible: set[UUID] = set(self._always_allowed)
                for members in self._by_domain.values():
                    eligible |= members
                return eligible
            return self._by_domain.get(domain, set()) | self._always_allowed
        if domain is None:
            return None
        if not self._any_classified:
            # Nothing in this org has been classified: filtering here would be
            # filtering on an absence.
            return None
        return self._by_domain.get(domain, set()) | self._always_allowed


def _domain_for_scf_id(scf_id: str | None) -> str | None:
    """Domain prefix of a control identifier.

    Derived from the identifier rather than read from
    ``scf_catalog_controls.scf_domain``, which is known-broken.
    """
    if not scf_id:
        return None
    prefix = scf_id.split("-", 1)[0].strip()
    return prefix or None


def compute_mappings_v2(
    session: Session,
    org_id: UUID,
    *,
    extracted_text_loader: Callable[[CDMDocument], str | None],
    backend: "cdm_retrieval.RetrievalBackend | None" = None,
    score_threshold: Optional[float] = None,
    top_k: Optional[int] = None,
    kb_revision: Optional[str] = None,
    objectives_loader: Optional[Callable[[Sequence[str]], dict[str, list[str]]]] = None,
    intent_gate: Optional[DocumentIntentGate] = None,
) -> ComputeMappingsSummary:
    """CDM v2 mapping computation — Postgres FTS discovery, verified provenance.

    Differences from v1 that matter:

    * Retrieval goes through a typed :class:`~services.cdm_retrieval.RetrievalBackend`
      whose rows carry character offsets. A backend that cannot supply them
      cannot satisfy the contract, and one whose ``can_produce_mappings`` is
      False is refused outright — that is the offset rule, enforced rather
      than documented.
    * The score is composed from observable components, and the components and
      weights are persisted alongside it.
    * Queries are built per assessment objective, so a mapping records *which*
      objective the passage answers.
    """
    from services import cdm_retrieval, cdm_scoring, cdm_verification

    resolved_backend = backend or cdm_retrieval.get_retrieval_backend()
    if not getattr(resolved_backend, "can_produce_mappings", False):
        raise ValueError(
            f"Retrieval backend {resolved_backend.name!r} cannot return verifiable "
            "offsets and must not be used on the mapping path (epic #709 HTV-2)"
        )

    weights = cdm_scoring.ScoreWeights.from_env()
    max_per_control = cdm_scoring.get_max_proposals_per_control()
    relative_cutoff = cdm_scoring.get_relative_score_cutoff()
    weights_json = weights.as_dict()
    # #712 provenance: the cap/cutoff active for this run ride along in the
    # persisted score_weights JSON, same rationale as the weights themselves —
    # a later tuning change must not leave historical rows uninterpretable.
    weights_json["max_proposals_per_control"] = max_per_control
    weights_json["relative_score_cutoff"] = relative_cutoff
    threshold = score_threshold if score_threshold is not None else cdm_scoring.get_score_threshold()
    limit = top_k if top_k is not None else cdm_scoring.get_top_k()
    revision = kb_revision if kb_revision is not None else get_kb_revision()

    # Suggestions are active-catalog-only (plan §4.4 consumer 8): no new
    # proposals against deprecated controls. The IS NULL arm keeps the
    # pre-existing outerjoin behavior for scoped rows with no catalog match.
    control_rows = session.execute(
        select(
            ScopedControl.id,
            SCFCatalogControl.scf_id,
            SCFCatalogControl.control_name,
            SCFCatalogControl.control_question,
        )
        .outerjoin(SCFCatalogControl, ScopedControl.scf_id == SCFCatalogControl.scf_id)
        .where(
            ScopedControl.organization_id == org_id,
            ScopedControl.selected.is_(True),
            or_(
                SCFCatalogControl.status.is_(None),
                SCFCatalogControl.status == "active",
            ),
        )
    ).all()

    scf_ids = [row[1] for row in control_rows if row[1] is not None]
    objectives_by_scf: dict[str, list[str]] = {}
    if objectives_loader is not None and scf_ids:
        objectives_by_scf = objectives_loader(scf_ids)

    controls_processed = 0
    hits_evaluated = 0
    mappings_created = 0
    skipped_below_threshold = 0
    skipped_duplicate = 0
    skipped_unresolved_offset = 0
    skipped_by_intent_gate = 0
    skipped_by_cap = 0

    doc_cache: dict[UUID, CDMDocument | None] = {}
    text_cache: dict[UUID, str | None] = {}

    for control_id, scf_id, control_name, control_question in control_rows:
        controls_processed += 1
        allowed_documents = (
            intent_gate.allowed_documents(_domain_for_scf_id(scf_id))
            if intent_gate is not None
            else None
        )
        objectives = tuple(objectives_by_scf.get(scf_id, []) if scf_id else ())
        query = cdm_retrieval.ControlQuery(
            scf_id=scf_id,
            control_name=control_name,
            control_question=control_question,
            objectives=objectives,
        )
        if not query.query_texts():
            continue

        try:
            rows, _total = resolved_backend.search(session, org_id, query, limit=limit)
        except Exception:
            logger.exception("CDM retrieval failed for control %s", control_id)
            continue

        control_terms = query.all_terms()

        # #712: the cap and relative cutoff need the control's full candidate
        # list before anything is emitted, so score into a buffer first and
        # flush the survivors after the loop.
        candidates: list[tuple[Any, Any]] = []

        for row in rows:
            hits_evaluated += 1

            # Filter before scoring: the gate is set membership, scoring is not.
            if allowed_documents is not None and row.cdm_document_id not in allowed_documents:
                skipped_by_intent_gate += 1
                continue

            coverage = cdm_retrieval.compute_objective_coverage(
                row.matched_objectives, objectives
            )
            overlap = cdm_retrieval.compute_term_overlap(row.body_norm, control_terms)
            components = cdm_scoring.compose_score(
                ts_rank=row.ts_rank,
                objective_coverage=coverage,
                term_overlap=overlap,
                weights=weights,
            )
            if components.score < threshold:
                skipped_below_threshold += 1
                continue

            candidates.append((components, row))

        if not candidates:
            continue

        # Stable sort by composed score: ties keep retrieval order. The best
        # hit always survives — the cutoff is a fraction (≤ 1.0) of its own
        # score and the cap is ≥ 1 by construction.
        candidates.sort(key=lambda item: item[0].score, reverse=True)
        cutoff_score = candidates[0][0].score * relative_cutoff
        kept: list[tuple[Any, Any]] = []
        for item in candidates:
            if len(kept) >= max_per_control or item[0].score < cutoff_score:
                skipped_by_cap += 1
                continue
            kept.append(item)

        for components, row in kept:
            if row.cdm_document_id not in doc_cache:
                doc_cache[row.cdm_document_id] = session.get(
                    CDMDocument, row.cdm_document_id
                )
            document = doc_cache[row.cdm_document_id]
            if document is None or document.organization_id != org_id:
                skipped_unresolved_offset += 1
                continue

            if row.cdm_document_id not in text_cache:
                try:
                    text_cache[row.cdm_document_id] = extracted_text_loader(document)
                except Exception:
                    logger.exception(
                        "Extracted-text loader failed for document %s",
                        row.cdm_document_id,
                    )
                    text_cache[row.cdm_document_id] = None
            extracted_text = text_cache[row.cdm_document_id]
            if extracted_text is None:
                skipped_unresolved_offset += 1
                continue

            # Tier 2. The chunk arrived from our own table, but its offsets are
            # only trustworthy if the extracted text still agrees with it —
            # re-locating proves that, and narrows the citation to the sentence
            # that actually answers the control.
            citation = _best_citation_sentence(row.body, control_terms)
            verified = cdm_verification.locate_phrase_in_document(
                extracted_text,
                row.char_start,
                row.body,
                citation,
            )
            if verified is None:
                skipped_unresolved_offset += 1
                continue

            existing = session.execute(
                select(CDMMapping.id).where(
                    CDMMapping.scoped_control_id == control_id,
                    CDMMapping.cdm_document_id == row.cdm_document_id,
                    CDMMapping.byte_offset_start == verified.char_start,
                )
            ).first()
            if existing is not None:
                session.execute(
                    update(CDMMapping)
                    .where(CDMMapping.id == existing[0])
                    .values(
                        excerpt=verified.matched_text,
                        section=row.heading or derive_section(
                            extracted_text, verified.char_start
                        ),
                    )
                )
                skipped_duplicate += 1
                continue

            session.add(
                CDMMapping(
                    organization_id=org_id,
                    scoped_control_id=control_id,
                    cdm_document_id=row.cdm_document_id,
                    byte_offset_start=verified.char_start,
                    byte_offset_end=verified.char_end,
                    relevance_score=components.score,
                    status="proposed",
                    kb_revision=revision,
                    excerpt=verified.matched_text,
                    section=row.heading or derive_section(extracted_text, verified.char_start),
                    ts_rank_component=components.ts_rank,
                    objective_coverage_component=components.objective_coverage,
                    term_overlap_component=components.term_overlap,
                    score_weights=weights_json,
                    match_type=verified.match_type.value,
                    matched_objective_text=(
                        row.matched_objectives[0] if row.matched_objectives else None
                    ),
                    cdm_document_chunk_id=row.chunk_id,
                    retrieval_tier=resolved_backend.name,
                )
            )
            mappings_created += 1

    if skipped_by_intent_gate:
        logger.info(
            "CDM intent gate filtered %d candidate hits for org %s",
            skipped_by_intent_gate,
            org_id,
        )

    session.commit()

    return ComputeMappingsSummary(
        controls_processed=controls_processed,
        hits_evaluated=hits_evaluated,
        mappings_created=mappings_created,
        mappings_skipped_below_threshold=skipped_below_threshold,
        mappings_skipped_duplicate=skipped_duplicate,
        mappings_skipped_unresolved_offset=skipped_unresolved_offset,
        mappings_skipped_by_intent_gate=skipped_by_intent_gate,
        mappings_skipped_by_cap=skipped_by_cap,
        documents_excluded_awaiting_intent=(
            intent_gate.documents_excluded_awaiting_intent
            if intent_gate is not None
            else 0
        ),
        documents_excluded_unclassified=(
            intent_gate.documents_excluded_unclassified
            if intent_gate is not None
            else 0
        ),
    )


def compute_mappings_for_org(
    session: Session,
    org_id: UUID,
    *,
    query_callable: Callable[[str, str, int], dict],
    extracted_text_loader: Callable[[CDMDocument], str | None],
    score_threshold: float,
    top_k: int,
    kb_revision: str,
    objectives_loader: Optional[Callable[[Sequence[str]], dict[str, list[str]]]] = None,
) -> ComputeMappingsSummary:
    """Iterate selected ScopedControls × LightRAG passage retrieval; persist proposed mappings.

    Legacy v1 path (no live callers outside tests). Does NOT run control-level
    consolidation — rows inserted here stay unlinked to cdm_control_proposals
    unless a v2 compute later runs for the same org.

    Parameters
    ----------
    session
        Sync SQLAlchemy session. Caller owns commit/rollback.
    org_id
        Tenant scope.
    query_callable
        Callable(query_text, workspace, top_k) -> {"hits": [{content, file_source, ...}, ...], "kb_revision": str}
        Matches the return shape of ``tasks_cdm.query_cdm`` (post-_build_query_hits).
    extracted_text_loader
        Callable(CDMDocument) -> extracted text str, or None if unresolvable.
        Production caller reads ``{object_key}.extracted.txt`` from cdm_storage.
    objectives_loader
        Optional Callable(list[scf_id]) -> {scf_id: [objective_text, ...]}. When
        provided, called once before the loop to enrich query seeds with
        assessment-objective text. Kept injectable so tests need no real DB.
    score_threshold
        Hits with derived score below this are skipped.
    top_k
        Max chunks requested per control from LightRAG.
    kb_revision
        Value stamped on every created CDMMapping.kb_revision.

    Returns
    -------
    ComputeMappingsSummary
    """
    # 1. Load every selected scoped control for the org, with catalog metadata
    #    for query-text derivation.
    # Same active-catalog-only rule as compute_mappings_v2 (plan §4.4
    # consumer 8): deprecated controls receive no new suggestions.
    control_rows = session.execute(
        select(
            ScopedControl.id,
            SCFCatalogControl.scf_id,
            SCFCatalogControl.control_name,
            SCFCatalogControl.control_description,
            SCFCatalogControl.control_question,
            SCFCatalogControl.required_artifact_types,
        )
        .outerjoin(SCFCatalogControl, ScopedControl.scf_id == SCFCatalogControl.scf_id)
        .where(
            ScopedControl.organization_id == org_id,
            ScopedControl.selected.is_(True),
            or_(
                SCFCatalogControl.status.is_(None),
                SCFCatalogControl.status == "active",
            ),
        )
    ).all()

    # 2. Bulk-fetch assessment objectives for all controls in one call.
    scf_ids = [row[1] for row in control_rows if row[1] is not None]
    objectives_by_scf: dict[str, list[str]] = {}
    if objectives_loader is not None and scf_ids:
        objectives_by_scf = objectives_loader(scf_ids)

    controls_processed = 0
    hits_evaluated = 0
    mappings_created = 0
    mappings_skipped_below_threshold = 0
    mappings_skipped_duplicate = 0
    mappings_skipped_unresolved_offset = 0

    # Per-org document + extracted-text caches keep us from re-reading storage
    # on every chunk of a multi-control batch.
    doc_cache: dict[UUID, CDMDocument | None] = {}
    extracted_cache: dict[UUID, str | None] = {}

    for control_id, scf_id, control_name, control_description, control_question, required_artifact_types in control_rows:
        controls_processed += 1
        query_text = _derive_query_text_for_control(
            control_name,
            control_description,
            control_question,
            required_artifact_types,
            objectives_by_scf.get(scf_id) if scf_id is not None else None,
        )
        if query_text is None:
            # Cannot query LightRAG without a meaningful text seed.
            continue

        try:
            response = query_callable(query_text, str(org_id), top_k)
        except Exception:
            logger.exception(
                "LightRAG query failed for control %s during compute_mappings", control_id
            )
            continue

        hits = response.get("hits") if isinstance(response, dict) else None
        if not isinstance(hits, list):
            continue

        for rank_index, hit in enumerate(hits):
            hits_evaluated += 1
            if not isinstance(hit, dict):
                continue

            score = _rank_derived_score(rank_index)
            if score < score_threshold:
                mappings_skipped_below_threshold += 1
                continue

            content = hit.get("content")
            file_source = hit.get("file_source") or hit.get("file_path")
            if not isinstance(content, str) or not isinstance(file_source, str):
                mappings_skipped_unresolved_offset += 1
                continue

            doc_id = _parse_doc_id_from_file_source(file_source)
            if doc_id is None:
                mappings_skipped_unresolved_offset += 1
                continue

            # Doc cache: confirm the document exists in this org.
            if doc_id not in doc_cache:
                doc_cache[doc_id] = session.get(CDMDocument, doc_id)
            document = doc_cache[doc_id]
            if document is None or document.organization_id != org_id:
                mappings_skipped_unresolved_offset += 1
                continue

            # Extracted-text cache for byte-offset computation.
            if doc_id not in extracted_cache:
                try:
                    extracted_cache[doc_id] = extracted_text_loader(document)
                except Exception:
                    logger.exception(
                        "Extracted-text loader failed for document %s", doc_id
                    )
                    extracted_cache[doc_id] = None
            extracted_text = extracted_cache[doc_id]
            if extracted_text is None:
                mappings_skipped_unresolved_offset += 1
                continue

            offset_start = extracted_text.find(content)
            if offset_start < 0:
                mappings_skipped_unresolved_offset += 1
                continue
            offset_end = offset_start + len(content)

            # D-5: dedup over (scoped_control_id, cdm_document_id, byte_offset_start).
            existing = session.execute(
                select(CDMMapping.id).where(
                    CDMMapping.scoped_control_id == control_id,
                    CDMMapping.cdm_document_id == doc_id,
                    CDMMapping.byte_offset_start == offset_start,
                )
            ).first()
            if existing is not None:
                # Slice 11: keep excerpt fresh on re-runs so re-indexed
                # documents replace stale chunk text. Status / score /
                # kb_revision have their own lifecycles (accept/dismiss
                # + stale detection) and stay untouched here.
                existing_id = existing[0]
                session.execute(
                    update(CDMMapping)
                    .where(CDMMapping.id == existing_id)
                    .values(
                        excerpt=content,
                        section=derive_section(extracted_text, offset_start),
                    )
                )
                mappings_skipped_duplicate += 1
                continue

            mapping = CDMMapping(
                organization_id=org_id,
                scoped_control_id=control_id,
                cdm_document_id=doc_id,
                byte_offset_start=offset_start,
                byte_offset_end=offset_end,
                relevance_score=score,
                status="proposed",
                kb_revision=kb_revision,
                excerpt=content,
                section=derive_section(extracted_text, offset_start),
            )
            session.add(mapping)
            mappings_created += 1

    session.commit()

    return ComputeMappingsSummary(
        controls_processed=controls_processed,
        hits_evaluated=hits_evaluated,
        mappings_created=mappings_created,
        mappings_skipped_below_threshold=mappings_skipped_below_threshold,
        mappings_skipped_duplicate=mappings_skipped_duplicate,
        mappings_skipped_unresolved_offset=mappings_skipped_unresolved_offset,
    )


def get_score_threshold() -> float:
    raw = os.getenv("CDM_MAPPING_SCORE_THRESHOLD", "0.5")
    try:
        return float(raw)
    except ValueError:
        return 0.5


def get_top_k() -> int:
    raw = os.getenv("CDM_MAPPING_TOP_K", "10")
    try:
        value = int(raw)
        if value < 1:
            return 10
        if value > 200:
            return 200
        return value
    except ValueError:
        return 10


def get_kb_revision() -> str:
    return os.getenv("CDM_KB_REVISION", "lightrag-v1")


# ───────────────────────────── Slice 6 ───────────────────────────────
# Stale-mapping detection on re-ingest. Single point of mutation — invoked
# by tasks_cdm.ingest_cdm_document after the successful indexing commit.
# ────────────────────────────────────────────────────────────────────


def detect_stale_mappings_for_document(
    session: Session,
    document_id: UUID,
    new_kb_revision: str,
    *,
    actor_user_id: Optional[UUID] = None,
) -> int:
    """Flip accepted mappings on a re-ingested document to status='stale'.

    Triggered when a CDMDocument re-ingest stamps a new ``kb_revision_at_ingest``.
    Only mappings that meet ALL of the following are flipped:
      - cdm_document_id == document_id
      - status == 'accepted'
      - kb_revision != new_kb_revision

    Proposed/dismissed/stale mappings, mappings on other documents, and
    already-current accepted mappings are untouched.

    For each flipped mapping, one ``AuditLog`` row is written with
    ``action='stale'``, ``field_name='status'``, ``old_value='accepted'``,
    ``new_value`` JSON carrying old/new kb_revision + timestamp.

    #722: accepted parent ``CDMControlProposal`` rows whose citations were
    flipped follow to 'stale' with their own audit rows, so the review card
    never reads "accepted" over invalidated evidence.

    Parameters
    ----------
    session
        Sync SQLAlchemy session (typically the ingest task's session). Caller
        is responsible for the outer commit; this helper does NOT commit, so
        an audit-write failure rolls back along with the surrounding ingest UoW.
    document_id
        Target document whose accepted mappings are candidates.
    new_kb_revision
        kb_revision the document was just (re-)indexed at. Any accepted
        mapping not at this revision is considered stale.
    actor_user_id
        Optional user UUID to attribute the audit rows to. When None
        (unattended ingest), falls back to the system actor sentinel.

    Returns
    -------
    int
        Number of mappings flipped to 'stale'.
    """
    actor = actor_user_id or _get_system_actor_user_id()

    candidate_rows = session.execute(
        select(
            CDMMapping.id,
            CDMMapping.organization_id,
            CDMMapping.kb_revision,
            CDMMapping.control_proposal_id,
        ).where(
            CDMMapping.cdm_document_id == document_id,
            CDMMapping.status == "accepted",
            CDMMapping.kb_revision != new_kb_revision,
        )
    ).all()

    if not candidate_rows:
        return 0

    now = datetime.now(timezone.utc)
    flipped = 0
    flipped_proposal_orgs: dict[UUID, UUID] = {}

    for mapping_id, mapping_org_id, old_kb_revision, proposal_id in candidate_rows:
        # Optimistic guard: only flip if still 'accepted' AND kb_revision still
        # mismatches. Prevents racing with a slice 5 dismissal on the same row.
        result = session.execute(
            update(CDMMapping)
            .where(
                CDMMapping.id == mapping_id,
                CDMMapping.status == "accepted",
                CDMMapping.kb_revision != new_kb_revision,
            )
            .values(status="stale")
        )
        if result.rowcount == 0:
            continue

        session.add(
            AuditLog(
                organization_id=mapping_org_id,
                entity_type="cdm_mapping",
                entity_id=mapping_id,
                action="stale",
                field_name="status",
                old_value="accepted",
                new_value=json.dumps(
                    {
                        "status": "stale",
                        "old_kb_revision": old_kb_revision,
                        "new_kb_revision": new_kb_revision,
                        "detected_at": now.isoformat(),
                    }
                ),
                changed_by_user_id=actor,
                action_source="system",
            )
        )
        flipped += 1
        if proposal_id is not None:
            flipped_proposal_orgs[proposal_id] = mapping_org_id

    # #722: an accepted parent proposal whose citation just went stale must
    # follow — otherwise the card says "accepted" over stale evidence. Same
    # optimistic pattern; guarded so the no-candidate path stays one statement.
    if flipped_proposal_orgs:
        from models import CDMControlProposal

        for flipped_proposal_id, proposal_org_id in flipped_proposal_orgs.items():
            result = session.execute(
                update(CDMControlProposal)
                .where(
                    CDMControlProposal.id == flipped_proposal_id,
                    CDMControlProposal.status == "accepted",
                )
                .values(status="stale", updated_at=now)
            )
            if result.rowcount == 0:
                continue
            session.add(
                AuditLog(
                    organization_id=proposal_org_id,
                    entity_type="cdm_control_proposal",
                    entity_id=flipped_proposal_id,
                    action="stale",
                    field_name="status",
                    old_value="accepted",
                    new_value=json.dumps(
                        {
                            "status": "stale",
                            "new_kb_revision": new_kb_revision,
                            "detected_at": now.isoformat(),
                        }
                    ),
                    changed_by_user_id=actor,
                    action_source="system",
                )
            )

    return flipped
