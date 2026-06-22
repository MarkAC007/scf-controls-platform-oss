"""Add per-window review columns to evidence_window_assessments.

M4 PR 1 (#574) — moves review_status semantics from EvidenceFile (per-file)
to EvidenceWindowAssessment (per-window). This migration is purely additive:
four new columns + one composite index on the windowed-assessment table. No
ALTER COLUMN, no drops on existing tables, no triggers. The legacy review
columns on ``evidence_files`` remain untouched (ISC-A1) — the two columns
coexist after this migration; cutover to per-window review happens in M4 PR
2 + PR 3 via ``ENABLE_PER_WINDOW_REVIEW`` feature flag.

Revision ID: kk2l3m4n5o6p
Revises: jj1k2l3m4n5o
Create Date: 2026-05-09 14:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'kk2l3m4n5o6p'
down_revision: Union[str, None] = 'jj1k2l3m4n5o'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add four review columns + composite (organization_id, review_status) index."""

    op.add_column(
        'evidence_window_assessments',
        sa.Column(
            'review_status',
            sa.String(20),
            nullable=False,
            server_default='not_reviewed',
        ),
    )
    op.add_column(
        'evidence_window_assessments',
        sa.Column(
            'reviewed_by_user_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('users.id', ondelete='SET NULL'),
            nullable=True,
        ),
    )
    op.add_column(
        'evidence_window_assessments',
        sa.Column(
            'reviewed_at',
            sa.DateTime(timezone=False),
            nullable=True,
        ),
    )
    op.add_column(
        'evidence_window_assessments',
        sa.Column(
            'review_notes',
            sa.Text,
            nullable=True,
        ),
    )

    op.create_index(
        'ix_evidence_window_assessments_org_review',
        'evidence_window_assessments',
        ['organization_id', 'review_status'],
    )


def downgrade() -> None:
    """Drop the composite index and the four review columns."""
    op.drop_index(
        'ix_evidence_window_assessments_org_review',
        table_name='evidence_window_assessments',
    )
    op.drop_column('evidence_window_assessments', 'review_notes')
    op.drop_column('evidence_window_assessments', 'reviewed_at')
    op.drop_column('evidence_window_assessments', 'reviewed_by_user_id')
    op.drop_column('evidence_window_assessments', 'review_status')
