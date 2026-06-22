"""
Evidence Tracking API endpoints.
Handles CRUD operations for evidence tracking.
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from sqlalchemy.orm import selectinload
from typing import List, Optional
from uuid import UUID

from database import get_db
from models import EvidenceTracking, EvidenceFile, Organization, System
from schemas import (
    EvidenceTrackingResponse,
    EvidenceTrackingCreate,
    EvidenceTrackingUpdate,
    BatchEvidenceTrackingRequest,
    BatchEvidenceTrackingResponse,
)
from auth import require_org_role, OrgMembership
from services.audit_service import (
    log_entity_changes,
    get_request_id,
    detect_action_source,
    EVIDENCE_TRACKING_TRACKED_FIELDS,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["evidence_tracking"])


@router.get(
    "/organizations/{org_id}/evidence-tracking",
    response_model=List[EvidenceTrackingResponse]
)
async def list_evidence_tracking(
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    system_id: Optional[UUID] = Query(None, description="Filter by collecting system"),
    db: AsyncSession = Depends(get_db)
):
    """
    Get all evidence tracking records for an organization.
    Requires: viewer role or higher.
    Optionally filter by system_id to find evidence collected by a specific system.
    """
    # Organization existence verified by require_org_role

    # Build query with eager loading for system relationship
    query = select(EvidenceTracking).options(
        selectinload(EvidenceTracking.system)
    ).where(EvidenceTracking.organization_id == org_id)

    # Apply optional system_id filter
    if system_id is not None:
        query = query.where(EvidenceTracking.system_id == system_id)

    result = await db.execute(query)
    tracking = result.scalars().all()

    # Compute file counts for all evidence items in one query
    if tracking:
        evidence_ids = [t.evidence_id for t in tracking]
        count_result = await db.execute(
            select(
                EvidenceFile.evidence_id,
                func.count(EvidenceFile.id).label("file_count"),
            )
            .where(
                and_(
                    EvidenceFile.organization_id == org_id,
                    EvidenceFile.evidence_id.in_(evidence_ids),
                    EvidenceFile.is_deleted == False,
                )
            )
            .group_by(EvidenceFile.evidence_id)
        )
        counts = {row.evidence_id: row.file_count for row in count_result}

        # Attach file_count to each tracking record for serialization
        for t in tracking:
            t.file_count = counts.get(t.evidence_id, 0)
    else:
        for t in tracking:
            t.file_count = 0

    return tracking


@router.get(
    "/organizations/{org_id}/evidence-tracking/{evidence_id}",
    response_model=EvidenceTrackingResponse
)
async def get_evidence_tracking(
    org_id: UUID,
    evidence_id: str,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db)
):
    """
    Get a single evidence tracking record by evidence ID.
    Requires: viewer role or higher.
    """
    result = await db.execute(
        select(EvidenceTracking).options(
            selectinload(EvidenceTracking.system)
        ).where(
            and_(
                EvidenceTracking.organization_id == org_id,
                EvidenceTracking.evidence_id == evidence_id
            )
        )
    )
    tracking = result.scalar_one_or_none()

    if not tracking:
        raise HTTPException(status_code=404, detail="Evidence tracking record not found")

    # Compute file count
    count_result = await db.execute(
        select(func.count(EvidenceFile.id)).where(
            and_(
                EvidenceFile.organization_id == org_id,
                EvidenceFile.evidence_id == evidence_id,
                EvidenceFile.is_deleted == False,
            )
        )
    )
    tracking.file_count = count_result.scalar() or 0

    return tracking


@router.post(
    "/organizations/{org_id}/evidence-tracking",
    response_model=EvidenceTrackingResponse,
    status_code=201
)
async def create_or_update_evidence_tracking(
    org_id: UUID,
    tracking_data: EvidenceTrackingCreate,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db)
):
    """
    Create or update evidence tracking (upsert).
    Requires: editor role or higher.
    If a record with the same evidence_id exists, it will be updated.
    Otherwise, a new record will be created.
    """
    # Organization existence verified by require_org_role

    # Validate system_id if provided
    if tracking_data.system_id is not None:
        system_result = await db.execute(
            select(System).where(
                and_(
                    System.id == tracking_data.system_id,
                    System.organization_id == org_id
                )
            )
        )
        if not system_result.scalar_one_or_none():
            raise HTTPException(
                status_code=400,
                detail="Invalid system_id: System not found or belongs to different organization"
            )

    # Check if tracking record already exists
    result = await db.execute(
        select(EvidenceTracking).where(
            and_(
                EvidenceTracking.organization_id == org_id,
                EvidenceTracking.evidence_id == tracking_data.evidence_id
            )
        )
    )
    existing_tracking = result.scalar_one_or_none()

    if existing_tracking:
        # Update existing record
        for key, value in tracking_data.model_dump(exclude_unset=True).items():
            setattr(existing_tracking, key, value)
        await db.commit()
        # Reload with system relationship
        result = await db.execute(
            select(EvidenceTracking).options(
                selectinload(EvidenceTracking.system)
            ).where(EvidenceTracking.id == existing_tracking.id)
        )
        return result.scalar_one()
    else:
        # Create new record
        new_tracking = EvidenceTracking(
            organization_id=org_id,
            **tracking_data.model_dump()
        )
        db.add(new_tracking)
        await db.commit()
        # Reload with system relationship
        result = await db.execute(
            select(EvidenceTracking).options(
                selectinload(EvidenceTracking.system)
            ).where(EvidenceTracking.id == new_tracking.id)
        )
        return result.scalar_one()


@router.post(
    "/organizations/{org_id}/evidence-tracking/batch",
    response_model=BatchEvidenceTrackingResponse,
    status_code=200
)
async def batch_update_evidence_tracking(
    org_id: UUID,
    request: BatchEvidenceTrackingRequest,
    http_request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """
    Batch create/update evidence tracking records in a single transaction.
    Requires: editor role or higher.
    Max 500 operations per request.

    For each operation:
    - If a tracking record with the same evidence_id exists, it is updated.
    - Otherwise, a new record is created.
    """
    user_id = UUID(membership.user.db_id)
    updated_count = 0
    created_count = 0
    failed_count = 0
    errors: List[str] = []
    result_evidence: List[EvidenceTracking] = []

    for op in request.operations:
        try:
            # Validate system_id if provided
            update_data = op.model_dump(exclude={'evidence_id'}, exclude_unset=True)
            if "system_id" in update_data and update_data["system_id"] is not None:
                system_result = await db.execute(
                    select(System).where(
                        and_(
                            System.id == update_data["system_id"],
                            System.organization_id == org_id
                        )
                    )
                )
                if not system_result.scalar_one_or_none():
                    raise ValueError(
                        "Invalid system_id: System not found or belongs to different organization"
                    )

            # Check if tracking record already exists
            result = await db.execute(
                select(EvidenceTracking).where(
                    and_(
                        EvidenceTracking.organization_id == org_id,
                        EvidenceTracking.evidence_id == op.evidence_id
                    )
                )
            )
            existing = result.scalar_one_or_none()

            if existing:
                # Capture old values for audit
                old_values = {f: getattr(existing, f) for f in EVIDENCE_TRACKING_TRACKED_FIELDS}

                # Apply updates
                for field_name, value in update_data.items():
                    setattr(existing, field_name, value)

                # Audit log
                new_values = {f: getattr(existing, f) for f in EVIDENCE_TRACKING_TRACKED_FIELDS}
                await log_entity_changes(
                    db=db, organization_id=org_id, entity_type='evidence_tracking',
                    entity_id=existing.id, action='update', changed_by_user_id=user_id,
                    old_values=old_values, new_values=new_values,
                    tracked_fields=EVIDENCE_TRACKING_TRACKED_FIELDS,
                    action_source=detect_action_source(http_request),
                    request_id=get_request_id(http_request),
                )

                result_evidence.append(existing)
                updated_count += 1
            else:
                # Create new record
                create_data = op.model_dump(exclude={'evidence_id'}, exclude_unset=True)
                new_tracking = EvidenceTracking(
                    organization_id=org_id,
                    evidence_id=op.evidence_id,
                    **create_data,
                )
                db.add(new_tracking)
                await db.flush()

                # Audit log
                new_values = {f: getattr(new_tracking, f) for f in EVIDENCE_TRACKING_TRACKED_FIELDS}
                await log_entity_changes(
                    db=db, organization_id=org_id, entity_type='evidence_tracking',
                    entity_id=new_tracking.id, action='create', changed_by_user_id=user_id,
                    old_values={}, new_values=new_values,
                    tracked_fields=EVIDENCE_TRACKING_TRACKED_FIELDS,
                    action_source=detect_action_source(http_request),
                    request_id=get_request_id(http_request),
                )

                result_evidence.append(new_tracking)
                created_count += 1
        except Exception as e:
            failed_count += 1
            errors.append(f"{op.evidence_id}: {str(e)}")
            logger.error(f"Batch evidence operation failed for {op.evidence_id}: {e}")

    await db.commit()

    # Refresh all records to get updated timestamps and system relationships
    for tracking in result_evidence:
        await db.refresh(tracking)

    logger.info(
        f"Batch evidence tracking: org={org_id}, updated={updated_count}, "
        f"created={created_count}, failed={failed_count}"
    )

    return BatchEvidenceTrackingResponse(
        updated=updated_count,
        created=created_count,
        failed=failed_count,
        errors=errors,
        evidence=result_evidence,
    )


@router.patch(
    "/organizations/{org_id}/evidence-tracking/{evidence_id}",
    response_model=EvidenceTrackingResponse
)
async def update_evidence_tracking(
    org_id: UUID,
    evidence_id: str,
    tracking_update: EvidenceTrackingUpdate,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db)
):
    """
    Partially update evidence tracking.
    Requires: editor role or higher.
    Only provided fields will be updated.
    """
    result = await db.execute(
        select(EvidenceTracking).where(
            and_(
                EvidenceTracking.organization_id == org_id,
                EvidenceTracking.evidence_id == evidence_id
            )
        )
    )
    tracking = result.scalar_one_or_none()

    if not tracking:
        raise HTTPException(status_code=404, detail="Evidence tracking record not found")

    # Validate system_id if provided
    update_data = tracking_update.model_dump(exclude_unset=True)
    if "system_id" in update_data and update_data["system_id"] is not None:
        system_result = await db.execute(
            select(System).where(
                and_(
                    System.id == update_data["system_id"],
                    System.organization_id == org_id
                )
            )
        )
        if not system_result.scalar_one_or_none():
            raise HTTPException(
                status_code=400,
                detail="Invalid system_id: System not found or belongs to different organization"
            )

    # Update only provided fields
    for key, value in update_data.items():
        setattr(tracking, key, value)

    await db.commit()
    # Reload with system relationship
    result = await db.execute(
        select(EvidenceTracking).options(
            selectinload(EvidenceTracking.system)
        ).where(EvidenceTracking.id == tracking.id)
    )
    return result.scalar_one()
