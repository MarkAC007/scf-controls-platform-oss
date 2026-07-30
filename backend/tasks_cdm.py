import hashlib
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Sequence
from uuid import UUID, uuid4

import httpx
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import sessionmaker

from celery_app import celery_app
from catalog_models import SCFCatalogAssessmentObjective
from models import (
    CDMControlProposal,
    CDMDocument,
    CDMDocumentChunk,
    CDMDocumentIntent,
    CDMMapping,
)
from services import (
    cdm_chunking,
    cdm_consolidation,
    cdm_docling_service,
    cdm_extraction,
    cdm_intent,
    cdm_mapping,
    cdm_retrieval,
    cdm_storage,
    text_extraction_service,
)
from services.cdm_docling_service import (
    DoclingExtractionError,
    DoclingResult,
    DoclingUnsupportedFormatError,
    Section,
)
from services.cdm_lightrag import get_lightrag_client, is_lightrag_enabled

logger = logging.getLogger(__name__)


class CDMQueryTimeoutError(RuntimeError):
    """LightRAG query exceeded the configured timeout."""


class CDMQueryUpstreamError(RuntimeError):
    """LightRAG query failed or returned an invalid payload."""

_SYNC_DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+asyncpg://odin:changeme@localhost:5432/odin_scf"
).replace("+asyncpg", "+psycopg2").replace("?ssl=require", "?sslmode=require")

_sync_engine = None
SyncSession = None


def _get_sync_session():
    global _sync_engine, SyncSession
    if SyncSession is None:
        _sync_engine = create_engine(_SYNC_DATABASE_URL, pool_pre_ping=True, pool_size=2, max_overflow=3)
        SyncSession = sessionmaker(bind=_sync_engine, expire_on_commit=False)
    return SyncSession()


def _load_objectives_for_controls(scf_ids: Sequence[str]) -> dict[str, list[str]]:
    """Fetch assessment-objective texts for a batch of SCF control IDs.

    Opens a fresh sync session, queries ``scf_catalog_assessment_objectives``
    for all rows whose ``scf_id`` is in *scf_ids*, and groups ``objective_text``
    values into a dict keyed by ``scf_id``. Returns an empty dict if
    *scf_ids* is empty. The session is always closed even on error.
    """
    if not scf_ids:
        return {}
    session = _get_sync_session()
    try:
        rows = session.execute(
            select(
                SCFCatalogAssessmentObjective.scf_id,
                SCFCatalogAssessmentObjective.objective_text,
            ).where(SCFCatalogAssessmentObjective.scf_id.in_(scf_ids))
        ).all()
        result: dict[str, list[str]] = defaultdict(list)
        for scf_id, objective_text in rows:
            if objective_text:
                result[scf_id].append(objective_text)
        return dict(result)
    finally:
        session.close()


def _normalise_extraction_content_type(mime_type: str) -> str:
    if mime_type == "text/markdown":
        return "text/plain"
    return mime_type


def _run_docling_extraction(
    *,
    payload: bytes,
    content_type: str,
    document: CDMDocument,
    object_key: str,
) -> tuple[str, int, str]:
    """Slice 13 — Docling branch of the CDM ingest extractor.

    Persists ``.docling.json`` (full Docling intermediate, enables re-chunking
    without re-OCR) and ``.extracted.md`` (markdown LightRAG indexes) alongside
    the raw payload. Returns ``(text_for_lightrag, word_count, file_source)``.

    Docling-internal exceptions bubble up as ``RuntimeError`` so the outer
    catch in ``ingest_cdm_document`` lands ``ingest_status='failed'`` with the
    same surface as the legacy extractor.
    """
    try:
        result: DoclingResult = cdm_docling_service.extract(
            payload, content_type, document.original_filename
        )
    except DoclingUnsupportedFormatError as exc:
        # Should not happen — is_docling_format gated this branch — but
        # surface as a clean failure if routing logic ever drifts.
        raise RuntimeError(f"Text extraction failed: {exc}") from exc
    except DoclingExtractionError as exc:
        raise RuntimeError(f"Text extraction failed: {exc}") from exc

    if not result.markdown.strip():
        raise RuntimeError("Text extraction produced no text")

    cdm_storage.write_cdm_payload(
        f"{object_key}.extracted.md",
        result.markdown.encode("utf-8"),
        str(document.organization_id),
    )

    # Persist the Docling intermediate JSON for offline re-chunking + audit.
    # Failure to write the intermediate is non-fatal: the markdown is what
    # downstream consumers depend on, and the absence of the intermediate
    # just means a future re-chunk needs to re-run Docling.
    try:
        intermediate_bytes = _json_dumps_bytes(result.intermediate_json)
        cdm_storage.write_cdm_payload(
            f"{object_key}.docling.json",
            intermediate_bytes,
            str(document.organization_id),
        )
    except Exception:
        logger.exception(
            "CDM: failed to persist .docling.json for %s (markdown still durable)",
            document.id,
        )

    return result.markdown, result.word_count, f"cdm-{document.id}.md"


