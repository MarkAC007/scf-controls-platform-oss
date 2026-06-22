"""
SQLAlchemy ORM models for SCF Catalog tables.
These are READ-ONLY reference tables seeded from JSON on application startup.
NOT included in backup/restore operations.

Catalog Tables:
- scf_catalog_controls: SCF control definitions with full metadata
- scf_catalog_domains: SCF domain definitions
- scf_catalog_evidence: Evidence Request List (ERL) entries
- scf_catalog_assessment_objectives: Assessment objectives for controls
- capability_themes: KSI-aligned capability theme definitions
- capability_theme_mappings: SCF control to capability theme mappings
"""
from sqlalchemy import Column, String, Boolean, Text, Integer, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from database import Base


class SCFCatalogControl(Base):
    """SCF Control catalog entry.

    Contains full SCF control metadata including CMM maturity guidance,
    business size guidance, SCRM focus, and risk/threat mappings.
    """
    __tablename__ = "scf_catalog_controls"

    # Primary key (natural key)
    scf_id = Column(String(20), primary_key=True)

    # Core metadata
    scf_domain = Column(String(100), nullable=False)
    control_name = Column(String(500), nullable=False)
    control_description = Column(Text, nullable=False)
    control_question = Column(Text)

    # SCF validation and weighting
    validation_cadence = Column(String(50))
    control_weighting = Column(Integer)
    nist_csf_function = Column(String(20))

    # PPTDF Applicability
    pptdf_people = Column(Boolean, default=False)
    pptdf_process = Column(Boolean, default=False)
    pptdf_technology = Column(Boolean, default=False)
    pptdf_data = Column(Boolean, default=False)
    pptdf_facility = Column(Boolean, default=False)

    # JSONB fields
    evidence_requests = Column(JSONB, default=[])
    framework_mappings = Column(JSONB, default={})

    # C|P-CMM Maturity Model (6 levels)
    cmm_level_0 = Column(Text)
    cmm_level_1 = Column(Text)
    cmm_level_2 = Column(Text)
    cmm_level_3 = Column(Text)
    cmm_level_4 = Column(Text)
    cmm_level_5 = Column(Text)

    # Business Size Guidance (5 sizes)
    biz_micro_small = Column(Text)
    biz_small = Column(Text)
    biz_medium = Column(Text)
    biz_large = Column(Text)
    biz_enterprise = Column(Text)

    # SCRM Focus tiers
    scrm_tier1_strategic = Column(Boolean, default=False)
    scrm_tier2_operational = Column(Boolean, default=False)
    scrm_tier3_tactical = Column(Boolean, default=False)

    # Risk/Threat Mapping
    risk_codes = Column(JSONB, default=[])
    threat_codes = Column(JSONB, default=[])

    # Required artifact types — extracted from control description + assessment
    # objectives via LLM. Consumed by windowed evidence assessment to decide
    # what a complete evidence portfolio for this control should contain.
    # Shape: [{"type": str, "weight": "high|medium|low", "mandatory": bool, "description": str}, ...]
    required_artifact_types = Column(JSONB, default=list, server_default="[]", nullable=False)
    required_artifact_types_extracted_at = Column(DateTime(timezone=False))

    # Catalog metadata
    catalog_version = Column(String(20), default='2025.4')
    created_at = Column(DateTime(timezone=False), server_default=func.now())
    updated_at = Column(DateTime(timezone=False), server_default=func.now())

    def __repr__(self):
        return f"<SCFCatalogControl(scf_id={self.scf_id}, name={self.control_name})>"


class SCFCatalogDomain(Base):
    """SCF Domain catalog entry."""
    __tablename__ = "scf_catalog_domains"

    # Primary key (natural key)
    identifier = Column(String(10), primary_key=True)

    # Domain metadata
    order = Column(Integer, nullable=False)
    name = Column(String(200), nullable=False)
    principle = Column(Text, nullable=False)
    principle_intent = Column(Text)

    # Catalog metadata
    catalog_version = Column(String(20), default='2025.4')
    created_at = Column(DateTime(timezone=False), server_default=func.now())
    updated_at = Column(DateTime(timezone=False), server_default=func.now())

    def __repr__(self):
        return f"<SCFCatalogDomain(identifier={self.identifier}, name={self.name})>"


