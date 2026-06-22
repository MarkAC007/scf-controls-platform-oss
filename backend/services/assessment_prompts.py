"""
Assessment Prompt Templates for Evidence AI Assessment.

Builds versioned prompts for evaluating evidence content against
control requirements. Includes control context assembly and
SHA-256 hashing for audit trail reproducibility.
"""
import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from catalog_models import SCFCatalogEvidence, SCFCatalogControl

logger = logging.getLogger(__name__)

# Prompt template version — increment when changing prompt structure
PROMPT_VERSION = "1.1.0"

# Output schema for structured AI response
ASSESSMENT_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "relevance_score": {
            "type": "number",
            "description": "0-100 score indicating how relevant this evidence is to the mapped controls",
        },
        "status": {
            "type": "string",
            "enum": ["sufficient", "partial", "insufficient"],
            "description": "Overall sufficiency determination",
        },
        "summary": {
            "type": "string",
            "description": "2-3 sentence summary of the assessment",
        },
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["relevance", "completeness", "quality", "error"],
                    },
                    "level": {
                        "type": "string",
                        "enum": ["sufficient", "partial", "insufficient", "info"],
                    },
                    "message": {"type": "string"},
                    "control_id": {"type": "string"},
                    "suggestion": {"type": "string"},
                },
                "required": ["category", "level", "message"],
            },
        },
    },
    "required": ["relevance_score", "status", "summary", "findings"],
}


@dataclass
class ControlContext:
    """Assembled control context for an evidence item."""
    evidence_id: str
    artifact_title: str
    artifact_description: str
    area_of_focus: str
    controls: List[Dict[str, str]]  # [{scf_id, control_name, control_description}]
    context_hash: str  # SHA-256 of the assembled context
    framework_version: str


async def assemble_control_context(
    db: AsyncSession,
    evidence_id: str,
) -> Optional[ControlContext]:
    """Assemble the control context for an evidence item.

    Looks up the evidence catalog entry and resolves mapped control
    descriptions. Returns None if the evidence ID is not in the catalog.
    """
    # Look up catalog entry
    result = await db.execute(
        select(SCFCatalogEvidence).where(
            SCFCatalogEvidence.evidence_id == evidence_id
        )
    )
    catalog_entry = result.scalar_one_or_none()

    if not catalog_entry:
        return None

    # Resolve control mappings
    control_ids = catalog_entry.control_mappings or []
    controls = []

    if control_ids:
        ctrl_result = await db.execute(
            select(SCFCatalogControl).where(
                SCFCatalogControl.scf_id.in_(control_ids)
            )
        )
        for ctrl in ctrl_result.scalars().all():
            controls.append({
                "scf_id": ctrl.scf_id,
                "control_name": ctrl.control_name,
                "control_description": ctrl.control_description or "",
            })

    # Build context hash (for cache invalidation and audit)
    context_data = {
        "evidence_id": evidence_id,
        "artifact_title": catalog_entry.artifact_title,
        "artifact_description": catalog_entry.artifact_description or "",
        "area_of_focus": catalog_entry.area_of_focus,
        "controls": controls,
        "catalog_version": catalog_entry.catalog_version or "",
        "prompt_version": PROMPT_VERSION,
    }
    context_json = json.dumps(context_data, sort_keys=True, default=str)
    context_hash = hashlib.sha256(context_json.encode()).hexdigest()

    return ControlContext(
        evidence_id=evidence_id,
        artifact_title=catalog_entry.artifact_title,
        artifact_description=catalog_entry.artifact_description or "",
        area_of_focus=catalog_entry.area_of_focus,
        controls=controls,
        context_hash=context_hash,
        framework_version=catalog_entry.catalog_version or "unknown",
    )


def assemble_control_context_sync(
    session,
    evidence_id: str,
) -> Optional[ControlContext]:
    """Sync variant of assemble_control_context for Celery tasks.

    Same logic as the async version but uses a sync SQLAlchemy session (psycopg2).
    """
    result = session.execute(
        select(SCFCatalogEvidence).where(
            SCFCatalogEvidence.evidence_id == evidence_id
        )
    )
    catalog_entry = result.scalar_one_or_none()

    if not catalog_entry:
        return None

    control_ids = catalog_entry.control_mappings or []
    controls = []

    if control_ids:
        ctrl_result = session.execute(
            select(SCFCatalogControl).where(
                SCFCatalogControl.scf_id.in_(control_ids)
            )
        )
        for ctrl in ctrl_result.scalars().all():
            controls.append({
                "scf_id": ctrl.scf_id,
                "control_name": ctrl.control_name,
                "control_description": ctrl.control_description or "",
            })

    context_data = {
        "evidence_id": evidence_id,
        "artifact_title": catalog_entry.artifact_title,
        "artifact_description": catalog_entry.artifact_description or "",
        "area_of_focus": catalog_entry.area_of_focus,
        "controls": controls,
        "catalog_version": catalog_entry.catalog_version or "",
        "prompt_version": PROMPT_VERSION,
    }
    context_json = json.dumps(context_data, sort_keys=True, default=str)
    context_hash = hashlib.sha256(context_json.encode()).hexdigest()

    return ControlContext(
        evidence_id=evidence_id,
        artifact_title=catalog_entry.artifact_title,
        artifact_description=catalog_entry.artifact_description or "",
        area_of_focus=catalog_entry.area_of_focus,
        controls=controls,
        context_hash=context_hash,
        framework_version=catalog_entry.catalog_version or "unknown",
    )


