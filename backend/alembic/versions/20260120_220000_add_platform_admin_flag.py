"""Add is_platform_admin flag to users table.

This migration adds the is_platform_admin column to the users table, which grants
cross-organisation administrative access for platform management operations.

Platform admins can:
- View and manage all users across all organisations
- View and manage all organisations
- Grant/revoke platform admin privileges to other users

This is separate from organisation-level admin roles (OrganizationMember.role='admin'),
which only grant admin access within a specific organisation.

Revision ID: d4e5f6g7h8i9
Revises: c3d4e5f6g7h8
Create Date: 2026-01-20 22:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e5f6g7h8i9'
down_revision: Union[str, None] = 'c3d4e5f6g7h8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add is_platform_admin column to users table."""
    # Add the column with default=False for existing users
    op.add_column(
        'users',
        sa.Column(
            'is_platform_admin',
            sa.Boolean(),
            nullable=False,
            server_default='false'
        )
    )

    # Create index for faster lookups when listing platform admins
    op.create_index(
        'ix_users_is_platform_admin',
        'users',
        ['is_platform_admin'],
        postgresql_where=sa.text('is_platform_admin = true')
    )


def downgrade() -> None:
    """Remove is_platform_admin column from users table."""
    op.drop_index('ix_users_is_platform_admin', table_name='users')
    op.drop_column('users', 'is_platform_admin')
