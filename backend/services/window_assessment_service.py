"""Windowed evidence assessment service.

Assesses an evidence object over a time window derived from
EvidenceTracking.frequency (via STALENESS_THRESHOLDS) as a portfolio of files.
This is a richer signal than per-file assessment because the LLM sees all
files together and can reason about coverage gaps across expected artifact
types rather than judging a single file in isolation.

Entry points:
    assess_window(session, org_id, evidence_id, ...) -> EvidenceWindowAssessment

Synchronous (psycopg2) so it can run in Celery workers. Intentionally parallel
in structure to tasks_assessment.assess_evidence_task, but keyed on
evidence_id + window rather than file id.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import and_, or_, select, text
from sqlalchemy.orm import Session

from catalog_models import SCFCatalogControl, SCFCatalogEvidence
from models import EvidenceFile, EvidenceTracking, EvidenceWindowAssessment
from services.assessment_prompts import (
    assemble_control_context_sync,
    build_window_assessment_prompt,
    hash_prompt,
    WINDOW_PROMPT_VERSION,
    WINDOW_SCHEMA_VERSION,
)
from services.assessment_verdict import (
    AssessmentParseError,
    ParsedWindowAssessment,
    derive_assessment_status,
    parse_window_assessment_v2,
    status_coercion_finding,
)
from services.text_extraction_service import (
    download_evidence_bytes,
    extract_text_from_bytes,
)
from services.validation_service import STALENESS_THRESHOLDS
from services.frequency_vocabulary import UI_OPTIONS, normalize as normalize_frequency
from services.anthropic_response import extract_text
from services.artifact_type_extraction_service import extract_for_control
from services.model_registry import cost_cents as model_cost_cents, resolve as resolve_model

from services.llm_client import build_anthropic_client
from services.secrets import get_secret

logger = logging.getLogger(__name__)

# Sized for Opus 5's default adaptive thinking, whose tokens are spent INSIDE
# max_tokens — 2048 fit the non-thinking Sonnet answer and truncated the moment
# the model thought first (2026-09-03 incident; see tasks_assessment.py).
MAX_OUTPUT_TOKENS = 32000

# Model id and price both come from services/model_registry (#782). This module
# used to pin "claude-sonnet-4-20250514" here and carry its own copy of the
# Sonnet rate card two lines below — the pin was retired, the API answered 404,
# and every window assessment failed soft with status=error. The rate card
# beside a pin is the other half of that defect: it makes a repoint silently
# corrupt every cost figure. Neither belongs at a call site.
MODEL_ROLE = "evidence_assessment"

# Upper bound on extracted text per file (characters) so the prompt stays
# within a reasonable token budget when a window has many files.
PER_FILE_TEXT_CAP = 20_000

# Upper bound on extracted text across the whole window (characters). A
# monthly window on a daily collector held 22 files in production (#569);
# at the per-file cap alone that is 440k characters, past what one prompt
# should carry. Identical payloads are collapsed first (see
# _plan_prompt_content), then representatives are admitted newest-first
# until this budget is spent, and whatever was not shown is disclosed on the
# row. Roughly 40k tokens; comfortably inside the model's context beside a
# 60-objective prompt and MAX_OUTPUT_TOKENS of thinking + answer.
WINDOW_TEXT_BUDGET = int(os.getenv("WINDOW_ASSESSMENT_TEXT_BUDGET") or "150000")

# Self-hosted installs ship the catalog with `required_artifact_types` empty:
# the shipped catalog data carries no artifact-type extraction, and the
# one-off CLI (backend/scripts/extract_artifact_types.py) is not part of the
# install path. Rather than seeding every control up front (an LLM call per
# control against a catalog most tenants never touch), the window assessor
# extracts lazily: the first window assessment that needs a control's artifact
# types runs the extraction once and caches the result on the control row
# (`required_artifact_types` + `required_artifact_types_extracted_at`).
# Controls that were attempted and produced an empty list are stamped too, so
# they are never re-tried by the assessor; the CLI's --force remains the way
# to re-extract. Extraction is fail-open: any failure logs and the assessment
# proceeds without an artifact-type coverage section, exactly as before.
# Set ARTIFACT_TYPE_LAZY_EXTRACTION=false to opt out (assessments then only
# see whatever the CLI populated).
_TRUTHY = ("1", "true", "yes", "on")


def _lazy_artifact_type_extraction_enabled() -> bool:
    return (os.getenv("ARTIFACT_TYPE_LAZY_EXTRACTION") or "true").strip().lower() in _TRUTHY


def _artifact_types_for_control(session: Session, ctrl: SCFCatalogControl) -> list:
    """Return a control's required_artifact_types, extracting lazily if unset.

    Only controls that have never been attempted (empty list AND no
    `required_artifact_types_extracted_at` stamp) trigger an extraction.
    Failures never propagate: the caller gets an empty list and the
    assessment continues.
    """
    existing = ctrl.required_artifact_types or []
    if existing:
        return existing
    if ctrl.required_artifact_types_extracted_at is not None:
        return []
    if not _lazy_artifact_type_extraction_enabled():
        return []
    try:
        result = extract_for_control(session, ctrl.scf_id)
    except Exception as exc:  # noqa: BLE001 — fail-open by design
        logger.warning(
            "Lazy artifact-type extraction failed for %s: %s", ctrl.scf_id, exc,
        )
        return []
    if result.error:
        logger.warning(
            "Lazy artifact-type extraction for %s returned no result: %s",
            ctrl.scf_id, result.error,
        )
        return []
    logger.info(
        "Lazily extracted %d artifact type(s) for %s on first window assessment",
        len(result.artifact_types or []), ctrl.scf_id,
    )
    return result.artifact_types or []

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
    # Window parity (WS2/WS3): preparer assertions, why the file is in the
    # window, and how its content was used in the prompt.
    effective_period_start: Optional[date] = None
    effective_period_end: Optional[date] = None
    membership_rule: str = "uploaded_at"
    storage_config_id: Optional[str] = None
    content_hash: Optional[str] = None
    truncated: bool = False
    represented_by: Optional[UUID] = None
    omitted_reason: Optional[str] = None


def _resolve_frequency(tracking: Optional[EvidenceTracking]) -> tuple[str, bool]:
    """Return (frequency, is_fallback) using the shared frequency vocabulary.

    If tracking is missing/blank/unknown, fall back to FALLBACK_FREQUENCY and
    mark is_fallback=True so the caller can surface a warning finding.
    """
    if tracking is None or not tracking.frequency:
        return FALLBACK_FREQUENCY, True
    key = normalize_frequency(tracking.frequency)
    if key is not None:
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

    Controls whose artifact types were never extracted are extracted lazily
    here and cached on the control row (see _artifact_types_for_control).
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
        for entry in _artifact_types_for_control(session, ctrl):
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


def _prior_review_reference(
    session: Session,
    organization_id: UUID,
    evidence_id: str,
) -> Optional[dict]:
    """Describe the most recent human review of *earlier* windows, as a finding.

    Supersedes the M4 PR 3 "sticky review carryover" (Decision D3), which
    copied ``review_status``, ``reviewed_by_user_id``, ``reviewed_at`` and
    ``review_notes`` from the last approved/rejected row onto a brand-new
    window assessment. That fabricated an attestation: a reviewer's approval
    of last quarter's evidence appeared, under their name, against this
    quarter's fresh and unexamined AI verdict. A missing attestation is
    visibly missing; a fabricated one is indistinguishable from a real one,
    including to the person named in it.

    D3's stated rationale — that without the carryover "every nightly cycle
    silently clobbers the human review back to not_reviewed" — does not hold
    at the call site. The carryover only ever ran in the branch that creates
    a *new* window row; re-assessing an existing window takes the other
    branch, which never touches the review block. So the carryover protected
    nothing and fabricated in every case it fired.

    What replaces it is a pointer, not an inheritance. The new row keeps its
    ``not_reviewed`` default and a NULL reviewer, so every consumer — the
    review queue, the KSI maths, the UI badge — correctly reads it as
    unattested. The prior disposition is surfaced as an informational
    finding on the row instead, where a reviewer picking the item up can see
    it without any part of the system mistaking it for a signature.

    Returns the finding dict, or ``None`` when this evidence has no prior
    approved/rejected review. Mutates nothing.
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
    if prior_reviewed is None:
        return None

    reviewed_on = (
        prior_reviewed.reviewed_at.strftime("%Y-%m-%d")
        if prior_reviewed.reviewed_at
        else "an unrecorded date"
    )
    prior_window = "an earlier window"
    if prior_reviewed.window_start and prior_reviewed.window_end:
        prior_window = (
            f"the window {prior_reviewed.window_start.strftime('%Y-%m-%d')} to "
            f"{prior_reviewed.window_end.strftime('%Y-%m-%d')}"
        )
    return {
        "category": "review",
        "level": "info",
        "message": (
            f"A human reviewer marked this evidence '{prior_reviewed.review_status}' "
            f"on {reviewed_on}, for {prior_window}. That decision does not attest "
            f"to this window — this assessment is unreviewed."
        ),
        "suggestion": "Review this window on its own merits before relying on it.",
    }


