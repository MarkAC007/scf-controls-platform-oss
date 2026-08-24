/**
 * EvidenceFileUpload — preparer assertions reach the confirm (#786, #802).
 *
 * The assertion form is *ambient*: an upload starts the instant a file is
 * dropped, so there is no modal step between "chose a file" and "bytes are
 * moving" in which to ask questions. The form therefore sits beside the drop
 * zone and is applied to whatever is dropped next — which is also how a batch
 * behaves, since files dropped together share a period, a population and an
 * extract.
 *
 * That design only holds if two things are true, and both are pinned here:
 *   1. what the preparer typed actually rides along on the confirm call, and
 *   2. an incoherent assertion stops the upload *before* the bytes move,
 *      rather than after a large file has finished and the confirm 422s.
 *
 * jsdom has neither `crypto.subtle` nor a working `XMLHttpRequest` upload, so
 * both are stubbed. The stubs are deliberately thin — this file is about the
 * payload and the guard, not about transport.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react'
import { EvidenceFileUpload } from '../EvidenceFileUpload'

vi.mock('../../../data/apiClient', () => ({
  getEvidenceUploadUrl: vi.fn(),
  confirmEvidenceUpload: vi.fn(),
}))

import { getEvidenceUploadUrl, confirmEvidenceUpload } from '../../../data/apiClient'

// --- transport stubs -------------------------------------------------------

class FakeXhr {
  status = 204
  upload = { addEventListener: vi.fn() }
  private handlers: Record<string, Array<() => void>> = {}
  addEventListener(event: string, fn: () => void) {
    (this.handlers[event] ||= []).push(fn)
  }
  open = vi.fn()
  setRequestHeader = vi.fn()
  abort = vi.fn()
  send = vi.fn(() => {
    // Resolve on a later tick so the component sees `uploading` first, the way
    // a real request would.
    setTimeout(() => (this.handlers['load'] || []).forEach(fn => fn()), 0)
  })
}

function stubTransport() {
  vi.stubGlobal('XMLHttpRequest', FakeXhr as unknown as typeof XMLHttpRequest)
  vi.stubGlobal('crypto', {
    subtle: {
      digest: async () => new Uint8Array(32).fill(0xab).buffer,
    },
  })
}

function dropFile(container: HTMLElement, name = 'q2-access-review.csv') {
  const input = container.querySelector('input[type="file"]') as HTMLInputElement
  const file = new File(['a,b\n1,2\n'], name, { type: 'text/csv' })
  // jsdom's Blob has no `arrayBuffer()` (real browsers do), and the component
  // hashes the file before it uploads. Without this the hash step throws and
  // every assertion below would be testing the error path by accident.
  Object.defineProperty(file, 'arrayBuffer', { value: async () => new ArrayBuffer(8) })
  fireEvent.change(input, { target: { files: [file] } })
}

function renderUpload() {
  const onUploadComplete = vi.fn()
  const { container } = render(
    <EvidenceFileUpload orgId="org-1" evidenceId="ERL-001" onUploadComplete={onUploadComplete} />,
  )
  return { container, onUploadComplete }
}

function expand() {
  fireEvent.click(screen.getByTestId('preparer-assertions-toggle'))
}

describe('EvidenceFileUpload preparer assertions', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    stubTransport()
    vi.mocked(getEvidenceUploadUrl).mockResolvedValue({
      url: 'https://blob.example/upload',
      fields: {},
      s3_key: 'org-1/ERL-001/q2.csv',
      upload_ticket: 'ticket-abc',
      expires_in: 900,
    } as never)
    vi.mocked(confirmEvidenceUpload).mockResolvedValue({ id: 'file-1' } as never)
  })
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('carries what the preparer asserted through to the confirm', async () => {
    const { container } = renderUpload()
    expand()
    fireEvent.change(screen.getByTestId('assertion-period-start'), { target: { value: '2026-04-01' } })
    fireEvent.change(screen.getByTestId('assertion-period-end'), { target: { value: '2026-06-30' } })
    fireEvent.change(screen.getByTestId('assertion-population-size'), { target: { value: '412' } })
    fireEvent.change(screen.getByTestId('assertion-sample-size'), { target: { value: '25' } })
    fireEvent.change(screen.getByTestId('assertion-sample-method'), { target: { value: 'haphazard' } })
    fireEvent.change(screen.getByTestId('assertion-ipe-system'), { target: { value: 'Okta' } })

    dropFile(container)

    await waitFor(() => expect(confirmEvidenceUpload).toHaveBeenCalled())
    const [, body] = vi.mocked(confirmEvidenceUpload).mock.calls[0]
    expect(body).toMatchObject({
      s3_key: 'org-1/ERL-001/q2.csv',
      upload_ticket: 'ticket-abc',
      effective_period_start: '2026-04-01',
      effective_period_end: '2026-06-30',
      population_size: 412,
      sample_size: 25,
      sample_method: 'haphazard',
      ipe_source_system: 'Okta',
    })
  })

  it('omits untouched fields instead of sending empty strings', async () => {
    // "Not asserted" is a state the column records. An empty string is a
    // different claim — that the preparer looked and had nothing to say.
    const { container } = renderUpload()
    expand()
    fireEvent.change(screen.getByTestId('assertion-population-size'), { target: { value: '412' } })

    dropFile(container)

    await waitFor(() => expect(confirmEvidenceUpload).toHaveBeenCalled())
    const [, body] = vi.mocked(confirmEvidenceUpload).mock.calls[0]
    expect(body).not.toHaveProperty('effective_period_start')
    expect(body).not.toHaveProperty('sample_basis')
    expect(body).not.toHaveProperty('ipe_query_or_filter')
  })

  it('confirms with no assertion fields at all when the form was never opened', async () => {
    // The whole feature is optional. Uploading without touching the panel must
    // behave exactly as it did before this PR existed.
    const { container } = renderUpload()
    dropFile(container)

    await waitFor(() => expect(confirmEvidenceUpload).toHaveBeenCalled())
    const [, body] = vi.mocked(confirmEvidenceUpload).mock.calls[0]
    expect(Object.keys(body as object).sort()).toEqual(['s3_key', 'sha256_hash', 'upload_ticket'])
  })

  it('blocks the upload before the bytes move when the period is half-asserted', async () => {
    const { container } = renderUpload()
    expand()
    fireEvent.change(screen.getByTestId('assertion-period-start'), { target: { value: '2026-04-01' } })

    dropFile(container)

    await waitFor(() => expect(screen.getByText(/Fix the audit assertions first/)).toBeTruthy())
    // Nothing was requested, so nothing was uploaded and nothing was confirmed.
    expect(getEvidenceUploadUrl).not.toHaveBeenCalled()
    expect(confirmEvidenceUpload).not.toHaveBeenCalled()
  })

  it('blocks a sample larger than its own population', async () => {
    const { container } = renderUpload()
    expand()
    fireEvent.change(screen.getByTestId('assertion-population-size'), { target: { value: '10' } })
    fireEvent.change(screen.getByTestId('assertion-sample-size'), { target: { value: '25' } })

    dropFile(container)

    await waitFor(() => expect(screen.getByText(/Fix the audit assertions first/)).toBeTruthy())
    expect(getEvidenceUploadUrl).not.toHaveBeenCalled()
  })

  it('reopens the panel it is complaining about', async () => {
    // The guard can fire while the panel is collapsed — the preparer typed a
    // start date, collapsed the panel, then dropped a file. An error naming
    // fields the reader cannot see is a dead end.
    const { container } = renderUpload()
    expand()
    fireEvent.change(screen.getByTestId('assertion-period-end'), { target: { value: '2026-06-30' } })
    fireEvent.click(screen.getByTestId('preparer-assertions-toggle')) // collapse again
    expect(screen.queryByTestId('assertion-period-end')).toBeNull()

    dropFile(container)

    await waitFor(() => expect(screen.getByTestId('assertion-period-end')).toBeTruthy())
  })
})
