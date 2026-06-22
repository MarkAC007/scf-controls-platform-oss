-- Initial database setup for CG SCF
-- This script runs automatically when the postgres container is first created

-- Create UUID extension for generating UUIDs (fallback for older PostgreSQL)
-- PostgreSQL 13+ has gen_random_uuid() built-in which we prefer
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Create organizations table
-- Using gen_random_uuid() which is built-in to PostgreSQL 13+
CREATE TABLE IF NOT EXISTS organizations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(100) UNIQUE NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create scoped_controls table
CREATE TABLE IF NOT EXISTS scoped_controls (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    scf_id VARCHAR(50) NOT NULL,
    selected BOOLEAN DEFAULT false,
    selection_reason TEXT,
    implementation_status VARCHAR(50),
    priority VARCHAR(20),
    owner VARCHAR(255),
    assigned_to VARCHAR(255),
    maturity_level VARCHAR(50),
    target_date DATE,
    completion_date DATE,
    implementation_notes TEXT,
    related_documentation JSONB,
    custom_fields JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(organization_id, scf_id)
);

-- Create evidence_tracking table
CREATE TABLE IF NOT EXISTS evidence_tracking (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    evidence_id VARCHAR(50) NOT NULL,
    is_tracked BOOLEAN DEFAULT false,
    method_of_collection TEXT,
    collecting_system VARCHAR(255),
    owner VARCHAR(255),
    frequency VARCHAR(50),
    comments TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(organization_id, evidence_id)
);

-- ============================================
-- USER PERSISTENCE FOUNDATION
-- ============================================

-- Create users table
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    google_sub VARCHAR(255) UNIQUE NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    display_name VARCHAR(255),
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    last_login_at TIMESTAMP WITHOUT TIME ZONE,
    email_notifications_enabled BOOLEAN DEFAULT TRUE,
    notification_frequency VARCHAR(50) DEFAULT 'immediate'
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_google_sub ON users(google_sub);

