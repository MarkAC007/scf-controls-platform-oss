import { useEffect, useState } from 'react'
import { useAuth } from '../contexts/AuthContext'
import { NotificationBell } from './NotificationBell'
import UserProfileDropdown from './UserProfileDropdown'
import ThemeMenu from './ThemeMenu'
import OrgSwitcher from './OrgSwitcher'
import { Organization, useOrganization } from '../contexts/OrganizationContext'
import { useOrgLogo } from '../hooks/useOrgLogo'

type Tab = 'dashboard' | 'capability-posture' | 'library' | 'scoping' | 'evidence' | 'mapping-matrix' | 'tasks' | 'systems' | 'users' | 'consultant-portal' | 'risk-register' | 'vendors' | 'settings' | 'webhooks' | 'audit-log' | 'engagements' | 'cdm' | 'document-map' | 'documents' | 'platform-catalog' | 'platform-tenants' | 'catalog-changelog'

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
  const { currentOrg } = useOrganization()
  const { data: orgLogoUrl } = useOrgLogo(currentOrg?.id)

  // Get configurable logo and title from environment variables
  // If VITE_APP_LOGO is explicitly set to empty string, hide logo; otherwise use value or default
  const appLogoEnv = import.meta.env.VITE_APP_LOGO
  const appLogo = appLogoEnv === '' ? null : (appLogoEnv || '/cropped-Logo-301x101.webp')
  const appTitle = import.meta.env.VITE_APP_TITLE || 'SCF Controls Platform'
  // Org-uploaded logo takes precedence over the deploy-time default
  const logoSrc = orgLogoUrl || appLogo

  // A logo that fails to load degrades to the wordmark alone. Without this the
  // header rendered the browser's broken-image icon on every route (#807).
  const [logoBroken, setLogoBroken] = useState(false)
  useEffect(() => {
    setLogoBroken(false)
  }, [logoSrc])

  const showOrgSwitcher = isConsultant && clientOrgIds && clientOrgIds.length > 0

  return (
    <div className="header header-streamlined">
      {/* Left: Brand (mobile hamburger first — hidden on desktop via CSS) */}
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
        <div className="brand">
          {logoSrc && !logoBroken && (
            <img src={logoSrc} alt="Logo" onError={() => setLogoBroken(true)} />
          )}
          <div className="brand-title">{appTitle}</div>
        </div>
      </div>

      {/* Center: Org Switcher (consultant only) */}
      {showOrgSwitcher && (
        <div className="header-center">
          <OrgSwitcher
            compact
            clientOrgIds={clientOrgIds}
            onSwitch={onOrgSwitch}
          />
        </div>
      )}

      {/* Right: Theme & User */}
      <div className="header-right">
        <ThemeMenu />

        {user && (
          <div className="header-user-section">
            <NotificationBell
              onNavigateToEvidence={onNavigateToEvidence}
              onNavigateToControl={(controlId) => {
                // Forward the control id — dropping it here left control
                // notifications landing on an unfiltered scoping list.
                if (onNavigateToControl) {
                  onNavigateToControl(controlId);
                } else {
                  onTabChange('scoping');
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
