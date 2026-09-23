/**
 * CatalogUpgradePage wizard.
 *
 * Backend endpoints are 501 stubs during this WP, so everything runs against
 * mocked API functions. The assertions that matter: a non-platform-admin gets
 * nothing (no data fetch, no console), the upload→staged→diff flow surfaces
 * the run for review, the apply is gated on typing the exact target version,
 * and a 409 revert lists the blocking organisations instead of pretending
 * the revert started.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import CatalogUpgradePage from '../platform/CatalogUpgradePage'
import {
  RevertBlockedError,
  applyCatalogUpgrade,
  getCatalogStatusExtended,
  getCatalogUpgradeDiff,
  getCatalogUpgradeRun,
  getFrameworkRegistryStatus,
  getPublisherChanges,
  listCatalogUpgradeRuns,
  registerFrameworkRegistry,
  revertCatalogUpgrade,
  uploadCatalogUpgrade,
} from '../../data/catalogUpgradeApi'
import { useAuth } from '../../contexts/AuthContext'
import type {
  DiffPageResponse,
  PlatformImportRunDetail,
  PlatformImportRunSummary,
} from '../../types/catalogUpgrade'

vi.mock('../../data/catalogUpgradeApi', async () => {
  const actual = await vi.importActual<typeof import('../../data/catalogUpgradeApi')>(
    '../../data/catalogUpgradeApi'
  )
  return {
    RevertBlockedError: actual.RevertBlockedError,
    getCatalogStatusExtended: vi.fn(),
    listCatalogUpgradeRuns: vi.fn(),
    getCatalogUpgradeRun: vi.fn(),
    getCatalogUpgradeDiff: vi.fn(),
    putCatalogUpgradePairings: vi.fn(),
    uploadCatalogUpgrade: vi.fn(),
    applyCatalogUpgrade: vi.fn(),
    cancelCatalogUpgradeRun: vi.fn(),
    revertCatalogUpgrade: vi.fn(),
    registerFrameworkRegistry: vi.fn(),
    getFrameworkRegistryStatus: vi.fn(),
    getPublisherChanges: vi.fn(),
  }
})

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: vi.fn(),
}))

const mockUseAuth = vi.mocked(useAuth)
const mockStatus = vi.mocked(getCatalogStatusExtended)
const mockListRuns = vi.mocked(listCatalogUpgradeRuns)
const mockGetRun = vi.mocked(getCatalogUpgradeRun)
const mockGetDiff = vi.mocked(getCatalogUpgradeDiff)
const mockUpload = vi.mocked(uploadCatalogUpgrade)
const mockApply = vi.mocked(applyCatalogUpgrade)
const mockRevert = vi.mocked(revertCatalogUpgrade)
const mockRegisterRegistry = vi.mocked(registerFrameworkRegistry)
const mockRegistryStatus = vi.mocked(getFrameworkRegistryStatus)
const mockPublisherChanges = vi.mocked(getPublisherChanges)

// Fixture keys deliberately avoid the real SCF `XXX-NN` id shape.
const RUN_ID = 'run-1'

function stagedRun(): PlatformImportRunDetail {
  return {
    id: RUN_ID,
    from_version: '2026.1',
    to_version: '2026.2',
    status: 'staged',
    created_by: 'admin@example.com',
    created_at: '2026-08-20T10:00:00Z',
    updated_at: '2026-08-20T10:05:00Z',
    diff_summary: {
      from_version: '2026.1',
      to_version: '2026.2',
      entities: {
        controls: { added: 1, changed: 1, deprecated: 1, resurrected: 0, unchanged: 1400 },
      },
    },
    sanity_report: { passed: true, checks: [] },
    superseded_pairings: [],
  }
}

function appliedRun(): PlatformImportRunDetail {
  return {
    ...stagedRun(),
    status: 'applied',
    applied_at: '2026-08-20T11:00:00Z',
  }
}

function appliedSummary(): PlatformImportRunSummary {
  const { id, from_version, to_version, status, created_by, created_at, updated_at } = appliedRun()
  return { id, from_version, to_version, status, created_by, created_at, updated_at }
}

/**
 * A blocked 2026.2 -> 2026.3 run whose live_framework_registry check failed:
 * the platform has no stored framework registry for the live catalog, so the
 * admin must register the current workbook before the churn gate can pass.
 */
