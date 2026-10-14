import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import CompensatingControlFormModal from '../CompensatingControlFormModal'
import type { CompensatingControl, RiskAssessment } from '../../../types'

const api = vi.hoisted(() => ({
  createCompensatingControl: vi.fn(),
  updateCompensatingControl: vi.fn(),
  getRisksForControl: vi.fn(),
  getCustomRiskDefinitions: vi.fn(),
  getCustomRiskControlLinks: vi.fn(),
  getRiskAssessments: vi.fn(),
}))

vi.mock('../../../data/apiClient', () => api)
vi.mock('react-hot-toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const inAYear = () => {
  const d = new Date()
  return `${d.getFullYear() + 1}-01-15`
}

const assessed = (over: Partial<RiskAssessment>): RiskAssessment =>
  ({ risk_code: 'x', treatment_status: 'analysed', created_at: '', updated_at: '', ...over }) as RiskAssessment

// R-CAT-1: catalogue-linked, accepted and approved. R-CAT-2: catalogue-linked,
// accepted but never approved. R-ORG-1: a custom risk linked to this control.
// R-ORG-2: a custom risk linked elsewhere. R-OTHER: assessed, not linked.
const ASSESSMENTS = [
  assessed({
    risk_code: 'R-CAT-1',
    treatment_status: 'accepted',
    accepted_at: '2026-10-01T00:00:00Z',
    accepted_by: 'user-admin',
    acceptance_expires_on: inAYear(),
    acceptance_state: 'approved',
  }),
  assessed({ risk_code: 'R-CAT-2', treatment_status: 'accepted', acceptance_state: 'unapproved' }),
  assessed({ risk_code: 'R-ORG-1' }),
  assessed({ risk_code: 'R-ORG-2' }),
  assessed({ risk_code: 'R-OTHER' }),
]

const existing: CompensatingControl = {
  id: 'cc-1',
  organization_id: 'org-one',
  scoped_control_id: 'sc-1',
  scf_id: 'TESTCONTROL-01',
  domain: 'TESTCONTROL',
  gap_description: 'No MFA on the legacy VPN',
  compensating_control: 'Office network only',
  effectiveness_rating: 'partial',
  risk_reduction_notes: 'One site',
  risk_code: 'R-ORG-1',
  risk_acceptance_state: null,
  material: false,
  justified: true,
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-01T00:00:00Z',
}

function renderModal(
  props: Partial<Parameters<typeof CompensatingControlFormModal>[0]> = {},
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } }),
) {
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  const onClose = vi.fn()
  render(
    <QueryClientProvider client={client}>
      <CompensatingControlFormModal
        organizationId="org-one"
        scfId="TESTCONTROL-01"
        material={false}
        onClose={onClose}
        {...props}
      />
    </QueryClientProvider>,
  )
  return { invalidate, onClose }
}

const RISK = /^Risk( \(required\))?$/
const fill = (label: string | RegExp, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } })
const riskOptions = () =>
  within(screen.getByLabelText(RISK) as HTMLSelectElement)
    .getAllByRole('option')
    .map((o) => o.textContent)

function mockLinks() {
  api.getRisksForControl.mockResolvedValue({
    scf_id: 'TESTCONTROL-01',
    catalog_risk_codes: ['R-CAT-1', 'R-CAT-2', 'R-CAT-UNASSESSED'],
    assessments: [],
  })
  api.getCustomRiskDefinitions.mockResolvedValue([{ risk_code: 'R-ORG-1' }, { risk_code: 'R-ORG-2' }])
  api.getCustomRiskControlLinks.mockImplementation(async (code: string) =>
    code === 'R-ORG-1' ? [{ scf_id: 'testcontrol-01', document_id: null }] : [{ scf_id: 'ELSEWHERE-01', document_id: null }],
  )
  api.getRiskAssessments.mockResolvedValue(ASSESSMENTS)
}

