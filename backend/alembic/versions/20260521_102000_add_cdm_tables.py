"""Add CDM foundation tables for document ingest and control mappings.

Creates the first two Control Documentation Mapper (CDM) tables:
``cdm_documents`` for uploaded source files and ``cdm_mappings`` for
per-control mapping candidates and decisions. This migration is purely
additive and does not alter existing tables or workflows.

Revision ID: lm5n6o7p8q9r
Revises: kk2l3m4n5o6p
Create Date: 2026-05-21 10:20:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'lm5n6o7p8q9r'
down_revision: Union[str, None] = 'kk2l3m4n5o6p'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the CDM document and mapping tables with their indexes."""

    op.create_table(
        'cdm_documents',
        sa.Column(
            'id',
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text('gen_random_uuid()'),
        ),
        sa.Column(
            'organization_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('organizations.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('original_filename', sa.String(512), nullable=False),
        sa.Column('mime_type', sa.String(100), nullable=False),
        sa.Column('sha256', sa.String(64), nullable=False),
        sa.Column('size_bytes', sa.BigInteger(), nullable=False),
        sa.Column(
            'upload_user_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('users.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column('kb_revision', sa.String(128), nullable=True),
        sa.Column(
            'ingest_status',
            sa.String(20),
            nullable=False,
            server_default='pending',
        ),
        sa.Column('ingest_error', sa.Text(), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_index(
        'ix_cdm_documents_org',
        'cdm_documents',
        ['organization_id'],
    )
    op.create_index(
        'ix_cdm_documents_sha256',
        'cdm_documents',
        ['organization_id', 'sha256'],
    )

    op.create_table(
        'cdm_mappings',
        sa.Column(
            'id',
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text('gen_random_uuid()'),
        ),
        sa.Column(
            'organization_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('organizations.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column(
            'scoped_control_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('scoped_controls.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column(
            'cdm_document_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('cdm_documents.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('section', sa.String(255), nullable=True),
        sa.Column('byte_offset_start', sa.Integer(), nullable=False),
        sa.Column('byte_offset_end', sa.Integer(), nullable=False),
        sa.Column('relevance_score', sa.Float(), nullable=False),
        sa.Column(
            'status',
            sa.String(20),
            nullable=False,
            server_default='proposed',
        ),
        sa.Column('kb_revision', sa.String(128), nullable=False),
        sa.Column(
            'accepted_by_user_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('users.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('dismiss_reason', sa.Text(), nullable=True),
        sa.Column(
            'dismissed_by_user_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('users.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column('dismissed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_index(
        'ix_cdm_mappings_org_status',
        'cdm_mappings',
        ['organization_id', 'status'],
    )
    op.create_index(
        'ix_cdm_mappings_control',
        'cdm_mappings',
        ['organization_id', 'scoped_control_id'],
    )
    op.create_index(
        'ix_cdm_mappings_document',
        'cdm_mappings',
        ['cdm_document_id'],
    )


def downgrade() -> None:
    """Drop CDM indexes first, then tables in reverse foreign-key order."""
    op.drop_index('ix_cdm_mappings_document', table_name='cdm_mappings')
    op.drop_index('ix_cdm_mappings_control', table_name='cdm_mappings')
    op.drop_index('ix_cdm_mappings_org_status', table_name='cdm_mappings')
    op.drop_table('cdm_mappings')

    op.drop_index('ix_cdm_documents_sha256', table_name='cdm_documents')
    op.drop_index('ix_cdm_documents_org', table_name='cdm_documents')
    op.drop_table('cdm_documents')
