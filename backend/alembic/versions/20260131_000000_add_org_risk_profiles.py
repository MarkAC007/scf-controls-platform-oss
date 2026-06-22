"""Add organisation risk profiles table.

Adds a per-organisation risk profile with configurable risk level thresholds,
replacing hardcoded boundaries (1-4/5-9/10-16/17-25).

Revision ID: i9j0k1l2m3n4
Revises: h8i9j0k1l2m3
Create Date: 2026-01-31 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision = 'i9j0k1l2m3n4'
down_revision = 'h8i9j0k1l2m3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create organization_risk_profiles table
    op.create_table(
        'organization_risk_profiles',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('organization_id', UUID(as_uuid=True), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False, unique=True),
        sa.Column('low_max', sa.Integer(), nullable=False, server_default='4'),
        sa.Column('medium_max', sa.Integer(), nullable=False, server_default='9'),
        sa.Column('high_max', sa.Integer(), nullable=False, server_default='16'),
        sa.Column('acceptable_risk_level', sa.String(20), nullable=False, server_default='medium'),
        sa.Column('auto_escalate_above', sa.String(20), nullable=False, server_default='high'),
        sa.Column('required_vendor_certifications', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('preferred_vendor_certifications', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('vendor_auto_approve_max', sa.Integer(), nullable=False, server_default='4'),
        sa.Column('vendor_auto_reject_min', sa.Integer(), nullable=False, server_default='20'),
        sa.Column('created_at', sa.DateTime(timezone=False), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=False), server_default=sa.func.now()),
        sa.Column('updated_by_user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
    )

    # Auto-provision a default profile for each existing organisation
    op.execute("""
        INSERT INTO organization_risk_profiles (organization_id)
        SELECT id FROM organizations
        ON CONFLICT (organization_id) DO NOTHING
    """)


def downgrade() -> None:
    op.drop_table('organization_risk_profiles')
