"""
Validation for system catalog vendor seed files (backend/data/system_catalog/).

Pure functions, no DB access — used by the seeder (skip invalid files with a
logged error), the validation CLI, and the AI recipe-generation engine
(validate model output before persisting).
"""
import re

# Canonical system-type list — schemas.SYSTEM_TYPES (the API validation
# pattern) is derived from this, so new types are added in exactly one place.
SYSTEM_TYPE_LIST = (
    "cloud_provider", "identity_provider", "ticketing", "logging",
    "security_tool", "code_repository", "document_management",
    "endpoint_management", "vulnerability_management", "email_security",
    "security_awareness", "password_manager", "communication", "hr_system",
    "custom",
)

VALID_SYSTEM_TYPES = set(SYSTEM_TYPE_LIST)

RECIPE_LEVELS = ("L1", "L2", "L3", "L4")

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

_REQUIRED_STR_FIELDS = ("slug", "name", "vendor", "system_type", "description", "version")
_OPTIONAL_STR_FIELDS = ("category", "website", "logo_hint")


def validate_recipe(recipe, where: str) -> list:
    """Validate a single recipe dict. `where` names the location for error messages."""
    errors = []
    if not isinstance(recipe, dict):
        return [f"{where}: recipe must be an object"]
    if not recipe.get("title") or not isinstance(recipe.get("title"), str):
        errors.append(f"{where}: recipe title is required")
    for opt in ("estimated_time", "frequency"):
        if opt in recipe and recipe[opt] is not None and not isinstance(recipe[opt], str):
            errors.append(f"{where}: {opt} must be a string")
    steps = recipe.get("steps")
    if not isinstance(steps, list) or not steps:
        errors.append(f"{where}: steps must be a non-empty array")
        return errors
    for i, step in enumerate(steps):
        loc = f"{where}.steps[{i}]"
        if not isinstance(step, dict):
            errors.append(f"{loc}: must be an object")
            continue
        if not isinstance(step.get("step"), int):
            errors.append(f"{loc}: step number (int) is required")
        if not step.get("action") or not isinstance(step.get("action"), str):
            errors.append(f"{loc}: action is required")
        for opt in ("permissions_required", "security_note", "audit_note", "vendor_docs_url"):
            if opt in step and step[opt] is not None and not isinstance(step[opt], str):
                errors.append(f"{loc}: {opt} must be a string")
    return errors


def validate_recipes_map(recipes, where: str = "recipes", require_all_levels: bool = False) -> list:
    """Validate a {level: recipe} map (used for vendor files and AI output).

    With require_all_levels, every level in RECIPE_LEVELS must be present —
    seed files ship complete L1-L4 ladders so recipe resolution never has to
    fall through to another source mid-ladder.
    """
    errors = []
    if not isinstance(recipes, dict) or not recipes:
        return [f"{where}: must be a non-empty object keyed by maturity level"]
    for level, recipe in recipes.items():
        if level not in RECIPE_LEVELS:
            errors.append(
                f"{where}: unknown maturity level '{level}' "
                f"(expected one of {', '.join(RECIPE_LEVELS)})"
            )
            continue
        errors.extend(validate_recipe(recipe, f"{where}.{level}"))
    if require_all_levels:
        missing = [lvl for lvl in RECIPE_LEVELS if lvl not in recipes]
        if missing:
            errors.append(f"{where}: missing maturity levels: {', '.join(missing)}")
    return errors


def validate_vendor_file(data) -> list:
    """Validate a full vendor seed file. Returns a list of error strings (empty = valid)."""
    if not isinstance(data, dict):
        return ["file root must be a JSON object"]
    errors = []
    for field in _REQUIRED_STR_FIELDS:
        if not data.get(field) or not isinstance(data.get(field), str):
            errors.append(f"{field} is required and must be a string")
    for field in _OPTIONAL_STR_FIELDS:
        if field in data and data[field] is not None and not isinstance(data[field], str):
            errors.append(f"{field} must be a string")
    slug = data.get("slug")
    if isinstance(slug, str) and not _SLUG_RE.match(slug):
        errors.append("slug must be lowercase kebab-case (a-z, 0-9, hyphens)")
    system_type = data.get("system_type")
    if isinstance(system_type, str) and system_type not in VALID_SYSTEM_TYPES:
        errors.append(f"system_type '{system_type}' is not a valid type")
    aliases = data.get("aliases", [])
    if not isinstance(aliases, list) or not all(isinstance(a, str) for a in aliases):
        errors.append("aliases must be an array of strings")
    errors.extend(validate_recipes_map(data.get("recipes"), "recipes", require_all_levels=True))
    return errors


def validate_fallbacks_file(data) -> list:
    """Validate _fallbacks.json: {version, fallbacks: {system_type: {level: recipe}}}."""
    if not isinstance(data, dict):
        return ["file root must be a JSON object"]
    errors = []
    if not isinstance(data.get("version"), str) or not data.get("version"):
        errors.append("version is required and must be a string")
    fallbacks = data.get("fallbacks")
    if not isinstance(fallbacks, dict) or not fallbacks:
        return errors + ["fallbacks must be a non-empty object keyed by system_type"]
    for system_type, recipes in fallbacks.items():
        if system_type not in VALID_SYSTEM_TYPES:
            errors.append(f"fallbacks: unknown system_type '{system_type}'")
            continue
        errors.extend(validate_recipes_map(recipes, f"fallbacks.{system_type}", require_all_levels=True))
    return errors
