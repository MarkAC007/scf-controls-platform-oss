"""Add organization_invites table for org member invitation system.

This migration creates the organization_invites table to support
tracked invitations with secure tokens, domain enforcement, and
role-based access. Invitations count toward subscription team member limits.

Table schema:
- id: UUID primary key
- organization_id: FK to organizations, CASCADE delete
- invited_by_user_id: FK to users, SET NULL on delete
- email: invitee email address
- role: admin/editor/viewer (default viewer)
- invite_token: unique secure URL-safe token (32 bytes)
- status: pending/accepted/expired/cancelled
- custom_message: optional personal note
- expires_at: 7-day expiry window
- created_at/updated_at: timestamps

Revision ID: g7h8i9j0k1l2
Revises: f6g7h8i9j0k1
Create Date: 2026-01-28 00:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'g7h8i9j0k1l2'
down_revision: Union[str, None] = 'f6g7h8i9j0k1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create organization_invites table with indexes."""

    # ==========================================================================
    # ORGANIZATION INVITES TABLE
    # ==========================================================================
    op.create_table(
        'organization_invites',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('invited_by_user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('role', sa.String(50), nullable=False, server_default='viewer'),
        sa.Column('invite_token', sa.String(64), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('custom_message', sa.Text(), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=False), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['invited_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.UniqueConstraint('invite_token', name='uq_organization_invites_token'),
    )

    # ==========================================================================
    # INDEXES
    # ==========================================================================

    # Composite index for duplicate checking (org + email + status)
    op.create_index(
        'idx_org_invites_org_email_status',
        'organization_invites',
        ['organization_id', 'email', 'status'],
    )

    # Index for token lookup (accept flow)
    op.create_index(
        'idx_org_invites_token',
        'organization_invites',
        ['invite_token'],
        unique=True,
    )

    # Index for listing invites by organisation
    op.create_index(
        'idx_org_invites_org_id',
        'organization_invites',
        ['organization_id'],
    )


def downgrade() -> None:
    """Drop organization_invites table and indexes."""

    # Drop indexes
    op.drop_index('idx_org_invites_org_id', table_name='organization_invites')
    op.drop_index('idx_org_invites_token', table_name='organization_invites')
    op.drop_index('idx_org_invites_org_email_status', table_name='organization_invites')

    # Drop table
    op.drop_table('organization_invites')
