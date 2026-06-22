import { useState, useCallback } from 'react'
import type { ScopedControlsFile } from '../types'
import { createOrUpdateScopedControl } from '../data/apiClient'

interface FrameworkGapDetailProps {
  frameworkName: string
  frameworkKey: string
  gapControlIds: string[]
  gapsByDomain: Record<string, { controlIds: string[], controlNames: string[] }>
  totalControls: number
  selectedControls: number
  onScopingDataChange: (data: ScopedControlsFile) => void
  scopingData: ScopedControlsFile
  onNavigateToScoping?: (framework: string) => void
}

export default function FrameworkGapDetail({
  frameworkName,
  frameworkKey,
  gapControlIds,
  gapsByDomain,
  totalControls,
  selectedControls,
  onScopingDataChange,
  scopingData,
  onNavigateToScoping
}: FrameworkGapDetailProps) {
  const [expandedDomains, setExpandedDomains] = useState<Set<string>>(new Set())
  const [addingControls, setAddingControls] = useState<Set<string>>(new Set())
  const [addingDomains, setAddingDomains] = useState<Set<string>>(new Set())

  const gapCount = gapControlIds.length
  const gapPercentage = totalControls > 0 ? Math.round((gapCount / totalControls) * 100) : 0

  // Sort domains by gap count (descending)
  const sortedDomains = Object.entries(gapsByDomain)
    .sort(([, a], [, b]) => b.controlIds.length - a.controlIds.length)

  const toggleDomain = useCallback((domain: string) => {
    setExpandedDomains(prev => {
      const next = new Set(prev)
      if (next.has(domain)) {
        next.delete(domain)
      } else {
        next.add(domain)
      }
      return next
    })
  }, [])

  const handleAddControl = useCallback(async (controlId: string) => {
    if (addingControls.has(controlId)) return

    setAddingControls(prev => new Set(prev).add(controlId))
    try {
      await createOrUpdateScopedControl({
        scf_id: controlId,
        selected: true,
        selection_reason: `Added via ${frameworkName} gap analysis`,
        implementation_status: 'not_started'
      })

      // Update local state
      const updatedScopingData = { ...scopingData }
      const existingIndex = updatedScopingData.scoped_controls.findIndex(
        sc => sc.scf_id === controlId
      )

      if (existingIndex >= 0) {
        updatedScopingData.scoped_controls[existingIndex] = {
          ...updatedScopingData.scoped_controls[existingIndex],
          selected: true,
          selection_reason: `Added via ${frameworkName} gap analysis`
        }
      } else {
        updatedScopingData.scoped_controls.push({
          scf_id: controlId,
          selected: true,
          selection_reason: `Added via ${frameworkName} gap analysis`,
          implementation_status: 'not_started'
        })
      }

      // Update metadata
      updatedScopingData.metadata = {
        ...updatedScopingData.metadata,
        total_selected: updatedScopingData.scoped_controls.filter(sc => sc.selected).length,
        last_updated: new Date().toISOString()
      }

      onScopingDataChange(updatedScopingData)
    } catch (error) {
      console.error('Failed to add control to scope:', error)
    } finally {
      setAddingControls(prev => {
        const next = new Set(prev)
        next.delete(controlId)
        return next
      })
    }
  }, [addingControls, frameworkName, scopingData, onScopingDataChange])

  const handleAddDomain = useCallback(async (domain: string) => {
    if (addingDomains.has(domain)) return

    const domainGaps = gapsByDomain[domain]
    if (!domainGaps || domainGaps.controlIds.length === 0) return

    setAddingDomains(prev => new Set(prev).add(domain))
    try {
      // Add all controls from this domain
      const promises = domainGaps.controlIds.map(controlId =>
        createOrUpdateScopedControl({
          scf_id: controlId,
          selected: true,
          selection_reason: `Bulk added via ${frameworkName} gap analysis (${domain})`,
          implementation_status: 'not_started'
        })
      )

      await Promise.all(promises)

      // Update local state
      const updatedScopingData = { ...scopingData }
      domainGaps.controlIds.forEach(controlId => {
        const existingIndex = updatedScopingData.scoped_controls.findIndex(
          sc => sc.scf_id === controlId
        )

        if (existingIndex >= 0) {
          updatedScopingData.scoped_controls[existingIndex] = {
            ...updatedScopingData.scoped_controls[existingIndex],
            selected: true,
            selection_reason: `Bulk added via ${frameworkName} gap analysis (${domain})`
          }
        } else {
          updatedScopingData.scoped_controls.push({
            scf_id: controlId,
            selected: true,
            selection_reason: `Bulk added via ${frameworkName} gap analysis (${domain})`,
            implementation_status: 'not_started'
          })
        }
      })

      // Update metadata
      updatedScopingData.metadata = {
        ...updatedScopingData.metadata,
        total_selected: updatedScopingData.scoped_controls.filter(sc => sc.selected).length,
        last_updated: new Date().toISOString()
      }

      onScopingDataChange(updatedScopingData)
    } catch (error) {
      console.error('Failed to add domain controls to scope:', error)
    } finally {
      setAddingDomains(prev => {
        const next = new Set(prev)
        next.delete(domain)
        return next
      })
    }
  }, [addingDomains, frameworkName, gapsByDomain, scopingData, onScopingDataChange])

  if (gapCount === 0) {
    return (
      <div className="framework-gap-detail framework-gap-complete">
        <div className="gap-complete-badge">
          <span className="gap-complete-icon">&#10003;</span>
          <span className="gap-complete-text">Full Coverage</span>
        </div>
        <p className="gap-complete-message">
          All {totalControls} controls for {frameworkName} are in scope.
        </p>
      </div>
    )
  }

  return (
    <div className="framework-gap-detail">
      <div className="gap-summary-header">
        <div className="gap-summary-stats">
          <div className="gap-stat-item gap-stat-danger">
            <span className="gap-stat-value">{gapCount}</span>
            <span className="gap-stat-label">Not in Scope</span>
          </div>
          <div className="gap-stat-item">
            <span className="gap-stat-value">{gapPercentage}%</span>
            <span className="gap-stat-label">Gap Rate</span>
          </div>
          <div className="gap-stat-item">
            <span className="gap-stat-value">{sortedDomains.length}</span>
            <span className="gap-stat-label">Domains Affected</span>
          </div>
        </div>
        {onNavigateToScoping && (
          <button
            className="gap-action-btn gap-action-secondary"
            onClick={() => onNavigateToScoping(frameworkName)}
            title={`View all ${frameworkName} controls in Control Scoping`}
          >
            View in Scoping
          </button>
        )}
      </div>

      <div className="gap-domains-list">
        {sortedDomains.map(([domain, domainData]) => {
          const isExpanded = expandedDomains.has(domain)
          const isDomainAdding = addingDomains.has(domain)

          return (
            <div key={domain} className="gap-domain-item">
              <div
                className="gap-domain-header"
                onClick={() => toggleDomain(domain)}
              >
                <span className={`gap-domain-chevron ${isExpanded ? 'expanded' : ''}`}>
                  &#9656;
                </span>
                <span className="gap-domain-name">{domain}</span>
                <span className="gap-domain-count">{domainData.controlIds.length}</span>
                <button
                  className={`gap-add-btn gap-add-domain ${isDomainAdding ? 'loading' : ''}`}
                  onClick={(e) => {
                    e.stopPropagation()
                    handleAddDomain(domain)
                  }}
                  disabled={isDomainAdding}
                  title={`Add all ${domainData.controlIds.length} controls from ${domain}`}
                >
                  {isDomainAdding ? 'Adding...' : 'Add All'}
                </button>
              </div>

              {isExpanded && (
                <div className="gap-controls-list">
                  {domainData.controlIds.map((controlId, idx) => {
                    const isAdding = addingControls.has(controlId)
                    return (
                      <div key={controlId} className="gap-control-item">
                        <div className="gap-control-info">
                          <span className="gap-control-id">{controlId}</span>
                          <span className="gap-control-name">{domainData.controlNames[idx]}</span>
                        </div>
                        <button
                          className={`gap-add-btn gap-add-single ${isAdding ? 'loading' : ''}`}
                          onClick={() => handleAddControl(controlId)}
                          disabled={isAdding}
                          title={`Add ${controlId} to scope`}
                        >
                          {isAdding ? '...' : '+'}
                        </button>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
