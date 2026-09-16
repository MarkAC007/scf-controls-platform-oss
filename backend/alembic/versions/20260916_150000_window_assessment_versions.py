"""Append-only history and human confirmation for window assessments.

Revision ID: winasver1
Revises: winasv2cols1
Create Date: 2026-09-16 15:00:00

Mirrors ``evidence_assessment_versions`` (revision ``evassessver1``) for the
windowed assessment layer, so a window verdict gets the same assurance
plumbing the per-file verdict has had since #881:

1. ``evidence_window_assessment_versions`` — one frozen row per verdict a
   window has ever received, append-only under database triggers. The only
   permitted mutation is the one-shot review block (NULL -> set); a
   corrected verdict is a new version, never an edit.
2. On ``evidence_window_assessments``: ``current_version_id`` /
   ``version_number`` pointers, and a verdict-confirmation block
   (``review_decision`` confirmed|overridden, ``review_reason``,
   ``verdict_reviewed_by_user_id``, ``verdict_reviewed_at``). These sit
   beside, and do not replace, the existing acceptance verbs in
   ``review_status`` (approved / rejected / needs_revision): that column
   says what the organisation decided to do with the evidence; the new
   block says whether a person has stood behind the AI's reading of it.
   The distinct ``verdict_reviewed_*`` names exist because ``reviewed_by_user_id``
   and ``reviewed_at`` are already taken by the acceptance workflow.
3. A partial index for the window review queue.
4. Backfill: every window already in a terminal status becomes version 1 of
   its own history, labelled with the ``schema_version`` the row carries
   (1 for the pre-objective prompt, 2 for the v2 contract) rather than
   dressed up as something it is not.

Additive. Downgrade drops the triggers, the index, the new columns and the
table; the backfilled history is lost with it, which is what a downgrade
means.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "winasver1"
down_revision = "winasv2cols1"
branch_labels = None
depends_on = None

TERMINAL_STATUSES = (
    "('sufficient', 'partial', 'insufficient', 'insufficient_sample', 'unassessable', 'error')"
)

REFUSE_UPDATE_FN = """
CREATE OR REPLACE FUNCTION evidence_window_assessment_versions_refuse_update()
RETURNS trigger AS $fn$
BEGIN
    -- Permitted mutation 1: the one-shot review write. Every column identical
    -- except the five review columns, on a row nobody has reviewed yet.
    IF OLD.review_decision IS NULL
       AND (to_jsonb(NEW) - 'review_decision' - 'review_reason'
                          - 'reviewed_by_user_id' - 'reviewed_at' - 'ao_overrides')
         = (to_jsonb(OLD) - 'review_decision' - 'review_reason'
                          - 'reviewed_by_user_id' - 'reviewed_at' - 'ao_overrides')
    THEN
        RETURN NEW;
    END IF;

    -- Permitted mutation 2: the ON DELETE SET NULL anonymisation from users.
    -- A row may LOSE an actor, but never gain one or swap one for another.
    IF (to_jsonb(NEW) - 'requested_by_user_id' - 'reviewed_by_user_id')
     = (to_jsonb(OLD) - 'requested_by_user_id' - 'reviewed_by_user_id')
       AND (NEW.requested_by_user_id IS NULL
            OR NEW.requested_by_user_id = OLD.requested_by_user_id)
       AND (NEW.reviewed_by_user_id IS NULL
            OR NEW.reviewed_by_user_id = OLD.reviewed_by_user_id)
    THEN
        RETURN NEW;
    END IF;

    RAISE EXCEPTION
        'evidence_window_assessment_versions is append-only: UPDATE is refused (row %). '
        'A verdict is corrected by appending a new version, and the review block '
        'may be written only once, on a version that has not been reviewed.',
        OLD.id;
END;
$fn$ LANGUAGE plpgsql;
"""

REFUSE_DELETE_FN = """
CREATE OR REPLACE FUNCTION evidence_window_assessment_versions_refuse_delete()
RETURNS trigger AS $fn$
BEGIN
    -- A cascade has already removed the parent row by the time referencing
    -- rows are collected, so the absence of a parent is what distinguishes
    -- "the thing this history belongs to is going" from "someone is deleting
    -- the record of an assessment".
    IF NOT EXISTS (SELECT 1 FROM organizations WHERE id = OLD.organization_id)
       OR NOT EXISTS (SELECT 1 FROM evidence_window_assessments WHERE id = OLD.window_assessment_id)
    THEN
        RETURN OLD;
    END IF;

    RAISE EXCEPTION
        'evidence_window_assessment_versions is append-only: DELETE is refused (row %). '
        'Window assessment history is removed only with the organization or the '
        'window assessment it belongs to.',
        OLD.id;
