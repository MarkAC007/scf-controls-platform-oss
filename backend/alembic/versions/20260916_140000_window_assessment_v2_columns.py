"""Window assessment verdict v2 columns (window parity with #881).

Adds to ``evidence_window_assessments`` the columns that let a window row
carry the same verdict contract as a per-file ``evidence_assessments`` row:

- ``schema_version``   — 1 for every existing row (they were produced by the
                          pre-objective window prompt), 2 from now on
- ``ao_findings``      — one advisory entry per SCF assessment objective,
                          each naming the evidence_file ids it relied on
- ``gap_count`` / ``cannot_assess_count`` — denormalised from ao_findings
- ``unassessable_reason``
- ``file_effective_dates`` — model-extracted effective date per file
- ``file_membership``  — why each file is in the window (asserted effective
                          period vs upload date) and how its text was used

Additive only. Every column has a server default, so the ALTERs need no
table rewrite beyond the metadata change and existing rows read as v1
verdicts without a backfill statement.

Revision ID: winasv2cols1
Revises: invidpcols1
Create Date: 2026-09-16 14:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "winasv2cols1"
down_revision: Union[str, None] = "invidpcols1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "evidence_window_assessments"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column(
        "schema_version", sa.Integer(), nullable=False, server_default="1",
    ))
    op.add_column(TABLE, sa.Column(
        "ao_findings", postgresql.JSONB(astext_type=sa.Text()),
        nullable=False, server_default="[]",
    ))
    op.add_column(TABLE, sa.Column(
        "gap_count", sa.Integer(), nullable=False, server_default="0",
    ))
    op.add_column(TABLE, sa.Column(
        "cannot_assess_count", sa.Integer(), nullable=False, server_default="0",
    ))
    op.add_column(TABLE, sa.Column(
        "unassessable_reason", sa.Text(), nullable=True,
    ))
    op.add_column(TABLE, sa.Column(
        "file_effective_dates", postgresql.JSONB(astext_type=sa.Text()),
        nullable=False, server_default="[]",
    ))
    op.add_column(TABLE, sa.Column(
        "file_membership", postgresql.JSONB(astext_type=sa.Text()),
        nullable=False, server_default="{}",
    ))


def downgrade() -> None:
    op.drop_column(TABLE, "file_membership")
    op.drop_column(TABLE, "file_effective_dates")
    op.drop_column(TABLE, "unassessable_reason")
    op.drop_column(TABLE, "cannot_assess_count")
    op.drop_column(TABLE, "gap_count")
    op.drop_column(TABLE, "ao_findings")
    op.drop_column(TABLE, "schema_version")
