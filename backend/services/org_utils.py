"""
Organisation Utility Service - Shared helpers for organisation operations.

Provides:
- Unique slug generation from organisation names
- Shared across provisioning sync, consultant flows, and org creation
"""
import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from models import Organization

logger = logging.getLogger(__name__)


async def generate_unique_slug(name: str, db: AsyncSession) -> str:
    """
    Generate a unique URL-safe slug from an organisation name.

    Strips non-alphanumeric characters (except hyphens), truncates to 90 chars,
    and appends a numeric suffix if the slug already exists.

    Args:
        name: The organisation name to slugify
        db: Database session for uniqueness checks

    Returns:
        A unique slug string (max 100 chars)
    """
    base_slug = name.lower().replace(" ", "-").replace("_", "-")
    base_slug = "".join(c for c in base_slug if c.isalnum() or c == "-")[:90]

    # Remove leading/trailing hyphens and collapse doubles
    while "--" in base_slug:
        base_slug = base_slug.replace("--", "-")
    base_slug = base_slug.strip("-")

    if not base_slug:
        base_slug = "organisation"

    slug = base_slug
    counter = 1
    while True:
        result = await db.execute(
            select(Organization.id).where(Organization.slug == slug)
        )
        if not result.scalar_one_or_none():
            break
        slug = f"{base_slug}-{counter}"
        counter += 1

    return slug
