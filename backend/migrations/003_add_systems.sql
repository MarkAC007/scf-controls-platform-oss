-- Migration 003: Add Systems Registry
-- Purpose: Create systems table for tracking tools and platforms that provide evidence
-- Date: 2025-12-31

-- ============================================================================
-- SYSTEMS TABLE
-- ============================================================================

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

-- ============================================================================
-- CONSTRAINTS
-- ============================================================================

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

-- ============================================================================
-- INDEXES
-- ============================================================================

-- Organization lookup (most common query pattern)
CREATE INDEX IF NOT EXISTS idx_systems_organization_id ON systems(organization_id);

-- Filter by type and status
CREATE INDEX IF NOT EXISTS idx_systems_type ON systems(system_type);
CREATE INDEX IF NOT EXISTS idx_systems_status ON systems(status);

-- Composite index for common filter: active systems by type in an org
CREATE INDEX IF NOT EXISTS idx_systems_org_type_status ON systems(organization_id, system_type, status);

-- ============================================================================
-- TRIGGERS
-- ============================================================================

-- Auto-update updated_at timestamp
CREATE TRIGGER update_systems_updated_at
    BEFORE UPDATE ON systems
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ============================================================================
-- COMMENTS
-- ============================================================================

COMMENT ON TABLE systems IS 'Registry of tools and systems that can provide evidence for compliance controls';
COMMENT ON COLUMN systems.system_type IS 'Type of system: cloud_provider, identity_provider, ticketing, logging, security_tool, code_repository, document_management, custom';
COMMENT ON COLUMN systems.connection_config IS 'JSONB field for storing API endpoints, auth method hints, and integration configuration';
COMMENT ON COLUMN systems.status IS 'System status: active (in use), inactive (not currently used), deprecated (being phased out)';
