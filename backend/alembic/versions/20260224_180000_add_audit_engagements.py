"""Add audit_engagements and engagement_control_scope tables.

Revision ID: cc3d4e5f6a7b
Revises: bbc2d3e4f5a6
Create Date: 2026-02-24 18:00:00.000000

Issue: #370 Audit Module — Scoped Engagement Workspaces (Phase D Foundation)
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, ARRAY

revision = 'cc3d4e5f6a7b'
down_revision = 'bbc2d3e4f5a6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'audit_engagements',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('organization_id', UUID(as_uuid=True), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('frameworks', ARRAY(sa.String()), nullable=False, server_default='{}'),
        sa.Column('status', sa.String(20), nullable=False, server_default='draft'),
        sa.Column('start_date', sa.Date(), nullable=True),
        sa.Column('end_date', sa.Date(), nullable=True),
        sa.Column('created_by_user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=False), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=False), server_default=sa.text('now()')),
    )
    op.create_index('ix_audit_engagements_org_id', 'audit_engagements', ['organization_id'])
    op.create_index('ix_audit_engagements_status', 'audit_engagements', ['status'])

    op.create_table(
        'engagement_control_scope',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('engagement_id', UUID(as_uuid=True), sa.ForeignKey('audit_engagements.id', ondelete='CASCADE'), nullable=False),
        sa.Column('scoped_control_id', UUID(as_uuid=True), sa.ForeignKey('scoped_controls.id', ondelete='CASCADE'), nullable=False),
        sa.Column('added_at', sa.DateTime(timezone=False), server_default=sa.text('now()')),
    )
    op.create_index('ix_eng_control_scope_engagement_id', 'engagement_control_scope', ['engagement_id'])
    op.create_unique_constraint(
        'uq_engagement_scoped_control',
        'engagement_control_scope',
        ['engagement_id', 'scoped_control_id']
    )


def downgrade() -> None:
    op.drop_table('engagement_control_scope')
    op.drop_index('ix_audit_engagements_status', table_name='audit_engagements')
    op.drop_index('ix_audit_engagements_org_id', table_name='audit_engagements')
    op.drop_table('audit_engagements')
