import { NotificationBell } from './NotificationBell'
import RefreshControl from './RefreshControl'
import UserProfileDropdown from './UserProfileDropdown'
import ThemeMenu from './ThemeMenu'
import OrgSwitcher from './OrgSwitcher'
import WorkScopeControl from './WorkScopeControl'
import { Organization } from '../contexts/OrganizationContext'
import { useAuth } from '../contexts/AuthContext'
import { Tab, TAB_TITLES } from '../data/appUrl'

/**
 * The tabs whose lists honour the work scope: the Controls list and the
 * Evidence list, and nothing else.
 *
 * `library` IS the Controls page — `UnifiedLibraryPage`, the list a user
 * actually works in. `scoping` is `FrameworkScopingPage`, which picks
 * frameworks and has no per-control rows to narrow, so the switch was inert
 * there and is deliberately gone.
 */
const WORK_SCOPE_TABS: ReadonlySet<Tab> = new Set<Tab>(['library', 'evidence'])

interface HeaderProps {
  activeTab: Tab
  onTabChange: (tab: Tab) => void
  onNavigateToEvidence?: (evidenceId: string) => void
  onNavigateToControl?: (controlId: string) => void
  isConsultant?: boolean
  clientOrgIds?: string[]
  onOrgSwitch?: (org: Organization) => void
  /** Mobile: toggle the navigation drawer (button only rendered below the mobile breakpoint via CSS) */
  onMobileNavToggle?: () => void
  mobileNavOpen?: boolean
}

export default function Header({
  activeTab,
  onTabChange,
  onNavigateToEvidence,
  onNavigateToControl,
  isConsultant,
  clientOrgIds,
  onOrgSwitch,
  onMobileNavToggle,
  mobileNavOpen = false
}: HeaderProps) {
  const { user } = useAuth()

  const showOrgSwitcher = isConsultant && clientOrgIds && clientOrgIds.length > 0

  const pageTitle = TAB_TITLES[activeTab]

  return (
    <div className="header header-streamlined">
      {/* Left: mobile hamburger (hidden on desktop via CSS) + page title */}
      <div className="header-left">
        <button
          className="mobile-nav-toggle"
          aria-label={mobileNavOpen ? 'Close navigation' : 'Open navigation'}
          aria-expanded={mobileNavOpen}
          onClick={onMobileNavToggle}
        >
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            {mobileNavOpen ? (
              <>
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </>
            ) : (
              <>
                <line x1="3" y1="6" x2="21" y2="6" />
                <line x1="3" y1="12" x2="21" y2="12" />
                <line x1="3" y1="18" x2="21" y2="18" />
              </>
            )}
          </svg>
        </button>
        <span className="header-page-title">{pageTitle}</span>
      </div>

      {/* Right: utilities cluster */}
      <div className="header-right">
        {/* Org switcher chip — consultant-only, keep today's render condition */}
        {showOrgSwitcher && (
          <OrgSwitcher
            compact
            clientOrgIds={clientOrgIds}
            onSwitch={onOrgSwitch}
          />
        )}

        {/* Work scope — the one place the "whose work?" question is answered.
            Sits next to the org switcher because the two together say which
            slice of which organisation the list below is showing.

            Rendered ONLY on the tabs whose list actually passes `my_teams`
            (see WORK_SCOPE_TABS above). A control rendered over a list that
            ignores it would read "My teams" across an unfiltered page, which is
            the exact failure this issue exists to remove. */}
        {WORK_SCOPE_TABS.has(activeTab) && <WorkScopeControl />}

        <ThemeMenu />

        {user && (
          <div className="header-user-section">
            <RefreshControl />
            <NotificationBell
              onNavigateToEvidence={onNavigateToEvidence}
              onNavigateToControl={(controlId) => {
                if (onNavigateToControl) {
                  onNavigateToControl(controlId)
                } else {
                  onTabChange('scoping')
                }
              }}
              onNavigateToTask={() => onTabChange('tasks')}
              onNavigateToChangelog={() => onTabChange('catalog-changelog')}
            />
            <UserProfileDropdown
              onNavigateToUsers={() => onTabChange('users')}
            />
          </div>
        )}
      </div>
    </div>
  )
}
