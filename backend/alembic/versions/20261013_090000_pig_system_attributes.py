"""System and vendor attributes for the boundary and recovery record.

Revision ID: pigsys001
Revises: pigbasis001
Create Date: 2026-10-13 09:00:00

PIG R4 (#1124). The playbook asks which systems are inside the boundary, how
critical each one is, how quickly it must come back and how much data it may
lose, what data it holds and where it runs. For third parties it asks where
the data sits, which requirements flow down by contract, and who is
responsible for what.

* ``systems`` gains ``in_scope`` (true, false, or NULL for "not decided"),
  ``criticality`` (low | medium | high | critical — the vendor vocabulary),
  ``rto_hours`` and ``rpo_hours`` (non-negative), ``data_classification``
  (public | internal | confidential | restricted — the vendor vocabulary) and
  ``hosting_location`` (free text).
* ``vendors`` gains ``data_location``, ``flow_down_requirements`` and
  ``shared_responsibility_note`` (free text).

The organisation's business size and mission statement live in
``organizations.settings`` and need no column.

Every column is nullable and nothing is backfilled: no existing system has
been placed inside or outside the boundary, and guessing would assert a scope
decision nobody made. Downgrade drops only these columns.
"""
from alembic import op
import sqlalchemy as sa


revision = "pigsys001"
down_revision = "pigbasis001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("systems", sa.Column("in_scope", sa.Boolean(), nullable=True))
    op.add_column("systems", sa.Column("criticality", sa.String(20), nullable=True))
    op.add_column("systems", sa.Column("rto_hours", sa.Float(), nullable=True))
    op.add_column("systems", sa.Column("rpo_hours", sa.Float(), nullable=True))
    op.add_column("systems", sa.Column("data_classification", sa.String(50), nullable=True))
    op.add_column("systems", sa.Column("hosting_location", sa.String(255), nullable=True))
    op.create_check_constraint(
        "ck_systems_criticality",
        "systems",
        "criticality IS NULL OR criticality IN ('low', 'medium', 'high', 'critical')",
    )
    op.create_check_constraint(
        "ck_systems_data_classification",
        "systems",
        "data_classification IS NULL OR data_classification IN "
        "('public', 'internal', 'confidential', 'restricted')",
    )
    op.create_check_constraint(
        "ck_systems_recovery_objectives",
        "systems",
        "(rto_hours IS NULL OR rto_hours >= 0) AND (rpo_hours IS NULL OR rpo_hours >= 0)",
    )

    op.add_column("vendors", sa.Column("data_location", sa.String(255), nullable=True))
    op.add_column("vendors", sa.Column("flow_down_requirements", sa.Text(), nullable=True))
    op.add_column("vendors", sa.Column("shared_responsibility_note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("vendors", "shared_responsibility_note")
    op.drop_column("vendors", "flow_down_requirements")
    op.drop_column("vendors", "data_location")

    op.drop_constraint("ck_systems_recovery_objectives", "systems", type_="check")
    op.drop_constraint("ck_systems_data_classification", "systems", type_="check")
    op.drop_constraint("ck_systems_criticality", "systems", type_="check")
    op.drop_column("systems", "hosting_location")
    op.drop_column("systems", "data_classification")
    op.drop_column("systems", "rpo_hours")
    op.drop_column("systems", "rto_hours")
    op.drop_column("systems", "criticality")
    op.drop_column("systems", "in_scope")
