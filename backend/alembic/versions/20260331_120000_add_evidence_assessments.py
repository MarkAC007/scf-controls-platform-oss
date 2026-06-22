"""Add evidence_assessments table for AI-based content assessment.

Stores AI assessment results for evidence files, evaluating whether
uploaded evidence content satisfies mapped control requirements.
Includes full audit trail with frozen inference chain metadata.

Revision ID: gg7h8i9j0k1l
Revises: ff6g7h8i9j0k
Create Date: 2026-03-31 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'gg7h8i9j0k1l'
down_revision: Union[str, None] = 'ff6g7h8i9j0k'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create evidence_assessments table."""
    op.create_table(
        'evidence_assessments',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('evidence_file_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('evidence_files.id', ondelete='CASCADE'), nullable=False, unique=True),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('evidence_id', sa.String(50), nullable=False),

        # Assessment result
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('relevance_score', sa.Numeric(5, 2), nullable=True),
        sa.Column('findings', postgresql.JSONB, nullable=False, server_default='[]'),
        sa.Column('summary', sa.Text, nullable=True),

        # Audit trail (frozen inference chain)
        sa.Column('model_id', sa.String(100), nullable=True),
        sa.Column('prompt_hash', sa.String(64), nullable=True),
        sa.Column('control_context_hash', sa.String(64), nullable=True),
        sa.Column('framework_version', sa.String(50), nullable=True),
        sa.Column('input_token_count', sa.Integer, nullable=True),
        sa.Column('output_token_count', sa.Integer, nullable=True),
        sa.Column('cost_cents', sa.Numeric(8, 4), nullable=True),
        sa.Column('processing_time_ms', sa.Integer, nullable=True),

        # Source tracking
        sa.Column('assessment_source', sa.String(30), nullable=False, server_default='on_demand'),
        sa.Column('requested_by_user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),

        # Lifecycle
        sa.Column('assessed_at', sa.DateTime(timezone=False), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=False),
    )

    # Index for org-scoped queries
    op.create_index('ix_evidence_assessments_org_id', 'evidence_assessments', ['organization_id'])
    op.create_index('ix_evidence_assessments_evidence_id', 'evidence_assessments', ['evidence_id'])
    op.create_index('ix_evidence_assessments_status', 'evidence_assessments', ['status'])


def downgrade() -> None:
    """Drop evidence_assessments table."""
    op.drop_index('ix_evidence_assessments_status')
    op.drop_index('ix_evidence_assessments_evidence_id')
    op.drop_index('ix_evidence_assessments_org_id')
    op.drop_table('evidence_assessments')
