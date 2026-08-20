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
import { beforeEach, describe, expect, it, vi } from 'vitest'

import CatalogUpgradePage from '../platform/CatalogUpgradePage'
import {
  RevertBlockedError,
  applyCatalogUpgrade,
  getCatalogStatusExtended,
  getCatalogUpgradeDiff,
  getCatalogUpgradeRun,
  listCatalogUpgradeRuns,
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
      superseded_by: null,
      suggestions: [{ scf_id: 'NEW-9', name: 'Successor Control', score: 0.85 }],
    },
  ],
  total: 1,
  page: 1,
  page_size: 200,
}

function primeDiffMock() {
  mockGetDiff.mockImplementation(async (_runId, params = {}) =>
    params.change_class === 'deprecated' ? deprecatedPage : changedPage
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  mockUseAuth.mockReturnValue({
    user: null,
    token: null,
    isAuthenticated: true,
    authReady: true,
    isPlatformAdmin: true,
    login: vi.fn(),
    logout: vi.fn(),
    refreshUserProfile: vi.fn(),
  })
  mockStatus.mockResolvedValue({ seeded: true, controls: 1451, catalog_version: '2026.1' })
  mockListRuns.mockResolvedValue({ runs: [], total: 0 })
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

    // Pairing editor lists the deprecated control with its suggestion chip
    expect(await screen.findByText('OLD-9')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'NEW-9 · 85%' })).toBeInTheDocument()
  })

  it('apply is gated on typing the exact target version', async () => {
    mockUpload.mockResolvedValue({ run_id: RUN_ID, status: 'staging' })
    mockGetRun.mockResolvedValueOnce(stagedRun()).mockResolvedValue(appliedRun())
    mockApply.mockResolvedValue({ run_id: RUN_ID, status: 'applying' })

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

    // The refreshed run is applied → completion report with re-extraction list
    expect(await screen.findByText('Artifact re-extraction')).toBeInTheDocument()
    expect(screen.getByText(/Catalog upgraded from/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Revert upgrade…' })).toBeInTheDocument()
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
  })
})
