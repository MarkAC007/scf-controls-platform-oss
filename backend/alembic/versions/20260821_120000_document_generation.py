"""Document generation: generated documents, versions, sections, transitions, settings.

Five tables implementing the three-layer content model in Postgres, replacing
the standalone tool's .meta.json sidecars:

    generated_documents   one row per (org, generator, scope) -- the operative document
    document_versions     immutable generated snapshots (the "generated" layer)
    document_sections     the merge graph (the "human" layer + merge status)
    document_transitions  append-only lifecycle log
    doc_gen_settings      per-org toggle and the SCF licence acknowledgement

Note on the unique index: ``domain_id`` is NOT NULL with an empty-string
default rather than nullable. Postgres treats NULLs as distinct in a unique
index, so ``(organization_id, generator_name, NULL)`` would permit unlimited
duplicate Statements of Applicability. The empty string makes the constraint
actually constrain.

Revision ID: docgen001
Revises: catupg006
Create Date: 2026-08-21 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'docgen001'
down_revision = 'catupg006'
branch_labels = None
depends_on = None


SECTION_STATUSES = (
    'unchanged', 'updated', 'human_preserved',
    'conflict', 'new', 'pending_retirement',
)
LIFECYCLE_STATUSES = ('draft', 'in_review', 'approved', 'published')


def upgrade() -> None:
    # ------------------------------------------------------------------
    # generated_documents -- the operative document
    # ------------------------------------------------------------------
    op.create_table(
        'generated_documents',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),

        # Identity
        sa.Column('generator_name', sa.String(64), nullable=False),
        sa.Column('document_type', sa.String(32), nullable=False),
        sa.Column('domain_id', sa.String(16), nullable=False, server_default=''),
        sa.Column('title', sa.String(500), nullable=False),
        sa.Column('filename', sa.String(255), nullable=False),

        # Content -- the merged layer (generated combined with human edits)
        sa.Column('merged_content', sa.Text(), nullable=False, server_default=''),

        # Regeneration skip logic
        sa.Column('input_fingerprint', sa.String(64), nullable=True),
        sa.Column('input_components', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('catalog_version', sa.String(20), nullable=True),

        # Provenance
        sa.Column('generator_version', sa.String(40), nullable=True),
        sa.Column('model_id', sa.String(100), nullable=True),
        sa.Column('generation_version', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('tier', sa.SmallInteger(), nullable=False, server_default='1'),
        sa.Column('is_derivative', sa.Boolean(), nullable=False,
                  server_default=sa.text('false')),

        # Spec R7: CDM must be able to tell a generated document from a
        # customer-authored one, or a generated policy gets ingested as
        # evidence of the control it was written from.
        sa.Column('origin', sa.String(32), nullable=False, server_default='platform_generated'),

        # Lifecycle
        sa.Column('lifecycle_status', sa.String(20), nullable=False, server_default='draft'),

        sa.Column('created_by_user_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=False), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=False), server_default=sa.func.now()),
        sa.CheckConstraint(
            "lifecycle_status IN " + str(LIFECYCLE_STATUSES),
            name='ck_generated_documents_lifecycle_status',
        ),
    )
    op.create_index('ix_generated_documents_org', 'generated_documents', ['organization_id'])
    op.create_index(
        'ix_generated_documents_org_status',
        'generated_documents', ['organization_id', 'lifecycle_status'],
    )
    op.create_unique_constraint(
        'uq_generated_documents_org_generator_domain',
        'generated_documents', ['organization_id', 'generator_name', 'domain_id'],
    )

    # ------------------------------------------------------------------
    # document_versions -- the immutable generated layer
    # ------------------------------------------------------------------
    op.create_table(
        'document_versions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('document_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('generated_documents.id', ondelete='CASCADE'), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        # Either inline content or a blob key, never both -- large documents go
        # to storage_service and the row keeps the pointer.
        sa.Column('content', sa.Text(), nullable=True),
        sa.Column('blob_key', sa.String(500), nullable=True),
        sa.Column('input_fingerprint', sa.String(64), nullable=True),
        sa.Column('model_id', sa.String(100), nullable=True),
        sa.Column('generator_version', sa.String(40), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=False), server_default=sa.func.now()),
        sa.UniqueConstraint('document_id', 'version', name='uq_document_versions_doc_version'),
    )
    op.create_index('ix_document_versions_doc', 'document_versions', ['document_id'])

    # ------------------------------------------------------------------
    # document_sections -- the merge graph
    # ------------------------------------------------------------------
    op.create_table(
        'document_sections',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('document_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('generated_documents.id', ondelete='CASCADE'), nullable=False),
        sa.Column('section_id', sa.String(500), nullable=False),
        sa.Column('heading_text', sa.Text(), nullable=False, server_default=''),
        sa.Column('heading_level', sa.SmallInteger(), nullable=False, server_default='2'),
        sa.Column('ordinal', sa.Integer(), nullable=False, server_default='0'),

        # The three layers, per section
        sa.Column('content_hash', sa.String(64), nullable=False, server_default=''),
        sa.Column('last_generated_hash', sa.String(64), nullable=False, server_default=''),
        sa.Column('human_edited', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('edited_content', sa.Text(), nullable=True),

        sa.Column('status', sa.String(24), nullable=False, server_default='new'),
        sa.Column('control_ids', postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column('edited_by_user_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('edited_at', sa.DateTime(timezone=False), nullable=True),
        sa.UniqueConstraint('document_id', 'section_id', name='uq_document_sections_doc_section'),
        sa.CheckConstraint(
            "status IN " + str(SECTION_STATUSES),
            name='ck_document_sections_status',
        ),
    )
    op.create_index('ix_document_sections_doc', 'document_sections', ['document_id', 'ordinal'])

    # ------------------------------------------------------------------
    # document_transitions -- append-only lifecycle log
    # ------------------------------------------------------------------
    op.create_table(
        'document_transitions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('document_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('generated_documents.id', ondelete='CASCADE'), nullable=False),
        sa.Column('from_status', sa.String(20), nullable=True),
        sa.Column('to_status', sa.String(20), nullable=False),
        sa.Column('actor_user_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('actor_email', sa.String(255), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('trigger', sa.String(20), nullable=False, server_default='manual'),
        sa.Column('created_at', sa.DateTime(timezone=False), server_default=sa.func.now()),
    )
    op.create_index(
        'ix_document_transitions_doc',
        'document_transitions', ['document_id', 'created_at'],
    )

    # ------------------------------------------------------------------
    # doc_gen_settings -- the toggle and the licence acknowledgement
    # ------------------------------------------------------------------
    op.create_table(
        'doc_gen_settings',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('organizations.id', ondelete='CASCADE'),
                  nullable=False, unique=True),

        # Two independent gates. Tier 1 output is arguably a compilation that
        # CC BY-ND permits; Tier 2/3 is unambiguously derivative. One switch
        # would gate the free-licence case for no benefit.
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('derivative_generators_enabled', sa.Boolean(), nullable=False,
                  server_default=sa.text('false')),

        # Immutable once written. A boolean proves nothing when SCF asks who
        # authorised derivative generation and when.
        sa.Column('licence_acknowledged_at', sa.DateTime(timezone=False), nullable=True),
        sa.Column('licence_acknowledged_by_user_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('licence_acknowledged_by_email', sa.String(255), nullable=True),
        sa.Column('licence_text_version', sa.String(20), nullable=True),
        sa.Column('acknowledged_ip', sa.String(64), nullable=True),

        sa.Column('enabled_at', sa.DateTime(timezone=False), nullable=True),
        sa.Column('enabled_by_user_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('disabled_at', sa.DateTime(timezone=False), nullable=True),

        sa.Column('daily_generation_limit', sa.Integer(), nullable=False, server_default='25'),
        sa.Column('created_at', sa.DateTime(timezone=False), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=False), server_default=sa.func.now()),
        sa.CheckConstraint(
            'NOT (enabled AND licence_acknowledged_at IS NULL)',
            name='ck_doc_gen_settings_enabled_requires_acknowledgement',
        ),
    )


def downgrade() -> None:
    op.drop_table('doc_gen_settings')
    op.drop_index('ix_document_transitions_doc', table_name='document_transitions')
    op.drop_table('document_transitions')
    op.drop_index('ix_document_sections_doc', table_name='document_sections')
    op.drop_table('document_sections')
    op.drop_index('ix_document_versions_doc', table_name='document_versions')
    op.drop_table('document_versions')
    op.drop_constraint(
        'uq_generated_documents_org_generator_domain',
        'generated_documents', type_='unique',
    )
    op.drop_index('ix_generated_documents_org_status', table_name='generated_documents')
    op.drop_index('ix_generated_documents_org', table_name='generated_documents')
    op.drop_table('generated_documents')
