"""
Assessment Prompt Templates for Evidence AI Assessment.

Builds versioned prompts for evaluating evidence content against
control requirements. Includes control context assembly and
SHA-256 hashing for audit trail reproducibility.
"""
import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from catalog_models import (
    SCFCatalogAssessmentObjective,
    SCFCatalogEvidence,
    SCFCatalogControl,
)

logger = logging.getLogger(__name__)

# Prompt template version — increment when changing prompt structure.
# 2.0.0 is the AO-grounded rewrite (#881): the model now answers per SCF
# assessment objective rather than producing one opinion about the file, and
# extracts the evidence's own effective date. Major bump because the output
# contract changed shape — a 1.x verdict cannot be read as a 2.x one, and the
# version is how a reader tells them apart.
PROMPT_VERSION = "2.0.0"

# How many assessment objectives may enter one prompt. Mapped controls can
# carry a long tail of AOs, and past roughly this many the objective list
# crowds out the evidence itself — the model reads less of the document to
# answer more questions about it, which is the wrong trade. Objectives are
# taken in ao_id order so the cut is deterministic and the context hash stays
# stable; the caller discloses the cap in the findings.
MAX_ASSESSMENT_OBJECTIVES = 60

# The advisory vocabulary. These four words are the product contract: they are
# deliberately NOT the CAP assessor's terms (satisfied / other-than-satisfied),
# because this platform advises a preparer and must never look like it has
# rendered an assessor's determination.
AO_DESIGNATIONS = (
    "appears_satisfied",
    "gap_identified",
    "not_applicable",
    "cannot_assess",
)

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
            "description": (
                "Your overall impression of sufficiency. Advisory only — the "
                "file's recorded status is derived by the server from your "
                "per-objective designations."
            ),
        },
        "summary": {
            "type": "string",
            "description": "2-3 sentence summary of the assessment",
        },
        "evidence_effective_date": {
            "type": ["string", "null"],
            "description": (
                "YYYY-MM-DD date this evidence CONTENT is effective (approval "
                "date, report period end, screenshot capture date). null when "
                "the document does not state one — never infer or guess."
            ),
        },
        "effective_date_source": {
            "type": ["string", "null"],
            "description": (
                "Where in the document the date came from, quoted or described "
                "(e.g. 'Approved: 14 March 2026 on page 1'). null when the date is null."
            ),
        },
        "ao_findings": {
            "type": "array",
            "description": (
                "Exactly one entry per assessment objective listed in the "
                "prompt, and no entries for anything else."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "ao_id": {
                        "type": "string",
                        "description": "Must be one of the AO ids listed in the prompt, copied exactly",
                    },
                    "suggested_designation": {
                        "type": "string",
                        "enum": list(AO_DESIGNATIONS),
                    },
                    "rationale": {
                        "type": "string",
                        "description": "Why, citing what the evidence does or does not show",
                    },
                    "suggestion": {
                        "type": "string",
                        "description": "Concrete next step to close the gap; empty string when there is nothing to add",
                    },
                },
                "required": ["ao_id", "suggested_designation", "rationale", "suggestion"],
            },
        },
        "findings": {
            "type": "array",
            "description": "File-level observations that are not about a single objective",
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
    "required": [
        "relevance_score", "status", "summary",
        "evidence_effective_date", "ao_findings", "findings",
    ],
}


def _control_entry(ctrl) -> Dict[str, str]:
    """Serialize one catalog control for the prompt context.

    Deprecation keys are added ONLY for deprecated controls (plan §4.4
    consumer 11) so context hashes for active controls are byte-identical to
    the pre-upgrade shape — no spurious cache invalidation or re-assessment.
    """
    entry = {
        "scf_id": ctrl.scf_id,
        "control_name": ctrl.control_name,
        "control_description": ctrl.control_description or "",
    }
    if getattr(ctrl, "status", None) == "deprecated":
        entry["catalog_status"] = "deprecated"
        entry["retired_in_version"] = ctrl.retired_in_version or ""
        entry["superseded_by"] = ctrl.superseded_by or ""
    return entry


