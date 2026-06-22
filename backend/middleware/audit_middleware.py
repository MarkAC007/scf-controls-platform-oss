"""
Audit Middleware - Baseline audit capture for every mutation endpoint.

Creates a lightweight audit record for every successful POST/PUT/PATCH/DELETE,
ensuring no mutation goes untracked even if the endpoint lacks explicit
audit service calls.

Design decisions:
- Uses a SEPARATE database session to avoid coupling with the request transaction.
- Captures audit AFTER call_next so request.state.user is populated by auth deps.
- Wraps audit writes in try/except so failures never break the request.
- Skips GET/HEAD/OPTIONS and non-API/health/docs paths.
"""
import re
import uuid as uuid_mod
import logging
from typing import Optional
from uuid import UUID

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from database import AsyncSessionLocal
from models import AuditLog
from services.audit_service import detect_action_source

logger = logging.getLogger(__name__)

# Methods that never produce audit records
_SKIP_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Paths to skip (health, docs, openapi)
_SKIP_PATH_PREFIXES = (
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/",  # root endpoint exactly
)

# HTTP method -> audit action mapping
_METHOD_ACTION_MAP = {
    "POST": "create",
    "PUT": "update",
    "PATCH": "update",
    "DELETE": "delete",
}

# URL path -> entity_type extraction patterns
# Match: /api/organizations/{uuid}/entity-segment/...
_ENTITY_PATTERN = re.compile(
    r'^/api/organizations/[^/]+/([^/]+)'
)

# Map URL path segments to entity_type values
_SEGMENT_TO_ENTITY: dict[str, str] = {
    "scoped-controls": "scoped_control",
    "evidence-tracking": "evidence_tracking",
    "evidence-files": "evidence_file",
    "evidence-maturity": "evidence_maturity",
    "evidence-tasks": "evidence_task",
    "evidence-inbox": "evidence_inbox",
    "evidence-validation": "evidence_validation",
    "evidence-health": "evidence_health",
    "vendors": "vendor",
    "vendor-reports": "vendor_report",
    "risk-assessments": "risk_assessment",
    "risk-profiles": "risk_profile",
    "systems": "system",
    "capabilities": "capability",
    "capability-themes": "capability_theme",
    "webhook-endpoints": "webhook_endpoint",
    "webhooks": "webhook",
    "comments": "comment",
    "assignments": "assignment",
    "notifications": "notification",
    "tasks": "task",
    "api-keys": "api_key",
    "audit-log": "audit_log",
    "dashboard": "dashboard",
    "users": "user",
    "admin": "admin",
}

# Extract org ID from URL path
_ORG_ID_PATTERN = re.compile(
    r'/api/organizations/([0-9a-fA-F-]{36})'
)


def _extract_entity_type(path: str) -> str:
    """Extract entity_type from URL path segment."""
    match = _ENTITY_PATTERN.match(path)
    if match:
        segment = match.group(1)
        return _SEGMENT_TO_ENTITY.get(segment, segment.replace("-", "_"))
    return "unknown"


def _extract_org_id(path: str) -> Optional[UUID]:
    """Extract organization UUID from URL path."""
    match = _ORG_ID_PATTERN.search(path)
    if match:
        try:
            return UUID(match.group(1))
        except ValueError:
            return None
    return None


def _should_skip(method: str, path: str) -> bool:
    """Check if this request should be skipped for audit."""
    if method in _SKIP_METHODS:
        return True
    # Exact root path match
    if path == "/":
        return True
    # Skip non-API paths and utility endpoints
    for prefix in _SKIP_PATH_PREFIXES:
        if prefix != "/" and path.startswith(prefix):
            return True
    return False


class AuditMiddleware(BaseHTTPMiddleware):
    """Baseline audit capture middleware.

    For every successful mutation (POST/PUT/PATCH/DELETE with status < 400),
    creates a single audit record capturing the entity type, action, source,
    and request ID. This coexists with field-level audit records created by
    explicit audit service calls — both share the same request_id.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # Generate request_id for EVERY request (so field-level calls can use it)
        request_id = uuid_mod.uuid4()
        request.state.audit_request_id = request_id

        method = request.method.upper()
        path = request.url.path

        # Skip non-mutation methods and utility paths
        if _should_skip(method, path):
            return await call_next(request)

        # Let the request proceed — auth deps populate request.state.user
        response = await call_next(request)

        # Only audit successful mutations
        if response.status_code >= 400:
            return response

        # Extract context for the audit record
        try:
            org_id = _extract_org_id(path)
            if org_id is None:
                # Can't create audit record without org context
                return response

            entity_type = _extract_entity_type(path)
            action = _METHOD_ACTION_MAP.get(method, "unknown")
            action_source = detect_action_source(request)

            # Get user ID from auth context
            user = getattr(request.state, "user", None)
            user_id = None
            if user is not None:
                db_id = getattr(user, "db_id", None)
                if db_id:
                    try:
                        user_id = UUID(db_id) if isinstance(db_id, str) else db_id
                    except (ValueError, TypeError):
                        pass

            if user_id is None:
                # Can't create audit record without user context
                logger.debug(
                    "Skipping middleware audit for %s %s — no user context",
                    method, path,
                )
                return response

            # Get client metadata
            ip_address = request.client.host if request.client else None
            user_agent = request.headers.get("user-agent")

            # Write audit record in a SEPARATE session
            async with AsyncSessionLocal() as audit_db:
                entry = AuditLog(
                    organization_id=org_id,
                    entity_type=entity_type,
                    entity_id=uuid_mod.UUID("00000000-0000-0000-0000-000000000000"),  # Baseline record — no specific entity
                    action=action,
                    changed_by_user_id=user_id,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    action_source=action_source,
                    request_id=request_id,
                )
                audit_db.add(entry)
                await audit_db.commit()

            logger.debug(
                "Middleware audit: %s %s/%s source=%s request_id=%s",
                action, entity_type, org_id, action_source, request_id,
            )

        except Exception:
            # NEVER let audit failures break the request
            logger.warning(
                "Middleware audit failed for %s %s — continuing without audit",
                method, path,
                exc_info=True,
            )

        return response
