"""Obligation basis: MCR / DSR on framework selections and scoped controls.

Revision ID: pigbasis001
Revises: rescope001
Create Date: 2026-10-12 22:00:00

PIG R2 (#1122). Why a control is in scope matters as much as whether it is:
a minimum compliance requirement (MCR) is owed to a statute, regulator or
contract; a discretionary security requirement (DSR) is the organisation's own
choice. The basis is recorded where the obligation enters — the framework
selection — and derived per control from the selections that map it.

* ``organization_framework_selections`` gains ``basis`` (mcr | dsr),
  ``obligation_type`` (statutory | regulatory | contractual | discretionary)
  and ``obligation_source`` (free text: the clause, contract or regulator).
* ``scoped_controls`` gains a per-control ``basis_override`` that must carry a
  reason, plus who set it and when, and ``scope_override_approved_by`` — the
  admin who approved excluding a control whose basis is MCR.
* ``engagement_control_scope`` gains ``obligation_basis``, frozen at
  materialisation like the rest of the audit assertion.

Every column is nullable and nothing is backfilled: an existing selection has
no recorded basis, and guessing one would assert a legal obligation nobody
made. Unclassified reads as unclassified. Downgrade drops only these columns.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "pigbasis001"
down_revision = "rescope001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organization_framework_selections",
        sa.Column("basis", sa.String(10), nullable=True),
    )
    op.add_column(
        "organization_framework_selections",
        sa.Column("obligation_type", sa.String(20), nullable=True),
    )
    op.add_column(
        "organization_framework_selections",
        sa.Column("obligation_source", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        "ck_org_framework_selections_basis",
        "organization_framework_selections",
        "basis IS NULL OR basis IN ('mcr', 'dsr')",
    )
    op.create_check_constraint(
        "ck_org_framework_selections_obligation_type",
        "organization_framework_selections",
        "obligation_type IS NULL OR obligation_type IN "
        "('statutory', 'regulatory', 'contractual', 'discretionary')",
    )

    op.add_column(
        "scoped_controls",
        sa.Column("basis_override", sa.String(10), nullable=True),
    )
    op.add_column(
        "scoped_controls",
        sa.Column("basis_override_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "scoped_controls",
        sa.Column("basis_override_set_at", sa.DateTime(timezone=False), nullable=True),
    )
    op.add_column(
        "scoped_controls",
        sa.Column(
            "basis_override_set_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "scoped_controls",
        sa.Column(
            "scope_override_approved_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_scoped_controls_basis_override",
        "scoped_controls",
        "basis_override IS NULL OR basis_override IN ('mcr', 'dsr')",
    )
    op.create_check_constraint(
        "ck_scoped_controls_basis_override_reason",
        "scoped_controls",
        "basis_override IS NULL OR (basis_override_reason IS NOT NULL "
        "AND length(btrim(basis_override_reason)) > 0)",
    )

    op.add_column(
        "engagement_control_scope",
        sa.Column("obligation_basis", sa.String(10), nullable=True),
    )
    op.create_check_constraint(
        "ck_engagement_control_scope_obligation_basis",
        "engagement_control_scope",
        "obligation_basis IS NULL OR obligation_basis IN ('mcr', 'dsr')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_engagement_control_scope_obligation_basis",
        "engagement_control_scope",
        type_="check",
    )
    op.drop_column("engagement_control_scope", "obligation_basis")

    op.drop_constraint("ck_scoped_controls_basis_override_reason", "scoped_controls", type_="check")
    op.drop_constraint("ck_scoped_controls_basis_override", "scoped_controls", type_="check")
    op.drop_column("scoped_controls", "scope_override_approved_by")
    op.drop_column("scoped_controls", "basis_override_set_by")
    op.drop_column("scoped_controls", "basis_override_set_at")
    op.drop_column("scoped_controls", "basis_override_reason")
    op.drop_column("scoped_controls", "basis_override")

    op.drop_constraint(
        "ck_org_framework_selections_obligation_type",
        "organization_framework_selections",
        type_="check",
    )
    op.drop_constraint(
        "ck_org_framework_selections_basis",
        "organization_framework_selections",
        type_="check",
    )
    op.drop_column("organization_framework_selections", "obligation_source")
    op.drop_column("organization_framework_selections", "obligation_type")
    op.drop_column("organization_framework_selections", "basis")
