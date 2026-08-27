/**
 * RiskAssessmentList Component - Explorer-chrome list view of risk assessments
 *
 * Phase 3 Task 5: converted to Explorer idiom:
 *  - FilterSidebar (collapsible): status + category filters
 *  - ListToolbar: search (code/title/description), count, "+ Add Custom Risk" action
 *  - Quiet level-count strip moved to RiskDashboard (see there)
 *  - Rows: ExplorerListRow with monoId=risk_code, title, category badge,
 *    INLINE selects (likelihood / impact / status) preserved as row children,
 *    score badge using existing semantic classes (risk-cell-low/medium/high/critical)
 *  - Sortable column headers preserved above the row list
 *  - Cell-filter chip in toolbar when filterByCell is active
 *
 * Matrix panel + slide-over detail + custom-risk modal + lazy-create: UNTOUCHED
 * (owned by RiskDashboard).
 */
import { useState, useMemo, useEffect } from 'react'
import type {
  RiskAssessment,
  RiskCodesFile,
  TreatmentStatus,
  RiskLevel,
  RiskCategory
} from '../types'
import {
  getRiskLevel,
  LIKELIHOOD_LABELS,
  IMPACT_LABELS,
  TREATMENT_STATUS_LABELS
} from '../types'
import FilterSidebar, { FilterGroup, FilterSelect } from './explorer/FilterSidebar'
import ListToolbar from './explorer/ListToolbar'
import ExplorerListRow, { RowChip, RowMeta } from './explorer/ListRow'

interface RiskAssessmentListProps {
  assessments: RiskAssessment[]
  riskCodes: RiskCodesFile
  onSelectRisk: (riskCode: string) => void
  onUpdateRisk: (riskCode: string, updates: Partial<RiskAssessment>) => void
  selectedRiskCode?: string | null
  filterByCell?: { likelihood: number; impact: number } | null
  matrixType: 'inherent' | 'residual'
  /** Called whenever the filtered+sorted list changes — used by RiskDashboard for pager. */
  onFilteredListChange?: (list: RiskAssessment[]) => void
}

type SortField = 'risk_code' | 'category' | 'title' | 'score' | 'status'
type SortDirection = 'asc' | 'desc'

/** Map risk level → existing CSS class names (from .risk-cell-* classes in styles.css) */
function riskLevelClass(level: RiskLevel | null | undefined): string {
  if (!level) return ''
  return `risk-cell-${level}`
}

