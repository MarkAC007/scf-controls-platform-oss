/**
 * Catalog Upgrade API Client — platform-admin catalog upgrade surface.
 *
 * Wraps the frozen WP-C routes (plan §4.5) under /api/admin/catalog/upgrade
 * plus the extended /api/catalog/status. Follows the catalogApi.ts pattern:
 * a small fetch wrapper sharing token resolution with apiClient.ts via
 * ./authToken, with one OIDC refresh-and-retry on 401.
 */

import { getAuthToken, refreshOidcToken, OIDC_ENABLED } from './authToken'
import type {
  CatalogEntityType,
  CatalogStatusExtended,
  ChangeClass,
  DiffPageResponse,
  OrgCatalogStatusResponse,
  OrgChangelogResponse,
  OrgReconciliationRunDetail,
  OrgReconciliationRunsListResponse,
  PairingsUpdateResponse,
  PlannedAction,
  PlatformImportRunDetail,
  PlatformImportRunsListResponse,
  ReconciliationActionsUpdateResponse,
  ReconciliationApplyResponse,
  ReconciliationCancelResponse,
  ReconciliationPreviewResponse,
  ReconciliationRollbackResponse,
  SupersededPairing,
  TenantsBoardResponse,
  UpgradeApplyResponse,
  UpgradeCancelResponse,
  UpgradeRevertResponse,
  UpgradeUploadResponse,
} from '../types/catalogUpgrade'

const API_BASE_URL = '/api'

/**
 * Thrown by revertCatalogUpgrade on 409: the platform revert is refused while
 * organisations are still reconciled forward to the run's to_version. The
 * blocking organisations (parsed best-effort from the error payload) are in
 * ``blockers``; the raw backend message is the Error message.
 */
export class RevertBlockedError extends Error {
  blockers: string[]

  constructor(message: string, blockers: string[]) {
    super(message)
    this.name = 'RevertBlockedError'
    this.blockers = blockers
  }
}

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
      message = detail.map((e: { msg?: string }) => e.msg || 'Validation error').join('; ')
    } else if (detail && typeof detail === 'object' && typeof detail.detail === 'string') {
      message = detail.detail
    }
  } catch {
    // non-JSON error body — keep status text
  }
  return message
}

/** Generic JSON fetch wrapper for the catalog upgrade endpoints. */
async function upgradeFetch<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
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

/** Tenant reconciliation board: org × reconciled version × eligibility. */
export async function getTenantsBoard(): Promise<TenantsBoardResponse> {
  return upgradeFetch<TenantsBoardResponse>('/admin/catalog/tenants')
}

/** Org reconciliation status: versions, eligibility, active run. */
export async function getOrgReconciliationStatus(orgId: string): Promise<OrgCatalogStatusResponse> {
  return upgradeFetch<OrgCatalogStatusResponse>(
    `/organizations/${orgId}/catalog-reconciliation/status`
  )
}

/**
 * Create a reconciliation preview (synchronous — run lands in 'previewed').
 * target_version defaults server-side to the platform's ledger version.
 */
export async function postOrgReconciliationPreview(
  orgId: string,
  targetVersion?: string
): Promise<ReconciliationPreviewResponse> {
  return upgradeFetch<ReconciliationPreviewResponse>(
    `/organizations/${orgId}/catalog-reconciliation/preview`,
    {
      method: 'POST',
      body: JSON.stringify({ target_version: targetVersion ?? null }),
    }
  )
}

/** List an org's reconciliation runs, newest first. */
export async function listOrgReconciliationRuns(
  orgId: string
): Promise<OrgReconciliationRunsListResponse> {
  return upgradeFetch<OrgReconciliationRunsListResponse>(
    `/organizations/${orgId}/catalog-reconciliation/runs`
  )
}

