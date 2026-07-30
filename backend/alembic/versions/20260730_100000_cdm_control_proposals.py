"""CDM control-level proposals — one review decision per (control, document)

The retrieval pass keeps up to ``CDM_MAX_PROPOSALS_PER_CONTROL`` citations per
(control, document) pair, each persisted as its own ``cdm_mappings`` row. The
review queue rendered one card per row, so a reviewer answered the same
control-level question up to three times with different excerpts (#722: 213
cards for 71 decisions on a single-document corpus).

* ``cdm_control_proposals`` — exactly one row per (organization, control,
  document), the review unit. A parent table rather than a "primary" flag on
  ``cdm_mappings`` because the pair needs real uniqueness (``cdm_mappings``
  has none) and its own lifecycle: a consolidated score/rationale from the
  recompute pass, review bookkeeping, and a citations fingerprint that makes
  re-runs idempotent and dismissals sticky until the evidence changes.

* ``cdm_mappings.control_proposal_id`` — nullable FK linking each citation to
  its parent. SET NULL on delete: citation rows are provenance and must
  survive a proposal row being replaced; unlinked rows are re-grouped by the
  next consolidation pass, which is also how pre-existing rows are adopted
  without a data backfill.

``consolidated_score``/``rationale`` start as a heuristic (max citation score,
no rationale) written inline by the compute task; the chained recompute task
upgrades them and stamps ``recompute_provider``/``recompute_model_id``. A NULL
provider pair therefore always means "heuristic values, recompute pending or
unavailable".

Non-destructive by construction: one new table plus one additive nullable
column. Nothing is dropped or retyped.

Revision ID: cdm4consol001
Revises: cdm3intent001
Create Date: 2026-07-30 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'cdm4consol001'
down_revision = 'cdm3intent001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── cdm_control_proposals ───────────────────────────────────────────
    op.create_table(
        'cdm_control_proposals',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('scoped_control_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('cdm_document_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False,
                  server_default='proposed'),
        sa.Column('consolidated_score', sa.Float(), nullable=False),
        # Recompute-pass output; NULL while only the heuristic has run
        sa.Column('rationale', sa.Text(), nullable=True),
        sa.Column('citation_count', sa.SmallInteger(), nullable=False),
        # sha256 over the document's extracted-text sha + sorted citation
        # offsets — identical fingerprint ⇒ re-run is a no-op
        sa.Column('citations_fingerprint', sa.String(length=64), nullable=False),
        sa.Column('recompute_provider', sa.String(length=32), nullable=True),
        sa.Column('recompute_model_id', sa.String(length=128), nullable=True),
        # Revision current when this consolidation was computed — a pass
        # property, not a citation property (citations may span revisions)
        sa.Column('kb_revision', sa.String(length=128), nullable=False),
        sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('accepted_by_user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('dismissed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('dismissed_by_user_id', postgresql.UUID(as_uuid=True), nullable=True),
        # Survives resurrection so the UI can show "previously dismissed"
        sa.Column('dismiss_reason', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['scoped_control_id'], ['scoped_controls.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['cdm_document_id'], ['cdm_documents.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['accepted_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['dismissed_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.UniqueConstraint('organization_id', 'scoped_control_id', 'cdm_document_id',
                            name='uq_cdm_control_proposals'),
        sa.CheckConstraint(
            "status IN ('proposed', 'accepted', 'dismissed', 'stale')",
            name='ck_cdm_control_proposals_status',
        ),
    )
    op.create_index(
        'ix_cdm_control_proposals_org_status',
        'cdm_control_proposals',
        ['organization_id', 'status'],
    )
    # Serves the delete/supersede/purge paths, which filter by document — the
    # unique constraint's (org, control, doc) order cannot
    op.create_index(
        'ix_cdm_control_proposals_org_document',
        'cdm_control_proposals',
        ['organization_id', 'cdm_document_id'],
    )

    # ── cdm_mappings: parent link ───────────────────────────────────────
    op.add_column(
        'cdm_mappings',
        sa.Column('control_proposal_id', postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        'fk_cdm_mappings_control_proposal',
        'cdm_mappings',
        'cdm_control_proposals',
        ['control_proposal_id'],
        ['id'],
        ondelete='SET NULL',
    )
    op.create_index(
        'ix_cdm_mappings_control_proposal',
        'cdm_mappings',
        ['control_proposal_id'],
    )


def downgrade() -> None:
    op.drop_index('ix_cdm_mappings_control_proposal', table_name='cdm_mappings')
    op.drop_constraint('fk_cdm_mappings_control_proposal', 'cdm_mappings', type_='foreignkey')
    op.drop_column('cdm_mappings', 'control_proposal_id')

    op.drop_index('ix_cdm_control_proposals_org_document', table_name='cdm_control_proposals')
    op.drop_index('ix_cdm_control_proposals_org_status', table_name='cdm_control_proposals')
    op.drop_table('cdm_control_proposals')
