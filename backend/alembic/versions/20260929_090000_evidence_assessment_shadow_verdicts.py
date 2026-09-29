"""Evidence assessment: shadow verdicts from a second engine.

Revision ID: jevshadow001
Revises: fwreg002
Create Date: 2026-09-29 09:00:00

An organisation can now choose its evidence-assessment engine in Settings
(``organizations.settings ->> 'evidence_assessment_engine'``: ``llm``,
``jev_shadow`` or ``jev``). The setting itself needs no schema — it is a key
in the existing JSON column. What needs a table is shadow mode.

In shadow mode the LLM verdict stands exactly as before and Jev (TypeSafe
System One) is asked the same per-objective questions afterwards. Its answer,
and the objective-by-objective comparison with the LLM's, is written here —
one row per shadowed version, including the runs where Jev failed (``error``
set, ``status`` NULL), because a second engine's failure rate is part of what
shadow mode is measuring.

Kept out of ``evidence_assessment_versions`` deliberately: that table is the
append-only audit record of what the platform asserted about a file, and a
shadow verdict asserts nothing. Nothing else references this table, so the
downgrade is a plain drop.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "jevshadow001"
down_revision = "fwreg002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evidence_assessment_shadow_verdicts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "assessment_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("evidence_assessments.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "version_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("evidence_assessment_versions.id", ondelete="CASCADE"), nullable=True,
        ),
        sa.Column(
            "evidence_file_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("evidence_files.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "organization_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("evidence_id", sa.String(50), nullable=False),
        sa.Column("engine", sa.String(20), nullable=False, server_default="jev"),
        sa.Column("model_id", sa.String(100), nullable=True),
        sa.Column("question_set_version", sa.String(16), nullable=True),
        sa.Column("status", sa.String(20), nullable=True),
        sa.Column("relevance_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("ao_findings", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("gap_count", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("cannot_assess_count", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("low_confidence_count", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("confidence_cutoff", sa.Numeric(4, 3), nullable=True),
        sa.Column("comparison", postgresql.JSONB(), nullable=True),
        sa.Column("state_truncated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("input_token_count", sa.Integer(), nullable=True),
        sa.Column("output_token_count", sa.Integer(), nullable=True),
        sa.Column("cost_cents", sa.Numeric(8, 4), nullable=True),
        sa.Column("processing_time_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_evidence_assessment_shadow_verdicts_org_created",
        "evidence_assessment_shadow_verdicts",
        ["organization_id", "created_at"],
    )
    op.create_index(
        "ix_evidence_assessment_shadow_verdicts_file",
        "evidence_assessment_shadow_verdicts",
        ["evidence_file_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_evidence_assessment_shadow_verdicts_file",
        table_name="evidence_assessment_shadow_verdicts",
    )
    op.drop_index(
        "ix_evidence_assessment_shadow_verdicts_org_created",
        table_name="evidence_assessment_shadow_verdicts",
    )
    op.drop_table("evidence_assessment_shadow_verdicts")
