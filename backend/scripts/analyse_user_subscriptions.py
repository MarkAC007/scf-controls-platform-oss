#!/usr/bin/env python3
"""
Analyse existing users' usage and generate subscription tier recommendations.

This script is used for the initial subscription system deployment to:
1. Count organisations per user
2. Count team members per organisation
3. Identify users exceeding free tier limits
4. Generate tier assignment recommendations

Usage:
    python scripts/analyse_user_subscriptions.py [--dry-run] [--output FILE]

Options:
    --dry-run       Only analyse, don't create subscriptions
    --output FILE   Write recommendations to JSON file
    --apply         Actually create subscription records (requires confirmation)
"""
import asyncio
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any
from uuid import UUID

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
import os

from models import User, Organization, OrganizationMember, UserSubscription, SubscriptionTier
from services.subscription import DEFAULT_TIER_LIMITS


# Tier limits for reference
FREE_TIER_LIMITS = DEFAULT_TIER_LIMITS[SubscriptionTier.FREE.value]
PROFESSIONAL_TIER_LIMITS = DEFAULT_TIER_LIMITS[SubscriptionTier.PROFESSIONAL.value]
ENTERPRISE_TIER_LIMITS = DEFAULT_TIER_LIMITS[SubscriptionTier.ENTERPRISE.value]


async def get_db_session():
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


async def analyse_user_usage(db: AsyncSession) -> List[Dict[str, Any]]:
    """
    Analyse each user's current usage.

    Returns a list of user analysis records with:
    - user_id, email
    - org_count: number of organisations where user is admin
    - total_team_members: sum of members across owned orgs
    - has_subscription: whether user already has a subscription record
    - recommended_tier: suggested tier based on usage
    - exceeds_free: whether user exceeds free tier limits
    """
    results = []

    # Get all users
    users_result = await db.execute(select(User))
    users = users_result.scalars().all()

    for user in users:
        user_id = user.id

        # Count organisations where user is admin (owner)
        org_count_result = await db.execute(
            select(func.count(OrganizationMember.id))
            .where(OrganizationMember.user_id == user_id)
            .where(OrganizationMember.role == "admin")
        )
        org_count = org_count_result.scalar() or 0

        # Get all organisation IDs where user is admin
        org_ids_result = await db.execute(
            select(OrganizationMember.organization_id)
            .where(OrganizationMember.user_id == user_id)
            .where(OrganizationMember.role == "admin")
        )
        org_ids = [row[0] for row in org_ids_result.all()]

        # Count total team members across all owned organisations
        total_team_members = 0
        max_team_members_in_org = 0
        if org_ids:
            for org_id in org_ids:
                member_count_result = await db.execute(
                    select(func.count(OrganizationMember.id))
                    .where(OrganizationMember.organization_id == org_id)
                )
                count = member_count_result.scalar() or 0
                total_team_members += count
                max_team_members_in_org = max(max_team_members_in_org, count)

        # Check if user has existing subscription
        sub_result = await db.execute(
            select(UserSubscription).where(UserSubscription.user_id == user_id)
        )
        existing_subscription = sub_result.scalar_one_or_none()

        # Determine recommended tier based on usage
        exceeds_free = False
        recommended_tier = SubscriptionTier.FREE.value

        if org_count > FREE_TIER_LIMITS["max_organisations"]:
            exceeds_free = True
            if org_count <= PROFESSIONAL_TIER_LIMITS["max_organisations"]:
                recommended_tier = SubscriptionTier.PROFESSIONAL.value
            else:
                recommended_tier = SubscriptionTier.ENTERPRISE.value

        if max_team_members_in_org > FREE_TIER_LIMITS["max_team_members"]:
            exceeds_free = True
            if max_team_members_in_org <= PROFESSIONAL_TIER_LIMITS["max_team_members"]:
                if recommended_tier == SubscriptionTier.FREE.value:
                    recommended_tier = SubscriptionTier.PROFESSIONAL.value
            else:
                recommended_tier = SubscriptionTier.ENTERPRISE.value

        results.append({
            "user_id": str(user_id),
            "email": user.email,
            "display_name": user.display_name,
            "org_count": org_count,
            "total_team_members": total_team_members,
            "max_team_members_in_org": max_team_members_in_org,
            "has_subscription": existing_subscription is not None,
            "current_tier": existing_subscription.tier if existing_subscription else None,
            "recommended_tier": recommended_tier,
            "exceeds_free_tier": exceeds_free,
            "recommendation_reason": get_recommendation_reason(
                org_count, max_team_members_in_org, recommended_tier
            ),
        })

    return results


def get_recommendation_reason(org_count: int, max_members: int, tier: str) -> str:
    """Generate human-readable reason for tier recommendation."""
    if tier == SubscriptionTier.FREE.value:
        return "Usage within free tier limits"

    reasons = []
    if org_count > FREE_TIER_LIMITS["max_organisations"]:
        reasons.append(f"{org_count} organisations (free limit: {FREE_TIER_LIMITS['max_organisations']})")
    if max_members > FREE_TIER_LIMITS["max_team_members"]:
        reasons.append(f"{max_members} team members in one org (free limit: {FREE_TIER_LIMITS['max_team_members']})")

    if tier == SubscriptionTier.ENTERPRISE.value:
        if org_count > PROFESSIONAL_TIER_LIMITS["max_organisations"]:
            reasons.append(f"exceeds professional org limit ({PROFESSIONAL_TIER_LIMITS['max_organisations']})")
        if max_members > PROFESSIONAL_TIER_LIMITS["max_team_members"]:
            reasons.append(f"exceeds professional member limit ({PROFESSIONAL_TIER_LIMITS['max_team_members']})")

    return "; ".join(reasons)


