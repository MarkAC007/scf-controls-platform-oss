// SPDX-License-Identifier: AGPL-3.0-only
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import LogValueModal from '../LogValueModal'
import { metric, objective, ORG, reading } from './fixtures'

const api = vi.hoisted(() => ({ logMetricResult: vi.fn() }))
vi.mock('../../../../data/securityObjectivesApi', () => api)
vi.mock('react-hot-toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const LOGGED = objective({
  metric: metric({ kind: 'logged', measure_key: null, frequency: 'quarterly' }),
  reading: reading({ status: 'on_track', current_period_start: '2026-10-01' }),
})
const MILESTONE = objective({
  metric: metric({ kind: 'milestone', unit: null }),
  reading: reading({ status: 'in_progress', value: 0, value_text: 'Not done' }),
})

function renderModal(o = LOGGED, onClose = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  render(
    <QueryClientProvider client={client}>
      <LogValueModal organizationId={ORG} objective={o} onClose={onClose} />
    </QueryClientProvider>,
  )
  return onClose
}

describe('LogValueModal', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.logMetricResult.mockResolvedValue(LOGGED)
  })

  it('blocks a percentage above 100', () => {
    renderModal()
    fireEvent.change(screen.getByLabelText('Value (%)'), { target: { value: '120' } })
    expect(screen.getByRole('button', { name: 'Log value' })).toBeDisabled()
    expect(screen.getByText('A percentage must be between 0 and 100.')).toBeInTheDocument()
  })

  it('defaults the period to the current one and leaves the note out when empty', async () => {
    const onClose = renderModal()
    expect(screen.getByLabelText('Period starting')).toHaveValue('2026-10-01')
    fireEvent.change(screen.getByLabelText('Value (%)'), { target: { value: '96' } })
    fireEvent.click(screen.getByRole('button', { name: 'Log value' }))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(api.logMetricResult).toHaveBeenCalledWith(ORG, 'obj-1', { value: 96, period_start: '2026-10-01' })
  })

  it('sends 1 for a milestone marked done, with a note', async () => {
    const onClose = renderModal(MILESTONE)
    fireEvent.click(screen.getByRole('button', { name: 'Done' }))
    fireEvent.change(screen.getByLabelText(/^Note/), { target: { value: 'Certificate issued' } })
    fireEvent.click(screen.getByRole('button', { name: 'Log value' }))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(api.logMetricResult).toHaveBeenCalledWith(ORG, 'obj-1', { value: 1, note: 'Certificate issued' })
  })

  it('sends 0 for a milestone marked not done again', async () => {
    renderModal(objective({ ...MILESTONE, reading: reading({ status: 'on_track', value: 1, value_text: 'Done' }) }))
    fireEvent.click(screen.getByRole('button', { name: 'Not done' }))
    fireEvent.click(screen.getByRole('button', { name: 'Log value' }))
    await waitFor(() => expect(api.logMetricResult).toHaveBeenCalledWith(ORG, 'obj-1', { value: 0 }))
  })

  it('M7: a milestone entry that changes nothing and has no note cannot be logged', () => {
    renderModal(MILESTONE)
    expect(screen.getByRole('button', { name: 'Log value' })).toBeDisabled()
    fireEvent.change(screen.getByLabelText(/^Note/), { target: { value: 'Still waiting on the auditor' } })
    expect(screen.getByRole('button', { name: 'Log value' })).toBeEnabled()
  })

  it('M7: the first milestone entry may be "Not done"', async () => {
    renderModal(objective({ ...MILESTONE, reading: reading({ status: 'first_measurement_due', value: null, value_text: '—' }) }))
    fireEvent.click(screen.getByRole('button', { name: 'Log value' }))
    await waitFor(() => expect(api.logMetricResult).toHaveBeenCalledWith(ORG, 'obj-1', { value: 0 }))
  })
})
