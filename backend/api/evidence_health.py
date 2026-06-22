"""
Evidence Health Dashboard API (Issue #220).

Provides aggregated evidence freshness data across an organisation,
using traffic-light indicators (green/amber/red) based on staleness thresholds.

Endpoints:
  GET /organizations/{org_id}/evidence-health
  GET /organizations/{org_id}/evidence-health/upcoming
  GET /organizations/{org_id}/evidence/frequency-health   — M4 PR 2 (#574)
"""
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, Query
from sqlalchemy import select, func as sa_func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_org_role, OrgMembership
from database import get_db
from models import EvidenceTracking, EvidenceFile, EvidenceValidationResult, EvidenceHealthConfig, EvidenceAssessment
from catalog_models import SCFCatalogEvidence
from schemas import (
    EvidenceHealthResponse,
    EvidenceHealthItem,
    EvidenceHealthSummaryStats,
    FrequencyHealthItem,
    FrequencyHealthResponse,
)
from rate_limiting import rate_limit_read
from services.validation_service import STALENESS_THRESHOLDS
from services import frequency_health_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["evidence-health"])

# Default staleness thresholds (days) when no per-org config exists
DEFAULT_WARNING_DAYS = 30
DEFAULT_CRITICAL_DAYS = 60


def _calculate_status(
    days_since: Optional[int],
    threshold_days: Optional[int],
) -> str:
    """Determine traffic-light status based on staleness.

    Green: within threshold
    Amber: within 1.5x threshold
    Red: beyond 1.5x threshold or no data
    Unknown: no threshold configured
    """
    if days_since is None or threshold_days is None:
        return "unknown"
    if days_since <= threshold_days:
        return "green"
    if days_since <= int(threshold_days * 1.5):
        return "amber"
    return "red"


