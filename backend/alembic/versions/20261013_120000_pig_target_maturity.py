"""Per-control target maturity override.

Revision ID: pigtgt001
Revises: pigsys001
Create Date: 2026-10-13 12:00:00

PIG R5 (#1125). The playbook asks for a target maturity per domain, inherited
by the domain's controls, and lets a single control depart from its domain's
target when there is a reason to.

* The domain targets live in ``organizations.settings`` under
  ``maturity_targets`` and need no column.
* ``scoped_controls`` gains ``target_maturity_override`` (L0 to L5, or NULL to
  inherit the domain target), ``target_maturity_reason``, and who set it and
  when. An override always carries a reason, enforced by a check constraint as
  the basis override is.

Every column is nullable and nothing is backfilled: no control has had a
target set, and inventing one would assert a decision nobody made. Downgrade
drops only these columns.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "pigtgt001"
down_revision = "pigsys001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scoped_controls",
        sa.Column("target_maturity_override", sa.String(2), nullable=True),
    )
    op.add_column(
        "scoped_controls",
        sa.Column("target_maturity_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "scoped_controls",
        sa.Column("target_maturity_set_at", sa.DateTime(timezone=False), nullable=True),
    )
    op.add_column(
        "scoped_controls",
        sa.Column(
            "target_maturity_set_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_scoped_controls_target_maturity",
        "scoped_controls",
        "target_maturity_override IS NULL OR target_maturity_override IN "
        "('L0', 'L1', 'L2', 'L3', 'L4', 'L5')",
    )
    op.create_check_constraint(
        "ck_scoped_controls_target_maturity_reason",
        "scoped_controls",
        "target_maturity_override IS NULL OR (target_maturity_reason IS NOT NULL "
        "AND length(btrim(target_maturity_reason)) > 0)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_scoped_controls_target_maturity_reason", "scoped_controls", type_="check")
    op.drop_constraint("ck_scoped_controls_target_maturity", "scoped_controls", type_="check")
    op.drop_column("scoped_controls", "target_maturity_set_by")
    op.drop_column("scoped_controls", "target_maturity_set_at")
    op.drop_column("scoped_controls", "target_maturity_reason")
    op.drop_column("scoped_controls", "target_maturity_override")
