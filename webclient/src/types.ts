export type EvidenceId = string
export type ControlId = string
export type SystemId = string

/**
 * NIST CSF 2.0 functions
 */
export type NistCsfFunction = 'Identify' | 'Protect' | 'Detect' | 'Respond' | 'Recover' | 'Govern'

/**
 * PPTDF Applicability flags (People, Process, Technology, Data, Facility)
 */
export interface PPTDFApplicability {
  people: boolean
  process: boolean
  technology: boolean
  data: boolean
  facility: boolean
}

/**
 * C|P-CMM Maturity Model guidance (6 levels)
 * Provides implementation roadmaps at each maturity level
 */
export interface CMMaturityGuidance {
  level_0?: string  // Not Performed
  level_1?: string  // Performed Informally
  level_2?: string  // Planned & Tracked
  level_3?: string  // Well Defined
  level_4?: string  // Quantitatively Controlled
  level_5?: string  // Continuously Improving
}

/**
 * Business Size Guidance for control implementation
 * Based on BLS Firm Size Classes
 */
export interface BusinessSizeGuidance {
  micro_small?: string   // <10 staff (Classes 1-2)
  small?: string         // 10-49 staff (Classes 3-4)
  medium?: string        // 50-249 staff (Classes 5-6)
  large?: string         // 250-999 staff (Classes 7-8)
  enterprise?: string    // >1000 staff (Class 9)
}

/**
 * SCRM Focus tiers for supply chain risk management
 */
export interface SCRMFocus {
  tier1_strategic?: boolean
  tier2_operational?: boolean
  tier3_tactical?: boolean
}

/**
 * Risk and Threat Mapping from SCF 2025.4
 * Maps controls to specific risk codes (R-XX-N) and threat codes (NT-N, MT-N)
 * Risk Categories: AC (Access), AM (Asset Mgmt), BC (Business Continuity),
 *                  EX (External), GV (Governance), IR (Incident Response),
 *                  SA (Situational Awareness), SC (Supply Chain)
 * Threat Categories: NT (Natural Threats), MT (Malicious Threats)
 */
export interface RiskThreatMapping {
  risk_codes?: string[]      // e.g., ['R-AC-1', 'R-AM-2', 'R-GV-1']
  threat_codes?: string[]    // e.g., ['NT-1', 'MT-5', 'MT-12']
}

/**
 * Assessment Objective from SCF 2025.4
 * Used for audit/compliance testing of controls
 */
export type AssessmentObjectiveId = string

export interface AssessmentObjective {
  ao_id: AssessmentObjectiveId          // SCF AO # (e.g., 'GOV-01_A01')
  scf_id: ControlId                     // Parent control ID (e.g., 'GOV-01')
  objective_text: string                // Full assessment objective description
  pptdf_applicability?: PPTDFApplicability
  ao_origins?: string                   // Source standards (SCF Created, CMMC, NIST, etc.)
  notes?: string                        // Notes / Errata
  assessment_rigor?: number             // Rigor level (1-3)
  scf_defined_parameters?: string       // SDP - parameters defined by SCF
  org_defined_parameters?: string       // ODP - parameters to be defined by organization
  // Framework-specific AO mappings
  cmmc_level1_ao?: string
  dhs_ztcf_ao?: string
  nist_800_53a?: string
  nist_800_171a?: string
  nist_800_171a_r3?: string
  nist_800_172a?: string
  // Assessment execution fields
  asset_type?: string                   // examine/interview/test
  assessment_procedure?: string
  expected_results?: string
}

export interface AssessmentObjectivesFile {
  objectives: AssessmentObjective[]
}

/**
 * Control guidance from SCF catalog.
 * Note: Migrated from CCF to SCF in v4.0.0. scf_id replaces ccf_id.
 */
export interface ControlGuidance {
  scf_id: ControlId
  scf_domain: string
  control_name: string
  control_description: string
  // SCF-specific fields (v4.0.0)
  control_question?: string
  validation_cadence?: string
  evidence_requests?: EvidenceId[]
  control_weighting?: number
  pptdf_applicability?: PPTDFApplicability
  nist_csf_function?: NistCsfFunction
  framework_mappings?: Record<string, string[]>
  // SCF 2025.4 extended fields (v4.1.0)
  cmm_maturity?: CMMaturityGuidance
  business_size_guidance?: BusinessSizeGuidance
  scrm_focus?: SCRMFocus
  risk_threat_mapping?: RiskThreatMapping
  // Legacy CCF fields (may be present in transitional data)
  control_theme?: string
  control_type?: string
  policy_standard?: string
  implementation_guidance?: string
  testing_procedure?: string
  audit_artifacts?: EvidenceId[]
}

export interface ControlGuidanceFile {
  controls: ControlGuidance[]
}

export interface MappingRefs {
  [framework: string]: string[]
}

/**
 * Control to framework mappings file.
 * Note: In SCF v4, mappings are embedded in controls. This interface is for legacy compatibility.
 */
export interface ControlsMappingFile {
  [scf_id: ControlId]: MappingRefs
}

export interface FrameworkNameMap {
  [frameworkRefId: string]: string
}

/**
 * Evidence Request List entry.
 * Note: SCF v4 uses area_of_focus/artifact_title. Legacy fields retained for compatibility.
 */
export interface ERLEntry {
  // SCF v4 fields
  evidence_id?: EvidenceId
  area_of_focus?: string
  artifact_title?: string
  artifact_description?: string
  control_mappings?: ControlId[]
  // Legacy CCF fields (for compatibility)
  evidence_domain?: string
  evidence_title?: string
  collection_interfaces?: string[]
}

export interface ERLFile {
  [evidenceId: EvidenceId]: ERLEntry
}

// ============================================================================
// Collection Interfaces Types (from Catalog v0.2.0)
// ============================================================================

export type CatalogCollectionMethod = 'api' | 'export' | 'manual' | 'webhook' | 'log_query' | 'scheduled_report' | 'agent'
export type AutomationPotential = 'high' | 'medium' | 'low'
export type CatalogSystemType =
  | 'cloud_provider'
  | 'identity_provider'
  | 'cmdb'
  | 'vulnerability_scanner'
  | 'security_tool'
  | 'siem'
  | 'pam_tool'
  | 'iga_platform'
  | 'ticketing'
  | 'code_repository'
  | 'backup_system'
  | 'network_scanner'
  | 'patch_management'
  | 'security_testing'
  | 'logging'
  | 'monitoring'
  | 'grc_platform'
  | 'hr_system'
  | 'spreadsheet'
  | 'document_management'
  | 'manual'

