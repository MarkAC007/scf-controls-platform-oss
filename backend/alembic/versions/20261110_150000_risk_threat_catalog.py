"""SCF Risk and Threat Catalogs as catalog tables; per-code ratings on controls.

Revision ID: riskcat001
Revises: ismsprof001
Create Date: 2026-11-10 15:00:00

Until now the "SCF risk catalogue" was a static JSON in the web client. SCF
2026.3 ships a Risk Catalog sheet (65 risks) and a Threat Catalog sheet (45
threats), and replaced the per-control "Risk Threat Summary" columns with a
Unlikely / Possible / Likely rating against every risk and threat. This
revision adds the two read-only catalog tables and two nullable JSONB columns
on scf_catalog_controls holding those ratings ({code: "likely"|"possible"}).

Schema only. Rows are written by the seeder, the catalog upgrade apply, or the
``refresh-risk-catalog`` admin command; nothing here touches organisation data
(risk_assessments is not read). Downgrade drops the tables and columns.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "riskcat001"
down_revision = "ismsprof001"
branch_labels = None
depends_on = None


def _lifecycle_columns():
    return [
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("retired_in_version", sa.String(20), nullable=True),
        sa.Column("catalog_version", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    ]


def upgrade() -> None:
    op.create_table(
        "scf_catalog_risks",
        sa.Column("code", sa.String(20), primary_key=True),
        sa.Column("grouping", sa.String(100), nullable=False),
        sa.Column("old_code", sa.String(20), nullable=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("nist_csf_function", sa.String(20), nullable=True),
        *_lifecycle_columns(),
        sa.CheckConstraint(
            "status IN ('active', 'deprecated')", name="ck_scf_catalog_risks_status"
        ),
    )
    op.create_index("ix_scf_catalog_risks_status", "scf_catalog_risks", ["status"])

    op.create_table(
        "scf_catalog_threats",
        sa.Column("code", sa.String(20), primary_key=True),
        sa.Column("grouping", sa.String(100), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        *_lifecycle_columns(),
        sa.CheckConstraint(
            "status IN ('active', 'deprecated')", name="ck_scf_catalog_threats_status"
        ),
    )
    op.create_index("ix_scf_catalog_threats_status", "scf_catalog_threats", ["status"])

    op.add_column("scf_catalog_controls", sa.Column("risk_ratings", JSONB, nullable=True))
    op.add_column("scf_catalog_controls", sa.Column("threat_ratings", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("scf_catalog_controls", "threat_ratings")
    op.drop_column("scf_catalog_controls", "risk_ratings")
    op.drop_index("ix_scf_catalog_threats_status", table_name="scf_catalog_threats")
    op.drop_table("scf_catalog_threats")
    op.drop_index("ix_scf_catalog_risks_status", table_name="scf_catalog_risks")
    op.drop_table("scf_catalog_risks")
