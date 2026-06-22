-- Migration 013: Add vendor management tables
-- Supports Third-Party Risk Management (TPRM) - Issue #58
-- Tables: vendors, vendor_assessments, vendor_certifications

-- =============================================================================
-- Vendors table
-- =============================================================================
CREATE TABLE IF NOT EXISTS vendors (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,

    -- Core fields
    name VARCHAR(255) NOT NULL,
    description TEXT,
    website VARCHAR(500),
    category VARCHAR(100),
    status VARCHAR(30) NOT NULL DEFAULT 'prospect',
    criticality VARCHAR(20) NOT NULL DEFAULT 'low',

    -- Contact information
    contact_name VARCHAR(255),
    contact_email VARCHAR(255),
    contact_phone VARCHAR(50),

    -- Contract details
    contract_start_date DATE,
    contract_end_date DATE,
    contract_value NUMERIC(12, 2),

    -- Risk scoring
    risk_score INTEGER CHECK (risk_score >= 1 AND risk_score <= 25),
    risk_level VARCHAR(20),
    data_classification VARCHAR(50),

    -- Audit timestamps and user FKs
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    created_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    updated_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL
);

-- Unique vendor name per organisation (case-insensitive)
CREATE UNIQUE INDEX IF NOT EXISTS idx_vendors_org_name_unique
    ON vendors (organization_id, LOWER(name));

-- Query indices
CREATE INDEX IF NOT EXISTS idx_vendors_org_id ON vendors(organization_id);
CREATE INDEX IF NOT EXISTS idx_vendors_status ON vendors(organization_id, status);
CREATE INDEX IF NOT EXISTS idx_vendors_criticality ON vendors(organization_id, criticality);
CREATE INDEX IF NOT EXISTS idx_vendors_category ON vendors(organization_id, category);


-- =============================================================================
-- Vendor Assessments table
-- =============================================================================
CREATE TABLE IF NOT EXISTS vendor_assessments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vendor_id UUID NOT NULL REFERENCES vendors(id) ON DELETE CASCADE,

    -- Assessment details
    assessment_type VARCHAR(50) NOT NULL DEFAULT 'initial',
    assessment_date DATE NOT NULL DEFAULT CURRENT_DATE,
    status VARCHAR(30) NOT NULL DEFAULT 'scheduled',

    -- CIA scores (1-5 scale)
    confidentiality_score INTEGER CHECK (confidentiality_score >= 1 AND confidentiality_score <= 5),
    integrity_score INTEGER CHECK (integrity_score >= 1 AND integrity_score <= 5),
    availability_score INTEGER CHECK (availability_score >= 1 AND availability_score <= 5),

    -- Findings and outcome
    findings TEXT,
    risk_rating VARCHAR(20),
    next_assessment_date DATE,

    -- Assessor
    assessor_user_id UUID REFERENCES users(id) ON DELETE SET NULL,

    -- Audit timestamps and user FKs
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    created_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    updated_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_vendor_assessments_vendor_id ON vendor_assessments(vendor_id);
CREATE INDEX IF NOT EXISTS idx_vendor_assessments_status ON vendor_assessments(status);


-- =============================================================================
-- Vendor Certifications table
-- =============================================================================
CREATE TABLE IF NOT EXISTS vendor_certifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vendor_id UUID NOT NULL REFERENCES vendors(id) ON DELETE CASCADE,

    -- Certification details
    certification_name VARCHAR(255) NOT NULL,
    certification_body VARCHAR(255),
    certificate_number VARCHAR(100),
    status VARCHAR(30) NOT NULL DEFAULT 'valid',

    -- Dates
    issue_date DATE,
    expiry_date DATE,

    -- Scope and verification
    scope TEXT,
    verification_url VARCHAR(500),

    -- Audit timestamps and user FKs
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    created_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    updated_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_vendor_certifications_vendor_id ON vendor_certifications(vendor_id);
CREATE INDEX IF NOT EXISTS idx_vendor_certifications_status ON vendor_certifications(status);
