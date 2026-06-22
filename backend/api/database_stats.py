"""
Database Statistics API endpoint.
Provides health check, statistics, backup, restore, and version info for the database.
"""
import logging
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, delete
from typing import Dict, Any, List, Optional
from uuid import UUID

from database import get_db
from models import (
    Organization, ScopedControl, EvidenceTracking,
    User as UserModel, OrganizationMember, Assignment, Comment,
    CommentHistory, EvidenceCollectionTask, Notification,
    System, SystemEvidenceCapability
)
from catalog_models import (
    SCFCatalogControl, SCFCatalogDomain,
    SCFCatalogEvidence, SCFCatalogAssessmentObjective
)
from auth import optional_auth, require_auth, require_admin, require_platform_admin, get_accessible_org_ids, User
from schemas import DatabaseBackupResponse, DatabaseRestoreRequest, DatabaseRestoreResponse
# Rate limiting temporarily disabled - see Phase 0 debugging
# from rate_limiting import limiter, READ_RATE_LIMIT, WRITE_RATE_LIMIT, AUTH_RATE_LIMIT

logger = logging.getLogger(__name__)

router = APIRouter(tags=["database"])


def serialize_row(row) -> Dict[str, Any]:
    """Serialize a SQLAlchemy model instance to a dictionary."""
    result = {}
    for column in row.__table__.columns:
        value = getattr(row, column.name)
        if value is None:
            result[column.name] = None
        elif isinstance(value, UUID):
            result[column.name] = str(value)
        elif isinstance(value, datetime):
            result[column.name] = value.isoformat()
        elif hasattr(value, 'isoformat'):  # date objects
            result[column.name] = value.isoformat()
        else:
            result[column.name] = value
    return result


