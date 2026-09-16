/**
 * EvidenceFilePreviewModal — the PDF branch must render an iframe Chrome will
 * actually put a PDF in.
 *
 * Chrome's built-in PDF viewer is a plugin, and Chrome refuses to load plugins
 * inside a sandboxed iframe. No sandbox token re-enables it. The modal shipped
 * with `sandbox="allow-same-origin allow-scripts allow-popups"` on the PDF
 * iframe, which rendered the grey "plugin blocked" placeholder for every PDF
 * while the console stayed silent and no request was made. The attribute gave
 * no isolation either: a same-origin frame with scripts can remove its own
 * sandbox. These tests pin the fix so it cannot quietly return.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, cleanup } from '@testing-library/react'

import { EvidenceFilePreviewModal } from '../EvidenceFilePreviewModal'
import { makeEvidenceFile } from './evidenceFileFixture'

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
      file={makeEvidenceFile(fileOverrides)}
      orgId="org-1"
      evidenceId="ERL-001"
      onClose={vi.fn()}
      onDownload={vi.fn()}
      onDelete={vi.fn()}
      isDeleting={false}
    />,
  )
}

describe('PDF preview branch', () => {
  beforeEach(() => vi.clearAllMocks())
  afterEach(() => cleanup())

  it('renders the PDF in an iframe pointed at the download URL', () => {
    const { container } = renderModal({
      content_type: 'application/pdf',
      filename: 'policy.pdf',
      download_url: '/api/organizations/org-1/evidence/ERL-001/files/file-1/download?token=t',
    })

    const iframe = container.querySelector('iframe.evidence-preview-iframe')
    expect(iframe).not.toBeNull()
    expect(iframe?.getAttribute('src')).toBe(
      '/api/organizations/org-1/evidence/ERL-001/files/file-1/download?token=t',
    )
    expect(iframe?.getAttribute('title')).toBe('policy.pdf')
  })

  it('does not sandbox the PDF iframe — Chrome will not load its PDF viewer in one', () => {
    const { container } = renderModal({ content_type: 'application/pdf' })

    const iframe = container.querySelector('iframe.evidence-preview-iframe')
    expect(iframe).not.toBeNull()
    expect(iframe?.hasAttribute('sandbox')).toBe(false)
  })

  it('keeps the new-tab fallback link next to the iframe', () => {
    const { container } = renderModal({
      content_type: 'application/pdf',
      download_url: '/download',
    })

    const fallback = container.querySelector('a.evidence-preview-pdf-fallback')
    expect(fallback).not.toBeNull()
    expect(fallback?.getAttribute('href')).toBe('/download')
    expect(fallback?.getAttribute('target')).toBe('_blank')
  })

  it('does not use the iframe branch for images', () => {
    const { container } = renderModal({
      content_type: 'image/png',
      filename: 'screenshot.png',
    })

    expect(container.querySelector('iframe')).toBeNull()
    expect(container.querySelector('img.evidence-preview-image')).not.toBeNull()
  })
})
