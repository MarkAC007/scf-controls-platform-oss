"""Append-only version history for AI evidence assessments (#881).

Revision ID: evassessver1
Revises: notiforg1
Create Date: 2026-09-01 15:00:00

``evidence_assessments`` holds one row per evidence file, and the assessment
task rewrites that row in place on every run. So each re-assessment destroyed
the verdict before it *and* the inference chain that produced it — model id,
prompt hash, prompt version, control context hash, the findings a reviewer may
already have acted on. A compliance record that is overwritten every time the
pipeline runs cannot answer "what did the platform tell us in July, and on what
basis" — which is the only question an auditor asks about it.

``evidence_assessment_versions`` is that record. One row per verdict, frozen,
never edited. ``evidence_assessments`` keeps its shape and becomes two things:
a pointer at the current version, and a denormalized read cache so list views,
the review queue and the quality-axis SQL do not have to join history.

**The review block is the one permitted mutation.** A human confirming or
overriding an AI verdict is not producing a new verdict — the AI's answer is
unchanged, and freezing the human decision into a separate row would leave the
version everyone reads looking unreviewed. So five columns
(``review_decision``, ``review_reason``, ``reviewed_by_user_id``,
``reviewed_at``, ``ao_overrides``) may be written exactly once, NULL -> set, on
a row whose ``review_decision`` is still NULL. Everything else, including a
second opinion on an already-reviewed version, is refused. A corrected verdict
is a new version; a corrected *review* is a re-assessment.

**The DELETE exceptions are load-bearing cascades, not loopholes.** This table
hangs off three parents, all ``ON DELETE CASCADE``: organizations (tenant
removal, relied on by ``api/admin.py``, ``api/organizations.py``,
``api/provisioning.py``), evidence_files (a file being removed takes its
assessment history with it), and evidence_assessments itself. During any of
those cascades Postgres has already deleted the parent row before the
referencing rows are collected, so "the parent is gone" is exactly what
separates a legitimate cascade from someone deleting the record of an
assessment. All three are checked because the order in which Postgres fires
the two file-side cascades is not specified: if the versions cascade runs
first, the parent ``evidence_assessments`` row is still there, and a trigger
that only asked about that one would refuse a perfectly ordinary file
deletion. A bare ``DELETE FROM evidence_assessment_versions`` finds all three
parents alive and is refused.

TRUNCATE gets its own statement-level trigger for the reason it did on
audit_log: row triggers do not fire for it, so without one the other two
protections are bypassed by a single word.

**``schema_version`` labels the contract, not the quality.** Existing terminal
rows are backfilled as version 1, ``schema_version = 1``: file-level findings
only, no assessment objectives, because that is genuinely all those verdicts
contained. Marking them 2 would claim an AO-grounded basis that was never
computed. Rows still ``pending``/``processing`` get no version — there is no
verdict to freeze.

**Two Wave-1 tombstones are resolved here.**

``truncated`` was being persisted inside the findings JSONB, as an object in
the array carrying ``"truncated": true``, because the column did not exist.
The backfill reads that carrier back out (guarded on ``jsonb_typeof`` — a
malformed payload must not fail the migration) and writes the real column on
both the history row and the parent, so the JSONB stops being the source of
truth.

``assessed_file_sha256`` records which bytes a verdict was computed over, so
the task's cache gate can compare hashes rather than merely check that one
exists. It cannot be recovered for old rows — nobody wrote it down — so it is
*derived* from the file row's ``computed_sha256``/``sha256_hash``, and only for
rows that carry a ``prompt_hash`` (i.e. were actually assessed rather than
short-circuited). That derivation is sound because an evidence_files row is
immutable — a re-upload creates a new row with a new id — but it is an
inference about the past, not a recording of it, and is stated here as such.

``evidence_effective_date`` is extracted by the model from the document's own
content and is NULL whenever the model could not determine one. It never
propagates to ``EvidenceFile.effective_period_start/end``: those are preparer
assertions about the evidence and must stay human-asserted.
``age_exceeds_12_months`` is computed by the server from that date (>365 days),
and is NULL rather than False when there is no date — False would read as "this
evidence is current", which is a claim nobody made.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = 'evassessver1'
down_revision: Union[str, None] = 'notiforg1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Statuses that represent a finished verdict. 'pending' and 'processing' are
# in-flight and have nothing to freeze.
TERMINAL_STATUSES = "('sufficient', 'partial', 'insufficient', 'unassessable', 'error')"

# Read the Wave-1 truncation carrier back out of the findings payload.
# jsonb_array_elements raises on a non-array, so the type is checked first: a
# malformed row must not take the whole migration down with it.
TRUNCATED_FROM_FINDINGS = """
    CASE WHEN jsonb_typeof(COALESCE(ea.findings, '[]'::jsonb)) = 'array' THEN
        EXISTS (
            SELECT 1
              FROM jsonb_array_elements(COALESCE(ea.findings, '[]'::jsonb)) AS f
             WHERE f->>'truncated' = 'true'
        )
    ELSE false END
