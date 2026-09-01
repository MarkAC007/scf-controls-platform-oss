"""Honesty guard for the tenant export/import scope (#872).

The app-level export at ``GET /database/backup`` is a PARTIAL tenant-migration
tool, not a full backup. This test enforces that it stays HONEST about that:
every organisation-scoped table (one carrying an ``organization_id`` column)
must be classified into exactly one of the two declared sets in
``api.database_stats`` — either it is exported (``TENANT_EXPORT_TABLES``) or it
is deliberately excluded with a stated reason (``TENANT_SCOPED_EXCLUDED_TABLES``).

Add a new org-scoped model without classifying it and this test fails, forcing
the decision to be made and recorded rather than silently dropping tenant data
from an export the operator believes is complete.

Pure metadata introspection — no database required, so it runs in the ordinary
backend test job (unlike the restore tests, which need TEST_RESTORE_DATABASE_URL).
"""
from __future__ import annotations

import os
import sys

# Make ``backend/`` importable and give the settings import a harmless DSN, the
# same way tests/test_database_backup_restore.py does.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")

from models import Base  # noqa: E402
from api.database_stats import (  # noqa: E402
    TENANT_EXPORT_TABLES,
    TENANT_SCOPED_EXCLUDED_TABLES,
)


def _org_scoped_tables() -> set[str]:
    """Tables with a direct ``organization_id`` column — the objective,
    introspected definition of an organisation-scoped table."""
    return {
        name
        for name, table in Base.metadata.tables.items()
        if "organization_id" in table.columns
    }


def test_every_org_scoped_table_is_classified():
    org_scoped = _org_scoped_tables()
    classified = set(TENANT_EXPORT_TABLES) | set(TENANT_SCOPED_EXCLUDED_TABLES)
    unclassified = sorted(org_scoped - classified)
    assert not unclassified, (
        "New organisation-scoped table(s) are neither exported nor explicitly "
        "excluded. Classify each in api.database_stats: add it to "
        "TENANT_EXPORT_TABLES (and to the endpoint's serialised data) if a "
        "tenant migration must carry it, or to TENANT_SCOPED_EXCLUDED_TABLES "
        f"with a reason if not: {unclassified}"
    )


def test_export_and_excluded_sets_are_disjoint():
    overlap = sorted(set(TENANT_EXPORT_TABLES) & set(TENANT_SCOPED_EXCLUDED_TABLES))
    assert not overlap, f"tables both exported and excluded: {overlap}"


def test_excluded_entries_are_real_org_scoped_tables():
    """No stale exclusions: every excluded name is a real, org-scoped table."""
    org_scoped = _org_scoped_tables()
    stale = sorted(t for t in TENANT_SCOPED_EXCLUDED_TABLES if t not in org_scoped)
    assert not stale, (
        "TENANT_SCOPED_EXCLUDED_TABLES lists tables that are not org-scoped "
        f"(or no longer exist); remove them: {stale}"
    )


def test_excluded_reasons_are_nonempty():
    blank = sorted(t for t, why in TENANT_SCOPED_EXCLUDED_TABLES.items() if not why.strip())
    assert not blank, f"excluded tables missing a reason: {blank}"


def test_export_table_list_has_no_duplicates():
    seen = list(TENANT_EXPORT_TABLES)
    dupes = sorted({t for t in seen if seen.count(t) > 1})
    assert not dupes, f"duplicate tables in TENANT_EXPORT_TABLES: {dupes}"


def test_indirectly_scoped_exports_are_present():
    """The export must still carry the indirectly-scoped tables that have no
    organization_id column but belong to the tenant (regression guard against
    a future edit trimming them out of TENANT_EXPORT_TABLES)."""
    for t in (
        "users",
        "assignments",
        "comments",
        "comment_history",
        "system_evidence_capabilities",
    ):
        assert t in TENANT_EXPORT_TABLES, f"{t} dropped from the export set"
