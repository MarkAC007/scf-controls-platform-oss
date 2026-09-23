/**
 * What the Journey screen says when there is no current stage.
 *
 * A path with every stage signed and one that was never started look identical
 * in the one field the screen consulted, so a finished journey announced "not
 * started" and offered the first stage as what comes next. These cases pin the
 * four things that must now be true of the completed state, and pin the two
 * states next to it that must survive unchanged.
 *
 * Every expectation is computed from the fixture the test built. No stage
 * count, title, date or locale is written into an assertion.
 */
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import JourneyPage from '../JourneyPage'
import type { JourneyResponse, JourneyStage, JourneyStageState } from '../../../data/apiClient'

let journey: JourneyResponse

vi.mock('../../../hooks/useJourney', () => ({
  useJourney: () => ({ data: journey, isLoading: false, isError: false, error: null }),
  useImportJourney: () => ({ mutate: vi.fn(), isPending: false }),
  useAttestStage: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))

// Reads AuthContext; without it the render dies on the provider rather than
// telling us anything about the completed state.
vi.mock('../../../hooks/useHasOrgRole', () => ({
  useHasOrgRole: () => true,
  useIsOrgEditor: () => true,
}))

interface StageSpec {
  state?: JourneyStageState
  signed?: boolean
  met?: number
  total?: number
  due?: string | null
  attestedAt?: string
}

function stage(ordinal: number, spec: StageSpec = {}): JourneyStage {
  const state = spec.state ?? 'passed'
  const signed = spec.signed ?? (state === 'passed' || state === 'passed_conditional')
  const total = spec.total ?? 0
  const met = spec.met ?? total
  return {
    id: `id-${ordinal}`,
    ordinal,
    key: `stage-${ordinal}`,
    title: `Mobilise the ${ordinal} wave`,
    summary: null,
    expect_next: null,
    state,
    started_at: null,
    attested_at: signed ? spec.attestedAt ?? '2026-08-14T09:00:00Z' : null,
    attested_by_user_id: null,
    attested_by_name: signed ? 'Dana Reeve' : null,
    attestation_note: null,
    target_date: spec.due ?? null,
    preconditions: {
      checks: [],
      met_count: met,
      total_count: total,
      unknown_count: 0,
      all_met: total === 0 ? true : met === total,
    },
  }
}

function response(stages: JourneyStage[], overrides: Partial<JourneyResponse> = {}): JourneyResponse {
  return {
    provisioned: true,
    activated: true,
    activated_at: '2026-01-05T09:00:00Z',
    practitioner: null,
    name: 'Your compliance journey',
    description: null,
    template_key: 'default',
    template_version: '1',
    current_stage_key: null,
    stages,
    focus: [],
    ...overrides,
  }
}

function panel(selector: string): string {
  const node = document.querySelector(selector)
  if (!node) throw new Error(`${selector} did not render`)
  return node.textContent ?? ''
}

const subtitle = () => panel('.journey-subtitle')
const today = () => panel('.journey-focus')
const comesNext = () => panel('.journey-next')

beforeEach(() => {
  journey = response([])
})
afterEach(cleanup)

describe('(a) every stage signed, unconditionally, every check still met', () => {
  // Four stages, so nothing can pass by assuming a template's length.
  const stages = [stage(1), stage(2, { met: 3, total: 3 }), stage(3), stage(4)]

  beforeEach(() => {
    journey = response(stages)
    render(<JourneyPage organizationId="org-1" />)
  })

  it('says every stage is attested, counting the stages it was given', () => {
    expect(subtitle()).toContain(`All ${stages.length} stages attested`)
  })

  it('does not say the journey has not started', () => {
    expect(screen.queryByText(/not started/i)).toBeNull()
  })

  it('does not claim an attestation is outstanding', () => {
    expect(screen.queryByText(/waiting on an attestation/i)).toBeNull()
  })

  it('tells the viewer nothing is waiting on them', () => {
    expect(today()).toContain('Nothing on the Journey is waiting on you')
  })

  // THE anti-wrap assertion: "what comes next" used to name stages[0].
  it('does not offer the first stage as what comes next', () => {
    expect(comesNext()).not.toContain(stages[0].title)
  })

  it('names the last signed stage and the date it was signed', () => {
    const last = stages[stages.length - 1]
    expect(comesNext()).toContain(last.title)
    expect(comesNext()).toContain(new Date(last.attested_at as string).toLocaleDateString())
  })
})

describe('(b) every stage signed, with conditions outstanding', () => {
  const stages = [
    stage(1),
    stage(2, { state: 'passed_conditional', met: 1, total: 4, due: '2026-10-22' }),
    stage(3, { state: 'passed_conditional', due: null }),
  ]
  const conditional = [stages[1], stages[2]]

  beforeEach(() => {
    journey = response(stages)
    render(<JourneyPage organizationId="org-1" />)
  })

  it('counts the conditions in the subtitle', () => {
    expect(subtitle()).toContain(`All ${stages.length} stages attested`)
    expect(subtitle()).toContain(`${conditional.length} with conditions outstanding`)
    expect(screen.queryByText(/not started/i)).toBeNull()
  })

  it('lists exactly one row per conditional stage', () => {
    const rows = document.querySelectorAll('.journey-focus .journey-focus-item')
    expect(rows).toHaveLength(conditional.length)
    for (const s of conditional) expect(today()).toContain(s.title)
  })

  it('renders each due date from the stage that carries it', () => {
    const due = stages[1].target_date as string
    expect(today()).toContain(new Date(due).toLocaleDateString())
    expect(today()).toContain('no due date recorded')
  })

  it('shows the live check position for a stage that declares checks', () => {
    const p = stages[1].preconditions
    expect(today()).toContain(`${p.met_count}/${p.total_count} checks met`)
  })

  it('points at closing the conditions, earliest first', () => {
    expect(comesNext()).toContain('Closing the outstanding conditions above')
    expect(comesNext()).toContain(new Date(stages[1].target_date as string).toLocaleDateString())
  })

  it('does not rewrite the signatures that were made', () => {
    // The stage row still reads as signed with conditions...
    const labels = [...document.querySelectorAll('.journey-stone-state')].map(n => n.textContent)
    expect(labels.filter(l => l === 'Passed with conditions')).toHaveLength(conditional.length)
    expect(today()).toContain('These signatures stand as recorded')
  })
})

describe('(c) a signed stage whose live checks have since regressed', () => {
  const stages = [stage(1), stage(2, { state: 'passed', met: 1, total: 2 }), stage(3)]
  const regressed = stages[1]

  beforeEach(() => {
    journey = response(stages)
    render(<JourneyPage organizationId="org-1" />)
  })

  it('counts it in the subtitle without calling it a conditional pass', () => {
    expect(subtitle()).toContain('1 signed stage no longer passing')
    expect(subtitle()).not.toContain('conditions outstanding')
  })

  it('surfaces it under its own heading, with the fixture’s own numbers', () => {
    expect(today()).toContain('Signed, but no longer passing')
    expect(today()).toContain(regressed.title)
    const p = regressed.preconditions
    expect(today()).toContain(`now ${p.met_count} of ${p.total_count} checks`)
  })

  it('leaves the stage row reading Complete', () => {
    const labels = [...document.querySelectorAll('.journey-stone-state')].map(n => n.textContent)
    expect(labels).toEqual(stages.map(() => 'Complete'))
  })

  it('does not claim an attestation is outstanding', () => {
    expect(screen.queryByText(/waiting on an attestation/i)).toBeNull()
  })

  it('names the remediation as what comes next, not a stage', () => {
    expect(comesNext()).toContain('Restoring the checks on the stage listed above')
    for (const s of stages) expect(comesNext()).not.toContain(s.title)
  })
})

describe('(b) and (c) together', () => {
  const stages = [
    stage(1, { state: 'passed_conditional', met: 2, total: 3, due: '2026-10-22' }),
    stage(2, { state: 'passed', met: 1, total: 2 }),
    stage(3),
  ]

  beforeEach(() => {
    journey = response(stages)
    render(<JourneyPage organizationId="org-1" />)
  })

  it('renders both blocks; neither suppresses the other', () => {
    expect(today()).toContain('passed with conditions that are still open')
    expect(today()).toContain('Signed, but no longer passing')
    expect(comesNext()).toContain('Closing the outstanding conditions above')
    expect(comesNext()).toContain('Restoring the checks on the stage listed above')
  })

  it('reports both counts in the subtitle', () => {
    expect(subtitle()).toContain('1 with conditions outstanding')
    expect(subtitle()).toContain('1 signed stage no longer passing')
  })
})

describe('the anomaly: no current stage, but stages unsigned', () => {
  const stages = [stage(1), stage(2, { state: 'locked', signed: false }), stage(3, { state: 'locked', signed: false })]

  beforeEach(() => {
    journey = response(stages)
    render(<JourneyPage organizationId="org-1" />)
  })

  // Scoped to the subtitle: the locked stage rows legitimately label themselves
  // "Not started", and it is the journey-level claim that was wrong.
  it('does not say the journey has not started', () => {
    expect(subtitle()).not.toMatch(/not started/i)
    expect(subtitle()).toContain('no stage is currently active')
  })

  it('says how much of the path is unsigned, from the payload', () => {
    const unsigned = stages.filter(s => !s.attested_at).length
    expect(today()).toContain(`${unsigned} of ${stages.length} stages are still unsigned`)
  })

  it('does not claim completion or offer the first stage', () => {
    expect(today()).not.toContain('Nothing on the Journey is waiting on you')
    expect(comesNext()).not.toContain(stages[0].title)
    expect(comesNext()).toContain('This is the last stage on the path')
  })
})

describe('the paths that must survive the fix', () => {
  it('a genuine awaiting-attestation stage still reads as waiting', () => {
    const stages = [
      stage(1),
      stage(2, { state: 'awaiting_attestation', signed: false, met: 2, total: 2 }),
      stage(3, { state: 'locked', signed: false }),
    ]
    journey = response(stages, { current_stage_key: 'stage-2' })
    render(<JourneyPage organizationId="org-1" />)

    expect(screen.getByText(/waiting on an attestation/i)).toBeTruthy()
    expect(subtitle()).toContain(`Stage 2 of ${stages.length}`)
    // The real next stage, not a wrap.
    expect(comesNext()).toContain(stages[2].title)
  })

  it('an unprovisioned journey still reads as not started', () => {
    const stages = [stage(1, { state: 'locked', signed: false }), stage(2, { state: 'locked', signed: false })]
    journey = response(stages, { provisioned: false, activated: false })
    render(<JourneyPage organizationId="org-1" />)

    expect(subtitle()).toContain(`${stages.length} stages · not started`)
  })

  it('a signed-off final stage still reports the last stage when one is current', () => {
    const stages = [stage(1), stage(2, { state: 'active', signed: false })]
    journey = response(stages, { current_stage_key: 'stage-2' })
    render(<JourneyPage organizationId="org-1" />)

    expect(comesNext()).toContain('This is the last stage on the path')
  })
})