function blockedNoRegistryRun(): PlatformImportRunDetail {
  return {
    ...stagedRun(),
    from_version: '2026.2',
    to_version: '2026.3',
    status: 'blocked',
    sanity_report: {
      passed: false,
      checks: [
        { check: 'version_parseable', passed: true },
        {
          check: 'live_framework_registry',
          passed: false,
          detail:
            'No framework registry is stored for the live catalog 2026.2 and it could not be recovered. Register the 2026.2 workbook to continue.',
        },
      ],
    },
  }
}

/** A staged run carrying the publisher's own declared changes. */
function stagedWithPublisherChanges(): PlatformImportRunDetail {
  const run = stagedRun()
  return {
    ...run,
    from_version: '2026.2',
    to_version: '2026.3',
    diff_summary: {
      ...run.diff_summary!,
      from_version: '2026.2',
      to_version: '2026.3',
      publisher_changes: {
        summary: 'This release retires two framework editions and renumbers the GOV domain.',
        frameworks_added: 3,
        frameworks_removed: 2,
        mapping_errata: 1,
        controls: { renumbered: 12, new_control: 5, merged: 2, wordsmithed: 0 },
      },
    },
  }
}

const changedPage: DiffPageResponse = {
  run_id: RUN_ID,
  items: [
    {
      entity: 'controls',
      change_class: 'changed',
      key: 'CTL-9',
      name: 'Access Enforcement',
      fields: { name: { old: 'Access Control', new: 'Access Enforcement' } },
      data: {},
      suggestions: [],
    },
  ],
  total: 1,
  page: 1,
  page_size: 50,
}

const deprecatedPage: DiffPageResponse = {
  run_id: RUN_ID,
  items: [
    {
      entity: 'controls',
      change_class: 'deprecated',
      key: 'OLD-9',
      name: 'Legacy Control',
      fields: {},
      data: {},
      // The workbook declared this successor, so the editor applies it as-is.
      superseded_by: 'NEW-9',
      superseded_source: 'workbook_crosswalk',
      suggestions: [{ scf_id: 'NEW-9', name: 'Successor Control', score: 1 }],
    },
  ],
  total: 1,
  page: 1,
  page_size: 500,
}

function primeDiffMock() {
  mockGetDiff.mockImplementation(async (_runId, params = {}) =>
    params.change_class === 'deprecated' ? deprecatedPage : changedPage
  )
}

afterEach(() => {
  // The apply test switches to fake timers; a failure before its own reset
  // must not leak them into the next test.
  vi.useRealTimers()
})

beforeEach(() => {
  vi.clearAllMocks()
  mockUseAuth.mockReturnValue({
    user: null,
    token: null,
    isAuthenticated: true,
    authReady: true,
    isPlatformAdmin: true,
    canManageIntegrations: true,
    login: vi.fn(),
    logout: vi.fn(),
    refreshUserProfile: vi.fn(),
  })
  mockStatus.mockResolvedValue({ seeded: true, controls: 1451, catalog_version: '2026.1' })
  mockListRuns.mockResolvedValue({ runs: [], total: 0 })
  mockRegistryStatus.mockResolvedValue({
    catalog_version: '2026.1',
    present: true,
    entries: 1200,
    with_focal_document_id: 1150,
    source: 'workbook_upload',
  })
  primeDiffMock()
})

describe('CatalogUpgradePage gating', () => {
  it('shows access denied and fetches nothing for non-platform-admins', () => {
    mockUseAuth.mockReturnValue({
      user: null,
      token: null,
      isAuthenticated: true,
      authReady: true,
      isPlatformAdmin: false,
      canManageIntegrations: false,
      login: vi.fn(),
      logout: vi.fn(),
      refreshUserProfile: vi.fn(),
    })

    render(<CatalogUpgradePage />)

    expect(screen.getByText('Access denied')).toBeInTheDocument()
    expect(screen.queryByText('Upgrade catalog')).not.toBeInTheDocument()
    expect(mockStatus).not.toHaveBeenCalled()
    expect(mockListRuns).not.toHaveBeenCalled()
  })
})

