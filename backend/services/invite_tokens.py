"""Deterministic lookup hash for invitation tokens.

Contract §3c. The token column itself is encrypted, and Fernet ciphertext is
non-deterministic, so a `WHERE invite_token = :token` lookup can no longer
work. Every creation site writes this hash alongside the token and every
lookup site filters on the hash.

SHA-256 with no salt is deliberate: the value must be reproducible from the
token alone at lookup time, and the token is already 32 bytes of urandom, so
there is nothing to brute-force.
"""
from __future__ import annotations

import hashlib


def hash_invite_token(token: str) -> str:
    """Return the lowercase hex SHA-256 of an invite token."""
    if token is None:
        raise ValueError("invite token is required")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