export interface CollectionInterface {
  domain: string
  title: string
  description?: string
  system_types: CatalogSystemType[]
  collection_method: CatalogCollectionMethod
  data_formats?: string[]
  typical_fields?: string[]
  example_systems?: string[]
  automation_potential?: AutomationPotential
  frequency_recommendation?: string
  maturity_range?: { min: EvidenceMaturityLevel; max: EvidenceMaturityLevel }
}

export interface CollectionInterfacesFile {
  [interfaceId: string]: CollectionInterface
}

// ============================================================================
// Evidence Templates Types (Issue #326 — Evidence Coaching Layer)
// ============================================================================

export interface EvidenceTemplateGuidance {
  summary: string
  acceptable_formats: string[]
  good_examples: string[]
  bad_examples: string[]
  redaction_warnings: string[]
  freshness: string
  auditor_tip: string
}

export interface EvidenceTemplate {
  evidence_id: EvidenceId
  title: string
  guidance: EvidenceTemplateGuidance
}

export interface EvidenceTemplatesFile {
  [evidenceId: EvidenceId]: EvidenceTemplate
}

export interface ResolvedArtifact {
  id: EvidenceId
  title: string
  domain: string
}

export interface EnrichedControl extends ControlGuidance {
  artifactsResolved: ResolvedArtifact[]
  frameworksResolved: {
    [framework: string]: string[]
  }
  frameworksCount: number
}

// Scoped Controls Types
export type ImplementationStatus =
  | 'not_started'
  | 'in_progress'
  | 'implemented'
  | 'ready_for_review'
  | 'monitored'
  | 'at_risk'
  | 'not_applicable'
  | 'deferred'

export type Priority = 'critical' | 'high' | 'medium' | 'low'

export type OwnerTeam =
  | 'Software Engineering'
  | 'Security Operations'
  | 'DevSecOps'
  | 'Cyber Security'
  | 'GRC'

// SCF C|P-CMM Maturity Levels (L0-L5) for Controls
export type MaturityLevel =
  | 'L0'  // Not Performed
  | 'L1'  // Performed Informally
  | 'L2'  // Planned & Tracked
  | 'L3'  // Well Defined
  | 'L4'  // Quantitatively Controlled
  | 'L5'  // Continuously Improving

// Evidence Collection Maturity Levels (L0-L5)
// Measures the maturity of evidence collection processes
export type EvidenceMaturityLevel =
  | 'L0'  // Non-Existent
  | 'L1'  // Ad Hoc
  | 'L2'  // Developing
  | 'L3'  // Defined
  | 'L4'  // Managed
  | 'L5'  // Optimising

export interface RelatedDocument {
  id: string
  url?: string
}

export interface EvidenceTracking {
  id?: string  // Database UUID
  is_tracked?: boolean
  method_of_collection?: string
  collecting_system?: string
  owner?: string
  frequency?: string
  comments?: string
  maturity_level?: EvidenceMaturityLevel  // Evidence collection maturity (L0-L5)
}

/**
 * Scoped control with organization-specific implementation details.
 * Note: Migrated from CCF to SCF in v4.0.0. scf_id replaces ccf_id.
 */
export interface ScopedControl {
  id?: string  // Database UUID
  scf_id: ControlId
  selected: boolean
  selection_reason?: string
  frameworks_driving_selection?: string[]
  implementation_status?: ImplementationStatus
  priority?: Priority
  owner?: OwnerTeam
  assigned_to?: string
  maturity_level?: MaturityLevel
  target_date?: string
  completion_date?: string
  implementation_notes?: string
  evidence_refs?: EvidenceId[]
  related_documentation?: RelatedDocument[]
  custom_fields?: Record<string, any>
  // SCF-specific fields (v4.0.0)
  control_weighting?: number
  validation_cadence?: string
  nist_csf_function?: NistCsfFunction
  control_question?: string
  pptdf_people?: boolean
  pptdf_process?: boolean
  pptdf_technology?: boolean
  pptdf_data?: boolean
  pptdf_facility?: boolean
}

export interface OrganizationInfo {
  name: string
  id: string
  created_at: string
  updated_at: string
}

export interface ScopingMetadata {
  version?: string
  total_controls?: number
  total_selected: number
  total_implemented: number
  last_updated?: string
}

export interface ScopedControlsFile {
  organizationId?: string  // Database UUID
  organization: OrganizationInfo
  scoped_controls: ScopedControl[]
  evidence_tracking: Record<EvidenceId, EvidenceTracking>
  metadata: ScopingMetadata
}

// ============================================================================
// System Registry Types
// ============================================================================

export type SystemType =
  | 'cloud_provider'
  | 'identity_provider'
  | 'ticketing'
  | 'logging'
  | 'security_tool'
  | 'code_repository'
  | 'document_management'
  | 'custom'

export type SystemStatus = 'active' | 'inactive' | 'deprecated'

export interface UserSimple {
  id: string
  email: string
  display_name?: string
}

export interface System {
  id: SystemId
  organization_id: string
  name: string
  system_type: SystemType
  category?: string
  description?: string
  vendor?: string
  status: SystemStatus
  connection_config?: Record<string, any>
  created_at: string
  updated_at: string
  created_by_user_id?: string
  updated_by_user_id?: string
  created_by?: UserSimple
  updated_by?: UserSimple
}

export interface SystemInput {
  name: string
  system_type: SystemType
  category?: string
  description?: string
  vendor?: string
  status?: SystemStatus
  connection_config?: Record<string, any>
}

export interface SystemUpdate {
  name?: string
  system_type?: SystemType
  category?: string
  description?: string
  vendor?: string
  status?: SystemStatus
  connection_config?: Record<string, any>
}

// ============================================================================
// System Evidence Capability Types
// ============================================================================

