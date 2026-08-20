/**
 * TenantReconciliationBoard.
 *
 * Backend endpoints are 501 stubs during this WP, so everything runs against
 * mocked API functions. The assertions that matter: a non-platform-admin gets
 * nothing (no board fetch), the board renders one row per organisation with
 * reconciled version / eligibility / active run, and clicking a row opens the
 * per-org reconciliation wizard for that organisation.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import TenantReconciliationBoard from '../platform/TenantReconciliationBoard'
import {
  getOrgReconciliationStatus,
  getTenantsBoard,
  listOrgReconciliationRuns,
} from '../../data/catalogUpgradeApi'
import { useAuth } from '../../contexts/AuthContext'
import type { TenantsBoardResponse } from '../../types/catalogUpgrade'

vi.mock('../../data/catalogUpgradeApi', () => ({
  getTenantsBoard: vi.fn(),
  getOrgReconciliationStatus: vi.fn(),
  listOrgReconciliationRuns: vi.fn(),
  getOrgReconciliationRun: vi.fn(),
  postOrgReconciliationPreview: vi.fn(),
  putOrgReconciliationActions: vi.fn(),
  applyOrgReconciliation: vi.fn(),
  rollbackOrgReconciliation: vi.fn(),
  cancelOrgReconciliationRun: vi.fn(),
}))

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: vi.fn(),
}))

const mockUseAuth = vi.mocked(useAuth)
const mockGetBoard = vi.mocked(getTenantsBoard)
const mockOrgStatus = vi.mocked(getOrgReconciliationStatus)
const mockListRuns = vi.mocked(listOrgReconciliationRuns)

const board: TenantsBoardResponse = {
  platform_catalog_version: '2026.2',
  tenants: [
    {
      organization_id: 'org-1',
      organization_name: 'Acme Corp',
      reconciled_catalog_version: '2026.1',
      last_reconciled_at: '2026-08-01T09:00:00Z',
      eligible: true,
      active_run_id: null,
      active_run_status: null,
    },
    {
      organization_id: 'org-2',
      organization_name: 'Globex',
      reconciled_catalog_version: '2026.2',
      last_reconciled_at: '2026-08-19T09:00:00Z',
      eligible: false,
      active_run_id: null,
      active_run_status: null,
    },
    {
      organization_id: 'org-3',
      organization_name: 'Initech',
      reconciled_catalog_version: '2026.1',
      last_reconciled_at: null,
      eligible: true,
      active_run_id: 'org-run-3',
      active_run_status: 'previewed',
    },
  ],
  total: 3,
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
  mockGetBoard.mockResolvedValue(board)
  mockOrgStatus.mockResolvedValue({
    organization_id: 'org-2',
    reconciled_catalog_version: '2026.2',
    platform_catalog_version: '2026.2',
    eligible: false,
    active_run: null,
    first_reconciliation: false,
  })
  mockListRuns.mockResolvedValue({ runs: [], total: 0 })
})

describe('TenantReconciliationBoard gating', () => {
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

    render(<TenantReconciliationBoard />)

    expect(screen.getByText('Access denied')).toBeInTheDocument()
    expect(screen.queryByText('Reconciliation board')).not.toBeInTheDocument()
    expect(mockGetBoard).not.toHaveBeenCalled()
  })
})

describe('TenantReconciliationBoard', () => {
  it('renders one row per organisation with version, eligibility, and active run', async () => {
    render(<TenantReconciliationBoard />)

    expect(await screen.findByText('Acme Corp')).toBeInTheDocument()
    expect(screen.getByText('Globex')).toBeInTheDocument()
    expect(screen.getByText('Initech')).toBeInTheDocument()

    // Platform version header
    expect(screen.getByText('2026.2', { selector: 'strong' })).toBeInTheDocument()

    // Eligibility: two eligible orgs, one up to date
    expect(screen.getAllByText('Upgrade available')).toHaveLength(2)
    expect(screen.getByText('Up to date')).toBeInTheDocument()

    // Initech's active previewed run is badged; the others show none
    expect(screen.getByText('Previewed')).toBeInTheDocument()

    // Never-reconciled org renders 'Never' for last reconciled
    expect(screen.getByText('Never')).toBeInTheDocument()
  })

  it('opens the per-org wizard when a row is clicked', async () => {
    render(<TenantReconciliationBoard />)

    fireEvent.click(await screen.findByText('Globex'))

    // The wizard loads that organisation's reconciliation status
    expect(await screen.findByText(/up to date with the platform catalog/)).toBeInTheDocument()
    expect(mockOrgStatus).toHaveBeenCalledWith('org-2')
    expect(mockListRuns).toHaveBeenCalledWith('org-2')
    // Wizard header names the organisation (also present in its board row)
    expect(screen.getAllByText('Globex').length).toBeGreaterThan(1)
  })

  it('renders an empty state when there are no tenants', async () => {
    mockGetBoard.mockResolvedValue({ platform_catalog_version: '2026.2', tenants: [], total: 0 })

    render(<TenantReconciliationBoard />)

    expect(await screen.findByText('No tenant organisations found.')).toBeInTheDocument()
  })
})
