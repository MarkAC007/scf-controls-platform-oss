"""Add CIA controls table and inherent risk columns (DPSIA Phase 2).

Revision ID: n5o6p7q8r9s0
Revises: m4n5o6p7q8r9
Create Date: 2026-02-02 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = 'n5o6p7q8r9s0'
down_revision = 'm4n5o6p7q8r9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add inherent risk columns to vendor_assessments
    op.add_column('vendor_assessments', sa.Column('inherent_risk_score', sa.Integer, nullable=True))
    op.add_column('vendor_assessments', sa.Column('inherent_risk_level', sa.String(20), nullable=True))
    op.add_column('vendor_assessments', sa.Column('control_effectiveness_pct', sa.Integer, nullable=True))

    # Create CIA controls table
    op.create_table(
        'vendor_cia_controls',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('assessment_id', UUID(as_uuid=True), sa.ForeignKey('vendor_assessments.id', ondelete='CASCADE'), nullable=False),
        sa.Column('pillar', sa.String(20), nullable=False),
        sa.Column('control_name', sa.String(255), nullable=False),
        sa.Column('control_category', sa.String(100), nullable=True),
        sa.Column('score', sa.Integer, nullable=True),
        sa.Column('detail', sa.Text, nullable=True),
        sa.Column('evidence', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index('ix_vendor_cia_controls_assessment_id', 'vendor_cia_controls', ['assessment_id'])
    op.create_index('ix_vendor_cia_controls_pillar', 'vendor_cia_controls', ['pillar'])


def downgrade() -> None:
    op.drop_index('ix_vendor_cia_controls_pillar', table_name='vendor_cia_controls')
    op.drop_index('ix_vendor_cia_controls_assessment_id', table_name='vendor_cia_controls')
    op.drop_table('vendor_cia_controls')
    op.drop_column('vendor_assessments', 'control_effectiveness_pct')
    op.drop_column('vendor_assessments', 'inherent_risk_level')
    op.drop_column('vendor_assessments', 'inherent_risk_score')
