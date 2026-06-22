-- Migration 008: Create SCF Catalog Evidence Table
-- Purpose: Store SCF Evidence Request List (ERL) catalog data (reference data, seeded on boot)
-- Date: 2026-01-04
-- Related: TASK-018 (SCF Schema Verification)
--
-- IMPORTANT: This is CATALOG data - read-only reference from SCF 2025.4
-- This table is NOT included in backup/restore operations.
-- Data is seeded from erl.json on application startup.

-- ============================================================================
-- SCF CATALOG EVIDENCE TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS scf_catalog_evidence (
    -- Primary identifier (evidence ID is the natural key)
    evidence_id VARCHAR(20) PRIMARY KEY,

    -- Evidence metadata
    area_of_focus VARCHAR(200) NOT NULL,
    artifact_title VARCHAR(500) NOT NULL,
    artifact_description TEXT,

    -- Control mappings (array of control IDs this evidence supports)
    control_mappings JSONB DEFAULT '[]'::jsonb,

    -- Catalog metadata
    catalog_version VARCHAR(20) DEFAULT '2025.4',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- INDEXES
-- ============================================================================

-- Area of focus lookup
CREATE INDEX IF NOT EXISTS idx_catalog_evidence_area
    ON scf_catalog_evidence(area_of_focus);

-- Artifact title search
CREATE INDEX IF NOT EXISTS idx_catalog_evidence_title
    ON scf_catalog_evidence(artifact_title);

-- GIN index for control mappings queries
CREATE INDEX IF NOT EXISTS idx_catalog_evidence_control_mappings
    ON scf_catalog_evidence USING GIN (control_mappings);

-- ============================================================================
-- COMMENTS
-- ============================================================================

COMMENT ON TABLE scf_catalog_evidence IS 'SCF 2025.4 Evidence Request List (ERL) catalog - reference data seeded on boot, NOT backed up';
COMMENT ON COLUMN scf_catalog_evidence.evidence_id IS 'Evidence ID (e.g., E-GOV-01, E-IAC-15)';
COMMENT ON COLUMN scf_catalog_evidence.area_of_focus IS 'Evidence area/category (e.g., Cybersecurity & Data Protection Management)';
COMMENT ON COLUMN scf_catalog_evidence.artifact_title IS 'Evidence artifact name (e.g., Charter - Cybersecurity Program)';
COMMENT ON COLUMN scf_catalog_evidence.artifact_description IS 'Description of what evidence should demonstrate';
COMMENT ON COLUMN scf_catalog_evidence.control_mappings IS 'JSONB array of control IDs this evidence supports';
COMMENT ON COLUMN scf_catalog_evidence.catalog_version IS 'SCF catalog version (e.g., 2025.4)';
