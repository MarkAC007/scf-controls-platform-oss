"""Invitation system remediation - add consultant org pre-creation columns.

Adds columns to support the redesigned consultant invitation flow where
consultants pre-create client organisations and then invite an admin user.

Changes:
- consultant_invites: + organization_id (FK to organizations, nullable)
- organizations: + awaiting_admin (boolean, default false)
- organizations: + created_by_consultant_id (FK to consultant_profiles, nullable)

Indexes:
- consultant_invites.organization_id for lookup on acceptance

Revision ID: h8i9j0k1l2m3
Revises: g7h8i9j0k1l2
Create Date: 2026-01-30 00:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'h8i9j0k1l2m3'
down_revision: Union[str, None] = 'g7h8i9j0k1l2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add columns for consultant org pre-creation flow."""

    # Add organization_id to consultant_invites (nullable for legacy invites)
    op.add_column(
        'consultant_invites',
        sa.Column(
            'organization_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('organizations.id', ondelete='SET NULL'),
            nullable=True,
        )
    )
    op.create_index(
        'ix_consultant_invites_organization_id',
        'consultant_invites',
        ['organization_id'],
    )

    # Add awaiting_admin flag to organizations
    op.add_column(
        'organizations',
        sa.Column(
            'awaiting_admin',
            sa.Boolean(),
            server_default=sa.text('false'),
            nullable=False,
        )
    )

    # Add created_by_consultant_id to organizations
    op.add_column(
        'organizations',
        sa.Column(
            'created_by_consultant_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('consultant_profiles.id', ondelete='SET NULL'),
            nullable=True,
        )
    )
    op.create_index(
        'ix_organizations_created_by_consultant_id',
        'organizations',
        ['created_by_consultant_id'],
    )


def downgrade() -> None:
    """Remove invitation remediation columns."""
    op.drop_index('ix_organizations_created_by_consultant_id', table_name='organizations')
    op.drop_column('organizations', 'created_by_consultant_id')
    op.drop_column('organizations', 'awaiting_admin')
    op.drop_index('ix_consultant_invites_organization_id', table_name='consultant_invites')
    op.drop_column('consultant_invites', 'organization_id')