-- Create organization_members table
CREATE TABLE IF NOT EXISTS organization_members (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role VARCHAR(50) NOT NULL DEFAULT 'viewer',
    joined_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(organization_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_org_members_org ON organization_members(organization_id);
CREATE INDEX IF NOT EXISTS idx_org_members_user ON organization_members(user_id);

-- ============================================
-- ASSIGNMENTS SYSTEM
-- ============================================

-- Create assignments table (polymorphic - works for controls, evidence, or tasks)
CREATE TABLE IF NOT EXISTS assignments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    assignable_type VARCHAR(50) NOT NULL, -- 'control', 'evidence', or 'task'
    assignable_id UUID NOT NULL,          -- references scoped_controls.id, evidence_tracking.id, or evidence_collection_tasks.id
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role VARCHAR(50) DEFAULT 'primary',   -- 'primary' or 'collaborator'
    assigned_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    assigned_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_assignments_assignable ON assignments(assignable_type, assignable_id);
CREATE INDEX IF NOT EXISTS idx_assignments_user ON assignments(user_id);
CREATE INDEX IF NOT EXISTS idx_assignments_assigned_by ON assignments(assigned_by_user_id);

-- Add user FK columns to existing tables (nullable for backward compatibility)
ALTER TABLE scoped_controls
    ADD COLUMN IF NOT EXISTS assigned_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS owner_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS created_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS updated_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    -- SCF 2025.4 extended fields (migration 011)
    ADD COLUMN IF NOT EXISTS control_weighting INTEGER,
    ADD COLUMN IF NOT EXISTS validation_cadence VARCHAR(50),
    ADD COLUMN IF NOT EXISTS nist_csf_function VARCHAR(20),
    ADD COLUMN IF NOT EXISTS control_question TEXT,
    ADD COLUMN IF NOT EXISTS pptdf_people BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS pptdf_process BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS pptdf_technology BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS pptdf_data BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS pptdf_facility BOOLEAN DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_scoped_controls_assigned_user ON scoped_controls(assigned_user_id);
CREATE INDEX IF NOT EXISTS idx_scoped_controls_owner_user ON scoped_controls(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_scoped_controls_created_by ON scoped_controls(created_by_user_id);
CREATE INDEX IF NOT EXISTS idx_scoped_controls_updated_by ON scoped_controls(updated_by_user_id);

ALTER TABLE evidence_tracking
    ADD COLUMN IF NOT EXISTS assigned_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS owner_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS created_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS updated_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS next_collection_date DATE,
    ADD COLUMN IF NOT EXISTS last_collection_date DATE;

CREATE INDEX IF NOT EXISTS idx_evidence_tracking_assigned_user ON evidence_tracking(assigned_user_id);
CREATE INDEX IF NOT EXISTS idx_evidence_tracking_owner_user ON evidence_tracking(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_evidence_tracking_created_by ON evidence_tracking(created_by_user_id);
CREATE INDEX IF NOT EXISTS idx_evidence_tracking_updated_by ON evidence_tracking(updated_by_user_id);
CREATE INDEX IF NOT EXISTS idx_evidence_tracking_next_collection ON evidence_tracking(next_collection_date);

-- ============================================
-- COMMENTS SYSTEM
-- ============================================

-- Create comments table (polymorphic - works for controls, evidence, or tasks)
CREATE TABLE IF NOT EXISTS comments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    commentable_type VARCHAR(50) NOT NULL, -- 'control', 'evidence', or 'task'
    commentable_id UUID NOT NULL,          -- references scoped_controls.id, evidence_tracking.id, or evidence_collection_tasks.id
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    parent_comment_id UUID REFERENCES comments(id) ON DELETE CASCADE, -- For threading/replies
    content TEXT NOT NULL,
    mentions JSONB DEFAULT '[]'::jsonb,    -- array of user IDs mentioned with @
    is_edited BOOLEAN DEFAULT FALSE,
    edited_at TIMESTAMP WITHOUT TIME ZONE,
    is_deleted BOOLEAN DEFAULT FALSE,      -- soft delete
    deleted_at TIMESTAMP WITHOUT TIME ZONE,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_comments_commentable ON comments(commentable_type, commentable_id);
CREATE INDEX IF NOT EXISTS idx_comments_user ON comments(user_id);
CREATE INDEX IF NOT EXISTS idx_comments_parent ON comments(parent_comment_id);
CREATE INDEX IF NOT EXISTS idx_comments_created_at ON comments(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_comments_not_deleted ON comments(is_deleted) WHERE is_deleted = FALSE;

-- Create comment_history table for audit trail
CREATE TABLE IF NOT EXISTS comment_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    comment_id UUID NOT NULL REFERENCES comments(id) ON DELETE CASCADE,
    old_content TEXT NOT NULL,
    edited_by_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    edited_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_comment_history_comment ON comment_history(comment_id);
CREATE INDEX IF NOT EXISTS idx_comment_history_edited_at ON comment_history(edited_at DESC);

-- ============================================
-- EVIDENCE COLLECTION TASKS
-- ============================================

-- Create evidence_collection_tasks table (with all enhanced fields)
CREATE TABLE IF NOT EXISTS evidence_collection_tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    evidence_tracking_id UUID NOT NULL REFERENCES evidence_tracking(id) ON DELETE CASCADE,
    -- Task classification (from migration 002)
    task_type VARCHAR(50) DEFAULT 'collection', -- 'feasibility', 'setup', 'collection', 'review', 'documentation', 'issue'
    title VARCHAR(255),
    description TEXT,
    priority VARCHAR(20) DEFAULT 'medium', -- 'low', 'medium', 'high', 'critical'
    -- Scheduling and assignment
    due_date DATE NOT NULL,
    status VARCHAR(50) DEFAULT 'not_started', -- 'not_started', 'in_progress', 'completed'
    assigned_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    -- Completion tracking
    completed_date DATE,
    completion_notes TEXT,
    -- Metadata (from migration 002)
    dependencies JSONB DEFAULT '[]'::jsonb, -- Array of task IDs that must complete first
    attachments JSONB DEFAULT '[]'::jsonb, -- Array of {url, name, type}
    auto_generated BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_evidence_tasks_evidence ON evidence_collection_tasks(evidence_tracking_id);
CREATE INDEX IF NOT EXISTS idx_evidence_tasks_assigned_user ON evidence_collection_tasks(assigned_user_id);
CREATE INDEX IF NOT EXISTS idx_evidence_tasks_status ON evidence_collection_tasks(status);
CREATE INDEX IF NOT EXISTS idx_evidence_tasks_due_date ON evidence_collection_tasks(due_date);
CREATE INDEX IF NOT EXISTS idx_evidence_tasks_type ON evidence_collection_tasks(task_type);
CREATE INDEX IF NOT EXISTS idx_evidence_tasks_priority ON evidence_collection_tasks(priority);

-- Add CHECK constraints for valid values
ALTER TABLE evidence_collection_tasks
    DROP CONSTRAINT IF EXISTS check_task_type,
    ADD CONSTRAINT check_task_type
        CHECK (task_type IN ('feasibility', 'setup', 'collection', 'review', 'documentation', 'issue'));

ALTER TABLE evidence_collection_tasks
    DROP CONSTRAINT IF EXISTS check_priority,
    ADD CONSTRAINT check_priority
        CHECK (priority IN ('low', 'medium', 'high', 'critical'));

-- ============================================
-- SYSTEMS REGISTRY
-- ============================================

-- Create systems table for tracking tools and platforms that provide evidence
CREATE TABLE IF NOT EXISTS systems (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,

    -- Core fields
    name VARCHAR(255) NOT NULL,
    system_type VARCHAR(50) NOT NULL,  -- cloud_provider, identity_provider, ticketing, logging, security_tool, code_repository, document_management, custom
    category VARCHAR(100),              -- Optional grouping (e.g., "Infrastructure", "Security")
    description TEXT,
    vendor VARCHAR(255),                -- e.g., "Amazon Web Services", "Okta Inc."
    status VARCHAR(20) DEFAULT 'active', -- active, inactive, deprecated

    -- Connection configuration (for future integrations)
    connection_config JSONB DEFAULT '{}',

    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Audit user FKs
    created_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    updated_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL
);

-- Unique system name per organization
ALTER TABLE systems
    ADD CONSTRAINT uq_systems_org_name UNIQUE (organization_id, name);

-- Valid system_type values
ALTER TABLE systems
    ADD CONSTRAINT check_system_type
        CHECK (system_type IN (
            'cloud_provider',
            'identity_provider',
            'ticketing',
            'logging',
            'security_tool',
            'code_repository',
            'document_management',
            'custom'
        ));

-- Valid status values
ALTER TABLE systems
    ADD CONSTRAINT check_system_status
        CHECK (status IN ('active', 'inactive', 'deprecated'));

CREATE INDEX IF NOT EXISTS idx_systems_organization_id ON systems(organization_id);
CREATE INDEX IF NOT EXISTS idx_systems_type ON systems(system_type);
CREATE INDEX IF NOT EXISTS idx_systems_status ON systems(status);
CREATE INDEX IF NOT EXISTS idx_systems_org_type_status ON systems(organization_id, system_type, status);

-- Add system_id to evidence_tracking (migration 011 - must be after systems table creation)
ALTER TABLE evidence_tracking
    ADD COLUMN IF NOT EXISTS system_id UUID REFERENCES systems(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_evidence_tracking_system ON evidence_tracking(system_id);

-- ============================================
-- NOTIFICATIONS
-- ============================================

-- Create notifications table
CREATE TABLE IF NOT EXISTS notifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type VARCHAR(50) NOT NULL,             -- 'assignment', 'mention', 'task_due', 'task_overdue'
    reference_type VARCHAR(50) NOT NULL,   -- 'control', 'evidence', 'comment', 'task'
    reference_id UUID NOT NULL,
    message TEXT NOT NULL,
    is_read BOOLEAN DEFAULT FALSE,
    read_at TIMESTAMP WITHOUT TIME ZONE,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id);
CREATE INDEX IF NOT EXISTS idx_notifications_user_unread ON notifications(user_id, is_read) WHERE is_read = FALSE;
CREATE INDEX IF NOT EXISTS idx_notifications_created_at ON notifications(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_type ON notifications(type);

-- ============================================
-- ORIGINAL INDEXES
-- ============================================

-- Create indexes for better query performance
CREATE INDEX IF NOT EXISTS idx_scoped_controls_org ON scoped_controls(organization_id);
CREATE INDEX IF NOT EXISTS idx_scoped_controls_status ON scoped_controls(implementation_status);
CREATE INDEX IF NOT EXISTS idx_scoped_controls_owner ON scoped_controls(owner);
CREATE INDEX IF NOT EXISTS idx_scoped_controls_scf_id ON scoped_controls(scf_id);

CREATE INDEX IF NOT EXISTS idx_evidence_tracking_org ON evidence_tracking(organization_id);
CREATE INDEX IF NOT EXISTS idx_evidence_tracking_owner ON evidence_tracking(owner);
CREATE INDEX IF NOT EXISTS idx_evidence_tracking_evidence_id ON evidence_tracking(evidence_id);

-- Create a default organization for initial setup
INSERT INTO organizations (name, slug)
VALUES ('Default Organization', 'default')
ON CONFLICT (slug) DO NOTHING;

-- Create update trigger function to automatically update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Add triggers to auto-update updated_at
CREATE TRIGGER update_organizations_updated_at BEFORE UPDATE ON organizations
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_scoped_controls_updated_at BEFORE UPDATE ON scoped_controls
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_evidence_tracking_updated_at BEFORE UPDATE ON evidence_tracking
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_systems_updated_at BEFORE UPDATE ON systems
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
