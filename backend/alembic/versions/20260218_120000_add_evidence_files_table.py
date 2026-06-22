"""Add evidence_files table for tracking uploaded evidence artifacts.

Revision ID: w5x6y7z8a9b0
Revises: v4w5x6y7z8a9
Create Date: 2026-02-18 12:00:00.000000

Issue: #325 - EvidenceFile model & API
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision = 'w5x6y7z8a9b0'
down_revision = 'v4w5x6y7z8a9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'evidence_files',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('organization_id', UUID(as_uuid=True), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('evidence_id', sa.String(50), nullable=False),
        sa.Column('filename', sa.String(255), nullable=False),
        sa.Column('s3_key', sa.String(1024), nullable=False),
        sa.Column('content_type', sa.String(100), nullable=False),
        sa.Column('file_size_bytes', sa.Integer, nullable=False),
        sa.Column('sha256_hash', sa.String(64), nullable=True),
        sa.Column('classification', sa.String(20), server_default='internal', nullable=False),
        sa.Column('uploaded_by_user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('uploaded_at', sa.DateTime(timezone=False), server_default=sa.text('now()'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=False), nullable=True),
        sa.Column('is_deleted', sa.Boolean, server_default='false', nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=False), nullable=True),
        sa.Column('deleted_by_user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
    )

    op.create_index('ix_evidence_files_org_evidence', 'evidence_files', ['organization_id', 'evidence_id'])
    op.create_index('ix_evidence_files_org', 'evidence_files', ['organization_id'])
    op.create_index('ix_evidence_files_s3_key', 'evidence_files', ['s3_key'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_evidence_files_s3_key')
    op.drop_index('ix_evidence_files_org')
    op.drop_index('ix_evidence_files_org_evidence')
    op.drop_table('evidence_files')