def _control_requirements_block(controls: List[Dict[str, str]]) -> str:
    """Render the mapped-control requirements lines for a prompt.

    Deprecated controls still resolve — historical evidence is assessed
    against the scope it was collected under — but the LLM context carries an
    explicit deprecation note line (plan §4.4 consumer 11).
    """
    if not controls:
        return "No specific control mappings defined for this evidence item."
    lines: List[str] = []
    for ctrl in controls:
        lines.append(
            f"- **{ctrl['scf_id']}** ({ctrl['control_name']}): {ctrl['control_description']}"
        )
        if ctrl.get("catalog_status") == "deprecated":
            note = f"  - NOTE: control {ctrl['scf_id']} is deprecated in the SCF catalog"
            if ctrl.get("retired_in_version"):
                note += f" (retired in {ctrl['retired_in_version']})"
            if ctrl.get("superseded_by"):
                note += f"; superseded by {ctrl['superseded_by']}"
            note += ". Assess the evidence against it as historical scope."
            lines.append(note)
    return "\n".join(lines)


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
    # SCF assessment objectives for the mapped controls, ao_id-ordered.
    # [{ao_id, scf_id, objective_text, expected_results}]. Defaulted so the
    # eval harness and other direct constructions keep working unchanged.
    objectives: List[Dict[str, str]] = field(default_factory=list)
    # True when the mapped controls carry more objectives than one prompt can
    # usefully hold and the list was cut at MAX_ASSESSMENT_OBJECTIVES. The
    # caller discloses this; silently assessing against a subset would present
    # partial coverage as complete.
    objectives_capped: bool = False


def _objective_entry(ao) -> Dict[str, str]:
    """Serialize one catalog assessment objective for the prompt context."""
    return {
        "ao_id": ao.ao_id,
        "scf_id": ao.scf_id,
        "objective_text": ao.objective_text or "",
        "expected_results": ao.expected_results or "",
    }


def _objectives_query(control_ids: List[str]):
    """Select active assessment objectives for the mapped controls.

    Ordered by ao_id so the assembled context is byte-stable across runs: the
    objectives enter context_data, so any non-determinism here would change
    the context hash and re-assess every file for no reason.
    """
    return (
        select(SCFCatalogAssessmentObjective)
        .where(
            SCFCatalogAssessmentObjective.scf_id.in_(control_ids),
            SCFCatalogAssessmentObjective.status == "active",
        )
        .order_by(SCFCatalogAssessmentObjective.ao_id)
    )


def _cap_objectives(objectives: List[Dict[str, str]]) -> tuple[List[Dict[str, str]], bool]:
    """Trim the objective list to what one prompt can carry."""
    if len(objectives) <= MAX_ASSESSMENT_OBJECTIVES:
        return objectives, False
    return objectives[:MAX_ASSESSMENT_OBJECTIVES], True


