"""
Audit Service - Create immutable audit log entries for SOC 2 Type II compliance.

This service provides helper functions to record field-level change history
for any auditable entity (scoped controls, evidence tracking, vendors, etc.).

Key design decisions:
- Records are added to the session but NOT committed - callers control
  transaction boundaries so audit entries live in the same transaction
  as the change they describe.
- Values are JSON-serialised for consistent storage regardless of type.
- Tracked-field sets allow callers to limit noise by auditing only the
  fields that matter for compliance.
"""
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID
from typing import Optional
import json
import logging
import re

from fastapi import Request

from models import AuditLog

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tracked field sets
# ---------------------------------------------------------------------------

SCOPED_CONTROL_TRACKED_FIELDS: set = {
    'selected',
    'selection_reason',
    'implementation_status',
    'priority',
    'owner',
    'assigned_to',
    'maturity_level',
    'target_date',
    'completion_date',
    'implementation_notes',
}

EVIDENCE_TRACKING_TRACKED_FIELDS: set = {
    'is_tracked',
    'method_of_collection',
    'collecting_system',
    'owner',
    'frequency',
    'comments',
}

EVIDENCE_FILE_TRACKED_FIELDS: set = {
    'filename',
    's3_key',
    'content_type',
    'file_size_bytes',
    'evidence_id',
    'sha256_hash',
    'classification',
    'is_deleted',
}

# Per-window review fields (M4 PR 2, #574 — ISC-15).
WINDOW_ASSESSMENT_TRACKED_FIELDS: set = {
    'review_status',
    'reviewed_by_user_id',
    'reviewed_at',
    'review_notes',
}

WEBHOOK_ENDPOINT_TRACKED_FIELDS: set = {
    'name',
    'description',
    'is_active',
    'allowed_evidence_ids',
}

VENDOR_TRACKED_FIELDS: set = {
    'name',
    'website',
    'description',
    'risk_tier',
    'status',
    'cia_confidentiality',
    'cia_integrity',
    'cia_availability',
}

RISK_ASSESSMENT_TRACKED_FIELDS: set = {
    'likelihood',
    'impact',
    'residual_likelihood',
    'residual_impact',
    'treatment_status',
    'treatment_plan',
    'treatment_due_date',
    'owner_user_id',
    'next_review_date',
    'notes',
}

CUSTOM_RISK_TRACKED_FIELDS: set = {
    'title',
    'description',
    'category_name',
    'category_color',
}

CUSTOM_RISK_CONTROL_MAPPING_TRACKED_FIELDS: set = {
    'risk_code',
    'scf_id',
}

ORGANIZATION_TRACKED_FIELDS: set = {
    'name',
    'slug',
    'settings',
}

ORG_MEMBER_TRACKED_FIELDS: set = {
    'role',
}

SYSTEM_TRACKED_FIELDS: set = {
    'name',
    'description',
    'system_type',
    'vendor',
    'vendor_id',
    'status',
    'owner_user_id',
    'catalog_template_id',
}

SYSTEM_CAPABILITY_TRACKED_FIELDS: set = {
    'evidence_type_name',
    'collection_interface',
    'maturity_level',
    'notes',
}

COMMENT_TRACKED_FIELDS: set = {
    'content',
    'is_edited',
    'is_deleted',
}

ASSIGNMENT_TRACKED_FIELDS: set = {
    'assignable_type',
    'assignable_id',
    'user_id',
    'role',
}

API_KEY_TRACKED_FIELDS: set = {
    'name',
    'description',
    'is_active',
    'last_used_at',
    # NEVER include: key_hash, plaintext key value
}


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

async def create_audit_entry(
    db: AsyncSession,
    organization_id: UUID,
    entity_type: str,
    entity_id: UUID,
    action: str,
    changed_by_user_id: UUID,
    field_name: Optional[str] = None,
    old_value: Optional[str] = None,
    new_value: Optional[str] = None,
    scf_id: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    action_source: Optional[str] = None,
    request_id: Optional[UUID] = None,
) -> AuditLog:
    """Create a single audit log entry and add it to the session.

    The caller is responsible for committing the transaction. This keeps
    the audit record in the same transaction as the entity change so
    they are atomically committed together.

    Args:
        db: Async database session.
        organization_id: Organisation that owns the entity.
        entity_type: Type of entity changed (e.g. 'scoped_control').
        entity_id: UUID of the changed record.
        action: One of 'create', 'update', or 'delete'.
        changed_by_user_id: UUID of the user who made the change.
        field_name: Optional specific field that changed.
        old_value: Optional previous value (JSON-encoded string).
        new_value: Optional new value (JSON-encoded string).
        scf_id: Optional denormalised SCF control identifier.
        ip_address: Optional client IP address.
        user_agent: Optional client user-agent string.

    Returns:
        The newly created AuditLog instance (already added to session).
    """
    entry = AuditLog(
        organization_id=organization_id,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        changed_by_user_id=changed_by_user_id,
        field_name=field_name,
        old_value=old_value,
        new_value=new_value,
        scf_id=scf_id,
        ip_address=ip_address,
        user_agent=user_agent,
        action_source=action_source,
        request_id=request_id,
    )
    db.add(entry)

    logger.info(
        "Audit entry created: %s %s/%s field=%s by user %s",
        action,
        entity_type,
        entity_id,
        field_name,
        changed_by_user_id,
    )

    return entry


