"""
Rate limiting middleware for CG SCF Backend API.

Implements rate limiting using slowapi to protect against:
- DDoS attacks
- Credential stuffing
- API abuse
- Resource exhaustion

Rate limits are applied per client IP address with the following tiers:
- Auth endpoints: 10 requests/minute (login, token operations)
- Read endpoints: 100 requests/minute (GET requests)
- Write endpoints: 30 requests/minute (POST, PUT, PATCH, DELETE)

Health check endpoints are excluded from rate limiting.
"""
import os
import logging
from typing import Callable, Optional
from fastapi import Request, Response
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

logger = logging.getLogger(__name__)


# ============================================================================
# Rate Limit Configuration
# ============================================================================

# Rate limits can be customised via environment variables
# Format: "requests per time_unit" e.g., "10/minute", "100/minute"
AUTH_RATE_LIMIT = os.getenv("RATE_LIMIT_AUTH", "10/minute")
READ_RATE_LIMIT = os.getenv("RATE_LIMIT_READ", "100/minute")
WRITE_RATE_LIMIT = os.getenv("RATE_LIMIT_WRITE", "30/minute")

# Specific limits for high-sensitivity unauthenticated/webhook endpoints
# POST /provisioning/sync — marketing-site-to-platform webhook (IP-based)
PROVISIONING_SYNC_RATE_LIMIT = os.getenv("RATE_LIMIT_PROVISIONING_SYNC", "20/minute")
# POST /webhooks/stripe — Stripe event delivery (IP-based, allows retries)
STRIPE_WEBHOOK_RATE_LIMIT = os.getenv("RATE_LIMIT_STRIPE_WEBHOOK", "60/minute")
# GET /public/trust/{slug} — public trust portal (IP-based, read-only)
TRUST_PORTAL_RATE_LIMIT = os.getenv("RATE_LIMIT_TRUST_PORTAL", "30/minute")

# Enable/disable rate limiting globally (useful for development/testing)
RATE_LIMITING_ENABLED = os.getenv("RATE_LIMITING_ENABLED", "true").lower() == "true"

# Endpoints excluded from rate limiting
EXCLUDED_PATHS = {
    "/health",
    "/",
    "/docs",
    "/redoc",
    "/openapi.json",
}


def get_client_identifier(request: Request) -> str:
    """
    Get a unique identifier for the client making the request.

    Uses X-Forwarded-For header if present (for reverse proxy setups),
    otherwise falls back to the direct client IP address.

    For authenticated requests, combines IP with user identifier for
    more granular rate limiting per user.
    """
    # Try to get IP from X-Forwarded-For (for reverse proxy setups)
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # Take the first IP in the chain (original client)
        client_ip = forwarded_for.split(",")[0].strip()
    else:
        client_ip = get_remote_address(request)

    # For authenticated requests, we could combine IP with user ID
    # This allows different rate limits per authenticated user
    # For now, we use just IP to keep it simple
    return client_ip or "unknown"


# ============================================================================
# Limiter Instance
# ============================================================================

# Create the limiter with our custom key function
limiter = Limiter(
    key_func=get_client_identifier,
    default_limits=[READ_RATE_LIMIT],  # Default to read rate limit
    enabled=RATE_LIMITING_ENABLED,
    headers_enabled=True,  # Add X-RateLimit headers to responses
)


# ============================================================================
# Rate Limit Decorators
# ============================================================================
# IMPORTANT: Any endpoint decorated with @rate_limit_read, @rate_limit_write,
# @rate_limit_auth, or @rate_limit_inbox MUST include `response: Response` in
# its function signature (from fastapi import Response). Without this parameter,
# slowapi cannot inject rate-limit headers and will raise a 500 error.

def rate_limit_auth(func: Callable) -> Callable:
    """
    Apply auth rate limit (10 requests/minute by default).
    Use for authentication-related endpoints.
    """
    return limiter.limit(AUTH_RATE_LIMIT)(func)


def rate_limit_read(func: Callable) -> Callable:
    """
    Apply read rate limit (100 requests/minute by default).
    Use for GET endpoints that retrieve data.
    """
    return limiter.limit(READ_RATE_LIMIT)(func)


def rate_limit_write(func: Callable) -> Callable:
    """
    Apply write rate limit (30 requests/minute by default).
    Use for POST, PUT, PATCH, DELETE endpoints that modify data.
    """
    return limiter.limit(WRITE_RATE_LIMIT)(func)


def exempt_from_rate_limit(func: Callable) -> Callable:
    """
    Exempt an endpoint from rate limiting.
    Use for health checks and other critical endpoints.
    """
    return limiter.exempt(func)


def rate_limit_provisioning_sync(func: Callable) -> Callable:
    """
    Apply provisioning sync rate limit (20 requests/minute by default).
    Use for POST /provisioning/sync (marketing-site webhook).
    IP-based limiting to protect against abuse of the unauthenticated provisioning path.
    """
    return limiter.limit(PROVISIONING_SYNC_RATE_LIMIT)(func)


def rate_limit_stripe_webhook(func: Callable) -> Callable:
    """
    Apply Stripe webhook rate limit (60 requests/minute by default).
    Use for POST /webhooks/stripe.
    IP-based limiting; set high enough to accommodate legitimate Stripe retries.
    """
    return limiter.limit(STRIPE_WEBHOOK_RATE_LIMIT)(func)


def rate_limit_trust_portal(func: Callable) -> Callable:
    """
    Apply trust portal rate limit (30 requests/minute by default).
    Use for GET /public/trust/{slug}.
    IP-based limiting for unauthenticated public trust portal endpoints.
    """
    return limiter.limit(TRUST_PORTAL_RATE_LIMIT)(func)


