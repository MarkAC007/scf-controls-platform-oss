/**
 * EvidenceFileList — review gating, the unassessable state, and bulk assess (#881).
 *
 * Three things are pinned here.
 *
 * 1. The review buttons. They were unreachable in production: the gate is
 *    `canReview && onReview`, the prop was optional, and the only render site
 *    passed neither — so Approve/Reject existed in the source and for nobody
 *    else. A test that only proves they hide would have passed the whole time
 *    they were dead, so the one that matters is the one proving they appear.
 *
 * 2. `unassessable`. It must not read as `error`: one says the run failed, the
 *    other says there was never anything to run on, and only the second is
 *    silent about the quality of the evidence.
 *
 * 3. The bulk cap. The server takes the first 50 and drops the rest without
 *    complaint, so the client must not send more and then report success for
 *    work nobody scheduled.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react'
import { EvidenceFileList } from '../EvidenceFileList'
import type { EvidenceFileResponse } from '../../../data/apiClient'
import { makeEvidenceFile as makeFile } from './evidenceFileFixture'

vi.mock('../../../data/apiClient', () => ({
  listEvidenceFiles: vi.fn(),
  deleteEvidenceFile: vi.fn(),
  reviewEvidenceFile: vi.fn(),
  getAssessment: vi.fn(),
  bulkAssess: vi.fn(),
}))

import { listEvidenceFiles, getAssessment, bulkAssess } from '../../../data/apiClient'

async function renderList(
  files: EvidenceFileResponse[],
  props: { canReview?: boolean; canAssess?: boolean } = {},
) {
  vi.mocked(listEvidenceFiles).mockResolvedValue({ files, total: files.length })
  render(
    <EvidenceFileList orgId="org-1" evidenceId="ERL-001" refreshTrigger={0} {...props} />,
  )
  await waitFor(() => expect(screen.getByText(files[0].filename)).toBeTruthy())
}

describe('EvidenceFileList review gating', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getAssessment).mockResolvedValue(null)
  })
  afterEach(() => cleanup())

  it('shows Approve and Reject to a user who may review', async () => {
    await renderList([makeFile({ review_status: 'not_reviewed' })], { canReview: true })
    expect(screen.getByText('Approve')).toBeTruthy()
    expect(screen.getByText('Reject')).toBeTruthy()
  })

  it('hides them when the prop is absent — the old production behaviour', async () => {
    await renderList([makeFile({ review_status: 'not_reviewed' })])
    expect(screen.queryByText('Approve')).toBeNull()
    expect(screen.queryByText('Reject')).toBeNull()
  })

  it('hides them from a user who may not review', async () => {
    await renderList([makeFile({ review_status: 'not_reviewed' })], { canReview: false })
    expect(screen.queryByText('Approve')).toBeNull()
  })

  it('does not offer Approve on an already-approved file', async () => {
    await renderList([makeFile({ review_status: 'approved' })], { canReview: true })
    expect(screen.queryByText('Approve')).toBeNull()
    expect(screen.getByText('Reject')).toBeTruthy()
  })
})

describe('EvidenceFileList assessment chips', () => {
  beforeEach(() => vi.clearAllMocks())
  afterEach(() => cleanup())

  it('labels an unassessable file distinctly from a failed one', async () => {
    vi.mocked(getAssessment).mockResolvedValue(
      { status: 'unassessable' } as never,
    )
    await renderList([makeFile()])
    await waitFor(() => expect(screen.getByText('AI suggests: Unassessable')).toBeTruthy())
    expect(screen.queryByText('AI: Error')).toBeNull()
  })

  it('still labels a genuine error as an error', async () => {
    vi.mocked(getAssessment).mockResolvedValue({ status: 'error' } as never)
    await renderList([makeFile()])
    await waitFor(() => expect(screen.getByText('AI: Error')).toBeTruthy())
  })
})

describe('EvidenceFileList bulk assess', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getAssessment).mockResolvedValue(null)
  })
  afterEach(() => cleanup())

  it('offers no selection to a user who may not assess', async () => {
    await renderList([makeFile()])
    expect(screen.queryByLabelText('Select all 1')).toBeNull()
    expect(screen.queryByText(/Select all/)).toBeNull()
  })

  it('queues the selected files', async () => {
    vi.mocked(bulkAssess).mockResolvedValue({ queued: 1, message: 'ok' })
    await renderList([makeFile()], { canAssess: true })

    fireEvent.click(screen.getByLabelText('Select all files'))
    fireEvent.click(screen.getByText('Assess selected (1)'))

    await waitFor(() =>
      expect(bulkAssess).toHaveBeenCalledWith('org-1', {
        evidence_id: 'ERL-001',
        file_ids: ['file-1'],
      }),
    )
    await waitFor(() =>
      expect(screen.getByText('Queued 1 file for assessment.')).toBeTruthy(),
    )
  })

  it('sends only 50 of an over-cap selection, and says so', async () => {
    const files = Array.from({ length: 55 }, (_, i) =>
      makeFile({ id: `file-${i}`, filename: `report-${i}.pdf` }),
    )
    vi.mocked(bulkAssess).mockResolvedValue({ queued: 50, message: 'ok' })
    await renderList(files, { canAssess: true })

    fireEvent.click(screen.getByLabelText('Select all files'))
    // The cap is disclosed before the click, not after it.
    expect(screen.getByText('Only the first 50 will be queued.')).toBeTruthy()
    fireEvent.click(screen.getByText('Assess selected (50)'))

    await waitFor(() => expect(bulkAssess).toHaveBeenCalled())
    const sent = vi.mocked(bulkAssess).mock.calls[0][1].file_ids
    expect(sent).toHaveLength(50)

    await waitFor(() =>
      expect(screen.getByText(/5 more were not sent/)).toBeTruthy(),
    )
  })

  it('reports a failed bulk request instead of implying the files were queued', async () => {
    vi.mocked(bulkAssess).mockRejectedValue(new Error('Forbidden'))
    await renderList([makeFile()], { canAssess: true })

    fireEvent.click(screen.getByLabelText('Select all files'))
    fireEvent.click(screen.getByText('Assess selected (1)'))

    await waitFor(() =>
      expect(screen.getByText(/Could not queue assessments: Forbidden/)).toBeTruthy(),
    )
  })
})