def _compose_findings(
    pre_findings: list[dict],
    prior_review_note: Optional[dict],
    llm_findings: list[dict],
) -> list[dict]:
    """Order a window's findings: coverage first, then provenance, then AI.

    Extracted so the prior-review pointer's presence in the persisted
    findings is unit-testable — inlining the concatenation would leave the
    only guard on it a source-level assertion.
    """
    composed = list(pre_findings)
    if prior_review_note is not None:
        composed.append(prior_review_note)
    composed.extend(llm_findings)
    return composed


def _compute_window_hash(
    evidence_id: str,
    window_start: datetime,
    window_end: datetime,
    files: list[_FileInWindow],
) -> str:
    """SHA-256 fingerprint of the window's file set for cache invalidation.

    Salted with WINDOW_PROMPT_VERSION (not the per-file PROMPT_VERSION) so a
    window prompt change re-assesses windows without disturbing per-file
    caches, and vice versa. The membership rule rides along with each file:
    the same file selected for a different reason is rendered differently.
    """
    parts = [evidence_id, window_start.isoformat(), window_end.isoformat(), WINDOW_PROMPT_VERSION]
    for f in sorted(files, key=lambda x: str(x.id)):
        parts.append(f"{f.sha256_hash or f.id}:{f.membership_rule}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Membership (WS2): asserted effective period first, upload date as fallback
# ---------------------------------------------------------------------------

MEMBERSHIP_ASSERTED_PERIOD = "asserted_period"
MEMBERSHIP_UPLOADED_AT = "uploaded_at"


def _membership_rule(
    *,
    uploaded_at: Optional[datetime],
    effective_period_start: Optional[date],
    effective_period_end: Optional[date],
    window_start: datetime,
    window_end: datetime,
) -> Optional[str]:
    """Which rule puts a file in this window, or None when it is not a member.

    A preparer who asserted an effective period has said what time the
    document speaks for, and that assertion governs: the file is in the
    window when the asserted period overlaps it, wherever the upload landed.
    An open-ended assertion (only a start, or only an end) extends to
    infinity on the unspecified side. Only an unasserted file falls back to
    when it was uploaded.
    """
    if effective_period_start is not None or effective_period_end is not None:
        ws, we = window_start.date(), window_end.date()
        starts_before_window_ends = effective_period_start is None or effective_period_start <= we
        ends_after_window_starts = effective_period_end is None or effective_period_end >= ws
        if starts_before_window_ends and ends_after_window_starts:
            return MEMBERSHIP_ASSERTED_PERIOD
        return None
    if uploaded_at is not None and window_start <= uploaded_at <= window_end:
        return MEMBERSHIP_UPLOADED_AT
    return None


def _select_files_for_window(
    session: Session,
    organization_id: UUID,
    evidence_id: str,
    window_start: datetime,
    window_end: datetime,
) -> list[tuple[EvidenceFile, str]]:
    """Files in the window with the rule that selected each, newest first."""
    candidates = session.execute(
        select(EvidenceFile).where(
            EvidenceFile.organization_id == organization_id,
            EvidenceFile.evidence_id == evidence_id,
            EvidenceFile.is_deleted.is_(False),
            or_(
                and_(
                    EvidenceFile.uploaded_at >= window_start,
                    EvidenceFile.uploaded_at <= window_end,
                ),
                EvidenceFile.effective_period_start.isnot(None),
                EvidenceFile.effective_period_end.isnot(None),
            ),
        ).order_by(EvidenceFile.uploaded_at.desc())
    ).scalars().all()

    selected: list[tuple[EvidenceFile, str]] = []
    for f in candidates:
        rule = _membership_rule(
            uploaded_at=f.uploaded_at,
            effective_period_start=f.effective_period_start,
            effective_period_end=f.effective_period_end,
            window_start=window_start,
            window_end=window_end,
        )
        if rule is not None:
            selected.append((f, rule))
    return selected


# ---------------------------------------------------------------------------
# Text budget (WS3): dedupe identical payloads, cap the window's characters
# ---------------------------------------------------------------------------

def _plan_prompt_content(files: list[_FileInWindow]) -> None:
    """Decide which files' text goes to the model. Mutates the entries.

    1. Files with identical content (same sha256) collapse to one
       representative — the newest — and the rest point at it via
       ``represented_by``. A daily collector that ships the same payload 22
       times is one document, not 22.
    2. Representatives are then admitted newest-first until the window's
       text budget is spent; the remainder are marked ``omitted_reason``
       and still count for coverage and sample size.

    Files must arrive newest-first. Nothing is downloaded here; the caller
    extracts text only for entries left with neither marker set.
    """
    seen_hash: dict[str, _FileInWindow] = {}
    for f in files:
        key = f.content_hash or f.sha256_hash
        if not key:
            continue
        if key in seen_hash:
            f.represented_by = seen_hash[key].id
        else:
            seen_hash[key] = f


def _apply_text_budget(f: _FileInWindow, text_value: str, budget_left: int) -> tuple[str, int]:
    """Cut one file's text to the per-file cap and the remaining budget."""
    allowed = min(PER_FILE_TEXT_CAP, max(budget_left, 0))
    cut = text_value[:allowed]
    f.truncated = len(text_value) > len(cut)
    return cut, budget_left - len(cut)


def _text_budget_finding(files: list[_FileInWindow]) -> Optional[dict]:
    """Disclose, as a coverage finding, what the model was not shown."""
    duplicates = [f for f in files if f.represented_by is not None]
    omitted = [f for f in files if f.omitted_reason is not None]
    if not duplicates and not omitted:
        return None
    parts: list[str] = []
    if duplicates:
        parts.append(
            f"{len(duplicates)} file(s) had content identical to another file in the "
            f"window and were sent to the model once, under the representative file's id: "
            f"{', '.join(str(f.id) for f in duplicates)}."
        )
    if omitted:
        parts.append(
            f"{len(omitted)} file(s) were counted for coverage but their content was not "
            f"sent to the model because the window exceeded the text budget of "
            f"{WINDOW_TEXT_BUDGET:,} characters: {', '.join(str(f.id) for f in omitted)}."
        )
    return {
        "category": "coverage",
        "level": "info",
        "message": " ".join(parts),
        "suggestion": (
            "Objective answers cite the representative file for duplicated content. "
            "If the omitted files could change an answer, review them directly."
        ),
        "duplicate_file_ids": [str(f.id) for f in duplicates],
        "omitted_file_ids": [str(f.id) for f in omitted],
    }


def _membership_snapshot(files: list[_FileInWindow]) -> dict:
    """Per-file record of why each file is in the window and how it was used."""
    out: dict = {}
    for f in files:
        out[str(f.id)] = {
            "rule": f.membership_rule,
            "uploaded_at": f.uploaded_at.isoformat() if f.uploaded_at else None,
            "effective_period_start": (
                f.effective_period_start.isoformat() if f.effective_period_start else None
            ),
            "effective_period_end": (
                f.effective_period_end.isoformat() if f.effective_period_end else None
            ),
            "in_prompt": f.represented_by is None and f.omitted_reason is None,
            "represented_by": str(f.represented_by) if f.represented_by else None,
            "omitted_reason": f.omitted_reason,
            "truncated": f.truncated,
        }
    return out


def _collection_context(tracking: Optional[EvidenceTracking]) -> Optional[dict]:
    if tracking is None:
        return None
    return {
        "method_of_collection": tracking.method_of_collection,
        "collecting_system": tracking.collecting_system,
    }


# ---------------------------------------------------------------------------
# Append-only history (parity with tasks_assessment._write_terminal_verdict)
# ---------------------------------------------------------------------------

#: The version row is copied FROM the parent row inside the database, so the
#: two cannot drift: whatever the parent says at the moment of the write is
#: what the history says forever. version_number is computed in SQL from the
#: parent's own counter, under the row lock the preceding flush took, so two
#: workers finishing the same window serialise on it — and if they race past
#: it anyway the unique constraint on (window_assessment_id, version_number)
#: refuses the second rather than admitting a duplicate.
_INSERT_WINDOW_VERSION_SQL = text("""
    INSERT INTO evidence_window_assessment_versions (
        id, window_assessment_id, organization_id, evidence_id,
        version_number, schema_version,
        window_start, window_end, frequency_used, file_ids, file_membership,
        status, relevance_score, summary, findings, ao_findings,
        gap_count, cannot_assess_count, file_effective_dates, unassessable_reason,
        model_id, prompt_hash, prompt_version, control_context_hash,
        framework_version, window_hash, input_token_count, output_token_count,
        cost_cents, processing_time_ms,
        assessment_source, requested_by_user_id, assessed_at
    )
    SELECT
        :version_id, ewa.id, ewa.organization_id, ewa.evidence_id,
        COALESCE(ewa.version_number, 0) + 1, COALESCE(ewa.schema_version, 2),
        ewa.window_start, ewa.window_end, ewa.frequency_used,
        COALESCE(ewa.file_ids, '[]'::jsonb), COALESCE(ewa.file_membership, '{}'::jsonb),
        ewa.status, ewa.relevance_score, ewa.summary,
        COALESCE(ewa.findings, '[]'::jsonb), COALESCE(ewa.ao_findings, '[]'::jsonb),
        COALESCE(ewa.gap_count, 0), COALESCE(ewa.cannot_assess_count, 0),
        COALESCE(ewa.file_effective_dates, '[]'::jsonb), ewa.unassessable_reason,
        ewa.model_id, ewa.prompt_hash, ewa.prompt_version, ewa.control_context_hash,
        ewa.framework_version, ewa.window_hash, ewa.input_token_count, ewa.output_token_count,
        ewa.cost_cents, ewa.processing_time_ms,
        ewa.assessment_source, ewa.requested_by_user_id, ewa.assessed_at
    FROM evidence_window_assessments ewa
    WHERE ewa.id = :assessment_id
    RETURNING version_number
""")

_POINT_CURRENT_VERSION_SQL = text("""
    UPDATE evidence_window_assessments SET
        current_version_id = :version_id,
        version_number = :version_number,
        -- A new verdict has not been reviewed. Carrying the previous decision
        -- forward would show a reviewer's name against findings they never saw.
        review_decision = NULL,
        review_reason = NULL,
        verdict_reviewed_by_user_id = NULL,
        verdict_reviewed_at = NULL
    WHERE id = :assessment_id
""")

_VERSION_POINTER_FIELDS = (
    "current_version_id", "version_number",
    "review_decision", "review_reason",
    "verdict_reviewed_by_user_id", "verdict_reviewed_at",
)


def _write_window_terminal_verdict(session: Session, assessment: EvidenceWindowAssessment) -> Optional[int]:
    """Persist the verdict on the parent row, freeze it as a new version, repoint the parent.

    One transaction, committed once. Called at every terminal outcome —
    success, no-files, error — so the history is complete rather than a
    record of the happy path. Returns the new version number.
    """
    session.flush()
    version_id = uuid.uuid4()
    row = session.execute(
        _INSERT_WINDOW_VERSION_SQL,
        {"version_id": version_id, "assessment_id": assessment.id},
    ).first()
    if row is None:
        # The parent vanished between the flush and the insert (deleted
        # mid-flight). Nothing to record a history against; keep the
        # commit so the parent write, if any, is not lost.
        logger.warning(
            "No evidence_window_assessments row id=%s — verdict not versioned", assessment.id,
        )
        session.commit()
        return None
    version_number = int(row[0])
    session.execute(
        _POINT_CURRENT_VERSION_SQL,
        {"version_id": version_id, "version_number": version_number, "assessment_id": assessment.id},
    )
    session.commit()
    # The pointer and the review reset were written in SQL, not through the
    # ORM; make sure the object reloads them instead of serving stale values.
    session.expire(assessment, list(_VERSION_POINTER_FIELDS))
    return version_number


def _call_llm(system_prompt: str, user_prompt: str) -> Optional[dict]:
    """Call Claude for windowed assessment. Mirrors per-file pattern."""
    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic package not installed — cannot run window assessment")
        return None

    if not get_secret("ANTHROPIC_API_KEY"):
        logger.warning("ANTHROPIC_API_KEY not set — cannot run window assessment")
        return None

    try:
        client = build_anthropic_client()
        with client.messages.stream(
            model=resolve_model(MODEL_ROLE),
            max_tokens=MAX_OUTPUT_TOKENS,
            system=[{
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": user_prompt}],
        ) as stream:
            message = stream.get_final_message()
        return {
            "content": extract_text(message),
            "model": message.model,
            "input_tokens": message.usage.input_tokens,
            "output_tokens": message.usage.output_tokens,
            # Needed to tell a complete answer from one cut off at the token
            # ceiling; the parser refuses the latter rather than reading the
            # fragment as a verdict.
            "stop_reason": getattr(message, "stop_reason", None),
        }
    except Exception as exc:
        logger.error("Claude API call failed during window assessment: %s", exc, exc_info=True)
        return None


def _derive_window_status(
    parsed: ParsedWindowAssessment,
    sample_insufficient: bool,
) -> tuple[str, Optional[str], Optional[dict]]:
    """(status, unassessable_reason, coercion_finding) for a parsed verdict.

    insufficient_sample, decided before the model was asked, still wins.
    Otherwise the status is derived from the per-objective designations. Only
    when the mapped controls publish no objectives at all does the model's
    advisory status stand, and if that is off-contract too the window is
    recorded partial with a finding saying why.
    """
    if sample_insufficient:
        return "insufficient_sample", None, None
    derived, reason = derive_assessment_status(parsed.designations)
    if derived is not None:
        return derived, reason, status_coercion_finding(parsed.model_status, derived)
    if parsed.model_status is not None:
        return parsed.model_status, None, None
    return "partial", None, {
        "category": "quality",
        "level": "info",
        "message": (
            "The mapped controls publish no assessment objectives and the model "
            "returned no usable overall status, so the window is recorded as "
            "'partial' pending human review."
        ),
    }


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
    assessment row with status="error" and the record is returned. No path
    manufactures a verdict the model did not return.
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

    # Membership: asserted effective period first, upload date as fallback.
    member_rows = _select_files_for_window(
        session, organization_id, evidence_id, window_start, window_end,
    )

    # Expected artifact types (union across mapped controls)
    expected_types = _build_expected_artifact_types(session, evidence_id)

    # Look up webhook-payload sources in one batch
    webhook_sources = _fetch_webhook_sources_for_files(
        session, [f.id for f, _rule in member_rows]
    )

    files_in_window: list[_FileInWindow] = []
    for f, rule in member_rows:
        source_label = _infer_source_label(
            filename=f.filename,
            webhook_source_by_file=webhook_sources,
            file_id=f.id,
        )
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
            extracted_text="",
            sha256_hash=f.sha256_hash,
            collector_id=collector_id,
            declared_artifact_types=declared_artifact_types,
            effective_period_start=f.effective_period_start,
            effective_period_end=f.effective_period_end,
            membership_rule=rule,
            storage_config_id=str(f.storage_config_id) if f.storage_config_id else None,
            # Prefer the digest the platform measured over the uploader's claim
            # when both exist; either identifies identical payloads.
            content_hash=getattr(f, "computed_sha256", None) or f.sha256_hash,
        ))

    # Collapse identical payloads before touching storage, then extract text
    # for the representatives until the window's text budget is spent.
    _plan_prompt_content(files_in_window)
    budget_left = WINDOW_TEXT_BUDGET
    for f in files_in_window:
        if f.represented_by is not None:
            continue
        if budget_left <= 0:
            f.omitted_reason = "text_budget"
            continue
        raw = download_evidence_bytes(
            f.s3_key,
            org_id=str(organization_id),
            storage_config_id=f.storage_config_id,
        )
        extracted_text = ""
        if raw:
            extracted = extract_text_from_bytes(
                data=raw, content_type=f.content_type, filename=f.filename
            )
            extracted_text = extracted.text or ""
        f.extracted_text, budget_left = _apply_text_budget(f, extracted_text, budget_left)

    # Coverage counts every member file, shown or not.
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

    # Cache hit? Same window_hash + terminal previous run → return it.
    if (
        assessment
        and assessment.window_hash == window_hash
        and assessment.status in ("sufficient", "partial", "insufficient", "insufficient_sample", "unassessable")
    ):
        logger.info(
            "Window assessment cache hit for org=%s evidence=%s hash=%s",
            organization_id, evidence_id, window_hash[:12],
        )
        return assessment

    file_membership = _membership_snapshot(files_in_window)
    prior_review_note: Optional[dict] = None
    if assessment is None:
        assessment = EvidenceWindowAssessment(
            organization_id=organization_id,
            evidence_id=evidence_id,
            window_start=window_start,
            window_end=window_end,
            frequency_used=frequency_used,
            file_ids=[str(f.id) for f in files_in_window],
            file_membership=file_membership,
            source_coverage=source_coverage,
            artifact_type_coverage=artifact_type_coverage,
            expected_artifact_types=expected_types,
            status="processing",
            assessment_source=assessment_source,
            requested_by_user_id=requested_by_user_id,
            window_hash=window_hash,
            schema_version=WINDOW_SCHEMA_VERSION,
        )

        # A new window starts unreviewed. The prior human disposition is
        # surfaced as a finding below, never copied onto this row's review
        # block — see _prior_review_reference.
        prior_review_note = _prior_review_reference(
            session, organization_id, evidence_id,
        )

        session.add(assessment)
    else:
        assessment.frequency_used = frequency_used
        assessment.file_ids = [str(f.id) for f in files_in_window]
        assessment.file_membership = file_membership
        assessment.source_coverage = source_coverage
        assessment.artifact_type_coverage = artifact_type_coverage
        assessment.expected_artifact_types = expected_types
        assessment.status = "processing"
        assessment.assessment_source = assessment_source
        assessment.requested_by_user_id = requested_by_user_id
        assessment.window_hash = window_hash
        assessment.schema_version = WINDOW_SCHEMA_VERSION
        # A re-run replaces the verdict; nothing from the previous answer may
        # linger beside the new one.
        assessment.ao_findings = []
        assessment.gap_count = 0
        assessment.cannot_assess_count = 0
        assessment.unassessable_reason = None
        assessment.file_effective_dates = []
        # The verdict those columns held is gone, so a decision recorded
        # against it must not sit on the row beside a blank. The terminal
        # write resets it again; this keeps the in-flight row honest.
        assessment.review_decision = None
        assessment.review_reason = None
        assessment.verdict_reviewed_by_user_id = None
        assessment.verdict_reviewed_at = None
    session.commit()

    # Build pre-findings: fallback frequency + insufficient sample + budget
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
            "suggestion": f"Set frequency to one of: {', '.join(o['value'] for o in UI_OPTIONS)}",
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

    budget_note = _text_budget_finding(files_in_window)
    if budget_note is not None:
        pre_findings.append(budget_note)

    # Build control context & prompt
    control_context = assemble_control_context_sync(session, evidence_id)
    if control_context is None:
        _finalise_error(
            session, assessment, start_time,
            "No catalog entry found for evidence ID — cannot assess without control context",
        )
        return assessment

    shown_files = [
        f for f in files_in_window
        if f.represented_by is None and f.omitted_reason is None
    ]
    represented: dict[UUID, list[str]] = {}
    for f in files_in_window:
        if f.represented_by is not None:
            represented.setdefault(f.represented_by, []).append(str(f.id))

    assessment_date = datetime.utcnow().strftime("%Y-%m-%d")
    system_prompt, user_prompt = build_window_assessment_prompt(
        control_context=control_context,
        window_start=window_start.isoformat(),
        window_end=window_end.isoformat(),
        frequency_used=frequency_used,
        files=[
            {
                "file_id": str(f.id),
                "filename": f.filename,
                "content_type": f.content_type,
                "source": f.source_label,
                "uploaded_at": f.uploaded_at.isoformat() if f.uploaded_at else "",
                "text": f.extracted_text,
                "truncated": f.truncated,
                "effective_period_start": (
                    f.effective_period_start.isoformat() if f.effective_period_start else None
                ),
                "effective_period_end": (
                    f.effective_period_end.isoformat() if f.effective_period_end else None
                ),
                "membership_rule": f.membership_rule,
                "represents": represented.get(f.id, []),
            }
            for f in shown_files
        ],
        expected_artifact_types=expected_types,
        source_coverage=source_coverage,
        artifact_type_coverage=artifact_type_coverage,
        assessment_date=assessment_date,
        collection=_collection_context(tracking),
        omitted_files=[
            {
                "file_id": str(f.id),
                "filename": f.filename,
                "source": f.source_label,
                "uploaded_at": f.uploaded_at.isoformat() if f.uploaded_at else "",
            }
            for f in files_in_window
            if f.omitted_reason is not None
        ],
    )
    prompt_hash_value = hash_prompt(system_prompt, user_prompt)

    # If no files in window, skip the LLM call — the coverage finding is
    # enough, and LLM cannot reason about an empty set.
    if not files_in_window:
        assessment.status = "insufficient_sample"
        assessment.relevance_score = Decimal("0.00")
        assessment.findings = _compose_findings(
            pre_findings or [{
                "category": "coverage",
                "level": "insufficient",
                "message": "No evidence files present within the window.",
                "suggestion": "Configure or verify the evidence collector for this evidence ID.",
            }],
            prior_review_note,
            [],
        )
        assessment.summary = "No files in window — nothing to assess."
        assessment.prompt_hash = prompt_hash_value
        assessment.prompt_version = WINDOW_PROMPT_VERSION
        assessment.control_context_hash = control_context.context_hash
        assessment.framework_version = control_context.framework_version
        assessment.model_id = None
        assessment.input_token_count = 0
        assessment.output_token_count = 0
        assessment.cost_cents = Decimal("0.0000")
        assessment.processing_time_ms = int((time.monotonic() - start_time) * 1000)
        assessment.assessed_at = datetime.utcnow()
        _write_window_terminal_verdict(session, assessment)
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

    prompted_ao_ids = [obj["ao_id"] for obj in control_context.objectives]
    try:
        parsed = parse_window_assessment_v2(
            llm.get("content") or "",
            llm.get("stop_reason"),
            prompted_ao_ids,
            [str(f.id) for f in shown_files],
        )
    except AssessmentParseError as exc:
        # An answer that cannot be read is recorded as exactly that. The row
        # says error and why; it does not carry a status the model never gave.
        _finalise_error(
            session, assessment, start_time,
            f"AI response could not be used: {exc.reason}",
            prompt_hash=prompt_hash_value,
            control_context_hash=control_context.context_hash,
            framework_version=control_context.framework_version,
            model_id=llm.get("model"),
            input_tokens=llm.get("input_tokens"),
            output_tokens=llm.get("output_tokens"),
        )
        return assessment

    status, unassessable_reason, coercion_note = _derive_window_status(parsed, sample_insufficient)

    llm_findings = list(parsed.findings)
    if coercion_note is not None:
        llm_findings.append(coercion_note)
    findings = _compose_findings(pre_findings, prior_review_note, llm_findings)

    input_tokens = llm.get("input_tokens", 0)
    output_tokens = llm.get("output_tokens", 0)
    # Price the model that ANSWERED, not the one we asked for — they differ when
    # an undated alias resolves to a dated snapshot, and they differ completely
    # when an operator has set EVIDENCE_AI_MODEL. `None` when the registry has
    # no price for it: a NULL cost reads as "unknown", a computed one reads as
    # fact.
    model_id = llm.get("model") or resolve_model(MODEL_ROLE)
    cost = model_cost_cents(model_id, input_tokens, output_tokens)

    designations = parsed.designations
    assessment.status = status
    assessment.unassessable_reason = unassessable_reason
    assessment.relevance_score = (
        Decimal(str(parsed.relevance_score)) if parsed.relevance_score is not None else None
    )
    assessment.findings = findings
    assessment.ao_findings = parsed.ao_findings
    assessment.gap_count = designations.count("gap_identified")
    assessment.cannot_assess_count = designations.count("cannot_assess")
    assessment.file_effective_dates = parsed.file_effective_dates
    assessment.summary = parsed.summary
    assessment.schema_version = WINDOW_SCHEMA_VERSION
    assessment.model_id = model_id
    assessment.prompt_hash = prompt_hash_value
    assessment.prompt_version = WINDOW_PROMPT_VERSION
    assessment.control_context_hash = control_context.context_hash
    assessment.framework_version = control_context.framework_version
    assessment.input_token_count = input_tokens
    assessment.output_token_count = output_tokens
    assessment.cost_cents = Decimal(str(cost)) if cost is not None else None
    assessment.processing_time_ms = int((time.monotonic() - start_time) * 1000)
    assessment.assessed_at = datetime.utcnow()
    _write_window_terminal_verdict(session, assessment)

    logger.info(
        "Window assessment complete org=%s evidence=%s status=%s score=%s files=%d shown=%d "
        "objectives=%d model=%s cost=%s",
        organization_id, evidence_id, assessment.status, assessment.relevance_score,
        len(files_in_window), len(shown_files), len(prompted_ao_ids), model_id,
        f"{cost:.4f}c" if cost is not None else "unknown (model not priced)",
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
    model_id: Optional[str] = None,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
) -> None:
    assessment.status = "error"
    assessment.findings = [{
        "category": "error",
        "level": "info",
        "message": message,
    }]
    assessment.ao_findings = []
    assessment.gap_count = 0
    assessment.cannot_assess_count = 0
    assessment.unassessable_reason = None
    assessment.file_effective_dates = []
    assessment.summary = message
    assessment.schema_version = WINDOW_SCHEMA_VERSION
    assessment.processing_time_ms = int((time.monotonic() - start_time) * 1000)
    assessment.assessed_at = datetime.utcnow()
    if prompt_hash:
        assessment.prompt_hash = prompt_hash
        # The version travels with the hash: recording one without the other
        # leaves a verdict whose provenance is half-known.
        assessment.prompt_version = WINDOW_PROMPT_VERSION
    if control_context_hash:
        assessment.control_context_hash = control_context_hash
    if framework_version:
        assessment.framework_version = framework_version
    if model_id:
        # The call happened and was paid for even though its answer was
        # unusable; the row says which model answered.
        assessment.model_id = model_id
        assessment.input_token_count = input_tokens
        assessment.output_token_count = output_tokens
    # An error is a terminal outcome and goes into the history like any
    # other: a re-run that succeeds must not erase the record that this one
    # did not.
    _write_window_terminal_verdict(session, assessment)
