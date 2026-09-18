/**
 * Task 6: Utility bar — page title + relocated utilities
 *
 * Covers:
 * - Page title renders from activeTab using TAB_TITLES
 * - All utility components present (bell, theme, profile, org switcher for consultants)
 * - No brand img in Header (brand relocated to Sidebar in Task 5)
 * - TAB_TITLES covers every member of the Tab union (type-level + runtime)
 * - Mobile hamburger toggle is present
 */
import { render as rtlRender, screen } from '@testing-library/react'
import type { ReactElement } from 'react'
import { WorkScopeProvider } from '../../contexts/WorkScopeContext'

/**
 * Work scope (#1052) is read from context by this tree. The header owns the
 * value; every render here supplies the provider so the component sees its
 * default ('everything') rather than throwing. Passed as RTL's ``wrapper`` so
 * that ``rerender`` keeps the provider in place.
 */
function render(ui: ReactElement, options?: Parameters<typeof rtlRender>[1]) {
  return rtlRender(ui, { wrapper: WorkScopeProvider, ...options })
}

import { describe, expect, it, vi } from 'vitest'

import Header from '../Header'
import { TAB_TITLES } from '../../data/appUrl'

// --- Mocks ---
vi.mock('../../hooks/useOrgLogo', () => ({
  useOrgLogo: vi.fn().mockReturnValue({ data: null }),
  ORG_LOGO_QUERY_KEY: 'organization-logo',
}))
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', name: 'Mark' } }),
}))
vi.mock('../../contexts/OrganizationContext', () => ({
  useOrganization: () => ({ currentOrg: { id: 'org-1', name: 'Acme' } }),
}))
vi.mock('../NotificationBell', () => ({
  NotificationBell: () => <div data-testid="notification-bell" />,
}))
// RefreshControl reads DataRefreshContext, which this suite deliberately does
// not mount — the utility bar's job here is composition, not refresh behaviour.
// RefreshControl has its own suite.
vi.mock('../RefreshControl', () => ({
  default: () => <div data-testid="refresh-control" />,
}))
vi.mock('../UserProfileDropdown', () => ({
  default: () => <div data-testid="user-profile-dropdown" />,
}))
vi.mock('../ThemeMenu', () => ({
  default: () => <div data-testid="theme-menu" />,
}))
vi.mock('../OrgSwitcher', () => ({
  default: () => <div data-testid="org-switcher" />,
}))

function renderHeader(props: Partial<Parameters<typeof Header>[0]> = {}) {
  return render(
    <Header activeTab="dashboard" onTabChange={vi.fn()} {...props} />
  )
}

// ----------- Page title -----------

describe('Header utility bar: page title', () => {
  it('renders "Control Library" when activeTab is "library"', () => {
    renderHeader({ activeTab: 'library' })
    expect(screen.getByText('Control Library')).toBeInTheDocument()
  })

  it('renders "Dashboard" when activeTab is "dashboard"', () => {
    renderHeader({ activeTab: 'dashboard' })
    expect(screen.getByText('Dashboard')).toBeInTheDocument()
  })

  it('renders "Analytics" when activeTab is "capability-posture"', () => {
    renderHeader({ activeTab: 'capability-posture' })
    expect(screen.getByText('Analytics')).toBeInTheDocument()
  })

  it('renders "Org Settings" when activeTab is "settings"', () => {
    renderHeader({ activeTab: 'settings' })
    expect(screen.getByText('Org Settings')).toBeInTheDocument()
  })
})

// ----------- Utilities present -----------

describe('Header utility bar: utilities', () => {
  it('renders ThemeMenu', () => {
    renderHeader()
    expect(screen.getByTestId('theme-menu')).toBeInTheDocument()
  })

  it('renders NotificationBell', () => {
    renderHeader()
    expect(screen.getByTestId('notification-bell')).toBeInTheDocument()
  })

  it('renders UserProfileDropdown', () => {
    renderHeader()
    expect(screen.getByTestId('user-profile-dropdown')).toBeInTheDocument()
  })

  it('renders OrgSwitcher when isConsultant and clientOrgIds are present', () => {
    renderHeader({ isConsultant: true, clientOrgIds: ['org-2'] })
    expect(screen.getByTestId('org-switcher')).toBeInTheDocument()
  })

  it('does NOT render OrgSwitcher for non-consultant users', () => {
    renderHeader({ isConsultant: false, clientOrgIds: [] })
    expect(screen.queryByTestId('org-switcher')).not.toBeInTheDocument()
  })

  it('renders mobile hamburger toggle button', () => {
    renderHeader()
    expect(
      screen.getByRole('button', { name: /open navigation|close navigation/i })
    ).toBeInTheDocument()
  })
})

