/**
 * A stage's checks say, in words, whether each is met, and show the server's
 * `detail` beside it. The glyph alone was the only met/unmet signal (an
 * aria-hidden dot for "not met"), and the detail sat in a no-wrap column that
 * ran off a phone screen.
 */
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import JourneyPage from '../JourneyPage'
import type { JourneyPreconditionCheck, JourneyResponse } from '../../../data/apiClient'

let journey: JourneyResponse

vi.mock('../../../hooks/useJourney', () => ({
  useJourney: () => ({ data: journey, isLoading: false, isError: false, error: null }),
  useImportJourney: () => ({ mutate: vi.fn(), isPending: false }),
  useAttestStage: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))

vi.mock('../../../hooks/useHasOrgRole', () => ({
  useHasOrgRole: () => true,
  useIsOrgEditor: () => true,
}))

const CHECKS: JourneyPreconditionCheck[] = [
  { type: 'controls_at_target', label: 'AST at target', met: false, detail: '13 of 22 in AST at target maturity (59.1%) — 22 required, 9 more needed' },
  { type: 'capability_tests_within_days', label: 'A test this year', met: true, detail: '1 capability test in the last 365 days — 1 required' },
  { type: 'future_check', label: 'Something new', met: null, detail: "This deployment cannot evaluate a 'future_check' check" },
]

function build(): JourneyResponse {
  return {
    provisioned: true,
    activated: true,
    activated_at: '2026-01-05T09:00:00Z',
    practitioner: null,
    name: 'Your compliance journey',
    description: null,
    template_key: 'default',
    template_version: '1',
    current_stage_key: 'gate',
    stages: [
      {
        id: 'id-1',
        ordinal: 1,
        key: 'gate',
        title: 'Validated and tested',
        summary: null,
        expect_next: null,
        state: 'active',
        started_at: null,
        attested_at: null,
        attested_by_user_id: null,
        attested_by_name: null,
        attestation_note: null,
        target_date: null,
        preconditions: { checks: CHECKS, met_count: 1, total_count: 3, unknown_count: 1, all_met: false },
      },
    ],
    focus: CHECKS.filter((c) => c.met !== true),
  }
}

afterEach(cleanup)

function expandStage() {
  fireEvent.click(screen.getByRole('button', { name: /Validated and tested/ }))
  return document.querySelector('.journey-check-list') as HTMLElement
}

describe('JourneyPage check rows', () => {
  it('names each check with its state in words and its detail', () => {
    journey = build()
    render(<JourneyPage organizationId="org-1" />)
    const list = expandStage()
    const rows = within(list).getAllByRole('listitem')
    expect(rows).toHaveLength(CHECKS.length)
    const expected = ['Not met', 'Met', 'Not evaluated']
    rows.forEach((row, i) => {
      expect(row).toHaveTextContent(CHECKS[i].label)
      expect(row).toHaveTextContent(CHECKS[i].detail)
      expect(row.querySelector('.journey-check-state')).toHaveTextContent(expected[i])
    })
  })

  it('keeps the glyph decorative, so the state is read once', () => {
    journey = build()
    render(<JourneyPage organizationId="org-1" />)
    const list = expandStage()
    list.querySelectorAll('.journey-check-glyph').forEach((glyph) => {
      expect(glyph).toHaveAttribute('aria-hidden', 'true')
    })
  })

  it("states each outstanding check in today's focus too", () => {
    journey = build()
    render(<JourneyPage organizationId="org-1" />)
    const items = document.querySelectorAll('.journey-focus-item')
    expect(items).toHaveLength(2)
    expect(items[0].querySelector('.journey-check-state')).toHaveTextContent('Not met')
    expect(items[0]).toHaveTextContent(CHECKS[0].detail)
    expect(items[1].querySelector('.journey-check-state')).toHaveTextContent('Not evaluated')
  })

  it('separates label, state and detail for assistive tech, in both places', () => {
    journey = build()
    render(<JourneyPage organizationId="org-1" />)
    const focus = Array.from(document.querySelectorAll('.journey-focus-item'))
    expect(focus.map((item) => item.textContent)).toEqual([
      `${CHECKS[0].label}, Not met: ${CHECKS[0].detail}`,
      `${CHECKS[2].label}, Not evaluated: ${CHECKS[2].detail}`,
    ])
    const rows = within(expandStage()).getAllByRole('listitem')
    expect(rows[1].textContent).toContain(`${CHECKS[1].label}, Met: ${CHECKS[1].detail}`)
  })
})
