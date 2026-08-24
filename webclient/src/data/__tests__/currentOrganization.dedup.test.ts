import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import {
  getCurrentOrganization,
  getEvidenceGaps,
  getFrameworkReadiness,
  setCurrentOrganization,
} from '../apiClient'
import { loadScopedControls } from '../scopingService'

/**
 * Org-fetch deduplication (#811).
 *
 * A cold Dashboard load issued five identical `GET /api/organizations/{id}`
 * requests. None of them wanted the organisation record: every org-scoped
 * helper in apiClient calls getCurrentOrganization() purely to turn the stored
 * org id into a URL segment, so the endpoint was hit once per data fetch.
 *
 * The Dashboard's cold-load fan-out is exactly those five resolutions:
 *   loadScopedControls()      -> getCurrentOrganization()   (1)
 *                             -> getScopedControls()        (2)
 *                             -> getEvidenceTracking()      (3)
 *   getEvidenceGaps()                                       (4)
 *   getFrameworkReadiness()                                 (5)
 *
 * Driving the data layer rather than mounting App is deliberate — App needs
 * auth, org and query providers, and the requests under test are made by plain
 * async functions underneath all of them.
 */

const ORG_ID = '6a1dad6d-f04e-4f40-bffc-931bc79708da'
const ORG_ENDPOINT = `/api/organizations/${ORG_ID}`

const ORG_RECORD = {
  id: ORG_ID,
  name: 'Acme',
  slug: 'acme',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}

/** Requests every URL the fan-out touches, recording each one. */
function installFetchMock(): string[] {
  const calls: string[] = []

  const json = (body: unknown) =>
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })

  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString()
      calls.push(url)

      if (url === ORG_ENDPOINT) return json(ORG_RECORD)
      if (url.endsWith('/scoped-controls')) return json([])
      if (url.endsWith('/evidence-tracking')) return json([])
      if (url.endsWith('/evidence-gaps')) return json({ gaps: [], total_gaps: 0 })
      if (url.endsWith('/framework-readiness')) return json({ frameworks: [] })
      if (url === '/api/organizations') return json([ORG_RECORD])

      return json({})
    })
  )

  return calls
}

/** Calls to the org record itself — exact path, no suffix, no query string. */
const orgRecordCalls = (calls: string[]) => calls.filter((url) => url === ORG_ENDPOINT)

describe('current organization fetch deduplication', () => {
  beforeEach(() => {
    localStorage.clear()
    // Also clears the module-level cache, so each test starts cold.
    setCurrentOrganization(ORG_ID)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    localStorage.clear()
  })

  it('fetches the organization record once for a cold dashboard load', async () => {
    const calls = installFetchMock()

    await Promise.all([
      loadScopedControls(),
      getEvidenceGaps(),
      getFrameworkReadiness({ frameworks: {} }),
    ])

    expect(orgRecordCalls(calls)).toEqual([ORG_ENDPOINT])
  })

  it('shares one in-flight request between concurrent callers', async () => {
    const calls = installFetchMock()

    const [a, b, c] = await Promise.all([
      getCurrentOrganization(),
      getCurrentOrganization(),
      getCurrentOrganization(),
    ])

    expect(orgRecordCalls(calls)).toHaveLength(1)
    expect(a).toEqual(ORG_RECORD)
    expect(b).toEqual(ORG_RECORD)
    expect(c).toEqual(ORG_RECORD)
  })

  it('refetches after switching organization', async () => {
    const calls = installFetchMock()
    const otherOrgId = '11111111-2222-3333-4444-555555555555'

    await getCurrentOrganization()
    setCurrentOrganization(otherOrgId)
    await getCurrentOrganization()

    expect(calls.filter((url) => url.startsWith('/api/organizations/'))).toEqual([
      ORG_ENDPOINT,
      `/api/organizations/${otherOrgId}`,
    ])
  })

  it('does not cache a failed lookup', async () => {
    const calls: string[] = []
    let attempt = 0

    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = typeof input === 'string' ? input : input.toString()
        calls.push(url)
        attempt += 1
        if (attempt === 1) {
          return new Response(JSON.stringify({ detail: 'boom' }), { status: 500 })
        }
        return new Response(JSON.stringify(ORG_RECORD), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      })
    )

    await expect(getCurrentOrganization()).rejects.toThrow()
    await expect(getCurrentOrganization()).resolves.toEqual(ORG_RECORD)
    expect(orgRecordCalls(calls)).toHaveLength(2)
  })
})
