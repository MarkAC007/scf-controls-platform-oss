"""
Recipe resolution for the systems knowledge catalog.

Resolves an organization's System to a SystemCatalogTemplate (and its
recipes) in three layers:

1. explicit link  — System.catalog_template_id ("template")
2. alias matching — name/vendor matched against global templates ("alias")
3. type fallback  — the generic template for the system_type ("fallback")

`match_template` is a pure function over template-shaped objects so the
matching heuristics are unit-testable without a database.
"""
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from catalog_models import SystemCatalogTemplate, SystemCatalogRecipe

# Substring matches shorter than this are ignored ("Git" must not match GitHub)
MIN_SUBSTRING_MATCH = 4


@dataclass
class RecipeResolution:
    template: Optional[SystemCatalogTemplate]
    matched_via: str  # "template" | "alias" | "fallback" | "none"
    recipes: List[SystemCatalogRecipe] = field(default_factory=list)


def _norm(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def match_template(templates: Sequence, *, name: str, vendor: Optional[str], system_type: str):
    """
    Match a system's name/vendor against global, non-fallback templates.

    Exact matches (slug, template name, or alias equal to the system's name or
    vendor) beat substring containment; ties prefer the same system_type, then
    the longest matched string. Substring matches additionally require the
    template's system_type to equal the system's — shared vendor words
    ("Microsoft" in "Microsoft Corporation") must not link an unrelated
    product across types. Returns the winning template or None.
    """
    sys_name = _norm(name)
    sys_vendor = _norm(vendor)
    sys_values = [v for v in (sys_name, sys_vendor) if v]
    if not sys_values:
        return None

    best = None
    best_key = None  # (exactness, same_type, match_length)

    for template in templates:
        if getattr(template, "is_fallback", False) or getattr(template, "organization_id", None):
            continue

        same_type = template.system_type == system_type

        candidates = [_norm(template.slug), _norm(template.name), _norm(template.vendor)]
        candidates += [_norm(a) for a in (template.aliases or [])]
        candidates = [c for c in candidates if c]

        for cand in candidates:
            for sys_val in sys_values:
                if sys_val == cand:
                    exact = 1
                elif not same_type:
                    continue
                elif len(cand) >= MIN_SUBSTRING_MATCH and cand in sys_val:
                    exact = 0
                elif len(sys_val) >= MIN_SUBSTRING_MATCH and sys_val in cand:
                    exact = 0
                else:
                    continue
                key = (exact, 1 if same_type else 0, len(cand))
                if best_key is None or key > best_key:
                    best, best_key = template, key

    return best


async def resolve_recipes_for_system(session: AsyncSession, system) -> RecipeResolution:
    """Resolve a System row to its template and recipes (template → alias → fallback)."""
    # Layer 1: explicit template link
    if system.catalog_template_id:
        result = await session.execute(
            select(SystemCatalogTemplate)
            .where(SystemCatalogTemplate.id == system.catalog_template_id)
            .options(selectinload(SystemCatalogTemplate.recipes))
        )
        template = result.scalar_one_or_none()
        if template is not None and (
            template.organization_id is None
            or template.organization_id == system.organization_id
        ):
            return RecipeResolution(template, "template", list(template.recipes))

    # Layer 2: alias matching over global templates. Matching only reads
    # template columns, so recipes (bulky steps JSONB) are fetched for the
    # winner alone rather than selectinload-ing the whole catalog.
    result = await session.execute(
        select(SystemCatalogTemplate).where(
            SystemCatalogTemplate.organization_id.is_(None),
            SystemCatalogTemplate.is_fallback.is_(False),
        )
    )
    globals_ = result.scalars().all()
    matched = match_template(
        globals_, name=system.name, vendor=system.vendor, system_type=system.system_type
    )
    if matched is not None:
        recipes_result = await session.execute(
            select(SystemCatalogRecipe)
            .where(SystemCatalogRecipe.template_id == matched.id)
            .order_by(SystemCatalogRecipe.maturity_level)
        )
        return RecipeResolution(matched, "alias", list(recipes_result.scalars().all()))

    # Layer 3: per-type generic fallback
    result = await session.execute(
        select(SystemCatalogTemplate)
        .where(
            SystemCatalogTemplate.organization_id.is_(None),
            SystemCatalogTemplate.is_fallback.is_(True),
            SystemCatalogTemplate.system_type == system.system_type,
        )
        .options(selectinload(SystemCatalogTemplate.recipes))
    )
    fallback = result.scalars().first()
    if fallback is not None:
        return RecipeResolution(fallback, "fallback", list(fallback.recipes))

    return RecipeResolution(None, "none", [])