export type CapabilityStatus = 'potential' | 'configured' | 'active'
export type ConfidenceLevel = 'high' | 'medium' | 'low'
export type CollectionMethod = 'api' | 'export' | 'manual' | 'webhook' | 'scheduled' | 'integration'

export interface SystemEvidenceCapability {
  id: string
  system_id: SystemId
  evidence_id: EvidenceId
  capability_status: CapabilityStatus
  collection_method?: CollectionMethod
  confidence_level: ConfidenceLevel
  data_format?: string
  notes?: string
  created_at: string
  updated_at: string
  created_by_user_id?: string
  updated_by_user_id?: string
  created_by?: UserSimple
  updated_by?: UserSimple
  system?: SystemSimple
}

export interface SystemSimple {
  id: SystemId
  name: string
  system_type: SystemType
  vendor?: string
  status: SystemStatus
}

export interface CapabilityInput {
  evidence_id: EvidenceId
  capability_status?: CapabilityStatus
  collection_method?: CollectionMethod
  confidence_level?: ConfidenceLevel
  data_format?: string
  notes?: string
}

export interface CapabilityUpdate {
  capability_status?: CapabilityStatus
  collection_method?: CollectionMethod
  confidence_level?: ConfidenceLevel
  data_format?: string
  notes?: string
}

// Extended EvidenceTracking with system reference
export interface EvidenceTrackingWithSystem extends EvidenceTracking {
  system_id?: SystemId
  system?: SystemSimple
}

// ============================================================================
// Evidence Collection Suggestions Types
// ============================================================================

export interface CapableSystemInfo {
  system_id: SystemId
  name: string
  system_type: SystemType
  vendor?: string
  capability_status: CapabilityStatus
  collection_method?: CollectionMethod
  confidence_level: ConfidenceLevel
  notes?: string
}

export interface EvidenceRecommendation {
  system_id: SystemId
  system_name: string
  reason: string
}

export interface EvidenceSuggestionsResponse {
  evidence_id: EvidenceId
  currently_tracking?: string
  current_system_id?: SystemId
  capable_systems: CapableSystemInfo[]
  recommendation?: EvidenceRecommendation
  has_suggestions: boolean
  collection_guidance?: CollectionGuidanceResponse
}

// ============================================================================
// Collection Recipe Types
// ============================================================================

export interface RecipeStep {
  step: number
  action: string
  permissions_required?: string
  security_note?: string
  audit_note?: string
  vendor_docs_url?: string
}

export interface CollectionRecipe {
  title: string
  estimated_time?: string
  frequency?: string
  steps: RecipeStep[]
}

export type RecipeConfidence = 'system_specific' | 'vendor_generic' | 'type_generic'

export interface CollectionGuidanceResponse {
  system_id: string
  system_name: string
  system_type: string
  vendor?: string
  current_maturity: EvidenceMaturityLevel
  recipe?: CollectionRecipe
  recipe_confidence: RecipeConfidence
  maturity_appropriate_methods: { id: string; title: string; collection_method: string }[]
  next_level_preview?: CollectionRecipe
  alternatives_count: number
}

export interface SystemRecipeProduct {
  name: string
  recipes: Partial<Record<EvidenceMaturityLevel, CollectionRecipe>>
}

export interface SystemRecipeEntry {
  vendor: string
  system_type: string
  products?: Record<string, SystemRecipeProduct>
  recipes?: Partial<Record<EvidenceMaturityLevel, CollectionRecipe>>
}

export interface SystemCollectionRecipesFile {
  $schema: string
  version: string
  systems: Record<string, SystemRecipeEntry>
  generic_fallbacks: Record<string, Partial<Record<EvidenceMaturityLevel, CollectionRecipe>>>
}

// ============================================================================
// Recipe Feedback Types
// ============================================================================

export type RecipeFeedbackType = 'helpful' | 'not_matching'

export interface RecipeFeedbackCreate {
  system_type: string
  vendor?: string
  feedback_type: RecipeFeedbackType
  maturity_level: EvidenceMaturityLevel
}

// ============================================================================
// Evidence Gap Analysis Types
// ============================================================================

export interface EvidenceGapItem {
  evidence_id: EvidenceId
  evidence_title?: string
  required_by_controls: ControlId[]
  capable_systems: string[]
  capable_system_ids: SystemId[]
  recommended_action?: string
}

export interface EvidenceGapsResponse {
  total_gaps: number
  total_tracked: number
  total_evidence: number
  coverage_percentage: number
  gaps: EvidenceGapItem[]
}

// ============================================================================
// Framework Readiness Types
// ============================================================================

export type ReadinessGrade = 'excellent' | 'good' | 'fair' | 'needs-work'

export interface FrameworkMappingInput {
  controls: ControlId[]
  evidence: EvidenceId[]
}

export interface FrameworkReadinessRequest {
  frameworks: Record<string, FrameworkMappingInput>
}

export interface FrameworkReadinessItem {
  framework_name: string
  total_controls: number
  selected_controls: number
  implemented_controls: number
  in_progress_controls: number
  at_risk_controls: number
  not_started_controls: number
  total_evidence: number
  tracked_evidence: number
  implementation_score: number  // 0-100
  evidence_score: number        // 0-100
  readiness_score: number       // 0-100
  readiness_grade: ReadinessGrade
}

export interface FrameworkReadinessResponse {
  organization_id: string
  calculation_weights: {
    implementation: number
    evidence: number
  }
  frameworks: FrameworkReadinessItem[]
}

// ============================================================================
// Consultant Portal Types
// ============================================================================

/**
 * Consultant profile with associated client organisations
 */
export interface ConsultantProfile {
  id: string
  user_id: string
  email: string
  display_name: string
  is_consultant: boolean
  client_organizations: string[]  // Organization IDs
  created_at: string
  updated_at: string
}

/**
 * Summary of a client organisation for consultant dashboard
 */
export interface ClientSummary {
  organization_id: string
  organization_name: string
  awaiting_admin?: boolean
  framework_readiness_percent: number
  controls_implemented: number
  controls_total: number
  controls_in_progress: number
  controls_at_risk: number
  evidence_tracked: number
  evidence_total: number
  last_activity_date: string
  last_activity_by?: string
  primary_framework?: string
}

/**
 * Invitation to join an organisation as a client
 */
