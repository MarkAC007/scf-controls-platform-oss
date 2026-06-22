-- Migration 005: Add system_evidence_capabilities junction table
-- Purpose: Map systems to evidence types they can provide (capabilities)
-- Date: 2025-12-31

-- ============================================================================
-- SYSTEM EVIDENCE CAPABILITIES TABLE
-- ============================================================================
-- This junction table maps what evidence a system CAN provide (capability),
-- distinct from evidence_tracking.system_id which tracks what IS collected.

CREATE TABLE IF NOT EXISTS system_evidence_capabilities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    system_id UUID NOT NULL REFERENCES systems(id) ON DELETE CASCADE,
    evidence_id VARCHAR(50) NOT NULL,  -- References ERL evidence ID

    -- Capability metadata
    capability_status VARCHAR(20) DEFAULT 'potential',  -- potential, configured, active
    collection_method VARCHAR(50),  -- api, export, manual, webhook, scheduled
    confidence_level VARCHAR(20) DEFAULT 'medium',  -- high, medium, low
    data_format VARCHAR(50),  -- json, csv, pdf, logs, etc.
    notes TEXT,

    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Audit fields
    created_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    updated_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL
);

-- ============================================================================
-- CONSTRAINTS
-- ============================================================================

-- Each system can only have one capability entry per evidence type
ALTER TABLE system_evidence_capabilities
    ADD CONSTRAINT uq_system_evidence_capability UNIQUE (system_id, evidence_id);

-- Validate capability_status values
ALTER TABLE system_evidence_capabilities
    ADD CONSTRAINT chk_capability_status CHECK (capability_status IN ('potential', 'configured', 'active'));

-- Validate confidence_level values
ALTER TABLE system_evidence_capabilities
    ADD CONSTRAINT chk_confidence_level CHECK (confidence_level IN ('high', 'medium', 'low'));

-- Validate collection_method values (nullable, but must be valid if provided)
ALTER TABLE system_evidence_capabilities
    ADD CONSTRAINT chk_collection_method CHECK (
        collection_method IS NULL OR
        collection_method IN ('api', 'export', 'manual', 'webhook', 'scheduled', 'integration')
    );

-- ============================================================================
-- INDEXES
-- ============================================================================

-- Index for querying capabilities by system
CREATE INDEX IF NOT EXISTS idx_sec_system_id ON system_evidence_capabilities(system_id);

-- Index for finding systems that can provide specific evidence
CREATE INDEX IF NOT EXISTS idx_sec_evidence_id ON system_evidence_capabilities(evidence_id);

-- Index for filtering by capability status
CREATE INDEX IF NOT EXISTS idx_sec_status ON system_evidence_capabilities(capability_status);

-- Composite index for common query patterns
CREATE INDEX IF NOT EXISTS idx_sec_system_status ON system_evidence_capabilities(system_id, capability_status);

-- ============================================================================
-- TRIGGERS
-- ============================================================================

-- Auto-update updated_at timestamp
CREATE OR REPLACE FUNCTION update_sec_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_sec_updated_at ON system_evidence_capabilities;
CREATE TRIGGER trigger_sec_updated_at
    BEFORE UPDATE ON system_evidence_capabilities
    FOR EACH ROW
    EXECUTE FUNCTION update_sec_updated_at();

-- ============================================================================
-- COMMENTS
-- ============================================================================

COMMENT ON TABLE system_evidence_capabilities IS 'Junction table mapping systems to evidence types they can provide. Represents capability (what CAN be collected), not current collection status.';
COMMENT ON COLUMN system_evidence_capabilities.evidence_id IS 'ERL evidence ID (e.g., ERL-AM-001). References catalog evidence definitions.';
COMMENT ON COLUMN system_evidence_capabilities.capability_status IS 'Status of capability: potential (known possible), configured (set up), active (in use).';
COMMENT ON COLUMN system_evidence_capabilities.collection_method IS 'How evidence is collected: api, export, manual, webhook, scheduled, integration.';
COMMENT ON COLUMN system_evidence_capabilities.confidence_level IS 'Confidence in evidence quality: high, medium, low.';
COMMENT ON COLUMN system_evidence_capabilities.data_format IS 'Format of collected evidence: json, csv, pdf, logs, etc.';
