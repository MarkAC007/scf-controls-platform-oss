/**
 * CatalogChangelogPage: renders the org changelog with the deprecated-badge
 * explainer and doc links; empty and error states degrade gracefully.
 */
import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import CatalogChangelogPage from '../CatalogChangelogPage'
import { getOrgCatalogChangelog } from '../../data/catalogUpgradeApi'
import type { OrgChangelogResponse } from '../../types/catalogUpgrade'

vi.mock('../../data/catalogUpgradeApi', () => ({
  getOrgCatalogChangelog: vi.fn(),
}))

const mockChangelog = vi.mocked(getOrgCatalogChangelog)

const ORG_ID = 'org-1'

// Fixture keys deliberately avoid the real SCF `XXX-NN` id shape.
function changelog(): OrgChangelogResponse {
  return {
    organization_id: ORG_ID,
    entries: [
      {
        version: '2026.2',
        applied_at: '2026-08-15T09:00:00Z',
        entity: 'controls',
        change_class: 'deprecated',
        key: 'ctrl-legacy',
        name: 'Legacy Control',
        summary: 'Retired; superseded by ctrl-successor',
      },
      {
        version: '2026.2',
        applied_at: '2026-08-15T09:00:00Z',
        entity: 'controls',
        change_class: 'added',
        key: 'ctrl-successor',
        name: 'Successor Control',
        summary: null,
      },
    ],
    total: 2,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('CatalogChangelogPage', () => {
  it('renders changelog entries with change-class labels', async () => {
    mockChangelog.mockResolvedValue(changelog())
    render(<CatalogChangelogPage organizationId={ORG_ID} />)

    await waitFor(() => {
      expect(screen.getByTestId('changelog-table')).toBeInTheDocument()
    })
    expect(screen.getByText('ctrl-legacy')).toBeInTheDocument()
    expect(screen.getByText('ctrl-successor')).toBeInTheDocument()
    expect(screen.getAllByText('Deprecated').length).toBeGreaterThan(0)
    expect(screen.getByText('Added')).toBeInTheDocument()
    expect(screen.getByText(/Showing 2 of 2/)).toBeInTheDocument()
    expect(mockChangelog).toHaveBeenCalledWith(ORG_ID, { limit: 50, offset: 0 })
  })

  it('always shows the deprecated-badge explainer with links to both docs', async () => {
    mockChangelog.mockResolvedValue({ organization_id: ORG_ID, entries: [], total: 0 })
    render(<CatalogChangelogPage organizationId={ORG_ID} />)

    await waitFor(() => {
      expect(screen.getByTestId('changelog-empty')).toBeInTheDocument()
    })
    expect(screen.getByTestId('deprecated-badge-explainer')).toBeInTheDocument()
    expect(
      screen.getByRole('link', { name: /organisation catalog reconciliation guide/ })
    ).toHaveAttribute('href', expect.stringContaining('docs.scfcontrolsplatform.app/user-guide/catalog-updates'))
    expect(
      screen.getByRole('link', { name: /platform catalog upgrade runbook/ })
    ).toHaveAttribute('href', expect.stringContaining('docs.scfcontrolsplatform.app/admin-guide/platform-catalog-upgrade'))
  })

  it('surfaces load errors', async () => {
    mockChangelog.mockRejectedValue(new Error('boom'))
    render(<CatalogChangelogPage organizationId={ORG_ID} />)

    await waitFor(() => {
      expect(screen.getByText('boom')).toBeInTheDocument()
    })
  })
})
