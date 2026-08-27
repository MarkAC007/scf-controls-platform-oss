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
      {/* Matrix toolbar — toolbar-idiom classes (no ListToolbar component: matrix has no search) */}
      <div className="matrix-toolbar">
        <div className="matrix-toolbar-title">SCF Framework Mapping Matrix</div>
        <div className="matrix-toolbar-actions">
          {hasActiveScopingData && (
            <>
              <button
                className="matrix-legend-btn"
                onClick={() => setShowLegend(!showLegend)}
                title="Toggle status legend"
              >
                {showLegend ? '✕' : '?'} Legend
              </button>
              <label className="matrix-scoped-toggle">
                <span
                  className={`matrix-scoped-checkbox${hideUnscoped ? ' is-checked' : ''}`}
                  aria-hidden="true"
                >
                  {hideUnscoped && (
                    <svg width="9" height="9" viewBox="0 0 10 10" fill="none">
                      <path d="M1.5 5.5l2.5 2.5 4.5-5" stroke="#fff" strokeWidth="1.6" strokeLinecap="round" />
                    </svg>
                  )}
                </span>
                <input
                  type="checkbox"
                  checked={hideUnscoped}
                  onChange={(e) => setHideUnscoped(e.target.checked)}
                />
                <span>Show scoped only</span>
              </label>
            </>
          )}
          <div className="matrix-toolbar-count">
            <span className="matrix-count-filtered">
              {filteredControls.length}
              {hideUnscoped && controls.length !== filteredControls.length && (
                <span className="matrix-count-total"> / {controls.length}</span>
              )}
              {' '}controls
            </span>
            <span className="matrix-count-sep"> · </span>
            <span className="matrix-count-fw">{frameworks.length} frameworks</span>
          </div>
        </div>
      </div>

      {/* Status Legend — inline strip (per Mappings.html artboard) */}
      {showLegend && hasActiveScopingData && (
        <div className="matrix-legend-strip">
          <span className="matrix-legend-label">STATUS LEGEND</span>
          <div className="matrix-legend-item">
            <div className="matrix-legend-swatch mlg-implemented"></div>
            <span>Implemented</span>
          </div>
          <div className="matrix-legend-item">
            <div className="matrix-legend-swatch mlg-in-progress"></div>
            <span>In Progress</span>
          </div>
          <div className="matrix-legend-item">
            <div className="matrix-legend-swatch mlg-not-started"></div>
            <span>Not Started</span>
          </div>
          <div className="matrix-legend-item">
            <div className="matrix-legend-swatch mlg-at-risk"></div>
            <span>At Risk</span>
          </div>
          <div className="matrix-legend-item">
            <div className="matrix-legend-swatch mlg-not-applicable"></div>
            <span>Not Applicable</span>
          </div>
          <div className="matrix-legend-item">
            <div className="matrix-legend-swatch mlg-deferred"></div>
            <span>Deferred</span>
          </div>
          <span className="matrix-legend-note">Row colors indicate implementation status of scoped controls</span>
        </div>
      )}

      <div className="matrix-scroll-wrapper">
        <table className="mapping-matrix">
          <thead>
            <tr>
              <th className="control-header sticky-col">
                <div className="header-content">
                  <div>SCF CONTROL</div>
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
                      <span className="control-id">{control.scf_id}</span>
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

      {/* Tooltip — dark surface per artboard */}
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