export default function RiskAssessmentList({
  assessments,
  riskCodes,
  onSelectRisk,
  onUpdateRisk,
  selectedRiskCode,
  filterByCell,
  matrixType,
  onFilteredListChange,
}: RiskAssessmentListProps) {
  const [filtersCollapsed, setFiltersCollapsed] = useState(false)
  const [sortField, setSortField] = useState<SortField>('risk_code')
  const [sortDirection, setSortDirection] = useState<SortDirection>('asc')
  const [filterStatus, setFilterStatus] = useState<TreatmentStatus | 'all'>('all')
  const [filterCategory, setFilterCategory] = useState<RiskCategory | 'all'>('all')
  const [searchTerm, setSearchTerm] = useState('')

  // Get category from risk code (e.g., "R-AC-1" -> "AC")
  const getCategory = (riskCode: string): RiskCategory => {
    return riskCode.split('-')[1] as RiskCategory
  }

  // Get score based on matrix type
  const getScore = (assessment: RiskAssessment): number | null => {
    if (matrixType === 'inherent') {
      return assessment.inherent_risk_score ?? null
    }
    return assessment.residual_risk_score ?? null
  }

  // Get level based on matrix type
  const getLevel = (assessment: RiskAssessment): RiskLevel | null => {
    if (matrixType === 'inherent') {
      return assessment.inherent_risk_level ?? null
    }
    return assessment.residual_risk_level ?? null
  }

  // Filter and sort assessments
  const filteredAssessments = useMemo(() => {
    let result = [...assessments]

    // Filter by cell (from matrix click)
    if (filterByCell) {
      result = result.filter(a => {
        if (matrixType === 'inherent') {
          return a.likelihood === filterByCell.likelihood && a.impact === filterByCell.impact
        }
        return a.residual_likelihood === filterByCell.likelihood &&
               a.residual_impact === filterByCell.impact
      })
    }

    // Filter by status
    if (filterStatus !== 'all') {
      result = result.filter(a => a.treatment_status === filterStatus)
    }

    // Filter by category
    if (filterCategory !== 'all') {
      result = result.filter(a => getCategory(a.risk_code) === filterCategory)
    }

    // Filter by search term (code / title / description)
    if (searchTerm) {
      const term = searchTerm.toLowerCase()
      result = result.filter(a => {
        const codeInfo = riskCodes.codes[a.risk_code]
        return a.risk_code.toLowerCase().includes(term) ||
               codeInfo?.title.toLowerCase().includes(term) ||
               codeInfo?.description.toLowerCase().includes(term)
      })
    }

    // Sort
    result.sort((a, b) => {
      let comparison = 0

      switch (sortField) {
        case 'risk_code':
          comparison = a.risk_code.localeCompare(b.risk_code)
          break
        case 'category':
          comparison = getCategory(a.risk_code).localeCompare(getCategory(b.risk_code))
          break
        case 'title': {
          const titleA = riskCodes.codes[a.risk_code]?.title || ''
          const titleB = riskCodes.codes[b.risk_code]?.title || ''
          comparison = titleA.localeCompare(titleB)
          break
        }
        case 'score': {
          const scoreA = getScore(a) ?? 0
          const scoreB = getScore(b) ?? 0
          comparison = scoreA - scoreB
          break
        }
        case 'status':
          comparison = a.treatment_status.localeCompare(b.treatment_status)
          break
      }

      return sortDirection === 'asc' ? comparison : -comparison
    })

    return result
  }, [assessments, filterByCell, filterStatus, filterCategory, searchTerm, sortField, sortDirection, matrixType, riskCodes])

  // Notify parent of filtered list changes (for pager in RiskDashboard)
  useEffect(() => {
    onFilteredListChange?.(filteredAssessments)
  }, [filteredAssessments, onFilteredListChange])

  // Handle sort click
  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDirection(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortField(field)
      setSortDirection('asc')
    }
  }

  // Sort indicator character
  const sortIndicator = (field: SortField) =>
    sortField === field ? (sortDirection === 'asc' ? ' ▲' : ' ▼') : ''

  // Category filter options
  const categoryOptions = useMemo(() => [
    { value: 'all', label: 'All Categories' },
    ...Object.entries(riskCodes.categories).map(([key, cat]) => ({
      value: key,
      label: cat.name,
    })),
  ], [riskCodes.categories])

  // Status filter options
  const statusOptions = useMemo(() => [
    { value: 'all', label: 'All Statuses' },
    ...Object.entries(TREATMENT_STATUS_LABELS).map(([key, label]) => ({
      value: key,
      label,
    })),
  ], [])

  // Toolbar actions: cell-filter chip only (Add Custom Risk moved to dashboard-level strip)
  const toolbarActions = (
    <div className="risk-toolbar-actions">
      {filterByCell && (
        <span className="risk-cell-filter-chip">
          {`L${filterByCell.likelihood} × I${filterByCell.impact}`}
          <button
            className="risk-cell-filter-clear"
            onClick={() => onSelectRisk('')}
            aria-label="Clear filter"
            title="Clear filter"
          >
            ×
          </button>
        </span>
      )}
    </div>
  )

  // Toolbar count node
  const countNode = (
    <span className="risk-toolbar-count">
      {filteredAssessments.length} {filteredAssessments.length === 1 ? 'risk' : 'risks'}
    </span>
  )

  return (
    <div className="risk-list-page">
      <FilterSidebar
        collapsed={filtersCollapsed}
        onToggleCollapsed={() => setFiltersCollapsed(c => !c)}
        aria-label="Risk filters"
      >
        <FilterGroup label="STATUS">
          <FilterSelect
            value={filterStatus}
            onChange={v => setFilterStatus(v as TreatmentStatus | 'all')}
            options={statusOptions}
          />
        </FilterGroup>

        <FilterGroup label="CATEGORY">
          <FilterSelect
            value={filterCategory}
            onChange={v => setFilterCategory(v as RiskCategory | 'all')}
            options={categoryOptions}
          />
        </FilterGroup>
      </FilterSidebar>

      <div className="risk-list-body">
        <ListToolbar
          search={searchTerm}
          onSearchChange={setSearchTerm}
          searchPlaceholder="Search risks — code, title, description…"
          count={countNode}
          actions={toolbarActions}
        />

        {/* Sortable column header row */}
        <div className="risk-list-header-row" role="row" aria-label="Sort columns">
          <button
            className={`risk-list-header-cell risk-col-code${sortField === 'risk_code' ? ' risk-sort-active' : ''}`}
            onClick={() => handleSort('risk_code')}
            aria-label={`Code${sortIndicator('risk_code')}`}
          >
            CODE{sortIndicator('risk_code')}
          </button>
          <button
            className={`risk-list-header-cell risk-col-title${sortField === 'title' ? ' risk-sort-active' : ''}`}
            onClick={() => handleSort('title')}
            aria-label={`Title${sortIndicator('title')}`}
          >
            TITLE{sortIndicator('title')}
          </button>
          <button
            className={`risk-list-header-cell risk-col-category${sortField === 'category' ? ' risk-sort-active' : ''}`}
            onClick={() => handleSort('category')}
            aria-label={`Category${sortIndicator('category')}`}
          >
            CATEGORY{sortIndicator('category')}
          </button>
          <div className="risk-list-header-cell risk-col-likelihood">LKH</div>
          <div className="risk-list-header-cell risk-col-impact">IMP</div>
          <button
            className={`risk-list-header-cell risk-col-score${sortField === 'score' ? ' risk-sort-active' : ''}`}
            onClick={() => handleSort('score')}
            aria-label={`Score${sortIndicator('score')}`}
          >
            SCORE{sortIndicator('score')}
          </button>
          <button
            className={`risk-list-header-cell risk-col-status${sortField === 'status' ? ' risk-sort-active' : ''}`}
            onClick={() => handleSort('status')}
            aria-label={`Status${sortIndicator('status')}`}
          >
            STATUS{sortIndicator('status')}
          </button>
          <div className="risk-list-header-cell risk-col-owner">OWNER</div>
        </div>

        {/* Risk rows */}
        <div className="risk-list-rows">
          {filteredAssessments.map(assessment => {
            const codeInfo = riskCodes.codes[assessment.risk_code]
            const category = getCategory(assessment.risk_code)
            const categoryInfo = riskCodes.categories[category]
            const score = getScore(assessment)
            const level = getLevel(assessment)
            const likelihood = matrixType === 'inherent' ? assessment.likelihood : assessment.residual_likelihood
            const impact = matrixType === 'inherent' ? assessment.impact : assessment.residual_impact
            const isCustom = assessment.risk_code.startsWith('R-ORG-')

            return (
              <div key={assessment.risk_code} className="risk-list-row-wrap">
                <ExplorerListRow
                  monoId={assessment.risk_code}
                  title={codeInfo?.title || 'Unknown'}
                  highlighted={selectedRiskCode === assessment.risk_code}
                  accent={level === 'critical' || level === 'high'}
                  onClick={() => onSelectRisk(assessment.risk_code)}
                >
                  {/* Category badge */}
                  <RowMeta width={120}>
                    <span
                      className="risk-category-badge"
                      style={{
                        backgroundColor: `${categoryInfo?.color ?? '#6b7280'}20`,
                        color: categoryInfo?.color ?? '#6b7280',
                      }}
                    >
                      {isCustom && <span className="risk-custom-tag">Custom · </span>}
                      {categoryInfo?.name || category}
                    </span>
                  </RowMeta>

                  {/* Inline likelihood select */}
                  <RowMeta width={80}>
                    <select
                      value={likelihood ?? ''}
                      onChange={e => {
                        e.stopPropagation()
                        const val = e.target.value ? parseInt(e.target.value) : null
                        const field = matrixType === 'inherent' ? 'likelihood' : 'residual_likelihood'
                        onUpdateRisk(assessment.risk_code, { [field]: val })
                      }}
                      onClick={e => e.stopPropagation()}
                      className="risk-inline-select"
                      aria-label="Likelihood"
                    >
                      <option value="">-</option>
                      {[1, 2, 3, 4, 5].map(v => (
                        <option key={v} value={v}>{v} – {LIKELIHOOD_LABELS[v]}</option>
                      ))}
                    </select>
                  </RowMeta>

                  {/* Inline impact select */}
                  <RowMeta width={80}>
                    <select
                      value={impact ?? ''}
                      onChange={e => {
                        e.stopPropagation()
                        const val = e.target.value ? parseInt(e.target.value) : null
                        const field = matrixType === 'inherent' ? 'impact' : 'residual_impact'
                        onUpdateRisk(assessment.risk_code, { [field]: val })
                      }}
                      onClick={e => e.stopPropagation()}
                      className="risk-inline-select"
                      aria-label="Impact"
                    >
                      <option value="">-</option>
                      {[1, 2, 3, 4, 5].map(v => (
                        <option key={v} value={v}>{v} – {IMPACT_LABELS[v]}</option>
                      ))}
                    </select>
                  </RowMeta>

                  {/* Score badge */}
                  <RowMeta width={52}>
                    {score != null ? (
                      <span className={`risk-score-badge ${riskLevelClass(level)}`}>
                        {score}
                      </span>
                    ) : (
                      <span className="risk-score-empty">–</span>
                    )}
                  </RowMeta>

                  {/* Treatment status select */}
                  <RowMeta width={116}>
                    <select
                      value={assessment.treatment_status}
                      onChange={e => {
                        e.stopPropagation()
                        onUpdateRisk(assessment.risk_code, {
                          treatment_status: e.target.value as TreatmentStatus
                        })
                      }}
                      onClick={e => e.stopPropagation()}
                      className="risk-inline-select risk-status-select"
                      aria-label="Status"
                    >
                      {Object.entries(TREATMENT_STATUS_LABELS).map(([key, label]) => (
                        <option key={key} value={key}>{label}</option>
                      ))}
                    </select>
                  </RowMeta>

                  {/* Owner */}
                  <RowMeta width={120}>
                    {assessment.owner ? (
                      <span className="risk-owner-name">
                        {assessment.owner.display_name || assessment.owner.email}
                      </span>
                    ) : (
                      <span className="risk-owner-empty">Unassigned</span>
                    )}
                  </RowMeta>
                </ExplorerListRow>
              </div>
            )
          })}

          {filteredAssessments.length === 0 && (
            <div className="risk-list-empty">
              {assessments.length === 0
                ? 'No risks have been assessed yet.'
                : 'No risks match the current filters.'}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
