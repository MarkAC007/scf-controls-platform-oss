"""Evidence storage configs, and the file column that pins bytes to one

ISA 20260912-0930 "Bring-your-own evidence storage", Phase 1 (criteria 9 to 15).

Two structural decisions are worth reading before changing anything here.

**`organization_id` is nullable.** A null row is the *platform* store. The
catalogue workbook, catalogue-upgrade diffs and reconciliation detail blobs
belong to the platform rather than to any tenant, so a NOT NULL column would
leave those artefacts with no configuration to resolve.

**"One active config per scope" needs two partial unique indexes, not one.**
A unique index on `organization_id` does not constrain the platform rows at
all: in Postgres NULLs are distinct, so any number of rows with a null
organisation satisfy it. The second index is therefore an *expression* index
over the constant `(organization_id IS NULL)`, restricted to rows that are both
active and platform-scoped — for every such row the expression is `true`, so at
most one can exist. Neither index restricts `draft` or `retired` rows, which is
what allows a replacement config to be created and tested alongside the live
one.

This migration adds a table and a nullable column and seeds nothing. The
bundled MinIO row is seeded by Phase 4, and no database *configuration* is
touched by any of it.

Revision ID: evstorcfg1a1
Revises: intsec947a1
Create Date: 2026-09-12 09:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "evstorcfg1a1"
down_revision: Union[str, None] = "intsec947a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "evidence_storage_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        # Nullable — null means platform scope. See the module docstring.
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("bucket", sa.String(255), nullable=False),
        sa.Column("region", sa.String(64), nullable=True),
        sa.Column("endpoint_url", sa.String(500), nullable=True),
        sa.Column("public_endpoint", sa.String(500), nullable=True),
        sa.Column(
            "path_style", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("sse_mode", sa.String(20), nullable=False, server_default="none"),
        # An identifier, not a secret.
        sa.Column("access_key_id", sa.String(255), nullable=True),
        sa.Column("secret_ciphertext", sa.Text(), nullable=True),
        sa.Column("key_version", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column(
            "is_bundled", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by_label", sa.String(200), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "provider IN ('minio', 'aws_s3', 'gcs', 's3_compatible')",
            name="ck_evidence_storage_configs_provider",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'retired')",
            name="ck_evidence_storage_configs_status",
        ),
    )

    op.create_index(
        "ix_evidence_storage_configs_org",
        "evidence_storage_configs",
        ["organization_id"],
    )

    # One active config per organisation.
    op.execute(
        "CREATE UNIQUE INDEX uq_evidence_storage_configs_active_org "
        "ON evidence_storage_configs (organization_id) "
        "WHERE status = 'active' AND organization_id IS NOT NULL"
    )
    # One active platform config. The indexed expression is constant `true` for
    # every row the predicate admits, so the index permits exactly one of them.
    op.execute(
        "CREATE UNIQUE INDEX uq_evidence_storage_configs_active_platform "
        "ON evidence_storage_configs ((organization_id IS NULL)) "
        "WHERE status = 'active' AND organization_id IS NULL"
    )

    # Which store an existing file's bytes are in. NULL means legacy; resolution
    # falls back to the organisation's config and then to the environment.
    # RESTRICT, not SET NULL: see the column comment in models.py.
    op.add_column(
        "evidence_files",
        sa.Column("storage_config_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_evidence_files_storage_config_id",
        "evidence_files",
        "evidence_storage_configs",
        ["storage_config_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_evidence_files_storage_config_id",
        "evidence_files",
        ["storage_config_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_evidence_files_storage_config_id", table_name="evidence_files")
    op.drop_constraint(
        "fk_evidence_files_storage_config_id", "evidence_files", type_="foreignkey"
    )
    op.drop_column("evidence_files", "storage_config_id")

    op.execute("DROP INDEX IF EXISTS uq_evidence_storage_configs_active_platform")
    op.execute("DROP INDEX IF EXISTS uq_evidence_storage_configs_active_org")
    op.drop_index(
        "ix_evidence_storage_configs_org", table_name="evidence_storage_configs"
    )
    op.drop_table("evidence_storage_configs")
