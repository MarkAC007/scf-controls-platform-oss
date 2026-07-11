"""
Async service layer for vendor AI assessments (unified vendor_assessments).

Provides:
    - trigger_assessment(): validates inputs, creates the unified
      VendorAssessment row (job_id, status='pending'), dispatches the Celery task
    - get_status(): returns current job status
    - get_results(): returns full results for a job (legacy DPSIA shape)
    - get_latest(): returns the most recent completed AI assessment for a vendor
    - get_active(): returns the pending/running AI assessment, if any

The result dictionaries keep the legacy DPSIA response shape so the
deprecated /dpsia/* alias endpoints continue to work unchanged.
"""
import json
import logging
import uuid
from datetime import date
from typing import Any, Dict, Optional

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from models import Organization, Vendor, VendorAssessment

logger = logging.getLogger(__name__)

CACHE_KEY_PREFIX = "scf:cache:v1:dpsia"

# API-facing assessment types -> engine (legacy DPSIA) vocabulary
API_TO_ENGINE_TYPE = {
    "initial": "new",
    "annual": "annual-review",
    "adhoc": "adhoc",
}
# Legacy DPSIA trigger vocabulary -> unified platform storage values
LEGACY_TO_API_TYPE = {
    "new": "initial",
    "annual-review": "annual",
    "adhoc": "adhoc",
}
# Platform storage values -> legacy DPSIA vocabulary (for alias responses)
API_TO_LEGACY_TYPE = {
    "initial": "new",
    "annual": "annual-review",
    "periodic": "annual-review",
    "triggered": "adhoc",
    "adhoc": "adhoc",
}


def _ai_assessment_filter():
    """AI assessment rows are those carrying a job_id."""
    return VendorAssessment.job_id.isnot(None)


