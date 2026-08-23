"""Log-forging defence, shared by the API and the Celery workers.

Lifted verbatim out of ``main.py`` (#784). It lived there as a private class
attached at import time, which meant it protected the FastAPI process and
nothing else. The Celery worker never imports ``main``, so it never had the
filter — a fact that did not matter while the worker's INFO logging was being
silently discarded, and starts mattering the moment that is fixed.

Placement note: this is a *handler*-level filter, not a logger-level one.
Filters on a logger are not consulted for records that propagate up from child
loggers; filters on a handler are. Since essentially every module here uses
``logging.getLogger(__name__)`` and propagates to root, the handler is the only
attachment point that covers all of them.
"""
from __future__ import annotations

import logging


class LogForgingSanitizer(logging.Filter):
    """Escape CR/LF in log records to prevent log forging / log injection.

    User-influenced values interpolated into log messages can contain newlines
    that inject forged log lines (CodeQL ``py/log-injection``). Attaching this
    to the root handlers gives one defence-in-depth control instead of
    sanitising 60+ call sites.

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


#: One shared instance, so re-attaching is idempotent by identity.
log_forging_sanitizer = LogForgingSanitizer()


def attach_to_handlers(logger: logging.Logger | None = None) -> None:
    """Attach the sanitizer to ``logger``'s handlers (root by default).

    Idempotent: re-attaching after Celery has reconfigured logging is the
    normal case, and a handler that already has the filter is skipped.
    """
    target = logger if logger is not None else logging.getLogger()
    for handler in target.handlers:
        if log_forging_sanitizer not in handler.filters:
            handler.addFilter(log_forging_sanitizer)
