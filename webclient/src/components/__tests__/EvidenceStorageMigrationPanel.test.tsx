/**
 * EvidenceStorageMigrationPanel — Settings → Evidence storage → move existing
 * evidence (ISA phase 6).
 *
 * What these tests are actually defending:
 *
 *   - The copy runs into the store in force now and out of one of the earlier
 *     ones, because that is the order an administrator works in.
 *   - There is a confirm step. A button that starts a bulk object copy on one
 *     click is a button that starts one by accident.
 *   - Progress is read back from the server, not inferred from the request.
 *   - The completion sentence says the source was retired ONLY when the
 *     server's own `source_retired` says so. This is the one claim on the
 *     screen that an operator might act on destructively, so a test asserts
 *     both directions of it.
 *   - A failed row is named, with the reason the task recorded, and the panel
 *     says those files are still on the source store.
 */
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import EvidenceStorageMigrationPanel from '../EvidenceStorageMigrationPanel'
import {
  getEvidenceStorageCopyRun,
  listEvidenceStorageConfigs,
  listEvidenceStorageCopyRuns,
  listEvidenceStorageCopySources,
  startEvidenceStorageCopy,
} from '../../data/evidenceStorageApi'
import type {
  EvidenceStorageConfig,
  EvidenceStorageCopyRun,
  EvidenceStorageCopySource,
} from '../../data/evidenceStorageApi'

vi.mock('../../data/evidenceStorageApi', async () => {
  const actual = await vi.importActual<typeof import('../../data/evidenceStorageApi')>(
    '../../data/evidenceStorageApi'
  )
  return {
    // A pure predicate over a run record. Mocking it would mean testing the
    // panel against a definition of "finished" that the app does not use.
    isCopyRunFinished: actual.isCopyRunFinished,
    listEvidenceStorageConfigs: vi.fn(),
    listEvidenceStorageCopyRuns: vi.fn(),
    listEvidenceStorageCopySources: vi.fn(),
    getEvidenceStorageCopyRun: vi.fn(),
    startEvidenceStorageCopy: vi.fn(),
  }
})

const mockList = vi.mocked(listEvidenceStorageConfigs)
const mockRuns = vi.mocked(listEvidenceStorageCopyRuns)
const mockSources = vi.mocked(listEvidenceStorageCopySources)
const mockRun = vi.mocked(getEvidenceStorageCopyRun)
const mockStart = vi.mocked(startEvidenceStorageCopy)

const ORG = 'org-1'

function config(overrides: Partial<EvidenceStorageConfig> = {}): EvidenceStorageConfig {
  return {
    id: 'cfg-old',
    organization_id: ORG,
    provider: 's3_compatible',
    provider_label: 'Other S3-compatible',
    bucket: 'acme-old',
    region: 'eu-west-1',
    endpoint_url: 'https://old.example.com',
    public_endpoint: null,
    path_style: true,
    sse_mode: 'none',
    access_key_id: 'AKIAEXAMPLE',
    secret_mask: '••••••••',
    key_version: 1,
    status: 'retired',
    is_bundled: false,
    source: 'org',
    managed_by_operator: false,
    created_at: '2026-09-12T09:00:00Z',
    updated_at: '2026-09-12T09:00:00Z',
    updated_by: 'admin@example.com',
    ...overrides,
  }
}

function source(
  overrides: Partial<EvidenceStorageCopySource> = {}
): EvidenceStorageCopySource {
  return {
    config_id: 'cfg-old',
    scope: 'org',
    provider: 's3_compatible',
    provider_label: 'Other S3-compatible',
    bucket: 'acme-old',
    endpoint_url: 'https://old.example.com',
    status: 'retired',
    file_count: 4,
    ...overrides,
  }
}

/** The platform store, as an organisation that came off a bundled install
 *  sees it: it holds that organisation's earlier evidence, it is not the
 *  organisation's to manage, and it is the source of the migration the whole
 *  feature exists for. */
const PLATFORM_SOURCE = source({
  config_id: 'cfg-platform',
  scope: 'platform',
  provider: 'minio',
  provider_label: 'MinIO',
  bucket: 'evidence',
  endpoint_url: 'http://minio:9000',
  status: 'active',
  file_count: 7,
})

const OLD = config()
const NEW = config({
  id: 'cfg-new',
  bucket: 'acme-new',
  endpoint_url: 'https://new.example.com',
  status: 'active',
})

function copyRun(overrides: Partial<EvidenceStorageCopyRun> = {}): EvidenceStorageCopyRun {
  return {
    run_id: 'run-1',
    organization_id: ORG,
    source_config_id: 'cfg-old',
    target_config_id: 'cfg-new',
    status: 'running',
    total: 4,
    copied: 1,
    failed: 0,
    skipped: 0,
    remaining: 3,
    failures: [],
    source_retired: false,
    source_retired_reason: '',
    message: '',
    started_at: '2026-09-12T10:00:00Z',
    finished_at: null,
    updated_at: '2026-09-12T10:00:01Z',
    ...overrides,
  }
}

