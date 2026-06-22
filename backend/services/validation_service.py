"""
Evidence Validation Engine (Issue #218).

Runs four rule-based checks on every evidence file and stores structured
results for dashboard metrics.  Validation is additive — it never blocks
ingestion.  All exceptions are caught and recorded as findings.

Rules:
  1. catalog_exists   — evidence_id is a known ERL catalog entry
  2. content_type_ok  — MIME type in the allowed set
  3. field_coverage   — JSON payload contains expected typical_fields
  4. freshness        — file age vs. org-configured collection frequency
"""
import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import EvidenceFile, EvidenceTracking, EvidenceValidationResult
from catalog_models import SCFCatalogEvidence
from services.storage_service import ALLOWED_CONTENT_TYPES

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Staleness thresholds (days) keyed by collection frequency
# ---------------------------------------------------------------------------
STALENESS_THRESHOLDS: Dict[str, int] = {
    "real_time": 2,
    "daily": 2,
    "weekly": 9,
    "monthly": 35,
    "quarterly": 95,
    "annual": 370,
    "on_demand": 35,  # treat like monthly
}

# Status severity ordering (worst → best)
_STATUS_SEVERITY = {"invalid": 0, "partial": 1, "warning": 2, "valid": 3}

# ---------------------------------------------------------------------------
# Collection interfaces cache (loaded once from JSON on first call)
# ---------------------------------------------------------------------------
_collection_interfaces: Optional[Dict[str, Any]] = None


def _load_collection_interfaces() -> Dict[str, Any]:
    """Load collection_interfaces.json from the webclient public data dir."""
    global _collection_interfaces
    if _collection_interfaces is not None:
        return _collection_interfaces

    # In Docker the webclient data is mounted at /app/data/json/;
    # locally (outside Docker) resolve relative to backend/ -> ../webclient/public/data/
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [
        os.path.join(base_dir, "data", "json", "collection_interfaces.json"),  # Docker mount
        os.path.join(base_dir, "webclient", "public", "data", "collection_interfaces.json"),  # local dev
    ]
    json_path = next((p for p in candidates if os.path.exists(p)), candidates[0])
    try:
        with open(json_path, "r") as f:
            _collection_interfaces = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        logger.warning("Could not load collection_interfaces.json: %s", exc)
        _collection_interfaces = {}
    return _collection_interfaces


def _find_typical_fields(evidence_id: str, area_of_focus: str) -> List[str]:
    """Find typical_fields from collection interfaces matching area_of_focus."""
    interfaces = _load_collection_interfaces()
    domain_lower = area_of_focus.lower() if area_of_focus else ""

    # Try to find a matching interface by domain substring
    for _ci_id, ci in interfaces.items():
        ci_domain = (ci.get("domain") or "").lower()
        if ci_domain and ci_domain in domain_lower:
            fields = ci.get("typical_fields", [])
            if fields:
                return fields

    return []


def _worst_status(findings: List[Dict[str, str]]) -> str:
    """Return the worst (most severe) status level across all findings."""
    worst = "valid"
    for f in findings:
        level = f.get("level", "valid")
        if _STATUS_SEVERITY.get(level, 3) < _STATUS_SEVERITY.get(worst, 3):
            worst = level
    return worst


# ---------------------------------------------------------------------------
# Individual rules
# ---------------------------------------------------------------------------


async def _rule_catalog_exists(
    db: AsyncSession, evidence_id: str
) -> Dict[str, str]:
    """Rule 1: Check evidence_id is a known ERL catalog entry."""
    result = await db.execute(
        select(SCFCatalogEvidence).where(
            SCFCatalogEvidence.evidence_id == evidence_id
        )
    )
    entry = result.scalar_one_or_none()
    if entry:
        return {
            "rule": "catalog_exists",
            "level": "valid",
            "message": f"Evidence ID '{evidence_id}' found in ERL catalog",
            "detail": entry.artifact_title,
        }
    return {
        "rule": "catalog_exists",
        "level": "invalid",
        "message": f"Evidence ID '{evidence_id}' is not a recognised ERL catalog entry",
    }


