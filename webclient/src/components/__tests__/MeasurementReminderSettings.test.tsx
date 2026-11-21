// SPDX-License-Identifier: AGPL-3.0-only
/**
 * MeasurementReminderSettings: Settings → Objective reminders.
 *
 * What these tests defend:
 *   - an absent setting reads as the last 14 days (the backend default);
 *   - the saved value is what the select shows, and Save is inert until the
 *     choice differs from it;
 *   - saving sends exactly { isms_measurement_reminder_days } as a number and
 *     refetches; "from the first day" is sent as 366;
 *   - a value set through the API that is not one of the listed choices is
 *     shown as its own option, not silently replaced by another;
 *   - below admin the select is read-only and no Save is offered, because the
 *     PATCH requires admin.
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import MeasurementReminderSettings, { reminderOptions } from '../MeasurementReminderSettings'
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
// roleKnown false is the window before the membership lookup returns.
let roleKnown = true
vi.mock('../../hooks/useHasOrgRole', () => ({
  useOrgRoleState: () => ({ allowed: roleKnown && admin, resolved: roleKnown }),
}))

vi.mock('react-hot-toast', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}))

const mockUpdate = vi.mocked(updateOrganizationSettings)
const ORG = 'org-1'
const select = () => screen.getByLabelText('Remind the owner to log a measurement') as HTMLSelectElement
const shown = () => select().selectedOptions[0].textContent

beforeEach(() => {
  vi.clearAllMocks()
  settings = undefined
  admin = true
  roleKnown = true
  mockUpdate.mockResolvedValue({} as never)
})

describe('MeasurementReminderSettings', () => {
  it('reads an absent setting as the last 14 days', () => {
    settings = { name: 'Org' }
    render(<MeasurementReminderSettings organizationId={ORG} />)
    expect(shown()).toBe('In the last 14 days of the period')
  })

  it('shows the saved timing', () => {
    settings = { isms_measurement_reminder_days: 366 }
    render(<MeasurementReminderSettings organizationId={ORG} />)
    expect(shown()).toBe('From the first day of the period')
  })

  it('lists the four choices in order of days', () => {
    settings = { isms_measurement_reminder_days: 14 }
    render(<MeasurementReminderSettings organizationId={ORG} />)
    expect(Array.from(select().options).map(option => [option.value, option.textContent])).toEqual([
      ['7', 'In the last 7 days of the period'],
      ['14', 'In the last 14 days of the period'],
      ['30', 'In the last 30 days of the period'],
      ['366', 'From the first day of the period'],
    ])
  })

  it('keeps Save disabled until the choice changes, then PATCHes only the days and refetches', async () => {
    settings = { isms_measurement_reminder_days: 14 }
    render(<MeasurementReminderSettings organizationId={ORG} />)

    const save = screen.getByRole('button', { name: 'Save reminder timing' })
    expect(save).toBeDisabled()

    await userEvent.selectOptions(select(), 'From the first day of the period')
    expect(save).toBeEnabled()

    await userEvent.click(save)
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith(ORG, { isms_measurement_reminder_days: 366 }))
    expect(mockUpdate).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(mockRefetch).toHaveBeenCalled())
  })

  it('disables Save again when the choice is put back', async () => {
    settings = { isms_measurement_reminder_days: 30 }
    render(<MeasurementReminderSettings organizationId={ORG} />)
    await userEvent.selectOptions(select(), '7')
    await userEvent.selectOptions(select(), '30')
    expect(screen.getByRole('button', { name: 'Save reminder timing' })).toBeDisabled()
  })

  it('shows a value set through the API that is not a listed choice as its own option', () => {
    settings = { isms_measurement_reminder_days: 21 }
    render(<MeasurementReminderSettings organizationId={ORG} />)
    expect(shown()).toBe('In the last 21 days of the period')
    expect(Array.from(select().options).map(option => option.value)).toEqual(['7', '14', '21', '30', '366'])
  })

  it('words one day in the singular', () => {
    expect(reminderOptions(1)[0]).toEqual({ days: 1, label: 'In the last 1 day of the period' })
  })

  it('is read-only, without the administrator line, while the role is not yet known', () => {
    roleKnown = false
    settings = { isms_measurement_reminder_days: 7 }
    render(<MeasurementReminderSettings organizationId={ORG} />)
    expect(select()).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Save reminder timing' })).not.toBeInTheDocument()
    expect(screen.queryByText(/Only an organisation administrator/)).not.toBeInTheDocument()
  })

  it('is read-only below admin and offers no Save', () => {
    admin = false
    settings = { isms_measurement_reminder_days: 7 }
    render(<MeasurementReminderSettings organizationId={ORG} />)
    expect(select()).toBeDisabled()
    expect(shown()).toBe('In the last 7 days of the period')
    expect(screen.queryByRole('button', { name: 'Save reminder timing' })).not.toBeInTheDocument()
    expect(screen.getByText('Only an organisation administrator can change the reminder timing.')).toBeInTheDocument()
  })

  it('says so when the save fails and leaves the choice for another try', async () => {
    const { toast } = await import('react-hot-toast')
    settings = { isms_measurement_reminder_days: 14 }
    mockUpdate.mockRejectedValue(new Error('Admin role required'))
    render(<MeasurementReminderSettings organizationId={ORG} />)
    await userEvent.selectOptions(select(), '7')
    await userEvent.click(screen.getByRole('button', { name: 'Save reminder timing' }))
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Admin role required'))
    expect(mockRefetch).not.toHaveBeenCalled()
    expect(shown()).toBe('In the last 7 days of the period')
    expect(screen.getByRole('button', { name: 'Save reminder timing' })).toBeEnabled()
  })
})