/** Fetch one org reconciliation run with planned actions and log. */
export async function getOrgReconciliationRun(
  orgId: string,
  runId: string
): Promise<OrgReconciliationRunDetail> {
  return upgradeFetch<OrgReconciliationRunDetail>(
    `/organizations/${orgId}/catalog-reconciliation/runs/${runId}`
  )
}

/**
 * Replace the run's planned actions; on the org's first reconciliation the
 * confirmed framework list travels in the same request (plan §4.3e).
 */
export async function putOrgReconciliationActions(
  orgId: string,
  runId: string,
  actions: PlannedAction[],
  confirmedFrameworkIds?: string[]
): Promise<ReconciliationActionsUpdateResponse> {
  return upgradeFetch<ReconciliationActionsUpdateResponse>(
    `/organizations/${orgId}/catalog-reconciliation/runs/${runId}/actions`,
    {
      method: 'PUT',
      body: JSON.stringify({
        actions,
        confirmed_framework_ids: confirmedFrameworkIds ?? null,
      }),
    }
  )
}

/** Apply a previewed run: stale-preview guard travels in the body (202). */
export async function applyOrgReconciliation(
  orgId: string,
  runId: string,
  expectedToVersion: string
): Promise<ReconciliationApplyResponse> {
  return upgradeFetch<ReconciliationApplyResponse>(
    `/organizations/${orgId}/catalog-reconciliation/runs/${runId}/apply`,
    {
      method: 'POST',
      body: JSON.stringify({ expected_to_version: expectedToVersion }),
    }
  )
}

/** Roll back the latest applied run: typed-confirm guard in the body (202). */
export async function rollbackOrgReconciliation(
  orgId: string,
  runId: string,
  confirmText: string
): Promise<ReconciliationRollbackResponse> {
  return upgradeFetch<ReconciliationRollbackResponse>(
    `/organizations/${orgId}/catalog-reconciliation/runs/${runId}/rollback`,
    {
      method: 'POST',
      body: JSON.stringify({ confirm_text: confirmText }),
    }
  )
}

/** Cancel a previewed run that has not been applied. */
export async function cancelOrgReconciliationRun(
  orgId: string,
  runId: string
): Promise<ReconciliationCancelResponse> {
  return upgradeFetch<ReconciliationCancelResponse>(
    `/organizations/${orgId}/catalog-reconciliation/runs/${runId}/cancel`,
    { method: 'POST' }
  )
}

/** Catalog status incl. the ledger version. Drives the version card. */
export async function getCatalogStatusExtended(): Promise<CatalogStatusExtended> {
  return upgradeFetch<CatalogStatusExtended>('/catalog/status')
}

export interface ChangelogPageParams {
  limit?: number
  offset?: number
}

/** Read-only org changelog of applied catalog changes (viewer-visible). */
export async function getOrgCatalogChangelog(
  orgId: string,
  params: ChangelogPageParams = {}
): Promise<OrgChangelogResponse> {
  const query = new URLSearchParams()
  if (params.limit !== undefined) query.set('limit', String(params.limit))
  if (params.offset !== undefined) query.set('offset', String(params.offset))
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return upgradeFetch<OrgChangelogResponse>(`/organizations/${orgId}/catalog-changelog${suffix}`)
}

/** List platform import runs, newest first. */
export async function listCatalogUpgradeRuns(): Promise<PlatformImportRunsListResponse> {
  return upgradeFetch<PlatformImportRunsListResponse>('/admin/catalog/upgrade/runs')
}

/** Fetch one run with sanity report, pairings, and error detail. */
export async function getCatalogUpgradeRun(runId: string): Promise<PlatformImportRunDetail> {
  return upgradeFetch<PlatformImportRunDetail>(`/admin/catalog/upgrade/runs/${runId}`)
}

export interface DiffPageParams {
  entity?: CatalogEntityType
  change_class?: ChangeClass
  page?: number
  page_size?: number
}

