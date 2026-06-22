"""Change evidence_files review_status default from 'pending' to 'not_reviewed'.

Existing 'pending' records are migrated to 'not_reviewed' since no human
reviewer has acted on them — they were simply the old default.

Revision ID: hh8i9j0k1l2m
Revises: gg7h8i9j0k1l
Create Date: 2026-04-01 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'hh8i9j0k1l2m'
down_revision: Union[str, None] = 'gg7h8i9j0k1l'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Change the server default
    op.alter_column(
        'evidence_files',
        'review_status',
        server_default='not_reviewed',
    )
    # Migrate existing 'pending' rows (these are unreviewed, not human-reviewed)
    op.execute(
        "UPDATE evidence_files SET review_status = 'not_reviewed' WHERE review_status = 'pending'"
    )


def downgrade() -> None:
    op.alter_column(
        'evidence_files',
        'review_status',
        server_default='pending',
    )
    op.execute(
        "UPDATE evidence_files SET review_status = 'pending' WHERE review_status = 'not_reviewed'"
    )
