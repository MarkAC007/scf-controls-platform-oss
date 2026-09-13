/**
 * errorMessageFrom — a 409 whose detail is an object with a `message` must
 * render its text, not a bare status (ISA phase 5, frontend defect 2).
 *
 * The backend returns that shape in four places that matter to a user:
 *   - `OperatorManaged` on Integrations (`api/integrations.py:140-147`),
 *   - the missing `SCF_SECRET_KEY` 409,
 *   - an activation or rotation whose probe failed,
 *   - a delete refused because evidence files still reference the store.
 *
 * Before this fix every one of them surfaced as `API Error: 409 Conflict`, so
 * the operator was told a status code and nothing about the cause.
 */
import { describe, expect, it } from 'vitest'

import { errorMessageFrom, errorMessageFromBody } from '../integrationsApi'

/** A Response-shaped stub; only `status`, `statusText` and `json` are read. */
function jsonResponse(body: unknown, status = 409, statusText = 'Conflict'): Response {
  return {
    status,
    statusText,
    json: async () => body,
  } as unknown as Response
}

describe('errorMessageFromBody', () => {
  it('renders the text of an object detail carrying a message', () => {
    const message = errorMessageFromBody(
      {
        detail: {
          message: 'OIDC_CLIENT_SECRET is supplied by the operator in a secrets file',
          managed_by_operator: true,
          source: 'file',
        },
      },
      'API Error: 409 Conflict'
    )
    expect(message).toBe(
      'OIDC_CLIENT_SECRET is supplied by the operator in a secrets file'
    )
  })

  it('renders the message of a failed activation, which also carries a report', () => {
    const message = errorMessageFromBody(
      {
        detail: {
          message: 'The connection test failed, so the configuration was not activated',
          report: { success: false, steps: [{ name: 'put', ok: false }] },
        },
      },
      'API Error: 409 Conflict'
    )
    expect(message).toBe(
      'The connection test failed, so the configuration was not activated'
    )
  })

  it('still prefers a nested detail string when both shapes are present', () => {
    const message = errorMessageFromBody(
      { detail: { detail: 'nested wins', message: 'not this one' } },
      'fallback'
    )
    expect(message).toBe('nested wins')
  })

  it('handles the three shapes it already handled', () => {
    expect(errorMessageFromBody({ detail: 'plain string' }, 'fallback')).toBe('plain string')
    expect(
      errorMessageFromBody({ detail: [{ msg: 'bucket is required' }, { msg: 'bad provider' }] }, 'f')
    ).toBe('bucket is required; bad provider')
    expect(errorMessageFromBody({ detail: { managed_by_operator: true } }, 'fallback')).toBe(
      'fallback'
    )
  })

  it('falls back when the body is not an object at all', () => {
    expect(errorMessageFromBody(null, 'fallback')).toBe('fallback')
    expect(errorMessageFromBody('a string body', 'fallback')).toBe('fallback')
  })

  it('does not treat an empty message as a message', () => {
    expect(errorMessageFromBody({ detail: { message: '' } }, 'fallback')).toBe('fallback')
  })
})

describe('errorMessageFrom', () => {
  it('unwraps a message detail off a real response', async () => {
    const response = jsonResponse({
      detail: { message: 'SCF_SECRET_KEY is not configured — see docs', encryption_key_configured: false },
    })
    await expect(errorMessageFrom(response)).resolves.toBe(
      'SCF_SECRET_KEY is not configured — see docs'
    )
  })

  it('keeps the status line when the body is not JSON', async () => {
    const response = {
      status: 502,
      statusText: 'Bad Gateway',
      json: async () => {
        throw new Error('not json')
      },
    } as unknown as Response
    await expect(errorMessageFrom(response)).resolves.toBe('API Error: 502 Bad Gateway')
  })
})
