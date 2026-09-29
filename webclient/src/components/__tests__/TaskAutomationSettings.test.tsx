/**
 * TaskAutomationSettings — Settings → Task automation.
 *
 * What these tests defend:
 *   - an absent setting reads as ON (the backend default, and what every
 *     organisation had before the switch existed);
 *   - the saved value is what the switch shows, and Save is inert until the
 *     switch differs from it;
 *   - saving sends exactly { auto_task_generation_enabled } and refetches;
 *   - below admin the switch is read-only and no Save is offered, because the
 *     PATCH requires admin.
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import TaskAutomationSettings from '../TaskAutomationSettings'
import { updateOrganizationSettings } from '../../data/apiClient'

vi.mock('../../data/apiClient', () => ({
  updateOrganizationSettings: vi.fn(),
}))

const mockRefetch = vi.fn()
let settings: Record<string, unknown> | undefined
vi.mock('../../hooks/useOrganizationSettings', () => ({
  useOrganizationSettings: () => ({ data: settings, refetch: mockRefetch }),
}))

let admin = true
vi.mock('../../hooks/useIsOrgAdmin', () => ({
  useIsOrgAdmin: () => admin,
}))

vi.mock('react-hot-toast', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}))

const mockUpdate = vi.mocked(updateOrganizationSettings)
const ORG = 'org-1'

beforeEach(() => {
  vi.clearAllMocks()
  settings = undefined
  admin = true
  mockUpdate.mockResolvedValue({} as never)
})

describe('TaskAutomationSettings', () => {
  it('reads an absent setting as on', () => {
    settings = { name: 'Org' }
    render(<TaskAutomationSettings organizationId={ORG} />)
    expect(screen.getByRole('switch', { name: 'Automatic task creation' })).toHaveAttribute('aria-checked', 'true')
  })

  it('shows the saved value when it is off', () => {
    settings = { auto_task_generation_enabled: false }
    render(<TaskAutomationSettings organizationId={ORG} />)
    expect(screen.getByRole('switch', { name: 'Automatic task creation' })).toHaveAttribute('aria-checked', 'false')
    expect(screen.getByText(/No tasks are created automatically/)).toBeInTheDocument()
  })

  it('keeps Save disabled until the switch changes, then PATCHes only the flag and refetches', async () => {
    settings = { auto_task_generation_enabled: true }
    render(<TaskAutomationSettings organizationId={ORG} />)

    const save = screen.getByRole('button', { name: 'Save task automation' })
    expect(save).toBeDisabled()

    await userEvent.click(screen.getByRole('switch', { name: 'Automatic task creation' }))
    expect(save).toBeEnabled()

    await userEvent.click(save)
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith(ORG, { auto_task_generation_enabled: false }))
    expect(mockRefetch).toHaveBeenCalled()
  })

  it('renders read-only below admin', () => {
    admin = false
    settings = { auto_task_generation_enabled: true }
    render(<TaskAutomationSettings organizationId={ORG} />)
    expect(screen.getByRole('switch', { name: 'Automatic task creation' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Save task automation' })).toBeNull()
    expect(screen.getByText(/Only an organisation administrator/)).toBeInTheDocument()
  })
})
