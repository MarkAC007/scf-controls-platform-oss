"""Persist prompt_version on the AI assessment tables.

Revision ID: promptversion01
Revises: orgassurance01
Create Date: 2026-08-24 00:15:00

An assessment row already carries prompt_hash — proof that *a* specific
prompt produced the verdict. It cannot answer the auditor's actual
question, which is "which release of the template was that, and which
verdicts came from the one we have since corrected?" A hash is an
identity, not a version: it is unordered, and it cannot be searched for a
range.

Both columns are nullable and are NOT backfilled. Every row written before
this migration was produced by an unknown template release; stamping the
current PROMPT_VERSION onto them would put a fact into the audit trail
that nobody established. NULL reads as "not recorded", which is true.
"""
from alembic import op
import sqlalchemy as sa

revision = 'promptversion01'
down_revision = 'orgassurance01'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'evidence_assessments',
        sa.Column('prompt_version', sa.String(length=16), nullable=True),
    )
    op.add_column(
        'evidence_window_assessments',
        sa.Column('prompt_version', sa.String(length=16), nullable=True),
    )


def downgrade():
    op.drop_column('evidence_window_assessments', 'prompt_version')
    op.drop_column('evidence_assessments', 'prompt_version')
