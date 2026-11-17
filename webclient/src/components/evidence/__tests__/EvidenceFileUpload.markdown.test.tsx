// SPDX-License-Identifier: AGPL-3.0-only
/**
 * EvidenceFileUpload — Markdown is an accepted evidence type.
 *
 * Browsers disagree about what a `.md` file is: Chrome and Safari usually send
 * an empty type, some platforms send `text/x-markdown`, a few `text/markdown`.
 * The backend allowlist knows one name, so every spelling must arrive at the
 * upload-URL request as `text/markdown` or the upload is refused.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, waitFor, cleanup, fireEvent } from '@testing-library/react'

import { EvidenceFileUpload } from '../EvidenceFileUpload'

vi.mock('../../../data/apiClient', () => ({
  getEvidenceUploadUrl: vi.fn(),
  confirmEvidenceUpload: vi.fn(),
}))

import { getEvidenceUploadUrl } from '../../../data/apiClient'

function dropFile(container: HTMLElement, name: string, type: string) {
  const input = container.querySelector('input[type="file"]') as HTMLInputElement
  const file = new File(['# Runbook\n'], name, { type })
  Object.defineProperty(file, 'arrayBuffer', { value: async () => new ArrayBuffer(8) })
  fireEvent.change(input, { target: { files: [file] } })
}

function renderUpload() {
  return render(
    <EvidenceFileUpload orgId="org-1" evidenceId="ERL-001" onUploadComplete={vi.fn()} />,
  )
}

describe('EvidenceFileUpload Markdown support', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubGlobal('crypto', {
      subtle: { digest: async () => new Uint8Array(32).fill(0xab).buffer },
    })
    // Stop after the upload-URL request: the content type it carries is the
    // thing under test, not the transport.
    vi.mocked(getEvidenceUploadUrl).mockRejectedValue(new Error('stop here'))
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('offers .md and .markdown in the file picker', () => {
    const { container } = renderUpload()
    const accept = container.querySelector('input[type="file"]')?.getAttribute('accept') ?? ''
    expect(accept.split(',')).toEqual(expect.arrayContaining(['text/markdown', '.md', '.markdown']))
  })

  it.each([
    ['runbook.md', ''],
    ['runbook.md', 'text/x-markdown'],
    ['runbook.md', 'text/markdown'],
    ['RUNBOOK.MARKDOWN', ''],
  ])('requests an upload URL for %s (browser type %j) as text/markdown', async (name, type) => {
    const { container } = renderUpload()
    dropFile(container, name, type)

    await waitFor(() => expect(getEvidenceUploadUrl).toHaveBeenCalledTimes(1))
    expect(vi.mocked(getEvidenceUploadUrl).mock.calls[0][1]).toMatchObject({
      filename: name,
      content_type: 'text/markdown',
    })
  })

  it('names Markdown among the allowed types when a file is refused', async () => {
    const { container, findByText } = renderUpload()
    dropFile(container, 'tool.exe', 'application/x-msdownload')
    expect(await findByText(/Markdown/)).toBeTruthy()
    expect(getEvidenceUploadUrl).not.toHaveBeenCalled()
  })
})
