/**
 * API Client for CG SCF Backend
 * Handles all HTTP communication with the FastAPI backend
 */

// Use relative URL - will be proxied by frontend service
// In local dev: Vite dev server proxies /api/* to backend:8000
// In production: Nginx proxies /api/* to backend Cloud Run service
const API_BASE_URL = '/api'

// Authentication configuration — token resolution lives in ./authToken
import {
  getAuthToken,
  getGoogleToken,
  clearAuthSession,
  refreshOidcToken,
  OIDC_ENABLED,
  GOOGLE_AUTH_ENABLED,
  API_KEY,
} from './authToken'

const DEBUG_API = import.meta.env.VITE_DEBUG_API === 'true'

// Validate API key configuration
if (!GOOGLE_AUTH_ENABLED && !API_KEY) {
  console.error(
    '❌ VITE_API_KEY environment variable is not set!\n' +
    '   Google auth is disabled, so API key is required.\n' +
    '   1. Create a file: webclient/.env\n' +
    '   2. Add: VITE_API_KEY=your-secret-api-key-here\n' +
    '   3. Make sure it matches the API_KEY in your backend .env file\n' +
    '   4. Restart the frontend server'
  )
} else if (GOOGLE_AUTH_ENABLED && !API_KEY) {
  console.log('ℹ️  Google auth enabled, API key is optional (using Google tokens)')
}

// Organization ID is managed via localStorage by OrganizationContext
// This provides persistence across page refreshes and coordination with the context
const ORG_STORAGE_KEY = 'scf_current_org_id'

/**
 * Generic fetch wrapper with error handling and authentication
 * Supports dual authentication: Google OAuth token (priority) or API key (fallback)
 */
