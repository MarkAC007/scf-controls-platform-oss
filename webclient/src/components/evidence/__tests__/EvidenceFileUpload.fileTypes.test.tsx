// SPDX-License-Identifier: AGPL-3.0-only
/**
 * EvidenceFileUpload — what can be uploaded comes from the server (#1309).
 *
 * The control used to carry its own list of accepted types, a second list of
 * friendly names and a third set of extension special cases. All three drifted
 * from the backend allowlist: plain text and log files were refused here while
 * the server would have taken them, and the refusal printed "spreadsheet,
 * spreadsheet, image, image". The list now comes from
 * `GET /api/evidence-file-types`, and when that request has not answered the
 * control steps aside and lets the server decide rather than guessing.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react'

import { EvidenceFileUpload } from '../EvidenceFileUpload'
import { resetEvidenceFileTypesCache } from '../../../hooks/useEvidenceFileTypes'
import { makeEvidenceFileTypes } from './evidenceFileTypesFixture'

vi.mock('../../../data/apiClient', () => ({
  getEvidenceUploadUrl: vi.fn(),
  confirmEvidenceUpload: vi.fn(),
  getEvidenceFileTypes: vi.fn(),
}))

import { getEvidenceUploadUrl, getEvidenceFileTypes } from '../../../data/apiClient'

function fileInput(container: HTMLElement): HTMLInputElement {
  return container.querySelector('input.evidence-upload-hidden-input') as HTMLInputElement
}

function dropFile(container: HTMLElement, name: string, type: string, size?: number) {
  const file = new File(['line one\n'], name, { type })
  Object.defineProperty(file, 'arrayBuffer', { value: async () => new ArrayBuffer(8) })
  if (size !== undefined) Object.defineProperty(file, 'size', { value: size })
  fireEvent.change(fileInput(container), { target: { files: [file] } })
}

async function renderLoaded(props: { acceptedTypes?: string[] } = {}) {
  const view = render(
    <EvidenceFileUpload orgId="org-1" evidenceId="ERL-001" onUploadComplete={vi.fn()} {...props} />,
  )
  // The list has loaded once the picker carries an accept attribute.
  await waitFor(() => expect(fileInput(view.container).getAttribute('accept')).toBeTruthy())
  return view
}

describe('EvidenceFileUpload file types from the server', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    resetEvidenceFileTypesCache()
    vi.stubGlobal('crypto', {
      subtle: { digest: async () => new Uint8Array(32).fill(0xab).buffer },
    })
    vi.mocked(getEvidenceFileTypes).mockResolvedValue(makeEvidenceFileTypes())
    // Stop at the upload-URL request: the content type it carries is the
    // thing under test, not the transport.
    vi.mocked(getEvidenceUploadUrl).mockRejectedValue(new Error('stop here'))
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it.each([
    ['change-log.txt', 'text/plain'],
    ['change-log.txt', ''],
    ['APP.LOG', ''],
    ['server.log', 'application/octet-stream'],
  ])('accepts %s (browser type %j) and sends it as text/plain', async (name, type) => {
    const { container } = await renderLoaded()
    dropFile(container, name, type)

    await waitFor(() => expect(getEvidenceUploadUrl).toHaveBeenCalledTimes(1))
    expect(vi.mocked(getEvidenceUploadUrl).mock.calls[0][1]).toMatchObject({
      filename: name,
      content_type: 'text/plain',
    })
  })

  // The server stores a file under the type its name gives. A name with no
  // listed extension would be stored as an unknown type and then quarantined
  // by the integrity scan, whatever the browser called it.
  it.each([
    ['sshd.conf', 'text/plain'],
    ['photo.jfif', 'image/jpeg'],
    ['README', 'text/plain'],
    ['export', 'text/csv'],
  ])('refuses %s although the browser calls it %s', async (name, type) => {
    const { container } = await renderLoaded()
    dropFile(container, name, type)

    expect(await screen.findByText(/not accepted/i)).toBeInTheDocument()
    expect(getEvidenceUploadUrl).not.toHaveBeenCalled()
  })

  it('builds the picker accept attribute from every listed type and extension', async () => {
    const { container } = await renderLoaded()
    const accept = fileInput(container).getAttribute('accept')!.split(',')
    expect(accept).toEqual(expect.arrayContaining(['text/plain', '.txt', '.log', 'application/xml', '.xml']))
    expect(accept).toEqual(expect.arrayContaining(['message/rfc822', '.eml', 'application/zip', '.zip']))
  })

  it('follows the server list when it changes, rather than a copy of its own', async () => {
    vi.mocked(getEvidenceFileTypes).mockResolvedValue(makeEvidenceFileTypes({
      types: [
        { content_type: 'application/x-made-up', label: 'Made-up', extensions: ['.mup'], preview: 'none', ai_readable: false },
      ],
    }))
    const { container } = await renderLoaded()
    expect(fileInput(container).getAttribute('accept')).toBe('application/x-made-up,.mup')
    expect(screen.getByText(/Accepted: Made-up/)).toBeInTheDocument()
  })

  it('names each allowed type once when it refuses a file', async () => {
    const { container } = await renderLoaded()
    dropFile(container, 'tool.exe', 'application/x-msdownload')

    const message = await screen.findByText(/not accepted/i)
    expect(getEvidenceUploadUrl).not.toHaveBeenCalled()
    const allowed = message.textContent!.replace(/^.*Allowed: /, '').split(', ')
    expect(allowed).toContain('Plain text')
    expect(allowed).toContain('Excel workbook')
    expect(new Set(allowed).size).toBe(allowed.length)
    expect(allowed).toHaveLength(makeEvidenceFileTypes().types.length)
  })

  it('narrows the server list to the types a caller passes', async () => {
    const { container } = await renderLoaded({ acceptedTypes: ['application/pdf', 'text/csv'] })
    expect(fileInput(container).getAttribute('accept')).toBe('application/pdf,text/csv,.pdf,.csv')

    dropFile(container, 'notes.txt', 'text/plain')
    const message = await screen.findByText(/not accepted/i)
    expect(message.textContent).toMatch(/Allowed: PDF, CSV$/)
    expect(getEvidenceUploadUrl).not.toHaveBeenCalled()
  })

  it('refuses a file over the size the server states', async () => {
    vi.mocked(getEvidenceFileTypes).mockResolvedValue(
      makeEvidenceFileTypes({ max_file_size_bytes: 10 * 1024 * 1024 }),
    )
    const { container } = await renderLoaded()
    expect(screen.getByText(/Max 10 MB/)).toBeInTheDocument()

    dropFile(container, 'big.pdf', 'application/pdf', 11 * 1024 * 1024)
    expect(await screen.findByText(/exceeds maximum size of 10 MB/)).toBeInTheDocument()
    expect(getEvidenceUploadUrl).not.toHaveBeenCalled()
  })

  it('waits for the list when a file is dropped before it has arrived', async () => {
    let deliver: (value: ReturnType<typeof makeEvidenceFileTypes>) => void = () => {}
    vi.mocked(getEvidenceFileTypes).mockReturnValue(
      new Promise(resolve => { deliver = resolve }),
    )
    const { container } = render(
      <EvidenceFileUpload orgId="org-1" evidenceId="ERL-001" onUploadComplete={vi.fn()} />,
    )
    // The browser gives a .log no type: sent now, it would go up as octet-stream.
    dropFile(container, 'server.log', '')
    await Promise.resolve()
    expect(getEvidenceUploadUrl).not.toHaveBeenCalled()

    deliver(makeEvidenceFileTypes())
    await waitFor(() => expect(getEvidenceUploadUrl).toHaveBeenCalledTimes(1))
    expect(vi.mocked(getEvidenceUploadUrl).mock.calls[0][1]).toMatchObject({
      filename: 'server.log',
      content_type: 'text/plain',
    })
  })

  describe('when the list cannot be loaded', () => {
    beforeEach(() => {
      vi.mocked(getEvidenceFileTypes).mockRejectedValue(new Error('API Error: 500'))
    })

    async function renderFailed() {
      const view = render(
        <EvidenceFileUpload orgId="org-1" evidenceId="ERL-001" onUploadComplete={vi.fn()} />,
      )
      await waitFor(() => expect(getEvidenceFileTypes).toHaveBeenCalled())
      return view
    }

    it('does not refuse the file itself, and sends the browser type', async () => {
      const { container } = await renderFailed()
      dropFile(container, 'tool.exe', 'application/x-msdownload')

      await waitFor(() => expect(getEvidenceUploadUrl).toHaveBeenCalledTimes(1))
      expect(vi.mocked(getEvidenceUploadUrl).mock.calls[0][1]).toMatchObject({
        content_type: 'application/x-msdownload',
      })
      expect(screen.queryByText(/not accepted/i)).toBeNull()
    })

    it.each(['config.yaml', 'notes.md', 'server.log', 'mystery'])(
      'holds back %s, which the browser gives no type for, and says why',
      async (name) => {
        const { container } = await renderFailed()
        dropFile(container, name, '')

        expect(await screen.findByText(/list of accepted file types could not be loaded/i)).toBeInTheDocument()
        expect(getEvidenceUploadUrl).not.toHaveBeenCalled()
      },
    )

    it('omits the accept attribute and any list of formats', async () => {
      const { container } = await renderFailed()
      expect(fileInput(container).hasAttribute('accept')).toBe(false)
      expect(screen.queryByText(/Accepted:/)).toBeNull()
      expect(screen.getByText(/Max 50 MB/)).toBeInTheDocument()
    })

    it("shows the server's refusal when it turns the file down", async () => {
      vi.mocked(getEvidenceUploadUrl).mockRejectedValue(
        new Error("Content type 'application/x-msdownload' not allowed"),
      )
      const { container } = await renderFailed()
      dropFile(container, 'tool.exe', 'application/x-msdownload')

      expect(
        await screen.findByText("Content type 'application/x-msdownload' not allowed"),
      ).toBeInTheDocument()
    })
  })
})
