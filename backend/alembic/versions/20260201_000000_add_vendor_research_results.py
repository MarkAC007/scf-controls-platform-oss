"""Add vendor_research_results table for AI-powered vendor research.

Stores per-source JSONB results (HIBP, CISA KEV, CVE/NVD, regulatory),
aggregated risk signals, and job lifecycle tracking for Issue #59.

Revision ID: k2l3m4n5o6p7
Revises: j1k2l3m4n5o6
Create Date: 2026-02-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

# revision identifiers, used by Alembic.
revision = 'k2l3m4n5o6p7'
down_revision = 'j1k2l3m4n5o6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'vendor_research_results',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('vendor_id', UUID(as_uuid=True), sa.ForeignKey('vendors.id', ondelete='CASCADE'), nullable=False),
        sa.Column('job_id', sa.String(50), unique=True, nullable=False),
        sa.Column('status', sa.String(30), nullable=False, server_default='pending'),

        # Per-source JSONB results
        sa.Column('hibp_results', JSONB, server_default='{}'),
        sa.Column('cisa_kev_results', JSONB, server_default='{}'),
        sa.Column('cve_nvd_results', JSONB, server_default='{}'),
        sa.Column('regulatory_results', JSONB, server_default='{}'),

        # Aggregated output
        sa.Column('summary', sa.Text, nullable=True),
        sa.Column('risk_indicators', JSONB, server_default='{}'),
        sa.Column('overall_risk_signal', sa.String(20), nullable=True),

        # Per-source status tracking
        sa.Column('source_statuses', JSONB, server_default='{}'),
        sa.Column('errors', JSONB, server_default='[]'),

        # Research metadata
        sa.Column('researched_domain', sa.String(500), nullable=True),
        sa.Column('triggered_by_user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),

        # Timestamps
        sa.Column('started_at', sa.DateTime, nullable=True),
        sa.Column('completed_at', sa.DateTime, nullable=True),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now()),
    )

    # Indices
    op.create_index('ix_vendor_research_results_vendor_id', 'vendor_research_results', ['vendor_id'])
    op.create_index('ix_vendor_research_results_status', 'vendor_research_results', ['status'])
    op.create_index(
        'ix_vendor_research_results_vendor_created',
        'vendor_research_results',
        ['vendor_id', sa.text('created_at DESC')]
    )


def downgrade() -> None:
    op.drop_index('ix_vendor_research_results_vendor_created', table_name='vendor_research_results')
    op.drop_index('ix_vendor_research_results_status', table_name='vendor_research_results')
    op.drop_index('ix_vendor_research_results_vendor_id', table_name='vendor_research_results')
    op.drop_table('vendor_research_results')