async function apiFetch<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  const url = `${API_BASE_URL}${endpoint}`

  const token = getAuthToken()

  // Debug logging (only when VITE_DEBUG_API=true)
  if (DEBUG_API) {
    console.debug(`🔐 API call to ${endpoint}`)
    console.debug(`   OIDC enabled: ${OIDC_ENABLED} | Google auth enabled: ${GOOGLE_AUTH_ENABLED}`)
    console.debug(`   Token last 4 chars: ...${token?.slice(-4) || '[NONE]'}`)
  }

  if (!token) {
    console.warn('⚠️  No authentication token available')
  }

  const buildInit = (bearer: string): RequestInit => ({
    ...options,
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${bearer}`,
      ...options.headers,
    },
  })

  let response = await fetch(url, buildInit(token))

  // OIDC: a 401 may just mean the id_token expired — attempt one silent refresh
  // and retry the request once before treating it as a hard auth failure.
  if (response.status === 401 && OIDC_ENABLED) {
    const refreshed = await refreshOidcToken()
    if (refreshed) {
      response = await fetch(url, buildInit(refreshed))
    }
  }

  if (!response.ok) {
    const errorText = await response.text()
    let errorMessage = `API Error: ${response.status} ${response.statusText}`

    try {
      const errorJson = JSON.parse(errorText)
      const detail = errorJson.detail
      if (typeof detail === 'string') {
        errorMessage = detail
      } else if (Array.isArray(detail)) {
        // FastAPI 422 validation errors: [{loc, msg, type}, ...]
        errorMessage = detail.map((e: { msg?: string }) => e.msg || 'Validation error').join('; ')
      } else if (detail && typeof detail === 'object' && typeof detail.detail === 'string') {
        // Structured cap-exceeded shape: { detail: { detail: "...", cap: "..." } }
        errorMessage = detail.detail
      } else if (typeof errorJson.error === 'string') {
        errorMessage = errorJson.error
      }
    } catch {
      // If error is not JSON, use status text
    }

    // Handle 403 account_not_provisioned specifically - BEFORE generic auth handler
    if (response.status === 403) {
      try {
        const errorJson = JSON.parse(errorText)
        const detail = errorJson.detail || errorJson
        if (detail?.error === 'account_not_provisioned') {
          console.warn('⚠️ Account not provisioned - redirecting to signup')
          clearAuthSession()
          const redirectUrl = detail.redirect || 'https://scfcontrolsplatform.com/signup'
          window.location.href = redirectUrl
          throw new Error('REDIRECT_IN_PROGRESS')
        }
      } catch (parseError) {
        // If redirect is in progress, propagate that error
        if (parseError instanceof Error && parseError.message === 'REDIRECT_IN_PROGRESS') {
          throw parseError
        }
        // Continue to generic handling if parse fails
      }
    }

    // Handle authentication errors.
    // In OIDC mode a 403 is deliberately NOT treated as an auth failure: the
    // silent refresh above only runs for 401s, account_not_provisioned was
    // already handled, so a 403 here is an ordinary RBAC "permission denied"
    // for a signed-in user. Clearing the session on it would sign out a valid
    // user and can loop sign-in → load → 403 → sign-out. Let it fall through to
    // the thrown apiError so the calling component surfaces the permission error.
    const isAuthError = OIDC_ENABLED
      ? response.status === 401
      : response.status === 401 || response.status === 403
    if (isAuthError) {
      // OIDC: refresh was already attempted above; a persistent 401 means
      // the session is dead — clear it and return to the sign-in screen.
      if (OIDC_ENABLED) {
        console.warn('⚠️  OIDC session rejected after refresh, clearing session')
        clearAuthSession()
        window.location.reload()
        throw new Error('Session expired. Please sign in again.')
      }

      // If Google token failed and Google auth is enabled, clear it and trigger re-authentication
      const googleToken = getGoogleToken()
      if (googleToken && GOOGLE_AUTH_ENABLED) {
        // Check if error message indicates token expiration
        const isExpired = errorMessage.toLowerCase().includes('expired') ||
                         errorMessage.toLowerCase().includes('token expired')

        if (isExpired) {
          console.warn('⚠️  Google access token expired (tokens expire after ~1 hour). Please sign in again.')
        } else {
          console.warn('⚠️  Google token invalid or rejected by backend, clearing session')
        }

        clearAuthSession()
        // Trigger page reload to show sign-in screen
        window.location.reload()
        throw new Error('Session expired. Please sign in again.')
      }

      console.error(
        '🔒 Authentication failed!\n' +
        `   Error: ${errorMessage}\n` +
        '   Possible causes:\n' +
        '   • VITE_API_KEY is not set in webclient/.env\n' +
        '   • API key does not match backend API_KEY\n' +
        '   • Backend authentication is not configured\n' +
        '   See FRONTEND_AUTH_SETUP.md for instructions.'
      )
      errorMessage = `Authentication failed: ${errorMessage}. Check console for details.`
    }

    // Preserve the HTTP status on the thrown error so callers can branch on it
    // (e.g. VendorPicker distinguishing 409 duplicate from 403 tier-cap).
    // Additive and backward compatible — existing callers only read `.message`.
    const apiError = new Error(errorMessage) as Error & { status?: number }
    apiError.status = response.status
    throw apiError
  }

  // Handle 204 No Content (e.g. DELETE endpoints) — no body to parse
  if (response.status === 204 || response.headers.get('content-length') === '0') {
    return undefined as T
  }
  return response.json()
}

/**
 * Fire a request and, in OIDC mode, transparently retry once on a 401 after a
 * silent token refresh. `doFetch` receives the Bearer token to use and must
 * build and send the request fresh on each call, so FormData/multipart bodies
 * are re-sent correctly on the retry. Mirrors the 401 → refresh → retry logic
 * baked into apiFetch, for the raw-fetch/upload paths that bypass apiFetch.
 */
async function fetchWithOidcRetry(
  doFetch: (bearer: string) => Promise<Response>
): Promise<Response> {
  let response = await doFetch(getAuthToken())
  if (response.status === 401 && OIDC_ENABLED) {
    const refreshed = await refreshOidcToken()
    if (refreshed) {
      response = await doFetch(refreshed)
    }
  }
  return response
}

/**
 * Raw fetch wrapper that returns the Response object without JSON parsing.
 * Used for endpoints that return binary data (PDF, DOCX, etc.).
 */
async function apiFetchRaw(
  endpoint: string,
  options: RequestInit = {}
): Promise<Response> {
  const url = `${API_BASE_URL}${endpoint}`

  const response = await fetchWithOidcRetry((bearer) =>
    fetch(url, {
      ...options,
      headers: {
        'Authorization': `Bearer ${bearer}`,
        ...options.headers,
      },
    })
  )

  if (!response.ok) {
    const errorText = await response.text()
    let errorMessage = `API Error: ${response.status} ${response.statusText}`

    try {
      const errorJson = JSON.parse(errorText)
      const detail = errorJson.detail
      if (typeof detail === 'string') {
        errorMessage = detail
      } else if (Array.isArray(detail)) {
        errorMessage = detail.map((e: { msg?: string }) => e.msg || 'Validation error').join('; ')
      } else if (typeof errorJson.error === 'string') {
        errorMessage = errorJson.error
      }
    } catch {
      // If error is not JSON, use status text
    }

    throw new Error(errorMessage)
  }

  return response
}

/**
 * Organization API
 */
export interface Organization {
  id: string
  name: string
  slug: string
  created_at: string
  updated_at: string
}

export async function getOrganizations(): Promise<Organization[]> {
  return apiFetch<Organization[]>('/organizations')
}

export async function getOrganization(orgId: string): Promise<Organization> {
  return apiFetch<Organization>(`/organizations/${orgId}`)
}

// Organization Settings
export interface OrganizationSettingsResponse {
  owner_teams: string[]
  is_trust_portal_enabled: boolean
  trust_portal_description: string | null
}

export async function fetchOrganizationSettings(
  orgId?: string
): Promise<OrganizationSettingsResponse> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<OrganizationSettingsResponse>(
    `/organizations/${orgId}/settings`
  )
}

export async function updateOrganizationSettings(
  orgId: string,
  settings: Partial<OrganizationSettingsResponse>
): Promise<OrganizationSettingsResponse> {
  return apiFetch<OrganizationSettingsResponse>(
    `/organizations/${orgId}/settings`,
    {
      method: 'PATCH',
      body: JSON.stringify(settings),
    }
  )
}

// Organization Logo
export interface OrganizationLogoResponse {
  filename: string | null
  content_type: string | null
  size_bytes: number
  updated_at: string | null
}

/**
 * Fetch the organization logo as a Blob (null when no logo is set).
 * Uses raw fetch because apiFetch is JSON-only.
 */
export async function fetchOrganizationLogoBlob(orgId: string): Promise<Blob | null> {
  const response = await fetch(`${API_BASE_URL}/organizations/${orgId}/logo`, {
    headers: { 'Authorization': `Bearer ${getAuthToken()}` },
  })
  if (response.status === 404) return null
  if (!response.ok) {
    throw new Error(`Failed to load organization logo: ${response.status}`)
  }
  return response.blob()
}

/**
 * Upload/replace the organization logo (admin only).
 * Uses raw fetch: multipart bodies must not get a manual Content-Type header.
 */
export async function uploadOrganizationLogo(orgId: string, file: File): Promise<OrganizationLogoResponse> {
  const formData = new FormData()
  formData.append('file', file)
  const response = await fetch(`${API_BASE_URL}/organizations/${orgId}/logo`, {
    method: 'PUT',
    headers: { 'Authorization': `Bearer ${getAuthToken()}` },
    body: formData,
  })
  if (!response.ok) {
    let message = `Logo upload failed: ${response.status}`
    try {
      const body = await response.json()
      if (typeof body.detail === 'string') message = body.detail
    } catch {
      // keep the status message
    }
    throw new Error(message)
  }
  return response.json()
}

export async function deleteOrganizationLogo(orgId: string): Promise<void> {
  await apiFetch<void>(`/organizations/${orgId}/logo`, { method: 'DELETE' })
}

/**
 * Get the current organization.
 *
 * Reads from localStorage (managed by OrganizationContext).
 * Falls back to first accessible org if no org is set.
 *
 * @throws Error if no organizations are accessible
 */
export async function getCurrentOrganization(): Promise<Organization> {
  // First check localStorage (set by OrganizationContext)
  let orgId = localStorage.getItem(ORG_STORAGE_KEY)

  if (!orgId) {
    // Fallback: fetch orgs and use first one
    const orgs = await getOrganizations()
    if (orgs.length === 0) {
      throw new Error('No organizations found')
    }
    orgId = orgs[0].id
    // Save to localStorage for persistence
    localStorage.setItem(ORG_STORAGE_KEY, orgId)
  }

  return getOrganization(orgId)
}

/**
 * Set the current organization ID.
 *
 * Updates localStorage which is read by OrganizationContext.
 * Prefer using OrganizationContext's switchOrganization() for full context updates.
 */
export function setCurrentOrganization(orgId: string) {
  localStorage.setItem(ORG_STORAGE_KEY, orgId)
}

/**
 * Get all members of an organization, returned as UserSimple[].
 * Calls GET /api/organizations/{org_id}/members and extracts the nested user object.
 */
export async function getOrgMembers(orgId: string): Promise<import('../types').UserSimple[]> {
  const members = await apiFetch<Array<{ user: import('../types').UserSimple | null }>>(
    `/organizations/${orgId}/members`
  )
  return members
    .filter((m): m is { user: import('../types').UserSimple } => m.user !== null)
    .map(m => m.user)
}

/**
 * Scoped Controls API
 * Note: Migrated from CCF to SCF in v4.0.0. scf_id replaces ccf_id.
 */
export interface ScopedControl {
  id: string
  organization_id: string
  scf_id: string
  selected: boolean
  selection_reason?: string | null
  implementation_status?: string | null
  priority?: string | null
  owner?: string | null
  assigned_to?: string | null
  maturity_level?: string | null
  target_date?: string | null
  completion_date?: string | null
  implementation_notes?: string | null
  related_documentation?: Record<string, any> | null
  custom_fields?: Record<string, any> | null
  created_at: string
  updated_at: string
  // SCF v4 fields
  control_weighting?: number | null
  validation_cadence?: string | null
  nist_csf_function?: string | null
  control_question?: string | null
  pptdf_people?: boolean | null
  pptdf_process?: boolean | null
  pptdf_technology?: boolean | null
  pptdf_data?: boolean | null
  pptdf_facility?: boolean | null
}

export interface ScopedControlInput {
  scf_id: string
  selected?: boolean
  selection_reason?: string | null
  implementation_status?: string | null
  priority?: string | null
  owner?: string | null
  assigned_to?: string | null
  maturity_level?: string | null
  target_date?: string | null
  completion_date?: string | null
  implementation_notes?: string | null
  related_documentation?: Record<string, any> | null
  custom_fields?: Record<string, any> | null
  // SCF v4 fields
  control_weighting?: number | null
  validation_cadence?: string | null
  nist_csf_function?: string | null
  control_question?: string | null
}

export async function getScopedControls(orgId?: string): Promise<ScopedControl[]> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<ScopedControl[]>(`/organizations/${orgId}/scoped-controls`)
}

export async function getScopedControl(scfId: string, orgId?: string): Promise<ScopedControl> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<ScopedControl>(`/organizations/${orgId}/scoped-controls/${scfId}`)
}

export async function createOrUpdateScopedControl(
  control: ScopedControlInput,
  orgId?: string
): Promise<ScopedControl> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<ScopedControl>(
    `/organizations/${orgId}/scoped-controls`,
    {
      method: 'POST',
      body: JSON.stringify(control),
    }
  )
}

export async function deleteScopedControl(scfId: string, orgId?: string): Promise<void> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  await apiFetch<{ success: boolean; message: string }>(
    `/organizations/${orgId}/scoped-controls/${scfId}`,
    { method: 'DELETE' }
  )
}

/**
 * Batch Scoped Controls API
 * Applies multiple create/update operations in a single transaction.
 */
export interface BatchScopedControlOperation {
  scf_id: string
  selected?: boolean
  implementation_status?: string
  selection_reason?: string
}

export interface BatchScopedControlResponse {
  updated: number
  created: number
  failed: number
  errors: string[]
  controls: ScopedControl[]
}

export async function batchUpdateScopedControls(
  operations: BatchScopedControlOperation[],
  orgId?: string
): Promise<BatchScopedControlResponse> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<BatchScopedControlResponse>(
    `/organizations/${orgId}/scoped-controls/batch`,
    {
      method: 'POST',
      body: JSON.stringify({ operations }),
    }
  )
}

/**
 * Bulk Scope by Framework API
 * Adds all controls mapped to specified framework(s) to the organization's scope.
 * Operation is ADDITIVE ONLY - existing scoped controls are never modified.
 */
export interface BulkScopeFrameworkRequest {
  frameworks: string[]
  selection_reason?: string
}

export interface BulkScopeFrameworkResponse {
  success: boolean
  added: number
  updated: number
  skipped: number
  total: number
  frameworks_processed: string[]
  message: string
}

export async function bulkScopeByFramework(
  request: BulkScopeFrameworkRequest,
  orgId?: string
): Promise<BulkScopeFrameworkResponse> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<BulkScopeFrameworkResponse>(
    `/organizations/${orgId}/scoped-controls/bulk-scope-framework`,
    {
      method: 'POST',
      body: JSON.stringify(request),
    }
  )
}

/**
 * Bulk Unscope by Framework API
 * Removes controls mapped to specified framework(s) from scope, but only if
 * they have no overlap with other in-scope frameworks.
 */
export interface BulkUnscopeFrameworkRequest {
  frameworks: string[]
  removal_reason?: string
}

export interface BulkUnscopeFrameworkResponse {
  success: boolean
  removed: number
  protected: number
  already_out_of_scope: number
  total: number
  protected_by: Record<string, number>
  frameworks_processed: string[]
  message: string
}

export async function bulkUnscopeByFramework(
  request: BulkUnscopeFrameworkRequest,
  orgId?: string
): Promise<BulkUnscopeFrameworkResponse> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<BulkUnscopeFrameworkResponse>(
    `/organizations/${orgId}/scoped-controls/bulk-unscope-framework`,
    {
      method: 'POST',
      body: JSON.stringify(request),
    }
  )
}

/**
 * Reset Scope API
 * Removes ALL controls from scope. Destructive operation requiring admin role.
 */
export interface ResetScopeResponse {
  success: boolean
  removed: number
  message: string
}

export async function resetAllScope(
  orgId?: string
): Promise<ResetScopeResponse> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<ResetScopeResponse>(
    `/organizations/${orgId}/scoped-controls/reset-scope`,
    {
      method: 'POST',
    }
  )
}

/**
 * Scoped Controls Stats API
 * Server-side aggregated counts for the stats bar
 */
export interface ScopedControlStatsResponse {
  total_controls: number
  in_scope: number
  implemented: number
  not_started: number
  in_progress: number
  not_applicable: number
  at_risk: number
  deferred: number
  ready_for_review: number
  monitored: number
}

export async function fetchScopedControlStats(
  orgId?: string
): Promise<ScopedControlStatsResponse> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<ScopedControlStatsResponse>(
    `/organizations/${orgId}/scoped-controls/stats`
  )
}

/**
 * Paginated Scoped Controls API
 * Server-side filtering and pagination for Control Scoping page
 */
export interface ScopedControlWithCatalog {
  scf_id: string
  scf_domain: string
  control_name: string
  control_description: string
  control_question?: string | null
  validation_cadence?: string | null
  control_weighting?: number | null
  nist_csf_function?: string | null
  evidence_requests: string[]
  framework_mappings: Record<string, string[]>
  // Scoping status
  is_scoped: boolean
  selected: boolean
  implementation_status?: string | null
  selection_reason?: string | null
  // Extended data
  pptdf_applicability: {
    people: boolean
    process: boolean
    technology: boolean
    data: boolean
    facility: boolean
  }
  cmm_maturity: {
    level_0?: string | null
    level_1?: string | null
    level_2?: string | null
    level_3?: string | null
    level_4?: string | null
    level_5?: string | null
  }
  business_size_guidance: {
    micro_small?: string | null
    small?: string | null
    medium?: string | null
    large?: string | null
    enterprise?: string | null
  }
  scrm_focus: {
    tier1_strategic: boolean
    tier2_operational: boolean
    tier3_tactical: boolean
  }
  risk_threat_mapping: {
    risk_codes: string[]
    threat_codes: string[]
  }
}

export interface PaginatedScopedControlsResponse {
  total: number
  limit: number
  offset: number
  controls: ScopedControlWithCatalog[]
}

export interface ScopedControlsPageParams {
  limit?: number
  offset?: number
  scope_status?: 'in_scope' | 'out_of_scope' | 'all'
  search?: string
  domain?: string
  csf_function?: string
  control_weighting?: number
  framework?: string
}

export async function fetchScopedControlsPage(
  params: ScopedControlsPageParams = {},
  orgId?: string
): Promise<PaginatedScopedControlsResponse> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }

  const queryParams = new URLSearchParams()
  if (params.limit) queryParams.set('limit', params.limit.toString())
  if (params.offset) queryParams.set('offset', params.offset.toString())
  if (params.scope_status) queryParams.set('scope_status', params.scope_status)
  if (params.search) queryParams.set('search', params.search)
  if (params.domain) queryParams.set('domain', params.domain)
  if (params.csf_function) queryParams.set('csf_function', params.csf_function)
  if (params.control_weighting !== undefined) queryParams.set('control_weighting', params.control_weighting.toString())
  if (params.framework) queryParams.set('framework', params.framework)

  const queryString = queryParams.toString()
  const endpoint = `/organizations/${orgId}/scoped-controls-paginated${queryString ? `?${queryString}` : ''}`

  return apiFetch<PaginatedScopedControlsResponse>(endpoint)
}

/**
 * Evidence Tracking API
 */
export interface EvidenceTracking {
  id: string
  organization_id: string
  evidence_id: string
  is_tracked?: boolean | null
  method_of_collection?: string | null
  collecting_system?: string | null
  owner?: string | null
  frequency?: string | null
  comments?: string | null
  maturity_level?: string | null
  created_at: string
  updated_at: string
}

export interface EvidenceTrackingInput {
  evidence_id: string
  is_tracked?: boolean | null
  method_of_collection?: string | null
  collecting_system?: string | null
  owner?: string | null
  frequency?: string | null
  comments?: string | null
  maturity_level?: string | null
}

export async function getEvidenceTracking(orgId?: string): Promise<EvidenceTracking[]> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<EvidenceTracking[]>(`/organizations/${orgId}/evidence-tracking`)
}

export async function createOrUpdateEvidenceTracking(
  tracking: EvidenceTrackingInput,
  orgId?: string
): Promise<EvidenceTracking> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<EvidenceTracking>(
    `/organizations/${orgId}/evidence-tracking`,
    {
      method: 'POST',
      body: JSON.stringify(tracking),
    }
  )
}

/**
 * Systems Registry API
 */
import type {
  System,
  SystemInput,
  SystemUpdate,
  SystemType,
  SystemStatus,
  SystemEvidenceCapability,
  CapabilityInput,
  CapabilityUpdate,
  EvidenceSuggestionsResponse,
  EvidenceGapsResponse,
  FrameworkReadinessRequest,
  FrameworkReadinessResponse,
} from '../types'

export async function getSystems(orgId?: string): Promise<System[]> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<System[]>(`/organizations/${orgId}/systems`)
}

export async function getSystemsFiltered(
  filters: { system_type?: SystemType; status?: SystemStatus; vendor_id?: string },
  orgId?: string
): Promise<System[]> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  const params = new URLSearchParams()
  if (filters.system_type) params.append('system_type', filters.system_type)
  if (filters.status) params.append('status', filters.status)
  if (filters.vendor_id) params.append('vendor_id', filters.vendor_id)
  const queryString = params.toString()
  return apiFetch<System[]>(`/organizations/${orgId}/systems${queryString ? `?${queryString}` : ''}`)
}

export async function getSystem(systemId: string, orgId?: string): Promise<System> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<System>(`/organizations/${orgId}/systems/${systemId}`)
}

export async function getSystemByName(name: string, orgId?: string): Promise<System> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<System>(`/organizations/${orgId}/systems/by-name/${encodeURIComponent(name)}`)
}

export async function createSystem(system: SystemInput, orgId?: string): Promise<System> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<System>(
    `/organizations/${orgId}/systems`,
    {
      method: 'POST',
      body: JSON.stringify(system),
    }
  )
}

export async function updateSystem(
  systemId: string,
  updates: SystemUpdate,
  orgId?: string
): Promise<System> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<System>(
    `/organizations/${orgId}/systems/${systemId}`,
    {
      method: 'PATCH',
      body: JSON.stringify(updates),
    }
  )
}

export async function deleteSystem(systemId: string, orgId?: string): Promise<void> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  await apiFetch<{ success: boolean; message: string }>(
    `/organizations/${orgId}/systems/${systemId}`,
    { method: 'DELETE' }
  )
}

/**
 * System Catalog API (systems knowledge catalog — template picker)
 */
export async function getSystemCatalogTemplates(
  params?: { search?: string; systemType?: string }
): Promise<import('../types').SystemCatalogTemplate[]> {
  const qs = new URLSearchParams()
  if (params?.search) qs.set('search', params.search)
  if (params?.systemType) qs.set('system_type', params.systemType)
  const suffix = qs.toString() ? `?${qs.toString()}` : ''
  return apiFetch<import('../types').SystemCatalogTemplate[]>(`/system-catalog${suffix}`)
}

export async function generateSystemRecipes(
  systemId: string,
  orgId?: string
): Promise<{ status: string }> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<{ status: string }>(
    `/organizations/${orgId}/systems/${systemId}/generate-recipes`,
    { method: 'POST' }
  )
}

export async function getRecipeGenerationStatus(
  systemId: string,
  orgId?: string
): Promise<import('../types').RecipeGenerationStatus> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<import('../types').RecipeGenerationStatus>(
    `/organizations/${orgId}/systems/${systemId}/generate-recipes/status`
  )
}

/**
 * System Evidence Capabilities API
 */
export async function getSystemCapabilities(
  systemId: string,
  orgId?: string
): Promise<SystemEvidenceCapability[]> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<SystemEvidenceCapability[]>(
    `/organizations/${orgId}/systems/${systemId}/capabilities`
  )
}

export async function createCapability(
  systemId: string,
  capability: CapabilityInput,
  orgId?: string
): Promise<SystemEvidenceCapability> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<SystemEvidenceCapability>(
    `/organizations/${orgId}/systems/${systemId}/capabilities`,
    {
      method: 'POST',
      body: JSON.stringify(capability),
    }
  )
}

export async function updateCapability(
  systemId: string,
  capabilityId: string,
  updates: CapabilityUpdate,
  orgId?: string
): Promise<SystemEvidenceCapability> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<SystemEvidenceCapability>(
    `/organizations/${orgId}/systems/${systemId}/capabilities/${capabilityId}`,
    {
      method: 'PATCH',
      body: JSON.stringify(updates),
    }
  )
}

export async function deleteCapability(
  systemId: string,
  capabilityId: string,
  orgId?: string
): Promise<void> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  await apiFetch<{ success: boolean; message: string }>(
    `/organizations/${orgId}/systems/${systemId}/capabilities/${capabilityId}`,
    { method: 'DELETE' }
  )
}

export async function getSystemsForEvidence(
  evidenceId: string,
  orgId?: string
): Promise<SystemEvidenceCapability[]> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<SystemEvidenceCapability[]>(
    `/organizations/${orgId}/evidence-capabilities/${encodeURIComponent(evidenceId)}`
  )
}

export async function getAllCapabilities(orgId?: string): Promise<SystemEvidenceCapability[]> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<SystemEvidenceCapability[]>(`/organizations/${orgId}/evidence-capabilities`)
}

/**
 * Evidence Collection Suggestions API
 */
export async function getEvidenceSuggestions(
  evidenceId: string,
  orgId?: string,
  options?: { systemId?: string; maturityLevel?: string; includeAlternatives?: boolean }
): Promise<EvidenceSuggestionsResponse> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  const params = new URLSearchParams()
  if (options?.systemId) params.set('system_id', options.systemId)
  if (options?.maturityLevel) params.set('maturity_level', options.maturityLevel)
  if (options?.includeAlternatives) params.set('include_alternatives', 'true')
  const qs = params.toString()
  return apiFetch<EvidenceSuggestionsResponse>(
    `/organizations/${orgId}/evidence/${encodeURIComponent(evidenceId)}/suggestions${qs ? `?${qs}` : ''}`
  )
}

/**
 * Recipe Feedback API
 */
export async function submitRecipeFeedback(
  evidenceId: string,
  feedback: import('../types').RecipeFeedbackCreate,
  orgId?: string
): Promise<{ id: string; feedback_type: string; maturity_level: string; created_at: string }> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch(
    `/organizations/${orgId}/evidence/${encodeURIComponent(evidenceId)}/recipe-feedback`,
    { method: 'POST', body: JSON.stringify(feedback) }
  )
}

/**
 * Evidence Gap Analysis API
 */
export async function getEvidenceGaps(orgId?: string): Promise<EvidenceGapsResponse> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<EvidenceGapsResponse>(`/organizations/${orgId}/evidence-gaps`)
}

/**
 * Framework Readiness API
 * Calculates readiness scores using: Readiness = (40% × Implementation) + (60% × Evidence)
 */
export async function getFrameworkReadiness(
  request: FrameworkReadinessRequest,
  orgId?: string
): Promise<FrameworkReadinessResponse> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<FrameworkReadinessResponse>(
    `/organizations/${orgId}/framework-readiness`,
    {
      method: 'POST',
      body: JSON.stringify(request),
    }
  )
}

/**
 * Generic API client for new endpoints (assignments, comments, tasks, notifications)
 */
export const apiClient = {
  async get<T = any>(endpoint: string): Promise<T> {
    return apiFetch<T>(endpoint, { method: 'GET' })
  },

  async post<T = any>(endpoint: string, data: any): Promise<T> {
    return apiFetch<T>(endpoint, {
      method: 'POST',
      body: JSON.stringify(data)
    })
  },

  async patch<T = any>(endpoint: string, data: any): Promise<T> {
    return apiFetch<T>(endpoint, {
      method: 'PATCH',
      body: JSON.stringify(data)
    })
  },

  async delete<T = any>(endpoint: string): Promise<T> {
    return apiFetch<T>(endpoint, { method: 'DELETE' })
  }
}

/**
 * Version / Update API (GET /api/version)
 *
 * The response is tiered by caller:
 * - Anonymous / non-admin callers get only a coarse liveness signal:
 *   { platform: { api_version }, status: "ok" } — no precise version, catalog,
 *   or update object.
 * - Platform admins additionally get platform.version, catalog.*, the `update`
 *   object (populated by the update poller) and the image `build` stamp.
 *
 * Every admin-only field is optional so the coarse shape still type-checks and
 * consumers can degrade gracefully instead of rendering "undefined".
 */
export interface VersionUpdateInfo {
  check_enabled: boolean
  installed_version?: string
  latest_version?: string
  update_available?: boolean | null
  breaking?: boolean
  release_url?: string
  summary?: string
  min_upgradable_version?: string
  skip_blocked?: boolean
  last_checked?: string | null
  status?: string
}

export interface VersionInfo {
  platform: {
    version?: string
    api_version: string
    git_commit?: string | null
  }
  catalog?: {
    version: string
    controls_count: number
    evidence_count: number
    interface_count: number
  }
  environment?: string
  update?: VersionUpdateInfo
  build?: { build_stamp?: string } | null
  status?: string
}

/**
 * Consultant Portal API
 *
 * NOTE: Consultant endpoints require Google OAuth authentication.
 * API key authentication is not supported for consultant features.
 */
import type { ClientSummary, ConsultantInvite, ConsultantProfile } from '../types'

export interface ConsultantDashboardResponse {
  profile: ConsultantProfile
  metrics: {
    total_clients: number
    active_clients: number
    pending_invites: number
    total_controls_across_clients: number
    implemented_controls_across_clients: number
    average_framework_readiness: number
    controls_by_status: Record<string, number>
    recent_activity: Array<{
      type: string
      scf_id: string
      organization_id: string
      status: string
      updated_at: string
    }>
  }
  clients: ClientSummaryBackend[]
}

export interface ClientSummaryBackend {
  id: string
  organization_id: string
  organization_name: string
  organization_slug: string
  role: string
  status: string
  linked_at: string
  metrics: {
    total_controls: number
    implemented_controls: number
    in_progress_controls: number
    at_risk_controls: number
    total_evidence: number
    tracked_evidence: number
    framework_readiness: number
  }
}

export interface ConsultantInviteBackend {
  id: string
  email: string
  organization_name: string
  organization_id: string | null
  invited_by_email?: string
  invited_by_name?: string
  status: string
  invite_token: string | null
  expires_at: string
  created_at: string
}

/**
 * Response from /consultant/check endpoint
 */
export interface ConsultantStatusResponse {
  is_consultant: boolean
  profile_id: string | null
  is_active: boolean
  has_consultant_subscription: boolean
  reason: string | null
}

/**
 * Check if the current user is registered as a consultant.
 * This does NOT auto-provision - it's safe to call for any user.
 */
export async function checkConsultantStatus(): Promise<ConsultantStatusResponse> {
  return apiFetch<ConsultantStatusResponse>('/consultant/check')
}

/**
 * @deprecated Self-registration removed. Consultant profiles are provisioned via website sync.
 * This endpoint returns 410 Gone from the backend.
 */
export async function registerAsConsultant(_companyName?: string): Promise<never> {
  throw new Error('Self-registration has been removed. Consultant profiles are provisioned via the marketing website.')
}

/**
 * Create a client organisation for the consultant.
 * The org is created with awaiting_admin=true until an admin user accepts the invite.
 */
export async function createClientOrganisation(orgName: string): Promise<{ id: string; name: string; slug: string; awaiting_admin: boolean }> {
  return apiFetch<{ id: string; name: string; slug: string; awaiting_admin: boolean }>('/consultant/clients/organisations', {
    method: 'POST',
    body: JSON.stringify({ name: orgName })
  })
}

/**
 * Invite an admin user to a pre-created client organisation.
 */
export async function inviteOrgAdmin(
  orgId: string,
  email: string,
  message?: string
): Promise<ConsultantInviteBackend> {
  return apiFetch<ConsultantInviteBackend>(`/consultant/clients/${orgId}/invite-admin`, {
    method: 'POST',
    body: JSON.stringify({ email, message })
  })
}

/**
 * Backend response for consultant profile
 */
export interface ConsultantProfileResponse {
  id: string
  user_id: string
  company_name: string | null
  is_active: boolean
  max_clients: number
  active_client_count: number
  created_at: string
  updated_at: string
}

/**
 * Get consultant dashboard data including profile, metrics, and client list
 */
export async function getConsultantDashboard(): Promise<ConsultantDashboardResponse> {
  return apiFetch<ConsultantDashboardResponse>('/consultant/dashboard')
}

/**
 * Get list of client organisations for the consultant
 */
export async function getConsultantClients(
  offset: number = 0,
  limit: number = 50,
  includeMetrics: boolean = true
): Promise<ClientSummaryBackend[]> {
  const params = new URLSearchParams({
    offset: offset.toString(),
    limit: limit.toString(),
    include_metrics: includeMetrics.toString()
  })
  return apiFetch<ClientSummaryBackend[]>(`/consultant/clients?${params}`)
}

/**
 * Get list of pending invitations
 */
export async function getConsultantInvites(
  status?: string
): Promise<ConsultantInviteBackend[]> {
  const params = new URLSearchParams()
  if (status) params.set('status', status)
  const queryString = params.toString()
  return apiFetch<ConsultantInviteBackend[]>(`/consultant/invites${queryString ? `?${queryString}` : ''}`)
}

/**
 * Create a new client invitation
 */
export async function createConsultantInvite(
  email: string,
  organizationName: string,
  message?: string
): Promise<ConsultantInviteBackend> {
  return apiFetch<ConsultantInviteBackend>('/consultant/clients/invite', {
    method: 'POST',
    body: JSON.stringify({
      email,
      organization_name: organizationName,
      message
    })
  })
}

/**
 * Cancel a pending invitation
 */
export async function cancelConsultantInvite(inviteId: string): Promise<{ success: boolean; message: string }> {
  return apiFetch<{ success: boolean; message: string }>(`/consultant/invites/${inviteId}`, {
    method: 'DELETE'
  })
}

/**
 * Transform backend client response to frontend ClientSummary format
 */
export function transformClientSummary(client: ClientSummaryBackend): ClientSummary {
  const metrics = client.metrics || {
    total_controls: 0,
    implemented_controls: 0,
    in_progress_controls: 0,
    at_risk_controls: 0,
    total_evidence: 0,
    tracked_evidence: 0,
    framework_readiness: 0
  }

  return {
    organization_id: client.organization_id,
    organization_name: client.organization_name,
    framework_readiness_percent: metrics.framework_readiness,
    controls_implemented: metrics.implemented_controls,
    controls_total: metrics.total_controls,
    controls_in_progress: metrics.in_progress_controls,
    controls_at_risk: metrics.at_risk_controls,
    evidence_tracked: metrics.tracked_evidence,
    evidence_total: metrics.total_evidence,
    last_activity_date: client.linked_at, // Use linked_at as fallback
    primary_framework: undefined // Not provided by backend
  }
}

/**
 * Transform backend invite response to frontend ConsultantInvite format
 */
export function transformConsultantInvite(invite: ConsultantInviteBackend): ConsultantInvite {
  return {
    id: invite.id,
    email: invite.email,
    organization_name: invite.organization_name,
    organization_id: invite.organization_id || undefined,
    invited_by_email: invite.invited_by_email || '',
    invited_by_name: invite.invited_by_name,
    status: invite.status as 'pending' | 'accepted' | 'expired' | 'cancelled',
    created_at: invite.created_at,
    expires_at: invite.expires_at
  }
}

// =============================================================================
// Invite Acceptance (for clients receiving invitations)
// =============================================================================

/**
 * Invite preview response from backend
 */
export interface InvitePreviewResponse {
  organization_name: string
  consultant_name: string | null
  consultant_email: string
  expires_at: string
  is_expired: boolean
  status: 'pending' | 'accepted' | 'expired' | 'cancelled'
}

/**
 * Accept invite response from backend
 */
export interface AcceptInviteResponse {
  success: boolean
  message: string
  organization: {
    id: string
    name: string
    slug: string
    created_at: string
    updated_at: string
  }
}

/**
 * Get invitation preview details (PUBLIC - no auth required)
 * Used to show invite details before the user signs in
 */
export async function getInvitePreview(token: string): Promise<InvitePreviewResponse> {
  const url = `${API_BASE_URL}/consultant/invites/${token}/preview`

  const response = await fetch(url, {
    method: 'GET',
    headers: {
      'Content-Type': 'application/json',
    },
  })

  if (!response.ok) {
    const errorText = await response.text()
    let errorMessage = `API Error: ${response.status} ${response.statusText}`

    try {
      const errorJson = JSON.parse(errorText)
      errorMessage = errorJson.detail || errorJson.error || errorMessage
    } catch {
      // Use default error message
    }

    throw new Error(errorMessage)
  }

  return response.json()
}

/**
 * Accept an invitation and create the organisation
 * Requires authentication
 */
export async function acceptConsultantInvite(token: string): Promise<AcceptInviteResponse> {
  return apiFetch<AcceptInviteResponse>(`/consultant/invites/${token}/accept`, {
    method: 'POST'
  })
}

// =============================================================================
// Organisation Member Invitation API
// =============================================================================

/**
 * Organisation invite preview response
 */
export interface OrgInvitePreviewResponse {
  organization_name: string
  inviter_name: string | null
  inviter_email: string | null
  role: string
  expires_at: string
  is_expired: boolean
  status: 'pending' | 'accepted' | 'expired' | 'cancelled'
}

/**
 * Organisation invite response (from create/list)
 */
export interface OrgInviteResponse {
  id: string
  organization_id: string
  organization_name: string
  email: string
  role: string
  status: string
  invite_token: string | null
  expires_at: string
  created_at: string
}

/**
 * Organisation invite list response
 */
export interface OrgInviteListResponse {
  invites: OrgInviteResponse[]
  total: number
}

/**
 * Get organisation invite preview (PUBLIC - no auth required)
 */
export async function getOrgInvitePreview(token: string): Promise<OrgInvitePreviewResponse> {
  const url = `${API_BASE_URL}/org-invites/${token}/preview`

  const response = await fetch(url, {
    method: 'GET',
    headers: {
      'Content-Type': 'application/json',
    },
  })

  if (!response.ok) {
    const errorText = await response.text()
    let errorMessage = `API Error: ${response.status} ${response.statusText}`

    try {
      const errorJson = JSON.parse(errorText)
      errorMessage = errorJson.detail || errorJson.error || errorMessage
    } catch {
      // Use default error message
    }

    throw new Error(errorMessage)
  }

  return response.json()
}

/**
 * Accept an organisation invite (requires authentication)
 */
export async function acceptOrgInvite(token: string): Promise<AcceptInviteResponse> {
  return apiFetch<AcceptInviteResponse>(`/org-invites/${token}/accept`, {
    method: 'POST'
  })
}

/**
 * List organisation invites (requires admin role)
 */
export async function getOrgInvites(orgId: string, status?: string): Promise<OrgInviteListResponse> {
  const params = status ? `?status=${status}` : ''
  return apiFetch<OrgInviteListResponse>(`/organizations/${orgId}/invites${params}`)
}

/**
 * Cancel an organisation invite (requires admin role)
 */
export async function cancelOrgInvite(orgId: string, inviteId: string): Promise<{ message: string }> {
  return apiFetch<{ message: string }>(`/organizations/${orgId}/invites/${inviteId}`, {
    method: 'DELETE'
  })
}

// =============================================================================
// Risk Assessment API
// =============================================================================

import type {
  RiskAssessment,
  RiskAssessmentInput,
  RiskAssessmentUpdate,
  RiskMatrixResponse,
  RiskSummaryResponse,
  TreatmentStatus,
  RiskLevel,
  CustomRiskDefinition,
  CustomRiskCreate,
  CustomRiskUpdate,
} from '../types'

/**
 * Get all risk assessments for an organisation
 */
export async function getRiskAssessments(
  filters?: {
    treatment_status?: TreatmentStatus
    risk_level?: RiskLevel
    category?: string
  },
  orgId?: string
): Promise<RiskAssessment[]> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  const params = new URLSearchParams()
  if (filters?.treatment_status) params.append('treatment_status', filters.treatment_status)
  if (filters?.risk_level) params.append('risk_level', filters.risk_level)
  if (filters?.category) params.append('category', filters.category)
  const queryString = params.toString()
  return apiFetch<RiskAssessment[]>(
    `/organizations/${orgId}/risk-assessments${queryString ? `?${queryString}` : ''}`
  )
}

/**
 * Get a single risk assessment by risk code
 */
export async function getRiskAssessment(
  riskCode: string,
  orgId?: string
): Promise<RiskAssessment> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<RiskAssessment>(
    `/organizations/${orgId}/risk-assessments/${encodeURIComponent(riskCode)}`
  )
}

/**
 * Create or update a risk assessment (upsert)
 */
export async function createOrUpdateRiskAssessment(
  assessment: RiskAssessmentInput,
  orgId?: string
): Promise<RiskAssessment> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<RiskAssessment>(
    `/organizations/${orgId}/risk-assessments`,
    {
      method: 'POST',
      body: JSON.stringify(assessment),
    }
  )
}

/**
 * Partially update a risk assessment
 */
export async function updateRiskAssessment(
  riskCode: string,
  updates: RiskAssessmentUpdate,
  orgId?: string
): Promise<RiskAssessment> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<RiskAssessment>(
    `/organizations/${orgId}/risk-assessments/${encodeURIComponent(riskCode)}`,
    {
      method: 'PATCH',
      body: JSON.stringify(updates),
    }
  )
}

/**
 * Delete a risk assessment
 */
export async function deleteRiskAssessment(
  riskCode: string,
  orgId?: string
): Promise<void> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  await apiFetch<{ success: boolean; message: string }>(
    `/organizations/${orgId}/risk-assessments/${encodeURIComponent(riskCode)}`,
    { method: 'DELETE' }
  )
}

// =============================================================================
// Custom Risk Definitions API
// =============================================================================

/**
 * List custom risk definitions for an organisation
 */
export async function getCustomRiskDefinitions(
  orgId?: string
): Promise<CustomRiskDefinition[]> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<CustomRiskDefinition[]>(
    `/organizations/${orgId}/custom-risks`
  )
}

/**
 * Create a custom risk definition
 */
export async function createCustomRisk(
  data: CustomRiskCreate,
  orgId?: string
): Promise<CustomRiskDefinition> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<CustomRiskDefinition>(
    `/organizations/${orgId}/custom-risks`,
    {
      method: 'POST',
      body: JSON.stringify(data),
    }
  )
}

/**
 * Update a custom risk definition
 */
export async function updateCustomRiskDefinition(
  riskCode: string,
  data: CustomRiskUpdate,
  orgId?: string
): Promise<CustomRiskDefinition> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<CustomRiskDefinition>(
    `/organizations/${orgId}/custom-risks/${encodeURIComponent(riskCode)}`,
    {
      method: 'PATCH',
      body: JSON.stringify(data),
    }
  )
}

/**
 * Delete a custom risk definition and its assessment
 */
export async function deleteCustomRisk(
  riskCode: string,
  orgId?: string
): Promise<void> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  await apiFetch<{ success: boolean; message: string }>(
    `/organizations/${orgId}/custom-risks/${encodeURIComponent(riskCode)}`,
    { method: 'DELETE' }
  )
}

/**
 * Add a control link to a custom risk
 */
export async function addCustomRiskControl(
  riskCode: string,
  scfId: string,
  orgId?: string
): Promise<void> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  await apiFetch<{ success: boolean; message: string }>(
    `/organizations/${orgId}/custom-risks/${encodeURIComponent(riskCode)}/controls`,
    {
      method: 'POST',
      body: JSON.stringify({ scf_id: scfId }),
    }
  )
}

/**
 * Remove a control link from a custom risk
 */
export async function removeCustomRiskControl(
  riskCode: string,
  scfId: string,
  orgId?: string
): Promise<void> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  await apiFetch<{ success: boolean; message: string }>(
    `/organizations/${orgId}/custom-risks/${encodeURIComponent(riskCode)}/controls/${encodeURIComponent(scfId)}`,
    { method: 'DELETE' }
  )
}

/**
 * Get the 5x5 risk matrix data
 */
export async function getRiskMatrix(
  matrixType: 'inherent' | 'residual' = 'inherent',
  orgId?: string
): Promise<RiskMatrixResponse> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<RiskMatrixResponse>(
    `/organizations/${orgId}/risk-matrix?matrix_type=${matrixType}`
  )
}

/**
 * Get risk summary statistics
 */
export async function getRiskSummary(orgId?: string): Promise<RiskSummaryResponse> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<RiskSummaryResponse>(`/organizations/${orgId}/risk-summary`)
}

// =============================================================================
// Risk Profile Configuration API
// =============================================================================

import type { RiskProfile, RiskProfileUpdate } from '../types'
import type {
  Vendor,
  VendorInput,
  VendorUpdate as VendorUpdateType,
  VendorAssessment,
  VendorCertification,
  VendorCertificationInput,
  VendorStatus,
  VendorCriticality,
} from '../types'

/**
 * Get the organisation's risk profile (auto-creates with defaults if missing)
 */
export async function getRiskProfile(orgId?: string): Promise<RiskProfile> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<RiskProfile>(`/organizations/${orgId}/risk-profile`)
}

/**
 * Update the organisation's risk profile
 */
export async function updateRiskProfile(
  data: RiskProfileUpdate,
  orgId?: string
): Promise<RiskProfile> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<RiskProfile>(
    `/organizations/${orgId}/risk-profile`,
    {
      method: 'PUT',
      body: JSON.stringify(data),
    }
  )
}

/**
 * Reset the organisation's risk profile to defaults
 */
export async function resetRiskProfile(orgId?: string): Promise<RiskProfile> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<RiskProfile>(
    `/organizations/${orgId}/risk-profile/reset`,
    { method: 'POST' }
  )
}

// =============================================================================
// Risk-Control Catalog Mappings API
// =============================================================================

/**
 * Response from get risks for control endpoint
 */
export interface RisksForControlResponse {
  scf_id: string
  catalog_risk_codes: string[]
  assessments: RiskAssessment[]
}

/**
 * Scoped control info returned in controls-for-risk response
 */
export interface ScopedControlForRisk {
  scf_id: string
  control_name: string
  implementation_status: string | null
  priority: string | null
  target_date: string | null
}

/**
 * Response from get controls for risk endpoint
 */
export interface ControlsForRiskResponse {
  risk_code: string
  total_catalog_controls: number
  catalog_control_ids: string[]
  scoped_controls: ScopedControlForRisk[]
}

/**
 * Get risk codes and assessments linked to a specific control.
 * Returns catalog risk codes from SCF mappings plus org-specific assessments.
 */
export async function getRisksForControl(
  scfId: string,
  orgId?: string
): Promise<RisksForControlResponse> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<RisksForControlResponse>(
    `/organizations/${orgId}/risks-for-control/${encodeURIComponent(scfId)}`
  )
}

/**
 * Get controls that address a specific risk code based on SCF catalog mappings.
 * Returns both catalog control IDs and scoped controls for this organisation.
 */
export async function getControlsForRisk(
  riskCode: string,
  orgId?: string
): Promise<ControlsForRiskResponse> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<ControlsForRiskResponse>(
    `/organizations/${orgId}/controls-for-risk/${encodeURIComponent(riskCode)}`
  )
}

// =============================================================================
// Vendor Management API (TPRM)
// =============================================================================

/**
 * Get all vendors for an organisation
 */
export async function getVendors(
  filters?: {
    status?: VendorStatus
    criticality?: VendorCriticality
    category?: string
    search?: string
  },
  orgId?: string
): Promise<Vendor[]> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  const params = new URLSearchParams()
  if (filters?.status) params.append('status', filters.status)
  if (filters?.criticality) params.append('criticality', filters.criticality)
  if (filters?.category) params.append('category', filters.category)
  if (filters?.search) params.append('search', filters.search)
  const queryString = params.toString()
  return apiFetch<Vendor[]>(
    `/organizations/${orgId}/vendors${queryString ? `?${queryString}` : ''}`
  )
}

/**
 * Get a single vendor by ID
 */
export async function getVendor(
  vendorId: string,
  orgId?: string
): Promise<Vendor> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<Vendor>(`/organizations/${orgId}/vendors/${vendorId}`)
}

/**
 * Create a new vendor
 */
export async function createVendor(
  vendor: VendorInput,
  orgId?: string
): Promise<Vendor> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<Vendor>(
    `/organizations/${orgId}/vendors`,
    {
      method: 'POST',
      body: JSON.stringify(vendor),
    }
  )
}

/**
 * Partially update a vendor
 */
export async function updateVendor(
  vendorId: string,
  updates: VendorUpdateType,
  orgId?: string
): Promise<Vendor> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<Vendor>(
    `/organizations/${orgId}/vendors/${vendorId}`,
    {
      method: 'PATCH',
      body: JSON.stringify(updates),
    }
  )
}

/**
 * Delete a vendor
 */
export async function deleteVendor(
  vendorId: string,
  orgId?: string
): Promise<void> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  await apiFetch<{ success: boolean; message: string }>(
    `/organizations/${orgId}/vendors/${vendorId}`,
    { method: 'DELETE' }
  )
}

/**
 * Get assessments for a vendor
 */
export async function getVendorAssessments(
  vendorId: string,
  orgId?: string
): Promise<VendorAssessment[]> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<VendorAssessment[]>(
    `/organizations/${orgId}/vendors/${vendorId}/assessments`
  )
}

/**
 * Trigger an AI assessment for a vendor (the single assessment pipeline).
 * Returns 202 with the new assessment id and background job id.
 */
export async function triggerVendorAIAssessment(
  vendorId: string,
  body: VendorAIAssessmentTriggerRequest,
  orgId?: string
): Promise<VendorAIAssessmentTriggerResponse> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<VendorAIAssessmentTriggerResponse>(
    `/organizations/${orgId}/vendors/${vendorId}/assessments`,
    {
      method: 'POST',
      body: JSON.stringify(body),
    }
  )
}

/**
 * Get a single vendor assessment (including full report fields)
 */
export async function getVendorAssessment(
  vendorId: string,
  assessmentId: string,
  orgId?: string
): Promise<VendorAssessment> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<VendorAssessment>(
    `/organizations/${orgId}/vendors/${vendorId}/assessments/${assessmentId}`
  )
}

/**
 * Poll the status of a vendor assessment
 */
export async function getVendorAssessmentStatus(
  vendorId: string,
  assessmentId: string,
  orgId?: string
): Promise<VendorAssessmentStatusResponse> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<VendorAssessmentStatusResponse>(
    `/organizations/${orgId}/vendors/${vendorId}/assessments/${assessmentId}/status`
  )
}

/**
 * Get the most recent completed AI assessment for a vendor (404 if none)
 */
export async function getLatestVendorAssessment(
  vendorId: string,
  orgId?: string
): Promise<VendorAssessment> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<VendorAssessment>(
    `/organizations/${orgId}/vendors/${vendorId}/assessments/latest`
  )
}

/**
 * Get certifications for a vendor
 */
export async function getVendorCertifications(
  vendorId: string,
  orgId?: string
): Promise<VendorCertification[]> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<VendorCertification[]>(
    `/organizations/${orgId}/vendors/${vendorId}/certifications`
  )
}

/**
 * Create a vendor certification
 */
export async function createVendorCertification(
  vendorId: string,
  certification: VendorCertificationInput,
  orgId?: string
): Promise<VendorCertification> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<VendorCertification>(
    `/organizations/${orgId}/vendors/${vendorId}/certifications`,
    {
      method: 'POST',
      body: JSON.stringify(certification),
    }
  )
}

// ---------------------------------------------------------------------------
// Vendor Action Items & Compensating Controls
// ---------------------------------------------------------------------------

import type {
  VendorAIAssessmentTriggerRequest,
  VendorAIAssessmentTriggerResponse,
  VendorAssessmentStatusResponse,
  VendorActionItem,
  VendorActionItemInput,
  VendorCompensatingControl,
  VendorCompensatingControlInput,
} from '../types'

export async function getVendorActionItems(
  vendorId: string,
  orgId?: string
): Promise<VendorActionItem[]> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<VendorActionItem[]>(
    `/organizations/${orgId}/vendors/${vendorId}/action-items`
  )
}

export async function createVendorActionItem(
  vendorId: string,
  data: VendorActionItemInput,
  orgId?: string
): Promise<VendorActionItem> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<VendorActionItem>(
    `/organizations/${orgId}/vendors/${vendorId}/action-items`,
    { method: 'POST', body: JSON.stringify(data) }
  )
}

export async function updateVendorActionItem(
  vendorId: string,
  itemId: string,
  data: Partial<VendorActionItemInput>,
  orgId?: string
): Promise<VendorActionItem> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<VendorActionItem>(
    `/organizations/${orgId}/vendors/${vendorId}/action-items/${itemId}`,
    { method: 'PATCH', body: JSON.stringify(data) }
  )
}

export async function deleteVendorActionItem(
  vendorId: string,
  itemId: string,
  orgId?: string
): Promise<void> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  await apiFetch<{ message: string }>(
    `/organizations/${orgId}/vendors/${vendorId}/action-items/${itemId}`,
    { method: 'DELETE' }
  )
}

export async function getOrgVendorActionItems(
  orgId?: string
): Promise<VendorActionItem[]> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<VendorActionItem[]>(
    `/organizations/${orgId}/vendor-action-items`
  )
}

// ---------------------------------------------------------------------------
// Vendor Compensating Controls
// ---------------------------------------------------------------------------

export async function getVendorCompensatingControls(
  vendorId: string,
  orgId?: string
): Promise<VendorCompensatingControl[]> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<VendorCompensatingControl[]>(
    `/organizations/${orgId}/vendors/${vendorId}/compensating-controls`
  )
}

export async function createVendorCompensatingControl(
  vendorId: string,
  data: VendorCompensatingControlInput,
  orgId?: string
): Promise<VendorCompensatingControl> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<VendorCompensatingControl>(
    `/organizations/${orgId}/vendors/${vendorId}/compensating-controls`,
    { method: 'POST', body: JSON.stringify(data) }
  )
}

export async function updateVendorCompensatingControl(
  vendorId: string,
  controlId: string,
  data: Partial<VendorCompensatingControlInput>,
  orgId?: string
): Promise<VendorCompensatingControl> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<VendorCompensatingControl>(
    `/organizations/${orgId}/vendors/${vendorId}/compensating-controls/${controlId}`,
    { method: 'PATCH', body: JSON.stringify(data) }
  )
}

export async function deleteVendorCompensatingControl(
  vendorId: string,
  controlId: string,
  orgId?: string
): Promise<void> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  await apiFetch<{ message: string }>(
    `/organizations/${orgId}/vendors/${vendorId}/compensating-controls/${controlId}`,
    { method: 'DELETE' }
  )
}

// ============================================================================
// API Key Management
// ============================================================================

export interface OrgApiKey {
  id: string
  name: string
  key_prefix: string
  role: string
  is_active: boolean
  expires_at: string | null
  last_used_at: string | null
  created_at: string
  user_id: string
  user_email: string | null
}

export interface OrgApiKeyCreated extends OrgApiKey {
  plaintext_key: string
  warning: string
}

export async function getOrgApiKeys(orgId: string): Promise<OrgApiKey[]> {
  return apiFetch<OrgApiKey[]>(`/organizations/${orgId}/api-keys`)
}

export async function createOrgApiKey(
  orgId: string,
  name: string,
  expiresAt?: string
): Promise<OrgApiKeyCreated> {
  const body: Record<string, unknown> = { name }
  if (expiresAt && expiresAt.trim()) {
    // Convert date-only (YYYY-MM-DD) to full ISO datetime for Pydantic
    body.expires_at = expiresAt.includes('T') ? expiresAt : `${expiresAt}T23:59:59`
  }
  return apiFetch<OrgApiKeyCreated>(`/organizations/${orgId}/api-keys`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export async function revokeOrgApiKey(
  orgId: string,
  keyId: string
): Promise<{ message: string }> {
  return apiFetch<{ message: string }>(`/organizations/${orgId}/api-keys/${keyId}`, {
    method: 'DELETE',
  })
}

// =============================================================================
// Dashboard Work Queue API
// =============================================================================

export interface OverdueEvidenceItem {
  task_id: string
  evidence_id: string
  title: string | null
  due_date: string
  days_overdue: number
  priority: string | null
}

export interface BlockingControlItem {
  scf_id: string
  implementation_status: string
  days_stale: number
}

export interface StaleCollectionItem {
  evidence_id: string
  next_collection_date: string
  days_overdue: number
}

export interface WorkQueueResponse {
  overdue_evidence: OverdueEvidenceItem[]
  blocking_controls: BlockingControlItem[]
  stale_collections: StaleCollectionItem[]
  total_items: number
}

export async function getWorkQueue(orgId?: string): Promise<WorkQueueResponse> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<WorkQueueResponse>(`/organizations/${orgId}/dashboard/work-queue`)
}

// ── Capability Themes (KSI Posture) ──

import type { CapabilityThemeListResponse, CapabilityThemeControlsResponse, CapabilityThemeEvidencePostureResponse } from '../types'

export async function getCapabilityThemes(orgId?: string): Promise<CapabilityThemeListResponse> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<CapabilityThemeListResponse>(`/organizations/${orgId}/capability-themes`)
}

export async function getCapabilityThemeControls(
  themeCode: string,
  params?: { limit?: number; offset?: number },
  orgId?: string
): Promise<CapabilityThemeControlsResponse> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  const searchParams = new URLSearchParams()
  if (params?.limit) searchParams.set('limit', String(params.limit))
  if (params?.offset) searchParams.set('offset', String(params.offset))
  const qs = searchParams.toString()
  return apiFetch<CapabilityThemeControlsResponse>(
    `/organizations/${orgId}/capability-themes/${encodeURIComponent(themeCode)}/controls${qs ? `?${qs}` : ''}`
  )
}

export async function getCapabilityThemeEvidencePosture(orgId?: string): Promise<CapabilityThemeEvidencePostureResponse> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<CapabilityThemeEvidencePostureResponse>(`/organizations/${orgId}/capability-themes/evidence-posture`)
}

// ── Audit Log ──

import type { AuditLogListResponse } from '../types'

export async function getAuditLog(
  scfId: string,
  orgId?: string,
  limit: number = 50,
  offset: number = 0
): Promise<AuditLogListResponse> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  const params = new URLSearchParams({
    scf_id: scfId,
    entity_type: 'scoped_control',
    limit: String(limit),
    offset: String(offset),
  })
  return apiFetch<AuditLogListResponse>(`/organizations/${orgId}/audit-log?${params}`)
}

export async function getOrgAuditLog(
  orgId: string,
  params: {
    entity_type?: string
    entity_id?: string
    action?: string
    action_source?: string
    actor_id?: string
    date_from?: string
    date_to?: string
    search_text?: string
    limit?: number
    offset?: number
  } = {}
): Promise<AuditLogListResponse> {
  const query = new URLSearchParams()
  if (params.entity_type) query.set('entity_type', params.entity_type)
  if (params.entity_id) query.set('entity_id', params.entity_id)
  if (params.action) query.set('action', params.action)
  if (params.action_source) query.set('action_source', params.action_source)
  if (params.actor_id) query.set('changed_by_user_id', params.actor_id)
  if (params.date_from) query.set('date_from', params.date_from)
  if (params.date_to) query.set('date_to', params.date_to)
  if (params.search_text) query.set('search_text', params.search_text)
  query.set('limit', String(params.limit ?? 50))
  query.set('offset', String(params.offset ?? 0))
  return apiFetch<AuditLogListResponse>(`/organizations/${orgId}/audit-log?${query}`)
}

export async function getScopePreferences(orgId: string): Promise<{
  active_frameworks: string[]
  audit_mode_locked: boolean
  audit_label: string | null
}> {
  return apiFetch(`/organizations/${orgId}/scope-preferences`)
}

export async function upsertScopePreferences(orgId: string, active_frameworks: string[]): Promise<{
  active_frameworks: string[]
}> {
  return apiFetch(`/organizations/${orgId}/scope-preferences`, {
    method: 'PUT',
    body: JSON.stringify({ active_frameworks }),
  })
}

// ---------------------------------------------------------------------------
// Evidence Files API (Issues #325, #327)
// ---------------------------------------------------------------------------

export interface EvidenceFileUploadUrlRequest {
  filename: string
  content_type: string
  file_size_bytes: number
}

export interface EvidenceFileUploadUrlResponse {
  url: string
  fields: Record<string, string>
  s3_key: string
  expires_in: number
}

export interface EvidenceFileConfirmRequest {
  s3_key: string
  sha256_hash?: string
}

export interface EvidenceFileResponse {
  id: string
  organization_id: string
  evidence_id: string
  filename: string
  s3_key: string
  content_type: string
  file_size_bytes: number
  sha256_hash: string | null
  classification: string
  uploaded_by_user_id: string | null
  uploaded_at: string
  expires_at: string | null
  is_deleted: boolean
  download_url: string | null
  uploaded_by: { id: string; display_name: string; email: string } | null
  review_status: string
  reviewed_by_user_id: string | null
  reviewed_at: string | null
  review_notes: string | null
  reviewed_by: { id: string; display_name: string; email: string } | null
}

export interface EvidenceFileListResponse {
  files: EvidenceFileResponse[]
  total: number
}

export async function reviewEvidenceFile(
  orgId: string,
  evidenceId: string,
  fileId: string,
  reviewData: { review_status: string; review_notes?: string }
): Promise<EvidenceFileResponse> {
  return apiFetch<EvidenceFileResponse>(
    `/organizations/${orgId}/evidence/${evidenceId}/files/${fileId}/review`,
    {
      method: 'PATCH',
      body: JSON.stringify(reviewData),
    }
  )
}

export async function getEvidenceUploadUrl(
  evidenceId: string,
  request: EvidenceFileUploadUrlRequest,
  orgId?: string
): Promise<EvidenceFileUploadUrlResponse> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<EvidenceFileUploadUrlResponse>(
    `/organizations/${orgId}/evidence/${evidenceId}/files/upload-url`,
    {
      method: 'POST',
      body: JSON.stringify(request),
    }
  )
}

export async function confirmEvidenceUpload(
  evidenceId: string,
  request: EvidenceFileConfirmRequest,
  orgId?: string
): Promise<EvidenceFileResponse> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<EvidenceFileResponse>(
    `/organizations/${orgId}/evidence/${evidenceId}/files/confirm`,
    {
      method: 'POST',
      body: JSON.stringify(request),
    }
  )
}

export async function listEvidenceFiles(
  evidenceId: string,
  orgId?: string
): Promise<EvidenceFileListResponse> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  return apiFetch<EvidenceFileListResponse>(
    `/organizations/${orgId}/evidence/${evidenceId}/files`
  )
}

export async function deleteEvidenceFile(
  evidenceId: string,
  fileId: string,
  orgId?: string
): Promise<void> {
  if (!orgId) {
    const org = await getCurrentOrganization()
    orgId = org.id
  }
  await apiFetch<void>(
    `/organizations/${orgId}/evidence/${evidenceId}/files/${fileId}`,
    { method: 'DELETE' }
  )
}

// ---- Webhook endpoint management (Issue #219) ----

interface CreateWebhookEndpointRequest {
  name: string
  description?: string
  allowed_evidence_ids?: string[]
  rate_limit_per_minute?: number
}

export interface WebhookEndpointCreatedResponse {
  id: string
  organization_id: string
  name: string
  description: string | null
  secret_prefix: string
  is_active: boolean
  allowed_evidence_ids: string[] | null
  rate_limit_per_minute: number | null
  created_by_user_id: string | null
  last_delivery_at: string | null
  delivery_count: number
  created_at: string
  updated_at: string
  plaintext_secret: string
  warning: string
}

export async function createWebhookEndpoint(
  orgId: string,
  request: CreateWebhookEndpointRequest
): Promise<WebhookEndpointCreatedResponse> {
  return apiFetch<WebhookEndpointCreatedResponse>(
    `/organizations/${orgId}/webhook-endpoints`,
    { method: 'POST', body: JSON.stringify(request) }
  )
}

export interface WebhookEndpointResponse {
  id: string
  organization_id: string
  name: string
  description: string | null
  secret_prefix: string
  is_active: boolean
  allowed_evidence_ids: string[] | null
  rate_limit_per_minute: number | null
  delivery_count: number
  last_delivery_at?: string | null
  created_at: string
  updated_at: string
}

export async function listWebhookEndpoints(
  orgId: string
): Promise<WebhookEndpointResponse[]> {
  return apiFetch<WebhookEndpointResponse[]>(
    `/organizations/${orgId}/webhook-endpoints`
  )
}

export async function testWebhookEndpoint(
  orgId: string,
  endpointId: string
): Promise<void> {
  // Send a lightweight GET to verify the endpoint exists and is active
  await apiFetch<WebhookEndpointResponse>(
    `/organizations/${orgId}/webhook-endpoints/${endpointId}`
  )
}

export async function rotateWebhookSecret(
  orgId: string,
  endpointId: string
): Promise<WebhookEndpointCreatedResponse> {
  return apiFetch<WebhookEndpointCreatedResponse>(
    `/organizations/${orgId}/webhook-endpoints/${endpointId}/rotate-secret`,
    { method: 'POST' }
  )
}

export async function revokeWebhookEndpoint(
  orgId: string,
  endpointId: string
): Promise<{ message: string }> {
  return apiFetch<{ message: string }>(
    `/organizations/${orgId}/webhook-endpoints/${endpointId}`,
    { method: 'DELETE' }
  )
}

export interface WebhookDelivery {
  id: string
  webhook_endpoint_id: string
  organization_id: string
  evidence_id: string
  event_id: string | null
  content_type: string | null
  signature_valid: boolean
  status: string
  error_message: string | null
  evidence_file_id: string | null
  ip_address: string | null
  user_agent: string | null
  created_at: string
  processed_at: string | null
}

export interface WebhookDeliveryList {
  deliveries: WebhookDelivery[]
  total: number
}

export async function getWebhookDeliveries(
  orgId: string,
  endpointId: string,
  limit = 50,
  offset = 0
): Promise<WebhookDeliveryList> {
  return apiFetch<WebhookDeliveryList>(
    `/organizations/${orgId}/webhook-endpoints/${endpointId}/deliveries?limit=${limit}&offset=${offset}`
  )
}

// ---- Evidence Health Dashboard (Issue #220) ----

interface EvidenceHealthItem {
  evidence_id: string
  evidence_name: string | null
  collecting_system: string | null
  frequency: string | null
  last_file_uploaded_at: string | null
  days_since_upload: number | null
  staleness_threshold_days: number | null
  status: 'green' | 'amber' | 'red' | 'unknown'
  file_count: number
  latest_validation_status: string | null
  latest_assessment_status: string | null
  latest_assessment_score: number | null
  control_mappings: string[]
}

interface EvidenceHealthSummary {
  total_tracked: number
  green_count: number
  amber_count: number
  red_count: number
  unknown_count: number
  green_pct: number
  amber_pct: number
  red_pct: number
}

export interface EvidenceHealthResponse {
  summary: EvidenceHealthSummary
  items: EvidenceHealthItem[]
}

export async function getEvidenceHealth(
  orgId: string
): Promise<EvidenceHealthResponse> {
  return apiFetch<EvidenceHealthResponse>(
    `/organizations/${orgId}/evidence-health`
  )
}

// ---- Evidence upcoming deadlines (#482) ----

export interface UpcomingEvidenceItem {
  evidence_id: string
  evidence_name: string | null
  frequency: string | null
  collecting_system: string | null
  last_uploaded_at: string | null
  next_due: string | null
  days_until_due: number
  is_overdue: boolean
  file_count: number
}

export interface UpcomingEvidenceResponse {
  items: UpcomingEvidenceItem[]
  total: number
}

export async function getUpcomingEvidence(
  orgId: string,
  days: number = 14
): Promise<UpcomingEvidenceResponse> {
  return apiFetch<UpcomingEvidenceResponse>(
    `/organizations/${orgId}/evidence-health/upcoming?days=${days}`
  )
}

// ---------------------------------------------------------------------------
// Audit Engagements API (Issue #378 — Phase D Frontend)
// ---------------------------------------------------------------------------

export interface AuditEngagement {
  id: string
  organization_id: string
  name: string
  frameworks: string[]
  status: 'draft' | 'active' | 'under_review' | 'closed'
  start_date: string | null
  end_date: string | null
  created_by_user_id: string | null
  created_at: string
  updated_at: string
  scope_count: number | null
}

export interface AuditEngagementCreate {
  name: string
  frameworks: string[]
  start_date?: string | null
  end_date?: string | null
}

export interface EngagementScopeItem {
  id: string
  scoped_control_id: string
  scf_id: string | null
  control_name: string | null
  added_at: string
}

export async function listEngagements(orgId: string): Promise<AuditEngagement[]> {
  return apiFetch<AuditEngagement[]>(`/organizations/${orgId}/engagements`)
}

export async function createEngagement(
  orgId: string,
  data: AuditEngagementCreate
): Promise<AuditEngagement> {
  return apiFetch<AuditEngagement>(`/organizations/${orgId}/engagements`, {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

export async function getEngagementScope(
  orgId: string,
  engagementId: string
): Promise<EngagementScopeItem[]> {
  return apiFetch<EngagementScopeItem[]>(
    `/organizations/${orgId}/engagements/${engagementId}/scope`
  )
}

export async function updateEngagement(
  orgId: string,
  engagementId: string,
  data: Partial<Pick<AuditEngagement, 'name' | 'status' | 'start_date' | 'end_date'>>
): Promise<AuditEngagement> {
  return apiFetch<AuditEngagement>(`/organizations/${orgId}/engagements/${engagementId}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  })
}

