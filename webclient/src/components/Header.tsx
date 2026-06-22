import { useAuth } from '../contexts/AuthContext'
import { NotificationBell } from './NotificationBell'
import UserProfileDropdown from './UserProfileDropdown'
import ThemeToggle from './ThemeToggle'
import OrgSwitcher from './OrgSwitcher'
import { Organization } from '../contexts/OrganizationContext'

type Tab = 'dashboard' | 'capability-posture' | 'library' | 'scoping' | 'evidence' | 'mapping-matrix' | 'tasks' | 'systems' | 'users' | 'consultant-portal' | 'risk-register' | 'vendors' | 'settings' | 'webhooks' | 'audit-log' | 'engagements' | 'cdm'

interface HeaderProps {
  activeTab: Tab
  onTabChange: (tab: Tab) => void
  onNavigateToEvidence?: (evidenceId: string) => void
  isConsultant?: boolean
  clientOrgIds?: string[]
  onOrgSwitch?: (org: Organization) => void
}

export default function Header({
  activeTab,
  onTabChange,
  onNavigateToEvidence,
  isConsultant,
  clientOrgIds,
  onOrgSwitch
}: HeaderProps) {
  const { user } = useAuth()

  // Get configurable logo and title from environment variables
  // If VITE_APP_LOGO is explicitly set to empty string, hide logo; otherwise use value or default
  const appLogoEnv = import.meta.env.VITE_APP_LOGO
  const appLogo = appLogoEnv === '' ? null : (appLogoEnv || '/cropped-Logo-301x101.webp')
  const appTitle = import.meta.env.VITE_APP_TITLE || 'SCF Controls Platform'

  const showOrgSwitcher = isConsultant && clientOrgIds && clientOrgIds.length > 0

  return (
    <div className="header header-streamlined">
      {/* Left: Brand */}
      <div className="header-left">
        <div className="brand">
          {appLogo && <img src={appLogo} alt="Logo" />}
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
        <ThemeToggle />

        {user && (
          <div className="header-user-section">
            <NotificationBell
              onNavigateToEvidence={onNavigateToEvidence}
              onNavigateToControl={() => {
                onTabChange('scoping');
              }}
              onNavigateToTask={() => onTabChange('tasks')}
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
