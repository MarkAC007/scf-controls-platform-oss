/**
 * Document Generation API client.
 *
 * Follows the catalogUpgradeApi.ts pattern: a small fetch wrapper sharing
 * token resolution with apiClient.ts via ./authToken, with one OIDC
 * refresh-and-retry on 401.
 *
 * Every route is organisation-scoped. The backend resolves the organisation
 * from the caller's membership rather than from the path, so the id here
 * selects which of the caller's organisations to act on — it is not the
 * authorisation.
 */

import { getAuthToken, refreshOidcToken, OIDC_ENABLED } from './authToken'

const API_BASE_URL = '/api'

// ─── Types ───────────────────────────────────────────────────────────────

export type LifecycleStatus = 'draft' | 'in_review' | 'approved' | 'published'

export type SectionStatus =
  | 'unchanged'
  | 'updated'
  | 'human_preserved'
  | 'conflict'
  | 'new'
  | 'pending_retirement'

export interface GeneratorInfo {
  name: string
  display_name: string
  tier: number
  document_type: string
  is_derivative: boolean
  domain_scoped: boolean
  description: string
}

export interface DocumentSummary {
  id: string
  generator_name: string
  document_type: string
  domain_id: string
  title: string
  lifecycle_status: LifecycleStatus
  tier: number
  is_derivative: boolean
  generation_version: number
  catalog_version: string | null
  section_count: number
  conflict_count: number
  edited_count: number
  updated_at: string | null
}

export interface DocumentSection {
  section_id: string
  heading_text: string
  heading_level: number
  ordinal: number
  status: SectionStatus
  human_edited: boolean
  control_ids: string[]
  edited_at: string | null
}

export interface TransitionOption {
  to_status: LifecycleStatus
  label: string
}

export interface DocumentDetail extends DocumentSummary {
  merged_content: string
  sections: DocumentSection[]
  available_transitions: TransitionOption[]
}

export interface DocGenSettings {
  enabled: boolean
  derivative_generators_enabled: boolean
  licence_acknowledged: boolean
  licence_acknowledged_at: string | null
  licence_acknowledged_by_email: string | null
  licence_text_version: string | null
  daily_generation_limit: number
  platform_disabled: boolean
  acknowledgement_text: string
}

export interface GenerationRequestItem {
  generator: string
  domain_id?: string | null
}

export interface GenerationStatus {
  status: 'idle' | 'queued' | 'running' | 'completed' | 'completed_with_errors' | 'failed'
  stage?: string
  message?: string
  total?: number
  completed?: number
  generated?: number
  skipped?: number
  failed?: number
  error?: string
  results?: Array<{
    generator: string
    domain_id: string
    action: 'created' | 'updated' | 'skipped' | 'failed'
    title?: string
    conflict_count?: number
    change_reasons?: string[]
    skip_reason?: string
    error?: string
  }>
}

export interface DocumentHistory {
  transitions: Array<{
    from_status: string | null
    to_status: string
    actor_email: string | null
    reason: string | null
    trigger: string
    created_at: string | null
  }>
  versions: Array<{
    version: number
    model_id: string | null
    generator_version: string | null
    input_fingerprint: string | null
    created_at: string | null
  }>
}

// ─── Fetch plumbing ──────────────────────────────────────────────────────

async function fetchWithAuthRetry(
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

async function errorMessageFrom(response: Response): Promise<string> {
  let message = `API Error: ${response.status} ${response.statusText}`
  try {
    const body = await response.json()
    const detail = body.detail
    if (typeof detail === 'string') {
      message = detail
    } else if (Array.isArray(detail)) {
      message = detail.map((e: { msg?: string }) => e.msg || 'Validation error').join('; ')
    }
  } catch {
    // non-JSON error body — keep status text
  }
  return message
}

async function docFetch<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
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
    throw new Error(await errorMessageFrom(response))
  }
  return response.json()
}

// ─── Generators and settings ─────────────────────────────────────────────

export async function listGenerators(orgId: string): Promise<GeneratorInfo[]> {
  return docFetch(`/organizations/${orgId}/documents/generators`)
}

export async function getDocGenSettings(orgId: string): Promise<DocGenSettings> {
  return docFetch(`/organizations/${orgId}/documents/settings`)
}

