/**
 * VendorAssessmentReport -- structured report viewer for a completed AI
 * vendor assessment.
 *
 * Renders from report_json (executive summary, key findings, certifications,
 * breach/CVE history, CIA controls, risk analysis, mandatory actions,
 * sources), with report_markdown available as the full-text view and the
 * download format (.md).
 */
import { useState } from 'react'
import type { VendorAssessment, VendorRAGStatus, VendorRecommendation } from '../types'
import {
  VENDOR_RAG_COLORS,
  VENDOR_RAG_LABELS,
  VENDOR_RECOMMENDATION_LABELS,
  VENDOR_RECOMMENDATION_COLORS,
} from '../types'
import VendorReportMarkdown from './VendorReportMarkdown'

interface VendorAssessmentReportProps {
  assessment: VendorAssessment
  vendorName: string
}

interface CIAControlRow {
  control?: string
  implementation?: string
  rating?: string
}

interface MandatoryAction {
  number?: number
  action?: string
  owner?: string
  dueDate?: string
  priority?: string
}

// ---------------------------------------------------------------------------
// report_json accessors (AI output — treat every field as possibly missing)
// ---------------------------------------------------------------------------

function asArray<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : []
}

function asString(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

const PRIORITY_COLORS: Record<string, string> = {
  critical: '#ef4444',
  high: '#f97316',
  medium: '#eab308',
  low: '#6b7280',
}

export default function VendorAssessmentReport({ assessment, vendorName }: VendorAssessmentReportProps) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [showFullReport, setShowFullReport] = useState(false)

  const report = (assessment.report_json || {}) as Record<string, unknown>

  const keyFindings = asArray<string>(report.keyFindings)
  const conditions = asArray<string>(report.conditions)
  const certifications = asArray<{ name?: string; status?: string; validUntil?: string; evidence?: string }>(report.certifications)
  const breachHistory = asArray<{ date?: string; description?: string; impact?: string; status?: string }>(report.breachHistory)
  const cveHistory = asArray<{ cve?: string; severity?: string; cvss?: string; description?: string; status?: string }>(report.cveHistory)
  const enforcementActions = asArray<{ authority?: string; action?: string; status?: string }>(report.enforcementActions)
  const inherentRisks = asArray<{ factor?: string; likelihood?: number; likelihoodLabel?: string; impact?: number; impactLabel?: string; score?: number }>(report.inherentRisks)
  const mandatoryActions = asArray<MandatoryAction>(report.mandatoryActions)
  const monitoringRequirements = asArray<string>(report.monitoringRequirements)
  const sources = assessment.research_sources || asArray<string>(report.primarySources)

  const ciaPillars: Array<{ label: string; score: string; controls: CIAControlRow[] }> = [
    // Prefer the platform-computed 1-5 pillar score (consistent across runs);
    // the report's own score string is free text and varies in scale.
    { label: 'Confidentiality', score: assessment.confidentiality_score != null ? `${assessment.confidentiality_score}/5` : asString(report.confidentialityScore), controls: asArray<CIAControlRow>(report.confidentialityControls) },
    { label: 'Integrity', score: assessment.integrity_score != null ? `${assessment.integrity_score}/5` : asString(report.integrityScore), controls: asArray<CIAControlRow>(report.integrityControls) },
    { label: 'Availability', score: assessment.availability_score != null ? `${assessment.availability_score}/5` : asString(report.availabilityScore), controls: asArray<CIAControlRow>(report.availabilityControls) },
  ]

  const toggle = (key: string) => setExpanded(prev => ({ ...prev, [key]: !prev[key] }))

  const handleDownload = () => {
    if (!assessment.report_markdown) return
    const blob = new Blob([assessment.report_markdown], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    const safeName = vendorName.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'vendor'
    const dateSuffix = assessment.completed_at ? assessment.completed_at.slice(0, 10) : 'report'
    link.href = url
    link.download = `${safeName}-assessment-${dateSuffix}.md`
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(url)
  }

  // ── Shared styles ─────────────────────────────────────────────────
  const cardStyle: React.CSSProperties = {
    padding: '1rem',
    backgroundColor: 'var(--card)',
    border: '1px solid var(--border)',
    borderRadius: '8px',
  }
  const headingStyle: React.CSSProperties = {
    fontSize: '0.8125rem',
    fontWeight: 600,
    color: 'var(--text)',
    margin: 0,
  }
  const textStyle: React.CSSProperties = {
    fontSize: '0.8125rem',
    color: 'var(--text)',
    lineHeight: 1.6,
  }
  const mutedStyle: React.CSSProperties = {
    ...textStyle,
    color: 'var(--muted)',
  }
  const thStyle: React.CSSProperties = {
    padding: '0.375rem 0.625rem',
    textAlign: 'left',
    fontWeight: 600,
    color: 'var(--text)',
    fontSize: '0.75rem',
  }
  const tdStyle: React.CSSProperties = {
    padding: '0.375rem 0.625rem',
    color: 'var(--text)',
    fontSize: '0.75rem',
    verticalAlign: 'top',
  }
  const tableStyle: React.CSSProperties = {
    width: '100%',
    borderCollapse: 'collapse',
  }
  const collapsibleHeader = (key: string, label: string, count?: number) => (
    <div
      style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        cursor: 'pointer',
        userSelect: 'none',
      }}
      onClick={() => toggle(key)}
    >
      <span style={headingStyle}>
        {label}{count != null ? ` (${count})` : ''}
      </span>
      <span style={{ color: 'var(--muted)', fontSize: '0.75rem' }}>
        {expanded[key] ? 'Collapse' : 'Expand'}
      </span>
    </div>
  )
  const badgeStyle = (color: string): React.CSSProperties => ({
    display: 'inline-block',
    padding: '0.25rem 0.75rem',
    borderRadius: '9999px',
    fontSize: '0.75rem',
    fontWeight: 700,
    color: '#fff',
    backgroundColor: color,
  })

  const ragStatus = assessment.rag_status as VendorRAGStatus | null | undefined
  const recommendation = assessment.recommendation as VendorRecommendation | null | undefined

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
      {/* Outcome badges + report actions */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.5rem' }}>
        <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'center' }}>
          {ragStatus && (
            <span style={badgeStyle(VENDOR_RAG_COLORS[ragStatus] || '#6b7280')}>
              {ragStatus} — {VENDOR_RAG_LABELS[ragStatus] || ragStatus}
            </span>
          )}
          {recommendation && (
            <span style={badgeStyle(VENDOR_RECOMMENDATION_COLORS[recommendation] || '#6b7280')}>
              {VENDOR_RECOMMENDATION_LABELS[recommendation] || recommendation}
            </span>
          )}
          {assessment.final_risk_score != null && (
            <span style={{ ...textStyle, fontWeight: 600 }}>
              Residual risk: {assessment.final_risk_score}
              {assessment.risk_level ? ` (${assessment.risk_level.charAt(0).toUpperCase() + assessment.risk_level.slice(1)})` : ''}
            </span>
          )}
        </div>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          {assessment.report_markdown && (
            <>
              <button
                onClick={() => setShowFullReport(v => !v)}
                style={{
                  padding: '0.375rem 0.75rem',
                  fontSize: '0.75rem',
                  fontWeight: 600,
                  border: '1px solid var(--border)',
                  borderRadius: '6px',
                  cursor: 'pointer',
                  color: 'var(--text)',
                  backgroundColor: 'var(--secondary)',
                }}
              >
                {showFullReport ? 'Structured view' : 'Full report'}
              </button>
              <button
                onClick={handleDownload}
                style={{
                  padding: '0.375rem 0.75rem',
                  fontSize: '0.75rem',
                  fontWeight: 600,
                  border: 'none',
                  borderRadius: '6px',
                  cursor: 'pointer',
                  color: '#fff',
                  backgroundColor: 'var(--primary)',
                }}
              >
                Download report (.md)
              </button>
            </>
          )}
        </div>
      </div>

      {/* Full-text markdown view */}
      {showFullReport && assessment.report_markdown ? (
        <div style={cardStyle}>
          <VendorReportMarkdown content={assessment.report_markdown} />
        </div>
      ) : (
        <>
          {/* Executive summary */}
          {assessment.executive_summary && (
            <div style={cardStyle}>
              <div style={{ ...headingStyle, marginBottom: '0.375rem' }}>Executive summary</div>
              <div style={{ ...textStyle, whiteSpace: 'pre-wrap' }}>{assessment.executive_summary}</div>
            </div>
          )}

          {/* Key findings */}
          {keyFindings.length > 0 && (
            <div style={cardStyle}>
              <div style={{ ...headingStyle, marginBottom: '0.375rem' }}>Key findings</div>
              <ul style={{ margin: '0 0 0 1rem', padding: 0 }}>
                {keyFindings.map((f, i) => (
                  <li key={i} style={{ ...textStyle, marginBottom: '0.25rem' }}>{f}</li>
                ))}
              </ul>
            </div>
          )}

          {/* Risk analysis: inherent vs residual */}
          <div style={cardStyle}>
            <div style={{ ...headingStyle, marginBottom: '0.625rem' }}>Risk analysis</div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: '0.5rem' }}>
              <div style={{ padding: '0.625rem', backgroundColor: 'var(--background)', borderRadius: '6px', textAlign: 'center' }}>
                <div style={{ fontSize: '0.6875rem', color: 'var(--muted)', marginBottom: '0.25rem' }}>Inherent risk</div>
                <div style={{ fontSize: '1.25rem', fontWeight: 700, color: 'var(--text)' }}>
                  {assessment.inherent_risk_score ?? '—'}
                </div>
                <div style={{ fontSize: '0.6875rem', color: 'var(--muted)' }}>
                  {assessment.inherent_risk_level ? assessment.inherent_risk_level.charAt(0).toUpperCase() + assessment.inherent_risk_level.slice(1) : ''}
                </div>
              </div>
              <div style={{ padding: '0.625rem', backgroundColor: 'var(--background)', borderRadius: '6px', textAlign: 'center' }}>
                <div style={{ fontSize: '0.6875rem', color: 'var(--muted)', marginBottom: '0.25rem' }}>Control effectiveness</div>
                <div style={{ fontSize: '1.25rem', fontWeight: 700, color: 'var(--text)' }}>
                  {assessment.control_effectiveness_pct != null ? `${assessment.control_effectiveness_pct}%` : '—'}
                </div>
                <div style={{ fontSize: '0.6875rem', color: 'var(--muted)' }}>
                  {asString(report.controlEffectiveness)}
                </div>
              </div>
              <div style={{ padding: '0.625rem', backgroundColor: 'var(--background)', borderRadius: '6px', textAlign: 'center' }}>
                <div style={{ fontSize: '0.6875rem', color: 'var(--muted)', marginBottom: '0.25rem' }}>Residual risk</div>
                <div style={{ fontSize: '1.25rem', fontWeight: 700, color: 'var(--text)' }}>
                  {assessment.final_risk_score ?? '—'}
                </div>
                <div style={{ fontSize: '0.6875rem', color: 'var(--muted)' }}>
                  {assessment.risk_level ? assessment.risk_level.charAt(0).toUpperCase() + assessment.risk_level.slice(1) : ''}
                </div>
              </div>
            </div>
            {inherentRisks.length > 0 && (
              <div style={{ overflowX: 'auto', marginTop: '0.625rem' }}>
                <table style={tableStyle}>
                  <thead>
                    <tr style={{ borderBottom: '2px solid var(--border)' }}>
                      <th style={thStyle}>Risk factor</th>
                      <th style={thStyle}>Likelihood</th>
                      <th style={thStyle}>Impact</th>
                      <th style={thStyle}>Score</th>
                    </tr>
                  </thead>
                  <tbody>
                    {inherentRisks.map((r, i) => (
                      <tr key={i} style={{ borderBottom: '1px solid var(--border)' }}>
                        <td style={tdStyle}>{r.factor || '—'}</td>
                        <td style={tdStyle}>{r.likelihood != null ? `${r.likelihood}${r.likelihoodLabel ? ` (${r.likelihoodLabel})` : ''}` : '—'}</td>
                        <td style={tdStyle}>{r.impact != null ? `${r.impact}${r.impactLabel ? ` (${r.impactLabel})` : ''}` : '—'}</td>
                        <td style={{ ...tdStyle, fontWeight: 600 }}>{r.score ?? '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* CIA controls */}
          <div style={cardStyle}>
            <div style={{ ...headingStyle, marginBottom: '0.625rem' }}>CIA triad controls</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
              {ciaPillars.map(({ label, score, controls }) => (
                <div key={label}>
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.5rem', marginBottom: '0.25rem' }}>
                    <span style={{ fontSize: '0.75rem', fontWeight: 600, color: 'var(--text)' }}>{label}</span>
                    {score && <span style={{ fontSize: '0.75rem', color: 'var(--muted)' }}>Score: {score}</span>}
                  </div>
                  {controls.length > 0 ? (
                    <div style={{ overflowX: 'auto' }}>
                      <table style={tableStyle}>
                        <thead>
                          <tr style={{ borderBottom: '2px solid var(--border)' }}>
                            <th style={thStyle}>Control</th>
                            <th style={thStyle}>Implementation</th>
                            <th style={thStyle}>Rating</th>
                          </tr>
                        </thead>
                        <tbody>
                          {controls.map((c, i) => (
                            <tr key={i} style={{ borderBottom: '1px solid var(--border)' }}>
                              <td style={{ ...tdStyle, fontWeight: 500 }}>{c.control || '—'}</td>
                              <td style={tdStyle}>{c.implementation || '—'}</td>
                              <td style={tdStyle}>{c.rating || '—'}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <div style={mutedStyle}>No controls documented.</div>
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* Certifications */}
          {certifications.length > 0 && (
            <div style={cardStyle}>
              <div style={{ ...headingStyle, marginBottom: '0.375rem' }}>Certifications ({certifications.length})</div>
              <div style={{ overflowX: 'auto' }}>
                <table style={tableStyle}>
                  <thead>
                    <tr style={{ borderBottom: '2px solid var(--border)' }}>
                      <th style={thStyle}>Certification</th>
                      <th style={thStyle}>Status</th>
                      <th style={thStyle}>Valid until</th>
                      <th style={thStyle}>Evidence</th>
                    </tr>
                  </thead>
                  <tbody>
                    {certifications.map((cert, i) => {
                      const status = (cert.status || '').toLowerCase()
                      const ok = status === 'active' || status === 'valid' || status === 'certified' || status === 'current'
                      return (
                        <tr key={i} style={{ borderBottom: '1px solid var(--border)' }}>
                          <td style={{ ...tdStyle, fontWeight: 500 }}>{cert.name || '—'}</td>
                          <td style={{ ...tdStyle, color: ok ? '#22c55e' : '#f59e0b', fontWeight: 600 }}>{cert.status || '—'}</td>
                          <td style={tdStyle}>{cert.validUntil || '—'}</td>
                          <td style={tdStyle}>{cert.evidence || '—'}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
              {asString(report.certificationNotes) && (
                <div style={{ ...mutedStyle, marginTop: '0.375rem' }}>{asString(report.certificationNotes)}</div>
              )}
            </div>
          )}

          {/* Breach, CVE and regulatory history */}
          {(breachHistory.length > 0 || cveHistory.length > 0 || enforcementActions.length > 0) && (
            <div style={cardStyle}>
              {collapsibleHeader('history', 'Breach, CVE & regulatory history', breachHistory.length + cveHistory.length + enforcementActions.length)}
              {expanded.history && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', marginTop: '0.625rem' }}>
                  {breachHistory.length > 0 && (
                    <div>
                      <div style={{ fontSize: '0.75rem', fontWeight: 600, color: 'var(--text)', marginBottom: '0.25rem' }}>Breach history</div>
                      <div style={{ overflowX: 'auto' }}>
                        <table style={tableStyle}>
                          <thead>
                            <tr style={{ borderBottom: '2px solid var(--border)' }}>
                              <th style={thStyle}>Date</th>
                              <th style={thStyle}>Description</th>
                              <th style={thStyle}>Impact</th>
                              <th style={thStyle}>Status</th>
                            </tr>
                          </thead>
                          <tbody>
                            {breachHistory.map((b, i) => (
                              <tr key={i} style={{ borderBottom: '1px solid var(--border)' }}>
                                <td style={tdStyle}>{b.date || '—'}</td>
                                <td style={tdStyle}>{b.description || '—'}</td>
                                <td style={tdStyle}>{b.impact || '—'}</td>
                                <td style={tdStyle}>{b.status || '—'}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}
                  {cveHistory.length > 0 && (
                    <div>
                      <div style={{ fontSize: '0.75rem', fontWeight: 600, color: 'var(--text)', marginBottom: '0.25rem' }}>CVE history</div>
                      <div style={{ overflowX: 'auto' }}>
                        <table style={tableStyle}>
                          <thead>
                            <tr style={{ borderBottom: '2px solid var(--border)' }}>
                              <th style={thStyle}>CVE</th>
                              <th style={thStyle}>Severity</th>
                              <th style={thStyle}>CVSS</th>
                              <th style={thStyle}>Description</th>
                              <th style={thStyle}>Status</th>
                            </tr>
                          </thead>
                          <tbody>
                            {cveHistory.map((c, i) => (
                              <tr key={i} style={{ borderBottom: '1px solid var(--border)' }}>
                                <td style={{ ...tdStyle, fontWeight: 500 }}>{c.cve || '—'}</td>
                                <td style={tdStyle}>{c.severity || '—'}</td>
                                <td style={tdStyle}>{c.cvss || '—'}</td>
                                <td style={tdStyle}>{c.description || '—'}</td>
                                <td style={tdStyle}>{c.status || '—'}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}
                  {enforcementActions.length > 0 && (
                    <div>
                      <div style={{ fontSize: '0.75rem', fontWeight: 600, color: 'var(--text)', marginBottom: '0.25rem' }}>Regulatory enforcement</div>
                      <div style={{ overflowX: 'auto' }}>
                        <table style={tableStyle}>
                          <thead>
                            <tr style={{ borderBottom: '2px solid var(--border)' }}>
                              <th style={thStyle}>Authority</th>
                              <th style={thStyle}>Action</th>
                              <th style={thStyle}>Status</th>
                            </tr>
                          </thead>
                          <tbody>
                            {enforcementActions.map((a, i) => (
                              <tr key={i} style={{ borderBottom: '1px solid var(--border)' }}>
                                <td style={tdStyle}>{a.authority || '—'}</td>
                                <td style={tdStyle}>{a.action || '—'}</td>
                                <td style={tdStyle}>{a.status || '—'}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Conditions (for conditional approval) */}
          {conditions.length > 0 && (
            <div style={cardStyle}>
              <div style={{ ...headingStyle, marginBottom: '0.375rem' }}>Conditions for use</div>
              <ul style={{ margin: '0 0 0 1rem', padding: 0 }}>
                {conditions.map((c, i) => (
                  <li key={i} style={{ ...textStyle, marginBottom: '0.25rem' }}>{c}</li>
                ))}
              </ul>
            </div>
          )}

          {/* Mandatory actions */}
          {mandatoryActions.length > 0 && (
            <div style={cardStyle}>
              <div style={{ ...headingStyle, marginBottom: '0.375rem' }}>Mandatory actions ({mandatoryActions.length})</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.375rem' }}>
                {mandatoryActions.map((action, i) => {
                  const priorityColor = PRIORITY_COLORS[(action.priority || '').toLowerCase()] || '#6b7280'
                  return (
                    <div key={i} style={{
                      padding: '0.5rem 0.625rem',
                      backgroundColor: 'var(--background)',
                      borderRadius: '6px',
                      borderLeft: `3px solid ${priorityColor}`,
                      fontSize: '0.75rem',
                    }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '0.5rem', marginBottom: '0.125rem' }}>
                        <span style={{ fontWeight: 600, color: 'var(--text)' }}>
                          {action.number != null ? `${action.number}. ` : ''}{action.action}
                        </span>
                        {action.priority && (
                          <span style={{ color: priorityColor, fontWeight: 600, fontSize: '0.6875rem', flexShrink: 0 }}>
                            {action.priority}
                          </span>
                        )}
                      </div>
                      <div style={{ color: 'var(--muted)', fontSize: '0.6875rem' }}>
                        Owner: {action.owner || 'TBC'} · Due: {action.dueDate || 'TBC'}
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* Monitoring requirements */}
          {monitoringRequirements.length > 0 && (
            <div style={cardStyle}>
              {collapsibleHeader('monitoring', 'Ongoing monitoring', monitoringRequirements.length)}
              {expanded.monitoring && (
                <ul style={{ margin: '0.375rem 0 0 1rem', padding: 0 }}>
                  {monitoringRequirements.map((m, i) => (
                    <li key={i} style={{ ...textStyle, marginBottom: '0.25rem' }}>{m}</li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {/* Sources */}
          {sources.length > 0 && (
            <div style={cardStyle}>
              {collapsibleHeader('sources', 'Research sources', sources.length)}
              {expanded.sources && (
                <ul style={{ margin: '0.375rem 0 0 1rem', padding: 0, listStyle: 'disc' }}>
                  {sources.map((src, i) => (
                    <li key={i} style={{ ...mutedStyle, fontSize: '0.75rem', marginBottom: '0.125rem', wordBreak: 'break-all' }}>
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
        </>
      )}

      {/* Footer metadata */}
      <div style={{ ...mutedStyle, fontSize: '0.6875rem', textAlign: 'right' }}>
        {assessment.processing_time_ms != null && (
          <span>Completed in {(assessment.processing_time_ms / 1000).toFixed(1)}s</span>
        )}
        {assessment.completed_at && (
          <span> · {new Date(assessment.completed_at).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })}</span>
        )}
      </div>
    </div>
  )
}
