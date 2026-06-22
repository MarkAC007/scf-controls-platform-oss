#!/usr/bin/env python3
"""
Data Migration Script: JSON to PostgreSQL
Migrates scoped controls and evidence tracking data from JSON files to Postgres database.

Usage:
    python migrate_json_to_postgres.py [--data-dir PATH] [--dry-run]
"""
import asyncio
import json
import os
import sys
from pathlib import Path
from datetime import datetime, date
from typing import Dict, List, Any, Optional
import argparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from database import AsyncSessionLocal, engine
from models import Organization, ScopedControl, EvidenceTracking


async def load_json_files(data_dir: Path) -> Dict[str, Any]:
    """Load all scoped_controls JSON files from the data directory."""
    print(f"📂 Loading JSON files from: {data_dir}")

    json_files = list(data_dir.glob("scoped_controls*.json"))

    if not json_files:
        print("⚠️  No scoped_controls JSON files found")
        return None

    print(f"   Found {len(json_files)} JSON file(s)")

    # Load the most recent file (by filename)
    latest_file = sorted(json_files)[-1]
    print(f"   Using: {latest_file.name}")

    with open(latest_file, 'r') as f:
        data = json.load(f)

    print(f"   ✓ Loaded {len(data.get('scoped_controls', []))} scoped controls")
    print(f"   ✓ Loaded {len(data.get('evidence_tracking', {}))} evidence tracking entries")

    return data


async def ensure_organization(session: AsyncSession, org_data: Dict[str, Any]) -> Organization:
    """Ensure default organization exists, create if not."""
    print("\n🏢 Checking organization...")

    # Try to find existing organization
    result = await session.execute(select(Organization).limit(1))
    org = result.scalar_one_or_none()

    if org:
        print(f"   ✓ Found existing organization: {org.name}")
        return org

    # Create new organization
    org = Organization(
        name=org_data.get('name', 'Default Organization'),
        slug='default'
    )
    session.add(org)
    await session.flush()
    print(f"   ✓ Created organization: {org.name}")

    return org


