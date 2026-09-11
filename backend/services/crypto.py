"""Application-layer encryption for credential-bearing columns.

Contract §3a. The key is `SCF_SECRET_KEY`, resolved through
`services.secrets.get_secret` (file tier, then env — it is a tier-1 name, so it
is structurally unreachable from the database).

Ciphertext is stored with a version prefix so a column can hold a mix of legacy
plaintext and encrypted values during a rolling upgrade. Reads of unprefixed
values are counted and warned about once per process, which is what
`backfill-encrypt` and the Integrations health view report on.

The key value is NEVER derived from any other credential, and no other
credential name is read in this module (ISC-23).
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from services import secrets as _secrets

logger = logging.getLogger(__name__)

PREFIX = "enc:v1:"

_KEY_NAME = "SCF_SECRET_KEY"


class SecretKeyMissing(Exception):
    """No encryption key is configured."""


class SecretKeyInvalid(Exception):
    """The configured key is not a valid Fernet key."""


class DecryptError(Exception):
    """A prefixed value could not be decrypted with any configured key."""


_lock = threading.Lock()
_cached_key_string: Optional[str] = None
_cached_fernet: Optional[MultiFernet] = None
_legacy_reads: int = 0
_legacy_warned: bool = False
_bind_plaintext_warned: bool = False


def reset() -> None:
    """Drop the cached instance and every counter. Tests only."""
    global _cached_key_string, _cached_fernet, _legacy_reads
    global _legacy_warned, _bind_plaintext_warned
    with _lock:
        _cached_key_string = None
        _cached_fernet = None
        _legacy_reads = 0
        _legacy_warned = False
        _bind_plaintext_warned = False


def get_fernet() -> Optional[MultiFernet]:
    """Return a MultiFernet over the configured key list, or None when unset.

    The value is a comma-separated list; the FIRST entry is the primary key and
    is the one used for every new encryption. The rest are decrypt-only, which
    is what makes key rotation a two-step operation.

    The instance is cached against the key STRING, so changing the configured
    value yields a new instance without a process restart.
    """
    global _cached_key_string, _cached_fernet

    raw = _secrets.get_secret(_KEY_NAME)
    if raw is None or not raw.strip():
        return None
    raw = raw.strip()

    with _lock:
        if raw == _cached_key_string and _cached_fernet is not None:
            return _cached_fernet

        parts = [p.strip() for p in raw.split(",")]
        parts = [p for p in parts if p]
        if not parts:
            return None

        keys = []
        for index, part in enumerate(parts):
            try:
                keys.append(Fernet(part.encode("utf-8")))
            except (ValueError, TypeError) as exc:
                raise SecretKeyInvalid(
                    f"{_KEY_NAME} entry {index + 1} of {len(parts)} is not a valid "
                    f"Fernet key (44 url-safe base64 characters)"
                ) from exc

        instance = MultiFernet(keys)
        _cached_key_string = raw
        _cached_fernet = instance
        return instance


def is_encrypted(value: Optional[str]) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt(plaintext: str) -> str:
    """Encrypt under the primary key. Raises SecretKeyMissing when unconfigured."""
    fernet = get_fernet()
    if fernet is None:
        raise SecretKeyMissing(
            f"{_KEY_NAME} is not configured — see docs"
        )
    token = fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")
    return PREFIX + token


def decrypt(value: str) -> str:
    """Decrypt a prefixed value; pass an unprefixed legacy value straight back."""
    global _legacy_reads, _legacy_warned

    if not is_encrypted(value):
        with _lock:
            _legacy_reads += 1
            warn = not _legacy_warned
            _legacy_warned = True
        if warn:
            logger.warning(
                "Read a legacy plaintext credential value from the database. "
                "Run `python -m cli.admin backfill-encrypt` to encrypt existing rows."
            )
        return value

    fernet = get_fernet()
    if fernet is None:
        raise SecretKeyMissing(
            f"{_KEY_NAME} is not configured — see docs"
        )
    token = value[len(PREFIX):].encode("utf-8")
    try:
        return fernet.decrypt(token).decode("utf-8")
    except InvalidToken as exc:
        raise DecryptError(
            "Stored value could not be decrypted with any configured key"
        ) from exc


def legacy_read_count() -> int:
    return _legacy_reads


class EncryptedString(TypeDecorator):
    """Transparently encrypt a Text column at the application layer.

    Bind: None stays None; an already-prefixed value is stored verbatim (so a
    re-save of a row that was never decrypted cannot double-encrypt); otherwise
    the value is encrypted when a key is configured, and stored as plaintext
    when it is not, which keeps a keyless legacy install working.

    Result: `decrypt`, which returns legacy plaintext untouched.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        global _bind_plaintext_warned

        if value is None:
            return None
        if not isinstance(value, str):
            value = str(value)
        if is_encrypted(value):
            return value

        fernet = get_fernet()
        if fernet is None:
            with _lock:
                warn = not _bind_plaintext_warned
                _bind_plaintext_warned = True
            if warn:
                logger.warning(
                    "%s is not configured — storing a credential column as plaintext. "
                    "Set it and run `python -m cli.admin backfill-encrypt`.",
                    _KEY_NAME,
                )
            return value
        return encrypt(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return decrypt(value)