def _rule_content_type_ok(content_type: str) -> Dict[str, str]:
    """Rule 2: Check MIME type against allowed set."""
    if content_type in ALLOWED_CONTENT_TYPES:
        return {
            "rule": "content_type_ok",
            "level": "valid",
            "message": f"Content type '{content_type}' is in the allowed set",
        }
    return {
        "rule": "content_type_ok",
        "level": "warning",
        "message": f"Content type '{content_type}' is not in the standard allowed set",
        "detail": "File was accepted but the content type is unusual for evidence uploads",
    }


def _rule_field_coverage(
    payload_json: Optional[Dict[str, Any]],
    evidence_id: str,
    area_of_focus: str,
) -> tuple[Dict[str, str], Optional[float]]:
    """Rule 3: Check JSON payload field coverage against typical_fields.

    Returns (finding_dict, completeness_score).
    """
    if payload_json is None:
        return (
            {
                "rule": "field_coverage",
                "level": "valid",
                "message": "Non-JSON file — field coverage check skipped",
            },
            None,
        )

    typical = _find_typical_fields(evidence_id, area_of_focus)
    if not typical:
        return (
            {
                "rule": "field_coverage",
                "level": "valid",
                "message": "No typical_fields defined for this evidence domain — check skipped",
            },
            None,
        )

    # Flatten payload keys (check top-level and nested 'data' key)
    payload_keys = set(payload_json.keys())
    data_section = payload_json.get("data")
    if isinstance(data_section, dict):
        payload_keys |= set(data_section.keys())

    present = [f for f in typical if f in payload_keys]
    score = len(present) / len(typical) if typical else 1.0

    if score >= 1.0:
        level = "valid"
        msg = f"All {len(typical)} typical fields present"
    elif score >= 0.7:
        missing = [f for f in typical if f not in payload_keys]
        level = "partial"
        msg = f"{len(present)}/{len(typical)} typical fields present (missing: {', '.join(missing)})"
    else:
        missing = [f for f in typical if f not in payload_keys]
        level = "partial"
        msg = f"Only {len(present)}/{len(typical)} typical fields present — consider enriching payload (missing: {', '.join(missing)})"

    return (
        {"rule": "field_coverage", "level": level, "message": msg},
        round(score, 4),
    )


async def _rule_freshness(
    db: AsyncSession,
    evidence_file: EvidenceFile,
) -> Dict[str, str]:
    """Rule 4: Check file freshness against org collection frequency."""
    # Look up EvidenceTracking for this evidence_id + org
    result = await db.execute(
        select(EvidenceTracking).where(
            EvidenceTracking.organization_id == evidence_file.organization_id,
            EvidenceTracking.evidence_id == evidence_file.evidence_id,
        )
    )
    tracking = result.scalar_one_or_none()

    if not tracking or not tracking.frequency:
        return {
            "rule": "freshness",
            "level": "valid",
            "message": "No collection frequency configured — freshness check skipped",
        }

    frequency = tracking.frequency.lower().strip()
    threshold_days = STALENESS_THRESHOLDS.get(frequency)
    if threshold_days is None:
        return {
            "rule": "freshness",
            "level": "valid",
            "message": f"Unknown frequency '{tracking.frequency}' — freshness check skipped",
        }

    uploaded_at = evidence_file.uploaded_at or datetime.utcnow()
    age_days = (datetime.utcnow() - uploaded_at).days

    if age_days <= threshold_days:
        return {
            "rule": "freshness",
            "level": "valid",
            "message": f"File is {age_days}d old (threshold: {threshold_days}d for {frequency} collection)",
        }
    return {
        "rule": "freshness",
        "level": "warning",
        "message": f"File is {age_days}d old — stale for {frequency} collection (threshold: {threshold_days}d)",
    }


