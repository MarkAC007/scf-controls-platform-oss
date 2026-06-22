#!/usr/bin/env python3
"""
Grandfather existing users with free tier subscriptions.

This script creates free tier subscriptions for all existing users who
don't already have one. This is a one-time migration for the website-first
provisioning overhaul.

All existing users are treated as grandfathered on the free tier.
They can continue using the platform normally and upgrade later.

Usage:
    python scripts/grandfather_existing_users.py [--dry-run] [--apply]

Options:
    --dry-run    Only analyse, don't create subscriptions (default)
    --apply      Actually create subscription records (requires confirmation)
"""
import asyncio
import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
import os

from models import User, UserSubscription, SubscriptionTier
from services.subscription import DEFAULT_TIER_LIMITS


async def get_db_session() -> AsyncSession:
    """Create an async database session."""
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL environment variable is required")

    # Convert to async URL if needed
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://")

    engine = create_async_engine(database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return async_session()


async def get_users_without_subscriptions(db: AsyncSession) -> List[Dict[str, Any]]:
    """
    Find all users who don't have a subscription record.

    Returns:
        List of user dicts with id, email, display_name, created_at
    """
    # Get all users
    users_result = await db.execute(select(User))
    all_users = users_result.scalars().all()

    # Get users who have subscriptions
    subs_result = await db.execute(select(UserSubscription.user_id))
    users_with_subs = {row[0] for row in subs_result.all()}

    # Filter to users without subscriptions
    users_without = []
    for user in all_users:
        if user.id not in users_with_subs:
            users_without.append({
                "id": user.id,
                "email": user.email,
                "display_name": user.display_name,
                "created_at": user.created_at,
            })

    return users_without


async def create_free_subscriptions(
    db: AsyncSession,
    users: List[Dict[str, Any]],
) -> int:
    """
    Create free tier subscriptions for the given users.

    Args:
        db: Database session
        users: List of user dicts with id field

    Returns:
        Number of subscriptions created
    """
    free_limits = DEFAULT_TIER_LIMITS[SubscriptionTier.FREE.value]
    created = 0

    for user in users:
        subscription = UserSubscription(
            user_id=user["id"],
            tier=SubscriptionTier.FREE.value,
            max_organisations=free_limits["max_organisations"],
            max_team_members=free_limits["max_team_members"],
            is_active=True,
            # No Stripe customer ID - they're grandfathered free tier
            stripe_customer_id=None,
            stripe_subscription_id=None,
        )
        db.add(subscription)
        created += 1

    await db.commit()
    return created


async def main():
    parser = argparse.ArgumentParser(
        description="Grandfather existing users with free tier subscriptions"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only analyse, don't create subscriptions (default behaviour)"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Create subscription records (requires confirmation)"
    )

    args = parser.parse_args()

    # Default to dry-run if neither flag is specified
    # --apply overrides --dry-run
    is_dry_run = not args.apply

    print("=" * 60)
    print("SCF Controls Platform - Grandfather Existing Users")
    print("=" * 60)
    print()
    print(f"Timestamp: {datetime.utcnow().isoformat()}")
    print()

    try:
        db = await get_db_session()

        # Get users without subscriptions
        print("Finding users without subscriptions...")
        users_without_subs = await get_users_without_subscriptions(db)

        # Get total user count for context
        total_result = await db.execute(select(func.count(User.id)))
        total_users = total_result.scalar()

        print()
        print(f"Total users in system: {total_users}")
        print(f"Users without subscriptions: {len(users_without_subs)}")
        print()

        if not users_without_subs:
            print("All users already have subscriptions. Nothing to do.")
            await db.close()
            return

        # Show affected users
        print("Affected users:")
        for user in users_without_subs[:10]:  # Show first 10
            print(f"  - {user['email']} (created: {user['created_at']})")
        if len(users_without_subs) > 10:
            print(f"  ... and {len(users_without_subs) - 10} more")
        print()

        # Apply if requested
        if not is_dry_run:
            print("=" * 60)
            print("APPLY MODE - Creating subscription records")
            print("=" * 60)
            print()
            print(f"About to create {len(users_without_subs)} free tier subscriptions.")
            print()
            print("This will:")
            print("  - Give each user a FREE tier subscription")
            print("  - Allow 1 organisation, 5 team members")
            print("  - No Stripe customer ID (grandfathered)")
            print()

            confirm = input("Type 'yes' to confirm: ")
            if confirm.lower() == "yes":
                print()
                print("Creating subscriptions...")
                created = await create_free_subscriptions(db, users_without_subs)
                print()
                print(f"SUCCESS: Created {created} free tier subscriptions")
                print()

                # Audit log
                print("Audit log entry:")
                print(f"  Action: grandfather_existing_users")
                print(f"  Timestamp: {datetime.utcnow().isoformat()}")
                print(f"  Users affected: {created}")
                print(f"  Tier assigned: {SubscriptionTier.FREE.value}")
            else:
                print("Cancelled.")
        else:
            print("DRY RUN - No changes made")
            print()
            print("Run with --apply to create subscriptions.")

        await db.close()

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
