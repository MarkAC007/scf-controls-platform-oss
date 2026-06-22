-- Migration 004: Add system reference to evidence tracking
-- Purpose: Link evidence collection to specific systems
-- Date: 2025-12-31

-- ============================================================================
-- ADD SYSTEM_ID FK TO EVIDENCE_TRACKING
-- ============================================================================

-- Add system_id column to evidence_tracking table
ALTER TABLE evidence_tracking
    ADD COLUMN IF NOT EXISTS system_id UUID REFERENCES systems(id) ON DELETE SET NULL;

-- ============================================================================
-- INDEXES
-- ============================================================================

-- Index for querying evidence by system
CREATE INDEX IF NOT EXISTS idx_evidence_tracking_system_id ON evidence_tracking(system_id);

-- Composite index for finding evidence by org and system
CREATE INDEX IF NOT EXISTS idx_evidence_tracking_org_system ON evidence_tracking(organization_id, system_id);

-- ============================================================================
-- COMMENTS
-- ============================================================================

COMMENT ON COLUMN evidence_tracking.system_id IS 'Reference to the system that collects/provides this evidence. Replaces legacy collecting_system text field.';