@router.get(
    "/organizations/{org_id}/evidence-health",
    response_model=EvidenceHealthResponse,
    summary="Get evidence health dashboard data",
    description="""
    Returns aggregated evidence freshness data for the organisation.

    Each tracked evidence item includes:
    - Last upload timestamp and days since
    - Traffic-light status (green/amber/red) based on staleness thresholds
    - File count and latest validation status
    - Summary statistics (% green/amber/red)

    **Staleness thresholds** default to the collection frequency but can be
    overridden per evidence item via the evidence_health_config table.
    """,
)
@rate_limit_read
async def get_evidence_health(
    request: Request,
    response: Response,
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """Get evidence health overview for an organisation."""
    now = datetime.utcnow()

    # 1. Get all tracked evidence for this org
    tracking_result = await db.execute(
        select(EvidenceTracking).where(
            and_(
                EvidenceTracking.organization_id == org_id,
                EvidenceTracking.is_tracked == True,
            )
        )
    )
    tracked_items = tracking_result.scalars().all()

    if not tracked_items:
        return EvidenceHealthResponse(
            summary=EvidenceHealthSummaryStats(
                total_tracked=0, green_count=0, amber_count=0, red_count=0, unknown_count=0,
            ),
            items=[],
        )

    # 2. Get per-org health config overrides
    config_result = await db.execute(
        select(EvidenceHealthConfig).where(
            EvidenceHealthConfig.organization_id == org_id,
        )
    )
    config_map: Dict[str, EvidenceHealthConfig] = {
        c.evidence_id: c for c in config_result.scalars().all()
    }

    # 3. Get latest evidence file per evidence_id
    latest_files_subq = (
        select(
            EvidenceFile.evidence_id,
            sa_func.max(EvidenceFile.uploaded_at).label("latest_upload"),
            sa_func.count(EvidenceFile.id).label("file_count"),
        )
        .where(
            and_(
                EvidenceFile.organization_id == org_id,
                EvidenceFile.is_deleted == False,
            )
        )
        .group_by(EvidenceFile.evidence_id)
        .subquery()
    )
    files_result = await db.execute(select(latest_files_subq))
    file_data = {row.evidence_id: row for row in files_result.all()}

    # 3b. Get latest AI assessment per evidence_id (most recent assessed_at, excluding errors)
    assessment_subq = (
        select(
            EvidenceAssessment.evidence_id,
            EvidenceAssessment.status.label("assessment_status"),
            EvidenceAssessment.relevance_score.label("assessment_score"),
            sa_func.row_number().over(
                partition_by=EvidenceAssessment.evidence_id,
                order_by=EvidenceAssessment.assessed_at.desc().nullslast(),
            ).label("rn"),
        )
        .where(
            and_(
                EvidenceAssessment.organization_id == org_id,
                EvidenceAssessment.status.notin_(["pending", "processing", "error"]),
            )
        )
        .subquery()
    )
    latest_assessments = (
        select(
            assessment_subq.c.evidence_id,
            assessment_subq.c.assessment_status,
            assessment_subq.c.assessment_score,
        )
        .where(assessment_subq.c.rn == 1)
        .subquery()
    )
    assessment_result = await db.execute(select(latest_assessments))
    assessment_data = {row.evidence_id: row for row in assessment_result.all()}

    # 3c. Bulk fetch control_mappings from the SCF Evidence catalog for every
    # tracked evidence_id (needed by consumers that filter by control scope,
    # e.g. capability-theme evidence cards).
    catalog_result = await db.execute(
        select(
            SCFCatalogEvidence.evidence_id,
            SCFCatalogEvidence.control_mappings,
        ).where(
            SCFCatalogEvidence.evidence_id.in_([t.evidence_id for t in tracked_items])
        )
    )
    control_mappings_by_eid: Dict[str, List[str]] = {
        row.evidence_id: list(row.control_mappings or []) for row in catalog_result.all()
    }

    # 4. Build health items
    items = []
    counts = {"green": 0, "amber": 0, "red": 0, "unknown": 0}

    for tracking in tracked_items:
        eid = tracking.evidence_id
        file_info = file_data.get(eid)

        # Determine threshold
        config = config_map.get(eid)
        if config:
            threshold_days = config.staleness_warning_days
        elif tracking.frequency:
            threshold_days = STALENESS_THRESHOLDS.get(tracking.frequency.lower().strip())
        else:
            threshold_days = DEFAULT_WARNING_DAYS

        # Calculate days since last upload
        days_since = None
        last_upload = None
        file_count = 0
        if file_info:
            last_upload = file_info.latest_upload
            file_count = file_info.file_count
            if last_upload:
                days_since = (now - last_upload).days

        status = _calculate_status(days_since, threshold_days)
        counts[status] = counts.get(status, 0) + 1

        assessment_info = assessment_data.get(eid)
        items.append(EvidenceHealthItem(
            evidence_id=eid,
            collecting_system=tracking.collecting_system,
            frequency=tracking.frequency,
            last_file_uploaded_at=last_upload,
            days_since_upload=days_since,
            staleness_threshold_days=threshold_days,
            status=status,
            file_count=file_count,
            latest_assessment_status=assessment_info.assessment_status if assessment_info else None,
            latest_assessment_score=float(assessment_info.assessment_score) if assessment_info and assessment_info.assessment_score is not None else None,
            control_mappings=control_mappings_by_eid.get(eid, []),
        ))

    total = len(items)
    summary = EvidenceHealthSummaryStats(
        total_tracked=total,
        green_count=counts["green"],
        amber_count=counts["amber"],
        red_count=counts["red"],
        unknown_count=counts["unknown"],
        green_pct=round(counts["green"] / total * 100, 1) if total else 0,
        amber_pct=round(counts["amber"] / total * 100, 1) if total else 0,
        red_pct=round(counts["red"] / total * 100, 1) if total else 0,
    )

    return EvidenceHealthResponse(summary=summary, items=items)


@router.get(
    "/organizations/{org_id}/evidence-health/upcoming",
    summary="Get upcoming evidence collection deadlines",
    description="Returns evidence items where the next collection is due within N days based on frequency and last upload.",
)
@rate_limit_read
async def get_upcoming_evidence(
    request: Request,
    response: Response,
    org_id: UUID,
    days: int = Query(default=14, ge=1, le=90),
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """Get evidence items with upcoming collection deadlines."""
    now = datetime.utcnow()

    tracking_result = await db.execute(
        select(EvidenceTracking).where(
            and_(
                EvidenceTracking.organization_id == org_id,
                EvidenceTracking.is_tracked == True,
            )
        )
    )
    tracked_items = tracking_result.scalars().all()

    # Get latest upload dates
    latest_files_subq = (
        select(
            EvidenceFile.evidence_id,
            sa_func.max(EvidenceFile.uploaded_at).label("latest_upload"),
            sa_func.count(EvidenceFile.id).label("file_count"),
        )
        .where(
            and_(
                EvidenceFile.organization_id == org_id,
                EvidenceFile.is_deleted == False,
            )
        )
        .group_by(EvidenceFile.evidence_id)
        .subquery()
    )
    files_result = await db.execute(select(latest_files_subq))
    file_data = {row.evidence_id: row for row in files_result.all()}

    upcoming: List[dict] = []
    for tracking in tracked_items:
        eid = tracking.evidence_id
        file_info = file_data.get(eid)
        threshold_days = STALENESS_THRESHOLDS.get(
            (tracking.frequency or "").lower().strip()
        ) or DEFAULT_WARNING_DAYS

        last_upload = file_info.latest_upload if file_info else None
        if last_upload:
            next_due = last_upload + timedelta(days=threshold_days)
            days_until_due = (next_due - now).days
        else:
            next_due = None
            days_until_due = -999  # Never uploaded = overdue

        if days_until_due <= days:
            upcoming.append({
                "evidence_id": eid,
                "evidence_name": getattr(tracking, "evidence_name", None),
                "frequency": tracking.frequency,
                "collecting_system": tracking.collecting_system,
                "last_uploaded_at": last_upload.isoformat() if last_upload else None,
                "next_due": next_due.isoformat() if next_due else None,
                "days_until_due": days_until_due,
                "is_overdue": days_until_due < 0,
                "file_count": file_info.file_count if file_info else 0,
            })

    upcoming.sort(key=lambda x: x["days_until_due"])
    return {"items": upcoming, "total": len(upcoming)}


# ---------------------------------------------------------------------------
# Frequency Health (M4 PR 2, #574 — ISC-17..22)
# ---------------------------------------------------------------------------

def _truncate_to_5min(dt: datetime) -> datetime:
    """Round ``dt`` down to a 5-minute bucket for ETag stability (ISC-20)."""
    return dt.replace(minute=(dt.minute // 5) * 5, second=0, microsecond=0)


def _frequency_health_etag(report) -> str:
    """ETag = sha256(computed_at_5min || misaligned_count || sha256(item_evidence_ids_sorted)).

    Per ISC-20 — 5-min compute granularity matches typical dashboard refresh
    rate. Browser revalidation hits 304 most of the time; the panel only
    flips when an evidence_id transitions or a new file arrives.
    """
    sorted_ids = sorted(i.evidence_id for i in report.items)
    inner = hashlib.sha256("|".join(sorted_ids).encode("utf-8")).hexdigest()
    base = (
        f"{_truncate_to_5min(report.computed_at).isoformat()}|"
        f"{report.misaligned_count}|{inner}"
    )
    digest = hashlib.sha256(base.encode("utf-8")).hexdigest()
    return f'W/"{digest}"'


def _etag_match(if_none_match: Optional[str], etag: str) -> bool:
    """Honour weak-ETag matching per RFC 7232 §3.2."""
    if not if_none_match:
        return False

    def _strip(token: str) -> str:
        token = token.strip()
        if token.startswith("W/"):
            token = token[2:]
        return token.strip().strip('"')

    candidates = {_strip(t) for t in if_none_match.split(",")}
    return _strip(etag) in candidates or "*" in candidates


@router.get(
    "/organizations/{org_id}/evidence/frequency-health",
    response_model=FrequencyHealthResponse,
    summary="Frequency-vs-cadence health report",
    description="""
    Detect mismatches between declared `EvidenceTracking.frequency` and the
    observed cadence inferred from `EvidenceFile.uploaded_at` histograms
    over the last 90 days.

    Response `items` only contains misaligned rows (ISC-19); low-confidence
    non-misaligned entries are summed in `low_confidence_count` for
    awareness. ETag is set on every 200 response and honoured for
    `If-None-Match` (304). 5-minute compute granularity (ISC-20).
    """,
)
async def get_frequency_health(
    request: Request,
    response: Response,
    org_id: UUID,
    if_none_match: Optional[str] = Header(default=None, alias="If-None-Match"),
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """Frequency Health endpoint (ISC-17, ISC-22 viewer auth)."""
    report = await frequency_health_service.compute_for_org(db, org_id)

    etag = _frequency_health_etag(report)
    if _etag_match(if_none_match, etag):
        # 304 — no body, must carry ETag.
        return Response(status_code=304, headers={"ETag": etag})

    # Items: only misaligned rows (ISC-19).
    items: List[FrequencyHealthItem] = [
        FrequencyHealthItem(
            evidence_id=obs.evidence_id,
            declared_frequency=obs.declared_frequency,
            suggested_frequency=obs.suggested_frequency,
            observed_cadence_days=obs.observed_cadence_days,
            confidence=obs.confidence,
            file_count=obs.file_count,
            misaligned=obs.misaligned,
            reason=obs.reason,
        )
        for obs in report.items
        if obs.misaligned
    ]

    response.headers["ETag"] = etag
    return FrequencyHealthResponse(
        organization_id=org_id,
        computed_at=report.computed_at,
        evaluation_window_days=frequency_health_service.EVALUATION_WINDOW_DAYS,
        total_evidence_ids_evaluated=report.total_evidence_ids_evaluated,
        misaligned_count=report.misaligned_count,
        low_confidence_count=report.low_confidence_count,
        items=items,
    )
