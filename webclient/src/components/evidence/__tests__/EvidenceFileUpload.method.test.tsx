/**
 * EvidenceFileUpload — the upload verb comes from the backend, not from a sniff.
 *
 * The component used to decide between a presigned POST and a raw PUT by asking
 * whether `fields` was empty — a stand-in for "this is Azure". That is only
 * ever right by coincidence: a presigned POST with no *extra* form fields is a
 * legal reply from an S3-compatible store, and it would have been uploaded with
 * the wrong verb, silently. The presign response now carries `method` and
 * `provider` (ISA phase 5, ISC 43/44), and these tests pin all three cases:
 * an empty-`fields` POST uploads as a POST, a PUT uploads as a PUT, and an
 * unrecognised verb is refused rather than guessed.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react'

import { EvidenceFileUpload } from '../EvidenceFileUpload'

vi.mock('../../../data/apiClient', () => ({
  getEvidenceUploadUrl: vi.fn(),
  confirmEvidenceUpload: vi.fn(),
}))

import { getEvidenceUploadUrl, confirmEvidenceUpload } from '../../../data/apiClient'

/** Records what the transport was actually asked to do. */
const opened: Array<{ method: string; url: string }> = []
const headers: Array<[string, string]> = []
const sent: unknown[] = []

class FakeXhr {
  status = 204
  upload = { addEventListener: vi.fn() }
  private handlers: Record<string, Array<() => void>> = {}
  addEventListener(event: string, fn: () => void) {
    (this.handlers[event] ||= []).push(fn)
  }
  open = vi.fn((method: string, url: string) => {
    opened.push({ method, url })
  })
  setRequestHeader = vi.fn((name: string, value: string) => {
    headers.push([name, value])
  })
  abort = vi.fn()
  send = vi.fn((body: unknown) => {
    sent.push(body)
    setTimeout(() => (this.handlers['load'] || []).forEach((fn) => fn()), 0)
  })
}

function stubTransport() {
  vi.stubGlobal('XMLHttpRequest', FakeXhr as unknown as typeof XMLHttpRequest)
  vi.stubGlobal('crypto', {
    subtle: { digest: async () => new Uint8Array(32).fill(0xab).buffer },
  })
}

function dropFile(container: HTMLElement) {
  const input = container.querySelector('input[type="file"]') as HTMLInputElement
  const file = new File(['a,b\n1,2\n'], 'access-review.csv', { type: 'text/csv' })
  Object.defineProperty(file, 'arrayBuffer', { value: async () => new ArrayBuffer(8) })
  fireEvent.change(input, { target: { files: [file] } })
}

function renderUpload() {
  return render(
    <EvidenceFileUpload orgId="org-1" evidenceId="ERL-001" onUploadComplete={vi.fn()} />
  )
}

describe('EvidenceFileUpload upload method', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    opened.length = 0
    headers.length = 0
    sent.length = 0
    stubTransport()
    vi.mocked(confirmEvidenceUpload).mockResolvedValue({ id: 'file-1' } as never)
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('uploads with POST when the backend says POST, even with no form fields', async () => {
    // The exact case the old sniff got wrong: a presigned POST that needed no
    // extra fields would have been sent as a PUT with an Azure header on it.
    vi.mocked(getEvidenceUploadUrl).mockResolvedValue({
      method: 'POST',
      provider: 'minio',
      url: 'https://objects.example.com/evidence',
      fields: {},
      s3_key: 'org-1/ERL-001/access-review.csv',
      upload_ticket: 'ticket-abc',
      expires_in: 900,
    })

    const { container } = renderUpload()
    dropFile(container)

    await waitFor(() => expect(opened).toHaveLength(1))
    expect(opened[0].method).toBe('POST')
    expect(sent[0]).toBeInstanceOf(FormData)
    expect(headers.map(([name]) => name)).not.toContain('x-ms-blob-type')
    await waitFor(() => expect(confirmEvidenceUpload).toHaveBeenCalled())
  })

  it('sends the signed fields with a presigned POST that has them', async () => {
    vi.mocked(getEvidenceUploadUrl).mockResolvedValue({
      method: 'POST',
      provider: 'aws_s3',
      url: 'https://s3.eu-west-1.amazonaws.com/evidence',
      fields: { key: 'org-1/ERL-001/access-review.csv', policy: 'signed-policy' },
      s3_key: 'org-1/ERL-001/access-review.csv',
      upload_ticket: 'ticket-abc',
      expires_in: 900,
    })

    const { container } = renderUpload()
    dropFile(container)

    await waitFor(() => expect(sent).toHaveLength(1))
    const body = sent[0] as FormData
    expect(body.get('policy')).toBe('signed-policy')
    expect(body.get('file')).toBeInstanceOf(File)
  })

  it('uploads with PUT when the backend says PUT, and keeps the blob header to Azure', async () => {
    vi.mocked(getEvidenceUploadUrl).mockResolvedValue({
      method: 'PUT',
      provider: 'azure_blob',
      url: 'https://acct.blob.core.windows.net/evidence/key?sig=x',
      fields: {},
      s3_key: 'org-1/ERL-001/access-review.csv',
      upload_ticket: 'ticket-abc',
      expires_in: 900,
    })

    const { container } = renderUpload()
    dropFile(container)

    await waitFor(() => expect(opened).toHaveLength(1))
    expect(opened[0].method).toBe('PUT')
    expect(headers).toContainEqual(['x-ms-blob-type', 'BlockBlob'])
    expect(sent[0]).toBeInstanceOf(File)
  })

  it('does not send the Azure header to a non-Azure store that signs a PUT', async () => {
    vi.mocked(getEvidenceUploadUrl).mockResolvedValue({
      method: 'PUT',
      provider: 's3_compatible',
      url: 'https://objects.example.com/evidence/key?X-Amz-Signature=x',
      fields: {},
      s3_key: 'org-1/ERL-001/access-review.csv',
      upload_ticket: 'ticket-abc',
      expires_in: 900,
    })

    const { container } = renderUpload()
    dropFile(container)

    await waitFor(() => expect(opened).toHaveLength(1))
    expect(opened[0].method).toBe('PUT')
    expect(headers.map(([name]) => name)).not.toContain('x-ms-blob-type')
  })

  it('refuses an unknown method instead of guessing one', async () => {
    vi.mocked(getEvidenceUploadUrl).mockResolvedValue({
      // A backend newer than this client. Guessing here is how the old sniff
      // would have failed silently; refusing is the point of the field.
      method: 'PATCH',
      provider: 'future_store',
      url: 'https://objects.example.com/evidence',
      fields: {},
      s3_key: 'org-1/ERL-001/access-review.csv',
      upload_ticket: 'ticket-abc',
      expires_in: 900,
    } as never)

    const { container } = renderUpload()
    dropFile(container)

    expect(
      await screen.findByText(/upload method this app does not support/i)
    ).toBeInTheDocument()
    expect(opened).toHaveLength(0)
    expect(confirmEvidenceUpload).not.toHaveBeenCalled()
  })

  it('refuses a missing method rather than falling back to a verb', async () => {
    vi.mocked(getEvidenceUploadUrl).mockResolvedValue({
      provider: 'minio',
      url: 'https://objects.example.com/evidence',
      fields: {},
      s3_key: 'org-1/ERL-001/access-review.csv',
      upload_ticket: 'ticket-abc',
      expires_in: 900,
    } as never)

    const { container } = renderUpload()
    dropFile(container)

    expect(
      await screen.findByText(/upload method this app does not support/i)
    ).toBeInTheDocument()
    expect(opened).toHaveLength(0)
  })
})