// ----------- No brand in Header -----------

describe('Header utility bar: no brand', () => {
  it('does NOT render a brand logo img', () => {
    renderHeader()
    expect(screen.queryByAltText('Logo')).not.toBeInTheDocument()
  })

  it('does NOT render the brand title wordmark', () => {
    renderHeader()
    expect(screen.queryByText('SCF Controls Platform')).not.toBeInTheDocument()
  })
})

// ----------- TAB_TITLES completeness -----------

describe('TAB_TITLES', () => {
  const ALL_TABS: Array<
    | 'dashboard'
    | 'capability-posture'
    | 'library'
    | 'scoping'
    | 'evidence'
    | 'mapping-matrix'
    | 'tasks'
    | 'systems'
    | 'users'
    | 'consultant-portal'
    | 'risk-register'
    | 'vendors'
    | 'settings'
    | 'webhooks'
    | 'audit-log'
    | 'engagements'
    | 'documents'
    | 'platform-catalog'
    | 'platform-tenants'
    | 'catalog-changelog'
  > = [
    'dashboard',
    'capability-posture',
    'library',
    'scoping',
    'evidence',
    'mapping-matrix',
    'tasks',
    'systems',
    'users',
    'consultant-portal',
    'risk-register',
    'vendors',
    'settings',
    'webhooks',
    'audit-log',
    'engagements',
    'documents',
    'platform-catalog',
    'platform-tenants',
    'catalog-changelog',
  ]

  it('has a non-empty string for every Tab member', () => {
    for (const tab of ALL_TABS) {
      expect(TAB_TITLES[tab], `TAB_TITLES["${tab}"] must be a non-empty string`)
        .toBeTruthy()
      expect(typeof TAB_TITLES[tab]).toBe('string')
    }
  })

  it('maps "library" to "Control Library"', () => {
    expect(TAB_TITLES['library']).toBe('Control Library')
  })

  it('maps "capability-posture" to "Analytics"', () => {
    expect(TAB_TITLES['capability-posture']).toBe('Analytics')
  })

  it('maps "settings" to "Org Settings"', () => {
    expect(TAB_TITLES['settings']).toBe('Org Settings')
  })
})

// ----------- Work scope visibility (#1052) -----------

/**
 * The work-scope switch governs the Controls and Evidence lists and nothing
 * else. It must not render on tabs it does not filter — most importantly the
 * Library, which reads the same ``useScopedControlsQuery`` but deliberately
 * never sends ``my_teams`` because it is a catalog browser. A switch reading
 * "My teams" above an unfiltered catalog is the precise failure #1052 exists
 * to remove, so absence here is the assertion that matters.
 */
describe('Header utility bar: work scope visibility', () => {
  // The two lists that actually pass `my_teams`: the Controls list
  // (`library` → UnifiedLibraryPage) and the Evidence list. `scoping` is
  // FrameworkScopingPage, which picks frameworks and has no per-control rows
  // to narrow, so the switch was inert there and is deliberately gone.
  const scoped = ['library', 'evidence'] as const
  const unscoped = [
    'dashboard',
    'scoping',
    'capability-posture',
    'mapping-matrix',
    'tasks',
    'risk-register',
    'audit-log',
    'settings',
  ] as const

  it.each(scoped)('renders the work-scope switch on "%s"', (tab) => {
    renderHeader({ activeTab: tab })
    expect(screen.getByRole('radiogroup', { name: /showing/i })).toBeInTheDocument()
  })

  it.each(unscoped)('does not render the work-scope switch on "%s"', (tab) => {
    renderHeader({ activeTab: tab })
    expect(screen.queryByRole('radiogroup', { name: /showing/i })).toBeNull()
  })

  it('still renders the other utilities on a tab without the switch', () => {
    renderHeader({ activeTab: 'dashboard' })
    expect(screen.getByTestId('theme-menu')).toBeInTheDocument()
    expect(screen.getByTestId('notification-bell')).toBeInTheDocument()
  })
})
