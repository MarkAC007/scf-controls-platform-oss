/**
 * EvidenceFileList — integrity disclosure (#57).
 *
 * The server now hashes and malware-scans every stored evidence object out of
 * band. What a reader needs from the list is the honest state of that check,
 * including the uncomfortable one: a file that has not been scanned yet stays
 * available and keeps counting toward posture, and says so, rather than being
 * quietly withheld for a backlog that is the platform's own debt.
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

describe('EvidenceFileList integrity badge', () => {
  beforeEach(() => vi.clearAllMocks())
  afterEach(() => cleanup())

  it('says nothing when the file is verified and clean', async () => {
    await renderWith(makeFile())
    expect(screen.queryByText('Not Yet Scanned')).toBeNull()
    expect(screen.queryByText('Infected')).toBeNull()
  })

  it('discloses an unscanned file rather than hiding it', async () => {
    await renderWith(makeFile({ integrity_badge: 'not_yet_scanned' }))
    expect(screen.getByText('Not Yet Scanned')).toBeTruthy()
    // Still listed, still linked — the ruling is disclosure, not withholding.
    expect(screen.getByText('report.pdf')).toBeTruthy()
  })

  it('flags a hash mismatch', async () => {
    await renderWith(makeFile({ integrity_badge: 'hash_mismatch' }))
    expect(screen.getByText('Hash Mismatch')).toBeTruthy()
  })

  it('flags an infected file', async () => {
    await renderWith(makeFile({ integrity_badge: 'infected', scan_status: 'infected' }))
    expect(screen.getByText('Infected')).toBeTruthy()
  })

  it('ignores a badge value it does not recognise instead of rendering it raw', async () => {
    await renderWith(makeFile({ integrity_badge: 'some_future_state' }))
    expect(screen.queryByText('some_future_state')).toBeNull()
  })
})
