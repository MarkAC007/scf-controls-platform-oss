"""Framework registry: allow the 'recovered' source.

Revision ID: fwreg002
Revises: fwreg001
Create Date: 2026-09-23 09:00:00

``catalog_framework_registries.source`` records which path wrote the row. Until
now there were three: 'seed' (first boot), 'apply' (a catalogue upgrade) and
'backfill' (the operator running ``cli.admin backfill-framework-registry``).

This adds a fourth, 'recovered', for the row the platform writes for ITSELF at
stage time. The failure it exists for is the one production hit: 2026.2 was
applied under a build that did not write the registry row, so the live catalogue
carried no focal-document identifiers, the declared succession tier could never
fire, and the framework_churn gate blocked the 2026.3 upgrade with 73
"unexplained" framework removals that were nothing of the kind. The only
documented remedy was a CLI command an operator had to know to run, against the
one workbook version that would be accepted.

The platform already has what it needs: the applied run's own workbook is still
in object storage under ``catalog_import_runs.workbook_object_key``. Staging now
re-reads the registry out of it and writes this row. 'recovered' keeps that
distinguishable from an operator's 'backfill' — the provenance of a
focal-document identifier is exactly the kind of thing a later investigation
needs to be able to ask about.

Up and down are symmetric: the constraint is dropped and recreated with the
other list, so a downgrade leaves a database that refuses 'recovered' rows
exactly as it did before. The downgrade first relabels any 'recovered' row as
'backfill' — narrowing a CHECK against rows that violate it fails outright, and
an operator downgrading a schema should not have to hand-repair data first. The
registry contents are untouched; only the provenance label loses its precision,
which is the most a narrower constraint can preserve.
"""
from alembic import op


revision = "fwreg002"
down_revision = "fwreg001"
branch_labels = None
depends_on = None


CONSTRAINT = "ck_catalog_framework_registries_source"
TABLE = "catalog_framework_registries"

# The writers after this revision, and the writers before it.
SOURCES = ("seed", "apply", "backfill", "recovered")
PREVIOUS_SOURCES = ("seed", "apply", "backfill")


def _in_list(sources) -> str:
    return "source IN (" + ", ".join(f"'{s}'" for s in sources) + ")"


def upgrade() -> None:
    op.drop_constraint(CONSTRAINT, TABLE, type_="check")
    op.create_check_constraint(CONSTRAINT, TABLE, _in_list(SOURCES))


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT, TABLE, type_="check")
    # Relabel before narrowing: the pre-fwreg002 constraint would reject these
    # rows and abort the downgrade. 'backfill' is the closest surviving label —
    # both mean "written outside the apply transaction from a stored workbook".
    # A constant statement, not a formatted one: the table name is fixed and
    # static analysis (semgrep formatted-sql-query) rightly refuses f-string SQL.
    op.execute(
        "UPDATE catalog_framework_registries "
        "SET source = 'backfill' WHERE source = 'recovered'"
    )
    op.create_check_constraint(CONSTRAINT, TABLE, _in_list(PREVIOUS_SOURCES))
