"""Capability test records, and a root cause on each deficiency.

Revision ID: pigcap001
Revises: pigcare001
Create Date: 2026-10-14 01:00:00

PIG R11 (#1131).

* ``capability_tests`` records each test of the organisation's capability:
  its type (incident response, continuity, other), the date it was run, the
  scenario, the result and the outcome. Who recorded it is kept; a deleted
  user leaves the record, unattributed.
* ``vendor_action_items`` gains ``capability_test_id``, the test a
  deficiency was found in (a test's findings are deficiencies under R8),
  and ``root_cause``, why the deficiency arose.

Nothing existing is rewritten: the new columns start empty and the table
starts empty.

Downgrade refuses while any capability test, any deficiency linked to one,
or any root cause exists, because dropping them would destroy the record.
With none present it drops only what upgrade added.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "pigcap001"
down_revision = "pigcare001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "capability_tests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("test_type", sa.String(30), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("tested_on", sa.Date(), nullable=False),
        sa.Column("scenario", sa.Text(), nullable=False),
        sa.Column("result", sa.String(30), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column(
            "recorded_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "test_type IN ('incident_response', 'continuity', 'other')",
            name="ck_capability_tests_type",
        ),
        sa.CheckConstraint(
            "result IN ('passed', 'passed_with_findings', 'failed')",
            name="ck_capability_tests_result",
        ),
        sa.CheckConstraint(
            "length(btrim(scenario)) > 0 AND length(btrim(outcome)) > 0",
            name="ck_capability_tests_written",
        ),
    )
    op.create_index(
        "ix_capability_tests_org_tested_on", "capability_tests", ["organization_id", "tested_on"]
    )

    op.add_column(
        "vendor_action_items",
        sa.Column(
            "capability_test_id",
            UUID(as_uuid=True),
            sa.ForeignKey("capability_tests.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_vendor_action_items_capability_test_id", "vendor_action_items", ["capability_test_id"]
    )
    op.add_column("vendor_action_items", sa.Column("root_cause", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    tests = bind.execute(sa.text("SELECT count(*) FROM capability_tests")).scalar()
    linked = bind.execute(
        sa.text(
            "SELECT count(*) FROM vendor_action_items "
            "WHERE capability_test_id IS NOT NULL OR root_cause IS NOT NULL"
        )
    ).scalar()
    if tests or linked:
        raise RuntimeError(
            f"Cannot downgrade pigcap001: {tests} capability test(s) and {linked} "
            "deficiency(ies) with a test link or a root cause would be dropped. Export them "
            "(GET /api/organizations/{org_id}/capability-tests and /deficiencies) and "
            "remove them deliberately before downgrading; this migration will not delete them."
        )
    op.drop_column("vendor_action_items", "root_cause")
    op.drop_index("ix_vendor_action_items_capability_test_id", table_name="vendor_action_items")
    op.drop_column("vendor_action_items", "capability_test_id")
    op.drop_index("ix_capability_tests_org_tested_on", table_name="capability_tests")
    op.drop_table("capability_tests")
