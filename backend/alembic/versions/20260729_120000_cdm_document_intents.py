"""CDM document intents — model-claimed domain edges (document map MVP)

Adds the substrate for the document map's model-claimed layer:

* ``cdm_document_intents`` — up to three domain codes per document, ranked in
  the order the classifier returned them. A child table rather than a JSONB
  array on ``cdm_documents`` because both the map aggregate and the mapping
  gate ask the same question — given a domain, which documents? — and the
  composite index answers it directly instead of unnesting every document row.

* Intent lifecycle columns on ``cdm_documents``. Zero intent rows is ambiguous
  (not yet classified / failed / the model returned nothing usable) and those
  three drive different UI and different gate behaviour, so the state is a
  scalar on the parent rather than an inference from a row count.

The intent lifecycle is parallel to, and independent of, ``ingest_status``:
classification is an enhancement layered on ingest, never a gate on it. A
document whose classification fails keeps whatever terminal ingest state it
earned and stays searchable, mappable and visible in the map.

Bookkeeping columns (``provider``, ``model_id``, ``prompt_version``,
``classification_id``, ``rationale``) exist for operators via SQL and logs, and
are deliberately absent from every API response model.

Non-destructive by construction: one new table plus three additive, nullable-or-
defaulted columns. Nothing is dropped or retyped.

Revision ID: cdm3intent001
Revises: cdm2c709chunk
Create Date: 2026-07-29 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'cdm3intent001'
down_revision = 'cdm2c709chunk'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── cdm_document_intents ────────────────────────────────────────────
    op.create_table(
        'cdm_document_intents',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('cdm_document_id', postgresql.UUID(as_uuid=True), nullable=False),
        # scf_catalog_domains.identifier
        sa.Column('domain', sa.String(length=16), nullable=False),
        # 1..3, the classifier's own ordering
        sa.Column('rank', sa.SmallInteger(), nullable=False),
        # <=40 words, internal/debug only — never serialised to the webclient
        sa.Column('rationale', sa.Text(), nullable=True),
        # groups the rows a single classification run produced
        sa.Column('classification_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('prompt_version', sa.String(length=16), nullable=False),
        sa.Column('provider', sa.String(length=32), nullable=False),
        sa.Column('model_id', sa.String(length=128), nullable=False),
        sa.Column('classified_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['cdm_document_id'], ['cdm_documents.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('cdm_document_id', 'domain', name='uq_cdm_document_intents'),
        sa.CheckConstraint('rank BETWEEN 1 AND 3', name='ck_cdm_document_intents_rank'),
    )
    op.create_index(
        'ix_cdm_document_intents_org_domain',
        'cdm_document_intents',
        ['organization_id', 'domain'],
    )

    # ── cdm_documents: intent lifecycle ─────────────────────────────────
    # pending | classified | unclassified | failed | stale
    op.add_column(
        'cdm_documents',
        sa.Column('intent_status', sa.String(length=20), nullable=False,
                  server_default='pending'),
    )
    op.add_column('cdm_documents', sa.Column('intent_error', sa.Text(), nullable=True))
    op.add_column(
        'cdm_documents',
        sa.Column('intent_classified_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('cdm_documents', 'intent_classified_at')
    op.drop_column('cdm_documents', 'intent_error')
    op.drop_column('cdm_documents', 'intent_status')

    op.drop_index('ix_cdm_document_intents_org_domain', table_name='cdm_document_intents')
    op.drop_table('cdm_document_intents')
