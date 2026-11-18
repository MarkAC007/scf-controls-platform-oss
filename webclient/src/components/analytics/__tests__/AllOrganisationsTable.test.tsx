// SPDX-License-Identifier: AGPL-3.0-only
/** The "All organisations" table (phase 3 B3): one row per organisation, from the server's counts. */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AllOrganisationsTable from '../AllOrganisationsTable'
import type { PortfolioItem } from '../../../data/securityObjectivesApi'

const api = vi.hoisted(() => ({ getIsmsPortfolio: vi.fn() }))
vi.mock('../../../data/securityObjectivesApi', () => api)

function item(over: Partial<PortfolioItem> = {}): PortfolioItem {
  return {
    organization_id: 'org-a', organization_name: 'Acme Ltd',
    objectives: 7, on_track: 3, at_risk: 1, off_track: 2, needs_attention: 1,
    in_progress: 0, proposed: 1, not_measured: 0, overdue: 0, insufficient_data: 0, achieved: 0, missed: 0,
    last_measured_on: '2026-11-18', stale: false,
    ...over,
  }
}

function renderTable(onOpen = vi.fn(), current = 'org-a') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  render(
    <QueryClientProvider client={client}>
      <AllOrganisationsTable currentOrganizationId={current} onOpen={onOpen} />
    </QueryClientProvider>,
  )
  return onOpen
}

describe('AllOrganisationsTable', () => {
  beforeEach(() => vi.clearAllMocks())

  it('is a captioned table with column headers and one row per organisation', async () => {
    api.getIsmsPortfolio.mockResolvedValue({
      items: [item(), item({ organization_id: 'org-b', organization_name: 'Beta plc', on_track: 0, off_track: 0 })],
      generated_at: '2026-11-18T09:00:00Z',
    })
    renderTable()
    const table = await screen.findByRole('table', { name: /Security objectives by organisation/ })
    expect(api.getIsmsPortfolio).toHaveBeenCalledWith()
    const headers = within(table).getAllByRole('columnheader').map((h) => h.textContent)
    expect(headers).toEqual(['Organisation', 'Objectives', 'On track', 'At risk', 'Off track', 'Needs attention', 'Last measured', 'Open'])
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(2)
    const cells = within(rows[0]).getAllByRole('cell').map((c) => c.textContent)
    expect(cells.slice(0, 6)).toEqual(['7', '3', '1', '2', '1', '18 Nov 2026'])
    // The organisation's name is a row header.
    expect(within(rows[1]).getByRole('rowheader')).toHaveTextContent('Beta plc')
  })

  it('inks a non-zero status count with its status, and leaves a zero plain', async () => {
    api.getIsmsPortfolio.mockResolvedValue({ items: [item({ at_risk: 0 })], generated_at: '2026-11-18T09:00:00Z' })
    renderTable()
    const row = (await screen.findAllByRole('row'))[1]
    const [, onTrack, atRisk, offTrack, attention] = within(row).getAllByRole('cell')
    expect(onTrack.className).toContain('ismsm-pf-count--on_track')
    expect(offTrack.className).toContain('ismsm-pf-count--off_track')
    expect(attention.className).toContain('ismsm-pf-count--needs_attention')
    expect(atRisk.className).not.toContain('ismsm-pf-count--')
  })

  it('marks the current organisation', async () => {
    api.getIsmsPortfolio.mockResolvedValue({
      items: [item({ organization_id: 'org-a' }), item({ organization_id: 'org-b', organization_name: 'Beta plc' })],
      generated_at: '2026-11-18T09:00:00Z',
    })
    renderTable(vi.fn(), 'org-b')
    const rows = (await screen.findAllByRole('row')).slice(1)
    expect(rows[0]).not.toHaveAttribute('aria-current')
    expect(rows[1]).toHaveAttribute('aria-current', 'true')
    expect(within(rows[1]).getByText('Current')).toBeInTheDocument()
    expect(within(rows[0]).queryByText('Current')).toBeNull()
  })

  it('says quietly when an organisation is stale, with or without a stored measurement', async () => {
    api.getIsmsPortfolio.mockResolvedValue({
      items: [
        item({ organization_id: 'org-a', last_measured_on: '2026-11-02', stale: true }),
        item({ organization_id: 'org-b', organization_name: 'Beta plc', last_measured_on: null, stale: true }),
        item({ organization_id: 'org-c', organization_name: 'Gamma', stale: false }),
      ],
      generated_at: '2026-11-18T09:00:00Z',
    })
    renderTable()
    const rows = (await screen.findAllByRole('row')).slice(1)
    expect(within(rows[0]).getByText('Not measured since 2 Nov 2026')).toBeInTheDocument()
    expect(within(rows[1]).getByText('No stored measurement yet')).toBeInTheDocument()
    expect(within(rows[2]).queryByText(/Not measured since|No stored measurement/)).toBeNull()
  })

  it('opens an organisation with its button', async () => {
    api.getIsmsPortfolio.mockResolvedValue({
      items: [item(), item({ organization_id: 'org-b', organization_name: 'Beta plc' })],
      generated_at: '2026-11-18T09:00:00Z',
    })
    const onOpen = renderTable()
    fireEvent.click(await screen.findByRole('button', { name: 'Open Beta plc' }))
    expect(onOpen).toHaveBeenCalledWith('org-b')
  })

  it('shows loading, then an error with a retry', async () => {
    api.getIsmsPortfolio.mockRejectedValueOnce(new Error('boom')).mockResolvedValue({ items: [item()], generated_at: '2026-11-18T09:00:00Z' })
    renderTable()
    expect(screen.getByText('Loading organisations…')).toBeInTheDocument()
    expect(await screen.findByRole('alert')).toHaveTextContent("Could not load the organisations' summaries.")
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('table')).toBeInTheDocument()
  })

  it('says so when there is nothing to show', async () => {
    api.getIsmsPortfolio.mockResolvedValue({ items: [], generated_at: '2026-11-18T09:00:00Z' })
    renderTable()
    expect(await screen.findByText('No organisations to show.')).toBeInTheDocument()
    expect(screen.queryByRole('table')).toBeNull()
  })
})
