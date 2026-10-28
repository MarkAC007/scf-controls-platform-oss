"""Document profiles: bundled document scopes.

Revision ID: docprof001
Revises: pigcap001
Create Date: 2026-10-28 12:00:00

Domain-scoped documents can now cover a bundle of SCF domains or an
operational topic (services/doc_gen/templates/bundles.yaml).

* ``generated_documents.domain_id`` widens from 16 to 64 characters. It keeps
  its meaning as the document's scope key: an SCF domain identifier ("IAC")
  as before, or a prefixed bundle or topic key ("B-ACCESS", "T-INCIDENT").
  The unique index on (organization_id, generator_name, domain_id) is
  unchanged, so every existing document stays valid with no rewrite.
* ``generated_documents.scope_domains`` records which domains a document
  covered when it was generated -- provenance, because bundles.yaml can change.
* ``generated_documents.chapter_cache`` holds each domain chapter of a bundled
  document with the hash of the prompt that produced it, so a regeneration
  rewrites only the chapters whose inputs moved.
* ``doc_gen_settings.document_profile`` is the organisation's profile; the
  server default keeps every existing organisation on ``granular``.

Downgrade drops the new columns and narrows domain_id back to 16, which fails
if a bundled document exists -- delete those first; nothing else can hold them.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "docprof001"
down_revision = "pigcap001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "generated_documents", "domain_id",
        type_=sa.String(64), existing_type=sa.String(16),
        existing_nullable=False, existing_server_default="",
    )
    op.add_column(
        "generated_documents",
        sa.Column("scope_domains", JSONB, nullable=False, server_default="[]"),
    )
    op.add_column(
        "generated_documents",
        sa.Column("chapter_cache", JSONB, nullable=False, server_default="{}"),
    )
    op.add_column(
        "doc_gen_settings",
        sa.Column(
            "document_profile", sa.String(32), nullable=False,
            server_default="granular",
        ),
    )


def downgrade() -> None:
    op.drop_column("doc_gen_settings", "document_profile")
    op.drop_column("generated_documents", "chapter_cache")
    op.drop_column("generated_documents", "scope_domains")
    op.alter_column(
        "generated_documents", "domain_id",
        type_=sa.String(16), existing_type=sa.String(64),
        existing_nullable=False, existing_server_default="",
    )