def parse_date(date_str: Optional[str]) -> Optional[date]:
    """Parse date string to date object. Returns None if invalid or None."""
    if not date_str or date_str == 'null':
        return None
    try:
        # Parse ISO format date string (YYYY-MM-DD)
        return datetime.strptime(date_str, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None


def parse_json_field(value: Any) -> Any:
    """Parse JSON field, handling string 'null' values."""
    if value == 'null' or value is None:
        return None
    return value


async def migrate_scoped_controls(
    session: AsyncSession,
    org_id: str,
    controls: List[Dict[str, Any]],
    dry_run: bool = False
) -> tuple[int, int]:
    """Migrate scoped controls to database."""
    print(f"\n📋 Migrating {len(controls)} scoped controls...")

    created = 0
    updated = 0

    for control_data in controls:
        # Support both legacy ccf_id and new scf_id field names
        scf_id = control_data.get('scf_id') or control_data.get('ccf_id')

        # Check if control already exists
        result = await session.execute(
            select(ScopedControl).where(
                ScopedControl.organization_id == org_id,
                ScopedControl.scf_id == scf_id
            )
        )
        existing = result.scalar_one_or_none()

        # Prepare data with proper type conversion
        prepared_data = {
            'selected': control_data.get('selected', False),
            'selection_reason': control_data.get('selection_reason'),
            'implementation_status': control_data.get('implementation_status'),
            'priority': control_data.get('priority'),
            'owner': control_data.get('owner'),
            'assigned_to': control_data.get('assigned_to'),
            'maturity_level': control_data.get('maturity_level'),
            'target_date': parse_date(control_data.get('target_date')),
            'completion_date': parse_date(control_data.get('completion_date')),
            'implementation_notes': control_data.get('implementation_notes'),
            'related_documentation': parse_json_field(control_data.get('related_documentation')),
            'custom_fields': parse_json_field(control_data.get('custom_fields'))
        }

        if existing:
            # Update existing control
            for key, value in prepared_data.items():
                setattr(existing, key, value)
            updated += 1
        else:
            # Create new control
            control = ScopedControl(
                organization_id=org_id,
                scf_id=scf_id,
                **prepared_data
            )
            session.add(control)
            created += 1

        if not dry_run and (created + updated) % 50 == 0:
            print(f"   Progress: {created + updated}/{len(controls)}...")

    print(f"   ✓ Created: {created}, Updated: {updated}")
    return created, updated


async def migrate_evidence_tracking(
    session: AsyncSession,
    org_id: str,
    evidence_data: Dict[str, Dict[str, Any]],
    dry_run: bool = False
) -> tuple[int, int]:
    """Migrate evidence tracking to database."""
    print(f"\n🗂️  Migrating {len(evidence_data)} evidence tracking entries...")

    created = 0
    updated = 0

    for evidence_id, tracking_data in evidence_data.items():
        # Check if tracking already exists
        result = await session.execute(
            select(EvidenceTracking).where(
                EvidenceTracking.organization_id == org_id,
                EvidenceTracking.evidence_id == evidence_id
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            # Update existing tracking
            for key, value in tracking_data.items():
                if hasattr(existing, key):
                    setattr(existing, key, value)
            updated += 1
        else:
            # Create new tracking
            tracking = EvidenceTracking(
                organization_id=org_id,
                evidence_id=evidence_id,
                is_tracked=tracking_data.get('is_tracked'),
                method_of_collection=tracking_data.get('method_of_collection'),
                collecting_system=tracking_data.get('collecting_system'),
                owner=tracking_data.get('owner'),
                frequency=tracking_data.get('frequency'),
                comments=tracking_data.get('comments')
            )
            session.add(tracking)
            created += 1

    print(f"   ✓ Created: {created}, Updated: {updated}")
    return created, updated


async def run_migration(data_dir: Path, dry_run: bool = False):
    """Run the complete migration process."""
    print("🚀 Starting Data Migration: JSON → PostgreSQL")
    print(f"   Dry run: {dry_run}")
    print(f"   Timestamp: {datetime.now().isoformat()}")

    # Load JSON data
    json_data = await load_json_files(data_dir)
    if not json_data:
        print("\n❌ No data to migrate")
        return

    # Connect to database
    async with AsyncSessionLocal() as session:
        try:
            # Ensure organization exists
            org = await ensure_organization(
                session,
                json_data.get('organization', {})
            )

            # Migrate scoped controls
            controls_created, controls_updated = await migrate_scoped_controls(
                session,
                org.id,
                json_data.get('scoped_controls', []),
                dry_run
            )

            # Migrate evidence tracking
            evidence_created, evidence_updated = await migrate_evidence_tracking(
                session,
                org.id,
                json_data.get('evidence_tracking', {}),
                dry_run
            )

            if dry_run:
                print("\n🔍 DRY RUN - No changes committed")
                await session.rollback()
            else:
                await session.commit()
                print("\n✅ Migration completed successfully!")

            # Print summary
            print("\n📊 Migration Summary:")
            print(f"   Scoped Controls: {controls_created} created, {controls_updated} updated")
            print(f"   Evidence Tracking: {evidence_created} created, {evidence_updated} updated")
            print(f"   Total Records: {controls_created + controls_updated + evidence_created + evidence_updated}")

        except Exception as e:
            await session.rollback()
            print(f"\n❌ Migration failed: {e}")
            raise


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Migrate scoped controls from JSON to PostgreSQL'
    )
    parser.add_argument(
        '--data-dir',
        type=Path,
        default=Path('/app/data/json'),
        help='Directory containing JSON files (default: /app/data/json)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Run migration without committing changes'
    )

    args = parser.parse_args()

    # Verify data directory exists
    if not args.data_dir.exists():
        print(f"❌ Data directory not found: {args.data_dir}")
        sys.exit(1)

    # Run migration
    asyncio.run(run_migration(args.data_dir, args.dry_run))


if __name__ == '__main__':
    main()
