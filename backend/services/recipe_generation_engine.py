"""
AI recipe generation engine for the systems knowledge catalog.

For custom systems with no catalog template, generates L1-L4 evidence
collection recipes grounded in the vendor's real admin console / API surface,
using the Anthropic API with the server-side web_search tool and a
`submit_recipes` client tool that forces structured output.

Mirrors services/vendor_assessment_engine.py. Synchronous by design — called
from the Celery worker (`tasks_recipe_generation.run_recipe_generation`).

Modes:
    - Live: requires ANTHROPIC_API_KEY. Model from SYSTEMS_AI_MODEL
      (default "claude-sonnet-4-6"), web_search max_uses 6.
    - Mock: SYSTEMS_AI_MOCK=1 or no ANTHROPIC_API_KEY — returns a clearly
      marked sample recipe set so the whole flow works keyless.

Entry point: run_generation()
"""
import logging
import os
import re
from typing import Any, Dict, List, Optional

from services.system_catalog_validation import validate_recipes_map, RECIPE_LEVELS

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-4-6"
MAX_OUTPUT_TOKENS = 16384
WEB_SEARCH_MAX_USES = 6
MAX_LOOP_ITERATIONS = 10


class RecipeGenerationError(Exception):
    """Raised when the engine cannot produce valid recipes."""


def slugify(value: str) -> str:
    """Kebab-case a system name for use in an org-private template slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return slug or "system"


def is_mock_mode() -> bool:
    """Mock mode: explicit flag, or no API key configured."""
    if os.getenv("SYSTEMS_AI_MOCK", "").strip() == "1":
        return True
    return not os.getenv("ANTHROPIC_API_KEY", "").strip()


# ---------------------------------------------------------------------------
# Prompts and tools
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a GRC evidence-automation specialist. Given a software system used by an organisation, you produce step-by-step evidence collection recipes at four maturity levels, describing how compliance teams gather audit evidence FROM that system.

## Maturity levels

- **L1 (Ad hoc / manual):** a person signs in to the admin console and manually exports or screenshots evidence. 4-6 concrete steps with real console navigation paths.
- **L2 (Scheduled exports):** the system's built-in scheduled reports / exports deliver evidence on a cadence without custom code.
- **L3 (API-driven automation):** a script or integration calls the system's real API on a schedule, with credential hygiene, error handling and data validation steps.
- **L4 (Managed pipeline):** continuous collection (webhooks/streaming where the product supports it) with completeness metrics, quality checks and alerting.

## Quality bar

- Ground every step in the vendor's REAL admin console navigation and REAL API endpoints — research with web_search first.
- Cite official vendor documentation URLs in `vendor_docs_url` on steps that reference consoles or APIs (L3/L4 API steps especially).
- Set `permissions_required` on steps needing privileged roles (use the vendor's real role names).
- Add `security_note` where credentials/tokens are handled, and `audit_note` where it affects evidentiary value (immutability, timestamps, completeness).
- If the product genuinely lacks a capability (e.g. no webhooks), say so in the step and give the closest real alternative — never invent features.
- Use British English spelling throughout (organisation, authorise, centre).

## Output

When research is complete, call the `submit_recipes` tool exactly once with all four levels (L1, L2, L3, L4) and the list of documentation source URLs you relied on. The tool call is the only accepted output channel."""

WEB_SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": WEB_SEARCH_MAX_USES,
}

_RECIPE_STEP_SCHEMA = {
    "type": "object",
    "properties": {
        "step": {"type": "integer"},
        "action": {"type": "string"},
        "permissions_required": {"type": "string"},
        "security_note": {"type": "string"},
        "audit_note": {"type": "string"},
        "vendor_docs_url": {"type": "string"},
    },
    "required": ["step", "action"],
}

_RECIPE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "estimated_time": {"type": "string"},
        "frequency": {"type": "string"},
        "steps": {"type": "array", "items": _RECIPE_STEP_SCHEMA, "minItems": 3},
    },
    "required": ["title", "steps"],
}

SUBMIT_RECIPES_TOOL = {
    "name": "submit_recipes",
    "description": (
        "Submit the completed evidence collection recipes. Call exactly once, "
        "after research is complete, with all four maturity levels."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "recipes": {
                "type": "object",
                "properties": {level: _RECIPE_SCHEMA for level in RECIPE_LEVELS},
                "required": list(RECIPE_LEVELS),
            },
            "sources": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["recipes"],
    },
}


