import { useState, useMemo } from 'react'
import FrameworkGapDetail from '../FrameworkGapDetail'

interface FrameworkTableProps {
  frameworkStats: Array<{
    frameworkKey: string
    frameworkName: string
    totalControls: number
    selectedControls: number
    implementedControls: number
    inProgressControls: number
    atRiskControls: number
    notStartedControls: number
    gapControlIds: string[]
    gapsByDomain: Record<string, { controlIds: string[], controlNames: string[] }>
  }>
  readinessMap: Map<string, {
    framework_name: string
    readiness_score: number
    readiness_grade: string
    implementation_score: number
    evidence_score: number
    tracked_evidence: number
    total_evidence: number
  }>
  loadingReadiness: boolean
  expandedFrameworkGaps: Set<string>
  onToggleFrameworkGap: (frameworkKey: string) => void
  onScopingDataChange: (data: any) => void
  scopingData: any
  onNavigateToScoping?: (framework?: string) => void
}

type SortColumn = 'framework' | 'readiness' | 'inScope' | 'implemented' | 'gaps' | 'evidence' | 'trend'
type SortDirection = 'asc' | 'desc'

function getReadinessScore(
  fwStat: FrameworkTableProps['frameworkStats'][number],
  readinessMap: FrameworkTableProps['readinessMap']
): number {
  const apiReadiness = readinessMap.get(fwStat.frameworkName)
  if (apiReadiness) {
    return apiReadiness.readiness_score
  }
  // Fallback: calculate from implementation only
  return fwStat.totalControls > 0
    ? Math.round((fwStat.implementedControls / fwStat.totalControls) * 100)
    : 0
}

function getEvidenceScore(
  fwStat: FrameworkTableProps['frameworkStats'][number],
  readinessMap: FrameworkTableProps['readinessMap']
): number {
  const apiReadiness = readinessMap.get(fwStat.frameworkName)
  return apiReadiness?.evidence_score ?? 0
}

function getReadinessClass(score: number): string {
  if (score >= 70) return 'ft-readiness-good'
  if (score >= 50) return 'ft-readiness-warning'
  return 'ft-readiness-danger'
}