def _run_text_extraction(
    *,
    payload: bytes,
    content_type: str,
    document: CDMDocument,
    object_key: str,
) -> tuple[str, int, str]:
    """Slice 13 — legacy text-extraction branch for plain-text formats
    (.txt / .csv / .json / .yaml). Preserved verbatim from slice 3.5b so
    the well-trodden path keeps identical semantics."""
    extracted = text_extraction_service.extract_text_from_bytes(
        payload,
        content_type,
        document.original_filename,
        max_length=None,
    )

    if extracted.error:
        raise RuntimeError(f"Text extraction failed: {extracted.error}")
    if not extracted.text.strip():
        raise RuntimeError("Text extraction produced no text")

    cdm_storage.write_cdm_payload(
        f"{object_key}.extracted.txt",
        extracted.text.encode("utf-8"),
        str(document.organization_id),
    )

    return extracted.text, extracted.word_count, f"cdm-{document.id}.txt"


def _json_dumps_bytes(data: dict) -> bytes:
    """Encode ``data`` as UTF-8 JSON bytes. Pulled out so tests can monkey-patch."""
    import json
    return json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")


def _persist_document_chunks(session, document: CDMDocument, extracted_text: str) -> int:
    """Replace a document's chunk set from ``extracted_text``. Returns the count.

    Delete-then-insert inside **one** transaction. Doing it across two commits
    would leave a window in which a concurrent mapping run sees zero chunks and
    reports "no coverage" — the one state that must never be faked, because a
    tenant cannot distinguish it from genuinely having no documentation.
    """
    session.execute(
        delete(CDMDocumentChunk).where(
            CDMDocumentChunk.cdm_document_id == document.id
        )
    )

    chunks = cdm_chunking.chunk_document_text(extracted_text)
    for chunk in chunks:
        session.add(
            CDMDocumentChunk(
                organization_id=document.organization_id,
                cdm_document_id=document.id,
                ordinal=chunk.ordinal,
                heading=chunk.heading,
                body=chunk.body,
                char_start=chunk.char_start,
                char_end=chunk.char_end,
                body_norm=chunk.body_norm,
            )
        )
    session.commit()
    return len(chunks)


def _persist_failed_status(session, document_id: UUID, error_message: str) -> None:
    try:
        session.rollback()
    except Exception:
        logger.exception("Failed to rollback CDM ingest session for %s", document_id)

    document = session.get(CDMDocument, document_id)
    if document is None:
        return

    document.ingest_status = "failed"
    document.ingest_error = error_message[:1000]
    session.commit()


