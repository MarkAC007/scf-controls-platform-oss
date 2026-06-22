#!/usr/bin/env python3
"""
Platform Admin CLI Toolkit

Command-line interface for platform-level administrative operations.
These commands provide direct database access for managing users and
organisations across the entire platform.

Usage:
    python -m cli.admin [COMMAND] [OPTIONS]

Commands:
    setup               Initial platform setup - create Default Organization
    seed-catalog        Seed SCF catalog from JSON files (run after setup)
    list-users          List all users with optional filters
    list-orgs           List all organisations with stats
    list-admins         List all platform admins
    grant-admin         Grant platform admin to a user
    revoke-admin        Revoke platform admin from a user
    add-member          Add a user to an organisation (creates user if needed)
    grant-consultant    Grant consultant access to a user (via API)
    revoke-consultant   Revoke consultant access from a user (via API)
    delete-user         Delete a user and all their data
    delete-org          Delete an organisation and all its data
    stats               Show platform-wide statistics

Examples:
    # Initial setup (run this first on a fresh deployment!)
    python -m cli.admin setup

    # List all users
    python -m cli.admin list-users

    # Grant platform admin to a user by email
    python -m cli.admin grant-admin --email admin@example.com

    # Add a user to an org as admin (creates user if they don't exist)
    python -m cli.admin add-member --email user@example.com --org-slug my-org --role admin

    # Grant consultant access via API
    python -m cli.admin grant-consultant --email user@example.com --base-url https://eu.scfcontrolsplatform.com --api-key <key>

    # List all platform admins
    python -m cli.admin list-admins

    # Delete a user (with confirmation)
    python -m cli.admin delete-user --email user@example.com --confirm

    # Show platform statistics
    python -m cli.admin stats
"""
import asyncio
import argparse
import sys
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

# Add parent directory to path for imports
sys.path.insert(0, str(__file__).rsplit('/', 2)[0])

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from database import AsyncSessionLocal
from models import (
    User,
    Organization,
    OrganizationMember,
    UserSubscription,
    ScopedControl,
    EvidenceTracking,
)
from catalog_seeder import seed_catalog_if_empty, reseed_catalog, get_catalog_stats


# =============================================================================
# Database Helpers
# =============================================================================

async def get_user_by_email(db: AsyncSession, email: str) -> Optional[User]:
    """Get a user by email address."""
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def get_user_by_id(db: AsyncSession, user_id: UUID) -> Optional[User]:
    """Get a user by ID."""
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def get_org_by_slug(db: AsyncSession, slug: str) -> Optional[Organization]:
    """Get an organisation by slug."""
    result = await db.execute(select(Organization).where(Organization.slug == slug))
    return result.scalar_one_or_none()


async def get_org_by_id(db: AsyncSession, org_id: UUID) -> Optional[Organization]:
    """Get an organisation by ID."""
    result = await db.execute(select(Organization).where(Organization.id == org_id))
    return result.scalar_one_or_none()


# =============================================================================
# CLI Commands
# =============================================================================

async def cmd_list_users(args: argparse.Namespace) -> int:
    """List all users with optional filters."""
    async with AsyncSessionLocal() as db:
        query = select(User)

        # Apply filters
        if args.admins_only:
            query = query.where(User.is_platform_admin == True)  # noqa: E712

        # Add ordering
        query = query.order_by(User.created_at.desc())

        result = await db.execute(query)
        users = result.scalars().all()

        if not users:
            print("No users found.")
            return 0

        # Print header
        print(f"\n{'='*80}")
        print(f"{'ID':<38} {'Email':<30} {'Admin':<6} {'Created':<12}")
        print(f"{'='*80}")

        for user in users:
            admin_flag = "Yes" if user.is_platform_admin else "No"
            created = user.created_at.strftime("%Y-%m-%d") if user.created_at else "N/A"
            print(f"{str(user.id):<38} {user.email:<30} {admin_flag:<6} {created:<12}")

        print(f"{'='*80}")
        print(f"Total: {len(users)} users")

        return 0