export async function deleteEngagement(orgId: string, engagementId: string): Promise<void> {
  return apiFetch<void>(`/organizations/${orgId}/engagements/${engagementId}`, {
    method: 'DELETE',
  })
}

// ---- AI Evidence Assessment ----

export interface AssessmentFinding {
  category: string
  level: string
  message: string
  control_id?: string
  suggestion?: string
}

export interface EvidenceAssessmentResponse {
  id: string
  evidence_file_id: string
  organization_id: string
  evidence_id: string
  status: string
  relevance_score: number | null
  findings: AssessmentFinding[]
  summary: string | null
  model_id: string | null
  prompt_hash: string | null
  control_context_hash: string | null
  framework_version: string | null
  input_token_count: number | null
  output_token_count: number | null
  cost_cents: number | null
  processing_time_ms: number | null
  assessment_source: string
  requested_by_user_id: string | null
  assessed_at: string | null
  created_at: string
}

export interface EvidenceAssessmentSummary {
  total_assessed: number
  sufficient_count: number
  partial_count: number
  insufficient_count: number
  pending_count: number
  error_count: number
  unassessed_count: number
  average_relevance_score: number | null
  total_cost_cents: number | null
}

export async function triggerAssessment(
  orgId: string,
  evidenceId: string,
  fileId: string,
  source: string = 'on_demand',
): Promise<EvidenceAssessmentResponse> {
  return apiFetch<EvidenceAssessmentResponse>(
    `/organizations/${orgId}/evidence/${evidenceId}/files/${fileId}/assess`,
    { method: 'POST', body: JSON.stringify({ assessment_source: source }) },
  )
}

