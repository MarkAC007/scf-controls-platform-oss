"""Add unique constraint on organization_members table.

This migration adds a unique constraint on the (organization_id, user_id) pair
in the organization_members table to prevent duplicate membership records.

Root cause: The 500 error on staging was caused by duplicate membership records
for the same user-organization pair. When the code used scalar_one_or_none()
to query membership, it threw MultipleResultsFound.

Fix: This constraint ensures each user can only have one membership record
per organization, making the membership relationship truly unique.

Revision ID: e5f6g7h8i9j0
Revises: d4e5f6g7h8i9
Create Date: 2026-01-23 22:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e5f6g7h8i9j0'
down_revision: Union[str, None] = 'd4e5f6g7h8i9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add unique constraint on (organization_id, user_id) in organization_members."""
    op.create_unique_constraint(
        'uq_organization_members_org_user',
        'organization_members',
        ['organization_id', 'user_id']
    )


def downgrade() -> None:
    """Remove unique constraint from organization_members."""
    op.drop_constraint(
        'uq_organization_members_org_user',
        'organization_members',
        type_='unique'
    )