def _build_query_hits(result: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    if not isinstance(result, dict):
        raise CDMQueryUpstreamError("LightRAG query returned a non-object payload")

    status = result.get("status")
    if not isinstance(status, str):
        raise CDMQueryUpstreamError("LightRAG query response missing string 'status'")

    data = result.get("data")
    if not isinstance(data, dict):
        raise CDMQueryUpstreamError("LightRAG query response missing object 'data'")

    metadata = result.get("metadata")
    if not isinstance(metadata, dict):
        raise CDMQueryUpstreamError("LightRAG query response missing object 'metadata'")

    query_mode = metadata.get("query_mode")
    if not isinstance(query_mode, str) or not query_mode:
        raise CDMQueryUpstreamError("LightRAG query response missing string 'metadata.query_mode'")

    references = data.get("references", [])
    if not isinstance(references, list):
        raise CDMQueryUpstreamError("LightRAG query response 'data.references' must be a list")

    chunks = data.get("chunks", [])
    if not isinstance(chunks, list):
        raise CDMQueryUpstreamError("LightRAG query response 'data.chunks' must be a list")

    reference_paths: dict[str, str] = {}
    for reference in references:
        if not isinstance(reference, dict):
            raise CDMQueryUpstreamError("LightRAG query reference entry must be an object")

        reference_id = reference.get("reference_id")
        if not isinstance(reference_id, str) or not reference_id:
            raise CDMQueryUpstreamError("LightRAG query reference missing string 'reference_id'")

        file_path = reference.get("file_path")
        if not isinstance(file_path, str) or not file_path:
            raise CDMQueryUpstreamError("LightRAG query reference missing string 'file_path'")

        reference_paths[reference_id] = file_path

    hits: list[dict[str, Any]] = []
    for chunk in chunks:
        if not isinstance(chunk, dict):
            raise CDMQueryUpstreamError("LightRAG query chunk entry must be an object")

        content = chunk.get("content")
        if not isinstance(content, str):
            raise CDMQueryUpstreamError("LightRAG query chunk missing string 'content'")

        reference_id = chunk.get("reference_id")
        if not isinstance(reference_id, str) or not reference_id:
            raise CDMQueryUpstreamError("LightRAG query chunk missing string 'reference_id'")

        chunk_id = chunk.get("chunk_id")
        if not isinstance(chunk_id, str) or not chunk_id:
            raise CDMQueryUpstreamError("LightRAG query chunk missing string 'chunk_id'")

        file_path = chunk.get("file_path")
        if not isinstance(file_path, str) or not file_path:
            file_path = reference_paths.get(reference_id, "")
        if not file_path:
            raise CDMQueryUpstreamError(
                f"LightRAG query chunk {chunk_id} missing resolvable file path"
            )

        hits.append(
            {
                "content": content,
                "chunk_id": chunk_id,
                "reference_id": reference_id,
                "file_path": file_path,
                "file_source": file_path,
            }
        )

    return hits, query_mode


@celery_app.task(name="cdm.ingest", queue="cdm", bind=True, autoretry_for=(), max_retries=0)
def ingest_cdm_document(self, document_id: str) -> dict:
    session = _get_sync_session()
    document_uuid: UUID | None = None

    try:
        document_uuid = UUID(document_id)
        document = session.get(CDMDocument, document_uuid)
        if document is None:
            # No row exists to transition to failed, so return a structured error.
            return {"document_id": document_id, "status": "failed", "error": "CDM document not found"}

        document.ingest_status = "parsing"
        document.ingest_error = None
        session.commit()

        object_key = cdm_storage.build_cdm_object_key(
            document.organization_id,
            document.id,
            document.original_filename,
        )
        payload = cdm_storage.download_cdm_payload(object_key)

        # ─── CDM v2 (#709 Part 4): extraction is pluggable, in-process by
        # default. v1 routed every binary format to a Docling sidecar that
        # docker-compose.yml does not ship, so PDF ingest failed outright on
        # the self-hosted stack. Docling stays available for table-structure
        # fidelity, but only when an operator opts in AND configures its URL.
        normalised_content_type = _normalise_extraction_content_type(document.mime_type)
        if cdm_extraction.should_use_docling(normalised_content_type):
            extracted_text, extracted_word_count, extracted_file_source = _run_docling_extraction(
                payload=payload,
                content_type=normalised_content_type,
                document=document,
                object_key=object_key,
            )
            extraction_backend = cdm_extraction.BACKEND_DOCLING
        else:
            extracted_text, extracted_word_count, extracted_file_source = _run_text_extraction(
                payload=payload,
                content_type=normalised_content_type,
                document=document,
                object_key=object_key,
            )
            extraction_backend = cdm_extraction.BACKEND_INPROCESS

        document.word_count = extracted_word_count
        document.extraction_backend = extraction_backend
        # Offsets index exactly this text; the hash is what lets a later
        # resolution detect extractor drift instead of citing a moved span.
        document.extracted_text_sha256 = hashlib.sha256(
            extracted_text.encode("utf-8")
        ).hexdigest()
        document.ingest_status = "parsed"
        document.ingest_error = None
        session.commit()

        # ─── CDM v2 (#709): chunking is a stored artefact ─────────────────
        # Persisted here, in the ingest unit of work, so a parsed document is
        # never left searchable-but-unchunked. Chunking failure is not fatal
        # to the ingest — the extracted text is durable and a re-chunk can be
        # run — but it is recorded rather than swallowed.
        try:
            chunks_written = _persist_document_chunks(
                session, document, extracted_text
            )
            logger.info(
                "CDM: persisted %d chunks for document %s", chunks_written, document_id
            )
        except Exception:
            logger.exception("CDM: chunk persistence failed for %s", document_id)
            try:
                session.rollback()
            except Exception:
                logger.exception("Rollback after chunk failure failed for %s", document_id)

        # ─── Slice 3.5b: LightRAG insert (D-3 partial-success) ──────────
        # Wired here so text-extraction success is durable in DB before any
        # LightRAG-side work begins. Insert failures are partial-success:
        # extracted text is preserved in storage; only KB indexing fails.
        # Operators retry via re-ingest (slice 5 audit/retry UI surfaces these).
        if is_lightrag_enabled():
            document.ingest_status = "indexing"
            session.commit()
            try:
                client = get_lightrag_client()
                client.insert(
                    text=extracted_text,
                    workspace=str(document.organization_id),
                    file_source=extracted_file_source,
                )
                document.ingest_status = "indexed"
                new_kb_revision = os.getenv("CDM_KB_REVISION", "lightrag-v1")
                document.kb_revision_at_ingest = new_kb_revision
                document.ingest_error = None
                session.commit()

                # Slice 6: detect stale accepted mappings whose kb_revision
                # predates this re-ingest. Single point of mutation; the
                # helper does the SELECT/UPDATE/AUDIT in the same session,
                # and we commit here so the audit rows land with the doc state.
                try:
                    flipped = cdm_mapping.detect_stale_mappings_for_document(
                        session,
                        document.id,
                        new_kb_revision,
                    )
                    if flipped:
                        session.commit()
                        logger.info(
                            "CDM stale-detection flipped %d mappings for doc %s",
                            flipped, document_id,
                        )
                except Exception:
                    # Stale detection failure must not regress the ingest result.
                    logger.exception(
                        "CDM stale-detection failed for doc %s (ingest still OK)",
                        document_id,
                    )
                    try:
                        session.rollback()
                    except Exception:
                        logger.exception(
                            "Rollback after stale-detection failure failed for %s",
                            document_id,
                        )
            except Exception as lightrag_exc:
                # D-3: do NOT re-raise. Record the failure and return success-with-degraded-state.
                logger.exception(
                    "LightRAG insert failed for CDM document %s", document_id
                )
                try:
                    session.rollback()
                except Exception:
                    logger.exception(
                        "Failed to rollback after LightRAG insert error for %s",
                        document_id,
                    )
                # Re-read after rollback in case the in-flight transaction
                # detached `document` from the session.
                document = session.get(CDMDocument, document_uuid)
                if document is not None:
                    document.ingest_status = "indexing_failed"
                    document.ingest_error = str(lightrag_exc)[:1000]
                    session.commit()

        # ─── Document map: intent classification ─────────────────────────
        # Dispatched, not called: document availability must never be hostage
        # to a hosted API's latency, and a chain would make a classification
        # failure present as an ingest failure. Every terminal document state
        # converges here — indexed, indexing_failed, and the LightRAG-disabled
        # path — and the precondition ("extracted text is durable") holds at
        # all three. Failure to enqueue is logged and swallowed for the same
        # reason: intent is an enhancement layered on ingest, never a gate.
        _dispatch_intent_classification(document_id)

        return {
            "document_id": document_id,
            "status": document.ingest_status if document is not None else "failed",
            "word_count": extracted_word_count,
            "extraction_method": "docling" if extracted_file_source.endswith(".md") else "text",
        }
    except Exception as exc:
        error_message = str(exc)[:1000]
        logger.exception("CDM ingest failed for %s: %s", document_id, exc)
        if document_uuid is not None:
            try:
                _persist_failed_status(session, document_uuid, error_message)
            except Exception:
                logger.exception("Failed to persist CDM ingest failure for %s", document_id)
        return {"document_id": document_id, "status": "failed", "error": error_message}
    finally:
        session.close()


def _dispatch_intent_classification(document_id: str) -> None:
    """Enqueue ``cdm.classify_intent`` unless intent classification is off.

    Skipping the dispatch when no provider is configured is the deliberate half
    of the disabled contract: nothing is queued, and the document's
    ``intent_status`` stays ``pending``. The task itself is a no-op under the
    same condition, so a direct invocation (the backfill script) reaches the
    same resting state.
    """
    try:
        if not cdm_intent.intent_classification_enabled():
            return
        classify_cdm_document_intent.delay(document_id)
    except Exception:
        logger.exception("CDM: failed to enqueue intent classification for %s", document_id)


def _persist_intent_failure(document_uuid: UUID, error_message: str) -> None:
    """Record a classification failure without touching ``ingest_status``.

    Its own session: the caller's may be poisoned by the failure that got us
    here, and the one thing this write must not do is fail silently.
    """
    session = _get_sync_session()
    try:
        document = session.get(CDMDocument, document_uuid)
        if document is None:
            return
        document.intent_status = "failed"
        document.intent_error = error_message[:1000]
        session.commit()
    except Exception:
        logger.exception("CDM: failed to persist intent failure for %s", document_uuid)
        try:
            session.rollback()
        except Exception:
            logger.exception("Rollback after intent-failure persistence failed for %s", document_uuid)
    finally:
        session.close()


@celery_app.task(
    name="cdm.classify_intent",
    queue="cdm_intent",
    bind=True,
    autoretry_for=(cdm_intent.IntentProviderTransientError,),
    max_retries=3,
    retry_backoff=True,
    retry_jitter=True,
)
def classify_cdm_document_intent(self, document_id: str, force: bool = False) -> dict:
    """Classify one document's authoritative domains.

    A dedicated queue, and a retry policy that deliberately differs from every
    other CDM task (``max_retries=0``): those fail on deterministic parse
    problems where retrying reproduces the failure, whereas this one's dominant
    failure mode is transient network or rate-limit trouble.

    ``ingest_status`` is never read as a precondition and never written. The
    intent lifecycle is parallel to the ingest lifecycle, and a document that
    failed to index is still worth classifying — it is searchable, mappable and
    visible in the map either way.
    """
    provider = cdm_intent.get_intent_provider()
    if provider is None:
        # Clean no-op: no rows, no status change, intent_status stays 'pending'.
        return {"document_id": document_id, "status": "disabled"}

    session = _get_sync_session()
    try:
        document_uuid = UUID(document_id)
        document = session.get(CDMDocument, document_uuid)
        if document is None:
            return {"document_id": document_id, "status": "failed", "error": "CDM document not found"}

        if document.intent_status == "classified" and not force:
            return {"document_id": document_id, "status": "skipped"}

        extracted_text = _load_extracted_text_for_document(document)
        if not extracted_text or not extracted_text.strip():
            document.intent_status = "failed"
            document.intent_error = "No extracted text available to classify"
            session.commit()
            return {"document_id": document_id, "status": "failed", "error": "no extracted text"}

        domains = cdm_intent.load_catalog_domains(session)
        if not domains:
            document.intent_status = "failed"
            document.intent_error = "SCF domain catalogue is empty"
            session.commit()
            return {"document_id": document_id, "status": "failed", "error": "empty domain catalogue"}

        classification = cdm_intent.classify_document_text(
            extracted_text, domains, provider=provider
        )

        # Re-classification is one transaction: the previous run's rows go, the
        # new run's rows land, and the status columns move with them. A partial
        # apply would leave the gate filtering on a mixture of two runs.
        session.execute(
            delete(CDMDocumentIntent).where(
                CDMDocumentIntent.cdm_document_id == document.id
            )
        )
        classification_id = uuid4()
        for rank, domain in enumerate(classification.domains, start=1):
            session.add(
                CDMDocumentIntent(
                    organization_id=document.organization_id,
                    cdm_document_id=document.id,
                    domain=domain,
                    rank=rank,
                    rationale=classification.rationale,
                    classification_id=classification_id,
                    prompt_version=classification.prompt_version,
                    provider=classification.provider,
                    model_id=classification.model_id,
                )
            )
        # An empty validated set means "authoritative for nothing in the
        # catalogue", which is not the same claim as "classified into zero
        # domains" and must not be recorded as one.
        document.intent_status = "classified" if classification.domains else "unclassified"
        document.intent_error = None
        document.intent_classified_at = datetime.now(timezone.utc)
        session.commit()

        return {
            "document_id": document_id,
            "status": document.intent_status,
            "domains": list(classification.domains),
        }
    except cdm_intent.IntentProviderTransientError as exc:
        try:
            session.rollback()
        except Exception:
            logger.exception("Rollback after transient intent error failed for %s", document_id)
        if self.request.retries < self.max_retries:
            # Let autoretry_for apply the configured backoff and jitter.
            raise
        logger.warning("CDM: intent classification exhausted retries for %s", document_id)
        _persist_intent_failure(UUID(document_id), str(exc))
        return {"document_id": document_id, "status": "failed", "error": str(exc)[:1000]}
    except Exception as exc:
        # Deterministic failures are not retried, and nothing here re-raises
        # into the ingest result — the two lifecycles stay independent.
        logger.exception("CDM: intent classification failed for %s", document_id)
        try:
            session.rollback()
        except Exception:
            logger.exception("Rollback after intent failure failed for %s", document_id)
        try:
            _persist_intent_failure(UUID(document_id), str(exc))
        except ValueError:
            logger.exception("CDM: malformed document id for intent classification: %s", document_id)
        return {"document_id": document_id, "status": "failed", "error": str(exc)[:1000]}
    finally:
        session.close()


@celery_app.task(name="cdm.query", queue="cdm", bind=True, autoretry_for=(), max_retries=0)
def query_cdm(self, query_text: str, workspace: str, limit: int) -> dict[str, Any]:
    """Run a LightRAG passage query against the per-org workspace.

    Wire contract:
    - Calls ``CDMLightRAGClient.query()``, which POSTs to LightRAG
      ``/query/data`` with body ``{"query": <text>, "mode": "hybrid", "top_k": <limit>}``.
    - Sends header ``LIGHTRAG-WORKSPACE: <sanitized workspace>`` so the
      upstream server can scope retrieval to one tenant workspace.
    - Expects ``QueryDataResponse`` JSON: top-level ``status``, ``message``,
      ``data``, ``metadata``; consumes ``data.chunks`` + ``data.references``
      and ``metadata.query_mode`` after validating those shapes.
    """
    del self

    if not is_lightrag_enabled():
        raise RuntimeError("LightRAG disabled — cdm.query task should not have been dispatched")

    client = get_lightrag_client()
    try:
        result = client.query(query_text, workspace=workspace, top_k=limit)
    except httpx.TimeoutException as exc:
        raise CDMQueryTimeoutError(str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise CDMQueryUpstreamError(
            f"LightRAG {exc.response.status_code}: {exc.response.text[:200]}"
        ) from exc

    hits, query_mode = _build_query_hits(result)
    return {
        "hits": hits,
        "kb_revision": os.getenv("CDM_KB_REVISION", "lightrag-v1"),
        "mode": query_mode,
    }


# ───────────────────────────── Slice 4 ───────────────────────────────
# cdm.compute_mappings: per-org batch — iterates selected ScopedControls
# × LightRAG passage retrieval, writes 'proposed' CDMMapping rows.
# Idempotency lock is held by the dispatcher endpoint and cleared in the
# task's `finally` block via a sync redis client (so we don't depend on
# Celery signals firing reliably under timeout/crash conditions).
# ────────────────────────────────────────────────────────────────────


_CDM_COMPUTE_LOCK_KEY_PREFIX = "cdm:compute_lock:"


def _get_sync_redis_client():
    """Sync redis client built off the broker URL.

    Used only inside the cdm.compute_mappings task to clear the per-org
    idempotency lock on success/failure. Returns ``None`` if the lock
    cannot be reached so a missing redis doesn't crash the task.
    """
    try:
        import redis  # type: ignore

        url = os.getenv("REDIS_URL", os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0"))
        return redis.Redis.from_url(url, socket_connect_timeout=3, socket_timeout=3)
    except Exception:
        logger.exception("Failed to build sync redis client for CDM compute lock")
        return None


def _load_extracted_text_for_document(document: CDMDocument) -> str | None:
    """Read the extracted-text artifact persisted by cdm.ingest.

    Both suffixes are tried. The in-process path writes ``.extracted.txt``
    and the Docling path writes ``.extracted.md``; v1 looked only for the
    former, so every Docling-ingested document failed offset resolution and
    silently produced no mappings at all.
    """
    object_key = cdm_storage.build_cdm_object_key(
        document.organization_id,
        document.id,
        document.original_filename,
    )
    for suffix in (".extracted.txt", ".extracted.md"):
        try:
            payload = cdm_storage.download_cdm_payload(f"{object_key}{suffix}")
        except FileNotFoundError:
            continue
        except Exception:
            logger.exception(
                "Failed to load extracted text for CDM document %s", document.id
            )
            return None
        return payload.decode("utf-8", errors="replace")
    return None


def _query_lightrag_for_compute(query_text: str, workspace: str, top_k: int) -> dict[str, Any]:
    """Query LightRAG and shape the result the way the helper expects."""
    client = get_lightrag_client()
    try:
        raw = client.query(query_text, workspace=workspace, top_k=top_k)
    except httpx.TimeoutException as exc:
        raise CDMQueryTimeoutError(str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise CDMQueryUpstreamError(
            f"LightRAG {exc.response.status_code}: {exc.response.text[:200]}"
        ) from exc

    hits, _query_mode = _build_query_hits(raw)
    return {
        "hits": hits,
        "kb_revision": os.getenv("CDM_KB_REVISION", "lightrag-v1"),
    }


@celery_app.task(name="cdm.backfill_chunks", queue="cdm", bind=True, autoretry_for=(), max_retries=0)
def backfill_chunks(self, org_id_str: str) -> dict[str, Any]:
    """Chunk documents ingested before CDM v2 existed.

    Without this, an org that uploaded everything under v1 has parsed
    documents and zero chunks, so Postgres FTS finds nothing and the review
    queue looks identical to having no documentation — the failure this epic
    exists to remove.

    Two deliberate constraints:

    * Accepted and dismissed mappings are never touched. A human decision is
      the most valuable data in this table and a maintenance job must not
      overwrite it. Only ``proposed`` rows, which no one has yet relied on,
      are cleared so the next compute run can rebuild them with components.
    * A document whose extracted text is gone is reported, not skipped
      silently. Its old mappings cite offsets into text nobody can produce,
      and an operator needs to know that rather than read an empty count as
      success.
    """
    del self
    session = _get_sync_session()
    summary: dict[str, Any] = {
        "org_id": org_id_str,
        "status": "ok",
        "documents_seen": 0,
        "documents_chunked": 0,
        "chunks_written": 0,
        "documents_missing_text": [],
        "proposed_mappings_cleared": 0,
        "proposed_proposals_cleared": 0,
    }

    try:
        org_id = UUID(org_id_str)
        documents = (
            session.execute(
                select(CDMDocument).where(
                    CDMDocument.organization_id == org_id,
                    CDMDocument.ingest_status == "parsed",
                )
            )
            .scalars()
            .all()
        )
        summary["documents_seen"] = len(documents)

        for document in documents:
            extracted_text = _load_extracted_text_for_document(document)
            if not extracted_text:
                summary["documents_missing_text"].append(str(document.id))
                continue
            try:
                written = _persist_document_chunks(session, document, extracted_text)
            except Exception:
                logger.exception("CDM backfill: chunking failed for %s", document.id)
                session.rollback()
                summary["documents_missing_text"].append(str(document.id))
                continue

            document.extracted_text_sha256 = hashlib.sha256(
                extracted_text.encode("utf-8")
            ).hexdigest()
            if document.extraction_backend is None:
                document.extraction_backend = cdm_extraction.BACKEND_INPROCESS
            session.commit()

            summary["documents_chunked"] += 1
            summary["chunks_written"] += written

        cleared = session.execute(
            delete(CDMMapping).where(
                CDMMapping.organization_id == org_id,
                CDMMapping.status == "proposed",
            )
        )
        summary["proposed_mappings_cleared"] = cleared.rowcount or 0
        # #722: same transaction — a proposed-status proposal can lose every
        # citation to the purge above (accepted ones keep accepted children).
        # Leaving zero-citation rows would force the next consolidation to
        # resurrect stale proposals against the unique constraint.
        cleared_proposals = session.execute(
            delete(CDMControlProposal).where(
                CDMControlProposal.organization_id == org_id,
                CDMControlProposal.status == "proposed",
            )
        )
        summary["proposed_proposals_cleared"] = cleared_proposals.rowcount or 0
        session.commit()

        if summary["documents_missing_text"]:
            summary["status"] = "partial"
    except Exception as exc:
        logger.exception("cdm.backfill_chunks failed for %s", org_id_str)
        try:
            session.rollback()
        except Exception:
            logger.exception("Rollback failed after backfill error for %s", org_id_str)
        summary["status"] = "failed"
        summary["error"] = str(exc)[:1000]
    finally:
        try:
            session.close()
        except Exception:
            logger.exception("Session close failed after backfill for %s", org_id_str)

    return summary


@celery_app.task(name="cdm.compute_mappings", queue="cdm", bind=True, autoretry_for=(), max_retries=0)
def compute_mappings(self, org_id_str: str) -> dict[str, Any]:
    """Batch-compute proposed CDM mappings for one org.

    Wire contract:
    - Resolves a :class:`~services.cdm_retrieval.RetrievalBackend` and calls
      :func:`services.cdm_mapping.compute_mappings_v2` with a sync session and
      an extracted-text loader closure.
    - Persists proposed mappings via that helper; helper handles commit.
    - Releases the per-org idempotency lock in ``finally`` so re-dispatch is
      possible immediately after this task settles (success or failure).

    v1 gated the whole task on ``is_lightrag_enabled()``, so on the
    self-hosted stack — where no LightRAG service exists — mapping silently
    never ran and the org saw an empty review queue indistinguishable from
    "your documents cover nothing". v2's default backend is Postgres, which is
    always present, so there is no configuration under which this task
    quietly does nothing.
    """
    del self
    session = _get_sync_session()
    summary_dict: dict[str, Any] = {
        "org_id": org_id_str,
        "status": "ok",
    }

    try:
        org_id = UUID(org_id_str)

        backend = cdm_retrieval.get_retrieval_backend()
        summary_dict["retrieval_backend"] = backend.name

        # One preload per run, consulted per control. Attached unconditionally
        # because the gate's default path fails open: with nothing classified
        # it returns None for every domain, which is exactly v2 behaviour.
        # Making attachment conditional would add a second code path that only
        # runs in the configuration nobody tests.
        #
        # #712: when a provider is enabled, classification is expected, so the
        # gate switches to fail-closed per-document eligibility — documents
        # still pending/failed produce no proposals rather than ungated ones.
        # With the provider disabled it will never arrive, so the fail-open
        # invariant stands there unchanged.
        intent_gate = cdm_mapping.DocumentIntentGate(
            session,
            org_id,
            require_defined_intent=cdm_intent.intent_classification_enabled(),
        )

        summary = cdm_mapping.compute_mappings_v2(
            session,
            org_id,
            extracted_text_loader=_load_extracted_text_for_document,
            backend=backend,
            objectives_loader=_load_objectives_for_controls,
            intent_gate=intent_gate,
        )
        summary_dict.update(
            controls_processed=summary.controls_processed,
            hits_evaluated=summary.hits_evaluated,
            mappings_created=summary.mappings_created,
            mappings_skipped_below_threshold=summary.mappings_skipped_below_threshold,
            mappings_skipped_duplicate=summary.mappings_skipped_duplicate,
            mappings_skipped_unresolved_offset=summary.mappings_skipped_unresolved_offset,
            mappings_skipped_by_intent_gate=summary.mappings_skipped_by_intent_gate,
            mappings_skipped_by_cap=summary.mappings_skipped_by_cap,
            documents_excluded_awaiting_intent=summary.documents_excluded_awaiting_intent,
            documents_excluded_unclassified=summary.documents_excluded_unclassified,
        )

        # #722: heuristic consolidation runs inline (no network, seconds) so
        # the queue shows one card per (control, document) immediately. The
        # LLM upgrade is a separate chained task — provider latency must
        # never push this task into its Celery time limits. Consolidation
        # failure is reported, never fatal: the mappings above are already
        # committed and valid on their own.
        try:
            consol = cdm_consolidation.consolidate_proposals(session, org_id)
            summary_dict.update(
                proposals_created=consol.proposals_created,
                proposals_updated=consol.proposals_updated,
                proposals_unchanged=consol.proposals_unchanged,
                proposals_resurrected=consol.proposals_resurrected,
                citations_linked=consol.citations_linked,
            )
            if cdm_intent.intent_classification_enabled():
                recompute_proposals.apply_async(
                    args=[org_id_str], queue="cdm", countdown=1
                )
                summary_dict["recompute_dispatched"] = True
        except Exception as consol_exc:
            logger.exception("CDM consolidation failed for %s", org_id_str)
            try:
                session.rollback()
            except Exception:
                logger.exception(
                    "Rollback failed after consolidation error for %s", org_id_str
                )
            summary_dict["consolidation_error"] = str(consol_exc)[:500]
    except Exception as exc:
        logger.exception("cdm.compute_mappings failed for %s", org_id_str)
        try:
            session.rollback()
        except Exception:
            logger.exception("Rollback failed after compute_mappings error for %s", org_id_str)
        summary_dict["status"] = "failed"
        summary_dict["error"] = str(exc)[:1000]
    finally:
        try:
            session.close()
        except Exception:
            logger.exception("Session close failed after compute_mappings for %s", org_id_str)

        # Clear per-org idempotency lock so a re-run can be dispatched.
        redis_client = _get_sync_redis_client()
        if redis_client is not None:
            try:
                redis_client.delete(f"{_CDM_COMPUTE_LOCK_KEY_PREFIX}{org_id_str}")
            except Exception:
                logger.exception(
                    "Failed to clear CDM compute lock for %s", org_id_str
                )

    return summary_dict


_CDM_RECOMPUTE_LOCK_KEY_PREFIX = "cdm:consolidate_lock:"
_CDM_RECOMPUTE_LOCK_TTL_S = 600


@celery_app.task(name="cdm.recompute_proposals", queue="cdm", bind=True, autoretry_for=(), max_retries=0)
def recompute_proposals(self, org_id_str: str) -> dict[str, Any]:
    """LLM upgrade pass over consolidated proposals (#722).

    Separate from ``cdm.compute_mappings`` on purpose: provider calls at up
    to ``CDM_CONSOLIDATION_TIMEOUT_S`` each would push the compute task into
    its Celery time limits (soft 540s / hard 600s). This task budgets its own
    wall clock (``CDM_CONSOLIDATION_BUDGET_S``, default 420s) and re-enqueues
    itself while unrecomputed groups remain, so an arbitrarily large corpus
    completes across runs instead of dying inside one.

    Its own redis lock (distinct from the compute lock) makes concurrent
    dispatch a no-op; the re-enqueue happens after the lock is released.
    """
    del self
    summary: dict[str, Any] = {"org_id": org_id_str, "status": "ok"}

    redis_client = _get_sync_redis_client()
    lock_key = f"{_CDM_RECOMPUTE_LOCK_KEY_PREFIX}{org_id_str}"
    if redis_client is not None:
        try:
            acquired = redis_client.set(
                lock_key, "1", nx=True, ex=_CDM_RECOMPUTE_LOCK_TTL_S
            )
        except Exception:
            logger.exception("CDM recompute lock check failed for %s", org_id_str)
            acquired = True
        if not acquired:
            summary["status"] = "skipped_locked"
            return summary

    session = _get_sync_session()
    requeue = False
    try:
        org_id = UUID(org_id_str)
        result = cdm_consolidation.recompute_proposals_llm(session, org_id)
        summary.update(
            proposals_recomputed=result.proposals_recomputed,
            recompute_failures=result.recompute_failures,
            proposals_remaining=result.proposals_remaining,
            budget_exhausted=result.budget_exhausted,
        )
        requeue = result.budget_exhausted and result.proposals_remaining > 0
    except Exception as exc:
        logger.exception("cdm.recompute_proposals failed for %s", org_id_str)
        try:
            session.rollback()
        except Exception:
            logger.exception(
                "Rollback failed after recompute_proposals error for %s", org_id_str
            )
        summary["status"] = "failed"
        summary["error"] = str(exc)[:1000]
    finally:
        try:
            session.close()
        except Exception:
            logger.exception(
                "Session close failed after recompute_proposals for %s", org_id_str
            )
        if redis_client is not None:
            try:
                redis_client.delete(lock_key)
            except Exception:
                logger.exception(
                    "Failed to clear CDM recompute lock for %s", org_id_str
                )

    if requeue:
        recompute_proposals.apply_async(args=[org_id_str], queue="cdm", countdown=5)
        summary["requeued"] = True

    return summary
