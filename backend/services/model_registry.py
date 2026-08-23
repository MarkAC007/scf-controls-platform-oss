"""LLM model inventory — the single source of truth for model ids and prices (#782).

Why this module exists
----------------------
Four services shipped with ``claude-sonnet-4-20250514`` hardcoded at module
scope. That model was retired; the API answers **404**. AI evidence review was
therefore entirely non-functional in production, and failed *soft* — the row
was written with ``status=error`` and the UI drew a low-severity "Error" chip,
so nothing escalated.

The tempting reading is "the services with an env override got maintained and
the ones without rotted". Git history says otherwise: the dead-pin services were
authored in April 2026, when that id was current; the env-var convention simply
arrived with newer code. **The cause is that a model id was chosen once, at
authoring time, and nothing ever revisited it.** Adding an override knob to each
call site would not have prevented this — it would have given each site its own
knob nobody turned.

So: one inventory, in one file, that a test can enumerate and a script can check
against the live ``GET /v1/models``. Call sites ask for a *role*, never for an id.

Price lives with the model, not the call site
---------------------------------------------
The four dead-pin services each carried their own copy of::

    INPUT_COST_PER_TOKEN  = 3.0 / 1_000_000
    OUTPUT_COST_PER_TOKEN = 15.0 / 1_000_000

Those are Sonnet-tier prices written next to a Sonnet-tier pin. Repointing the
model without touching them is how a repoint silently corrupts every cost figure
the platform reports — and there is nothing at the call site to remind you.
Here, price is an attribute of the model, so a repoint carries its price with it
and an unpriced model yields ``None`` (stored as NULL) rather than a confident
wrong number.

Import weight
-------------
**Stdlib only, by contract.** This is imported by Celery workers and by a CLI
script that must run without FastAPI startup. No sqlalchemy, no anthropic, no
models. ``tests/test_model_registry.py`` asserts it.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "ModelSpec",
    "MODELS",
    "ROLES",
    "GLOBAL_MODEL_ENV",
    "GLOBAL_DEFAULT_ROLES",
    "COST_TRACKED_ROLES",
    "resolve",
    "spec",
    "cost_cents",
    "anthropic_model_ids",
]


@dataclass(frozen=True)
class ModelSpec:
    """One model the platform is allowed to call.

    ``input_cost_per_mtok`` / ``output_cost_per_mtok`` are USD per million
    tokens, or ``None`` for a model on a path that does not compute cost. A
    ``None`` price is a deliberate declaration, not an oversight — see
    :data:`COST_TRACKED_ROLES`, which refuses to let a cost-computing role point
    at an unpriced model.
    """

    id: str
    provider: str
    input_cost_per_mtok: Optional[float]
    output_cost_per_mtok: Optional[float]
    notes: str


#: Every model id the backend may send to a provider.
#:
#: Anthropic prices are carried forward BYTE-FOR-BYTE from the constants this
#: module replaces ($3/M in, $15/M out — Sonnet tier). ``claude-sonnet-4-6`` is
#: the same tier as the retired ``claude-sonnet-4-20250514``, so consolidating
#: the maps does not re-tune anybody's reported spend. Verify against
#: https://www.anthropic.com/pricing before changing a tier.
MODELS: Dict[str, ModelSpec] = {
    "claude-sonnet-4-6": ModelSpec(
        id="claude-sonnet-4-6",
        provider="anthropic",
        input_cost_per_mtok=3.0,
        output_cost_per_mtok=15.0,
        notes="Sonnet tier. Already live for vendor assessment, recipe generation and doc-gen.",
    ),
    "claude-fable-5": ModelSpec(
        id="claude-fable-5",
        provider="anthropic",
        input_cost_per_mtok=None,
        output_cost_per_mtok=None,
        notes="CDM intent extraction. That path records no cost, so no price is declared.",
    ),
    "gpt-5.5": ModelSpec(
        id="gpt-5.5",
        provider="openai",
        input_cost_per_mtok=None,
        output_cost_per_mtok=None,
        notes="CDM intent extraction, alternate provider. Not covered by the Anthropic liveness check.",
    ),
    "gemini-3.7-flash": ModelSpec(
        id="gemini-3.7-flash",
        provider="google",
        input_cost_per_mtok=None,
        output_cost_per_mtok=None,
        notes=(
            "CDM intent extraction, alternate provider. Newest non-preview Gemini in the live "
            "ListModels response (2026-08-17); the pro line stops at 3.1-preview."
        ),
    ),
}


#: Role -> (environment variable that overrides it, default model id).
#:
#: Call sites name a role. The env var names for vendor / systems / doc-gen are
#: the ones already deployed and are kept exactly as they were — renaming them
#: would silently drop whatever is configured in production today.
ROLES: Dict[str, Tuple[str, str]] = {
    "evidence_assessment": ("EVIDENCE_AI_MODEL", "claude-sonnet-4-6"),
    "artifact_type_extraction": ("ARTIFACT_TYPE_AI_MODEL", "claude-sonnet-4-6"),
    "vendor_assessment": ("VENDOR_AI_MODEL", "claude-sonnet-4-6"),
    "recipe_generation": ("SYSTEMS_AI_MODEL", "claude-sonnet-4-6"),
    "doc_gen": ("DOC_GEN_AI_MODEL", "claude-sonnet-4-6"),
    "cdm_intent_claude": ("CDM_INTENT_CLAUDE_MODEL", "claude-fable-5"),
    "cdm_intent_gpt": ("CDM_INTENT_GPT_MODEL", "gpt-5.5"),
    "cdm_intent_gemini": ("CDM_INTENT_GEMINI_MODEL", "gemini-3.7-flash"),
}

#: The one variable that repoints the platform's model.
#:
#: Models change; the operator should not have to know which five services call
#: one to move them all. Set ``SCF_AI_MODEL`` and every role in
#: :data:`GLOBAL_DEFAULT_ROLES` follows it. A per-role variable still wins over
#: it, so a single service can be held back or pushed forward without unsetting
#: the global.
GLOBAL_MODEL_ENV = "SCF_AI_MODEL"

#: Roles that follow :data:`GLOBAL_MODEL_ENV`.
#:
#: Everything that asks "the platform's model" for a compliance task. The three
#: ``cdm_intent_*`` roles are deliberately absent: they are one job run against
#: three *different providers* on purpose, and a single id cannot be right for
#: all three — pointing the Gemini role at a Claude id would simply 404. Those
#: keep their own variables.
GLOBAL_DEFAULT_ROLES: Tuple[str, ...] = (
    "evidence_assessment",
    "artifact_type_extraction",
    "vendor_assessment",
    "recipe_generation",
    "doc_gen",
)

#: Roles whose call sites write a cost figure to the database. A model without a
#: declared price may not serve one of these — enforced by
#: ``tests/test_model_registry.py``, so a repoint onto an unpriced model fails in
#: CI rather than writing NULL costs nobody notices.
COST_TRACKED_ROLES: Tuple[str, ...] = (
    "evidence_assessment",
    "artifact_type_extraction",
)


#: A model id is interpolated into a request path by the Gemini provider, and
#: into request bodies elsewhere. Overrides come from the environment, which is
#: operator-controlled but not the same as trusted — an id containing ``/`` or
#: ``..`` would redirect the Gemini call to a different endpoint entirely. Every
#: resolved id must match this or it is refused, so the semgrep suppressions at
#: those call sites rest on something real rather than on "it is a module
#: constant", which stopped being true when the constant became configurable.
_SAFE_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

#: Trailing ``-YYYYMMDD`` snapshot suffix. The API may answer with a dated id
#: even when an undated alias was requested, and the dated form will not be a
#: key here. Strip it for pricing lookups only — never write it into
#: :data:`MODELS`, because a dated id is precisely the shape that rotted.
_DATE_SUFFIX = re.compile(r"-\d{8}$")


def _env_override(env_var: str, role: str, fallback: str) -> Optional[str]:
    """A validated model id from ``env_var``, or ``None`` to fall through.

    An override is trusted even when it is not in :data:`MODELS`: production
    must be able to escape a bad default without a deploy. It is logged at
    WARNING so an unrecognised id is visible rather than mysterious, and its
    cost will be ``None`` rather than wrong.
    """
    value = os.getenv(env_var, "").strip()
    if not value:
        return None
    if not _SAFE_MODEL_ID.match(value):
        logger.error(
            "%s is set to %r, which is not a syntactically valid model id. "
            "Ignoring it and using %r. A model id reaches a request URL, so an "
            "id with a path separator in it is refused rather than sent.",
            env_var, value, fallback,
        )
        return None
    if value not in MODELS:
        logger.warning(
            "%s sets role %r to %r, which is not in the model registry. "
            "It will be called as given, but cost cannot be computed for it.",
            env_var, role, value,
        )
    return value


def resolve(role: str) -> str:
    """Model id for ``role``.

    Precedence, most specific first:

    1. the role's own variable — one service held back or pushed forward;
    2. :data:`GLOBAL_MODEL_ENV`, for roles in :data:`GLOBAL_DEFAULT_ROLES` —
       the one place to move the whole platform onto a new model;
    3. the id declared in :data:`ROLES`.

    The global sits *between* them rather than replacing per-role variables
    because both questions get asked: "put everything on the new model" is the
    common one, and "everything except doc-gen, which regressed" is the one you
    need at 2am.
    """
    try:
        env_var, default = ROLES[role]
    except KeyError:
        raise KeyError(
            f"Unknown model role {role!r}. Add it to ROLES rather than hardcoding an id."
        ) from None

    specific = _env_override(env_var, role, default)
    if specific is not None:
        return specific

    if role in GLOBAL_DEFAULT_ROLES:
        shared = _env_override(GLOBAL_MODEL_ENV, role, default)
        if shared is not None:
            return shared

    return default


def spec(model_id: Optional[str]) -> Optional[ModelSpec]:
    """Registry entry for ``model_id``, tolerating a dated snapshot suffix."""
    if not model_id:
        return None
    found = MODELS.get(model_id)
    if found is not None:
        return found
    return MODELS.get(_DATE_SUFFIX.sub("", model_id))


def cost_cents(
    model_id: Optional[str], input_tokens: int, output_tokens: int
) -> Optional[float]:
    """Cost in cents for a call, or ``None`` when the model has no declared price.

    Pass the model the API **actually answered with**, not the one requested —
    that is the whole point of pricing by model. ``None`` is the honest answer
    for an unknown model: a NULL cost reads as "we do not know", whereas a
    figure computed from some other model's rate card reads as fact and is
    wrong.
    """
    found = spec(model_id)
    if found is None or found.input_cost_per_mtok is None or found.output_cost_per_mtok is None:
        logger.warning(
            "No declared price for model %r — this row's cost is recorded as "
            "NULL, and SUM() over the cost column silently excludes it, so the "
            "organisation's reported total spend will be short by whatever this "
            "call actually cost. Add the model to services/model_registry.MODELS.",
            model_id,
        )
        return None
    dollars = (
        input_tokens * found.input_cost_per_mtok
        + output_tokens * found.output_cost_per_mtok
    ) / 1_000_000
    return round(dollars * 100, 4)


def anthropic_model_ids() -> Tuple[str, ...]:
    """Every Anthropic id in the registry, for the live liveness check."""
    return tuple(sorted(m.id for m in MODELS.values() if m.provider == "anthropic"))
