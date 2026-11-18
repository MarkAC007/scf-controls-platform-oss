// SPDX-License-Identifier: AGPL-3.0-only
/** The create and edit form: measure picking, wording, logged metrics, confirm-now, edit bodies, the metric lock, target checks. */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ObjectiveFormModal from '../ObjectiveFormModal'
import { measure, metric, objective, ORG } from './fixtures'

const api = vi.hoisted(() => ({
  createSecurityObjective: vi.fn(),
  updateSecurityObjective: vi.fn(),
  setObjectiveMetric: vi.fn(),
  confirmSecurityObjective: vi.fn(),
  listIsmsMeasures: vi.fn(),
  getMetricWording: vi.fn(),
}))
vi.mock('../../../../data/securityObjectivesApi', () => api)
const core = vi.hoisted(() => ({ listTeams: vi.fn(), getOrgMembers: vi.fn() }))
vi.mock('../../../../data/apiClient', () => core)
const role = vi.hoisted(() => ({ editor: true, admin: true }))
vi.mock('../../../../hooks/useHasOrgRole', () => ({
  useIsOrgEditor: () => role.editor,
  useHasOrgRole: () => role.admin,
}))
vi.mock('react-hot-toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
import { toast } from 'react-hot-toast'

const WORDING = {
  evaluation_method: 'Risks within appetite, measured nightly from the Risk Register.',
  metric_name: 'Risks within appetite (%)',
  metric_description: 'Live from the Risk Register, measured nightly.',
  target_text: 'at least 100%',
  target_label: 'at least 100% · warning below 90%',
}
const CREATED = objective({ id: 'obj-new', lifecycle_status: 'proposed' })

let invalidateSpy: ReturnType<typeof vi.spyOn>
function renderForm(o?: ReturnType<typeof objective>, onClose = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  invalidateSpy = vi.spyOn(client, 'invalidateQueries')
  render(
    <QueryClientProvider client={client}>
      <ObjectiveFormModal organizationId={ORG} objective={o} onClose={onClose} />
    </QueryClientProvider>,
  )
  return onClose
}

const nameField = () => screen.getByLabelText('Security objective')
const submitButton = () => screen.getByRole('button', { name: 'Save objective' })

describe('ObjectiveFormModal', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    role.editor = true
    role.admin = true
    api.listIsmsMeasures.mockResolvedValue([measure()])
    api.getMetricWording.mockResolvedValue(WORDING)
    api.createSecurityObjective.mockResolvedValue(CREATED)
    api.updateSecurityObjective.mockResolvedValue(objective())
    api.setObjectiveMetric.mockResolvedValue(objective())
    api.confirmSecurityObjective.mockResolvedValue(objective())
    core.listTeams.mockResolvedValue([{ id: 'team-ops', name: 'Security Operations' }])
    core.getOrgMembers.mockResolvedValue([{ id: 'u-1', email: 'pat@example.test', display_name: 'Pat Example' }])
  })

  it('writes the evaluation text and fills target and clauses when a measure is chosen', async () => {
    renderForm()
    fireEvent.change(nameField(), { target: { value: 'Keep risk within appetite' } })
    fireEvent.click(await screen.findByRole('radio', { name: 'Risks within appetite' }))
    expect(await screen.findByDisplayValue(WORDING.evaluation_method, undefined, { timeout: 2000 })).toBeInTheDocument()
    expect(screen.getByDisplayValue('Risks within appetite (%)')).toBeInTheDocument()
    expect(screen.getByLabelText('Target value')).toHaveValue(100)
    expect(screen.getByLabelText('Warning level')).toHaveValue(90)
    expect(screen.getByText('6.1.3')).toBeInTheDocument()
    expect(screen.getByText('8.3')).toBeInTheDocument()
  })

  it('leaves typed evaluation text alone when a later wording reply arrives', async () => {
    renderForm()
    fireEvent.change(nameField(), { target: { value: 'Keep risk within appetite' } })
    fireEvent.change(screen.getByLabelText(/^How results are evaluated/), { target: { value: 'Our own words.' } })
    fireEvent.click(await screen.findByRole('radio', { name: 'Risks within appetite' }))
    await waitFor(() => expect(api.getMetricWording).toHaveBeenCalled(), { timeout: 2000 })
    await screen.findByDisplayValue('Risks within appetite (%)', undefined, { timeout: 2000 })
    expect(screen.getByLabelText(/^How results are evaluated/)).toHaveValue('Our own words.')
  })

  it('sends a logged metric from "Log it ourselves"', async () => {
    const onClose = renderForm()
    fireEvent.change(nameField(), { target: { value: 'Train everyone' } })
    fireEvent.click(screen.getByRole('button', { name: 'Log it ourselves' }))
    fireEvent.change(screen.getByLabelText('Metric name'), { target: { value: 'Training completion' } })
    fireEvent.change(screen.getByLabelText('Unit'), { target: { value: 'percent' } })
    fireEvent.change(screen.getByLabelText('How often it is entered'), { target: { value: 'quarterly' } })
    fireEvent.change(screen.getByLabelText('Target value'), { target: { value: '95' } })
    fireEvent.click(submitButton())
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    const body = api.createSecurityObjective.mock.calls[0][1]
    expect(body.metric).toMatchObject({ kind: 'logged', name: 'Training completion', unit: 'percent', frequency: 'quarterly', target_value: 95 })
    expect(body.metric).not.toHaveProperty('measure_key')
    expect(body).not.toHaveProperty('evaluation_method')
  })

  it('blocks "Confirm now" without a team, in the form', async () => {
    renderForm()
    fireEvent.change(nameField(), { target: { value: 'Keep risk within appetite' } })
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Confirm now' }))
    expect(screen.getByText('Choose a responsible team before confirming')).toBeInTheDocument()
    expect(submitButton()).toBeDisabled()
    fireEvent.submit(submitButton().closest('form')!)
    expect(api.createSecurityObjective).not.toHaveBeenCalled()
  })

  it('creates, then confirms the created objective', async () => {
    const onClose = renderForm()
    fireEvent.change(nameField(), { target: { value: 'Keep risk within appetite' } })
    await screen.findByRole('option', { name: 'Security Operations' })
    fireEvent.change(screen.getByLabelText('Responsible team'), { target: { value: 'team-ops' } })
    fireEvent.click(await screen.findByRole('radio', { name: 'Risks within appetite' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Confirm now' }))
    fireEvent.click(submitButton())
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(api.confirmSecurityObjective).toHaveBeenCalledWith(ORG, 'obj-new', {})
    expect(api.createSecurityObjective.mock.invocationCallOrder[0])
      .toBeLessThan(api.confirmSecurityObjective.mock.invocationCallOrder[0])
    expect(api.createSecurityObjective.mock.calls[0][1]).toMatchObject({
      name: 'Keep risk within appetite', owning_team_id: 'team-ops',
      metric: { kind: 'live', measure_key: 'risk_within_appetite', target_value: 100, warn_value: 90 },
    })
  })

  it('S3: closes and says so when the confirm is refused after the create, with no retry', async () => {
    api.confirmSecurityObjective.mockRejectedValueOnce(new Error('Set a target before confirming'))
    const onClose = renderForm()
    fireEvent.change(nameField(), { target: { value: 'Keep risk within appetite' } })
    await screen.findByRole('option', { name: 'Security Operations' })
    fireEvent.change(screen.getByLabelText('Responsible team'), { target: { value: 'team-ops' } })
    fireEvent.click(await screen.findByRole('radio', { name: 'Risks within appetite' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Confirm now' }))
    fireEvent.click(submitButton())
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(toast.error).toHaveBeenCalledWith(expect.stringMatching(
      /saved as proposed but could not be confirmed: Set a target before confirming\. Confirm it from the register\./))
    expect(api.createSecurityObjective).toHaveBeenCalledTimes(1)
    expect(api.confirmSecurityObjective).toHaveBeenCalledTimes(1)
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['security-objectives', ORG] })
  })

  it('S3: refuses "Confirm now" with no metric chosen, in the form', async () => {
    renderForm()
    fireEvent.change(nameField(), { target: { value: 'Keep risk within appetite' } })
    await screen.findByRole('option', { name: 'Security Operations' })
    fireEvent.change(screen.getByLabelText('Responsible team'), { target: { value: 'team-ops' } })
    fireEvent.click(screen.getByRole('checkbox', { name: 'Confirm now' }))
    expect(screen.getByText('Choose a metric before confirming')).toBeInTheDocument()
    expect(submitButton()).toBeDisabled()
    fireEvent.submit(submitButton().closest('form')!)
    expect(api.createSecurityObjective).not.toHaveBeenCalled()
  })

  it('edits only the changed fields', async () => {
    const onClose = renderForm(objective())
    fireEvent.change(nameField(), { target: { value: 'Keep risk well within appetite' } })
    fireEvent.click(submitButton())
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(api.updateSecurityObjective).toHaveBeenCalledWith(ORG, 'obj-1', { name: 'Keep risk well within appetite' })
    expect(api.setObjectiveMetric).not.toHaveBeenCalled()
  })

  it('skips the PATCH when only the metric changed', async () => {
    const onClose = renderForm(objective())
    fireEvent.change(screen.getByLabelText('Target value'), { target: { value: '95' } })
    fireEvent.click(submitButton())
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(api.updateSecurityObjective).not.toHaveBeenCalled()
    expect(api.setObjectiveMetric).toHaveBeenCalledWith(ORG, 'obj-1', expect.objectContaining({
      kind: 'live', measure_key: 'risk_within_appetite', target_value: 95, warn_value: 90,
    }))
  })

  it('locks the metric of a confirmed objective for an editor who is not an admin', async () => {
    role.admin = false
    const onClose = renderForm(objective({ lifecycle_status: 'active' }))
    expect(screen.getByText('Only an admin can change the metric of a confirmed objective.')).toBeInTheDocument()
    expect(screen.getByTestId('metric-pane')).toBeDisabled()
    expect(screen.queryByRole('checkbox', { name: 'Confirm now' })).toBeNull()
    fireEvent.change(nameField(), { target: { value: 'Renamed' } })
    fireEvent.click(submitButton())
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(api.setObjectiveMetric).not.toHaveBeenCalled()
  })

  it('shows type and due date read-only on a confirmed objective for an editor who is not an admin', async () => {
    role.admin = false
    const onClose = renderForm(objective({ lifecycle_status: 'active', objective_type: 'achieve', due_date: '2027-03-31' }))
    expect(screen.getByText('Only an admin can change the type or due date of a confirmed objective.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Maintain' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Achieve by a date' })).toBeDisabled()
    expect(screen.getByLabelText('Complete by')).toBeDisabled()
    fireEvent.change(screen.getByLabelText('What will be done'), { target: { value: 'Treat the top ten.' } })
    fireEvent.click(submitButton())
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    // Only the changed field travels: never objective_type or due_date.
    expect(api.updateSecurityObjective).toHaveBeenCalledWith(ORG, 'obj-1', { planned_actions: 'Treat the top ten.' })
  })

  it('shows the status of a confirmed objective read-only for an editor who is not an admin', () => {
    role.admin = false
    renderForm(objective({ lifecycle_status: 'active' }))
    expect(screen.getByLabelText('Status')).toBeDisabled()
    expect(screen.getByText('Only an admin can archive, restore or close a confirmed objective.')).toBeInTheDocument()
  })

  it('leaves the status of a confirmed objective open to an admin', async () => {
    const onClose = renderForm(objective({ lifecycle_status: 'active' }))
    expect(screen.getByLabelText('Status')).toBeEnabled()
    expect(screen.queryByText('Only an admin can archive, restore or close a confirmed objective.')).toBeNull()
    fireEvent.change(screen.getByLabelText('Status'), { target: { value: 'achieved' } })
    fireEvent.click(submitButton())
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(api.updateSecurityObjective).toHaveBeenCalledWith(ORG, 'obj-1', { lifecycle_status: 'achieved' })
  })

  it('leaves type and due date open to an admin', () => {
    renderForm(objective({ lifecycle_status: 'active', objective_type: 'achieve', due_date: '2027-03-31' }))
    expect(screen.queryByText('Only an admin can change the type or due date of a confirmed objective.')).toBeNull()
    expect(screen.getByLabelText('Complete by')).toBeEnabled()
  })

  it('shows the server sentence when an edit is refused', async () => {
    api.updateSecurityObjective.mockRejectedValue(new Error('Only an admin can change the type or due date of a confirmed objective'))
    const onClose = renderForm(objective({ lifecycle_status: 'active' }))
    fireEvent.change(nameField(), { target: { value: 'Renamed' } })
    fireEvent.click(submitButton())
    expect(await screen.findByRole('alert')).toHaveTextContent('Only an admin can change the type or due date of a confirmed objective')
    expect(onClose).not.toHaveBeenCalled()
  })

  it('lists each measure with today’s value and population, and marks the best match', async () => {
    api.listIsmsMeasures.mockResolvedValue([
      measure(),
      measure({
        key: 'mfa_coverage', name: 'MFA coverage', match: 0,
        today: { value: 3, value_text: '3', population: '3 of 40 accounts', segments: [] },
      }),
    ])
    renderForm()
    const first = (await screen.findByRole('radio', { name: 'Risks within appetite' })).closest('label')!
    expect(within(first).getByText('14%')).toBeInTheDocument()
    expect(within(first).getByText(/9 of 66 risks/)).toBeInTheDocument()
    expect(within(first).getByText('best match')).toBeInTheDocument()
    const second = screen.getByRole('radio', { name: 'MFA coverage' }).closest('label')!
    expect(within(second).getByText('3')).toBeInTheDocument()
    expect(within(second).getByText(/3 of 40 accounts/)).toBeInTheDocument()
    expect(within(second).queryByText('best match')).toBeNull()
  })

  it('sends a milestone metric with the due date from "Milestone"', async () => {
    const onClose = renderForm()
    fireEvent.change(nameField(), { target: { value: 'Achieve certification' } })
    fireEvent.click(screen.getByRole('button', { name: 'Milestone' }))
    expect(screen.getByText('Done or not done by the due date.')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Complete by'), { target: { value: '2027-03-31' } })
    fireEvent.click(submitButton())
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    const body = api.createSecurityObjective.mock.calls[0][1]
    expect(body).toMatchObject({ name: 'Achieve certification', objective_type: 'achieve', due_date: '2027-03-31' })
    expect(body.metric).toMatchObject({ kind: 'milestone' })
    expect(body.metric).not.toHaveProperty('measure_key')
    expect(body.metric).not.toHaveProperty('target_value')
  })

  it('S1: picking a different measure on an existing objective refills name, evaluation, target, warning and clauses', async () => {
    api.listIsmsMeasures.mockResolvedValue([
      measure(),
      measure({
        key: 'response_tests_passed', name: 'Response tests passed', unit: 'count', direction: 'at_least',
        suggested_target: 2, suggested_warn: 1, clauses: ['5.24'], match: 0,
        today: { value: 2, value_text: '2', population: 'in the last 12 months', segments: [] },
      }),
    ])
    api.getMetricWording.mockImplementation(async (_org: string, body: { measure_key?: string }) =>
      body.measure_key === 'response_tests_passed'
        ? { ...WORDING, evaluation_method: 'Response tests passed in the last 12 months.', metric_name: 'Response tests passed' }
        : WORDING)
    const onClose = renderForm(objective({ evaluation_method: 'Risks whose residual level is acceptable.' }))
    fireEvent.click(await screen.findByRole('radio', { name: 'Response tests passed' }))
    expect(screen.getByLabelText('Target value')).toHaveValue(2)
    expect(screen.getByLabelText('Warning level')).toHaveValue(1)
    expect(screen.getByText('5.24')).toBeInTheDocument()
    expect(screen.queryByText('6.1.3')).toBeNull()
    expect(await screen.findByDisplayValue('Response tests passed in the last 12 months.', undefined, { timeout: 2000 }))
      .toBeInTheDocument()
    expect(screen.getByLabelText(/^Supporting ISMS metric/)).toHaveValue('Response tests passed')
    fireEvent.click(submitButton())
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(api.setObjectiveMetric).toHaveBeenCalledWith(ORG, 'obj-1', expect.objectContaining({
      kind: 'live', measure_key: 'response_tests_passed', name: 'Response tests passed',
      target_value: 2, warn_value: 1, evaluation_method: 'Response tests passed in the last 12 months.',
    }))
    expect(api.updateSecurityObjective).toHaveBeenCalledWith(ORG, 'obj-1', { iso_clauses: ['5.24'] })
  })

  it('S1: switching an existing objective to "Log it ourselves" clears the old name and target', async () => {
    renderForm(objective())
    fireEvent.click(screen.getByRole('button', { name: 'Log it ourselves' }))
    expect(screen.getByLabelText('Metric name')).toHaveValue('')
    expect(screen.getByLabelText('Target value')).toHaveValue(null)
    expect(screen.getByLabelText('Warning level')).toHaveValue(null)
  })

  it('S2: in edit, a generated evaluation sentence follows a new target', async () => {
    api.getMetricWording.mockImplementation(async (_org: string, body: { target_value?: number | null }) => ({
      ...WORDING,
      evaluation_method: `Target: at least ${body.target_value}%. Measured nightly.`,
    }))
    const onClose = renderForm(objective({ evaluation_method: 'Target: at least 100%. Measured nightly.' }))
    await waitFor(() => expect(api.getMetricWording).toHaveBeenCalled())
    fireEvent.change(screen.getByLabelText('Target value'), { target: { value: '90' } })
    fireEvent.change(screen.getByLabelText('Warning level'), { target: { value: '80' } })
    expect(await screen.findByDisplayValue('Target: at least 90%. Measured nightly.', undefined, { timeout: 2000 }))
      .toBeInTheDocument()
    fireEvent.click(submitButton())
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(api.setObjectiveMetric).toHaveBeenCalledWith(ORG, 'obj-1', expect.objectContaining({
      target_value: 90, evaluation_method: 'Target: at least 90%. Measured nightly.',
    }))
  })

  it('S2: in edit, a sentence a person wrote is kept when the target changes', async () => {
    api.getMetricWording.mockImplementation(async (_org: string, body: { target_value?: number | null }) => ({
      ...WORDING, evaluation_method: `Target: at least ${body.target_value}%.`,
    }))
    renderForm(objective({ evaluation_method: 'Reviewed monthly by the risk committee.' }))
    await waitFor(() => expect(api.getMetricWording).toHaveBeenCalled())
    fireEvent.change(screen.getByLabelText('Target value'), { target: { value: '95' } })
    await waitFor(() => expect(api.getMetricWording).toHaveBeenCalledTimes(2), { timeout: 2000 })
    await new Promise((r) => setTimeout(r, 50))
    expect(screen.getByLabelText(/^How results are evaluated/)).toHaveValue('Reviewed monthly by the risk committee.')
  })

  it('M2: refreshes the objectives when an edit fails part way', async () => {
    api.setObjectiveMetric.mockRejectedValue(new Error('Only an admin can change the metric'))
    renderForm(objective())
    fireEvent.change(nameField(), { target: { value: 'Renamed' } })
    fireEvent.change(screen.getByLabelText('Target value'), { target: { value: '95' } })
    fireEvent.click(submitButton())
    expect(await screen.findByRole('alert')).toHaveTextContent('Only an admin can change the metric')
    expect(api.updateSecurityObjective).toHaveBeenCalled()
    await waitFor(() => expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['security-objectives', ORG] }))
  })

  it('M5: a measure’s settings are not labels inside the card’s label', async () => {
    api.listIsmsMeasures.mockResolvedValue([measure({
      params: [{ key: 'days', label: 'within days', kind: 'number', default: 14 }],
    })])
    renderForm()
    fireEvent.click(await screen.findByRole('radio', { name: 'Risks within appetite' }))
    expect(screen.getByRole('spinbutton', { name: 'within days' })).toHaveValue(14)
    expect(document.querySelector('label label')).toBeNull()
  })

  it('M6: refuses a warning level with no target', async () => {
    renderForm()
    fireEvent.change(nameField(), { target: { value: 'Keep risk within appetite' } })
    fireEvent.click(await screen.findByRole('radio', { name: 'Risks within appetite' }))
    fireEvent.change(screen.getByLabelText('Target value'), { target: { value: '' } })
    expect(screen.getByText('Set a target before a warning level.')).toBeInTheDocument()
    expect(submitButton()).toBeDisabled()
  })

  it('M6: switching back to Maintain does not send the hidden due date', async () => {
    const onClose = renderForm()
    fireEvent.change(nameField(), { target: { value: 'Keep risk within appetite' } })
    fireEvent.click(screen.getByRole('button', { name: 'Achieve by a date' }))
    fireEvent.change(screen.getByLabelText('Complete by'), { target: { value: '2027-03-31' } })
    fireEvent.click(screen.getByRole('button', { name: 'Maintain' }))
    fireEvent.click(submitButton())
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(api.createSecurityObjective.mock.calls[0][1]).toMatchObject({ objective_type: 'maintain', due_date: null })
  })

  it('M6: switching an edit from Achieve to Maintain clears the due date it no longer shows', async () => {
    const onClose = renderForm(objective({ objective_type: 'achieve', due_date: '2027-03-31', review_date: null }))
    fireEvent.click(screen.getByRole('button', { name: 'Maintain' }))
    fireEvent.click(submitButton())
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(api.updateSecurityObjective).toHaveBeenCalledWith(ORG, 'obj-1', { objective_type: 'maintain', due_date: null })
  })

  it('checks the target before saving', async () => {
    renderForm()
    fireEvent.change(nameField(), { target: { value: 'Keep risk within appetite' } })
    fireEvent.click(await screen.findByRole('radio', { name: 'Risks within appetite' }))
    fireEvent.change(screen.getByLabelText('Target value'), { target: { value: '120' } })
    expect(screen.getByText('A percentage target must be between 0 and 100.')).toBeInTheDocument()
    expect(submitButton()).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Target value'), { target: { value: '80' } })
    fireEvent.change(screen.getByLabelText('Warning level'), { target: { value: '85' } })
    expect(screen.getByText('The warning level must sit below the target.')).toBeInTheDocument()
    expect(submitButton()).toBeDisabled()
  })
})
