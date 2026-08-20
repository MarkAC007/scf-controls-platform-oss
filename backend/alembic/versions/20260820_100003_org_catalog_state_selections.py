"""Catalog-upgrade M3: per-org catalog state + structured framework selections.

organization_catalog_state: which catalog version each organisation is
reconciled to. A dedicated table, NOT Organization.settings JSON — generic
settings writes must not be able to corrupt upgrade eligibility.
last_reconciliation_run_id stays FK-less here; the FK to
organization_reconciliation_runs is added in M5 (catupg005) once that
table exists.

organization_framework_selections: structured record of which frameworks an
org scoped, replacing free-text selection_reason parsing. Backfill of these
rows is heuristic and happens at first reconciliation preview (with admin
confirmation), not here.

Backfill: one organization_catalog_state row per existing organisation,
reconciled_catalog_version = the current live catalog version, sampled the
way the codebase determines it today (a catalog_version row sample, see
api/database_stats.py get_catalog_version), falling back to '2026.1'.

Revision ID: catupg003
Revises: catupg002
Create Date: 2026-08-20 10:00:03.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = 'catupg003'
down_revision = 'catupg002'
branch_labels = None
depends_on = None


# Current live catalog version, the way the codebase samples it today.
CURRENT_VERSION_SUBQUERY = "(SELECT catalog_version FROM scf_catalog_controls LIMIT 1)"

# One state row per existing organisation, stamped with the live version.
STATE_BACKFILL_SQL = (
    "INSERT INTO organization_catalog_state (organization_id, reconciled_catalog_version) "
    f"SELECT id, COALESCE({CURRENT_VERSION_SUBQUERY}, '2026.1') "
    "FROM organizations"
)


def upgrade() -> None:
    op.create_table(
        'organization_catalog_state',
        sa.Column('organization_id', UUID(as_uuid=True),
                  sa.ForeignKey('organizations.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('reconciled_catalog_version', sa.String(20), nullable=False),
        sa.Column('last_reconciled_at', sa.DateTime(timezone=False), nullable=True),
        # FK added in M5 (catupg005) once organization_reconciliation_runs exists.
        sa.Column('last_reconciliation_run_id', UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=False), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=False), server_default=sa.text('now()')),
    )

    op.create_table(
        'organization_framework_selections',
        sa.Column('id', UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('organization_id', UUID(as_uuid=True),
                  sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('framework_id', sa.String(100), nullable=False),
        sa.Column('selected_at', sa.DateTime(timezone=False), server_default=sa.text('now()')),
        sa.Column('selected_by', UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('source', sa.String(20), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.CheckConstraint(
            "source IN ('bulk_scope', 'backfill', 'reconciliation')",
            name='ck_org_framework_selections_source',
        ),
        sa.UniqueConstraint('organization_id', 'framework_id',
                            name='uq_org_framework_selections_org_framework'),
    )
    op.create_index('ix_org_framework_selections_org',
                    'organization_framework_selections', ['organization_id'])

    op.execute(STATE_BACKFILL_SQL)


def downgrade() -> None:
    op.drop_index('ix_org_framework_selections_org',
                  table_name='organization_framework_selections')
    op.drop_table('organization_framework_selections')
    op.drop_table('organization_catalog_state')
