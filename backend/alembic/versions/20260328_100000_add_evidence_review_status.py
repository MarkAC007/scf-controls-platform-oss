"""Add review status fields to evidence_files table.

Revision ID: dd4e5f6a7b8c
Revises: cc3d4e5f6a7b
Create Date: 2026-03-28 10:00:00.000000

Issue: #482 Evidence Flow Restructure — Phase 2: Review/Approval Workflow
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = 'dd4e5f6a7b8c'
down_revision = 'cc3d4e5f6a7b'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('evidence_files', sa.Column(
        'review_status', sa.String(20),
        server_default='pending', nullable=False
    ))
    op.add_column('evidence_files', sa.Column(
        'reviewed_by_user_id', UUID(as_uuid=True),
        sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True
    ))
    op.add_column('evidence_files', sa.Column(
        'reviewed_at', sa.DateTime(timezone=False), nullable=True
    ))
    op.add_column('evidence_files', sa.Column(
        'review_notes', sa.Text(), nullable=True
    ))


def downgrade():
    op.drop_column('evidence_files', 'review_notes')
    op.drop_column('evidence_files', 'reviewed_at')
    op.drop_column('evidence_files', 'reviewed_by_user_id')
    op.drop_column('evidence_files', 'review_status')
