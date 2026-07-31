/**
 * CDM document workspace — truthful status semantics.
 *
 * The chip used to render the backend enum, and rendered it wrongly: with
 * knowledge-base indexing disabled (the self-hosted default) every document
 * terminates at 'parsed', which the old label map called "Indexing" — a
 * permanent in-progress badge on a finished document. These tests hold the
 * property that replaced it: the chip answers "can I map this yet?", machine
 * activity is a subordinate line, and no state is allowed to look in-flight
 * forever.
 *
 * The honesty assertions worth protecting: 'parsed' is Ready and NOT polled;
 * 'indexing_failed' is Ready-with-caveat, never fatal (the old copy told the
 * user to delete a fully mappable document); polling starts immediately
 * instead of after a dead 3s window; and a failed row offers Retry, which
 * re-dispatches ingest against the already-uploaded payload.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../data/apiClient', () => ({
  listCdmDocuments: vi.fn(),
  uploadCdmDocument: vi.fn(),
  getCdmJobStatus: vi.fn(),
  deleteCdmDocument: vi.fn(),
  reingestCdmDocuments: vi.fn(),
  listCdmControlProposals: vi.fn(),
  triggerCdmComputeMappings: vi.fn(),
  getCdmComputeMappingsStatus: vi.fn(),
}))

vi.mock('react-hot-toast', () => ({
  toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }),
}))

import CDMWorkspace from '../CDMWorkspace'
import {
  listCdmDocuments,
  getCdmJobStatus,
  reingestCdmDocuments,
  listCdmControlProposals,
  triggerCdmComputeMappings,
  getCdmComputeMappingsStatus,
} from '../../data/apiClient'
import type { CDMDocument, CDMIngestStatus } from '../../data/apiClient'

const mockList = vi.mocked(listCdmDocuments)
const mockJobStatus = vi.mocked(getCdmJobStatus)
const mockReingest = vi.mocked(reingestCdmDocuments)
const mockProposals = vi.mocked(listCdmControlProposals)
const mockTrigger = vi.mocked(triggerCdmComputeMappings)
const mockComputeStatus = vi.mocked(getCdmComputeMappingsStatus)

function proposalsResponse(total: number) {
  return { proposals: [], total, offset: 0, limit: 1 } as Awaited<
    ReturnType<typeof listCdmControlProposals>
  >
}

function doc(overrides: Partial<CDMDocument> = {}): CDMDocument {
  return {
    id: 'doc-1',
    organization_id: 'org-1',
    original_filename: 'access-policy.pdf',
    mime_type: 'application/pdf',
    size_bytes: 20480,
    sha256: 'a'.repeat(64),
    ingest_status: 'parsed',
    ingest_error: null,
    word_count: 1200,
    upload_user_id: null,
    kb_revision_at_ingest: null,
    created_at: '2026-07-30T09:00:00Z',
    ingest_started_at: null,
    is_stale: false,
    ...overrides,
  }
}

function listResponse(documents: CDMDocument[]) {
  return { documents, total: documents.length }
}

function renderWorkspace() {
  return render(<CDMWorkspace organizationId="org-1" />)
}

async function settle(ms = 300) {
  await new Promise((resolve) => setTimeout(resolve, ms))
}

beforeEach(() => {
  vi.clearAllMocks()
  window.sessionStorage.clear()
  // The workspace fetches the pending-proposal count on mount for the tab
  // badge; default to an empty queue so status tests stay focused.
  mockProposals.mockResolvedValue(proposalsResponse(0))
})

describe('CDMWorkspace status semantics', () => {
  it("renders 'parsed' as Ready to map with a success badge and does not poll it", async () => {
    mockList.mockResolvedValue(listResponse([doc({ ingest_status: 'parsed' })]))
    renderWorkspace()

    const badge = await screen.findByText('Ready to map')
    expect(badge.className).toContain('cdm-badge-success')

    // 'parsed' is terminal when indexing is disabled — a poll here would be
    // the old bug (watching for a transition nobody is going to make).
    await settle()
    expect(mockJobStatus).not.toHaveBeenCalled()
  })

  it("renders 'pending' as a neutral Queued chip, not a warning", async () => {
    mockList.mockResolvedValue(listResponse([doc({ ingest_status: 'pending' })]))
    mockJobStatus.mockResolvedValue({
      document_id: 'doc-1',
      ingest_status: 'pending' as CDMIngestStatus,
      ingest_error: null,
      word_count: null,
    })
    renderWorkspace()

    const badge = await screen.findByText('Queued')
    expect(badge.className).toContain('cdm-badge-neutral')
    expect(badge.className).not.toContain('cdm-badge-progress')
  })

  it("renders 'indexing_failed' as Ready with a warning line, never as fatal", async () => {
    mockList.mockResolvedValue(
      listResponse([doc({ ingest_status: 'indexing_failed' })]),
    )
    renderWorkspace()

    const badge = await screen.findByText('Ready to map')
    expect(badge.className).toContain('cdm-badge-success')
    expect(badge.className).not.toContain('cdm-badge-error')
    expect(
      screen.getByText(/Indexing failed — search may be incomplete/),
    ).toBeInTheDocument()
    // The destructive advice is gone: mapping works, nothing needs deleting.
    expect(badge.getAttribute('title') ?? '').not.toMatch(/delete/i)
  })

  it('polls an in-flight document with an immediate first tick', async () => {
    mockList.mockResolvedValue(listResponse([doc({ ingest_status: 'parsing' })]))
    mockJobStatus.mockResolvedValue({
      document_id: 'doc-1',
      ingest_status: 'parsing' as CDMIngestStatus,
      ingest_error: null,
      word_count: null,
    })
    renderWorkspace()

    await screen.findByText('Not ready')
    // The old poll waited a full interval before its first request; the fix
    // ticks immediately, so the status call lands well inside the 3s cadence.
    await waitFor(() => expect(mockJobStatus).toHaveBeenCalled(), {
      timeout: 1000,
    })
    expect(mockJobStatus).toHaveBeenCalledWith('org-1', 'doc-1')
  })

  it('stops polling when a document settles, and the failed row offers Retry', async () => {
    mockList.mockResolvedValue(listResponse([doc({ ingest_status: 'parsing' })]))
    mockJobStatus.mockResolvedValue({
      document_id: 'doc-1',
      ingest_status: 'failed' as CDMIngestStatus,
      ingest_error: 'exploded',
      word_count: null,
    })
    renderWorkspace()

    const badge = await screen.findByText('Extraction failed')
    expect(badge.className).toContain('cdm-badge-error')
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()

    const callsAtSettle = mockJobStatus.mock.calls.length
    await settle()
    expect(mockJobStatus.mock.calls.length).toBe(callsAtSettle)
  })

  it('Retry re-dispatches ingest for that document and resets the row to Queued', async () => {
    mockList.mockResolvedValue(
      listResponse([doc({ ingest_status: 'failed', ingest_error: 'boom' })]),
    )
    mockReingest.mockResolvedValue({
      dispatched_document_ids: ['doc-1'],
      skipped_document_ids: [],
    })
    // The retried row goes back in flight and the poll re-arms.
    mockJobStatus.mockResolvedValue({
      document_id: 'doc-1',
      ingest_status: 'pending' as CDMIngestStatus,
      ingest_error: null,
      word_count: null,
    })
    renderWorkspace()

    fireEvent.click(await screen.findByRole('button', { name: 'Retry' }))

    await waitFor(() =>
      expect(mockReingest).toHaveBeenCalledWith('org-1', ['doc-1']),
    )
    expect(await screen.findByText('Queued')).toBeInTheDocument()
  })

  it('offers Retry on a stale in-flight row', async () => {
    mockList.mockResolvedValue(
      listResponse([
        doc({
          ingest_status: 'parsing',
          ingest_started_at: '2026-07-30T08:00:00Z',
          is_stale: true,
        }),
      ]),
    )
    mockJobStatus.mockResolvedValue({
      document_id: 'doc-1',
      ingest_status: 'parsing' as CDMIngestStatus,
      ingest_error: null,
      word_count: null,
    })
    renderWorkspace()

    expect(await screen.findByRole('button', { name: 'Retry' })).toBeInTheDocument()
    expect(
      screen.getByText(/Status unknown — processing may have stalled/),
    ).toBeInTheDocument()
  })
})

/**
 * The mapping trigger used to live only on the Review-queue tab — the page
 * that says "Ready to map" offered no way to map. These tests hold the
 * relocated journey: the action bar sits above both tabs, the primary action
 * is reachable from the Documents tab, and completion points at the queue.
 */