class SCFCatalogEvidence(Base):
    """SCF Evidence Request List (ERL) catalog entry."""
    __tablename__ = "scf_catalog_evidence"

    # Primary key (natural key)
    evidence_id = Column(String(20), primary_key=True)

    # Evidence metadata
    area_of_focus = Column(String(200), nullable=False)
    artifact_title = Column(String(500), nullable=False)
    artifact_description = Column(Text)

    # Control mappings
    control_mappings = Column(JSONB, default=[])

    # Catalog metadata
    catalog_version = Column(String(20), default='2025.4')
    created_at = Column(DateTime(timezone=False), server_default=func.now())
    updated_at = Column(DateTime(timezone=False), server_default=func.now())

    def __repr__(self):
        return f"<SCFCatalogEvidence(evidence_id={self.evidence_id}, title={self.artifact_title})>"


class SCFCatalogAssessmentObjective(Base):
    """SCF Assessment Objective catalog entry."""
    __tablename__ = "scf_catalog_assessment_objectives"

    # Primary key (natural key)
    ao_id = Column(String(30), primary_key=True)

    # Parent control reference
    scf_id = Column(String(20), nullable=False)

    # Core objective data
    objective_text = Column(Text, nullable=False)

    # PPTDF Applicability
    pptdf_people = Column(Boolean, default=False)
    pptdf_process = Column(Boolean, default=False)
    pptdf_technology = Column(Boolean, default=False)
    pptdf_data = Column(Boolean, default=False)
    pptdf_facility = Column(Boolean, default=False)

    # Assessment metadata
    ao_origins = Column(Text)  # Can be long - source standards list
    notes = Column(Text)
    assessment_rigor = Column(Integer)

    # Parameters
    scf_defined_parameters = Column(Text)
    org_defined_parameters = Column(Text)

    # Framework-specific AO mappings
    cmmc_level1_ao = Column(Text)
    dhs_ztcf_ao = Column(Text)
    nist_800_53a = Column(Text)
    nist_800_171a = Column(Text)
    nist_800_171a_r3 = Column(Text)
    nist_800_172a = Column(Text)

    # Assessment execution fields
    asset_type = Column(String(100))
    assessment_procedure = Column(Text)
    expected_results = Column(Text)

    # Catalog metadata
    catalog_version = Column(String(20), default='2025.4')
    created_at = Column(DateTime(timezone=False), server_default=func.now())
    updated_at = Column(DateTime(timezone=False), server_default=func.now())

    def __repr__(self):
        return f"<SCFCatalogAssessmentObjective(ao_id={self.ao_id}, scf_id={self.scf_id})>"


class CapabilityTheme(Base):
    """KSI-aligned capability theme definition.

    Groups SCF controls into 11 capability categories inspired by FedRAMP 20x
    Key Security Indicators (KSIs). Read-only reference data.
    """
    __tablename__ = "capability_themes"

    id = Column(Integer, primary_key=True)
    theme_code = Column(String(16), unique=True, nullable=False)  # e.g., "IAM", "MLA"
    name = Column(String(128), nullable=False)  # e.g., "Identity & Access Management"
    description = Column(Text, nullable=False)
    ksi_reference = Column(String(16), nullable=True)  # e.g., "KSI-IAM"
    display_order = Column(Integer, default=0)
    icon = Column(String(32), nullable=True)  # UI icon identifier

    # Catalog metadata
    catalog_version = Column(String(20), default='2025.4')
    created_at = Column(DateTime(timezone=False), server_default=func.now())
    updated_at = Column(DateTime(timezone=False), server_default=func.now())

    def __repr__(self):
        return f"<CapabilityTheme(code={self.theme_code}, name={self.name})>"


class CapabilityThemeMapping(Base):
    """Maps SCF controls to capability themes via NIST 800-53 crosswalk.

    One SCF control can map to multiple themes with primary/supporting relevance.
    Read-only reference data computed from NIST 800-53 family mappings.
    """
    __tablename__ = "capability_theme_mappings"
    __table_args__ = (
        UniqueConstraint('theme_id', 'scf_id', name='uq_theme_mapping_theme_scf'),
    )

    id = Column(Integer, primary_key=True)
    theme_id = Column(Integer, ForeignKey("capability_themes.id"), nullable=False)
    scf_id = Column(String(32), nullable=False)  # References SCF control ID
    relevance = Column(String(16), default="primary")  # primary | supporting

    # Catalog metadata
    catalog_version = Column(String(20), default='2025.4')
    created_at = Column(DateTime(timezone=False), server_default=func.now())
    updated_at = Column(DateTime(timezone=False), server_default=func.now())

    def __repr__(self):
        return f"<CapabilityThemeMapping(theme_id={self.theme_id}, scf_id={self.scf_id}, relevance={self.relevance})>"
