"""Add settings JSONB column to organizations table.

Revision ID: s0t1u2v3w4x5
Revises: r9s0t1u2v3w4
Create Date: 2026-02-09 06:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 's0t1u2v3w4x5'
down_revision = 'r9s0t1u2v3w4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'organizations',
        sa.Column('settings', sa.JSON(), nullable=False, server_default='{}')
    )


def downgrade() -> None:
    op.drop_column('organizations', 'settings')
