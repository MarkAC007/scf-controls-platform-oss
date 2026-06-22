-- Migration 011: Add extended fields to scoped_controls and evidence_tracking
-- These fields support SCF 2025.4 control metadata for organization-specific scoping

-- =============================================================================
-- scoped_controls extended fields
-- =============================================================================
ALTER TABLE scoped_controls
ADD COLUMN IF NOT EXISTS control_weighting INTEGER,
ADD COLUMN IF NOT EXISTS validation_cadence VARCHAR(50),
ADD COLUMN IF NOT EXISTS nist_csf_function VARCHAR(20),
ADD COLUMN IF NOT EXISTS control_question TEXT,
ADD COLUMN IF NOT EXISTS pptdf_people BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS pptdf_process BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS pptdf_technology BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS pptdf_data BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS pptdf_facility BOOLEAN DEFAULT FALSE;

-- Add comments for documentation
COMMENT ON COLUMN scoped_controls.control_weighting IS 'Priority weighting 1-10 (10 = highest priority)';
COMMENT ON COLUMN scoped_controls.validation_cadence IS 'Recommended validation frequency (Annual, Quarterly, etc.)';
COMMENT ON COLUMN scoped_controls.nist_csf_function IS 'NIST CSF 2.0 function: Identify, Protect, Detect, Respond, Recover, Govern';
COMMENT ON COLUMN scoped_controls.control_question IS 'Assessment question for this control';
COMMENT ON COLUMN scoped_controls.pptdf_people IS 'Applies to People domain';
COMMENT ON COLUMN scoped_controls.pptdf_process IS 'Applies to Process domain';
COMMENT ON COLUMN scoped_controls.pptdf_technology IS 'Applies to Technology domain';
COMMENT ON COLUMN scoped_controls.pptdf_data IS 'Applies to Data domain';
COMMENT ON COLUMN scoped_controls.pptdf_facility IS 'Applies to Facility domain';

-- =============================================================================
-- evidence_tracking extended fields
-- =============================================================================
ALTER TABLE evidence_tracking
ADD COLUMN IF NOT EXISTS system_id UUID REFERENCES systems(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_evidence_tracking_system ON evidence_tracking(system_id);

COMMENT ON COLUMN evidence_tracking.system_id IS 'Link to system responsible for collecting this evidence';
