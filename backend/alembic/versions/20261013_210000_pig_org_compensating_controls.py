"""Organisation-level compensating controls.

Revision ID: pigcomp001
Revises: pigdef001
Create Date: 2026-10-13 21:00:00

PIG R9 (#1129). A vendor compensating control becomes an organisation-level
one: the same gap, compensating control and effectiveness, recorded against
one of the organisation's controls as well as against a vendor.

* ``organization_id`` is added and backfilled from each existing row's
  vendor, then made NOT NULL, so every row is scoped to its organisation by
  its own row rather than through a join.
* ``vendor_id`` becomes nullable.
* ``scoped_control_id`` (a deleted control unlinks rather than deleting the
  record) and ``scf_id`` (the control's SCF id, kept when it is deleted) say
  which control the compensation is for.
* ``risk_code`` names the accepted risk that justifies compensating rather
  than meeting the control. A material control needs one with an approval in
  force when the compensation is recorded.
* A check constraint holds that each row is for a vendor, a control, or both.

Downgrade refuses while any row has no vendor: making ``vendor_id`` NOT NULL
again would have to delete those records, and a downgrade must not destroy
records. With none present it drops only what upgrade added.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "pigcomp001"
down_revision = "pigdef001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vendor_compensating_controls",
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.execute(
        "UPDATE vendor_compensating_controls AS cc SET organization_id = v.organization_id "
        "FROM vendors AS v WHERE v.id = cc.vendor_id"
    )
    op.alter_column("vendor_compensating_controls", "organization_id", nullable=False)
    op.alter_column("vendor_compensating_controls", "vendor_id", nullable=True)
    op.add_column(
        "vendor_compensating_controls",
        sa.Column(
            "scoped_control_id",
            UUID(as_uuid=True),
            sa.ForeignKey("scoped_controls.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("vendor_compensating_controls", sa.Column("scf_id", sa.String(50), nullable=True))
    op.add_column("vendor_compensating_controls", sa.Column("risk_code", sa.String(50), nullable=True))
    op.create_check_constraint(
        "ck_vendor_compensating_controls_subject",
        "vendor_compensating_controls",
        "vendor_id IS NOT NULL OR scf_id IS NOT NULL",
    )
    op.create_index(
        "ix_vendor_compensating_controls_org_scf",
        "vendor_compensating_controls",
        ["organization_id", "scf_id"],
    )
    op.create_index(
        "ix_vendor_compensating_controls_scoped_control_id",
        "vendor_compensating_controls",
        ["scoped_control_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    org_level = bind.execute(
        sa.text("SELECT count(*) FROM vendor_compensating_controls WHERE vendor_id IS NULL")
    ).scalar()
    if org_level:
        raise RuntimeError(
            f"Cannot downgrade pigcomp001: {org_level} compensating control(s) have no "
            "vendor, and vendor_id would become NOT NULL again. Export them "
            "(GET /api/organizations/{org_id}/compensating-controls) and remove them "
            "deliberately before downgrading; this migration will not delete them."
        )
    op.drop_index(
        "ix_vendor_compensating_controls_scoped_control_id",
        table_name="vendor_compensating_controls",
    )
    op.drop_index(
        "ix_vendor_compensating_controls_org_scf", table_name="vendor_compensating_controls"
    )
    op.drop_constraint(
        "ck_vendor_compensating_controls_subject", "vendor_compensating_controls", type_="check"
    )
    op.drop_column("vendor_compensating_controls", "risk_code")
    op.drop_column("vendor_compensating_controls", "scf_id")
    op.drop_column("vendor_compensating_controls", "scoped_control_id")
    op.alter_column("vendor_compensating_controls", "vendor_id", nullable=False)
    op.drop_column("vendor_compensating_controls", "organization_id")