describe('CompensatingControlFormModal — record (PIG R9)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockLinks()
  })

  it('offers only the assessed risks linked to this control, catalogue and custom, with their acceptance', async () => {
    renderModal()
    await screen.findByRole('option', { name: /R-ORG-1/ })
    expect(riskOptions()).toEqual([
      'None',
      'R-CAT-1 · accepted, approved',
      'R-CAT-2 · accepted, not approved',
      'R-ORG-1 · not accepted',
    ])
    expect(api.getCustomRiskControlLinks).toHaveBeenCalledWith('R-ORG-2', 'org-one')
  })

  it('sends exactly what was entered for this control, then refreshes the list', async () => {
    api.createCompensatingControl.mockResolvedValue({ ...existing, id: 'cc-new' })
    const { invalidate, onClose } = renderModal()
    await screen.findByRole('option', { name: /R-ORG-1/ })
    const submit = screen.getByRole('button', { name: 'Record' })
    expect(submit).toBeDisabled()

    fill('Gap', '  No MFA on the legacy VPN  ')
    expect(submit).toBeDisabled()
    fill('Compensating control', ' Office network only ')
    fill('Effectiveness', 'minimal')
    fill(RISK, 'R-ORG-1')
    fill('Risk reduction notes', '  ')
    fireEvent.click(submit)

    await waitFor(() =>
      expect(api.createCompensatingControl).toHaveBeenCalledWith('org-one', {
        scf_id: 'TESTCONTROL-01',
        gap_description: 'No MFA on the legacy VPN',
        compensating_control: 'Office network only',
        effectiveness_rating: 'minimal',
        risk_code: 'R-ORG-1',
      }),
    )
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['compensating-controls', 'org-one'] })
  })

  it('on a material control needs a risk, and offers only one with an approval in force', async () => {
    renderModal({ material: true })
    await screen.findByRole('option', { name: /R-ORG-1/ })
    expect(screen.getByLabelText(RISK)).toHaveAccessibleName(/required/)
    fill('Gap', 'Gap')
    fill('Compensating control', 'Compensation')
    const submit = screen.getByRole('button', { name: 'Record' })
    expect(submit).toBeDisabled()

    expect(screen.getByRole('option', { name: /R-CAT-2/ })).toBeDisabled()
    expect(screen.getByRole('option', { name: /R-ORG-1/ })).toBeDisabled()
    expect(screen.getByRole('option', { name: /R-CAT-1/ })).toBeEnabled()
    fill(RISK, 'R-CAT-1')
    expect(submit).toBeEnabled()
  })

  it('on a material control says when no linked risk has an approval in force to name', async () => {
    api.getRiskAssessments.mockResolvedValue(ASSESSMENTS.filter((a) => a.risk_code !== 'R-CAT-1'))
    renderModal({ material: true })
    expect(await screen.findByText(/None of the risks linked to this control is accepted with an approval in force/)).toBeInTheDocument()
  })

  it('does not say so when one has', async () => {
    renderModal({ material: true })
    await screen.findByRole('option', { name: /R-CAT-1/ })
    expect(screen.queryByText(/None of the risks linked to this control/)).not.toBeInTheDocument()
  })

  it("shows the server's refusal and keeps the form", async () => {
    api.createCompensatingControl.mockRejectedValue(
      new Error('TESTCONTROL-01 is a material control: compensating for it needs an accepted risk'),
    )
    const { onClose } = renderModal()
    fill('Gap', 'Gap')
    fill('Compensating control', 'Compensation')
    fireEvent.click(screen.getByRole('button', { name: 'Record' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('is a material control')
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByLabelText('Gap')).toHaveValue('Gap')
  })

  it('says when the linked risks cannot be read, and still lets a non-material record be made', async () => {
    api.getCustomRiskDefinitions.mockRejectedValue(new Error('custom risks down'))
    renderModal()
    expect(await screen.findByText(/Could not load the risks linked to this control: custom risks down/)).toBeInTheDocument()
    fill('Gap', 'Gap')
    fill('Compensating control', 'Compensation')
    expect(screen.getByRole('button', { name: 'Record' })).toBeEnabled()
  })
})