describe('CDMWorkspace mapping action bar', () => {
  it('offers Run mapping from the Documents tab when documents are ready', async () => {
    mockList.mockResolvedValue(listResponse([doc({ ingest_status: 'parsed' })]))
    renderWorkspace()

    expect(await screen.findByText('1 document ready to map')).toBeInTheDocument()
    const run = screen.getByRole('button', { name: 'Run mapping' })
    expect(run).toBeEnabled()
  })

  it('disables Run mapping when no document is ready', async () => {
    mockList.mockResolvedValue(listResponse([]))
    renderWorkspace()

    expect(await screen.findByText('No documents ready yet')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run mapping' })).toBeDisabled()
  })

  it('shows the pending-proposal count on the Review queue tab label', async () => {
    mockList.mockResolvedValue(listResponse([doc()]))
    mockProposals.mockResolvedValue(proposalsResponse(229))
    renderWorkspace()

    const tab = await screen.findByRole('tab', { name: /Review queue/ })
    await waitFor(() => expect(tab).toHaveTextContent('229'))
  })

  it('reflects a running run on the bar and hands the button back only when settled', async () => {
    mockList.mockResolvedValue(listResponse([doc({ ingest_status: 'parsed' })]))
    mockTrigger.mockResolvedValue({ task_id: 't-1', idempotent_existing: false })
    mockComputeStatus.mockResolvedValue({
      task_id: 't-1',
      state: 'STARTED',
      ready: false,
      successful: null,
      result: null,
    })
    renderWorkspace()

    fireEvent.click(await screen.findByRole('button', { name: 'Run mapping' }))

    await waitFor(() => expect(mockTrigger).toHaveBeenCalledWith('org-1'))
    expect(await screen.findByText('Mapping run in progress')).toBeInTheDocument()
    const running = screen.getByRole('button', { name: 'Running…' })
    expect(running).toBeDisabled()
  })

  it('on completion shows the outcome and a CTA into the Review queue', async () => {
    mockList.mockResolvedValue(listResponse([doc({ ingest_status: 'parsed' })]))
    mockProposals.mockResolvedValue(proposalsResponse(5))
    mockTrigger.mockResolvedValue({ task_id: 't-1', idempotent_existing: false })
    mockComputeStatus.mockResolvedValue({
      task_id: 't-1',
      state: 'SUCCESS',
      ready: true,
      successful: true,
      result: null,
    })
    renderWorkspace()

    fireEvent.click(await screen.findByRole('button', { name: 'Run mapping' }))

    expect(await screen.findByText('Mapping run complete')).toBeInTheDocument()
    expect(screen.getByText(/5 proposals waiting for review/)).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'Review proposals →' }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'Run mapping again' }),
    ).toBeInTheDocument()
  })
})
