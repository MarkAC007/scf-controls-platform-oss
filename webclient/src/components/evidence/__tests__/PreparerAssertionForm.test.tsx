/**
 * Preparer assertion capture (#786, #802).
 *
 * The form records what a *person* claims the evidence is evidence of. Its
 * whole reason for existing is that the platform cannot derive any of it: a
 * period cannot be inferred from an upload date, a population cannot be counted
 * from a file, and provenance cannot be read off the bytes.
 *
 * So the properties worth pinning are about what the form does with *absence*
 * — a blank field must travel as "not asserted" and never as an empty string —
 * and about the two coherence rules, which have to be caught before a large
 * upload rather than after it.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'
import { afterEach } from 'vitest'
import {
  PreparerAssertionForm,
  EMPTY_ASSERTIONS,
  assertionErrors,
  hasAnyAssertion,
  toAssertionPayload,
  type AssertionFormValues,
} from '../PreparerAssertionForm'

afterEach(cleanup)

function values(overrides: Partial<AssertionFormValues> = {}): AssertionFormValues {
  return { ...EMPTY_ASSERTIONS, ...overrides }
}

describe('toAssertionPayload', () => {
  it('omits every blank field rather than sending an empty string', () => {
    // Absent, not present-and-undefined. The payload is spread into the confirm
    // body, and a body listing eleven keys nobody filled in reads as eleven
    // assertions that were made and then cleared.
    expect(Object.keys(toAssertionPayload(EMPTY_ASSERTIONS))).toEqual([])
  })

  it('carries only the fields that were actually typed into', () => {
    const payload = toAssertionPayload(values({ populationSize: '412', sampleMethod: 'random' }))
    expect(Object.keys(payload).sort()).toEqual(['population_size', 'sample_method'])
  })

  it('omits a field the user typed only whitespace into', () => {
    const payload = toAssertionPayload(values({ populationSource: '   ' }))
    expect(payload.population_source).toBeUndefined()
  })

  it('trims what it does send', () => {
    const payload = toAssertionPayload(values({ ipeSourceSystem: '  Workday  ' }))
    expect(payload.ipe_source_system).toBe('Workday')
  })

  it('sends counts as numbers, not as the strings the inputs hold', () => {
    const payload = toAssertionPayload(values({ populationSize: '412', sampleSize: '25' }))
    expect(payload.population_size).toBe(412)
    expect(payload.sample_size).toBe(25)
  })

  it('sends a declared population of zero, which is an assertion not an absence', () => {
    // "There was nothing to sample this quarter" is a real, testable claim.
    // Coercing it to undefined would erase it.
    const payload = toAssertionPayload(values({ populationSize: '0' }))
    expect(payload.population_size).toBe(0)
  })

  it('carries the whole period when both ends are given', () => {
    const payload = toAssertionPayload(
      values({ effectivePeriodStart: '2026-01-01', effectivePeriodEnd: '2026-03-31' }),
    )
    expect(payload.effective_period_start).toBe('2026-01-01')
    expect(payload.effective_period_end).toBe('2026-03-31')
  })
})

describe('hasAnyAssertion', () => {
  it('is false for an untouched form', () => {
    expect(hasAnyAssertion(EMPTY_ASSERTIONS)).toBe(false)
  })

  it('is true once anything at all is asserted', () => {
    expect(hasAnyAssertion(values({ sampleMethod: 'random' }))).toBe(true)
  })

  it('is false when the only content is whitespace', () => {
    expect(hasAnyAssertion(values({ sampleBasis: '  ' }))).toBe(false)
  })
})

describe('assertionErrors', () => {
  it('accepts an untouched form — asserting nothing is legitimate', () => {
    expect(assertionErrors(EMPTY_ASSERTIONS)).toEqual([])
  })

  it('rejects a period start with no end', () => {
    const errors = assertionErrors(values({ effectivePeriodStart: '2026-01-01' }))
    expect(errors).toHaveLength(1)
    expect(errors[0]).toMatch(/end date/i)
  })

  it('rejects a period end with no start', () => {
    const errors = assertionErrors(values({ effectivePeriodEnd: '2026-01-01' }))
    expect(errors).toHaveLength(1)
    expect(errors[0]).toMatch(/start date/i)
  })

  it('rejects a period that runs backwards', () => {
    const errors = assertionErrors(
      values({ effectivePeriodStart: '2026-03-01', effectivePeriodEnd: '2026-01-01' }),
    )
    expect(errors[0]).toMatch(/cannot be before/i)
  })

  it('accepts a single-day period', () => {
    expect(
      assertionErrors(
        values({ effectivePeriodStart: '2026-02-02', effectivePeriodEnd: '2026-02-02' }),
      ),
    ).toEqual([])
  })

  it('rejects a sample larger than its population', () => {
    const errors = assertionErrors(values({ populationSize: '2', sampleSize: '5' }))
    expect(errors[0]).toMatch(/cannot exceed/i)
  })

  it('accepts a sample equal to its population — testing everything is not an error', () => {
    expect(assertionErrors(values({ populationSize: '12', sampleSize: '12' }))).toEqual([])
  })

  it('does not complain about a sample with no declared population', () => {
    // Incomplete, not incoherent. The preparer may not know the denominator yet.
    expect(assertionErrors(values({ sampleSize: '25' }))).toEqual([])
  })
})

describe('PreparerAssertionForm', () => {
  it('starts collapsed, so the drop zone is not buried under a form', () => {
    render(
      <PreparerAssertionForm
        values={EMPTY_ASSERTIONS}
        onChange={vi.fn()}
        expanded={false}
        onToggle={vi.fn()}
      />,
    )
    expect(screen.queryByTestId('assertion-period-start')).toBeNull()
    expect(screen.getByTestId('preparer-assertions-toggle')).toHaveAttribute(
      'aria-expanded',
      'false',
    )
  })

  it('says plainly that a blank field is recorded as not asserted', () => {
    render(
      <PreparerAssertionForm
        values={EMPTY_ASSERTIONS}
        onChange={vi.fn()}
        expanded
        onToggle={vi.fn()}
      />,
    )
    expect(screen.getByText(/not asserted/i)).toBeTruthy()
  })

  it('tells the user when values are staged for the next upload', () => {
    render(
      <PreparerAssertionForm
        values={values({ sampleMethod: 'random' })}
        onChange={vi.fn()}
        expanded={false}
        onToggle={vi.fn()}
      />,
    )
    expect(screen.getByText(/applied to the next file/i)).toBeTruthy()
  })

  it('announces a coherence failure to assistive technology, not just visually', () => {
    render(
      <PreparerAssertionForm
        values={values({ populationSize: '2', sampleSize: '5' })}
        onChange={vi.fn()}
        expanded
        onToggle={vi.fn()}
      />,
    )
    const errors = screen.getByTestId('preparer-assertions-errors')
    expect(errors.getAttribute('role')).toBe('alert')
    expect(errors.textContent).toMatch(/cannot exceed/i)
  })

  it('reports a change without discarding the other fields', () => {
    const onChange = vi.fn()
    render(
      <PreparerAssertionForm
        values={values({ populationSize: '400' })}
        onChange={onChange}
        expanded
        onToggle={vi.fn()}
      />,
    )
    fireEvent.change(screen.getByTestId('assertion-sample-size'), { target: { value: '25' } })
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ populationSize: '400', sampleSize: '25' }),
    )
  })

  it('constrains the date inputs to each other so the pair cannot invert by picker', () => {
    render(
      <PreparerAssertionForm
        values={values({ effectivePeriodStart: '2026-01-01', effectivePeriodEnd: '2026-03-31' })}
        onChange={vi.fn()}
        expanded
        onToggle={vi.fn()}
      />,
    )
    expect(screen.getByTestId('assertion-period-start').getAttribute('max')).toBe('2026-03-31')
    expect(screen.getByTestId('assertion-period-end').getAttribute('min')).toBe('2026-01-01')
  })
})
