"""
Celery tasks for AI evidence assessment.

Runs evidence content assessment against mapped control requirements
using Claude LLM. Executes in Celery workers (separate from web server)
to avoid blocking the uvicorn event loop.

Follows conventions from tasks_research.py and tasks_dpsia.py.
"""
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from celery import shared_task
from sqlalchemy import create_engine, select, and_, text
from sqlalchemy.orm import sessionmaker

from services.assessment_prompts import (
    assemble_control_context_sync,
    build_assessment_prompt,
    hash_prompt,
    ASSESSMENT_OUTPUT_SCHEMA,
    PROMPT_VERSION,
)
from services.text_extraction_service import (
    extract_text_from_bytes,
    download_evidence_bytes,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model configuration (mirrored from ai_assessment_service.py)
# ---------------------------------------------------------------------------

DEFAULT_MODEL = "claude-sonnet-4-20250514"
MAX_OUTPUT_TOKENS = 2048
INPUT_COST_PER_TOKEN = 3.0 / 1_000_000
OUTPUT_COST_PER_TOKEN = 15.0 / 1_000_000

# ---------------------------------------------------------------------------
# Sync DB session (psycopg2 pattern from tasks_research.py)
# ---------------------------------------------------------------------------

_SYNC_DATABASE_URL = (
    os.getenv("DATABASE_URL", "postgresql+asyncpg://cg:cg@localhost:5432/cg_scf")
    .replace("+asyncpg", "+psycopg2")
    .replace("?ssl=require", "?sslmode=require")
)

_sync_engine = None
SyncSession = None


def _get_sync_session():
    global _sync_engine, SyncSession
    if SyncSession is None:
        _sync_engine = create_engine(
            _SYNC_DATABASE_URL,
            pool_pre_ping=True,
            pool_size=2,
            max_overflow=3,
        )
        SyncSession = sessionmaker(bind=_sync_engine, expire_on_commit=False)
    return SyncSession()


# ---------------------------------------------------------------------------
# LLM call (sync — runs naturally in Celery worker)
# ---------------------------------------------------------------------------

def _call_llm(system_prompt: str, user_prompt: str) -> Optional[dict]:
    """Call Claude API for evidence assessment (sync)."""
    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic package not installed — cannot run AI assessment")
        return None

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set — cannot run AI assessment")
        return None

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=[{
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": user_prompt}],
        )
        return {
            "content": message.content[0].text,
            "model": message.model,
            "input_tokens": message.usage.input_tokens,
            "output_tokens": message.usage.output_tokens,
        }
    except Exception as exc:
        logger.error("Claude API call failed: %s", exc, exc_info=True)
        return None


def _parse_llm_response(content: str) -> dict:
    """Parse JSON response from LLM, stripping markdown fences if present."""
    text = content.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:])
        if text.endswith("```"):
            text = text[:-3].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("Failed to parse LLM response as JSON: %s", exc)
        return {
            "status": "error",
            "relevance_score": 0,
            "summary": "AI assessment failed — could not parse model response",
            "findings": [{
                "category": "error",
                "level": "insufficient",
                "message": f"Response parsing error: {exc}",
            }],
        }


# ---------------------------------------------------------------------------
# Celery task: single evidence file assessment
# ---------------------------------------------------------------------------

