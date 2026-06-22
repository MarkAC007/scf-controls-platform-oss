import logging
import os
from collections import defaultdict
from typing import Any, Sequence
from uuid import UUID

import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from celery_app import celery_app
from catalog_models import SCFCatalogAssessmentObjective
from models import CDMDocument
from services import cdm_docling_service, cdm_mapping, cdm_storage, text_extraction_service
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

        # ─── Slice 13 (Epic #615 D-6): content-type-aware extraction ──────
        # Binary formats (PDF / DOCX / image-PDF) go through Docling — that
        # gives us hierarchical sections + markdown + page-level OCR data
        # for auditor-grade provenance. Plain-text formats stay on the
        # legacy text_extraction_service (no value-add from Docling, and
        # Docling refuses them explicitly).
        normalised_content_type = _normalise_extraction_content_type(document.mime_type)
        if cdm_docling_service.is_docling_format(normalised_content_type):
            extracted_text, extracted_word_count, extracted_file_source = _run_docling_extraction(
                payload=payload,
                content_type=normalised_content_type,
                document=document,
                object_key=object_key,
            )
        else:
            extracted_text, extracted_word_count, extracted_file_source = _run_text_extraction(
                payload=payload,
                content_type=normalised_content_type,
                document=document,
                object_key=object_key,
            )

        document.word_count = extracted_word_count
        document.ingest_status = "parsed"
        document.ingest_error = None
        session.commit()

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
    """Read the extracted-text artifact persisted by cdm.ingest."""
    try:
        object_key = cdm_storage.build_cdm_object_key(
            document.organization_id,
            document.id,
            document.original_filename,
        )
        extracted_key = f"{object_key}.extracted.txt"
        payload = cdm_storage.download_cdm_payload(extracted_key)
        return payload.decode("utf-8", errors="replace")
    except FileNotFoundError:
        return None
    except Exception:
        logger.exception(
            "Failed to load extracted text for CDM document %s", document.id
        )
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


@celery_app.task(name="cdm.compute_mappings", queue="cdm", bind=True, autoretry_for=(), max_retries=0)
def compute_mappings(self, org_id_str: str) -> dict[str, Any]:
    """Batch-compute proposed CDM mappings for one org.

    Wire contract:
    - Calls :func:`services.cdm_mapping.compute_mappings_for_org` with a sync
      session, a LightRAG query closure, and an extracted-text loader closure.
    - Persists proposed mappings via that helper; helper handles commit.
    - Releases the per-org idempotency lock in ``finally`` so re-dispatch is
      possible immediately after this task settles (success or failure).
    """
    del self
    session = _get_sync_session()
    summary_dict: dict[str, Any] = {
        "org_id": org_id_str,
        "status": "ok",
    }

    try:
        org_id = UUID(org_id_str)

        if not is_lightrag_enabled():
            summary_dict["status"] = "skipped"
            summary_dict["reason"] = "LightRAG disabled"
            return summary_dict

        summary = cdm_mapping.compute_mappings_for_org(
            session,
            org_id,
            query_callable=_query_lightrag_for_compute,
            extracted_text_loader=_load_extracted_text_for_document,
            score_threshold=cdm_mapping.get_score_threshold(),
            top_k=cdm_mapping.get_top_k(),
            kb_revision=cdm_mapping.get_kb_revision(),
            objectives_loader=_load_objectives_for_controls,
        )
        summary_dict.update(
            controls_processed=summary.controls_processed,
            hits_evaluated=summary.hits_evaluated,
            mappings_created=summary.mappings_created,
            mappings_skipped_below_threshold=summary.mappings_skipped_below_threshold,
            mappings_skipped_duplicate=summary.mappings_skipped_duplicate,
            mappings_skipped_unresolved_offset=summary.mappings_skipped_unresolved_offset,
        )
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
