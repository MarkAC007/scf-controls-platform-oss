"""Consolidate vendor assessments: merge vendor_dpsia_assessments into vendor_assessments

Vendor module consolidation (Phase 2):
- vendor_assessments becomes THE single assessment record. Adds AI-job
  tracking (job_id, started_at/completed_at, error_message, triggered_by),
  assessment inputs (data_role, services_used, client_name,
  additional_context) and report/outcome columns (rag_status, recommendation,
  executive_summary, report_markdown, report_json, research_sources,
  processing_time_ms).
- vendors gains risk provenance (risk_score_source, risk_scored_at) and the
  annual-review driver (next_review_date).
- Data-preserving backfill from vendor_dpsia_assessments: rows with a
  surviving linked_assessment_id merge their DPSIA fields into that
  vendor_assessments row; orphan rows become new vendor_assessments rows
  (keeping their original id). Vendor provenance is backfilled from the
  latest completed AI assessment.
- vendor_dpsia_assessments is then dropped.

Downgrade recreates vendor_dpsia_assessments and best-effort re-splits the
AI rows (job_id IS NOT NULL) back out, then drops the added columns.

Revision ID: qr9s0t1u2v3w
Revises: pq8r9s0t1u2v
Create Date: 2026-07-11 14:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = 'qr9s0t1u2v3w'
down_revision = 'pq8r9s0t1u2v'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. New columns on vendor_assessments (the unified assessment record)
    # ------------------------------------------------------------------
    op.add_column('vendor_assessments', sa.Column('job_id', sa.String(length=50), nullable=True))
    op.create_unique_constraint('uq_vendor_assessments_job_id', 'vendor_assessments', ['job_id'])
    op.add_column('vendor_assessments', sa.Column('started_at', sa.DateTime(), nullable=True))
    op.add_column('vendor_assessments', sa.Column('completed_at', sa.DateTime(), nullable=True))
    op.add_column('vendor_assessments', sa.Column('error_message', sa.Text(), nullable=True))
    op.add_column('vendor_assessments', sa.Column('triggered_by_user_id', UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        'fk_vendor_assessments_triggered_by_user_id',
        'vendor_assessments', 'users',
        ['triggered_by_user_id'], ['id'], ondelete='SET NULL',
    )
    op.add_column('vendor_assessments', sa.Column('data_role', sa.String(length=30), nullable=True))
    op.add_column('vendor_assessments', sa.Column('services_used', sa.Text(), nullable=True))
    op.add_column('vendor_assessments', sa.Column('client_name', sa.String(length=255), nullable=True))
    op.add_column('vendor_assessments', sa.Column('additional_context', sa.Text(), nullable=True))
    op.add_column('vendor_assessments', sa.Column('rag_status', sa.String(length=10), nullable=True))
    op.add_column('vendor_assessments', sa.Column('recommendation', sa.String(length=30), nullable=True))
    op.add_column('vendor_assessments', sa.Column('executive_summary', sa.Text(), nullable=True))
    op.add_column('vendor_assessments', sa.Column('report_markdown', sa.Text(), nullable=True))
    op.add_column('vendor_assessments', sa.Column('report_json', JSONB(), nullable=True))
    op.add_column('vendor_assessments', sa.Column('research_sources', JSONB(), nullable=True))
    op.add_column('vendor_assessments', sa.Column('processing_time_ms', sa.Integer(), nullable=True))

    # ------------------------------------------------------------------
    # 2. Risk provenance + annual review on vendors
    # ------------------------------------------------------------------
    op.add_column('vendors', sa.Column('risk_score_source', UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        'fk_vendors_risk_score_source',
        'vendors', 'vendor_assessments',
        ['risk_score_source'], ['id'], ondelete='SET NULL',
    )
    op.add_column('vendors', sa.Column('risk_scored_at', sa.DateTime(), nullable=True))
    op.add_column('vendors', sa.Column('next_review_date', sa.Date(), nullable=True))

    # ------------------------------------------------------------------
    # 3. Backfill from vendor_dpsia_assessments
    # ------------------------------------------------------------------
    # 3a. Merge DPSIA fields into linked vendor_assessments rows.
    #     One DPSIA row per linked assessment (each run created its own
    #     platform record); take the newest DPSIA row per assessment anyway.
    op.execute("""
        UPDATE vendor_assessments va SET
            job_id = d.job_id,
            status = d.status,
            started_at = d.started_at,
            completed_at = d.completed_at,
            error_message = d.error_message,
            triggered_by_user_id = d.triggered_by_user_id,
            data_role = d.data_role,
            services_used = d.services_used,
            client_name = d.client_name,
            additional_context = d.additional_context,
            rag_status = d.rag_status,
            recommendation = d.recommendation,
            executive_summary = COALESCE(d.executive_summary, va.ai_analysis),
            report_markdown = d.report_markdown,
            report_json = d.report_json,
            research_sources = d.research_sources,
            processing_time_ms = d.processing_time_ms,
            final_risk_score = COALESCE(d.risk_score, va.final_risk_score),
            risk_level = COALESCE(LOWER(d.risk_level), va.risk_level)
        FROM (
            SELECT DISTINCT ON (linked_assessment_id) *
            FROM vendor_dpsia_assessments
            WHERE linked_assessment_id IS NOT NULL
            ORDER BY linked_assessment_id, created_at DESC
        ) d
        WHERE va.id = d.linked_assessment_id
    """)

    # 3b. Orphan DPSIA rows (no surviving linked assessment) become new
    #     vendor_assessments rows, keeping their original id.
    op.execute("""
        INSERT INTO vendor_assessments (
            id, vendor_id, assessment_type, assessment_date, status,
            job_id, started_at, completed_at, error_message, triggered_by_user_id,
            data_role, services_used, client_name, additional_context,
            rag_status, recommendation, executive_summary,
            report_markdown, report_json, research_sources, processing_time_ms,
            final_risk_score, risk_level, ai_analysis, findings, risk_rating,
            created_at
        )
        SELECT
            d.id, d.vendor_id,
            CASE d.assessment_type
                WHEN 'new' THEN 'initial'
                WHEN 'annual-review' THEN 'annual'
                ELSE 'adhoc'
            END,
            COALESCE(d.completed_at::date, d.created_at::date, CURRENT_DATE),
            d.status,
            d.job_id, d.started_at, d.completed_at, d.error_message, d.triggered_by_user_id,
            d.data_role, d.services_used, d.client_name, d.additional_context,
            d.rag_status, d.recommendation, d.executive_summary,
            d.report_markdown, d.report_json, d.research_sources, d.processing_time_ms,
            d.risk_score, LOWER(d.risk_level), d.executive_summary, d.executive_summary, LOWER(d.risk_level),
            COALESCE(d.created_at, now())
        FROM vendor_dpsia_assessments d
        WHERE d.linked_assessment_id IS NULL
           OR NOT EXISTS (SELECT 1 FROM vendor_assessments va WHERE va.id = d.linked_assessment_id)
    """)

    # 3c. Annual-review date on completed AI assessments (completed + 12 months).
    op.execute("""
        UPDATE vendor_assessments
        SET next_assessment_date = (completed_at + interval '12 months')::date
        WHERE job_id IS NOT NULL
          AND status = 'completed'
          AND completed_at IS NOT NULL
          AND next_assessment_date IS NULL
    """)

    # 3d. Vendor risk provenance + next review date from the latest completed
    #     AI assessment per vendor.
    op.execute("""
        UPDATE vendors v SET
            risk_score_source = latest.id,
            risk_scored_at = latest.completed_at,
            next_review_date = (latest.completed_at + interval '12 months')::date
        FROM (
            SELECT DISTINCT ON (vendor_id) vendor_id, id, completed_at
            FROM vendor_assessments
            WHERE job_id IS NOT NULL AND status = 'completed' AND completed_at IS NOT NULL
            ORDER BY vendor_id, completed_at DESC
        ) latest
        WHERE v.id = latest.vendor_id
    """)

    # ------------------------------------------------------------------
    # 4. Drop the legacy DPSIA table (no other tables FK into it)
    # ------------------------------------------------------------------
    op.drop_index('ix_vendor_dpsia_assessments_status', table_name='vendor_dpsia_assessments')
    op.drop_index('ix_vendor_dpsia_assessments_organization_id', table_name='vendor_dpsia_assessments')
    op.drop_index('ix_vendor_dpsia_assessments_vendor_id', table_name='vendor_dpsia_assessments')
    op.drop_table('vendor_dpsia_assessments')


def downgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Recreate vendor_dpsia_assessments (original shape)
    # ------------------------------------------------------------------
    op.create_table(
        'vendor_dpsia_assessments',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('vendor_id', UUID(as_uuid=True), sa.ForeignKey('vendors.id', ondelete='CASCADE'), nullable=False),
        sa.Column('organization_id', UUID(as_uuid=True), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('job_id', sa.String(50), unique=True, nullable=False),
        sa.Column('status', sa.String(30), nullable=False, server_default='pending'),
        sa.Column('assessment_type', sa.String(30), nullable=False, server_default='new'),
        sa.Column('data_role', sa.String(30), nullable=False, server_default='Processor'),
        sa.Column('services_used', sa.Text, nullable=True),
        sa.Column('client_name', sa.String(255), nullable=True),
        sa.Column('additional_context', sa.Text, nullable=True),
        sa.Column('rag_status', sa.String(10), nullable=True),
        sa.Column('recommendation', sa.String(30), nullable=True),
        sa.Column('risk_score', sa.Integer, nullable=True),
        sa.Column('risk_level', sa.String(20), nullable=True),
        sa.Column('executive_summary', sa.Text, nullable=True),
        sa.Column('report_markdown', sa.Text, nullable=True),
        sa.Column('report_json', JSONB, nullable=True),
        sa.Column('report_docx_s3_key', sa.String(500), nullable=True),
        sa.Column('report_filename', sa.String(255), nullable=True),
        sa.Column('research_sources', JSONB, nullable=True),
        sa.Column('linked_assessment_id', UUID(as_uuid=True), sa.ForeignKey('vendor_assessments.id', ondelete='SET NULL'), nullable=True),
        sa.Column('linked_report_id', UUID(as_uuid=True), sa.ForeignKey('vendor_reports.id', ondelete='SET NULL'), nullable=True),
        sa.Column('processing_time_ms', sa.Integer, nullable=True),
        sa.Column('error_message', sa.Text, nullable=True),
        sa.Column('triggered_by_user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=False), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=False), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=False), server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_vendor_dpsia_assessments_vendor_id', 'vendor_dpsia_assessments', ['vendor_id'])
    op.create_index('ix_vendor_dpsia_assessments_organization_id', 'vendor_dpsia_assessments', ['organization_id'])
    op.create_index('ix_vendor_dpsia_assessments_status', 'vendor_dpsia_assessments', ['status'])

    # ------------------------------------------------------------------
    # 2. Best-effort re-split: every AI assessment row (job_id IS NOT NULL)
    #    gets a DPSIA row linked back to the unified assessment.
    # ------------------------------------------------------------------
    op.execute("""
        INSERT INTO vendor_dpsia_assessments (
            id, vendor_id, organization_id, job_id, status, assessment_type,
            data_role, services_used, client_name, additional_context,
            rag_status, recommendation, risk_score, risk_level,
            executive_summary, report_markdown, report_json, research_sources,
            linked_assessment_id, processing_time_ms, error_message,
            triggered_by_user_id, started_at, completed_at, created_at
        )
        SELECT
            gen_random_uuid(), va.vendor_id, v.organization_id, va.job_id, va.status,
            CASE va.assessment_type
                WHEN 'initial' THEN 'new'
                WHEN 'annual' THEN 'annual-review'
                WHEN 'periodic' THEN 'annual-review'
                ELSE 'adhoc'
            END,
            COALESCE(va.data_role, 'Processor'), va.services_used, va.client_name, va.additional_context,
            va.rag_status, va.recommendation, va.final_risk_score, va.risk_level,
            va.executive_summary, va.report_markdown, va.report_json, va.research_sources,
            va.id, va.processing_time_ms, va.error_message,
            va.triggered_by_user_id, va.started_at, va.completed_at, COALESCE(va.created_at, now())
        FROM vendor_assessments va
        JOIN vendors v ON v.id = va.vendor_id
        WHERE va.job_id IS NOT NULL
    """)

    # ------------------------------------------------------------------
    # 3. Drop the added columns (vendors first: FK into vendor_assessments)
    # ------------------------------------------------------------------
    op.drop_column('vendors', 'next_review_date')
    op.drop_column('vendors', 'risk_scored_at')
    op.drop_constraint('fk_vendors_risk_score_source', 'vendors', type_='foreignkey')
    op.drop_column('vendors', 'risk_score_source')

    op.drop_column('vendor_assessments', 'processing_time_ms')
    op.drop_column('vendor_assessments', 'research_sources')
    op.drop_column('vendor_assessments', 'report_json')
    op.drop_column('vendor_assessments', 'report_markdown')
    op.drop_column('vendor_assessments', 'executive_summary')
    op.drop_column('vendor_assessments', 'recommendation')
    op.drop_column('vendor_assessments', 'rag_status')
    op.drop_column('vendor_assessments', 'additional_context')
    op.drop_column('vendor_assessments', 'client_name')
    op.drop_column('vendor_assessments', 'services_used')
    op.drop_column('vendor_assessments', 'data_role')
    op.drop_constraint('fk_vendor_assessments_triggered_by_user_id', 'vendor_assessments', type_='foreignkey')
    op.drop_column('vendor_assessments', 'triggered_by_user_id')
    op.drop_column('vendor_assessments', 'error_message')
    op.drop_column('vendor_assessments', 'completed_at')
    op.drop_column('vendor_assessments', 'started_at')
    op.drop_constraint('uq_vendor_assessments_job_id', 'vendor_assessments', type_='unique')
    op.drop_column('vendor_assessments', 'job_id')
