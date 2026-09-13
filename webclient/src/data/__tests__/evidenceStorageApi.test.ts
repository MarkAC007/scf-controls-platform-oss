/**
 * evidenceStorageApi — the org-scoped evidence storage client.
 *
 * Two things are pinned here that nothing else can pin:
 *   1. **No type in this module declares a secret-bearing field.** That is a
 *      compile-time assertion, not a runtime one: adding `secret_access_key`
 *      to the config interface would fail `tsc`, which is the only place a
 *      field can be added.
 *   2. The structured 409 bodies survive the fetch wrapper. A delete refused
 *      because evidence still lives in the store carries a file count, and a
 *      failed activation carries the per-step probe report; both are useless
 *      if the wrapper keeps only the message.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  activateEvidenceStorageConfig,
  createEvidenceStorageConfig,
  deleteEvidenceStorageConfig,
  errorDetailOf,
  getEffectiveEvidenceStorage,
  listEvidenceStorageConfigs,
  retireEvidenceStorageConfig,
  rotateEvidenceStorageSecret,
  testEvidenceStorageConfig,
  updateEvidenceStorageConfig,
} from '../evidenceStorageApi'
import type {
  EvidenceStorageApiError,
  EvidenceStorageConfig,
  EvidenceStorageConfigInput,
  EvidenceStorageEffective,
} from '../evidenceStorageApi'

// --- ISC 42, at type level -------------------------------------------------

/** Every name that could hold, or be mistaken for, a stored credential. */
type SecretBearing =
  | 'value'
  | 'secret'
  | 'secret_access_key'
  | 'secret_ciphertext'
  | 'password'
  | 'credential'

/** `true` only when `T` declares none of them. */
type DeclaresNoSecret<T> = Extract<keyof T, SecretBearing> extends never ? true : false

// If any of these stops compiling, a type in the module grew a field that
// could carry a secret back from the server, and the DOM leak test in
// EvidenceStorageSettings.test.tsx is no longer the only thing standing
// between a backend mistake and a rendered credential.
const CONFIG_DECLARES_NO_SECRET: DeclaresNoSecret<EvidenceStorageConfig> = true
const EFFECTIVE_DECLARES_NO_SECRET: DeclaresNoSecret<EvidenceStorageEffective> = true

// --- fetch stub ------------------------------------------------------------

interface Call {
  url: string
  init: RequestInit
}

let calls: Call[] = []

