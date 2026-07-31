"""Add ingest_started_at to cdm_documents for stuck-ingest detection.

A document row that reads 'parsing' or 'indexing' tells you nothing about
whether a worker is still on it or died mid-run. Stamping the moment the
ingest task takes the row in-flight lets the list endpoint derive staleness
(started earlier than the Celery hard time limit ago == the run is dead)
without a beat schedule or write-on-read.

Revision ID: cdm5ingstart01
Revises: cdm4consol001
Create Date: 2026-07-30 14:00:00
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'cdm5ingstart01'
down_revision = 'cdm4consol001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'cdm_documents',
        sa.Column('ingest_started_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('cdm_documents', 'ingest_started_at')
