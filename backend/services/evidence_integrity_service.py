"""Server-side integrity classification for evidence files (#57).

The platform has always stored a `sha256_hash` on `evidence_files`, and the
column comment said what it was: "Computed client-side before upload". Nothing
ever recomputed it. An auditor reading that column was reading a number the
uploader typed, about bytes only the uploader ever saw — which is precisely the
assertion a hash is supposed to remove from the equation.

This module holds the *decisions* that integrity verification makes, kept free
of I/O so they can be tested without a storage backend, a database or a broker.
The fetch-and-write half lives in `tasks_evidence_integrity.py`.

Two separate facts are tracked, and they must not be conflated:

* `sha256_hash`     — what the client **asserted**. Never overwritten. It is the
                      uploader's claim, and an audit trail that silently
                      replaced a wrong claim with the right answer would destroy
                      the only evidence that the claim was ever wrong.
* `computed_sha256` — what the **server** measured over the bytes it fetched
                      from storage. This is the one a reviewer should rely on.

`hash_verification_status` records the relationship between them.
"""
from __future__ import annotations

import hashlib
from typing import Optional, Tuple

# ---------------------------------------------------------------------------
# Verification states
# ---------------------------------------------------------------------------

#: Not yet fetched. Every row starts here, including the ones that predate this
#: feature — see the backlog sweep in `tasks_evidence_integrity.py`.
HASH_PENDING = "pending"

#: Server-computed digest equals the client's assertion.
HASH_VERIFIED = "verified"

#: Server-computed digest differs from the client's assertion. The stored object
#: is not the object the uploader said they uploaded.
HASH_MISMATCH = "mismatch"

#: Bytes were fetched and hashed, but the client asserted nothing to compare
#: against. The digest is trustworthy; there is simply no claim to corroborate.
HASH_UNASSERTED = "unasserted"

#: The object could not be fetched (deleted from the bucket, storage
#: unconfigured, transient failure that outlived the retries). Distinct from
#: `pending` so an operator can tell "not looked at yet" from "looked at and
#: could not be read".
HASH_UNAVAILABLE = "unavailable"

HASH_STATES = frozenset(
    {HASH_PENDING, HASH_VERIFIED, HASH_MISMATCH, HASH_UNASSERTED, HASH_UNAVAILABLE}
)

#: Scan verdicts written by `malware_scan_service`. Mirrored here so the posture
#: predicates below read as one vocabulary rather than as bare strings.
SCAN_PENDING = "pending"
SCAN_CLEAN = "clean"
SCAN_INFECTED = "infected"
SCAN_SKIPPED = "skipped"
SCAN_ERROR = "scan_error"


# ---------------------------------------------------------------------------
# Digest
# ---------------------------------------------------------------------------

def compute_sha256(data: bytes) -> str:
    """Return the lowercase hex SHA-256 of `data`."""
    return hashlib.sha256(data).hexdigest()


def classify_hash(asserted: Optional[str], computed: str) -> Tuple[str, dict]:
    """Compare a client assertion against the server-computed digest.

    Returns `(status, details)` where `details` is a JSON-serialisable dict
    suitable for storing alongside the row. The asserted value is echoed into
    `details` on a mismatch so the discrepancy survives even if someone later
    edits the column.

    Comparison is case-insensitive and whitespace-tolerant: clients have been
    free to send either case since the column existed, and rejecting an
    uppercase digest as a mismatch would raise an integrity alarm about nothing.
    """
    normalised = (asserted or "").strip().lower()
    if not normalised:
        return HASH_UNASSERTED, {"computed_sha256": computed, "asserted_sha256": None}
    if normalised == computed:
        return HASH_VERIFIED, {"computed_sha256": computed, "asserted_sha256": normalised}
    return HASH_MISMATCH, {
        "computed_sha256": computed,
        "asserted_sha256": normalised,
        "message": (
            "The stored object does not hash to the value supplied at upload. "
            "Treat this file as unverified until the discrepancy is explained."
        ),
    }


# ---------------------------------------------------------------------------
# Posture predicates — Mark's ruling, recorded
# ---------------------------------------------------------------------------
#
# A file that has not yet been scanned stays downloadable and keeps counting
# toward posture, badged as unscanned. The alternative — withholding credit
# until a scan lands — would drop every existing customer's score on deploy day
# for a backlog that is the platform's own fault, and would make the score move
# for reasons unrelated to anything the customer did.
#
# Only two states are treated as disqualifying, and each for its own reason:
#
#   infected  — the bytes are hostile. Never served, never counted.
#   mismatch  — the bytes are not what was claimed. Still served (a reviewer
#               investigating the discrepancy needs to see them) but not counted,
#               because the artefact no longer evidences the assertion made
#               about it.

def is_download_blocked(scan_status: Optional[str]) -> bool:
    """True when a file must not be served to anyone.

    Confined to `infected`. A pending, skipped or errored scan does not block —
    see the module note above.
    """
    return scan_status == SCAN_INFECTED


def counts_toward_posture(
    scan_status: Optional[str],
    hash_verification_status: Optional[str],
) -> bool:
    """True when a file may contribute to coverage, maturity and KSI scoring."""
    if scan_status == SCAN_INFECTED:
        return False
    if hash_verification_status == HASH_MISMATCH:
        return False
    return True


def integrity_badge(
    scan_status: Optional[str],
    hash_verification_status: Optional[str],
) -> Optional[str]:
    """A short label for the UI, or None when there is nothing to say.

    Ordered by severity so a file that is both infected and mismatched reports
    the one a reader must act on first.
    """
    if scan_status == SCAN_INFECTED:
        return "infected"
    if hash_verification_status == HASH_MISMATCH:
        return "hash_mismatch"
    if hash_verification_status == HASH_UNAVAILABLE:
        return "unreadable"
    if scan_status == SCAN_PENDING or hash_verification_status == HASH_PENDING:
        return "not_yet_scanned"
    return None
