// SPDX-License-Identifier: AGPL-3.0-only
/**
 * EvidenceFilePreviewModal — every type the platform accepts can be looked at
 * (#1309).
 *
 * Which viewer a file gets is the server's `preview` value for its type, so a
 * type added to the backend list arrives here without a second edit. Text-like
 * files are fetched and shown verbatim; CSV becomes a table; Office files,
 * archives and email are read by the server and drawn from its structured
 * reply. Everything in a file, and everything the server read out of one, is
 * untrusted, so all of it is rendered as text and none of it as markup.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, cleanup, screen, waitFor } from '@testing-library/react'

import { EvidenceFilePreviewModal } from '../EvidenceFilePreviewModal'
import { makeEvidenceFile } from './evidenceFileFixture'
import { makeEvidenceFileTypes } from './evidenceFileTypesFixture'
import type { EvidenceFileStructuredPreview, EvidenceFileTypesResponse } from '../../../data/apiClient'

const mockUseAssessmentPolling = vi.fn()
vi.mock('../../../hooks/useAssessmentPolling', () => ({
  useAssessmentPolling: (...args: unknown[]) => mockUseAssessmentPolling(...args),
}))
vi.mock('../PreparerAssertionPanel', () => ({
  PreparerAssertionPanel: () => <div data-testid="preparer-assertion-panel" />,
}))
vi.mock('../AssessmentReviewPanel', () => ({
  AssessmentReviewPanel: () => <div data-testid="assessment-review-panel" />,
}))
vi.mock('../ShadowVerdictPanel', () => ({
  ShadowVerdictPanel: () => <div data-testid="shadow-verdict-panel-stub" />,
}))

let fileTypesState: { fileTypes: EvidenceFileTypesResponse | null; loading: boolean }
vi.mock('../../../hooks/useEvidenceFileTypes', () => ({
  useEvidenceFileTypes: () => fileTypesState,
}))

vi.mock('../../../data/apiClient', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../data/apiClient')>()),
  getEvidenceFilePreview: vi.fn(),
}))
import { getEvidenceFilePreview } from '../../../data/apiClient'

const fetchMock = vi.fn()

function serveBody(body: string) {
  fetchMock.mockResolvedValue({ ok: true, status: 200, text: async () => body })
}

const DOCX = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
const XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
const PPTX = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'

function renderModal(fileOverrides: Parameters<typeof makeEvidenceFile>[0]) {
  mockUseAssessmentPolling.mockReturnValue({
    assessment: null,
    loading: false,
    triggering: false,
    trigger: vi.fn(),
    requestError: null,
    retry: vi.fn(),
  })
  return render(
    <EvidenceFilePreviewModal
      file={makeEvidenceFile({ file_size_bytes: 400, download_url: '/api/download/file-1', ...fileOverrides })}
      orgId="org-1"
      evidenceId="ERL-001"
      onClose={vi.fn()}
      onDownload={vi.fn()}
      onDelete={vi.fn()}
      isDeleting={false}
    />,
  )
}

function dialog(): HTMLElement {
  return screen.getByRole('dialog')
}

function structured(preview: Partial<EvidenceFileStructuredPreview> & { kind: EvidenceFileStructuredPreview['kind'] }) {
  vi.mocked(getEvidenceFilePreview).mockResolvedValue({ truncated: false, note: null, ...preview })
}

describe('Preview by server file type', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    fileTypesState = { fileTypes: makeEvidenceFileTypes(), loading: false }
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  describe('code', () => {
    it('shows a plain text file verbatim in the code pane', async () => {
      const body = '2026-11-21 10:00 login ok\n\t2026-11-21 10:01   <admin> & "quoted"\n'
      serveBody(body)
      const { container } = renderModal({ filename: 'auth.log', content_type: 'text/plain' })

      await waitFor(() => expect(container.querySelector('pre.evidence-preview-code')).not.toBeNull())
      const pre = container.querySelector('pre.evidence-preview-code')!
      expect(fetchMock).toHaveBeenCalledWith('/api/download/file-1')
      expect(pre.textContent!.trim()).toBe(body.trim())
    })

    // The size on the record is 0 until the integrity task has measured the
    // file. In that window the only honest size is that of what arrived.
    it('does not draw a large file whose recorded size is still zero', async () => {
      serveBody('line of a very large log\n'.repeat(30_000))
      const { container } = renderModal({ filename: 'big.log', content_type: 'text/plain', file_size_bytes: 0 })

      expect(await screen.findByText(/too large to preview inline/i)).toBeInTheDocument()
      expect(container.querySelector('pre.evidence-preview-code')).toBeNull()
    })

    it('shows XML as text and never as markup', async () => {
      const body = '<?xml version="1.0"?>\n<root><script>alert(1)</script><b>bold</b></root>'
      serveBody(body)
      const { container } = renderModal({ filename: 'config.xml', content_type: 'application/xml' })

      await waitFor(() => expect(container.querySelector('pre.evidence-preview-code')).not.toBeNull())
      const pre = container.querySelector('pre.evidence-preview-code')!
      expect(pre.textContent).toBe(body)
      expect(dialog().querySelector('script')).toBeNull()
      expect(dialog().querySelector('b')).toBeNull()
      expect(screen.getByText(/<script>alert\(1\)<\/script>/)).toBeInTheDocument()
    })

    it('goes by the stored type over the name: text/plain named .md is shown verbatim', async () => {
      const body = '# not a heading\n<b>x</b>'
      serveBody(body)
      const { container } = renderModal({ filename: 'notes.md', content_type: 'text/plain' })
      await waitFor(() => expect(container.querySelector('pre.evidence-preview-code')).not.toBeNull())
      expect(container.querySelector('pre.evidence-preview-code')!.textContent).toBe(body)
      expect(screen.queryByTestId('evidence-preview-markdown')).toBeNull()
    })

    it('still does not fetch a text file over 512 KB', () => {
      renderModal({ filename: 'huge.log', content_type: 'text/plain', file_size_bytes: 600 * 1024 })
      expect(fetchMock).not.toHaveBeenCalled()
      expect(screen.getByText(/too large to preview inline/)).toBeInTheDocument()
    })
  })

  describe('csv', () => {
    it('draws a table with the first row as header cells', async () => {
      serveBody('user,role\r\n"Smith, Jane",admin\r\nbob,"read, write"\r\n')
      const { container } = renderModal({ filename: 'access.csv', content_type: 'text/csv' })

      const table = await waitFor(() => {
        const t = container.querySelector('table.evidence-preview-table')
        expect(t).not.toBeNull()
        return t!
      })
      expect(Array.from(table.querySelectorAll('th')).map(th => th.textContent)).toEqual(['user', 'role'])
      const rows = Array.from(table.querySelectorAll('tbody tr')).map(tr =>
        Array.from(tr.querySelectorAll('td')).map(td => td.textContent),
      )
      expect(rows).toEqual([['Smith, Jane', 'admin'], ['bob', 'read, write']])
      expect(dialog().querySelector('pre')).toBeNull()
    })

    it('draws at most 500 data rows and says how many there are', async () => {
      const lines = ['id,name']
      for (let i = 1; i <= 12340; i++) lines.push(`${i},user${i}`)
      serveBody(lines.join('\n'))
      const { container } = renderModal({ filename: 'users.csv', content_type: 'text/csv' })

      expect(await screen.findByText('Showing the first 500 of 12,340 rows.')).toBeInTheDocument()
      expect(container.querySelectorAll('table.evidence-preview-table tbody tr')).toHaveLength(500)
    })

    it('draws at most 50 columns and says so', async () => {
      const header = Array.from({ length: 60 }, (_, i) => `c${i}`).join(',')
      serveBody(`${header}\n${Array.from({ length: 60 }, () => 'x').join(',')}`)
      const { container } = renderModal({ filename: 'wide.csv', content_type: 'text/csv' })

      expect(await screen.findByText('Showing the first 50 of 60 columns.')).toBeInTheDocument()
      expect(container.querySelectorAll('table.evidence-preview-table th')).toHaveLength(50)
    })

    it('falls back to the text when there is nothing to tabulate', async () => {
      serveBody('\n\n')
      const { container } = renderModal({ filename: 'empty.csv', content_type: 'text/csv' })
      await waitFor(() => expect(container.querySelector('pre.evidence-preview-code')).not.toBeNull())
      expect(container.querySelector('table')).toBeNull()
    })
  })

  describe('structured', () => {
    it('asks the server for the preview of this file', async () => {
      structured({ kind: 'document', paragraphs: ['One'] })
      renderModal({ id: 'file-9', filename: 'policy.docx', content_type: DOCX })
      await screen.findByText('One')
      expect(getEvidenceFilePreview).toHaveBeenCalledWith('org-1', 'ERL-001', 'file-9')
    })

    it('renders Word paragraphs as paragraphs, with no pre', async () => {
      structured({ kind: 'document', paragraphs: ['Access Control Policy', 'All access is <b>reviewed</b> quarterly.'] })
      const { container } = renderModal({ filename: 'policy.docx', content_type: DOCX })

      await screen.findByText('Access Control Policy')
      const paragraphs = container.querySelectorAll('div.evidence-preview-document p')
      expect(Array.from(paragraphs).map(p => p.textContent)).toEqual([
        'Access Control Policy',
        'All access is <b>reviewed</b> quarterly.',
      ])
      expect(dialog().querySelector('pre')).toBeNull()
      expect(dialog().querySelector('b')).toBeNull()
    })

    it('renders Excel sheets as tables under the sheet name', async () => {
      structured({
        kind: 'spreadsheet',
        sheets: [
          { name: 'Users', rows: [['user', 'role'], ['alice', 'admin']], truncated: false },
          { name: 'Groups', rows: [['ops']], truncated: true },
        ],
      })
      const { container } = renderModal({ filename: 'review.xlsx', content_type: XLSX })

      expect(await screen.findByRole('heading', { name: 'Users' })).toBeInTheDocument()
      expect(screen.getByRole('heading', { name: 'Groups' })).toBeInTheDocument()
      const tables = container.querySelectorAll('table.evidence-preview-table')
      expect(tables).toHaveLength(2)
      // The first row is not assumed to be a header.
      expect(tables[0].querySelectorAll('th')).toHaveLength(0)
      expect(Array.from(tables[0].querySelectorAll('td')).map(td => td.textContent)).toEqual([
        'user', 'role', 'alice', 'admin',
      ])
      expect(screen.getByText(/Only the first rows of this sheet are shown/)).toBeInTheDocument()
      expect(dialog().querySelector('pre')).toBeNull()
    })

    it('renders PowerPoint slides with their text', async () => {
      structured({
        kind: 'slides',
        slides: [
          { number: 1, paragraphs: ['Security awareness', 'Q3 2026'] },
          { number: 2, paragraphs: ['Phishing results'] },
        ],
      })
      renderModal({ filename: 'deck.pptx', content_type: PPTX })

      expect(await screen.findByRole('heading', { name: 'Slide 1' })).toBeInTheDocument()
      expect(screen.getByRole('heading', { name: 'Slide 2' })).toBeInTheDocument()
      expect(screen.getByText('Security awareness')).toBeInTheDocument()
      expect(screen.getByText('Phishing results')).toBeInTheDocument()
      expect(dialog().querySelector('pre')).toBeNull()
    })

    it('lists the members of a ZIP archive', async () => {
      structured({
        kind: 'archive',
        members: [
          { name: 'logs/app.log', size_bytes: 1234, label: 'Plain text', readable: true, note: null },
          { name: 'inner.zip', size_bytes: 2048, label: null, readable: false, note: 'archive inside an archive, not opened' },
        ],
      })
      const { container } = renderModal({ filename: 'bundle.zip', content_type: 'application/zip' })

      await screen.findByText('logs/app.log')
      const items = container.querySelectorAll('ul.evidence-preview-archive > li')
      expect(items).toHaveLength(2)
      expect(items[0].textContent).toContain('1.2 KB')
      expect(items[0].textContent).toContain('Plain text')
      expect(items[1].textContent).toContain('inner.zip')
      expect(items[1].textContent).toContain('not read')
      expect(items[1].textContent).toContain('archive inside an archive, not opened')
    })

    it('shows an email with its headers, body and attachments', async () => {
      structured({
        kind: 'email',
        headers: { from: 'it@example.com', to: 'audit@example.com', cc: '', subject: 'Quarterly access review', date: 'Fri, 21 Nov 2026 10:00:00 +0000' },
        body: 'Hello,\n\nThe review is attached.\n<script>alert(1)</script>',
        attachments: [{ name: 'review.pdf', content_type: 'application/pdf', size_bytes: 100 }],
      })
      const { container } = renderModal({ filename: 'review.eml', content_type: 'message/rfc822' })

      expect(await screen.findByText('Quarterly access review')).toBeInTheDocument()
      expect(screen.getByText('it@example.com')).toBeInTheDocument()
      expect(screen.queryByText('Cc')).toBeNull()
      const body = container.querySelector('.evidence-preview-email-body')!
      expect(body.textContent).toBe('Hello,\n\nThe review is attached.\n<script>alert(1)</script>')
      expect(screen.getByText('review.pdf')).toBeInTheDocument()
      expect(screen.getByText(/Attachments are listed, not opened/)).toBeInTheDocument()
      expect(dialog().querySelector('pre')).toBeNull()
      expect(dialog().querySelector('script')).toBeNull()
    })

    it("shows the server's note and says when the preview is cut short", async () => {
      structured({ kind: 'document', paragraphs: ['Para'], note: 'Tracked changes are not shown.', truncated: true })
      renderModal({ filename: 'policy.docx', content_type: DOCX })

      expect(await screen.findByText('Tracked changes are not shown.')).toBeInTheDocument()
      expect(screen.getByText(/Only the first part of this file is shown/)).toBeInTheDocument()
    })

    it('shows the server refusal inside the not-available card', async () => {
      vi.mocked(getEvidenceFilePreview).mockRejectedValue(new Error('This file is too large to preview.'))
      renderModal({ filename: 'huge.xlsx', content_type: XLSX })

      expect(await screen.findByText('This file is too large to preview.')).toBeInTheDocument()
      expect(screen.getByText(/Preview not available/)).toBeInTheDocument()
      expect(screen.getByText('huge.xlsx', { selector: '.evidence-preview-meta-value' })).toBeInTheDocument()
    })
  })

  describe('before the list has loaded', () => {
    it('keeps the existing JSON viewer working', async () => {
      fileTypesState = { fileTypes: null, loading: false }
      serveBody('{"a":1}')
      const { container } = renderModal({ filename: 'export.json', content_type: 'application/json' })
      await waitFor(() => expect(container.querySelector('pre.evidence-preview-code')).not.toBeNull())
      expect(container.querySelector('pre.evidence-preview-code')!.textContent).toContain('"a": 1')
    })

    it('shows the not-available card for a type it cannot place', () => {
      fileTypesState = { fileTypes: null, loading: false }
      renderModal({ filename: 'policy.docx', content_type: DOCX })
      expect(screen.getByText('Preview not available for this file type')).toBeInTheDocument()
      expect(getEvidenceFilePreview).not.toHaveBeenCalled()
    })

    it('says it is loading rather than not available while the list is on its way', () => {
      fileTypesState = { fileTypes: null, loading: true }
      const { container } = renderModal({ filename: 'policy.docx', content_type: DOCX })
      expect(container.querySelector('.evidence-preview-code-loading')).not.toBeNull()
      expect(screen.queryByText('Preview not available for this file type')).toBeNull()
    })

    it("uses a type marked 'none' as not available", () => {
      fileTypesState = {
        fileTypes: makeEvidenceFileTypes({
          types: [{ content_type: 'application/x-thing', label: 'Thing', extensions: ['.thing'], preview: 'none', ai_readable: false }],
        }),
        loading: false,
      }
      renderModal({ filename: 'a.thing', content_type: 'application/x-thing' })
      expect(screen.getByText('Preview not available for this file type')).toBeInTheDocument()
    })
  })
})
