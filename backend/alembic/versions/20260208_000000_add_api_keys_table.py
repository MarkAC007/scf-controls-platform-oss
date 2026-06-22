"""Add api_keys table for per-organisation user API keys.

Revision ID: q8r9s0t1u2v3
Revises: p7q8r9s0t1u2
Create Date: 2026-02-08 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = 'q8r9s0t1u2v3'
down_revision = 'p7q8r9s0t1u2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'api_keys',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('organization_id', UUID(as_uuid=True), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('key_prefix', sa.String(8), nullable=False),
        sa.Column('key_hash', sa.String(64), nullable=False),
        sa.Column('role', sa.String(50), nullable=False),
        sa.Column('is_active', sa.Boolean, nullable=False, server_default=sa.text('true')),
        sa.Column('expires_at', sa.DateTime(timezone=False), nullable=True),
        sa.Column('last_used_at', sa.DateTime(timezone=False), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=False), server_default=sa.text('now()'), nullable=False),
    )

    op.create_index('ix_api_keys_key_prefix', 'api_keys', ['key_prefix'])
    op.create_index('ix_api_keys_user_id', 'api_keys', ['user_id'])
    op.create_index('ix_api_keys_organization_id', 'api_keys', ['organization_id'])


def downgrade() -> None:
    op.drop_index('ix_api_keys_organization_id')
    op.drop_index('ix_api_keys_user_id')
    op.drop_index('ix_api_keys_key_prefix')
    op.drop_table('api_keys')
