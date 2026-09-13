/**
 * Evidence storage API client — an organisation's own object store, behind
 * /api/organizations/{org_id}/evidence-storage (ISA phases 2 to 5).
 *
 * Write-only, the same way integrationsApi.ts is: a stored secret is never
 * returned by any endpoint here, so no type in this module names a field that
 * could hold one. `secret_mask` is a fixed eight-character constant the backend
 * renders for a row that has a secret; it is not the secret, not derived from
 * it, and the only bit it discloses is whether one exists.
 *
 * Conventions are integrationsApi.ts's, deliberately: the same
 * `fetchWithAuthRetry` (one OIDC refresh-and-retry on 401) and the same error
 * unwrapping, imported rather than copied, so the `{message, ...}` 409 shape is
 * unwrapped identically on both screens.
 *
 * One difference from that module, and it is the point of this one: every route
 * here is scoped to an organisation and takes its id. Integrations is
 * platform-wide; an evidence store belongs to a tenant.
 */

import { errorMessageFromBody, fetchWithAuthRetry } from './integrationsApi'

const API_BASE_URL = '/api'

/** Which provider preset a configuration uses. Mirrors `PRESETS` in
 *  `backend/services/storage_config.py`. */
export type StorageProvider = 'minio' | 'aws_s3' | 'gcs' | 's3_compatible'

/** Where the configuration in force came from. The resolver's own three
 *  constants, in its resolution order: org, then platform, then environment. */
export type StorageSource = 'org' | 'platform' | 'legacy_env'

/** Lifecycle of a configuration row. */
export type StorageStatus = 'draft' | 'active' | 'retired'

/**
 * One configuration row, exactly as `EvidenceStorageConfigResponse` renders it
 * (`backend/api/evidence_storage.py`). The field set of that model is the
 * security boundary; this interface mirrors it name for name.
 *
 * There is no `secret_access_key`, no `secret_ciphertext` and no `value` here,
 * and a test asserts at type level that none of those names can be added.
 */
export interface EvidenceStorageConfig {
  id: string
  organization_id: string | null
  provider: string
  provider_label: string
  bucket: string
  region: string | null
  endpoint_url: string | null
  public_endpoint: string | null
  path_style: boolean
  sse_mode: string
  /** An identifier, not a credential. Shown so an admin knows which key is in use. */
  access_key_id: string | null
  /** The fixed mask when a secret is stored, null when none is. Never the secret. */
  secret_mask: string | null
  key_version: number
  status: string
  /**
   * Set by the installer alone and never by a request. Present because the
   * backend sends it; it must never be rendered as a control — it is what
   * exempts a row from the loopback/RFC1918/`.local` address refusals.
   */
  is_bundled: boolean
  source: string
  managed_by_operator: boolean
  created_at: string | null
  updated_at: string | null
  updated_by: string | null
}

export interface EvidenceStorageConfigListResponse {
  items: EvidenceStorageConfig[]
}

/**
 * Where this organisation's evidence actually goes right now, after
 * resolution. Mirrors `EvidenceStorageEffectiveResponse`.
 *
 * There is no credential field at all — not even a mask. `configured` is the
 * flag the blank-and-editable state keys off: a resolved configuration that
 * names no bucket means nothing is configured anywhere (ISA D42).
 */
export interface EvidenceStorageEffective {
  /** null for `legacy_env`, which is synthesised and has no row. */
  config_id: string | null
  source: string
  managed_by_operator: boolean
  configured: boolean
  /**
   * True only for the store the installer provisioned inside this stack. The
   * card used to infer this from `source === 'platform' && provider ===
   * 'minio'` because the field did not exist; the inference was sound only
   * while the installer stayed the sole writer of platform rows.
   */
  is_bundled: boolean
  provider: string | null
  provider_label: string | null
  bucket: string | null
  region: string | null
  endpoint_url: string | null
  public_endpoint: string | null
  path_style: boolean
  sse_mode: string
  key_version: number | null
}

/** One step of the round-trip probe. Never carries a response body or a URL. */
export interface EvidenceStorageTestStep {
  name: string
  ok: boolean
  status_code: number | null
  error_class: string | null
}

export interface EvidenceStorageTestResult {
  success: boolean
  config_id: string
  steps: EvidenceStorageTestStep[]
}

/** What a create request may set. No `is_bundled`, no `status`, no
 *  `organization_id`: the backend forbids extra fields and answers 422. */
export interface EvidenceStorageConfigInput {
  provider: StorageProvider
  bucket: string
  region?: string
  endpoint_url?: string
  public_endpoint?: string
  path_style?: boolean
  access_key_id?: string
  /** Write-only. Sent once, never returned. */
  secret_access_key?: string
}

/** A partial edit of a draft. An omitted secret leaves the stored one alone. */
export type EvidenceStorageConfigPatch = Partial<EvidenceStorageConfigInput>

export interface EvidenceStorageRotateInput {
  secret_access_key: string
  access_key_id?: string
}

/**
 * An API failure that kept its status and its parsed detail.
 *
 * The detail matters on this screen in a way it does not on Integrations: a
 * delete refused because evidence still lives in the store answers 409 with
 * `evidence_file_count`, and a failed activation answers 409 with the per-step
 * probe report. Both are things the operator has to see, not just a sentence.
 */
