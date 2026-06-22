/**
 * VendorReportsPanel -- Report generation and management for a vendor (Issue #61).
 *
 * Lists existing reports, allows generating new ones, previewing markdown
 * content, exporting in multiple formats, and emailing reports.
 */
import { useState, useEffect, useCallback } from 'react'
import DOMPurify from 'dompurify'
import type { VendorReport } from '../types'
import { getRiskLevelColor } from '../types'
import type { RiskLevel } from '../types'
import {
  getVendorReports,
  generateVendorReport,
  exportVendorReport,
  emailVendorReport,
  deleteVendorReport,
} from '../data/apiClient'

interface VendorReportsPanelProps {
  organizationId: string
  vendorId: string
}

type ExportFormat = 'pdf' | 'docx' | 'json' | 'markdown'

const EXPORT_LABELS: Record<ExportFormat, string> = {
  pdf: 'PDF',
  docx: 'DOCX',
  json: 'JSON',
  markdown: 'Markdown',
}

/** Map string risk level to typed RiskLevel for colour lookup */
function toRiskLevel(level: string | null | undefined): RiskLevel | null {
  if (!level) return null
  const normalised = level.toLowerCase()
  if (normalised === 'low' || normalised === 'medium' || normalised === 'high' || normalised === 'critical') {
    return normalised as RiskLevel
  }
  return null
}

