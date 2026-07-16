"""Add maturity_level to evidence_tracking

The evidence collection maturity level (L0-L5) was previously frontend-only
React state and silently reset on every page reload. This adds the missing
column so the level set in the Evidence workspace persists. Nullable, no
backfill: no legacy values exist anywhere.

Revision ID: tu2v3w4x5y6z
Revises: st1u2v3w4x5y
Create Date: 2026-07-16 21:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = 'tu2v3w4x5y6z'
down_revision = 'st1u2v3w4x5y'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'evidence_tracking',
        sa.Column('maturity_level', sa.String(2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('evidence_tracking', 'maturity_level')