export type EvidenceStorageApiError = Error & {
  status?: number
  detail?: unknown
}

/** The structured 409 bodies this API returns, as far as the UI reads them. */
export interface EvidenceStorageErrorDetail {
  message?: string
  /** Present on the missing-encryption-key 409, and always false when present. */
  encryption_key_configured?: boolean
  /** Present when a delete is refused because files still reference the row. */
  evidence_file_count?: number
  /** Present when an activation or rotation probe failed. */
  report?: { success?: boolean; steps?: EvidenceStorageTestStep[] }
}

/** Read the parsed `detail` off a thrown error, or null if there is none. */
export function errorDetailOf(err: unknown): EvidenceStorageErrorDetail | null {
  const detail = (err as EvidenceStorageApiError | null)?.detail
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    return detail as EvidenceStorageErrorDetail
  }
  return null
}

function base(orgId: string): string {
  return `${API_BASE_URL}/organizations/${encodeURIComponent(orgId)}/evidence-storage`
}

/**
 * Generic JSON fetch for the evidence-storage endpoints.
 *
 * Parses the error body once and keeps both halves: the message goes through
 * the shared `errorMessageFromBody`, the parsed detail rides on the error so a
 * caller can read a file count or a probe report out of it.
 */
async function storageFetch<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const response = await fetchWithAuthRetry((bearer) =>
    fetch(endpoint, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${bearer}`,
        ...options.headers,
      },
    })
  )

  if (!response.ok) {
    const fallback = `API Error: ${response.status} ${response.statusText}`
    let body: unknown = null
    try {
      body = await response.json()
    } catch {
      // non-JSON error body — the status text is all there is
    }
    const error: EvidenceStorageApiError = new Error(errorMessageFromBody(body, fallback))
    error.status = response.status
    error.detail = (body as { detail?: unknown } | null)?.detail
    throw error
  }

  if (response.status === 204) return undefined as T
  return response.json()
}

/** Every configuration belonging to this organisation, newest first. */
export async function listEvidenceStorageConfigs(
  orgId: string
): Promise<EvidenceStorageConfigListResponse> {
  return storageFetch<EvidenceStorageConfigListResponse>(base(orgId))
}

/** One configuration by id. 404 for another tenant's id, deliberately. */
export async function getEvidenceStorageConfig(
  orgId: string,
  configId: string
): Promise<EvidenceStorageConfig> {
  return storageFetch<EvidenceStorageConfig>(`${base(orgId)}/${encodeURIComponent(configId)}`)
}

/** Create a draft. A draft is inert: nothing resolves it until it is activated. */
export async function createEvidenceStorageConfig(
  orgId: string,
  input: EvidenceStorageConfigInput
): Promise<EvidenceStorageConfig> {
  return storageFetch<EvidenceStorageConfig>(base(orgId), {
    method: 'POST',
    body: JSON.stringify(input),
  })
}

/** Edit a draft. Active and retired configurations refuse with 409. */
export async function updateEvidenceStorageConfig(
  orgId: string,
  configId: string,
  patch: EvidenceStorageConfigPatch
): Promise<EvidenceStorageConfig> {
  return storageFetch<EvidenceStorageConfig>(`${base(orgId)}/${encodeURIComponent(configId)}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  })
}

/** Delete. 409 while active, and 409 with `evidence_file_count` while files
 *  still hold their bytes under it. */
export async function deleteEvidenceStorageConfig(
  orgId: string,
  configId: string
): Promise<void> {
  await storageFetch<void>(`${base(orgId)}/${encodeURIComponent(configId)}`, {
    method: 'DELETE',
  })
}

/**
 * Write, read back and delete a throwaway object, reporting each step.
 *
 * Omit `configId` to test whatever this organisation resolves to today. The
 * reply never contains a response body, a URL or a credential.
 */
export async function testEvidenceStorageConfig(
  orgId: string,
  configId?: string
): Promise<EvidenceStorageTestResult> {
  return storageFetch<EvidenceStorageTestResult>(`${base(orgId)}/test`, {
    method: 'POST',
    body: JSON.stringify({ config_id: configId ?? null }),
  })
}

/** Run the probe and, only if it passes, retire the current active row and make
 *  this one live. A failing probe is a 409 carrying the per-step report. */
export async function activateEvidenceStorageConfig(
  orgId: string,
  configId: string
): Promise<EvidenceStorageConfig> {
  return storageFetch<EvidenceStorageConfig>(
    `${base(orgId)}/${encodeURIComponent(configId)}/activate`,
    { method: 'POST' }
  )
}

/** Replace the stored secret and bump the key version, which every process
 *  uses as part of its storage client cache key. */
export async function rotateEvidenceStorageSecret(
  orgId: string,
  configId: string,
  input: EvidenceStorageRotateInput
): Promise<EvidenceStorageConfig> {
  return storageFetch<EvidenceStorageConfig>(
    `${base(orgId)}/${encodeURIComponent(configId)}/rotate`,
    { method: 'POST', body: JSON.stringify(input) }
  )
}