function stubFetch(status: number, body: unknown) {
  const fetchMock = vi.fn(async (url: string, init: RequestInit = {}) => {
    calls.push({ url, init })
    return {
      ok: status >= 200 && status < 300,
      status,
      statusText: status === 409 ? 'Conflict' : 'OK',
      json: async () => body,
    } as unknown as Response
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function aConfig(overrides: Partial<EvidenceStorageConfig> = {}): EvidenceStorageConfig {
  return {
    id: 'cfg-1',
    organization_id: 'org-1',
    provider: 's3_compatible',
    provider_label: 'Other S3-compatible',
    bucket: 'evidence',
    region: 'eu-west-1',
    endpoint_url: 'https://objects.example.com',
    public_endpoint: null,
    path_style: true,
    sse_mode: 'none',
    access_key_id: 'AKIAEXAMPLE',
    secret_mask: '••••••••',
    key_version: 1,
    status: 'draft',
    is_bundled: false,
    source: 'org',
    managed_by_operator: false,
    created_at: '2026-09-12T09:00:00Z',
    updated_at: '2026-09-12T09:00:00Z',
    updated_by: 'admin@example.com',
  }
}

describe('evidenceStorageApi', () => {
  beforeEach(() => {
    calls = []
    vi.unstubAllGlobals()
  })

  it('compiles only while no type here declares a secret-bearing field', () => {
    expect(CONFIG_DECLARES_NO_SECRET).toBe(true)
    expect(EFFECTIVE_DECLARES_NO_SECRET).toBe(true)
  })

  it('scopes every route to the organisation', async () => {
    stubFetch(200, { items: [] })
    await listEvidenceStorageConfigs('org-1')
    expect(calls[0].url).toBe('/api/organizations/org-1/evidence-storage')

    stubFetch(200, {})
    await getEffectiveEvidenceStorage('org-1')
    expect(calls[1].url).toBe('/api/organizations/org-1/evidence-storage/effective')

    stubFetch(200, aConfig())
    await activateEvidenceStorageConfig('org-1', 'cfg-1')
    expect(calls[2].url).toBe('/api/organizations/org-1/evidence-storage/cfg-1/activate')
    expect(calls[2].init.method).toBe('POST')

    stubFetch(200, aConfig())
    await retireEvidenceStorageConfig('org-1', 'cfg-1')
    expect(calls[3].url).toBe('/api/organizations/org-1/evidence-storage/cfg-1/retire')

    stubFetch(200, { success: true, config_id: 'cfg-1', steps: [] })
    await testEvidenceStorageConfig('org-1', 'cfg-1')
    expect(calls[4].url).toBe('/api/organizations/org-1/evidence-storage/test')
    expect(JSON.parse(String(calls[4].init.body))).toEqual({ config_id: 'cfg-1' })
  })

  it('sends only the fields the backend accepts, and never is_bundled', async () => {
    stubFetch(200, aConfig())
    const input: EvidenceStorageConfigInput = {
      provider: 'gcs',
      bucket: 'evidence',
      region: 'auto',
      access_key_id: 'GOOG1EXAMPLE',
      secret_access_key: 'hmac-secret',
    }
    await createEvidenceStorageConfig('org-1', input)
    const sent = JSON.parse(String(calls[0].init.body))
    expect(sent).toEqual(input)
    expect(Object.keys(sent)).not.toContain('is_bundled')
    expect(Object.keys(sent)).not.toContain('status')
    expect(Object.keys(sent)).not.toContain('organization_id')
  })

  it('patches with PATCH and rotates with a write-only secret', async () => {
    stubFetch(200, aConfig())
    await updateEvidenceStorageConfig('org-1', 'cfg-1', { bucket: 'other' })
    expect(calls[0].init.method).toBe('PATCH')
    expect(JSON.parse(String(calls[0].init.body))).toEqual({ bucket: 'other' })

    stubFetch(200, aConfig())
    await rotateEvidenceStorageSecret('org-1', 'cfg-1', { secret_access_key: 'new-secret' })
    expect(calls[1].url).toBe('/api/organizations/org-1/evidence-storage/cfg-1/rotate')
    expect(JSON.parse(String(calls[1].init.body))).toEqual({ secret_access_key: 'new-secret' })
  })

  it('keeps the file count off a delete refused because files reference the store', async () => {
    stubFetch(409, {
      detail: {
        message: 'Evidence files are still stored under this configuration',
        evidence_file_count: 7,
      },
    })
    const err = await deleteEvidenceStorageConfig('org-1', 'cfg-1').catch((e) => e)
    expect((err as EvidenceStorageApiError).status).toBe(409)
    expect((err as Error).message).toBe(
      'Evidence files are still stored under this configuration'
    )
    expect(errorDetailOf(err)?.evidence_file_count).toBe(7)
  })

  it('keeps the per-step report off a failed activation', async () => {
    stubFetch(409, {
      detail: {
        message: 'The connection test failed, so the configuration was not activated',
        report: { success: false, steps: [{ name: 'put', ok: false, error_class: 'ClientError' }] },
      },
    })
    const err = await activateEvidenceStorageConfig('org-1', 'cfg-1').catch((e) => e)
    expect(errorDetailOf(err)?.report?.steps?.[0]).toMatchObject({ name: 'put', ok: false })
  })

  it('keeps the missing-encryption-key flag off a 409', async () => {
    stubFetch(409, {
      detail: { message: 'SCF_SECRET_KEY is not configured — see docs', encryption_key_configured: false },
    })
    const err = await createEvidenceStorageConfig('org-1', {
      provider: 'aws_s3',
      bucket: 'evidence',
    }).catch((e) => e)
    expect(errorDetailOf(err)?.encryption_key_configured).toBe(false)
  })

  it('returns nothing, and does not parse a body, on a 204 delete', async () => {
    const fetchMock = stubFetch(204, undefined)
    await expect(deleteEvidenceStorageConfig('org-1', 'cfg-1')).resolves.toBeUndefined()
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('reports a 403 with its status so the card can render a read-only state', async () => {
    stubFetch(403, { detail: 'Requires admin role' })
    const err = await getEffectiveEvidenceStorage('org-1').catch((e) => e)
    expect((err as EvidenceStorageApiError).status).toBe(403)
    expect((err as Error).message).toBe('Requires admin role')
  })
})