export default function VendorReportsPanel({
  organizationId,
  vendorId,
}: VendorReportsPanelProps) {
  const [reports, setReports] = useState<VendorReport[]>([])
  const [loading, setLoading] = useState(true)
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Modal states
  const [previewReport, setPreviewReport] = useState<VendorReport | null>(null)
  const [emailDialogReport, setEmailDialogReport] = useState<VendorReport | null>(null)
  const [emailTo, setEmailTo] = useState('')
  const [emailName, setEmailName] = useState('')
  const [emailing, setEmailing] = useState(false)
  const [emailSuccess, setEmailSuccess] = useState<string | null>(null)

  // Export dropdown state
  const [exportOpenId, setExportOpenId] = useState<string | null>(null)

  // --------------------------------------------------
  // Load reports
  // --------------------------------------------------
  const loadReports = useCallback(async () => {
    setLoading(true)
    try {
      const data = await getVendorReports(vendorId, organizationId)
      setReports(data)
      setError(null)
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to load reports'
      setError(message)
    } finally {
      setLoading(false)
    }
  }, [vendorId, organizationId])

  useEffect(() => {
    loadReports()
  }, [loadReports])

  // Close export dropdown when clicking outside
  useEffect(() => {
    if (!exportOpenId) return
    const handler = () => setExportOpenId(null)
    document.addEventListener('click', handler)
    return () => document.removeEventListener('click', handler)
  }, [exportOpenId])

  // --------------------------------------------------
  // Generate report
  // --------------------------------------------------
  const handleGenerate = async () => {
    setGenerating(true)
    setError(null)
    try {
      await generateVendorReport(vendorId, organizationId)
      await loadReports()
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to generate report'
      setError(message)
    } finally {
      setGenerating(false)
    }
  }

  // --------------------------------------------------
  // Export report
  // --------------------------------------------------
  const handleExport = async (report: VendorReport, format: ExportFormat) => {
    setExportOpenId(null)
    try {
      const blob = await exportVendorReport(vendorId, report.id, format, organizationId)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      const ext = format === 'markdown' ? 'md' : format
      a.download = `${report.title.replace(/[^a-zA-Z0-9-_ ]/g, '')}.${ext}`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to export report'
      setError(message)
    }
  }

  // --------------------------------------------------
  // Email report
  // --------------------------------------------------
  const handleEmail = async () => {
    if (!emailDialogReport || !emailTo.trim()) return
    setEmailing(true)
    setEmailSuccess(null)
    try {
      const resp = await emailVendorReport(
        vendorId,
        emailDialogReport.id,
        emailTo.trim(),
        emailName.trim() || undefined,
        organizationId
      )
      setEmailSuccess(resp.message || 'Report sent successfully')
      setTimeout(() => {
        setEmailDialogReport(null)
        setEmailTo('')
        setEmailName('')
        setEmailSuccess(null)
      }, 2000)
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to send email'
      setError(message)
    } finally {
      setEmailing(false)
    }
  }

  // --------------------------------------------------
  // Delete report
  // --------------------------------------------------
  const handleDelete = async (report: VendorReport) => {
    if (!confirm(`Delete report "${report.title}"? This cannot be undone.`)) return
    try {
      await deleteVendorReport(vendorId, report.id, organizationId)
      await loadReports()
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to delete report'
      setError(message)
    }
  }

  // --------------------------------------------------
  // Helpers
  // --------------------------------------------------
  const formatDate = (dateStr: string): string => {
    try {
      return new Date(dateStr).toLocaleDateString('en-GB', {
        day: 'numeric',
        month: 'short',
        year: 'numeric',
      })
    } catch {
      return dateStr
    }
  }

  const formatDateTime = (dateStr: string): string => {
    try {
      return new Date(dateStr).toLocaleString('en-GB', {
        day: 'numeric',
        month: 'short',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      })
    } catch {
      return dateStr
    }
  }

  // --------------------------------------------------
  // Render
  // --------------------------------------------------
  return (
    <div>
      {/* Section header */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: '1rem',
        }}
      >
        <h3 style={{ margin: 0, fontSize: '1rem', fontWeight: 600, color: 'var(--text)' }}>
          Reports ({reports.length})
        </h3>
        <button
          onClick={handleGenerate}
          disabled={generating}
          style={{
            padding: '0.5rem 1rem',
            borderRadius: '6px',
            border: 'none',
            backgroundColor: generating ? 'var(--muted)' : 'var(--primary)',
            color: '#ffffff',
            cursor: generating ? 'not-allowed' : 'pointer',
            fontSize: '0.875rem',
            fontWeight: 500,
          }}
        >
          {generating ? 'Generating...' : 'Generate Report'}
        </button>
      </div>

      {/* Error banner */}
      {error && (
        <div
          style={{
            padding: '0.75rem 1rem',
            backgroundColor: 'var(--destructive-bg, #fef2f2)',
            border: '1px solid var(--destructive-border, #fecaca)',
            borderRadius: '8px',
            color: 'var(--destructive)',
            marginBottom: '1rem',
            fontSize: '0.875rem',
          }}
        >
          {error}
          <button
            onClick={() => setError(null)}
            style={{
              marginLeft: '0.5rem',
              background: 'none',
              border: 'none',
              color: 'var(--destructive)',
              cursor: 'pointer',
              fontWeight: 600,
            }}
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Loading */}
      {loading && reports.length === 0 && (
        <div style={{ textAlign: 'center', padding: '1rem' }}>
          <div
            style={{
              display: 'inline-block',
              width: '1.5rem',
              height: '1.5rem',
              border: '2px solid var(--border)',
              borderTopColor: 'var(--primary)',
              borderRadius: '50%',
              animation: 'spin 0.8s linear infinite',
            }}
          />
          <p style={{ color: 'var(--muted)', fontSize: '0.8rem', marginTop: '0.5rem' }}>
            Loading reports...
          </p>
          <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
        </div>
      )}

      {/* Empty state */}
      {!loading && reports.length === 0 && !error && (
        <div
          style={{
            padding: '2rem',
            textAlign: 'center',
            color: 'var(--muted)',
            backgroundColor: 'var(--card)',
            borderRadius: '8px',
            border: '1px dashed var(--border)',
            fontSize: '0.875rem',
          }}
        >
          No reports generated yet. Click "Generate Report" to create a comprehensive
          vendor assessment report.
        </div>
      )}

      {/* Reports table */}
      {reports.length > 0 && (
        <div style={{ overflowX: 'auto' }}>
          <table
            style={{
              width: '100%',
              borderCollapse: 'collapse',
              fontSize: '0.875rem',
            }}
          >
            <thead>
              <tr
                style={{
                  borderBottom: '2px solid var(--border)',
                  textAlign: 'left',
                }}
              >
                <th style={{ padding: '0.5rem 0.75rem', fontWeight: 600, color: 'var(--text)' }}>Title</th>
                <th style={{ padding: '0.5rem 0.75rem', fontWeight: 600, color: 'var(--text)' }}>Date</th>
                <th style={{ padding: '0.5rem 0.75rem', fontWeight: 600, color: 'var(--text)' }}>Version</th>
                <th style={{ padding: '0.5rem 0.75rem', fontWeight: 600, color: 'var(--text)' }}>Risk Score</th>
                <th style={{ padding: '0.5rem 0.75rem', fontWeight: 600, color: 'var(--text)' }}>Recommendation</th>
                <th style={{ padding: '0.5rem 0.75rem', fontWeight: 600, color: 'var(--text)', textAlign: 'right' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {reports.map((report) => {
                const level = toRiskLevel(report.risk_level)
                return (
                  <tr
                    key={report.id}
                    style={{ borderBottom: '1px solid var(--border)' }}
                  >
                    <td style={{ padding: '0.5rem 0.75rem', color: 'var(--text)', fontWeight: 500 }}>
                      {report.title}
                    </td>
                    <td style={{ padding: '0.5rem 0.75rem', color: 'var(--text)' }}>
                      {formatDate(report.created_at)}
                    </td>
                    <td style={{ padding: '0.5rem 0.75rem', color: 'var(--text)', fontVariantNumeric: 'tabular-nums' }}>
                      v{report.version}
                    </td>
                    <td style={{ padding: '0.5rem 0.75rem' }}>
                      {report.risk_score != null ? (
                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                          <span style={{ fontWeight: 600, color: 'var(--text)' }}>
                            {report.risk_score}
                          </span>
                          {level && (
                            <span
                              style={{
                                display: 'inline-block',
                                padding: '1px 8px',
                                borderRadius: '9999px',
                                fontSize: '0.7rem',
                                fontWeight: 600,
                                color: '#ffffff',
                                backgroundColor: getRiskLevelColor(level),
                              }}
                            >
                              {level.charAt(0).toUpperCase() + level.slice(1)}
                            </span>
                          )}
                        </div>
                      ) : (
                        <span style={{ color: 'var(--muted)' }}>-</span>
                      )}
                    </td>
                    <td style={{ padding: '0.5rem 0.75rem', color: 'var(--text)', maxWidth: '200px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {report.recommendation || '-'}
                    </td>
                    <td style={{ padding: '0.5rem 0.75rem', textAlign: 'right' }}>
                      <div style={{ display: 'flex', gap: '0.375rem', justifyContent: 'flex-end', flexWrap: 'wrap' }}>
                        {/* View */}
                        <button
                          onClick={() => setPreviewReport(report)}
                          style={{
                            padding: '0.25rem 0.625rem',
                            borderRadius: '4px',
                            border: '1px solid var(--border)',
                            backgroundColor: 'var(--card)',
                            color: 'var(--text)',
                            cursor: 'pointer',
                            fontSize: '0.75rem',
                            fontWeight: 500,
                          }}
                        >
                          View
                        </button>

                        {/* Export dropdown */}
                        <div style={{ position: 'relative' }}>
                          <button
                            onClick={(e) => {
                              e.stopPropagation()
                              setExportOpenId(exportOpenId === report.id ? null : report.id)
                            }}
                            style={{
                              padding: '0.25rem 0.625rem',
                              borderRadius: '4px',
                              border: '1px solid var(--border)',
                              backgroundColor: 'var(--card)',
                              color: 'var(--text)',
                              cursor: 'pointer',
                              fontSize: '0.75rem',
                              fontWeight: 500,
                            }}
                          >
                            Export
                          </button>
                          {exportOpenId === report.id && (
                            <div
                              style={{
                                position: 'absolute',
                                right: 0,
                                top: '100%',
                                marginTop: '4px',
                                backgroundColor: 'var(--card)',
                                border: '1px solid var(--border)',
                                borderRadius: '6px',
                                boxShadow: '0 4px 12px rgba(0,0,0,0.1)',
                                zIndex: 50,
                                minWidth: '120px',
                                overflow: 'hidden',
                              }}
                              onClick={(e) => e.stopPropagation()}
                            >
                              {(['pdf', 'docx', 'json', 'markdown'] as ExportFormat[]).map((fmt) => (
                                <button
                                  key={fmt}
                                  onClick={() => handleExport(report, fmt)}
                                  style={{
                                    display: 'block',
                                    width: '100%',
                                    padding: '0.5rem 0.75rem',
                                    border: 'none',
                                    backgroundColor: 'transparent',
                                    color: 'var(--text)',
                                    cursor: 'pointer',
                                    fontSize: '0.8rem',
                                    textAlign: 'left',
                                  }}
                                  onMouseEnter={(e) => {
                                    (e.target as HTMLElement).style.backgroundColor = 'var(--secondary)'
                                  }}
                                  onMouseLeave={(e) => {
                                    (e.target as HTMLElement).style.backgroundColor = 'transparent'
                                  }}
                                >
                                  {EXPORT_LABELS[fmt]}
                                </button>
                              ))}
                            </div>
                          )}
                        </div>

                        {/* Email */}
                        <button
                          onClick={() => {
                            setEmailDialogReport(report)
                            setEmailTo('')
                            setEmailName('')
                            setEmailSuccess(null)
                          }}
                          style={{
                            padding: '0.25rem 0.625rem',
                            borderRadius: '4px',
                            border: '1px solid var(--border)',
                            backgroundColor: 'var(--card)',
                            color: 'var(--text)',
                            cursor: 'pointer',
                            fontSize: '0.75rem',
                            fontWeight: 500,
                          }}
                        >
                          Email
                        </button>

                        {/* Delete */}
                        <button
                          onClick={() => handleDelete(report)}
                          style={{
                            padding: '0.25rem 0.625rem',
                            borderRadius: '4px',
                            border: '1px solid var(--destructive-border, #fecaca)',
                            backgroundColor: 'var(--destructive-bg, #fef2f2)',
                            color: 'var(--destructive, #991b1b)',
                            cursor: 'pointer',
                            fontSize: '0.75rem',
                            fontWeight: 500,
                          }}
                        >
                          Delete
                        </button>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* ================================================================
          Report Preview Modal
          ================================================================ */}
      {previewReport && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'rgba(0,0,0,0.5)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1000,
            padding: '2rem',
          }}
          onClick={() => setPreviewReport(null)}
        >
          <div
            style={{
              backgroundColor: 'var(--card)',
              borderRadius: '12px',
              border: '1px solid var(--border)',
              width: '100%',
              maxWidth: '800px',
              maxHeight: '80vh',
              display: 'flex',
              flexDirection: 'column',
              overflow: 'hidden',
            }}
            onClick={(e) => e.stopPropagation()}
          >
            {/* Modal header */}
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                padding: '1rem 1.25rem',
                borderBottom: '1px solid var(--border)',
              }}
            >
              <div>
                <h3 style={{ margin: 0, fontSize: '1.125rem', fontWeight: 600, color: 'var(--text)' }}>
                  {previewReport.title}
                </h3>
                <div style={{ fontSize: '0.75rem', color: 'var(--muted)', marginTop: '0.25rem' }}>
                  Generated {formatDateTime(previewReport.created_at)} | v{previewReport.version}
                  {previewReport.generated_by && (
                    <> | By {previewReport.generated_by.display_name || previewReport.generated_by.email}</>
                  )}
                </div>
              </div>
              <button
                onClick={() => setPreviewReport(null)}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  width: '2rem',
                  height: '2rem',
                  background: 'none',
                  border: '1px solid var(--border)',
                  borderRadius: '6px',
                  cursor: 'pointer',
                  fontSize: '1rem',
                  color: 'var(--text)',
                }}
              >
                &times;
              </button>
            </div>

            {/* Risk summary bar */}
            {(previewReport.risk_score != null || previewReport.recommendation) && (
              <div
                style={{
                  padding: '0.75rem 1.25rem',
                  backgroundColor: 'var(--secondary)',
                  borderBottom: '1px solid var(--border)',
                  display: 'flex',
                  gap: '1rem',
                  alignItems: 'center',
                  flexWrap: 'wrap',
                  fontSize: '0.875rem',
                }}
              >
                {previewReport.risk_score != null && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                    <span style={{ fontWeight: 500, color: 'var(--muted)' }}>Risk Score:</span>
                    <span style={{ fontWeight: 700, color: 'var(--text)' }}>{previewReport.risk_score}</span>
                    {toRiskLevel(previewReport.risk_level) && (
                      <span
                        style={{
                          display: 'inline-block',
                          padding: '2px 10px',
                          borderRadius: '9999px',
                          fontSize: '0.7rem',
                          fontWeight: 600,
                          color: '#ffffff',
                          backgroundColor: getRiskLevelColor(toRiskLevel(previewReport.risk_level)!),
                        }}
                      >
                        {previewReport.risk_level!.charAt(0).toUpperCase() + previewReport.risk_level!.slice(1)}
                      </span>
                    )}
                  </div>
                )}
                {previewReport.recommendation && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flex: 1 }}>
                    <span style={{ fontWeight: 500, color: 'var(--muted)' }}>Recommendation:</span>
                    <span style={{ color: 'var(--text)' }}>{previewReport.recommendation}</span>
                  </div>
                )}
              </div>
            )}

            {/* Markdown content with table of contents */}
            <div
              style={{
                flex: 1,
                overflow: 'auto',
                padding: '1.25rem',
              }}
            >
              <ReportMarkdownRenderer content={previewReport.content_markdown} />
            </div>
          </div>
        </div>
      )}

      {/* ================================================================
          Email Dialog Modal
          ================================================================ */}
      {emailDialogReport && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'rgba(0,0,0,0.5)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1000,
            padding: '2rem',
          }}
          onClick={() => setEmailDialogReport(null)}
        >
          <div
            style={{
              backgroundColor: 'var(--card)',
              borderRadius: '12px',
              border: '1px solid var(--border)',
              width: '100%',
              maxWidth: '440px',
              overflow: 'hidden',
            }}
            onClick={(e) => e.stopPropagation()}
          >
            {/* Modal header */}
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                padding: '1rem 1.25rem',
                borderBottom: '1px solid var(--border)',
              }}
            >
              <h3 style={{ margin: 0, fontSize: '1rem', fontWeight: 600, color: 'var(--text)' }}>
                Email Report
              </h3>
              <button
                onClick={() => setEmailDialogReport(null)}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  width: '2rem',
                  height: '2rem',
                  background: 'none',
                  border: '1px solid var(--border)',
                  borderRadius: '6px',
                  cursor: 'pointer',
                  fontSize: '1rem',
                  color: 'var(--text)',
                }}
              >
                &times;
              </button>
            </div>

            {/* Form */}
            <div style={{ padding: '1.25rem' }}>
              <div style={{ marginBottom: '0.75rem', fontSize: '0.8rem', color: 'var(--muted)' }}>
                Sending: <strong style={{ color: 'var(--text)' }}>{emailDialogReport.title}</strong>
              </div>

              {/* Email field */}
              <div style={{ marginBottom: '0.75rem' }}>
                <label
                  htmlFor="email-to"
                  style={{
                    display: 'block',
                    fontSize: '0.8rem',
                    fontWeight: 500,
                    color: 'var(--text)',
                    marginBottom: '0.25rem',
                  }}
                >
                  Recipient Email <span style={{ color: 'var(--destructive)' }}>*</span>
                </label>
                <input
                  id="email-to"
                  type="email"
                  value={emailTo}
                  onChange={(e) => setEmailTo(e.target.value)}
                  placeholder="recipient@example.com"
                  style={{
                    width: '100%',
                    padding: '0.5rem 0.75rem',
                    borderRadius: '6px',
                    border: '1px solid var(--border)',
                    backgroundColor: 'var(--card)',
                    color: 'var(--text)',
                    fontSize: '0.875rem',
                    outline: 'none',
                    boxSizing: 'border-box',
                  }}
                />
              </div>

              {/* Name field */}
              <div style={{ marginBottom: '1rem' }}>
                <label
                  htmlFor="email-name"
                  style={{
                    display: 'block',
                    fontSize: '0.8rem',
                    fontWeight: 500,
                    color: 'var(--text)',
                    marginBottom: '0.25rem',
                  }}
                >
                  Recipient Name (optional)
                </label>
                <input
                  id="email-name"
                  type="text"
                  value={emailName}
                  onChange={(e) => setEmailName(e.target.value)}
                  placeholder="Jane Smith"
                  style={{
                    width: '100%',
                    padding: '0.5rem 0.75rem',
                    borderRadius: '6px',
                    border: '1px solid var(--border)',
                    backgroundColor: 'var(--card)',
                    color: 'var(--text)',
                    fontSize: '0.875rem',
                    outline: 'none',
                    boxSizing: 'border-box',
                  }}
                />
              </div>

              {/* Success message */}
              {emailSuccess && (
                <div
                  style={{
                    padding: '0.5rem 0.75rem',
                    backgroundColor: '#dcfce7',
                    borderRadius: '6px',
                    fontSize: '0.8rem',
                    color: '#166534',
                    marginBottom: '0.75rem',
                  }}
                >
                  {emailSuccess}
                </div>
              )}

              {/* Actions */}
              <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end' }}>
                <button
                  onClick={() => setEmailDialogReport(null)}
                  style={{
                    padding: '0.5rem 1rem',
                    borderRadius: '6px',
                    border: '1px solid var(--border)',
                    backgroundColor: 'var(--card)',
                    color: 'var(--text)',
                    cursor: 'pointer',
                    fontSize: '0.875rem',
                    fontWeight: 500,
                  }}
                >
                  Cancel
                </button>
                <button
                  onClick={handleEmail}
                  disabled={emailing || !emailTo.trim()}
                  style={{
                    padding: '0.5rem 1rem',
                    borderRadius: '6px',
                    border: 'none',
                    backgroundColor: emailing || !emailTo.trim() ? 'var(--muted)' : 'var(--primary)',
                    color: '#ffffff',
                    cursor: emailing || !emailTo.trim() ? 'not-allowed' : 'pointer',
                    fontSize: '0.875rem',
                    fontWeight: 500,
                  }}
                >
                  {emailing ? 'Sending...' : 'Send Email'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Simple Markdown to HTML renderer (no external dependencies)
// ---------------------------------------------------------------------------

/**
 * Converts markdown text to HTML. Handles:
 * - Headers (# through ######)
 * - Bold (**text**)
 * - Italic (*text*)
 * - Inline code (`code`)
 * - Horizontal rules (--- / ***)
 * - Unordered lists (- item)
 * - Ordered lists (1. item)
 * - Tables (| col | col |)
 * - Line breaks
 */
function renderMarkdown(md: string): string {
  const lines = md.split('\n')
  const html: string[] = []
  let inList = false
  let listType: 'ul' | 'ol' = 'ul'
  let inTable = false

  const inlineFormat = (text: string): string => {
    return text
      .replace(/`([^`]+)`/g, '<code style="background:var(--secondary);padding:1px 4px;border-radius:3px;font-size:0.85em">$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/\*([^*]+)\*/g, '<em>$1</em>')
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer" style="color:var(--primary)">$1</a>')
  }

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    const trimmed = line.trim()

    // Horizontal rule
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
      if (inList) { html.push(listType === 'ul' ? '</ul>' : '</ol>'); inList = false }
      if (inTable) { html.push('</tbody></table>'); inTable = false }
      html.push('<hr style="border:none;border-top:1px solid var(--border);margin:1rem 0" />')
      continue
    }

    // Headers
    const headerMatch = trimmed.match(/^(#{1,6})\s+(.+)$/)
    if (headerMatch) {
      if (inList) { html.push(listType === 'ul' ? '</ul>' : '</ol>'); inList = false }
      if (inTable) { html.push('</tbody></table>'); inTable = false }
      const level = headerMatch[1].length
      const sizes: Record<number, string> = { 1: '1.5rem', 2: '1.25rem', 3: '1.1rem', 4: '1rem', 5: '0.9rem', 6: '0.85rem' }
      const id = headerMatch[2].toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
      html.push(
        `<h${level} id="${id}" style="font-size:${sizes[level] || '1rem'};font-weight:600;margin:1rem 0 0.5rem 0;color:var(--text)">${inlineFormat(headerMatch[2])}</h${level}>`
      )
      continue
    }

    // Table row
    if (trimmed.startsWith('|') && trimmed.endsWith('|')) {
      if (inList) { html.push(listType === 'ul' ? '</ul>' : '</ol>'); inList = false }

      // Check if this is a separator row (|---|---|)
      if (/^\|[\s-:|]+\|$/.test(trimmed)) {
        continue // Skip separator rows
      }

      const cells = trimmed.slice(1, -1).split('|').map((c) => c.trim())

      if (!inTable) {
        inTable = true
        html.push('<table style="width:100%;border-collapse:collapse;margin:0.75rem 0;font-size:0.85rem">')
        html.push('<thead><tr style="border-bottom:2px solid var(--border)">')
        cells.forEach((cell) => {
          html.push(`<th style="padding:0.375rem 0.625rem;text-align:left;font-weight:600;color:var(--text)">${inlineFormat(cell)}</th>`)
        })
        html.push('</tr></thead><tbody>')
        continue
      }

      html.push('<tr style="border-bottom:1px solid var(--border)">')
      cells.forEach((cell) => {
        html.push(`<td style="padding:0.375rem 0.625rem;color:var(--text)">${inlineFormat(cell)}</td>`)
      })
      html.push('</tr>')
      continue
    } else if (inTable) {
      html.push('</tbody></table>')
      inTable = false
    }

    // Unordered list
    if (/^[-*+]\s+/.test(trimmed)) {
      if (!inList || listType !== 'ul') {
        if (inList) html.push(listType === 'ul' ? '</ul>' : '</ol>')
        html.push('<ul style="margin:0.5rem 0;padding-left:1.5rem">')
        inList = true
        listType = 'ul'
      }
      html.push(`<li style="margin:0.125rem 0;color:var(--text)">${inlineFormat(trimmed.replace(/^[-*+]\s+/, ''))}</li>`)
      continue
    }

    // Ordered list
    if (/^\d+\.\s+/.test(trimmed)) {
      if (!inList || listType !== 'ol') {
        if (inList) html.push(listType === 'ul' ? '</ul>' : '</ol>')
        html.push('<ol style="margin:0.5rem 0;padding-left:1.5rem">')
        inList = true
        listType = 'ol'
      }
      html.push(`<li style="margin:0.125rem 0;color:var(--text)">${inlineFormat(trimmed.replace(/^\d+\.\s+/, ''))}</li>`)
      continue
    }

    // Close list if we're no longer in one
    if (inList && trimmed !== '') {
      html.push(listType === 'ul' ? '</ul>' : '</ol>')
      inList = false
    }

    // Empty line
    if (trimmed === '') {
      continue
    }

    // Regular paragraph
    html.push(`<p style="margin:0.375rem 0;color:var(--text);line-height:1.6">${inlineFormat(trimmed)}</p>`)
  }

  if (inList) html.push(listType === 'ul' ? '</ul>' : '</ol>')
  if (inTable) html.push('</tbody></table>')

  return html.join('\n')
}

/**
 * Extract headings from markdown for a table of contents
 */
function extractHeadings(md: string): Array<{ level: number; text: string; id: string }> {
  const headings: Array<{ level: number; text: string; id: string }> = []
  const lines = md.split('\n')
  for (const line of lines) {
    const match = line.trim().match(/^(#{1,6})\s+(.+)$/)
    if (match) {
      const text = match[2].replace(/\*\*([^*]+)\*\*/g, '$1').replace(/\*([^*]+)\*/g, '$1')
      headings.push({
        level: match[1].length,
        text,
        id: text.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, ''),
      })
    }
  }
  return headings
}

/**
 * Renders markdown content with a table of contents at the top.
 */
function ReportMarkdownRenderer({ content }: { content: string }) {
  const headings = extractHeadings(content)
  const htmlContent = renderMarkdown(content)

  return (
    <div style={{ fontSize: '0.875rem', lineHeight: 1.7, color: 'var(--text)' }}>
      {/* Table of Contents */}
      {headings.length > 2 && (
        <div
          style={{
            padding: '0.75rem 1rem',
            backgroundColor: 'var(--secondary)',
            borderRadius: '8px',
            border: '1px solid var(--border)',
            marginBottom: '1rem',
          }}
        >
          <div style={{ fontWeight: 600, fontSize: '0.8rem', marginBottom: '0.5rem', color: 'var(--text)' }}>
            Table of Contents
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.125rem' }}>
            {headings.map((h, i) => (
              <a
                key={i}
                href={`#${h.id}`}
                style={{
                  color: 'var(--primary)',
                  textDecoration: 'none',
                  fontSize: '0.8rem',
                  paddingLeft: `${(h.level - 1) * 0.75}rem`,
                  lineHeight: 1.5,
                }}
                onClick={(e) => {
                  e.preventDefault()
                  const el = document.getElementById(h.id)
                  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
                }}
              >
                {h.text}
              </a>
            ))}
          </div>
        </div>
      )}

      {/* Rendered markdown — sanitized to prevent XSS from AI-generated content */}
      <div dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(htmlContent, {
        ALLOWED_TAGS: ['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'ul', 'ol', 'li', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'strong', 'em', 'code', 'a', 'hr', 'div', 'span'],
        ALLOWED_ATTR: ['style', 'id', 'href', 'target', 'rel'],
        ALLOW_DATA_ATTR: false,
      }) }} />
    </div>
  )
}
