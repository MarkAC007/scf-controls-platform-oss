/**
 * The upload panel says what an untracked upload will not reach (#789).
 *
 * Filed as "upload is gated behind tracking". It is not gated — see the
 * component's own header for why the truth is worse — so what is pinned here is
 * that the consequence is stated, that the one click removing it is offered, and
 * that the notice does not become a gate.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'
import { UntrackedUploadNotice } from '../UntrackedUploadNotice'

afterEach(cleanup)

describe('UntrackedUploadNotice', () => {
  it('names each surface the upload will not reach', () => {
    render(<UntrackedUploadNotice onStartTracking={() => {}} />)
    const text = screen.getByRole('status').textContent ?? ''
    for (const surface of [/evidence health/i, /freshness/i, /collection tasks/i, /L0/]) {
      expect(text).toMatch(surface)
    }
  })

  it('offers one click that starts tracking', () => {
    const onStartTracking = vi.fn()
    render(<UntrackedUploadNotice onStartTracking={onStartTracking} />)
    fireEvent.click(screen.getByRole('button', { name: /start tracking/i }))
    expect(onStartTracking).toHaveBeenCalledTimes(1)
  })

  it('announces itself without stealing focus', () => {
    // `role="status"` is polite: a screen reader hears it at the next pause
    // rather than being interrupted mid-sentence. The notice is context for a
    // decision, not an error about one.
    render(<UntrackedUploadNotice onStartTracking={() => {}} />)
    expect(screen.getByRole('status')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})

describe('the notice is not a gate', () => {
  // Phase 4 (Task 3): upload controls live in EvidenceDetailPage, not EvidenceReview.
  const sources = import.meta.glob('../*.tsx', {
    query: '?raw',
    import: 'default',
    eager: true,
  }) as Record<string, string>

  function evidenceDetailPage(): string {
    const key = Object.keys(sources).find(k => k.endsWith('EvidenceDetailPage.tsx'))
    if (!key) {
      throw new Error(
        `EvidenceDetailPage.tsx not loaded — the glob matched ${Object.keys(sources).length} files`,
      )
    }
    return sources[key]
  }

  it('loaded the fixture it is asserting on', () => {
    // A glob matching nothing would make the case below pass vacuously.
    expect(evidenceDetailPage()).toContain('EvidenceFileUpload')
  })

  it('renders the upload control regardless of tracking state', () => {
    // Refusing the upload would be a second wrong answer: capturing evidence
    // before deciding how it will be collected is a legitimate order to work in.
    // Since Phase 4 Task 3, upload controls live in EvidenceDetailPage.
    const text = evidenceDetailPage()
    const uploadBlock = text.slice(text.indexOf('<UntrackedUploadNotice'))
    expect(uploadBlock).toMatch(/<EvidenceFileUpload/)
    expect(uploadBlock.slice(0, uploadBlock.indexOf('<EvidenceFileUpload'))).not.toMatch(
      /isTracked \?/,
    )
  })
})