END;
$fn$ LANGUAGE plpgsql;
"""

REFUSE_TRUNCATE_FN = """
CREATE OR REPLACE FUNCTION evidence_window_assessment_versions_refuse_truncate()
RETURNS trigger AS $fn$
BEGIN
    RAISE EXCEPTION
        'evidence_window_assessment_versions is append-only: TRUNCATE is refused.';
END;
$fn$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    # Step 1: the history table.
    op.create_table(
        "evidence_window_assessment_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("window_assessment_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("evidence_window_assessments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("evidence_id", sa.String(50), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="2"),

        # The window this verdict was reached over, frozen with it.
        sa.Column("window_start", sa.DateTime(timezone=False), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=False), nullable=False),
        sa.Column("frequency_used", sa.String(20), nullable=False),
        sa.Column("file_ids", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("file_membership", postgresql.JSONB(), nullable=False, server_default="{}"),

        # Verdict snapshot
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("relevance_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("findings", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("ao_findings", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("gap_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cannot_assess_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("file_effective_dates", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("unassessable_reason", sa.Text(), nullable=True),

        # Frozen provenance
        sa.Column("model_id", sa.String(100), nullable=True),
        sa.Column("prompt_hash", sa.String(64), nullable=True),
        sa.Column("prompt_version", sa.String(16), nullable=True),
        sa.Column("control_context_hash", sa.String(64), nullable=True),
        sa.Column("framework_version", sa.String(50), nullable=True),
        sa.Column("window_hash", sa.String(64), nullable=True),
        sa.Column("input_token_count", sa.Integer(), nullable=True),
        sa.Column("output_token_count", sa.Integer(), nullable=True),
        sa.Column("cost_cents", sa.Numeric(8, 4), nullable=True),
        sa.Column("processing_time_ms", sa.Integer(), nullable=True),

        sa.Column("assessment_source", sa.String(30), nullable=False, server_default="on_demand"),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("assessed_at", sa.DateTime(timezone=False), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=False),

        # Review block — the ONLY mutable part of this row, and only once.
        sa.Column("review_decision", sa.String(16), nullable=True),
        sa.Column("review_reason", sa.Text(), nullable=True),
        sa.Column("reviewed_by_user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=False), nullable=True),
        sa.Column("ao_overrides", postgresql.JSONB(), nullable=True),

        sa.UniqueConstraint("window_assessment_id", "version_number",
                            name="uq_evidence_window_assessment_versions_assessment_version"),
    )
    op.create_index("ix_evidence_window_assessment_versions_org_evidence",
                    "evidence_window_assessment_versions", ["organization_id", "evidence_id"])

    # Step 2: pointers and the verdict-confirmation block on the parent.
    op.add_column("evidence_window_assessments",
                  sa.Column("current_version_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("evidence_window_assessments",
                  sa.Column("version_number", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("evidence_window_assessments",
                  sa.Column("review_decision", sa.String(16), nullable=True))
    op.add_column("evidence_window_assessments",
                  sa.Column("review_reason", sa.Text(), nullable=True))
    op.add_column("evidence_window_assessments",
                  sa.Column("verdict_reviewed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("evidence_window_assessments",
                  sa.Column("verdict_reviewed_at", sa.DateTime(timezone=False), nullable=True))
    op.create_foreign_key(
        "fk_evidence_window_assessments_current_version",
        "evidence_window_assessments", "evidence_window_assessment_versions",
        ["current_version_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_evidence_window_assessments_verdict_reviewed_by",
        "evidence_window_assessments", "users",
        ["verdict_reviewed_by_user_id"], ["id"], ondelete="SET NULL",
    )

    # Step 3: backfill — every terminal window becomes version 1 of its own
    # history, carrying whichever contract it was produced under.
    # The only interpolated value is the module-level TERMINAL_STATUSES
    # literal; no runtime or user input reaches this string.
    # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query, python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
    op.execute(f"""
        INSERT INTO evidence_window_assessment_versions (
            id, window_assessment_id, organization_id, evidence_id,
            version_number, schema_version,
            window_start, window_end, frequency_used, file_ids, file_membership,
            status, relevance_score, summary, findings, ao_findings,
            gap_count, cannot_assess_count, file_effective_dates, unassessable_reason,
            model_id, prompt_hash, prompt_version, control_context_hash,
            framework_version, window_hash, input_token_count, output_token_count,
            cost_cents, processing_time_ms,
            assessment_source, requested_by_user_id, assessed_at, created_at
        )
        SELECT
            gen_random_uuid(), ewa.id, ewa.organization_id, ewa.evidence_id,
            1, COALESCE(ewa.schema_version, 1),
            ewa.window_start, ewa.window_end, ewa.frequency_used,
            COALESCE(ewa.file_ids, '[]'::jsonb), COALESCE(ewa.file_membership, '{{}}'::jsonb),
            ewa.status, ewa.relevance_score, ewa.summary,
            COALESCE(ewa.findings, '[]'::jsonb), COALESCE(ewa.ao_findings, '[]'::jsonb),
            COALESCE(ewa.gap_count, 0), COALESCE(ewa.cannot_assess_count, 0),
            COALESCE(ewa.file_effective_dates, '[]'::jsonb), ewa.unassessable_reason,
            ewa.model_id, ewa.prompt_hash, ewa.prompt_version, ewa.control_context_hash,
            ewa.framework_version, ewa.window_hash, ewa.input_token_count, ewa.output_token_count,
            ewa.cost_cents, ewa.processing_time_ms,
            ewa.assessment_source, ewa.requested_by_user_id, ewa.assessed_at,
            COALESCE(ewa.assessed_at, ewa.created_at)
        FROM evidence_window_assessments ewa
        WHERE ewa.status IN {TERMINAL_STATUSES}
    """)
    op.execute("""
        UPDATE evidence_window_assessments AS ewa
           SET current_version_id = v.id,
               version_number = v.version_number
          FROM evidence_window_assessment_versions AS v
         WHERE v.window_assessment_id = ewa.id
           AND v.version_number = 1
    """)

    # Step 4: the window review queue's index. Partial: "awaiting a decision"
    # is a small slice of a table that grows with every window.
    op.execute("""
        CREATE INDEX ix_evidence_window_assessments_org_awaiting
            ON evidence_window_assessments (organization_id, gap_count DESC)
         WHERE review_decision IS NULL
           AND status IN ('sufficient', 'partial', 'insufficient', 'insufficient_sample', 'unassessable')
    """)

    # Step 5: append-only enforcement, in the database rather than in the
    # application that has every reason to want to edit it.
    op.execute(REFUSE_UPDATE_FN)
    op.execute(REFUSE_DELETE_FN)
    op.execute(REFUSE_TRUNCATE_FN)
    op.execute("""
        CREATE TRIGGER evidence_window_assessment_versions_no_update
        BEFORE UPDATE ON evidence_window_assessment_versions
        FOR EACH ROW EXECUTE FUNCTION evidence_window_assessment_versions_refuse_update();
    """)
    op.execute("""
        CREATE TRIGGER evidence_window_assessment_versions_no_delete
        BEFORE DELETE ON evidence_window_assessment_versions
        FOR EACH ROW EXECUTE FUNCTION evidence_window_assessment_versions_refuse_delete();
    """)
    op.execute("""
        CREATE TRIGGER evidence_window_assessment_versions_no_truncate
        BEFORE TRUNCATE ON evidence_window_assessment_versions
        FOR EACH STATEMENT EXECUTE FUNCTION evidence_window_assessment_versions_refuse_truncate();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS evidence_window_assessment_versions_no_truncate ON evidence_window_assessment_versions;")
    op.execute("DROP TRIGGER IF EXISTS evidence_window_assessment_versions_no_delete ON evidence_window_assessment_versions;")
    op.execute("DROP TRIGGER IF EXISTS evidence_window_assessment_versions_no_update ON evidence_window_assessment_versions;")
    op.execute("DROP FUNCTION IF EXISTS evidence_window_assessment_versions_refuse_truncate();")
    op.execute("DROP FUNCTION IF EXISTS evidence_window_assessment_versions_refuse_delete();")
    op.execute("DROP FUNCTION IF EXISTS evidence_window_assessment_versions_refuse_update();")
    op.execute("DROP INDEX IF EXISTS ix_evidence_window_assessments_org_awaiting;")

    op.drop_constraint("fk_evidence_window_assessments_verdict_reviewed_by",
                       "evidence_window_assessments", type_="foreignkey")
    op.drop_constraint("fk_evidence_window_assessments_current_version",
                       "evidence_window_assessments", type_="foreignkey")
    for column in (
        "verdict_reviewed_at",
        "verdict_reviewed_by_user_id",
        "review_reason",
        "review_decision",
        "version_number",
        "current_version_id",
    ):
        op.drop_column("evidence_window_assessments", column)

    op.drop_index("ix_evidence_window_assessment_versions_org_evidence",
                  table_name="evidence_window_assessment_versions")
    op.drop_table("evidence_window_assessment_versions")
