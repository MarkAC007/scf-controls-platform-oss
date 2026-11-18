// SPDX-License-Identifier: AGPL-3.0-only
/** The metric detail screen: links per record kind, the raise-deficiency rules, the log. */
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import MetricDetail, { barItems, deficiencyPrefill, levelWord, recordDestination, segmentKind, segWidth } from '../MetricDetail'
import type { MetricDetail as MetricDetailData } from '../../../data/securityObjectivesApi'
import { metric, objective, ORG, reading, result } from '../../isms/objectives/__tests__/fixtures'

const api = vi.hoisted(() => ({ getMetricDetail: vi.fn() }))
vi.mock('../../../data/securityObjectivesApi', () => api)
const role = vi.hoisted(() => ({ editor: true }))
vi.mock('../../../hooks/useHasOrgRole', () => ({
  useIsOrgEditor: () => role.editor,
  useHasOrgRole: () => role.editor,
}))
const deficiencyForm = vi.hoisted(() => vi.fn())
vi.mock('../../deficiencies/DeficiencyFormModal', () => ({
  default: (props: unknown) => {
    deficiencyForm(props)
    return <div data-testid="deficiency-form" />
  },
}))
vi.mock('../../isms/objectives/EvaluationModal', () => ({ default: () => <div data-testid="evaluation-form" /> }))

function detail(over: Partial<MetricDetailData> = {}): MetricDetailData {
  return {
    objective: objective(),
    results: [result({ id: 'r-2', measured_on: '2026-11-18', value: 14, value_text: '14%', numerator: 9 }), result()],
    metric_versions: [],
    breakdown: {
      title: '9 risks above appetite · 48 not assessed',
      columns: [{ key: 'risk', label: 'Risk' }, { key: 'level', label: 'Residual level' }],
      rows: [
        { key: 'R-BC-1', group: 'above appetite', cells: { risk: 'R-BC-1 Business interruption', level: 'Critical · 20' }, link: { kind: 'risk', key: 'R-BC-1' } },
        { key: 'R-NA-1', group: 'not assessed', cells: { risk: 'R-NA-1 Not yet looked at', level: '—' }, link: { kind: 'risk', key: null } },
        { key: 'X-1', group: '', cells: { risk: 'Plain row', level: '' }, link: null },
      ],
      total: 57,
      register: { kind: 'risk', key: null },
      register_label: 'Open all 57 in the Risk Register',
    },
    change: { delta: 2, days: 60, text: 'up 2 points in the last 60 days, to the last stored measurement' },
    raised_deficiencies: 0,
    ...over,
  }
}

function renderDetail() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(
    <QueryClientProvider client={client}>
      <MetricDetail organizationId={ORG} objectiveId="obj-1" />
    </QueryClientProvider>,
  )
}

const params = (a: HTMLElement) => new URLSearchParams(a.getAttribute('href')!.split('?')[1] ?? '')

describe('recordDestination', () => {
  it('opens each kind’s own record, and its register when the key is null', () => {
    expect(recordDestination({ kind: 'risk', key: 'R-1' })).toEqual({ kind: 'risk', id: 'R-1' })
    expect(recordDestination({ kind: 'risk', key: null })).toEqual({ kind: 'register', of: 'risk' })
    expect(recordDestination({ kind: 'vendor', key: 'v-1' })).toEqual({ kind: 'vendor', id: 'v-1' })
    expect(recordDestination({ kind: 'vendor', key: null })).toEqual({ kind: 'register', of: 'vendor' })
    expect(recordDestination({ kind: 'evidence', key: 'E-HRS-16' })).toEqual({ kind: 'evidence', id: 'E-HRS-16' })
    expect(recordDestination({ kind: 'evidence', key: null })).toEqual({ kind: 'register', of: 'evidence' })
    expect(recordDestination({ kind: 'control', key: 'AST-14' })).toEqual({ kind: 'control', id: 'AST-14' })
    expect(recordDestination({ kind: 'control', key: null })).toEqual({ kind: 'register', of: 'library' })
    expect(recordDestination({ kind: 'capability_test', key: 't-1' })).toEqual({ kind: 'capability-test', id: 't-1' })
    expect(recordDestination({ kind: 'capability_test', key: null })).toEqual({ kind: 'register', of: 'capability-test' })
    expect(recordDestination({ kind: 'deficiency', key: 'd-1' })).toEqual({ kind: 'register', of: 'deficiencies' })
    expect(recordDestination({ kind: 'audit', key: null })).toEqual({ kind: 'register', of: 'audit-programme' })
  })
})

