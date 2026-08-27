/**
 * Sidebar brand block and logo-fallback coverage (#807).
 *
 * Adapted from Header.logo.test.tsx (which Task 6 will delete).
 * The Sidebar now renders the brand block in the nav header:
 * - org-uploaded logo (via useOrgLogo) takes precedence
 * - falls back to VITE_APP_LOGO / default when no org logo
 * - falls back to wordmark alone when the image fails to load (#807)
 * - VITE_APP_TITLE controls the wordmark: custom title replaces the
 *   stylized "SCF Controls" wordmark; default/unset renders the stylized form
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import Sidebar from '../Sidebar'
import { useOrgLogo } from '../../hooks/useOrgLogo'

vi.mock('../../hooks/useOrgLogo', () => ({
  useOrgLogo: vi.fn(),
  ORG_LOGO_QUERY_KEY: 'organization-logo',
}))

vi.mock('../../contexts/OrganizationContext', () => ({
  useOrganization: () => ({ currentOrg: { id: 'org-1', name: 'Acme' } }),
}))

// Silence catalog version fetch
vi.mock('../../data/catalogUpgradeApi', () => ({
  getCatalogStatusExtended: vi.fn().mockResolvedValue({ catalog_version: null, seeded: false, controls: 0 }),
}))

const mockUseOrgLogo = vi.mocked(useOrgLogo)

function renderSidebar(props: Partial<Parameters<typeof Sidebar>[0]> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <Sidebar activeTab="dashboard" onTabChange={vi.fn()} {...props} />
    </QueryClientProvider>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => {
  vi.unstubAllEnvs()
})

describe('Sidebar brand block', () => {
  it('renders the stylized SCF Controls wordmark by default when no org logo is available', () => {
    mockUseOrgLogo.mockReturnValue({ data: null } as ReturnType<typeof useOrgLogo>)

    renderSidebar()

    // The stylized wordmark renders "Controls" in a dedicated accent span
    expect(screen.getByText('Controls', { selector: '.sidebar-brand-accent' })).toBeInTheDocument()
    // The default wordmark must NOT fall through as a plain custom-title string
    expect(screen.queryByText('SCF Controls Platform')).not.toBeInTheDocument()
  })

  it('renders the custom VITE_APP_TITLE instead of the stylized wordmark when set', () => {
    vi.stubEnv('VITE_APP_TITLE', 'Acme GRC Suite')
    mockUseOrgLogo.mockReturnValue({ data: null } as ReturnType<typeof useOrgLogo>)

    renderSidebar()

    expect(screen.getByText('Acme GRC Suite')).toBeInTheDocument()
    // The accent "Controls" span must not appear when a custom title is set
    expect(screen.queryByText('Controls', { selector: '.sidebar-brand-accent' })).not.toBeInTheDocument()
  })

  it('renders the stylized wordmark when VITE_APP_TITLE equals the default value', () => {
    vi.stubEnv('VITE_APP_TITLE', 'SCF Controls Platform')
    mockUseOrgLogo.mockReturnValue({ data: null } as ReturnType<typeof useOrgLogo>)

    renderSidebar()

    expect(screen.getByText('Controls', { selector: '.sidebar-brand-accent' })).toBeInTheDocument()
    expect(screen.queryByText('SCF Controls Platform')).not.toBeInTheDocument()
  })

  it('renders an org logo image when useOrgLogo returns a URL', () => {
    mockUseOrgLogo.mockReturnValue({
      data: 'blob:https://scf.test/org-logo-123',
    } as ReturnType<typeof useOrgLogo>)

    renderSidebar()

    expect(screen.getByAltText('Logo')).toBeInTheDocument()
    expect(screen.getByAltText('Logo')).toHaveAttribute('src', 'blob:https://scf.test/org-logo-123')
  })

  it('drops the logo to the wordmark when the image fails to load (#807)', () => {
    mockUseOrgLogo.mockReturnValue({
      data: 'blob:https://scf.test/64b14db8',
    } as ReturnType<typeof useOrgLogo>)

    renderSidebar()

    const logo = screen.getByAltText('Logo')
    fireEvent.error(logo)

    expect(screen.queryByAltText('Logo')).not.toBeInTheDocument()
    // stylized wordmark should still be visible after logo error
    expect(screen.getByText('Controls', { selector: '.sidebar-brand-accent' })).toBeInTheDocument()
  })
})
