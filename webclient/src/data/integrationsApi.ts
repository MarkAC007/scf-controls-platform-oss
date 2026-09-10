/**
 * Integrations API client — the platform-admin credential surface behind
 * /api/admin/integrations (issue #947, CONTRACT.md §3e).
 *
 * Write-only by design: no endpoint here ever returns a stored credential, so
 * nothing in this module returns one either. Callers send a value in and read
 * back metadata (configured / source / who changed it and when).
 *
 * Follows catalogUpgradeApi.ts: a small fetch wrapper sharing token resolution
 * with apiClient.ts via ./authToken, with one OIDC refresh-and-retry on 401.
 * The thrown Error carries `.status` so the UI can tell a 409 "managed by the
 * operator" apart from a 422 "empty value" without parsing the message.
 */

import { getAuthToken, refreshOidcToken, OIDC_ENABLED } from './authToken'

const API_BASE_URL = '/api'

/** Which tier actually supplies the value right now. `null` when unset. */
export type IntegrationSource = 'db' | 'file' | 'env' | null

/** One tier-3 credential row. Never carries the value itself. */
export interface IntegrationItem {
  name: string
  label: string
  feature: string
  configured: boolean
  source: IntegrationSource
  /** A file or env value is present, so the app must not write this one. */
  managed_by_operator: boolean
  updated_at: string | null
  updated_by: string | null
}

export interface IntegrationsListResponse {
  encryption_key_configured: boolean
  /** Rows still stored unencrypted from before #947; cleared by `backfill-encrypt`. */
  legacy_plaintext_rows: number
  items: IntegrationItem[]
}

export interface IntegrationsHealthResponse {
  encryption_key_configured: boolean
  configured: string[]
  unconfigured: string[]
  items: IntegrationItem[]
  secrets_dir_mode: 'file' | 'env' | 'mixed'
}

export interface IntegrationAuditEntry {
  action: string
  entity_id: string
  actor: string
  created_at: string
  ip_address: string | null
  action_source: string | null
}

export interface IntegrationsAuditResponse {
  items: IntegrationAuditEntry[]
}

/** An API failure that kept its HTTP status for the caller to branch on. */
export type IntegrationsApiError = Error & { status?: number }

/** Run a fetch with the current bearer; retry once on 401 after OIDC refresh. */
async function fetchWithAuthRetry(doFetch: (bearer: string) => Promise<Response>): Promise<Response> {
  let response = await doFetch(getAuthToken())
  if (response.status === 401 && OIDC_ENABLED) {
    const refreshed = await refreshOidcToken()
    if (refreshed) {
      response = await doFetch(refreshed)
    }
  }
  return response
}

/** Extract the FastAPI error message from a non-OK response body. */
async function errorMessageFrom(response: Response): Promise<string> {
  let message = `API Error: ${response.status} ${response.statusText}`
  try {
    const body = await response.json()
    const detail = body.detail
    if (typeof detail === 'string') {
      message = detail
    } else if (Array.isArray(detail)) {
      // FastAPI 422 validation errors: [{loc, msg, type}, ...]
      message = detail.map((e: { msg?: string }) => e.msg || 'Validation error').join('; ')
    } else if (detail && typeof detail === 'object' && typeof detail.detail === 'string') {
      message = detail.detail
    }
  } catch {
    // non-JSON error body — keep status text
  }
  return message
}

/** Generic JSON fetch wrapper for the integrations endpoints. */
async function integrationsFetch<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const response = await fetchWithAuthRetry((bearer) =>
    fetch(`${API_BASE_URL}${endpoint}`, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${bearer}`,
        ...options.headers,
      },
    })
  )
  if (!response.ok) {
    const error: IntegrationsApiError = new Error(await errorMessageFrom(response))
    error.status = response.status
    throw error
  }
  return response.json()
}

/** Every tier-3 credential with its configured/source metadata. */
export async function listIntegrations(): Promise<IntegrationsListResponse> {
  return integrationsFetch<IntegrationsListResponse>('/admin/integrations')
}

/**
 * Store (or replace) one credential. 404 unknown name, 409 when no encryption
 * key is configured or the value is managed by the operator, 422 when empty.
 */
export async function setIntegration(name: string, value: string): Promise<IntegrationItem> {
  return integrationsFetch<IntegrationItem>(`/admin/integrations/${encodeURIComponent(name)}`, {
    method: 'PUT',
    body: JSON.stringify({ value }),
  })
}

/** Remove the app-managed value. Any file/env value keeps working. */
export async function clearIntegration(name: string): Promise<IntegrationItem> {
  return integrationsFetch<IntegrationItem>(`/admin/integrations/${encodeURIComponent(name)}`, {
    method: 'DELETE',
  })
}

/** Setup health: which credentials are configured, and how they are supplied. */
export async function getIntegrationsHealth(): Promise<IntegrationsHealthResponse> {
  return integrationsFetch<IntegrationsHealthResponse>('/admin/integrations/health')
}

/** Recent credential changes from the platform audit log. Never includes values. */
export async function getIntegrationsAudit(limit = 50): Promise<IntegrationsAuditResponse> {
  return integrationsFetch<IntegrationsAuditResponse>(
    `/admin/integrations/audit?limit=${encodeURIComponent(String(limit))}`
  )
}