@router.get("/database/stats")
# @limiter.limit(READ_RATE_LIMIT)  # Temporarily disabled
async def get_database_stats(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """
    Get comprehensive database health and statistics.

    Requires authentication.
    """
    logger.info(f"Database stats accessed by: {current_user.email or current_user.user_id} ({current_user.auth_method})")

    try:
        # Count core tables
        org_count = await db.scalar(select(func.count()).select_from(Organization))
        control_count = await db.scalar(select(func.count()).select_from(ScopedControl))
        evidence_count = await db.scalar(select(func.count()).select_from(EvidenceTracking))

        # Count user persistence tables
        user_count = await db.scalar(select(func.count()).select_from(UserModel))
        org_member_count = await db.scalar(select(func.count()).select_from(OrganizationMember))
        assignment_count = await db.scalar(select(func.count()).select_from(Assignment))
        comment_count = await db.scalar(select(func.count()).select_from(Comment))
        task_count = await db.scalar(select(func.count()).select_from(EvidenceCollectionTask))
        notification_count = await db.scalar(select(func.count()).select_from(Notification))

        # Control statistics
        selected_count = await db.scalar(
            select(func.count()).select_from(ScopedControl).where(ScopedControl.selected == True)
        )

        implemented_count = await db.scalar(
            select(func.count()).select_from(ScopedControl).where(
                ScopedControl.implementation_status == 'implemented'
            )
        )

        at_risk_count = await db.scalar(
            select(func.count()).select_from(ScopedControl).where(
                ScopedControl.implementation_status == 'at_risk'
            )
        )

        # Evidence statistics
        tracked_evidence_count = await db.scalar(
            select(func.count()).select_from(EvidenceTracking).where(
                EvidenceTracking.is_tracked == True
            )
        )

        # Group by implementation status
        status_result = await db.execute(
            select(
                ScopedControl.implementation_status,
                func.count(ScopedControl.id).label('count')
            )
            .where(ScopedControl.implementation_status.isnot(None))
            .group_by(ScopedControl.implementation_status)
        )
        by_status = {row[0]: row[1] for row in status_result}

        # Group by maturity level
        maturity_result = await db.execute(
            select(
                ScopedControl.maturity_level,
                func.count(ScopedControl.id).label('count')
            )
            .where(ScopedControl.maturity_level.isnot(None))
            .group_by(ScopedControl.maturity_level)
        )
        by_maturity = {row[0]: row[1] for row in maturity_result}

        # User statistics
        active_users_count = await db.scalar(
            select(func.count()).select_from(UserModel).where(UserModel.last_login_at.isnot(None))
        )

        # Task statistics
        pending_tasks_count = await db.scalar(
            select(func.count()).select_from(EvidenceCollectionTask).where(
                EvidenceCollectionTask.status != 'completed'
            )
        )

        completed_tasks_count = await db.scalar(
            select(func.count()).select_from(EvidenceCollectionTask).where(
                EvidenceCollectionTask.status == 'completed'
            )
        )

        overdue_tasks_count = await db.scalar(
            select(func.count()).select_from(EvidenceCollectionTask).where(
                and_(
                    EvidenceCollectionTask.status != 'completed',
                    EvidenceCollectionTask.due_date < func.current_date()
                )
            )
        )

        # Group tasks by status
        task_status_result = await db.execute(
            select(
                EvidenceCollectionTask.status,
                func.count(EvidenceCollectionTask.id).label('count')
            )
            .where(EvidenceCollectionTask.status.isnot(None))
            .group_by(EvidenceCollectionTask.status)
        )
        tasks_by_status = {row[0]: row[1] for row in task_status_result}

        # Group tasks by type
        task_type_result = await db.execute(
            select(
                EvidenceCollectionTask.task_type,
                func.count(EvidenceCollectionTask.id).label('count')
            )
            .where(EvidenceCollectionTask.task_type.isnot(None))
            .group_by(EvidenceCollectionTask.task_type)
        )
        tasks_by_type = {row[0]: row[1] for row in task_type_result}

        # Notification statistics
        unread_notifications_count = await db.scalar(
            select(func.count()).select_from(Notification).where(
                Notification.is_read == False
            )
        )

        # Comment statistics
        recent_comments_count = await db.scalar(
            select(func.count()).select_from(Comment).where(
                Comment.is_deleted == False
            )
        )

        # Recent activity
        last_control_update = await db.scalar(
            select(func.max(ScopedControl.updated_at)).select_from(ScopedControl)
        )

        last_evidence_update = await db.scalar(
            select(func.max(EvidenceTracking.updated_at)).select_from(EvidenceTracking)
        )

        # For comments, use created_at (edited_at tracks edits but created_at is the base timestamp)
        last_comment_update = await db.scalar(
            select(func.max(Comment.created_at)).select_from(Comment)
        )

        # For tasks, use created_at (no updated_at field, completed_date is just a date)
        last_task_update = await db.scalar(
            select(func.max(EvidenceCollectionTask.created_at)).select_from(EvidenceCollectionTask)
        )

        return {
            "status": "healthy",
            "database": {
                "organizations": org_count or 0,
                "scoped_controls": control_count or 0,
                "evidence_tracking": evidence_count or 0,
                "users": user_count or 0,
                "organization_members": org_member_count or 0,
                "assignments": assignment_count or 0,
                "comments": comment_count or 0,
                "evidence_collection_tasks": task_count or 0,
                "notifications": notification_count or 0,
                "total_records": (
                    (org_count or 0) + (control_count or 0) + (evidence_count or 0) +
                    (user_count or 0) + (org_member_count or 0) + (assignment_count or 0) +
                    (comment_count or 0) + (task_count or 0) + (notification_count or 0)
                )
            },
            "statistics": {
                "selected_controls": selected_count or 0,
                "implemented_controls": implemented_count or 0,
                "at_risk_controls": at_risk_count or 0,
                "tracked_evidence": tracked_evidence_count or 0,
                "active_users": active_users_count or 0,
                "pending_tasks": pending_tasks_count or 0,
                "completed_tasks": completed_tasks_count or 0,
                "overdue_tasks": overdue_tasks_count or 0,
                "unread_notifications": unread_notifications_count or 0,
                "total_comments": recent_comments_count or 0
            },
            "by_status": by_status,
            "by_maturity": by_maturity,
            "tasks_by_status": tasks_by_status,
            "tasks_by_type": tasks_by_type,
            "recent_updates": {
                "last_control_update": last_control_update.isoformat() if last_control_update else None,
                "last_evidence_update": last_evidence_update.isoformat() if last_evidence_update else None,
                "last_comment_update": last_comment_update.isoformat() if last_comment_update else None,
                "last_task_update": last_task_update.isoformat() if last_task_update else None
            }
        }
    except Exception as e:
        logger.error(f"Failed to retrieve database statistics: {type(e).__name__}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve database statistics: {str(e)}"
        )


@router.get("/database/backup")
# @limiter.limit(AUTH_RATE_LIMIT)  # Temporarily disabled
async def backup_database(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_platform_admin)
):
    """
    Export user data tables to JSON format for backup.

    PLATFORM ADMIN ONLY: Database backups contain sensitive multi-tenant data.
    Only platform administrators can export data.

    Returns a JSON object containing:
    - metadata: version, timestamp, table counts, accessible_organizations
    - data: filtered records from each table

    Tables are exported in dependency order to support proper restoration.

    NOTE: Catalog tables (scf_catalog_*) are NOT included in backups.
    Catalog data is reference data seeded from SCF JSON files on application startup.
    """
    logger.info(f"Database backup initiated by: {current_user.email or current_user.user_id}")

    try:
        # Get organisations the user can access (via membership or consultant relationship)
        accessible_org_ids = await get_accessible_org_ids(current_user, db)

        if not accessible_org_ids:
            logger.warning(f"User {current_user.email} has no accessible organisations for backup")
            return {
                "metadata": {
                    "version": "1.1",
                    "created_at": datetime.utcnow().isoformat(),
                    "created_by": current_user.email or current_user.user_id,
                    "note": "No accessible organisations. User has no organisation memberships.",
                    "accessible_organizations": [],
                    "table_counts": {}
                },
                "data": {}
            }

        logger.info(f"Backup scoped to {len(accessible_org_ids)} accessible organisation(s)")

        # Export tables in dependency order, filtered by accessible organisations
        # 1. Root entities - filter by accessible org IDs
        orgs_result = await db.execute(
            select(Organization).where(Organization.id.in_(accessible_org_ids))
        )
        organizations = [serialize_row(row) for row in orgs_result.scalars().all()]

        # Get user IDs who are members of accessible organisations
        member_user_ids_result = await db.execute(
            select(OrganizationMember.user_id).where(
                OrganizationMember.organization_id.in_(accessible_org_ids)
            )
        )
        accessible_user_ids = [row[0] for row in member_user_ids_result.fetchall()]

        # Users - only those who are members of accessible organisations
        if accessible_user_ids:
            users_result = await db.execute(
                select(UserModel).where(UserModel.id.in_(accessible_user_ids))
            )
            users = [serialize_row(row) for row in users_result.scalars().all()]
        else:
            users = []

        # 2. First-level dependencies - filter by accessible org IDs
        members_result = await db.execute(
            select(OrganizationMember).where(
                OrganizationMember.organization_id.in_(accessible_org_ids)
            )
        )
        organization_members = [serialize_row(row) for row in members_result.scalars().all()]

        # Systems (depends on organizations)
        systems_result = await db.execute(
            select(System).where(System.organization_id.in_(accessible_org_ids))
        )
        systems = [serialize_row(row) for row in systems_result.scalars().all()]
        system_ids = [s["id"] for s in systems]

        # Scoped controls - filter by accessible org IDs
        controls_result = await db.execute(
            select(ScopedControl).where(ScopedControl.organization_id.in_(accessible_org_ids))
        )
        scoped_controls = [serialize_row(row) for row in controls_result.scalars().all()]
        control_ids = [c["id"] for c in scoped_controls]

        # Evidence tracking - filter by accessible org IDs
        evidence_result = await db.execute(
            select(EvidenceTracking).where(EvidenceTracking.organization_id.in_(accessible_org_ids))
        )
        evidence_tracking = [serialize_row(row) for row in evidence_result.scalars().all()]
        evidence_ids = [e["id"] for e in evidence_tracking]

        # System evidence capabilities - filter by system IDs in accessible orgs
        if system_ids:
            # Convert string IDs back to UUIDs for the query
            system_uuids = [UUID(sid) if isinstance(sid, str) else sid for sid in system_ids]
            capabilities_result = await db.execute(
                select(SystemEvidenceCapability).where(
                    SystemEvidenceCapability.system_id.in_(system_uuids)
                )
            )
            system_evidence_capabilities = [serialize_row(row) for row in capabilities_result.scalars().all()]
        else:
            system_evidence_capabilities = []

        # Assignments - filter by assignable_id (controls or evidence in accessible orgs)
        # Assignments reference either ScopedControl or EvidenceTracking
        accessible_assignable_ids = control_ids + evidence_ids
        if accessible_assignable_ids:
            assignable_uuids = [UUID(aid) if isinstance(aid, str) else aid for aid in accessible_assignable_ids]
            assignments_result = await db.execute(
                select(Assignment).where(Assignment.assignable_id.in_(assignable_uuids))
            )
            assignments = [serialize_row(row) for row in assignments_result.scalars().all()]
        else:
            assignments = []

        # 3. Second-level dependencies
        # Comments - filter by commentable_id (controls or evidence in accessible orgs)
        if accessible_assignable_ids:
            commentable_uuids = [UUID(cid) if isinstance(cid, str) else cid for cid in accessible_assignable_ids]
            comments_result = await db.execute(
                select(Comment).where(Comment.commentable_id.in_(commentable_uuids))
            )
            comments = [serialize_row(row) for row in comments_result.scalars().all()]
            comment_ids = [c["id"] for c in comments]
        else:
            comments = []
            comment_ids = []

        # Evidence collection tasks - filter by evidence_tracking_id in accessible orgs
        if evidence_ids:
            evidence_uuids = [UUID(eid) if isinstance(eid, str) else eid for eid in evidence_ids]
            tasks_result = await db.execute(
                select(EvidenceCollectionTask).where(
                    EvidenceCollectionTask.evidence_tracking_id.in_(evidence_uuids)
                )
            )
            evidence_collection_tasks = [serialize_row(row) for row in tasks_result.scalars().all()]
        else:
            evidence_collection_tasks = []

        # Notifications - filter by user_id belonging to accessible orgs
        if accessible_user_ids:
            notifications_result = await db.execute(
                select(Notification).where(Notification.user_id.in_(accessible_user_ids))
            )
            notifications = [serialize_row(row) for row in notifications_result.scalars().all()]
        else:
            notifications = []

        # 4. Third-level dependencies
        # Comment history - filter by comment_id in accessible comments
        if comment_ids:
            comment_uuids = [UUID(cid) if isinstance(cid, str) else cid for cid in comment_ids]
            history_result = await db.execute(
                select(CommentHistory).where(CommentHistory.comment_id.in_(comment_uuids))
            )
            comment_history = [serialize_row(row) for row in history_result.scalars().all()]
        else:
            comment_history = []

        # Build backup structure with tenant scope metadata
        backup = {
            "metadata": {
                "version": "1.1",
                "created_at": datetime.utcnow().isoformat(),
                "created_by": current_user.email or current_user.user_id,
                "note": "This backup contains USER DATA only, scoped to accessible organisations. Catalog tables (scf_catalog_*) are NOT included.",
                "accessible_organizations": [str(org_id) for org_id in accessible_org_ids],
                "table_counts": {
                    "organizations": len(organizations),
                    "users": len(users),
                    "organization_members": len(organization_members),
                    "systems": len(systems),
                    "scoped_controls": len(scoped_controls),
                    "evidence_tracking": len(evidence_tracking),
                    "system_evidence_capabilities": len(system_evidence_capabilities),
                    "assignments": len(assignments),
                    "comments": len(comments),
                    "comment_history": len(comment_history),
                    "evidence_collection_tasks": len(evidence_collection_tasks),
                    "notifications": len(notifications),
                }
            },
            "data": {
                "organizations": organizations,
                "users": users,
                "organization_members": organization_members,
                "systems": systems,
                "scoped_controls": scoped_controls,
                "evidence_tracking": evidence_tracking,
                "system_evidence_capabilities": system_evidence_capabilities,
                "assignments": assignments,
                "comments": comments,
                "comment_history": comment_history,
                "evidence_collection_tasks": evidence_collection_tasks,
                "notifications": notifications,
            }
        }

        total_records = sum(backup["metadata"]["table_counts"].values())
        logger.info(f"Database backup completed: {total_records} total records across {len(backup['data'])} tables for {len(accessible_org_ids)} organisation(s)")

        return backup

    except Exception as e:
        logger.error(f"Database backup failed: {type(e).__name__}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Database backup failed: {str(e)}"
        )


