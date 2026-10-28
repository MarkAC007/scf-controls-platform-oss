"""ISMS profile: the organisation facts ISO 27001 clause documents need.

Revision ID: ismsprof001
Revises: docprof001
Create Date: 2026-10-28 14:00:00

One row per organisation holding the scope statement (clause 4.3) and five
registers: context issues (4.1), interested parties (4.2), objectives (6.2),
communication plan (7.4) and audit programme (9.2). Each is JSONB validated by
the API; nothing is backfilled, and an absent row reads as an empty profile.
Downgrade drops the table.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "ismsprof001"
down_revision = "docprof001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "isms_profiles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id", UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False, unique=True,
        ),
        sa.Column("scope", JSONB, nullable=False, server_default="{}"),
        sa.Column("context_issues", JSONB, nullable=False, server_default="[]"),
        sa.Column("interested_parties", JSONB, nullable=False, server_default="[]"),
        sa.Column("objectives", JSONB, nullable=False, server_default="[]"),
        sa.Column("communication_plan", JSONB, nullable=False, server_default="[]"),
        sa.Column("audit_programme", JSONB, nullable=False, server_default="[]"),
        sa.Column(
            "updated_by_user_id", UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=False), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("isms_profiles")