"""

REFUSE_UPDATE_FN = """
CREATE OR REPLACE FUNCTION evidence_assessment_versions_refuse_update()
RETURNS trigger AS $fn$
BEGIN
    -- Permitted mutation 1: the one-shot review write. Every column identical
    -- except the five review columns, on a row nobody has reviewed yet. The
    -- comparison is over to_jsonb minus those keys, so a column added later is
    -- protected without anybody remembering to come back here.
    IF OLD.review_decision IS NULL
       AND (to_jsonb(NEW) - 'review_decision' - 'review_reason'
                          - 'reviewed_by_user_id' - 'reviewed_at' - 'ao_overrides')
         = (to_jsonb(OLD) - 'review_decision' - 'review_reason'
                          - 'reviewed_by_user_id' - 'reviewed_at' - 'ao_overrides')
    THEN
        RETURN NEW;
    END IF;

    -- Permitted mutation 2: the ON DELETE SET NULL anonymisation from users.
    -- Deleting a user must not be blocked by this table, and must not destroy
    -- the verdict they requested or reviewed. A row may therefore LOSE an
    -- actor, but never gain one or swap one for another.
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
        'evidence_assessment_versions is append-only: UPDATE is refused (row %). '
        'A verdict is corrected by appending a new version, and the review block '
        'may be written only once, on a version that has not been reviewed.',
        OLD.id;
END;
$fn$ LANGUAGE plpgsql;
"""

REFUSE_DELETE_FN = """
CREATE OR REPLACE FUNCTION evidence_assessment_versions_refuse_delete()
RETURNS trigger AS $fn$
BEGIN
    -- A cascade has already removed the parent row by the time referencing
    -- rows are collected, so the absence of a parent is what distinguishes
    -- "the thing this history belongs to is going" from "someone is deleting
    -- the record of an assessment". All three parents are checked because the
    -- order of the two file-side cascades is not specified.
    IF NOT EXISTS (SELECT 1 FROM organizations WHERE id = OLD.organization_id)
       OR NOT EXISTS (SELECT 1 FROM evidence_files WHERE id = OLD.evidence_file_id)
       OR NOT EXISTS (SELECT 1 FROM evidence_assessments WHERE id = OLD.assessment_id)
    THEN
        RETURN OLD;
    END IF;

    RAISE EXCEPTION
        'evidence_assessment_versions is append-only: DELETE is refused (row %). '
        'Assessment history is removed only with the organization, evidence file '
        'or assessment it belongs to.',
        OLD.id;
END;
$fn$ LANGUAGE plpgsql;
"""

REFUSE_TRUNCATE_FN = """
CREATE OR REPLACE FUNCTION evidence_assessment_versions_refuse_truncate()
RETURNS trigger AS $fn$
BEGIN
    RAISE EXCEPTION
        'evidence_assessment_versions is append-only: TRUNCATE is refused.';
