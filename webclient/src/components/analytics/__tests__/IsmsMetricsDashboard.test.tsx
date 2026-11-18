// SPDX-License-Identifier: AGPL-3.0-only
/** The dashboard strip and tiles render from a payload. */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import IsmsMetricsDashboard from '../IsmsMetricsDashboard'
import type { MetricFilters } from '../ismsMetricFilters'
import { list, metric, objective, ORG, reading } from '../../isms/objectives/__tests__/fixtures'

const api = vi.hoisted(() => ({ getIsmsMetrics: vi.fn() }))
vi.mock('../../../data/securityObjectivesApi', () => api)
const role = vi.hoisted(() => ({ editor: true, admin: true }))
vi.mock('../../../hooks/useHasOrgRole', () => ({
  useIsOrgEditor: () => role.editor,
  useHasOrgRole: () => role.admin,
}))
vi.mock('../../isms/objectives/ObjectiveFormModal', () => ({ default: () => <div data-testid="objective-form" /> }))

const PAYLOAD = list([
  objective(),
  objective({
    id: 'obj-2', ref: 'OBJ-02', name: 'Test our response capability', owning_team_id: null, owning_team_name: null,
    lifecycle_status: 'proposed', iso_clauses: ['8.1'],
    metric: metric({ unit: 'count', target_value: null, warn_value: null, target_text: 'at least 2 passed tests a year' }),
    reading: reading({ status: 'proposed', value: 2, value_text: '2', population: '', segments: [], target_label: '' }),
  }),
  objective({
    id: 'obj-9', ref: 'OBJ-09', name: 'Train everyone in security awareness',
    metric: metric({ kind: 'logged', measure_key: null, frequency: 'quarterly', target_value: 95, warn_value: 85 }),
    reading: reading({ status: 'on_track', value: 96, value_text: '96%', logged_by_name: 'People Lead', measured_on: '2026-11-03', target_label: 'at least 95% · warning below 85%' }),
  }),
  objective({
    id: 'obj-12', ref: 'OBJ-12', name: 'Achieve certification', objective_type: 'achieve', due_date: '2027-03-31',
    metric: metric({ kind: 'milestone', unit: null, direction: null, target_value: null, warn_value: null }),
    reading: reading({ status: 'in_progress', value: 0, value_text: 'Not done', days_left: 133, population: '', segments: [] }),
  }),
  objective({
    id: 'obj-13', ref: 'OBJ-13', name: 'Enforce MFA', source: 'migrated', metric: null, owning_team_name: null,
    reading: reading({ status: 'not_measured', value: null, value_text: '—', population: '', segments: [], target_label: '' }),
  }),
])

function renderDashboard(filters?: MetricFilters, onFilters = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  render(
    <QueryClientProvider client={client}>
      <IsmsMetricsDashboard organizationId={ORG} filters={filters} onFilters={onFilters} />
    </QueryClientProvider>,
  )
  return { onFilters }
}

const NONE: MetricFilters = { team: '', clause: '', status: '' }
const tiles = () => screen.queryAllByTestId(/^metric-tile-/).map((t) => t.dataset.testid!.replace('metric-tile-', ''))
const group = (name: string) => screen.getByRole('group', { name })
const pill = (groupName: string, name: string | RegExp) => within(group(groupName)).getByRole('button', { name })
const pressed = (groupName: string) =>
  within(group(groupName)).getAllByRole('button').filter((b) => b.getAttribute('aria-pressed') === 'true')
const kpi = (key: string) => within(screen.getByRole('group', { name: 'Objectives summary' })).getByTestId(`ismsm-kpi-${key}`)

