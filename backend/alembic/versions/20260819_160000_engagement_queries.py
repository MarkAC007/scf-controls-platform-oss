"""Structured auditor queries on engagement controls.

Increment 4 of the framework-native audit-engagement design
(docs/superpowers/specs/2026-08-19-audit-engagements-design.md).

Adds engagement_queries: an auditor's question/request against a control, with an
open/answered/closed lifecycle. Responses reuse the existing comments table
(commentable_type='engagement_query'), so no new response table is needed.

Revision ID: eng4query001
Revises: eng3auditor01
Create Date: 2026-08-19 16:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = 'eng4query001'
down_revision = 'eng3auditor01'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'engagement_queries',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('engagement_id', UUID(as_uuid=True), sa.ForeignKey('audit_engagements.id', ondelete='CASCADE'), nullable=False),
        sa.Column('scf_id', sa.String(50), nullable=False),
        sa.Column('raised_by_user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='open'),
        sa.Column('created_at', sa.DateTime(timezone=False), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=False), server_default=sa.text('now()')),
        sa.Column('closed_at', sa.DateTime(timezone=False), nullable=True),
    )
    op.create_index('ix_engagement_queries_engagement_id', 'engagement_queries', ['engagement_id'])
    op.create_index('ix_engagement_queries_engagement_scf', 'engagement_queries', ['engagement_id', 'scf_id'])


def downgrade() -> None:
    op.drop_index('ix_engagement_queries_engagement_scf', table_name='engagement_queries')
    op.drop_index('ix_engagement_queries_engagement_id', table_name='engagement_queries')
    op.drop_table('engagement_queries')
