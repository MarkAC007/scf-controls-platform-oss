"""Add recipe_feedback table for collection recipe feedback tracking.

Revision ID: r9s0t1u2v3w4
Revises: q8r9s0t1u2v3
Create Date: 2026-02-09 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = 'r9s0t1u2v3w4'
down_revision = 'q8r9s0t1u2v3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'recipe_feedback',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('organization_id', UUID(as_uuid=True), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('evidence_id', sa.String(50), nullable=False),
        sa.Column('system_type', sa.String(50), nullable=False),
        sa.Column('vendor', sa.String(255), nullable=True),
        sa.Column('feedback_type', sa.String(20), nullable=False),
        sa.Column('maturity_level', sa.String(5), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=False), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by_user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
    )

    op.create_index('ix_recipe_feedback_org_id', 'recipe_feedback', ['organization_id'])
    op.create_index('ix_recipe_feedback_evidence_id', 'recipe_feedback', ['evidence_id'])


def downgrade() -> None:
    op.drop_index('ix_recipe_feedback_evidence_id')
    op.drop_index('ix_recipe_feedback_org_id')
    op.drop_table('recipe_feedback')
