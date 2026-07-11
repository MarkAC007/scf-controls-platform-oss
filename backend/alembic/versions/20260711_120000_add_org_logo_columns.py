"""Add organization logo columns

Revision ID: pq8r9s0t1u2v
Revises: op7q8r9s0t1u
Create Date: 2026-07-11 12:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = 'pq8r9s0t1u2v'
down_revision = 'op7q8r9s0t1u'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('organizations', sa.Column('logo_data', sa.LargeBinary(), nullable=True))
    op.add_column('organizations', sa.Column('logo_content_type', sa.String(length=100), nullable=True))
    op.add_column('organizations', sa.Column('logo_filename', sa.String(length=255), nullable=True))
    op.add_column('organizations', sa.Column('logo_updated_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('organizations', 'logo_updated_at')
    op.drop_column('organizations', 'logo_filename')
    op.drop_column('organizations', 'logo_content_type')
    op.drop_column('organizations', 'logo_data')
