// SPDX-License-Identifier: AGPL-3.0-only
/** Phase 3: the tile trend line and the pace label beside the status chip. */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { ObjectiveOut, Pace, TrendPoint } from '../../../data/securityObjectivesApi'
import MetricTile from '../MetricTile'
import Sparkline, { SPARK_H, SPARK_W, sparkGeometry, sparkPoints, sparkSentence } from '../Sparkline'
import { metric, objective, reading } from '../../isms/objectives/__tests__/fixtures'

const TODAY = '2026-11-18'
const TREND: TrendPoint[] = [
  { measured_on: '2026-11-05', value: 38 },
  { measured_on: '2026-11-10', value: 40 },
  { measured_on: '2026-11-12', value: 41 },
]
const PACE: Pace = {
  state: 'behind', label: 'Behind pace', expected_value: 52.4, expected_text: '52%',
  baseline_on: '2026-11-05', baseline_value: 38, due_date: '2027-06-30', target_value: 80,
}

/** An objective as an older server sends it: no `trend`, no `pace` keys at all. */
function oldServer(o: ObjectiveOut): ObjectiveOut {
  const { trend: _t, pace: _p, ...rest } = o.reading
  return { ...o, reading: rest }
}

/** A live objective (the fixture's metric); its reading carries the server's date for today's value. */
function withTrend(trend: TrendPoint[] | undefined, over: Partial<ObjectiveOut> = {}, value: number | null = 43, measuredOn: string | null = TODAY): ObjectiveOut {
  return objective({
    reading: reading({ value, value_text: value === null ? '—' : `${value}%`, measured_on: measuredOn, ...(trend ? { trend } : {}) }),
    ...over,
  })
}

describe('sparkPoints', () => {
  it("appends today's value as the last point when the server's date is later than the last stored result", () => {
    const pts = sparkPoints(TREND, 43, TODAY)
    expect(pts.map((p) => p.value)).toEqual([38, 40, 41, 43])
    expect(pts[3]).toEqual({ measured_on: TODAY, value: 43, today: true })
  })

  it('does not append when the last stored result is today', () => {
    const trend = [...TREND, { measured_on: TODAY, value: 43 }]
    expect(sparkPoints(trend, 43, TODAY)).toEqual(trend)
  })

  it('does not append when there is no value today', () => {
    expect(sparkPoints(TREND, null, TODAY)).toEqual(TREND)
  })

  it("is today's value alone when nothing is stored, and nothing when there is no value either", () => {
    expect(sparkPoints([], 43, TODAY)).toEqual([{ measured_on: TODAY, value: 43, today: true }])
    expect(sparkPoints([], null, TODAY)).toEqual([])
  })

  it('appends nothing without a server date (a logged metric, or none sent)', () => {
    expect(sparkPoints(TREND, 43, null)).toEqual(TREND)
    expect(sparkPoints([], 43, null)).toEqual([])
  })

  it('never edits the trend it was given', () => {
    const trend = [...TREND]
    sparkPoints(trend, 43, TODAY)
    expect(trend).toEqual(TREND)
  })
})

describe('sparkGeometry', () => {
  it('spans the box, padded, with the highest value at the top and the lowest at the bottom', () => {
    const { xs, ys } = sparkGeometry([{ value: 10 }, { value: 20 }, { value: 30 }], null)
    expect(xs[0]).toBe(4)
    expect(xs[2]).toBe(SPARK_W - 4)
    expect(ys[0]).toBe(SPARK_H - 4)
    expect(ys[2]).toBe(4)
  })

  it('includes the target in the range', () => {
    const { ys, targetY } = sparkGeometry([{ value: 40 }, { value: 50 }], 90)
    expect(targetY).toBe(4)
    expect(ys[0]).toBe(SPARK_H - 4)
    expect(ys[1]).toBeGreaterThan(targetY!)
  })

  it('draws a flat series at mid-height, without dividing by zero', () => {
    const { xs, ys, targetY } = sparkGeometry([{ value: 7 }, { value: 7 }, { value: 7 }], null)
    expect(ys).toEqual([SPARK_H / 2, SPARK_H / 2, SPARK_H / 2])
    expect(xs.every(Number.isFinite)).toBe(true)
    expect(targetY).toBeNull()
    // Flat at the target too.
    expect(sparkGeometry([{ value: 80 }], 80)).toEqual({ xs: [SPARK_W - 4], ys: [SPARK_H / 2], targetY: SPARK_H / 2 })
  })

  it('puts a single point at the right, where the line would end', () => {
    const { xs, ys } = sparkGeometry([{ value: 5 }], null)
    expect(xs).toEqual([SPARK_W - 4])
    expect(ys.every(Number.isFinite)).toBe(true)
  })

  it('returns nothing for no points', () => {
    expect(sparkGeometry([], null)).toEqual({ xs: [], ys: [], targetY: null })
  })
})

