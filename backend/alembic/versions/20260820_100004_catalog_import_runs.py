"""Catalog-upgrade M4: platform-level catalog import run ledger.

catalog_import_runs records every platform catalog upgrade attempt: versions,
lifecycle status, object-store keys for the uploaded workbook and the
field-level diff detail (old AND new values per changed field — the diff IS
the platform revert anchor), sanity report and admin-confirmed superseded
pairings. The latest 'applied' run's to_version is the canonical catalog
version authority.

A partial unique index enforces at most ONE in-flight platform run
(status in staging|staged|applying) at any time.

Revision ID: catupg004
Revises: catupg003
Create Date: 2026-08-20 10:00:04.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = 'catupg004'
down_revision = 'catupg003'
branch_labels = None
depends_on = None


STATUSES = (
    'staging', 'staged', 'blocked', 'applying',
    'applied', 'failed', 'cancelled', 'reverted',
)
IN_FLIGHT_STATUSES = ('staging', 'staged', 'applying')


def upgrade() -> None:
    op.create_table(
        'catalog_import_runs',
        sa.Column('id', UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('from_version', sa.String(20), nullable=True),
        sa.Column('to_version', sa.String(20), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='staging'),
        sa.Column('workbook_object_key', sa.String(512), nullable=True),
        sa.Column('diff_detail_object_key', sa.String(512), nullable=True),
        sa.Column('diff_summary', JSONB, nullable=True),
        sa.Column('sanity_report', JSONB, nullable=True),
        sa.Column('superseded_pairings', JSONB, nullable=True),
        sa.Column('started_by', UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=False), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=False), server_default=sa.text('now()')),
        sa.Column('completed_at', sa.DateTime(timezone=False), nullable=True),
        sa.CheckConstraint(
            "status IN ('staging', 'staged', 'blocked', 'applying', "
            "'applied', 'failed', 'cancelled', 'reverted')",
            name='ck_catalog_import_runs_status',
        ),
    )
    # At most one in-flight platform run: unique over a constant expression,
    # restricted to in-flight statuses.
    op.create_index(
        'uq_catalog_import_runs_in_flight',
        'catalog_import_runs',
        [sa.text('(true)')],
        unique=True,
        postgresql_where=sa.text("status IN ('staging', 'staged', 'applying')"),
    )


def downgrade() -> None:
    op.drop_table('catalog_import_runs')
