"""Add vendor claim verifications table (DPSIA Phase 1).

Revision ID: m4n5o6p7q8r9
Revises: l3m4n5o6p7q8
Create Date: 2026-02-02 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = 'm4n5o6p7q8r9'
down_revision = 'l3m4n5o6p7q8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'vendor_claim_verifications',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('vendor_id', UUID(as_uuid=True), sa.ForeignKey('vendors.id', ondelete='CASCADE'), nullable=False),
        sa.Column('assessment_id', UUID(as_uuid=True), sa.ForeignKey('vendor_assessments.id', ondelete='SET NULL'), nullable=True),
        sa.Column('claim_type', sa.String(50), nullable=False),
        sa.Column('claim_description', sa.Text, nullable=False),
        sa.Column('verification_status', sa.String(30), nullable=False, server_default='unverified'),
        sa.Column('verification_source', sa.String(255), nullable=True),
        sa.Column('verification_detail', sa.Text, nullable=True),
        sa.Column('evidence_url', sa.String(500), nullable=True),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index('ix_vendor_claim_verifications_vendor_id', 'vendor_claim_verifications', ['vendor_id'])
    op.create_index('ix_vendor_claim_verifications_status', 'vendor_claim_verifications', ['verification_status'])


def downgrade() -> None:
    op.drop_index('ix_vendor_claim_verifications_status', table_name='vendor_claim_verifications')
    op.drop_index('ix_vendor_claim_verifications_vendor_id', table_name='vendor_claim_verifications')
    op.drop_table('vendor_claim_verifications')