/** Take a configuration out of service. The row stays, so evidence written
 *  under it keeps a resolvable configuration. */
export async function retireEvidenceStorageConfig(
  orgId: string,
  configId: string
): Promise<EvidenceStorageConfig> {
  return storageFetch<EvidenceStorageConfig>(
    `${base(orgId)}/${encodeURIComponent(configId)}/retire`,
    { method: 'POST' }
  )
}

/** What is in force right now, which is a different question from what rows
 *  exist whenever the answer is not a row of this organisation's own. */
export async function getEffectiveEvidenceStorage(
  orgId: string
): Promise<EvidenceStorageEffective> {
  return storageFetch<EvidenceStorageEffective>(`${base(orgId)}/effective`)
}

/** Where a copy run has got to. Mirrors the `STATE_*` constants in
 *  `backend/tasks_evidence_storage_copy.py`. `completed_with_errors` means the
 *  run finished and some rows did not move, which is a different thing from
 *  `failed`, where the run itself stopped. */
export type EvidenceStorageCopyStatus =
  | 'queued'
  | 'running'
  | 'completed'
  | 'completed_with_errors'
  | 'failed'

/** One object the copy could not move. The reason names a class of failure —
 *  a missing object, a size disagreement, a checksum mismatch, an exception
 *  type — and never a URL, a response body or a credential. */
export interface EvidenceStorageCopyFailure {
  s3_key: string
  reason: string
}

/**
 * A copy run, exactly as `EvidenceStorageCopyRunResponse` renders it.
 *
 * As with `EvidenceStorageConfig`, the field set is the security boundary: no
 * name here can hold a credential, so no later refactor can leak one through
 * this type.
 */
export interface EvidenceStorageCopyRun {
  run_id: string
  organization_id: string
  source_config_id: string
  target_config_id: string
  status: EvidenceStorageCopyStatus | string
  total: number
  copied: number
  failed: number
  skipped: number
  remaining: number
  failures: EvidenceStorageCopyFailure[]
  /** True only when no evidence file references the source any more. */
  source_retired: boolean
  /** Why it was not retired, when it was not. A store this organisation does
   *  not own — the platform store above all — is never retired by a copy. */
  source_retired_reason: string
  message: string
  started_at: string | null
  finished_at: string | null
  updated_at: string | null
}

/** True once the run has stopped moving, whichever way it ended. */
export function isCopyRunFinished(run: EvidenceStorageCopyRun | null): boolean {
  if (!run) return false
  return (
    run.status === 'completed' ||
    run.status === 'completed_with_errors' ||
    run.status === 'failed'
  )
}

/**
 * Start a copy of this organisation's evidence from one store into another.
 *
 * Answers 202 with the queued run, so the caller gets a `run_id` to poll
 * immediately rather than a job id it has to translate. A copy already running
 * for this organisation answers 409; a broker that will not take the job
 * answers 503, which is worth distinguishing in the UI because retrying is the
 * right response to one and not the other.
 */
export async function startEvidenceStorageCopy(
  orgId: string,
  sourceConfigId: string,
  targetConfigId: string
): Promise<EvidenceStorageCopyRun> {
  return storageFetch<EvidenceStorageCopyRun>(
    `${base(orgId)}/${encodeURIComponent(sourceConfigId)}` +
      `/copy-to/${encodeURIComponent(targetConfigId)}`,
    { method: 'POST' }
  )
}

/**
 * One store this organisation could copy evidence out of.
 *
 * Not the same question as the configuration list. The list says what this
 * organisation owns; this says where its evidence is, which on a bundled
 * install includes the platform store it wrote to before it brought its own.
 */
export interface EvidenceStorageCopySource {
  config_id: string
  /** `org` or `platform`. A `platform` entry is read-only and is never
   *  retired or emptied by a copy. */
  scope: string
  provider: string
  provider_label: string
  bucket: string
  endpoint_url: string | null
  status: string
  file_count: number
}

/**
 * Where this organisation's evidence is, other than the store in force now.
 *
 * The panel cannot work this out from the configuration list: after the first
 * activation the organisation's earlier files are stamped with the *platform*
 * row, which the list does not carry, and the effective read has by then moved
 * on to the organisation's own store.
 */
export async function listEvidenceStorageCopySources(
  orgId: string
): Promise<EvidenceStorageCopySource[]> {
  return storageFetch<EvidenceStorageCopySource[]>(`${base(orgId)}/copy-sources`)
}

/** The recent copy runs for this organisation, newest first. */
export async function listEvidenceStorageCopyRuns(
  orgId: string
): Promise<EvidenceStorageCopyRun[]> {
  return storageFetch<EvidenceStorageCopyRun[]>(`${base(orgId)}/copy-runs`)
}

/** One run by id. A run belonging to another organisation answers 404, the
 *  same as one that does not exist. */
export async function getEvidenceStorageCopyRun(
  orgId: string,
  runId: string
): Promise<EvidenceStorageCopyRun> {
  return storageFetch<EvidenceStorageCopyRun>(
    `${base(orgId)}/copy-runs/${encodeURIComponent(runId)}`
  )
}