export interface ConsultantInvite {
  id: string
  email: string
  organization_name: string
  organization_id?: string
  invited_by_email: string
  invited_by_name?: string
  status: 'pending' | 'accepted' | 'expired' | 'cancelled'
  created_at: string
  expires_at: string
}

/**
 * Cross-organisation comparison metrics
 */
export interface OrgComparisonMetric {
  organization_id: string
  organization_name: string
  readiness_score: number
  implementation_score: number
  evidence_score: number
  controls_selected: number
  controls_implemented: number
  maturity_average: number
}

// ============================================================================
// Risk Assessment Types
// ============================================================================

/**
 * Treatment workflow status for risk assessments
 */
export type TreatmentStatus =
  | 'identified'
  | 'analysed'
  | 'treating'
  | 'treated'
  | 'accepted'
  | 'monitoring'

/**
 * Risk level based on likelihood × impact score
 */
export type RiskLevel = 'low' | 'medium' | 'high' | 'critical'

/**
 * Risk category from SCF risk codes
 */
export type RiskCategory = 'AC' | 'AM' | 'BC' | 'EX' | 'GV' | 'IR' | 'SA' | 'SC' | 'ORG'

/**
 * Risk code information from risk_codes.json catalog
 */
export interface RiskCodeInfo {
  category: RiskCategory
  title: string
  description: string
}

/**
 * Risk category information
 */
export interface RiskCategoryInfo {
  name: string
  color: string
}

/**
 * Risk codes catalog file structure
 */
export interface RiskCodesFile {
  categories: Partial<Record<RiskCategory, RiskCategoryInfo>>
  codes: Record<string, RiskCodeInfo>
}

/**
 * Custom risk definition from the database
 */
export interface CustomRiskDefinition {
  id: string
  organization_id: string
  risk_code: string
  title: string
  description: string
  category_name: string
  category_color: string
  created_at: string
  updated_at: string
  created_by_user_id?: string | null
}

/**
 * Input for creating a custom risk
 */
export interface CustomRiskCreate {
  title: string
  description: string
  category_name?: string
  category_color?: string
}

/**
 * Input for updating a custom risk definition
 */
export interface CustomRiskUpdate {
  title?: string
  description?: string
  category_name?: string
  category_color?: string
}

/**
 * Risk assessment record from database
 */
export interface RiskAssessment {
  id: string
  organization_id: string
  risk_code: string

  // Inherent risk (1-5 scale)
  likelihood?: number | null
  impact?: number | null

  // Residual risk (1-5 scale)
  residual_likelihood?: number | null
  residual_impact?: number | null

  // Treatment workflow
  treatment_status: TreatmentStatus
  treatment_plan?: string | null
  treatment_due_date?: string | null

  // Ownership
  owner_user_id?: string | null
  owner?: UserSimple | null

  // Review tracking
  next_review_date?: string | null
  notes?: string | null

  // Computed fields
  inherent_risk_score?: number | null
  residual_risk_score?: number | null
  inherent_risk_level?: RiskLevel | null
  residual_risk_level?: RiskLevel | null

  // Audit
  created_at: string
  updated_at: string
  created_by_user_id?: string | null
  updated_by_user_id?: string | null
}

/**
 * Input for creating/updating a risk assessment
 */
export interface RiskAssessmentInput {
  risk_code: string
  likelihood?: number | null
  impact?: number | null
  residual_likelihood?: number | null
  residual_impact?: number | null
  treatment_status?: TreatmentStatus
  treatment_plan?: string | null
  treatment_due_date?: string | null
  owner_user_id?: string | null
  next_review_date?: string | null
  notes?: string | null
}

/**
 * Input for partially updating a risk assessment
 */
export interface RiskAssessmentUpdate {
  likelihood?: number | null
  impact?: number | null
  residual_likelihood?: number | null
  residual_impact?: number | null
  treatment_status?: TreatmentStatus
  treatment_plan?: string | null
  treatment_due_date?: string | null
  owner_user_id?: string | null
  next_review_date?: string | null
  notes?: string | null
}

/**
 * A single cell in the 5x5 risk matrix
 */
export interface RiskMatrixCell {
  likelihood: number
  impact: number
  score: number
  level: RiskLevel
  risk_codes: string[]
  count: number
}

/**
 * Response for risk matrix endpoint
 */
export interface RiskMatrixResponse {
  organization_id: string
  matrix_type: 'inherent' | 'residual'
  cells: RiskMatrixCell[]
  total_assessed: number
  total_unassessed: number
  by_level: Record<RiskLevel, number>
}

/**
 * Summary statistics for risk assessments
 */
export interface RiskSummaryResponse {
  organization_id: string
  total_risks: number
  assessed_risks: number
  unassessed_risks: number

  // By level (inherent)
  inherent_low: number
  inherent_medium: number
  inherent_high: number
  inherent_critical: number

  // By level (residual)
  residual_low: number
  residual_medium: number
  residual_high: number
  residual_critical: number

  // By treatment status
  by_treatment_status: Record<string, number>
}

/**
 * Risk threshold configuration for an organisation
 */
export interface RiskThresholds {
  lowMax: number
  mediumMax: number
  highMax: number
}

/**
 * Organisation risk profile from the backend
 */
export interface RiskProfile {
  id: string
  organization_id: string
  low_max: number
  medium_max: number
  high_max: number
  acceptable_risk_level: RiskLevel
  auto_escalate_above: RiskLevel
  required_vendor_certifications: string
  preferred_vendor_certifications: string
  vendor_auto_approve_max: number
  vendor_auto_reject_min: number
  created_at: string
  updated_at: string
  updated_by_user_id?: string | null
}

/**
 * Input for updating a risk profile
 */
export interface RiskProfileUpdate {
  low_max?: number
  medium_max?: number
  high_max?: number
  acceptable_risk_level?: RiskLevel
  auto_escalate_above?: RiskLevel
  required_vendor_certifications?: string
  preferred_vendor_certifications?: string
  vendor_auto_approve_max?: number
  vendor_auto_reject_min?: number
}

/** Default risk thresholds matching backend defaults */
export const DEFAULT_RISK_THRESHOLDS: RiskThresholds = {
  lowMax: 4,
  mediumMax: 9,
  highMax: 16,
}

