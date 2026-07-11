"""Functional round-trip test for the vendor assessment consolidation migration
(20260711_140000_consolidate_vendor_assessments).

Runs the real upgrade()/downgrade() against a live PostgreSQL instance in a
throwaway schema:
  - creates the pre-migration table shapes (minimal but faithful),
  - inserts legacy-shaped rows (one DPSIA row linked to a platform
    assessment, one orphan DPSIA row),
  - runs upgrade() and asserts merge, orphan-row creation, vendor provenance
    backfill and the DPSIA table drop,
  - runs downgrade() and asserts the best-effort re-split.

Skipped automatically when no PostgreSQL is reachable (CI without a DB).
Set TEST_MIGRATION_DATABASE_URL to point at a specific instance; otherwise
the dev-demo Postgres (127.0.0.1:7796/scf_demo) is tried.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import sys
import uuid
from datetime import datetime

import pytest
import sqlalchemy as sa
from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MIGRATION_FILE = (
    pathlib.Path(__file__).resolve().parents[1]
    / "alembic" / "versions" / "20260711_140000_consolidate_vendor_assessments.py"
)

DEFAULT_DSN = (
    "postgresql+psycopg2://scfdev:"
    + os.getenv("DEMO_DB_PASSWORD", "scfdev-local")
    + "@127.0.0.1:7796/scf_demo"
)
DSN = os.getenv("TEST_MIGRATION_DATABASE_URL", DEFAULT_DSN)

SCHEMA = f"pytest_vmig_{uuid.uuid4().hex[:8]}"

# Fixed ids for assertions
ORG_ID = str(uuid.uuid4())
USER_ID = str(uuid.uuid4())
VENDOR1_ID = str(uuid.uuid4())
VENDOR2_ID = str(uuid.uuid4())
LINKED_ASSESSMENT_ID = str(uuid.uuid4())
ORPHAN_DPSIA_ID = str(uuid.uuid4())
T1 = datetime(2026, 3, 15, 10, 30, 0)
T2 = datetime(2026, 5, 1, 9, 0, 0)


def _connect_or_skip():
    try:
        engine = sa.create_engine(DSN, connect_args={"connect_timeout": 3})
        conn = engine.connect()
        return engine, conn
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"No PostgreSQL reachable for migration test: {exc}")


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("vendor_consolidation_migration", MIGRATION_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_premigration_schema(conn):
    """Minimal-but-faithful pre-migration tables in the throwaway schema."""
    conn.execute(text(f"CREATE SCHEMA {SCHEMA}"))
    conn.execute(text(f"SET search_path TO {SCHEMA}"))
    conn.execute(text("""
        CREATE TABLE users (id uuid PRIMARY KEY);
        CREATE TABLE organizations (id uuid PRIMARY KEY);
        CREATE TABLE vendors (
            id uuid PRIMARY KEY,
            organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            name varchar(255) NOT NULL,
            risk_score integer,
            risk_level varchar(20),
            created_at timestamp DEFAULT now()
        );
        CREATE TABLE vendor_assessments (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            vendor_id uuid NOT NULL REFERENCES vendors(id) ON DELETE CASCADE,
            assessment_type varchar(50) NOT NULL DEFAULT 'initial',
            assessment_date date NOT NULL,
            status varchar(30) NOT NULL DEFAULT 'scheduled',
            confidentiality_score integer,
            integrity_score integer,
            availability_score integer,
            breach_score integer,
            certification_score integer,
            cve_score integer,
            regulatory_score integer,
            data_handling_score integer,
            likelihood integer,
            impact integer,
            final_risk_score integer,
            risk_level varchar(20),
            ai_analysis text,
            inherent_risk_score integer,
            inherent_risk_level varchar(20),
            control_effectiveness_pct integer,
            findings text,
            risk_rating varchar(20),
            next_assessment_date date,
            assessor_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
            created_at timestamp DEFAULT now(),
            updated_at timestamp DEFAULT now(),
            created_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
            updated_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL
        );
        CREATE TABLE vendor_reports (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            vendor_id uuid NOT NULL REFERENCES vendors(id) ON DELETE CASCADE
        );
        CREATE TABLE vendor_dpsia_assessments (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            vendor_id uuid NOT NULL REFERENCES vendors(id) ON DELETE CASCADE,
            organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            job_id varchar(50) UNIQUE NOT NULL,
            status varchar(30) NOT NULL DEFAULT 'pending',
            assessment_type varchar(30) NOT NULL DEFAULT 'new',
            data_role varchar(30) NOT NULL DEFAULT 'Processor',
            services_used text,
            client_name varchar(255),
            additional_context text,
            rag_status varchar(10),
            recommendation varchar(30),
            risk_score integer,
            risk_level varchar(20),
            executive_summary text,
            report_markdown text,
            report_json jsonb,
            report_docx_s3_key varchar(500),
            report_filename varchar(255),
            research_sources jsonb,
            linked_assessment_id uuid REFERENCES vendor_assessments(id) ON DELETE SET NULL,
            linked_report_id uuid REFERENCES vendor_reports(id) ON DELETE SET NULL,
            processing_time_ms integer,
            error_message text,
            triggered_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
            started_at timestamp,
            completed_at timestamp,
            created_at timestamp DEFAULT now() NOT NULL
        );
        CREATE INDEX ix_vendor_dpsia_assessments_vendor_id ON vendor_dpsia_assessments (vendor_id);
        CREATE INDEX ix_vendor_dpsia_assessments_organization_id ON vendor_dpsia_assessments (organization_id);
        CREATE INDEX ix_vendor_dpsia_assessments_status ON vendor_dpsia_assessments (status);
    """))


def _seed_legacy_rows(conn):
    conn.execute(text("INSERT INTO users (id) VALUES (:u)"), {"u": USER_ID})
    conn.execute(text("INSERT INTO organizations (id) VALUES (:o)"), {"o": ORG_ID})
    conn.execute(
        text("INSERT INTO vendors (id, organization_id, name, risk_score, risk_level) "
             "VALUES (:v1, :o, 'Acme Corp', 12, 'high'), (:v2, :o, 'Globex Ltd', 20, 'critical')"),
        {"v1": VENDOR1_ID, "v2": VENDOR2_ID, "o": ORG_ID},
    )
    # Platform assessment auto-created by the legacy DPSIA task for vendor 1
    conn.execute(
        text("""
            INSERT INTO vendor_assessments (
                id, vendor_id, assessment_type, assessment_date, status,
                final_risk_score, risk_level, ai_analysis
            ) VALUES (:id, :v1, 'initial', :d, 'completed', 12, 'high', 'Old summary')
        """),
        {"id": LINKED_ASSESSMENT_ID, "v1": VENDOR1_ID, "d": T1.date()},
    )
    # DPSIA row LINKED to that assessment (the merge case)
    conn.execute(
        text("""
            INSERT INTO vendor_dpsia_assessments (
                id, vendor_id, organization_id, job_id, status, assessment_type,
                data_role, services_used, client_name, additional_context,
                rag_status, recommendation, risk_score, risk_level,
                executive_summary, report_markdown, report_json, research_sources,
                linked_assessment_id, processing_time_ms, triggered_by_user_id,
                started_at, completed_at, created_at
            ) VALUES (
                gen_random_uuid(), :v1, :o, 'dpsia-linked00001', 'completed', 'new',
                'Processor', 'CRM hosting', 'Client A', 'ctx',
                'AMBER', 'CONDITIONAL', 12, 'High',
                'Merged exec summary', '# Report MD', '{"inherentRiskScore": 16}'::jsonb,
                '["https://example.com"]'::jsonb,
                :la, 45000, :u, :t1, :t1, :t1
            )
        """),
        {"v1": VENDOR1_ID, "o": ORG_ID, "la": LINKED_ASSESSMENT_ID, "u": USER_ID, "t1": T1},
    )
    # ORPHAN DPSIA row (no linked assessment) for vendor 2
    conn.execute(
        text("""
            INSERT INTO vendor_dpsia_assessments (
                id, vendor_id, organization_id, job_id, status, assessment_type,
                data_role, services_used,
                rag_status, recommendation, risk_score, risk_level,
                executive_summary, report_markdown,
                linked_assessment_id, started_at, completed_at, created_at
            ) VALUES (
                :id, :v2, :o, 'dpsia-orphan00002', 'completed', 'annual-review',
                'Controller', 'Payroll',
                'RED', 'REJECTED', 20, 'Critical',
                'Orphan exec summary', '# Orphan MD',
                NULL, :t2, :t2, :t2
            )
        """),
        {"id": ORPHAN_DPSIA_ID, "v2": VENDOR2_ID, "o": ORG_ID, "t2": T2},
    )


@pytest.fixture(scope="module")
def migrated_conn():
    """Yields (conn, migration_module) after schema setup + seed + upgrade()."""
    from alembic.runtime.migration import MigrationContext
    from alembic.operations import Operations

    engine, conn = _connect_or_skip()
    module = _load_migration_module()
    try:
        _create_premigration_schema(conn)
        _seed_legacy_rows(conn)
        conn.commit()
        conn.execute(text(f"SET search_path TO {SCHEMA}"))

        ctx = MigrationContext.configure(
            conn, opts={"version_table_schema": SCHEMA}
        )
        with Operations.context(ctx):
            module.upgrade()
        conn.commit()
        conn.execute(text(f"SET search_path TO {SCHEMA}"))

        yield conn, module, ctx
    finally:
        conn.rollback()
        conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        conn.commit()
        conn.close()
        engine.dispose()


def _table_exists(conn, name: str) -> bool:
    return bool(conn.execute(
        text("SELECT 1 FROM information_schema.tables WHERE table_schema = :s AND table_name = :t"),
        {"s": SCHEMA, "t": name},
    ).fetchone())


def _column_exists(conn, table: str, column: str) -> bool:
    return bool(conn.execute(
        text("SELECT 1 FROM information_schema.columns "
             "WHERE table_schema = :s AND table_name = :t AND column_name = :c"),
        {"s": SCHEMA, "t": table, "c": column},
    ).fetchone())


class TestUpgrade:
    def test_linked_dpsia_row_merged_into_assessment(self, migrated_conn):
        conn, _, _ = migrated_conn
        row = conn.execute(
            text("""
                SELECT job_id, status, rag_status, recommendation, executive_summary,
                       report_markdown, final_risk_score, risk_level, data_role,
                       services_used, client_name, processing_time_ms, completed_at
                FROM vendor_assessments WHERE id = :id
            """),
            {"id": LINKED_ASSESSMENT_ID},
        ).fetchone()
        assert row is not None
        assert row.job_id == "dpsia-linked00001"
        assert row.status == "completed"
        assert row.rag_status == "AMBER"
        assert row.recommendation == "CONDITIONAL"
        assert row.executive_summary == "Merged exec summary"
        assert row.report_markdown == "# Report MD"
        assert row.final_risk_score == 12
        assert row.risk_level == "high"  # lowercased on merge
        assert row.data_role == "Processor"
        assert row.services_used == "CRM hosting"
        assert row.client_name == "Client A"
        assert row.processing_time_ms == 45000
        assert row.completed_at == T1

    def test_orphan_dpsia_row_created_as_new_assessment(self, migrated_conn):
        conn, _, _ = migrated_conn
        row = conn.execute(
            text("""
                SELECT vendor_id::text, assessment_type, assessment_date, status, job_id,
                       rag_status, final_risk_score, risk_level, executive_summary, ai_analysis
                FROM vendor_assessments WHERE id = :id
            """),
            {"id": ORPHAN_DPSIA_ID},
        ).fetchone()
        assert row is not None, "orphan DPSIA row was not backfilled into vendor_assessments"
        assert row.vendor_id == VENDOR2_ID
        assert row.assessment_type == "annual"  # 'annual-review' mapped
        assert row.assessment_date == T2.date()
        assert row.status == "completed"
        assert row.job_id == "dpsia-orphan00002"
        assert row.rag_status == "RED"
        assert row.final_risk_score == 20
        assert row.risk_level == "critical"
        assert row.executive_summary == "Orphan exec summary"
        assert row.ai_analysis == "Orphan exec summary"

    def test_next_assessment_date_set_to_completed_plus_12_months(self, migrated_conn):
        conn, _, _ = migrated_conn
        row = conn.execute(
            text("SELECT next_assessment_date FROM vendor_assessments WHERE id = :id"),
            {"id": LINKED_ASSESSMENT_ID},
        ).fetchone()
        assert str(row.next_assessment_date) == "2027-03-15"

    def test_vendor_provenance_backfilled(self, migrated_conn):
        conn, _, _ = migrated_conn
        v1 = conn.execute(
            text("SELECT risk_score_source::text, risk_scored_at, next_review_date "
                 "FROM vendors WHERE id = :id"),
            {"id": VENDOR1_ID},
        ).fetchone()
        assert v1.risk_score_source == LINKED_ASSESSMENT_ID
        assert v1.risk_scored_at == T1
        assert str(v1.next_review_date) == "2027-03-15"

        v2 = conn.execute(
            text("SELECT risk_score_source::text, next_review_date FROM vendors WHERE id = :id"),
            {"id": VENDOR2_ID},
        ).fetchone()
        assert v2.risk_score_source == ORPHAN_DPSIA_ID
        assert str(v2.next_review_date) == "2027-05-01"

    def test_dpsia_table_dropped(self, migrated_conn):
        conn, _, _ = migrated_conn
        assert not _table_exists(conn, "vendor_dpsia_assessments")

    def test_job_id_unique_constraint_exists(self, migrated_conn):
        conn, _, _ = migrated_conn
        row = conn.execute(
            text("SELECT 1 FROM information_schema.table_constraints "
                 "WHERE table_schema = :s AND table_name = 'vendor_assessments' "
                 "AND constraint_name = 'uq_vendor_assessments_job_id' AND constraint_type = 'UNIQUE'"),
            {"s": SCHEMA},
        ).fetchone()
        assert row is not None


class TestDowngrade:
    """Downgrade runs after the upgrade assertions (same module-scoped fixture,
    ordered by class declaration within the file)."""

    @pytest.fixture(scope="class")
    def downgraded_conn(self, migrated_conn):
        from alembic.operations import Operations
        conn, module, ctx = migrated_conn
        with Operations.context(ctx):
            module.downgrade()
        conn.commit()
        conn.execute(text(f"SET search_path TO {SCHEMA}"))
        return conn

    def test_dpsia_table_recreated_with_ai_rows(self, downgraded_conn):
        conn = downgraded_conn
        rows = conn.execute(
            text("SELECT job_id, linked_assessment_id::text, risk_score, organization_id::text "
                 "FROM vendor_dpsia_assessments ORDER BY job_id")
        ).fetchall()
        assert len(rows) == 2
        by_job = {r.job_id: r for r in rows}
        assert by_job["dpsia-linked00001"].linked_assessment_id == LINKED_ASSESSMENT_ID
        assert by_job["dpsia-linked00001"].risk_score == 12
        assert by_job["dpsia-orphan00002"].linked_assessment_id == ORPHAN_DPSIA_ID
        assert by_job["dpsia-orphan00002"].organization_id == ORG_ID

    def test_added_columns_dropped(self, downgraded_conn):
        conn = downgraded_conn
        for col in ("job_id", "rag_status", "report_markdown", "report_json",
                    "research_sources", "processing_time_ms", "executive_summary"):
            assert not _column_exists(conn, "vendor_assessments", col), col
        for col in ("risk_score_source", "risk_scored_at", "next_review_date"):
            assert not _column_exists(conn, "vendors", col), col
