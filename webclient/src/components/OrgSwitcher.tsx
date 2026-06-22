/**
 * OrgSwitcher - Organization switching dropdown component
 *
 * Displays:
 * - Current organization name
 * - Dropdown with all accessible organisations
 * - Recent organisations for quick access
 * - Loading and error states
 *
 * SECURITY: Only shows organisations the user has access to
 * (filtered by backend based on membership + consultant relationships)
 */
import { useState, useRef, useEffect } from 'react'
import { useOrganization, Organization } from '../contexts/OrganizationContext'

interface OrgSwitcherProps {
  /** Optional callback when org is switched */
  onSwitch?: (org: Organization) => void
  /** Compact mode for smaller spaces */
  compact?: boolean
  /** Client org IDs for consultant mode — groups dropdown into "My Organisation" / "Client Organisations" */
  clientOrgIds?: string[]
}

export default function OrgSwitcher({ onSwitch, compact = false, clientOrgIds }: OrgSwitcherProps) {
  const {
    currentOrg,
    availableOrgs,
    isLoading,
    error,
    recentOrgIds,
    switchOrganization,
    refreshOrganizations
  } = useOrganization()

  const [isOpen, setIsOpen] = useState(false)
  const [isSwitching, setIsSwitching] = useState(false)
  const dropdownRef = useRef<HTMLDivElement>(null)

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false)
      }
    }

    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  // Handle org selection
  const handleSelect = async (org: Organization) => {
    if (org.id === currentOrg?.id) {
      setIsOpen(false)
      return
    }

    setIsSwitching(true)
    try {
      await switchOrganization(org.id)
      onSwitch?.(org)
      setIsOpen(false)
    } catch (err) {
      console.error('Failed to switch organisation:', err)
    } finally {
      setIsSwitching(false)
    }
  }

  // Consultant mode: group by own vs client orgs
  const isConsultantMode = clientOrgIds && clientOrgIds.length > 0
  const myOrgs = isConsultantMode
    ? availableOrgs.filter(o => o.id !== currentOrg?.id && !clientOrgIds.includes(o.id))
    : []
  const clientOrgs = isConsultantMode
    ? availableOrgs.filter(o => o.id !== currentOrg?.id && clientOrgIds.includes(o.id))
    : []

  // Standard mode: group by recent vs other
  const recentOrgs = isConsultantMode ? [] : recentOrgIds
    .map(id => availableOrgs.find(o => o.id === id))
    .filter((o): o is Organization => o !== undefined && o.id !== currentOrg?.id)
    .slice(0, 3)

  const otherOrgs = isConsultantMode ? [] : availableOrgs.filter(
    o => o.id !== currentOrg?.id && !recentOrgIds.includes(o.id)
  )

  // Determine if current org is a client org (for label)
  const currentIsClient = isConsultantMode && currentOrg && clientOrgIds.includes(currentOrg.id)

  // Loading state
  if (isLoading) {
    return (
      <div className={`org-switcher ${compact ? 'compact' : ''}`}>
        <div className="org-switcher-trigger loading">
          <div className="org-icon">
            <div className="loading-spinner small" />
          </div>
          <span className="org-name">Loading...</span>
        </div>
      </div>
    )
  }

  // Error state
  if (error) {
    return (
      <div className={`org-switcher ${compact ? 'compact' : ''}`}>
        <div className="org-switcher-trigger error" onClick={() => refreshOrganizations()}>
          <div className="org-icon error">!</div>
          <span className="org-name">Error loading orgs</span>
        </div>
      </div>
    )
  }

  // No current org
  if (!currentOrg) {
    return (
      <div className={`org-switcher ${compact ? 'compact' : ''}`}>
        <div className="org-switcher-trigger empty">
          <div className="org-icon">-</div>
          <span className="org-name">No organisation</span>
        </div>
      </div>
    )
  }

  return (
    <div className={`org-switcher ${compact ? 'compact' : ''}`} ref={dropdownRef}>
      <button
        className={`org-switcher-trigger ${isOpen ? 'open' : ''}`}
        onClick={() => setIsOpen(!isOpen)}
        disabled={isSwitching}
      >
        <div className={`org-icon ${currentIsClient ? 'client' : ''}`}>
          {currentOrg.name.charAt(0).toUpperCase()}
        </div>
        <div className="org-details">
          <span className="org-name">{currentOrg.name}</span>
          {!compact && (
            <span className="org-slug">
              {isConsultantMode ? (currentIsClient ? 'Client' : 'My Org') : currentOrg.slug}
            </span>
          )}
        </div>
        <div className={`dropdown-chevron ${isOpen ? 'open' : ''}`}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <polyline points="6 9 12 15 18 9" />
          </svg>
        </div>
      </button>

      {isOpen && (
        <div className="org-switcher-dropdown">
          {/* Current org indicator */}
          <div className="dropdown-section current">
            <div className="section-label">
              {isConsultantMode
                ? (currentIsClient ? 'Viewing Client' : 'My Organisation')
                : 'Current Organisation'}
            </div>
            <div className="org-item current">
              <div className={`org-icon ${currentIsClient ? 'client' : ''}`}>
                {currentOrg.name.charAt(0).toUpperCase()}
              </div>
              <div className="org-details">
                <span className="org-name">{currentOrg.name}</span>
                <span className="org-slug">{currentOrg.slug}</span>
              </div>
              <div className="current-indicator">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <polyline points="20 6 9 17 4 12" />
                </svg>
              </div>
            </div>
          </div>

          {/* Consultant mode: My Organisation(s) */}
          {isConsultantMode && myOrgs.length > 0 && (
            <div className="dropdown-section">
              <div className="section-label">My Organisation</div>
              {myOrgs.map(org => (
                <button
                  key={org.id}
                  className="org-item"
                  onClick={() => handleSelect(org)}
                  disabled={isSwitching}
                >
                  <div className="org-icon">{org.name.charAt(0).toUpperCase()}</div>
                  <div className="org-details">
                    <span className="org-name">{org.name}</span>
                    <span className="org-slug">{org.slug}</span>
                  </div>
                </button>
              ))}
            </div>
          )}

          {/* Consultant mode: Client Organisations */}
          {isConsultantMode && clientOrgs.length > 0 && (
            <div className="dropdown-section">
              <div className="section-label">Client Organisations</div>
              {clientOrgs.map(org => (
                <button
                  key={org.id}
                  className="org-item"
                  onClick={() => handleSelect(org)}
                  disabled={isSwitching}
                >
                  <div className="org-icon client">{org.name.charAt(0).toUpperCase()}</div>
                  <div className="org-details">
                    <span className="org-name">{org.name}</span>
                    <span className="org-slug">{org.slug}</span>
                  </div>
                </button>
              ))}
            </div>
          )}

          {/* Standard mode: Recent organisations */}
          {!isConsultantMode && recentOrgs.length > 0 && (
            <div className="dropdown-section recent">
              <div className="section-label">Recent</div>
              {recentOrgs.map(org => (
                <button
                  key={org.id}
                  className="org-item"
                  onClick={() => handleSelect(org)}
                  disabled={isSwitching}
                >
                  <div className="org-icon">{org.name.charAt(0).toUpperCase()}</div>
                  <div className="org-details">
                    <span className="org-name">{org.name}</span>
                    <span className="org-slug">{org.slug}</span>
                  </div>
                </button>
              ))}
            </div>
          )}

          {/* Standard mode: Other organisations */}
          {!isConsultantMode && otherOrgs.length > 0 && (
            <div className="dropdown-section other">
              <div className="section-label">
                {recentOrgs.length > 0 ? 'Other Organisations' : 'Switch Organisation'}
              </div>
              {otherOrgs.map(org => (
                <button
                  key={org.id}
                  className="org-item"
                  onClick={() => handleSelect(org)}
                  disabled={isSwitching}
                >
                  <div className="org-icon">{org.name.charAt(0).toUpperCase()}</div>
                  <div className="org-details">
                    <span className="org-name">{org.name}</span>
                    <span className="org-slug">{org.slug}</span>
                  </div>
                </button>
              ))}
            </div>
          )}

          {/* No other orgs available */}
          {!isConsultantMode && recentOrgs.length === 0 && otherOrgs.length === 0 && (
            <div className="dropdown-section empty">
              <p className="empty-message">No other organisations available</p>
            </div>
          )}
          {isConsultantMode && myOrgs.length === 0 && clientOrgs.length === 0 && (
            <div className="dropdown-section empty">
              <p className="empty-message">No other organisations available</p>
            </div>
          )}

          {/* Switching indicator */}
          {isSwitching && (
            <div className="switching-overlay">
              <div className="loading-spinner" />
              <span>Switching...</span>
            </div>
          )}
        </div>
      )}

      <style>{`
        .org-switcher {
          position: relative;
          font-family: inherit;
        }

        .org-switcher-trigger {
          display: flex;
          align-items: center;
          gap: 0.75rem;
          padding: 0.5rem 0.75rem;
          background: var(--color-surface, #1e293b);
          border: 1px solid var(--color-border, #334155);
          border-radius: 8px;
          cursor: pointer;
          transition: all 0.2s;
          min-width: 200px;
        }

        .org-switcher.compact .org-switcher-trigger {
          min-width: auto;
          padding: 0.375rem 0.5rem;
        }

        .org-switcher-trigger:hover:not(:disabled) {
          background: var(--color-surface-hover, #334155);
          border-color: var(--color-border-hover, #475569);
        }

        .org-switcher-trigger.open {
          background: var(--color-surface-hover, #334155);
        }

        .org-switcher-trigger:disabled {
          opacity: 0.6;
          cursor: not-allowed;
        }

        .org-switcher-trigger.loading,
        .org-switcher-trigger.error,
        .org-switcher-trigger.empty {
          cursor: default;
        }

        .org-switcher-trigger.error {
          border-color: var(--color-error, #ef4444);
          cursor: pointer;
        }

        .org-icon {
          width: 28px;
          height: 28px;
          border-radius: 6px;
          background: var(--color-primary, #3b82f6);
          color: white;
          display: flex;
          align-items: center;
          justify-content: center;
          font-weight: 600;
          font-size: 0.875rem;
          flex-shrink: 0;
        }

        .org-icon.error {
          background: var(--color-error, #ef4444);
        }

        .org-icon.client {
          background: var(--color-warning, #f59e0b);
        }

        .org-details {
          flex: 1;
          min-width: 0;
          display: flex;
          flex-direction: column;
          align-items: flex-start;
        }

        .org-name {
          font-weight: 500;
          color: var(--color-text, #f1f5f9);
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
          max-width: 100%;
          font-size: 0.875rem;
        }

        .org-slug {
          font-size: 0.75rem;
          color: var(--color-text-secondary, #94a3b8);
        }

        .dropdown-chevron {
          width: 16px;
          height: 16px;
          color: var(--color-text-secondary, #94a3b8);
          transition: transform 0.2s;
          flex-shrink: 0;
        }

        .dropdown-chevron.open {
          transform: rotate(180deg);
        }

        .dropdown-chevron svg {
          width: 100%;
          height: 100%;
        }

        .org-switcher-dropdown {
          position: absolute;
          top: calc(100% + 4px);
          left: 0;
          right: 0;
          min-width: 280px;
          background: var(--color-surface, #1e293b);
          border: 1px solid var(--color-border, #334155);
          border-radius: 8px;
          box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.4);
          z-index: 1000;
          overflow: hidden;
        }

        .dropdown-section {
          padding: 0.5rem;
        }

        .dropdown-section:not(:last-child) {
          border-bottom: 1px solid var(--color-border, #334155);
        }

        .section-label {
          font-size: 0.625rem;
          font-weight: 600;
          text-transform: uppercase;
          letter-spacing: 0.05em;
          color: var(--color-text-tertiary, #64748b);
          padding: 0.25rem 0.5rem;
          margin-bottom: 0.25rem;
        }

        .org-item {
          display: flex;
          align-items: center;
          gap: 0.75rem;
          padding: 0.5rem;
          border-radius: 6px;
          cursor: pointer;
          transition: background 0.15s;
          width: 100%;
          border: none;
          background: transparent;
          text-align: left;
        }

        .org-item:hover:not(:disabled) {
          background: var(--color-surface-hover, #334155);
        }

        .org-item:disabled {
          opacity: 0.6;
          cursor: not-allowed;
        }

        .org-item.current {
          background: rgba(59, 130, 246, 0.1);
          cursor: default;
        }

        .org-item .org-icon {
          width: 32px;
          height: 32px;
        }

        .current-indicator {
          width: 20px;
          height: 20px;
          color: var(--color-primary, #3b82f6);
        }

        .current-indicator svg {
          width: 100%;
          height: 100%;
        }

        .empty-message {
          text-align: center;
          color: var(--color-text-secondary, #94a3b8);
          font-size: 0.875rem;
          padding: 0.5rem;
        }

        .switching-overlay {
          position: absolute;
          inset: 0;
          background: rgba(15, 23, 42, 0.9);
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          gap: 0.5rem;
          color: var(--color-text, #f1f5f9);
          font-size: 0.875rem;
        }

        .loading-spinner.small {
          width: 16px;
          height: 16px;
        }
      `}</style>
    </div>
  )
}
