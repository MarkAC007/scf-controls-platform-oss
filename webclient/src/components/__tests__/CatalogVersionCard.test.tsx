/**
 * CatalogVersionCard: the upgrade-available banner shows only when the
 * platform catalog version is ahead of the org's reconciled version.
 */
import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import CatalogVersionCard, {
  compareCatalogVersions,
  isCatalogUpgradeAvailable,
} from '../CatalogVersionCard'
import { getOrgReconciliationStatus } from '../../data/catalogUpgradeApi'
import type { OrgCatalogStatusResponse } from '../../types/catalogUpgrade'

vi.mock('../../data/catalogUpgradeApi', () => ({
  getOrgReconciliationStatus: vi.fn(),
}))

const mockStatus = vi.mocked(getOrgReconciliationStatus)

const ORG_ID = 'org-1'

function orgStatus(overrides: Partial<OrgCatalogStatusResponse> = {}): OrgCatalogStatusResponse {
  return {
    organization_id: ORG_ID,
    reconciled_catalog_version: '2026.1',
    platform_catalog_version: '2026.1',
    eligible: false,
    last_reconciled_at: '2026-08-01T00:00:00Z',
    active_run: null,
    first_reconciliation: false,
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('isCatalogUpgradeAvailable', () => {
  it('is true only when the platform version is ahead of the org version', () => {
    expect(isCatalogUpgradeAvailable(orgStatus({ platform_catalog_version: '2026.2', eligible: true }))).toBe(true)
    // version compare fallback when eligible is not set by the backend
    expect(isCatalogUpgradeAvailable(orgStatus({ platform_catalog_version: '2026.2' }))).toBe(true)
    expect(isCatalogUpgradeAvailable(orgStatus())).toBe(false)
    expect(isCatalogUpgradeAvailable(orgStatus({ platform_catalog_version: '2025.4' }))).toBe(false)
    expect(isCatalogUpgradeAvailable(orgStatus({ platform_catalog_version: null }))).toBe(false)
    expect(isCatalogUpgradeAvailable(null)).toBe(false)
  })

  it('compares versions numerically, not lexically', () => {
    expect(compareCatalogVersions('2026.10', '2026.9')).toBeGreaterThan(0)
    expect(compareCatalogVersions('2026.2', '2026.2')).toBe(0)
    expect(compareCatalogVersions('2025.4', '2026.1')).toBeLessThan(0)
  })
})

describe('CatalogVersionCard', () => {
  it('shows the banner when a newer catalog is available', async () => {
    mockStatus.mockResolvedValue(orgStatus({ platform_catalog_version: '2026.2', eligible: true }))
    render(<CatalogVersionCard organizationId={ORG_ID} />)

    await waitFor(() => {
      expect(screen.getByTestId('catalog-upgrade-banner')).toBeInTheDocument()
    })
    expect(screen.getByText(/Catalog 2026\.2 available/)).toBeInTheDocument()
    expect(screen.getByText(/reconciled to\s+2026\.1/)).toBeInTheDocument()
    expect(mockStatus).toHaveBeenCalledWith(ORG_ID)
  })

  it('hides the banner when the org is up to date', async () => {
    mockStatus.mockResolvedValue(orgStatus())
    render(<CatalogVersionCard organizationId={ORG_ID} />)

    await waitFor(() => {
      expect(screen.getByText(/up to date with the platform catalog/)).toBeInTheDocument()
    })
    expect(screen.queryByTestId('catalog-upgrade-banner')).not.toBeInTheDocument()
  })

  it('links the org reconciliation guide', async () => {
    mockStatus.mockResolvedValue(orgStatus())
    render(<CatalogVersionCard organizationId={ORG_ID} />)

    await waitFor(() => {
      const link = screen.getByRole('link', { name: /organisation catalog reconciliation guide/ })
      expect(link).toHaveAttribute('href', expect.stringContaining('docs/user/org-catalog-reconciliation.md'))
    })
  })

  it('renders nothing when the status endpoint is unavailable', async () => {
    mockStatus.mockRejectedValue(new Error('403'))
    const { container } = render(<CatalogVersionCard organizationId={ORG_ID} />)

    await waitFor(() => {
      expect(container).toBeEmptyDOMElement()
    })
  })
})
