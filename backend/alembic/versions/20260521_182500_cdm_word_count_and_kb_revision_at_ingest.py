"""Add CDM word count and kb revision at ingest columns.

Adds two nullable metadata columns to ``cdm_documents``:
``word_count`` for persisted extraction output and
``kb_revision_at_ingest`` for a future immutable LightRAG revision pin.

Revision ID: nm6o7p8q9r0s
Revises: lm5n6o7p8q9r
Create Date: 2026-05-21 18:25:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'nm6o7p8q9r0s'
down_revision: Union[str, None] = 'lm5n6o7p8q9r'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add CDM document metadata columns for slice 3."""

    op.add_column('cdm_documents', sa.Column('word_count', sa.Integer(), nullable=True))
    op.add_column(
        'cdm_documents',
        sa.Column('kb_revision_at_ingest', sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    """Drop slice 3 CDM document metadata columns in reverse order."""

    op.drop_column('cdm_documents', 'kb_revision_at_ingest')
    op.drop_column('cdm_documents', 'word_count')
