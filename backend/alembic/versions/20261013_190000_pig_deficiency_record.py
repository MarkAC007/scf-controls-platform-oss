"""Control deficiency record (POA&M).

Revision ID: pigdef001
Revises: pigrisk001
Create Date: 2026-10-13 19:00:00

PIG R8 (#1128). A vendor action item becomes an organisation-level action
item: the same owner, due date, priority and status, raised against a control
or a risk as well as against a vendor. A control deficiency is such an item
with no vendor.

* ``organization_id`` is added and backfilled from each existing item's
  vendor, then made NOT NULL, so every item is scoped to its organisation by
  its own row rather than through a join.
* ``vendor_id`` becomes nullable.
* ``scoped_control_id`` (a deleted control unlinks rather than deleting the
  record), ``scf_id`` (the control's SCF id, kept when it is deleted, so the
  deficiency keeps its domain) and ``risk_code`` say what the deficiency is
  against.
* ``source`` and ``source_ref`` record what raised an automatic one
  (a control going at risk, a composite going insufficient, a reviewer
  rejecting evidence) so it is raised once, not on every recompute.
* ``retest_note`` and ``closed_by_user_id`` record how one was closed. A check
  constraint holds that a deficiency (an item with no vendor) is completed
  only with a non-blank retest note. Vendor items close as before.

Downgrade refuses while any item has no vendor: making ``vendor_id`` NOT NULL
again would have to delete those deficiencies, and a downgrade must not
destroy records. With none present it drops only what upgrade added.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "pigdef001"
down_revision = "pigrisk001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vendor_action_items",
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.execute(
        "UPDATE vendor_action_items AS item SET organization_id = v.organization_id "
        "FROM vendors AS v WHERE v.id = item.vendor_id"
    )
    op.alter_column("vendor_action_items", "organization_id", nullable=False)
    op.alter_column("vendor_action_items", "vendor_id", nullable=True)
    op.add_column(
        "vendor_action_items",
        sa.Column(
            "scoped_control_id",
            UUID(as_uuid=True),
            sa.ForeignKey("scoped_controls.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("vendor_action_items", sa.Column("scf_id", sa.String(50), nullable=True))
    op.add_column("vendor_action_items", sa.Column("risk_code", sa.String(50), nullable=True))
    op.add_column("vendor_action_items", sa.Column("source", sa.String(30), nullable=True))
    op.add_column("vendor_action_items", sa.Column("source_ref", sa.String(100), nullable=True))
    op.add_column("vendor_action_items", sa.Column("retest_note", sa.Text(), nullable=True))
    op.add_column(
        "vendor_action_items",
        sa.Column(
            "closed_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_vendor_action_items_deficiency_retest",
        "vendor_action_items",
        "vendor_id IS NOT NULL OR status <> 'completed' "
        "OR (retest_note IS NOT NULL AND length(btrim(retest_note)) > 0)",
    )
    op.create_index(
        "ix_vendor_action_items_org_status",
        "vendor_action_items",
        ["organization_id", "status"],
    )
    op.create_index(
        "ix_vendor_action_items_scoped_control_id",
        "vendor_action_items",
        ["scoped_control_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    org_level = bind.execute(
        sa.text("SELECT count(*) FROM vendor_action_items WHERE vendor_id IS NULL")
    ).scalar()
    if org_level:
        raise RuntimeError(
            f"Cannot downgrade pigdef001: {org_level} deficiency record(s) have no "
            "vendor, and vendor_id would become NOT NULL again. Export them "
            "(GET /api/organizations/{org_id}/deficiencies) and remove them "
            "deliberately before downgrading; this migration will not delete them."
        )
    op.drop_index("ix_vendor_action_items_scoped_control_id", table_name="vendor_action_items")
    op.drop_index("ix_vendor_action_items_org_status", table_name="vendor_action_items")
    op.drop_constraint(
        "ck_vendor_action_items_deficiency_retest", "vendor_action_items", type_="check"
    )
    op.drop_column("vendor_action_items", "closed_by_user_id")
    op.drop_column("vendor_action_items", "retest_note")
    op.drop_column("vendor_action_items", "source_ref")
    op.drop_column("vendor_action_items", "source")
    op.drop_column("vendor_action_items", "risk_code")
    op.drop_column("vendor_action_items", "scf_id")
    op.drop_column("vendor_action_items", "scoped_control_id")
    op.alter_column("vendor_action_items", "vendor_id", nullable=False)
    op.drop_column("vendor_action_items", "organization_id")
