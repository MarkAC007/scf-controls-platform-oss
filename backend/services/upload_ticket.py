"""
Signed upload tickets binding a confirm call to the presign that authorised it (#57).

The upload flow is presign → direct-to-storage PUT → confirm. The confirm
endpoint creates the database record, and it used to validate its `s3_key`
argument with a prefix test and nothing else:

    if not request.s3_key.startswith(f"evidence/{org_id}/"): 403

Any editor could therefore confirm a record pointing at *any* object already in
their own organisation's evidence prefix — attaching someone else's file to an
evidence item of their choosing, under a filename of their choosing, with a
hash string of their choosing, and with their own name recorded as the uploader.
No upload had to happen at all.

A ticket closes that. `get_upload_url` mints one over the exact key it just
generated, and `confirm_upload` will only create a record for a key that
arrives with a matching ticket. The ticket is stateless — no table, no Redis,
nothing to clean up — for the same reason the download token is: the properties
being asserted are all knowable at verification time, so an HMAC over them is
sufficient and a stored nonce would only add a failure mode.

The ticket binds five things:

* the object key       — so a different key cannot be substituted
* the organisation     — so it cannot cross a tenant boundary
* the evidence item    — so a file presigned for ERL-004 cannot be confirmed
                         against ERL-011
* the minting user     — so one member cannot confirm another's upload
* an expiry            — so a ticket recovered from an old response is inert

It shares `download_token`'s secret resolution deliberately: a deployment
configures one signing secret, not a menagerie of them, and a second env var
that a self-hoster could forget to set would be a hole rather than a feature.
"""
import hmac
import hashlib
import time
from typing import Optional

from services.download_token import signing_secret

#: Tickets outlive the presigned URL they accompany by a margin, because the
#: clock that matters is "how long can a large upload take", not "how long is
#: the grant valid". A ticket that expires mid-upload would fail the confirm
#: after the bytes had already landed.
DEFAULT_TICKET_TTL_SECONDS = 3600


def _digest(secret: str, object_key: str, org_id: str, evidence_id: str, user_id: str, expires: int) -> str:
    message = f"{object_key}|{org_id}|{evidence_id}|{user_id}|{expires}"
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def mint_upload_ticket(
    object_key: str,
    org_id: str,
    evidence_id: str,
    user_id: str,
    ttl_seconds: int = DEFAULT_TICKET_TTL_SECONDS,
) -> Optional[str]:
    """Return an opaque `"{expires}.{hmac}"` ticket, or None with no signing secret."""
    secret = signing_secret()
    if secret is None:
        return None
    expires = int(time.time()) + ttl_seconds
    return f"{expires}.{_digest(secret, object_key, org_id, evidence_id, user_id, expires)}"


def verify_upload_ticket(
    ticket: Optional[str],
    object_key: str,
    org_id: str,
    evidence_id: str,
    user_id: str,
) -> bool:
    """True only when `ticket` was minted for exactly this key, org, item and user."""
    secret = signing_secret()
    if secret is None or not ticket or "." not in ticket:
        return False
    raw_expires, _, digest = ticket.partition(".")
    if not raw_expires or not digest:
        return False
    try:
        expires = int(raw_expires)
    except ValueError:
        return False
    if int(time.time()) > expires:
        return False
    expected = _digest(secret, object_key, org_id, evidence_id, user_id, expires)
    return hmac.compare_digest(digest, expected)
