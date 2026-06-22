"""Initial schema migration - captures all existing tables.

This migration creates the complete database schema for the CG SCF platform,
including both user data tables and SCF catalog tables.

Revision ID: 62bdef86f249
Revises: None
Create Date: 2026-01-19 00:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '62bdef86f249'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create all database tables."""

    # ==========================================================================
    # USER DATA TABLES
    # ==========================================================================

    # Users table
    op.create_table(
        'users',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('google_sub', sa.String(255), nullable=False),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('display_name', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=True),
        sa.Column('last_login_at', sa.DateTime(timezone=False), nullable=True),
        sa.Column('email_notifications_enabled', sa.Boolean(), nullable=True, default=True),
        sa.Column('notification_frequency', sa.String(50), nullable=True, default='immediate'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('google_sub'),
        sa.UniqueConstraint('email')
    )

    # Organizations table
    op.create_table(
        'organizations',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('slug', sa.String(100), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('slug')
    )

    # Organization members (junction table)
    op.create_table(
        'organization_members',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('role', sa.String(50), nullable=False, server_default='viewer'),
        sa.Column('joined_at', sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE')
    )

    # Systems table
    op.create_table(
        'systems',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('system_type', sa.String(50), nullable=False),
        sa.Column('category', sa.String(100), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('vendor', sa.String(255), nullable=True),
        sa.Column('status', sa.String(20), nullable=True, server_default='active'),
        sa.Column('connection_config', postgresql.JSONB(astext_type=sa.Text()), nullable=True, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=True),
        sa.Column('created_by_user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('updated_by_user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['updated_by_user_id'], ['users.id'], ondelete='SET NULL')
    )

    # Scoped controls table
    op.create_table(
        'scoped_controls',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('scf_id', sa.String(50), nullable=False),
        sa.Column('selected', sa.Boolean(), nullable=True, default=False),
        sa.Column('selection_reason', sa.Text(), nullable=True),
        sa.Column('implementation_status', sa.String(50), nullable=True),
        sa.Column('priority', sa.String(20), nullable=True),
        sa.Column('owner', sa.String(255), nullable=True),
        sa.Column('assigned_to', sa.String(255), nullable=True),
        sa.Column('maturity_level', sa.String(50), nullable=True),
        sa.Column('target_date', sa.Date(), nullable=True),
        sa.Column('completion_date', sa.Date(), nullable=True),
        sa.Column('implementation_notes', sa.Text(), nullable=True),
        sa.Column('related_documentation', sa.JSON(), nullable=True),
        sa.Column('custom_fields', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=True),
        # SCF-specific fields
        sa.Column('control_weighting', sa.Integer(), nullable=True),
        sa.Column('validation_cadence', sa.String(50), nullable=True),
        sa.Column('nist_csf_function', sa.String(20), nullable=True),
        sa.Column('control_question', sa.Text(), nullable=True),
        # PPTDF Applicability flags
        sa.Column('pptdf_people', sa.Boolean(), nullable=True, default=False),
        sa.Column('pptdf_process', sa.Boolean(), nullable=True, default=False),
        sa.Column('pptdf_technology', sa.Boolean(), nullable=True, default=False),
        sa.Column('pptdf_data', sa.Boolean(), nullable=True, default=False),
        sa.Column('pptdf_facility', sa.Boolean(), nullable=True, default=False),
        # User FK columns
        sa.Column('assigned_user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('owner_user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_by_user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('updated_by_user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['assigned_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['updated_by_user_id'], ['users.id'], ondelete='SET NULL')
    )

    # Evidence tracking table
    op.create_table(
        'evidence_tracking',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('evidence_id', sa.String(50), nullable=False),
        sa.Column('is_tracked', sa.Boolean(), nullable=True, default=False),
        sa.Column('method_of_collection', sa.Text(), nullable=True),
        sa.Column('collecting_system', sa.String(255), nullable=True),
        sa.Column('owner', sa.String(255), nullable=True),
        sa.Column('frequency', sa.String(50), nullable=True),
        sa.Column('comments', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=True),
        # User FK columns
        sa.Column('assigned_user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('owner_user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_by_user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('updated_by_user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('next_collection_date', sa.Date(), nullable=True),
        sa.Column('last_collection_date', sa.Date(), nullable=True),
        # System reference
        sa.Column('system_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['assigned_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['updated_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['system_id'], ['systems.id'], ondelete='SET NULL')
    )

    # Evidence collection tasks table
    op.create_table(
        'evidence_collection_tasks',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('evidence_tracking_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('task_type', sa.String(50), nullable=True, server_default='collection'),
        sa.Column('title', sa.String(255), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('priority', sa.String(20), nullable=True, server_default='medium'),
        sa.Column('due_date', sa.Date(), nullable=False),
        sa.Column('status', sa.String(50), nullable=True, server_default='not_started'),
        sa.Column('assigned_user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('completed_date', sa.Date(), nullable=True),
        sa.Column('completion_notes', sa.Text(), nullable=True),
        sa.Column('dependencies', postgresql.JSONB(astext_type=sa.Text()), nullable=True, server_default='[]'),
        sa.Column('attachments', postgresql.JSONB(astext_type=sa.Text()), nullable=True, server_default='[]'),
        sa.Column('auto_generated', sa.Boolean(), nullable=True, default=True),
        sa.Column('created_at', sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['evidence_tracking_id'], ['evidence_tracking.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['assigned_user_id'], ['users.id'], ondelete='SET NULL')
    )

    # Assignments table (polymorphic)
    op.create_table(
        'assignments',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('assignable_type', sa.String(50), nullable=False),
        sa.Column('assignable_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('role', sa.String(50), nullable=True, server_default='primary'),
        sa.Column('assigned_at', sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=True),
        sa.Column('assigned_by_user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['assigned_by_user_id'], ['users.id'], ondelete='SET NULL')
    )

    # Comments table (polymorphic)
    op.create_table(
        'comments',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('commentable_type', sa.String(50), nullable=False),
        sa.Column('commentable_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('parent_comment_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('mentions', postgresql.JSONB(astext_type=sa.Text()), nullable=True, server_default='[]'),
        sa.Column('is_edited', sa.Boolean(), nullable=True, default=False),
        sa.Column('edited_at', sa.DateTime(timezone=False), nullable=True),
        sa.Column('is_deleted', sa.Boolean(), nullable=True, default=False),
        sa.Column('deleted_at', sa.DateTime(timezone=False), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['parent_comment_id'], ['comments.id'], ondelete='CASCADE')
    )

    # Comment history table
    op.create_table(
        'comment_history',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('comment_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('old_content', sa.Text(), nullable=False),
        sa.Column('edited_by_user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('edited_at', sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['comment_id'], ['comments.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['edited_by_user_id'], ['users.id'], ondelete='CASCADE')
    )

    # Notifications table
    op.create_table(
        'notifications',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('type', sa.String(50), nullable=False),
        sa.Column('reference_type', sa.String(50), nullable=False),
        sa.Column('reference_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('is_read', sa.Boolean(), nullable=True, default=False),
        sa.Column('read_at', sa.DateTime(timezone=False), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE')
    )

    # System evidence capabilities (junction table)
    op.create_table(
        'system_evidence_capabilities',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('system_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('evidence_id', sa.String(50), nullable=False),
        sa.Column('capability_status', sa.String(20), nullable=True, server_default='potential'),
        sa.Column('collection_method', sa.String(50), nullable=True),
        sa.Column('confidence_level', sa.String(20), nullable=True, server_default='medium'),
        sa.Column('data_format', sa.String(50), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=True),
        sa.Column('created_by_user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('updated_by_user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['system_id'], ['systems.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['updated_by_user_id'], ['users.id'], ondelete='SET NULL')
    )

    # ==========================================================================
    # SCF CATALOG TABLES (READ-ONLY REFERENCE DATA)
    # ==========================================================================

    # SCF Catalog Controls
    op.create_table(
        'scf_catalog_controls',
        sa.Column('scf_id', sa.String(20), nullable=False),
        sa.Column('scf_domain', sa.String(100), nullable=False),
        sa.Column('control_name', sa.String(500), nullable=False),
        sa.Column('control_description', sa.Text(), nullable=False),
        sa.Column('control_question', sa.Text(), nullable=True),
        sa.Column('validation_cadence', sa.String(50), nullable=True),
        sa.Column('control_weighting', sa.Integer(), nullable=True),
        sa.Column('nist_csf_function', sa.String(20), nullable=True),
        # PPTDF Applicability
        sa.Column('pptdf_people', sa.Boolean(), nullable=True, default=False),
        sa.Column('pptdf_process', sa.Boolean(), nullable=True, default=False),
        sa.Column('pptdf_technology', sa.Boolean(), nullable=True, default=False),
        sa.Column('pptdf_data', sa.Boolean(), nullable=True, default=False),
        sa.Column('pptdf_facility', sa.Boolean(), nullable=True, default=False),
        # JSONB fields
        sa.Column('evidence_requests', postgresql.JSONB(astext_type=sa.Text()), nullable=True, server_default='[]'),
        sa.Column('framework_mappings', postgresql.JSONB(astext_type=sa.Text()), nullable=True, server_default='{}'),
        # C|P-CMM Maturity Model
        sa.Column('cmm_level_0', sa.Text(), nullable=True),
        sa.Column('cmm_level_1', sa.Text(), nullable=True),
        sa.Column('cmm_level_2', sa.Text(), nullable=True),
        sa.Column('cmm_level_3', sa.Text(), nullable=True),
        sa.Column('cmm_level_4', sa.Text(), nullable=True),
        sa.Column('cmm_level_5', sa.Text(), nullable=True),
        # Business Size Guidance
        sa.Column('biz_micro_small', sa.Text(), nullable=True),
        sa.Column('biz_small', sa.Text(), nullable=True),
        sa.Column('biz_medium', sa.Text(), nullable=True),
        sa.Column('biz_large', sa.Text(), nullable=True),
        sa.Column('biz_enterprise', sa.Text(), nullable=True),
        # SCRM Focus
        sa.Column('scrm_tier1_strategic', sa.Boolean(), nullable=True, default=False),
        sa.Column('scrm_tier2_operational', sa.Boolean(), nullable=True, default=False),
        sa.Column('scrm_tier3_tactical', sa.Boolean(), nullable=True, default=False),
        # Risk/Threat Mapping
        sa.Column('risk_codes', postgresql.JSONB(astext_type=sa.Text()), nullable=True, server_default='[]'),
        sa.Column('threat_codes', postgresql.JSONB(astext_type=sa.Text()), nullable=True, server_default='[]'),
        # Metadata
        sa.Column('catalog_version', sa.String(20), nullable=True, server_default='2025.4'),
        sa.Column('created_at', sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('scf_id')
    )

    # SCF Catalog Domains
    op.create_table(
        'scf_catalog_domains',
        sa.Column('identifier', sa.String(10), nullable=False),
        sa.Column('order', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('principle', sa.Text(), nullable=False),
        sa.Column('principle_intent', sa.Text(), nullable=True),
        sa.Column('catalog_version', sa.String(20), nullable=True, server_default='2025.4'),
        sa.Column('created_at', sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('identifier')
    )

    # SCF Catalog Evidence
    op.create_table(
        'scf_catalog_evidence',
        sa.Column('evidence_id', sa.String(20), nullable=False),
        sa.Column('area_of_focus', sa.String(200), nullable=False),
        sa.Column('artifact_title', sa.String(500), nullable=False),
        sa.Column('artifact_description', sa.Text(), nullable=True),
        sa.Column('control_mappings', postgresql.JSONB(astext_type=sa.Text()), nullable=True, server_default='[]'),
        sa.Column('catalog_version', sa.String(20), nullable=True, server_default='2025.4'),
        sa.Column('created_at', sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('evidence_id')
    )

    # SCF Catalog Assessment Objectives
    op.create_table(
        'scf_catalog_assessment_objectives',
        sa.Column('ao_id', sa.String(30), nullable=False),
        sa.Column('scf_id', sa.String(20), nullable=False),
        sa.Column('objective_text', sa.Text(), nullable=False),
        # PPTDF Applicability
        sa.Column('pptdf_people', sa.Boolean(), nullable=True, default=False),
        sa.Column('pptdf_process', sa.Boolean(), nullable=True, default=False),
        sa.Column('pptdf_technology', sa.Boolean(), nullable=True, default=False),
        sa.Column('pptdf_data', sa.Boolean(), nullable=True, default=False),
        sa.Column('pptdf_facility', sa.Boolean(), nullable=True, default=False),
        # Assessment metadata
        sa.Column('ao_origins', sa.Text(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('assessment_rigor', sa.Integer(), nullable=True),
        # Parameters
        sa.Column('scf_defined_parameters', sa.Text(), nullable=True),
        sa.Column('org_defined_parameters', sa.Text(), nullable=True),
        # Framework-specific AO mappings
        sa.Column('cmmc_level1_ao', sa.Text(), nullable=True),
        sa.Column('dhs_ztcf_ao', sa.Text(), nullable=True),
        sa.Column('nist_800_53a', sa.Text(), nullable=True),
        sa.Column('nist_800_171a', sa.Text(), nullable=True),
        sa.Column('nist_800_171a_r3', sa.Text(), nullable=True),
        sa.Column('nist_800_172a', sa.Text(), nullable=True),
        # Assessment execution
        sa.Column('asset_type', sa.String(100), nullable=True),
        sa.Column('assessment_procedure', sa.Text(), nullable=True),
        sa.Column('expected_results', sa.Text(), nullable=True),
        # Metadata
        sa.Column('catalog_version', sa.String(20), nullable=True, server_default='2025.4'),
        sa.Column('created_at', sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('ao_id')
    )

    # ==========================================================================
    # INDEXES
    # ==========================================================================

    # User data table indexes
    op.create_index('ix_users_email', 'users', ['email'])
    op.create_index('ix_users_google_sub', 'users', ['google_sub'])
    op.create_index('ix_organizations_slug', 'organizations', ['slug'])
    op.create_index('ix_organization_members_org_user', 'organization_members', ['organization_id', 'user_id'])
    op.create_index('ix_scoped_controls_org_scf', 'scoped_controls', ['organization_id', 'scf_id'])
    op.create_index('ix_evidence_tracking_org_evidence', 'evidence_tracking', ['organization_id', 'evidence_id'])
    op.create_index('ix_evidence_collection_tasks_tracking', 'evidence_collection_tasks', ['evidence_tracking_id'])
    op.create_index('ix_assignments_user', 'assignments', ['user_id'])
    op.create_index('ix_assignments_assignable', 'assignments', ['assignable_type', 'assignable_id'])
    op.create_index('ix_comments_commentable', 'comments', ['commentable_type', 'commentable_id'])
    op.create_index('ix_comments_user', 'comments', ['user_id'])
    op.create_index('ix_notifications_user', 'notifications', ['user_id'])
    op.create_index('ix_notifications_user_unread', 'notifications', ['user_id', 'is_read'])
    op.create_index('ix_systems_org', 'systems', ['organization_id'])
    op.create_index('ix_system_evidence_capabilities_system', 'system_evidence_capabilities', ['system_id'])

    # Catalog table indexes
    op.create_index('ix_scf_catalog_controls_domain', 'scf_catalog_controls', ['scf_domain'])
    op.create_index('ix_scf_catalog_domains_order', 'scf_catalog_domains', ['order'])
    op.create_index('ix_scf_catalog_assessment_objectives_scf', 'scf_catalog_assessment_objectives', ['scf_id'])

    # ==========================================================================
    # SEED DATA - Default organization for fresh deployments
    # ==========================================================================
    # Insert default organization to ensure app is usable immediately
    # Additional organizations will be created via multi-tenancy features later
    op.execute(
        """
        INSERT INTO organizations (id, name, slug, created_at, updated_at)
        VALUES (
            gen_random_uuid(),
            'Default Organization',
            'default',
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP
        )
        ON CONFLICT (slug) DO NOTHING;
        """
    )


def downgrade() -> None:
    """Drop all database tables in reverse order."""

    # Drop indexes first
    op.drop_index('ix_scf_catalog_assessment_objectives_scf', table_name='scf_catalog_assessment_objectives')
    op.drop_index('ix_scf_catalog_domains_order', table_name='scf_catalog_domains')
    op.drop_index('ix_scf_catalog_controls_domain', table_name='scf_catalog_controls')
    op.drop_index('ix_system_evidence_capabilities_system', table_name='system_evidence_capabilities')
    op.drop_index('ix_systems_org', table_name='systems')
    op.drop_index('ix_notifications_user_unread', table_name='notifications')
    op.drop_index('ix_notifications_user', table_name='notifications')
    op.drop_index('ix_comments_user', table_name='comments')
    op.drop_index('ix_comments_commentable', table_name='comments')
    op.drop_index('ix_assignments_assignable', table_name='assignments')
    op.drop_index('ix_assignments_user', table_name='assignments')
    op.drop_index('ix_evidence_collection_tasks_tracking', table_name='evidence_collection_tasks')
    op.drop_index('ix_evidence_tracking_org_evidence', table_name='evidence_tracking')
    op.drop_index('ix_scoped_controls_org_scf', table_name='scoped_controls')
    op.drop_index('ix_organization_members_org_user', table_name='organization_members')
    op.drop_index('ix_organizations_slug', table_name='organizations')
    op.drop_index('ix_users_google_sub', table_name='users')
    op.drop_index('ix_users_email', table_name='users')

    # Drop catalog tables
    op.drop_table('scf_catalog_assessment_objectives')
    op.drop_table('scf_catalog_evidence')
    op.drop_table('scf_catalog_domains')
    op.drop_table('scf_catalog_controls')

    # Drop user data tables (reverse dependency order)
    op.drop_table('system_evidence_capabilities')
    op.drop_table('notifications')
    op.drop_table('comment_history')
    op.drop_table('comments')
    op.drop_table('assignments')
    op.drop_table('evidence_collection_tasks')
    op.drop_table('evidence_tracking')
    op.drop_table('scoped_controls')
    op.drop_table('systems')
    op.drop_table('organization_members')
    op.drop_table('organizations')
    op.drop_table('users')