export async function getAssessment(
  orgId: string,
  evidenceId: string,
  fileId: string,
): Promise<EvidenceAssessmentResponse | null> {
  try {
    return await apiFetch<EvidenceAssessmentResponse>(
      `/organizations/${orgId}/evidence/${evidenceId}/files/${fileId}/assessment`,
    )
  } catch {
    return null // 404 — no assessment exists
  }
}

export async function bulkAssess(
  orgId: string,
  body: { evidence_id?: string; file_ids?: string[]; assess_unassessed?: boolean },
): Promise<{ queued: number; message: string }> {
  return apiFetch<{ queued: number; message: string }>(
    `/organizations/${orgId}/evidence/assess-bulk`,
    { method: 'POST', body: JSON.stringify(body) },
  )
}

export async function getAssessmentSummary(
  orgId: string,
): Promise<EvidenceAssessmentSummary> {
  return apiFetch<EvidenceAssessmentSummary>(
    `/organizations/${orgId}/evidence/assessment/summary`,
  )
}

// ---------------------------------------------------------------------------
// M4 (#574) — Per-window review + Frequency Health
// ---------------------------------------------------------------------------

import type {
  EvidenceWindowAssessment,
  EvidenceWindowAssessmentSummary,
  FrequencyHealthResponse,
  RefreshStaleWindowAssessmentsResponse,
} from '../types'

