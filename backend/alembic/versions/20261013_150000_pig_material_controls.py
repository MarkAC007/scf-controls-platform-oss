"""Material controls and the approved procedure link.

Revision ID: pigmat001
Revises: pigtgt001
Create Date: 2026-10-13 15:00:00

PIG R6 (#1126). The playbook treats some controls as material: they cannot
leave scope or be marked not applicable without an approver.

* A control is material by default when its catalogue weighting is 10. That
  default is computed, not stored, so a catalogue update that changes a
  weighting is reflected without a backfill.
* ``scoped_controls.material_override`` replaces the default for one control
  (true or false, or NULL to follow the weighting). It always carries a
  reason, enforced by a check constraint as the basis and target overrides
  are, and records the admin who set it and when.
* ``scoped_controls.procedure_document_id`` links the control to the approved
  procedure document that describes how it is operated (step 2d). A deleted
  document unlinks rather than blocking the delete.

Every column is nullable and nothing is backfilled. Downgrade drops only
these columns, the constraint and the index.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "pigmat001"
down_revision = "pigtgt001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scoped_controls",
        sa.Column("material_override", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "scoped_controls",
        sa.Column("material_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "scoped_controls",
        sa.Column("material_set_at", sa.DateTime(timezone=False), nullable=True),
    )
    op.add_column(
        "scoped_controls",
        sa.Column(
            "material_set_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "scoped_controls",
        sa.Column(
            "procedure_document_id",
            UUID(as_uuid=True),
            sa.ForeignKey("generated_documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_scoped_controls_material_reason",
        "scoped_controls",
        "material_override IS NULL OR (material_reason IS NOT NULL "
        "AND length(btrim(material_reason)) > 0)",
    )
    # A document delete sets the link NULL; the index keeps that lookup cheap.
    op.create_index(
        "ix_scoped_controls_procedure_document_id",
        "scoped_controls",
        ["procedure_document_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_scoped_controls_procedure_document_id", table_name="scoped_controls")
    op.drop_constraint("ck_scoped_controls_material_reason", "scoped_controls", type_="check")
    op.drop_column("scoped_controls", "procedure_document_id")
    op.drop_column("scoped_controls", "material_set_by")
    op.drop_column("scoped_controls", "material_set_at")
    op.drop_column("scoped_controls", "material_reason")
    op.drop_column("scoped_controls", "material_override")
