"""
System Catalog API endpoints.

Read-only access to the systems knowledge catalog: known-system templates
(powering the add-system template picker) and their per-maturity-level
collection recipes. Global catalog entries only — org-private AI-generated
templates are served through the per-system recipes endpoint instead.
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, load_only

from database import get_db
from auth import require_auth
from catalog_models import SystemCatalogTemplate, SystemCatalogRecipe
from schemas import (
    SystemCatalogTemplateSummary,
    SystemCatalogTemplateDetail,
    SystemCatalogRecipeResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/system-catalog", tags=["system-catalog"])


def template_summary(template: SystemCatalogTemplate) -> SystemCatalogTemplateSummary:
    """Map a template row (with recipes loaded) to its picker summary."""
    return SystemCatalogTemplateSummary(
        id=template.id,
        slug=template.slug,
        name=template.name,
        vendor=template.vendor,
        system_type=template.system_type,
        category=template.category,
        description=template.description,
        website=template.website,
        logo_hint=template.logo_hint,
        is_fallback=template.is_fallback,
        recipe_levels=sorted(r.maturity_level for r in template.recipes),
    )


def _matches_search(template: SystemCatalogTemplate, needle: str) -> bool:
    if needle in template.name.lower() or needle in template.vendor.lower() or needle in template.slug:
        return True
    return any(needle in (a or "").lower() for a in (template.aliases or []))


@router.get("", response_model=List[SystemCatalogTemplateSummary])
async def list_templates(
    search: Optional[str] = Query(None, description="Search name, vendor, slug or aliases"),
    system_type: Optional[str] = Query(None, description="Filter by system type"),
    include_fallbacks: bool = Query(False, description="Include per-type generic fallback templates"),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """List global catalog templates for the add-system template picker."""
    # The global catalog is small (~50 rows); one query with recipe levels
    # only (not the bulky steps JSONB), then search filtering in Python so
    # aliases (JSONB string array) match the same way as name/vendor/slug.
    query = (
        select(SystemCatalogTemplate)
        .where(SystemCatalogTemplate.organization_id.is_(None))
        .options(
            selectinload(SystemCatalogTemplate.recipes).options(
                load_only(SystemCatalogRecipe.maturity_level, SystemCatalogRecipe.template_id)
            )
        )
        .order_by(SystemCatalogTemplate.name)
    )
    if not include_fallbacks:
        query = query.where(SystemCatalogTemplate.is_fallback.is_(False))
    if system_type:
        query = query.where(SystemCatalogTemplate.system_type == system_type)

    result = await db.execute(query)
    templates = result.scalars().all()

    if search and search.strip():
        needle = search.strip().lower()
        templates = [t for t in templates if _matches_search(t, needle)]

    return [template_summary(t) for t in templates]


@router.get("/{slug}", response_model=SystemCatalogTemplateDetail)
async def get_template(
    slug: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Get a global catalog template with its full recipes."""
    result = await db.execute(
        select(SystemCatalogTemplate)
        .where(
            SystemCatalogTemplate.slug == slug,
            SystemCatalogTemplate.organization_id.is_(None),
        )
        .options(selectinload(SystemCatalogTemplate.recipes))
    )
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Catalog template not found")

    summary = template_summary(template)
    return SystemCatalogTemplateDetail(
        **summary.model_dump(),
        aliases=template.aliases or [],
        recipes=[SystemCatalogRecipeResponse.model_validate(r) for r in template.recipes],
    )
