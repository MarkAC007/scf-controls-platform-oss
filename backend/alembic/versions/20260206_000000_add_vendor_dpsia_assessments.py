"""Add vendor_dpsia_assessments table for DPSIA Lambda integration.

Revision ID: p7q8r9s0t1u2
Revises: o6p7q8r9s0t1
Create Date: 2026-02-06 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = 'p7q8r9s0t1u2'
down_revision = 'o6p7q8r9s0t1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'vendor_dpsia_assessments',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('vendor_id', UUID(as_uuid=True), sa.ForeignKey('vendors.id', ondelete='CASCADE'), nullable=False),
        sa.Column('organization_id', UUID(as_uuid=True), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('job_id', sa.String(50), unique=True, nullable=False),
        sa.Column('status', sa.String(30), nullable=False, server_default='pending'),
        sa.Column('assessment_type', sa.String(30), nullable=False, server_default='new'),
        sa.Column('data_role', sa.String(30), nullable=False, server_default='Processor'),
        sa.Column('services_used', sa.Text, nullable=True),
        sa.Column('client_name', sa.String(255), nullable=True),
        sa.Column('additional_context', sa.Text, nullable=True),

        # DPSIA results
        sa.Column('rag_status', sa.String(10), nullable=True),
        sa.Column('recommendation', sa.String(30), nullable=True),
        sa.Column('risk_score', sa.Integer, nullable=True),
        sa.Column('risk_level', sa.String(20), nullable=True),
        sa.Column('executive_summary', sa.Text, nullable=True),
        sa.Column('report_markdown', sa.Text, nullable=True),
        sa.Column('report_json', JSONB, nullable=True),
        sa.Column('report_docx_s3_key', sa.String(500), nullable=True),
        sa.Column('report_filename', sa.String(255), nullable=True),
        sa.Column('research_sources', JSONB, nullable=True),

        # Links to auto-created platform records
        sa.Column('linked_assessment_id', UUID(as_uuid=True), sa.ForeignKey('vendor_assessments.id', ondelete='SET NULL'), nullable=True),
        sa.Column('linked_report_id', UUID(as_uuid=True), sa.ForeignKey('vendor_reports.id', ondelete='SET NULL'), nullable=True),

        # Metadata
        sa.Column('processing_time_ms', sa.Integer, nullable=True),
        sa.Column('error_message', sa.Text, nullable=True),
        sa.Column('triggered_by_user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=False), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=False), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=False), server_default=sa.text('now()'), nullable=False),
    )

    op.create_index('ix_vendor_dpsia_assessments_vendor_id', 'vendor_dpsia_assessments', ['vendor_id'])
    op.create_index('ix_vendor_dpsia_assessments_organization_id', 'vendor_dpsia_assessments', ['organization_id'])
    op.create_index('ix_vendor_dpsia_assessments_status', 'vendor_dpsia_assessments', ['status'])


def downgrade() -> None:
    op.drop_index('ix_vendor_dpsia_assessments_status')
    op.drop_index('ix_vendor_dpsia_assessments_organization_id')
    op.drop_index('ix_vendor_dpsia_assessments_vendor_id')
    op.drop_table('vendor_dpsia_assessments')
