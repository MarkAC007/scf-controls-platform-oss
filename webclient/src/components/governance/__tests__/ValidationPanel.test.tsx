import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ValidationPanel from '../ValidationPanel'
import type { ControlValidation, ControlValidationList } from '../../../types'

const state = vi.hoisted(() => ({ editor: true }))
const api = vi.hoisted(() => ({
  listControlValidations: vi.fn(),
  recordControlValidation: vi.fn(),
  getOrgMembers: vi.fn(),
}))

vi.mock('../../../data/apiClient', () => api)
vi.mock('../../../hooks/useHasOrgRole', () => ({ useIsOrgEditor: () => state.editor }))
vi.mock('react-hot-toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const row = (over: Partial<ControlValidation> = {}): ControlValidation => ({
  scoped_control_id: 'sc-1',
  scf_id: 'TESTCONTROL-01',
  validation_cadence: 'Quarterly',
  last_validated_at: '2026-09-20T10:15:00+00:00',
  last_validated_by: 'user-1',
  last_validation_note: 'Sampled ten joiners against HR records.\nAll matched.',
  next_validation_due: '2026-12-20',
  validation_state: 'current',
  ...over,
})

const list = (controls: ControlValidation[]): ControlValidationList => ({
  counts: { current: 0, due_soon: 0, overdue: 0, never: 0, unknown_cadence: 0 },
  controls,
})

/** Sets the browser's zone and clock. Node re-reads TZ when it is assigned; the check proves Date follows it. */
function clockAt(zone: string, utc: string) {
  vi.stubEnv('TZ', zone)
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(utc))
  const hour = new Intl.DateTimeFormat('en-US', { timeZone: zone, hour: 'numeric', hourCycle: 'h23' }).format(new Date())
  expect(new Date().getHours()).toBe(Number(hour))
}

function restoreClock() {
  vi.useRealTimers()
  vi.unstubAllEnvs()
}

function renderPanel(client = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  return render(
    <QueryClientProvider client={client}>
      <ValidationPanel organizationId="org-one" scfId="TESTCONTROL-01" />
    </QueryClientProvider>,
  )
}

