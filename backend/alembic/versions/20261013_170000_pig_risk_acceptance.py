"""Risk acceptance and standards exceptions.

Revision ID: pigrisk001
Revises: pigmat001
Create Date: 2026-10-13 17:00:00

PIG R7 (#1127). Accepting a risk is a decision someone answers for, so it is
recorded with who approved it, when, why, and until when.

* ``risk_assessments`` gains ``accepted_by`` (an admin; a deleted user sets it
  NULL), ``accepted_at``, ``acceptance_rationale`` and
  ``acceptance_expires_on``. A check constraint holds that a recorded
  acceptance always has a non-blank rationale and an expiry.
* ``custom_risk_control_mappings`` gains ``document_id``: an exception against
  a standard is a custom risk linked to the control and to the document that
  sets the standard. A deleted document unlinks rather than blocking the
  delete; the index keeps that lookup cheap.

Every column is nullable and nothing is backfilled: a risk already marked
accepted has no recorded approval, which is exactly what the new gate counts.
Downgrade drops only these columns, the constraint and the index.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "pigrisk001"
down_revision = "pigmat001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "risk_assessments",
        sa.Column(
            "accepted_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "risk_assessments",
        sa.Column("accepted_at", sa.DateTime(timezone=False), nullable=True),
    )
    op.add_column(
        "risk_assessments",
        sa.Column("acceptance_rationale", sa.Text(), nullable=True),
    )
    op.add_column(
        "risk_assessments",
        sa.Column("acceptance_expires_on", sa.Date(), nullable=True),
    )
    op.create_check_constraint(
        "ck_risk_assessments_acceptance_record",
        "risk_assessments",
        "accepted_at IS NULL OR (acceptance_rationale IS NOT NULL "
        "AND length(btrim(acceptance_rationale)) > 0 "
        "AND acceptance_expires_on IS NOT NULL)",
    )
    op.add_column(
        "custom_risk_control_mappings",
        sa.Column(
            "document_id",
            UUID(as_uuid=True),
            sa.ForeignKey("generated_documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_custom_risk_control_mappings_document_id",
        "custom_risk_control_mappings",
        ["document_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_custom_risk_control_mappings_document_id",
        table_name="custom_risk_control_mappings",
    )
    op.drop_column("custom_risk_control_mappings", "document_id")
    op.drop_constraint(
        "ck_risk_assessments_acceptance_record", "risk_assessments", type_="check"
    )
    op.drop_column("risk_assessments", "acceptance_expires_on")
    op.drop_column("risk_assessments", "acceptance_rationale")
    op.drop_column("risk_assessments", "accepted_at")
    op.drop_column("risk_assessments", "accepted_by")
