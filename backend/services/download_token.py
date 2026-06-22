"""
Short-lived signed download tokens for browser-accessible evidence file URLs.

Generates HMAC-SHA256 tokens that encode file_id + org_id + expiry, allowing
the download endpoint to authenticate requests via URL query params instead
of requiring Bearer auth headers (which browsers can't send for img/iframe/navigation).
"""
import hmac
import hashlib
import os
import time

# Secret for signing tokens — dedicated env var with API_KEY fallback
_SECRET: str | None = None


def _get_secret() -> str:
    global _SECRET
    if _SECRET is None:
        _SECRET = os.getenv("DOWNLOAD_TOKEN_SECRET") or os.getenv("API_KEY") or ""
    return _SECRET


def generate_download_token(
    file_id: str,
    org_id: str,
    ttl_seconds: int = 900,
) -> tuple[str, int]:
    """Generate a short-lived HMAC download token.

    Returns (token_hex, expires_unix).
    """
    expires = int(time.time()) + ttl_seconds
    message = f"{file_id}:{org_id}:{expires}"
    token = hmac.new(
        _get_secret().encode(),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()
    return token, expires


def verify_download_token(
    file_id: str,
    org_id: str,
    token: str,
    expires: int,
) -> bool:
    """Verify an HMAC download token.

    Returns False if expired or tampered.
    """
    if int(time.time()) > expires:
        return False
    message = f"{file_id}:{org_id}:{expires}"
    expected = hmac.new(
        _get_secret().encode(),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(token, expected)
