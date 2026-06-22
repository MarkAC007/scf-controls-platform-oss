"""Add action items and compensating controls tables (DPSIA Phase 3).

Revision ID: o6p7q8r9s0t1
Revises: n5o6p7q8r9s0
Create Date: 2026-02-03 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = 'o6p7q8r9s0t1'
down_revision = 'n5o6p7q8r9s0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Action items table
    op.create_table(
        'vendor_action_items',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('vendor_id', UUID(as_uuid=True), sa.ForeignKey('vendors.id', ondelete='CASCADE'), nullable=False),
        sa.Column('assessment_id', UUID(as_uuid=True), sa.ForeignKey('vendor_assessments.id', ondelete='SET NULL'), nullable=True),
        sa.Column('report_id', UUID(as_uuid=True), sa.ForeignKey('vendor_reports.id', ondelete='SET NULL'), nullable=True),
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('priority', sa.String(20), nullable=False, server_default='medium'),
        sa.Column('status', sa.String(30), nullable=False, server_default='open'),
        sa.Column('category', sa.String(100), nullable=True),
        sa.Column('owner_name', sa.String(255), nullable=True),
        sa.Column('owner_user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('due_date', sa.Date, nullable=True),
        sa.Column('completed_date', sa.Date, nullable=True),
        sa.Column('auto_generated', sa.Boolean, server_default='false'),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index('ix_vendor_action_items_vendor_id', 'vendor_action_items', ['vendor_id'])
    op.create_index('ix_vendor_action_items_status', 'vendor_action_items', ['status'])
    op.create_index('ix_vendor_action_items_priority', 'vendor_action_items', ['priority'])

    # Compensating controls table
    op.create_table(
        'vendor_compensating_controls',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('vendor_id', UUID(as_uuid=True), sa.ForeignKey('vendors.id', ondelete='CASCADE'), nullable=False),
        sa.Column('assessment_id', UUID(as_uuid=True), sa.ForeignKey('vendor_assessments.id', ondelete='SET NULL'), nullable=True),
        sa.Column('gap_description', sa.Text, nullable=False),
        sa.Column('compensating_control', sa.Text, nullable=False),
        sa.Column('effectiveness_rating', sa.String(20), nullable=False, server_default='partial'),
        sa.Column('risk_reduction_notes', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index('ix_vendor_compensating_controls_vendor_id', 'vendor_compensating_controls', ['vendor_id'])


def downgrade() -> None:
    op.drop_index('ix_vendor_compensating_controls_vendor_id', table_name='vendor_compensating_controls')
    op.drop_table('vendor_compensating_controls')
    op.drop_index('ix_vendor_action_items_priority', table_name='vendor_action_items')
    op.drop_index('ix_vendor_action_items_status', table_name='vendor_action_items')
    op.drop_index('ix_vendor_action_items_vendor_id', table_name='vendor_action_items')
    op.drop_table('vendor_action_items')
