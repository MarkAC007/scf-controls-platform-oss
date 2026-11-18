// SPDX-License-Identifier: AGPL-3.0-only
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import EvaluationModal from '../EvaluationModal'
import { objective, ORG } from './fixtures'

const api = vi.hoisted(() => ({ recordObjectiveEvaluation: vi.fn() }))
vi.mock('../../../../data/securityObjectivesApi', () => api)
vi.mock('react-hot-toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

function renderModal(o = objective(), onClose = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  render(
    <QueryClientProvider client={client}>
      <EvaluationModal organizationId={ORG} objective={o} onClose={onClose} />
    </QueryClientProvider>,
  )
  return onClose
}

describe('EvaluationModal', () => {
  beforeEach(() => vi.clearAllMocks())

  it('needs a note', () => {
    renderModal()
    expect(screen.getByRole('button', { name: 'Record evaluation' })).toBeDisabled()
    fireEvent.change(screen.getByLabelText('What the results show'), { target: { value: '   ' } })
    expect(screen.getByRole('button', { name: 'Record evaluation' })).toBeDisabled()
  })

  it('sends the trimmed note and closes', async () => {
    api.recordObjectiveEvaluation.mockResolvedValue(objective())
    const onClose = renderModal()
    fireEvent.change(screen.getByLabelText('What the results show'), { target: { value: '  Below target; treatment planned.  ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Record evaluation' }))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(api.recordObjectiveEvaluation).toHaveBeenCalledWith(ORG, 'obj-1', 'Below target; treatment planned.')
  })

  it('shows the previous evaluation', () => {
    renderModal(objective({
      last_evaluation_note: 'Reviewed at management review.', last_evaluated_by_name: 'Pat Example',
      last_evaluated_at: '2026-10-01T09:00:00',
    }))
    expect(screen.getByText('Reviewed at management review.')).toBeInTheDocument()
    expect(screen.getByText(/by Pat Example/)).toBeInTheDocument()
  })
})