/**
 * List recent windowed assessments for an evidence_id (newest first).
 *
 * Used by the per-window review panel to surface the latest EWA. The panel
 * only renders the head row; older rows are exposed in audit/history views
 * we do not yet have a dedicated screen for.
 *
 * Backend route: ``GET /organizations/{org_id}/evidence/{evidence_id}/window-assessments``
 */
export async function listWindowAssessments(
  orgId: string,
  evidenceId: string,
  options: { limit?: number; offset?: number } = {},
): Promise<EvidenceWindowAssessment[]> {
  const params = new URLSearchParams()
  if (options.limit !== undefined) params.set('limit', String(options.limit))
  if (options.offset !== undefined) params.set('offset', String(options.offset))
  const qs = params.toString()
  return apiFetch<EvidenceWindowAssessment[]>(
    `/organizations/${orgId}/evidence/${evidenceId}/window-assessments${qs ? `?${qs}` : ''}`,
  )
}

/**
 * Aggregate windowed-assessment metrics for the dashboard overview card.
 *
 * Backend route: ``GET /organizations/{org_id}/evidence/window-assessments/summary``
 *
 * Backend includes ``total_cost_cents`` in its response shape; we omit it
 * from this typed client because internal AI inference spend must not surface
 * in customer-facing UI.
 */
