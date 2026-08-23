/**
 * The dashboard maturity chart links to the advice it was hiding (#789).
 *
 * `EVIDENCE_MATURITY_LEVELS` has always carried, per level, what that level
 * looks like, what moves an item off it, how long that takes and what it buys.
 * The dashboard — where someone sees "31 items at L1" and decides whether to
 * care — rendered a bar and none of it.
 */
import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'
import { MaturityDistributionWidget } from '../MaturityDistributionWidget'
import { EVIDENCE_MATURITY_LEVELS } from '../EvidenceMaturityTypes'

afterEach(cleanup)

const distribution = { L0: 4, L1: 31, L2: 10, L3: 2, L4: 0, L5: 1 }

function openLevel(level: string) {
  render(<MaturityDistributionWidget distribution={distribution} />)
  // A function matcher rather than `new RegExp(`^${level} `)`: a regex built from
  // an interpolated string is a ReDoS shape semgrep rejects on sight, and the
  // accessible name here only ever needs a prefix test.
  const row = screen.getByRole('button', {
    name: accessibleName => accessibleName.startsWith(`${level} `),
  })
  fireEvent.click(row)
  return row
}

describe('each level reveals its guidance', () => {
  it('reveals nothing until a row is activated', () => {
    render(<MaturityDistributionWidget distribution={distribution} />)
    const [firstCharacteristic] = EVIDENCE_MATURITY_LEVELS.L1.characteristics
    expect(screen.queryByText(firstCharacteristic)).not.toBeInTheDocument()
  })

  it('lists that level characteristics', () => {
    openLevel('L1')
    for (const c of EVIDENCE_MATURITY_LEVELS.L1.characteristics) {
      expect(screen.getByText(c)).toBeInTheDocument()
    }
  })

  it('lists the actions that move items off that level', () => {
    openLevel('L1')
    for (const a of EVIDENCE_MATURITY_LEVELS.L1.upgradeActions) {
      expect(screen.getByText(a)).toBeInTheDocument()
    }
  })

  it('states how long the upgrade takes and what it pays back', () => {
    openLevel('L1')
    expect(screen.getByText(EVIDENCE_MATURITY_LEVELS.L1.timeToUpgrade!)).toBeInTheDocument()
    expect(screen.getByText(EVIDENCE_MATURITY_LEVELS.L1.roiIndicator!)).toBeInTheDocument()
  })

  it('counts the items the advice applies to', () => {
    openLevel('L1')
    expect(screen.getByText(/Moving 31 items off L1/)).toBeInTheDocument()
  })

  it('singularises the count for a level holding one item', () => {
    openLevel('L5')
    expect(screen.getByText(/Moving 1 item off L5/)).toBeInTheDocument()
  })

  it('shows one level at a time', () => {
    render(<MaturityDistributionWidget distribution={distribution} />)
    fireEvent.click(screen.getByRole('button', { name: /^L1 / }))
    fireEvent.click(screen.getByRole('button', { name: /^L2 / }))
    const [l1First] = EVIDENCE_MATURITY_LEVELS.L1.characteristics
    expect(screen.queryByText(l1First)).not.toBeInTheDocument()
    expect(screen.getByText(EVIDENCE_MATURITY_LEVELS.L2.characteristics[0])).toBeInTheDocument()
  })

  it('closes when the open row is activated again', () => {
    const row = openLevel('L1')
    fireEvent.click(row)
    const [first] = EVIDENCE_MATURITY_LEVELS.L1.characteristics
    expect(screen.queryByText(first)).not.toBeInTheDocument()
  })
})

describe('the rows are operable without a mouse', () => {
  it('opens on Enter', () => {
    render(<MaturityDistributionWidget distribution={distribution} />)
    fireEvent.keyDown(screen.getByRole('button', { name: /^L0 / }), { key: 'Enter' })
    expect(screen.getByText(EVIDENCE_MATURITY_LEVELS.L0.characteristics[0])).toBeInTheDocument()
  })

  it('opens on Space', () => {
    render(<MaturityDistributionWidget distribution={distribution} />)
    fireEvent.keyDown(screen.getByRole('button', { name: /^L0 / }), { key: ' ' })
    expect(screen.getByText(EVIDENCE_MATURITY_LEVELS.L0.characteristics[0])).toBeInTheDocument()
  })

  it('reports its expanded state', () => {
    render(<MaturityDistributionWidget distribution={distribution} />)
    const row = screen.getByRole('button', { name: /^L0 / })
    expect(row).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(row)
    expect(row).toHaveAttribute('aria-expanded', 'true')
  })
})

describe('rendering defects the disclosure sat on top of', () => {
  it('renders a chart glyph in the empty state, not its escape sequence', () => {
    // The source held the seven literal characters of a \u escape in JSX text.
    render(<MaturityDistributionWidget distribution={{ L0: 0, L1: 0, L2: 0, L3: 0, L4: 0, L5: 0 }} />)
    expect(screen.getByText('No evidence items to analyse')).toBeInTheDocument()
    expect(screen.queryByText(/uD83D/)).not.toBeInTheDocument()
  })

  it('keeps the chart worst-first across re-renders', () => {
    // The legend used to call `.reverse()` on the array the chart had just
    // mapped over. Harmless only while the array was rebuilt every render.
    const { rerender, container } = render(
      <MaturityDistributionWidget distribution={distribution} />,
    )
    const order = () =>
      Array.from(container.querySelectorAll('.maturity-distribution-level')).map(
        n => n.textContent,
      )
    const first = order()
    rerender(<MaturityDistributionWidget distribution={distribution} />)
    expect(order()).toEqual(first)
    expect(first).toEqual(['L5', 'L4', 'L3', 'L2', 'L1', 'L0'])
  })

  it('does not reverse an array in render to get the legend order', () => {
    // The behavioural cases above cannot see this one: the mutation was
    // survivable only because the array was rebuilt every render, so both
    // orders come out correct today and would come out wrong the moment
    // someone memoises `levels` the way everything else here is memoised.
    // Pinned in source because that is where the fragility lives.
    const sources = import.meta.glob('../*.tsx', {
      query: '?raw',
      import: 'default',
      eager: true,
    }) as Record<string, string>
    const key = Object.keys(sources).find(k => k.endsWith('MaturityDistributionWidget.tsx'))
    if (!key) {
      throw new Error(`widget source not loaded — glob matched ${Object.keys(sources).length}`)
    }
    expect(sources[key]).toContain('maturity-distribution-chart')
    expect(sources[key]).not.toMatch(/levels\.reverse\(\)/)
  })

  it('still reads the legend from L0 upwards', () => {
    const { container } = render(<MaturityDistributionWidget distribution={distribution} />)
    const legend = Array.from(
      container.querySelectorAll('.maturity-distribution-legend-level'),
    ).map(n => n.textContent)
    expect(legend).toEqual(['L0', 'L1', 'L2', 'L3', 'L4', 'L5'])
  })
})
