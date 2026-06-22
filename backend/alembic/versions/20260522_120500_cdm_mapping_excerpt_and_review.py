"""Add CDM mapping excerpt + review fields (slice 11).

Adds four nullable columns to ``cdm_mappings`` so reviewers can run the
terminology-alignment loop end-to-end inside the per-control Knowledge
Base panel:

- ``excerpt``                    -- matched LightRAG chunk text, written
                                    by ``cdm.compute_mappings`` at
                                    mapping insert time. Populated on
                                    next compute run; existing rows
                                    remain NULL until then.
- ``review_notes``               -- free-form reviewer note.
- ``last_reviewed_at``           -- terminology-review timestamp.
- ``last_reviewed_by_user_id``   -- actor on last review; FK
                                    ``users.id`` ON DELETE SET NULL so
                                    user offboarding doesn't break the
                                    audit chain.

No backfill. Existing accepted mappings render "Never reviewed" until
their next interaction.

Revision ID: op7q8r9s0t1u
Revises: nm6o7p8q9r0s
Create Date: 2026-05-22 12:05:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'op7q8r9s0t1u'
down_revision: Union[str, None] = 'nm6o7p8q9r0s'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add slice 11 CDM mapping review/excerpt columns."""

    op.add_column('cdm_mappings', sa.Column('excerpt', sa.Text(), nullable=True))
    op.add_column('cdm_mappings', sa.Column('review_notes', sa.Text(), nullable=True))
    op.add_column(
        'cdm_mappings',
        sa.Column('last_reviewed_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'cdm_mappings',
        sa.Column('last_reviewed_by_user_id', postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        'fk_cdm_mappings_last_reviewed_by_user_id_users',
        'cdm_mappings',
        'users',
        ['last_reviewed_by_user_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    """Drop slice 11 columns in reverse order."""

    op.drop_constraint(
        'fk_cdm_mappings_last_reviewed_by_user_id_users',
        'cdm_mappings',
        type_='foreignkey',
    )
    op.drop_column('cdm_mappings', 'last_reviewed_by_user_id')
    op.drop_column('cdm_mappings', 'last_reviewed_at')
    op.drop_column('cdm_mappings', 'review_notes')
    op.drop_column('cdm_mappings', 'excerpt')
