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
import hashlib
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
    # Reassignment is a governance event — who is accountable for a piece of
    # evidence is exactly the sort of change an auditor asks you to evidence
    # (#781). Free-text `owner` was already tracked; the FK columns that
    # actually drive task assignment were not.
    'assigned_user_id',
    'owner_user_id',
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
    # Preparer assertions (#786, #802). Tracked because they are the fields an
    # auditor is most likely to ask about after the fact — "was the period
    # always 1 Jan to 31 Mar, or did someone widen it when the sample came up
    # short?" is exactly the question an audit trail exists to answer.
    'effective_period_start',
    'effective_period_end',
    'population_size',
    'population_source',
    'sample_size',
    'sample_method',
    'sample_basis',
    'ipe_source_system',
    'ipe_query_or_filter',
    'ipe_extracted_by_user_id',
    'ipe_extracted_at',
    'ipe_completeness_check',
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
# Column-width guards
# ---------------------------------------------------------------------------

#: Derived from the model so the guard cannot drift from the schema. If the
#: column is ever widened (or narrowed) the clamp follows automatically.
MAX_FIELD_NAME_LENGTH: int = AuditLog.field_name.type.length

#: Hex digits of the digest appended to a clamped ``field_name``. Enough to
#: keep two different long names distinguishable in the audit trail.
_FIELD_NAME_DIGEST_CHARS = 10

#: Separator between the readable head and the digest. Chosen so a clamped
#: name is visually obvious and cannot be mistaken for a real field path.
_FIELD_NAME_TRUNCATION_MARK = "~"


def clamp_field_name(field_name: Optional[str]) -> Optional[str]:
    """Fit ``field_name`` inside its column without ever raising.

    ``AuditLog.field_name`` is a bounded ``varchar``, but several callers
    compose it from data of unbounded length -- most notably document
    section identifiers, which are derived from heading text
    (``section:statement-of-applicability.controls.gov-...-35-controls``).
    A composed name longer than the column silently worked until the data
    grew, then failed the *entire* transaction with
    ``StringDataRightTruncationError``, taking the user's action down with
    it. An audit write must never break the thing it is auditing.

    Over-long names are truncated to a readable head plus a short digest of
    the full original, so distinct names stay distinct in the trail and the
    entry is visibly marked as clamped. Callers that must retain the exact
    value should also record it in ``old_value``/``new_value``, which are
    unbounded ``Text``.

    Args:
        field_name: The composed field name, or ``None``.

    Returns:
        ``field_name`` unchanged when it already fits, ``None`` when given
        ``None``, otherwise a clamped form of exactly
        ``MAX_FIELD_NAME_LENGTH`` characters or fewer.
    """
    if field_name is None or len(field_name) <= MAX_FIELD_NAME_LENGTH:
        return field_name

    digest = hashlib.sha256(field_name.encode("utf-8")).hexdigest()[
        :_FIELD_NAME_DIGEST_CHARS
    ]
    suffix = f"{_FIELD_NAME_TRUNCATION_MARK}{digest}"
    head = field_name[: MAX_FIELD_NAME_LENGTH - len(suffix)]
    clamped = f"{head}{suffix}"

    logger.warning(
        "Audit field_name exceeded %s chars (%s); clamped to %r",
        MAX_FIELD_NAME_LENGTH,
        len(field_name),
        clamped,
    )
    return clamped


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
        field_name=clamp_field_name(field_name),
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


def get_client_ip(request: Request) -> Optional[str]:
    """Best-effort client IP for an audit row, honouring the reverse proxy.

    `X-Forwarded-For` is a list and the left-most entry is the originating
    client; the platform sits behind a proxy in every deployment shape it
    supports, so reading `request.client.host` alone records the load balancer.

    Truncated to `audit_log.ip_address`'s 45 characters (IPv6 with a scope id is
    the worst case) rather than risking a write failure on a long or malicious
    header — a clipped address in the audit trail beats no audit row at all.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first[:45]
    client = getattr(request, "client", None)
    host = getattr(client, "host", None)
    return host[:45] if host else None


def get_user_agent(request: Request) -> Optional[str]:
    """The requesting client's User-Agent, or None. `audit_log.user_agent` is Text."""
    return request.headers.get("user-agent")
