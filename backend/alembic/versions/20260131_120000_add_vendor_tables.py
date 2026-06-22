"""Add vendor management tables for TPRM.

Adds vendors, vendor_assessments, and vendor_certifications tables
with indices and unique constraints for Issue #58.

Revision ID: j1k2l3m4n5o6
Revises: i9j0k1l2m3n4
Create Date: 2026-01-31 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision = 'j1k2l3m4n5o6'
down_revision = 'i9j0k1l2m3n4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # Vendors table
    # -------------------------------------------------------------------------
    op.create_table(
        'vendors',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('organization_id', UUID(as_uuid=True), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),

        # Core fields
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('website', sa.String(500), nullable=True),
        sa.Column('category', sa.String(100), nullable=True),
        sa.Column('status', sa.String(30), nullable=False, server_default='prospect'),
        sa.Column('criticality', sa.String(20), nullable=False, server_default='low'),

        # Contact information
        sa.Column('contact_name', sa.String(255), nullable=True),
        sa.Column('contact_email', sa.String(255), nullable=True),
        sa.Column('contact_phone', sa.String(50), nullable=True),

        # Contract details
        sa.Column('contract_start_date', sa.Date(), nullable=True),
        sa.Column('contract_end_date', sa.Date(), nullable=True),
        sa.Column('contract_value', sa.Numeric(12, 2), nullable=True),

        # Risk scoring
        sa.Column('risk_score', sa.Integer(), nullable=True),
        sa.Column('risk_level', sa.String(20), nullable=True),
        sa.Column('data_classification', sa.String(50), nullable=True),

        # Audit timestamps and user FKs
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('created_by_user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('updated_by_user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
    )

    # Unique vendor name per organisation (case-insensitive)
    op.create_index('idx_vendors_org_name_unique', 'vendors', [sa.text('organization_id'), sa.text('LOWER(name)')], unique=True)

    # Query indices
    op.create_index('idx_vendors_org_id', 'vendors', ['organization_id'])
    op.create_index('idx_vendors_status', 'vendors', ['organization_id', 'status'])
    op.create_index('idx_vendors_criticality', 'vendors', ['organization_id', 'criticality'])
    op.create_index('idx_vendors_category', 'vendors', ['organization_id', 'category'])

    # -------------------------------------------------------------------------
    # Vendor Assessments table
    # -------------------------------------------------------------------------
    op.create_table(
        'vendor_assessments',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('vendor_id', UUID(as_uuid=True), sa.ForeignKey('vendors.id', ondelete='CASCADE'), nullable=False),

        # Assessment details
        sa.Column('assessment_type', sa.String(50), nullable=False, server_default='initial'),
        sa.Column('assessment_date', sa.Date(), nullable=False, server_default=sa.text('CURRENT_DATE')),
        sa.Column('status', sa.String(30), nullable=False, server_default='scheduled'),

        # CIA scores (1-5 scale)
        sa.Column('confidentiality_score', sa.Integer(), nullable=True),
        sa.Column('integrity_score', sa.Integer(), nullable=True),
        sa.Column('availability_score', sa.Integer(), nullable=True),

        # Findings and outcome
        sa.Column('findings', sa.Text(), nullable=True),
        sa.Column('risk_rating', sa.String(20), nullable=True),
        sa.Column('next_assessment_date', sa.Date(), nullable=True),

        # Assessor
        sa.Column('assessor_user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),

        # Audit timestamps and user FKs
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('created_by_user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('updated_by_user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
    )

    op.create_index('idx_vendor_assessments_vendor_id', 'vendor_assessments', ['vendor_id'])
    op.create_index('idx_vendor_assessments_status', 'vendor_assessments', ['status'])

    # -------------------------------------------------------------------------
    # Vendor Certifications table
    # -------------------------------------------------------------------------
    op.create_table(
        'vendor_certifications',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('vendor_id', UUID(as_uuid=True), sa.ForeignKey('vendors.id', ondelete='CASCADE'), nullable=False),

        # Certification details
        sa.Column('certification_name', sa.String(255), nullable=False),
        sa.Column('certification_body', sa.String(255), nullable=True),
        sa.Column('certificate_number', sa.String(100), nullable=True),
        sa.Column('status', sa.String(30), nullable=False, server_default='valid'),

        # Dates
        sa.Column('issue_date', sa.Date(), nullable=True),
        sa.Column('expiry_date', sa.Date(), nullable=True),

        # Scope and verification
        sa.Column('scope', sa.Text(), nullable=True),
        sa.Column('verification_url', sa.String(500), nullable=True),

        # Audit timestamps and user FKs
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('created_by_user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('updated_by_user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
    )

    op.create_index('idx_vendor_certifications_vendor_id', 'vendor_certifications', ['vendor_id'])
    op.create_index('idx_vendor_certifications_status', 'vendor_certifications', ['status'])


def downgrade() -> None:
    op.drop_table('vendor_certifications')
    op.drop_table('vendor_assessments')
    op.drop_table('vendors')