describe('MetricDetail', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    role.editor = true
    window.history.replaceState(null, '', '/?tab=capability-posture&view=isms-metrics&metric=obj-1')
  })

  it('shows the header, value, change, how it is measured and the trend', async () => {
    api.getMetricDetail.mockResolvedValue(detail())
    renderDetail()
    expect(await screen.findByRole('heading', { level: 2 })).toHaveTextContent('OBJ-01 Keep risk within appetite')
    expect(api.getMetricDetail).toHaveBeenCalledWith(ORG, 'obj-1')
    expect(screen.getByText('up 2 points in the last 60 days, to the last stored measurement')).toBeInTheDocument()
    expect(screen.getByText('How this is measured')).toBeInTheDocument()
    expect(screen.getByText('Pat Example, 18 Nov 2026')).toBeInTheDocument()
    expect(screen.getByRole('group', { name: /^Trend of Risks within appetite/ })).toBeInTheDocument()
    const back = screen.getByRole('link', { name: '← Analytics · ISMS metrics' })
    expect(params(back).get('metric')).toBeNull()
    expect(params(back).get('view')).toBe('isms-metrics')
    expect(params(screen.getByRole('link', { name: 'Open objective' })).get('objective')).toBe('obj-1')
  })

  it('shows a residual level as a pill coloured by its level word (design row 5)', async () => {
    renderDetail()
    const cell = within(await screen.findByTestId('breakdown-row-R-BC-1')).getByText('Critical · 20')
    expect(cell.className).toBe('ismsm-lv ismsm-lv--critical')
    // A cell that is not a level stays plain text.
    expect(within(screen.getByTestId('breakdown-row-R-NA-1')).getByText('—').className).not.toContain('ismsm-lv')
    expect(['High · 12', 'medium', 'Low · 2', '—', '', 'Severe'].map(levelWord)).toEqual(['high', 'medium', 'low', '', '', ''])
  })

  it('links each breakdown row into its register, a null key to the register list, and one register link', async () => {
    api.getMetricDetail.mockResolvedValue(detail())
    renderDetail()
    const row = await screen.findByTestId('breakdown-row-R-BC-1')
    const link = within(row).getByRole('link', { name: 'R-BC-1 Business interruption' })
    expect(params(link).get('tab')).toBe('risk-register')
    expect(params(link).get('risk')).toBe('R-BC-1')
    const unkeyed = within(screen.getByTestId('breakdown-row-R-NA-1')).getByRole('link')
    expect(params(unkeyed).get('tab')).toBe('risk-register')
    expect(params(unkeyed).get('risk')).toBeNull()
    expect(within(screen.getByTestId('breakdown-row-X-1')).queryByRole('link')).toBeNull()
    expect(screen.getByText(/Showing 3 of 57/)).toBeInTheDocument()
    const register = screen.getByTestId('breakdown-register')
    expect(register).toHaveTextContent('Open all 57 in the Risk Register')
    expect(params(register).get('tab')).toBe('risk-register')
  })

  it('logs each result with the target in force then, and shows more on request', async () => {
    const many = Array.from({ length: 25 }, (_, i) =>
      result({ id: `r-${i}`, measured_on: `2026-10-${String(i + 1).padStart(2, '0')}`, target_value: i < 5 ? 95 : 100 }))
    api.getMetricDetail.mockResolvedValue(detail({ results: many.reverse() }))
    renderDetail()
    expect(await screen.findAllByTestId('log-row')).toHaveLength(10)
    expect(screen.getAllByTestId('log-row')[0]).toHaveTextContent('25 Oct 2026')
    expect(screen.getAllByTestId('log-row')[0]).toHaveTextContent('8 of 66')
    expect(screen.getAllByTestId('log-row')[0]).toHaveTextContent('≥ 100%')
    expect(screen.getAllByTestId('log-row')[0]).toHaveTextContent('Computed')
    fireEvent.click(screen.getByRole('button', { name: 'Show more (15 more)' }))
    expect(screen.getAllByTestId('log-row')).toHaveLength(25)
    expect(screen.getAllByTestId('log-row')[24]).toHaveTextContent('≥ 95%')
    expect(screen.queryByRole('button', { name: /Show more/ })).toBeNull()
  })

  it('offers Raise deficiency to an editor on an off-track objective, pre-filled, saving nothing itself', async () => {
    api.getMetricDetail.mockResolvedValue(detail())
    renderDetail()
    fireEvent.click(await screen.findByRole('button', { name: 'Raise deficiency' }))
    expect(screen.getByTestId('deficiency-form')).toBeInTheDocument()
    const props = deficiencyForm.mock.lastCall![0] as { organizationId: string; prefill: unknown }
    expect(props.organizationId).toBe(ORG)
    expect(props.prefill).toEqual({
      title: 'Objective OBJ-01 off track: Keep risk within appetite',
      description:
        'Risks within appetite (%) reads 14% (9 of 66 risks), measured 18 Nov 2026, against a target of ' +
        'at least 100% · warning below 90%. Raised from the ISMS metrics detail screen.',
      priority: 'high',
      source: 'security_objective',
      source_ref: 'obj-1',
    })
  })

  it('offers no Raise deficiency when the objective is not off track', async () => {
    api.getMetricDetail.mockResolvedValue(detail({ objective: objective({ reading: reading({ status: 'at_risk' }) }) }))
    renderDetail()
    await screen.findByRole('heading', { level: 2 })
    expect(screen.queryByRole('button', { name: 'Raise deficiency' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Record evaluation' })).toBeInTheDocument()
  })

  it('offers a viewer neither Raise deficiency nor Record evaluation', async () => {
    role.editor = false
    api.getMetricDetail.mockResolvedValue(detail())
    renderDetail()
    await screen.findByRole('heading', { level: 2 })
    expect(screen.queryByRole('button', { name: 'Raise deficiency' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Record evaluation' })).toBeNull()
    expect(screen.getByRole('link', { name: 'Open objective' })).toBeInTheDocument()
  })

  it('says when deficiencies were already raised from it, with a link to Deficiencies', async () => {
    api.getMetricDetail.mockResolvedValue(detail({ raised_deficiencies: 1 }))
    renderDetail()
    const line = await screen.findByTestId('already-raised')
    expect(line).toHaveTextContent('1 open deficiency already raised')
    expect(params(within(line).getByRole('link')).get('tab')).toBe('deficiencies')
  })

  it('reads the detail again when the form closes, so the already-raised line follows a raise', async () => {
    api.getMetricDetail.mockResolvedValueOnce(detail()).mockResolvedValue(detail({ raised_deficiencies: 1 }))
    renderDetail()
    fireEvent.click(await screen.findByRole('button', { name: 'Raise deficiency' }))
    expect(screen.queryByTestId('already-raised')).toBeNull()
    const { onClose } = deficiencyForm.mock.lastCall![0] as { onClose: () => void }
    act(() => onClose())
    expect(screen.queryByTestId('deficiency-form')).toBeNull()
    expect(await screen.findByTestId('already-raised')).toHaveTextContent('1 open deficiency already raised')
    expect(api.getMetricDetail).toHaveBeenCalledTimes(2)
  })

  it('has no already-raised line when none were', async () => {
    api.getMetricDetail.mockResolvedValue(detail())
    renderDetail()
    await screen.findByRole('heading', { level: 2 })
    expect(screen.queryByTestId('already-raised')).toBeNull()
  })

  it('builds the prefill from the reading', () => {
    const p = deficiencyPrefill(objective({ name: 'x'.repeat(300) }))
    expect(p.title!.length).toBe(255)
  })

  describe('pace (phase 3)', () => {
    const PACE = {
      state: 'behind' as const, label: 'Behind pace', expected_value: 52.4, expected_text: '52%',
      baseline_on: '2026-09-30', baseline_value: 12, due_date: '2027-06-30', target_value: 80,
    }

    it('says what was expected by today, in the server\'s words, and draws the pace line', async () => {
      const o = objective({ objective_type: 'achieve', due_date: '2027-06-30', reading: reading({ status: 'in_progress', pace: PACE }) })
      api.getMetricDetail.mockResolvedValue(detail({ objective: o }))
      renderDetail()
      expect(await screen.findByTestId('detail-pace')).toHaveTextContent('Expected by today: 52%. Behind pace.')
      expect(screen.getByTestId('trend-pace')).toBeInTheDocument()
      // Pace never changes the status chip.
      expect(screen.getAllByText('In progress').length).toBeGreaterThan(0)
    })

    it("review F5: words both numbers together when the server sends the pace's value_text", async () => {
      const pace = { ...PACE, expected_value: 50.9, expected_text: '50.9%', value_text: '50.6%' }
      const o = objective({ objective_type: 'achieve', due_date: '2027-06-30', reading: reading({ status: 'in_progress', value: 50.6, value_text: '51%', pace }) })
      api.getMetricDetail.mockResolvedValue(detail({ objective: o }))
      renderDetail()
      const line = await screen.findByTestId('detail-pace')
      expect(line).toHaveTextContent(/^Today 50\.6%, expected 50\.9%\. Behind pace\.$/)
      expect(line.textContent).not.toMatch(/Expected by today/)
    })

    it('says nothing about pace when the server sends none, or an older server omits it', async () => {
      const { pace: _p, ...older } = reading()
      for (const r of [reading({ pace: null }), older]) {
        api.getMetricDetail.mockResolvedValue(detail({ objective: objective({ reading: r }) }))
        const { unmount } = renderDetail()
        await screen.findByRole('heading', { level: 2 })
        expect(screen.queryByTestId('detail-pace')).toBeNull()
        expect(screen.queryByTestId('trend-pace')).toBeNull()
        unmount()
      }
    })
  })

  describe('segment bar (review S1, design rows 3 and 6)', () => {
    const width = (id = 'segbar-value') => screen.getByTestId(id).style.getPropertyValue('--seg')
    const parts = () => [...document.querySelectorAll('.ismsm-segbar .ismsm-seg')].map((e) => e.className)
    const legend = () => [...screen.getByTestId('segbar-legend').querySelectorAll('li')].map((li) => [
      li.querySelector('.ismsm-swatch')?.className.replace('ismsm-swatch ', ''), li.textContent,
    ])
    const show = async (r: Parameters<typeof reading>[0], status = 'off_track') => {
      api.getMetricDetail.mockResolvedValue(detail({ objective: objective({ reading: reading({ status: status as never, ...r }) }) }))
      renderDetail()
      await screen.findByTestId('segbar-value')
    }

    it('draws good, bad and unknown as separate parts when the counts are disjoint; accepted is a legend-only swatch', async () => {
      await show({
        numerator: 50, denominator: 66, value: 75.8, value_text: '76%', population: '50 of 66 risks',
        segments: [{ label: 'accepted', count: 5 }, { label: 'above appetite', count: 9 }, { label: 'not assessed', count: 7 }],
      })
      expect(parts()).toEqual(['ismsm-seg ismsm-seg--value', 'ismsm-seg ismsm-seg--bad', 'ismsm-seg ismsm-seg--unknown'])
      expect(width()).toBe('75.8%')
      expect(width('segbar-part-above appetite')).toBe('13.6%')
      expect(width('segbar-part-not assessed')).toBe('10.6%')
      expect(screen.queryByTestId('segbar-part-accepted')).toBeNull()
      expect(legend()).toEqual([
        ['ismsm-swatch--value', '50 of 66 risks'],
        ['ismsm-swatch--overlap', '5 accepted'],
        ['ismsm-swatch--bad', '9 above appetite'],
        ['ismsm-swatch--unknown', '7 not assessed'],
      ])
    })

    it('never carries the status colour on the bar', async () => {
      await show({ numerator: 2, denominator: 4, value: 50, value_text: '50%', population: '2 of 4', segments: [] }, 'off_track')
      const bar = document.querySelector('.ismsm-segbar')!
      expect(bar.className).toBe('ismsm-segbar')
      expect(document.querySelector('.ismsm-swatch--value')!.className).toBe('ismsm-swatch ismsm-swatch--value')
    })

    it('keeps one value part when the segments do not account for every bad row (corrected actions)', async () => {
      await show({
        numerator: 2, denominator: 4, value: 50, value_text: '50%', population: '2 of 4 audits',
        segments: [{ label: 'cancelled', count: 0 }, { label: 'not yet due', count: 6 }],
      })
      expect(parts()).toEqual(['ismsm-seg ismsm-seg--value'])
      expect(width()).toBe('50%')
      expect(legend()).toEqual([
        ['ismsm-swatch--value', '2 of 4 audits'],
        ['ismsm-swatch--other', '0 cancelled'],
        ['ismsm-swatch--other', '6 not yet due'],
      ])
    })

    it('keeps one value part when known labels do not sum to the bad share', async () => {
      // Treatments: 3 overdue + 1 no due date = 4, but 10 - 5 = 5 bad: one bad row is not in a segment.
      await show({
        numerator: 5, denominator: 10, value: 50, value_text: '50%', population: '5 of 10 treatments',
        segments: [{ label: 'overdue', count: 3 }, { label: 'no due date', count: 1 }],
      })
      expect(parts()).toEqual(['ismsm-seg ismsm-seg--value'])
      expect(legend().map(([k]) => k)).toEqual(['ismsm-swatch--value', 'ismsm-swatch--other', 'ismsm-swatch--other'])
    })

    it('leaves a zero-count part out of the bar but keeps its legend item', async () => {
      await show({
        numerator: 6, denominator: 10, value: 60, value_text: '60%', population: '6 of 10 treatments',
        segments: [{ label: 'overdue', count: 4 }, { label: 'no due date', count: 0 }],
      })
      expect(parts()).toEqual(['ismsm-seg ismsm-seg--value', 'ismsm-seg ismsm-seg--bad'])
      expect(width('segbar-part-overdue')).toBe('40%')
      expect(legend()[2]).toEqual(['ismsm-swatch--bad', '0 no due date'])
    })

    it('review F1: an at_most count (the number to keep down) is a plain count part, never green', async () => {
      api.getMetricDetail.mockResolvedValue(detail({ objective: objective({
        metric: metric({ unit: 'count', direction: 'at_most', target_value: 0, warn_value: null, name: 'Open material deficiencies' }),
        reading: reading({ status: 'off_track', numerator: 16, denominator: 31, value: 16, value_text: '16', population: '16 of 31 open are material', segments: [] }),
      }) }))
      renderDetail()
      await screen.findByTestId('segbar-value')
      expect(parts()).toEqual(['ismsm-seg ismsm-seg--count'])
      expect(width()).toBe('51.6%')
      expect(document.querySelector('.ismsm-segbar')!.className).toBe('ismsm-segbar')
      expect(legend()).toEqual([['ismsm-swatch--count', '16 of 31 open are material']])
      expect(document.querySelector('.ismsm-seg--value, .ismsm-swatch--value')).toBeNull()
    })

    it('review F1: at_most draws no bad or unknown parts and no overlap swatch, even when the labels are known', () => {
      const items = barItems(3, 10, [{ label: 'overdue', count: 7 }, { label: 'accepted', count: 1 }], 'at_most')
      expect(items.map((i) => [i.kind, i.part])).toEqual([['count', true], ['other', false], ['other', false]])
      expect(barItems(3, 10, [], null)[0].kind).toBe('count')
      expect(barItems(3, 10, [], 'at_least')[0].kind).toBe('value')
    })

    it('classifies only the library labels it knows', () => {
      expect(segmentKind('above appetite')).toBe('bad')
      expect(segmentKind('overdue')).toBe('bad')
      expect(segmentKind('not assessed')).toBe('unknown')
      expect(segmentKind('accepted')).toBe('overlap')
      expect(segmentKind('not yet due')).toBe('other')
      // Review F6: its measure always sends "won't do" too, so it could never be a part.
      expect(segmentKind('completed with no date')).toBe('other')
      expect(barItems(1, 2, [{ label: 'something new', count: 1 }]).filter((i) => i.part)).toHaveLength(1)
    })

    it('rounds to one decimal and clamps', () => {
      expect(segWidth(2, 4)).toBe(50)
      expect(segWidth(50, 66)).toBe(75.8)
      expect(segWidth(9, 4)).toBe(100)
    })
  })

  it('lists every result in the log, including those under an earlier measure (review S2)', async () => {
    api.getMetricDetail.mockResolvedValue(detail({ results: [
      result({ id: 'r-3', measured_on: '2026-11-18' }),
      result({ id: 'r-2', measured_on: '2026-11-01' }),
      result({ id: 'r-1', measured_on: '2026-10-01', same_measure: false }),
    ] }))
    renderDetail()
    expect(await screen.findAllByTestId('log-row')).toHaveLength(3)
    expect(screen.getAllByTestId('trend-point')).toHaveLength(2)
    expect(screen.getByText('Earlier results under a different measure are in the log.')).toBeInTheDocument()
  })

  it.each([
    ['a milestone', metric({ kind: 'milestone', measure_key: null, unit: null })],
    ['no metric', null],
  ])('shows no trend at all for %s (review M5)', async (_name, m) => {
    api.getMetricDetail.mockResolvedValue(detail({ objective: objective({ metric: m }), results: [] }))
    renderDetail()
    await screen.findByRole('heading', { level: 2 })
    expect(screen.queryByTestId('trend-chart')).toBeNull()
    expect(screen.queryByTestId('trend-empty')).toBeNull()
    expect(screen.queryByText(/The trend starts once/)).toBeNull()
  })

  describe('collector on a result (phase 3 B4)', () => {
    it('prints the collector in the source cell of a result that names one', async () => {
      api.getMetricDetail.mockResolvedValue(detail({ results: [
        result({ id: 'r-c', source: 'logged', entered_by_name: 'Pat Example', collector: 'patch-sla' }),
        result({ id: 'r-p', source: 'logged', entered_by_name: 'Sam Person' }),
        result({ id: 'r-n', source: 'logged', entered_by_name: 'Kim Null', collector: null }),
        result({ id: 'r-k', source: 'logged', entered_by_name: null, collector: 'mfa-coverage' }),
      ] }))
      renderDetail()
      const rows = (await screen.findAllByTestId('log-row')) as HTMLTableRowElement[]
      expect(rows[0].cells[4]).toHaveTextContent(/^Collector patch-sla \(key of Pat Example\)$/)
      // A collector with no person behind the key names the collector alone.
      expect(rows[3].cells[4]).toHaveTextContent(/^Collector mfa-coverage$/)
      // Without a collector the cell is unchanged.
      expect(rows[1].cells[4]).toHaveTextContent(/^Logged by Sam Person$/)
      expect(rows[2].cells[4]).toHaveTextContent(/^Logged by Kim Null$/)
    })

    it('never prints the origin the server reports', async () => {
      const withOrigin = { ...result({ id: 'r-o', source: 'logged', entered_by_name: 'Pat Example' }), origin: 'system' }
      api.getMetricDetail.mockResolvedValue(detail({ results: [withOrigin as never] }))
      renderDetail()
      const [row] = await screen.findAllByTestId('log-row')
      expect(row).not.toHaveTextContent(/system/i)
      expect(screen.queryByText(/system/i)).toBeNull()
    })
  })
})
