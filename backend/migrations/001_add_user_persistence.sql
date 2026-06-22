-- Migration: 001_add_user_persistence.sql
-- Description: Add user persistence layer and assignment/task/comment tracking
-- Date: 2025-12-08
-- WARNING: This migration adds new tables without dropping any existing data

-- ============================================
-- PHASE 1: USER PERSISTENCE FOUNDATION
-- ============================================

-- Create users table
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    google_sub VARCHAR(255) UNIQUE NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    display_name VARCHAR(255),
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    last_login_at TIMESTAMP WITHOUT TIME ZONE,
    email_notifications_enabled BOOLEAN DEFAULT TRUE,
    notification_frequency VARCHAR(50) DEFAULT 'immediate'
);

CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_users_google_sub ON users(google_sub);

-- Create organization_members table
CREATE TABLE IF NOT EXISTS organization_members (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role VARCHAR(50) NOT NULL DEFAULT 'viewer',
    joined_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(organization_id, user_id)
);

CREATE INDEX idx_org_members_org ON organization_members(organization_id);
CREATE INDEX idx_org_members_user ON organization_members(user_id);

-- ============================================
-- PHASE 2: ASSIGNMENTS SYSTEM
-- ============================================

-- Create assignments table (polymorphic - works for both controls and evidence)
CREATE TABLE IF NOT EXISTS assignments (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    assignable_type VARCHAR(50) NOT NULL, -- 'control' or 'evidence'
    assignable_id UUID NOT NULL,          -- references scoped_controls.id or evidence_tracking.id
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role VARCHAR(50) DEFAULT 'primary',   -- 'primary' or 'collaborator'
    assigned_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    assigned_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX idx_assignments_assignable ON assignments(assignable_type, assignable_id);
CREATE INDEX idx_assignments_user ON assignments(user_id);
CREATE INDEX idx_assignments_assigned_by ON assignments(assigned_by_user_id);

-- Add new user FK columns to existing tables (nullable for backward compatibility)
ALTER TABLE scoped_controls
    ADD COLUMN IF NOT EXISTS assigned_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS owner_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS created_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS updated_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_scoped_controls_assigned_user ON scoped_controls(assigned_user_id);
CREATE INDEX IF NOT EXISTS idx_scoped_controls_owner_user ON scoped_controls(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_scoped_controls_created_by ON scoped_controls(created_by_user_id);
CREATE INDEX IF NOT EXISTS idx_scoped_controls_updated_by ON scoped_controls(updated_by_user_id);

ALTER TABLE evidence_tracking
    ADD COLUMN IF NOT EXISTS assigned_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS owner_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS created_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS updated_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_evidence_tracking_assigned_user ON evidence_tracking(assigned_user_id);
CREATE INDEX IF NOT EXISTS idx_evidence_tracking_owner_user ON evidence_tracking(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_evidence_tracking_created_by ON evidence_tracking(created_by_user_id);
CREATE INDEX IF NOT EXISTS idx_evidence_tracking_updated_by ON evidence_tracking(updated_by_user_id);

-- ============================================
-- PHASE 3: COMMENTS SYSTEM
-- ============================================

-- Create comments table (polymorphic - works for both controls and evidence)
CREATE TABLE IF NOT EXISTS comments (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    commentable_type VARCHAR(50) NOT NULL, -- 'control' or 'evidence'
    commentable_id UUID NOT NULL,          -- references scoped_controls.id or evidence_tracking.id
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    mentions JSONB DEFAULT '[]'::jsonb,    -- array of user IDs mentioned with @
    is_edited BOOLEAN DEFAULT FALSE,
    edited_at TIMESTAMP WITHOUT TIME ZONE,
    is_deleted BOOLEAN DEFAULT FALSE,      -- soft delete
    deleted_at TIMESTAMP WITHOUT TIME ZONE,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_comments_commentable ON comments(commentable_type, commentable_id);
CREATE INDEX idx_comments_user ON comments(user_id);
CREATE INDEX idx_comments_created_at ON comments(created_at DESC);
CREATE INDEX idx_comments_not_deleted ON comments(is_deleted) WHERE is_deleted = FALSE;

-- Create comment_history table for audit trail
CREATE TABLE IF NOT EXISTS comment_history (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    comment_id UUID NOT NULL REFERENCES comments(id) ON DELETE CASCADE,
    old_content TEXT NOT NULL,
    edited_by_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    edited_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_comment_history_comment ON comment_history(comment_id);
CREATE INDEX idx_comment_history_edited_at ON comment_history(edited_at DESC);

-- ============================================
-- PHASE 4: EVIDENCE COLLECTION TASKS
-- ============================================

-- Create evidence_collection_tasks table
CREATE TABLE IF NOT EXISTS evidence_collection_tasks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    evidence_tracking_id UUID NOT NULL REFERENCES evidence_tracking(id) ON DELETE CASCADE,
    due_date DATE NOT NULL,
    status VARCHAR(50) DEFAULT 'not_started', -- 'not_started', 'in_progress', 'completed'
    assigned_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    completed_date DATE,
    completion_notes TEXT,
    auto_generated BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_evidence_tasks_evidence ON evidence_collection_tasks(evidence_tracking_id);
CREATE INDEX idx_evidence_tasks_assigned_user ON evidence_collection_tasks(assigned_user_id);
CREATE INDEX idx_evidence_tasks_status ON evidence_collection_tasks(status);
CREATE INDEX idx_evidence_tasks_due_date ON evidence_collection_tasks(due_date);

-- Add collection date tracking to evidence_tracking
ALTER TABLE evidence_tracking
    ADD COLUMN IF NOT EXISTS next_collection_date DATE,
    ADD COLUMN IF NOT EXISTS last_collection_date DATE;

CREATE INDEX IF NOT EXISTS idx_evidence_tracking_next_collection ON evidence_tracking(next_collection_date);

-- ============================================
-- PHASE 5: NOTIFICATIONS
-- ============================================

-- Create notifications table
CREATE TABLE IF NOT EXISTS notifications (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type VARCHAR(50) NOT NULL,             -- 'assignment', 'mention', 'task_due', 'task_overdue'
    reference_type VARCHAR(50) NOT NULL,   -- 'control', 'evidence', 'comment', 'task'
    reference_id UUID NOT NULL,
    message TEXT NOT NULL,
    is_read BOOLEAN DEFAULT FALSE,
    read_at TIMESTAMP WITHOUT TIME ZONE,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_notifications_user ON notifications(user_id);
CREATE INDEX idx_notifications_user_unread ON notifications(user_id, is_read) WHERE is_read = FALSE;
CREATE INDEX idx_notifications_created_at ON notifications(created_at DESC);
CREATE INDEX idx_notifications_type ON notifications(type);

-- ============================================
-- GRANT PERMISSIONS (if needed)
-- ============================================

-- Grant permissions to odin user (already owner, but being explicit)
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO odin;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO odin;

-- ============================================
-- MIGRATION COMPLETE
-- ============================================

-- Log migration completion
DO $$
BEGIN
    RAISE NOTICE 'Migration 001_add_user_persistence.sql completed successfully';
    RAISE NOTICE 'Created tables: users, organization_members, assignments, comments, comment_history, evidence_collection_tasks, notifications';
    RAISE NOTICE 'Updated tables: scoped_controls, evidence_tracking (added user FK columns)';
END $$;
