"""Add user_subscriptions table for subscription tier management.

This migration creates the user_subscriptions table to track user subscription
tiers and their associated limits (max organisations, max team members).

The table enforces:
- One subscription per user (unique constraint on user_id)
- Positive limits (CHECK constraints on max_organisations, max_team_members)
- Valid tier values (CHECK constraint on tier)

For existing users, a default free tier subscription is created with:
- tier: 'free'
- max_organisations: 1
- max_team_members: 5
- is_active: True

Revision ID: f6g7h8i9j0k1
Revises: e5f6g7h8i9j0
Create Date: 2026-01-24 12:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'f6g7h8i9j0k1'
down_revision: Union[str, None] = 'e5f6g7h8i9j0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create user_subscriptions table and populate for existing users."""

    # ==========================================================================
    # USER SUBSCRIPTIONS TABLE
    # ==========================================================================
    # Tracks subscription tier and limits for each user. Each user can have
    # at most one subscription record.
    op.create_table(
        'user_subscriptions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('tier', sa.String(20), nullable=False, server_default='free'),
        sa.Column('max_organisations', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('max_team_members', sa.Integer(), nullable=False, server_default='5'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('stripe_customer_id', sa.String(255), nullable=True),
        sa.Column('stripe_subscription_id', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('user_id', name='uq_user_subscriptions_user_id')
    )

    # ==========================================================================
    # INDEXES
    # ==========================================================================

    # Index on user_id for fast lookup (also supports unique constraint)
    op.create_index(
        'ix_user_subscriptions_user_id',
        'user_subscriptions',
        ['user_id'],
        unique=True
    )

    # Index on tier for analytics queries (e.g., count by tier)
    op.create_index(
        'ix_user_subscriptions_tier',
        'user_subscriptions',
        ['tier']
    )

    # ==========================================================================
    # CHECK CONSTRAINTS
    # ==========================================================================

    # Validate tier values
    op.execute("""
        ALTER TABLE user_subscriptions
        ADD CONSTRAINT ck_user_subscriptions_tier
        CHECK (tier IN ('free', 'professional', 'enterprise'))
    """)

    # Ensure max_organisations is positive
    op.execute("""
        ALTER TABLE user_subscriptions
        ADD CONSTRAINT ck_user_subscriptions_max_organisations
        CHECK (max_organisations > 0)
    """)

    # Ensure max_team_members is positive
    op.execute("""
        ALTER TABLE user_subscriptions
        ADD CONSTRAINT ck_user_subscriptions_max_team_members
        CHECK (max_team_members > 0)
    """)

    # ==========================================================================
    # CREATE DEFAULT SUBSCRIPTIONS FOR EXISTING USERS
    # ==========================================================================
    # All existing users get a free tier subscription with default limits.
    op.execute("""
        INSERT INTO user_subscriptions (user_id, tier, max_organisations, max_team_members, is_active)
        SELECT id, 'free', 1, 5, true
        FROM users
        WHERE id NOT IN (SELECT user_id FROM user_subscriptions)
    """)


def downgrade() -> None:
    """Drop user_subscriptions table."""

    # Drop CHECK constraints first
    op.execute("""
        ALTER TABLE user_subscriptions
        DROP CONSTRAINT IF EXISTS ck_user_subscriptions_max_team_members
    """)

    op.execute("""
        ALTER TABLE user_subscriptions
        DROP CONSTRAINT IF EXISTS ck_user_subscriptions_max_organisations
    """)

    op.execute("""
        ALTER TABLE user_subscriptions
        DROP CONSTRAINT IF EXISTS ck_user_subscriptions_tier
    """)

    # Drop indexes
    op.drop_index('ix_user_subscriptions_tier', table_name='user_subscriptions')
    op.drop_index('ix_user_subscriptions_user_id', table_name='user_subscriptions')

    # Drop table
    op.drop_table('user_subscriptions')