/** Fetch one page of a run's diff, optionally filtered by entity/class. */
export async function getCatalogUpgradeDiff(
  runId: string,
  params: DiffPageParams = {}
): Promise<DiffPageResponse> {
  const query = new URLSearchParams()
  if (params.entity) query.set('entity', params.entity)
  if (params.change_class) query.set('change_class', params.change_class)
  if (params.page !== undefined) query.set('page', String(params.page))
  if (params.page_size !== undefined) query.set('page_size', String(params.page_size))
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return upgradeFetch<DiffPageResponse>(`/admin/catalog/upgrade/runs/${runId}/diff${suffix}`)
}

/** Replace the full superseded-pairings list for a staged run. */
export async function putCatalogUpgradePairings(
  runId: string,
  pairings: SupersededPairing[]
): Promise<PairingsUpdateResponse> {
  return upgradeFetch<PairingsUpdateResponse>(`/admin/catalog/upgrade/runs/${runId}/pairings`, {
    method: 'PUT',
    body: JSON.stringify({ pairings }),
  })
}

/**
 * Upload a new SCF workbook to start an upgrade run (202 → staging).
 * Raw fetch: the browser must set the multipart boundary itself.
 */
export async function uploadCatalogUpgrade(file: File): Promise<UpgradeUploadResponse> {
  const form = new FormData()
  form.append('file', file)
  const response = await fetchWithAuthRetry((bearer) =>
    fetch(`${API_BASE_URL}/admin/catalog/upgrade`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${bearer}` },
      body: form,
    })
  )
  if (!response.ok) {
    throw new Error(await errorMessageFrom(response))
  }
  return response.json()
}

/** Apply a staged run: typed-confirm guard travels in the body (202). */
export async function applyCatalogUpgrade(
  runId: string,
  expectedToVersion: string,
  confirmText: string
): Promise<UpgradeApplyResponse> {
  return upgradeFetch<UpgradeApplyResponse>(`/admin/catalog/upgrade/runs/${runId}/apply`, {
    method: 'POST',
    body: JSON.stringify({ expected_to_version: expectedToVersion, confirm_text: confirmText }),
  })
}

/** Cancel a run that has not been applied. */
export async function cancelCatalogUpgradeRun(runId: string): Promise<UpgradeCancelResponse> {
  return upgradeFetch<UpgradeCancelResponse>(`/admin/catalog/upgrade/runs/${runId}/cancel`, {
    method: 'POST',
  })
}

/**
 * Revert an applied run (202). A 409 means organisations are reconciled
 * forward to the run's version — raised as RevertBlockedError with the
 * blocking org names parsed from the error payload.
 */
export async function revertCatalogUpgrade(runId: string): Promise<UpgradeRevertResponse> {
  const response = await fetchWithAuthRetry((bearer) =>
    fetch(`${API_BASE_URL}/admin/catalog/upgrade/runs/${runId}/revert`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${bearer}`,
      },
    })
  )
  if (response.status === 409) {
    let message = 'Revert blocked: organisations are reconciled to this catalog version.'
    let blockers: string[] = []
    try {
      const body = await response.json()
      const detail = body.detail ?? body
      if (typeof detail === 'string') {
        message = detail
      } else if (detail && typeof detail === 'object') {
        if (typeof detail.detail === 'string') message = detail.detail
        else if (typeof detail.message === 'string') message = detail.message
        const rawBlockers = detail.blockers ?? detail.blocking_organizations ?? detail.organizations
        if (Array.isArray(rawBlockers)) {
          blockers = rawBlockers.map((b: unknown) => {
            if (typeof b === 'string') return b
            if (b && typeof b === 'object') {
              const org = b as { organization_name?: string; name?: string; organization_id?: string; id?: string }
              return org.organization_name || org.name || org.organization_id || org.id || JSON.stringify(b)
            }
            return String(b)
          })
        }
      }
    } catch {
      // non-JSON 409 body — keep default message
    }
    throw new RevertBlockedError(message, blockers)
  }
  if (!response.ok) {
    throw new Error(await errorMessageFrom(response))
  }
  return response.json()
}
