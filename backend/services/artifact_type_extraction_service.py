"""Artifact-type extraction service.

One-shot per-control extraction of the concrete artifact types that satisfy an
SCF control. Reads the control description, question, and mapped assessment
objectives, then asks Claude to enumerate the artifact types an evidence
portfolio for that control should contain.

The result is persisted on SCFCatalogControl.required_artifact_types as a JSONB
array. Downstream, the windowed evidence assessment engine unions these per
control mapped to an evidence_id to build `expected_artifact_types` for each
window.

Advisory only — the artifact types are suggestions that guide scoring, not
hard gates. Re-run when the catalog version changes or the extractor prompt
is materially revised.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from catalog_models import SCFCatalogControl, SCFCatalogAssessmentObjective

logger = logging.getLogger(__name__)

# Reuse the same Claude model used by per-file assessment so the platform has
# one LLM dependency. Not imported from ai_assessment_service to keep this
# module runnable from a CLI without full FastAPI app startup.
DEFAULT_MODEL = "claude-sonnet-4-20250514"
MAX_OUTPUT_TOKENS = 1024

INPUT_COST_PER_TOKEN = 3.0 / 1_000_000
OUTPUT_COST_PER_TOKEN = 15.0 / 1_000_000

ARTIFACT_TYPES_OUTPUT_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "description": "Short snake_case identifier, e.g. status_snapshot, integrity_test_result, recovery_objective_policy",
            },
            "weight": {
                "type": "string",
                "enum": ["high", "medium", "low"],
            },
            "mandatory": {
                "type": "boolean",
                "description": "Whether this artifact type is required to demonstrate the control",
            },
            "description": {
                "type": "string",
                "description": "One sentence describing what this artifact looks like",
            },
        },
        "required": ["type", "weight", "mandatory", "description"],
    },
}


class ExtractionResult:
    """Return value from extract_for_control."""

    def __init__(
        self,
        scf_id: str,
        artifact_types: list,
        model_id: str,
        input_tokens: int,
        output_tokens: int,
        cost_cents: float,
        error: Optional[str] = None,
    ):
        self.scf_id = scf_id
        self.artifact_types = artifact_types
        self.model_id = model_id
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cost_cents = cost_cents
        self.error = error


def _build_extraction_prompt(
    scf_id: str,
    control_name: str,
    control_description: str,
    control_question: Optional[str],
    objectives: list,
) -> tuple[str, str]:
    """Build the system + user prompts for artifact-type extraction."""
    objectives_text = ""
    if objectives:
        lines = []
        for obj in objectives:
            parts = [obj.get("objective_text", "").strip()]
            if obj.get("expected_results"):
                parts.append(f"Expected: {obj['expected_results'].strip()}")
            if obj.get("assessment_procedure"):
                parts.append(f"Procedure: {obj['assessment_procedure'].strip()}")
            if obj.get("asset_type"):
                parts.append(f"Asset type: {obj['asset_type']}")
            lines.append("- " + " | ".join(p for p in parts if p))
        objectives_text = "\n".join(lines)
    else:
        objectives_text = "None provided — derive artifact types from the control description alone."

    system_prompt = (
        "You are an expert GRC evidence analyst. Given an SCF control's "
        "description, assessment question, and assessment objectives, "
        "enumerate the concrete ARTIFACT TYPES an organisation must collect "
        "to demonstrate the control is implemented.\n\n"
        "Rules:\n"
        "1. Each artifact type must be a distinct KIND of evidence (a policy "
        "document is different from a configuration export, which is "
        "different from a test-result report).\n"
        "2. Use short snake_case identifiers (e.g. status_snapshot, "
        "integrity_test_result, recovery_objective_policy, "
        "access_review_report). Prefer specific over generic.\n"
        "3. Mark mandatory=true only when the control text implies the "
        "artifact is not optional. Otherwise mandatory=false.\n"
        "4. Weights: high = central to proving the control; medium = "
        "supporting evidence; low = nice-to-have context.\n"
        "5. Typically 2-5 artifact types per control. Only go above 5 if the "
        "control genuinely requires that much diversity.\n"
        "6. Respond with JSON only — an array of objects matching the "
        "required schema. No prose, no markdown."
    )

    user_prompt = (
        f"## Control\n"
        f"ID: {scf_id}\n"
        f"Name: {control_name}\n\n"
        f"## Description\n{control_description}\n\n"
        f"## Assessment Question\n{control_question or 'Not provided.'}\n\n"
        f"## Assessment Objectives\n{objectives_text}\n\n"
        f"## Output\n"
        f"Respond with a JSON array matching this schema:\n"
        f"{json.dumps(ARTIFACT_TYPES_OUTPUT_SCHEMA, indent=2)}"
    )

    return system_prompt, user_prompt


def _call_llm(system_prompt: str, user_prompt: str) -> Optional[dict]:
    """Call Claude for artifact-type extraction.

    Mirrors services.ai_assessment_service._call_llm so the extraction path
    has no FastAPI dependency and can run from a CLI script.
    """
    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic package not installed — cannot extract artifact types")
        return None

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set — cannot extract artifact types")
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
        logger.error("Claude API call failed during artifact-type extraction: %s", exc, exc_info=True)
        return None


def _parse_artifact_types(content: str) -> list:
    """Parse the LLM response into a list of artifact-type dicts.

    Strips markdown code fences if present. Returns [] on parse failure
    rather than raising — the caller records the error and continues.
    """
    body = content.strip()
    if body.startswith("```"):
        lines = body.split("\n")
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        body = "\n".join(lines).strip()

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        logger.warning("Failed to parse artifact-type response as JSON")
        return []

    if not isinstance(parsed, list):
        logger.warning("Artifact-type response was not a JSON array: %r", type(parsed))
        return []

    cleaned = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        t = entry.get("type")
        if not isinstance(t, str) or not t.strip():
            continue
        weight = entry.get("weight", "medium")
        if weight not in ("high", "medium", "low"):
            weight = "medium"
        cleaned.append({
            "type": t.strip(),
            "weight": weight,
            "mandatory": bool(entry.get("mandatory", False)),
            "description": str(entry.get("description", "")).strip(),
        })
    return cleaned


def extract_for_control(session: Session, scf_id: str, force: bool = False) -> ExtractionResult:
    """Extract required_artifact_types for a single control.

    Args:
        session: Synchronous SQLAlchemy session.
        scf_id: The SCF control ID (e.g. "BCD-11").
        force: If False, skip controls that already have a non-empty
            required_artifact_types populated.

    Returns:
        ExtractionResult describing what was written (or the error).
    """
    control = session.execute(
        select(SCFCatalogControl).where(SCFCatalogControl.scf_id == scf_id)
    ).scalar_one_or_none()

    if control is None:
        return ExtractionResult(
            scf_id=scf_id, artifact_types=[], model_id="", input_tokens=0,
            output_tokens=0, cost_cents=0.0, error=f"Control {scf_id} not found in catalog",
        )

    existing = control.required_artifact_types or []
    if existing and not force:
        logger.info("Skipping %s — already has %d artifact types (use --force to re-extract)", scf_id, len(existing))
        return ExtractionResult(
            scf_id=scf_id, artifact_types=existing, model_id="", input_tokens=0,
            output_tokens=0, cost_cents=0.0,
        )

    objectives = session.execute(
        select(SCFCatalogAssessmentObjective).where(
            SCFCatalogAssessmentObjective.scf_id == scf_id
        )
    ).scalars().all()

    objectives_payload = [
        {
            "objective_text": o.objective_text or "",
            "expected_results": o.expected_results or "",
            "assessment_procedure": o.assessment_procedure or "",
            "asset_type": o.asset_type or "",
        }
        for o in objectives
    ]

    system_prompt, user_prompt = _build_extraction_prompt(
        scf_id=control.scf_id,
        control_name=control.control_name,
        control_description=control.control_description,
        control_question=control.control_question,
        objectives=objectives_payload,
    )

    llm = _call_llm(system_prompt, user_prompt)
    if llm is None:
        return ExtractionResult(
            scf_id=scf_id, artifact_types=[], model_id="", input_tokens=0,
            output_tokens=0, cost_cents=0.0, error="LLM call failed",
        )

    artifact_types = _parse_artifact_types(llm["content"])

    input_tokens = llm.get("input_tokens", 0)
    output_tokens = llm.get("output_tokens", 0)
    cost_cents = round(
        (input_tokens * INPUT_COST_PER_TOKEN + output_tokens * OUTPUT_COST_PER_TOKEN) * 100,
        4,
    )

    session.execute(
        text(
            """
            UPDATE scf_catalog_controls
               SET required_artifact_types = :artifact_types,
                   required_artifact_types_extracted_at = :extracted_at
             WHERE scf_id = :scf_id
            """
        ),
        {
            "artifact_types": json.dumps(artifact_types),
            "extracted_at": datetime.utcnow(),
            "scf_id": scf_id,
        },
    )
    session.commit()

    logger.info(
        "Extracted %d artifact type(s) for %s (cost: %.4fc, model: %s)",
        len(artifact_types), scf_id, cost_cents, llm.get("model", DEFAULT_MODEL),
    )

    return ExtractionResult(
        scf_id=scf_id,
        artifact_types=artifact_types,
        model_id=llm.get("model", DEFAULT_MODEL),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_cents=cost_cents,
    )


def extract_batch(session: Session, scf_ids: list[str], force: bool = False) -> dict:
    """Extract artifact types for a list of controls.

    Returns a summary dict with per-control results + aggregate totals.
    """
    results = {"controls": [], "total_cost_cents": 0.0, "errors": 0, "skipped": 0, "extracted": 0}

    for idx, scf_id in enumerate(scf_ids, start=1):
        logger.info("[%d/%d] extracting %s", idx, len(scf_ids), scf_id)
        start = time.monotonic()
        try:
            r = extract_for_control(session, scf_id, force=force)
        except Exception as exc:
            logger.error("Unhandled error extracting %s: %s", scf_id, exc, exc_info=True)
            results["errors"] += 1
            results["controls"].append({
                "scf_id": scf_id, "status": "error", "message": str(exc)[:300],
            })
            continue

        elapsed_ms = int((time.monotonic() - start) * 1000)
        if r.error:
            results["errors"] += 1
            status = "error"
        elif r.cost_cents == 0 and not force and r.artifact_types:
            results["skipped"] += 1
            status = "skipped"
        else:
            results["extracted"] += 1
            status = "extracted"

        results["total_cost_cents"] += r.cost_cents
        results["controls"].append({
            "scf_id": r.scf_id,
            "status": status,
            "artifact_type_count": len(r.artifact_types),
            "cost_cents": r.cost_cents,
            "elapsed_ms": elapsed_ms,
            "error": r.error,
        })

    results["total_cost_cents"] = round(results["total_cost_cents"], 4)
    return results
