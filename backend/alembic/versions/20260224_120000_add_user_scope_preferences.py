"""Add user_scope_preferences table for persistent per-user framework scope filters.

Revision ID: bbc2d3e4f5a6
Revises: aab1c2d3e4f5
Create Date: 2026-02-24 12:00:00.000000

Issue: #362 Audit Scope Filters
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, ARRAY

revision = 'bbc2d3e4f5a6'
down_revision = 'aab1c2d3e4f5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'user_scope_preferences',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('org_id', UUID(as_uuid=True), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('active_frameworks', ARRAY(sa.String), nullable=False, server_default='{}'),
        sa.Column('audit_mode_locked', sa.Boolean, nullable=False, server_default='false'),
        sa.Column('audit_label', sa.String, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint(
        'uq_user_scope_preferences', 'user_scope_preferences', ['user_id', 'org_id']
    )
    op.create_index(
        'idx_user_scope_preferences_user_org',
        'user_scope_preferences',
        ['user_id', 'org_id'],
    )


def downgrade() -> None:
    op.drop_index('idx_user_scope_preferences_user_org', table_name='user_scope_preferences')
    op.drop_table('user_scope_preferences')
