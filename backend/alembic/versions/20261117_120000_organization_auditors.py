"""External auditor roster: ``organization_auditors``.

Revision ID: orgaud001
Revises: engrev001
Create Date: 2026-11-17 12:00:00

An organisation can invite an external auditor by email from outside its own
email domain. The auditor is deliberately not an organisation member: this
table is the organisation's list of its auditors, and being on it grants
nothing. Access to an engagement still comes only from an ACTIVE
``engagement_auditors`` row.

Additive only: no existing row changes, and existing grants keep working.
Downgrade drops the table.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "orgaud001"
down_revision = "engrev001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "organization_auditors",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "organization_id", UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "user_id", UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("firm", sa.String(255), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="invited"),
        sa.Column("idp_user_id", sa.String(255), nullable=True),
        sa.Column(
            "invited_by_user_id", UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.func.now()),
        sa.Column("accepted_at", sa.DateTime(timezone=False), nullable=True),
        sa.Column("disabled_at", sa.DateTime(timezone=False), nullable=True),
        sa.UniqueConstraint("organization_id", "email", name="uq_organization_auditor_email"),
        sa.CheckConstraint(
            "status IN ('invited', 'active', 'disabled')",
            name="ck_organization_auditors_status",
        ),
    )
    op.create_index(
        "ix_organization_auditors_organization_id", "organization_auditors", ["organization_id"]
    )
    op.create_index("ix_organization_auditors_user_id", "organization_auditors", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_organization_auditors_user_id", table_name="organization_auditors")
    op.drop_index("ix_organization_auditors_organization_id", table_name="organization_auditors")
    op.drop_table("organization_auditors")