END;
$fn$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    # Step 1: the history table. The FK back from evidence_assessments is not
    # created here — the two tables reference each other, so one of the
    # constraints has to be added after both exist (step 3).
    op.create_table(
        'evidence_assessment_versions',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('assessment_id', UUID(as_uuid=True),
                  sa.ForeignKey('evidence_assessments.id', ondelete='CASCADE'), nullable=False),
        sa.Column('evidence_file_id', UUID(as_uuid=True),
                  sa.ForeignKey('evidence_files.id', ondelete='CASCADE'), nullable=False),
        sa.Column('organization_id', UUID(as_uuid=True),
                  sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('evidence_id', sa.String(50), nullable=False),

        sa.Column('version_number', sa.Integer(), nullable=False),
        sa.Column('schema_version', sa.Integer(), nullable=False, server_default='2'),

        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('relevance_score', sa.Numeric(5, 2), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('findings', JSONB(), nullable=False, server_default='[]'),
        sa.Column('ao_findings', JSONB(), nullable=False, server_default='[]'),
        sa.Column('gap_count', sa.SmallInteger(), nullable=False, server_default='0'),
        sa.Column('cannot_assess_count', sa.SmallInteger(), nullable=False, server_default='0'),

        sa.Column('evidence_effective_date', sa.Date(), nullable=True),
        sa.Column('effective_date_source', sa.Text(), nullable=True),
        sa.Column('age_exceeds_12_months', sa.Boolean(), nullable=True),
        sa.Column('truncated', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('unassessable_reason', sa.Text(), nullable=True),

        sa.Column('model_id', sa.String(100), nullable=True),
        sa.Column('prompt_hash', sa.String(64), nullable=True),
        sa.Column('prompt_version', sa.String(16), nullable=True),
        sa.Column('control_context_hash', sa.String(64), nullable=True),
        sa.Column('framework_version', sa.String(50), nullable=True),
        sa.Column('input_token_count', sa.Integer(), nullable=True),
        sa.Column('output_token_count', sa.Integer(), nullable=True),
        sa.Column('cost_cents', sa.Numeric(8, 4), nullable=True),
        sa.Column('processing_time_ms', sa.Integer(), nullable=True),
        sa.Column('assessed_file_sha256', sa.String(64), nullable=True),

        sa.Column('assessment_source', sa.String(30), nullable=False, server_default='on_demand'),
        sa.Column('requested_by_user_id', UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('assessed_at', sa.DateTime(timezone=False), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=False),
                  server_default=sa.func.now(), nullable=False),

        sa.Column('review_decision', sa.String(16), nullable=True),
        sa.Column('review_reason', sa.Text(), nullable=True),
        sa.Column('reviewed_by_user_id', UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(timezone=False), nullable=True),
        sa.Column('ao_overrides', JSONB(), nullable=True),

        # Doubles as the concurrency guard for the write protocol: two workers
        # racing to append version N both read current+1, and the loser gets an
        # integrity error rather than a duplicate history entry.
        sa.UniqueConstraint('assessment_id', 'version_number',
                            name='uq_evidence_assessment_versions_assessment_version'),
    )
    op.create_index(
        'ix_evidence_assessment_versions_org_file',
        'evidence_assessment_versions',
        ['organization_id', 'evidence_file_id'],
    )

    # Step 2: the pointer and the denormalized read cache on the parent. Every
    # NOT NULL addition carries a server_default — the table has rows.
    op.add_column('evidence_assessments',
                  sa.Column('current_version_id', UUID(as_uuid=True), nullable=True))
    op.add_column('evidence_assessments',
                  sa.Column('version_number', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('evidence_assessments',
                  sa.Column('review_decision', sa.String(16), nullable=True))
    op.add_column('evidence_assessments',
                  sa.Column('reviewed_by_user_id', UUID(as_uuid=True), nullable=True))
    op.add_column('evidence_assessments',
                  sa.Column('reviewed_at', sa.DateTime(timezone=False), nullable=True))
    op.add_column('evidence_assessments',
                  sa.Column('gap_count', sa.SmallInteger(), nullable=False, server_default='0'))
    op.add_column('evidence_assessments',
                  sa.Column('cannot_assess_count', sa.SmallInteger(), nullable=False, server_default='0'))
    op.add_column('evidence_assessments',
                  sa.Column('ao_findings', JSONB(), nullable=False, server_default='[]'))
    op.add_column('evidence_assessments',
                  sa.Column('evidence_effective_date', sa.Date(), nullable=True))
    op.add_column('evidence_assessments',
                  sa.Column('effective_date_source', sa.Text(), nullable=True))
    op.add_column('evidence_assessments',
                  sa.Column('age_exceeds_12_months', sa.Boolean(), nullable=True))
    op.add_column('evidence_assessments',
                  sa.Column('truncated', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('evidence_assessments',
                  sa.Column('assessed_file_sha256', sa.String(64), nullable=True))
    op.add_column('evidence_assessments',
                  sa.Column('unassessable_reason', sa.Text(), nullable=True))

    op.create_foreign_key(
        'fk_evidence_assessments_reviewed_by',
        'evidence_assessments', 'users',
        ['reviewed_by_user_id'], ['id'], ondelete='SET NULL',
    )
    # Step 3: the half of the cycle that needed both tables to exist.
    op.create_foreign_key(
        'fk_evidence_assessments_current_version',
        'evidence_assessments', 'evidence_assessment_versions',
        ['current_version_id'], ['id'], ondelete='SET NULL',
    )

    # Step 4: freeze every finished verdict as version 1. schema_version 1
    # says what it is — a pre-AO, file-level verdict.
    # The only interpolated values are the module-level SQL constants above
    # (TRUNCATED_FROM_FINDINGS, TERMINAL_STATUSES) — no runtime or user input
    # reaches this string, so parameterization has nothing to protect here.
    # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query, python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
    op.execute(f"""
        INSERT INTO evidence_assessment_versions (
            id, assessment_id, evidence_file_id, organization_id, evidence_id,
            version_number, schema_version,
            status, relevance_score, summary, findings, ao_findings,
            gap_count, cannot_assess_count, truncated,
            model_id, prompt_hash, prompt_version, control_context_hash,
            framework_version, input_token_count, output_token_count,
            cost_cents, processing_time_ms, assessed_file_sha256,
            assessment_source, requested_by_user_id, assessed_at, created_at
        )
        SELECT
            gen_random_uuid(), ea.id, ea.evidence_file_id, ea.organization_id, ea.evidence_id,
            1, 1,
            ea.status, ea.relevance_score, ea.summary,
            COALESCE(ea.findings, '[]'::jsonb), '[]'::jsonb,
            0, 0, {TRUNCATED_FROM_FINDINGS},
            ea.model_id, ea.prompt_hash, ea.prompt_version, ea.control_context_hash,
            ea.framework_version, ea.input_token_count, ea.output_token_count,
            ea.cost_cents, ea.processing_time_ms,
            -- Derived, not recorded: sound only because an evidence_files row
            -- is immutable, and only claimed for rows that were really assessed.
            CASE WHEN ea.prompt_hash IS NOT NULL
                 THEN COALESCE(ef.computed_sha256, ef.sha256_hash) END,
            ea.assessment_source, ea.requested_by_user_id, ea.assessed_at,
            COALESCE(ea.assessed_at, ea.created_at)
        FROM evidence_assessments ea
        JOIN evidence_files ef ON ef.id = ea.evidence_file_id
        WHERE ea.status IN {TERMINAL_STATUSES}
    """)

    # Step 5: point the parent rows at their new history and lift the two
    # columns that were previously only derivable from JSONB / a join.
    op.execute("""
        UPDATE evidence_assessments AS ea
           SET current_version_id = v.id,
               version_number = v.version_number,
               truncated = v.truncated,
               assessed_file_sha256 = v.assessed_file_sha256
          FROM evidence_assessment_versions AS v
         WHERE v.assessment_id = ea.id
           AND v.version_number = 1
    """)

    # Step 6: the review queue's index. Partial, because "awaiting review" is a
    # small slice of a table that grows with every uploaded file, and the queue
    # orders by gap_count first.
    op.execute("""
        CREATE INDEX ix_evidence_assessments_org_awaiting
            ON evidence_assessments (organization_id, gap_count DESC)
         WHERE review_decision IS NULL
           AND status IN ('sufficient', 'partial', 'insufficient')
    """)

    # Step 7: append-only enforcement, in the database rather than in the
    # application that has every reason to want to edit it.
    op.execute(REFUSE_UPDATE_FN)
    op.execute(REFUSE_DELETE_FN)
    op.execute(REFUSE_TRUNCATE_FN)

    op.execute("""
        CREATE TRIGGER evidence_assessment_versions_no_update
        BEFORE UPDATE ON evidence_assessment_versions
        FOR EACH ROW EXECUTE FUNCTION evidence_assessment_versions_refuse_update();
    """)
    op.execute("""
        CREATE TRIGGER evidence_assessment_versions_no_delete
        BEFORE DELETE ON evidence_assessment_versions
        FOR EACH ROW EXECUTE FUNCTION evidence_assessment_versions_refuse_delete();
    """)
    op.execute("""
        CREATE TRIGGER evidence_assessment_versions_no_truncate
        BEFORE TRUNCATE ON evidence_assessment_versions
        FOR EACH STATEMENT EXECUTE FUNCTION evidence_assessment_versions_refuse_truncate();
    """)


def downgrade() -> None:
    # Triggers first: the DELETE trigger would otherwise refuse the drop's own
    # cleanup, and dropping the table with them attached leaves the functions
    # orphaned.
    op.execute("DROP TRIGGER IF EXISTS evidence_assessment_versions_no_truncate ON evidence_assessment_versions;")
    op.execute("DROP TRIGGER IF EXISTS evidence_assessment_versions_no_delete ON evidence_assessment_versions;")
    op.execute("DROP TRIGGER IF EXISTS evidence_assessment_versions_no_update ON evidence_assessment_versions;")
    op.execute("DROP FUNCTION IF EXISTS evidence_assessment_versions_refuse_truncate();")
    op.execute("DROP FUNCTION IF EXISTS evidence_assessment_versions_refuse_delete();")
    op.execute("DROP FUNCTION IF EXISTS evidence_assessment_versions_refuse_update();")

    op.execute("DROP INDEX IF EXISTS ix_evidence_assessments_org_awaiting;")

    op.drop_constraint('fk_evidence_assessments_current_version',
                       'evidence_assessments', type_='foreignkey')
    op.drop_constraint('fk_evidence_assessments_reviewed_by',
                       'evidence_assessments', type_='foreignkey')

    for column in (
        'unassessable_reason',
        'assessed_file_sha256',
        'truncated',
        'age_exceeds_12_months',
        'effective_date_source',
        'evidence_effective_date',
        'ao_findings',
        'cannot_assess_count',
        'gap_count',
        'reviewed_at',
        'reviewed_by_user_id',
        'review_decision',
        'version_number',
        'current_version_id',
    ):
        op.drop_column('evidence_assessments', column)

    op.drop_index('ix_evidence_assessment_versions_org_file',
                  table_name='evidence_assessment_versions')
    op.drop_table('evidence_assessment_versions')
