-- Migration 006: Create SCF Catalog Controls Table
-- Purpose: Store SCF control catalog data (reference data, seeded on boot)
-- Date: 2026-01-04
-- Related: TASK-018 (SCF Schema Verification)
--
-- IMPORTANT: This is CATALOG data - read-only reference from SCF 2025.4
-- This table is NOT included in backup/restore operations.
-- Data is seeded from control_guidance.json on application startup.

-- ============================================================================
-- SCF CATALOG CONTROLS TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS scf_catalog_controls (
    -- Primary identifier (SCF control ID is the natural key)
    scf_id VARCHAR(20) PRIMARY KEY,

    -- Core control metadata
    scf_domain VARCHAR(100) NOT NULL,
    control_name VARCHAR(500) NOT NULL,
    control_description TEXT NOT NULL,
    control_question TEXT,

    -- SCF validation and weighting
    validation_cadence VARCHAR(50),           -- Annual, Quarterly, etc.
    control_weighting INTEGER,                -- Priority 1-10
    nist_csf_function VARCHAR(20),            -- Identify, Protect, Detect, Respond, Recover, Govern

    -- PPTDF Applicability (People, Process, Technology, Data, Facility)
    pptdf_people BOOLEAN DEFAULT FALSE,
    pptdf_process BOOLEAN DEFAULT FALSE,
    pptdf_technology BOOLEAN DEFAULT FALSE,
    pptdf_data BOOLEAN DEFAULT FALSE,
    pptdf_facility BOOLEAN DEFAULT FALSE,

    -- Evidence requests (array of evidence IDs)
    evidence_requests JSONB DEFAULT '[]'::jsonb,

    -- Framework mappings (object: framework_id -> array of control refs)
    framework_mappings JSONB DEFAULT '{}'::jsonb,

    -- =========================================================================
    -- SCF 2025.4 Extended Fields
    -- =========================================================================

    -- C|P-CMM Maturity Model guidance (6 levels)
    cmm_level_0 TEXT,   -- Not Performed
    cmm_level_1 TEXT,   -- Performed Informally
    cmm_level_2 TEXT,   -- Planned & Tracked
    cmm_level_3 TEXT,   -- Well Defined
    cmm_level_4 TEXT,   -- Quantitatively Controlled
    cmm_level_5 TEXT,   -- Continuously Improving

    -- Business Size Guidance (5 organization sizes)
    biz_micro_small TEXT,   -- <10 staff (BLS Classes 1-2)
    biz_small TEXT,         -- 10-49 staff (BLS Classes 3-4)
    biz_medium TEXT,        -- 50-249 staff (BLS Classes 5-6)
    biz_large TEXT,         -- 250-999 staff (BLS Classes 7-8)
    biz_enterprise TEXT,    -- >1000 staff (BLS Class 9)

    -- SCRM Focus tiers (Supply Chain Risk Management)
    scrm_tier1_strategic BOOLEAN DEFAULT FALSE,
    scrm_tier2_operational BOOLEAN DEFAULT FALSE,
    scrm_tier3_tactical BOOLEAN DEFAULT FALSE,

    -- Risk/Threat Mapping (arrays of codes)
    risk_codes JSONB DEFAULT '[]'::jsonb,    -- e.g., ['R-AC-1', 'R-GV-3']
    threat_codes JSONB DEFAULT '[]'::jsonb,  -- e.g., ['NT-1', 'MT-5']

    -- Catalog metadata
    catalog_version VARCHAR(20) DEFAULT '2025.4',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- INDEXES
-- ============================================================================

-- Domain lookup (common filter)
CREATE INDEX IF NOT EXISTS idx_catalog_controls_domain
    ON scf_catalog_controls(scf_domain);

-- NIST CSF function filtering
CREATE INDEX IF NOT EXISTS idx_catalog_controls_csf_function
    ON scf_catalog_controls(nist_csf_function);

-- Control weighting for prioritization
CREATE INDEX IF NOT EXISTS idx_catalog_controls_weighting
    ON scf_catalog_controls(control_weighting DESC);

-- GIN indexes for JSONB array queries
CREATE INDEX IF NOT EXISTS idx_catalog_controls_evidence_requests
    ON scf_catalog_controls USING GIN (evidence_requests);

CREATE INDEX IF NOT EXISTS idx_catalog_controls_risk_codes
    ON scf_catalog_controls USING GIN (risk_codes);

CREATE INDEX IF NOT EXISTS idx_catalog_controls_threat_codes
    ON scf_catalog_controls USING GIN (threat_codes);

CREATE INDEX IF NOT EXISTS idx_catalog_controls_framework_mappings
    ON scf_catalog_controls USING GIN (framework_mappings);

-- SCRM tier partial indexes
CREATE INDEX IF NOT EXISTS idx_catalog_controls_scrm_strategic
    ON scf_catalog_controls (scrm_tier1_strategic) WHERE scrm_tier1_strategic = TRUE;

CREATE INDEX IF NOT EXISTS idx_catalog_controls_scrm_operational
    ON scf_catalog_controls (scrm_tier2_operational) WHERE scrm_tier2_operational = TRUE;

CREATE INDEX IF NOT EXISTS idx_catalog_controls_scrm_tactical
    ON scf_catalog_controls (scrm_tier3_tactical) WHERE scrm_tier3_tactical = TRUE;

-- ============================================================================
-- COMMENTS
-- ============================================================================

COMMENT ON TABLE scf_catalog_controls IS 'SCF 2025.4 control catalog - reference data seeded on boot, NOT backed up';

-- Core fields
COMMENT ON COLUMN scf_catalog_controls.scf_id IS 'SCF control ID (e.g., GOV-01, IAC-15) - primary key';
COMMENT ON COLUMN scf_catalog_controls.scf_domain IS 'SCF domain name (e.g., Cybersecurity & Data Protection Governance)';
COMMENT ON COLUMN scf_catalog_controls.control_name IS 'Human-readable control name';
COMMENT ON COLUMN scf_catalog_controls.control_description IS 'Full control description text';
COMMENT ON COLUMN scf_catalog_controls.control_question IS 'Assessment question for auditors';
COMMENT ON COLUMN scf_catalog_controls.validation_cadence IS 'Recommended validation frequency (Annual, Quarterly, etc.)';
COMMENT ON COLUMN scf_catalog_controls.control_weighting IS 'Priority weighting 1-10 (10 = highest priority)';
COMMENT ON COLUMN scf_catalog_controls.nist_csf_function IS 'NIST CSF 2.0 function: Identify, Protect, Detect, Respond, Recover, Govern';

-- PPTDF
COMMENT ON COLUMN scf_catalog_controls.pptdf_people IS 'Applies to People domain';
COMMENT ON COLUMN scf_catalog_controls.pptdf_process IS 'Applies to Process domain';
COMMENT ON COLUMN scf_catalog_controls.pptdf_technology IS 'Applies to Technology domain';
COMMENT ON COLUMN scf_catalog_controls.pptdf_data IS 'Applies to Data domain';
COMMENT ON COLUMN scf_catalog_controls.pptdf_facility IS 'Applies to Facility domain';

-- JSONB fields
COMMENT ON COLUMN scf_catalog_controls.evidence_requests IS 'JSONB array of evidence IDs required for this control';
COMMENT ON COLUMN scf_catalog_controls.framework_mappings IS 'JSONB object mapping framework IDs to arrays of control references';

-- C|P-CMM Maturity
COMMENT ON COLUMN scf_catalog_controls.cmm_level_0 IS 'C|P-CMM Level 0: Not Performed';
COMMENT ON COLUMN scf_catalog_controls.cmm_level_1 IS 'C|P-CMM Level 1: Performed Informally';
COMMENT ON COLUMN scf_catalog_controls.cmm_level_2 IS 'C|P-CMM Level 2: Planned & Tracked';
COMMENT ON COLUMN scf_catalog_controls.cmm_level_3 IS 'C|P-CMM Level 3: Well Defined';
COMMENT ON COLUMN scf_catalog_controls.cmm_level_4 IS 'C|P-CMM Level 4: Quantitatively Controlled';
COMMENT ON COLUMN scf_catalog_controls.cmm_level_5 IS 'C|P-CMM Level 5: Continuously Improving';

-- Business Size
COMMENT ON COLUMN scf_catalog_controls.biz_micro_small IS 'Guidance for <10 staff (BLS Classes 1-2)';
COMMENT ON COLUMN scf_catalog_controls.biz_small IS 'Guidance for 10-49 staff (BLS Classes 3-4)';
COMMENT ON COLUMN scf_catalog_controls.biz_medium IS 'Guidance for 50-249 staff (BLS Classes 5-6)';
COMMENT ON COLUMN scf_catalog_controls.biz_large IS 'Guidance for 250-999 staff (BLS Classes 7-8)';
COMMENT ON COLUMN scf_catalog_controls.biz_enterprise IS 'Guidance for >1000 staff (BLS Class 9)';

-- SCRM Focus
COMMENT ON COLUMN scf_catalog_controls.scrm_tier1_strategic IS 'SCRM Tier 1: Strategic focus (organization-level)';
COMMENT ON COLUMN scf_catalog_controls.scrm_tier2_operational IS 'SCRM Tier 2: Operational focus (mission/business process)';
COMMENT ON COLUMN scf_catalog_controls.scrm_tier3_tactical IS 'SCRM Tier 3: Tactical focus (information system level)';

-- Risk/Threat
COMMENT ON COLUMN scf_catalog_controls.risk_codes IS 'JSONB array of SCF risk codes (e.g., R-AC-1, R-GV-3)';
COMMENT ON COLUMN scf_catalog_controls.threat_codes IS 'JSONB array of SCF threat codes (e.g., NT-1, MT-5)';

-- Metadata
COMMENT ON COLUMN scf_catalog_controls.catalog_version IS 'SCF catalog version (e.g., 2025.4)';
