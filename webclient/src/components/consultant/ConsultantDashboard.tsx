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
      <div className="consultant-page">
        {/* Toolbar still shown in empty state so invite is accessible */}
        <div className="consultant-toolbar">
          <div className="consultant-toolbar-search">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="11" cy="11" r="8" />
              <line x1="21" y1="21" x2="16.65" y2="16.65" />
            </svg>
            <input
              type="text"
              placeholder="Search clients — organisation, framework…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              aria-label="Search clients"
            />
          </div>
          <div className="consultant-toolbar-right">
            <button
              className="consultant-invite-btn"
              onClick={() => setShowInviteModal(true)}
            >
              + Invite Client
            </button>
          </div>
        </div>

        <div className="consultant-empty">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" style={{ marginBottom: 16 }}>
            <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
            <circle cx="9" cy="7" r="4" />
            <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
            <path d="M16 3.13a4 4 0 0 1 0 7.75" />
          </svg>
          <h2>No Clients Yet</h2>
          <p>Start building your consultancy portfolio by inviting your first client organisation.</p>
          <button
            className="consultant-invite-btn"
            onClick={() => setShowInviteModal(true)}
          >
            + Invite Client
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
    <div className="consultant-page">
      {/* Toolbar: search · sort · view toggle · invite */}
      <div className="consultant-toolbar">
        <div className="consultant-toolbar-search">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="11" cy="11" r="8" />
            <line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
          <input
            type="text"
            placeholder="Search clients — organisation, framework…"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            aria-label="Search clients"
          />
        </div>
        <div className="consultant-toolbar-right">
          <span className="consultant-sort-label">
            Sort by{' '}
            <select
              className="consultant-sort-select"
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value as SortBy)}
              aria-label="Sort clients by"
            >
              <option value="activity">Recent activity</option>
              <option value="name">Name</option>
              <option value="readiness">Readiness</option>
            </select>
          </span>

          <div className="consultant-view-toggle" role="group" aria-label="View mode">
            <button
              className={`consultant-view-toggle-btn${viewMode === 'grid' ? ' active' : ''}`}
              onClick={() => setViewMode('grid')}
              aria-pressed={viewMode === 'grid'}
            >
              Grid
            </button>
            <button
              className={`consultant-view-toggle-btn${viewMode === 'comparison' ? ' active' : ''}`}
              onClick={() => setViewMode('comparison')}
              aria-pressed={viewMode === 'comparison'}
            >
              Comparison
            </button>
          </div>

          <button
            className="consultant-invite-btn"
            onClick={() => setShowInviteModal(true)}
          >
            + Invite Client
          </button>
        </div>
      </div>

      {/* Portfolio stats strip */}
      <div className="consultant-stats-strip">
        <div className="consultant-stat-tile">
          <div className="consultant-stat-tile-label">Total Clients</div>
          <div className="consultant-stat-tile-value">{stats.totalClients}</div>
        </div>
        <div className="consultant-stat-tile">
          <div className="consultant-stat-tile-label">Avg. Readiness</div>
          <div className="consultant-stat-tile-value">{stats.avgReadiness}%</div>
        </div>
        <div className="consultant-stat-tile">
          <div className="consultant-stat-tile-label">Controls Implemented</div>
          <div className="consultant-stat-tile-value">
            {stats.totalControlsImplemented}
            <span className="stat-denom">/{stats.totalControlsTotal}</span>
          </div>
        </div>
        {stats.clientsAtRisk > 0 && (
          <div className="consultant-stat-tile">
            <div className="consultant-stat-tile-label">Clients with Risks</div>
            <div className="consultant-stat-tile-value stat-risk">{stats.clientsAtRisk}</div>
          </div>
        )}
        {stats.awaitingAdmin > 0 && (
          <div className="consultant-stat-tile">
            <div className="consultant-stat-tile-label">Awaiting Admin</div>
            <div className="consultant-stat-tile-value stat-awaiting">{stats.awaitingAdmin}</div>
          </div>
        )}
      </div>

      {/* Main content: grid or comparison */}
      {viewMode === 'grid' ? (
        <div className="consultant-client-grid">
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
          No clients match your search for "{searchQuery}"
          {' — '}
          <button className="btn-text" onClick={() => setSearchQuery('')}>
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