@router.post("/database/restore")
# @limiter.limit(AUTH_RATE_LIMIT)  # Temporarily disabled
async def restore_database(
    request: Request,
    restore_request: DatabaseRestoreRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_platform_admin)
):
    """
    Restore database from a backup JSON file.

    PLATFORM ADMIN ONLY: This endpoint requires platform administrator privileges.

    This operation will:
    1. Validate the backup format and version
    2. Clear existing data (if confirm_clear=true)
    3. Insert all records in dependency order
    4. Return a summary of restored records

    WARNING: This is a destructive operation that replaces all existing data.
    """
    logger.info(f"Database restore initiated by: {current_user.email or current_user.user_id}")

    backup_data = restore_request.backup_data

    # Validate backup structure
    if "metadata" not in backup_data or "data" not in backup_data:
        raise HTTPException(
            status_code=400,
            detail="Invalid backup format: missing 'metadata' or 'data' sections"
        )

    metadata = backup_data["metadata"]
    data = backup_data["data"]

    # Validate version (support both 1.0 and 1.1)
    version = metadata.get("version")
    if version not in ("1.0", "1.1"):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported backup version: {version}. Expected: 1.0 or 1.1"
        )

    # Check confirmation flag
    if not restore_request.confirm_clear:
        # Return preview of what will be restored
        return {
            "status": "preview",
            "message": "This is a preview. Set confirm_clear=true to proceed with restore.",
            "backup_metadata": metadata,
            "records_to_restore": metadata.get("table_counts", {})
        }

    try:
        # Clear existing data in reverse dependency order
        logger.info("Clearing existing data...")

        await db.execute(delete(CommentHistory))
        await db.execute(delete(Notification))
        await db.execute(delete(EvidenceCollectionTask))
        await db.execute(delete(Comment))
        await db.execute(delete(Assignment))
        await db.execute(delete(SystemEvidenceCapability))  # Depends on systems
        await db.execute(delete(EvidenceTracking))  # References systems
        await db.execute(delete(System))  # Depends on organizations
        await db.execute(delete(ScopedControl))
        await db.execute(delete(OrganizationMember))
        await db.execute(delete(UserModel))
        await db.execute(delete(Organization))

        await db.commit()
        logger.info("Existing data cleared")

        # Helper to parse dates and UUIDs from backup
        def parse_datetime(val):
            if val is None:
                return None
            if isinstance(val, str):
                return datetime.fromisoformat(val.replace('Z', '+00:00'))
            return val

        def parse_date(val):
            if val is None:
                return None
            if isinstance(val, str):
                from datetime import date
                return date.fromisoformat(val)
            return val

        def parse_uuid(val):
            if val is None:
                return None
            if isinstance(val, str):
                return UUID(val)
            return val

        restored_counts = {}

        # 1. Restore organizations
        for row in data.get("organizations", []):
            org = Organization(
                id=parse_uuid(row["id"]),
                name=row["name"],
                slug=row["slug"],
                created_at=parse_datetime(row.get("created_at")),
                updated_at=parse_datetime(row.get("updated_at"))
            )
            db.add(org)
        restored_counts["organizations"] = len(data.get("organizations", []))

        # 2. Restore users
        for row in data.get("users", []):
            user = UserModel(
                id=parse_uuid(row["id"]),
                google_sub=row["google_sub"],
                email=row["email"],
                display_name=row.get("display_name"),
                created_at=parse_datetime(row.get("created_at")),
                last_login_at=parse_datetime(row.get("last_login_at")),
                email_notifications_enabled=row.get("email_notifications_enabled", True),
                notification_frequency=row.get("notification_frequency", "immediate")
            )
            db.add(user)
        restored_counts["users"] = len(data.get("users", []))

        # Flush to ensure FKs are available
        await db.flush()

        # 3. Restore organization_members
        for row in data.get("organization_members", []):
            member = OrganizationMember(
                id=parse_uuid(row["id"]),
                organization_id=parse_uuid(row["organization_id"]),
                user_id=parse_uuid(row["user_id"]),
                role=row.get("role", "viewer"),
                joined_at=parse_datetime(row.get("joined_at"))
            )
            db.add(member)
        restored_counts["organization_members"] = len(data.get("organization_members", []))

        # 3.5. Restore systems (v1.1+ backups only)
        for row in data.get("systems", []):
            system = System(
                id=parse_uuid(row["id"]),
                organization_id=parse_uuid(row["organization_id"]),
                name=row["name"],
                system_type=row["system_type"],
                category=row.get("category"),
                description=row.get("description"),
                vendor=row.get("vendor"),
                status=row.get("status", "active"),
                connection_config=row.get("connection_config", {}),
                created_at=parse_datetime(row.get("created_at")),
                updated_at=parse_datetime(row.get("updated_at")),
                created_by_user_id=parse_uuid(row.get("created_by_user_id")),
                updated_by_user_id=parse_uuid(row.get("updated_by_user_id"))
            )
            db.add(system)
        restored_counts["systems"] = len(data.get("systems", []))

        # 4. Restore scoped_controls
        for row in data.get("scoped_controls", []):
            # Support both legacy ccf_id and new scf_id field names for backward compatibility
            scf_id = row.get("scf_id") or row.get("ccf_id")
            control = ScopedControl(
                id=parse_uuid(row["id"]),
                organization_id=parse_uuid(row["organization_id"]),
                scf_id=scf_id,
                selected=row.get("selected", False),
                selection_reason=row.get("selection_reason"),
                implementation_status=row.get("implementation_status"),
                priority=row.get("priority"),
                owner=row.get("owner"),
                assigned_to=row.get("assigned_to"),
                maturity_level=row.get("maturity_level"),
                target_date=parse_date(row.get("target_date")),
                completion_date=parse_date(row.get("completion_date")),
                implementation_notes=row.get("implementation_notes"),
                related_documentation=row.get("related_documentation"),
                custom_fields=row.get("custom_fields"),
                created_at=parse_datetime(row.get("created_at")),
                updated_at=parse_datetime(row.get("updated_at")),
                assigned_user_id=parse_uuid(row.get("assigned_user_id")),
                owner_user_id=parse_uuid(row.get("owner_user_id")),
                created_by_user_id=parse_uuid(row.get("created_by_user_id")),
                updated_by_user_id=parse_uuid(row.get("updated_by_user_id"))
            )
            db.add(control)
        restored_counts["scoped_controls"] = len(data.get("scoped_controls", []))

        # 5. Restore evidence_tracking
        for row in data.get("evidence_tracking", []):
            evidence = EvidenceTracking(
                id=parse_uuid(row["id"]),
                organization_id=parse_uuid(row["organization_id"]),
                evidence_id=row["evidence_id"],
                is_tracked=row.get("is_tracked", False),
                method_of_collection=row.get("method_of_collection"),
                collecting_system=row.get("collecting_system"),
                owner=row.get("owner"),
                frequency=row.get("frequency"),
                comments=row.get("comments"),
                created_at=parse_datetime(row.get("created_at")),
                updated_at=parse_datetime(row.get("updated_at")),
                assigned_user_id=parse_uuid(row.get("assigned_user_id")),
                owner_user_id=parse_uuid(row.get("owner_user_id")),
                created_by_user_id=parse_uuid(row.get("created_by_user_id")),
                updated_by_user_id=parse_uuid(row.get("updated_by_user_id")),
                next_collection_date=parse_date(row.get("next_collection_date")),
                last_collection_date=parse_date(row.get("last_collection_date")),
                system_id=parse_uuid(row.get("system_id"))  # v1.1+ field
            )
            db.add(evidence)
        restored_counts["evidence_tracking"] = len(data.get("evidence_tracking", []))

        # 5.5. Restore system_evidence_capabilities (v1.1+ backups only)
        for row in data.get("system_evidence_capabilities", []):
            capability = SystemEvidenceCapability(
                id=parse_uuid(row["id"]),
                system_id=parse_uuid(row["system_id"]),
                evidence_id=row["evidence_id"],
                capability_status=row.get("capability_status", "potential"),
                collection_method=row.get("collection_method"),
                confidence_level=row.get("confidence_level", "medium"),
                data_format=row.get("data_format"),
                notes=row.get("notes"),
                created_at=parse_datetime(row.get("created_at")),
                updated_at=parse_datetime(row.get("updated_at")),
                created_by_user_id=parse_uuid(row.get("created_by_user_id")),
                updated_by_user_id=parse_uuid(row.get("updated_by_user_id"))
            )
            db.add(capability)
        restored_counts["system_evidence_capabilities"] = len(data.get("system_evidence_capabilities", []))

        await db.flush()

        # 6. Restore assignments
        for row in data.get("assignments", []):
            assignment = Assignment(
                id=parse_uuid(row["id"]),
                assignable_type=row["assignable_type"],
                assignable_id=parse_uuid(row["assignable_id"]),
                user_id=parse_uuid(row["user_id"]),
                role=row.get("role", "primary"),
                assigned_at=parse_datetime(row.get("assigned_at")),
                assigned_by_user_id=parse_uuid(row.get("assigned_by_user_id"))
            )
            db.add(assignment)
        restored_counts["assignments"] = len(data.get("assignments", []))

        # 7. Restore comments (need to handle self-referencing parent_comment_id)
        # First pass: insert all comments without parent references
        comment_map = {}
        for row in data.get("comments", []):
            comment = Comment(
                id=parse_uuid(row["id"]),
                commentable_type=row["commentable_type"],
                commentable_id=parse_uuid(row["commentable_id"]),
                user_id=parse_uuid(row["user_id"]),
                parent_comment_id=None,  # Will update in second pass
                content=row["content"],
                mentions=row.get("mentions", []),
                is_edited=row.get("is_edited", False),
                edited_at=parse_datetime(row.get("edited_at")),
                is_deleted=row.get("is_deleted", False),
                deleted_at=parse_datetime(row.get("deleted_at")),
                created_at=parse_datetime(row.get("created_at"))
            )
            db.add(comment)
            comment_map[row["id"]] = (comment, row.get("parent_comment_id"))

        await db.flush()

        # Second pass: update parent references
        for comment_id, (comment, parent_id) in comment_map.items():
            if parent_id:
                comment.parent_comment_id = parse_uuid(parent_id)

        restored_counts["comments"] = len(data.get("comments", []))

        # 8. Restore evidence_collection_tasks
        for row in data.get("evidence_collection_tasks", []):
            task = EvidenceCollectionTask(
                id=parse_uuid(row["id"]),
                evidence_tracking_id=parse_uuid(row["evidence_tracking_id"]),
                task_type=row.get("task_type", "collection"),
                title=row.get("title"),
                description=row.get("description"),
                priority=row.get("priority", "medium"),
                due_date=parse_date(row["due_date"]),
                status=row.get("status", "not_started"),
                assigned_user_id=parse_uuid(row.get("assigned_user_id")),
                completed_date=parse_date(row.get("completed_date")),
                completion_notes=row.get("completion_notes"),
                dependencies=row.get("dependencies", []),
                attachments=row.get("attachments", []),
                auto_generated=row.get("auto_generated", True),
                created_at=parse_datetime(row.get("created_at"))
            )
            db.add(task)
        restored_counts["evidence_collection_tasks"] = len(data.get("evidence_collection_tasks", []))

        # 9. Restore notifications
        for row in data.get("notifications", []):
            notification = Notification(
                id=parse_uuid(row["id"]),
                user_id=parse_uuid(row["user_id"]),
                type=row["type"],
                reference_type=row["reference_type"],
                reference_id=parse_uuid(row["reference_id"]),
                message=row["message"],
                is_read=row.get("is_read", False),
                read_at=parse_datetime(row.get("read_at")),
                created_at=parse_datetime(row.get("created_at"))
            )
            db.add(notification)
        restored_counts["notifications"] = len(data.get("notifications", []))

        # 10. Restore comment_history
        for row in data.get("comment_history", []):
            history = CommentHistory(
                id=parse_uuid(row["id"]),
                comment_id=parse_uuid(row["comment_id"]),
                old_content=row["old_content"],
                edited_by_user_id=parse_uuid(row["edited_by_user_id"]),
                edited_at=parse_datetime(row.get("edited_at"))
            )
            db.add(history)
        restored_counts["comment_history"] = len(data.get("comment_history", []))

        # Commit all changes
        await db.commit()

        total_restored = sum(restored_counts.values())
        logger.info(f"Database restore completed: {total_restored} total records restored")

        return {
            "status": "success",
            "message": f"Database restored successfully. {total_restored} records restored.",
            "restored_by": current_user.email or current_user.user_id,
            "restored_at": datetime.utcnow().isoformat(),
            "original_backup_created_at": metadata.get("created_at"),
            "original_backup_created_by": metadata.get("created_by"),
            "restored_counts": restored_counts
        }

    except Exception as e:
        await db.rollback()
        logger.error(f"Database restore failed: {type(e).__name__}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Database restore failed: {str(e)}"
        )


