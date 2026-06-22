"""Add Consultant Portal tables for multi-client management.

This migration creates the data model for the Consultant Portal feature,
which allows GRC consultants to manage multiple client organisations from
a single dashboard.

Tables created:
1. consultant_profiles - Links users to consultant capabilities
2. consultant_client_relationships - Maps consultants to client organisations
3. consultant_invites - Tracks pending client invitations

Key design decisions:
- CASCADE on delete for consultant_profiles ensures clean user deletion
- CASCADE on delete for relationships ensures no orphaned records
- Unique constraint on (consultant_id, organization_id) prevents duplicates
- Indexes on consultant_id columns for fast dashboard queries
- max_clients field supports subscription tier enforcement

Revision ID: b2c3d4e5f6g7
Revises: a1b2c3d4e5f6
Create Date: 2026-01-20 12:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6g7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create Consultant Portal tables."""

    # ==========================================================================
    # CONSULTANT PROFILES TABLE
    # ==========================================================================
    # Links a user to consultant capabilities. One user can have at most one
    # consultant profile (enforced by unique constraint on user_id).
    op.create_table(
        'consultant_profiles',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('company_name', sa.String(255), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('max_clients', sa.Integer(), nullable=False, server_default='20'),
        sa.Column('created_at', sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('user_id', name='uq_consultant_profiles_user_id')
    )

    # ==========================================================================
    # CONSULTANT CLIENT RELATIONSHIPS TABLE
    # ==========================================================================
    # Junction table mapping consultants to client organisations with role and status.
    # A consultant can have multiple clients; an organisation can have multiple consultants.
    op.create_table(
        'consultant_client_relationships',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('consultant_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('role', sa.String(20), nullable=False, server_default='editor'),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('invited_at', sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=True),
        sa.Column('accepted_at', sa.DateTime(timezone=False), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['consultant_id'], ['consultant_profiles.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('consultant_id', 'organization_id', name='uq_consultant_client_consultant_org')
    )

    # ==========================================================================
    # CONSULTANT INVITES TABLE
    # ==========================================================================
    # Tracks pending invitations from consultants to potential clients.
    # The invite_token is a secure random string used in invite links.
    op.create_table(
        'consultant_invites',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('consultant_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('organization_name', sa.String(255), nullable=False),
        sa.Column('invite_token', sa.String(64), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('expires_at', sa.DateTime(timezone=False), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['consultant_id'], ['consultant_profiles.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('invite_token', name='uq_consultant_invites_token')
    )

    # ==========================================================================
    # INDEXES
    # ==========================================================================

    # Index on user_id for fast lookup when checking if user is a consultant
    op.create_index(
        'ix_consultant_profiles_user_id',
        'consultant_profiles',
        ['user_id'],
        unique=True
    )

    # Index on consultant_id for fast client list retrieval (dashboard query)
    op.create_index(
        'ix_consultant_client_relationships_consultant_id',
        'consultant_client_relationships',
        ['consultant_id']
    )

    # Index on organization_id for finding all consultants for an org
    op.create_index(
        'ix_consultant_client_relationships_organization_id',
        'consultant_client_relationships',
        ['organization_id']
    )

    # Composite index for filtering by consultant and status (active clients query)
    op.create_index(
        'ix_consultant_client_relationships_consultant_status',
        'consultant_client_relationships',
        ['consultant_id', 'status']
    )

    # Index on consultant_id for retrieving pending invites
    op.create_index(
        'ix_consultant_invites_consultant_id',
        'consultant_invites',
        ['consultant_id']
    )

    # Index on invite_token for fast invite lookup
    op.create_index(
        'ix_consultant_invites_token',
        'consultant_invites',
        ['invite_token'],
        unique=True
    )

    # Index on email for finding invites by email address
    op.create_index(
        'ix_consultant_invites_email',
        'consultant_invites',
        ['email']
    )

    # ==========================================================================
    # CHECK CONSTRAINTS
    # ==========================================================================

    # Validate role values
    op.execute("""
        ALTER TABLE consultant_client_relationships
        ADD CONSTRAINT ck_consultant_client_relationships_role
        CHECK (role IN ('admin', 'editor', 'viewer'))
    """)

    # Validate status values for relationships
    op.execute("""
        ALTER TABLE consultant_client_relationships
        ADD CONSTRAINT ck_consultant_client_relationships_status
        CHECK (status IN ('active', 'suspended', 'pending'))
    """)

    # Validate status values for invites
    op.execute("""
        ALTER TABLE consultant_invites
        ADD CONSTRAINT ck_consultant_invites_status
        CHECK (status IN ('pending', 'accepted', 'expired', 'cancelled'))
    """)

    # Ensure max_clients is positive
    op.execute("""
        ALTER TABLE consultant_profiles
        ADD CONSTRAINT ck_consultant_profiles_max_clients
        CHECK (max_clients > 0)
    """)


def downgrade() -> None:
    """Drop Consultant Portal tables in reverse order."""

    # Drop CHECK constraints first
    op.execute("""
        ALTER TABLE consultant_profiles
        DROP CONSTRAINT IF EXISTS ck_consultant_profiles_max_clients
    """)

    op.execute("""
        ALTER TABLE consultant_invites
        DROP CONSTRAINT IF EXISTS ck_consultant_invites_status
    """)

    op.execute("""
        ALTER TABLE consultant_client_relationships
        DROP CONSTRAINT IF EXISTS ck_consultant_client_relationships_status
    """)

    op.execute("""
        ALTER TABLE consultant_client_relationships
        DROP CONSTRAINT IF EXISTS ck_consultant_client_relationships_role
    """)

    # Drop indexes
    op.drop_index('ix_consultant_invites_email', table_name='consultant_invites')
    op.drop_index('ix_consultant_invites_token', table_name='consultant_invites')
    op.drop_index('ix_consultant_invites_consultant_id', table_name='consultant_invites')
    op.drop_index('ix_consultant_client_relationships_consultant_status', table_name='consultant_client_relationships')
    op.drop_index('ix_consultant_client_relationships_organization_id', table_name='consultant_client_relationships')
    op.drop_index('ix_consultant_client_relationships_consultant_id', table_name='consultant_client_relationships')
    op.drop_index('ix_consultant_profiles_user_id', table_name='consultant_profiles')

    # Drop tables in reverse dependency order
    op.drop_table('consultant_invites')
    op.drop_table('consultant_client_relationships')
    op.drop_table('consultant_profiles')