@shared_task(
    bind=True,
    name="tasks_assessment.assess_evidence_task",
    time_limit=360,
    soft_time_limit=300,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 2},
)
def assess_evidence_task(
    self,
    evidence_file_id: str,
    organization_id: str,
    requested_by_user_id: str,
    assessment_source: str,
) -> Dict[str, Any]:
    """Assess a single evidence file via Claude API.

    Runs in Celery worker — does not block the web server.
    """
    task_id = self.request.id
    start_time = time.monotonic()
    logger.info(
        "assess_evidence_task[%s] starting for file=%s org=%s",
        task_id, evidence_file_id, organization_id,
    )

    session = _get_sync_session()
    try:
        # Step 1: Fetch the evidence file record
        result = session.execute(
            text("""
                SELECT id, evidence_id, organization_id, filename, content_type, s3_key
                FROM evidence_files
                WHERE id = :file_id AND organization_id = :org_id AND is_deleted = false
            """),
            {"file_id": evidence_file_id, "org_id": organization_id},
        )
        row = result.mappings().first()
        if not row:
            logger.error("assess_evidence_task[%s] file not found: %s", task_id, evidence_file_id)
            return {"status": "error", "message": "Evidence file not found"}

        evidence_id = row["evidence_id"]
        filename = row["filename"]
        content_type = row["content_type"]
        s3_key = row["s3_key"]

        # Step 2: Update assessment status to "processing"
        session.execute(
            text("""
                UPDATE evidence_assessments
                SET status = 'processing'
                WHERE evidence_file_id = :file_id AND organization_id = :org_id
            """),
            {"file_id": evidence_file_id, "org_id": organization_id},
        )
        session.commit()

        # Step 3: Assemble control context
        control_context = assemble_control_context_sync(session, evidence_id)
        if not control_context:
            _update_assessment_error(
                session, evidence_file_id, organization_id, start_time,
                "No catalog entry found for evidence ID — cannot assess without control context",
            )
            return {"status": "error", "message": "No control context"}

        # Step 4: Download and extract text
        file_bytes = download_evidence_bytes(s3_key)
        if file_bytes is None:
            _update_assessment_error(
                session, evidence_file_id, organization_id, start_time,
                "Failed to download evidence file from storage",
            )
            return {"status": "error", "message": "Download failed"}

        extracted = extract_text_from_bytes(file_bytes, content_type, filename)

        if extracted.is_empty:
            _update_assessment_result(
                session, evidence_file_id, organization_id, start_time,
                status="insufficient",
                relevance_score=0,
                findings=[{
                    "category": "error",
                    "level": "insufficient",
                    "message": "Evidence file contains no readable content",
                    "suggestion": "Upload a document with substantive content",
                }],
                summary="Evidence file is empty or contains no extractable text.",
            )
            return {"status": "insufficient", "message": "Empty file"}

        # Step 5: Build prompt
        assessment_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        system_prompt, user_prompt = build_assessment_prompt(
            control_context=control_context,
            extracted_text=extracted.text,
            filename=filename,
            content_type=content_type,
            assessment_date=assessment_date,
        )
        prompt_hash_value = hash_prompt(system_prompt, user_prompt)

        # Step 6: Log prompt for App Insights monitoring
        logger.info(
            "AI assessment prompt assembled",
            extra={
                "custom_dimensions": {
                    "event_type": "ai_assessment_prompt",
                    "evidence_file_id": evidence_file_id,
                    "evidence_id": evidence_id,
                    "organization_id": organization_id,
                    "filename": filename,
                    "content_type": content_type,
                    "prompt_hash": prompt_hash_value,
                    "control_context_hash": control_context.context_hash,
                    "framework_version": control_context.framework_version,
                    "control_count": len(control_context.controls),
                    "extracted_text_length": len(extracted.text),
                    "model_id": DEFAULT_MODEL,
                    "system_prompt": system_prompt,
                    "user_prompt": user_prompt,
                }
            },
        )

        # Step 7: Call LLM
        llm_result = _call_llm(system_prompt, user_prompt)

        if llm_result is None:
            _update_assessment_error(
                session, evidence_file_id, organization_id, start_time,
                "LLM call failed — AI assessment unavailable",
                prompt_hash=prompt_hash_value,
                control_context_hash=control_context.context_hash,
            )
            return {"status": "error", "message": "LLM call failed"}

        # Step 8: Parse and store result
        parsed = _parse_llm_response(llm_result["content"])

        input_cost = llm_result.get("input_tokens", 0) * INPUT_COST_PER_TOKEN
        output_cost = llm_result.get("output_tokens", 0) * OUTPUT_COST_PER_TOKEN
        cost_cents = round((input_cost + output_cost) * 100, 4)
        processing_time_ms = int((time.monotonic() - start_time) * 1000)

        session.execute(
            text("""
                UPDATE evidence_assessments SET
                    status = :status,
                    relevance_score = :relevance_score,
                    findings = :findings,
                    summary = :summary,
                    model_id = :model_id,
                    prompt_hash = :prompt_hash,
                    control_context_hash = :control_context_hash,
                    framework_version = :framework_version,
                    input_token_count = :input_tokens,
                    output_token_count = :output_tokens,
                    cost_cents = :cost_cents,
                    processing_time_ms = :processing_time_ms,
                    assessed_at = :assessed_at
                WHERE evidence_file_id = :file_id AND organization_id = :org_id
            """),
            {
                "status": parsed.get("status", "error"),
                "relevance_score": parsed.get("relevance_score"),
                "findings": json.dumps(parsed.get("findings", []), default=str),
                "summary": parsed.get("summary", ""),
                "model_id": llm_result.get("model", DEFAULT_MODEL),
                "prompt_hash": prompt_hash_value,
                "control_context_hash": control_context.context_hash,
                "framework_version": control_context.framework_version,
                "input_tokens": llm_result.get("input_tokens", 0),
                "output_tokens": llm_result.get("output_tokens", 0),
                "cost_cents": cost_cents,
                "processing_time_ms": processing_time_ms,
                "assessed_at": datetime.utcnow(),
                "file_id": evidence_file_id,
                "org_id": organization_id,
            },
        )
        session.commit()

        # Step 9: Log result for App Insights
        logger.info(
            "AI assessment complete: file=%s, status=%s, score=%s, cost=%.4f cents, time=%dms",
            evidence_file_id,
            parsed.get("status", "error"),
            parsed.get("relevance_score"),
            cost_cents,
            processing_time_ms,
            extra={
                "custom_dimensions": {
                    "event_type": "ai_assessment_result",
                    "evidence_file_id": evidence_file_id,
                    "evidence_id": evidence_id,
                    "organization_id": organization_id,
                    "status": parsed.get("status", "error"),
                    "relevance_score": parsed.get("relevance_score"),
                    "finding_count": len(parsed.get("findings", [])),
                    "input_tokens": llm_result.get("input_tokens", 0),
                    "output_tokens": llm_result.get("output_tokens", 0),
                    "cost_cents": cost_cents,
                    "processing_time_ms": processing_time_ms,
                    "model_id": llm_result.get("model", DEFAULT_MODEL),
                    "prompt_hash": prompt_hash_value,
                }
            },
        )

        return {
            "status": parsed.get("status", "error"),
            "relevance_score": parsed.get("relevance_score"),
            "processing_time_ms": processing_time_ms,
        }

    except Exception as exc:
        logger.error("assess_evidence_task[%s] failed: %s", task_id, exc, exc_info=True)
        try:
            _update_assessment_error(
                session, evidence_file_id, organization_id, start_time,
                f"Assessment failed: {exc}",
            )
        except Exception:
            pass
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _update_assessment_error(
    session,
    evidence_file_id: str,
    organization_id: str,
    start_time: float,
    error_message: str,
    prompt_hash: Optional[str] = None,
    control_context_hash: Optional[str] = None,
):
    """Update assessment record with error status."""
    processing_time_ms = int((time.monotonic() - start_time) * 1000)
    session.execute(
        text("""
            UPDATE evidence_assessments SET
                status = 'error',
                summary = :summary,
                findings = :findings,
                processing_time_ms = :processing_time_ms,
                assessed_at = :assessed_at,
                prompt_hash = COALESCE(:prompt_hash, prompt_hash),
                control_context_hash = COALESCE(:control_context_hash, control_context_hash)
            WHERE evidence_file_id = :file_id AND organization_id = :org_id
        """),
        {
            "summary": error_message,
            "findings": json.dumps([{
                "category": "error",
                "level": "insufficient",
                "message": error_message,
            }]),
            "processing_time_ms": processing_time_ms,
            "assessed_at": datetime.utcnow(),
            "prompt_hash": prompt_hash,
            "control_context_hash": control_context_hash,
            "file_id": evidence_file_id,
            "org_id": organization_id,
        },
    )
    session.commit()


def _update_assessment_result(
    session,
    evidence_file_id: str,
    organization_id: str,
    start_time: float,
    status: str,
    relevance_score: float,
    findings: list,
    summary: str,
):
    """Update assessment record with a result (no LLM call)."""
    processing_time_ms = int((time.monotonic() - start_time) * 1000)
    session.execute(
        text("""
            UPDATE evidence_assessments SET
                status = :status,
                relevance_score = :relevance_score,
                findings = :findings,
                summary = :summary,
                processing_time_ms = :processing_time_ms,
                assessed_at = :assessed_at
            WHERE evidence_file_id = :file_id AND organization_id = :org_id
        """),
        {
            "status": status,
            "relevance_score": relevance_score,
            "findings": json.dumps(findings, default=str),
            "summary": summary,
            "processing_time_ms": processing_time_ms,
            "assessed_at": datetime.utcnow(),
            "file_id": evidence_file_id,
            "org_id": organization_id,
        },
    )
    session.commit()