describe('sparkSentence', () => {
  it("reads first and last from the server's numbers, today's from the server's own text", () => {
    expect(sparkSentence(sparkPoints(TREND, 43, TODAY), 'percent', '43%')).toBe(
      'Trend: 38% on 5 Nov 2026 to 43% today, 4 measurements.',
    )
  })

  it('names dates only when today was not appended, and has no % for a count', () => {
    expect(sparkSentence(sparkPoints(TREND, null, TODAY), 'count', '')).toBe(
      'Trend: 38 on 5 Nov 2026 to 41 on 12 Nov 2026, 3 measurements.',
    )
  })

  it('reads one measurement as one', () => {
    expect(sparkSentence(sparkPoints([], 43, TODAY), 'percent', '43%')).toBe('Trend: 43% today, 1 measurement.')
  })

  it("prints a stored point in the server's words, not the raw number", () => {
    const trend = [{ measured_on: '2026-11-05', value: 15.384615, value_text: '15%' }]
    expect(sparkSentence(sparkPoints(trend, 43, TODAY), 'percent', '43%')).toBe(
      'Trend: 15% on 5 Nov 2026 to 43% today, 2 measurements.',
    )
  })
})

describe('Sparkline', () => {
  it('draws the line, end dot and dashed target line, hidden from screen readers, with a sentence instead', () => {
    render(<Sparkline objective={withTrend(TREND)} />)
    const svg = screen.getByTestId('tile-spark')
    expect(svg).toHaveAttribute('aria-hidden', 'true')
    expect(svg).toHaveAttribute('width', String(SPARK_W))
    expect(svg).toHaveAttribute('height', String(SPARK_H))
    expect(screen.getByTestId('spark-line').getAttribute('points')!.split(' ')).toHaveLength(4)
    const dot = screen.getByTestId('spark-dot')
    expect(dot.getAttribute('cx')).toBe(String(SPARK_W - 4))
    const target = screen.getByTestId('spark-target')
    expect(target.getAttribute('x1')).toBe('0')
    expect(target.getAttribute('x2')).toBe(String(SPARK_W))
    expect(document.querySelector('.ismsm-spark-wrap > .ismsm-sr-only')).toHaveTextContent('Trend: 38% on 5 Nov 2026 to 43% today, 4 measurements.')
  })

  it('draws no target line when the metric has no numeric target', () => {
    render(<Sparkline objective={withTrend(TREND, { metric: metric({ target_value: null }) })} />)
    expect(screen.getByTestId('spark-line')).toBeInTheDocument()
    expect(screen.queryByTestId('spark-target')).toBeNull()
  })

  it('draws only the end dot and the target line for one point', () => {
    render(<Sparkline objective={withTrend([])} />)
    expect(screen.queryByTestId('spark-line')).toBeNull()
    expect(screen.getByTestId('spark-dot')).toBeInTheDocument()
    expect(screen.getByTestId('spark-target')).toBeInTheDocument()
  })

  it('draws nothing with no points', () => {
    const { container } = render(<Sparkline objective={withTrend([], {}, null)} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('draws nothing for a milestone, an unset metric or no metric', () => {
    for (const over of [
      { metric: metric({ kind: 'milestone', unit: null, target_value: null }) },
      { metric: metric({ kind: 'unset' }) },
      { metric: null },
    ] as Partial<ObjectiveOut>[]) {
      const { container, unmount } = render(<Sparkline objective={withTrend(TREND, over)} />)
      expect(container).toBeEmptyDOMElement()
      unmount()
    }
  })

  it('reads an older server with no trend as no stored points: today alone', () => {
    render(<Sparkline objective={oldServer(withTrend(TREND))} />)
    expect(screen.queryByTestId('spark-line')).toBeNull()
    expect(screen.getByTestId('spark-dot')).toBeInTheDocument()
    expect(document.querySelector('.ismsm-spark-wrap > .ismsm-sr-only')).toHaveTextContent('Trend: 43% today, 1 measurement.')
  })

  it('review F2: a logged metric gains no "today" point; its value is already the last entry', () => {
    const logged = objective({
      metric: metric({ kind: 'logged', measure_key: null, frequency: 'quarterly' }),
      reading: reading({
        // Logged on 2 Nov for the period starting 1 Oct: the trend point sits on the period, the reading on the day written.
        value: 43, value_text: '43%', measured_on: '2026-11-02',
        trend: [{ measured_on: '2026-08-05', value: 38, value_text: '38%' }, { measured_on: '2026-10-01', value: 43, value_text: '43%' }],
      }),
    })
    render(<Sparkline objective={logged} />)
    expect(screen.getByTestId('spark-line').getAttribute('points')!.split(' ')).toHaveLength(2)
    expect(document.querySelector('.ismsm-spark-wrap > .ismsm-sr-only'))
      .toHaveTextContent('Trend: 38% on 5 Aug 2026 to 43% on 1 Oct 2026, 2 measurements.')
  })

  it("review F2: a live metric whose last stored point is the server's today gains no extra point, whatever the browser's date", () => {
    const live = withTrend([...TREND, { measured_on: TODAY, value: 43, value_text: '43%' }])
    expect(live.reading.measured_on).toBe(TODAY)
    render(<Sparkline objective={live} />)
    expect(screen.getByTestId('spark-line').getAttribute('points')!.split(' ')).toHaveLength(4)
    expect(document.querySelector('.ismsm-spark-wrap > .ismsm-sr-only')).toHaveTextContent(/, 4 measurements\.$/)
    expect(document.querySelector('.ismsm-spark-wrap > .ismsm-sr-only')?.textContent).not.toMatch(/today/)
  })

  it('review F2: a live reading with no server date appends nothing', () => {
    render(<Sparkline objective={withTrend(TREND, {}, 43, null)} />)
    expect(screen.getByTestId('spark-line').getAttribute('points')!.split(' ')).toHaveLength(3)
  })

  it('review F6: carries no tooltip that the tile link would cover', () => {
    render(<Sparkline objective={withTrend(TREND)} />)
    expect(screen.getByTestId('tile-spark').querySelector('title, rect')).toBeNull()
  })
})

describe('MetricTile pace', () => {
  const status = () => screen.getByTestId('metric-tile-OBJ-01').querySelector('.ismsm-tile-status > span:last-child')!

  it('compresses runs of Annex A controls in the tile foot, with the full list for screen readers (design row 9; no title, review F6)', () => {
    const o = objective({ iso_clauses: ['6.1.3', 'A.5.24', 'A.5.25', 'A.5.26', 'A.5.27', 'A.5.28', 'A.5.29', 'A.5.30'] })
    render(<MetricTile objective={o} />)
    const chips = screen.getByTestId('metric-tile-OBJ-01').querySelector('.ismsm-tile-foot .obj-clauses')!
    expect([...chips.querySelectorAll('.obj-clause')].map((c) => c.querySelector('[aria-hidden]')?.textContent ?? c.textContent))
      .toEqual(['6.1.3', 'A.5.24–30'])
    expect(chips.hasAttribute('title')).toBe(false)
    expect(chips.querySelector('.ismsm-sr-only')).toHaveTextContent('A.5.24, A.5.25, A.5.26, A.5.27, A.5.28, A.5.29, A.5.30')
  })

  it('shows no pace label when the server sends none', () => {
    render(<MetricTile objective={withTrend(TREND, {}, 43)} />)
    expect(screen.queryByTestId('tile-pace')).toBeNull()
    expect(status().textContent).not.toMatch(/pace/)
  })

  it("starts the text beside the chip with the server's label, then the aside: Behind pace in the warning style", () => {
    const o = objective({ reading: reading({ status: 'in_progress', value: 43, value_text: '43%', pace: PACE }) })
    render(<MetricTile objective={o} />)
    const label = screen.getByTestId('tile-pace')
    expect(label).toHaveTextContent('Behind pace')
    expect(label).toHaveClass('ismsm-pace--behind')
    expect(status().textContent).toMatch(/^Behind pace · /)
  })

  it('shows "On pace" in normal ink', () => {
    const o = objective({ reading: reading({ status: 'in_progress', value: 60, value_text: '60%', pace: { ...PACE, state: 'on_pace', label: 'On pace' } }) })
    render(<MetricTile objective={o} />)
    expect(screen.getByTestId('tile-pace')).toHaveTextContent('On pace')
    expect(screen.getByTestId('tile-pace')).toHaveClass('ismsm-pace--on_pace')
  })

  it('never changes the status chip', () => {
    const o = objective({ reading: reading({ status: 'in_progress', value: 43, value_text: '43%', days_left: 224, pace: PACE }) })
    render(<MetricTile objective={o} />)
    expect(screen.getByTestId('metric-tile-OBJ-01')).toHaveClass('ismsm-tile--in_progress')
  })

  it('renders a tile from an older server with neither field', () => {
    render(<MetricTile objective={oldServer(withTrend(TREND))} />)
    expect(screen.getByTestId('metric-tile-OBJ-01')).toBeInTheDocument()
    expect(screen.queryByTestId('tile-pace')).toBeNull()
    expect(screen.getByTestId('tile-spark')).toBeInTheDocument()
  })

  it('puts the trend line in the value row', () => {
    render(<MetricTile objective={withTrend(TREND)} />)
    const row = screen.getByTestId('tile-spark').closest('.ismsm-tile-mid')!
    expect(row.querySelector('.ismsm-tile-value')).not.toBeNull()
  })
})
