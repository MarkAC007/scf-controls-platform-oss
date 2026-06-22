/**
 * RiskAssessmentList Component - Table view of risk assessments
 *
 * Displays risks in a sortable, filterable table with inline editing
 * for likelihood and impact scores.
 */
import { useState, useMemo } from 'react'
import type {
  RiskAssessment,
  RiskCodesFile,
  TreatmentStatus,
  RiskLevel,
  RiskCategory
} from '../types'
import {
  getRiskLevel,
  getRiskLevelColor,
  LIKELIHOOD_LABELS,
  IMPACT_LABELS,
  TREATMENT_STATUS_LABELS
} from '../types'

interface RiskAssessmentListProps {
  assessments: RiskAssessment[]
  riskCodes: RiskCodesFile
  onSelectRisk: (riskCode: string) => void
  onUpdateRisk: (riskCode: string, updates: Partial<RiskAssessment>) => void
  selectedRiskCode?: string | null
  filterByCell?: { likelihood: number; impact: number } | null
  matrixType: 'inherent' | 'residual'
}

type SortField = 'risk_code' | 'category' | 'title' | 'score' | 'status'
type SortDirection = 'asc' | 'desc'

export default function RiskAssessmentList({
  assessments,
  riskCodes,
  onSelectRisk,
  onUpdateRisk,
  selectedRiskCode,
  filterByCell,
  matrixType
}: RiskAssessmentListProps) {
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

    // Filter by search term
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
        case 'title':
          const titleA = riskCodes.codes[a.risk_code]?.title || ''
          const titleB = riskCodes.codes[b.risk_code]?.title || ''
          comparison = titleA.localeCompare(titleB)
          break
        case 'score':
          const scoreA = getScore(a) ?? 0
          const scoreB = getScore(b) ?? 0
          comparison = scoreA - scoreB
          break
        case 'status':
          comparison = a.treatment_status.localeCompare(b.treatment_status)
          break
      }

      return sortDirection === 'asc' ? comparison : -comparison
    })

    return result
  }, [assessments, filterByCell, filterStatus, filterCategory, searchTerm, sortField, sortDirection, matrixType, riskCodes])

  // Handle sort click
  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDirection(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortField(field)
      setSortDirection('asc')
    }
  }

  // Render sort indicator
  const SortIndicator = ({ field }: { field: SortField }) => {
    if (sortField !== field) return null
    return <span className="sort-indicator">{sortDirection === 'asc' ? '▲' : '▼'}</span>
  }

  return (
    <div className="risk-assessment-list">
      {/* Filters */}
      <div className="risk-list-filters">
        <input
          type="text"
          placeholder="Search risks..."
          value={searchTerm}
          onChange={e => setSearchTerm(e.target.value)}
          className="risk-search-input"
        />

        <select
          value={filterCategory}
          onChange={e => setFilterCategory(e.target.value as RiskCategory | 'all')}
          className="risk-filter-select"
        >
          <option value="all">All Categories</option>
          {Object.entries(riskCodes.categories).map(([key, cat]) => (
            <option key={key} value={key}>{cat.name}</option>
          ))}
        </select>

        <select
          value={filterStatus}
          onChange={e => setFilterStatus(e.target.value as TreatmentStatus | 'all')}
          className="risk-filter-select"
        >
          <option value="all">All Statuses</option>
          {Object.entries(TREATMENT_STATUS_LABELS).map(([key, label]) => (
            <option key={key} value={key}>{label}</option>
          ))}
        </select>

        {filterByCell && (
          <span className="risk-cell-filter">
            Showing L{filterByCell.likelihood} × I{filterByCell.impact}
            <button
              className="clear-cell-filter"
              onClick={() => onSelectRisk('')}
              title="Clear filter"
            >
              ×
            </button>
          </span>
        )}

        <span className="risk-count">{filteredAssessments.length} risks</span>
      </div>

      {/* Table */}
      <div className="risk-list-table-container">
        <table className="risk-list-table">
          <thead>
            <tr>
              <th onClick={() => handleSort('risk_code')} className="sortable">
                Code <SortIndicator field="risk_code" />
              </th>
              <th onClick={() => handleSort('category')} className="sortable">
                Category <SortIndicator field="category" />
              </th>
              <th onClick={() => handleSort('title')} className="sortable">
                Title <SortIndicator field="title" />
              </th>
              <th>Likelihood</th>
              <th>Impact</th>
              <th onClick={() => handleSort('score')} className="sortable">
                Score <SortIndicator field="score" />
              </th>
              <th onClick={() => handleSort('status')} className="sortable">
                Status <SortIndicator field="status" />
              </th>
              <th>Owner</th>
            </tr>
          </thead>
          <tbody>
            {filteredAssessments.map(assessment => {
              const codeInfo = riskCodes.codes[assessment.risk_code]
              const category = getCategory(assessment.risk_code)
              const categoryInfo = riskCodes.categories[category]
              const score = getScore(assessment)
              const level = getLevel(assessment)
              const likelihood = matrixType === 'inherent' ? assessment.likelihood : assessment.residual_likelihood
              const impact = matrixType === 'inherent' ? assessment.impact : assessment.residual_impact

              return (
                <tr
                  key={assessment.risk_code}
                  className={`${selectedRiskCode === assessment.risk_code ? 'selected' : ''}`}
                  onClick={() => onSelectRisk(assessment.risk_code)}
                >
                  <td className="risk-code-cell">
                    <span className="risk-code">{assessment.risk_code}</span>
                    {assessment.risk_code.startsWith('R-ORG-') && (
                      <span className="custom-risk-badge-small">Custom</span>
                    )}
                  </td>
                  <td>
                    <span
                      className="category-badge"
                      style={{ backgroundColor: categoryInfo?.color + '20', color: categoryInfo?.color }}
                    >
                      {categoryInfo?.name || category}
                    </span>
                  </td>
                  <td className="risk-title-cell">
                    <span className="risk-title">{codeInfo?.title || 'Unknown'}</span>
                  </td>
                  <td className="score-cell">
                    <select
                      value={likelihood ?? ''}
                      onChange={e => {
                        e.stopPropagation()
                        const val = e.target.value ? parseInt(e.target.value) : null
                        const field = matrixType === 'inherent' ? 'likelihood' : 'residual_likelihood'
                        onUpdateRisk(assessment.risk_code, { [field]: val })
                      }}
                      onClick={e => e.stopPropagation()}
                      className="inline-select"
                    >
                      <option value="">-</option>
                      {[1, 2, 3, 4, 5].map(v => (
                        <option key={v} value={v}>{v} - {LIKELIHOOD_LABELS[v]}</option>
                      ))}
                    </select>
                  </td>
                  <td className="score-cell">
                    <select
                      value={impact ?? ''}
                      onChange={e => {
                        e.stopPropagation()
                        const val = e.target.value ? parseInt(e.target.value) : null
                        const field = matrixType === 'inherent' ? 'impact' : 'residual_impact'
                        onUpdateRisk(assessment.risk_code, { [field]: val })
                      }}
                      onClick={e => e.stopPropagation()}
                      className="inline-select"
                    >
                      <option value="">-</option>
                      {[1, 2, 3, 4, 5].map(v => (
                        <option key={v} value={v}>{v} - {IMPACT_LABELS[v]}</option>
                      ))}
                    </select>
                  </td>
                  <td className="score-cell">
                    {score != null ? (
                      <span
                        className="score-badge"
                        style={{
                          backgroundColor: level ? getRiskLevelColor(level) + '20' : undefined,
                          color: level ? getRiskLevelColor(level) : undefined,
                          borderColor: level ? getRiskLevelColor(level) : undefined
                        }}
                      >
                        {score}
                      </span>
                    ) : (
                      <span className="score-empty">-</span>
                    )}
                  </td>
                  <td>
                    <select
                      value={assessment.treatment_status}
                      onChange={e => {
                        e.stopPropagation()
                        onUpdateRisk(assessment.risk_code, {
                          treatment_status: e.target.value as TreatmentStatus
                        })
                      }}
                      onClick={e => e.stopPropagation()}
                      className="inline-select status-select"
                    >
                      {Object.entries(TREATMENT_STATUS_LABELS).map(([key, label]) => (
                        <option key={key} value={key}>{label}</option>
                      ))}
                    </select>
                  </td>
                  <td className="owner-cell">
                    {assessment.owner ? (
                      <span className="owner-name">{assessment.owner.display_name || assessment.owner.email}</span>
                    ) : (
                      <span className="owner-empty">Unassigned</span>
                    )}
                  </td>
                </tr>
              )
            })}
            {filteredAssessments.length === 0 && (
              <tr>
                <td colSpan={8} className="empty-message">
                  {assessments.length === 0
                    ? 'No risks have been assessed yet. Click a risk code to start assessing.'
                    : 'No risks match the current filters.'}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
