// SPDX-License-Identifier: AGPL-3.0-only
/** The trend chart: steps from each result's own target, current unit only, an honest empty state. */
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import TrendChart, { axisTicks, niceMax, plottable, stepPath, targetThen, tipParts } from '../TrendChart'
import { result } from '../../isms/objectives/__tests__/fixtures'

// Newest first, as the server sends them.
const THREE = [
  result({ id: 'r-3', measured_on: '2026-11-18', value: 14, value_text: '14%', numerator: 9, target_value: 95, warn_value: 85 }),
  result({ id: 'r-2', measured_on: '2026-10-15', value: 13, value_text: '13%' }),
  result({ id: 'r-1', measured_on: '2026-09-30' }),
]

describe('TrendChart', () => {
  it('draws the value line through every result, oldest first, as a group named by its summary', () => {
    render(<TrendChart results={THREE} unit="percent" name="Risks within appetite" />)
    const svg = screen.getByRole('group')
    expect(svg).toHaveAccessibleName(
      'Trend of Risks within appetite: 3 measurements from 30 Sept 2026 to 18 Nov 2026, from 12% to 14%. Target ≥ 95%.',
    )
    // The summary is a sibling, so the points are not hidden inside an image (review S3).
    expect(svg.closest('[role="img"]')).toBeNull()
    expect(screen.getByTestId('trend-summary').contains(svg)).toBe(false)
    expect(screen.getAllByTestId('trend-point')).toHaveLength(3)
    expect(screen.getByTestId('trend-line').getAttribute('points')!.split(' ')).toHaveLength(3)
    expect(svg).toHaveTextContent('14%')
  })

  describe('review F3: a logged result is one point per period, on its period', () => {
    const logged = (over: Parameters<typeof result>[0]) => result({ source: 'logged', ...over })
    const dates = () => screen.getAllByTestId('trend-point').map((g) => g.getAttribute('aria-label') ?? '')

    it('plots only the newest entry of a period, at the period start, and keeps computed results as they are', () => {
      const pts = plottable([
        logged({ id: 'a', period_start: '2026-07-01', measured_on: '2026-07-20', created_at: '2026-07-20T09:00:00', value: 40, value_text: '40%' }),
        logged({ id: 'b', period_start: '2026-07-01', measured_on: '2026-07-20', created_at: '2026-07-20T09:05:00', value: 4, value_text: '4%' }),
        logged({ id: 'c', period_start: '2026-10-01', measured_on: '2026-11-02', created_at: '2026-11-02T10:00:00', value: 43, value_text: '43%' }),
      ], 'percent')
      expect(pts.map((p) => [p.id, p.measured_on])).toEqual([['b', '2026-07-01'], ['c', '2026-10-01']])
      const live = plottable(THREE, 'percent')
      expect(live.map((p) => p.measured_on)).toEqual(['2026-09-30', '2026-10-15', '2026-11-18'])
    })

    it('sorts a period logged later before the newer period, so the line ends on the newest period', () => {
      const pts = plottable([
        logged({ id: 'q4', period_start: '2026-10-01', measured_on: '2026-10-05', created_at: '2026-10-05T10:00:00', value: 43, value_text: '43%' }),
        logged({ id: 'q3', period_start: '2026-07-01', measured_on: '2026-11-18', created_at: '2026-11-18T10:00:00', value: 38, value_text: '38%' }),
      ], 'percent')
      expect(pts.map((p) => p.id)).toEqual(['q3', 'q4'])
    })

    it('breaks a created_at tie by id, the newer id winning', () => {
      const same = '2026-07-20T09:00:00'
      const pts = plottable([
        logged({ id: 'r-1', period_start: '2026-07-01', created_at: same, value: 1, value_text: '1%' }),
        logged({ id: 'r-2', period_start: '2026-07-01', created_at: same, value: 2, value_text: '2%' }),
        logged({ id: 'r-0', period_start: '2026-07-01', created_at: same, value: 0, value_text: '0%' }),
      ], 'percent')
      expect(pts.map((p) => p.id)).toEqual(['r-2'])
    })

    it('draws two dots for three entries in two periods, each named by its period start', () => {
      render(<TrendChart unit="percent" name="m" results={[
        logged({ id: 'a', period_start: '2026-07-01', measured_on: '2026-07-20', created_at: '2026-07-20T09:00:00', value: 40, value_text: '40%' }),
        logged({ id: 'b', period_start: '2026-07-01', measured_on: '2026-07-20', created_at: '2026-07-20T09:05:00', value: 4, value_text: '4%' }),
        logged({ id: 'c', period_start: '2026-10-01', measured_on: '2026-11-02', created_at: '2026-11-02T10:00:00', value: 43, value_text: '43%' }),
      ]} />)
      expect(dates()).toHaveLength(2)
      expect(dates()[0]).toMatch(/^1 Jul 2026.*4%/)
      expect(dates()[1]).toMatch(/^1 Oct 2026.*43%/)
    })
  })

  it('draws the last dot larger than the others (design row 19)', () => {
    render(<TrendChart results={THREE} unit="percent" name="m" />)
    const r = screen.getAllByTestId('trend-point').map((g) => g.querySelector('.ismsm-trend-dot')!.getAttribute('r'))
    expect(r).toEqual(['4', '4', '5'])
  })

  it('draws a step where the target changed, and none where it did not', () => {
    // The levels a stepped path passes through: its start, then each vertical move.
    const levels = (d: string) => [...d.matchAll(/(?:M[\d.]+,|V)([\d.]+)/g)].map((m) => Number(m[1]))
    const steps = (d: string) => levels(d).filter((v, i, all) => i > 0 && v !== all[i - 1]).length
    const { unmount } = render(<TrendChart results={THREE} unit="percent" name="m" />)
    // 100, 100, then 95: one step, at the newest result; the warning 90, 90, 85 likewise.
    expect(steps(screen.getByTestId('trend-target').getAttribute('d')!)).toBe(1)
    expect(steps(screen.getByTestId('trend-warn').getAttribute('d')!)).toBe(1)
    unmount()
    const flat = THREE.map((r) => ({ ...r, target_value: 100, warn_value: 90 }))
    render(<TrendChart results={flat} unit="percent" name="m" />)
    expect(steps(screen.getByTestId('trend-target').getAttribute('d')!)).toBe(0)
    expect(stepPath([0, 10, 20], [100, 100, 95], (v) => 100 - v)).toBe('M0.0,0.0H10.0V0.0H20.0V5.0H20.0')
  })

  it('breaks the target line where a result had none', () => {
    expect(stepPath([0, 10, 20], [100, null, 95], (v) => v)).toBe('M0.0,100.0H10.0M20.0,95.0H20.0')
  })

  it('shows the empty state with one result, and draws no line', () => {
    render(<TrendChart results={[result()]} unit="percent" name="m" />)
    expect(screen.getByTestId('trend-empty')).toHaveTextContent('The trend starts once there are two measurements.')
    expect(screen.getByTestId('trend-empty')).toHaveTextContent('One so far: 12% on 30 Sept 2026.')
    expect(screen.queryByRole('img')).toBeNull()
    expect(screen.queryByTestId('trend-line')).toBeNull()
  })

  it('ignores a result in another unit', () => {
    const mixed = [result({ id: 'r-9', measured_on: '2026-11-20', value: 7, value_text: '7', unit: 'count' }), ...THREE.slice(1)]
    expect(plottable(mixed, 'percent').map((r) => r.id)).toEqual(['r-1', 'r-2'])
    render(<TrendChart results={mixed} unit="percent" name="m" />)
    expect(screen.getAllByTestId('trend-point')).toHaveLength(2)
    // With that one left out, a pair in another unit is not a trend.
    const { container } = render(<TrendChart results={[mixed[0], THREE[2]]} unit="percent" name="m" />)
    expect(container.querySelector('[data-testid="trend-empty"]')).not.toBeNull()
  })

  it('shows date, value, count and target then on hover and on keyboard focus', () => {
    render(<TrendChart results={THREE} unit="percent" name="m" />)
    const points = screen.getAllByTestId('trend-point')
    fireEvent.mouseEnter(points[0])
    expect(screen.getByTestId('trend-tip')).toHaveTextContent('30 Sept 2026')
    expect(screen.getByTestId('trend-tip')).toHaveTextContent('12% · 8 of 66')
    expect(screen.getByTestId('trend-tip')).toHaveTextContent('Target then ≥ 100%')
    fireEvent.mouseLeave(points[0])
    expect(screen.queryByTestId('trend-tip')).toBeNull()
    expect(points[2]).toHaveAttribute('tabindex', '0')
    fireEvent.focus(points[2])
    expect(screen.getByTestId('trend-tip')).toHaveTextContent('Target then ≥ 95%')
  })

  it('rounds a count axis up to 1, 2 or 5 times a power of ten', () => {
    expect(niceMax(7)).toBe(10)
    expect(niceMax(13)).toBe(20)
    expect(niceMax(41)).toBe(50)
    expect(niceMax(0)).toBe(1)
    expect(targetThen({ target_value: 2, direction: 'at_most', unit: 'count' })).toBe('≤ 2')
    expect(targetThen({ target_value: null, direction: null, unit: null })).toBe('none')
  })

  describe('keyboard (review S3)', () => {
    it('is one tab stop, on the newest point', () => {
      render(<TrendChart results={THREE} unit="percent" name="m" />)
      const points = screen.getAllByTestId('trend-point')
      expect(points.filter((p) => p.getAttribute('tabindex') === '0')).toHaveLength(1)
      expect(points[2]).toHaveAttribute('tabindex', '0')
      expect(points.slice(0, 2).every((p) => p.getAttribute('tabindex') === '-1')).toBe(true)
    })

    it('moves with Left and Right, and jumps with Home and End, keeping one tab stop', () => {
      render(<TrendChart results={THREE} unit="percent" name="m" />)
      const points = () => screen.getAllByTestId('trend-point')
      const stop = () => points().findIndex((p) => p.getAttribute('tabindex') === '0')
      points()[2].focus()
      fireEvent.keyDown(points()[2], { key: 'ArrowLeft' })
      expect(stop()).toBe(1)
      expect(document.activeElement).toBe(points()[1])
      expect(screen.getByTestId('trend-tip')).toHaveTextContent('15 Oct 2026')
      fireEvent.keyDown(points()[1], { key: 'Home' })
      expect(stop()).toBe(0)
      expect(document.activeElement).toBe(points()[0])
      fireEvent.keyDown(points()[0], { key: 'ArrowLeft' })
      expect(stop()).toBe(0)
      fireEvent.keyDown(points()[0], { key: 'End' })
      expect(stop()).toBe(2)
      fireEvent.keyDown(points()[2], { key: 'ArrowRight' })
      expect(stop()).toBe(2)
      fireEvent.keyDown(points()[2], { key: 'ArrowLeft' })
      fireEvent.keyDown(points()[1], { key: 'ArrowRight' })
      expect(stop()).toBe(2)
      expect(points().filter((p) => p.getAttribute('tabindex') === '0')).toHaveLength(1)
    })

    it('names each point with its tooltip text', () => {
      render(<TrendChart results={THREE} unit="percent" name="m" />)
      const oldest = screen.getAllByTestId('trend-point')[0]
      expect(oldest).toHaveAttribute('role', 'img')
      expect(oldest).toHaveAccessibleName('30 Sept 2026, 12% · 8 of 66, Target then ≥ 100%')
      fireEvent.focus(oldest)
      expect(screen.getByTestId('trend-tip').textContent).toBe(tipParts(THREE[2]).join(''))
      expect(tipParts(THREE[2]).join(', ')).toBe('30 Sept 2026, 12% · 8 of 66, Target then ≥ 100%')
    })
  })

  describe('a change of measure (review S2)', () => {
    const changed = [
      result({ id: 'r-3', measured_on: '2026-11-18', value: 90, value_text: '90%' }),
      result({ id: 'r-2', measured_on: '2026-11-01', value: 88, value_text: '88%' }),
      result({ id: 'r-1', measured_on: '2026-10-01', value: 40, value_text: '40%', same_measure: false }),
    ]

    it('plots only results under the same measure, and says the others are in the log', () => {
      render(<TrendChart results={changed} unit="percent" name="m" />)
      expect(screen.getAllByTestId('trend-point')).toHaveLength(2)
      expect(screen.getByTestId('trend-line').getAttribute('points')!.split(' ')).toHaveLength(2)
      expect(screen.getByTestId('trend-other-measure')).toHaveTextContent(
        'Earlier results under a different measure are in the log.',
      )
    })

    it('treats a missing flag as the same measure, and says nothing then', () => {
      expect(plottable(THREE, 'percent')).toHaveLength(3)
      render(<TrendChart results={THREE} unit="percent" name="m" />)
      expect(screen.queryByTestId('trend-other-measure')).toBeNull()
    })

    it('still says so when what is left is too little for a line', () => {
      render(<TrendChart results={[changed[0], changed[2]]} unit="percent" name="m" />)
      expect(screen.getByTestId('trend-empty')).toBeInTheDocument()
      expect(screen.getByTestId('trend-other-measure')).toBeInTheDocument()
    })
  })

  describe('axis inside one month (review M1)', () => {
    it('labels the first and last dates when no first-of-month falls in the range', () => {
      expect(axisTicks('2026-11-18', '2026-11-30').map((t) => t.label)).toEqual(['18 Nov', '30 Nov'])
      const inMonth = [
        result({ id: 'b', measured_on: '2026-11-30', value: 20, value_text: '20%' }),
        result({ id: 'a', measured_on: '2026-11-18', value: 10, value_text: '10%' }),
      ]
      const { container } = render(<TrendChart results={inMonth} unit="percent" name="m" />)
      const labels = [...container.querySelectorAll('text.ismsm-trend-axis')].map((t) => t.textContent)
      expect(labels).toContain('18 Nov')
      expect(labels).toContain('30 Nov')
    })

    it('keeps month labels when a first-of-month is inside the range', () => {
      expect(axisTicks('2026-09-30', '2026-11-18').map((t) => t.label)).toEqual(['Oct', 'Nov'])
    })
  })

  describe('pace line (phase 3)', () => {
    const PACE = {
      state: 'behind' as const, label: 'Behind pace', expected_value: 52.4, expected_text: '52%',
      baseline_on: '2026-09-30', baseline_value: 12, due_date: '2027-06-30', target_value: 80,
    }
    const x = (el: Element, a: string) => Number(el.getAttribute(a))

    it('draws a dashed line from the baseline to the target on the due date, and labels it in the legend', () => {
      render(<TrendChart results={THREE} unit="percent" name="m" pace={PACE} />)
      const line = screen.getByTestId('trend-pace')
      expect(line).toHaveClass('ismsm-trend-pace')
      // 0% at the bottom (y 212), 100% at the top (y 14): baseline 12% low, target 80% high.
      expect(x(line, 'y1')).toBeCloseTo(14 + 198 * 0.88, 1)
      expect(x(line, 'y2')).toBeCloseTo(14 + 198 * 0.2, 1)
      // The baseline is the first date, the due date the last: the line spans the plot.
      expect(x(line, 'x1')).toBe(40)
      expect(x(line, 'x2')).toBe(640 - 84)
      expect(screen.getByTestId('trend-legend')).toHaveTextContent('Pace to target')
    })

    it('extends the x range to the due date, so the newest result is no longer at the right edge', () => {
      const { unmount } = render(<TrendChart results={THREE} unit="percent" name="m" />)
      const without = screen.getAllByTestId('trend-point').map((g) => x(g.querySelector('.ismsm-trend-dot')!, 'cx'))
      unmount()
      render(<TrendChart results={THREE} unit="percent" name="m" pace={PACE} />)
      const withPace = screen.getAllByTestId('trend-point').map((g) => x(g.querySelector('.ismsm-trend-dot')!, 'cx'))
      expect(without[2]).toBe(640 - 84)
      expect(withPace[2]).toBeLessThan(without[2])
      expect(withPace[0]).toBe(without[0])
      // The axis now runs to the due date's month (Oct to Jun, every other month).
      const labels = [...screen.getByTestId('trend-chart').querySelectorAll('text.ismsm-trend-axis')].map((t) => t.textContent)
      expect(labels).toContain('Jun')
    })

    it('fits a count axis to the pace target', () => {
      const counts = THREE.map((r) => ({ ...r, unit: 'count' as const, value: 3, target_value: 4, warn_value: null }))
      render(<TrendChart results={counts} unit="count" name="m" pace={{ ...PACE, baseline_value: 3, target_value: 40 }} />)
      // The top of the axis is a round number at least 40.
      expect(screen.getByTestId('trend-chart')).toHaveTextContent('50')
      expect(x(screen.getByTestId('trend-pace'), 'y2')).toBeGreaterThanOrEqual(14)
    })

    it('draws nothing extra without a pace', () => {
      render(<TrendChart results={THREE} unit="percent" name="m" pace={null} />)
      expect(screen.queryByTestId('trend-pace')).toBeNull()
      expect(screen.queryByTestId('trend-legend')).toBeNull()
    })
  })
})
