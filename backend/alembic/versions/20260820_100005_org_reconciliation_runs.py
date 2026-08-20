"""Catalog-upgrade M5: per-org reconciliation run ledger + rollback anchor.

organization_reconciliation_runs records each org's reconciliation to a new
catalog version: planned_actions (per-deprecation migrate|retain|retire_only
decisions), org_snapshot (pre-images of every row the run touches — the
rollback authority), actions_log, and a catalog_import_run_id staleness guard.

A partial unique index enforces one active run per org
(status in previewed|applying|rolling_back).

Also adds the FK deferred from M3: organization_catalog_state.
last_reconciliation_run_id -> organization_reconciliation_runs.id.

Revision ID: catupg005
Revises: catupg004
Create Date: 2026-08-20 10:00:05.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = 'catupg005'
down_revision = 'catupg004'
branch_labels = None
depends_on = None


STATUSES = (
    'previewed', 'applying', 'applied', 'failed',
    'rolling_back', 'rolled_back', 'cancelled',
)
ACTIVE_STATUSES = ('previewed', 'applying', 'rolling_back')


def upgrade() -> None:
    op.create_table(
        'organization_reconciliation_runs',
        sa.Column('id', UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('organization_id', UUID(as_uuid=True),
                  sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('from_version', sa.String(20), nullable=False),
        sa.Column('to_version', sa.String(20), nullable=False),
        # Staleness guard: which platform import run this reconciliation targets.
        sa.Column('catalog_import_run_id', UUID(as_uuid=True),
                  sa.ForeignKey('catalog_import_runs.id'), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='previewed'),
        sa.Column('diff_summary', JSONB, nullable=True),
        sa.Column('planned_actions', JSONB, nullable=True),
        # Pre-images of touched rows — the rollback authority.
        sa.Column('org_snapshot', JSONB, nullable=True),
        sa.Column('actions_log', JSONB, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=False), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=False), server_default=sa.text('now()')),
        sa.Column('completed_at', sa.DateTime(timezone=False), nullable=True),
        sa.CheckConstraint(
            "status IN ('previewed', 'applying', 'applied', 'failed', "
            "'rolling_back', 'rolled_back', 'cancelled')",
            name='ck_org_reconciliation_runs_status',
        ),
    )
    op.create_index('ix_org_reconciliation_runs_org',
                    'organization_reconciliation_runs', ['organization_id'])
    # One active run per org.
    op.create_index(
        'uq_org_reconciliation_runs_active',
        'organization_reconciliation_runs',
        ['organization_id'],
        unique=True,
        postgresql_where=sa.text("status IN ('previewed', 'applying', 'rolling_back')"),
    )
    # FK deferred from M3 (organization_catalog_state predates this table).
    op.create_foreign_key(
        'fk_org_catalog_state_last_run',
        'organization_catalog_state', 'organization_reconciliation_runs',
        ['last_reconciliation_run_id'], ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint('fk_org_catalog_state_last_run',
                       'organization_catalog_state', type_='foreignkey')
    op.drop_table('organization_reconciliation_runs')
