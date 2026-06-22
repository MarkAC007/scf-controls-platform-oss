"""
Async service layer for AI-powered vendor research (Issue #59).

Provides:
    - trigger_research(): validates inputs, creates DB row, dispatches Celery orchestrator
    - get_status(): returns current job status + per-source progress
    - get_results(): returns full results for a completed job
    - get_latest(): returns the most recent completed research for a vendor
    - extract_domain(): extracts domain from a vendor's website URL
"""
import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from models import VendorResearchResult, Vendor

logger = logging.getLogger(__name__)

# Cache key prefix (must match tasks_research.py)
CACHE_KEY_PREFIX = "scf:cache:v1:vendor_research"


def extract_domain(website: str) -> Optional[str]:
    """
    Extract the bare domain from a vendor website URL.

    Examples:
        "https://www.adobe.com/products" -> "adobe.com"
        "http://example.co.uk" -> "example.co.uk"
        "example.com" -> "example.com"
    """
    if not website:
        return None

    url = website.strip()
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        # Strip leading 'www.'
        if host.startswith("www."):
            host = host[4:]
        return host if host else None
    except Exception:
        return None


async def trigger_research(
    db: AsyncSession,
    vendor_id: str,
    user_id: Optional[str] = None,
    domain_override: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Trigger a new research job for the given vendor.

    1. Validates vendor exists and has a resolvable domain.
    2. Creates a VendorResearchResult row with status='pending'.
    3. Dispatches the Celery orchestrator task.

    Returns dict with job_id and status.
    Raises ValueError if vendor not found or no domain available.
    """
    # Fetch vendor
    result = await db.execute(select(Vendor).where(Vendor.id == vendor_id))
    vendor = result.scalar_one_or_none()
    if not vendor:
        raise ValueError(f"Vendor {vendor_id} not found")

    # Resolve domain
    domain = domain_override or extract_domain(vendor.website)
    if not domain:
        raise ValueError(
            "Vendor has no website configured and no domain_override was provided. "
            "Please add a website to the vendor or provide a domain_override."
        )

    # Create research result row
    job_id = f"vr-{uuid.uuid4().hex[:12]}"
    research_row = VendorResearchResult(
        vendor_id=vendor.id,
        job_id=job_id,
        status="pending",
        researched_domain=domain,
        triggered_by_user_id=user_id,
    )
    db.add(research_row)
    await db.commit()
    await db.refresh(research_row)

    # Dispatch Celery orchestrator via send_task (avoids importing psycopg2 in web process)
    from celery_app import celery_app
    celery_app.send_task(
        "tasks_research.research_vendor_orchestrator",
        kwargs={
            "vendor_id": str(vendor.id),
            "job_id": job_id,
            "domain": domain,
            "vendor_name": vendor.name,
        },
    )

    logger.info(f"Triggered research job {job_id} for vendor {vendor.name} (domain={domain})")

    return {
        "job_id": job_id,
        "vendor_id": str(vendor.id),
        "status": "pending",
        "domain": domain,
    }


async def get_status(
    db: AsyncSession,
    vendor_id: str,
    job_id: str,
) -> Optional[Dict[str, Any]]:
    """
    Return the current status of a research job.

    Includes per-source progress from source_statuses JSONB.
    Returns None if job not found.
    """
    result = await db.execute(
        select(VendorResearchResult).where(
            VendorResearchResult.vendor_id == vendor_id,
            VendorResearchResult.job_id == job_id,
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        return None

    return {
        "job_id": row.job_id,
        "vendor_id": str(row.vendor_id),
        "status": row.status,
        "source_statuses": row.source_statuses or {},
        "errors": row.errors or [],
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def get_results(
    db: AsyncSession,
    vendor_id: str,
    job_id: str,
) -> Optional[Dict[str, Any]]:
    """
    Return full results for a completed research job.

    Tries Redis cache first, falls back to database.
    Returns None if job not found.
    """
    # Try Redis cache first
    try:
        from redis_client import get_redis_client
        redis = await get_redis_client()
        cache_key = f"{CACHE_KEY_PREFIX}:{vendor_id}:{job_id}"
        cached = await redis.get(cache_key)
        if cached:
            logger.debug(f"Cache hit for research job {job_id}")
            return json.loads(cached)
    except Exception as exc:
        logger.warning(f"Redis cache read failed for job {job_id}: {exc}")

    # Fall back to database
    result = await db.execute(
        select(VendorResearchResult).where(
            VendorResearchResult.vendor_id == vendor_id,
            VendorResearchResult.job_id == job_id,
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
    """
    Return the most recent completed research for a vendor.

    Tries Redis "latest" cache key first, falls back to database.
    Returns None if no completed research exists.
    """
    # Try Redis cache first
    try:
        from redis_client import get_redis_client
        redis = await get_redis_client()
        latest_key = f"{CACHE_KEY_PREFIX}:{vendor_id}:latest"
        cached = await redis.get(latest_key)
        if cached:
            logger.debug(f"Cache hit for latest research of vendor {vendor_id}")
            return json.loads(cached)
    except Exception as exc:
        logger.warning(f"Redis cache read failed for vendor {vendor_id} latest: {exc}")

    # Fall back to database
    result = await db.execute(
        select(VendorResearchResult)
        .where(
            VendorResearchResult.vendor_id == vendor_id,
            VendorResearchResult.status.in_(["completed", "partial"]),
        )
        .order_by(desc(VendorResearchResult.created_at))
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if not row:
        return None

    return _row_to_dict(row)


def _row_to_dict(row: VendorResearchResult) -> Dict[str, Any]:
    """Convert a VendorResearchResult row to a serialisable dictionary."""
    return {
        "job_id": row.job_id,
        "vendor_id": str(row.vendor_id),
        "status": row.status,
        "hibp_results": row.hibp_results or {},
        "cisa_kev_results": row.cisa_kev_results or {},
        "cve_nvd_results": row.cve_nvd_results or {},
        "regulatory_results": row.regulatory_results or {},
        "summary": row.summary,
        "risk_indicators": row.risk_indicators or {},
        "overall_risk_signal": row.overall_risk_signal,
        "source_statuses": row.source_statuses or {},
        "errors": row.errors or [],
        "researched_domain": row.researched_domain,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