/**
 * Helper function to get risk level from score
 */
export function getRiskLevel(score: number, thresholds?: RiskThresholds): RiskLevel {
  const t = thresholds ?? DEFAULT_RISK_THRESHOLDS
  if (score <= t.lowMax) return 'low'
  if (score <= t.mediumMax) return 'medium'
  if (score <= t.highMax) return 'high'
  return 'critical'
}

/**
 * Helper function to get risk level color
 */
export function getRiskLevelColor(level: RiskLevel): string {
  switch (level) {
    case 'low': return '#22c55e'      // Green
    case 'medium': return '#eab308'   // Yellow
    case 'high': return '#f97316'     // Orange
    case 'critical': return '#ef4444' // Red
  }
}

/**
 * Likelihood labels for the 5-point scale
 */
export const LIKELIHOOD_LABELS: Record<number, string> = {
  1: 'Rare',
  2: 'Unlikely',
  3: 'Possible',
  4: 'Likely',
  5: 'Almost Certain'
}

/**
 * Impact labels for the 5-point scale
 */
export const IMPACT_LABELS: Record<number, string> = {
  1: 'Insignificant',
  2: 'Minor',
  3: 'Moderate',
  4: 'Major',
  5: 'Catastrophic'
}

/**
 * Treatment status labels
 */
export const TREATMENT_STATUS_LABELS: Record<TreatmentStatus, string> = {
  identified: 'Identified',
  analysed: 'Analysed',
  treating: 'Treating',
  treated: 'Treated',
  accepted: 'Accepted',
  monitoring: 'Monitoring'
}

// ============================================================================
// Vendor Management Types (TPRM)
// ============================================================================

/**
 * Vendor status workflow
 */
export type VendorStatus =
  | 'prospect'
  | 'active'
  | 'under_review'
  | 'approved'
  | 'suspended'
  | 'offboarded'

/**
 * Vendor criticality level
 */
export type VendorCriticality = 'low' | 'medium' | 'high' | 'critical'

/**
 * Vendor assessment type
 */
export type VendorAssessmentType = 'initial' | 'periodic' | 'triggered' | 'follow_up'

/**
 * Vendor assessment status
 */
export type VendorAssessmentStatusType = 'scheduled' | 'in_progress' | 'completed' | 'cancelled'

/**
 * Vendor certification status
 */
export type VendorCertificationStatusType = 'valid' | 'expired' | 'revoked' | 'pending'

/**
 * Data classification levels
 */
export type DataClassification = 'public' | 'internal' | 'confidential' | 'restricted'

/**
 * Vendor record from database
 */
export interface Vendor {
  id: string
  organization_id: string
  name: string
  description?: string | null
  website?: string | null
  category?: string | null
  status: VendorStatus
  criticality: VendorCriticality
  contact_name?: string | null
  contact_email?: string | null
  contact_phone?: string | null
  contract_start_date?: string | null
  contract_end_date?: string | null
  contract_value?: number | null
  risk_score?: number | null
  risk_level?: string | null
  data_classification?: DataClassification | null
  created_at: string
  updated_at: string
  created_by_user_id?: string | null
  updated_by_user_id?: string | null
  created_by?: UserSimple | null
  updated_by?: UserSimple | null
}

/**
 * Input for creating a new vendor
 */
export interface VendorInput {
  name: string
  description?: string | null
  website?: string | null
  category?: string | null
  status?: VendorStatus
  criticality?: VendorCriticality
  contact_name?: string | null
  contact_email?: string | null
  contact_phone?: string | null
  contract_start_date?: string | null
  contract_end_date?: string | null
  contract_value?: number | null
  risk_score?: number | null
  risk_level?: string | null
  data_classification?: DataClassification | null
}

/**
 * Input for partially updating a vendor
 */
export interface VendorUpdate {
  name?: string
  description?: string | null
  website?: string | null
  category?: string | null
  status?: VendorStatus
  criticality?: VendorCriticality
  contact_name?: string | null
  contact_email?: string | null
  contact_phone?: string | null
  contract_start_date?: string | null
  contract_end_date?: string | null
  contract_value?: number | null
  risk_score?: number | null
  risk_level?: string | null
  data_classification?: DataClassification | null
}

/**
 * Vendor assessment record
 */
export interface VendorAssessment {
  id: string
  vendor_id: string
  assessment_type: VendorAssessmentType
  assessment_date: string
  status: VendorAssessmentStatusType
  confidentiality_score?: number | null
  integrity_score?: number | null
  availability_score?: number | null
  breach_score?: number | null
  certification_score?: number | null
  cve_score?: number | null
  regulatory_score?: number | null
  data_handling_score?: number | null
  likelihood?: number | null
  impact?: number | null
  final_risk_score?: number | null
  risk_level?: string | null
  ai_analysis?: string | null
  findings?: string | null
  risk_rating?: string | null
  next_assessment_date?: string | null
  assessor_user_id?: string | null
  assessor?: UserSimple | null
  created_at: string
  updated_at: string
  created_by_user_id?: string | null
  updated_by_user_id?: string | null
  created_by?: UserSimple | null
  updated_by?: UserSimple | null
  // DPSIA Enhancement fields
  inherent_risk_score?: number | null
  inherent_risk_level?: string | null
  control_effectiveness_pct?: number | null
}

/**
 * Input for creating a vendor assessment
 */
export interface VendorAssessmentInput {
  assessment_type?: VendorAssessmentType
  assessment_date: string
  status?: VendorAssessmentStatusType
  confidentiality_score?: number | null
  integrity_score?: number | null
  availability_score?: number | null
  findings?: string | null
  risk_rating?: string | null
  next_assessment_date?: string | null
  assessor_user_id?: string | null
}

/**
 * Vendor certification record
 */
export interface VendorCertification {
  id: string
  vendor_id: string
  certification_name: string
  certification_body?: string | null
  certificate_number?: string | null
  status: VendorCertificationStatusType
  issue_date?: string | null
  expiry_date?: string | null
  scope?: string | null
  verification_url?: string | null
  created_at: string
  updated_at: string
  created_by_user_id?: string | null
  updated_by_user_id?: string | null
  created_by?: UserSimple | null
  updated_by?: UserSimple | null
}

