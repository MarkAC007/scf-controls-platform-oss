/**
 * Sidebar platform gating and section/label structure.
 *
 * The Platform section (Catalog / Tenants) must be invisible to everyone but
 * platform admins — for a normal org user the nav offers no way to reach the
 * platform pages at all. The org-visible Catalog Changelog entry is not
 * admin-gated.
 *
 * Section headers must be rendered with the mockup labels (OVERVIEW,
 * CONTROLS & FRAMEWORKS, etc.). Item labels must match the mockup text.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'

import Sidebar from '../Sidebar'

// Silence the catalog status network call — tests don't need the chip
vi.mock('../../data/catalogUpgradeApi', () => ({
  getCatalogStatusExtended: vi.fn().mockResolvedValue({ catalog_version: null, seeded: false, controls: 0 }),
}))

vi.mock('../../hooks/useOrgLogo', () => ({
  useOrgLogo: vi.fn().mockReturnValue({ data: null }),
  ORG_LOGO_QUERY_KEY: 'organization-logo',
}))

vi.mock('../../contexts/OrganizationContext', () => ({
  useOrganization: () => ({ currentOrg: null }),
}))

function renderSidebar(props: Partial<Parameters<typeof Sidebar>[0]> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <Sidebar activeTab="dashboard" onTabChange={vi.fn()} {...props} />
    </QueryClientProvider>
  )
}

describe('Sidebar platform gating', () => {
  it('hides the Platform section for non-platform-admins', () => {
    renderSidebar()

    expect(screen.queryByText('PLATFORM')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Catalog' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Tenants' })).not.toBeInTheDocument()
  })

  it('hides the Platform section when isPlatformAdmin is explicitly false', () => {
    renderSidebar({ isPlatformAdmin: false })

    expect(screen.queryByText('PLATFORM')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Catalog' })).not.toBeInTheDocument()
  })

  it('shows Catalog and Tenants to platform admins and routes to the platform tabs', () => {
    const onTabChange = vi.fn()
    renderSidebar({ onTabChange, isPlatformAdmin: true })

    expect(screen.getByText('PLATFORM')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Catalog' }))
    expect(onTabChange).toHaveBeenCalledWith('platform-catalog')

    fireEvent.click(screen.getByRole('button', { name: 'Tenants' }))
    expect(onTabChange).toHaveBeenCalledWith('platform-tenants')
  })

  it('shows the org-visible Catalog Changelog entry regardless of admin status', () => {
    const onTabChange = vi.fn()
    renderSidebar({ onTabChange })

    fireEvent.click(screen.getByRole('button', { name: 'Catalog Changelog' }))
    expect(onTabChange).toHaveBeenCalledWith('catalog-changelog')
  })
})

describe('Sidebar section structure', () => {
  it('renders all 8 section headers in uppercase mono style', () => {
    renderSidebar({ isPlatformAdmin: true, cdmEnabled: true })

    expect(screen.getByText('OVERVIEW')).toBeInTheDocument()
    expect(screen.getByText('CONTROLS & FRAMEWORKS')).toBeInTheDocument()
    expect(screen.getByText('RISK & THIRD PARTY')).toBeInTheDocument()
    expect(screen.getByText('EVIDENCE')).toBeInTheDocument()
    expect(screen.getByText('KNOWLEDGE BASE')).toBeInTheDocument()
    expect(screen.getByText('OPERATIONS')).toBeInTheDocument()
    expect(screen.getByText('ADMIN')).toBeInTheDocument()
    expect(screen.getByText('PLATFORM')).toBeInTheDocument()
  })

  it('renders all 22 nav items with mockup labels', () => {
    renderSidebar({ isPlatformAdmin: true, showConsultantPortal: true, cdmEnabled: true })

    // OVERVIEW (2)
    expect(screen.getByRole('button', { name: 'Dashboard' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Analytics' })).toBeInTheDocument()

    // CONTROLS & FRAMEWORKS (3)
    expect(screen.getByRole('button', { name: 'Control Library' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Framework Mappings' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Control Scoping' })).toBeInTheDocument()

    // RISK & THIRD PARTY (2)
    expect(screen.getByRole('button', { name: 'Risk Register' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Vendor Inventory' })).toBeInTheDocument()

    // EVIDENCE (1)
    expect(screen.getByRole('button', { name: 'Evidence' })).toBeInTheDocument()

    // KNOWLEDGE BASE (3)
    expect(screen.getByRole('button', { name: 'Control Documents' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Document Map' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Generated Documents' })).toBeInTheDocument()

    // OPERATIONS (3)
    expect(screen.getByRole('button', { name: 'Task Management' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Systems Registry' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'User Management' })).toBeInTheDocument()

    // ADMIN (6)
    expect(screen.getByRole('button', { name: 'Engagements' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Webhooks' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Audit Log' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Catalog Changelog' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Consultant Portal' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Org Settings' })).toBeInTheDocument()

    // PLATFORM (2)
    expect(screen.getByRole('button', { name: 'Catalog' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Tenants' })).toBeInTheDocument()
  })

  it('hides Consultant Portal when showConsultantPortal is false', () => {
    renderSidebar({ showConsultantPortal: false })
    expect(screen.queryByRole('button', { name: 'Consultant Portal' })).not.toBeInTheDocument()
  })
})

describe('Sidebar CDM gating', () => {
  // The Control Documents Mapper is being retired. It never ran on the
  // self-hosted target, so a default install must not be offered either of
  // its entries — and the group they shared with Generated Documents stops
  // being a "knowledge base" once they are gone.

  it('drops both CDM entries and renames the group when cdmEnabled is false', () => {
    renderSidebar({ cdmEnabled: false })

    expect(screen.getByText('DOCUMENTS')).toBeInTheDocument()
    expect(screen.queryByText('KNOWLEDGE BASE')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Control Documents' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Document Map' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Generated Documents' })).toBeInTheDocument()
  })

  it('defaults to hidden when the prop is not passed at all', () => {
    // A caller that has not yet resolved the flag must not leak the module.
    renderSidebar()

    expect(screen.getByText('DOCUMENTS')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Control Documents' })).not.toBeInTheDocument()
  })

  it('keeps the group label and all three entries when cdmEnabled is true', () => {
    renderSidebar({ cdmEnabled: true })

    expect(screen.getByText('KNOWLEDGE BASE')).toBeInTheDocument()
    expect(screen.queryByText('DOCUMENTS')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Control Documents' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Document Map' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Generated Documents' })).toBeInTheDocument()
  })

  it('routes to the CDM tabs when they are shown', () => {
    const onTabChange = vi.fn()
    renderSidebar({ onTabChange, cdmEnabled: true })

    fireEvent.click(screen.getByRole('button', { name: 'Control Documents' }))
    expect(onTabChange).toHaveBeenCalledWith('cdm')

    fireEvent.click(screen.getByRole('button', { name: 'Document Map' }))
    expect(onTabChange).toHaveBeenCalledWith('document-map')
  })

  it('leaves every other section untouched when CDM is hidden', () => {
    renderSidebar({ cdmEnabled: false, isPlatformAdmin: true, showConsultantPortal: true })

    expect(screen.getByText('OVERVIEW')).toBeInTheDocument()
    expect(screen.getByText('CONTROLS & FRAMEWORKS')).toBeInTheDocument()
    expect(screen.getByText('RISK & THIRD PARTY')).toBeInTheDocument()
    expect(screen.getByText('EVIDENCE')).toBeInTheDocument()
    expect(screen.getByText('OPERATIONS')).toBeInTheDocument()
    expect(screen.getByText('ADMIN')).toBeInTheDocument()
    expect(screen.getByText('PLATFORM')).toBeInTheDocument()
  })
})

describe('Sidebar nav footer', () => {
  it('shows the role-gate note when at least one gated entry is visible', () => {
    renderSidebar({ showConsultantPortal: true })
    expect(screen.getByText(/role-gated/)).toBeInTheDocument()
  })

  it('shows the role-gate note for platform admins', () => {
    renderSidebar({ isPlatformAdmin: true })
    expect(screen.getByText(/role-gated/)).toBeInTheDocument()
  })

  it('hides the role-gate note when no gated entries are visible', () => {
    renderSidebar({ showConsultantPortal: false, isPlatformAdmin: false })
    expect(screen.queryByText(/role-gated/)).not.toBeInTheDocument()
  })
})

describe('Sidebar tab routing', () => {
  it('calls onTabChange with the tab id on each nav item click', () => {
    const onTabChange = vi.fn()
    renderSidebar({ onTabChange })

    fireEvent.click(screen.getByRole('button', { name: 'Dashboard' }))
    expect(onTabChange).toHaveBeenCalledWith('dashboard')

    fireEvent.click(screen.getByRole('button', { name: 'Analytics' }))
    expect(onTabChange).toHaveBeenCalledWith('capability-posture')
  })

  it('calls onMobileClose when a nav item is selected', () => {
    const onTabChange = vi.fn()
    const onMobileClose = vi.fn()
    renderSidebar({ onTabChange, onMobileClose })

    fireEvent.click(screen.getByRole('button', { name: 'Dashboard' }))
    expect(onMobileClose).toHaveBeenCalled()
  })
})

describe('Sidebar mobile overlay', () => {
  it('renders the mobile overlay when mobileOpen is true', () => {
    renderSidebar({ mobileOpen: true })
    expect(document.querySelector('.mobile-nav-overlay')).toBeInTheDocument()
  })

  it('does not render the mobile overlay when mobileOpen is false', () => {
    renderSidebar({ mobileOpen: false })
    expect(document.querySelector('.mobile-nav-overlay')).not.toBeInTheDocument()
  })
})

describe('Sidebar pinned expansion', () => {
  it('renders expanded by default', () => {
    localStorage.removeItem('scf-nav-collapsed')
    renderSidebar()
    expect(document.querySelector('.sidebar-nav')?.className).toContain('expanded')
  })

  it('the toggle collapses the rail and persists the preference', () => {
    localStorage.removeItem('scf-nav-collapsed')
    renderSidebar()
    fireEvent.click(screen.getByRole('button', { name: 'Collapse' }))
    expect(document.querySelector('.sidebar-nav')?.className).not.toContain('expanded')
    expect(localStorage.getItem('scf-nav-collapsed')).toBe('1')
  })

  it('honors a stored collapsed preference on mount', () => {
    localStorage.setItem('scf-nav-collapsed', '1')
    renderSidebar()
    expect(document.querySelector('.sidebar-nav')?.className).not.toContain('expanded')
    localStorage.removeItem('scf-nav-collapsed')
  })
})
