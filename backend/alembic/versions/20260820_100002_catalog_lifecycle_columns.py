"""Catalog-upgrade M2: lifecycle columns on catalog entity tables.

Adds status ('active'|'deprecated', default 'active'), retired_in_version and
superseded_by to the four catalog entity tables, plus an index on status.
superseded_by is a natural-key reference (scf_id / evidence_id / ao_id /
domain identifier) with deliberately NO database FK — keeps catalog upserts
order-free; validated in the apply service instead.

Behavior-neutral: every existing row defaults to 'active' and no consumer
filters on status yet. Themes/mappings are excluded (re-derived at apply).

Revision ID: catupg002
Revises: catupg001
Create Date: 2026-08-20 10:00:02.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'catupg002'
down_revision = 'catupg001'
branch_labels = None
depends_on = None


CATALOG_TABLES = (
    'scf_catalog_controls',
    'scf_catalog_evidence',
    'scf_catalog_assessment_objectives',
    'scf_catalog_domains',
)


def upgrade() -> None:
    for table in CATALOG_TABLES:
        op.add_column(table, sa.Column('status', sa.String(16), nullable=False, server_default='active'))
        op.add_column(table, sa.Column('retired_in_version', sa.String(20), nullable=True))
        # Natural-key reference to the successor entity; no FK by design.
        op.add_column(table, sa.Column('superseded_by', sa.String(30), nullable=True))
        op.create_check_constraint(
            f'ck_{table}_status', table, "status IN ('active', 'deprecated')",
        )
        op.create_index(f'ix_{table}_status', table, ['status'])


def downgrade() -> None:
    for table in reversed(CATALOG_TABLES):
        op.drop_index(f'ix_{table}_status', table_name=table)
        op.drop_constraint(f'ck_{table}_status', table, type_='check')
        op.drop_column(table, 'superseded_by')
        op.drop_column(table, 'retired_in_version')
        op.drop_column(table, 'status')
