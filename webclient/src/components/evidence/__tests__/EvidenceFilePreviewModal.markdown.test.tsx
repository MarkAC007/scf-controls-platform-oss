// SPDX-License-Identifier: AGPL-3.0-only
/**
 * EvidenceFilePreviewModal — Markdown evidence is shown rendered, not as source.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, cleanup, waitFor, screen } from '@testing-library/react'

import { EvidenceFilePreviewModal } from '../EvidenceFilePreviewModal'
import { makeEvidenceFile } from './evidenceFileFixture'
import { makeEvidenceFileTypes } from './evidenceFileTypesFixture'

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
// #1309: the viewer is chosen from the server's file-type list. Supplied here
// as already loaded so `fetch` below sees only the file download.
vi.mock('../../../hooks/useEvidenceFileTypes', () => ({
  useEvidenceFileTypes: () => ({ fileTypes: makeEvidenceFileTypes(), loading: false }),
}))

const SOURCE = [
  '# Access review Q3',
  '',
  '| User | Role |',
  '|---|---|',
  '| alice | admin |',
  '',
  '- reviewed by **security**',
  '',
  '```',
  'SELECT * FROM users;',
  '```',
  '',
  '<script>window.__pwned = true</script>',
].join('\n')

const fetchMock = vi.fn()

function renderModal(fileOverrides: Parameters<typeof makeEvidenceFile>[0] = {}) {
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
      file={makeEvidenceFile({
        filename: 'access-review.md',
        content_type: 'text/markdown',
        file_size_bytes: 400,
        download_url: '/api/download/file-1',
        ...fileOverrides,
      })}
      orgId="org-1"
      evidenceId="ERL-001"
      onClose={vi.fn()}
      onDownload={vi.fn()}
      onDelete={vi.fn()}
      isDeleting={false}
    />,
  )
}

describe('Markdown preview branch', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    fetchMock.mockResolvedValue({ ok: true, status: 200, text: async () => SOURCE })
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('fetches the file and renders it as a document', async () => {
    renderModal()
    const body = await screen.findByTestId('evidence-preview-markdown')
    expect(fetchMock).toHaveBeenCalledWith('/api/download/file-1')
    expect(body.querySelector('h1')?.textContent).toBe('Access review Q3')
    expect(body.querySelector('table tbody td')?.textContent).toBe('alice')
    expect(body.querySelector('ul li strong')?.textContent).toBe('security')
    expect(body.querySelector('pre code')?.textContent).toBe('SELECT * FROM users;')
  })

  it('does not insert or run script from the file', async () => {
    renderModal()
    const body = await screen.findByTestId('evidence-preview-markdown')
    expect(body.querySelector('script')).toBeNull()
    expect((window as unknown as { __pwned?: boolean }).__pwned).toBeUndefined()
  })

  it('recognises Markdown by extension when the stored type is generic', async () => {
    renderModal({ content_type: 'application/octet-stream', filename: 'notes.markdown' })
    await screen.findByTestId('evidence-preview-markdown')
  })

  it('does not fetch a file over 512 KB and offers Download instead', () => {
    renderModal({ file_size_bytes: 600 * 1024 })
    expect(fetchMock).not.toHaveBeenCalled()
    expect(screen.getByText(/too large to preview inline/)).toBeTruthy()
  })

  it('shows an error instead of rendering an error response body', async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 403, text: async () => '# Forbidden' })
    renderModal()
    expect(await screen.findByText('Failed to load file content')).toBeTruthy()
    await waitFor(() => expect(screen.queryByTestId('evidence-preview-markdown')).toBeNull())
  })
})
