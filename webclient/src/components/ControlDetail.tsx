import { useMemo, useState, useEffect, useCallback } from 'react'
import type { EnrichedControl, ScopedControlsFile } from '../types'
import { getEvidenceTracking } from '../data/scopingService'
import { getEvidenceHealth, type EvidenceHealthResponse } from '../data/apiClient'
import GraphView from './GraphView'
import MaturityRoadmap from './MaturityRoadmap'
import BusinessSizeGuidance from './BusinessSizeGuidance'
import SCRMFocusBadges from './SCRMFocusBadges'
import RiskThreatContext from './RiskThreatContext'
import AssessmentObjectivesList from './AssessmentObjectivesList'
import CDMControlPanel from './CDMControlPanel'
import { WorkspaceRecord } from './provenance/WorkspaceRecord'

interface Props {
  control?: EnrichedControl
  scopingData?: ScopedControlsFile | null
  organizationId?: string
  onNavigateToEvidence?: (evidenceId: string) => void
}

type DetailTab = 'details' | 'assessment' | 'mappings' | 'knowledge-base'

export default function ControlDetail({ control, scopingData, organizationId, onNavigateToEvidence }: Props) {
  const [showGraph, setShowGraph] = useState(false)
  const [activeTab, setActiveTab] = useState<DetailTab>('details')

  const [healthData, setHealthData] = useState<EvidenceHealthResponse | null>(null)

  const loadHealthData = useCallback(async () => {
    if (!organizationId) return
    try {
      const result = await getEvidenceHealth(organizationId)
      setHealthData(result)
    } catch {
      // Health data is optional enhancement — fail silently
    }
  }, [organizationId])

  useEffect(() => {
    loadHealthData()
  }, [loadHealthData])

  const evidenceStatusItems = useMemo(() => {
    if (!control || !control.artifactsResolved.length) return []
    return control.artifactsResolved.map(artifact => {
      const tracking = scopingData ? getEvidenceTracking(scopingData, artifact.id) : null
      const healthItem = healthData?.items.find(i => i.evidence_id === artifact.id)
      return {
        id: artifact.id,
        title: artifact.title,
        isTracked: tracking?.is_tracked || false,
        status: healthItem?.status || 'unknown',
        fileCount: healthItem?.file_count || 0,
      }
    })
  }, [control, scopingData, healthData])

  const groupedArtifacts = useMemo(() => {
    const groups: Record<string, { id: string; title: string }[]> = {}
    if (!control) return groups
    for (const a of control.artifactsResolved) {
      if (!groups[a.domain]) groups[a.domain] = []
      groups[a.domain].push({ id: a.id, title: a.title })
    }
    return groups
  }, [control])

  if (!control) {
    return (
      <div className="detail">
        <div className="empty">Select a control to view details</div>
      </div>
    )
  }

  const totalArtifacts = control.artifactsResolved.length
  const totalFrameworks = Object.keys(control.frameworksResolved).length

  return (
    <div className="detail">
      {/* Redesigned header — matches scoping page pattern */}
      <div className="detail-header-redesign surface-bedrock" data-source="SCF Reference">
        <span className="scf-source-tag">SCF Catalog</span>
        <div className="detail-header-badges">
          <span className="scf-id-pill">{control.scf_id}</span>
          <button
            className={`btn-graph-toggle ${showGraph ? 'active' : ''}`}
            onClick={() => setShowGraph(v => !v)}
            title={showGraph ? 'Hide graph view' : 'Show graph view'}
          >
            📊
          </button>
          <div className="cadence-row">
            <span className="cadence-label">Domain:</span>
            <span className="cadence-badge">{control.scf_domain}</span>
            {control.validation_cadence && (
              <>
                <span className="cadence-label">Validation Cadence:</span>
                <span className="cadence-badge">{control.validation_cadence}</span>
              </>
            )}
          </div>
        </div>
        <h1 className="control-title">{control.control_name}</h1>

        {/* 3-column: description left, widgets center, SCRM right */}
        <div className="detail-header-split">
          <div className="detail-header-left">
            <p className="control-description">{control.control_description}</p>
            {control.control_question && (
              <div className="assessment-question-block">
                <div className="assessment-question-label">Assessment Question</div>
                <blockquote className="assessment-question-text">
                  "{control.control_question}"
                </blockquote>
              </div>
            )}
          </div>
          <div className="detail-header-right">
            <div className="detail-widget-group">
              <div className="detail-widget-group-label">Classification</div>
              <div className="detail-widget-group-items">
                {control.nist_csf_function && (
                  <div className={`detail-widget theme-${control.nist_csf_function.toLowerCase()}`}>
                    <span className="detail-widget-value">{control.nist_csf_function}</span>
                    <span className="detail-widget-label">CSF Function</span>
                  </div>
                )}
                {control.control_weighting && (
                  <div className="detail-widget widget-weight">
                    <span className="detail-widget-value">{control.control_weighting}</span>
                    <span className="detail-widget-label">Weight</span>
                  </div>
                )}
              </div>
            </div>
            <div className="detail-widget-group">
              <div className="detail-widget-group-label">Coverage</div>
              <div className="detail-widget-group-items">
                <div className="detail-widget widget-count">
                  <span className="detail-widget-value">{totalFrameworks}</span>
                  <span className="detail-widget-label">Frameworks</span>
                </div>
                <div className="detail-widget widget-count">
                  <span className="detail-widget-value">{totalArtifacts}</span>
                  <span className="detail-widget-label">Artifacts</span>
                </div>
              </div>
            </div>
          </div>
          <div className="detail-header-scrm">
            <SCRMFocusBadges focus={control.scrm_focus} />
          </div>
        </div>
      </div>

      {showGraph ? (
        <div className="graph-container">
          <GraphView control={control} />
        </div>
      ) : (
        <div className="detail-content-compact">
          {/* SCF-derived guidance — reference material, rendered flat */}
          <div className="surface-bedrock">
            {/* Risk & Threat Context — full width */}
            <RiskThreatContext mapping={control.risk_threat_mapping} />

            {/* Maturity + Right-Sizing — side by side */}
            <div className="scoping-card-grid">
              <MaturityRoadmap maturity={control.cmm_maturity} />
              <BusinessSizeGuidance guidance={control.business_size_guidance} />
            </div>
          </div>

          {/* Tab Navigation */}
          <div className="detail-tabs">
            <button
              className={`detail-tab ${activeTab === 'details' ? 'active' : ''}`}
              onClick={() => setActiveTab('details')}
            >
              DETAILS
            </button>
            <button
              className={`detail-tab ${activeTab === 'assessment' ? 'active' : ''}`}
              onClick={() => setActiveTab('assessment')}
            >
              ASSESSMENT
            </button>
            <button
              className={`detail-tab ${activeTab === 'mappings' ? 'active' : ''}`}
              onClick={() => setActiveTab('mappings')}
            >
              MAPPINGS
              <span className="detail-tab-count">{totalFrameworks}</span>
            </button>
            <button
              className={`detail-tab ${activeTab === 'knowledge-base' ? 'active' : ''}`}
              onClick={() => setActiveTab('knowledge-base')}
            >
              KNOWLEDGE BASE
            </button>
          </div>

          {/* Details Tab */}
          {activeTab === 'details' && (
            <>
              {/* Legacy CCF fields — only shown if present */}
              {(control.policy_standard || control.implementation_guidance || control.testing_procedure) && (
                <div className="detail-section-container surface-bedrock">
                  <div className="container-header">
                    <span className="container-icon">📄</span>
                    <span className="container-title">Additional Guidance</span>
                  </div>
                  <div className="container-content">
                    {control.policy_standard && (
                      <div className="detail-field">
                        <div className="field-label">
                          <span className="field-icon">📜</span>
                          Policy Standard
                        </div>
                        <div className="field-content">{control.policy_standard}</div>
                      </div>
                    )}
                    {control.implementation_guidance && (
                      <div className="detail-field">
                        <div className="field-label">
                          <span className="field-icon">💡</span>
                          Implementation Guidance
                        </div>
                        <div className="field-content prewrap">{control.implementation_guidance}</div>
                      </div>
                    )}
                    {control.testing_procedure && (
                      <div className="detail-field">
                        <div className="field-label">
                          <span className="field-icon">🔍</span>
                          Testing Procedure
                        </div>
                        <div className="field-content prewrap">{control.testing_procedure}</div>
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Audit Artifacts */}
              <div className="detail-section-container surface-bedrock">
                <div className="container-header">
                  <span className="container-icon">📋</span>
                  <span className="container-title">Audit Artifacts</span>
                  <span className="container-count">{totalArtifacts}</span>
                </div>
                <div className="container-content">
                  {Object.keys(groupedArtifacts).length === 0 ? (
                    <div className="muted">No artifacts listed</div>
                  ) : (
                    <div className="artifact-list-compact">
                      {Object.entries(groupedArtifacts).map(([domain, items]) => (
                        <div key={domain} className="artifact-domain-group">
                          <div className="artifact-domain-title">{domain}</div>
                          <div className="artifact-items">
                            {items.map(it => (
                              <div key={it.id} className="artifact-item-compact">
                                <span className="artifact-id-badge">{it.id}</span>
                                <span className="artifact-title-text">{it.title}</span>
                              </div>
                            ))}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>

              {/* Evidence Status (#482) */}
              {evidenceStatusItems.length > 0 && (
                <div className="detail-section-container">
                  <div className="container-header">
                    <span className="container-icon">&#x2714;&#xFE0F;</span>
                    <span className="container-title">Evidence Status</span>
                    <span className="container-count">{evidenceStatusItems.length}</span>
                  </div>
                  <div className="container-content">
                    <div className="evidence-status-grid">
                      {evidenceStatusItems.map(item => (
                        <div
                          key={item.id}
                          className={`evidence-status-row${onNavigateToEvidence ? ' cursor-pointer' : ''}`}
                          onClick={() => onNavigateToEvidence?.(item.id)}
                        >
                          <span className={`ehd-status-dot ehd-dot-${item.status}`} />
                          <span className="evidence-status-id">{item.id}</span>
                          <span className="evidence-status-title">{item.title}</span>
                          <span className="evidence-status-files">
                            {item.fileCount > 0 ? `${item.fileCount} file${item.fileCount !== 1 ? 's' : ''}` : 'No files'}
                          </span>
                          <span className={`evidence-status-tracked ${item.isTracked ? 'tracked' : 'not-tracked'}`}>
                            {item.isTracked ? 'Tracked' : 'Not tracked'}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              )}
            </>
          )}

          {/* Assessment Tab */}
          {activeTab === 'assessment' && (
            <AssessmentObjectivesList scfId={control.scf_id} />
          )}

          {/* Mappings Tab */}
          {activeTab === 'mappings' && (
            <div className="detail-section-container surface-bedrock">
              <div className="container-header">
                <span className="container-icon">🔗</span>
                <span className="container-title">Framework Mappings</span>
                <span className="container-count">{totalFrameworks}</span>
              </div>
              <div className="container-content">
                {Object.keys(control.frameworksResolved).length === 0 ? (
                  <div className="muted">No mappings listed</div>
                ) : (
                  <div className="framework-list-compact">
                    {Object.entries(control.frameworksResolved).map(([fw, refs]) => (
                      <div key={fw} className="framework-item-compact">
                        <div className="framework-name-compact">{fw}</div>
                        <div className="framework-refs">
                          {refs.map((r, i) => (
                            <span key={`${r}-${i}`} className="ref-chip">{r}</span>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Knowledge Base Tab (CDM v1 slice 10) */}
          {activeTab === 'knowledge-base' && organizationId && (
            <WorkspaceRecord title="Your Knowledge Base">
              <CDMControlPanel
                organizationId={organizationId}
                scopedControlId={
                  scopingData?.scoped_controls.find(
                    (sc) => sc.scf_id === control.scf_id,
                  )?.id
                }
                controlName={control.control_name}
                controlDescription={control.control_description}
              />
            </WorkspaceRecord>
          )}
        </div>
      )}
    </div>
  )
}
