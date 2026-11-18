// SPDX-License-Identifier: AGPL-3.0-only
/**
 * apiClient — the theme controls request carries the scope it was asked for.
 *
 * The route lists in-scope controls only by default. The theme detail's
 * Catalog tab needs every control, so `scope_status` has to reach the URL.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { getCapabilityThemeControls } from '../apiClient'

const ORG = '22222222-2222-2222-2222-222222222222'

function ok(body: unknown) {
  return {
    ok: true,
    status: 200,
    statusText: 'OK',
    text: async () => JSON.stringify(body),
    json: async () => body,
    headers: { get: () => 'application/json' },
  } as unknown as Response
}

function requestedUrl(): string {
  return String(vi.mocked(fetch).mock.calls[0][0])
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn())
  localStorage.setItem('access_token', 'test-token')
  vi.mocked(fetch).mockResolvedValue(ok({ controls: [], total: 0, offset: 0, limit: 200 }))
})

afterEach(() => {
  vi.unstubAllGlobals()
  localStorage.clear()
})

describe('getCapabilityThemeControls', () => {
  it('sends scope_status when one is given', async () => {
    await getCapabilityThemeControls('CM', { limit: 200, offset: 200, scope_status: 'all' }, ORG)
    const url = requestedUrl()
    expect(url).toContain(`/organizations/${ORG}/capability-themes/CM/controls?`)
    expect(url).toContain('scope_status=all')
    expect(url).toContain('limit=200')
    expect(url).toContain('offset=200')
  })

  it('leaves it out otherwise, so the server default still applies', async () => {
    await getCapabilityThemeControls('CM', { limit: 50 }, ORG)
    expect(requestedUrl()).not.toContain('scope_status')
  })
})
