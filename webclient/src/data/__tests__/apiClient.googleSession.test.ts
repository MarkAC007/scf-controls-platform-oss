/**
 * apiClient — in Google auth mode only a 401 may end the session.
 *
 * Prod runs Google auth. Opening an evidence task fans out ~40 requests; a
 * transient Google failure on one of them used to come back 401 and the user
 * was bounced to sign-in mid-click. The backend now answers that case 503, and
 * a 403 is an ordinary RBAC "permission denied". Neither may clear the stored
 * token or reload the page — only a genuine 401 does.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const clearAuthSession = vi.fn()

vi.mock('../authToken', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../authToken')>()
  return {
    ...actual,
    OIDC_ENABLED: false,
    GOOGLE_AUTH_ENABLED: true,
    getAuthToken: () => 'google-access-token',
    getGoogleToken: () => 'google-access-token',
    clearAuthSession: () => clearAuthSession(),
  }
})

import { getEvidenceUploadUrl } from '../apiClient'

const ORG = '22222222-2222-2222-2222-222222222222'
const reload = vi.fn()

function reply(status: number, body: unknown) {
  return {
    ok: false,
    status,
    statusText: String(status),
    text: async () => JSON.stringify(body),
    json: async () => body,
    headers: { get: () => 'application/json' },
  } as unknown as Response
}

function callApi() {
  return getEvidenceUploadUrl(
    'EV-1',
    { filename: 'p.pdf', content_type: 'application/pdf', file_size_bytes: 10 },
    ORG
  )
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn())
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: { ...window.location, reload },
  })
  clearAuthSession.mockClear()
  reload.mockClear()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('Google auth mode session handling', () => {
  it('keeps the session when Google is temporarily unavailable (503)', async () => {
    vi.mocked(fetch).mockResolvedValue(
      reply(503, { detail: 'Google sign-in is temporarily unavailable. Please retry.' })
    )
    await expect(callApi()).rejects.toMatchObject({ status: 503 })
    expect(clearAuthSession).not.toHaveBeenCalled()
    expect(reload).not.toHaveBeenCalled()
  })

  it('keeps the session on an RBAC permission denial (403)', async () => {
    vi.mocked(fetch).mockResolvedValue(reply(403, { detail: 'Insufficient permissions' }))
    await expect(callApi()).rejects.toMatchObject({ status: 403 })
    expect(clearAuthSession).not.toHaveBeenCalled()
    expect(reload).not.toHaveBeenCalled()
  })

  it('still ends the session on a genuine 401', async () => {
    vi.mocked(fetch).mockResolvedValue(reply(401, { detail: 'Invalid Google authentication token' }))
    await expect(callApi()).rejects.toThrow('Session expired')
    expect(clearAuthSession).toHaveBeenCalledTimes(1)
    expect(reload).toHaveBeenCalledTimes(1)
  })
})
