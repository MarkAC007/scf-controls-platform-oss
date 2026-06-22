"""Add audit_log table for SOC 2 Type II compliance.

Revision ID: t1u2v3w4x5y6
Revises: s0t1u2v3w4x5
Create Date: 2026-02-09 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = 't1u2v3w4x5y6'
down_revision = 's0t1u2v3w4x5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'audit_log',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('organization_id', UUID(as_uuid=True), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('entity_type', sa.String(50), nullable=False),
        sa.Column('entity_id', UUID(as_uuid=True), nullable=False),
        sa.Column('scf_id', sa.String(20), nullable=True),
        sa.Column('action', sa.String(20), nullable=False),
        sa.Column('field_name', sa.String(100), nullable=True),
        sa.Column('old_value', sa.Text, nullable=True),
        sa.Column('new_value', sa.Text, nullable=True),
        sa.Column('changed_by_user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=False),
        sa.Column('changed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('ip_address', sa.String(45), nullable=True),
        sa.Column('user_agent', sa.Text, nullable=True),
    )

    op.create_index('idx_audit_log_org', 'audit_log', ['organization_id'])
    op.create_index('idx_audit_log_entity', 'audit_log', ['entity_type', 'entity_id'])
    op.create_index('idx_audit_log_scf_id', 'audit_log', ['scf_id'])
    op.create_index('idx_audit_log_changed_at', 'audit_log', ['changed_at'])


def downgrade() -> None:
    op.drop_index('idx_audit_log_changed_at')
    op.drop_index('idx_audit_log_scf_id')
    op.drop_index('idx_audit_log_entity')
    op.drop_index('idx_audit_log_org')
    op.drop_table('audit_log')