describe('IsmsMetricsDashboard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    role.editor = true
    window.history.replaceState(null, '', '/?tab=capability-posture')
  })

  it('renders the summary strip from the payload', async () => {
    api.getIsmsMetrics.mockResolvedValue(PAYLOAD)
    renderDashboard()
    const strip = await screen.findByRole('group', { name: 'Objectives summary' })
    const card = (key: string) => within(strip).getByTestId(`ismsm-kpi-${key}`)
    expect(card('objectives')).toHaveTextContent('14')
    expect(card('objectives')).toHaveTextContent('9 measured · 2 proposed · 1 no metric')
    expect(card('on-track')).toHaveTextContent('plus 2 in progress, not yet due')
    expect(card('at-risk')).toHaveTextContent('inside the warning margin')
    expect(card('off-track')).toHaveTextContent('3')
    expect(card('needs-attention')).toHaveTextContent('2 to confirm · 1 overdue · 1 thin data · 1 no metric')
    expect(card('on-track').querySelector('svg[aria-hidden="true"]')).not.toBeNull()
  })

  it('adds achieved and missed to the Objectives card only when there are any', async () => {
    api.getIsmsMetrics.mockResolvedValue({ ...PAYLOAD, summary: { ...PAYLOAD.summary, achieved: 2, missed: 0 } })
    renderDashboard()
    const strip = await screen.findByRole('group', { name: 'Objectives summary' })
    const card = within(strip).getByTestId('ismsm-kpi-objectives')
    expect(card).toHaveTextContent('9 measured · 2 proposed · 1 no metric · 2 achieved')
    expect(card).not.toHaveTextContent('missed')
  })

  it('shows a closed objective with its chip and value, and no judgement against the target', async () => {
    api.getIsmsMetrics.mockResolvedValue(list([
      objective({
        id: 'obj-9', ref: 'OBJ-09', name: 'Train everyone in security awareness', lifecycle_status: 'missed',
        metric: metric({ kind: 'logged', measure_key: null, frequency: 'quarterly', target_value: 95, warn_value: 85 }),
        reading: reading({ status: 'missed', value: 80, value_text: '80%', measured_on: '2026-10-01', target_label: 'at least 95% · warning below 85%' }),
      }),
    ]))
    renderDashboard()
    const tile = await screen.findByTestId('metric-tile-OBJ-09')
    expect(tile).toHaveClass('ismsm-tile--missed')
    expect(within(tile).getByText('Missed').closest('.obj-status')).toHaveClass('obj-status--missed')
    expect(tile).toHaveTextContent('80%')
    expect(tile).toHaveTextContent('Target was at least 95% · warning below 85%')
    expect(tile).not.toHaveTextContent(/Off track|At risk|On track|beyond|inside the warning/)
  })

  it('renders one tile per objective with its value, target, status and team', async () => {
    api.getIsmsMetrics.mockResolvedValue(PAYLOAD)
    renderDashboard()
    const live = await screen.findByTestId('metric-tile-OBJ-01')
    expect(live).toHaveTextContent('OBJ-01')
    expect(live).toHaveTextContent('Live')
    expect(live).toHaveTextContent('Keep risk within appetite')
    expect(live).toHaveTextContent('14%')
    expect(live).toHaveTextContent('Target at least 100% · warning below 90%')
    expect(live).toHaveTextContent('Off track')
    expect(live).toHaveTextContent('9 of 66 risks · 48 not assessed')
    expect(live).toHaveTextContent('Risk and Compliance')
    expect(live).toHaveTextContent('6.1.3')
    const meter = live.querySelector('.ismsm-meter') as HTMLElement
    expect(meter.style.getPropertyValue('--ismsm-fill')).toBe('14%')
    expect(meter.style.getPropertyValue('--ismsm-target')).toBe('100%')
    // The title is the one link, to the metric detail screen.
    const links = within(live).getAllByRole('link')
    expect(links).toHaveLength(1)
    expect(links[0]).toHaveTextContent('Keep risk within appetite')
    const href = new URLSearchParams(links[0].getAttribute('href')!.split('?')[1])
    expect(href.get('tab')).toBe('capability-posture')
    expect(href.get('view')).toBe('isms-metrics')
    expect(href.get('metric')).toBe('obj-1')

    const proposed = screen.getByTestId('metric-tile-OBJ-02')
    expect(proposed).toHaveTextContent('Suggested target: at least 2 passed tests a year')
    expect(proposed).toHaveTextContent('Proposed · confirm target')
    expect(proposed).toHaveTextContent('No team yet')
    expect(proposed.querySelector('.ismsm-meter')).toBeNull()

    expect(screen.getByTestId('metric-tile-OBJ-09')).toHaveTextContent('Logged')
    expect(screen.getByTestId('metric-tile-OBJ-09')).toHaveTextContent('Logged 3 Nov 2026 by People Lead')
    expect(screen.getByTestId('metric-tile-OBJ-12')).toHaveTextContent('Milestone')
    expect(screen.getByTestId('metric-tile-OBJ-12')).toHaveTextContent('In progress · 133 days left')
    const none = screen.getByTestId('metric-tile-OBJ-13')
    expect(none).toHaveTextContent('No metric')
    expect(none).toHaveTextContent('Choose a metric')
    expect(none).toHaveTextContent('Brought over from the old Objectives 6.2 list')
    expect(none).toHaveTextContent('Not measured')
  })

  it('offers Add objective to an editor and opens the form', async () => {
    api.getIsmsMetrics.mockResolvedValue(PAYLOAD)
    renderDashboard()
    fireEvent.click(await screen.findByRole('button', { name: 'Add objective' }))
    expect(screen.getByTestId('objective-form')).toBeInTheDocument()
  })

  it('offers a viewer no Add objective', async () => {
    role.editor = false
    api.getIsmsMetrics.mockResolvedValue(PAYLOAD)
    renderDashboard()
    await screen.findByTestId('metric-tile-OBJ-01')
    expect(screen.queryByRole('button', { name: 'Add objective' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Open objectives register' })).toBeInTheDocument()
  })

  it('says when there are no objectives', async () => {
    api.getIsmsMetrics.mockResolvedValue(list([], { objectives: 0 }))
    renderDashboard()
    expect(await screen.findByText('No objectives yet')).toBeInTheDocument()
  })

  it('says when the metrics could not be loaded', async () => {
    api.getIsmsMetrics.mockRejectedValue(new Error('Server unavailable'))
    renderDashboard()
    expect(await screen.findByRole('alert')).toHaveTextContent('The ISMS metrics could not be loaded: Server unavailable')
  })

  describe('filters', () => {
    it('narrows tiles and the strip by team', async () => {
      api.getIsmsMetrics.mockResolvedValue(PAYLOAD)
      renderDashboard({ ...NONE, team: 'team-ops' })
      await screen.findByTestId('metric-tile-OBJ-01')
      expect(tiles()).toEqual(['OBJ-01', 'OBJ-09', 'OBJ-12', 'OBJ-13'])
      // The strip now counts the four shown, not the server's 14.
      expect(kpi('objectives')).toHaveTextContent(/^Objectives4/)
      expect(kpi('off-track')).toHaveTextContent(/^Off track1/)
    })

    it('shows a team in the URL that no objective carries as "Unknown team" (review M4)', async () => {
      api.getIsmsMetrics.mockResolvedValue(PAYLOAD)
      renderDashboard({ ...NONE, team: 'team-moved' })
      expect(await screen.findByTestId('ismsm-no-match')).toBeInTheDocument()
      const select = screen.getByLabelText('Team') as HTMLSelectElement
      expect(select.value).toBe('team-moved')
      expect(select.selectedOptions[0]).toHaveTextContent('Unknown team')
      expect([...select.options].filter((o) => o.textContent === 'Unknown team')).toHaveLength(1)
    })

    it('adds no extra option for a team that is listed', async () => {
      api.getIsmsMetrics.mockResolvedValue(PAYLOAD)
      renderDashboard({ ...NONE, team: 'team-ops' })
      await screen.findByTestId('metric-tile-OBJ-01')
      expect(screen.queryByRole('option', { name: 'Unknown team' })).toBeNull()
    })

    it('narrows to objectives with no team', async () => {
      api.getIsmsMetrics.mockResolvedValue(PAYLOAD)
      renderDashboard({ ...NONE, team: 'none' })
      await screen.findByTestId('metric-tile-OBJ-02')
      expect(tiles()).toEqual(['OBJ-02'])
    })

    it('narrows tiles and the strip by clause group', async () => {
      api.getIsmsMetrics.mockResolvedValue(PAYLOAD)
      renderDashboard({ ...NONE, clause: '6' })
      await screen.findByTestId('metric-tile-OBJ-01')
      // OBJ-02 carries 8.1 only; the others carry the fixture's 6.1.3.
      expect(tiles()).toEqual(['OBJ-01', 'OBJ-09', 'OBJ-12', 'OBJ-13'])
      expect(kpi('objectives')).toHaveTextContent(/^Objectives4/)
      // The proposed one left out, so nothing is waiting to be confirmed.
      expect(kpi('needs-attention')).not.toHaveTextContent('to confirm')
    })

    it('narrows the tiles by status but leaves the strip alone', async () => {
      api.getIsmsMetrics.mockResolvedValue(PAYLOAD)
      renderDashboard({ ...NONE, status: 'off_track' })
      await screen.findByTestId('metric-tile-OBJ-01')
      expect(tiles()).toEqual(['OBJ-01'])
      expect(kpi('objectives')).toHaveTextContent(/^Objectives14/)
      expect(kpi('off-track')).toHaveTextContent(/^Off track3/)
    })

    it('reports each change through onFilters', async () => {
      api.getIsmsMetrics.mockResolvedValue(PAYLOAD)
      const { onFilters } = renderDashboard(NONE)
      await screen.findByTestId('metric-tile-OBJ-01')
      fireEvent.change(screen.getByLabelText('Team'), { target: { value: 'team-ops' } })
      expect(onFilters).toHaveBeenLastCalledWith({ team: 'team-ops', clause: '', status: '' })
      fireEvent.click(pill('Clause', 'Annex A'))
      expect(onFilters).toHaveBeenLastCalledWith({ team: '', clause: 'annex-a', status: '' })
      fireEvent.click(pill('Status', 'Needs attention'))
      expect(onFilters).toHaveBeenLastCalledWith({ team: '', clause: '', status: 'needs_attention' })
      expect(screen.queryByRole('button', { name: 'Clear filters' })).toBeNull()
    })

    it('puts the filter row under the summary strip (phase 3, as the mock-up)', async () => {
      api.getIsmsMetrics.mockResolvedValue(PAYLOAD)
      renderDashboard(NONE)
      await screen.findByTestId('metric-tile-OBJ-01')
      const strip = group('Objectives summary')
      const filters = group('Filter objectives')
      expect(strip.compareDocumentPosition(filters) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    })

    it('keeps Team a select, and makes Clause and Status pill groups with All first, labelled as the mock-up (no counts)', async () => {
      api.getIsmsMetrics.mockResolvedValue(PAYLOAD)
      renderDashboard(NONE)
      await screen.findByTestId('metric-tile-OBJ-01')
      expect(screen.getByLabelText('Team').tagName).toBe('SELECT')
      const clause = within(group('Clause')).getAllByRole('button')
      expect(clause.map((b) => b.textContent)).toEqual([
        'All', 'Planning 6', 'Support 7', 'Operation 8', 'Performance 9', 'Improvement 10', 'Annex A',
      ])
      expect(within(group('Status')).getAllByRole('button').map((b) => b.textContent)).toEqual([
        'All', 'On track', 'At risk', 'Off track', 'In progress', 'Needs attention', 'Achieved or missed',
      ])
      for (const b of [...clause, ...within(group('Status')).getAllByRole('button')]) {
        expect(b).toHaveAttribute('type', 'button')
        expect(b).toHaveAttribute('aria-pressed')
      }
    })

    it('presses the pill the URL names, and only that one', async () => {
      api.getIsmsMetrics.mockResolvedValue(PAYLOAD)
      renderDashboard({ ...NONE, clause: '8', status: 'proposed_unknown' })
      await screen.findByTestId('metric-tile-OBJ-02')
      expect(pressed('Clause').map((b) => b.textContent)).toEqual(['Operation 8'])
      // Review F8: a status the dashboard does not know filters nothing, so All is the one pressed.
      expect(pressed('Status').map((b) => b.textContent)).toEqual(['All'])
      expect(screen.getByTestId('metric-tile-OBJ-02')).toBeInTheDocument()
    })

    it('presses All when a filter is unset', async () => {
      api.getIsmsMetrics.mockResolvedValue(PAYLOAD)
      renderDashboard(NONE)
      await screen.findByTestId('metric-tile-OBJ-01')
      expect(pressed('Clause').map((b) => b.textContent)).toEqual(['All'])
      expect(pressed('Status').map((b) => b.textContent)).toEqual(['All'])
    })

    it('All sends the filter back to empty', async () => {
      api.getIsmsMetrics.mockResolvedValue(PAYLOAD)
      const { onFilters } = renderDashboard({ ...NONE, team: 'team-ops', clause: '6', status: 'off_track' })
      await screen.findByTestId('metric-tile-OBJ-01')
      fireEvent.click(pill('Clause', 'All'))
      expect(onFilters).toHaveBeenLastCalledWith({ team: 'team-ops', clause: '', status: 'off_track' })
      fireEvent.click(pill('Status', 'All'))
      expect(onFilters).toHaveBeenLastCalledWith({ team: 'team-ops', clause: '6', status: '' })
    })

    it('says when nothing matches, and clears with one control', async () => {
      api.getIsmsMetrics.mockResolvedValue(PAYLOAD)
      const { onFilters } = renderDashboard({ ...NONE, clause: 'annex-a', status: 'on_track' })
      expect(await screen.findByTestId('ismsm-no-match')).toHaveTextContent('No objectives match these filters')
      expect(tiles()).toEqual([])
      const clears = screen.getAllByRole('button', { name: 'Clear filters' })
      expect(clears).toHaveLength(2)
      fireEvent.click(clears[1])
      expect(onFilters).toHaveBeenLastCalledWith(NONE)
      fireEvent.click(clears[0])
      expect(onFilters).toHaveBeenCalledTimes(2)
    })
  })
})