export async function getWindowAssessmentSummary(
  orgId: string,
): Promise<EvidenceWindowAssessmentSummary> {
  return apiFetch<EvidenceWindowAssessmentSummary>(
    `/organizations/${orgId}/evidence/window-assessments/summary`,
  )
}

/**
 * Reassess every evidence ID with new files since the last window
 * assessment (or never assessed). Equivalent to the nightly beat task fired
 * on demand. Capped at ``WINDOW_ASSESSMENT_NIGHTLY_CAP`` server-side.
 *
 * Backend route: ``POST /organizations/{org_id}/evidence/window-assessments/refresh-stale``
 */
export async function refreshStaleWindowAssessments(
  orgId: string,
): Promise<RefreshStaleWindowAssessmentsResponse> {
  return apiFetch<RefreshStaleWindowAssessmentsResponse>(
    `/organizations/${orgId}/evidence/window-assessments/refresh-stale`,
    { method: 'POST' },
  )
}

/**
 * Set the review state of a windowed evidence assessment (M4 PR 2 endpoint).
 *
 * ``review_status`` must be one of: ``approved``, ``rejected``,
 * ``needs_revision``, ``not_reviewed`` (revoke). Backend returns 422 on any
 * other value. ``review_notes`` is optional and capped at 2000 chars.
 *
 * Backend route: ``PUT /organizations/{org_id}/window-assessments/{ewa_id}/review``
 */
