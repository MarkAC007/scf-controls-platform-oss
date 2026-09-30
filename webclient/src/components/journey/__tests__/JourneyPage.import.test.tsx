/**
 * The "Import journey file" control: an org admin uploads a practitioner's
 * artefact and it goes to the import endpoint as `template`. Non-admins never
 * see it; a bad file is refused before any request; a server refusal is shown
 * with the server's own words.
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import JourneyPage from '../JourneyPage'
import { readJourneyArtefact } from '../journeyArtefact'
import type { JourneyResponse } from '../../../data/apiClient'

let journey: JourneyResponse
let isAdmin = true
const mutate = vi.fn()
const importState: { isPending: boolean; isError: boolean; isSuccess: boolean; error: Error | null; data: unknown } = {
  isPending: false, isError: false, isSuccess: false, error: null, data: undefined,
}

vi.mock('../../../hooks/useJourney', () => ({
  useJourney: () => ({ data: journey, isLoading: false, isError: false, error: null }),
  useImportJourney: () => ({ mutate, reset: vi.fn(), ...importState }),
  useAttestStage: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))

vi.mock('../../../hooks/useHasOrgRole', () => ({
  useHasOrgRole: (_org: string, role: string) => (role === 'admin' ? isAdmin : true),
  useIsOrgEditor: () => true,
}))

function response(overrides: Partial<JourneyResponse> = {}): JourneyResponse {
  return {
    provisioned: false,
    activated: false,
    activated_at: null,
    practitioner: null,
    name: 'Compliance journey',
    description: null,
    template_key: 'compliancegenie-default',
    template_version: '1',
    current_stage_key: null,
    stages: [],
    focus: [],
    ...overrides,
  }
}

const artefact = {
  template_key: 'cg-12-month',
  template_version: '2.0.0',
  name: 'Compliance transformation',
  stages: [
    { key: 'mobilise', title: 'Wave 0 — Mobilise', precondition_spec: [] },
    { key: 'determinants', title: 'Wave 1 — Determinants', precondition_spec: [] },
  ],
}

function pick(text: string, name = 'wave-plan.json') {
  const input = screen.getByLabelText('Import journey file') as HTMLInputElement
  const file = new File([text], name, { type: 'application/json' })
  fireEvent.change(input, { target: { files: [file] } })
}

beforeEach(() => {
  journey = response()
  isAdmin = true
  mutate.mockReset()
  Object.assign(importState, { isPending: false, isError: false, isSuccess: false, error: null, data: undefined })
})
afterEach(cleanup)

describe('who sees the control', () => {
  it('renders the file input for an org admin', () => {
    render(<JourneyPage organizationId="org-1" />)
    expect(screen.getByLabelText('Import journey file')).toBeTruthy()
  })

  it('does not render it for a non-admin', () => {
    isAdmin = false
    render(<JourneyPage organizationId="org-1" />)
    expect(screen.queryByLabelText('Import journey file')).toBeNull()
  })

  it('stays available after the first import, labelled as a re-issue', () => {
    journey = response({ provisioned: true, activated: true })
    render(<JourneyPage organizationId="org-1" />)
    expect(screen.getByText('Re-issue from file')).toBeTruthy()
  })
})

describe('a valid file', () => {
  it('posts the parsed artefact as template and activates a journey not yet started', async () => {
    render(<JourneyPage organizationId="org-1" />)
    pick(JSON.stringify(artefact))
    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1))
    const [body] = mutate.mock.calls[0]
    expect(body.template).toEqual(artefact)
    expect(body.activate).toBe(true)
    expect(body.template_key).toBeUndefined()
  })

  it('does not re-activate a journey that is already active', async () => {
    journey = response({ provisioned: true, activated: true })
    render(<JourneyPage organizationId="org-1" />)
    pick(JSON.stringify(artefact))
    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1))
    expect(mutate.mock.calls[0][0].activate).toBe(false)
  })

  it('reports what was imported', () => {
    Object.assign(importState, { isSuccess: true, data: { template_key: 'cg-12-month', template_version: '2.0.0' } })
    render(<JourneyPage organizationId="org-1" />)
    expect(screen.getByRole('status').textContent).toContain('Imported cg-12-month 2.0.0')
  })
})

describe('a bad file', () => {
  it('is refused before any request, with the reason', async () => {
    render(<JourneyPage organizationId="org-1" />)
    pick('{"name": "no stages here"}')
    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('the file has no stages'))
    expect(mutate).not.toHaveBeenCalled()
  })

  it('shows the server refusal in the server’s words', () => {
    Object.assign(importState, {
      isError: true,
      error: new Error('This deployment cannot evaluate these check types: vibes_acceptable'),
    })
    render(<JourneyPage organizationId="org-1" />)
    expect(screen.getByRole('alert').textContent).toContain('vibes_acceptable')
  })
})

describe('readJourneyArtefact', () => {
  it('rejects non-JSON', () => {
    expect(() => readJourneyArtefact('not json')).toThrow(/not valid JSON/)
  })
  it('rejects an array', () => {
    expect(() => readJourneyArtefact('[]')).toThrow(/journey object/)
  })
  it('rejects a stage without a key', () => {
    expect(() => readJourneyArtefact(JSON.stringify({ stages: [{ title: 'x' }] }))).toThrow(/stage 1 needs a key/)
  })
  it('passes a well-formed artefact through untouched', () => {
    expect(readJourneyArtefact(JSON.stringify(artefact))).toEqual(artefact)
  })
})