def build_assessment_prompt(
    control_context: ControlContext,
    extracted_text: str,
    filename: str,
    content_type: str,
    assessment_date: str = "",
) -> tuple[str, str]:
    """Build the full assessment prompt.

    Returns (system_prompt, user_prompt) tuple.
    Also returns the prompt hash for audit trail.
    """
    # Format control requirements
    controls_text = ""
    if control_context.controls:
        control_lines = []
        for ctrl in control_context.controls:
            control_lines.append(
                f"- **{ctrl['scf_id']}** ({ctrl['control_name']}): {ctrl['control_description']}"
            )
        controls_text = "\n".join(control_lines)
    else:
        controls_text = "No specific control mappings defined for this evidence item."

    date_line = f"\n\nToday's date is {assessment_date}. Evaluate all date references relative to this date." if assessment_date else ""

    system_prompt = f"""You are a GRC (Governance, Risk, Compliance) evidence assessor for the Secure Controls Framework (SCF).{date_line}

Your task is to evaluate whether uploaded evidence content actually demonstrates compliance with the control requirements it is mapped to.

You must:
1. Assess RELEVANCE — does this evidence relate to the control requirements?
2. Assess COMPLETENESS — does it cover all aspects of the requirements?
3. Assess QUALITY — is the evidence current, clear, and substantive?
4. Flag ERRORS — blank documents, placeholder text, wrong content, dates unreasonably old relative to today's date, redacted critical info

You are advisory only. Your assessment helps human reviewers prioritise their review. Be specific about what's present, what's missing, and what would improve the evidence.

Respond with valid JSON matching the required schema."""

    user_prompt = f"""Assess the following evidence file against its mapped control requirements.

## Evidence Item
- **Evidence ID:** {control_context.evidence_id}
- **Artifact Title:** {control_context.artifact_title}
- **Description:** {control_context.artifact_description}
- **Area of Focus:** {control_context.area_of_focus}
- **File:** {filename} ({content_type})
- **Assessment Date:** {assessment_date or "Not specified"}

## Mapped Control Requirements
{controls_text}

## Evidence Content
```
{extracted_text}
```

## Assessment Instructions
1. Score relevance 0-100 (how well does this content address the control requirements?)
2. Determine sufficiency: "sufficient" (evidence adequately demonstrates compliance), "partial" (some coverage but gaps), "insufficient" (does not demonstrate compliance)
3. Provide specific findings with categories (relevance, completeness, quality, error)
4. For each finding, suggest concrete improvement actions where applicable
5. Reference specific control IDs in findings where relevant

Respond with JSON only, matching this schema:
{json.dumps(ASSESSMENT_OUTPUT_SCHEMA, indent=2)}"""

    return system_prompt, user_prompt


def hash_prompt(system_prompt: str, user_prompt: str) -> str:
    """SHA-256 hash of the full prompt for audit trail."""
    combined = f"{system_prompt}\n---\n{user_prompt}"
    return hashlib.sha256(combined.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Windowed assessment (portfolio over a time window)
# ---------------------------------------------------------------------------

WINDOW_ASSESSMENT_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "relevance_score": {
            "type": "number",
            "description": "0-100 score of how well the PORTFOLIO of files in this window addresses the mapped controls",
        },
        "status": {
            "type": "string",
            "enum": ["sufficient", "partial", "insufficient"],
            "description": "Overall portfolio sufficiency. If the caller pre-computed insufficient_sample, that is authoritative and this value becomes advisory.",
        },
        "summary": {
            "type": "string",
            "description": "2-3 sentence summary of the portfolio assessment, explicitly noting any missing expected artifact types or source labels",
        },
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["relevance", "completeness", "quality", "coverage", "error"],
                    },
                    "level": {
                        "type": "string",
                        "enum": ["sufficient", "partial", "insufficient", "info"],
                    },
                    "message": {"type": "string"},
                    "control_id": {"type": "string"},
                    "artifact_type": {
                        "type": "string",
                        "description": "If the finding is about a missing or insufficient artifact type, name it here",
                    },
                    "suggestion": {"type": "string"},
                },
                "required": ["category", "level", "message"],
            },
        },
    },
    "required": ["relevance_score", "status", "summary", "findings"],
}


