/**
 * NoOrganisationScreen — the screen a signed-in user with no organisation gets.
 *
 * The defect this guards: App.tsx only clears its `loading` flag inside the
 * org-gated data loader, so a user with zero organisations used to see
 * "Loading data" for ever. The fix renders this screen instead, and the test
 * pins down what it must say and do — it is the only feedback the stranded
 * person gets.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import NoOrganisationScreen from '../NoOrganisationScreen'

const logout = vi.fn()
const refreshOrganizations = vi.fn(() => Promise.resolve())

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { email: 'newcomer@example.com' }, logout }),
}))

vi.mock('../../contexts/OrganizationContext', () => ({
  useOrganization: () => ({ refreshOrganizations }),
}))

describe('NoOrganisationScreen', () => {
  it('says who is signed in and that they have no organisation', () => {
    render(<NoOrganisationScreen />)
    expect(screen.getByText('No organisation yet')).toBeInTheDocument()
    expect(screen.getByText('newcomer@example.com')).toBeInTheDocument()
    expect(screen.getByText(/not a member of any organisation/i)).toBeInTheDocument()
  })

  it('tells them how to get in: an admin invite, or the invitation link', () => {
    render(<NoOrganisationScreen />)
    expect(screen.getByText(/ask an administrator to invite you/i)).toBeInTheDocument()
    expect(screen.getByText(/open the link in it/i)).toBeInTheDocument()
  })

  it('does not show the loading spinner', () => {
    const { container } = render(<NoOrganisationScreen />)
    expect(container.querySelector('.loading-spinner')).toBeNull()
    expect(screen.queryByText(/loading data/i)).not.toBeInTheDocument()
  })

  it('offers to check again and to sign out', () => {
    render(<NoOrganisationScreen />)
    fireEvent.click(screen.getByRole('button', { name: /check again/i }))
    expect(refreshOrganizations).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole('button', { name: /sign out/i }))
    expect(logout).toHaveBeenCalledTimes(1)
  })
})
