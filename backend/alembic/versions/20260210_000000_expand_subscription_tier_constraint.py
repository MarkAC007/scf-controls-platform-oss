"""Expand user_subscriptions tier check constraint to include all valid tiers.

The original constraint only allowed 'free', 'professional', 'enterprise'.
The marketing site sync uses 'consultant', 'pro', and 'custom' tier names.
This migration updates the check constraint to accept all valid tier values.

Revision ID: u2v3w4x5y6z7
Revises: t1u2v3w4x5y6
Create Date: 2026-02-10
"""
revision = 'u2v3w4x5y6z7'
down_revision = 't1u2v3w4x5y6'
branch_labels = None
depends_on = None

from alembic import op


def upgrade() -> None:
    # Drop the old check constraint
    op.execute("""
        ALTER TABLE user_subscriptions
        DROP CONSTRAINT IF EXISTS ck_user_subscriptions_tier
    """)

    # Add the new check constraint with all valid tiers
    op.execute("""
        ALTER TABLE user_subscriptions
        ADD CONSTRAINT ck_user_subscriptions_tier
        CHECK (tier IN ('free', 'professional', 'enterprise', 'pro', 'consultant', 'custom'))
    """)


def downgrade() -> None:
    # Revert to the original constraint
    op.execute("""
        ALTER TABLE user_subscriptions
        DROP CONSTRAINT IF EXISTS ck_user_subscriptions_tier
    """)

    op.execute("""
        ALTER TABLE user_subscriptions
        ADD CONSTRAINT ck_user_subscriptions_tier
        CHECK (tier IN ('free', 'professional', 'enterprise'))
    """)