async def get_catalog_counts(db: AsyncSession) -> Dict[str, int]:
    """
    Get counts of catalog items from the database.

    Queries the seeded catalog tables instead of reading from files.
    """
    try:
        controls_count = await db.scalar(
            select(func.count()).select_from(SCFCatalogControl)
        )
        evidence_count = await db.scalar(
            select(func.count()).select_from(SCFCatalogEvidence)
        )

        return {
            "collection_interfaces": 0,  # Legacy CCF concept, not in SCF
            "evidence_items": evidence_count or 0,
            "controls": controls_count or 0
        }
    except Exception as e:
        logger.warning(f"Failed to get catalog counts from database: {e}")
        return {
            "collection_interfaces": 0,
            "evidence_items": 0,
            "controls": 0
        }


def get_git_info(repo_path: str) -> Optional[Dict[str, str]]:
    """
    Get git commit hash and tag info from a repository path.
    """
    try:
        # Get current commit hash (short)
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=5
        )
        commit = result.stdout.strip() if result.returncode == 0 else None

        # Try to get tag
        result = subprocess.run(
            ["git", "describe", "--tags", "--always"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=5
        )
        tag = result.stdout.strip() if result.returncode == 0 else None

        return {"commit": commit, "tag": tag}
    except Exception as e:
        logger.warning(f"Failed to get git info from {repo_path}: {e}")
        return None


def get_platform_version() -> str:
    """
    Get platform version from package.json (mounted at /version/package.json).
    Falls back to PLATFORM_VERSION env var if file not available.
    """
    package_json_path = Path("/version/package.json")
    if package_json_path.exists():
        try:
            with open(package_json_path) as f:
                data = json.load(f)
                version = data.get("version")
                if version:
                    logger.debug(f"Platform version from package.json: {version}")
                    return version
        except Exception as e:
            logger.warning(f"Failed to read package.json: {e}")

    # Fallback to environment variable
    return os.getenv("PLATFORM_VERSION", "0.0.0")


def get_catalog_version() -> str:
    """
    Get catalog version from git describe (catalog repo mounted at /version/catalog).
    Falls back to CATALOG_VERSION env var if git not available.
    """
    catalog_repo_path = "/version/catalog"

    # Try git describe first
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--always"],
            cwd=catalog_repo_path,
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            version = result.stdout.strip()
            logger.debug(f"Catalog version from git: {version}")
            return version
    except Exception as e:
        logger.warning(f"Failed to get catalog version from git: {e}")

    # Fallback to environment variable
    return os.getenv("CATALOG_VERSION", "unknown")


@router.get("/version")
# @limiter.limit(READ_RATE_LIMIT)  # Temporarily disabled
async def get_version_info(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Get version information for the platform and catalog.

    Versions are read dynamically from:
    - Platform: webclient/package.json (mounted at /version/package.json)
    - Catalog: Database (seeded from SCF JSON files at startup)

    Falls back to environment variables if sources unavailable.
    No authentication required - this is public info for troubleshooting.
    """
    logger.info("Version info requested")

    # Read versions dynamically from actual sources
    platform_version = get_platform_version()
    catalog_version = get_catalog_version()

    # Get catalog counts from database
    catalog_counts = await get_catalog_counts(db)

    # Try to get git info for backend (if running in dev with git)
    git_info = get_git_info("/app")

    return {
        "platform": {
            "version": platform_version,
            "api_version": "1.0.0",
            "git_commit": git_info.get("commit") if git_info else None
        },
        "catalog": {
            "version": catalog_version,
            "controls_count": catalog_counts["controls"],
            "evidence_count": catalog_counts["evidence_items"],
            "interface_count": catalog_counts["collection_interfaces"]
        }
    }


@router.get("/database/catalog-stats")
# @limiter.limit(READ_RATE_LIMIT)  # Temporarily disabled
async def get_catalog_stats(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """
    Get statistics for SCF catalog tables (reference data).

    Requires authentication.

    Catalog tables are seeded from SCF JSON files on application startup and contain:
    - scf_catalog_controls: SCF control definitions with full metadata
    - scf_catalog_domains: SCF domain definitions
    - scf_catalog_evidence: Evidence Request List (ERL) entries
    - scf_catalog_assessment_objectives: Assessment objectives for controls

    NOTE: Catalog data is READ-ONLY reference data and is NOT included in backups.
    """
    logger.info(f"Catalog stats accessed by: {current_user.email or current_user.user_id}")

    try:
        # Count catalog tables
        controls_count = await db.scalar(
            select(func.count()).select_from(SCFCatalogControl)
        )
        domains_count = await db.scalar(
            select(func.count()).select_from(SCFCatalogDomain)
        )
        evidence_count = await db.scalar(
            select(func.count()).select_from(SCFCatalogEvidence)
        )
        ao_count = await db.scalar(
            select(func.count()).select_from(SCFCatalogAssessmentObjective)
        )

        # Get catalog version from first control (all should have same version)
        version_result = await db.execute(
            select(SCFCatalogControl.catalog_version).limit(1)
        )
        catalog_version = version_result.scalar() or "unknown"

        return {
            "status": "healthy",
            "catalog_version": catalog_version,
            "tables": {
                "scf_catalog_controls": controls_count or 0,
                "scf_catalog_domains": domains_count or 0,
                "scf_catalog_evidence": evidence_count or 0,
                "scf_catalog_assessment_objectives": ao_count or 0
            },
            "total_records": (
                (controls_count or 0) +
                (domains_count or 0) +
                (evidence_count or 0) +
                (ao_count or 0)
            ),
            "note": "Catalog data is READ-ONLY reference data seeded from SCF JSON files. Not included in backups."
        }
    except Exception as e:
        logger.error(f"Failed to retrieve catalog statistics: {type(e).__name__}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve catalog statistics: {str(e)}"
        )
