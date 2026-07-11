import { useAuth } from '../contexts/AuthContext'
import { NotificationBell } from './NotificationBell'
import UserProfileDropdown from './UserProfileDropdown'
import ThemeMenu from './ThemeMenu'
import OrgSwitcher from './OrgSwitcher'
import { Organization, useOrganization } from '../contexts/OrganizationContext'
import { useOrgLogo } from '../hooks/useOrgLogo'

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
  const { currentOrg } = useOrganization()
  const { data: orgLogoUrl } = useOrgLogo(currentOrg?.id)

  // Get configurable logo and title from environment variables
  // If VITE_APP_LOGO is explicitly set to empty string, hide logo; otherwise use value or default
  const appLogoEnv = import.meta.env.VITE_APP_LOGO
  const appLogo = appLogoEnv === '' ? null : (appLogoEnv || '/cropped-Logo-301x101.webp')
  const appTitle = import.meta.env.VITE_APP_TITLE || 'SCF Controls Platform'
  // Org-uploaded logo takes precedence over the deploy-time default
  const logoSrc = orgLogoUrl || appLogo

  const showOrgSwitcher = isConsultant && clientOrgIds && clientOrgIds.length > 0

  return (
    <div className="header header-streamlined">
      {/* Left: Brand */}
      <div className="header-left">
        <div className="brand">
          {logoSrc && <img src={logoSrc} alt="Logo" />}
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
