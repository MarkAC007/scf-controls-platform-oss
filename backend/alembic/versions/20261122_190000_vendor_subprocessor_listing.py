"""Vendor subprocessor listing for the public trust portal.

Revision ID: subproc001
Revises: ismsobj001
Create Date: 2026-11-22 19:00:00

Issue #1321. The trust portal publishes a subprocessor list derived from the
vendor register rather than kept by hand in a second place.

* ``vendors`` gains ``is_subprocessor`` (boolean, NOT NULL, database default
  false): the switch that publishes the vendor's name on the trust portal.
* ``vendors`` gains ``subprocessor_purpose`` and ``subprocessor_location``
  (short free text, nullable): the only text published beside the name. The
  internal ``description`` and ``data_location`` are never published.

Nothing is backfilled: every existing vendor takes the default false, so no
vendor becomes public until someone turns the switch. Downgrade drops only
these three columns.
"""
from alembic import op
import sqlalchemy as sa


revision = "subproc001"
down_revision = "ismsobj001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vendors",
        sa.Column("is_subprocessor", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("vendors", sa.Column("subprocessor_purpose", sa.String(255), nullable=True))
    op.add_column("vendors", sa.Column("subprocessor_location", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("vendors", "subprocessor_location")
    op.drop_column("vendors", "subprocessor_purpose")
    op.drop_column("vendors", "is_subprocessor")
