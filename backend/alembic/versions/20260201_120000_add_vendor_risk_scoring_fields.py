"""Add risk scoring fields to vendor_assessments and create vendor_reports table.

Issue #60: Adds breach_score, certification_score, cve_score, regulatory_score,
data_handling_score, likelihood, impact, final_risk_score, risk_level, and
ai_analysis columns to vendor_assessments for deterministic risk scoring.

Issue #61: Creates vendor_reports table for generated assessment reports
including markdown/JSON content, risk summaries, and audit trail.

Revision ID: l3m4n5o6p7q8
Revises: k2l3m4n5o6p7
Create Date: 2026-02-01 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

# revision identifiers, used by Alembic.
revision = 'l3m4n5o6p7q8'
down_revision = 'k2l3m4n5o6p7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # Issue #60: Add risk scoring columns to vendor_assessments
    # -------------------------------------------------------------------------
    op.add_column('vendor_assessments', sa.Column('breach_score', sa.Integer, nullable=True))
    op.add_column('vendor_assessments', sa.Column('certification_score', sa.Integer, nullable=True))
    op.add_column('vendor_assessments', sa.Column('cve_score', sa.Integer, nullable=True))
    op.add_column('vendor_assessments', sa.Column('regulatory_score', sa.Integer, nullable=True))
    op.add_column('vendor_assessments', sa.Column('data_handling_score', sa.Integer, nullable=True))
    op.add_column('vendor_assessments', sa.Column('likelihood', sa.Integer, nullable=True))
    op.add_column('vendor_assessments', sa.Column('impact', sa.Integer, nullable=True))
    op.add_column('vendor_assessments', sa.Column('final_risk_score', sa.Integer, nullable=True))
    op.add_column('vendor_assessments', sa.Column('risk_level', sa.String(20), nullable=True))
    op.add_column('vendor_assessments', sa.Column('ai_analysis', sa.Text, nullable=True))

    # -------------------------------------------------------------------------
    # Issue #61: Create vendor_reports table
    # -------------------------------------------------------------------------
    op.create_table(
        'vendor_reports',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('vendor_id', UUID(as_uuid=True), sa.ForeignKey('vendors.id', ondelete='CASCADE'), nullable=False),
        sa.Column('assessment_id', UUID(as_uuid=True), sa.ForeignKey('vendor_assessments.id', ondelete='SET NULL'), nullable=True),
        sa.Column('organization_id', UUID(as_uuid=True), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),

        # Report content
        sa.Column('report_type', sa.String(50), nullable=False, server_default='comprehensive'),
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('content_markdown', sa.Text, nullable=False),
        sa.Column('content_json', JSONB, nullable=True),

        # Risk summary
        sa.Column('risk_score', sa.Integer, nullable=True),
        sa.Column('risk_level', sa.String(20), nullable=True),
        sa.Column('recommendation', sa.String(50), nullable=True),

        # Versioning
        sa.Column('version', sa.Integer, server_default='1'),

        # Audit
        sa.Column('generated_by_user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, server_default=sa.func.now()),
    )

    # Indices for vendor_reports
    op.create_index('ix_vendor_reports_vendor_id', 'vendor_reports', ['vendor_id'])
    op.create_index('ix_vendor_reports_organization_id', 'vendor_reports', ['organization_id'])
    op.create_index(
        'ix_vendor_reports_vendor_created',
        'vendor_reports',
        ['vendor_id', sa.text('created_at DESC')]
    )


def downgrade() -> None:
    # Drop vendor_reports indices and table
    op.drop_index('ix_vendor_reports_vendor_created', table_name='vendor_reports')
    op.drop_index('ix_vendor_reports_organization_id', table_name='vendor_reports')
    op.drop_index('ix_vendor_reports_vendor_id', table_name='vendor_reports')
    op.drop_table('vendor_reports')

    # Drop risk scoring columns from vendor_assessments
    op.drop_column('vendor_assessments', 'ai_analysis')
    op.drop_column('vendor_assessments', 'risk_level')
    op.drop_column('vendor_assessments', 'final_risk_score')
    op.drop_column('vendor_assessments', 'impact')
    op.drop_column('vendor_assessments', 'likelihood')
    op.drop_column('vendor_assessments', 'data_handling_score')
    op.drop_column('vendor_assessments', 'regulatory_score')
    op.drop_column('vendor_assessments', 'cve_score')
    op.drop_column('vendor_assessments', 'certification_score')
    op.drop_column('vendor_assessments', 'breach_score')