async def log_entity_changes(
    db: AsyncSession,
    organization_id: UUID,
    entity_type: str,
    entity_id: UUID,
    action: str,
    changed_by_user_id: UUID,
    old_values: dict,
    new_values: dict,
    scf_id: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    tracked_fields: Optional[set] = None,
    action_source: Optional[str] = None,
    request_id: Optional[UUID] = None,
) -> list[AuditLog]:
    """Log field-level changes for an entity by diffing old and new values.

    Behaviour varies by action:
    - 'create': One entry per field in new_values (old_value is None).
    - 'update': One entry per field whose value actually changed.
    - 'delete': One entry per field in old_values (new_value is None).

    If *tracked_fields* is provided, only those fields are considered.

    Args:
        db: Async database session.
        organization_id: Organisation that owns the entity.
        entity_type: Type of entity changed.
        entity_id: UUID of the changed record.
        action: One of 'create', 'update', or 'delete'.
        changed_by_user_id: UUID of the user who made the change.
        old_values: Dict of previous field values.
        new_values: Dict of new field values.
        scf_id: Optional denormalised SCF control identifier.
        ip_address: Optional client IP address.
        user_agent: Optional client user-agent string.
        tracked_fields: Optional set of field names to limit auditing to.

    Returns:
        List of AuditLog instances that were added to the session.
    """
    entries: list[AuditLog] = []

    if action == 'create':
        fields = new_values.keys()
        if tracked_fields is not None:
            fields = [f for f in fields if f in tracked_fields]

        for field in fields:
            entry = await create_audit_entry(
                db=db,
                organization_id=organization_id,
                entity_type=entity_type,
                entity_id=entity_id,
                action=action,
                changed_by_user_id=changed_by_user_id,
                field_name=field,
                old_value=None,
                new_value=json.dumps(new_values[field], default=str),
                scf_id=scf_id,
                ip_address=ip_address,
                user_agent=user_agent,
                action_source=action_source,
                request_id=request_id,
            )
            entries.append(entry)

    elif action == 'update':
        all_fields = set(old_values.keys()) | set(new_values.keys())
        if tracked_fields is not None:
            all_fields = all_fields & tracked_fields

        for field in all_fields:
            old_val = old_values.get(field)
            new_val = new_values.get(field)

            # Skip fields with identical values
            if old_val == new_val:
                continue

            entry = await create_audit_entry(
                db=db,
                organization_id=organization_id,
                entity_type=entity_type,
                entity_id=entity_id,
                action=action,
                changed_by_user_id=changed_by_user_id,
                field_name=field,
                old_value=json.dumps(old_val, default=str),
                new_value=json.dumps(new_val, default=str),
                scf_id=scf_id,
                ip_address=ip_address,
                user_agent=user_agent,
                action_source=action_source,
                request_id=request_id,
            )
            entries.append(entry)

    elif action == 'delete':
        fields = old_values.keys()
        if tracked_fields is not None:
            fields = [f for f in fields if f in tracked_fields]

        for field in fields:
            entry = await create_audit_entry(
                db=db,
                organization_id=organization_id,
                entity_type=entity_type,
                entity_id=entity_id,
                action=action,
                changed_by_user_id=changed_by_user_id,
                field_name=field,
                old_value=json.dumps(old_values[field], default=str),
                new_value=None,
                scf_id=scf_id,
                ip_address=ip_address,
                user_agent=user_agent,
                action_source=action_source,
                request_id=request_id,
            )
            entries.append(entry)

    logger.info(
        "Logged %d audit entries for %s on %s/%s",
        len(entries),
        action,
        entity_type,
        entity_id,
    )

    return entries


# ---------------------------------------------------------------------------
# Source detection helpers
# ---------------------------------------------------------------------------

# MCP User-Agent patterns
_MCP_UA_PATTERN = re.compile(r'mcp|model.context.protocol', re.IGNORECASE)


def detect_action_source(request: Request) -> str:
    """Detect the origin of a mutation from request context.

    Priority:
    1. Explicit X-Audit-Source header (trusted override)
    2. Auth method from request.state.user
    3. Fallback to 'system'
    """
    # 1. Explicit header override
    explicit = request.headers.get("x-audit-source")
    if explicit and explicit in ("ui", "api_key", "mcp", "system"):
        return explicit

    # 2. Infer from auth method
    user = getattr(request.state, "user", None)
    if user is not None:
        auth_method = getattr(user, "auth_method", None)
        if auth_method == "google":
            return "ui"
        if auth_method == "api_key":
            return "api_key"
        if auth_method == "user_api_key":
            # Check User-Agent for MCP patterns
            ua = request.headers.get("user-agent", "")
            if _MCP_UA_PATTERN.search(ua):
                return "mcp"
            return "api_key"

    # 3. Fallback
    return "system"


def get_request_id(request: Request) -> Optional['UUID']:
    """Read the middleware-generated request_id from request state."""
    return getattr(request.state, "audit_request_id", None)
