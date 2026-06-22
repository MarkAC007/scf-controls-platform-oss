"""Windowed evidence assessment service.

Assesses an evidence object over a time window derived from
EvidenceTracking.frequency (via STALENESS_THRESHOLDS) as a portfolio of files.
This is a richer signal than per-file assessment because the LLM sees all
files together and can reason about coverage gaps across expected artifact
types rather than judging a single file in isolation.

Entry points:
    assess_window(session, org_id, evidence_id, ...) -> EvidenceWindowAssessment

Synchronous (psycopg2) so it can run in Celery workers. Intentionally parallel
in structure to services.ai_assessment_service.assess_evidence, but keyed on
evidence_id + window rather than file id.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from catalog_models import SCFCatalogControl, SCFCatalogEvidence
from models import EvidenceFile, EvidenceTracking, EvidenceWindowAssessment
from services.assessment_prompts import (
    assemble_control_context_sync,
    build_window_assessment_prompt,
    hash_prompt,
    PROMPT_VERSION,
)
from services.text_extraction_service import (
    download_evidence_bytes,
    extract_text_from_bytes,
)
from services.validation_service import STALENESS_THRESHOLDS

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-4-20250514"
MAX_OUTPUT_TOKENS = 2048
INPUT_COST_PER_TOKEN = 3.0 / 1_000_000
OUTPUT_COST_PER_TOKEN = 15.0 / 1_000_000

# Upper bound on extracted text per file (characters) so the prompt stays
# within a reasonable token budget when a window has many files.
PER_FILE_TEXT_CAP = 20_000

# Fallback window when tracking.frequency is missing/unknown. Conservative
# (monthly) — also captured in findings as a warning for the user.
FALLBACK_FREQUENCY = "monthly"

# Expected collection cadence (files per window) keyed off frequency. Used to
# decide whether a window has an "insufficient_sample" number of files. Very
# forgiving — real-world collection can miss runs.
_EXPECTED_FILES_IN_WINDOW = {
    "real_time": 1,
    "daily": 3,       # window is 2 days; expect at least 2-3 runs
    "weekly": 1,
    "monthly": 1,
    "quarterly": 1,
    "annual": 1,
    "on_demand": 1,
}


class WindowAssessmentError(Exception):
    """Raised when the window cannot be built (e.g. missing frequency)."""


@dataclass
class _FileInWindow:
    id: UUID
    filename: str
    s3_key: str
    content_type: str
    uploaded_at: datetime
    source_label: str
    extracted_text: str
    sha256_hash: Optional[str]
    # M2 (#572): declarations from webhook payload / header. Used by
    # collectors.registry.resolve_artifact_types as the top-of-chain input.
    collector_id: Optional[str] = None
    declared_artifact_types: Optional[list[str]] = None


def _resolve_frequency(tracking: Optional[EvidenceTracking]) -> tuple[str, bool]:
    """Return (frequency, is_fallback) using STALENESS_THRESHOLDS vocabulary.

    If tracking is missing/blank/unknown, fall back to FALLBACK_FREQUENCY and
    mark is_fallback=True so the caller can surface a warning finding.
    """
    if tracking is None or not tracking.frequency:
        return FALLBACK_FREQUENCY, True
    key = tracking.frequency.strip().lower()
    if key in STALENESS_THRESHOLDS:
        return key, False
    return FALLBACK_FREQUENCY, True


def _infer_source_label(
    filename: str,
    webhook_source_by_file: Optional[dict] = None,
    file_id: Optional[UUID] = None,
) -> str:
    """Best-effort source label — prefer the webhook payload, fall back to filename."""
    if webhook_source_by_file and file_id and file_id in webhook_source_by_file:
        entry = webhook_source_by_file[file_id]
        # Backward-compat: some callers used to pass {id: str}; new shape is {id: {"source": str, ...}}.
        if isinstance(entry, dict):
            src = entry.get("source")
        else:
            src = entry
        if src:
            return str(src)
    # Filenames like "webhook_AzureBackup_<uuid>.json" carry the source in the middle
    base = filename or ""
    if base.startswith("webhook_"):
        rest = base[len("webhook_"):]
        parts = rest.split("_", 1)
        if parts and parts[0]:
            return parts[0]
    return "unknown"


def _fetch_webhook_sources_for_files(session: Session, file_ids: list[UUID]) -> dict:
    """Look up webhook payload metadata for a batch of files.

    Returns {evidence_file_id: {"source", "collector_id", "artifact_types"}} —
    all three are optional. Files that weren't ingested via webhook are
    omitted. Kept backward-compatible: callers that only care about source
    can still use `out[id]["source"]`.
    """
    if not file_ids:
        return {}
    rows = session.execute(
        text(
            """
            SELECT evidence_file_id, payload_json
              FROM webhook_deliveries
             WHERE evidence_file_id = ANY(:file_ids)
            """
        ),
        {"file_ids": list(file_ids)},
    ).all()

    out: dict = {}
    for file_id, payload in rows:
        if not payload:
            continue
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                continue
        if not isinstance(payload, dict):
            continue
        entry: dict = {}
        src = payload.get("source")
        if isinstance(src, str) and src.strip():
            entry["source"] = src.strip()
        cid = payload.get("collector_id")
        if isinstance(cid, str) and cid.strip():
            entry["collector_id"] = cid.strip()
        raw_atype = payload.get("artifact_type")
        if isinstance(raw_atype, str) and raw_atype.strip():
            entry["artifact_types"] = [raw_atype.strip()]
        elif isinstance(raw_atype, list):
            entry["artifact_types"] = [t.strip() for t in raw_atype if isinstance(t, str) and t.strip()]
        if entry:
            out[file_id] = entry
    return out


def _build_expected_artifact_types(session: Session, evidence_id: str) -> list[dict]:
    """Union required_artifact_types across all controls mapped to the evidence.

    Deduplicates by `type`. If a type appears in multiple controls, the
    most-demanding metadata wins (mandatory=True sticks, highest weight sticks).
    """
    catalog = session.execute(
        select(SCFCatalogEvidence).where(SCFCatalogEvidence.evidence_id == evidence_id)
    ).scalar_one_or_none()
    if catalog is None:
        return []

    control_ids = catalog.control_mappings or []
    if not control_ids:
        return []

    ctrls = session.execute(
        select(SCFCatalogControl).where(SCFCatalogControl.scf_id.in_(control_ids))
    ).scalars().all()

    weight_rank = {"low": 0, "medium": 1, "high": 2}
    merged: dict[str, dict] = {}
    for ctrl in ctrls:
        for entry in (ctrl.required_artifact_types or []):
            if not isinstance(entry, dict):
                continue
            atype = entry.get("type")
            if not atype:
                continue
            existing = merged.get(atype)
            if not existing:
                merged[atype] = dict(entry)
                continue
            # Merge: mandatory OR, weight MAX, description keep first non-empty
            if entry.get("mandatory"):
                existing["mandatory"] = True
            if weight_rank.get(entry.get("weight", "medium"), 1) > weight_rank.get(existing.get("weight", "medium"), 1):
                existing["weight"] = entry["weight"]
            if not existing.get("description") and entry.get("description"):
                existing["description"] = entry["description"]

    # Stable ordering: mandatory first, then weight desc, then alpha
    def _sort_key(e):
        return (
            0 if e.get("mandatory") else 1,
            -weight_rank.get(e.get("weight", "medium"), 1),
            e.get("type", ""),
        )
    return sorted(merged.values(), key=_sort_key)


def _guess_artifact_type_for_source(source_label: str, expected_types: list[dict]) -> Optional[str]:
    """Heuristic mapping from a source label to one of the expected artifact types.

    M1a stub: pick the first expected type whose key appears as a substring in
    the source label (case-insensitive). Returns None if no match. A proper
    collector registry replaces this in M2.
    """
    if not source_label or not expected_types:
        return None
    lower_src = source_label.lower()
    for entry in expected_types:
        t = str(entry.get("type", "")).lower()
        if not t:
            continue
        tokens = [tok for tok in t.split("_") if tok]
        if any(tok in lower_src for tok in tokens):
            return entry["type"]
    return None


def _compute_coverage(
    files: list[_FileInWindow], expected_types: list[dict]
) -> tuple[dict, dict]:
    """Return (source_coverage, artifact_type_coverage) dictionaries.

    Resolution order per file (M2, #572):
      1. Declared types on the file (payload/header) — always honoured.
      2. Registry lookup via ENABLE_COLLECTOR_REGISTRY flag.
      3. Heuristic fallback (_guess_artifact_type_for_source) — preserves M1a behaviour.
    """
    from collectors.registry import resolve_artifact_types

    source_coverage: dict = {}
    for f in files:
        source_coverage[f.source_label] = source_coverage.get(f.source_label, 0) + 1

    # Initialise all expected types to "missing"
    artifact_type_coverage: dict = {
        e["type"]: {"present": False, "file_count": 0}
        for e in expected_types
        if e.get("type")
    }
    for f in files:
        resolved, _via = resolve_artifact_types(
            collector_id=f.collector_id,
            source_label=f.source_label,
            declared=f.declared_artifact_types,
        )
        if not resolved:
            guessed = _guess_artifact_type_for_source(f.source_label, expected_types)
            resolved = [guessed] if guessed else []
            # M2 PR 1.1 (#572 §6a): complete the four-arm resolution log.
            # registry.resolve_artifact_types covers {payload, registry, empty};
            # this covers the heuristic fallback arm.
            logger.info(
                "collector.resolve collector_id=%r source_label=%r resolved_via=heuristic types=%s",
                f.collector_id, f.source_label, resolved,
            )
        for atype in resolved:
            if atype in artifact_type_coverage:
                artifact_type_coverage[atype]["present"] = True
                artifact_type_coverage[atype]["file_count"] += 1

    return source_coverage, artifact_type_coverage


def _apply_sticky_review_carryover(
    session: Session,
    organization_id: UUID,
    evidence_id: str,
    new_assessment: EvidenceWindowAssessment,
) -> None:
    """M4 PR 3 sticky review carryover (Decision D3).

    Reviewer disposition outlives AI re-runs — without this, every nightly
    cycle silently clobbers the human review back to not_reviewed, making
    the audit trail meaningless. Take the most recent approved/rejected row
    for the same (org, evidence_id) and copy its review fields onto the
    new row.

    ``needs_revision`` is INTENTIONALLY excluded from carryover — that
    disposition explicitly requested a re-assessment, so the fresh row must
    start unreviewed and surface to the reviewer again.

    Mutates ``new_assessment`` in place; returns None.
    """
    prior_reviewed = session.execute(
        select(EvidenceWindowAssessment)
        .where(
            EvidenceWindowAssessment.organization_id == organization_id,
            EvidenceWindowAssessment.evidence_id == evidence_id,
            EvidenceWindowAssessment.review_status.in_(["approved", "rejected"]),
        )
        .order_by(EvidenceWindowAssessment.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if prior_reviewed is not None:
        new_assessment.review_status = prior_reviewed.review_status
        new_assessment.reviewed_by_user_id = prior_reviewed.reviewed_by_user_id
        new_assessment.reviewed_at = prior_reviewed.reviewed_at
        new_assessment.review_notes = prior_reviewed.review_notes


def _compute_window_hash(
    evidence_id: str,
    window_start: datetime,
    window_end: datetime,
    files: list[_FileInWindow],
) -> str:
    """SHA-256 fingerprint of the window's file set for cache invalidation."""
    parts = [evidence_id, window_start.isoformat(), window_end.isoformat(), PROMPT_VERSION]
    for f in sorted(files, key=lambda x: str(x.id)):
        parts.append(f.sha256_hash or str(f.id))
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _call_llm(system_prompt: str, user_prompt: str) -> Optional[dict]:
    """Call Claude for windowed assessment. Mirrors per-file pattern."""
    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic package not installed — cannot run window assessment")
        return None

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set — cannot run window assessment")
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
        logger.error("Claude API call failed during window assessment: %s", exc, exc_info=True)
        return None


def _parse_llm_response(content: str) -> dict:
    """Parse the windowed-assessment LLM JSON response with safe defaults."""
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
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Failed to parse window assessment LLM response: %s", exc)
        return {
            "status": "error",
            "relevance_score": None,
            "summary": "AI response could not be parsed",
            "findings": [{
                "category": "error",
                "level": "info",
                "message": f"LLM response was not valid JSON: {content[:200]}",
            }],
        }

    status = parsed.get("status")
    if status not in ("sufficient", "partial", "insufficient"):
        parsed["status"] = "partial"
    score = parsed.get("relevance_score")
    if score is not None:
        try:
            parsed["relevance_score"] = max(0.0, min(100.0, float(score)))
        except (TypeError, ValueError):
            parsed["relevance_score"] = None
    if not isinstance(parsed.get("findings"), list):
        parsed["findings"] = []
    if not isinstance(parsed.get("summary"), str):
        parsed["summary"] = ""
    return parsed


def assess_window(
    session: Session,
    *,
    organization_id: UUID,
    evidence_id: str,
    assessment_source: str = "on_demand",
    requested_by_user_id: Optional[UUID] = None,
) -> EvidenceWindowAssessment:
    """Assess an evidence object over its current frequency-derived window.

    Sync — intended to be called from Celery workers and CLI paths.

    Raises WindowAssessmentError on hard failures (e.g. no evidence tracking).
    Soft failures (LLM unavailable, parse errors) are recorded on the
    assessment row with status="error" and the record is returned.
    """
    start_time = time.monotonic()

    tracking = session.execute(
        select(EvidenceTracking).where(
            EvidenceTracking.organization_id == organization_id,
            EvidenceTracking.evidence_id == evidence_id,
        )
    ).scalar_one_or_none()

    frequency_used, frequency_is_fallback = _resolve_frequency(tracking)
    window_days = STALENESS_THRESHOLDS[frequency_used]
    window_end = datetime.utcnow()
    window_start = window_end - timedelta(days=window_days)

    # Fetch files in window
    file_rows = session.execute(
        select(EvidenceFile).where(
            EvidenceFile.organization_id == organization_id,
            EvidenceFile.evidence_id == evidence_id,
            EvidenceFile.is_deleted.is_(False),
            EvidenceFile.uploaded_at >= window_start,
            EvidenceFile.uploaded_at <= window_end,
        ).order_by(EvidenceFile.uploaded_at.desc())
    ).scalars().all()

    # Expected artifact types (union across mapped controls)
    expected_types = _build_expected_artifact_types(session, evidence_id)

    # Look up webhook-payload sources in one batch
    webhook_sources = _fetch_webhook_sources_for_files(
        session, [f.id for f in file_rows]
    )

    files_in_window: list[_FileInWindow] = []
    for f in file_rows:
        source_label = _infer_source_label(
            filename=f.filename,
            webhook_source_by_file=webhook_sources,
            file_id=f.id,
        )
        # Extract text (best-effort; empty extraction still keeps the file as a source signal)
        raw = download_evidence_bytes(f.s3_key)
        extracted_text = ""
        if raw:
            extracted = extract_text_from_bytes(
                data=raw, content_type=f.content_type, filename=f.filename
            )
            extracted_text = (extracted.text or "")[:PER_FILE_TEXT_CAP]

        webhook_entry = webhook_sources.get(f.id) if isinstance(webhook_sources, dict) else None
        collector_id: Optional[str] = None
        declared_artifact_types: Optional[list[str]] = None
        if isinstance(webhook_entry, dict):
            cid = webhook_entry.get("collector_id")
            if isinstance(cid, str) and cid:
                collector_id = cid
            atypes = webhook_entry.get("artifact_types")
            if isinstance(atypes, list) and atypes:
                declared_artifact_types = list(atypes)

        files_in_window.append(_FileInWindow(
            id=f.id,
            filename=f.filename,
            s3_key=f.s3_key,
            content_type=f.content_type,
            uploaded_at=f.uploaded_at,
            source_label=source_label,
            extracted_text=extracted_text,
            sha256_hash=f.sha256_hash,
            collector_id=collector_id,
            declared_artifact_types=declared_artifact_types,
        ))

    # Coverage
    source_coverage, artifact_type_coverage = _compute_coverage(files_in_window, expected_types)

    # Compute window hash for cache lookup
    window_hash = _compute_window_hash(evidence_id, window_start, window_end, files_in_window)

    # Upsert skeleton record (status=processing)
    assessment = session.execute(
        select(EvidenceWindowAssessment).where(
            EvidenceWindowAssessment.organization_id == organization_id,
            EvidenceWindowAssessment.evidence_id == evidence_id,
            EvidenceWindowAssessment.window_start == window_start,
            EvidenceWindowAssessment.window_end == window_end,
        )
    ).scalar_one_or_none()

    # Cache hit? Same window_hash + non-terminal previous run → return it.
    if (
        assessment
        and assessment.window_hash == window_hash
        and assessment.status in ("sufficient", "partial", "insufficient", "insufficient_sample")
    ):
        logger.info(
            "Window assessment cache hit for org=%s evidence=%s hash=%s",
            organization_id, evidence_id, window_hash[:12],
        )
        return assessment

    if assessment is None:
        assessment = EvidenceWindowAssessment(
            organization_id=organization_id,
            evidence_id=evidence_id,
            window_start=window_start,
            window_end=window_end,
            frequency_used=frequency_used,
            file_ids=[str(f.id) for f in files_in_window],
            source_coverage=source_coverage,
            artifact_type_coverage=artifact_type_coverage,
            expected_artifact_types=expected_types,
            status="processing",
            assessment_source=assessment_source,
            requested_by_user_id=requested_by_user_id,
            window_hash=window_hash,
        )

        _apply_sticky_review_carryover(
            session, organization_id, evidence_id, assessment,
        )

        session.add(assessment)
    else:
        assessment.frequency_used = frequency_used
        assessment.file_ids = [str(f.id) for f in files_in_window]
        assessment.source_coverage = source_coverage
        assessment.artifact_type_coverage = artifact_type_coverage
        assessment.expected_artifact_types = expected_types
        assessment.status = "processing"
        assessment.assessment_source = assessment_source
        assessment.requested_by_user_id = requested_by_user_id
        assessment.window_hash = window_hash
    session.commit()

    # Build pre-findings: fallback frequency + insufficient sample
    pre_findings: list[dict] = []
    if frequency_is_fallback:
        pre_findings.append({
            "category": "coverage",
            "level": "info",
            "message": (
                f"Evidence frequency was missing or unrecognised — fell back to "
                f"'{FALLBACK_FREQUENCY}'. Set a valid frequency via update_evidence "
                f"to tune the window correctly."
            ),
            "suggestion": f"Set frequency to one of: {', '.join(sorted(STALENESS_THRESHOLDS.keys()))}",
        })

    expected_files = _EXPECTED_FILES_IN_WINDOW.get(frequency_used, 1)
    sample_insufficient = len(files_in_window) < expected_files
    if sample_insufficient:
        pre_findings.append({
            "category": "coverage",
            "level": "insufficient",
            "message": (
                f"Expected at least {expected_files} file(s) within the last "
                f"{window_days} day(s) for '{frequency_used}' frequency, "
                f"found {len(files_in_window)}."
            ),
            "suggestion": "Verify the collector is running on schedule and shipping to this evidence ID.",
        })

    # Build control context & prompt
    control_context = assemble_control_context_sync(session, evidence_id)
    if control_context is None:
        _finalise_error(
            session, assessment, start_time,
            "No catalog entry found for evidence ID — cannot assess without control context",
        )
        return assessment

    assessment_date = datetime.utcnow().strftime("%Y-%m-%d")
    system_prompt, user_prompt = build_window_assessment_prompt(
        control_context=control_context,
        window_start=window_start.isoformat(),
        window_end=window_end.isoformat(),
        frequency_used=frequency_used,
        files=[
            {
                "filename": f.filename,
                "content_type": f.content_type,
                "source": f.source_label,
                "uploaded_at": f.uploaded_at.isoformat() if f.uploaded_at else "",
                "text": f.extracted_text,
            }
            for f in files_in_window
        ],
        expected_artifact_types=expected_types,
        source_coverage=source_coverage,
        artifact_type_coverage=artifact_type_coverage,
        assessment_date=assessment_date,
    )
    prompt_hash_value = hash_prompt(system_prompt, user_prompt)

    # If no files in window, skip the LLM call — the coverage finding is
    # enough, and LLM cannot reason about an empty set.
    if not files_in_window:
        assessment.status = "insufficient_sample"
        assessment.relevance_score = Decimal("0.00")
        assessment.findings = pre_findings or [{
            "category": "coverage",
            "level": "insufficient",
            "message": "No evidence files present within the window.",
            "suggestion": "Configure or verify the evidence collector for this evidence ID.",
        }]
        assessment.summary = "No files in window — nothing to assess."
        assessment.prompt_hash = prompt_hash_value
        assessment.control_context_hash = control_context.context_hash
        assessment.framework_version = control_context.framework_version
        assessment.model_id = None
        assessment.input_token_count = 0
        assessment.output_token_count = 0
        assessment.cost_cents = Decimal("0.0000")
        assessment.processing_time_ms = int((time.monotonic() - start_time) * 1000)
        assessment.assessed_at = datetime.utcnow()
        session.commit()
        return assessment

    llm = _call_llm(system_prompt, user_prompt)
    if llm is None:
        _finalise_error(
            session, assessment, start_time,
            "LLM call failed — AI window assessment unavailable",
            prompt_hash=prompt_hash_value,
            control_context_hash=control_context.context_hash,
            framework_version=control_context.framework_version,
        )
        return assessment

    parsed = _parse_llm_response(llm["content"])

    # Merge pre_findings (coverage-level) with LLM findings
    findings = list(pre_findings) + list(parsed.get("findings") or [])

    # Window-level status: insufficient_sample wins if we set it upfront;
    # otherwise take the LLM's verdict.
    status = "insufficient_sample" if sample_insufficient else parsed.get("status", "error")

    input_tokens = llm.get("input_tokens", 0)
    output_tokens = llm.get("output_tokens", 0)
    cost_cents = round(
        (input_tokens * INPUT_COST_PER_TOKEN + output_tokens * OUTPUT_COST_PER_TOKEN) * 100,
        4,
    )

    assessment.status = status
    rel = parsed.get("relevance_score")
    assessment.relevance_score = Decimal(str(rel)) if rel is not None else None
    assessment.findings = findings
    assessment.summary = parsed.get("summary", "")
    assessment.model_id = llm.get("model", DEFAULT_MODEL)
    assessment.prompt_hash = prompt_hash_value
    assessment.control_context_hash = control_context.context_hash
    assessment.framework_version = control_context.framework_version
    assessment.input_token_count = input_tokens
    assessment.output_token_count = output_tokens
    assessment.cost_cents = Decimal(str(cost_cents))
    assessment.processing_time_ms = int((time.monotonic() - start_time) * 1000)
    assessment.assessed_at = datetime.utcnow()
    session.commit()

    logger.info(
        "Window assessment complete org=%s evidence=%s status=%s score=%s files=%d cost=%.4fc",
        organization_id, evidence_id, assessment.status, assessment.relevance_score,
        len(files_in_window), cost_cents,
    )
    return assessment


def _finalise_error(
    session: Session,
    assessment: EvidenceWindowAssessment,
    start_time: float,
    message: str,
    *,
    prompt_hash: Optional[str] = None,
    control_context_hash: Optional[str] = None,
    framework_version: Optional[str] = None,
) -> None:
    assessment.status = "error"
    assessment.findings = [{
        "category": "error",
        "level": "info",
        "message": message,
    }]
    assessment.summary = message
    assessment.processing_time_ms = int((time.monotonic() - start_time) * 1000)
    assessment.assessed_at = datetime.utcnow()
    if prompt_hash:
        assessment.prompt_hash = prompt_hash
    if control_context_hash:
        assessment.control_context_hash = control_context_hash
    if framework_version:
        assessment.framework_version = framework_version
    session.commit()