def build_window_assessment_prompt(
    control_context: ControlContext,
    window_start: str,
    window_end: str,
    frequency_used: str,
    files: list[dict],
    expected_artifact_types: list[dict],
    source_coverage: dict,
    artifact_type_coverage: dict,
    assessment_date: str = "",
) -> tuple[str, str]:
    """Build the windowed assessment prompt.

    Args:
        control_context: Assembled control context (same as per-file path).
        window_start / window_end: ISO-8601 timestamps bounding the window.
        frequency_used: Frequency string that drove the window size.
        files: List of {filename, content_type, source, uploaded_at, text}.
        expected_artifact_types: Union of required_artifact_types across mapped controls.
        source_coverage: {source_label: file_count} for files actually present.
        artifact_type_coverage: {artifact_type: {present: bool, file_count: int}}.
        assessment_date: Optional date string to anchor freshness reasoning.

    Returns (system_prompt, user_prompt).
    """
    # --- Control requirements block (reused shape from build_assessment_prompt) ---
    if control_context.controls:
        control_lines = [
            f"- **{c['scf_id']}** ({c['control_name']}): {c['control_description']}"
            for c in control_context.controls
        ]
        controls_text = "\n".join(control_lines)
    else:
        controls_text = "No specific control mappings defined for this evidence item."

    # --- Expected artifact types block ---
    if expected_artifact_types:
        atype_lines = []
        for a in expected_artifact_types:
            mand = "mandatory" if a.get("mandatory") else "optional"
            weight = a.get("weight", "medium")
            desc = a.get("description", "")
            atype_lines.append(f"- `{a['type']}` ({mand}, weight={weight}): {desc}")
        expected_text = "\n".join(atype_lines)
    else:
        expected_text = (
            "Not extracted for the mapped controls. Assess using the control "
            "descriptions alone; do not penalise for unknown expected types."
        )

    # --- Coverage tables ---
    present_sources = sorted(source_coverage.items(), key=lambda kv: kv[0])
    sources_text = (
        "\n".join(f"- {src}: {count} file(s)" for src, count in present_sources)
        if present_sources else "- (none)"
    )

    atype_coverage_lines = []
    for atype in expected_artifact_types:
        key = atype.get("type", "")
        cov = artifact_type_coverage.get(key, {})
        present = cov.get("present", False)
        count = cov.get("file_count", 0)
        marker = "PRESENT" if present else "MISSING"
        atype_coverage_lines.append(f"- {key}: {marker} ({count} file(s))")
    atype_coverage_text = "\n".join(atype_coverage_lines) if atype_coverage_lines else "- (no expected types)"

    # --- Files block ---
    file_blocks = []
    for i, f in enumerate(files, start=1):
        header = (
            f"### Artifact {i} — source={f.get('source', 'unknown')}, "
            f"uploaded_at={f.get('uploaded_at', '')}, "
            f"filename={f.get('filename', '')} ({f.get('content_type', '')})"
        )
        file_blocks.append(f"{header}\n```\n{f.get('text', '').strip()}\n```")
    files_text = "\n\n".join(file_blocks) if file_blocks else "_No files in window._"

    date_line = (
        f"\n\nToday's date is {assessment_date}. Evaluate freshness relative to this date."
        if assessment_date else ""
    )

    system_prompt = (
        f"You are a GRC (Governance, Risk, Compliance) evidence assessor for "
        f"the Secure Controls Framework (SCF).{date_line}\n\n"
        "You are assessing a PORTFOLIO of evidence files for one evidence "
        "item over a time window, against the controls it is mapped to. "
        "Score the set as a whole — not each file in isolation.\n\n"
        "When an expected artifact type is missing from the portfolio, treat "
        "it as a coverage gap (not a defect of the files present). When an "
        "expected source is absent, say so explicitly in findings. When files "
        "are present but thin, flag quality. Distinguish coverage gaps from "
        "content quality in your findings.\n\n"
        "You are advisory only. Respond with valid JSON matching the schema."
    )

    user_prompt = (
        f"## Evidence Item\n"
        f"- **Evidence ID:** {control_context.evidence_id}\n"
        f"- **Artifact Title:** {control_context.artifact_title}\n"
        f"- **Description:** {control_context.artifact_description}\n"
        f"- **Area of Focus:** {control_context.area_of_focus}\n\n"
        f"## Assessment Window\n"
        f"- **Start:** {window_start}\n"
        f"- **End:** {window_end}\n"
        f"- **Frequency driving window size:** {frequency_used}\n"
        f"- **File count in window:** {len(files)}\n\n"
        f"## Mapped Control Requirements\n{controls_text}\n\n"
        f"## Expected Artifact Types\n{expected_text}\n\n"
        f"## Source Coverage (what actually arrived)\n{sources_text}\n\n"
        f"## Artifact Type Coverage\n{atype_coverage_text}\n\n"
        f"## Files in Window\n{files_text}\n\n"
        f"## Assessment Instructions\n"
        f"1. Score the portfolio's overall relevance 0-100.\n"
        f"2. Determine sufficiency at the portfolio level.\n"
        f"3. For each finding, name the category, and reference a control_id or artifact_type where relevant.\n"
        f"4. Prefer category=coverage for missing expected types; category=completeness/quality for weaknesses within files present.\n"
        f"5. Include concrete suggestions (e.g. \"add a RestoreTest collector that ships quarterly\").\n\n"
        f"Respond with JSON only, matching this schema:\n"
        f"{json.dumps(WINDOW_ASSESSMENT_OUTPUT_SCHEMA, indent=2)}"
    )

    return system_prompt, user_prompt
