"""Catalog-upgrade M1: unique org-scoped catalog keys.

Adds UNIQUE (organization_id, scf_id) on scoped_controls and
UNIQUE (organization_id, evidence_id) on evidence_tracking. These keys are
the reconciliation identity for the catalog-upgrade flow — per-org rows must
resolve to exactly one row per catalog entity.

Aborts (by design) if duplicate rows already exist: run
scripts/report_catalog_key_dupes.py to list them, clean up, and re-run.

Revision ID: catupg001
Revises: eng4query001
Create Date: 2026-08-20 10:00:01.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'catupg001'
down_revision = 'eng4query001'
branch_labels = None
depends_on = None


# One query per (table, key) pair: count of key groups with more than one row.
DUPLICATE_GROUP_COUNT_SQL = {
    'scoped_controls': (
        "SELECT count(*) FROM ("
        "SELECT organization_id, scf_id FROM scoped_controls "
        "GROUP BY organization_id, scf_id HAVING count(*) > 1"
        ") AS dupes"
    ),
    'evidence_tracking': (
        "SELECT count(*) FROM ("
        "SELECT organization_id, evidence_id FROM evidence_tracking "
        "GROUP BY organization_id, evidence_id HAVING count(*) > 1"
        ") AS dupes"
    ),
}


def _duplicate_group_counts(bind) -> dict:
    return {
        table: bind.execute(sa.text(query)).scalar() or 0
        for table, query in DUPLICATE_GROUP_COUNT_SQL.items()
    }


def _assert_no_duplicates(counts: dict) -> None:
    offending = {table: count for table, count in counts.items() if count > 0}
    if offending:
        detail = ', '.join(
            f"{table}: {count} duplicated key group(s)"
            for table, count in offending.items()
        )
        raise RuntimeError(
            "Cannot add catalog key unique constraints - duplicate rows exist "
            f"({detail}). Run 'python scripts/report_catalog_key_dupes.py' inside "
            "the backend container to list the offending rows, merge or delete "
            "the surplus rows, then re-run 'alembic upgrade head'."
        )


def upgrade() -> None:
    _assert_no_duplicates(_duplicate_group_counts(op.get_bind()))
    op.create_unique_constraint(
        'uq_scoped_controls_org_scf', 'scoped_controls',
        ['organization_id', 'scf_id'],
    )
    op.create_unique_constraint(
        'uq_evidence_tracking_org_evidence', 'evidence_tracking',
        ['organization_id', 'evidence_id'],
    )


def downgrade() -> None:
    op.drop_constraint('uq_evidence_tracking_org_evidence', 'evidence_tracking', type_='unique')
    op.drop_constraint('uq_scoped_controls_org_scf', 'scoped_controls', type_='unique')