describe('ValidationPanel (PIG R10)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    state.editor = true
    api.getOrgMembers.mockResolvedValue([{ id: 'user-1', email: 'ana@example.com', display_name: 'Ana Silva' }])
    api.listControlValidations.mockResolvedValue(list([row()]))
  })
  afterEach(restoreClock)

  it("shows this control's schedule, asked of the server by control", async () => {
    renderPanel()
    const panel = await screen.findByRole('region', { name: 'Validation' })
    expect(api.listControlValidations).toHaveBeenCalledWith('org-one', { scf_id: 'TESTCONTROL-01' })
    expect(await within(panel).findByText('Current')).toHaveClass('validation-chip--current')
    expect(within(panel).getByText('Quarterly')).toBeInTheDocument()
    expect(within(panel).getByText('Dec 20, 2026')).toBeInTheDocument()
    expect(await within(panel).findByText(/Sep 20, 2026 by Ana Silva/)).toBeInTheDocument()
    expect(within(panel).getByText(/Sampled ten joiners/)).toHaveTextContent('All matched.')
  })

  it('names an account outside the member list as such, such as an API key', async () => {
    api.listControlValidations.mockResolvedValue(list([row({ last_validated_by: 'machine-user' })]))
    renderPanel()
    expect(await screen.findByText('Sep 20, 2026 by a non-member account')).toBeInTheDocument()
  })

  it('names nobody until the members are read', async () => {
    api.getOrgMembers.mockReturnValue(new Promise(() => {}))
    renderPanel()
    expect(await screen.findByText('Sep 20, 2026')).toBeInTheDocument()
  })

  it('says plainly when the members cannot be read, rather than dropping who validated it', async () => {
    api.getOrgMembers.mockRejectedValue(new Error('Service unavailable'))
    renderPanel()
    expect(
      await screen.findByText('Sep 20, 2026 by an account that could not be named (the member list did not load)'),
    ).toBeInTheDocument()
  })

  it('dates the last validation by the same calendar as the next due date: the server\'s', async () => {
    // 23:30 UTC on 13 October is 00:30 on the 14th in London; the server dated it the 13th.
    clockAt('Europe/London', '2026-10-14T12:00:00Z')
    api.listControlValidations.mockResolvedValue(
      list([row({ last_validated_at: '2026-10-13T23:30:00+00:00', next_validation_due: '2027-01-13' })]),
    )
    renderPanel()
    expect(await screen.findByText('Oct 13, 2026 by Ana Silva')).toBeInTheDocument()
    expect(screen.getByText('Jan 13, 2027')).toBeInTheDocument()
  })

  it.each([
    ['due_soon', 'Due soon', /within 30 days/],
    ['overdue', 'Overdue', /past its due date/],
  ] as const)('marks a %s validation', async (validationState, label, hint) => {
    api.listControlValidations.mockResolvedValue(list([row({ validation_state: validationState })]))
    renderPanel()
    expect(await screen.findByText(label)).toHaveClass(`validation-chip--${validationState}`)
    expect(screen.getByText(hint)).toBeInTheDocument()
  })

  it('says a control never validated does not count as validated', async () => {
    api.listControlValidations.mockResolvedValue(
      list([row({ validation_state: 'never', last_validated_at: null, last_validated_by: null, last_validation_note: null, next_validation_due: null })]),
    )
    renderPanel()
    expect(await screen.findByText('Never validated')).toBeInTheDocument()
    expect(screen.getByText(/does not count as validated/)).toBeInTheDocument()
    expect(screen.queryByText('Last validated')).not.toBeInTheDocument()
  })

  it('says why no next date can be given for a cadence the platform does not know', async () => {
    api.listControlValidations.mockResolvedValue(
      list([row({ validation_state: 'unknown_cadence', validation_cadence: 'As needed', next_validation_due: null })]),
    )
    renderPanel()
    expect(await screen.findByText('Cadence unknown')).toBeInTheDocument()
    expect(screen.getByText(/"As needed" is not a cadence/)).toBeInTheDocument()
    expect(screen.getByText('As needed')).toBeInTheDocument()
  })

  it('offers no record button on a control the organisation has not scoped', async () => {
    api.listControlValidations.mockResolvedValue(list([]))
    renderPanel()
    expect(await screen.findByText(/not in scope, so it has no validation schedule/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Record validation' })).not.toBeInTheDocument()
  })

  it('opens the record form for an editor', async () => {
    renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: 'Record validation' }))
    expect(screen.getByRole('dialog', { name: 'Record validation' })).toBeInTheDocument()
  })

  it('offers a viewer the schedule but not the form', async () => {
    state.editor = false
    renderPanel()
    expect(await screen.findByText('Current')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Record validation' })).not.toBeInTheDocument()
    expect(screen.getByText('Only an editor or admin can record a validation.')).toBeInTheDocument()
  })

  it.each([
    ['the control is not in scope', () => api.listControlValidations.mockResolvedValue(list([]))],
    ['the schedule is loading', () => api.listControlValidations.mockReturnValue(new Promise(() => {}))],
    ['the schedule cannot be read', () => api.listControlValidations.mockRejectedValue(new Error('Boom'))],
  ])('tells a viewer nothing about editors when %s', async (_case, arrange) => {
    state.editor = false
    arrange()
    renderPanel()
    await screen.findByRole('region', { name: 'Validation' })
    await waitFor(() => expect(api.listControlValidations).toHaveBeenCalled())
    await new Promise((r) => setTimeout(r, 0))
    expect(screen.queryByText('Only an editor or admin can record a validation.')).not.toBeInTheDocument()
  })

  it('says when the schedule cannot be read', async () => {
    api.listControlValidations.mockRejectedValue(new Error('Not a member of this organization'))
    renderPanel()
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Could not load the validation schedule: Not a member of this organization',
    )
  })

  it('reads the schedule afresh on every visit, since the server dates it against today', async () => {
    // The app's own default: data stays fresh for five minutes unless a query says otherwise.
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 5 * 60_000 } } })
    const first = renderPanel(client)
    await screen.findByText('Current')
    first.unmount()
    renderPanel(client)
    await waitFor(() => expect(api.listControlValidations).toHaveBeenCalledTimes(2))
  })
})