def generate_summary(analysis: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Generate summary statistics from analysis."""
    total_users = len(analysis)
    users_with_subscriptions = sum(1 for u in analysis if u["has_subscription"])
    users_without_subscriptions = total_users - users_with_subscriptions

    tier_recommendations = {
        SubscriptionTier.FREE.value: 0,
        SubscriptionTier.PROFESSIONAL.value: 0,
        SubscriptionTier.ENTERPRISE.value: 0,
    }
    for user in analysis:
        tier_recommendations[user["recommended_tier"]] += 1

    exceeding_free = [u for u in analysis if u["exceeds_free_tier"]]

    return {
        "analysis_date": datetime.utcnow().isoformat(),
        "total_users": total_users,
        "users_with_subscriptions": users_with_subscriptions,
        "users_without_subscriptions": users_without_subscriptions,
        "tier_recommendations": tier_recommendations,
        "users_exceeding_free_tier": len(exceeding_free),
        "exceeding_users": [
            {
                "email": u["email"],
                "org_count": u["org_count"],
                "max_team_members": u["max_team_members_in_org"],
                "recommended_tier": u["recommended_tier"],
                "reason": u["recommendation_reason"],
            }
            for u in exceeding_free
        ],
    }


async def apply_subscriptions(
    db: AsyncSession,
    analysis: List[Dict[str, Any]],
    grandfather_exceeding: bool = True
) -> Dict[str, int]:
    """
    Create subscription records for users without them.

    Args:
        db: Database session
        analysis: User analysis results
        grandfather_exceeding: If True, assign recommended tier to users
                               exceeding free limits (grandfather clause).
                               If False, assign free tier to all.

    Returns:
        Dict with counts of created subscriptions by tier
    """
    created = {tier.value: 0 for tier in SubscriptionTier}

    for user in analysis:
        if user["has_subscription"]:
            continue  # Skip users who already have subscriptions

        # Determine tier to assign
        if grandfather_exceeding and user["exceeds_free_tier"]:
            tier = user["recommended_tier"]
        else:
            tier = SubscriptionTier.FREE.value

        tier_limits = DEFAULT_TIER_LIMITS[tier]

        subscription = UserSubscription(
            user_id=UUID(user["user_id"]),
            tier=tier,
            max_organisations=tier_limits["max_organisations"],
            max_team_members=tier_limits["max_team_members"],
            is_active=True,
        )
        db.add(subscription)
        created[tier] += 1

    await db.commit()
    return created


async def main():
    parser = argparse.ArgumentParser(
        description="Analyse user usage and generate subscription recommendations"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only analyse, don't create subscriptions"
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Write analysis to JSON file"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Create subscription records (requires confirmation)"
    )
    parser.add_argument(
        "--grandfather",
        action="store_true",
        default=True,
        help="Grandfather users exceeding free tier to appropriate paid tier (default: True)"
    )

    args = parser.parse_args()

    print("=" * 60)
    print("SCF Controls Platform - User Subscription Analysis")
    print("=" * 60)
    print()

    try:
        db = await get_db_session()

        print("Analysing user usage...")
        analysis = await analyse_user_usage(db)
        summary = generate_summary(analysis)

        # Print summary
        print()
        print(f"Total users: {summary['total_users']}")
        print(f"Users with subscriptions: {summary['users_with_subscriptions']}")
        print(f"Users without subscriptions: {summary['users_without_subscriptions']}")
        print()
        print("Tier Recommendations:")
        for tier, count in summary["tier_recommendations"].items():
            print(f"  {tier}: {count}")
        print()

        if summary["users_exceeding_free_tier"] > 0:
            print(f"Users exceeding free tier limits: {summary['users_exceeding_free_tier']}")
            print()
            print("Affected users:")
            for user in summary["exceeding_users"]:
                print(f"  - {user['email']}")
                print(f"    Orgs: {user['org_count']}, Max members: {user['max_team_members']}")
                print(f"    Recommendation: {user['recommended_tier']} ({user['reason']})")
            print()

        # Write output file if requested
        if args.output:
            output_data = {
                "summary": summary,
                "users": analysis,
            }
            with open(args.output, "w") as f:
                json.dump(output_data, f, indent=2)
            print(f"Analysis written to: {args.output}")
            print()

        # Apply subscriptions if requested
        if args.apply and not args.dry_run:
            users_to_update = [u for u in analysis if not u["has_subscription"]]
            if not users_to_update:
                print("No users need subscription records.")
            else:
                print(f"About to create {len(users_to_update)} subscription records.")
                if args.grandfather:
                    print("Users exceeding free tier will be grandfathered to appropriate tier.")
                else:
                    print("All users will be assigned free tier.")
                print()

                confirm = input("Type 'yes' to confirm: ")
                if confirm.lower() == "yes":
                    created = await apply_subscriptions(db, analysis, args.grandfather)
                    print()
                    print("Subscriptions created:")
                    for tier, count in created.items():
                        if count > 0:
                            print(f"  {tier}: {count}")
                else:
                    print("Cancelled.")

        await db.close()

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