export async function reviewWindowAssessment(
  orgId: string,
  ewaId: string,
  body: { review_status: string; review_notes?: string },
): Promise<EvidenceWindowAssessment> {
  return apiFetch<EvidenceWindowAssessment>(
    `/organizations/${orgId}/window-assessments/${ewaId}/review`,
    {
      method: 'PUT',
      body: JSON.stringify(body),
    },
  )
}

/**
 * Fetch the frequency-health report for an organization (M4 PR 2 endpoint).
 *
 * Backend route: ``GET /organizations/{org_id}/evidence/frequency-health``
 *
 * ``items`` only contains misaligned rows per ISC-19; low-confidence
 * non-misaligned entries are summed in ``low_confidence_count`` for awareness.
 * The backend serves an ETag — the existing ``apiFetch`` wrapper passes it
 * through transparently.
 */
export async function getFrequencyHealth(
  orgId: string,
): Promise<FrequencyHealthResponse> {
  return apiFetch<FrequencyHealthResponse>(
    `/organizations/${orgId}/evidence/frequency-health`,
  )
}

/**
 * CDM (Control Document Mapping) API — slice 8 (frontend) + slices 2/4/5
 * (backend already shipped). The upload endpoint accepts multipart/form-data
 * with a `file` field; everything else is JSON.
 */