/**
 * Input for creating a vendor certification
 */
export interface VendorCertificationInput {
  certification_name: string
  certification_body?: string | null
  certificate_number?: string | null
  status?: VendorCertificationStatusType
  issue_date?: string | null
  expiry_date?: string | null
  scope?: string | null
  verification_url?: string | null
}

/**
 * Vendor status labels
 */
export const VENDOR_STATUS_LABELS: Record<VendorStatus, string> = {
  prospect: 'Prospect',
  active: 'Active',
  under_review: 'Under Review',
  approved: 'Approved',
  suspended: 'Suspended',
  offboarded: 'Offboarded'
}

/**
 * Vendor criticality labels
 */
export const VENDOR_CRITICALITY_LABELS: Record<VendorCriticality, string> = {
  low: 'Low',
  medium: 'Medium',
  high: 'High',
  critical: 'Critical'
}

/**
 * Vendor status colours
 */
export const VENDOR_STATUS_COLORS: Record<VendorStatus, string> = {
  prospect: '#6b7280',
  active: '#3b82f6',
  under_review: '#f59e0b',
  approved: '#22c55e',
  suspended: '#ef4444',
  offboarded: '#9ca3af'
}

/**
 * Vendor criticality colours
 */
export const VENDOR_CRITICALITY_COLORS: Record<VendorCriticality, string> = {
  low: '#22c55e',
  medium: '#eab308',
  high: '#f97316',
  critical: '#ef4444'
}

// ---------------------------------------------------------------------------
// Vendor Research (Issue #59)
// ---------------------------------------------------------------------------

export type ResearchJobStatus = 'pending' | 'running' | 'completed' | 'partial' | 'failed'
export type RiskSignal = 'low' | 'medium' | 'high' | 'unknown'
export type ResearchSource = 'hibp' | 'cisa_kev' | 'nvd' | 'regulatory' | 'gdpr' | 'ico'

export interface VendorResearchTriggerRequest {
  domain_override?: string
}

export interface VendorResearchTriggerResponse {
  job_id: string
  vendor_id: string
  status: string
  domain: string
}

export interface VendorResearchStatusResponse {
  job_id: string
  vendor_id: string
  status: ResearchJobStatus
  source_statuses: Record<string, string>
  errors: Array<{ source: string; error: string }>
  started_at: string | null
  completed_at: string | null
  created_at: string | null
}

export interface VendorResearchResultResponse {
  job_id: string
  vendor_id: string
  status: ResearchJobStatus
  hibp_results: Record<string, unknown>
  cisa_kev_results: Record<string, unknown>
  cve_nvd_results: Record<string, unknown>
  regulatory_results: Record<string, unknown>
  summary: string | null
  risk_indicators: Record<string, RiskSignal>
  overall_risk_signal: RiskSignal | null
  source_statuses: Record<string, string>
  errors: Array<{ source: string; error: string }>
  researched_domain: string | null
  started_at: string | null
  completed_at: string | null
  created_at: string | null
}

export const RESEARCH_SOURCE_LABELS: Record<ResearchSource, string> = {
  hibp: 'Have I Been Pwned',
  cisa_kev: 'CISA KEV',
  nvd: 'CVE / NVD',
  regulatory: 'Regulatory',
  gdpr: 'GDPR',
  ico: 'ICO'
}

export const RISK_SIGNAL_COLORS: Record<RiskSignal, string> = {
  low: '#22c55e',
  medium: '#eab308',
  high: '#ef4444',
  unknown: '#9ca3af'
}

export const RISK_SIGNAL_LABELS: Record<RiskSignal, string> = {
  low: 'Low Risk',
  medium: 'Medium Risk',
  high: 'High Risk',
  unknown: 'Unknown'
}

// ---------------------------------------------------------------------------
// DPSIA Assessment (Lambda Integration)
// ---------------------------------------------------------------------------

export type DPSIAJobStatus = 'pending' | 'running' | 'completed' | 'failed'
export type DPSIARAGStatus = 'RED' | 'AMBER' | 'GREEN'
export type DPSIARecommendation = 'APPROVE' | 'CONDITIONAL_APPROVAL' | 'REJECT'

export interface DPSIATriggerRequest {
  assessment_type?: string
  services_used: string
  data_role?: string
  client_name?: string
  additional_context?: string
}

export interface DPSIATriggerResponse {
  job_id: string
  vendor_id: string
  status: string
}

export interface DPSIAStatusResponse {
  job_id: string
  vendor_id: string
  status: DPSIAJobStatus
  started_at: string | null
  completed_at: string | null
  created_at: string | null
  error_message: string | null
}

export interface DPSIAResultResponse {
  job_id: string
  vendor_id: string
  status: DPSIAJobStatus
  assessment_type: string | null
  data_role: string | null
  rag_status: DPSIARAGStatus | null
  recommendation: DPSIARecommendation | null
  risk_score: number | null
  risk_level: string | null
  executive_summary: string | null
  report_markdown: string | null
  report_json: Record<string, unknown> | null
  report_filename: string | null
  research_sources: string[] | null
  linked_assessment_id: string | null
  linked_report_id: string | null
  processing_time_ms: number | null
  error_message: string | null
  started_at: string | null
  completed_at: string | null
  created_at: string | null
}

export const DPSIA_RAG_COLORS: Record<DPSIARAGStatus, string> = {
  RED: '#ef4444',
  AMBER: '#f59e0b',
  GREEN: '#22c55e',
}

export const DPSIA_RAG_LABELS: Record<DPSIARAGStatus, string> = {
  RED: 'High Risk',
  AMBER: 'Medium Risk',
  GREEN: 'Low Risk',
}

export const DPSIA_RECOMMENDATION_LABELS: Record<DPSIARecommendation, string> = {
  APPROVE: 'Approve',
  CONDITIONAL_APPROVAL: 'Conditional Approval',
  REJECT: 'Reject',
}

export const DPSIA_RECOMMENDATION_COLORS: Record<DPSIARecommendation, string> = {
  APPROVE: '#22c55e',
  CONDITIONAL_APPROVAL: '#f59e0b',
  REJECT: '#ef4444',
}

