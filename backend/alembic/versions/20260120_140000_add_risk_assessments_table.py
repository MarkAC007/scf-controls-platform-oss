"""Add Risk Assessments table for risk management.

This migration creates the risk_assessments table which tracks organisation-scoped
risk assessments using SCF-aligned risk codes (e.g., R-AC-1, R-AM-2).

Key features:
- 5x5 likelihood/impact matrix for both inherent and residual risk
- Treatment workflow (identified -> analysed -> treating -> treated -> monitoring)
- Ownership and review date tracking
- Unique constraint per (organization_id, risk_code)

Risk Level Calculation:
- Score = Likelihood × Impact (1-25)
- Low: 1-4 (Green)
- Medium: 5-9 (Yellow)
- High: 10-16 (Orange)
- Critical: 17-25 (Red)

Revision ID: c3d4e5f6g7h8
Revises: b2c3d4e5f6g7
Create Date: 2026-01-20 14:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6g7h8'
down_revision: Union[str, None] = 'b2c3d4e5f6g7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create risk_assessments table with constraints and indexes."""

    # ==========================================================================
    # RISK ASSESSMENTS TABLE
    # ==========================================================================
    op.create_table(
        'risk_assessments',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('risk_code', sa.String(20), nullable=False),

        # Inherent risk scores (1-5 scale)
        sa.Column('likelihood', sa.Integer(), nullable=True),
        sa.Column('impact', sa.Integer(), nullable=True),

        # Residual risk scores (1-5 scale)
        sa.Column('residual_likelihood', sa.Integer(), nullable=True),
        sa.Column('residual_impact', sa.Integer(), nullable=True),

        # Treatment workflow
        sa.Column('treatment_status', sa.String(30), nullable=False, server_default='identified'),
        sa.Column('treatment_plan', sa.Text(), nullable=True),
        sa.Column('treatment_due_date', sa.Date(), nullable=True),

        # Ownership
        sa.Column('owner_user_id', postgresql.UUID(as_uuid=True), nullable=True),

        # Review tracking
        sa.Column('next_review_date', sa.Date(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),

        # Audit timestamps
        sa.Column('created_at', sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=True),
        sa.Column('created_by_user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('updated_by_user_id', postgresql.UUID(as_uuid=True), nullable=True),

        # Primary key
        sa.PrimaryKeyConstraint('id'),

        # Foreign keys
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['updated_by_user_id'], ['users.id'], ondelete='SET NULL'),

        # Unique constraint: one assessment per risk code per org
        sa.UniqueConstraint('organization_id', 'risk_code', name='uq_risk_assessments_org_risk_code')
    )

    # ==========================================================================
    # INDEXES
    # ==========================================================================

    # Index on organization_id for fast org-scoped queries
    op.create_index(
        'ix_risk_assessments_org_id',
        'risk_assessments',
        ['organization_id']
    )

    # Index on treatment_status for filtering by status
    op.create_index(
        'ix_risk_assessments_treatment_status',
        'risk_assessments',
        ['treatment_status']
    )

    # Composite index for org + status queries (dashboard filtering)
    op.create_index(
        'ix_risk_assessments_org_status',
        'risk_assessments',
        ['organization_id', 'treatment_status']
    )

    # Index on risk_code for finding assessments by risk code
    op.create_index(
        'ix_risk_assessments_risk_code',
        'risk_assessments',
        ['risk_code']
    )

    # Index on owner for finding risks by owner
    op.create_index(
        'ix_risk_assessments_owner',
        'risk_assessments',
        ['owner_user_id']
    )

    # Index on next_review_date for finding overdue reviews
    op.create_index(
        'ix_risk_assessments_review_date',
        'risk_assessments',
        ['next_review_date']
    )

    # ==========================================================================
    # CHECK CONSTRAINTS
    # ==========================================================================

    # Validate likelihood values (1-5)
    op.execute("""
        ALTER TABLE risk_assessments
        ADD CONSTRAINT ck_risk_assessments_likelihood
        CHECK (likelihood IS NULL OR (likelihood >= 1 AND likelihood <= 5))
    """)

    # Validate impact values (1-5)
    op.execute("""
        ALTER TABLE risk_assessments
        ADD CONSTRAINT ck_risk_assessments_impact
        CHECK (impact IS NULL OR (impact >= 1 AND impact <= 5))
    """)

    # Validate residual_likelihood values (1-5)
    op.execute("""
        ALTER TABLE risk_assessments
        ADD CONSTRAINT ck_risk_assessments_residual_likelihood
        CHECK (residual_likelihood IS NULL OR (residual_likelihood >= 1 AND residual_likelihood <= 5))
    """)

    # Validate residual_impact values (1-5)
    op.execute("""
        ALTER TABLE risk_assessments
        ADD CONSTRAINT ck_risk_assessments_residual_impact
        CHECK (residual_impact IS NULL OR (residual_impact >= 1 AND residual_impact <= 5))
    """)

    # Validate treatment_status values
    op.execute("""
        ALTER TABLE risk_assessments
        ADD CONSTRAINT ck_risk_assessments_treatment_status
        CHECK (treatment_status IN ('identified', 'analysed', 'treating', 'treated', 'accepted', 'monitoring'))
    """)


def downgrade() -> None:
    """Drop risk_assessments table and all related objects."""

    # Drop CHECK constraints first
    op.execute("""
        ALTER TABLE risk_assessments
        DROP CONSTRAINT IF EXISTS ck_risk_assessments_treatment_status
    """)

    op.execute("""
        ALTER TABLE risk_assessments
        DROP CONSTRAINT IF EXISTS ck_risk_assessments_residual_impact
    """)

    op.execute("""
        ALTER TABLE risk_assessments
        DROP CONSTRAINT IF EXISTS ck_risk_assessments_residual_likelihood
    """)

    op.execute("""
        ALTER TABLE risk_assessments
        DROP CONSTRAINT IF EXISTS ck_risk_assessments_impact
    """)

    op.execute("""
        ALTER TABLE risk_assessments
        DROP CONSTRAINT IF EXISTS ck_risk_assessments_likelihood
    """)

    # Drop indexes
    op.drop_index('ix_risk_assessments_review_date', table_name='risk_assessments')
    op.drop_index('ix_risk_assessments_owner', table_name='risk_assessments')
    op.drop_index('ix_risk_assessments_risk_code', table_name='risk_assessments')
    op.drop_index('ix_risk_assessments_org_status', table_name='risk_assessments')
    op.drop_index('ix_risk_assessments_treatment_status', table_name='risk_assessments')
    op.drop_index('ix_risk_assessments_org_id', table_name='risk_assessments')

    # Drop table
    op.drop_table('risk_assessments')
