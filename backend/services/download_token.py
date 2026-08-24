"""
Short-lived signed download tokens for browser-accessible evidence file URLs.

Generates HMAC-SHA256 tokens that encode file_id + org_id + **user_id** + expiry,
allowing the download endpoint to authenticate requests via URL query params
instead of requiring Bearer auth headers (which browsers can't send for
img/iframe/navigation).

**Why the user id is in there (#57).** The token used to sign only
`file_id:org_id:expires`. Every download made through it was therefore anonymous
at the point of service: the one handler that sees 100% of evidence reads could
name the file but not the reader, so no custody record could be written. An
auditor asking "who has seen this evidence?" had nothing to read. Binding the
minting user into the signed payload makes the answer recoverable from the token
itself, with no extra query parameter and no session lookup.

**In-flight links minted before this change stop working.** They carry a
two-part payload where a three-part one is now required, and there is
deliberately no legacy verification branch: a fallback that accepted the old
format would leave exactly the anonymous path this change exists to close, and
would do so permanently, since nothing forces old links out of circulation. The
tokens live for 15 minutes, so the window is a quarter of an hour and it closes
by itself.
"""
import hmac
import hashlib
import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Secret for signing tokens — dedicated env var with API_KEY fallback
_SECRET: Optional[str] = None
_WARNED_NO_SECRET = False


def _get_secret() -> str:
    global _SECRET
    if _SECRET is None:
        _SECRET = os.getenv("DOWNLOAD_TOKEN_SECRET") or os.getenv("API_KEY") or ""
    return _SECRET


def _secret_or_none() -> Optional[str]:
    """Return the signing secret, or None when the deployment has none.

    With no secret, `hmac.new(b"", ...)` is perfectly deterministic — and so is
    forging it, from public knowledge alone. Signing and verification both
    refuse rather than pretend, which costs a deployment its browser download
    links and costs an attacker the entire evidence store.
    """
    global _WARNED_NO_SECRET
    secret = _get_secret()
    if not secret:
        if not _WARNED_NO_SECRET:
            logger.error(
                "Neither DOWNLOAD_TOKEN_SECRET nor API_KEY is set — signed evidence "
                "download links are disabled. Browser downloads will be unavailable "
                "until one is configured; API downloads over Bearer auth are unaffected."
            )
            _WARNED_NO_SECRET = True
        return None
    return secret


def signing_secret() -> Optional[str]:
    """The configured signing secret, or None when the deployment has none.

    Exposed so `upload_ticket` can sign with the same secret rather than
    introduce a second one a self-hoster could forget to set.
    """
    return _secret_or_none()


def generate_download_token(
    file_id: str,
    org_id: str,
    user_id: str,
    ttl_seconds: int = 900,
) -> Optional[tuple[str, int]]:
    """Generate a short-lived HMAC download token bound to the requesting user.

    Returns `(token, expires_unix)`, or None when no signing secret is
    configured. The token is `"{user_id}.{hmac_hex}"` — the user id travels in
    the clear so the verifier can recover it, and the MAC covers it so it cannot
    be swapped for someone else's.
    """
    secret = _secret_or_none()
    if secret is None:
        return None
    expires = int(time.time()) + ttl_seconds
    digest = hmac.new(
        secret.encode(),
        f"{file_id}:{org_id}:{user_id}:{expires}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"{user_id}.{digest}", expires


def verify_download_token(
    file_id: str,
    org_id: str,
    token: str,
    expires: int,
) -> Optional[str]:
    """Verify an HMAC download token and return the user id it was minted for.

    Returns None if expired, malformed, tampered with, or if the deployment has
    no signing secret. Callers must treat None as "not authenticated" — the old
    boolean contract returned False for the same cases, so a caller that tests
    truthiness keeps working, but one that wants the identity now gets it.
    """
    secret = _secret_or_none()
    if secret is None:
        return None
    if int(time.time()) > expires:
        return None
    if not token or "." not in token:
        return None
    user_id, _, digest = token.partition(".")
    if not user_id or not digest:
        return None
    expected = hmac.new(
        secret.encode(),
        f"{file_id}:{org_id}:{user_id}:{expires}".encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(digest, expected):
        return None
    return user_id
