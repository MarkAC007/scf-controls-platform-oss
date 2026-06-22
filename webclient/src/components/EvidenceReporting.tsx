import { useMemo, useState } from 'react'
import type { EnrichedControl, ScopedControlsFile, EvidenceId } from '../types'
import { getScopedControl, getEvidenceTracking } from '../data/scopingService'

interface EvidenceReportingProps {
  controls: EnrichedControl[]
  scopingData: ScopedControlsFile
  onNavigateToEvidence?: (evidenceId: string) => void
}

interface EvidenceItem {
  id: EvidenceId
  title: string
  domain: string
  controlIds: string[]
  isTracked: boolean
  owner?: string
  frequency?: string
  collectingSystem?: string
  methodOfCollection?: string
}

export default function EvidenceReporting({ controls, scopingData, onNavigateToEvidence }: EvidenceReportingProps) {
  const [groupBy, setGroupBy] = useState<'team' | 'frequency'>('team')
  const [showOnlyTracked, setShowOnlyTracked] = useState(false)

  // Get all selected controls
  const selectedControls = useMemo(() => {
    return controls.filter(c => {
      const scoped = getScopedControl(scopingData, c.scf_id)
      return scoped?.selected
    })
  }, [controls, scopingData])

  // Build unique evidence list with metadata
  const evidenceItems = useMemo(() => {
    const evidenceMap = new Map<EvidenceId, EvidenceItem>()

    selectedControls.forEach(control => {
      control.artifactsResolved.forEach(artifact => {
        if (!evidenceMap.has(artifact.id)) {
          const tracking = getEvidenceTracking(scopingData, artifact.id)
          evidenceMap.set(artifact.id, {
            id: artifact.id,
            title: artifact.title,
            domain: artifact.domain,
            controlIds: [control.scf_id],
            isTracked: tracking?.is_tracked || false,
            owner: tracking?.owner,
            frequency: tracking?.frequency,
            collectingSystem: tracking?.collecting_system,
            methodOfCollection: tracking?.method_of_collection
          })
        } else {
          const existing = evidenceMap.get(artifact.id)!
          if (!existing.controlIds.includes(control.scf_id)) {
            existing.controlIds.push(control.scf_id)
          }
        }
      })
    })

    return Array.from(evidenceMap.values())
  }, [selectedControls, scopingData])

  // Filter evidence
  const filteredEvidence = useMemo(() => {
    if (showOnlyTracked) {
      return evidenceItems.filter(e => e.isTracked)
    }
    return evidenceItems
  }, [evidenceItems, showOnlyTracked])

  // Group evidence by team
  const evidenceByTeam = useMemo(() => {
    const groups: Record<string, EvidenceItem[]> = {}

    filteredEvidence.forEach(evidence => {
      const team = evidence.owner || 'Unassigned'
      if (!groups[team]) {
        groups[team] = []
      }
      groups[team].push(evidence)
    })

    return groups
  }, [filteredEvidence])

  // Group evidence by frequency
  const evidenceByFrequency = useMemo(() => {
    const groups: Record<string, EvidenceItem[]> = {}

    filteredEvidence.forEach(evidence => {
      const frequency = evidence.frequency || 'Not Specified'
      if (!groups[frequency]) {
        groups[frequency] = []
      }
      groups[frequency].push(evidence)
    })

    return groups
  }, [filteredEvidence])

  // Calculate summary stats
  const stats = useMemo(() => {
    const totalEvidence = evidenceItems.length
    const trackedEvidence = evidenceItems.filter(e => e.isTracked).length
    const byTeam: Record<string, { total: number, tracked: number }> = {}

    evidenceItems.forEach(evidence => {
      const team = evidence.owner || 'Unassigned'
      if (!byTeam[team]) {
        byTeam[team] = { total: 0, tracked: 0 }
      }
      byTeam[team].total++
      if (evidence.isTracked) {
        byTeam[team].tracked++
      }
    })

    return {
      totalEvidence,
      trackedEvidence,
      notTracked: totalEvidence - trackedEvidence,
      byTeam
    }
  }, [evidenceItems])

  const currentGroups = groupBy === 'team' ? evidenceByTeam : evidenceByFrequency

  return (
    <div className="evidence-reporting">
      <div className="reporting-header">
        <div>
          <h1>Evidence Reporting</h1>
          <p className="reporting-subtitle">Team responsibilities and evidence collection overview</p>
        </div>

        <div className="reporting-stats">
          <div className="stat">
            <span className="stat-value">{stats.totalEvidence}</span>
            <span className="stat-label">Total Evidence</span>
          </div>
          <div className="stat stat-implemented">
            <span className="stat-value">{stats.trackedEvidence}</span>
            <span className="stat-label">Tracked</span>
          </div>
          <div className="stat stat-gap">
            <span className="stat-value">{stats.notTracked}</span>
            <span className="stat-label">Not Tracked</span>
          </div>
        </div>
      </div>

      <div className="reporting-controls">
        <div className="filter-group">
          <label>Group By:</label>
          <select
            value={groupBy}
            onChange={e => setGroupBy(e.target.value as 'team' | 'frequency')}
            className="form-control"
          >
            <option value="team">Owner Team</option>
            <option value="frequency">Collection Frequency</option>
          </select>
        </div>

        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={showOnlyTracked}
            onChange={e => setShowOnlyTracked(e.target.checked)}
          />
          <span>Show only tracked evidence</span>
        </label>
      </div>

      <div className="reporting-content">
        {Object.entries(currentGroups)
          .sort(([,a], [,b]) => b.length - a.length)
          .map(([groupName, items]) => {
            const tracked = items.filter(i => i.isTracked).length
            const percentage = items.length > 0 ? Math.round((tracked / items.length) * 100) : 0

            return (
              <div key={groupName} className="evidence-group">
                <div className="group-header">
                  <div className="group-title">
                    <h3>{groupName}</h3>
                    <span className="group-count">
                      {tracked}/{items.length} tracked ({percentage}%)
                    </span>
                  </div>
                  <div className="progress-bar progress-bar-small">
                    <div
                      className="progress-fill progress-fill-info"
                      style={{ width: `${percentage}%` }}
                    ></div>
                  </div>
                </div>

                <div className="evidence-table-wrapper">
                  <table className="evidence-table">
                    <thead>
                      <tr>
                        <th>Evidence ID</th>
                        <th>Title</th>
                        <th>Domain</th>
                        <th>Controls</th>
                        {groupBy === 'team' && <th>Frequency</th>}
                        {groupBy === 'frequency' && <th>Owner Team</th>}
                        <th>System</th>
                        <th>Method</th>
                        <th>Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {items
                        .sort((a, b) => a.id.localeCompare(b.id))
                        .map(evidence => (
                          <tr
                            key={evidence.id}
                            className={`${evidence.isTracked ? 'row-tracked' : 'row-not-tracked'}${onNavigateToEvidence ? ' cursor-pointer' : ''}`}
                            onClick={() => onNavigateToEvidence?.(evidence.id)}
                          >
                            <td className="cell-id">{evidence.id}</td>
                            <td className="cell-title">{evidence.title}</td>
                            <td className="cell-domain">{evidence.domain}</td>
                            <td className="cell-controls">
                              {evidence.controlIds.map(id => {
                                const control = controls.find(c => c.scf_id === id)
                                if (!control) return <span key={id} className="control-badge">{id}</span>

                                const tooltipId = `tooltip-report-${evidence.id}-${id}`
                                const ctrlScopedData = getScopedControl(scopingData, id)
                                const implStatus = ctrlScopedData?.implementation_status || 'not_started'

                                // Status display helpers (same as Evidence Scoping page)
                                const statusConfig = {
                                  implemented: { label: 'IMPLEMENTED', icon: '✅', class: 'status-implemented' },
                                  in_progress: { label: 'IN PROGRESS', icon: '🔄', class: 'status-in-progress' },
                                  not_started: { label: 'NOT STARTED', icon: '⭕', class: 'status-not-started' },
                                  at_risk: { label: 'AT RISK', icon: '⚠️', class: 'status-at-risk' },
                                  not_applicable: { label: 'NOT APPLICABLE', icon: '❌', class: 'status-not-applicable' },
                                  deferred: { label: 'DEFERRED', icon: '⏸️', class: 'status-deferred' }
                                }

                                const status = statusConfig[implStatus as keyof typeof statusConfig] || statusConfig.not_started
                                const badgeStatusClass = implStatus === 'not_applicable' ? 'badge-not-applicable' :
                                                        implStatus === 'deferred' ? 'badge-deferred' :
                                                        implStatus === 'at_risk' ? 'badge-at-risk' : ''

                                return (
                                  <div key={id} className="control-pill-wrapper">
                                    <span
                                      className={`control-badge ${badgeStatusClass}`}
                                      onMouseEnter={(e) => {
                                        const tooltip = document.getElementById(tooltipId)
                                        if (tooltip) {
                                          const rect = e.currentTarget.getBoundingClientRect()
                                          tooltip.style.top = `${rect.top - tooltip.offsetHeight - 8}px`
                                          tooltip.style.left = `${Math.max(10, rect.left + rect.width / 2 - 200)}px`
                                        }
                                      }}
                                    >
                                      {id}
                                    </span>
                                    <div id={tooltipId} className="control-tooltip">
                                      <div className="tooltip-header">
                                        <strong>{control.scf_id}</strong> — {control.control_name}
                                      </div>
                                      <div className="tooltip-domain">{control.scf_domain}</div>

                                      {ctrlScopedData && status && (
                                        <div className={`tooltip-status-box ${status.class}`}>
                                          <div className="status-row">
                                            <span className="status-label">Status:</span>
                                            <span className="status-value">
                                              {status.icon} {status.label}
                                            </span>
                                          </div>
                                          {ctrlScopedData.owner && (
                                            <div className="status-row">
                                              <span className="status-label">Owner:</span>
                                              <span className="status-value">{ctrlScopedData.owner}</span>
                                            </div>
                                          )}
                                          {ctrlScopedData.completion_date && (
                                            <div className="status-row">
                                              <span className="status-label">Target Date:</span>
                                              <span className="status-value">{ctrlScopedData.completion_date}</span>
                                            </div>
                                          )}
                                          {ctrlScopedData.maturity_level && (
                                            <div className="status-row">
                                              <span className="status-label">Maturity:</span>
                                              <span className="status-value">
                                                {ctrlScopedData.maturity_level.charAt(0).toUpperCase() + ctrlScopedData.maturity_level.slice(1)}
                                              </span>
                                            </div>
                                          )}
                                        </div>
                                      )}

                                      {ctrlScopedData?.selection_reason && (
                                        <div className="tooltip-section">
                                          <strong>Selection Reason:</strong>
                                          <p>{ctrlScopedData.selection_reason}</p>
                                        </div>
                                      )}

                                      <div className="tooltip-section">
                                        <strong>Description:</strong>
                                        <p>{control.control_description}</p>
                                      </div>
                                      <div className="tooltip-section">
                                        <strong>Testing Procedure:</strong>
                                        <p>{control.testing_procedure || 'No testing procedure defined'}</p>
                                      </div>
                                    </div>
                                  </div>
                                )
                              })}
                            </td>
                            {groupBy === 'team' && (
                              <td className="cell-frequency">{evidence.frequency || '-'}</td>
                            )}
                            {groupBy === 'frequency' && (
                              <td className="cell-owner">{evidence.owner || 'Unassigned'}</td>
                            )}
                            <td className="cell-system">{evidence.collectingSystem || '-'}</td>
                            <td className="cell-method">{evidence.methodOfCollection || '-'}</td>
                            <td className="cell-status">
                              {evidence.isTracked ? (
                                <span className="status-badge-tracked">✓ Tracked</span>
                              ) : (
                                <span className="status-badge-not-tracked">Not Tracked</span>
                              )}
                            </td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )
          })}
      </div>

      {Object.keys(currentGroups).length === 0 && (
        <div className="empty-state">
          <p>No evidence items found. Please select controls and configure evidence tracking in the Evidence Scoping tab.</p>
        </div>
      )}
    </div>
  )
}