async def trigger_assessment(
    db: AsyncSession,
    vendor_id: str,
    organization_id: str,
    services_used: str,
    user_id: Optional[str] = None,
    assessment_type: str = "initial",
    data_role: str = "Processor",
    client_name: Optional[str] = None,
    additional_context: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Trigger a new AI assessment for the given vendor.

    `assessment_type` accepts the unified API vocabulary (initial | annual |
    adhoc) or the legacy DPSIA vocabulary (new | annual-review | adhoc).

    1. Validates vendor exists.
    2. Checks no AI assessment is already running.
    3. Creates a unified VendorAssessment row with status='pending'.
    4. Dispatches the Celery task.

    Returns dict with assessment_id, job_id and status.
    Raises ValueError if vendor not found or assessment already running.
    """
    # Normalise legacy vocabulary to the unified storage values
    stored_type = LEGACY_TO_API_TYPE.get(assessment_type, assessment_type)
    if stored_type not in API_TO_ENGINE_TYPE:
        raise ValueError(f"Invalid assessment_type '{assessment_type}'")
    engine_type = API_TO_ENGINE_TYPE[stored_type]

    result = await db.execute(select(Vendor).where(Vendor.id == vendor_id))
    vendor = result.scalar_one_or_none()
    if not vendor:
        raise ValueError(f"Vendor {vendor_id} not found")

    # Check for already-running AI assessment
    running = await db.execute(
        select(VendorAssessment).where(
            VendorAssessment.vendor_id == vendor_id,
            _ai_assessment_filter(),
            VendorAssessment.status.in_(["pending", "running"]),
        )
    )
    if running.first():
        raise ValueError("An AI assessment is already in progress for this vendor")

    # Default the client name to the organisation name (used as the report
    # addressee by the engine).
    if not client_name:
        org_result = await db.execute(
            select(Organization.name).where(Organization.id == organization_id)
        )
        client_name = org_result.scalar_one_or_none()

    job_id = f"dpsia-{uuid.uuid4().hex[:12]}"
    row = VendorAssessment(
        vendor_id=vendor.id,
        assessment_type=stored_type,
        assessment_date=date.today(),
        status="pending",
        job_id=job_id,
        data_role=data_role,
        services_used=services_used,
        client_name=client_name,
        additional_context=additional_context,
        triggered_by_user_id=user_id,
        created_by_user_id=user_id,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    from celery_app import celery_app
    celery_app.send_task(
        "tasks_vendor_assessment.run_vendor_assessment",
        kwargs={
            "vendor_id": str(vendor.id),
            "organization_id": str(organization_id),
            "job_id": job_id,
            "vendor_name": vendor.name,
            "vendor_description": vendor.description or "",
            "vendor_website": vendor.website or "",
            "services_used": services_used,
            "assessment_type": engine_type,
            "data_role": data_role,
            "client_name": client_name or "",
            "additional_context": additional_context or "",
        },
    )

    logger.info(f"Triggered AI assessment {job_id} (assessment {row.id}) for vendor {vendor.name}")

    return {
        "assessment_id": str(row.id),
        "job_id": job_id,
        "vendor_id": str(vendor.id),
        "status": "pending",
    }


async def get_status(
    db: AsyncSession,
    vendor_id: str,
    job_id: str,
) -> Optional[Dict[str, Any]]:
    """Return the current status of an AI assessment job."""
    result = await db.execute(
        select(VendorAssessment).where(
            VendorAssessment.vendor_id == vendor_id,
            VendorAssessment.job_id == job_id,
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        return None
    return _status_dict(row)


async def get_results(
    db: AsyncSession,
    vendor_id: str,
    job_id: str,
) -> Optional[Dict[str, Any]]:
    """Return full results for an AI assessment job. Tries Redis cache first."""
    try:
        from redis_client import get_redis_client
        redis = await get_redis_client()
        cache_key = f"{CACHE_KEY_PREFIX}:{vendor_id}:{job_id}"
        cached = await redis.get(cache_key)
        if cached:
            logger.debug(f"Cache hit for assessment job {job_id}")
            return json.loads(cached)
    except Exception as exc:
        logger.warning(f"Redis cache read failed for assessment job {job_id}: {exc}")

    result = await db.execute(
        select(VendorAssessment).where(
            VendorAssessment.vendor_id == vendor_id,
            VendorAssessment.job_id == job_id,
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        return None

    return _row_to_dict(row)


async def get_latest(
    db: AsyncSession,
    vendor_id: str,
) -> Optional[Dict[str, Any]]:
    """Return the most recent completed AI assessment for a vendor."""
    try:
        from redis_client import get_redis_client
        redis = await get_redis_client()
        latest_key = f"{CACHE_KEY_PREFIX}:{vendor_id}:latest"
        cached = await redis.get(latest_key)
        if cached:
            logger.debug(f"Cache hit for latest assessment of vendor {vendor_id}")
            return json.loads(cached)
    except Exception as exc:
        logger.warning(f"Redis cache read failed for vendor {vendor_id} latest assessment: {exc}")

    row = await get_latest_row(db, vendor_id)
    if not row:
        return None
    return _row_to_dict(row)


async def get_latest_row(
    db: AsyncSession,
    vendor_id: str,
) -> Optional[VendorAssessment]:
    """Return the most recent completed AI VendorAssessment ORM row."""
    result = await db.execute(
        select(VendorAssessment)
        .where(
            VendorAssessment.vendor_id == vendor_id,
            _ai_assessment_filter(),
            VendorAssessment.status == "completed",
        )
        .order_by(desc(VendorAssessment.created_at))
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_active(
    db: AsyncSession,
    vendor_id: str,
) -> Optional[Dict[str, Any]]:
    """Return the most recent pending/running AI assessment for a vendor, if any."""
    result = await db.execute(
        select(VendorAssessment)
        .where(
            VendorAssessment.vendor_id == vendor_id,
            _ai_assessment_filter(),
            VendorAssessment.status.in_(["pending", "running"]),
        )
        .order_by(desc(VendorAssessment.created_at))
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if not row:
        return None
    return _status_dict(row)


def _status_dict(row: VendorAssessment) -> Dict[str, Any]:
    """Status-shaped dictionary (legacy DPSIAStatusResponse shape + assessment_id)."""
    return {
        "assessment_id": str(row.id),
        "job_id": row.job_id,
        "vendor_id": str(row.vendor_id),
        "status": row.status,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "error_message": row.error_message,
    }


def _row_to_dict(row: VendorAssessment) -> Dict[str, Any]:
    """Convert a unified VendorAssessment row to the legacy DPSIA result shape."""
    return {
        "assessment_id": str(row.id),
        "job_id": row.job_id,
        "vendor_id": str(row.vendor_id),
        "status": row.status,
        "assessment_type": API_TO_LEGACY_TYPE.get(row.assessment_type, row.assessment_type),
        "data_role": row.data_role,
        "rag_status": row.rag_status,
        "recommendation": row.recommendation,
        "risk_score": row.final_risk_score,
        "risk_level": row.risk_level,
        "executive_summary": row.executive_summary,
        "report_markdown": row.report_markdown,
        "report_json": row.report_json,
        "report_filename": None,
        "research_sources": row.research_sources,
        "linked_assessment_id": str(row.id),
        "linked_report_id": None,
        "processing_time_ms": row.processing_time_ms,
        "error_message": row.error_message,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
