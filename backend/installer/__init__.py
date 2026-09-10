"""First-run provisioning wizard for the SCF Controls Platform.

The package is deliberately self-contained: it runs from the backend image
*before* any database, `.env` or secrets directory exists, so it must not import
``main``, ``database`` or the ORM models.  Its only dependencies are the ones the
backend image already ships (FastAPI, uvicorn, asyncpg, cryptography).

Entry point: ``python -m installer serve|unattended|import-env``.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

__all__ = [
    "LOGGER_NAME",
    "RedactionFilter",
    "configure_logging",
    "get_logger",
    "redact",
    "secrets_dir",
    "out_dir",
    "host_secrets_dir",
]

LOGGER_NAME = "scf.installer"

# Container-side mount points.  Overridable so the package can be unit-tested
# and dev-run outside a container; production paths come from install.sh.
_DEFAULT_SECRETS_DIR = "/secrets"
_DEFAULT_OUT_DIR = "/out"

# Anything shaped like a credential never reaches a log record.
_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    # scheme://user:password@host  ->  scheme://user:***@host
    (re.compile(r"(://[^:@/\s]+:)[^@\s]+(@)"), r"\1***\2"),
    # password=..., PWD=..., password: ...
    (re.compile(r"(?i)\b(password|passwd|pwd)\s*[=:]\s*\S+"), r"\1=***"),
)


def redact(text: str) -> str:
    """Strip DSN passwords and ``password=`` pairs out of arbitrary text."""
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


class RedactionFilter(logging.Filter):
    """Rewrite log records in place so a credential can never reach a handler."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 - logging API
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: redact(str(v)) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(redact(str(a)) for a in record.args)
        return True


def configure_logging(level: str = "INFO") -> logging.Logger:
    """Install the wizard logger with the redaction filter attached."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    if not any(isinstance(f, RedactionFilter) for f in logger.filters):
        logger.addFilter(RedactionFilter())
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        handler.addFilter(RedactionFilter())
        logger.addHandler(handler)
    logger.propagate = False
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def secrets_dir() -> Path:
    return Path(os.environ.get("SCF_INSTALLER_SECRETS_DIR", _DEFAULT_SECRETS_DIR))


def out_dir() -> Path:
    return Path(os.environ.get("SCF_INSTALLER_OUT_DIR", _DEFAULT_OUT_DIR))


def host_secrets_dir() -> str:
    """Absolute HOST path of the secrets directory, as passed by install.sh.

    Falls back to the container path so a dev run still writes something usable.
    """
    return os.environ.get("SCF_SECRETS_DIR_HOST") or str(secrets_dir())
