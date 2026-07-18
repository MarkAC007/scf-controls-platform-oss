"""
CG SCF Backend API
FastAPI application for managing compliance controls and evidence tracking.
"""
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import os
import logging
from urllib.parse import urlparse

from database import init_db
from middleware import AuditMiddleware
from catalog_seeder import seed_catalog_if_empty
from redis_client import get_redis_client, close_redis_client, redis_health_check
from rate_limiting import (
    limiter,
    rate_limit_exceeded_handler,
    exempt_from_rate_limit,
    log_rate_limit_config,
    RATE_LIMITING_ENABLED,
)
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from api import (
    organizations,
    scoped_controls,
    evidence_tracking,
    evidence_maturity,
    evidence_files,
    database_stats,
    users,
    assignments,
    comments,
    evidence_tasks,
    notifications,
    systems,
    system_catalog,
    capabilities,
    catalog,
    cdm,
    tasks_api,
    consultant,
    risk_assessments,
    risk_profiles,
    custom_risks,
    admin,
    provisioning,
    webhooks,
    vendors,
    api_keys,
    dashboard,
    audit_log,
    scope_preferences,
    capability_themes,
    webhook_endpoints,
    evidence_inbox,
    evidence_validation,
    evidence_health,
    evidence_assessment,
    evidence_window_assessment,
    control_composites,
    audit_engagements,
    trust_portal,
    catalog_admin,
    oidc_auth,
)

# Configure logging
logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper()),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)


class _LogForgingSanitizer(logging.Filter):
    """Escape CR/LF in log records to prevent log forging / log injection.

    User-influenced values interpolated into log messages can contain newlines
    that inject forged log lines (CodeQL ``py/log-injection``). This filter is
    attached to the root handlers, so every propagating module logger inherits
    it — one defence-in-depth control instead of sanitising 60+ call sites.
    Note: a framework-level filter mitigates the real risk but may not clear the
    per-value CodeQL alerts, which need per-site sanitisers or a triage pass.
    """

    @staticmethod
    def _clean(value):
        if isinstance(value, str):
            return value.replace("\r", "\\r").replace("\n", "\\n")
        return value

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._clean(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self._clean(v) for k, v in record.args.items()}
            else:
                record.args = tuple(self._clean(a) for a in record.args)
        return True


# Attach to the root logger's handlers (not the logger itself — logger-level
# filters are not applied to records propagated from child loggers, but
# handler-level filters are).
_log_forging_sanitizer = _LogForgingSanitizer()
for _root_handler in logging.getLogger().handlers:
    _root_handler.addFilter(_log_forging_sanitizer)

logger = logging.getLogger(__name__)

