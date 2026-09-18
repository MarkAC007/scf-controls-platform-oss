"""Durable individual control scope overrides.

Revision ID: scopeoverride1
Revises: winasver1
Create Date: 2026-09-17 10:00:00

Active framework selections establish the baseline scope. These columns
record the two explicit exceptions to that baseline: an individual inclusion
or an individual exclusion. Existing selected rows that are not covered by
an active framework are backfilled as inclusions; existing exclusions with a
recorded auditor-facing justification are backfilled as exclusions.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "scopeoverride1"
down_revision = "winasver1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("scoped_controls", sa.Column("scope_override", sa.String(10), nullable=True))
    op.add_column("scoped_controls", sa.Column("scope_override_reason", sa.Text(), nullable=True))
    op.add_column("scoped_controls", sa.Column("scope_override_set_at", sa.DateTime(timezone=False), nullable=True))
    op.add_column(
        "scoped_controls",
        sa.Column(
            "scope_override_set_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_scoped_controls_scope_override",
        "scoped_controls",
        "scope_override IS NULL OR scope_override IN ('include', 'exclude')",
    )
    op.create_check_constraint(
        "ck_scoped_controls_exclusion_reason",
        "scoped_controls",
        "scope_override <> 'exclude' OR length(btrim(scope_override_reason)) > 0",
    )
    op.create_index(
        "ix_scoped_controls_org_scope_override",
        "scoped_controls",
        ["organization_id", "scope_override"],
    )

    op.execute("""
        UPDATE scoped_controls AS sc
        SET scope_override = 'include',
            scope_override_reason = COALESCE(NULLIF(btrim(sc.selection_reason), ''),
                                             'Existing individual scope decision'),
            scope_override_set_at = COALESCE(sc.updated_at, sc.created_at, now()),
            scope_override_set_by = sc.updated_by_user_id
        WHERE sc.selected = true
          AND NOT EXISTS (
              SELECT 1
              FROM organization_framework_selections AS ofs
              JOIN scf_catalog_controls AS cc
                ON cc.scf_id = sc.scf_id
               AND cc.framework_mappings ? ofs.framework_id
              WHERE ofs.organization_id = sc.organization_id
                AND ofs.active = true
          )
    """)
    op.execute("""
        UPDATE scoped_controls
        SET scope_override = 'exclude',
            scope_override_reason = btrim(out_of_scope_justification),
            scope_override_set_at = COALESCE(updated_at, created_at, now()),
            scope_override_set_by = updated_by_user_id
        WHERE selected = false
          AND NULLIF(btrim(out_of_scope_justification), '') IS NOT NULL
    """)


def downgrade() -> None:
    op.drop_index("ix_scoped_controls_org_scope_override", table_name="scoped_controls")
    op.drop_constraint("ck_scoped_controls_exclusion_reason", "scoped_controls", type_="check")
    op.drop_constraint("ck_scoped_controls_scope_override", "scoped_controls", type_="check")
    op.drop_column("scoped_controls", "scope_override_set_by")
    op.drop_column("scoped_controls", "scope_override_set_at")
    op.drop_column("scoped_controls", "scope_override_reason")
    op.drop_column("scoped_controls", "scope_override")