def _context_hash(
    evidence_id: str,
    catalog_entry,
    controls: List[Dict[str, str]],
    objectives: List[Dict[str, str]],
) -> str:
    """SHA-256 over everything that can change the answer.

    Objectives are part of the hash, which is what makes the assessment cache
    self-invalidating: a catalog upgrade that adds, retires or rewords an AO
    moves the hash, and the next run re-assesses instead of serving a verdict
    reached against objectives that no longer exist.
    """
    context_data = {
        "evidence_id": evidence_id,
        "artifact_title": catalog_entry.artifact_title,
        "artifact_description": catalog_entry.artifact_description or "",
        "area_of_focus": catalog_entry.area_of_focus,
        "controls": controls,
        "objectives": objectives,
        "catalog_version": catalog_entry.catalog_version or "",
        "prompt_version": PROMPT_VERSION,
    }
    context_json = json.dumps(context_data, sort_keys=True, default=str)
    return hashlib.sha256(context_json.encode()).hexdigest()


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
    objectives: List[Dict[str, str]] = []

    if control_ids:
        ctrl_result = await db.execute(
            select(SCFCatalogControl).where(
                SCFCatalogControl.scf_id.in_(control_ids)
            )
        )
        for ctrl in ctrl_result.scalars().all():
            controls.append(_control_entry(ctrl))

        ao_result = await db.execute(_objectives_query(control_ids))
        objectives = [_objective_entry(ao) for ao in ao_result.scalars().all()]

    objectives, capped = _cap_objectives(objectives)

    return ControlContext(
        evidence_id=evidence_id,
        artifact_title=catalog_entry.artifact_title,
        artifact_description=catalog_entry.artifact_description or "",
        area_of_focus=catalog_entry.area_of_focus,
        controls=controls,
        context_hash=_context_hash(evidence_id, catalog_entry, controls, objectives),
        framework_version=catalog_entry.catalog_version or "unknown",
        objectives=objectives,
        objectives_capped=capped,
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
    objectives: List[Dict[str, str]] = []

    if control_ids:
        ctrl_result = session.execute(
            select(SCFCatalogControl).where(
                SCFCatalogControl.scf_id.in_(control_ids)
            )
        )
        for ctrl in ctrl_result.scalars().all():
            controls.append(_control_entry(ctrl))

        ao_result = session.execute(_objectives_query(control_ids))
        objectives = [_objective_entry(ao) for ao in ao_result.scalars().all()]

    objectives, capped = _cap_objectives(objectives)

    return ControlContext(
        evidence_id=evidence_id,
        artifact_title=catalog_entry.artifact_title,
        artifact_description=catalog_entry.artifact_description or "",
        area_of_focus=catalog_entry.area_of_focus,
        controls=controls,
        context_hash=_context_hash(evidence_id, catalog_entry, controls, objectives),
        framework_version=catalog_entry.catalog_version or "unknown",
        objectives=objectives,
        objectives_capped=capped,
    )


def _truncation_notice(truncated: bool) -> str:
    """Factual disclosure appended to the evidence block when text was cut."""
    if not truncated:
        return ""
    return (
        "\n**Note:** the content above is only the beginning of this document; "
        "it was truncated to fit the assessment budget. Judge what you can see, "
        "and do not treat the absence of later material as a gap in the evidence."
    )


def _assessment_objectives_block(
    objectives: List[Dict[str, str]],
    capped: bool,
) -> str:
    """Render the assessment objectives the model must answer, grouped by control.

    Objectives arrive in ao_id order, so grouping preserves that order within
    each control and the block is byte-stable for a given catalog.
    """
    if not objectives:
        return (
            "No assessment objectives are published for the mapped controls. "
            "Return an empty `ao_findings` array and judge the file at the "
            "control level in `findings` instead."
        )

    grouped: Dict[str, List[Dict[str, str]]] = {}
    for obj in objectives:
        grouped.setdefault(obj["scf_id"], []).append(obj)

    lines: List[str] = []
    for scf_id, group in grouped.items():
        lines.append(f"### {scf_id}")
        for obj in group:
            lines.append(f"- **{obj['ao_id']}**: {obj['objective_text']}")
            if obj.get("expected_results"):
                lines.append(f"  - Expected results: {obj['expected_results']}")
    if capped:
        lines.append("")
        lines.append(
            f"_Only the first {MAX_ASSESSMENT_OBJECTIVES} objectives (by AO id) are "
            "listed. Answer these and do not speculate about the rest._"
        )
    return "\n".join(lines)


def build_assessment_prompt(
    control_context: ControlContext,
    extracted_text: str,
    filename: str,
    content_type: str,
    assessment_date: str = "",
    truncated: bool = False,
) -> tuple[str, str]:
    """Build the full assessment prompt.

    Returns (system_prompt, user_prompt) tuple.
    Also returns the prompt hash for audit trail.

    ``truncated`` discloses to the model that it is seeing only the head of
    the document. Without it the model reads a cut-off policy as an incomplete
    policy and marks the evidence down for gaps that exist in our extraction
    budget, not in the customer's evidence.
    """
    # Format control requirements (deprecated controls get a note line)
    controls_text = _control_requirements_block(control_context.controls)
    objectives_text = _assessment_objectives_block(
        control_context.objectives, control_context.objectives_capped,
    )
    ao_ids = [obj["ao_id"] for obj in control_context.objectives]

    date_line = f"\n\nToday's date is {assessment_date}. Evaluate all date references relative to this date." if assessment_date else ""

    system_prompt = f"""You are a GRC (Governance, Risk, Compliance) evidence assessor for the Secure Controls Framework (SCF).{date_line}

You evaluate uploaded evidence against the SCF **assessment objectives** of the controls it is mapped to. An assessment objective is a single, testable statement; you answer each one separately, from what the document actually shows.

For every objective you return one of exactly four designations:

- `appears_satisfied` — the evidence shows this objective being met. Say what shows it.
- `gap_identified` — the evidence is on topic but does not show this objective being met, or shows it only partly.
- `not_applicable` — this objective cannot apply to this organisation or this artifact type. Use sparingly and justify it.
- `cannot_assess` — this artifact is not the kind of evidence that could demonstrate this objective, or the document does not contain enough to judge. This is not a criticism of the evidence.

`gap_identified` and `cannot_assess` are different answers. A gap means "I looked and it is not here"; cannot_assess means "this document was never going to tell me". Do not use one for the other.

You are ADVISORY. You are not an assessor and you do not issue determinations — a human reviewer confirms or overrides everything you say. Never use assessor vocabulary such as "satisfied", "other than satisfied", "finding", "nonconformity", "pass" or "fail". Use only the four designations above.

Be specific. A rationale that could have been written without reading the document is worthless.

Respond with valid JSON matching the required schema. No prose outside the JSON."""

    user_prompt = f"""Assess the following evidence file against the assessment objectives of its mapped controls.

## Evidence Item
- **Evidence ID:** {control_context.evidence_id}
- **Artifact Title:** {control_context.artifact_title}
- **Description:** {control_context.artifact_description}
- **Area of Focus:** {control_context.area_of_focus}
- **File:** {filename} ({content_type})
- **Assessment Date:** {assessment_date or "Not specified"}

## Mapped Control Requirements
{controls_text}

## Assessment Objectives
{objectives_text}

## Evidence Content
```
{extracted_text}
```
{_truncation_notice(truncated)}

## Assessment Instructions
1. Answer EVERY assessment objective listed above — exactly one `ao_findings` entry per AO id, using the id verbatim. Do not invent, merge, split or omit ids, and do not answer an objective that is not listed. There are {len(ao_ids)} to answer.
2. Give each objective a `suggested_designation` from: {", ".join(f"`{d}`" for d in AO_DESIGNATIONS)}. Cite what the evidence does or does not show in `rationale`. Put a concrete next step in `suggestion`, or an empty string when there is nothing useful to add.
3. Extract `evidence_effective_date`: the date this CONTENT is effective — the approval or issue date of a policy, the period end of a report, the capture date of a screenshot or export. It is not the upload date, and it is not today. If the document does not state a date you can rely on, return null. Do not estimate, infer from context, or guess: a wrong date in a compliance record is worse than an absent one. When you do return one, say where it came from in `effective_date_source`.
4. Evidence supporting an assessment is normally expected to be no more than 12 months old at the time of assessment. If the effective date you found is older than that, add a `quality` finding saying so and recommending refreshed evidence — but do NOT downgrade the objective designations for age alone. Age is a currency problem for the reviewer to weigh, not an absence of the control.
5. Score `relevance_score` 0-100: how well this content addresses the mapped controls overall.
6. Set `status` to your overall impression. It is advisory — the recorded status is derived by the server from your per-objective designations — so spend your effort on the objectives, not on this field.
7. Use `findings` for observations about the file as a whole (blank or placeholder content, wrong document, illegible scan, missing signatures, currency). Keep objective-specific reasoning in `ao_findings`.

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
    # --- Control requirements block (shared with build_assessment_prompt) ---
    controls_text = _control_requirements_block(control_context.controls)

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