describe('CompensatingControlFormModal — linked risks (review L4, L5, L9)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockLinks()
  })

  it("reads the control's catalogue and custom risks afresh on every opening", async () => {
    // The app's client holds data fresh for five minutes; a risk linked a moment ago must still show.
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 5 * 60 * 1000 } } })
    renderModal({}, client)
    await screen.findByRole('option', { name: /R-ORG-1/ })
    const [custom] = client.getQueryCache().findAll({ queryKey: ['custom-risks', 'org-one'] })
    const [catalogue] = client.getQueryCache().findAll({ queryKey: ['risks-for-control', 'org-one'] })
    expect(custom.state.status).toBe('success')
    expect(custom.isStale()).toBe(true)
    expect(catalogue.state.status).toBe('success')
    expect(catalogue.isStale()).toBe(true)
  })

  it('ties every hint to the risk select, and marks it required on a material control', async () => {
    api.getRiskAssessments.mockResolvedValue(ASSESSMENTS.filter((a) => a.risk_code !== 'R-CAT-1'))
    renderModal({ material: true })
    await screen.findByText(/None of the risks linked to this control is accepted/)
    const select = screen.getByLabelText(RISK)
    expect(select).toHaveAttribute('aria-required', 'true')
    expect(select).toHaveAccessibleDescription(/This control is material: name a risk/)
    expect(select).toHaveAccessibleDescription(/None of the risks linked to this control is accepted/)
  })

  it('announces a failed read of the linked risks on the select', async () => {
    api.getCustomRiskDefinitions.mockRejectedValue(new Error('custom risks down'))
    renderModal()
    await screen.findByText(/Could not load the risks linked to this control/)
    expect(screen.getByLabelText(RISK)).toHaveAccessibleDescription(/Could not load the risks linked to this control: custom risks down/)
  })

  it("treats a control the catalogue does not know as having no catalogue risks, not as a failed read", async () => {
    api.getRisksForControl.mockRejectedValue(new Error('Control TESTCONTROL-01 not found in SCF catalog'))
    renderModal()
    expect(await screen.findByRole('option', { name: 'R-ORG-1 · not accepted' })).toBeInTheDocument()
    expect(screen.queryByText(/Could not load the risks linked/)).not.toBeInTheDocument()
    expect(riskOptions()).toEqual(['None', 'R-ORG-1 · not accepted'])
  })

  it('still reports any other failure of the catalogue read', async () => {
    api.getRisksForControl.mockRejectedValue(new Error('Server exploded'))
    renderModal()
    expect(await screen.findByText(/Could not load the risks linked to this control: Server exploded/)).toBeInTheDocument()
  })
})

describe('CompensatingControlFormModal — edit (PIG R9)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockLinks()
  })

  it('sends only what changed, a cleared risk and notes as null', async () => {
    api.updateCompensatingControl.mockResolvedValue(existing)
    const { invalidate, onClose } = renderModal({ record: existing })
    await screen.findByRole('option', { name: /R-CAT-1/ })
    const save = screen.getByRole('button', { name: 'Save' })
    expect(save).toBeDisabled()

    fill('Effectiveness', 'full')
    fill(RISK, '')
    fill('Risk reduction notes', '')
    fireEvent.click(save)
    await waitFor(() =>
      expect(api.updateCompensatingControl).toHaveBeenCalledWith('org-one', 'cc-1', {
        effectiveness_rating: 'full',
        risk_code: null,
        risk_reduction_notes: null,
      }),
    )
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['compensating-controls', 'org-one'] })
  })

  it('keeps a risk that is no longer linked visible as the current choice', async () => {
    renderModal({ record: { ...existing, risk_code: 'R-GONE' } })
    await screen.findByRole('option', { name: /R-CAT-1/ })
    expect(screen.getByLabelText(RISK)).toHaveValue('R-GONE')
    expect(screen.getByRole('option', { name: 'R-GONE · no longer linked to this control' })).toBeInTheDocument()
  })
})

