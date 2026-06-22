"""
Async service layer for DPSIA Lambda vendor assessments.

Provides:
    - trigger_assessment(): validates inputs, creates DB row, dispatches Celery task
    - get_status(): returns current job status
    - get_results(): returns full results for a completed job
    - get_latest(): returns the most recent completed DPSIA for a vendor
"""
import json
import logging
import uuid
from typing import Any, Dict, Optional

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from models import VendorDPSIAAssessment, Vendor

logger = logging.getLogger(__name__)

CACHE_KEY_PREFIX = "scf:cache:v1:dpsia"


async def trigger_assessment(
    db: AsyncSession,
    vendor_id: str,
    organization_id: str,
    services_used: str,
    user_id: Optional[str] = None,
    assessment_type: str = "new",
    data_role: str = "Processor",
    client_name: Optional[str] = None,
    additional_context: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Trigger a new DPSIA assessment for the given vendor.

    1. Validates vendor exists.
    2. Checks no assessment is already running.
    3. Creates a VendorDPSIAAssessment row with status='pending'.
    4. Dispatches the Celery task.

    Returns dict with job_id and status.
    Raises ValueError if vendor not found or assessment already running.
    """
    result = await db.execute(select(Vendor).where(Vendor.id == vendor_id))
    vendor = result.scalar_one_or_none()
    if not vendor:
        raise ValueError(f"Vendor {vendor_id} not found")

    # Check for already-running assessment
    running = await db.execute(
        select(VendorDPSIAAssessment).where(
            VendorDPSIAAssessment.vendor_id == vendor_id,
            VendorDPSIAAssessment.status.in_(["pending", "running"]),
        )
    )
    if running.scalar_one_or_none():
        raise ValueError("A DPSIA assessment is already in progress for this vendor")

    job_id = f"dpsia-{uuid.uuid4().hex[:12]}"
    row = VendorDPSIAAssessment(
        vendor_id=vendor.id,
        organization_id=organization_id,
        job_id=job_id,
        status="pending",
        assessment_type=assessment_type,
        data_role=data_role,
        services_used=services_used,
        client_name=client_name,
        additional_context=additional_context,
        triggered_by_user_id=user_id,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    from celery_app import celery_app
    celery_app.send_task(
        "tasks_dpsia.dpsia_invoke_lambda",
        kwargs={
            "vendor_id": str(vendor.id),
            "organization_id": str(organization_id),
            "job_id": job_id,
            "vendor_name": vendor.name,
            "vendor_description": vendor.description or "",
            "vendor_website": vendor.website or "",
            "services_used": services_used,
            "assessment_type": assessment_type,
            "data_role": data_role,
            "client_name": client_name or "",
            "additional_context": additional_context or "",
        },
    )

    logger.info(f"Triggered DPSIA assessment {job_id} for vendor {vendor.name}")

    return {
        "job_id": job_id,
        "vendor_id": str(vendor.id),
        "status": "pending",
    }


async def get_status(
    db: AsyncSession,
    vendor_id: str,
    job_id: str,
) -> Optional[Dict[str, Any]]:
    """Return the current status of a DPSIA assessment job."""
    result = await db.execute(
        select(VendorDPSIAAssessment).where(
            VendorDPSIAAssessment.vendor_id == vendor_id,
            VendorDPSIAAssessment.job_id == job_id,
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        return None

    return {
        "job_id": row.job_id,
        "vendor_id": str(row.vendor_id),
        "status": row.status,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "error_message": row.error_message,
    }


async def get_results(
    db: AsyncSession,
    vendor_id: str,
    job_id: str,
) -> Optional[Dict[str, Any]]:
    """Return full results for a completed DPSIA assessment. Tries Redis cache first."""
    try:
        from redis_client import get_redis_client
        redis = await get_redis_client()
        cache_key = f"{CACHE_KEY_PREFIX}:{vendor_id}:{job_id}"
        cached = await redis.get(cache_key)
        if cached:
            logger.debug(f"Cache hit for DPSIA job {job_id}")
            return json.loads(cached)
    except Exception as exc:
        logger.warning(f"Redis cache read failed for DPSIA job {job_id}: {exc}")

    result = await db.execute(
        select(VendorDPSIAAssessment).where(
            VendorDPSIAAssessment.vendor_id == vendor_id,
            VendorDPSIAAssessment.job_id == job_id,
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
    """Return the most recent completed DPSIA for a vendor."""
    try:
        from redis_client import get_redis_client
        redis = await get_redis_client()
        latest_key = f"{CACHE_KEY_PREFIX}:{vendor_id}:latest"
        cached = await redis.get(latest_key)
        if cached:
            logger.debug(f"Cache hit for latest DPSIA of vendor {vendor_id}")
            return json.loads(cached)
    except Exception as exc:
        logger.warning(f"Redis cache read failed for vendor {vendor_id} latest DPSIA: {exc}")

    result = await db.execute(
        select(VendorDPSIAAssessment)
        .where(
            VendorDPSIAAssessment.vendor_id == vendor_id,
            VendorDPSIAAssessment.status == "completed",
        )
        .order_by(desc(VendorDPSIAAssessment.created_at))
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if not row:
        return None

    return _row_to_dict(row)


async def get_active(
    db: AsyncSession,
    vendor_id: str,
) -> Optional[Dict[str, Any]]:
    """Return the most recent pending/running DPSIA job for a vendor, if any."""
    result = await db.execute(
        select(VendorDPSIAAssessment)
        .where(
            VendorDPSIAAssessment.vendor_id == vendor_id,
            VendorDPSIAAssessment.status.in_(["pending", "running"]),
        )
        .order_by(desc(VendorDPSIAAssessment.created_at))
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if not row:
        return None

    return {
        "job_id": row.job_id,
        "vendor_id": str(row.vendor_id),
        "status": row.status,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "error_message": row.error_message,
    }


def _row_to_dict(row: VendorDPSIAAssessment) -> Dict[str, Any]:
    """Convert a VendorDPSIAAssessment row to a serialisable dictionary."""
    return {
        "job_id": row.job_id,
        "vendor_id": str(row.vendor_id),
        "status": row.status,
        "assessment_type": row.assessment_type,
        "data_role": row.data_role,
        "rag_status": row.rag_status,
        "recommendation": row.recommendation,
        "risk_score": row.risk_score,
        "risk_level": row.risk_level,
        "executive_summary": row.executive_summary,
        "report_markdown": row.report_markdown,
        "report_json": row.report_json,
        "report_filename": row.report_filename,
        "research_sources": row.research_sources,
        "linked_assessment_id": str(row.linked_assessment_id) if row.linked_assessment_id else None,
        "linked_report_id": str(row.linked_report_id) if row.linked_report_id else None,
        "processing_time_ms": row.processing_time_ms,
        "error_message": row.error_message,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
