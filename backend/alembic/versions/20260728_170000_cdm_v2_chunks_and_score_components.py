"""CDM v2 — document chunks + score provenance (epic #709)

Adds the Postgres-native two-tier search substrate:

* ``cdm_document_chunks`` — persisted, offset-bearing chunks with a generated
  ``tsvector`` and a GIN index. Ranking (Tier 1) happens over whole chunks;
  phrase location (Tier 2) happens within the winning chunk. Offsets must be
  captured at chunk-creation time because ``ts_rank_cd`` ranks lexeme positions
  and character offsets cannot be recovered from a ``tsvector``.

* Score-provenance columns on ``cdm_mappings`` — the three scoring components,
  the weights used, the match type, the matched objective, and the chunk the
  citation came from.

* Extraction provenance on ``cdm_documents`` — the SHA-256 of the extracted
  text the chunk set was built from, plus which backend produced it. Offsets
  index that text, so extractor drift would otherwise move every citation
  silently.

Non-destructive by construction: every change is an additive column or a new
table. No existing column is dropped or retyped, so existing accepted and
dismissed mappings keep their offsets, status and audit history.

Deliberately does **not** ``CREATE EXTENSION pg_trgm``. Fuzzy matching is an
enhancement, never a dependency: self-hosters on managed Postgres (RDS,
Supabase, hardened corporate instances) may be unable to create extensions, and
a hard dependency would turn this migration into a deployment failure for them.

Revision ID: cdm2c709chunk
Revises: wx4y5z6a7b8c
Create Date: 2026-07-28 17:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'cdm2c709chunk'
down_revision = 'wx4y5z6a7b8c'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── cdm_document_chunks ─────────────────────────────────────────────
    op.create_table(
        'cdm_document_chunks',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('cdm_document_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('ordinal', sa.Integer(), nullable=False),
        sa.Column('heading', sa.String(length=255), nullable=True),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('char_start', sa.Integer(), nullable=False),
        sa.Column('char_end', sa.Integer(), nullable=False),
        sa.Column('body_norm', sa.Text(), nullable=False),
        sa.Column(
            'search_vector',
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('english', body)", persisted=True),
            nullable=True,
        ),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['cdm_document_id'], ['cdm_documents.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('cdm_document_id', 'ordinal', name='uq_cdm_chunks_document_ordinal'),
        sa.CheckConstraint('char_end > char_start', name='ck_cdm_chunks_offsets_ordered'),
        sa.CheckConstraint('char_start >= 0', name='ck_cdm_chunks_offset_non_negative'),
    )
    op.create_index(
        'ix_cdm_chunks_org_document',
        'cdm_document_chunks',
        ['organization_id', 'cdm_document_id'],
    )
    op.create_index(
        'ix_cdm_chunks_search_vector',
        'cdm_document_chunks',
        ['search_vector'],
        postgresql_using='gin',
    )

    # ── cdm_mappings: score provenance ──────────────────────────────────
    op.add_column('cdm_mappings', sa.Column('ts_rank_component', sa.Float(), nullable=True))
    op.add_column('cdm_mappings', sa.Column('objective_coverage_component', sa.Float(), nullable=True))
    op.add_column('cdm_mappings', sa.Column('term_overlap_component', sa.Float(), nullable=True))
    op.add_column('cdm_mappings', sa.Column('score_weights', postgresql.JSONB(), nullable=True))
    op.add_column('cdm_mappings', sa.Column('match_type', sa.String(length=24), nullable=True))
    op.add_column('cdm_mappings', sa.Column('matched_objective_text', sa.Text(), nullable=True))
    op.add_column('cdm_mappings', sa.Column('retrieval_tier', sa.String(length=24), nullable=True))
    op.add_column(
        'cdm_mappings',
        sa.Column('cdm_document_chunk_id', postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        'fk_cdm_mappings_chunk',
        'cdm_mappings',
        'cdm_document_chunks',
        ['cdm_document_chunk_id'],
        ['id'],
        ondelete='SET NULL',
    )

    # ── cdm_documents: extraction provenance ────────────────────────────
    op.add_column('cdm_documents', sa.Column('extracted_text_sha256', sa.String(length=64), nullable=True))
    op.add_column('cdm_documents', sa.Column('extraction_backend', sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column('cdm_documents', 'extraction_backend')
    op.drop_column('cdm_documents', 'extracted_text_sha256')

    op.drop_constraint('fk_cdm_mappings_chunk', 'cdm_mappings', type_='foreignkey')
    op.drop_column('cdm_mappings', 'cdm_document_chunk_id')
    op.drop_column('cdm_mappings', 'retrieval_tier')
    op.drop_column('cdm_mappings', 'matched_objective_text')
    op.drop_column('cdm_mappings', 'match_type')
    op.drop_column('cdm_mappings', 'score_weights')
    op.drop_column('cdm_mappings', 'term_overlap_component')
    op.drop_column('cdm_mappings', 'objective_coverage_component')
    op.drop_column('cdm_mappings', 'ts_rank_component')

    op.drop_index('ix_cdm_chunks_search_vector', table_name='cdm_document_chunks')
    op.drop_index('ix_cdm_chunks_org_document', table_name='cdm_document_chunks')
    op.drop_table('cdm_document_chunks')
