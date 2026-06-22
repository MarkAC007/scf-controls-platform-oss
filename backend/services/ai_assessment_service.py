"""
AI Evidence Assessment Service.

Core service for evaluating evidence content against control requirements
using Claude LLM. Runs asynchronously, stores structured findings with
full audit trail. Advisory only — never auto-approves or rejects.

Entry point: assess_evidence()
"""
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from models import EvidenceFile, EvidenceAssessment
from services.text_extraction_service import (
    extract_text_from_bytes,
    download_evidence_bytes,
)
from services.assessment_prompts import (
    assemble_control_context,
    build_assessment_prompt,
    hash_prompt,
    PROMPT_VERSION,
)

logger = logging.getLogger(__name__)

# Model configuration
DEFAULT_MODEL = "claude-sonnet-4-20250514"
MAX_OUTPUT_TOKENS = 2048

# Cost per token (Sonnet pricing as of 2025-05 — $3/M input, $15/M output)
INPUT_COST_PER_TOKEN = 3.0 / 1_000_000
OUTPUT_COST_PER_TOKEN = 15.0 / 1_000_000


async def assess_evidence(
    db: AsyncSession,
    evidence_file: EvidenceFile,
    requested_by_user_id: Optional[UUID] = None,
    assessment_source: str = "on_demand",
) -> EvidenceAssessment:
    """Run AI assessment on an evidence file.

    This function NEVER raises — all errors are caught and stored
    as status="error" in the assessment record.

    If an assessment already exists for this file, it is updated (upsert).
    If the file content hash + control context hash match a previous
    assessment, the cached result is returned.

    Args:
        db: Database session
        evidence_file: The EvidenceFile to assess
        requested_by_user_id: User who triggered the assessment
        assessment_source: "on_demand", "auto", or "bulk"

    Returns:
        EvidenceAssessment record (persisted to DB)
    """
    start_time = time.monotonic()

    # Upsert: check for existing assessment
    existing_result = await db.execute(
        select(EvidenceAssessment).where(
            EvidenceAssessment.evidence_file_id == evidence_file.id
        )
    )
    assessment = existing_result.scalar_one_or_none()

    if not assessment:
        assessment = EvidenceAssessment(
            evidence_file_id=evidence_file.id,
            organization_id=evidence_file.organization_id,
            evidence_id=evidence_file.evidence_id,
            status="processing",
            assessment_source=assessment_source,
            requested_by_user_id=requested_by_user_id,
        )
        db.add(assessment)
    else:
        assessment.status = "processing"
        assessment.assessment_source = assessment_source
        assessment.requested_by_user_id = requested_by_user_id

    await db.flush()

    try:
        # Step 1: Assemble control context
        control_context = await assemble_control_context(db, evidence_file.evidence_id)

        if not control_context:
            _set_error(assessment, start_time, f"Evidence ID '{evidence_file.evidence_id}' not found in catalog — cannot assess")
            await db.flush()
            return assessment

        # Step 2: Check cache — same content + context = skip reassessment
        if (
            assessment.control_context_hash == control_context.context_hash
            and evidence_file.sha256_hash
            and assessment.prompt_hash  # has been assessed before
            and assessment.status not in ("pending", "processing", "error")
        ):
            logger.info(
                "Assessment cache hit for file %s — content and context unchanged",
                evidence_file.id,
            )
            return assessment

        # Step 3: Download and extract text
        file_bytes = download_evidence_bytes(evidence_file.s3_key)
        if file_bytes is None:
            _set_error(assessment, start_time, f"Could not download evidence file from storage: {evidence_file.s3_key}")
            await db.flush()
            return assessment

        extracted = extract_text_from_bytes(
            data=file_bytes,
            content_type=evidence_file.content_type,
            filename=evidence_file.filename,
        )

        if extracted.is_empty and extracted.error:
            # Can't extract text — record the limitation
            assessment.status = "error"
            assessment.findings = [{
                "category": "error",
                "level": "info",
                "message": extracted.error,
            }]
            assessment.summary = f"Cannot assess: {extracted.error}"
            assessment.processing_time_ms = int((time.monotonic() - start_time) * 1000)
            assessment.assessed_at = datetime.utcnow()
            await db.flush()
            return assessment

        if extracted.is_empty:
            # Empty content — flag it
            assessment.status = "insufficient"
            assessment.relevance_score = 0
            assessment.findings = [{
                "category": "error",
                "level": "insufficient",
                "message": "Evidence file contains no readable content (blank or empty document)",
                "suggestion": "Upload a document with substantive content demonstrating compliance",
            }]
            assessment.summary = "Evidence file is empty or contains no extractable text."
            assessment.processing_time_ms = int((time.monotonic() - start_time) * 1000)
            assessment.assessed_at = datetime.utcnow()
            await db.flush()
            return assessment

        # Step 4: Build prompt
        system_prompt, user_prompt = build_assessment_prompt(
            control_context=control_context,
            extracted_text=extracted.text,
            filename=evidence_file.filename,
            content_type=evidence_file.content_type,
            assessment_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        )
        prompt_hash_value = hash_prompt(system_prompt, user_prompt)

        # Log the assembled prompt for monitoring via Azure Application Insights
        # These log records are auto-forwarded to App Insights when configured
        logger.info(
            "AI assessment prompt assembled",
            extra={
                "custom_dimensions": {
                    "event_type": "ai_assessment_prompt",
                    "evidence_file_id": str(evidence_file.id),
                    "evidence_id": evidence_file.evidence_id,
                    "organization_id": str(evidence_file.organization_id),
                    "filename": evidence_file.filename,
                    "content_type": evidence_file.content_type,
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

        # Step 5: Call LLM
        llm_result = await _call_llm(system_prompt, user_prompt)

        if llm_result is None:
            _set_error(assessment, start_time, "LLM call failed — AI assessment unavailable")
            assessment.prompt_hash = prompt_hash_value
            assessment.control_context_hash = control_context.context_hash
            await db.flush()
            return assessment

        # Step 6: Parse and store result
        parsed = _parse_llm_response(llm_result["content"])

        assessment.status = parsed.get("status", "error")
        assessment.relevance_score = parsed.get("relevance_score")
        assessment.findings = parsed.get("findings", [])
        assessment.summary = parsed.get("summary", "")

        # Audit trail
        assessment.model_id = llm_result.get("model", DEFAULT_MODEL)
        assessment.prompt_hash = prompt_hash_value
        assessment.control_context_hash = control_context.context_hash
        assessment.framework_version = control_context.framework_version
        assessment.input_token_count = llm_result.get("input_tokens", 0)
        assessment.output_token_count = llm_result.get("output_tokens", 0)

        # Cost calculation
        input_cost = (llm_result.get("input_tokens", 0)) * INPUT_COST_PER_TOKEN
        output_cost = (llm_result.get("output_tokens", 0)) * OUTPUT_COST_PER_TOKEN
        assessment.cost_cents = round((input_cost + output_cost) * 100, 4)

        assessment.processing_time_ms = int((time.monotonic() - start_time) * 1000)
        assessment.assessed_at = datetime.utcnow()

        await db.flush()

        logger.info(
            "AI assessment complete: file=%s, status=%s, score=%s, cost=%.4f cents, time=%dms",
            evidence_file.id,
            assessment.status,
            assessment.relevance_score,
            assessment.cost_cents or 0,
            assessment.processing_time_ms,
            extra={
                "custom_dimensions": {
                    "event_type": "ai_assessment_result",
                    "evidence_file_id": str(evidence_file.id),
                    "evidence_id": evidence_file.evidence_id,
                    "organization_id": str(evidence_file.organization_id),
                    "status": assessment.status,
                    "relevance_score": assessment.relevance_score,
                    "finding_count": len(assessment.findings) if assessment.findings else 0,
                    "input_tokens": assessment.input_token_count,
                    "output_tokens": assessment.output_token_count,
                    "cost_cents": assessment.cost_cents,
                    "processing_time_ms": assessment.processing_time_ms,
                    "model_id": assessment.model_id,
                    "prompt_hash": prompt_hash_value,
                }
            },
        )

        return assessment

    except Exception as exc:
        logger.error(
            "Assessment engine error for file %s: %s",
            evidence_file.id, exc, exc_info=True,
        )
        _set_error(assessment, start_time, f"Assessment engine error: {str(exc)[:500]}")
        await db.flush()
        return assessment


def _set_error(assessment: EvidenceAssessment, start_time: float, message: str):
    """Set assessment to error state with timing."""
    assessment.status = "error"
    assessment.findings = [{"category": "error", "level": "info", "message": message}]
    assessment.summary = message
    assessment.processing_time_ms = int((time.monotonic() - start_time) * 1000)
    assessment.assessed_at = datetime.utcnow()


async def _call_llm(system_prompt: str, user_prompt: str) -> Optional[dict]:
    """Call Claude API for evidence assessment.

    Returns dict with {content, model, input_tokens, output_tokens}
    or None on failure.
    """
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
    """Parse the LLM JSON response into assessment fields.

    Handles both clean JSON and JSON wrapped in markdown code blocks.
    Returns a safe default on parse failure.
    """
    text = content.strip()

    # Strip markdown code block wrapper if present
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first line (```json) and last line (```)
        if lines[-1].strip() == "```":
            lines = lines[1:-1]
        elif lines[0].strip().startswith("```"):
            lines = lines[1:]
        text = "\n".join(lines).strip()

    try:
        parsed = json.loads(text)

        # Validate required fields
        if "status" not in parsed:
            parsed["status"] = "error"
        if parsed["status"] not in ("sufficient", "partial", "insufficient"):
            parsed["status"] = "partial"  # Default to partial if unexpected value

        # Clamp relevance score
        score = parsed.get("relevance_score")
        if score is not None:
            parsed["relevance_score"] = max(0, min(100, float(score)))

        # Ensure findings is a list
        if not isinstance(parsed.get("findings"), list):
            parsed["findings"] = []

        return parsed

    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Failed to parse LLM assessment response: %s", exc)
        return {
            "status": "error",
            "relevance_score": None,
            "summary": f"AI response could not be parsed: {str(exc)[:200]}",
            "findings": [{
                "category": "error",
                "level": "info",
                "message": f"LLM response was not valid JSON: {content[:200]}",
            }],
        }