// ---------------------------------------------------------------------------
// Vendor Risk Scoring (Issue #60)
// ---------------------------------------------------------------------------

/**
 * Vendor risk calculation result
 */
export interface VendorRiskCalculationResult {
  assessment_id: string
  vendor_id: string
  breach_score: number | null
  certification_score: number | null
  cve_score: number | null
  regulatory_score: number | null
  data_handling_score: number | null
  likelihood: number | null
  impact: number | null
  final_risk_score: number | null
  risk_level: string | null
  ai_analysis: string | null
  has_research_data: boolean
  dpsia_protected?: boolean
}

/**
 * Vendor risk matrix cell (for vendor risk view)
 */
export interface VendorRiskMatrixCell {
  likelihood: number
  impact: number
  score: number
  level: RiskLevel
  vendor_names: string[]
  vendor_ids: string[]
  count: number
}

/**
 * Vendor risk matrix response
 */
export interface VendorRiskMatrixResponse {
  organization_id: string
  matrix_type: 'vendor'
  cells: VendorRiskMatrixCell[]
  total_vendors: number
  total_assessed: number
  total_unassessed: number
  by_level: Record<RiskLevel, number>
}

// ---------------------------------------------------------------------------
// Vendor Reports (Issue #61)
// ---------------------------------------------------------------------------

/**
 * Vendor report record
 */
export interface VendorReport {
  id: string
  vendor_id: string
  assessment_id?: string | null
  organization_id: string
  report_type: string
  title: string
  content_markdown: string
  content_json?: Record<string, any> | null
  risk_score?: number | null
  risk_level?: string | null
  recommendation?: string | null
  version: number
  generated_by_user_id?: string | null
  generated_by?: UserSimple | null
  created_at: string
  updated_at: string
}

// ---------------------------------------------------------------------------
// Vendor Claim Verification (DPSIA Enhancement)
// ---------------------------------------------------------------------------

export type VerificationStatus = 'confirmed' | 'unverified' | 'discrepancy' | 'anomaly'
export type ClaimType = 'certification' | 'breach_disclosure' | 'compliance' | 'security_control'

export interface VendorClaimVerification {
  id: string
  vendor_id: string
  assessment_id?: string | null
  claim_type: ClaimType
  claim_description: string
  verification_status: VerificationStatus
  verification_source?: string | null
  verification_detail?: string | null
  evidence_url?: string | null
  created_at: string
  updated_at: string
}

export interface VendorClaimVerificationInput {
  claim_type: ClaimType
  claim_description: string
  verification_status?: VerificationStatus
  verification_source?: string | null
  verification_detail?: string | null
  evidence_url?: string | null
}

export const VERIFICATION_STATUS_COLORS: Record<VerificationStatus, string> = {
  confirmed: '#22c55e',
  unverified: '#eab308',
  discrepancy: '#ef4444',
  anomaly: '#f97316'
}

export const VERIFICATION_STATUS_LABELS: Record<VerificationStatus, string> = {
  confirmed: 'Confirmed',
  unverified: 'Unverified',
  discrepancy: 'Discrepancy',
  anomaly: 'Anomaly'
}

// ---------------------------------------------------------------------------
// CIA Control Breakdown (DPSIA Enhancement)
// ---------------------------------------------------------------------------

export type CIAPillar = 'confidentiality' | 'integrity' | 'availability'

export interface VendorCIAControl {
  id: string
  assessment_id: string
  pillar: CIAPillar
  control_name: string
  control_category?: string | null
  score?: number | null
  detail?: string | null
  evidence?: string | null
  created_at: string
}

export interface VendorCIAControlInput {
  pillar: CIAPillar
  control_name: string
  control_category?: string | null
  score?: number | null
  detail?: string | null
  evidence?: string | null
}

export const CIA_PILLAR_LABELS: Record<CIAPillar, string> = {
  confidentiality: 'Confidentiality',
  integrity: 'Integrity',
  availability: 'Availability'
}

export const CIA_PILLAR_COLORS: Record<CIAPillar, string> = {
  confidentiality: '#3b82f6',
  integrity: '#8b5cf6',
  availability: '#06b6d4'
}

// ---------------------------------------------------------------------------
// Vendor Action Items (DPSIA Enhancement)
// ---------------------------------------------------------------------------

export type ActionItemPriority = 'critical' | 'high' | 'medium' | 'low'
export type ActionItemStatus = 'open' | 'in_progress' | 'completed' | 'cancelled'

export interface VendorActionItem {
  id: string
  vendor_id: string
  assessment_id?: string | null
  report_id?: string | null
  title: string
  description?: string | null
  priority: ActionItemPriority
  status: ActionItemStatus
  category?: string | null
  owner_name?: string | null
  owner_user_id?: string | null
  due_date?: string | null
  completed_date?: string | null
  auto_generated: boolean
  created_at: string
  updated_at: string
}

export interface VendorActionItemInput {
  title: string
  description?: string | null
  priority?: ActionItemPriority
  status?: ActionItemStatus
  category?: string | null
  owner_name?: string | null
  owner_user_id?: string | null
  due_date?: string | null
  completed_date?: string | null
}

export const ACTION_PRIORITY_COLORS: Record<ActionItemPriority, string> = {
  critical: '#ef4444',
  high: '#f97316',
  medium: '#eab308',
  low: '#22c55e'
}

export const ACTION_STATUS_COLORS: Record<ActionItemStatus, string> = {
  open: '#3b82f6',
  in_progress: '#f59e0b',
  completed: '#22c55e',
  cancelled: '#9ca3af'
}

// ---------------------------------------------------------------------------
// Vendor Compensating Controls (DPSIA Enhancement)
// ---------------------------------------------------------------------------

export type EffectivenessRating = 'full' | 'partial' | 'minimal'

export interface VendorCompensatingControl {
  id: string
  vendor_id: string
  assessment_id?: string | null
  gap_description: string
  compensating_control: string
  effectiveness_rating: EffectivenessRating
  risk_reduction_notes?: string | null
  created_at: string
  updated_at: string
}

export interface VendorCompensatingControlInput {
  gap_description: string
  compensating_control: string
  effectiveness_rating?: EffectivenessRating
  risk_reduction_notes?: string | null
}

