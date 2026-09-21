"""Organisational journey: an ordered path of stages an org walks.

Revision ID: orgjourney1
Revises: scopeoverride1
Create Date: 2026-09-21 15:00:00

Two tables. `org_journeys` is one row per organisation naming the journey and
who, if anyone, is walking it with them. `journey_stages` is the ordered path.

Stage content — titles, what each stage asks for, what to expect next, and the
preconditions to check — is data imported from a template, never compiled into
the product. `state` never reaches 'passed' without `attested_by_user_id`; the
check constraint below is what makes that an invariant rather than a habit.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "orgjourney1"
down_revision = "scopeoverride1"
branch_labels = None
depends_on = None


STATES = ("locked", "active", "awaiting_attestation", "passed", "passed_conditional")


def upgrade() -> None:
    op.create_table(
        "org_journeys",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("template_key", sa.String(100), nullable=True),
        sa.Column("template_version", sa.String(50), nullable=True),
        sa.Column(
            "practitioner_organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("practitioner_name", sa.String(255), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("organization_id", name="uq_org_journey_organization"),
    )

    op.create_table(
        "journey_stages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "journey_id",
            UUID(as_uuid=True),
            sa.ForeignKey("org_journeys.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("expect_next", sa.Text(), nullable=True),
        sa.Column("precondition_spec", sa.JSON(), nullable=True),
        sa.Column("state", sa.String(50), nullable=False, server_default="locked"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "attested_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("attested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attestation_note", sa.Text(), nullable=True),
        sa.Column("target_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("journey_id", "ordinal", name="uq_journey_stage_ordinal"),
        sa.UniqueConstraint("journey_id", "key", name="uq_journey_stage_key"),
    )

    op.create_index("idx_journey_stages_journey_ordinal", "journey_stages", ["journey_id", "ordinal"])

    op.create_check_constraint(
        "ck_journey_stages_state",
        "journey_stages",
        "state IN " + str(STATES),
    )

    # The invariant the whole feature rests on: a stage that claims to be
    # passed carries the signature of the person who passed it. Without this,
    # "the practitioner attests" is a convention any later code path can skip.
    op.create_check_constraint(
        "ck_journey_stages_passed_requires_attestation",
        "journey_stages",
        "state NOT IN ('passed', 'passed_conditional') "
        "OR (attested_by_user_id IS NOT NULL AND attested_at IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_journey_stages_passed_requires_attestation", "journey_stages", type_="check")
    op.drop_constraint("ck_journey_stages_state", "journey_stages", type_="check")
    op.drop_index("idx_journey_stages_journey_ordinal", table_name="journey_stages")
    op.drop_table("journey_stages")
    op.drop_table("org_journeys")
