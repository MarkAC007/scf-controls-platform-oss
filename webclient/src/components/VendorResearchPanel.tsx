/**
 * VendorResearchPanel — DPSIA Lambda assessment panel.
 *
 * Replaces the previous multi-source research panel with a comprehensive
 * DPSIA (Data Protection Security Impact Assessment) that invokes the
 * DPSIA Lambda for AI-powered vendor security analysis.
 */
import { useState, useEffect, useRef, useCallback } from 'react'
import type {
  DPSIATriggerResponse,
  DPSIAStatusResponse,
  DPSIAResultResponse,
  DPSIARAGStatus,
  DPSIARecommendation,
} from '../types'
import {
  DPSIA_RAG_COLORS,
  DPSIA_RAG_LABELS,
  DPSIA_RECOMMENDATION_LABELS,
  DPSIA_RECOMMENDATION_COLORS,
} from '../types'
import {
  triggerDPSIAAssessment,
  getDPSIAStatus,
  getDPSIAResults,
  getDPSIALatest,
  getDPSIAActive,
  getDPSIADocxUrl,
} from '../data/apiClient'

interface VendorResearchPanelProps {
  organizationId: string
  vendorId: string
  vendorWebsite?: string | null
  vendorName?: string | null
  vendorDescription?: string | null
  onAssessmentComplete?: () => void
}

const POLL_INTERVAL = 5000 // 5 seconds (DPSIA takes 2-4 minutes)

