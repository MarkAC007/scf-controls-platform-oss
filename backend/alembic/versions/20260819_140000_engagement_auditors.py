"""Engagement-scoped external auditor access grants.

Increment 3 of the framework-native audit-engagement design
(docs/superpowers/specs/2026-08-19-audit-engagements-design.md).

Adds engagement_auditors: a grant of read access to a single engagement for an
external auditor. Access is enforced separately from OrganizationMember /
consultant paths so an auditor is confined to the engagement they hold an active
grant to.

Revision ID: eng3auditor01
Revises: eng1scopecmp01
Create Date: 2026-08-19 14:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = 'eng3auditor01'
down_revision = 'eng1scopecmp01'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'engagement_auditors',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('engagement_id', UUID(as_uuid=True), sa.ForeignKey('audit_engagements.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='active'),
        sa.Column('invited_by_user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('invited_at', sa.DateTime(timezone=False), server_default=sa.text('now()')),
        sa.Column('accepted_at', sa.DateTime(timezone=False), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=False), nullable=True),
    )
    op.create_index('ix_engagement_auditors_engagement_id', 'engagement_auditors', ['engagement_id'])
    op.create_index('ix_engagement_auditors_user_id', 'engagement_auditors', ['user_id'])
    op.create_unique_constraint(
        'uq_engagement_auditor',
        'engagement_auditors',
        ['engagement_id', 'user_id'],
    )


def downgrade() -> None:
    op.drop_constraint('uq_engagement_auditor', 'engagement_auditors', type_='unique')
    op.drop_index('ix_engagement_auditors_user_id', table_name='engagement_auditors')
    op.drop_index('ix_engagement_auditors_engagement_id', table_name='engagement_auditors')
    op.drop_table('engagement_auditors')
