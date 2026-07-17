"""Add platform_upgrade_state (applied platform-version history)

Append-only history of the platform versions that have been migrated into this
database. The latest row is the last applied platform version; the backend-side
migration guard (upgrade_guard.py) reads it to enforce the forward-only
min_upgradable_version floor before Alembic auto-migrates on startup.

Revision ID: uv3w4x5y6z7a
Revises: tu2v3w4x5y6z
Create Date: 2026-07-17 17:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = 'uv3w4x5y6z7a'
down_revision = 'tu2v3w4x5y6z'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'platform_upgrade_state',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('version', sa.String(), nullable=False),
        sa.Column(
            'applied_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table('platform_upgrade_state')