export default function VendorResearchPanel({
  organizationId,
  vendorId,
  vendorWebsite,
  vendorName,
  vendorDescription,
  onAssessmentComplete,
}: VendorResearchPanelProps) {
  const [jobId, setJobId] = useState<string | null>(null)
  const [status, setStatus] = useState<DPSIAStatusResponse | null>(null)
  const [results, setResults] = useState<DPSIAResultResponse | null>(null)
  const [triggering, setTriggering] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [expandedSections, setExpandedSections] = useState<Record<string, boolean>>({})
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // Form state
  const [formServicesUsed, setFormServicesUsed] = useState('')
  const [formAssessmentType, setFormAssessmentType] = useState('new')
  const [formDataRole, setFormDataRole] = useState('Processor')
  const [formClientName, setFormClientName] = useState('')
  const [formAdditionalContext, setFormAdditionalContext] = useState('')

  // --------------------------------------------------
  // Polling
  // --------------------------------------------------
  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }, [])

  const startPolling = useCallback((jid: string) => {
    stopPolling()
    pollRef.current = setInterval(async () => {
      try {
        const s = await getDPSIAStatus(vendorId, jid, organizationId)
        setStatus(s)

        if (s.status === 'completed' || s.status === 'failed') {
          stopPolling()
          if (s.status === 'completed') {
            const r = await getDPSIAResults(vendorId, jid, organizationId)
            setResults(r)
            onAssessmentComplete?.()
          } else {
            setError(s.error_message || 'Assessment failed')
          }
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Polling failed')
        stopPolling()
      }
    }, POLL_INTERVAL)
  }, [vendorId, organizationId, stopPolling, onAssessmentComplete])

  useEffect(() => () => stopPolling(), [stopPolling])

  // --------------------------------------------------
  // Load latest results on mount, or resume polling if a job is active
  // --------------------------------------------------
  useEffect(() => {
    let cancelled = false
    async function loadLatestOrActive() {
      // First try to load the most recent completed result
      try {
        const latest = await getDPSIALatest(vendorId, organizationId)
        if (!cancelled && latest) {
          setResults(latest)
          setJobId(latest.job_id)
          return
        }
      } catch {
        // No completed results — check for an active job
      }

      // Check if there's an active (pending/running) job to resume polling
      try {
        const active = await getDPSIAActive(vendorId, organizationId)
        if (!cancelled && active) {
          setJobId(active.job_id)
          setStatus(active)
          startPolling(active.job_id)
        }
      } catch {
        // No active job either — empty state
      }
    }
    loadLatestOrActive()
    return () => { cancelled = true }
  }, [vendorId, organizationId, startPolling])

  // --------------------------------------------------
  // Trigger assessment
  // --------------------------------------------------
  const handleTrigger = async () => {
    if (!formServicesUsed.trim()) return
    setTriggering(true)
    setError(null)
    setResults(null)

    try {
      const resp = await triggerDPSIAAssessment(vendorId, {
        services_used: formServicesUsed.trim(),
        assessment_type: formAssessmentType,
        data_role: formDataRole,
        client_name: formClientName.trim() || undefined,
        additional_context: formAdditionalContext.trim() || undefined,
      }, organizationId)

      setJobId(resp.job_id)
      setShowForm(false)
      startPolling(resp.job_id)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to trigger assessment')
    } finally {
      setTriggering(false)
    }
  }

  const toggleSection = (key: string) => {
    setExpandedSections(prev => ({ ...prev, [key]: !prev[key] }))
  }

  // --------------------------------------------------
  // Styles
  // --------------------------------------------------
  const headingStyle: React.CSSProperties = {
    fontSize: '1rem',
    fontWeight: 600,
    margin: 0,
    color: 'var(--text)',
  }
  const textStyle: React.CSSProperties = {
    fontSize: '0.8125rem',
    color: 'var(--text)',
    lineHeight: 1.5,
  }
  const mutedStyle: React.CSSProperties = {
    ...textStyle,
    color: 'var(--muted)',
  }
  const buttonStyle: React.CSSProperties = {
    padding: '0.5rem 1rem',
    fontSize: '0.8125rem',
    fontWeight: 600,
    border: 'none',
    borderRadius: '6px',
    cursor: 'pointer',
    color: '#fff',
    backgroundColor: 'var(--primary)',
  }
  const inputStyle: React.CSSProperties = {
    width: '100%',
    padding: '0.5rem',
    fontSize: '0.8125rem',
    border: '1px solid var(--border)',
    borderRadius: '6px',
    backgroundColor: 'var(--card)',
    color: 'var(--text)',
    boxSizing: 'border-box',
  }
  const selectStyle: React.CSSProperties = {
    ...inputStyle,
    cursor: 'pointer',
  }
  const labelStyle: React.CSSProperties = {
    display: 'block',
    fontSize: '0.75rem',
    fontWeight: 600,
    color: 'var(--text)',
    marginBottom: '0.25rem',
  }
  const cardStyle: React.CSSProperties = {
    padding: '0.75rem',
    backgroundColor: 'var(--card)',
    border: '1px solid var(--border)',
    borderRadius: '8px',
  }
  const badgeStyle = (color: string): React.CSSProperties => ({
    display: 'inline-block',
    padding: '0.25rem 0.75rem',
    borderRadius: '9999px',
    fontSize: '0.75rem',
    fontWeight: 700,
    color: '#fff',
    backgroundColor: color,
  })
  const sectionHeaderStyle: React.CSSProperties = {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    cursor: 'pointer',
    padding: '0.5rem 0',
    userSelect: 'none',
  }

  // --------------------------------------------------
  // Running state
  // --------------------------------------------------
  const isRunning = status && (status.status === 'pending' || status.status === 'running')

  // --------------------------------------------------
  // Render
  // --------------------------------------------------
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', padding: '1rem' }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h3 style={headingStyle}>DPSIA Assessment</h3>
        {!isRunning && !showForm && (
          <button
            style={buttonStyle}
            onClick={() => setShowForm(true)}
          >
            {results ? 'Run New Assessment' : 'Run DPSIA Assessment'}
          </button>
        )}
      </div>

      {/* Error */}
      {error && (
        <div style={{
          padding: '0.75rem',
          backgroundColor: '#fef2f2',
          border: '1px solid #fecaca',
          borderRadius: '6px',
          color: '#991b1b',
          fontSize: '0.8125rem',
        }}>
          {error}
        </div>
      )}

      {/* Trigger Form */}
      {showForm && (
        <div className="surface-bench" style={{ ...cardStyle, display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
          <div className="bench-header" style={{ fontSize: '0.875rem', fontWeight: 600, color: 'var(--text)' }}>
            <span className="container-title">Your Vendor Assessment</span>
          </div>
          <div style={{ fontSize: '0.75rem', color: 'var(--muted)', padding: '0.5rem', backgroundColor: '#fffbeb', borderRadius: '6px', border: '1px solid #fef3c7' }}>
            Each assessment uses multiple AI API calls and typically takes 2-4 minutes to complete.
          </div>

          <div>
            <label style={labelStyle}>Services Used *</label>
            <textarea
              style={{ ...inputStyle, minHeight: '4rem', resize: 'vertical' }}
              placeholder="Describe the services this vendor provides to your organisation..."
              value={formServicesUsed}
              onChange={e => setFormServicesUsed(e.target.value)}
            />
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem' }}>
            <div>
              <label style={labelStyle}>Assessment Type</label>
              <select style={selectStyle} value={formAssessmentType} onChange={e => setFormAssessmentType(e.target.value)}>
                <option value="new">New Vendor</option>
                <option value="annual-review">Annual Review</option>
                <option value="adhoc">Ad-hoc</option>
              </select>
            </div>
            <div>
              <label style={labelStyle}>Data Role</label>
              <select style={selectStyle} value={formDataRole} onChange={e => setFormDataRole(e.target.value)}>
                <option value="Processor">Processor</option>
                <option value="Controller">Controller</option>
                <option value="Joint Controller">Joint Controller</option>
              </select>
            </div>
          </div>

          <div>
            <label style={labelStyle}>Client / Organisation Name</label>
            <input
              style={inputStyle}
              placeholder="Optional — your organisation name"
              value={formClientName}
              onChange={e => setFormClientName(e.target.value)}
            />
          </div>

          <div>
            <label style={labelStyle}>Additional Context</label>
            <textarea
              style={{ ...inputStyle, minHeight: '3rem', resize: 'vertical' }}
              placeholder="Optional — any additional context for the assessment..."
              value={formAdditionalContext}
              onChange={e => setFormAdditionalContext(e.target.value)}
            />
          </div>

          <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end' }}>
            <button
              style={{ ...buttonStyle, backgroundColor: 'var(--muted)', color: 'var(--text)' }}
              onClick={() => setShowForm(false)}
            >
              Cancel
            </button>
            <button
              style={{ ...buttonStyle, opacity: triggering || !formServicesUsed.trim() ? 0.5 : 1 }}
              disabled={triggering || !formServicesUsed.trim()}
              onClick={handleTrigger}
            >
              {triggering ? 'Starting...' : 'Start Assessment'}
            </button>
          </div>
        </div>
      )}

      {/* Progress */}
      {isRunning && (
        <div style={{
          ...cardStyle,
          display: 'flex',
          alignItems: 'center',
          gap: '0.75rem',
        }}>
          <div style={{
            width: '1.25rem',
            height: '1.25rem',
            border: '2px solid var(--primary)',
            borderTopColor: 'transparent',
            borderRadius: '50%',
            animation: 'spin 1s linear infinite',
          }} />
          <div>
            <div style={{ ...textStyle, fontWeight: 600 }}>
              Running DPSIA assessment...
            </div>
            <div style={mutedStyle}>
              This typically takes 2-4 minutes. Analysing vendor security posture across multiple research providers.
            </div>
          </div>
          <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
        </div>
      )}

      {/* Results */}
      {results && results.status === 'completed' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
          {/* RAG Status + Recommendation */}
          <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap', alignItems: 'center' }}>
            {results.rag_status && (
              <span style={badgeStyle(DPSIA_RAG_COLORS[results.rag_status as DPSIARAGStatus] || '#6b7280')}>
                {results.rag_status} - {DPSIA_RAG_LABELS[results.rag_status as DPSIARAGStatus] || results.rag_status}
              </span>
            )}
            {results.recommendation && (
              <span style={badgeStyle(DPSIA_RECOMMENDATION_COLORS[results.recommendation as DPSIARecommendation] || '#6b7280')}>
                {DPSIA_RECOMMENDATION_LABELS[results.recommendation as DPSIARecommendation] || results.recommendation}
              </span>
            )}
            {results.risk_score !== null && results.risk_score !== undefined && (
              <span style={{ ...textStyle, fontWeight: 600 }}>
                Risk Score: {results.risk_score} ({results.risk_level || 'N/A'})
              </span>
            )}
          </div>

          {/* Executive Summary */}
          {results.executive_summary && (
            <div style={cardStyle}>
              <div style={{ fontSize: '0.8125rem', fontWeight: 600, marginBottom: '0.375rem', color: 'var(--text)' }}>
                Executive Summary
              </div>
              <div style={{ ...textStyle, whiteSpace: 'pre-wrap' }}>
                {results.executive_summary}
              </div>
            </div>
          )}

          {/* Key Findings */}
          {results.report_json && (results.report_json as any).keyFindings?.length > 0 && (
            <div style={cardStyle}>
              <div
                style={sectionHeaderStyle}
                onClick={() => toggleSection('findings')}
              >
                <span style={{ fontSize: '0.8125rem', fontWeight: 600, color: 'var(--text)' }}>
                  Key Findings ({(results.report_json as any).keyFindings.length})
                </span>
                <span style={{ color: 'var(--muted)', fontSize: '0.75rem' }}>
                  {expandedSections.findings ? 'Collapse' : 'Expand'}
                </span>
              </div>
              {expandedSections.findings && (
                <ul style={{ margin: '0.25rem 0 0 1rem', padding: 0 }}>
                  {((results.report_json as any).keyFindings as string[]).map((f: string, i: number) => (
                    <li key={i} style={{ ...textStyle, marginBottom: '0.25rem' }}>{f}</li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {/* CIA Triad Summary */}
          {results.report_json && (
            <div style={cardStyle}>
              <div style={{ fontSize: '0.8125rem', fontWeight: 600, marginBottom: '0.5rem', color: 'var(--text)' }}>
                CIA Triad Assessment
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '0.5rem' }}>
                {[
                  { label: 'Confidentiality', key: 'confidentialityScore', controlsKey: 'confidentialityControls' },
                  { label: 'Integrity', key: 'integrityScore', controlsKey: 'integrityControls' },
                  { label: 'Availability', key: 'availabilityScore', controlsKey: 'availabilityControls' },
                ].map(({ label, key, controlsKey }) => {
                  const score = (results.report_json as any)?.[key]
                  const controls = (results.report_json as any)?.[controlsKey] || []
                  return (
                    <div key={label} style={{
                      padding: '0.5rem',
                      backgroundColor: 'var(--background)',
                      borderRadius: '6px',
                      textAlign: 'center',
                    }}>
                      <div style={{ fontSize: '0.6875rem', color: 'var(--muted)', marginBottom: '0.25rem' }}>
                        {label}
                      </div>
                      <div style={{ fontSize: '1.25rem', fontWeight: 700, color: 'var(--text)' }}>
                        {score || 'N/A'}
                      </div>
                      <div style={{ fontSize: '0.625rem', color: 'var(--muted)' }}>
                        {controls.length} controls
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* Certifications */}
          {results.report_json && (results.report_json as any).certifications?.length > 0 && (
            <div style={cardStyle}>
              <div
                style={sectionHeaderStyle}
                onClick={() => toggleSection('certifications')}
              >
                <span style={{ fontSize: '0.8125rem', fontWeight: 600, color: 'var(--text)' }}>
                  Certifications ({(results.report_json as any).certifications.length})
                </span>
                <span style={{ color: 'var(--muted)', fontSize: '0.75rem' }}>
                  {expandedSections.certifications ? 'Collapse' : 'Expand'}
                </span>
              </div>
              {expandedSections.certifications && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.375rem', marginTop: '0.25rem' }}>
                  {((results.report_json as any).certifications as any[]).map((cert: any, i: number) => (
                    <div key={i} style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                      padding: '0.375rem 0.5rem',
                      backgroundColor: 'var(--background)',
                      borderRadius: '4px',
                      fontSize: '0.75rem',
                    }}>
                      <span style={{ fontWeight: 600, color: 'var(--text)' }}>{cert.name}</span>
                      <span style={{
                        color: cert.status?.toLowerCase() === 'active' || cert.status?.toLowerCase() === 'valid'
                          ? '#22c55e' : '#f59e0b',
                        fontWeight: 600,
                      }}>
                        {cert.status}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Mandatory Actions */}
          {results.report_json && (results.report_json as any).mandatoryActions?.length > 0 && (
            <div style={cardStyle}>
              <div
                style={sectionHeaderStyle}
                onClick={() => toggleSection('actions')}
              >
                <span style={{ fontSize: '0.8125rem', fontWeight: 600, color: 'var(--text)' }}>
                  Mandatory Actions ({(results.report_json as any).mandatoryActions.length})
                </span>
                <span style={{ color: 'var(--muted)', fontSize: '0.75rem' }}>
                  {expandedSections.actions ? 'Collapse' : 'Expand'}
                </span>
              </div>
              {expandedSections.actions && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.375rem', marginTop: '0.25rem' }}>
                  {((results.report_json as any).mandatoryActions as any[]).map((action: any, i: number) => {
                    const priorityColor = action.priority === 'Critical' ? '#ef4444'
                      : action.priority === 'High' ? '#f97316'
                      : action.priority === 'Medium' ? '#eab308' : '#6b7280'
                    return (
                      <div key={i} style={{
                        padding: '0.5rem',
                        backgroundColor: 'var(--background)',
                        borderRadius: '6px',
                        borderLeft: `3px solid ${priorityColor}`,
                        fontSize: '0.75rem',
                      }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.125rem' }}>
                          <span style={{ fontWeight: 600, color: 'var(--text)' }}>
                            #{action.number || i + 1}. {action.action}
                          </span>
                          <span style={{ color: priorityColor, fontWeight: 600, fontSize: '0.6875rem' }}>
                            {action.priority}
                          </span>
                        </div>
                        <div style={{ color: 'var(--muted)', fontSize: '0.6875rem' }}>
                          Owner: {action.owner || 'TBD'} | Due: {action.dueDate || 'TBD'}
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )}

          {/* Research Sources */}
          {results.research_sources && results.research_sources.length > 0 && (
            <div style={cardStyle}>
              <div
                style={sectionHeaderStyle}
                onClick={() => toggleSection('sources')}
              >
                <span style={{ fontSize: '0.8125rem', fontWeight: 600, color: 'var(--text)' }}>
                  Research Sources ({results.research_sources.length})
                </span>
                <span style={{ color: 'var(--muted)', fontSize: '0.75rem' }}>
                  {expandedSections.sources ? 'Collapse' : 'Expand'}
                </span>
              </div>
              {expandedSections.sources && (
                <ul style={{ margin: '0.25rem 0 0 1rem', padding: 0, listStyle: 'disc' }}>
                  {results.research_sources.map((src: string, i: number) => (
                    <li key={i} style={{ ...mutedStyle, fontSize: '0.75rem', marginBottom: '0.125rem' }}>
                      {src.startsWith('http') ? (
                        <a href={src} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--primary)' }}>
                          {src}
                        </a>
                      ) : src}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {/* Footer: download + metadata */}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.5rem' }}>
            <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
              {results.report_filename && (
                <a
                  href={getDPSIADocxUrl(vendorId, results.job_id, organizationId)}
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{
                    ...buttonStyle,
                    backgroundColor: '#2563eb',
                    textDecoration: 'none',
                    fontSize: '0.75rem',
                    padding: '0.375rem 0.75rem',
                  }}
                >
                  Download DOCX
                </a>
              )}
              {results.linked_assessment_id && (
                <span style={{ ...mutedStyle, fontSize: '0.6875rem' }}>
                  Platform assessment created
                </span>
              )}
            </div>
            <div style={{ ...mutedStyle, fontSize: '0.6875rem', textAlign: 'right' }}>
              {results.processing_time_ms && (
                <span>Completed in {(results.processing_time_ms / 1000).toFixed(1)}s</span>
              )}
              {results.completed_at && (
                <span> | {new Date(results.completed_at).toLocaleDateString()}</span>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Empty state */}
      {!results && !isRunning && !showForm && !error && (
        <div style={{
          ...cardStyle,
          textAlign: 'center',
          padding: '2rem 1rem',
          color: 'var(--muted)',
        }}>
          <div style={{ fontSize: '0.875rem', marginBottom: '0.5rem' }}>
            No DPSIA assessment yet.
          </div>
          <div style={{ fontSize: '0.75rem' }}>
            Click &quot;Run DPSIA Assessment&quot; to perform a comprehensive vendor security analysis.
          </div>
        </div>
      )}
    </div>
  )
}