async def _rule_s3_object_exists(evidence_file: "EvidenceFile") -> Dict[str, str]:
    """Rule 5: Verify the storage object backing this EvidenceFile record exists.

    Detects the inbox write bug (Issue #400) and any future storage lifecycle /
    deletion issues that leave orphaned DB records.
    Works with both S3 and Azure Blob Storage via storage_service facade.
    """
    from services.storage_service import is_configured, check_object_exists

    if not is_configured():
        return {
            "rule": "s3_object_exists",
            "level": "warning",
            "message": "Evidence storage not configured — existence check skipped",
        }

    if evidence_file.scan_status == "quarantined":
        return {
            "rule": "s3_object_exists",
            "level": "valid",
            "message": "File is quarantined — existence check skipped",
        }

    try:
        if check_object_exists(evidence_file.s3_key):
            return {
                "rule": "s3_object_exists",
                "level": "valid",
                "message": f"Storage object verified at {evidence_file.s3_key}",
            }
        else:
            return {
                "rule": "s3_object_exists",
                "level": "invalid",
                "message": (
                    f"Storage object missing at {evidence_file.s3_key} — "
                    "DB record references a non-existent file"
                ),
                "detail": "s3_object_missing",
            }
    except Exception as exc:
        return {
            "rule": "s3_object_exists",
            "level": "warning",
            "message": f"Could not verify storage object existence ({exc})",
        }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def run_validation(
    db: AsyncSession,
    evidence_file: EvidenceFile,
    payload_json: Optional[Dict[str, Any]] = None,
    validation_source: str = "manual_upload",
) -> EvidenceValidationResult:
    """Run all four validation rules and store the result.

    This function NEVER raises — all exceptions are caught and stored
    as invalid findings so the upload/webhook flow is never interrupted.

    If a result already exists for this file it is updated (upsert).
    """
    try:
        findings: List[Dict[str, str]] = []
        completeness_score: Optional[float] = None

        # Rule 1: Catalog existence
        catalog_finding = await _rule_catalog_exists(db, evidence_file.evidence_id)
        findings.append(catalog_finding)

        # Get area_of_focus from catalog for Rule 3
        area_of_focus = ""
        if catalog_finding["level"] == "valid":
            cat_result = await db.execute(
                select(SCFCatalogEvidence).where(
                    SCFCatalogEvidence.evidence_id == evidence_file.evidence_id
                )
            )
            cat_entry = cat_result.scalar_one_or_none()
            if cat_entry:
                area_of_focus = cat_entry.area_of_focus or ""

        # Rule 2: Content type
        findings.append(_rule_content_type_ok(evidence_file.content_type))

        # Rule 3: Field coverage (JSON payloads only)
        coverage_finding, score = _rule_field_coverage(
            payload_json, evidence_file.evidence_id, area_of_focus
        )
        findings.append(coverage_finding)
        if score is not None:
            completeness_score = score

        # Rule 4: Freshness
        freshness_finding = await _rule_freshness(db, evidence_file)
        findings.append(freshness_finding)

        # Rule 5: S3 object existence
        s3_finding = await _rule_s3_object_exists(evidence_file)
        findings.append(s3_finding)

        # Overall status = worst level across findings
        overall_status = _worst_status(findings)

    except Exception as exc:
        logger.error(
            "Validation engine error for file %s: %s",
            evidence_file.id, exc, exc_info=True,
        )
        findings = [
            {
                "rule": "engine_error",
                "level": "invalid",
                "message": f"Validation engine encountered an error: {str(exc)[:500]}",
            }
        ]
        overall_status = "invalid"
        completeness_score = None

    # Upsert: check for existing result
    existing_result = await db.execute(
        select(EvidenceValidationResult).where(
            EvidenceValidationResult.evidence_file_id == evidence_file.id
        )
    )
    validation_result = existing_result.scalar_one_or_none()

    if validation_result:
        # Update existing
        validation_result.status = overall_status
        validation_result.completeness_score = completeness_score
        validation_result.findings = findings
        validation_result.validation_source = validation_source
        validation_result.validated_at = datetime.utcnow()
    else:
        # Create new
        validation_result = EvidenceValidationResult(
            evidence_file_id=evidence_file.id,
            organization_id=evidence_file.organization_id,
            evidence_id=evidence_file.evidence_id,
            status=overall_status,
            completeness_score=completeness_score,
            findings=findings,
            validation_source=validation_source,
        )
        db.add(validation_result)

    await db.flush()
    return validation_result
