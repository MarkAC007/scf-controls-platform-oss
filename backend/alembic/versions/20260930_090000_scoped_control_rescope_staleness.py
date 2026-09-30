"""Scoped controls: re-scope staleness stamps.

Revision ID: rescope001
Revises: jevshadow001
Create Date: 2026-09-30 09:00:00

Un-scoping a control is a flag flip that deliberately keeps its
implementation_status and maturity_level. When the control is later restored
to scope those values describe a control nobody was maintaining, and nothing
in the record said so. Two nullable timestamps make it sayable:

* ``scope_restored_at`` — stamped whenever ``selected`` flips False → True
  (bulk scope re-select, individual include override, catalog migration to
  a previously un-scoped successor).
* ``assessment_recorded_at`` — stamped whenever a write changes
  ``implementation_status`` or ``maturity_level``.

``assessment_stale`` is derived (model property): in scope, has a status or
maturity, restored after the last recording. No backfill: an existing row has
no restore date, so it reads as not stale — the honest answer for history
the platform did not record. Downgrade drops both columns.
"""
from alembic import op
import sqlalchemy as sa


revision = "rescope001"
down_revision = "jevshadow001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scoped_controls",
        sa.Column("scope_restored_at", sa.DateTime(timezone=False), nullable=True),
    )
    op.add_column(
        "scoped_controls",
        sa.Column("assessment_recorded_at", sa.DateTime(timezone=False), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("scoped_controls", "assessment_recorded_at")
    op.drop_column("scoped_controls", "scope_restored_at")
