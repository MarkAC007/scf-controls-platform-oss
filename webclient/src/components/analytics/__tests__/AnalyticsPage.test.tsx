// SPDX-License-Identifier: AGPL-3.0-only
/** The Analytics tab choice follows the URL parameter `view`. */
import { act, fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AnalyticsPage from '../AnalyticsPage'

const dashboard = vi.hoisted(() => vi.fn())
vi.mock('../IsmsMetricsDashboard', () => ({
  default: (props: { filters: unknown; onFilters: (f: unknown) => void }) => {
    dashboard(props)
    return <div data-testid="isms-metrics" data-filters={JSON.stringify(props.filters)} />
  },
}))
vi.mock('../MetricDetail', () => ({
  default: ({ objectiveId }: { objectiveId: string }) => <div data-testid="metric-detail" data-id={objectiveId} />,
}))
vi.mock('../../CapabilityPosture', () => ({ default: () => <div data-testid="capability-posture" /> }))
const allOrgs = vi.hoisted(() => vi.fn())
vi.mock('../AllOrganisationsTable', () => ({
  default: (props: { currentOrganizationId: string; onOpen: (id: string) => void }) => {
    allOrgs(props)
    return <div data-testid="all-organisations" data-current={props.currentOrganizationId} />
  },
}))

const TWO_ORGS = [{ id: 'org-one', name: 'One' }, { id: 'org-two', name: 'Two' }]

const OBJ_1 = '9b2f0c1e-4a7d-4e2b-8c3f-1d2e3f4a5b6c'
const OBJ_2 = '0a1b2c3d-4e5f-4a6b-9c7d-8e9f0a1b2c3d'

describe('AnalyticsPage', () => {
  beforeEach(() => {
    window.history.replaceState(null, '', '/')
  })

  it('opens on ISMS metrics when the URL names no view', () => {
    render(<AnalyticsPage organizationId="org-one" />)
    expect(screen.getByRole('tab', { name: 'ISMS metrics' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByTestId('isms-metrics')).toBeInTheDocument()
    expect(window.location.search).toBe('')
  })

  it('opens on the view the URL names', () => {
    window.history.replaceState(null, '', '/?tab=capability-posture&view=capability-posture')
    render(<AnalyticsPage organizationId="org-one" />)
    expect(screen.getByRole('tab', { name: 'Capability posture' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByTestId('capability-posture')).toBeInTheDocument()
  })

  it('writes the view on a click without adding a history entry', () => {
    window.history.replaceState(null, '', '/?tab=capability-posture')
    const before = window.history.length
    render(<AnalyticsPage organizationId="org-one" />)
    fireEvent.click(screen.getByRole('tab', { name: 'Capability posture' }))
    expect(new URLSearchParams(window.location.search).get('view')).toBe('capability-posture')
    expect(window.history.length).toBe(before)
    expect(screen.getByTestId('capability-posture')).toBeInTheDocument()
  })

  it('follows Back and Forward', () => {
    window.history.replaceState(null, '', '/?tab=capability-posture&view=isms-metrics')
    render(<AnalyticsPage organizationId="org-one" />)
    window.history.replaceState(null, '', '/?tab=capability-posture&view=capability-posture')
    fireEvent(window, new PopStateEvent('popstate'))
    expect(screen.getByTestId('capability-posture')).toBeInTheDocument()
  })

  it('stays on the dashboard when the URL names something that is not an objective id', () => {
    window.history.replaceState(null, '', '/?tab=capability-posture&view=isms-metrics&metric=..%2F..%2Fx')
    render(<AnalyticsPage organizationId="org-one" />)
    expect(screen.queryByTestId('metric-detail')).toBeNull()
    expect(screen.getByTestId('isms-metrics')).toBeInTheDocument()
  })

  it('opens the metric detail the URL names, and again on reload', () => {
    window.history.replaceState(null, '', `/?tab=capability-posture&view=isms-metrics&metric=${OBJ_1}`)
    const { unmount } = render(<AnalyticsPage organizationId="org-one" />)
    expect(screen.getByTestId('metric-detail')).toHaveAttribute('data-id', OBJ_1)
    expect(screen.queryByTestId('isms-metrics')).toBeNull()
    // A reload is a fresh mount on the same URL.
    unmount()
    render(<AnalyticsPage organizationId="org-one" />)
    expect(screen.getByTestId('metric-detail')).toHaveAttribute('data-id', OBJ_1)
  })

  it('follows Back from the detail to the dashboard, and Forward again', () => {
    window.history.replaceState(null, '', `/?tab=capability-posture&view=isms-metrics&metric=${OBJ_1}`)
    render(<AnalyticsPage organizationId="org-one" />)
    window.history.replaceState(null, '', '/?tab=capability-posture&view=isms-metrics')
    fireEvent(window, new PopStateEvent('popstate'))
    expect(screen.getByTestId('isms-metrics')).toBeInTheDocument()
    window.history.replaceState(null, '', `/?tab=capability-posture&view=isms-metrics&metric=${OBJ_2}`)
    fireEvent(window, new PopStateEvent('popstate'))
    expect(screen.getByTestId('metric-detail')).toHaveAttribute('data-id', OBJ_2)
  })

  it('closes the detail when a sub-tab is chosen', () => {
    window.history.replaceState(null, '', `/?tab=capability-posture&view=isms-metrics&metric=${OBJ_1}`)
    render(<AnalyticsPage organizationId="org-one" />)
    fireEvent.click(screen.getByRole('tab', { name: 'ISMS metrics' }))
    expect(screen.getByTestId('isms-metrics')).toBeInTheDocument()
    expect(new URLSearchParams(window.location.search).get('metric')).toBeNull()
  })

  it('passes the URL filters to the dashboard and writes changes without a history entry', () => {
    window.history.replaceState(null, '', '/?tab=capability-posture&view=isms-metrics&team=t1&clause=6')
    const before = window.history.length
    render(<AnalyticsPage organizationId="org-one" />)
    expect(JSON.parse(screen.getByTestId('isms-metrics').dataset.filters!)).toEqual({ team: 't1', clause: '6', status: '' })
    const { onFilters } = dashboard.mock.lastCall![0]
    act(() => onFilters({ team: '', clause: '6', status: 'off_track' }))
    const p = new URLSearchParams(window.location.search)
    expect(p.get('team')).toBeNull()
    expect(p.get('clause')).toBe('6')
    expect(p.get('status')).toBe('off_track')
    expect(window.history.length).toBe(before)
    expect(JSON.parse(screen.getByTestId('isms-metrics').dataset.filters!)).toEqual({ team: '', clause: '6', status: 'off_track' })
  })

  describe('All organisations (phase 3 B3)', () => {
    it('hides the sub-tab with one organisation, even when the URL names it', () => {
      window.history.replaceState(null, '', '/?tab=capability-posture&view=all-organisations')
      render(<AnalyticsPage organizationId="org-one" organizations={[{ id: 'org-one', name: 'One' }]} />)
      expect(screen.queryByRole('tab', { name: 'All organisations' })).toBeNull()
      expect(screen.queryByTestId('all-organisations')).toBeNull()
      expect(screen.getByTestId('isms-metrics')).toBeInTheDocument()
    })

    it('shows the sub-tab with two organisations and opens the table from it', () => {
      window.history.replaceState(null, '', '/?tab=capability-posture')
      render(<AnalyticsPage organizationId="org-one" organizations={TWO_ORGS} />)
      fireEvent.click(screen.getByRole('tab', { name: 'All organisations' }))
      expect(screen.getByTestId('all-organisations')).toHaveAttribute('data-current', 'org-one')
      expect(new URLSearchParams(window.location.search).get('view')).toBe('all-organisations')
    })

    it('"Open" switches organisation and opens ISMS metrics', async () => {
      window.history.replaceState(null, '', '/?tab=capability-posture&view=all-organisations')
      const onSwitch = vi.fn().mockResolvedValue(undefined)
      render(<AnalyticsPage organizationId="org-one" organizations={TWO_ORGS} onSwitchOrganization={onSwitch} />)
      await act(async () => allOrgs.mock.lastCall![0].onOpen('org-two'))
      expect(onSwitch).toHaveBeenCalledWith('org-two')
      expect(new URLSearchParams(window.location.search).get('view')).toBe('isms-metrics')
      expect(screen.getByTestId('isms-metrics')).toBeInTheDocument()
    })

    it('"Open" on the current organisation only changes the view', async () => {
      window.history.replaceState(null, '', '/?tab=capability-posture&view=all-organisations')
      const onSwitch = vi.fn().mockResolvedValue(undefined)
      render(<AnalyticsPage organizationId="org-one" organizations={TWO_ORGS} onSwitchOrganization={onSwitch} />)
      await act(async () => allOrgs.mock.lastCall![0].onOpen('org-one'))
      expect(onSwitch).not.toHaveBeenCalled()
      expect(screen.getByTestId('isms-metrics')).toBeInTheDocument()
    })

    it('stays on the table when the switch fails', async () => {
      window.history.replaceState(null, '', '/?tab=capability-posture&view=all-organisations')
      const onSwitch = vi.fn().mockRejectedValue(new Error('You do not have access to this organization'))
      render(<AnalyticsPage organizationId="org-one" organizations={TWO_ORGS} onSwitchOrganization={onSwitch} />)
      await act(async () => allOrgs.mock.lastCall![0].onOpen('org-two'))
      expect(screen.getByTestId('all-organisations')).toBeInTheDocument()
      expect(new URLSearchParams(window.location.search).get('view')).toBe('all-organisations')
    })
  })
})
