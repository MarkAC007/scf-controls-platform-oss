import { forwardRef, useCallback, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { EnrichedControl, ScopedControlsFile, ImplementationStatus } from '../types'
import AppLink from './AppLink'
import { FilterSelect } from './explorer/FilterSidebar'
import { useCatalogFilters } from '../hooks/useCatalogFilters'

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

interface MatrixTooltipHandle {
  show: (data: TooltipData) => void
  hide: () => void
}

/**
 * The hover tooltip owns its own state. When it lived in MappingMatrix, every
 * hover re-rendered the whole matrix just to move one small box.
 */
const MatrixTooltip = forwardRef<MatrixTooltipHandle>(function MatrixTooltip(_props, ref) {
  const [tooltip, setTooltip] = useState<TooltipData | null>(null)
  useImperativeHandle(ref, () => ({ show: setTooltip, hide: () => setTooltip(null) }), [])
  if (!tooltip) return null
  return (
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
  )
})

// Virtualisation. The full catalog is ~2,400 controls × ~340 frameworks
// (~810k cells); rendering all of it froze the page for ~6 s. Only the rows and
// columns inside the scroll viewport (plus an overscan margin) are rendered;
// spacer cells stand in for the rest so the scroll extent and the sticky
// header / first column behave exactly as with the full table. Row height and
// column width are measured from the rendered DOM, so theme CSS stays in charge.
const ROW_OVERSCAN = 8
const COL_OVERSCAN = 4
// Rendered before the viewport has been measured (first paint, and jsdom,
// which has no layout).
const INITIAL_ROWS = 60
const INITIAL_COLS = 60
// The 45° header bands overhang to the right of their column by the header
// height; the last column needs that much room after it.
const LABEL_OVERHANG = 165

interface Metrics {
  rowHeight: number
  colWidth: number
  headerHeight: number
  stickyWidth: number
}

interface Viewport {
  top: number
  left: number
  width: number
  height: number
}

const controlDomain = (control: EnrichedControl) => control.scf_id.split('-')[0]

export default function MappingMatrix({ controls, scopingData }: MappingMatrixProps) {
  const tooltipRef = useRef<MatrixTooltipHandle>(null)
  // null = the user hasn't touched the toggle yet
  const [hideUnscopedChoice, setHideUnscoped] = useState<boolean | null>(null)
  const [showLegend, setShowLegend] = useState(false)
  const [domainFilter, setDomainFilter] = useState('all')
  const { domains: catalogDomainOptions } = useCatalogFilters()

  // Check if we have active scoping data
  const hasActiveScopingData = !!(scopingData &&
    scopingData.scoped_controls &&
    scopingData.scoped_controls.length > 0)

  // An org that has scoped controls opens on its own controls (and only the
  // frameworks they map to); the full catalog is one click away.
  const hideUnscoped = hideUnscopedChoice ?? hasActiveScopingData

  // First scoping entry per scf_id — Map lookups instead of a linear find per row
  const scopedById = useMemo(() => {
    const byId = new Map<string, ScopedControlsFile['scoped_controls'][number]>()
    for (const sc of scopingData?.scoped_controls ?? []) {
      if (!byId.has(sc.scf_id)) byId.set(sc.scf_id, sc)
    }
    return byId
  }, [scopingData])

  // Controls in scope open in the in-scope library; everything else opens in
  // the full library, so following a link never implies (or changes) scope.
  const selectedIds = useMemo(
    () => new Set((scopingData?.scoped_controls ?? []).filter(sc => sc.selected).map(sc => sc.scf_id)),
    [scopingData],
  )

  // Filter controls based on scoping status
  const scopeFilteredControls = useMemo(() => {
    if (!hideUnscoped || !hasActiveScopingData) {
      return controls
    }

    // Only show controls whose first scoping entry is selected
    return controls.filter(control => scopedById.get(control.scf_id)?.selected === true)
  }, [controls, hideUnscoped, hasActiveScopingData, scopedById])

  // Controls per domain within the current scope. SCF control IDs are
  // prefixed with their domain identifier (AST-02 → AST).
  const domainCounts = useMemo(() => {
    const counts = new Map<string, number>()
    for (const control of scopeFilteredControls) {
      const domain = controlDomain(control)
      counts.set(domain, (counts.get(domain) ?? 0) + 1)
    }
    return counts
  }, [scopeFilteredControls])

  // Same options as the Control Library (catalog domains, `ABBR - Name`, in
  // catalog order), cut to the domains present, with counts. The selected
  // domain stays listed even when the scope toggle empties it.
  const domainOptions = useMemo(() => {
    const catalogIds = new Set(catalogDomainOptions.map(o => o.value))
    const listed = (value: string) => domainCounts.has(value) || value === domainFilter
    return [
      { value: 'all', label: `All Domains (${scopeFilteredControls.length})` },
      ...catalogDomainOptions
        .filter(o => listed(o.value))
        .map(o => ({ value: o.value, label: `${o.label} (${domainCounts.get(o.value) ?? 0})` })),
      ...Array.from(domainCounts.keys())
        .filter(key => !catalogIds.has(key))
        .sort()
        .map(key => ({ value: key, label: `${key} (${domainCounts.get(key)})` })),
    ]
  }, [catalogDomainOptions, domainCounts, domainFilter, scopeFilteredControls.length])

  const filteredControls = useMemo(
    () => domainFilter === 'all'
      ? scopeFilteredControls
      : scopeFilteredControls.filter(control => controlDomain(control) === domainFilter),
    [scopeFilteredControls, domainFilter],
  )

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
    return scopedById.get(scfId)?.implementation_status
  }

  // Get CSS class for status
  const getStatusClass = (status?: ImplementationStatus): string => {
    if (!status) return ''
    return `matrix-row-${status}`
  }

  // Handle tooltip display
  const handleMouseEnter = useCallback((
    e: React.MouseEvent<HTMLSpanElement>,
    control: EnrichedControl,
    framework: string
  ) => {
    const refs = control.frameworksResolved[framework] || []
    if (refs.length === 0) return

    const rect = e.currentTarget.getBoundingClientRect()
    tooltipRef.current?.show({
      scfId: control.scf_id,
      controlName: control.control_name,
      framework,
      refs,
      x: rect.left + rect.width / 2,
      y: rect.top - 10
    })
  }, [])

  const handleMouseLeave = useCallback(() => {
    tooltipRef.current?.hide()
  }, [])

  // ── Virtualisation ──────────────────────────────────────────────────────────
  const scrollRef = useRef<HTMLDivElement>(null)
  const tableRef = useRef<HTMLTableElement>(null)
  const [viewport, setViewport] = useState<Viewport>({ top: 0, left: 0, width: 0, height: 0 })
  const [metrics, setMetrics] = useState<Metrics | null>(null)

  const readViewport = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    setViewport(prev => (
      prev.top === el.scrollTop && prev.left === el.scrollLeft &&
      prev.width === el.clientWidth && prev.height === el.clientHeight
        ? prev
        : { top: el.scrollTop, left: el.scrollLeft, width: el.clientWidth, height: el.clientHeight }
    ))
  }, [])

  useLayoutEffect(() => {
    readViewport()
    const el = scrollRef.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => readViewport())
    observer.observe(el)
    return () => observer.disconnect()
  }, [readViewport])

  // Measure one real row / cell after each render; only stores a change.
  useLayoutEffect(() => {
    const table = tableRef.current
    if (!table) return
    const row = table.querySelector<HTMLElement>('tbody tr.matrix-body-row')
    const cell = table.querySelector<HTMLElement>('tbody td.mapping-cell')
    const head = table.querySelector<HTMLElement>('thead')
    const sticky = table.querySelector<HTMLElement>('th.control-header')
    if (!row || !cell || !head || !sticky) return
    // Fractional sizes: rows render at e.g. 78.39px, and a rounded offsetHeight
    // would make the spacers drift against the real rows.
    const next: Metrics = {
      rowHeight: row.getBoundingClientRect().height,
      colWidth: cell.getBoundingClientRect().width,
      headerHeight: head.getBoundingClientRect().height,
      stickyWidth: sticky.getBoundingClientRect().width,
    }
    if (next.rowHeight === 0 || next.colWidth === 0) return
    setMetrics(prev => (
      prev && prev.rowHeight === next.rowHeight && prev.colWidth === next.colWidth &&
      prev.headerHeight === next.headerHeight && prev.stickyWidth === next.stickyWidth
        ? prev
        : next
    ))
  })

  let rowStart = 0
  let rowEnd = Math.min(filteredControls.length, INITIAL_ROWS)
  let colStart = 0
  let colEnd = Math.min(frameworks.length, INITIAL_COLS)
  if (metrics && viewport.height > 0 && viewport.width > 0) {
    const bodyTop = viewport.top - metrics.headerHeight
    rowStart = Math.max(0, Math.floor(bodyTop / metrics.rowHeight) - ROW_OVERSCAN)
    rowEnd = Math.min(filteredControls.length, Math.ceil((bodyTop + viewport.height) / metrics.rowHeight) + ROW_OVERSCAN)
    colStart = Math.max(0, Math.floor(viewport.left / metrics.colWidth) - COL_OVERSCAN)
    colEnd = Math.min(
      frameworks.length,
      Math.ceil((viewport.left + viewport.width - metrics.stickyWidth) / metrics.colWidth) + COL_OVERSCAN,
    )
  }
  const rowHeight = metrics?.rowHeight ?? 0
  const colWidth = metrics?.colWidth ?? 0
  const visibleControls = filteredControls.slice(rowStart, rowEnd)
  const visibleFrameworks = frameworks.slice(colStart, colEnd)
  const leftPad = colStart * colWidth
  const rightPad = (frameworks.length - colEnd) * colWidth + LABEL_OVERHANG
  const topPad = rowStart * rowHeight
  const bottomPad = (filteredControls.length - rowEnd) * rowHeight
  // Column count of every row: control + [left spacer] + visible + [right spacer]
  const rowColSpan = 1 + visibleFrameworks.length + (leftPad > 0 ? 1 : 0) + (rightPad > 0 ? 1 : 0)
  const spacerStyle = (px: number) => ({ width: px, minWidth: px, maxWidth: px })

  return (
    <div className="mapping-matrix-container">
      {/* Matrix toolbar — toolbar-idiom classes (no ListToolbar component: matrix has no search) */}
      <div className="matrix-toolbar">
        <div className="matrix-toolbar-actions">
          <div className="matrix-domain-filter">
            <FilterSelect value={domainFilter} onChange={setDomainFilter} options={domainOptions} />
          </div>
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
              {controls.length !== filteredControls.length && (
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

      <div className="matrix-scroll-wrapper" ref={scrollRef} onScroll={readViewport}>
        <table className="mapping-matrix" ref={tableRef}>
          <thead>
            <tr>
              <th className="control-header sticky-col">
                <div className="header-content">
                  <div>SCF CONTROL</div>
                </div>
              </th>
              {leftPad > 0 && <th className="matrix-spacer" aria-hidden="true" style={spacerStyle(leftPad)} />}
              {visibleFrameworks.map(fw => (
                <th key={fw} className="framework-header" title={fw.replace(/_ref$/, '')}>
                  <div className="framework-label">
                    <span>{fw.replace(/_ref$/, '')}</span>
                  </div>
                </th>
              ))}
              {rightPad > 0 && <th className="matrix-spacer" aria-hidden="true" style={spacerStyle(rightPad)} />}
            </tr>
          </thead>
          <tbody>
            {topPad > 0 && (
              <tr className="matrix-spacer-row" aria-hidden="true">
                <td className="matrix-spacer" colSpan={rowColSpan} style={{ height: topPad }} />
              </tr>
            )}
            {visibleControls.map(control => {
              const status = getImplementationStatus(control.scf_id)
              return (
                <tr key={control.scf_id} className={`matrix-body-row ${getStatusClass(status)}`.trim()}>
                  <td className="control-cell sticky-col">
                    <AppLink
                      className="control-info control-info-link"
                      to={{
                        kind: 'control',
                        id: control.scf_id,
                        mode: selectedIds.has(control.scf_id) ? 'in-scope' : 'full-library',
                      }}
                    >
                      <span className="control-id">{control.scf_id}</span>
                      <span className="control-name">{control.control_name}</span>
                    </AppLink>
                  </td>
                {leftPad > 0 && <td className="matrix-spacer" aria-hidden="true" style={spacerStyle(leftPad)} />}
                {visibleFrameworks.map(fw => (
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
                {rightPad > 0 && <td className="matrix-spacer" aria-hidden="true" style={spacerStyle(rightPad)} />}
                </tr>
              )
            })}
            {bottomPad > 0 && (
              <tr className="matrix-spacer-row" aria-hidden="true">
                <td className="matrix-spacer" colSpan={rowColSpan} style={{ height: bottomPad }} />
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Tooltip — dark surface per artboard */}
      <MatrixTooltip ref={tooltipRef} />
    </div>
  )
}
