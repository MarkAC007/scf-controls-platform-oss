"""Add System.vendor_id structural link to the TPRM Vendor entity

Adds a nullable, org-consistent FK from systems to vendors (ON DELETE SET NULL)
plus a supporting index, then performs a MATCH-ONLY backfill: existing systems
are linked to a vendor in the SAME organization whose name matches the legacy
free-text `systems.vendor` string case-insensitively. No vendors are created,
the legacy free-text column is left untouched, and nothing is deleted.

Revision ID: st1u2v3w4x5y
Revises: rs0t1u2v3w4x
Create Date: 2026-07-16 18:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'st1u2v3w4x5y'
down_revision = 'rs0t1u2v3w4x'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'systems',
        sa.Column('vendor_id', postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        'fk_systems_vendor_id',
        'systems',
        'vendors',
        ['vendor_id'],
        ['id'],
        ondelete='SET NULL',
    )
    op.create_index('ix_systems_vendor', 'systems', ['vendor_id'])

    # MATCH-ONLY backfill: link only where a vendor already exists in the same
    # organization with a case-insensitive name match to the legacy free-text
    # `vendor` string. Never creates vendors, never edits the free-text column.
    op.execute(
        """
        UPDATE systems
        SET vendor_id = v.id
        FROM vendors v
        WHERE systems.organization_id = v.organization_id
          AND systems.vendor IS NOT NULL
          AND LOWER(TRIM(systems.vendor)) = LOWER(TRIM(v.name))
        """
    )


def downgrade() -> None:
    op.drop_index('ix_systems_vendor', table_name='systems')
    op.drop_constraint('fk_systems_vendor_id', 'systems', type_='foreignkey')
    op.drop_column('systems', 'vendor_id')
