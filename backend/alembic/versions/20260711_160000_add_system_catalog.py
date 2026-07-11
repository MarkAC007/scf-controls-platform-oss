"""Add system catalog templates/recipes tables and System.catalog_template_id

Systems knowledge catalog (Systems Module v2): seeded templates for known
products, per-maturity-level collection recipes, and a deterministic link
from org systems to their template.

Revision ID: rs0t1u2v3w4x
Revises: qr9s0t1u2v3w
Create Date: 2026-07-11 16:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'rs0t1u2v3w4x'
down_revision = 'qr9s0t1u2v3w'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'system_catalog_templates',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('slug', sa.String(length=100), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('vendor', sa.String(length=255), nullable=False),
        sa.Column('system_type', sa.String(length=50), nullable=False),
        sa.Column('category', sa.String(length=100), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('website', sa.String(length=500), nullable=True),
        sa.Column('aliases', postgresql.JSONB(), nullable=True),
        sa.Column('logo_hint', sa.String(length=50), nullable=True),
        sa.Column('is_fallback', sa.Boolean(), nullable=True, server_default=sa.text('false')),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=True),
        sa.Column('version', sa.String(length=20), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()')),
    )
    op.create_index('ix_system_catalog_templates_slug', 'system_catalog_templates', ['slug'], unique=True)
    op.create_index('ix_system_catalog_templates_org', 'system_catalog_templates', ['organization_id'])

    op.create_table(
        'system_catalog_recipes',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('template_id', sa.Integer(),
                  sa.ForeignKey('system_catalog_templates.id', ondelete='CASCADE'), nullable=False),
        sa.Column('maturity_level', sa.String(length=2), nullable=False),
        sa.Column('title', sa.String(length=500), nullable=False),
        sa.Column('estimated_time', sa.String(length=100), nullable=True),
        sa.Column('frequency', sa.String(length=100), nullable=True),
        sa.Column('steps', postgresql.JSONB(), nullable=True),
        sa.Column('source', sa.String(length=20), nullable=True, server_default='curated'),
        sa.Column('version', sa.String(length=20), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()')),
        sa.UniqueConstraint('template_id', 'maturity_level', name='uq_system_catalog_recipe_level'),
    )
    op.create_index('ix_system_catalog_recipes_template', 'system_catalog_recipes', ['template_id'])

    op.add_column(
        'systems',
        sa.Column('catalog_template_id', sa.Integer(),
                  sa.ForeignKey('system_catalog_templates.id', ondelete='SET NULL'), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('systems', 'catalog_template_id')
    op.drop_index('ix_system_catalog_recipes_template', table_name='system_catalog_recipes')
    op.drop_table('system_catalog_recipes')
    op.drop_index('ix_system_catalog_templates_org', table_name='system_catalog_templates')
    op.drop_index('ix_system_catalog_templates_slug', table_name='system_catalog_templates')
    op.drop_table('system_catalog_templates')
