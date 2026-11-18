// SPDX-License-Identifier: AGPL-3.0-only
/** The dashboard's ISMS metrics summary: the five figures, each a link into ISMS metrics. */
import { render, screen, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import IsmsSummaryPanel from '../IsmsSummaryPanel'
import { list, objective, ORG, reading } from '../../isms/objectives/__tests__/fixtures'

const api = vi.hoisted(() => ({ getIsmsMetrics: vi.fn() }))
vi.mock('../../../data/securityObjectivesApi', () => api)

// Five different figures, so a tile showing the wrong count cannot pass.
const PAYLOAD = list(
  [objective(), objective({ id: 'obj-2', ref: 'OBJ-02', reading: reading({ status: 'off_track' }) })],
  { objectives: 18, on_track: 1, at_risk: 2, off_track: 5, needs_attention: 10 },
)

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  render(
    <QueryClientProvider client={client}>
      <IsmsSummaryPanel organizationId={ORG} />
    </QueryClientProvider>,
  )
}

const strip = () => screen.getByRole('group', { name: 'Objectives summary' })
const figure = (key: string) => within(strip()).getByTestId(`ismsm-kpi-${key}`)
const query = (el: HTMLElement) => new URLSearchParams((el as HTMLAnchorElement).getAttribute('href')!.split('?')[1])

describe('IsmsSummaryPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.history.replaceState(null, '', '/?tab=dashboard')
  })

  it('reads the metrics of the organisation it is given', async () => {
    api.getIsmsMetrics.mockResolvedValue(PAYLOAD)
    renderPanel()
    await screen.findByRole('group', { name: 'Objectives summary' })
    expect(api.getIsmsMetrics).toHaveBeenCalledWith(ORG)
  })

  it('shows the five figures of the server summary under its own heading', async () => {
    api.getIsmsMetrics.mockResolvedValue(PAYLOAD)
    renderPanel()
    await screen.findByRole('group', { name: 'Objectives summary' })
    const region = screen.getByRole('region', { name: 'ISMS metrics' })
    expect(within(region).getByRole('group', { name: 'Objectives summary' })).toBeInTheDocument()
    const shown = ['objectives', 'on-track', 'at-risk', 'off-track', 'needs-attention']
      .map((key) => figure(key).querySelector('.kpi-value')!.textContent)
    expect(shown).toEqual(['18', '1', '2', '5', '10'])
  })

  it('links each figure to ISMS metrics on the status it counts, and the total to the whole list', async () => {
    api.getIsmsMetrics.mockResolvedValue(PAYLOAD)
    renderPanel()
    await screen.findByRole('group', { name: 'Objectives summary' })
    const statuses: Record<string, string | null> = {
      objectives: null, 'on-track': 'on_track', 'at-risk': 'at_risk', 'off-track': 'off_track', 'needs-attention': 'needs_attention',
    }
    for (const [key, status] of Object.entries(statuses)) {
      const link = figure(key)
      expect(link.tagName).toBe('A')
      const q = query(link)
      expect(q.get('tab')).toBe('capability-posture')
      expect(q.get('view')).toBe('isms-metrics')
      expect(q.get('status')).toBe(status)
    }
  })

  it('offers a way into ISMS metrics that carries no filter', async () => {
    api.getIsmsMetrics.mockResolvedValue(PAYLOAD)
    renderPanel()
    const open = screen.getByRole('link', { name: /Open ISMS metrics/ })
    const q = query(open)
    expect(q.get('view')).toBe('isms-metrics')
    expect(q.get('status')).toBeNull()
  })

  it('says when the metrics were measured', async () => {
    api.getIsmsMetrics.mockResolvedValue(PAYLOAD)
    renderPanel()
    await screen.findByRole('group', { name: 'Objectives summary' })
    expect(screen.getByText(/^Measured /)).toBeInTheDocument()
  })

  it('says so when there is no objective, and shows no figures', async () => {
    api.getIsmsMetrics.mockResolvedValue(list([], { objectives: 0, on_track: 0, at_risk: 0, off_track: 0, needs_attention: 0 }))
    renderPanel()
    expect(await screen.findByText(/No security objectives yet/)).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Objectives summary' })).not.toBeInTheDocument()
  })

  it('reports a failed read instead of showing zeros', async () => {
    api.getIsmsMetrics.mockRejectedValue(new Error('403 Forbidden'))
    renderPanel()
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Could not load the ISMS metrics. 403 Forbidden')
    expect(screen.queryByRole('group', { name: 'Objectives summary' })).not.toBeInTheDocument()
  })
})
