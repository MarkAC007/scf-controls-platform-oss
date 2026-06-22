"""Add audit middleware columns: action_source, request_id, and performance indexes.

Revision ID: aab1c2d3e4f5
Revises: z8a9b0c1d2e3
Create Date: 2026-02-22 12:00:00.000000

Issue: #343 Middleware-Level Audit Capture
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision = 'aab1c2d3e4f5'
down_revision = 'z8a9b0c1d2e3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add action_source column — tracks change origin (ui, api_key, mcp, system)
    op.add_column('audit_log', sa.Column('action_source', sa.String(20), nullable=True))

    # Add request_id column — correlates middleware + field-level records from same request
    op.add_column('audit_log', sa.Column('request_id', UUID(as_uuid=True), nullable=True))

    # Performance indexes for common query patterns
    op.create_index(
        'idx_audit_log_entity_type_timestamp',
        'audit_log',
        ['entity_type', 'changed_at'],
        unique=False,
    )
    op.create_index(
        'idx_audit_log_action_source',
        'audit_log',
        ['action_source'],
        unique=False,
    )
    op.create_index(
        'idx_audit_log_request_id',
        'audit_log',
        ['request_id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('idx_audit_log_request_id', table_name='audit_log')
    op.drop_index('idx_audit_log_action_source', table_name='audit_log')
    op.drop_index('idx_audit_log_entity_type_timestamp', table_name='audit_log')
    op.drop_column('audit_log', 'request_id')
    op.drop_column('audit_log', 'action_source')
