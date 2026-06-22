import { useState, useEffect } from 'react'
import type { AssessmentObjective, ControlId } from '../types'
import { fetchControlAssessmentObjectives } from '../data/catalogApi'

interface Props {
  scfId: ControlId
}

function PPTDFBadges({ pptdf }: { pptdf?: AssessmentObjective['pptdf_applicability'] }) {
  const items = [
    { key: 'people', label: 'People', active: pptdf?.people },
    { key: 'process', label: 'Process', active: pptdf?.process },
    { key: 'technology', label: 'Tech', active: pptdf?.technology },
    { key: 'data', label: 'Data', active: pptdf?.data },
    { key: 'facility', label: 'Fac', active: pptdf?.facility },
  ]

  return (
    <div className="ao-pptdf">
      {items.map(item => (
        <span
          key={item.key}
          className={`ao-pptdf-badge ${item.active ? 'active' : ''}`}
        >
          {item.active && <span className="ao-pptdf-check">✓</span>}
          {item.label}
        </span>
      ))}
    </div>
  )
}

function ObjectiveCard({ objective }: { objective: AssessmentObjective }) {
  const [showParameters, setShowParameters] = useState(false)
  const [showProcedure, setShowProcedure] = useState(false)

  const hasParameters = objective.scf_defined_parameters || objective.org_defined_parameters
  const hasProcedure = objective.assessment_procedure

  // Collect framework mappings
  const frameworks: string[] = []
  if (objective.cmmc_level1_ao) frameworks.push('CMMC L1')
  if (objective.dhs_ztcf_ao) frameworks.push('DHS ZTCF')
  if (objective.nist_800_53a) frameworks.push('NIST 800-53A')
  if (objective.nist_800_171a) frameworks.push('NIST 800-171A')
  if (objective.nist_800_171a_r3) frameworks.push('NIST 800-171A R3')
  if (objective.nist_800_172a) frameworks.push('NIST 800-172A')

  return (
    <div className="assessment-objective-card">
      <div className="ao-header">
        <span className="ao-id">{objective.ao_id}</span>
        {objective.assessment_rigor && (
          <div className="ao-rigor">
            <span className="ao-rigor-label">Rigor:</span>
            <span className="ao-rigor-value">{objective.assessment_rigor}</span>
          </div>
        )}
      </div>

      <div className="ao-text">{objective.objective_text}</div>

      <div className="ao-meta">
        {objective.asset_type && (
          <div className="ao-meta-item">
            <span className="ao-meta-label">Method:</span>
            <span>{objective.asset_type}</span>
          </div>
        )}
        {objective.ao_origins && (
          <div className="ao-meta-item">
            <span className="ao-meta-label">Origin:</span>
            <span>{objective.ao_origins}</span>
          </div>
        )}
      </div>

      {objective.pptdf_applicability && (
        <PPTDFBadges pptdf={objective.pptdf_applicability} />
      )}

      {frameworks.length > 0 && (
        <div className="ao-frameworks">
          {frameworks.map(fw => (
            <span key={fw} className="ao-framework-chip">{fw}</span>
          ))}
        </div>
      )}

      {(hasParameters || hasProcedure) && (
        <div className="ao-expandable">
          {hasParameters && (
            <>
              <button
                className="ao-expand-btn"
                onClick={() => setShowParameters(!showParameters)}
                aria-expanded={showParameters}
              >
                <span className="ao-expand-icon">{showParameters ? '▼' : '▶'}</span>
                <span>Parameters</span>
              </button>
              {showParameters && (
                <div className="ao-expanded-content">
                  {objective.scf_defined_parameters && (
                    <>
                      <strong>SCF Defined:</strong>
                      <p>{objective.scf_defined_parameters}</p>
                    </>
                  )}
                  {objective.org_defined_parameters && (
                    <>
                      <strong>Org Defined:</strong>
                      <p>{objective.org_defined_parameters}</p>
                    </>
                  )}
                </div>
              )}
            </>
          )}

          {hasProcedure && (
            <>
              <button
                className="ao-expand-btn"
                onClick={() => setShowProcedure(!showProcedure)}
                aria-expanded={showProcedure}
                style={{ marginTop: hasParameters ? '8px' : 0 }}
              >
                <span className="ao-expand-icon">{showProcedure ? '▼' : '▶'}</span>
                <span>Procedure</span>
              </button>
              {showProcedure && (
                <div className="ao-expanded-content">
                  {objective.assessment_procedure}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}

export default function AssessmentObjectivesList({ scfId }: Props) {
  const [objectives, setObjectives] = useState<AssessmentObjective[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false

    async function loadObjectives() {
      setLoading(true)
      setError(null)

      try {
        const data = await fetchControlAssessmentObjectives(scfId)
        if (!cancelled) {
          setObjectives(data)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load assessment objectives')
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    loadObjectives()

    return () => {
      cancelled = true
    }
  }, [scfId])

  if (loading) {
    return (
      <div className="ao-loading">
        Loading assessment objectives...
      </div>
    )
  }

  if (error) {
    return (
      <div className="ao-empty">
        Error: {error}
      </div>
    )
  }

  if (objectives.length === 0) {
    return (
      <div className="ao-empty">
        No assessment objectives available for this control
      </div>
    )
  }

  return (
    <div className="assessment-objectives-list">
      {objectives.map(obj => (
        <ObjectiveCard key={obj.ao_id} objective={obj} />
      ))}
    </div>
  )
}