# Azure Application Insights is initialized in celery_app.py (module-level).
# celery_app.py is imported before this point via the task import chain,
# and the setup_logging signal prevents Celery from stripping the OTel handler.


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for startup and shutdown events.
    """
    # Startup
    logger.info("Starting CG SCF Backend API")

    # Log rate limiting configuration
    log_rate_limit_config()

    # Log database connection info without credentials
    db_url = os.getenv('DATABASE_URL', 'Not set')
    if db_url != 'Not set':
        try:
            parsed = urlparse(db_url)
            # Extract only non-sensitive information: scheme, hostname, and database name
            db_name = parsed.path.lstrip('/') if parsed.path else 'unknown'
            db_host = parsed.hostname or 'unknown'
            logger.info(f"Database connection: {parsed.scheme}://{db_host}/{db_name}")
        except Exception:
            logger.info("Database connection: configured")
    else:
        logger.info("Database URL: Not set")

    # Initialize database (create tables if they don't exist)
    # Note: In production, use Alembic migrations
    try:
        await init_db()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        raise

    # Seed SCF catalog data if tables are empty
    # Catalog data is READ-ONLY reference data from SCF 2025.4
    try:
        seed_results = await seed_catalog_if_empty()
        for table, result in seed_results.items():
            status = result.get("status", "unknown")
            if status == "seeded":
                logger.info(f"Seeded SCF catalog {table}: {result.get('count', 0)} records")
            elif status == "skipped":
                logger.info(f"SCF catalog {table} already populated: {result.get('existing', 0)} records")
            elif status == "error":
                logger.error(f"Failed to seed SCF catalog {table}: {result.get('message', 'unknown error')}")
    except Exception as e:
        logger.error(f"Failed to seed SCF catalog data: {e}")

    # Self-hosted single-tenant: seed the service-account user (so the static master
    # API key can attribute audit_log rows — changed_by_user_id is NOT NULL), then
    # evaluate whether single-tenant master-key admin is safe to enable. Gated on the
    # explicit OSS_SINGLE_TENANT flag, NOT on ENVIRONMENT (issue #662).
    from services.single_tenant import single_tenant_flag_set, evaluate_single_tenant
    if os.getenv("API_KEY") and single_tenant_flag_set():
        try:
            from services.service_account import seed_service_account
            await seed_service_account()
            await evaluate_single_tenant()
        except Exception as e:
            logger.error("Single-tenant setup failed (non-fatal): %s", e, exc_info=True)

    # Seed the initial platform admin from BOOTSTRAP_ADMIN_EMAIL (self-hosted
    # first-run bootstrap). Idempotent and non-fatal — a failure must not block
    # startup (mirrors the single-tenant seed handling above).
    try:
        from auth import seed_bootstrap_admin
        await seed_bootstrap_admin()
    except Exception as e:
        logger.error("Bootstrap admin seed failed (non-fatal): %s", e, exc_info=True)

    if os.getenv("ENVIRONMENT") == "development":
        logger.critical(
            "ENVIRONMENT=development — debug error detail and uvicorn reload are ON. "
            "Do NOT run a public/production deployment in development mode."
        )

    # Initialize Redis connection
    redis_url = os.getenv('REDIS_URL', 'Not set')
    if redis_url != 'Not set':
        try:
            redis_client = await get_redis_client()
            await redis_client.ping()
            logger.info(f"Redis connection established: {redis_url.split('@')[-1] if '@' in redis_url else redis_url}")
        except Exception as e:
            logger.warning(f"Redis connection failed (non-fatal): {e}")
            logger.info("Application will continue without Redis caching")
    else:
        logger.info("Redis URL not configured - caching disabled")

    yield

    # Shutdown
    logger.info("Shutting down CG SCF Backend API")

    # Close Redis connection
    try:
        await close_redis_client()
        logger.info("Redis connection closed")
    except Exception as e:
        logger.warning(f"Error closing Redis connection: {e}")


# OpenAPI tag metadata (Issue #221)
openapi_tags = [
    {
        "name": "evidence-inbox",
        "description": "Webhook-based evidence ingestion. External systems POST evidence payloads to per-org webhook URLs.",
    },
    {
        "name": "webhook-endpoints",
        "description": "Manage webhook endpoints: create, list, revoke, rotate secrets.",
    },
    {
        "name": "evidence-files",
        "description": "Upload, list, download, and delete evidence files.",
    },
    {
        "name": "evidence-validation",
        "description": "View validation results for evidence files.",
    },
    {
        "name": "evidence-health",
        "description": "Evidence freshness monitoring with traffic-light health indicators.",
    },
]

# Create FastAPI application
app = FastAPI(
    title="CG SCF API",
    description="Backend API for CG SCF Explorer - Compliance Control Framework",
    version="1.0.0",
    lifespan=lifespan,
    openapi_tags=openapi_tags,
)

# Configure rate limiting (only if enabled)
if RATE_LIMITING_ENABLED:
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)
    logger.info("Rate limiting middleware enabled")
else:
    # Still attach limiter to app state but don't add middleware
    app.state.limiter = limiter
    logger.info("Rate limiting disabled via RATE_LIMITING_ENABLED=false")

# Audit middleware — registered BEFORE CORS so it runs as innermost middleware
# (after auth dependency injection populates request.state.user)
app.add_middleware(AuditMiddleware)

# Configure CORS
cors_origins_str = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:5173")
cors_origins = [origin.strip() for origin in cors_origins_str.split(",")]
logger.info(f"CORS origins configured: {cors_origins}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "Origin", "X-Requested-With", "X-Audit-Source"],
    expose_headers=["Content-Length", "Content-Type"],
)

# Compress large JSON responses. The catalog bulk exports are ~7 MB raw and
# compress ~10x; without this every app boot ships the full payload over the
# wire, which saturates slow links (VPN/mesh clients). Level 6 keeps CPU cost
# low on the single-process server.
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)


# Health check endpoint - exempt from rate limiting
@app.get("/health", tags=["health"])
@exempt_from_rate_limit
async def health_check():
    """
    Health check endpoint for Docker and load balancers.
    Exempt from rate limiting to ensure availability monitoring works.
    """
    # Check Redis health
    redis_status = await redis_health_check()

    # Determine overall health status
    overall_status = "healthy"
    if redis_status.get("status") != "healthy":
        overall_status = "degraded"  # Non-critical service down

    return {
        "status": overall_status,
        "service": "cg-scf-backend",
        "version": "1.0.0",
        "components": {
            "redis": redis_status,
        }
    }


# Root endpoint - exempt from rate limiting
@app.get("/", tags=["root"])
@exempt_from_rate_limit
async def root():
    """
    Root endpoint - API information.
    Exempt from rate limiting to allow basic availability checks.
    """
    return {
        "service": "CG SCF Backend API",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health"
    }


# Structured validation error handler (replaces FastAPI default for clearer API errors)
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Return structured validation errors with per-field detail.
    Replaces the default FastAPI 422 handler to produce errors that are
    easier for API clients (including MCP tools) to parse and display.
    """
    errors = []
    for error in exc.errors():
        loc = " -> ".join(str(part) for part in error.get("loc", []))
        errors.append({
            "field": loc,
            "message": error.get("msg", "Validation error"),
            "type": error.get("type", "unknown"),
        })
    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "error": "Validation error",
            "detail": errors,
        }
    )


# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    Global exception handler to catch unhandled exceptions.
    """
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": "Internal server error",
            "detail": str(exc) if os.getenv("ENVIRONMENT") == "development" else None
        }
    )


# Include API routers
app.include_router(organizations.router, prefix="/api")
app.include_router(scoped_controls.router, prefix="/api")
app.include_router(evidence_tracking.router, prefix="/api")
app.include_router(evidence_maturity.router, prefix="/api")  # Evidence maturity advisory
app.include_router(systems.router, prefix="/api")
app.include_router(system_catalog.router, prefix="/api")  # Systems knowledge catalog (template picker)
app.include_router(capabilities.router, prefix="/api")
app.include_router(database_stats.router, prefix="/api")
app.include_router(catalog.router, prefix="/api")  # SCF catalog reference data
app.include_router(catalog_admin.router, prefix="/api")  # OSS live SCF Excel upload + reseed
app.include_router(cdm.router, prefix="/api")
app.include_router(tasks_api.router, prefix="/api")  # Background tasks and cache management
app.include_router(users.router)
app.include_router(assignments.router)
app.include_router(comments.router)
app.include_router(evidence_tasks.router)
app.include_router(notifications.router)
app.include_router(consultant.router, prefix="/api")  # Consultant Portal
app.include_router(risk_assessments.router, prefix="/api")  # Risk Register
app.include_router(risk_profiles.router, prefix="/api")  # Risk Profile Config
app.include_router(custom_risks.router, prefix="/api")  # Custom Risk Definitions
app.include_router(admin.router, prefix="/api")  # Platform Admin Toolkit
app.include_router(provisioning.router, prefix="/api")  # Subscription Provisioning
app.include_router(webhooks.router, prefix="/api")  # External Webhooks (Stripe)
app.include_router(vendors.router, prefix="/api")  # Vendor Management (TPRM)
app.include_router(api_keys.router, prefix="/api")  # Per-org user API keys
app.include_router(dashboard.router, prefix="/api")  # GRC Dashboard work queue
app.include_router(audit_log.router, prefix="/api")  # Audit trail (SOC 2 Type II)
app.include_router(scope_preferences.router, prefix="/api")  # Audit scope preferences (Issue #362)
app.include_router(capability_themes.router, prefix="/api")  # KSI capability themes (Epic #317)
app.include_router(evidence_files.router, prefix="/api")  # Evidence S3 file uploads (Issue #324)
app.include_router(webhook_endpoints.router, prefix="/api")  # Webhook endpoint management (Issue #214)
app.include_router(evidence_inbox.router, prefix="/api")  # Evidence inbox ingestion (Issue #214)
app.include_router(evidence_validation.router, prefix="/api")  # Evidence validation engine (Issue #218)
app.include_router(evidence_health.router, prefix="/api")  # Evidence health dashboard (Issue #220)
app.include_router(evidence_assessment.router, prefix="/api")  # AI evidence assessment
app.include_router(evidence_window_assessment.router, prefix="/api")  # Windowed multi-artifact assessment (M1a)
app.include_router(control_composites.router, prefix="/api")  # Control assessment composites read API (M3 PR 2, #575)
app.include_router(audit_engagements.router, prefix="/api")  # Audit Engagement Workspaces (Issue #370 Phase D)
app.include_router(trust_portal.router, prefix="/api")  # Public trust portal (unauthenticated)
# OIDC login / callback endpoints. The router self-prefixes /api/auth, so it is
# included WITHOUT an extra prefix here (a "/api" prefix would double to /api/api/auth).
app.include_router(oidc_auth.router)


if __name__ == "__main__":
    import uvicorn

    # Run the application
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        reload=os.getenv("ENVIRONMENT") == "development",
        log_level=os.getenv("LOG_LEVEL", "info").lower()
    )