export async function updateDocGenSettings(
  orgId: string,
  payload: {
    enabled?: boolean
    derivative_generators_enabled?: boolean
    acknowledge_licence?: boolean
  }
): Promise<DocGenSettings> {
  return docFetch(`/organizations/${orgId}/documents/settings`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

// ─── Generation ──────────────────────────────────────────────────────────

export async function generateDocuments(
  orgId: string,
  requests: GenerationRequestItem[],
  force = false
): Promise<{ task_id: string; queued: number }> {
  return docFetch(`/organizations/${orgId}/documents/generate`, {
    method: 'POST',
    body: JSON.stringify({ requests, force }),
  })
}

export async function getGenerationStatus(orgId: string): Promise<GenerationStatus> {
  return docFetch(`/organizations/${orgId}/documents/generation-status`)
}

// ─── Documents ───────────────────────────────────────────────────────────

export async function listDocuments(
  orgId: string,
  filters: { status?: string; documentType?: string } = {}
): Promise<DocumentSummary[]> {
  const params = new URLSearchParams()
  if (filters.status) params.set('status', filters.status)
  if (filters.documentType) params.set('document_type', filters.documentType)
  const qs = params.toString()
  return docFetch(`/organizations/${orgId}/documents${qs ? `?${qs}` : ''}`)
}

export async function getDocument(orgId: string, documentId: string): Promise<DocumentDetail> {
  return docFetch(`/organizations/${orgId}/documents/${documentId}`)
}

export async function saveSection(
  orgId: string,
  documentId: string,
  sectionId: string,
  content: string
): Promise<{ ok: boolean; lifecycle_status: LifecycleStatus }> {
  return docFetch(
    `/organizations/${orgId}/documents/${documentId}/sections/${encodeURIComponent(sectionId)}`,
    { method: 'PUT', body: JSON.stringify({ content }) }
  )
}

export async function transitionDocument(
  orgId: string,
  documentId: string,
  toStatus: LifecycleStatus,
  reason?: string
): Promise<{
  ok: boolean
  from_status: string
  to_status: string
  available_transitions: TransitionOption[]
}> {
  return docFetch(`/organizations/${orgId}/documents/${documentId}/transition`, {
    method: 'POST',
    body: JSON.stringify({ to_status: toStatus, reason: reason ?? null }),
  })
}

export async function getDocumentHistory(
  orgId: string,
  documentId: string
): Promise<DocumentHistory> {
  return docFetch(`/organizations/${orgId}/documents/${documentId}/history`)
}

export async function previewDocument(
  orgId: string,
  documentId: string
): Promise<{ html: string; title: string; lifecycle_status: LifecycleStatus }> {
  return docFetch(`/organizations/${orgId}/documents/${documentId}/preview`)
}

/**
 * Download a document. Goes through fetch rather than a plain link so the
 * bearer token travels with the request — an `<a href>` would send no
 * Authorization header and get a 401.
 */
export async function downloadDocument(
  orgId: string,
  documentId: string,
  format: 'md' | 'pdf',
  fallbackName: string
): Promise<void> {
  const response = await fetchWithAuthRetry((bearer) =>
    fetch(`${API_BASE_URL}/organizations/${orgId}/documents/${documentId}/export?format=${format}`, {
      headers: { Authorization: `Bearer ${bearer}` },
    })
  )
  if (!response.ok) {
    throw new Error(await errorMessageFrom(response))
  }

  const disposition = response.headers.get('Content-Disposition') || ''
  const match = disposition.match(/filename="?([^";]+)"?/)
  const filename = match ? match[1] : `${fallbackName}.${format}`

  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  // Revoke on the next tick: revoking synchronously can cancel the download
  // in some browsers before it has started reading the blob.
  setTimeout(() => URL.revokeObjectURL(url), 0)
}

// ─── Presentation helpers ────────────────────────────────────────────────

export const LIFECYCLE_LABELS: Record<LifecycleStatus, string> = {
  draft: 'Draft',
  in_review: 'In Review',
  approved: 'Approved',
  published: 'Published',
}

export const SECTION_STATUS_LABELS: Record<SectionStatus, string> = {
  unchanged: 'Unchanged',
  updated: 'Updated',
  human_preserved: 'Your edit kept',
  conflict: 'Needs your decision',
  new: 'New',
  pending_retirement: 'Pending retirement',
}
