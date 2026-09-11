"""Invite-token lookup hash (Issue #947, contract §3c). ISC-26..29.

The token column is encrypted and Fernet ciphertext is non-deterministic, so
any surviving `WHERE invite_token = :token` query is a latent 100%-failure bug,
not a style problem. The grep test below is the guard against one coming back.
"""
import hashlib
import os
import re
import subprocess

import pytest

from services.invite_tokens import hash_invite_token

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_hash_is_sha256_hex():
    token = "abc123"
    assert hash_invite_token(token) == hashlib.sha256(b"abc123").hexdigest()
    assert len(hash_invite_token(token)) == 64


def test_hash_is_deterministic_and_distinct():
    assert hash_invite_token("a") == hash_invite_token("a")
    assert hash_invite_token("a") != hash_invite_token("b")


def test_hash_rejects_none():
    with pytest.raises((ValueError, AttributeError)):
        hash_invite_token(None)


def _python_sources():
    for root, dirs, files in os.walk(BACKEND_DIR):
        dirs[:] = [
            d for d in dirs
            if d not in {"tests", "__pycache__", "alembic", ".pytest_cache", "installer"}
        ]
        for name in files:
            if name.endswith(".py"):
                yield os.path.join(root, name)


def test_no_query_filters_on_invite_token_by_value():
    """ISC-28: every lookup must go through invite_token_hash."""
    pattern = re.compile(r"\binvite_token\s*==")
    offenders = []
    for path in _python_sources():
        with open(path, encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                if pattern.search(line) and "invite_token_hash" not in line:
                    offenders.append(f"{os.path.relpath(path, BACKEND_DIR)}:{lineno}: {line.strip()}")
    assert not offenders, "invite_token must never be matched by value:\n" + "\n".join(offenders)


def test_no_query_filters_on_webhook_secret_by_value():
    """ISC-29: the same trap for webhook_endpoints.secret."""
    pattern = re.compile(r"WebhookEndpoint\.secret\s*==|\bsecret\s*==\s*(?!None)")
    offenders = []
    for path in _python_sources():
        with open(path, encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                if pattern.search(line):
                    offenders.append(f"{os.path.relpath(path, BACKEND_DIR)}:{lineno}: {line.strip()}")
    assert not offenders, "webhook secret must never be matched by value:\n" + "\n".join(offenders)


def test_creation_sites_set_both_columns():
    """A creation site that sets only invite_token makes the row unfindable."""
    for rel in ("services/org_invite.py", "services/consultant.py"):
        path = os.path.join(BACKEND_DIR, rel)
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        token_sets = source.count("invite_token=")
        hash_sets = source.count("invite_token_hash=")
        assert token_sets == hash_sets, (
            f"{rel}: {token_sets} invite_token= but {hash_sets} invite_token_hash="
        )
