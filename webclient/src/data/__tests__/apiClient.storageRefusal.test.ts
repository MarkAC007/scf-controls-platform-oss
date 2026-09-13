/**
 * apiClient — a structured refusal's `message` must reach the user.
 *
 * Phase 7, ISC 53. The backend answers an organisation with no evidence store
 * with `409 {detail: {error, message}}`, and the message is the only part that
 * says what to do: go to Settings, Evidence storage. `apiFetch` decoded
 * `detail` as a string, as a validation array and as `{detail: string}` — but
 * not as `{message: string}`, so the actionable half was dropped and the
 * upload card rendered "API Error: 409 Conflict".
 *
 * The evidence-storage card had its own decoder (`errorMessageFromBody`) and
 * was fine; the UPLOAD path is the one that goes through here, which is why
 * the gap survived Phase 5.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { getEvidenceUploadUrl } from '../apiClient'

const ORG = '22222222-2222-2222-2222-222222222222'

function refusal(status: number, body: unknown) {
  return {
    ok: false,
    status,
    statusText: status === 409 ? 'Conflict' : 'Bad Request',
    text: async () => JSON.stringify(body),
    json: async () => body,
    headers: { get: () => 'application/json' },
  } as unknown as Response
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn())
  localStorage.setItem('access_token', 'test-token')
})

afterEach(() => {
  vi.unstubAllGlobals()
  localStorage.clear()
})

describe('a structured storage refusal', () => {
  it('renders the message, not the bare status', async () => {
    const message =
      'No evidence store is configured for this organisation, so evidence ' +
      'files cannot be uploaded or read. An organisation administrator sets ' +
      'one up under Settings → Evidence storage.'
    vi.mocked(fetch).mockResolvedValue(
      refusal(409, {
        detail: { error: 'evidence_storage_not_configured', message },
      })
    )

    await expect(
      getEvidenceUploadUrl(
        'EV-1',
        { filename: 'p.pdf', content_type: 'application/pdf', file_size_bytes: 10 },
        ORG
      )
    ).rejects.toThrow(message)
  })

  it('names the screen, so the user is not left guessing', async () => {
    vi.mocked(fetch).mockResolvedValue(
      refusal(409, {
        detail: {
          error: 'evidence_storage_not_configured',
          message: 'Configure one under Settings → Evidence storage.',
        },
      })
    )

    await expect(
      getEvidenceUploadUrl(
        'EV-1',
        { filename: 'p.pdf', content_type: 'application/pdf', file_size_bytes: 10 },
        ORG
      )
    ).rejects.toThrow(/Settings .* Evidence storage/)
  })

  it('still prefers detail.detail for the cap-exceeded shape', async () => {
    // The pre-existing branch. `message` is checked AFTER it, so a body that
    // carries both keeps its old meaning.
    vi.mocked(fetch).mockResolvedValue(
      refusal(400, {
        detail: { detail: 'Evidence cap reached', cap: 'evidence', message: 'ignored' },
      })
    )

    await expect(
      getEvidenceUploadUrl(
        'EV-1',
        { filename: 'p.pdf', content_type: 'application/pdf', file_size_bytes: 10 },
        ORG
      )
    ).rejects.toThrow('Evidence cap reached')
  })

  it('still renders a plain string detail', async () => {
    vi.mocked(fetch).mockResolvedValue(
      refusal(400, { detail: 'Content type application/x-msdownload is not allowed' })
    )

    await expect(
      getEvidenceUploadUrl(
        'EV-1',
        { filename: 'p.exe', content_type: 'application/x-msdownload', file_size_bytes: 10 },
        ORG
      )
    ).rejects.toThrow('not allowed')
  })
})
