"""CDM mapping computation — pure helper consumed by the cdm.compute_mappings task.

The helper is testable without Celery, Redis, or HTTPX live calls; callers inject
a sync SQLAlchemy ``Session``, a LightRAG query callable, and an extracted-text
resolver. The Celery task in ``tasks_cdm.py`` is a thin wrapper that wires the
production session + real ``CDMLightRAGClient`` + real ``cdm_storage`` reader.

D-1: scores are rank-derived (``1.0 - 0.05*rank``) because LightRAG's
``/query/data`` does not return per-chunk scores.

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
from typing import Any, Callable, Iterable, Optional, Protocol, Sequence
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from catalog_models import SCFCatalogControl
from models import AuditLog, CDMDocument, CDMMapping, ScopedControl

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
    """Return shape from compute_mappings_for_org."""

    controls_processed: int
    hits_evaluated: int
    mappings_created: int
    mappings_skipped_below_threshold: int
    mappings_skipped_duplicate: int
    mappings_skipped_unresolved_offset: int


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

    for mapping_id, mapping_org_id, old_kb_revision in candidate_rows:
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

    return flipped
