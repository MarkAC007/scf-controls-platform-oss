/**
 * Read-back of preparer assertions (#786, #802).
 *
 * One property carries this whole component: **"not asserted" must be visible
 * as a fact.** A blank cell reads as an oversight or a rendering bug; the
 * explicit label reads as what it is — nobody made this claim, so nobody should
 * rely on it. Every test here is a way of checking that the distinction between
 * "asserted" and "nobody said" survives the render.
 */
import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { PreparerAssertionPanel } from '../PreparerAssertionPanel'
import { makeEvidenceFile } from './evidenceFileFixture'

afterEach(cleanup)

describe('PreparerAssertionPanel', () => {
  it('renders for a file with nothing asserted, rather than hiding', () => {
    // Hiding the panel would hide the finding: this evidence makes no claim
    // about its period, its population or where its data came from.
    render(<PreparerAssertionPanel file={makeEvidenceFile()} />)
    expect(screen.getByTestId('preparer-assertions-panel')).toBeTruthy()
  })

  it('names every unasserted field explicitly instead of leaving it blank', () => {
    render(<PreparerAssertionPanel file={makeEvidenceFile()} />)
    for (const testId of [
      'assertion-effective-period',
      'assertion-coverage',
      'assertion-population-source',
      'assertion-sample-method',
      'assertion-sample-basis',
      'assertion-ipe-system',
      'assertion-ipe-extracted-at',
      'assertion-ipe-query',
      'assertion-ipe-completeness',
    ]) {
      expect(screen.getByTestId(testId).textContent, testId).toBe('Not asserted')
    }
  })

  it('marks an unasserted value so it cannot be styled as a real one', () => {
    render(<PreparerAssertionPanel file={makeEvidenceFile()} />)
    expect(
      screen.getByTestId('assertion-effective-period').className,
    ).toContain('preparer-panel-value--unasserted')
  })

  it('renders an asserted period as a readable range', () => {
    render(
      <PreparerAssertionPanel
        file={makeEvidenceFile({
          effective_period_start: '2026-01-01',
          effective_period_end: '2026-03-31',
        })}
      />,
    )
    const period = screen.getByTestId('assertion-effective-period')
    expect(period.textContent).toContain('2026')
    expect(period.textContent).toContain('Jan')
    expect(period.textContent).toContain('Mar')
    expect(period.className).not.toContain('unasserted')
  })

  it('does not shift a date-only period across a timezone boundary', () => {
    // A bare '2026-01-01' parses as UTC midnight, which is 31 Dec 2025 west of
    // Greenwich. On a period boundary that is a wrong answer, not a cosmetic
    // one — the codebase's T00:00:00 guard is what keeps the day intact.
    render(
      <PreparerAssertionPanel
        file={makeEvidenceFile({
          effective_period_start: '2026-01-01',
          effective_period_end: '2026-01-01',
        })}
      />,
    )
    expect(screen.getByTestId('assertion-effective-period').textContent).toContain('1 Jan 2026')
  })

  it('treats a half-asserted period as no period at all', () => {
    // The API refuses to store one, but a row written by any other path must
    // not render as an open-ended claim nobody made.
    render(
      <PreparerAssertionPanel
        file={makeEvidenceFile({ effective_period_start: '2026-01-01' })}
      />,
    )
    expect(screen.getByTestId('assertion-effective-period').textContent).toBe('Not asserted')
  })

  it('states coverage as a fraction when both halves are asserted', () => {
    render(
      <PreparerAssertionPanel
        file={makeEvidenceFile({ population_size: 412, sample_size: 25 })}
      />,
    )
    expect(screen.getByTestId('assertion-coverage').textContent).toBe('25 of 412')
  })

  it('says the population is missing rather than reporting a bare sample size', () => {
    // "25" alone is exactly the inference an auditor will not make.
    render(<PreparerAssertionPanel file={makeEvidenceFile({ sample_size: 25 })} />)
    expect(screen.getByTestId('assertion-coverage').textContent).toMatch(
      /population not declared/i,
    )
  })

  it('says the sample is missing rather than implying full coverage', () => {
    render(<PreparerAssertionPanel file={makeEvidenceFile({ population_size: 412 })} />)
    expect(screen.getByTestId('assertion-coverage').textContent).toMatch(/sample not declared/i)
  })

  it('reports a population of zero as an assertion, not as an absence', () => {
    render(
      <PreparerAssertionPanel
        file={makeEvidenceFile({ population_size: 0, sample_size: 0 })}
      />,
    )
    expect(screen.getByTestId('assertion-coverage').textContent).toBe('0 of 0')
  })

  it('lets prose fields wrap instead of truncating the testable part', () => {
    render(
      <PreparerAssertionPanel
        file={makeEvidenceFile({
          ipe_query_or_filter:
            'Report: New Hires, hire_date between 2026-01-01 and 2026-03-31, status = active',
        })}
      />,
    )
    const query = screen.getByTestId('assertion-ipe-query')
    expect(query.className).toContain('preparer-panel-value--wrap')
    expect(query.textContent).toContain('status = active')
  })

  it('says out loud that these are claims, not measurements', () => {
    render(<PreparerAssertionPanel file={makeEvidenceFile()} />)
    expect(screen.getByText(/not measured by the platform/i)).toBeTruthy()
  })
})