export const EFFECTIVENESS_COLORS: Record<EffectivenessRating, string> = {
  full: '#22c55e',
  partial: '#eab308',
  minimal: '#ef4444'
}

// ============================================================================
// Capability Theme Types (KSI-Aligned Posture)
// ============================================================================

export interface CapabilityThemePosture {
  monitored: number
  implemented: number
  ready_for_review: number
  in_progress: number
  not_started: number
  at_risk: number
  not_applicable: number
  deferred: number
}

export type AxisBand = 'Strong' | 'Moderate' | 'Developing'

export interface CapabilityThemeResponse {
  theme_code: string
  name: string
  description: string
  ksi_reference: string | null
  icon: string | null
  display_order: number
  total_controls: number
  scoped_controls: number
  posture: CapabilityThemePosture
  maturity_score: number | null
  // Multi-axis scoring (#549 Phase 1) — nullable for backward compat with pre-Phase-1 backends.
  implementation_coverage: number | null
  implementation_band: AxisBand | null
  maturity_band: AxisBand | null
  evidence_coverage: number | null
  evidence_coverage_band: AxisBand | null
  evidence_quality: number | null
  evidence_quality_band: AxisBand | null
  evidence_quality_warning: string | null
  composite_score: number | null
  composite_band: AxisBand | null
}

export interface CapabilityThemeListResponse {
  themes: CapabilityThemeResponse[]
}

export interface CapabilityThemeControlItem {
  scf_id: string
  control_name: string | null
  scf_domain: string | null
  selected: boolean
  implementation_status: string | null
  maturity_level: string | null
  relevance: string
}

export interface CapabilityThemeControlsResponse {
  theme_code: string
  theme_name: string
  controls: CapabilityThemeControlItem[]
  total: number
  offset: number
  limit: number
}

export interface CapabilityThemeEvidencePosture {
  theme_code: string
  controls_with_evidence: number
  total_evidence_files: number
  sufficient_count: number
  partial_count: number
  insufficient_count: number
  pending_count: number
  unassessed_count: number
  average_relevance_score: number | null
  evidence_confidence: 'strong' | 'moderate' | 'weak' | 'none'
}

export interface CapabilityThemeEvidencePostureResponse {
  themes: CapabilityThemeEvidencePosture[]
}

// Audit Log Types
export interface AuditLogEntry {
  id: string
  organization_id: string
  entity_type: string
  entity_id: string
  scf_id?: string
  action: string
  field_name?: string
  old_value?: string
  new_value?: string
  changed_by_user_id: string
  changed_by_email?: string
  changed_at: string
  ip_address?: string
  action_source?: string
  request_id?: string
}

export interface AuditLogListResponse {
  entries: AuditLogEntry[]
  total: number
  offset: number
  limit: number
}

// ---------------------------------------------------------------------------
// M4 (#574) — Per-window review + Frequency Health
// ---------------------------------------------------------------------------

/**
 * Mirror of backend ``EvidenceWindowAssessmentResponse`` (Pydantic) for the
 * fields the webclient consumes in the per-window review UI. Only includes
 * the columns surfaced by the review panel — the full EWA row carries many
 * more fields handled inside the M1a/M3 read paths. Review fields are all
 * Optional to remain backward compatible with pre-M4 serialisations.
 */
export interface EvidenceWindowAssessment {
  id: string
  organization_id: string
  evidence_id: string
  window_start: string
  window_end: string
  assessment_status: string
  relevance_score: number | null
  // M4 PR 1 review fields — null until reviewed.
  review_status: string | null
  reviewed_by_user_id: string | null
  reviewed_at: string | null
  review_notes: string | null
  // M4 PR 3 context fields — surface the AI's actual assessment in the
  // review panel so reviewers have a basis for approve/reject/revise.
  // All optional with safe defaults so pre-M4 callers that destructure the
  // legacy shape continue to compile.
  // Intentionally omitted: ``cost_cents``, ``input_token_count``,
  // ``output_token_count``. Those are SaaS-internal cost metrics and must
  // not leak into customer-facing dashboards.
  status?: string
  summary?: string | null
  findings?: Array<{
    control_id?: string
    severity?: string
    gap?: string
    suggestion?: string
    [key: string]: unknown
  }>
  artifact_type_coverage?: Record<string, {
    present: boolean
    [key: string]: unknown
  }>
  expected_artifact_types?: Array<{
    type?: string
    mandatory?: boolean
    [key: string]: unknown
  }>
  source_coverage?: Record<string, number>
  file_ids?: string[]
  frequency_used?: string
}

/**
 * Mirror of backend ``EvidenceWindowAssessmentSummary`` Pydantic model.
 *
 * ``total_cost_cents`` is intentionally omitted from this client mirror —
 * it is the SaaS provider's internal cost metric and must not surface to
 * customer-facing dashboards. Keep it as a backend-only field; if you ever
 * need it for an admin/operator surface, add a separate admin type.
 */
export interface EvidenceWindowAssessmentSummary {
  total_windows_assessed: number
  sufficient_count: number
  partial_count: number
  insufficient_count: number
  insufficient_sample_count: number
  pending_count: number
  error_count: number
  average_relevance_score: number | null
}

/** Mirror of backend response for ``POST .../window-assessments/refresh-stale``. */
export interface RefreshStaleWindowAssessmentsResponse {
  queued: number
  skipped: number
  candidates: number
  cap: number
  queued_evidence_ids: string[]
  skipped_detail: Array<{ evidence_id: string; reason: string }>
}

/** Mirror of backend ``FrequencyHealthItem`` Pydantic model (PR 2). */
export interface FrequencyHealthItem {
  evidence_id: string
  declared_frequency: string | null
  suggested_frequency: string | null
  observed_cadence_days: number | null
  confidence: string
  file_count: number
  misaligned: boolean
  reason: string
}

/** Mirror of backend ``FrequencyHealthResponse`` Pydantic model (PR 2). */
export interface FrequencyHealthResponse {
  organization_id: string
  computed_at: string
  evaluation_window_days: number
  total_evidence_ids_evaluated: number
  misaligned_count: number
  low_confidence_count: number
  items: FrequencyHealthItem[]
}
