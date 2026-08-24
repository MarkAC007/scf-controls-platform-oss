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


#: The values ``organization_members.member_type`` accepts (#822 phase 2).
#:
#: Mirrors the ``ck_organization_members_member_type`` CHECK constraint on
#: :class:`models.OrganizationMember`. Kept in one place because three
#: endpoints validate against it -- the membership PATCH and the
#: ``accountable_owner_type`` filter on both list endpoints -- and a vocabulary
#: that drifts between them means the filter silently returns nothing for a
#: value the writer happily accepted.
#:
#: A label, never a grant. Nothing in an authorisation path reads it;
#: permissions live entirely on ``organization_members.role``.
MEMBER_TYPES: frozenset = frozenset({'internal', 'external_contractor'})


def invalid_member_type_detail(field: str = "member_type") -> str:
    """The 400 detail for a bad member-type value, named by its parameter.

    One wording for every endpoint that takes one, so the rule cannot be
    described three different ways to the same caller.
    """
    return f"Invalid {field}. Must be one of: {', '.join(sorted(MEMBER_TYPES))}"


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
