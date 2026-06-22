import { useState, useRef, useEffect } from 'react'
import type { ClientSummary } from '../../types'

interface ClientSwitcherProps {
  clients: ClientSummary[]
  currentOrgId?: string
  currentOrgName?: string
  onSwitchOrg: (orgId: string) => void
  recentOrgIds?: string[]
  compact?: boolean
}

export default function ClientSwitcher({
  clients,
  currentOrgId,
  currentOrgName,
  onSwitchOrg,
  recentOrgIds = [],
  compact = false
}: ClientSwitcherProps) {
  const [isOpen, setIsOpen] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const dropdownRef = useRef<HTMLDivElement>(null)
  const searchInputRef = useRef<HTMLInputElement>(null)

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false)
        setSearchQuery('')
      }
    }

    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  // Focus search input when dropdown opens
  useEffect(() => {
    if (isOpen && searchInputRef.current) {
      searchInputRef.current.focus()
    }
  }, [isOpen])

  // Handle keyboard navigation
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      setIsOpen(false)
      setSearchQuery('')
    }
  }

  const currentClient = clients.find(c => c.organization_id === currentOrgId)

  // Filter clients based on search
  const filteredClients = clients.filter(c =>
    c.organization_name.toLowerCase().includes(searchQuery.toLowerCase())
  )

  // Get recent clients
  const recentClients = recentOrgIds
    .map(id => clients.find(c => c.organization_id === id))
    .filter((c): c is ClientSummary => c !== undefined && c.organization_id !== currentOrgId)
    .slice(0, 3)

  // Get other clients (not recent, not current)
  const otherClients = filteredClients.filter(c =>
    c.organization_id !== currentOrgId &&
    !recentOrgIds.includes(c.organization_id)
  )

  const handleSwitch = (orgId: string) => {
    onSwitchOrg(orgId)
    setIsOpen(false)
    setSearchQuery('')
  }

  const getReadinessColor = (percent: number): string => {
    if (percent >= 90) return 'var(--success)'
    if (percent >= 70) return 'var(--info)'
    if (percent >= 50) return 'var(--warning)'
    return 'var(--destructive)'
  }

  if (clients.length <= 1) {
    // No switching needed if only one client
    return null
  }

  return (
    <div
      className={`client-switcher ${compact ? 'client-switcher-compact' : ''}`}
      ref={dropdownRef}
      onKeyDown={handleKeyDown}
    >
      <button
        className="client-switcher-trigger"
        onClick={() => setIsOpen(!isOpen)}
        aria-expanded={isOpen}
        aria-haspopup="listbox"
      >
        <div className="switcher-current">
          <div className="switcher-org-icon">
            {(currentOrgName || currentClient?.organization_name || 'O').charAt(0).toUpperCase()}
          </div>
          {!compact && (
            <div className="switcher-org-info">
              <span className="switcher-org-name">
                {currentOrgName || currentClient?.organization_name || 'Select Organisation'}
              </span>
              {currentClient && (
                <span className="switcher-org-readiness">
                  {currentClient.framework_readiness_percent}% ready
                </span>
              )}
            </div>
          )}
        </div>
        <svg
          className={`switcher-chevron ${isOpen ? 'open' : ''}`}
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>

      {isOpen && (
        <div className="client-switcher-dropdown" role="listbox">
          {/* Search */}
          {clients.length > 5 && (
            <div className="switcher-search">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="11" cy="11" r="8" />
                <line x1="21" y1="21" x2="16.65" y2="16.65" />
              </svg>
              <input
                ref={searchInputRef}
                type="text"
                placeholder="Search organisations..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
              />
            </div>
          )}

          <div className="switcher-list">
            {/* Current Organisation */}
            {currentClient && !searchQuery && (
              <div className="switcher-section">
                <div className="switcher-section-label">Current</div>
                <div className="switcher-item switcher-item-current">
                  <div className="switcher-item-icon">
                    {currentClient.organization_name.charAt(0).toUpperCase()}
                  </div>
                  <div className="switcher-item-content">
                    <span className="switcher-item-name">{currentClient.organization_name}</span>
                    <span className="switcher-item-meta">
                      {currentClient.controls_implemented}/{currentClient.controls_total} controls
                    </span>
                  </div>
                  <div
                    className="switcher-item-readiness"
                    style={{ color: getReadinessColor(currentClient.framework_readiness_percent) }}
                  >
                    {currentClient.framework_readiness_percent}%
                  </div>
                  <span className="switcher-item-check">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <polyline points="20 6 9 17 4 12" />
                    </svg>
                  </span>
                </div>
              </div>
            )}

            {/* Recent Organisations */}
            {recentClients.length > 0 && !searchQuery && (
              <div className="switcher-section">
                <div className="switcher-section-label">Recent</div>
                {recentClients.map(client => (
                  <button
                    key={client.organization_id}
                    className="switcher-item"
                    onClick={() => handleSwitch(client.organization_id)}
                    role="option"
                  >
                    <div className="switcher-item-icon">
                      {client.organization_name.charAt(0).toUpperCase()}
                    </div>
                    <div className="switcher-item-content">
                      <span className="switcher-item-name">{client.organization_name}</span>
                      <span className="switcher-item-meta">
                        {client.controls_implemented}/{client.controls_total} controls
                      </span>
                    </div>
                    <div
                      className="switcher-item-readiness"
                      style={{ color: getReadinessColor(client.framework_readiness_percent) }}
                    >
                      {client.framework_readiness_percent}%
                    </div>
                  </button>
                ))}
              </div>
            )}

            {/* All/Other Organisations */}
            {(searchQuery ? filteredClients.filter(c => c.organization_id !== currentOrgId) : otherClients).length > 0 && (
              <div className="switcher-section">
                <div className="switcher-section-label">
                  {searchQuery ? 'Results' : 'All Organisations'}
                </div>
                {(searchQuery ? filteredClients.filter(c => c.organization_id !== currentOrgId) : otherClients).map(client => (
                  <button
                    key={client.organization_id}
                    className="switcher-item"
                    onClick={() => handleSwitch(client.organization_id)}
                    role="option"
                  >
                    <div className="switcher-item-icon">
                      {client.organization_name.charAt(0).toUpperCase()}
                    </div>
                    <div className="switcher-item-content">
                      <span className="switcher-item-name">{client.organization_name}</span>
                      <span className="switcher-item-meta">
                        {client.controls_implemented}/{client.controls_total} controls
                      </span>
                    </div>
                    <div
                      className="switcher-item-readiness"
                      style={{ color: getReadinessColor(client.framework_readiness_percent) }}
                    >
                      {client.framework_readiness_percent}%
                    </div>
                  </button>
                ))}
              </div>
            )}

            {/* No results */}
            {searchQuery && filteredClients.length === 0 && (
              <div className="switcher-no-results">
                No organisations match "{searchQuery}"
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
