"""
Licence gating for document generation.

The Secure Controls Framework is published under CC BY-ND 4.0 — attribution
required, **no derivatives**. That "ND" is why this module exists.

Two distinct questions, deliberately not collapsed into one switch:

**Tier 1 — tabular output.** A Statement of Applicability lists SCF IDs and
control names beside the organisation's own implementation status. Nothing is
reworded. This is arguably a compilation of factual identifiers rather than a
derivative work, which is the position that keeps the free Council licence
intact. Tier 1 is therefore gated only by the feature toggle.

**Tier 2/3 — generated prose.** A language model reads SCF control descriptions
and assessment objectives and writes policy text from them. That is
unambiguously a derivative work. It needs its own switch, its own
acknowledgement, and its own audit trail.

Gating both behind a single boolean would surrender the free-licence position
for no benefit — an organisation that only wants an SoA would be made to
acknowledge a derivative-work notice that does not apply to what it is doing.

Enforcement is layered, because a boolean in a settings table proves nothing on
its own:

1. **Database** — ``ck_doc_gen_settings_enabled_requires_acknowledgement``
   makes an enabled-but-unacknowledged row physically unstorable.
2. **Service** — :func:`assert_generation_allowed`, called by the Celery task
   before any work begins, so a queued job cannot outlive the permission that
   queued it.
3. **API** — the same check at the request boundary, so the user gets a 403
   rather than a failed job.
4. **UI** — the toggle and the notice.

The UI is the weakest of the four and is treated as courtesy, not control. A
direct ``PUT`` from a script bypasses it entirely; layers 1-3 do not care.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

#: Bump when the acknowledgement wording changes materially. Stored on the
#: settings row so an organisation that acknowledged v1 is not silently treated
#: as having accepted v2.
LICENCE_TEXT_VERSION = "v1"

#: Platform-wide kill switch, independent of any organisation's settings.
#: Set ``DOC_GEN_DISABLED=1`` to stop all generation everywhere — the lever to
#: pull if the Council's position on derivation changes and every tenant needs
#: turning off at once, without a deploy or a database migration.
_PLATFORM_KILL_SWITCH_ENV = "DOC_GEN_DISABLED"

ATTRIBUTION_NOTICE = (
    "Portions of this document are derived from the Secure Controls Framework "
    "(SCF), (c) Secure Controls Framework Council, LLC, licensed under "
    "CC BY-ND 4.0. Control identifiers, names, and descriptions are reproduced "
    "under that licence."
)

ACKNOWLEDGEMENT_TEXT = (
    "Enabling AI-augmented document generation produces derivative works of the "
    "Secure Controls Framework. The SCF is licensed CC BY-ND 4.0, which does not "
    "permit derivatives without separate arrangement with the SCF Council. By "
    "enabling this, you confirm your organisation has reviewed its SCF licence "
    "position and accepts responsibility for the documents it generates."
)


class LicenceError(PermissionError):
    """Raised when generation is refused on licensing grounds.

    A ``PermissionError`` subclass so the API layer can map it to 403 without
    knowing anything about licensing.
    """


@dataclass(frozen=True)
class GenerationPermission:
    """The outcome of a gate check, with a reason attached to a refusal."""

    allowed: bool
    reason: Optional[str] = None

    def raise_if_denied(self) -> None:
        if not self.allowed:
            raise LicenceError(self.reason or "Document generation is not permitted")


def platform_kill_switch_engaged() -> bool:
    """True when the platform-wide disable flag is set."""
    return os.getenv(_PLATFORM_KILL_SWITCH_ENV, "").strip().lower() in ("1", "true", "yes")


def is_derivative_tier(tier: int) -> bool:
    """Whether a tier produces derivative work.

    Tier 1 renders tables from data. Tier 2 and 3 have a model write prose from
    SCF content. The per-generator ``is_derivative`` flag in the registry is
    authoritative; this is the default when a generator does not override it.
    """
    return tier >= 2


def check_generation_allowed(settings, *, tier: int, is_derivative: bool) -> GenerationPermission:
    """Decide whether one generation may proceed.

    Args:
        settings: The organisation's ``DocGenSettings`` row, or ``None`` if the
            organisation has never enabled the feature.
        tier: Generation tier of the requested generator.
        is_derivative: The generator's own derivation flag from the registry.

    Returns:
        A :class:`GenerationPermission`. The refusal reason is written to be
        shown to a user, so it says what to do, not just what failed.
    """
    if platform_kill_switch_engaged():
        return GenerationPermission(
            False,
            "Document generation is disabled platform-wide by the operator.",
        )

    if settings is None or not settings.enabled:
        return GenerationPermission(
            False,
            "Document generation is not enabled for this organisation. "
            "An administrator can enable it in Settings.",
        )

    # Belt and braces against the check constraint: if a row somehow carries
    # enabled without an acknowledgement, refuse rather than trust it.
    if settings.licence_acknowledged_at is None:
        return GenerationPermission(
            False,
            "Document generation is enabled but the SCF licence acknowledgement "
            "is missing. An administrator must re-confirm it in Settings.",
        )

    if is_derivative and not settings.derivative_generators_enabled:
        return GenerationPermission(
            False,
            "This document is AI-generated from SCF content, which produces a "
            "derivative work. An administrator must enable AI-augmented "
            "generation separately in Settings.",
        )

    return GenerationPermission(True)


def assert_generation_allowed(settings, *, tier: int, is_derivative: bool) -> None:
    """Raise :class:`LicenceError` unless generation may proceed.

    Called at the start of the Celery task as well as at the API boundary. The
    duplication is deliberate: settings can change between a job being queued
    and it running, and a job that outlives its permission must not produce a
    document.
    """
    permission = check_generation_allowed(settings, tier=tier, is_derivative=is_derivative)
    if not permission.allowed:
        logger.warning(
            "doc_gen refused: tier=%s derivative=%s reason=%s",
            tier, is_derivative, permission.reason,
        )
    permission.raise_if_denied()


def attribution_footer(is_derivative: bool) -> str:
    """The attribution block appended to every generated document.

    Attribution is required by CC BY regardless of tier, so this is not
    conditional on derivation — only the wording shifts.
    """
    if is_derivative:
        return f"\n\n---\n\n*{ATTRIBUTION_NOTICE}*\n"
    return (
        "\n\n---\n\n*Control identifiers and names are reproduced from the "
        "Secure Controls Framework (SCF), (c) Secure Controls Framework "
        "Council, LLC, licensed under CC BY-ND 4.0.*\n"
    )