describe('CompensatingControlFormModal — edit on a material control (review M1, L1, L8)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockLinks()
  })

  it('saves a change that leaves the risk alone, as the server allows, even when the record names none', async () => {
    api.updateCompensatingControl.mockResolvedValue(existing)
    const { onClose } = renderModal({ record: { ...existing, risk_code: null, justified: false, material: true }, material: true })
    await screen.findByRole('option', { name: /R-CAT-1/ })
    expect(screen.getByText(/Saving keeps this record not justified until an accepted risk is named/)).toBeInTheDocument()
    fill('Effectiveness', 'full')
    const save = screen.getByRole('button', { name: 'Save' })
    expect(save).toBeEnabled()
    fireEvent.click(save)
    await waitFor(() =>
      expect(api.updateCompensatingControl).toHaveBeenCalledWith('org-one', 'cc-1', { effectiveness_rating: 'full' }),
    )
    await waitFor(() => expect(onClose).toHaveBeenCalled())
  })

  it('still needs a risk once the edit clears the one the record names', async () => {
    renderModal({ record: { ...existing, risk_code: 'R-CAT-1' }, material: true })
    await screen.findByRole('option', { name: /R-CAT-1/ })
    fill(RISK, '')
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
  })

  it("keeps the record's own risk selectable though it has no approval in force", async () => {
    renderModal({ record: existing, material: true })
    const own = await screen.findByRole('option', { name: 'R-ORG-1 · not accepted' })
    expect(own).toBeEnabled()
    expect(screen.getByLabelText(RISK)).toHaveValue('R-ORG-1')
    expect(screen.getByRole('option', { name: /R-CAT-2/ })).toBeDisabled()
    fill('Effectiveness', 'full')
    expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled()
  })

  it("does not call the record's risk unlinked when one link read failed", async () => {
    api.getCustomRiskControlLinks.mockImplementation(async (code: string) => {
      if (code === 'R-ORG-1') throw new Error('links down')
      return [{ scf_id: 'ELSEWHERE-01', document_id: null }]
    })
    renderModal({ record: existing })
    expect(await screen.findByText(/Could not load the risks linked to this control: links down/)).toBeInTheDocument()
    expect(screen.getByLabelText(RISK)).toHaveValue('R-ORG-1')
    expect(screen.getByRole('option', { name: 'R-ORG-1 · current choice (links could not be checked)' })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: /no longer linked/ })).not.toBeInTheDocument()
  })

  it("shows a refused edit in the alert and keeps the form", async () => {
    api.updateCompensatingControl.mockRejectedValue(
      new Error('TESTCONTROL-01 is a material control: R-CAT-2 needs to be accepted, with an approval in force'),
    )
    const { onClose } = renderModal({ record: existing })
    await screen.findByRole('option', { name: /R-CAT-2/ })
    fill(RISK, 'R-CAT-2')
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('needs to be accepted, with an approval in force')
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByLabelText(RISK)).toHaveValue('R-CAT-2')
  })
})

describe('CompensatingControlFormModal — closing', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockLinks()
  })

  it('stays open while a save is in flight, and still shows its refusal', async () => {
    let refuse: (err: Error) => void = () => {}
    api.createCompensatingControl.mockReturnValue(new Promise((_, reject) => { refuse = reject }))
    const { onClose } = renderModal()
    fill('Gap', 'Gap')
    fill('Compensating control', 'Compensation')
    fireEvent.click(screen.getByRole('button', { name: 'Record' }))
    await screen.findByRole('button', { name: 'Saving…' })

    fireEvent.keyDown(document, { key: 'Escape' })
    fireEvent.click(document.querySelector('.modal-overlay')!)
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()
    const close = screen.getByRole('button', { name: 'Close' })
    expect(close).toBeDisabled()
    fireEvent.click(close)
    expect(onClose).not.toHaveBeenCalled()

    refuse(new Error('API key role viewer insufficient'))
    expect(await screen.findByRole('alert')).toHaveTextContent('API key role viewer insufficient')
    expect(onClose).not.toHaveBeenCalled()
  })

  it('ignores Escape and a backdrop click once something is typed', () => {
    const { onClose } = renderModal()
    fill('Gap', 'Gap')
    fireEvent.keyDown(document, { key: 'Escape' })
    fireEvent.click(document.querySelector('.modal-overlay')!)
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByLabelText('Gap')).toHaveValue('Gap')
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('closes on Escape while untouched', () => {
    const first = renderModal()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(first.onClose).toHaveBeenCalledTimes(1)
  })

  it('closes on a backdrop click while untouched, and not on a click inside the dialog', () => {
    const { onClose } = renderModal()
    fireEvent.click(screen.getByRole('dialog'))
    expect(onClose).not.toHaveBeenCalled()
    fireEvent.click(document.querySelector('.modal-overlay')!)
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('ignores Escape on an edit with unsaved changes', () => {
    const { onClose } = renderModal({ record: existing })
    fill('Effectiveness', 'full')
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).not.toHaveBeenCalled()
  })
})