async def cmd_list_orgs(args: argparse.Namespace) -> int:
    """List all organisations with stats."""
    async with AsyncSessionLocal() as db:
        # Get all organisations with member counts
        result = await db.execute(
            select(Organization).order_by(Organization.created_at.desc())
        )
        orgs = result.scalars().all()

        if not orgs:
            print("No organisations found.")
            return 0

        # Print header
        print(f"\n{'='*100}")
        print(f"{'ID':<38} {'Name':<25} {'Slug':<20} {'Members':<8} {'Created':<12}")
        print(f"{'='*100}")

        for org in orgs:
            # Count members
            member_count = await db.execute(
                select(func.count(OrganizationMember.id)).where(
                    OrganizationMember.organization_id == org.id
                )
            )
            count = member_count.scalar() or 0
            created = org.created_at.strftime("%Y-%m-%d") if org.created_at else "N/A"
            print(f"{str(org.id):<38} {org.name[:25]:<25} {org.slug[:20]:<20} {count:<8} {created:<12}")

        print(f"{'='*100}")
        print(f"Total: {len(orgs)} organisations")

        return 0


async def cmd_list_admins(args: argparse.Namespace) -> int:
    """List all platform admins."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(User).where(User.is_platform_admin == True).order_by(User.email)  # noqa: E712
        )
        admins = result.scalars().all()

        if not admins:
            print("No platform admins found.")
            return 0

        # Print header
        print(f"\n{'='*80}")
        print(f"{'Email':<40} {'Display Name':<25} {'Last Login':<15}")
        print(f"{'='*80}")

        for admin in admins:
            display_name = admin.display_name or "N/A"
            last_login = admin.last_login_at.strftime("%Y-%m-%d") if admin.last_login_at else "Never"
            print(f"{admin.email:<40} {display_name[:25]:<25} {last_login:<15}")

        print(f"{'='*80}")
        print(f"Total: {len(admins)} platform admins")

        return 0


async def cmd_grant_admin(args: argparse.Namespace) -> int:
    """Grant platform admin to a user."""
    async with AsyncSessionLocal() as db:
        # Find user
        user = None
        if args.email:
            user = await get_user_by_email(db, args.email)
        elif args.user_id:
            try:
                user = await get_user_by_id(db, UUID(args.user_id))
            except ValueError:
                print(f"Error: Invalid UUID format: {args.user_id}")
                return 1

        if not user:
            print(f"Error: User not found.")
            return 1

        if user.is_platform_admin:
            print(f"User {user.email} is already a platform admin.")
            return 0

        # Dry run check
        if args.dry_run:
            print(f"[DRY RUN] Would grant platform admin to: {user.email}")
            return 0

        # Grant admin
        user.is_platform_admin = True
        await db.commit()

        print(f"✅ Successfully granted platform admin to: {user.email}")
        return 0


async def cmd_revoke_admin(args: argparse.Namespace) -> int:
    """Revoke platform admin from a user."""
    async with AsyncSessionLocal() as db:
        # Find user
        user = None
        if args.email:
            user = await get_user_by_email(db, args.email)
        elif args.user_id:
            try:
                user = await get_user_by_id(db, UUID(args.user_id))
            except ValueError:
                print(f"Error: Invalid UUID format: {args.user_id}")
                return 1

        if not user:
            print(f"Error: User not found.")
            return 1

        if not user.is_platform_admin:
            print(f"User {user.email} is not a platform admin.")
            return 0

        # Dry run check
        if args.dry_run:
            print(f"[DRY RUN] Would revoke platform admin from: {user.email}")
            return 0

        # Revoke admin
        user.is_platform_admin = False
        await db.commit()

        print(f"✅ Successfully revoked platform admin from: {user.email}")
        return 0


async def cmd_add_member(args: argparse.Namespace) -> int:
    """Add a user to an organisation as a member (creates user if needed)."""
    if not args.email:
        print("Error: --email is required.")
        return 1
    if not args.org_id and not args.org_slug:
        print("Error: Either --org-id or --org-slug is required.")
        return 1

    role = args.role or "admin"
    if role not in ("admin", "editor", "viewer"):
        print(f"Error: Invalid role '{role}'. Must be admin, editor, or viewer.")
        return 1

    email = args.email.strip().lower()

    async with AsyncSessionLocal() as db:
        # Find the organisation
        org = None
        if args.org_id:
            try:
                org = await get_org_by_id(db, UUID(args.org_id))
            except ValueError:
                print(f"Error: Invalid UUID format: {args.org_id}")
                return 1
        elif args.org_slug:
            org = await get_org_by_slug(db, args.org_slug)

        if not org:
            print("Error: Organisation not found.")
            # List available orgs to help
            result = await db.execute(select(Organization).order_by(Organization.name))
            orgs = result.scalars().all()
            if orgs:
                print("\nAvailable organisations:")
                for o in orgs:
                    print(f"  {o.slug:<25} {o.name:<30} (ID: {o.id})")
            return 1

        # Find or create the user
        user = await get_user_by_email(db, email)
        created_user = False

        if not user:
            if args.dry_run:
                print(f"[DRY RUN] Would create user: {email}")
                print(f"[DRY RUN] Would add to org '{org.name}' as {role}")
                return 0

            # Create user with pending google_sub (linked on first Google login)
            user = User(
                google_sub=f"pending:{email}",
                email=email,
                display_name=args.display_name or email.split('@')[0].replace('.', ' ').title(),
            )
            db.add(user)
            await db.flush()

            # Create free-tier subscription
            subscription = UserSubscription(
                user_id=user.id,
                tier="free",
                is_active=True,
            )
            db.add(subscription)
            created_user = True
            print(f"Created user: {email} (ID: {user.id})")

        # Check if already a member
        existing = await db.execute(
            select(OrganizationMember).where(
                OrganizationMember.organization_id == org.id,
                OrganizationMember.user_id == user.id,
            )
        )
        if existing.scalar_one_or_none():
            print(f"User {email} is already a member of '{org.name}'.")
            return 0

        if args.dry_run:
            print(f"[DRY RUN] Would add {email} to org '{org.name}' as {role}")
            return 0

        # Create membership
        member = OrganizationMember(
            organization_id=org.id,
            user_id=user.id,
            role=role,
        )
        db.add(member)
        await db.commit()

        if created_user:
            print(f"✅ Created user {email} and added to '{org.name}' as {role}")
            print(f"   User can now sign in with Google at the platform URL.")
        else:
            print(f"✅ Added existing user {email} to '{org.name}' as {role}")

        return 0


async def cmd_delete_user(args: argparse.Namespace) -> int:
    """Delete a user and all their data."""
    async with AsyncSessionLocal() as db:
        # Find user
        user = None
        if args.email:
            user = await get_user_by_email(db, args.email)
        elif args.user_id:
            try:
                user = await get_user_by_id(db, UUID(args.user_id))
            except ValueError:
                print(f"Error: Invalid UUID format: {args.user_id}")
                return 1

        if not user:
            print(f"Error: User not found.")
            return 1

        # Show user details
        print(f"\nUser to delete:")
        print(f"  ID: {user.id}")
        print(f"  Email: {user.email}")
        print(f"  Display Name: {user.display_name or 'N/A'}")
        print(f"  Platform Admin: {'Yes' if user.is_platform_admin else 'No'}")

        # Confirm deletion
        if not args.confirm:
            print(f"\n⚠️  This will permanently delete the user and all related data.")
            print(f"   To confirm, run with --confirm flag.")
            return 1

        # Dry run check
        if args.dry_run:
            print(f"\n[DRY RUN] Would delete user: {user.email}")
            return 0

        # Delete user (CASCADE handles related records)
        await db.delete(user)
        await db.commit()

        print(f"\n✅ Successfully deleted user: {user.email}")
        return 0


async def cmd_delete_org(args: argparse.Namespace) -> int:
    """Delete an organisation and all its data."""
    async with AsyncSessionLocal() as db:
        # Find organisation
        org = None
        if args.slug:
            org = await get_org_by_slug(db, args.slug)
        elif args.org_id:
            try:
                org = await get_org_by_id(db, UUID(args.org_id))
            except ValueError:
                print(f"Error: Invalid UUID format: {args.org_id}")
                return 1

        if not org:
            print(f"Error: Organisation not found.")
            return 1

        # Get stats
        member_count = await db.execute(
            select(func.count(OrganizationMember.id)).where(
                OrganizationMember.organization_id == org.id
            )
        )
        members = member_count.scalar() or 0

        control_count = await db.execute(
            select(func.count(ScopedControl.id)).where(
                ScopedControl.organization_id == org.id
            )
        )
        controls = control_count.scalar() or 0

        # Show organisation details
        print(f"\nOrganisation to delete:")
        print(f"  ID: {org.id}")
        print(f"  Name: {org.name}")
        print(f"  Slug: {org.slug}")
        print(f"  Members: {members}")
        print(f"  Controls: {controls}")

        # Confirm deletion
        if not args.confirm:
            print(f"\n⚠️  This will permanently delete the organisation and all related data.")
            print(f"   To confirm, run with --confirm flag.")
            return 1

        # Dry run check
        if args.dry_run:
            print(f"\n[DRY RUN] Would delete organisation: {org.name}")
            return 0

        # Delete organisation (CASCADE handles related records)
        await db.delete(org)
        await db.commit()

        print(f"\n✅ Successfully deleted organisation: {org.name}")
        return 0


async def cmd_grant_consultant(args: argparse.Namespace) -> int:
    """Grant consultant access to a user via the admin API."""
    if not args.base_url or not args.api_key:
        print("Error: --base-url and --api-key are required for API-based commands.")
        return 1

    if not args.email and not args.user_id:
        print("Error: Either --email or --user-id is required.")
        return 1

    try:
        import httpx
    except ImportError:
        print("Error: httpx is required. Install with: pip install httpx")
        return 1

    base_url = args.base_url.rstrip('/')
    headers = {
        "Authorization": f"Bearer {args.api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Resolve user ID from email if needed
        user_id = args.user_id
        if args.email and not user_id:
            resp = await client.get(
                f"{base_url}/api/admin/users",
                params={"search": args.email},
                headers=headers,
            )
            if resp.status_code != 200:
                print(f"Error: Failed to search users: {resp.status_code} {resp.text}")
                return 1
            data = resp.json()
            users = data.get("users", [])
            match = [u for u in users if u["email"].lower() == args.email.lower()]
            if not match:
                print(f"Error: User not found with email: {args.email}")
                return 1
            user_id = match[0]["id"]
            print(f"Resolved {args.email} → {user_id}")

        if args.dry_run:
            print(f"[DRY RUN] Would grant consultant access to user {user_id}")
            return 0

        # Call the grant-consultant endpoint
        body = {}
        if args.company_name:
            body["company_name"] = args.company_name
        if args.max_clients:
            body["max_clients"] = args.max_clients

        resp = await client.post(
            f"{base_url}/api/admin/users/{user_id}/grant-consultant",
            headers=headers,
            json=body if body else None,
        )

        if resp.status_code != 200:
            print(f"Error: {resp.status_code} {resp.text}")
            return 1

        result = resp.json()
        print(f"✅ {result['message']}")
        return 0


async def cmd_revoke_consultant(args: argparse.Namespace) -> int:
    """Revoke consultant access from a user via the admin API."""
    if not args.base_url or not args.api_key:
        print("Error: --base-url and --api-key are required for API-based commands.")
        return 1

    if not args.email and not args.user_id:
        print("Error: Either --email or --user-id is required.")
        return 1

    try:
        import httpx
    except ImportError:
        print("Error: httpx is required. Install with: pip install httpx")
        return 1

    base_url = args.base_url.rstrip('/')
    headers = {
        "Authorization": f"Bearer {args.api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Resolve user ID from email if needed
        user_id = args.user_id
        if args.email and not user_id:
            resp = await client.get(
                f"{base_url}/api/admin/users",
                params={"search": args.email},
                headers=headers,
            )
            if resp.status_code != 200:
                print(f"Error: Failed to search users: {resp.status_code} {resp.text}")
                return 1
            data = resp.json()
            users = data.get("users", [])
            match = [u for u in users if u["email"].lower() == args.email.lower()]
            if not match:
                print(f"Error: User not found with email: {args.email}")
                return 1
            user_id = match[0]["id"]
            print(f"Resolved {args.email} → {user_id}")

        if args.dry_run:
            print(f"[DRY RUN] Would revoke consultant access from user {user_id}")
            return 0

        resp = await client.post(
            f"{base_url}/api/admin/users/{user_id}/revoke-consultant",
            headers=headers,
        )

        if resp.status_code != 200:
            print(f"Error: {resp.status_code} {resp.text}")
            return 1

        result = resp.json()
        print(f"✅ {result['message']}")
        return 0


async def cmd_setup(args: argparse.Namespace) -> int:
    """Initial platform setup - create Default Organization."""
    async with AsyncSessionLocal() as db:
        # Check if Default Organization already exists
        result = await db.execute(
            select(Organization).where(Organization.slug == "default")
        )
        existing_org = result.scalar_one_or_none()

        if existing_org:
            print(f"✅ Default Organization already exists:")
            print(f"   ID: {existing_org.id}")
            print(f"   Name: {existing_org.name}")
            print(f"   Slug: {existing_org.slug}")
            return 0

        # Dry run check
        if args.dry_run:
            print(f"[DRY RUN] Would create Default Organization:")
            print(f"   Name: {args.name}")
            print(f"   Slug: default")
            return 0

        # Create Default Organization
        org = Organization(
            name=args.name,
            slug="default"
        )
        db.add(org)
        await db.commit()
        await db.refresh(org)

        print(f"\n{'='*60}")
        print(f"✅ Platform Setup Complete!")
        print(f"{'='*60}")
        print(f"Default Organization created:")
        print(f"   ID: {org.id}")
        print(f"   Name: {org.name}")
        print(f"   Slug: {org.slug}")
        print(f"{'='*60}")
        print(f"\nNext steps:")
        print(f"1. Sign in via Google OAuth at your frontend URL")
        print(f"2. Your user will be auto-created and linked to this org")
        print(f"3. Run 'grant-admin --email your@email.com' to make yourself admin")
        print(f"{'='*60}")

        return 0


async def cmd_seed_catalog(args: argparse.Namespace) -> int:
    """Seed or reseed the SCF catalog from JSON files."""
    # Get current stats first
    current_stats = await get_catalog_stats()

    print(f"\n{'='*50}")
    print(f"SCF Catalog Status")
    print(f"{'='*50}")
    print(f"Current Controls:            {current_stats['controls']}")
    print(f"Current Domains:             {current_stats['domains']}")
    print(f"Current Evidence Items:      {current_stats['evidence']}")
    print(f"Current Assessment Objects:  {current_stats['assessment_objectives']}")
    print(f"Catalog Version:             {current_stats['catalog_version']}")
    print(f"{'='*50}")

    if args.force:
        if not args.confirm:
            print("\n⚠️  WARNING: --force will DELETE all existing catalog data and reseed.")
            print("   This is safe for catalog data (it's read-only reference data).")
            print("   Run with --confirm to proceed.")
            return 1

        print("\nReseeding catalog (force mode)...")
        results = await reseed_catalog(force=True)
    else:
        print("\nSeeding catalog (if empty)...")
        results = await seed_catalog_if_empty()

    print(f"\n{'='*50}")
    print(f"Seeding Results")
    print(f"{'='*50}")
    for table, result in results.items():
        status = result.get('status', 'unknown')
        if status == 'seeded':
            print(f"✅ {table}: seeded {result.get('count', 0)} records")
        elif status == 'skipped':
            print(f"⏭️  {table}: skipped (already has {result.get('existing', 0)} records)")
        elif status == 'error':
            print(f"❌ {table}: error - {result.get('message', 'unknown error')}")
        else:
            print(f"❓ {table}: {status}")
    print(f"{'='*50}")

    # Show final stats
    final_stats = await get_catalog_stats()
    print(f"\n{'='*50}")
    print(f"Final Catalog Status")
    print(f"{'='*50}")
    print(f"Controls:            {final_stats['controls']}")
    print(f"Domains:             {final_stats['domains']}")
    print(f"Evidence Items:      {final_stats['evidence']}")
    print(f"Assessment Objects:  {final_stats['assessment_objectives']}")
    print(f"{'='*50}")

    return 0


async def cmd_stats(args: argparse.Namespace) -> int:
    """Show platform-wide statistics."""
    async with AsyncSessionLocal() as db:
        # Total users
        total_users = await db.execute(select(func.count(User.id)))
        users_count = total_users.scalar() or 0

        # Platform admins
        platform_admins = await db.execute(
            select(func.count(User.id)).where(User.is_platform_admin == True)  # noqa: E712
        )
        admins_count = platform_admins.scalar() or 0

        # Total organisations
        total_orgs = await db.execute(select(func.count(Organization.id)))
        orgs_count = total_orgs.scalar() or 0

        # Total controls
        total_controls = await db.execute(select(func.count(ScopedControl.id)))
        controls_count = total_controls.scalar() or 0

        # Total evidence
        total_evidence = await db.execute(select(func.count(EvidenceTracking.id)))
        evidence_count = total_evidence.scalar() or 0

        # Users active in last 30 days
        thirty_days_ago = datetime.utcnow() - timedelta(days=30)
        active_users = await db.execute(
            select(func.count(User.id)).where(User.last_login_at >= thirty_days_ago)
        )
        active_count = active_users.scalar() or 0

        # Orgs created in last 30 days
        new_orgs = await db.execute(
            select(func.count(Organization.id)).where(Organization.created_at >= thirty_days_ago)
        )
        new_orgs_count = new_orgs.scalar() or 0

        # Get catalog stats
        catalog_stats = await get_catalog_stats()

        # Print stats
        print(f"\n{'='*50}")
        print(f"Platform Statistics")
        print(f"{'='*50}")
        print(f"Total Users:              {users_count}")
        print(f"Platform Admins:          {admins_count}")
        print(f"Total Organisations:      {orgs_count}")
        print(f"Total Scoped Controls:    {controls_count}")
        print(f"Total Evidence Items:     {evidence_count}")
        print(f"{'='*50}")
        print(f"SCF Catalog (v{catalog_stats['catalog_version']})")
        print(f"{'='*50}")
        print(f"Catalog Controls:         {catalog_stats['controls']}")
        print(f"Catalog Domains:          {catalog_stats['domains']}")
        print(f"Catalog Evidence:         {catalog_stats['evidence']}")
        print(f"Assessment Objectives:    {catalog_stats['assessment_objectives']}")
        print(f"{'='*50}")
        print(f"Activity (Last 30 Days)")
        print(f"{'='*50}")
        print(f"Active Users:             {active_count}")
        print(f"New Organisations:        {new_orgs_count}")
        print(f"{'='*50}")

        return 0


# =============================================================================
# Main Entry Point
# =============================================================================

def create_parser() -> argparse.ArgumentParser:
    """Create the argument parser."""
    parser = argparse.ArgumentParser(
        prog="cli.admin",
        description="Platform Admin CLI Toolkit",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # list-users command
    list_users = subparsers.add_parser("list-users", help="List all users")
    list_users.add_argument("--admins-only", action="store_true", help="Only show platform admins")

    # list-orgs command
    subparsers.add_parser("list-orgs", help="List all organisations")

    # list-admins command
    subparsers.add_parser("list-admins", help="List all platform admins")

    # grant-admin command
    grant_admin = subparsers.add_parser("grant-admin", help="Grant platform admin to a user")
    grant_admin.add_argument("--email", help="User email address")
    grant_admin.add_argument("--user-id", help="User UUID")
    grant_admin.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")

    # revoke-admin command
    revoke_admin = subparsers.add_parser("revoke-admin", help="Revoke platform admin from a user")
    revoke_admin.add_argument("--email", help="User email address")
    revoke_admin.add_argument("--user-id", help="User UUID")
    revoke_admin.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")

    # grant-consultant command (API-based)
    grant_consultant = subparsers.add_parser("grant-consultant", help="Grant consultant access to a user (via API)")
    grant_consultant.add_argument("--email", help="User email address")
    grant_consultant.add_argument("--user-id", help="User UUID")
    grant_consultant.add_argument("--base-url", help="Platform API base URL (e.g., https://eu.scfcontrolsplatform.com)")
    grant_consultant.add_argument("--api-key", help="Platform API key")
    grant_consultant.add_argument("--company-name", help="Consultant company/firm name")
    grant_consultant.add_argument("--max-clients", type=int, help="Maximum number of clients (default: 5)")
    grant_consultant.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")

    # revoke-consultant command (API-based)
    revoke_consultant = subparsers.add_parser("revoke-consultant", help="Revoke consultant access from a user (via API)")
    revoke_consultant.add_argument("--email", help="User email address")
    revoke_consultant.add_argument("--user-id", help="User UUID")
    revoke_consultant.add_argument("--base-url", help="Platform API base URL (e.g., https://eu.scfcontrolsplatform.com)")
    revoke_consultant.add_argument("--api-key", help="Platform API key")
    revoke_consultant.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")

    # add-member command
    add_member = subparsers.add_parser("add-member", help="Add a user to an organisation (creates user if needed)")
    add_member.add_argument("--email", required=True, help="User email address")
    add_member.add_argument("--org-id", help="Organisation UUID")
    add_member.add_argument("--org-slug", help="Organisation slug")
    add_member.add_argument("--role", default="admin", choices=["admin", "editor", "viewer"], help="Member role (default: admin)")
    add_member.add_argument("--display-name", help="Display name for new user (default: derived from email)")
    add_member.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")

    # delete-user command
    delete_user = subparsers.add_parser("delete-user", help="Delete a user and all their data")
    delete_user.add_argument("--email", help="User email address")
    delete_user.add_argument("--user-id", help="User UUID")
    delete_user.add_argument("--confirm", action="store_true", help="Confirm deletion")
    delete_user.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")

    # delete-org command
    delete_org = subparsers.add_parser("delete-org", help="Delete an organisation and all its data")
    delete_org.add_argument("--slug", help="Organisation slug")
    delete_org.add_argument("--org-id", help="Organisation UUID")
    delete_org.add_argument("--confirm", action="store_true", help="Confirm deletion")
    delete_org.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")

    # stats command
    subparsers.add_parser("stats", help="Show platform-wide statistics")

    # setup command
    setup = subparsers.add_parser("setup", help="Initial platform setup - create Default Organization")
    setup.add_argument("--name", default="Default Organization", help="Name for the default organization")
    setup.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")

    # seed-catalog command
    seed_catalog = subparsers.add_parser("seed-catalog", help="Seed the SCF catalog from JSON files")
    seed_catalog.add_argument("--force", action="store_true", help="Force reseed (deletes existing catalog data)")
    seed_catalog.add_argument("--confirm", action="store_true", help="Confirm force reseed")

    return parser


def main():
    """Main entry point."""
    parser = create_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    # Map commands to handlers
    commands = {
        "list-users": cmd_list_users,
        "list-orgs": cmd_list_orgs,
        "list-admins": cmd_list_admins,
        "grant-admin": cmd_grant_admin,
        "revoke-admin": cmd_revoke_admin,
        "add-member": cmd_add_member,
        "grant-consultant": cmd_grant_consultant,
        "revoke-consultant": cmd_revoke_consultant,
        "delete-user": cmd_delete_user,
        "delete-org": cmd_delete_org,
        "stats": cmd_stats,
        "setup": cmd_setup,
        "seed-catalog": cmd_seed_catalog,
    }

    handler = commands.get(args.command)
    if not handler:
        print(f"Unknown command: {args.command}")
        return 1

    # Run the async handler
    return asyncio.run(handler(args))


if __name__ == "__main__":
    sys.exit(main())
