"""Allow each team to serve one or more business functions.

Revision ID: teamfunctions2
Revises: evtaskteam1
Create Date: 2026-08-24 17:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "teamfunctions2"
down_revision: Union[str, None] = "evtaskteam1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "team_functions",
        sa.Column("team_id", UUID(as_uuid=True), nullable=False),
        sa.Column("function_id", UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["function_id"], ["functions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("team_id", "function_id", name="pk_team_functions"),
    )
    op.create_index("ix_team_functions_function_id", "team_functions", ["function_id"])
    op.execute(sa.text(
        "INSERT INTO team_functions (team_id, function_id) "
        "SELECT id, function_id FROM teams"
    ))


def downgrade() -> None:
    op.drop_index("ix_team_functions_function_id", table_name="team_functions")
    op.drop_table("team_functions")
