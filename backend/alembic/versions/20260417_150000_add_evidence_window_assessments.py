"""Add evidence_window_assessments table and required_artifact_types on scf_catalog_controls.

Introduces windowed multi-file AI evidence assessment. Each window assessment
scores an evidence object over a time window (derived from
EvidenceTracking.frequency via STALENESS_THRESHOLDS) as a portfolio, rather
than scoring each file in isolation. Expected artifact types are derived from
the SCF catalog by extracting required artifact types per control.

Per-file evidence_assessments remains untouched; windowed assessment runs
alongside as a richer signal consumed by the KSI evidence_quality axis when
ENABLE_WINDOW_ASSESSMENT_KSI=true.

Revision ID: ii9j0k1l2m3n
Revises: hh8i9j0k1l2m
Create Date: 2026-04-17 15:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'ii9j0k1l2m3n'
down_revision: Union[str, None] = 'hh8i9j0k1l2m'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add catalog extraction columns and evidence_window_assessments table."""

    op.add_column(
        'scf_catalog_controls',
        sa.Column(
            'required_artifact_types',
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        'scf_catalog_controls',
        sa.Column('required_artifact_types_extracted_at', sa.DateTime(timezone=False), nullable=True),
    )

    op.create_table(
        'evidence_window_assessments',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('evidence_id', sa.String(50), nullable=False),

        sa.Column('window_start', sa.DateTime(timezone=False), nullable=False),
        sa.Column('window_end', sa.DateTime(timezone=False), nullable=False),
        sa.Column('frequency_used', sa.String(20), nullable=False),

        sa.Column('file_ids', postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column('source_coverage', postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('artifact_type_coverage', postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('expected_artifact_types', postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),

        sa.Column('status', sa.String(30), nullable=False, server_default='pending'),
        sa.Column('relevance_score', sa.Numeric(5, 2), nullable=True),
        sa.Column('findings', postgresql.JSONB, nullable=False, server_default='[]'),
        sa.Column('summary', sa.Text, nullable=True),

        sa.Column('model_id', sa.String(100), nullable=True),
        sa.Column('prompt_hash', sa.String(64), nullable=True),
        sa.Column('control_context_hash', sa.String(64), nullable=True),
        sa.Column('framework_version', sa.String(50), nullable=True),
        sa.Column('window_hash', sa.String(64), nullable=True),
        sa.Column('input_token_count', sa.Integer, nullable=True),
        sa.Column('output_token_count', sa.Integer, nullable=True),
        sa.Column('cost_cents', sa.Numeric(8, 4), nullable=True),
        sa.Column('processing_time_ms', sa.Integer, nullable=True),

        sa.Column('assessment_source', sa.String(30), nullable=False, server_default='on_demand'),
        sa.Column('requested_by_user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),

        sa.Column('assessed_at', sa.DateTime(timezone=False), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=False),

        sa.UniqueConstraint(
            'organization_id', 'evidence_id', 'window_start', 'window_end',
            name='uq_evidence_window_assessments_org_ev_window',
        ),
    )

    op.create_index('ix_evidence_window_assessments_org_id', 'evidence_window_assessments', ['organization_id'])
    op.create_index('ix_evidence_window_assessments_evidence_id', 'evidence_window_assessments', ['evidence_id'])
    op.create_index('ix_evidence_window_assessments_status', 'evidence_window_assessments', ['status'])
    op.create_index(
        'ix_evidence_window_assessments_assessed_at_desc',
        'evidence_window_assessments',
        [sa.text('assessed_at DESC')],
    )


def downgrade() -> None:
    """Drop evidence_window_assessments table and catalog extraction columns."""
    op.drop_index('ix_evidence_window_assessments_assessed_at_desc', table_name='evidence_window_assessments')
    op.drop_index('ix_evidence_window_assessments_status', table_name='evidence_window_assessments')
    op.drop_index('ix_evidence_window_assessments_evidence_id', table_name='evidence_window_assessments')
    op.drop_index('ix_evidence_window_assessments_org_id', table_name='evidence_window_assessments')
    op.drop_table('evidence_window_assessments')

    op.drop_column('scf_catalog_controls', 'required_artifact_types_extracted_at')
    op.drop_column('scf_catalog_controls', 'required_artifact_types')
