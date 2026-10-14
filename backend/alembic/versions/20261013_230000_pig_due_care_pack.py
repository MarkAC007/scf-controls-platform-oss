"""Due-care pack: control validation record and monthly posture snapshots.

Revision ID: pigcare001
Revises: pigcomp001
Create Date: 2026-10-13 23:00:00

PIG R10 (#1130).

* ``scoped_controls`` gains the last validation of each control:
  ``last_validated_at``, ``last_validated_by`` (a deleted user leaves the
  record, unattributed) and ``last_validation_note``. With the control's
  validation cadence it gives the next validation date.
* ``posture_snapshots`` holds one snapshot per organisation per month: counts
  of in-scope controls by status, obligation basis and materiality, and of
  open deficiencies. ``period`` is the first day of the month the snapshot
  was taken in; ``trigger`` says whether the monthly schedule or a person
  took it.

Nothing existing is rewritten: the new columns start empty and the table
starts empty.

Downgrade refuses while any snapshot or recorded validation exists, because
dropping them would destroy the due-care record. With none present it drops
only what upgrade added.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "pigcare001"
down_revision = "pigcomp001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("scoped_controls", sa.Column("last_validated_at", sa.DateTime(), nullable=True))
    op.add_column(
        "scoped_controls",
        sa.Column(
            "last_validated_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("scoped_controls", sa.Column("last_validation_note", sa.Text(), nullable=True))

    op.create_table(
        "posture_snapshots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column("taken_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column(
            "taken_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("counts", JSONB(), nullable=False),
        sa.UniqueConstraint("organization_id", "period", name="uq_posture_snapshots_org_period"),
        sa.CheckConstraint(
            "trigger IN ('scheduled', 'manual')", name="ck_posture_snapshots_trigger"
        ),
        sa.CheckConstraint(
            "EXTRACT(DAY FROM period) = 1", name="ck_posture_snapshots_period_month"
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    snapshots = bind.execute(sa.text("SELECT count(*) FROM posture_snapshots")).scalar()
    validations = bind.execute(
        sa.text("SELECT count(*) FROM scoped_controls WHERE last_validated_at IS NOT NULL")
    ).scalar()
    if snapshots or validations:
        raise RuntimeError(
            f"Cannot downgrade pigcare001: {snapshots} posture snapshot(s) and "
            f"{validations} recorded control validation(s) would be dropped. Export them "
            "(GET /api/organizations/{org_id}/posture-snapshots and "
            "/control-validations) and remove them deliberately before downgrading; "
            "this migration will not delete them."
        )
    op.drop_table("posture_snapshots")
    op.drop_column("scoped_controls", "last_validation_note")
    op.drop_column("scoped_controls", "last_validated_by")
    op.drop_column("scoped_controls", "last_validated_at")