export default function FrameworkTable({
  frameworkStats,
  readinessMap,
  loadingReadiness,
  expandedFrameworkGaps,
  onToggleFrameworkGap,
  onScopingDataChange,
  scopingData,
  onNavigateToScoping
}: FrameworkTableProps) {
  const [sortColumn, setSortColumn] = useState<SortColumn>('readiness')
  const [sortDirection, setSortDirection] = useState<SortDirection>('asc')

  const handleSort = (column: SortColumn) => {
    if (sortColumn === column) {
      setSortDirection(prev => prev === 'asc' ? 'desc' : 'asc')
    } else {
      setSortColumn(column)
      setSortDirection('asc')
    }
  }

  const sortedStats = useMemo(() => {
    const sorted = [...frameworkStats]
    const direction = sortDirection === 'asc' ? 1 : -1

    sorted.sort((a, b) => {
      let aVal: number | string
      let bVal: number | string

      switch (sortColumn) {
        case 'framework':
          aVal = a.frameworkName.toLowerCase()
          bVal = b.frameworkName.toLowerCase()
          return direction * (aVal < bVal ? -1 : aVal > bVal ? 1 : 0)
        case 'readiness':
          aVal = getReadinessScore(a, readinessMap)
          bVal = getReadinessScore(b, readinessMap)
          break
        case 'inScope':
          aVal = a.selectedControls
          bVal = b.selectedControls
          break
        case 'implemented':
          aVal = a.implementedControls
          bVal = b.implementedControls
          break
        case 'gaps':
          aVal = a.gapControlIds.length
          bVal = b.gapControlIds.length
          break
        case 'evidence':
          aVal = getEvidenceScore(a, readinessMap)
          bVal = getEvidenceScore(b, readinessMap)
          break
        case 'trend':
          // Placeholder: no real trend data yet, sort by name as fallback
          aVal = a.frameworkName.toLowerCase()
          bVal = b.frameworkName.toLowerCase()
          return direction * (aVal < bVal ? -1 : aVal > bVal ? 1 : 0)
        default:
          return 0
      }

      return direction * ((aVal as number) - (bVal as number))
    })

    return sorted
  }, [frameworkStats, readinessMap, sortColumn, sortDirection])

  const renderSortIndicator = (column: SortColumn) => {
    if (sortColumn !== column) return null
    return (
      <span className="ft-sort-indicator">
        {sortDirection === 'asc' ? '\u25B2' : '\u25BC'}
      </span>
    )
  }

  const headerClass = (column: SortColumn) => {
    const classes = ['ft-th', 'ft-th-sortable']
    if (sortColumn === column) {
      classes.push('ft-th-active')
    }
    return classes.join(' ')
  }

  return (
    <div className="framework-table">
      {loadingReadiness && (
        <div className="readiness-loading">Calculating readiness scores...</div>
      )}
      <table>
        <thead>
          <tr className="ft-header">
            <th className={headerClass('framework')} onClick={() => handleSort('framework')}>
              Framework {renderSortIndicator('framework')}
            </th>
            <th className={headerClass('readiness')} onClick={() => handleSort('readiness')}>
              Readiness % {renderSortIndicator('readiness')}
            </th>
            <th className={headerClass('inScope')} onClick={() => handleSort('inScope')}>
              In Scope {renderSortIndicator('inScope')}
            </th>
            <th className={headerClass('implemented')} onClick={() => handleSort('implemented')}>
              Implemented {renderSortIndicator('implemented')}
            </th>
            <th className={headerClass('gaps')} onClick={() => handleSort('gaps')}>
              Gaps {renderSortIndicator('gaps')}
            </th>
            <th className={headerClass('evidence')} onClick={() => handleSort('evidence')}>
              Evidence % {renderSortIndicator('evidence')}
            </th>
            <th className={headerClass('trend')} onClick={() => handleSort('trend')}>
              Trend {renderSortIndicator('trend')}
            </th>
          </tr>
        </thead>
        <tbody>
          {sortedStats.map(fwStat => {
            const readiness = getReadinessScore(fwStat, readinessMap)
            const evidence = getEvidenceScore(fwStat, readinessMap)
            const gapCount = fwStat.gapControlIds.length
            const isExpanded = expandedFrameworkGaps.has(fwStat.frameworkKey)

            return (
              <tr
                key={fwStat.frameworkKey}
                className={`ft-row ${isExpanded ? 'ft-row-expanded' : ''}`}
                onClick={() => onToggleFrameworkGap(fwStat.frameworkKey)}
              >
                <td className="ft-cell ft-cell-framework">{fwStat.frameworkName}</td>
                <td className={`ft-cell ${getReadinessClass(readiness)}`}>
                  {readiness.toFixed(0)}%
                </td>
                <td className="ft-cell">
                  {fwStat.selectedControls} / {fwStat.totalControls}
                </td>
                <td className="ft-cell">{fwStat.implementedControls}</td>
                <td className="ft-cell">{gapCount > 0 ? gapCount : '--'}</td>
                <td className="ft-cell">{evidence > 0 ? `${evidence.toFixed(0)}%` : '--'}</td>
                <td className="ft-cell">-</td>
              </tr>
            )
          })}
        </tbody>
      </table>

      {/* Expanded gap detail panels render outside the table for proper layout */}
      {sortedStats.map(fwStat => {
        if (!expandedFrameworkGaps.has(fwStat.frameworkKey)) return null

        return (
          <div key={`gap-${fwStat.frameworkKey}`} className="ft-gap-detail">
            <FrameworkGapDetail
              frameworkName={fwStat.frameworkName}
              frameworkKey={fwStat.frameworkKey}
              gapControlIds={fwStat.gapControlIds}
              gapsByDomain={fwStat.gapsByDomain}
              totalControls={fwStat.totalControls}
              selectedControls={fwStat.selectedControls}
              onScopingDataChange={onScopingDataChange}
              scopingData={scopingData}
              onNavigateToScoping={onNavigateToScoping}
            />
          </div>
        )
      })}
    </div>
  )
}
