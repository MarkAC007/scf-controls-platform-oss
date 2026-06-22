"""
Data Migration Script - Migrate existing text-based owner/assigned_to fields to user FK relationships.

This script:
1. Finds all scoped_controls and evidence_tracking records with owner/assigned_to text values
2. Attempts to match text values to existing users by email
3. Updates the new user FK columns
4. Reports results

Run with: python backend/scripts/migrate_text_to_user_assignments.py
"""
import asyncio
import sys
import os
import re
from sqlalchemy import select

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from database import AsyncSessionLocal
from models import User, ScopedControl, EvidenceTracking


def extract_email(text: str) -> str | None:
    """Extract email address from text using regex."""
    if not text:
        return None

    # Try to extract email pattern
    email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    match = re.search(email_pattern, text)

    if match:
        return match.group(0).lower()

    # If text looks like an email itself
    if '@' in text and '.' in text:
        return text.strip().lower()

    return None


async def migrate_text_to_users(dry_run: bool = False):
    """Migrate text-based assignments to user FK relationships."""
    print("="*80)
    print("Data Migration: Text-based assignments → User FK relationships")
    print("="*80)

    if dry_run:
        print("DRY RUN MODE - No changes will be committed")

    async with AsyncSessionLocal() as db:
        # Get all users for lookup
        result = await db.execute(select(User))
        all_users = result.scalars().all()

        # Create email → user_id mapping
        email_to_user_id = {user.email.lower(): user.id for user in all_users}
        print(f"\nFound {len(all_users)} users in database")
        print(f"Emails: {', '.join(sorted(email_to_user_id.keys()))}")

        # Statistics
        stats = {
            'controls_total': 0,
            'controls_owner_matched': 0,
            'controls_assigned_matched': 0,
            'controls_owner_no_match': 0,
            'controls_assigned_no_match': 0,
            'evidence_total': 0,
            'evidence_owner_matched': 0,
            'evidence_assigned_matched': 0,
            'evidence_owner_no_match': 0,
            'evidence_assigned_no_match': 0
        }

        # Migrate Scoped Controls
        print("\n" + "="*80)
        print("Migrating Scoped Controls...")
        print("="*80)

        result = await db.execute(select(ScopedControl))
        controls = result.scalars().all()
        stats['controls_total'] = len(controls)

        for control in controls:
            print(f"\nControl {control.scf_id} (ID: {control.id})")

            # Migrate owner
            if control.owner and not control.owner_user_id:
                email = extract_email(control.owner)
                print(f"  Owner text: '{control.owner}' → Email: {email}")

                if email and email in email_to_user_id:
                    control.owner_user_id = email_to_user_id[email]
                    stats['controls_owner_matched'] += 1
                    print(f"  ✓ Matched owner to user {email}")
                else:
                    stats['controls_owner_no_match'] += 1
                    print(f"  ✗ No user match for owner")

            # Migrate assigned_to
            if control.assigned_to and not control.assigned_user_id:
                email = extract_email(control.assigned_to)
                print(f"  Assigned to text: '{control.assigned_to}' → Email: {email}")

                if email and email in email_to_user_id:
                    control.assigned_user_id = email_to_user_id[email]
                    stats['controls_assigned_matched'] += 1
                    print(f"  ✓ Matched assigned_to to user {email}")
                else:
                    stats['controls_assigned_no_match'] += 1
                    print(f"  ✗ No user match for assigned_to")

        # Migrate Evidence Tracking
        print("\n" + "="*80)
        print("Migrating Evidence Tracking...")
        print("="*80)

        result = await db.execute(select(EvidenceTracking))
        evidence_records = result.scalars().all()
        stats['evidence_total'] = len(evidence_records)

        for evidence in evidence_records:
            print(f"\nEvidence {evidence.evidence_id} (ID: {evidence.id})")

            # Migrate owner
            if evidence.owner and not evidence.owner_user_id:
                email = extract_email(evidence.owner)
                print(f"  Owner text: '{evidence.owner}' → Email: {email}")

                if email and email in email_to_user_id:
                    evidence.owner_user_id = email_to_user_id[email]
                    stats['evidence_owner_matched'] += 1
                    print(f"  ✓ Matched owner to user {email}")
                else:
                    stats['evidence_owner_no_match'] += 1
                    print(f"  ✗ No user match for owner")

        # Commit or rollback
        if not dry_run:
            await db.commit()
            print("\n✓ Changes committed to database")
        else:
            await db.rollback()
            print("\n⚠ DRY RUN - Changes rolled back")

        # Print statistics
        print("\n" + "="*80)
        print("MIGRATION SUMMARY")
        print("="*80)
        print(f"\nScoped Controls ({stats['controls_total']} total):")
        print(f"  Owner field:       {stats['controls_owner_matched']} matched, {stats['controls_owner_no_match']} no match")
        print(f"  Assigned-to field: {stats['controls_assigned_matched']} matched, {stats['controls_assigned_no_match']} no match")
        print(f"\nEvidence Tracking ({stats['evidence_total']} total):")
        print(f"  Owner field:       {stats['evidence_owner_matched']} matched, {stats['evidence_owner_no_match']} no match")

        total_matched = (stats['controls_owner_matched'] + stats['controls_assigned_matched'] +
                        stats['evidence_owner_matched'])
        total_no_match = (stats['controls_owner_no_match'] + stats['controls_assigned_no_match'] +
                         stats['evidence_owner_no_match'])

        print(f"\nTOTAL: {total_matched} matched, {total_no_match} no match")

        if total_no_match > 0:
            print("\n⚠ Note: Records with no match will retain their text values.")
            print("   Users can be matched once they log in with Google.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Migrate text-based assignments to user FK relationships")
    parser.add_argument('--dry-run', action='store_true', help="Run without committing changes")
    args = parser.parse_args()

    asyncio.run(migrate_text_to_users(dry_run=args.dry_run))
