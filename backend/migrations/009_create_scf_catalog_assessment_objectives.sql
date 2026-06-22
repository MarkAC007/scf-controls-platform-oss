-- Migration 009: Create SCF Catalog Assessment Objectives Table
-- Purpose: Store SCF Assessment Objectives catalog data (reference data, seeded on boot)
-- Date: 2026-01-04
-- Related: TASK-018 (SCF Schema Verification)
--
-- IMPORTANT: This is CATALOG data - read-only reference from SCF 2025.4
-- This table is NOT included in backup/restore operations.
-- Data is seeded from assessment_objectives.json on application startup.

-- ============================================================================
-- SCF CATALOG ASSESSMENT OBJECTIVES TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS scf_catalog_assessment_objectives (
    -- Primary identifier (AO ID is the natural key)
    ao_id VARCHAR(30) PRIMARY KEY,

    -- Parent control reference
    scf_id VARCHAR(20) NOT NULL,

    -- Core objective data
    objective_text TEXT NOT NULL,

    -- PPTDF Applicability
    pptdf_people BOOLEAN DEFAULT FALSE,
    pptdf_process BOOLEAN DEFAULT FALSE,
    pptdf_technology BOOLEAN DEFAULT FALSE,
    pptdf_data BOOLEAN DEFAULT FALSE,
    pptdf_facility BOOLEAN DEFAULT FALSE,

    -- Assessment metadata
    ao_origins TEXT,                    -- Source standards (SCF Created, CMMC, NIST, etc.) - can be long
    notes TEXT,                         -- Notes / Errata
    assessment_rigor INTEGER,           -- Rigor level (1-3)

    -- Parameters
    scf_defined_parameters TEXT,        -- SDP - parameters defined by SCF
    org_defined_parameters TEXT,        -- ODP - parameters to be defined by organization

    -- Framework-specific AO mappings
    cmmc_level1_ao TEXT,
    dhs_ztcf_ao TEXT,
    nist_800_53a TEXT,
    nist_800_171a TEXT,
    nist_800_171a_r3 TEXT,
    nist_800_172a TEXT,

    -- Assessment execution fields
    asset_type VARCHAR(100),            -- examine/interview/test
    assessment_procedure TEXT,
    expected_results TEXT,

    -- Catalog metadata
    catalog_version VARCHAR(20) DEFAULT '2025.4',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- INDEXES
-- ============================================================================

-- Parent control lookup (most common query)
CREATE INDEX IF NOT EXISTS idx_catalog_ao_scf_id
    ON scf_catalog_assessment_objectives(scf_id);

-- Assessment rigor filtering
CREATE INDEX IF NOT EXISTS idx_catalog_ao_rigor
    ON scf_catalog_assessment_objectives(assessment_rigor);

-- Origins search (for framework-specific queries)
CREATE INDEX IF NOT EXISTS idx_catalog_ao_origins
    ON scf_catalog_assessment_objectives(ao_origins);

-- Asset type filtering
CREATE INDEX IF NOT EXISTS idx_catalog_ao_asset_type
    ON scf_catalog_assessment_objectives(asset_type);

-- Composite index for control + rigor queries
CREATE INDEX IF NOT EXISTS idx_catalog_ao_scf_rigor
    ON scf_catalog_assessment_objectives(scf_id, assessment_rigor);

-- ============================================================================
-- FOREIGN KEY (optional - depends on load order)
-- ============================================================================
-- Note: This FK is added separately to allow flexible seeding order
-- Uncomment if controls are always seeded before AOs

-- ALTER TABLE scf_catalog_assessment_objectives
--     ADD CONSTRAINT fk_ao_control
--     FOREIGN KEY (scf_id) REFERENCES scf_catalog_controls(scf_id)
--     ON DELETE CASCADE;

-- ============================================================================
-- COMMENTS
-- ============================================================================

COMMENT ON TABLE scf_catalog_assessment_objectives IS 'SCF 2025.4 Assessment Objectives catalog - reference data seeded on boot, NOT backed up';

-- Primary fields
COMMENT ON COLUMN scf_catalog_assessment_objectives.ao_id IS 'Assessment Objective ID (e.g., GOV-01_A01)';
COMMENT ON COLUMN scf_catalog_assessment_objectives.scf_id IS 'Parent control ID (e.g., GOV-01)';
COMMENT ON COLUMN scf_catalog_assessment_objectives.objective_text IS 'Full assessment objective description';

-- PPTDF
COMMENT ON COLUMN scf_catalog_assessment_objectives.pptdf_people IS 'Applies to People domain';
COMMENT ON COLUMN scf_catalog_assessment_objectives.pptdf_process IS 'Applies to Process domain';
COMMENT ON COLUMN scf_catalog_assessment_objectives.pptdf_technology IS 'Applies to Technology domain';
COMMENT ON COLUMN scf_catalog_assessment_objectives.pptdf_data IS 'Applies to Data domain';
COMMENT ON COLUMN scf_catalog_assessment_objectives.pptdf_facility IS 'Applies to Facility domain';

-- Assessment metadata
COMMENT ON COLUMN scf_catalog_assessment_objectives.ao_origins IS 'Source standards (SCF Created, CMMC, NIST, etc.)';
COMMENT ON COLUMN scf_catalog_assessment_objectives.assessment_rigor IS 'Rigor level 1-3 (1=basic, 3=comprehensive)';
COMMENT ON COLUMN scf_catalog_assessment_objectives.scf_defined_parameters IS 'Parameters defined by SCF (SDP)';
COMMENT ON COLUMN scf_catalog_assessment_objectives.org_defined_parameters IS 'Parameters to be defined by organization (ODP)';

-- Framework mappings
COMMENT ON COLUMN scf_catalog_assessment_objectives.cmmc_level1_ao IS 'CMMC Level 1 assessment objective mapping';
COMMENT ON COLUMN scf_catalog_assessment_objectives.dhs_ztcf_ao IS 'DHS Zero Trust Capability Framework mapping';
COMMENT ON COLUMN scf_catalog_assessment_objectives.nist_800_53a IS 'NIST 800-53A assessment procedure mapping';
COMMENT ON COLUMN scf_catalog_assessment_objectives.nist_800_171a IS 'NIST 800-171A assessment mapping';
COMMENT ON COLUMN scf_catalog_assessment_objectives.nist_800_171a_r3 IS 'NIST 800-171A Rev 3 mapping';
COMMENT ON COLUMN scf_catalog_assessment_objectives.nist_800_172a IS 'NIST 800-172A mapping';

-- Execution fields
COMMENT ON COLUMN scf_catalog_assessment_objectives.asset_type IS 'Assessment method: examine, interview, test';
COMMENT ON COLUMN scf_catalog_assessment_objectives.assessment_procedure IS 'Detailed assessment procedure';
COMMENT ON COLUMN scf_catalog_assessment_objectives.expected_results IS 'Expected results for compliance';

-- Metadata
COMMENT ON COLUMN scf_catalog_assessment_objectives.catalog_version IS 'SCF catalog version (e.g., 2025.4)';
