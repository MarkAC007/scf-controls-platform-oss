/**
 * Regression cover for #807 — a logo whose fetch or decode fails must degrade
 * to the wordmark, never to the browser's broken-image icon.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import Header from '../Header'
import { useOrgLogo } from '../../hooks/useOrgLogo'

vi.mock('../../hooks/useOrgLogo', () => ({
  useOrgLogo: vi.fn(),
  ORG_LOGO_QUERY_KEY: 'organization-logo',
}))
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: null }),
}))
vi.mock('../../contexts/OrganizationContext', () => ({
  useOrganization: () => ({ currentOrg: { id: 'org-1', name: 'Acme' } }),
}))
vi.mock('../NotificationBell', () => ({ NotificationBell: () => null }))
vi.mock('../UserProfileDropdown', () => ({ default: () => null }))
vi.mock('../ThemeMenu', () => ({ default: () => null }))
vi.mock('../OrgSwitcher', () => ({ default: () => null }))

const mockUseOrgLogo = vi.mocked(useOrgLogo)

function renderHeader() {
  return render(<Header activeTab="dashboard" onTabChange={vi.fn()} />)
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('Header logo fallback', () => {
  it('drops the logo to the wordmark when the image fails to load', () => {
    mockUseOrgLogo.mockReturnValue({
      data: 'blob:https://scf.test/64b14db8',
    } as ReturnType<typeof useOrgLogo>)

    renderHeader()

    const logo = screen.getByAltText('Logo')
    fireEvent.error(logo)

    expect(screen.queryByAltText('Logo')).not.toBeInTheDocument()
    expect(screen.getByText('SCF Controls Platform')).toBeInTheDocument()
  })
})
