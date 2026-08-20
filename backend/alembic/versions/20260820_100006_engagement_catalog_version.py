"""Catalog-upgrade M6: stamp audit engagements with their catalog version.

Adds audit_engagements.catalog_version so a frozen engagement scope renders
against the catalog version it was materialised under. Backfills existing
engagements to the current live version (an approximation, noted in the
plan); going forward the version is stamped at materialisation time.

Same version-sampling approach as M3 (catupg003): a catalog_version row
sample from scf_catalog_controls, falling back to '2026.1'.

Revision ID: catupg006
Revises: catupg005
Create Date: 2026-08-20 10:00:06.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'catupg006'
down_revision = 'catupg005'
branch_labels = None
depends_on = None


CURRENT_VERSION_SUBQUERY = "(SELECT catalog_version FROM scf_catalog_controls LIMIT 1)"

ENGAGEMENT_BACKFILL_SQL = (
    "UPDATE audit_engagements "
    f"SET catalog_version = COALESCE({CURRENT_VERSION_SUBQUERY}, '2026.1') "
    "WHERE catalog_version IS NULL"
)


def upgrade() -> None:
    op.add_column('audit_engagements',
                  sa.Column('catalog_version', sa.String(20), nullable=True))
    op.execute(ENGAGEMENT_BACKFILL_SQL)


def downgrade() -> None:
    op.drop_column('audit_engagements', 'catalog_version')
