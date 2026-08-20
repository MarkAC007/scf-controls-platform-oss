#!/usr/bin/env python3
"""
Read-only report of duplicate catalog keys in org-scoped tables.

The catalog-upgrade migration chain adds UNIQUE (organization_id, scf_id) on
scoped_controls and UNIQUE (organization_id, evidence_id) on evidence_tracking.
That migration aborts if duplicates exist. This script lists every offending
key group so an operator can decide which rows to merge or delete before
re-running the migration.

Strictly read-only: it never modifies data and always exits 0 (duplicates
found are the report, not an error).

Usage (inside the backend container):
    python scripts/report_catalog_key_dupes.py
"""
import asyncio
import os
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

DUPLICATE_KEY_QUERIES = {
    "scoped_controls (organization_id, scf_id)": """
        SELECT organization_id::text AS organization_id,
               scf_id AS duplicate_key,
               count(*) AS row_count
        FROM scoped_controls
        GROUP BY organization_id, scf_id
        HAVING count(*) > 1
        ORDER BY organization_id, scf_id
    """,
    "evidence_tracking (organization_id, evidence_id)": """
        SELECT organization_id::text AS organization_id,
               evidence_id AS duplicate_key,
               count(*) AS row_count
        FROM evidence_tracking
        GROUP BY organization_id, evidence_id
        HAVING count(*) > 1
        ORDER BY organization_id, evidence_id
    """,
}


async def report_duplicates() -> int:
    database_url = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://odin:changeme@localhost:5432/odin_scf",
    )
    engine = create_async_engine(database_url)
    total_groups = 0
    try:
        async with engine.connect() as conn:
            for label, query in DUPLICATE_KEY_QUERIES.items():
                rows = (await conn.execute(text(query))).fetchall()
                print(f"{label}: {len(rows)} duplicated key group(s)")
                for row in rows:
                    print(
                        f"  organization_id={row.organization_id} "
                        f"key={row.duplicate_key} rows={row.row_count}"
                    )
                total_groups += len(rows)
    finally:
        await engine.dispose()
    return total_groups


def main() -> None:
    total_groups = asyncio.run(report_duplicates())
    print()
    if total_groups == 0:
        print("No duplicate catalog keys found. The unique-constraint migration is safe to run.")
    else:
        print(
            f"{total_groups} duplicated key group(s) found. Merge or delete the surplus rows "
            "listed above, then re-run 'alembic upgrade head'."
        )


if __name__ == "__main__":
    main()
