/**
 * EvidenceFileList — effective-period chip (#786).
 *
 * The list is where a reviewer decides which file to open. A file that carries
 * a preparer-asserted period should say so there, because "which of these six
 * exports covers Q2" is exactly the question the list is being scanned for.
 *
 * The chip is deliberately conservative: it renders only when both ends of the
 * period are present. A half-asserted period is not a window, and printing
 * "covers 1 Apr 2026 – " would read as a claim the preparer never made.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup } from '@testing-library/react'
import { EvidenceFileList } from '../EvidenceFileList'
import type { EvidenceFileResponse } from '../../../data/apiClient'
import { makeEvidenceFile as makeFile } from './evidenceFileFixture'

vi.mock('../../../data/apiClient', () => ({
  listEvidenceFiles: vi.fn(),
  deleteEvidenceFile: vi.fn(),
  reviewEvidenceFile: vi.fn(),
  getAssessment: vi.fn(),
}))

import { listEvidenceFiles, getAssessment } from '../../../data/apiClient'

async function renderWith(file: EvidenceFileResponse) {
  vi.mocked(listEvidenceFiles).mockResolvedValue({ files: [file], total: 1 })
  vi.mocked(getAssessment).mockRejectedValue(new Error('no assessment'))
  render(<EvidenceFileList orgId="org-1" evidenceId="ERL-001" refreshTrigger={0} />)
  await waitFor(() => expect(screen.getByText('report.pdf')).toBeTruthy())
}

describe('EvidenceFileList effective-period chip', () => {
  beforeEach(() => vi.clearAllMocks())
  afterEach(() => cleanup())

  it('shows the asserted period on the row', async () => {
    await renderWith(makeFile({
      effective_period_start: '2026-04-01',
      effective_period_end: '2026-06-30',
    }))
    const chip = screen.getByTestId('evidence-file-effective-period')
    expect(chip.textContent).toContain('1 Apr 2026')
    expect(chip.textContent).toContain('30 Jun 2026')
  })

  it('says what the chip means, for a reader who has never seen one', async () => {
    await renderWith(makeFile({
      effective_period_start: '2026-04-01',
      effective_period_end: '2026-06-30',
    }))
    const chip = screen.getByTestId('evidence-file-effective-period')
    expect(chip.getAttribute('title')).toContain('Preparer asserts')
  })

  it('stays silent when nothing was asserted', async () => {
    await renderWith(makeFile())
    expect(screen.queryByTestId('evidence-file-effective-period')).toBeNull()
  })

  it('stays silent on a half-asserted period rather than printing a dangling range', async () => {
    // Both directions: a start with no end, and an end with no start. The API
    // rejects either at confirm, but a row rendered from older or hand-patched
    // data must not invent the missing half.
    await renderWith(makeFile({ effective_period_start: '2026-04-01' }))
    expect(screen.queryByTestId('evidence-file-effective-period')).toBeNull()
    cleanup()
    await renderWith(makeFile({ effective_period_end: '2026-06-30' }))
    expect(screen.queryByTestId('evidence-file-effective-period')).toBeNull()
  })

  it('brings its own separator, and takes it away again', async () => {
    // The chip owns the interpunct that precedes it. If the two were guarded
    // separately, a file with no asserted period would render a stray "·"
    // dangling after the timestamp.
    await renderWith(makeFile())
    const withoutPeriod = document.querySelectorAll('.evidence-files-separator').length
    cleanup()

    await renderWith(makeFile({
      effective_period_start: '2026-04-01',
      effective_period_end: '2026-06-30',
    }))
    const withPeriod = document.querySelectorAll('.evidence-files-separator').length

    expect(withPeriod).toBe(withoutPeriod + 1)
  })

  it('falls back to the raw value rather than rendering "Invalid Date"', async () => {
    await renderWith(makeFile({
      effective_period_start: 'not-a-date',
      effective_period_end: '2026-06-30',
    }))
    const chip = screen.getByTestId('evidence-file-effective-period')
    expect(chip.textContent).toContain('not-a-date')
    expect(chip.textContent).not.toContain('Invalid Date')
  })
})
