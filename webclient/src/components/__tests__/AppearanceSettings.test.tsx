/**
 * AppearanceSettings.test.tsx — the organisation profile fields (PIG R4).
 *
 * Coverage:
 *   - Business size and mission statement load from settings
 *   - Saving sends both, with blanks cleared to null
 *   - Saving refreshes the cached settings that Scoping reads business size from
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import AppearanceSettings from '../AppearanceSettings'

const mockFetch = vi.fn()
const mockUpdate = vi.fn()
const mockInvalidate = vi.fn().mockResolvedValue(undefined)

vi.mock('../../data/apiClient', () => ({
  fetchOrganizationSettings: (...args: unknown[]) => mockFetch(...args),
  updateOrganizationSettings: (...args: unknown[]) => mockUpdate(...args),
  uploadOrganizationLogo: vi.fn(),
  deleteOrganizationLogo: vi.fn(),
}))

vi.mock('../../hooks/useOrgLogo', () => ({
  useOrgLogo: () => ({ data: null }),
  ORG_LOGO_QUERY_KEY: 'org-logo',
}))

vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => ({ invalidateQueries: mockInvalidate }),
}))

vi.mock('react-hot-toast', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}))

const stored = {
  name: 'Example Ltd',
  industry: 'Health',
  business_size: 'small',
  mission_statement: 'Keep patients safe',
}

describe('AppearanceSettings organisation profile', () => {
  beforeEach(() => {
    mockFetch.mockReset().mockResolvedValue(stored)
    mockUpdate.mockReset().mockImplementation((_org: string, body: object) =>
      Promise.resolve({ ...stored, ...body }),
    )
    mockInvalidate.mockClear()
  })

  it('loads business size and mission statement', async () => {
    render(<AppearanceSettings organizationId="org-1" />)
    await waitFor(() => {
      expect(screen.getByLabelText('Business Size')).toHaveValue('small')
    })
    expect(screen.getByLabelText('Mission Statement')).toHaveValue('Keep patients safe')
  })

  it('saves both fields and clears blanks to null', async () => {
    render(<AppearanceSettings organizationId="org-1" />)
    await waitFor(() => expect(screen.getByLabelText('Business Size')).toHaveValue('small'))

    fireEvent.change(screen.getByLabelText('Business Size'), { target: { value: 'enterprise' } })
    fireEvent.change(screen.getByLabelText('Mission Statement'), { target: { value: '   ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save Organisation Details' }))

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1))
    expect(mockUpdate.mock.calls[0][1]).toMatchObject({
      business_size: 'enterprise',
      mission_statement: null,
    })
    await waitFor(() => {
      expect(mockInvalidate).toHaveBeenCalledWith({ queryKey: ['organization-settings'] })
    })
  })

  it('sends null when business size is set back to not recorded', async () => {
    render(<AppearanceSettings organizationId="org-1" />)
    await waitFor(() => expect(screen.getByLabelText('Business Size')).toHaveValue('small'))

    fireEvent.change(screen.getByLabelText('Business Size'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save Organisation Details' }))

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1))
    expect(mockUpdate.mock.calls[0][1]).toEqual({ business_size: null })
  })

  it('sends only the fields that changed', async () => {
    render(<AppearanceSettings organizationId="org-1" />)
    await waitFor(() => expect(screen.getByLabelText('Business Size')).toHaveValue('small'))

    fireEvent.change(screen.getByLabelText('Mission Statement'), { target: { value: 'Serve patients' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save Organisation Details' }))

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1))
    expect(mockUpdate.mock.calls[0][1]).toEqual({ mission_statement: 'Serve patients' })
  })

  it('will not save over stored details it failed to load', async () => {
    mockFetch.mockReset().mockRejectedValue(new Error('network down'))
    render(<AppearanceSettings organizationId="org-1" />)
    const save = screen.getByRole('button', { name: 'Save Organisation Details' })
    await waitFor(() => expect(screen.getByLabelText('Organisation Name')).not.toBeDisabled())

    fireEvent.change(screen.getByLabelText('Organisation Name'), { target: { value: 'New name' } })
    expect(save).toBeDisabled()
  })
})