describe('CatalogUpgradePage wizard', () => {
  it('renders the version card and empty history', async () => {
    render(<CatalogUpgradePage />)

    expect(await screen.findByText('2026.1')).toBeInTheDocument()
    expect(screen.getByText('No catalog upgrade runs yet.')).toBeInTheDocument()
  })

  it('upload → staged run surfaces the diff preview and pairing editor', async () => {
    mockUpload.mockResolvedValue({ run_id: RUN_ID, status: 'staging' })
    mockGetRun.mockResolvedValue(stagedRun())

    render(<CatalogUpgradePage />)

    const file = new File(['workbook'], 'scf-2026-2.xlsx', {
      type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    })
    fireEvent.change(screen.getByLabelText('SCF workbook file'), { target: { files: [file] } })
    fireEvent.click(screen.getByRole('button', { name: 'Upload & stage' }))

    expect(await screen.findByText('Run 2026.1 → 2026.2')).toBeInTheDocument()
    expect(mockUpload).toHaveBeenCalledWith(file)
    expect(mockGetRun).toHaveBeenCalledWith(RUN_ID)

    // Diff preview shows the changed control with field-level old/new
    // ('Access Enforcement' appears twice: the name cell and the new value)
    expect(await screen.findByText('CTL-9')).toBeInTheDocument()
    expect(screen.getByText('Access Control')).toBeInTheDocument()
    expect(screen.getAllByText('Access Enforcement')).toHaveLength(2)

    // Pairing editor lists the deprecated control against the successor the
    // workbook declared — and offers no scored similarity chip to click.
    expect(await screen.findByText('OLD-9')).toBeInTheDocument()
    expect(screen.getByText('Declared by workbook')).toBeInTheDocument()
    expect(screen.getByText('NEW-9')).toBeInTheDocument()
    expect(screen.getByText('Legacy SCF # crosswalk')).toBeInTheDocument()
    expect(screen.getByText('1 declared by the workbook · 0 overridden · 0 undecided')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /NEW-9 ·/ })).not.toBeInTheDocument()
  })

  it('apply is gated on typing the exact target version, then polls until the worker settles the run', async () => {
    mockUpload.mockResolvedValue({ run_id: RUN_ID, status: 'staging' })
    // The apply route answers 202 before the worker flips the row: the first
    // read after the apply still says 'staged', the next 'applying', then
    // 'applied'. The page must not fall back to the staged view in between.
    mockGetRun
      .mockResolvedValueOnce(stagedRun())
      .mockResolvedValueOnce(stagedRun())
      .mockResolvedValueOnce({ ...stagedRun(), status: 'applying' })
      .mockResolvedValue(appliedRun())
    mockApply.mockResolvedValue({ run_id: RUN_ID, status: 'applying' })
    vi.useFakeTimers({ shouldAdvanceTime: true })

    render(<CatalogUpgradePage />)

    const file = new File(['workbook'], 'scf-2026-2.xlsx')
    fireEvent.change(screen.getByLabelText('SCF workbook file'), { target: { files: [file] } })
    fireEvent.click(screen.getByRole('button', { name: 'Upload & stage' }))
    await screen.findByText('Run 2026.1 → 2026.2')

    fireEvent.click(screen.getByRole('button', { name: 'Apply upgrade…' }))
    const confirmButton = screen.getByRole('button', { name: 'Apply 2026.2' })
    expect(confirmButton).toBeDisabled()

    // Wrong text keeps the apply disabled
    fireEvent.change(screen.getByLabelText('Confirm version'), { target: { value: '2026.1' } })
    expect(confirmButton).toBeDisabled()
    expect(mockApply).not.toHaveBeenCalled()

    // Exact version enables it, and both guard fields travel in the request
    fireEvent.change(screen.getByLabelText('Confirm version'), { target: { value: '2026.2' } })
    expect(confirmButton).toBeEnabled()
    fireEvent.click(confirmButton)

    await waitFor(() =>
      expect(mockApply).toHaveBeenCalledWith(RUN_ID, '2026.2', '2026.2')
    )

    // Straight after the 202 the panel shows the apply in progress, not the
    // staged view with its Apply button.
    expect(await screen.findByText('Applying catalog 2026.2…')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Apply upgrade…' })).not.toBeInTheDocument()

    // First poll reads 'staged' (worker not started): still applying.
    await vi.advanceTimersByTimeAsync(2600)
    expect(screen.getByText('Applying catalog 2026.2…')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Apply upgrade…' })).not.toBeInTheDocument()

    // Then 'applying', then 'applied' → completion report with re-extraction list
    await vi.advanceTimersByTimeAsync(2600)
    await vi.advanceTimersByTimeAsync(2600)
    expect(await screen.findByText('Artifact re-extraction')).toBeInTheDocument()
    expect(screen.getByText(/Catalog upgraded from/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Revert upgrade…' })).toBeInTheDocument()
  })

  it('revert holds the run in "reverting" and polls until the worker lands it as reverted', async () => {
    mockListRuns.mockResolvedValue({ runs: [appliedSummary()], total: 1 })
    // The revert route answers 202 while the row still reads 'applied'; the
    // worker later lands it as 'reverted'. The page must not fall back to the
    // Applied view (with its Revert button) in between.
    mockGetRun
      .mockResolvedValueOnce(appliedRun())
      .mockResolvedValueOnce(appliedRun())
      .mockResolvedValue({ ...appliedRun(), status: 'reverted', reverted_at: '2026-08-20T12:00:00Z' })
    mockRevert.mockResolvedValue({ run_id: RUN_ID, status: 'reverting' })
    vi.useFakeTimers({ shouldAdvanceTime: true })

    render(<CatalogUpgradePage />)

    fireEvent.click(await screen.findByText('Applied'))
    await screen.findByText('Run 2026.1 → 2026.2')

    fireEvent.click(screen.getByRole('button', { name: 'Revert upgrade…' }))
    fireEvent.click(screen.getByRole('button', { name: 'Revert upgrade' }))
    await waitFor(() => expect(mockRevert).toHaveBeenCalledWith(RUN_ID))

    expect(await screen.findByText('Reverting catalog 2026.2…')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Revert upgrade…' })).not.toBeInTheDocument()

    // First poll still reads 'applied': keep reverting, keep polling.
    await vi.advanceTimersByTimeAsync(2600)
    expect(screen.getByText('Reverting catalog 2026.2…')).toBeInTheDocument()

    await vi.advanceTimersByTimeAsync(2600)
    expect(await screen.findByText(/This upgrade was reverted/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Revert upgrade…' })).not.toBeInTheDocument()
  })

  it('revert blocked with 409 lists the blocking organisations', async () => {
    mockListRuns.mockResolvedValue({ runs: [appliedSummary()], total: 1 })
    mockGetRun.mockResolvedValue(appliedRun())
    mockRevert.mockRejectedValue(
      new RevertBlockedError('2 organisations are reconciled to 2026.2', ['Acme Corp', 'Globex'])
    )

    render(<CatalogUpgradePage />)

    // Open the applied run from history
    fireEvent.click(await screen.findByText('Applied'))
    await screen.findByText('Run 2026.1 → 2026.2')

    fireEvent.click(screen.getByRole('button', { name: 'Revert upgrade…' }))
    fireEvent.click(screen.getByRole('button', { name: 'Revert upgrade' }))

    expect(await screen.findByText(/Revert blocked\./)).toBeInTheDocument()
    expect(screen.getByText('Acme Corp')).toBeInTheDocument()
    expect(screen.getByText('Globex')).toBeInTheDocument()
    expect(mockRevert).toHaveBeenCalledWith(RUN_ID)
  })

  it('blocked staging run shows the failed sanity checks', async () => {
    const blocked: PlatformImportRunDetail = {
      ...stagedRun(),
      status: 'blocked',
      sanity_report: {
        passed: false,
        checks: [
          { check: 'version_parseable', passed: true },
          { check: 'control_count_drop', passed: false, detail: 'Control count dropped by 40%' },
        ],
      },
    }
    mockListRuns.mockResolvedValue({
      runs: [{ ...appliedSummary(), status: 'blocked' }],
      total: 1,
    })
    mockGetRun.mockResolvedValue(blocked)

    render(<CatalogUpgradePage />)

    fireEvent.click(await screen.findByText('Blocked'))
    expect(await screen.findByText('control_count_drop')).toBeInTheDocument()
    expect(screen.getByText(/Control count dropped by 40%/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Apply upgrade…' })).not.toBeInTheDocument()

    // No live_framework_registry failure -> no registration card
    expect(
      screen.queryByText('Register your current catalog workbook')
    ).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Register workbook' })).not.toBeInTheDocument()
  })
})

describe('CatalogUpgradePage framework registry registration', () => {
  it('blocked live_framework_registry check offers registration and confirms it', async () => {
    mockListRuns.mockResolvedValue({
      runs: [{ ...appliedSummary(), from_version: '2026.2', to_version: '2026.3', status: 'blocked' }],
      total: 1,
    })
    mockGetRun.mockResolvedValue(blockedNoRegistryRun())
    mockRegisterRegistry.mockResolvedValue({
      catalog_version: '2026.2',
      workbook_version: '2026.2',
      entries: 1201,
      with_focal_document_id: 1187,
      source: 'workbook_upload',
    })

    render(<CatalogUpgradePage />)

    fireEvent.click(await screen.findByText('Blocked'))

    // The failed check is still listed, and the card explains the fix
    expect(await screen.findByText('live_framework_registry')).toBeInTheDocument()
    expect(
      await screen.findByText('Register your current catalog workbook')
    ).toBeInTheDocument()

    const registerButton = screen.getByRole('button', { name: 'Register workbook' })
    expect(registerButton).toBeDisabled()

    const workbook = new File(['workbook'], 'scf-2026-2.xlsx', {
      type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    })
    fireEvent.change(screen.getByLabelText('Current catalog workbook'), {
      target: { files: [workbook] },
    })
    expect(registerButton).toBeEnabled()
    fireEvent.click(registerButton)

    await waitFor(() => expect(mockRegisterRegistry).toHaveBeenCalledWith(workbook))

    expect(
      await screen.findByText(
        'Registered 2026.2: 1201 frameworks, 1187 with a focal-document identifier'
      )
    ).toBeInTheDocument()
    expect(
      screen.getByText('Discard this run and upload the 2026.3 workbook again')
    ).toBeInTheDocument()

    // The discard path stays available
    expect(screen.getByRole('button', { name: 'Discard run' })).toBeInTheDocument()
  })

  it('surfaces a 409 version mismatch from the registration endpoint verbatim', async () => {
    mockListRuns.mockResolvedValue({
      runs: [{ ...appliedSummary(), from_version: '2026.2', to_version: '2026.3', status: 'blocked' }],
      total: 1,
    })
    mockGetRun.mockResolvedValue(blockedNoRegistryRun())
    mockRegisterRegistry.mockRejectedValue(
      new Error('Workbook version 2026.3 does not match the live catalog version 2026.2.')
    )

    render(<CatalogUpgradePage />)

    fireEvent.click(await screen.findByText('Blocked'))
    await screen.findByText('Register your current catalog workbook')

    const workbook = new File(['workbook'], 'scf-2026-3.xlsx')
    fireEvent.change(screen.getByLabelText('Current catalog workbook'), {
      target: { files: [workbook] },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Register workbook' }))

    expect(
      await screen.findByText(
        'Workbook version 2026.3 does not match the live catalog version 2026.2.'
      )
    ).toBeInTheDocument()
  })
})

describe('CatalogUpgradePage publisher changes', () => {
  it('staged run with publisher changes shows the counts and lazy-loads details', async () => {
    mockListRuns.mockResolvedValue({
      runs: [{ ...appliedSummary(), from_version: '2026.2', to_version: '2026.3', status: 'staged' }],
      total: 1,
    })
    mockGetRun.mockResolvedValue(stagedWithPublisherChanges())
    mockPublisherChanges.mockResolvedValue({
      summary: 'This release retires two framework editions and renumbers the GOV domain.',
      frameworks: {
        added: [{ fdi: 'FDI-NEW', name: 'Brand New Framework' }],
        removed: [{ fdi: 'FDI-OLD', name: 'Retired Framework' }],
        mapping_errata: [{ fdi: 'FDI-ERR', name: 'Errata Framework', note: 'Mapping corrected' }],
      },
      controls: {
        counts: { renumbered: 12, new_control: 5, merged: 2 },
        merged: [
          { legacy_scf_id: 'OLD-1', legacy_name: 'Legacy One', merged_into: 'NEW-1' },
        ],
        tags: {},
      },
    })

    render(<CatalogUpgradePage />)

    fireEvent.click(await screen.findByText('Staged'))

    expect(
      await screen.findByText('What the publisher changed in 2026.3')
    ).toBeInTheDocument()
    expect(
      screen.getByText(/This release retires two framework editions/)
    ).toBeInTheDocument()

    // Non-zero counts render; zero/absent keys are omitted
    expect(screen.getByText('3 frameworks added')).toBeInTheDocument()
    expect(screen.getByText('2 frameworks removed')).toBeInTheDocument()
    expect(screen.getByText('1 mapping errata')).toBeInTheDocument()
    expect(screen.getByText('12 controls renumbered')).toBeInTheDocument()
    expect(screen.getByText('5 new controls')).toBeInTheDocument()
    expect(screen.getByText('2 controls absorbed a merge')).toBeInTheDocument()
    expect(screen.queryByText(/controls wordsmithed/)).not.toBeInTheDocument()
    expect(screen.queryByText(/controls renamed/)).not.toBeInTheDocument()

    // The publisher's declaration is read before the platform's computed diff
    const heading = screen.getByText('What the publisher changed in 2026.3')
    const diffTablist = screen.getByRole('tablist', { name: 'Diff entity' })
    expect(
      heading.compareDocumentPosition(diffTablist) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()

    // Details are only fetched when asked for; the toggle announces its state
    expect(mockPublisherChanges).not.toHaveBeenCalled()
    const toggle = screen.getByRole('button', { name: 'Show details' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(toggle)
    expect(screen.getByRole('button', { name: 'Hide details' })).toHaveAttribute(
      'aria-expanded',
      'true'
    )

    await waitFor(() => expect(mockPublisherChanges).toHaveBeenCalledWith(RUN_ID))
    expect(await screen.findByText('Retired Framework (FDI-OLD)')).toBeInTheDocument()
    expect(screen.getByText('Brand New Framework (FDI-NEW)')).toBeInTheDocument()
    expect(screen.getByText('OLD-1 (Legacy One) → NEW-1')).toBeInTheDocument()
  })

  it('staged run keeps the passing staging checks readable, collapsed by default', async () => {
    const staged: PlatformImportRunDetail = {
      ...stagedRun(),
      from_version: '2026.2',
      to_version: '2026.3',
      sanity_report: {
        passed: true,
        checks: [
          { check: 'version_parseable', passed: true },
          {
            check: 'live_framework_registry',
            passed: true,
            detail: 'registry for 2026.2: 251 frameworks, 251 carrying a focal-document identifier (source: backfill)',
          },
          {
            check: 'framework_churn',
            passed: true,
            detail: '73 live frameworks absent from the workbook: 68 renamed (same focal document), 5 superseded by a new edition, 0 retired by the publisher, 0 unexplained (0.0% of 248 live active)',
          },
        ],
      },
    }
    mockListRuns.mockResolvedValue({
      runs: [{ ...appliedSummary(), from_version: '2026.2', to_version: '2026.3', status: 'staged' }],
      total: 1,
    })
    mockGetRun.mockResolvedValue(staged)

    render(<CatalogUpgradePage />)
    fireEvent.click(await screen.findByText('Staged'))
    await screen.findByText('Run 2026.2 → 2026.3')

    const disclosure = screen.getByText('Staging checks: 3 of 3 passed').closest('details')
    expect(disclosure).not.toBeNull()
    expect(disclosure).not.toHaveAttribute('open')
    expect(screen.getByText('live_framework_registry')).toBeInTheDocument()
    expect(screen.getByText(/68 renamed \(same focal document\)/)).toBeInTheDocument()
    // Still a staged run: apply stays available
    expect(screen.getByRole('button', { name: 'Apply upgrade…' })).toBeInTheDocument()
  })

  it('staged run without publisher changes shows no publisher panel', async () => {
    mockUpload.mockResolvedValue({ run_id: RUN_ID, status: 'staging' })
    mockGetRun.mockResolvedValue(stagedRun())

    render(<CatalogUpgradePage />)

    const file = new File(['workbook'], 'scf-2026-2.xlsx')
    fireEvent.change(screen.getByLabelText('SCF workbook file'), { target: { files: [file] } })
    fireEvent.click(screen.getByRole('button', { name: 'Upload & stage' }))
    await screen.findByText('Run 2026.1 → 2026.2')

    expect(screen.queryByText(/What the publisher changed/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Show details' })).not.toBeInTheDocument()
    expect(mockPublisherChanges).not.toHaveBeenCalled()
  })
})

describe('VersionCard framework registry line', () => {
  it('reports the stored registry for the live catalog', async () => {
    render(<CatalogUpgradePage />)

    expect(
      await screen.findByText(
        'Framework registry: 1200 frameworks, 1150 with focal-document identifiers (workbook_upload)'
      )
    ).toBeInTheDocument()
  })

  it('says the registry is not stored and how it gets recovered', async () => {
    mockRegistryStatus.mockResolvedValue({
      catalog_version: '2026.2',
      present: false,
      entries: 0,
      with_focal_document_id: 0,
      source: null,
    })

    render(<CatalogUpgradePage />)

    expect(
      await screen.findByText(
        'Framework registry: not stored for 2026.2 — the next upgrade will try to recover it, or register the workbook from a blocked run'
      )
    ).toBeInTheDocument()
  })

  it('renders nothing for the registry when the request fails', async () => {
    mockRegistryStatus.mockRejectedValue(new Error('boom'))

    render(<CatalogUpgradePage />)

    expect(await screen.findByText('2026.1')).toBeInTheDocument()
    expect(screen.queryByText(/Framework registry:/)).not.toBeInTheDocument()
  })
})
