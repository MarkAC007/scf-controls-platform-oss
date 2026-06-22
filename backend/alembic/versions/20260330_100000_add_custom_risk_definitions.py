"""Add custom_risk_definitions table for org-defined risks.

Allows organizations to create their own risk definitions alongside
the static SCF risk catalog. Custom risks use auto-generated codes
in the format R-ORG-N to avoid collision with SCF codes.

Revision ID: ee5f6g7h8i9j
Revises: dd4e5f6a7b8c
Create Date: 2026-03-30 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'ee5f6g7h8i9j'
down_revision: Union[str, None] = 'dd4e5f6a7b8c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create custom_risk_definitions table."""
    op.create_table(
        'custom_risk_definitions',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('risk_code', sa.String(20), nullable=False),
        sa.Column('title', sa.String(100), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('category_name', sa.String(50), nullable=False, server_default='Custom'),
        sa.Column('category_color', sa.String(7), nullable=False, server_default='#6b7280'),
        sa.Column('created_at', sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=True),
        sa.Column('created_by_user_id', postgresql.UUID(as_uuid=True), nullable=True),

        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.UniqueConstraint('organization_id', 'risk_code', name='uq_custom_risk_defs_org_risk_code'),
    )

    op.create_index(
        'ix_custom_risk_defs_org_id',
        'custom_risk_definitions',
        ['organization_id']
    )


def downgrade() -> None:
    """Drop custom_risk_definitions table."""
    op.drop_index('ix_custom_risk_defs_org_id', table_name='custom_risk_definitions')
    op.drop_table('custom_risk_definitions')