def build_user_prompt(system_name: str, vendor: Optional[str], system_type: str, description: Optional[str]) -> str:
    lines = [
        "Generate evidence collection recipes for the following system.",
        "",
        f"- **System name:** {system_name}",
        f"- **Vendor:** {vendor or 'Unknown'}",
        f"- **System type:** {system_type}",
        f"- **Description:** {description or 'Not provided'}",
        "",
        "Research the vendor's admin console and API documentation with the",
        "web_search tool, then call submit_recipes with L1-L4 recipes.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Mock mode
# ---------------------------------------------------------------------------

def build_mock_recipes(system_name: str, system_type: str) -> Dict[str, Any]:
    """Deterministic sample recipes for keyless demos. Clearly marked as mock."""
    def steps(flavour: str) -> List[Dict[str, Any]]:
        return [
            {
                "step": 1,
                "action": (
                    f"Sample guidance — no AI call was made. Sign in to {system_name} "
                    f"and locate the {flavour} area for the evidence you need."
                ),
                "security_note": "Use a least-privilege read-only account for evidence collection.",
            },
            {
                "step": 2,
                "action": f"Export or capture the relevant records from {system_name} with a date-stamped filename.",
                "audit_note": "Record the collection date and period covered so reviewers can verify continuity.",
            },
            {
                "step": 3,
                "action": "Upload the export to the evidence repository against the correct evidence ID.",
            },
        ]

    return {
        "recipes": {
            "L1": {
                "title": f"[SAMPLE] {system_name} manual evidence collection",
                "estimated_time": "15 minutes",
                "frequency": "weekly",
                "steps": steps("reporting or export"),
            },
            "L2": {
                "title": f"[SAMPLE] {system_name} scheduled report collection",
                "estimated_time": "30 minutes setup",
                "frequency": "automated weekly",
                "steps": steps("scheduled reports"),
            },
            "L3": {
                "title": f"[SAMPLE] {system_name} API-driven collection",
                "estimated_time": "2 hours setup",
                "frequency": "automated daily",
                "steps": steps("API or integration"),
            },
            "L4": {
                "title": f"[SAMPLE] {system_name} managed evidence pipeline",
                "estimated_time": "4 hours setup",
                "frequency": "continuous",
                "steps": steps("webhook or streaming"),
            },
        },
        "sources": [],
    }


# ---------------------------------------------------------------------------
# Anthropic call loop
# ---------------------------------------------------------------------------

def _tool_use_input(block) -> Dict[str, Any]:
    return dict(getattr(block, "input", None) or {})


def _find_submission(content: List[Any]):
    for block in content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", "") == "submit_recipes":
            return block
    return None


def _collect_sources(content: List[Any], sources: List[str]) -> None:
    """Accumulate result URLs from server-side web_search tool blocks.

    A failed search returns a result block whose content is an error object
    rather than a list — skip those instead of crashing the generation loop.
    """
    for block in content:
        if getattr(block, "type", None) == "web_search_tool_result":
            items = getattr(block, "content", None)
            if not isinstance(items, (list, tuple)):
                continue
            for item in items:
                url = getattr(item, "url", None)
                if url and url not in sources:
                    sources.append(url)


def _call_anthropic_for_recipes(user_prompt: str, model: str, sources: List[str]) -> Dict[str, Any]:
    """
    Run the research/submission loop until a valid submit_recipes call arrives.
    An invalid submission is retried exactly once with the validation errors
    fed back as an error tool_result.
    """
    import anthropic

    client = anthropic.Anthropic(timeout=540.0)
    messages: List[Dict[str, Any]] = [{"role": "user", "content": user_prompt}]
    tools = [WEB_SEARCH_TOOL, SUBMIT_RECIPES_TOOL]
    invalid_attempts = 0
    nudges = 0

    for _ in range(MAX_LOOP_ITERATIONS):
        response = client.messages.create(
            model=model,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=SYSTEM_PROMPT,
            messages=messages,
            tools=tools,
        )
        content = list(response.content or [])
        _collect_sources(content, sources)

        submission = _find_submission(content)
        if submission is not None:
            payload = _tool_use_input(submission)
            recipes = payload.get("recipes") or {}
            problems = validate_recipes_map(recipes)
            missing = [lvl for lvl in RECIPE_LEVELS if lvl not in recipes]
            if missing:
                problems.append(f"missing maturity levels: {', '.join(missing)}")
            if not problems:
                return {"recipes": recipes, "sources": payload.get("sources") or sources}
            invalid_attempts += 1
            logger.warning("submit_recipes validation failed (attempt %d): %s", invalid_attempts, problems)
            if invalid_attempts > 1:
                raise RecipeGenerationError(
                    f"Model produced invalid recipes twice: {'; '.join(problems)}"
                )
            messages.append({"role": "assistant", "content": content})
            messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": getattr(submission, "id", ""),
                    "is_error": True,
                    "content": (
                        "The submitted recipes were rejected: " + "; ".join(problems)
                        + ". Call submit_recipes again with corrected values."
                    ),
                }],
            })
            continue

        if getattr(response, "stop_reason", None) == "pause_turn":
            messages.append({"role": "assistant", "content": content})
            continue

        nudges += 1
        if nudges > 2:
            raise RecipeGenerationError("Model ended the conversation without calling submit_recipes")
        messages.append({"role": "assistant", "content": content})
        messages.append({
            "role": "user",
            "content": "Now call the submit_recipes tool with the completed L1-L4 recipes.",
        })

    raise RecipeGenerationError(f"No valid recipes produced within {MAX_LOOP_ITERATIONS} model calls")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_generation(
    system_name: str,
    vendor: Optional[str],
    system_type: str,
    description: Optional[str],
) -> Dict[str, Any]:
    """
    Generate L1-L4 collection recipes for a system. Synchronous — call from
    a Celery worker.

    Returns {"recipes": {level: recipe}, "sources": [urls]}.
    """
    if is_mock_mode():
        logger.info("Recipe generation engine running in MOCK mode for %s", system_name)
        result = build_mock_recipes(system_name, system_type)
    else:
        model = os.getenv("SYSTEMS_AI_MODEL", DEFAULT_MODEL)
        logger.info("Recipe generation engine calling Anthropic (model=%s) for %s", model, system_name)
        sources: List[str] = []
        user_prompt = build_user_prompt(system_name, vendor, system_type, description)
        result = _call_anthropic_for_recipes(user_prompt, model, sources)

    problems = validate_recipes_map(result["recipes"])
    if problems:
        raise RecipeGenerationError(f"Generated recipes failed validation: {'; '.join(problems)}")
    return result