async function renderLoaded() {
  const view = render(<EvidenceStorageMigrationPanel organizationId={ORG} />)
  await waitFor(() => expect(screen.queryByText('Loading…')).not.toBeInTheDocument())
  return view
}

describe('EvidenceStorageMigrationPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockList.mockResolvedValue({ items: [NEW, OLD] })
    mockRuns.mockResolvedValue([])
    mockSources.mockResolvedValue([source()])
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('offers the earlier stores as sources and names the active store as the target', async () => {
    await renderLoaded()

    const select = screen.getByTestId('evidence-storage-copy-source')
    const options = within(select).getAllByRole('option')
    // The placeholder plus the one non-active configuration. The active store
    // is the destination, so offering it as a source would be a copy to self.
    expect(options).toHaveLength(2)
    expect(options[1]).toHaveValue('cfg-old')
    expect(options[1].textContent).toContain('acme-old')
    expect(screen.getByText(/the store in force now/i).textContent).toContain('acme-new')
  })

  it('says there is nowhere to copy to when no store of this organisation is active', async () => {
    mockList.mockResolvedValue({ items: [OLD] })
    await renderLoaded()

    expect(screen.getByTestId('evidence-storage-migration-no-target')).toBeInTheDocument()
    expect(screen.queryByTestId('evidence-storage-copy-source')).not.toBeInTheDocument()
  })

  it('will not start a copy without a confirm step', async () => {
    const user = userEvent.setup()
    await renderLoaded()

    // Nothing chosen: the action is not available at all.
    expect(screen.getByTestId('evidence-storage-copy-start')).toBeDisabled()

    await user.selectOptions(screen.getByTestId('evidence-storage-copy-source'), 'cfg-old')
    await user.click(screen.getByTestId('evidence-storage-copy-start'))

    // One click got a question, not a copy.
    expect(mockStart).not.toHaveBeenCalled()
    expect(screen.getByRole('alertdialog', { name: 'Confirm evidence copy' })).toBeInTheDocument()

    await user.click(screen.getByTestId('evidence-storage-copy-cancel'))
    expect(mockStart).not.toHaveBeenCalled()
  })

  it('starts the copy from the chosen source into the active store once confirmed', async () => {
    const user = userEvent.setup()
    mockStart.mockResolvedValue(copyRun({ status: 'queued', copied: 0, remaining: 4 }))
    await renderLoaded()

    await user.selectOptions(screen.getByTestId('evidence-storage-copy-source'), 'cfg-old')
    await user.click(screen.getByTestId('evidence-storage-copy-start'))
    await user.click(screen.getByTestId('evidence-storage-copy-confirm'))

    await waitFor(() => expect(mockStart).toHaveBeenCalledWith(ORG, 'cfg-old', 'cfg-new'))
    expect(await screen.findByTestId('evidence-storage-copy-run')).toBeInTheDocument()
  })

  it('polls the run endpoint and moves the progress bar with the server, not the click', async () => {
    const user = userEvent.setup()
    mockStart.mockResolvedValue(copyRun({ status: 'queued', copied: 0, remaining: 4 }))
    mockRun.mockResolvedValue(
      copyRun({ status: 'completed', copied: 4, remaining: 0, source_retired: true })
    )
    await renderLoaded()

    await user.selectOptions(screen.getByTestId('evidence-storage-copy-source'), 'cfg-old')
    await user.click(screen.getByTestId('evidence-storage-copy-start'))
    await user.click(screen.getByTestId('evidence-storage-copy-confirm'))

    // The queued run says nothing has moved, and the bar agrees.
    expect(await screen.findByTestId('evidence-storage-copy-progress')).toHaveAttribute(
      'aria-valuenow',
      '0'
    )

    await waitFor(
      () => expect(mockRun).toHaveBeenCalledWith(ORG, 'run-1'),
      { timeout: 4000 }
    )
    await waitFor(() =>
      expect(screen.getByTestId('evidence-storage-copy-progress')).toHaveAttribute(
        'aria-valuenow',
        '100'
      )
    )
  })

  it('adopts a run that was already in flight when the page loaded', async () => {
    mockRuns.mockResolvedValue([copyRun()])
    await renderLoaded()

    expect(screen.getByTestId('evidence-storage-copy-run')).toBeInTheDocument()
    expect(screen.getByTestId('evidence-storage-copy-counts').textContent).toContain('1 of 4')
    // A copy is in progress, so the panel does not offer to start a second.
    expect(screen.queryByTestId('evidence-storage-copy-start')).not.toBeInTheDocument()
  })

  it('says the source was retired only when the server says it was', async () => {
    mockRuns.mockResolvedValue([
      copyRun({
        status: 'completed',
        copied: 4,
        remaining: 0,
        source_retired: true,
        finished_at: '2026-09-12T10:05:00Z',
      }),
    ])
    const { unmount } = await renderLoaded()
    expect(screen.getByTestId('evidence-storage-copy-complete').textContent).toContain(
      'has been retired'
    )
    unmount()

    // Same run, one row left behind. The source is still referenced, so the
    // panel must not tell an operator it is safe to delete.
    vi.clearAllMocks()
    mockList.mockResolvedValue({ items: [NEW, OLD] })
    mockRuns.mockResolvedValue([
      copyRun({
        status: 'completed_with_errors',
        copied: 3,
        failed: 1,
        remaining: 1,
        source_retired: false,
        finished_at: '2026-09-12T10:05:00Z',
        failures: [{ s3_key: 'org-1/evidence/late.pdf', reason: 'checksum mismatch' }],
      }),
    ])
    render(<EvidenceStorageMigrationPanel organizationId={ORG} />)

    const done = await screen.findByTestId('evidence-storage-copy-complete')
    expect(done.textContent).not.toContain('has been retired')
    expect(done.textContent).toContain('has not been retired')
  })

  it('names each file that was not copied and says it is still on the source store', async () => {
    mockRuns.mockResolvedValue([
      copyRun({
        status: 'completed_with_errors',
        copied: 3,
        failed: 1,
        remaining: 1,
        finished_at: '2026-09-12T10:05:00Z',
        failures: [{ s3_key: 'org-1/evidence/late.pdf', reason: 'checksum mismatch' }],
      }),
    ])
    await renderLoaded()

    const list = screen.getByTestId('evidence-storage-copy-failures')
    expect(within(list).getByText('org-1/evidence/late.pdf')).toBeInTheDocument()
    expect(within(list).getByText('checksum mismatch')).toBeInTheDocument()
    expect(screen.getByText(/still on the source store/i)).toBeInTheDocument()
  })

  it('offers the platform store as a source when this organisation\u2019s evidence is in it', async () => {
    // The migration the feature exists for: installed bundled, wrote evidence
    // to the platform store, then brought its own. Those files are stamped to
    // the platform row, which the organisation does not own and which the
    // configuration list therefore does not carry — so if the panel built its
    // source list from that list, this operator would be told there was
    // nothing to copy from and the evidence would be stranded.
    mockSources.mockResolvedValue([PLATFORM_SOURCE])
    await renderLoaded()

    const select = screen.getByTestId('evidence-storage-copy-source')
    const options = within(select).getAllByRole('option')
    expect(options).toHaveLength(2)
    expect(options[1]).toHaveValue('cfg-platform')
    expect(options[1].textContent).toContain('evidence')
    // Labelled as the installation's, not as something this organisation
    // manages (D42: never a control, never a link).
    expect(options[1].textContent).toMatch(/managed by the platform/i)
  })

  it('starts a copy out of the platform store into the organisation\u2019s own store', async () => {
    const user = userEvent.setup()
    mockSources.mockResolvedValue([PLATFORM_SOURCE])
    mockStart.mockResolvedValue(
      copyRun({ source_config_id: 'cfg-platform', status: 'queued', copied: 0, remaining: 7 })
    )
    await renderLoaded()

    await user.selectOptions(screen.getByTestId('evidence-storage-copy-source'), 'cfg-platform')
    await user.click(screen.getByTestId('evidence-storage-copy-start'))
    await user.click(screen.getByTestId('evidence-storage-copy-confirm'))

    await waitFor(() => expect(mockStart).toHaveBeenCalledWith(ORG, 'cfg-platform', 'cfg-new'))
  })

  it('never says the platform store was retired, and repeats the server\u2019s reason', async () => {
    // The one sentence on this screen an operator might act on destructively.
    // The platform store is shared: other tenants are still writing to it, so
    // a copy out of it retires nothing and must not imply otherwise.
    const finished = copyRun({
      source_config_id: 'cfg-platform',
      status: 'completed',
      total: 7,
      copied: 7,
      remaining: 0,
      source_retired: false,
      source_retired_reason: 'platform store, left in service',
    })
    mockSources.mockResolvedValue([PLATFORM_SOURCE])
    mockRuns.mockResolvedValue([finished])
    await renderLoaded()

    const text = (await screen.findByTestId('evidence-storage-copy-run')).textContent || ''
    expect(text).toContain('platform store, left in service')
    expect(text).not.toMatch(/has been retired/i)
  })

  it('says there is nothing to copy from when no other store holds this organisation\u2019s evidence', async () => {
    mockSources.mockResolvedValue([])
    await renderLoaded()

    expect(screen.getByTestId('evidence-storage-migration-no-source')).toBeInTheDocument()
    expect(screen.queryByTestId('evidence-storage-copy-source')).not.toBeInTheDocument()
  })

  it('renders nothing at all when the caller is not an organisation admin', async () => {
    const forbidden = Object.assign(new Error('Forbidden'), { status: 403 })
    mockList.mockRejectedValue(forbidden)
    mockRuns.mockRejectedValue(forbidden)

    const { container } = render(<EvidenceStorageMigrationPanel organizationId={ORG} />)
    await waitFor(() => expect(container).toBeEmptyDOMElement())
  })
})
