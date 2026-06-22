"""Add custom_risk_control_mappings table for manual control-risk linking.

Allows organizations to manually associate scoped controls with their
custom risks (R-ORG-N codes), since custom risks have no automatic
SCF catalog mappings.

Revision ID: ff6g7h8i9j0k
Revises: ee5f6g7h8i9j
Create Date: 2026-03-30 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'ff6g7h8i9j0k'
down_revision: Union[str, None] = 'ee5f6g7h8i9j'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create custom_risk_control_mappings table."""
    op.create_table(
        'custom_risk_control_mappings',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('risk_code', sa.String(20), nullable=False),
        sa.Column('scf_id', sa.String(50), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=True),
        sa.Column('created_by_user_id', postgresql.UUID(as_uuid=True), nullable=True),

        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.UniqueConstraint('organization_id', 'risk_code', 'scf_id', name='uq_custom_risk_ctrl_map_org_risk_scf'),
    )

    op.create_index(
        'ix_custom_risk_ctrl_map_org_risk',
        'custom_risk_control_mappings',
        ['organization_id', 'risk_code']
    )


def downgrade() -> None:
    """Drop custom_risk_control_mappings table."""
    op.drop_index('ix_custom_risk_ctrl_map_org_risk', table_name='custom_risk_control_mappings')
    op.drop_table('custom_risk_control_mappings')
