/**
 * Catalog API Client
 * Fetches SCF catalog reference data from backend API instead of static JSON files.
 *
 * Migration: Replaces direct JSON file loading with API calls to:
 * - /api/catalog/bulk/controls - All controls with full metadata
 * - /api/catalog/bulk/evidence - All evidence in ERL format
 * - /api/catalog/domains - Domain definitions
 * - /api/catalog/assessment-objectives - Assessment objectives
 */

import type {
  ControlGuidance,
  ERLFile,
  FrameworkNameMap,
  AssessmentObjective,
} from '../types'

const API_BASE_URL = '/api'

// Token resolution is shared with apiClient.ts via ./authToken
import { getAuthToken, refreshOidcToken, OIDC_ENABLED } from './authToken'

/**
 * Generic fetch wrapper for catalog API calls
 */
async function catalogFetch<T>(endpoint: string): Promise<T> {
  const url = `${API_BASE_URL}${endpoint}`

  const doFetch = (bearer: string) =>
    fetch(url, {
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${bearer}`,
      },
    })

  let response = await doFetch(getAuthToken())

  // OIDC: retry once with a refreshed token if the id_token expired.
  if (response.status === 401 && OIDC_ENABLED) {
    const refreshed = await refreshOidcToken()
    if (refreshed) {
      response = await doFetch(refreshed)
    }
  }

  if (!response.ok) {
    throw new Error(`Catalog API Error: ${response.status} ${response.statusText}`)
  }

  return response.json()
}

// =============================================================================
// API Response Types (match backend catalog.py responses)
// =============================================================================

interface BulkControlsResponse {
  total: number
  controls: ControlGuidance[]
}

// Paginated controls response (from /api/catalog/controls)
export interface PaginatedControlsResponse {
  total: number
  limit: number
  offset: number
  controls: ControlGuidance[]
}

// Filter parameters for paginated controls
export interface ControlsPageParams {
  limit?: number
  offset?: number
  search?: string
  domain?: string
  csf_function?: string
  control_weighting?: number
}

interface CatalogDomainListResponse {
  total: number
  domains: CatalogDomain[]
}

interface CatalogDomain {
  identifier: string
  order: number
  name: string
  principle: string
  principle_intent: string | null
}

// =============================================================================
// Bulk Export Functions (for frontend initial load)
// =============================================================================

/**
 * Fetch all controls with full metadata from catalog API.
 * Uses bulk endpoint for efficiency (single request for all 1,451 controls).
 *
 * Returns controls in the same format as control_guidance.json
 */
export async function fetchBulkControls(): Promise<ControlGuidance[]> {
  const response = await catalogFetch<BulkControlsResponse>('/catalog/bulk/controls')
  return response.controls
}

/**
 * Fetch a page of controls with optional filtering.
 * Uses the paginated /catalog/controls endpoint for efficient loading.
 *
 * @param params - Pagination and filter parameters
 * @returns Paginated response with total count
 */
export async function fetchControlsPage(params: ControlsPageParams = {}): Promise<PaginatedControlsResponse> {
  const { limit = 50, offset = 0, search, domain, csf_function, control_weighting } = params

  const queryParams = new URLSearchParams()
  queryParams.set('limit', limit.toString())
  queryParams.set('offset', offset.toString())

  if (search) {
    queryParams.set('search', search)
  }
  if (domain) {
    queryParams.set('domain', domain)
  }
  if (csf_function) {
    queryParams.set('csf_function', csf_function)
  }
  if (control_weighting !== undefined) {
    queryParams.set('control_weighting', control_weighting.toString())
  }

  return catalogFetch<PaginatedControlsResponse>(`/catalog/controls?${queryParams.toString()}`)
}

/**
 * Fetch all evidence from catalog API in ERL format.
 * Returns the same format as erl.json (keyed by evidence_id).
 */
export async function fetchBulkEvidence(): Promise<ERLFile> {
  return catalogFetch<ERLFile>('/catalog/bulk/evidence')
}

/**
 * Fetch all domains from catalog API
 */
export async function fetchCatalogDomains(): Promise<CatalogDomain[]> {
  const response = await catalogFetch<CatalogDomainListResponse>('/catalog/domains')
  return response.domains
}

/**
 * Fetch assessment objectives for a specific control
 */
export async function fetchControlAssessmentObjectives(scfId: string): Promise<AssessmentObjective[]> {
  interface ControlAOResponse {
    scf_id: string
    control_name: string
    assessment_objective_count: number
    assessment_objectives: Array<{
      ao_id: string
      objective_text: string
      assessment_rigor: number | null
      ao_origins: string | null
    }>
  }

  const response = await catalogFetch<ControlAOResponse>(
    `/catalog/controls/${encodeURIComponent(scfId)}/assessment-objectives`
  )

  return response.assessment_objectives.map(ao => ({
    ao_id: ao.ao_id,
    scf_id: scfId,
    objective_text: ao.objective_text,
    assessment_rigor: ao.assessment_rigor || undefined,
    ao_origins: ao.ao_origins || undefined,
  }))
}

/**
 * Build framework name map from controls.
 * Derives framework display names from the framework_mappings keys.
 */
export function buildFrameworkNameMap(controls: ControlGuidance[]): FrameworkNameMap {
  const nameMap: FrameworkNameMap = {}

  // Known framework name mappings
  const knownMappings: Record<string, string> = {
    'nist_csf_2_0': 'NIST CSF 2.0',
    'nist_800_53_r5': 'NIST 800-53 Rev 5',
    'nist_800_53_r5_noc': 'NIST 800-53 Rev 5 (No Controls)',
    'nist_800_171_r3': 'NIST 800-171 Rev 3',
    'iso_27001_2022': 'ISO 27001:2022',
    'iso_27002_2022': 'ISO 27002:2022',
    'iso_27017_2015': 'ISO 27017:2015',
    'iso_27701_2025': 'ISO 27701:2025',
    'iso_42001_2023': 'ISO 42001:2023',
    'iso_22301_2019': 'ISO 22301:2019',
    'pci_dss_4_0_1': 'PCI DSS 4.0.1',
    'cis_controls_8': 'CIS Controls v8',
    'hipaa': 'HIPAA',
    'cmmc_2_0': 'CMMC 2.0',
    'csa_ccm_4': 'CSA CCM 4.0',
    'cobit_2019': 'COBIT 2019',
    'coso_2017': 'COSO 2017',
    'tisax_isa_6': 'TISAX ISA 6',
    'nist_ai_600_1': 'NIST AI 600-1',
    'nist_ai_100_1_ai_rmf_1_0': 'NIST AI RMF 1.0',
    'emea_eu_ai_act': 'EU AI Act',
    'emea_eu_dora': 'EU DORA',
    'emea_eu_nis2': 'EU NIS2',
    'emea_eu_psd2': 'EU PSD2',
    'us_hipaa_security_rule_nist_sp_800_66_r2': 'HIPAA Security Rule',
    'us_hipaa_administrative_simplification_2013': 'HIPAA Admin',
    'us_nerc_cip_2024': 'NERC CIP 2024',
    'us_dfars_cybersecurity_252_204_70xx': 'DFARS Cyber',
    'us_glba_cfr_314_2023': 'GLBA',
    'us_cms_mars_e_2_0': 'CMS MARS-E 2.0',
    'us_cjis_security_policy_5_9_3': 'CJIS 5.9.3',
    'bsi_standard_200_1': 'BSI 200-1',
    // Risk and threat mappings
    'risk_threat_summary': 'Risk & Threat Summary',
    'control_threat_summary': 'Control Threat Summary',
  }

  // Collect all unique framework keys from controls
  for (const control of controls) {
    if (control.framework_mappings) {
      for (const key of Object.keys(control.framework_mappings)) {
        if (!nameMap[key]) {
          // Use known mapping or transform the key
          nameMap[key] = knownMappings[key] || formatFrameworkKey(key)
        }
      }
    }
  }

  return nameMap
}

/**
 * Format a framework key into a readable name
 */
function formatFrameworkKey(key: string): string {
  // Remove common suffixes
  let name = key.replace(/_ref$/, '')

  // Handle risk/threat prefixes
  if (name.startsWith('risk_r_')) {
    return `Risk ${name.replace('risk_r_', 'R-').toUpperCase()}`
  }
  if (name.startsWith('threat_')) {
    return `Threat ${name.replace('threat_', '').toUpperCase()}`
  }

  // Replace underscores with spaces
  name = name.replace(/_/g, ' ')

  // Capitalize words
  name = name.replace(/\b\w/g, c => c.toUpperCase())

  return name
}

// =============================================================================
// Framework List API (for Scope by Framework feature)
// =============================================================================

export interface FrameworkInfo {
  id: string
  name: string
  control_count: number
}

interface FrameworkListResponse {
  total: number
  frameworks: FrameworkInfo[]
}

/**
 * Fetch all available frameworks with control counts.
 * Used by the "Scope by Framework" feature to show which frameworks
 * can be bulk-scoped.
 *
 * @param includeInternal - Include internal SCF mappings (risk_, threat_, etc.)
 */
export async function fetchFrameworks(includeInternal = false): Promise<FrameworkInfo[]> {
  const params = new URLSearchParams()
  if (includeInternal) {
    params.set('include_internal', 'true')
  }
  const queryString = params.toString()
  const response = await catalogFetch<FrameworkListResponse>(
    `/catalog/frameworks${queryString ? `?${queryString}` : ''}`
  )
  return response.frameworks
}
