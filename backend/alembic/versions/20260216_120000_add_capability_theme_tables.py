"""Add capability theme and mapping tables for KSI-aligned capability groupings.

Creates two new catalog tables:
- capability_themes: 11 KSI-inspired capability theme definitions
- capability_theme_mappings: SCF control to capability theme mappings

Part of Epic #317: KSI-Aligned Platform Evolution
Issue #302: Create CapabilityTheme reference model and KSI-aligned seed data

Revision ID: v4w5x6y7z8a9
Revises: u2v3w4x5y6z7
Create Date: 2026-02-16
"""
revision = 'v4w5x6y7z8a9'
down_revision = 'u2v3w4x5y6z7'
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    # Create capability_themes table
    op.create_table(
        'capability_themes',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('theme_code', sa.String(16), unique=True, nullable=False),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('ksi_reference', sa.String(16), nullable=True),
        sa.Column('display_order', sa.Integer(), server_default='0'),
        sa.Column('icon', sa.String(32), nullable=True),
        sa.Column('catalog_version', sa.String(20), server_default='2025.4'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )

    # Create capability_theme_mappings table
    op.create_table(
        'capability_theme_mappings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('theme_id', sa.Integer(), sa.ForeignKey('capability_themes.id'), nullable=False),
        sa.Column('scf_id', sa.String(32), nullable=False),
        sa.Column('relevance', sa.String(16), server_default='primary'),
        sa.Column('catalog_version', sa.String(20), server_default='2025.4'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('theme_id', 'scf_id', name='uq_theme_mapping_theme_scf'),
    )

    # Indexes for common query patterns
    op.create_index('idx_capability_themes_code', 'capability_themes', ['theme_code'])
    op.create_index('idx_capability_theme_mappings_theme_id', 'capability_theme_mappings', ['theme_id'])
    op.create_index('idx_capability_theme_mappings_scf_id', 'capability_theme_mappings', ['scf_id'])


def downgrade() -> None:
    op.drop_index('idx_capability_theme_mappings_scf_id')
    op.drop_index('idx_capability_theme_mappings_theme_id')
    op.drop_index('idx_capability_themes_code')
    op.drop_table('capability_theme_mappings')
    op.drop_table('capability_themes')