export type CDMIngestStatus =
  | 'pending'
  | 'parsing'
  | 'parsed'
  | 'indexing'
  | 'indexed'
  | 'failed'
  | 'indexing_failed'

export interface CDMDocument {
  id: string
  organization_id: string
  original_filename: string
  mime_type: string
  size_bytes: number
  sha256: string
  ingest_status: CDMIngestStatus
  ingest_error: string | null
  word_count: number | null
  upload_user_id: string | null
  kb_revision_at_ingest: string | null
  created_at: string
  updated_at: string
}

export interface CDMDocumentListResponse {
  documents: CDMDocument[]
  total: number
}

export interface CDMUploadResponse {
  document_id: string
  ingest_status: CDMIngestStatus
}

export interface CDMJobStatusResponse {
  document_id: string
  ingest_status: CDMIngestStatus
  ingest_error: string | null
  word_count: number | null
}

export interface CDMCapError {
  detail: string
  cap: 'documents' | 'tokens' | 'proposed_mappings'
}

/**
 * Upload a CDM document. Returns immediately with `ingest_status='pending'`;
 * caller should poll `getCdmJobStatus` to follow the state machine.
 *
 * Sends multipart/form-data — DO NOT set Content-Type manually; the browser
 * needs to add the boundary itself.
 */
export async function uploadCdmDocument(
  orgId: string,
  file: File,
): Promise<CDMUploadResponse> {
  const formData = new FormData()
  formData.append('file', file)

  const url = `${API_BASE_URL}/organizations/${orgId}/cdm/upload`

  const response = await fetchWithOidcRetry((bearer) =>
    fetch(url, {
      method: 'POST',
      headers: { Authorization: `Bearer ${bearer}` },
      body: formData,
    })
  )

  if (!response.ok) {
    const errorText = await response.text()
    let errorMessage = `Upload failed: ${response.status} ${response.statusText}`
    try {
      const errorJson = JSON.parse(errorText)
      const detail = errorJson.detail
      if (typeof detail === 'string') {
        errorMessage = detail
      } else if (detail && typeof detail === 'object' && 'cap' in detail) {
        // Structured cap error from slice 7 — surface the underlying message.
        errorMessage = (detail as CDMCapError).detail
      }
    } catch {
      // Non-JSON error body — keep generic message.
    }
    throw new Error(errorMessage)
  }

  return response.json()
}

export async function getCdmJobStatus(
  orgId: string,
  documentId: string,
): Promise<CDMJobStatusResponse> {
  return apiFetch<CDMJobStatusResponse>(
    `/organizations/${orgId}/cdm/jobs/${documentId}`,
  )
}

export async function listCdmDocuments(
  orgId: string,
  limit = 50,
  offset = 0,
): Promise<CDMDocumentListResponse> {
  return apiFetch<CDMDocumentListResponse>(
    `/organizations/${orgId}/cdm/documents?limit=${limit}&offset=${offset}`,
  )
}

export async function deleteCdmDocument(
  orgId: string,
  documentId: string,
): Promise<void> {
  const url = `${API_BASE_URL}/organizations/${orgId}/cdm/documents/${documentId}`

  const response = await fetchWithOidcRetry((bearer) =>
    fetch(url, {
      method: 'DELETE',
      headers: { Authorization: `Bearer ${bearer}` },
    })
  )

  if (!response.ok && response.status !== 204) {
    const errorText = await response.text()
    let errorMessage = `Delete failed: ${response.status} ${response.statusText}`
    try {
      const errorJson = JSON.parse(errorText)
      if (typeof errorJson.detail === 'string') {
        errorMessage = errorJson.detail
      }
    } catch {
      /* keep generic */
    }
    throw new Error(errorMessage)
  }
}

export type CDMMappingStatus = 'proposed' | 'accepted' | 'dismissed' | 'stale'

export interface CDMMapping {
  id: string
  organization_id: string
  scoped_control_id: string
  cdm_document_id: string
  section: string | null
  byte_offset_start: number
  byte_offset_end: number
  relevance_score: number
  status: CDMMappingStatus
  kb_revision: string
  accepted_by_user_id: string | null
  accepted_at: string | null
  dismiss_reason: string | null
  dismissed_by_user_id: string | null
  dismissed_at: string | null
  excerpt: string | null
  review_notes: string | null
  last_reviewed_at: string | null
  last_reviewed_by_user_id: string | null
  created_at: string
  scf_id: string | null
  original_filename: string | null
}

export interface CDMMappingListResponse {
  mappings: CDMMapping[]
  total: number
  offset: number
  limit: number
}

export interface ListCdmMappingsParams {
  status?: CDMMappingStatus
  controlId?: string
  limit?: number
  offset?: number
}

export async function listCdmMappings(
  orgId: string,
  params: ListCdmMappingsParams = {},
): Promise<CDMMappingListResponse> {
  const search = new URLSearchParams()
  if (params.status) search.set('status', params.status)
  if (params.controlId) search.set('control_id', params.controlId)
  search.set('limit', String(params.limit ?? 25))
  search.set('offset', String(params.offset ?? 0))
  return apiFetch<CDMMappingListResponse>(
    `/organizations/${orgId}/cdm/mappings?${search.toString()}`,
  )
}

export interface CDMMappingAcceptResponse {
  mapping_id: string
  status: 'accepted'
  accepted_at: string
  accepted_by_user_id: string
}

export async function acceptCdmMapping(
  orgId: string,
  mappingId: string,
): Promise<CDMMappingAcceptResponse> {
  return apiFetch<CDMMappingAcceptResponse>(
    `/organizations/${orgId}/cdm/mappings/${mappingId}/accept`,
    { method: 'POST' },
  )
}

export interface CDMMappingDismissResponse {
  mapping_id: string
  status: 'dismissed'
  reason: string | null
  dismissed_at: string
  dismissed_by_user_id: string
}

export async function dismissCdmMapping(
  orgId: string,
  mappingId: string,
  reason?: string | null,
): Promise<CDMMappingDismissResponse> {
  const trimmed = reason && reason.trim() ? reason.trim() : null
  return apiFetch<CDMMappingDismissResponse>(
    `/organizations/${orgId}/cdm/mappings/${mappingId}/dismiss`,
    {
      method: 'POST',
      body: trimmed ? JSON.stringify({ reason: trimmed }) : JSON.stringify({}),
    },
  )
}

export interface CDMMappingBulkResponse {
  accepted: string[]
  dismissed: string[]
  skipped: string[]
  not_found: string[]
}

export async function bulkAcceptCdmMappings(
  orgId: string,
  mappingIds: string[],
): Promise<CDMMappingBulkResponse> {
  return apiFetch<CDMMappingBulkResponse>(
    `/organizations/${orgId}/cdm/mappings/bulk-accept`,
    {
      method: 'POST',
      body: JSON.stringify({ mapping_ids: mappingIds }),
    },
  )
}

export async function bulkDismissCdmMappings(
  orgId: string,
  mappingIds: string[],
  reason?: string | null,
): Promise<CDMMappingBulkResponse> {
  const trimmed = reason && reason.trim() ? reason.trim() : null
  return apiFetch<CDMMappingBulkResponse>(
    `/organizations/${orgId}/cdm/mappings/bulk-dismiss`,
    {
      method: 'POST',
      body: JSON.stringify(
        trimmed ? { mapping_ids: mappingIds, reason: trimmed } : { mapping_ids: mappingIds },
      ),
    },
  )
}

export interface CDMMappingReviewRequest {
  notes?: string | null
  mark_reviewed?: boolean
}

export interface CDMMappingReviewResponse {
  mapping_id: string
  review_notes: string | null
  last_reviewed_at: string | null
  last_reviewed_by_user_id: string | null
}

export async function reviewCdmMapping(
  orgId: string,
  mappingId: string,
  body: CDMMappingReviewRequest,
): Promise<CDMMappingReviewResponse> {
  return apiFetch<CDMMappingReviewResponse>(
    `/organizations/${orgId}/cdm/mappings/${mappingId}/review`,
    {
      method: 'PUT',
      body: JSON.stringify(body),
    },
  )
}

export interface CDMQueryHit {
  content: string
  chunk_id?: string
  reference_id?: string
  file_path?: string
  file_source?: string
  [key: string]: unknown
}

export interface CDMQueryRequest {
  control_id: string
  query_text?: string | null
  limit?: number
}

export interface CDMQueryResponse {
  hits: CDMQueryHit[]
  kb_revision: string | null
}

export async function queryCdm(
  orgId: string,
  request: CDMQueryRequest,
): Promise<CDMQueryResponse> {
  return apiFetch<CDMQueryResponse>(
    `/organizations/${orgId}/cdm/query`,
    {
      method: 'POST',
      body: JSON.stringify(request),
    },
  )
}

export interface CDMComputeMappingsResponse {
  task_id: string
  idempotent_existing: boolean
}

export interface CDMComputeMappingsStatusResponse {
  task_id: string
  state: string
  ready: boolean
  successful: boolean | null
  result: Record<string, unknown> | null
}

export async function triggerCdmComputeMappings(
  orgId: string,
): Promise<CDMComputeMappingsResponse> {
  return apiFetch<CDMComputeMappingsResponse>(
    `/organizations/${orgId}/cdm/compute-mappings`,
    { method: 'POST' },
  )
}

export async function getCdmComputeMappingsStatus(
  orgId: string,
  taskId: string,
): Promise<CDMComputeMappingsStatusResponse> {
  return apiFetch<CDMComputeMappingsStatusResponse>(
    `/organizations/${orgId}/cdm/compute-mappings/${taskId}`,
  )
}

// ─── OSS catalogue onboarding ("bring your own SCF Excel") ──────────────────

export interface CatalogStatus {
  seeded: boolean
  controls: number
}

export interface CatalogImportAccepted {
  task_id: string
  status: string
}

export interface CatalogImportStatus {
  task_id: string
  state: string
  step: string | null
  result: { catalog_meta?: Record<string, unknown> } | null
  error: string | null
}

/** Is the SCF catalogue seeded yet? Drives the onboarding gate. Unauthenticated. */
export async function getCatalogStatus(): Promise<CatalogStatus> {
  return apiFetch<CatalogStatus>('/catalog/status')
}

/**
 * Upload an SCF .xlsx to seed the catalogue (self-hosted single-tenant only).
 * Uses a raw fetch — apiFetch forces JSON content-type, which would break the
 * multipart boundary the browser must set for a file upload.
 */
export async function uploadCatalogExcel(file: File): Promise<CatalogImportAccepted> {
  const form = new FormData()
  form.append('file', file)

  const response = await fetchWithOidcRetry((bearer) =>
    fetch(`${API_BASE_URL}/admin/catalog/import`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${bearer}` },
      body: form,
    })
  )
  if (!response.ok) {
    let message = `Upload failed: ${response.status} ${response.statusText}`
    try {
      const body = await response.json()
      if (typeof body.detail === 'string') message = body.detail
    } catch {
      // non-JSON error body — keep status text
    }
    throw new Error(message)
  }
  return response.json()
}

/** Poll a catalogue import task. */
export async function getCatalogImportStatus(taskId: string): Promise<CatalogImportStatus> {
  return apiFetch<CatalogImportStatus>(`/admin/catalog/import/${taskId}`)
}
