import { useMemo, useState } from 'react'
import type { EnrichedControl, ScopedControlsFile, ImplementationStatus } from '../types'

interface MappingMatrixProps {
  controls: EnrichedControl[]
  scopingData: ScopedControlsFile | null
}

interface TooltipData {
  scfId: string
  controlName: string
  framework: string
  refs: string[]
  x: number
  y: number
}

export default function MappingMatrix({ controls, scopingData }: MappingMatrixProps) {
  const [tooltip, setTooltip] = useState<TooltipData | null>(null)
  const [hideUnscoped, setHideUnscoped] = useState(false)
  const [showLegend, setShowLegend] = useState(false)

  // Check if we have active scoping data
  const hasActiveScopingData = scopingData &&
    scopingData.scoped_controls &&
    scopingData.scoped_controls.length > 0

  // Filter controls based on scoping status
  const filteredControls = useMemo(() => {
    if (!hideUnscoped || !hasActiveScopingData) {
      return controls
    }

    // Only show controls that are selected in scopingData
    return controls.filter(control => {
      const scopedControl = scopingData.scoped_controls.find(sc => sc.scf_id === control.scf_id)
      return scopedControl?.selected === true
    })
  }, [controls, hideUnscoped, hasActiveScopingData, scopingData])

  // Extract unique frameworks from filtered controls
  const frameworks = useMemo(() => {
    const frameworkSet = new Set<string>()
    filteredControls.forEach(control => {
      Object.keys(control.frameworksResolved).forEach(fw => {
        frameworkSet.add(fw)
      })
    })
    return Array.from(frameworkSet).sort()
  }, [filteredControls])

  // Check if a control maps to a framework
  const hasMapping = (control: EnrichedControl, framework: string): boolean => {
    return control.frameworksResolved[framework]?.length > 0
  }

  // Get implementation status for a control (only if we have active scoping data)
  const getImplementationStatus = (scfId: string): ImplementationStatus | undefined => {
    if (!scopingData || !scopingData.scoped_controls || scopingData.scoped_controls.length === 0) {
      return undefined
    }
    const scopedControl = scopingData.scoped_controls.find(sc => sc.scf_id === scfId)
    return scopedControl?.implementation_status
  }

  // Get CSS class for status
  const getStatusClass = (status?: ImplementationStatus): string => {
    if (!status) return ''
    return `matrix-row-${status}`
  }

  // Handle tooltip display
  const handleMouseEnter = (
    e: React.MouseEvent<HTMLSpanElement>,
    control: EnrichedControl,
    framework: string
  ) => {
    const refs = control.frameworksResolved[framework] || []
    if (refs.length === 0) return

    const rect = e.currentTarget.getBoundingClientRect()
    setTooltip({
      scfId: control.scf_id,
      controlName: control.control_name,
      framework,
      refs,
      x: rect.left + rect.width / 2,
      y: rect.top - 10
    })
  }

  const handleMouseLeave = () => {
    setTooltip(null)
  }

  return (
    <div className="mapping-matrix-container">
      <div className="mapping-matrix-header">
        <div className="matrix-header-left">
          <h1>SCF Framework Mapping Matrix</h1>
        </div>
        <div className="matrix-header-right">
          {hasActiveScopingData && (
            <>
              <button
                className="btn-legend"
                onClick={() => setShowLegend(!showLegend)}
                title="Toggle status legend"
              >
                {showLegend ? '✕' : '?'} Legend
              </button>
              <label className="matrix-filter-toggle">
                <input
                  type="checkbox"
                  checked={hideUnscoped}
                  onChange={(e) => setHideUnscoped(e.target.checked)}
                />
                <span>Show scoped only</span>
              </label>
            </>
          )}
          <div className="matrix-stats">
            <span>
              {filteredControls.length}
              {hideUnscoped && controls.length !== filteredControls.length && (
                <span className="stat-total"> / {controls.length}</span>
              )}
              {' '}Controls
            </span>
            <span>{frameworks.length} Frameworks</span>
          </div>
        </div>
      </div>

      {/* Status Legend */}
      {showLegend && hasActiveScopingData && (
        <div className="matrix-legend">
          <div className="legend-title">Implementation Status Legend</div>
          <div className="legend-items">
            <div className="legend-item legend-implemented">
              <div className="legend-color legend-implemented"></div>
              <span className="legend-label">Implemented</span>
            </div>
            <div className="legend-item legend-in-progress">
              <div className="legend-color legend-in-progress"></div>
              <span className="legend-label">In Progress</span>
            </div>
            <div className="legend-item legend-not-started">
              <div className="legend-color legend-not-started"></div>
              <span className="legend-label">Not Started</span>
            </div>
            <div className="legend-item legend-at-risk">
              <div className="legend-color legend-at-risk"></div>
              <span className="legend-label">At Risk</span>
            </div>
            <div className="legend-item legend-not-applicable">
              <div className="legend-color legend-not-applicable"></div>
              <span className="legend-label">Not Applicable</span>
            </div>
            <div className="legend-item legend-deferred">
              <div className="legend-color legend-deferred"></div>
              <span className="legend-label">Deferred</span>
            </div>
          </div>
          <div className="legend-note">
            Row colors indicate the implementation status of scoped controls
          </div>
        </div>
      )}

      <div className="matrix-scroll-wrapper">
        <table className="mapping-matrix">
          <thead>
            <tr>
              <th className="control-header sticky-col">
                <div className="header-content">
                  <div>SCF Control</div>
                </div>
              </th>
              {frameworks.map(fw => (
                <th key={fw} className="framework-header">
                  <div className="framework-label">
                    <span>{fw.replace(/_ref$/, '')}</span>
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filteredControls.map(control => {
              const status = getImplementationStatus(control.scf_id)
              return (
                <tr key={control.scf_id} className={getStatusClass(status)}>
                  <td className="control-cell sticky-col">
                    <div className="control-info">
                      <strong>{control.scf_id}</strong>
                      <span className="control-name">{control.control_name}</span>
                    </div>
                  </td>
                {frameworks.map(fw => (
                  <td key={fw} className="mapping-cell">
                    {hasMapping(control, fw) ? (
                      <span
                        className="mapping-mark"
                        onMouseEnter={(e) => handleMouseEnter(e, control, fw)}
                        onMouseLeave={handleMouseLeave}
                      >
                        X
                      </span>
                    ) : (
                      ''
                    )}
                  </td>
                ))}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* Tooltip */}
      {tooltip && (
        <div
          className="matrix-tooltip"
          style={{
            left: `${tooltip.x}px`,
            top: `${tooltip.y}px`,
          }}
        >
          <div className="tooltip-header">
            <strong>{tooltip.scfId}</strong> → {tooltip.framework}
          </div>
          <div className="tooltip-divider"></div>
          <div className="tooltip-refs">
            {tooltip.refs.map((ref, idx) => (
              <span key={idx} className="tooltip-ref-chip">
                {ref}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