# ============================================================================
# Custom Rate Limit Exceeded Handler
# ============================================================================

async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> Response:
    """
    Custom handler for rate limit exceeded errors.

    Returns a 429 Too Many Requests response with:
    - Clear error message
    - Rate limit headers
    - Retry-After header
    """
    from fastapi.responses import JSONResponse

    # Extract rate limit info from the exception
    limit_string = str(exc.detail)

    # Log the rate limit event
    client_id = get_client_identifier(request)
    logger.warning(
        f"Rate limit exceeded for client {client_id}: "
        f"path={request.url.path}, limit={limit_string}"
    )

    response = JSONResponse(
        status_code=429,
        content={
            "success": False,
            "error": "Rate limit exceeded",
            "detail": f"Too many requests. {limit_string}",
            "retry_after": "Please wait before making another request."
        }
    )

    # Add Retry-After header (in seconds)
    # slowapi includes rate limit headers automatically
    response.headers["Retry-After"] = "60"

    return response


# ============================================================================
# Middleware for Path-Based Rate Limiting
# ============================================================================

def should_apply_rate_limit(path: str) -> bool:
    """
    Determine if rate limiting should be applied to a path.

    Excludes health checks and documentation endpoints.
    """
    # Normalize path
    path = path.rstrip("/")

    # Check against excluded paths
    if path in EXCLUDED_PATHS:
        return False

    # Also exclude paths that start with excluded prefixes
    for excluded in EXCLUDED_PATHS:
        if path.startswith(excluded):
            return False

    return True


def get_rate_limit_for_method(method: str) -> str:
    """
    Get the appropriate rate limit based on HTTP method.

    GET, HEAD, OPTIONS -> Read rate limit
    POST, PUT, PATCH, DELETE -> Write rate limit
    """
    read_methods = {"GET", "HEAD", "OPTIONS"}
    write_methods = {"POST", "PUT", "PATCH", "DELETE"}

    if method.upper() in read_methods:
        return READ_RATE_LIMIT
    elif method.upper() in write_methods:
        return WRITE_RATE_LIMIT
    else:
        return READ_RATE_LIMIT  # Default to read limit


# ============================================================================
# Logging Configuration
# ============================================================================

def log_rate_limit_config():
    """Log the current rate limiting configuration."""
    if RATE_LIMITING_ENABLED:
        logger.info("Rate limiting ENABLED with configuration:")
        logger.info(f"  - Auth endpoints: {AUTH_RATE_LIMIT}")
        logger.info(f"  - Read endpoints (GET): {READ_RATE_LIMIT}")
        logger.info(f"  - Write endpoints (POST/PUT/PATCH/DELETE): {WRITE_RATE_LIMIT}")
        logger.info(f"  - Provisioning sync (POST /provisioning/sync): {PROVISIONING_SYNC_RATE_LIMIT}")
        logger.info(f"  - Stripe webhook (POST /webhooks/stripe): {STRIPE_WEBHOOK_RATE_LIMIT}")
        logger.info(f"  - Trust portal (GET /public/trust/{{slug}}): {TRUST_PORTAL_RATE_LIMIT}")
        logger.info(f"  - Excluded paths: {EXCLUDED_PATHS}")
    else:
        logger.warning("Rate limiting is DISABLED")


# ============================================================================
# Shared Limiter for Router Decoration
# ============================================================================

# These are pre-configured limit decorators that can be applied to routes
# Example usage in a router:
#   from rate_limiting import limiter
#   @router.get("/items")
#   @limiter.limit(READ_RATE_LIMIT)
#   async def get_items(request: Request):
#       ...

# Note: The Request parameter must be present in endpoint functions
# for slowapi to extract the client identifier


# ============================================================================
# SlowAPI Middleware for Global Rate Limiting
# ============================================================================

# ============================================================================
# Per-Endpoint Inbox Rate Limiting (#216)
# ============================================================================

INBOX_RATE_LIMIT = os.getenv("RATE_LIMIT_INBOX", "60/minute")


def get_inbox_rate_key(request: Request) -> str:
    """Composite key: org_id + webhook_endpoint_id for per-endpoint limiting.

    Returns a key like ``inbox:{org_id}:{webhook_id}`` so each webhook
    endpoint gets its own rate-limit counter.
    """
    org_id = request.path_params.get("org_id", "unknown")
    webhook_id = request.headers.get("X-SCF-Webhook-Id", "unknown")
    return f"inbox:{org_id}:{webhook_id}"


# Dedicated limiter for inbox routes (separate from global limiter)
inbox_limiter = Limiter(
    key_func=get_inbox_rate_key,
    default_limits=[INBOX_RATE_LIMIT],
    enabled=RATE_LIMITING_ENABLED,
    headers_enabled=True,
)


def rate_limit_inbox(func: Callable) -> Callable:
    """Apply inbox rate limit (60 requests/minute by default).

    Uses per-endpoint composite key instead of IP-based limiting.
    """
    return inbox_limiter.limit(INBOX_RATE_LIMIT)(func)


from slowapi.middleware import SlowAPIMiddleware


def configure_rate_limiting(app):
    """
    Configure rate limiting middleware for the FastAPI application.

    This applies:
    - Default rate limits based on HTTP method
    - Excludes health check endpoints
    - Adds X-RateLimit headers to all responses

    Args:
        app: FastAPI application instance
    """
    # Attach limiter to app state (required by slowapi)
    app.state.limiter = limiter

    # Add SlowAPI middleware
    app.add_middleware(SlowAPIMiddleware)

    return app
