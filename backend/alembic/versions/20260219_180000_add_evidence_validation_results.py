"""Add evidence_validation_results table for Evidence Validation Engine.

Revision ID: y7z8a9b0c1d2
Revises: x6y7z8a9b0c1
Create Date: 2026-02-19 18:00:00.000000

Issue: #218 - Evidence Validation Engine
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

# revision identifiers, used by Alembic.
revision = 'y7z8a9b0c1d2'
down_revision = 'x6y7z8a9b0c1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'evidence_validation_results',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('evidence_file_id', UUID(as_uuid=True), sa.ForeignKey('evidence_files.id', ondelete='CASCADE'), nullable=False, unique=True),
        sa.Column('organization_id', UUID(as_uuid=True), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('evidence_id', sa.String(50), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('completeness_score', sa.Numeric(5, 4), nullable=True),
        sa.Column('findings', JSONB, nullable=False, server_default='[]'),
        sa.Column('validation_source', sa.String(30), nullable=False),
        sa.Column('validated_at', sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=False),
    )

    # Indexes for dashboard queries
    op.create_index(
        'ix_evr_org_status',
        'evidence_validation_results',
        ['organization_id', 'status'],
    )
    op.create_index(
        'ix_evr_org_evidence_id',
        'evidence_validation_results',
        ['organization_id', 'evidence_id'],
    )


def downgrade() -> None:
    op.drop_index('ix_evr_org_evidence_id', table_name='evidence_validation_results')
    op.drop_index('ix_evr_org_status', table_name='evidence_validation_results')
    op.drop_table('evidence_validation_results')
