"""Add control_assessment_composites table for M3 control-level rollup.

Aggregates EvidenceWindowAssessment rows per (organization_id, scf_id) into a
single composite status + score record. Recompute is Celery-async via the
``evidence_composite`` queue, fed by an after_commit session listener that
watches for terminal-status transitions on EvidenceWindowAssessment.

This migration is purely additive — no ALTER COLUMN, no drops on existing
tables, no triggers. M1a's ``evidence_window_assessments`` table is left
unchanged per ISC-A1 / ISC-A2 of the M3 design spec (#575).

Revision ID: jj1k2l3m4n5o
Revises: ii9j0k1l2m3n
Create Date: 2026-05-09 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'jj1k2l3m4n5o'
down_revision: Union[str, None] = 'ii9j0k1l2m3n'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the control_assessment_composites table and supporting indices."""

    op.create_table(
        'control_assessment_composites',
        sa.Column(
            'id',
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text('gen_random_uuid()'),
        ),
        sa.Column(
            'organization_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('organizations.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('scf_id', sa.String(20), nullable=False),

        sa.Column('composite_status', sa.String(30), nullable=False),
        sa.Column('composite_score', sa.Numeric(5, 2), nullable=True),

        sa.Column(
            'included_window_ids',
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            'included_evidence_ids',
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            'mandatory_gaps',
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),

        sa.Column(
            'computation_version',
            sa.Integer,
            nullable=False,
            server_default=sa.text('1'),
        ),
        sa.Column('computed_at', sa.DateTime(timezone=False), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=False),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=False),
            server_default=sa.func.now(),
            nullable=False,
        ),

        sa.UniqueConstraint(
            'organization_id', 'scf_id',
            name='uq_control_assessment_composites_org_scf',
        ),
    )

    op.create_index(
        'ix_control_assessment_composites_org_id',
        'control_assessment_composites',
        ['organization_id'],
    )
    op.create_index(
        'ix_control_assessment_composites_org_status',
        'control_assessment_composites',
        ['organization_id', 'composite_status'],
    )
    op.create_index(
        'ix_control_assessment_composites_computed_at_desc',
        'control_assessment_composites',
        [sa.text('computed_at DESC')],
    )


def downgrade() -> None:
    """Drop the control_assessment_composites table and its indices."""
    op.drop_index(
        'ix_control_assessment_composites_computed_at_desc',
        table_name='control_assessment_composites',
    )
    op.drop_index(
        'ix_control_assessment_composites_org_status',
        table_name='control_assessment_composites',
    )
    op.drop_index(
        'ix_control_assessment_composites_org_id',
        table_name='control_assessment_composites',
    )
    op.drop_table('control_assessment_composites')
