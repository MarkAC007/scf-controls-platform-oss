// SPDX-License-Identifier: AGPL-3.0-only
/** Phase 3: the clause and status pills read and write the same URL parameters the selects did. */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AnalyticsPage from '../AnalyticsPage'
import { list, objective } from '../../isms/objectives/__tests__/fixtures'

const api = vi.hoisted(() => ({ getIsmsMetrics: vi.fn() }))
vi.mock('../../../data/securityObjectivesApi', () => api)
vi.mock('../../../hooks/useHasOrgRole', () => ({ useIsOrgEditor: () => false, useHasOrgRole: () => false }))
vi.mock('../../isms/objectives/ObjectiveFormModal', () => ({ default: () => null }))
vi.mock('../MetricDetail', () => ({ default: () => null }))
vi.mock('../../CapabilityPosture', () => ({ default: () => null }))

const pills = (name: string) => within(screen.getByRole('group', { name })).getAllByRole('button')
const pressedText = (name: string) =>
  pills(name).filter((b) => b.getAttribute('aria-pressed') === 'true').map((b) => b.textContent)
const param = (k: string) => new URLSearchParams(window.location.search).get(k)

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  render(
    <QueryClientProvider client={client}>
      <AnalyticsPage organizationId="org-one" />
    </QueryClientProvider>,
  )
}

describe('ISMS metrics filter pills and the URL', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getIsmsMetrics.mockResolvedValue(list([objective()]))
  })

  it('presses the pills the URL names, writes clicks back to it, and All removes the parameter', async () => {
    window.history.replaceState(null, '', '/?tab=capability-posture&view=isms-metrics&clause=6')
    const before = window.history.length
    renderPage()
    await screen.findByTestId('metric-tile-OBJ-01')
    expect(pressedText('Clause')).toEqual(['Planning 6'])
    expect(pressedText('Status')).toEqual(['All'])

    fireEvent.click(within(screen.getByRole('group', { name: 'Status' })).getByRole('button', { name: 'Off track' }))
    expect(param('status')).toBe('off_track')
    expect(param('clause')).toBe('6')
    expect(pressedText('Status')).toEqual(['Off track'])

    fireEvent.click(within(screen.getByRole('group', { name: 'Clause' })).getByRole('button', { name: 'All' }))
    expect(param('clause')).toBeNull()
    expect(param('status')).toBe('off_track')
    expect(pressedText('Clause')).toEqual(['All'])
    // replaceState, as before: no history entry per click.
    expect(window.history.length).toBe(before)
  })
})
