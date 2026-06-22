import { useState, useMemo } from 'react'
import type { ClientSummary, ConsultantInvite } from '../../types'
import ClientCard from './ClientCard'
import InviteClientModal from './InviteClientModal'
import CrossOrgComparison from './CrossOrgComparison'

interface ConsultantDashboardProps {
  clients: ClientSummary[]
  currentOrgId?: string
  pendingInvites?: ConsultantInvite[]
  onCancelInvite?: (inviteId: string) => void
  onInviteClient?: (email: string, orgName: string) => Promise<void>
  onCreateOrg?: (orgName: string) => Promise<{ id: string; name: string }>
  onInviteAdmin?: (orgId: string, email: string) => Promise<void>
}

type ViewMode = 'grid' | 'comparison'
type SortBy = 'name' | 'readiness' | 'activity'

export default function ConsultantDashboard({
  clients,
  currentOrgId,
  pendingInvites = [],
  onCancelInvite,
  onInviteClient,
  onCreateOrg,
  onInviteAdmin
}: ConsultantDashboardProps) {
  const [viewMode, setViewMode] = useState<ViewMode>('grid')
  const [sortBy, setSortBy] = useState<SortBy>('activity')
  const [searchQuery, setSearchQuery] = useState('')
  const [showInviteModal, setShowInviteModal] = useState(false)

  // Calculate summary statistics
  const stats = useMemo(() => {
    if (clients.length === 0) {
      return {
        totalClients: 0,
        avgReadiness: 0,
        totalControlsImplemented: 0,
        totalControlsTotal: 0,
        clientsAtRisk: 0,
        awaitingAdmin: 0
      }
    }

    const totalClients = clients.length
    const avgReadiness = Math.round(
      clients.reduce((sum, c) => sum + c.framework_readiness_percent, 0) / totalClients
    )
    const totalControlsImplemented = clients.reduce((sum, c) => sum + c.controls_implemented, 0)
    const totalControlsTotal = clients.reduce((sum, c) => sum + c.controls_total, 0)
    const clientsAtRisk = clients.filter(c => c.controls_at_risk > 0).length
    const awaitingAdmin = clients.filter(c => c.awaiting_admin).length

    return {
      totalClients,
      avgReadiness,
      totalControlsImplemented,
      totalControlsTotal,
      clientsAtRisk,
      awaitingAdmin
    }
  }, [clients])

  // Filter and sort clients
  const filteredClients = useMemo(() => {
    let result = [...clients]

    // Apply search filter
    if (searchQuery.trim()) {
      const query = searchQuery.toLowerCase()
      result = result.filter(c =>
        c.organization_name.toLowerCase().includes(query) ||
        c.primary_framework?.toLowerCase().includes(query)
      )
    }

    // Apply sorting
    result.sort((a, b) => {
      switch (sortBy) {
        case 'name':
          return a.organization_name.localeCompare(b.organization_name)
        case 'readiness':
          return b.framework_readiness_percent - a.framework_readiness_percent
        case 'activity':
        default:
          return new Date(b.last_activity_date).getTime() - new Date(a.last_activity_date).getTime()
      }
    })

    return result
  }, [clients, searchQuery, sortBy])

  const handleInviteSubmit = async (email: string, orgName: string) => {
    if (onInviteClient) {
      await onInviteClient(email, orgName)
    }
    setShowInviteModal(false)
  }

  if (clients.length === 0) {
    return (
      <div className="consultant-dashboard">
        <div className="consultant-empty-state">
          <div className="empty-icon">
            <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
              <circle cx="9" cy="7" r="4" />
              <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
              <path d="M16 3.13a4 4 0 0 1 0 7.75" />
            </svg>
          </div>
          <h2>No Clients Yet</h2>
          <p>Start building your consultancy portfolio by inviting your first client organisation.</p>
          <button
            className="btn-primary btn-invite-first"
            onClick={() => setShowInviteModal(true)}
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
              <circle cx="8.5" cy="7" r="4" />
              <line x1="20" y1="8" x2="20" y2="14" />
              <line x1="23" y1="11" x2="17" y2="11" />
            </svg>
            Invite Client
          </button>
        </div>

        {showInviteModal && (
          <InviteClientModal
            pendingInvites={pendingInvites}
            onClose={() => setShowInviteModal(false)}
            onSubmit={handleInviteSubmit}
            onCreateOrg={onCreateOrg}
            onInviteAdmin={onInviteAdmin}
            onCancelInvite={onCancelInvite}
          />
        )}
      </div>
    )
  }

  return (
    <div className="consultant-dashboard">
      {/* Header */}
      <div className="consultant-header">
        <div className="consultant-header-left">
          <h1>Consultant Portal</h1>
          <p className="consultant-subtitle">Manage your client organisations</p>
        </div>
        <div className="consultant-header-actions">
          <button
            className="btn-primary btn-invite"
            onClick={() => setShowInviteModal(true)}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
              <circle cx="8.5" cy="7" r="4" />
              <line x1="20" y1="8" x2="20" y2="14" />
              <line x1="23" y1="11" x2="17" y2="11" />
            </svg>
            Invite Client
          </button>
        </div>
      </div>

      {/* Summary Stats */}
      <div className="consultant-stats">
        <div className="consultant-stat-card">
          <div className="stat-icon">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
              <circle cx="9" cy="7" r="4" />
              <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
              <path d="M16 3.13a4 4 0 0 1 0 7.75" />
            </svg>
          </div>
          <div className="stat-content">
            <div className="stat-value">{stats.totalClients}</div>
            <div className="stat-label">Total Clients</div>
          </div>
        </div>

        <div className="consultant-stat-card">
          <div className="stat-icon stat-icon-readiness">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
              <polyline points="22 4 12 14.01 9 11.01" />
            </svg>
          </div>
          <div className="stat-content">
            <div className="stat-value">{stats.avgReadiness}%</div>
            <div className="stat-label">Avg. Readiness</div>
          </div>
        </div>

        <div className="consultant-stat-card">
          <div className="stat-icon stat-icon-controls">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M9 11l3 3L22 4" />
              <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
            </svg>
          </div>
          <div className="stat-content">
            <div className="stat-value">
              {stats.totalControlsImplemented}
              <span className="stat-total">/{stats.totalControlsTotal}</span>
            </div>
            <div className="stat-label">Controls Implemented</div>
          </div>
        </div>

        {stats.clientsAtRisk > 0 && (
          <div className="consultant-stat-card stat-card-warning">
            <div className="stat-icon stat-icon-risk">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
                <line x1="12" y1="9" x2="12" y2="13" />
                <line x1="12" y1="17" x2="12.01" y2="17" />
              </svg>
            </div>
            <div className="stat-content">
              <div className="stat-value">{stats.clientsAtRisk}</div>
              <div className="stat-label">Clients with Risks</div>
            </div>
          </div>
        )}

        {stats.awaitingAdmin > 0 && (
          <div className="consultant-stat-card stat-card-info">
            <div className="stat-icon stat-icon-awaiting">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="10" />
                <polyline points="12 6 12 12 16 14" />
              </svg>
            </div>
            <div className="stat-content">
              <div className="stat-value">{stats.awaitingAdmin}</div>
              <div className="stat-label">Awaiting Admin</div>
            </div>
          </div>
        )}
      </div>

      {/* Controls Bar */}
      <div className="consultant-controls">
        <div className="consultant-search">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="11" cy="11" r="8" />
            <line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
          <input
            type="text"
            placeholder="Search clients..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>

        <div className="consultant-view-controls">
          <div className="sort-control">
            <label>Sort by:</label>
            <select value={sortBy} onChange={(e) => setSortBy(e.target.value as SortBy)}>
              <option value="activity">Recent Activity</option>
              <option value="name">Name</option>
              <option value="readiness">Readiness</option>
            </select>
          </div>

          <div className="view-toggle">
            <button
              className={`view-toggle-btn ${viewMode === 'grid' ? 'active' : ''}`}
              onClick={() => setViewMode('grid')}
              title="Grid view"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="3" y="3" width="7" height="7" />
                <rect x="14" y="3" width="7" height="7" />
                <rect x="14" y="14" width="7" height="7" />
                <rect x="3" y="14" width="7" height="7" />
              </svg>
            </button>
            <button
              className={`view-toggle-btn ${viewMode === 'comparison' ? 'active' : ''}`}
              onClick={() => setViewMode('comparison')}
              title="Comparison view"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <line x1="18" y1="20" x2="18" y2="10" />
                <line x1="12" y1="20" x2="12" y2="4" />
                <line x1="6" y1="20" x2="6" y2="14" />
              </svg>
            </button>
          </div>
        </div>
      </div>

      {/* Main Content */}
      {viewMode === 'grid' ? (
        <div className="client-grid">
          {filteredClients.map(client => (
            <ClientCard
              key={client.organization_id}
              client={client}
              isCurrentOrg={client.organization_id === currentOrgId}
            />
          ))}
        </div>
      ) : (
        <CrossOrgComparison clients={filteredClients} currentOrgId={currentOrgId} />
      )}

      {filteredClients.length === 0 && searchQuery && (
        <div className="consultant-no-results">
          <p>No clients match your search for "{searchQuery}"</p>
          <button
            className="btn-text"
            onClick={() => setSearchQuery('')}
          >
            Clear search
          </button>
        </div>
      )}

      {/* Invite Modal */}
      {showInviteModal && (
        <InviteClientModal
          pendingInvites={pendingInvites}
          onClose={() => setShowInviteModal(false)}
          onSubmit={handleInviteSubmit}
          onCreateOrg={onCreateOrg}
          onInviteAdmin={onInviteAdmin}
          onCancelInvite={onCancelInvite}
        />
      )}
    </div>
  )
}
