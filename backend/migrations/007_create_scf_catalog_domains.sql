-- Migration 007: Create SCF Catalog Domains Table
-- Purpose: Store SCF domain catalog data (reference data, seeded on boot)
-- Date: 2026-01-04
-- Related: TASK-018 (SCF Schema Verification)
--
-- IMPORTANT: This is CATALOG data - read-only reference from SCF 2025.4
-- This table is NOT included in backup/restore operations.
-- Data is seeded from domains.json on application startup.

-- ============================================================================
-- SCF CATALOG DOMAINS TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS scf_catalog_domains (
    -- Primary identifier (domain identifier is the natural key)
    identifier VARCHAR(10) PRIMARY KEY,

    -- Domain metadata
    "order" INTEGER NOT NULL,
    name VARCHAR(200) NOT NULL,
    principle TEXT NOT NULL,
    principle_intent TEXT,

    -- Catalog metadata
    catalog_version VARCHAR(20) DEFAULT '2025.4',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- INDEXES
-- ============================================================================

-- Order lookup for sorting
CREATE INDEX IF NOT EXISTS idx_catalog_domains_order
    ON scf_catalog_domains("order");

-- Name search
CREATE INDEX IF NOT EXISTS idx_catalog_domains_name
    ON scf_catalog_domains(name);

-- ============================================================================
-- COMMENTS
-- ============================================================================

COMMENT ON TABLE scf_catalog_domains IS 'SCF 2025.4 domain catalog - reference data seeded on boot, NOT backed up';
COMMENT ON COLUMN scf_catalog_domains.identifier IS 'Domain identifier (e.g., GOV, IAC, AST)';
COMMENT ON COLUMN scf_catalog_domains."order" IS 'Display order (1-33)';
COMMENT ON COLUMN scf_catalog_domains.name IS 'Full domain name (e.g., Cybersecurity & Data Protection Governance)';
COMMENT ON COLUMN scf_catalog_domains.principle IS 'Domain principle statement';
COMMENT ON COLUMN scf_catalog_domains.principle_intent IS 'Intent behind the domain principle';
COMMENT ON COLUMN scf_catalog_domains.catalog_version IS 'SCF catalog version (e.g., 2025.4)';
