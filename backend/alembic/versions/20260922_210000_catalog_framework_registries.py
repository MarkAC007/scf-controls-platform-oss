"""Framework registry per catalogue version: the transactional record.

Revision ID: fwreg001
Revises: orgjourney1
Create Date: 2026-09-22 21:00:00

One row per catalogue version holding ``{framework_id: {name,
focal_document_id, geography}}`` exactly as the extractor emits it.

Why a table. A framework's focal-document identifier is the publisher's stable
identity for the document, and it is what makes succession a DECLARED fact
rather than a guess. Until now the live side of an upgrade diff could only read
it from ``DATA_DIR/framework_registry.json`` — a file on a mounted volume that
no apply path ever writes and that does not exist at all on installs seeded
before this branch. With no live identifiers the declared succession tier can
never fire, so the ``framework_churn`` sanity gate blocks every real upgrade.
The file stays as a frontend cache derived from the same extraction; this table
is the record the diff reads, written in the apply transaction.

``source`` records which path wrote the row: 'seed' (first boot), 'apply' (a
catalogue upgrade) or 'backfill' (``cli.admin backfill-framework-registry`` on
an install that predates this revision).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "fwreg001"
down_revision = "orgjourney1"
branch_labels = None
depends_on = None


SOURCES = ("seed", "apply", "backfill")


def upgrade() -> None:
    op.create_table(
        "catalog_framework_registries",
        sa.Column("catalog_version", sa.String(20), primary_key=True),
        sa.Column("registry", JSONB(), nullable=False, server_default="{}"),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=False), server_default=sa.func.now()),
        # Inside create_table on purpose: downgrade's drop_table takes it with
        # the table, so up/down stay symmetric without a second statement.
        sa.CheckConstraint(
            "source IN " + str(SOURCES),
            name="ck_catalog_framework_registries_source",
        ),
    )


def downgrade() -> None:
    op.drop_table("catalog_framework_registries")
