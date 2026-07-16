/**
 * VendorDetail Component - Detail view for a single vendor record
 *
 * Structured around the vendor lifecycle: Add -> Assess -> Decide -> Review.
 *
 *   Overview -- vendor metadata, contact, contract, certifications
 *   Assess   -- AI assessment (run/progress/report viewer)
 *   Decide   -- recommendation, conditions, action items, compensating controls
 *   Review   -- assessment history and next annual review
 *
 * A persistent header strip shows the single authoritative risk score with
 * RAG pill, the latest recommendation, provenance ("Assessed ... / Review
 * due ...") and the primary assessment CTA.
 */
import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import type {
  Vendor,
  VendorAssessment,
  VendorCertification,
  VendorAIAssessmentType,
  VendorRAGStatus,
  VendorRecommendation,
  System,
  VendorSimple,
} from '../types'
import {
  VENDOR_STATUS_LABELS,
  VENDOR_CRITICALITY_LABELS,
  VENDOR_STATUS_COLORS,
  VENDOR_CRITICALITY_COLORS,
  VENDOR_RAG_COLORS,
  VENDOR_RECOMMENDATION_LABELS,
  VENDOR_RECOMMENDATION_COLORS,
  vendorRiskLevelToRAG,
} from '../types'
import {
  getVendor,
  getVendorAssessments,
  getVendorCertifications,
  getVendorAssessmentStatus,
  getSystemsFiltered,
} from '../data/apiClient'
import VendorAssessmentRunDialog from './VendorAssessmentRunDialog'
import VendorAssessmentReport from './VendorAssessmentReport'
import VendorActionItemsPanel from './VendorActionItemsPanel'
import VendorCompensatingControlsPanel from './VendorCompensatingControlsPanel'
import AddSystemModal from './AddSystemModal'

type VendorTab = 'overview' | 'assess' | 'decide' | 'review'

const TAB_LABELS: Record<VendorTab, string> = {
  overview: 'Overview',
  assess: 'Assess',
  decide: 'Decide',
  review: 'Review',
}

const POLL_INTERVAL_MS = 3000

interface VendorDetailProps {
  organizationId: string
  vendorId: string
  onBack: () => void
  onEdit: (vendor: Vendor) => void
  onDelete: (vendor: Vendor) => void
}

const formatDate = (dateStr: string | null | undefined): string => {
  if (!dateStr) return '-'
  try {
    return new Date(dateStr).toLocaleDateString('en-GB', {
      day: 'numeric',
      month: 'short',
      year: 'numeric'
    })
  } catch {
    return dateStr
  }
}

const formatCurrency = (value: number | null | undefined): string => {
  if (value == null) return '-'
  return new Intl.NumberFormat('en-GB', {
    style: 'currency',
    currency: 'GBP',
    minimumFractionDigits: 0,
    maximumFractionDigits: 2
  }).format(value)
}

const formatLabel = (value: string): string => {
  return value
    .split('_')
    .map(word => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ')
}

export default function VendorDetail({
  organizationId,
  vendorId,
  onBack,
  onEdit,
  onDelete
}: VendorDetailProps) {
  const [vendor, setVendor] = useState<Vendor | null>(null)
  const [assessments, setAssessments] = useState<VendorAssessment[]>([])
  const [certifications, setCertifications] = useState<VendorCertification[]>([])
  const [systems, setSystems] = useState<System[]>([])
  const [showAddSystem, setShowAddSystem] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)
  const [activeTab, setActiveTab] = useState<VendorTab>('assess')
  const [showRunDialog, setShowRunDialog] = useState(false)
  const [pollError, setPollError] = useState<string | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const loadVendorData = useCallback(async (withSpinner: boolean) => {
    if (withSpinner) setLoading(true)
    setError(null)
    try {
      const [vendorData, assessmentData, certificationData] = await Promise.all([
        getVendor(vendorId, organizationId),
        getVendorAssessments(vendorId, organizationId),
        getVendorCertifications(vendorId, organizationId)
      ])
      setVendor(vendorData)
      setAssessments(assessmentData)
      setCertifications(certificationData)
    } catch (err) {
      console.error('Failed to fetch vendor details:', err)
      setError(err instanceof Error ? err.message : 'Failed to load vendor details')
    } finally {
      if (withSpinner) setLoading(false)
    }
  }, [vendorId, organizationId])

  useEffect(() => {
    loadVendorData(true)
  }, [loadVendorData])

  const loadSystems = useCallback(async () => {
    try {
      const linkedSystems = await getSystemsFiltered({ vendor_id: vendorId }, organizationId)
      setSystems(linkedSystems)
    } catch (err) {
      console.error('Failed to load linked systems:', err)
      // Non-fatal: the Systems section simply shows an empty/error-free state.
    }
  }, [vendorId, organizationId])

  useEffect(() => {
    loadSystems()
  }, [loadSystems])

  // ── Assessment lifecycle derivations ──────────────────────────────
  const aiAssessments = useMemo(
    () => assessments.filter(a => a.job_id != null),
    [assessments]
  )
  const inProgressAssessment = useMemo(
    () => aiAssessments.find(a => a.status === 'pending' || a.status === 'running') || null,
    [aiAssessments]
  )
  const latestCompleted = useMemo(
    () => aiAssessments.find(a => a.status === 'completed') || null,
    [aiAssessments]
  )
  // A failed run is only surfaced if it is more recent than the last success
  const latestFailed = useMemo(() => {
    const newest = aiAssessments[0]
    return newest && newest.status === 'failed' ? newest : null
  }, [aiAssessments])

  const reviewStatus = vendor?.review_status || null
  const neverAssessed = !latestCompleted

  const defaultAssessmentType: VendorAIAssessmentType =
    reviewStatus === 'due_soon' || reviewStatus === 'overdue'
      ? 'annual'
      : neverAssessed
        ? 'initial'
        : 'adhoc'

  const ctaLabel =
    reviewStatus === 'due_soon' || reviewStatus === 'overdue'
      ? 'Run annual review'
      : neverAssessed
        ? 'Run AI assessment'
        : 'Re-run assessment'

  // ── Poll while an assessment is in progress ────────────────────────
  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }, [])

  useEffect(() => {
    stopPolling()
    if (!inProgressAssessment) return

    const assessmentId = inProgressAssessment.id
    pollRef.current = setInterval(async () => {
      try {
        const status = await getVendorAssessmentStatus(vendorId, assessmentId, organizationId)
        if (status.status === 'completed' || status.status === 'failed') {
          stopPolling()
          setPollError(null)
          // Refresh everything: vendor risk score/review date + assessments
          await loadVendorData(false)
          setRefreshKey(k => k + 1)
        }
      } catch (err) {
        console.error('Assessment status poll failed:', err)
        setPollError(err instanceof Error ? err.message : 'Failed to check assessment progress')
        stopPolling()
      }
    }, POLL_INTERVAL_MS)

    return stopPolling
  }, [inProgressAssessment, vendorId, organizationId, stopPolling, loadVendorData])

  const handleAssessmentStarted = useCallback(() => {
    setShowRunDialog(false)
    setPollError(null)
    setActiveTab('assess')
    // Re-fetch so the pending assessment row appears and polling starts
    loadVendorData(false)
  }, [loadVendorData])

  // ── Shared styles ──────────────────────────────────────────────────
  const cardStyle: React.CSSProperties = {
    marginBottom: '1.5rem',
    padding: '1.25rem',
    backgroundColor: 'var(--card)',
    borderRadius: '8px',
    border: '1px solid var(--border)',
  }

  const gridStyle: React.CSSProperties = {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
    gap: '0.75rem',
  }

  const sectionHeading: React.CSSProperties = {
    margin: '0 0 1rem 0',
    fontSize: '1rem',
    fontWeight: 600,
    color: 'var(--text)',
  }

  const fieldLabel: React.CSSProperties = {
    fontSize: '0.75rem',
    color: 'var(--muted)',
    fontWeight: 500,
  }

  const fieldValue: React.CSSProperties = {
    margin: '0.25rem 0 0 0',
    fontSize: '0.875rem',
    color: 'var(--text)',
  }

  const thStyle: React.CSSProperties = {
    padding: '0.5rem 0.75rem',
    fontWeight: 600,
    color: 'var(--text)',
  }

  const tdStyle: React.CSSProperties = {
    padding: '0.5rem 0.75rem',
    color: 'var(--text)',
  }

  const pillStyle = (background: string): React.CSSProperties => ({
    display: 'inline-block',
    padding: '2px 10px',
    borderRadius: '9999px',
    fontSize: '0.75rem',
    fontWeight: 500,
    color: '#ffffff',
    backgroundColor: background,
  })

  // ── Loading state ──────────────────────────────────────────────────
  if (loading) {
    return (
      <div className="vendor-detail" style={{ padding: '2rem', textAlign: 'center' }}>
        <div
          style={{
            display: 'inline-block',
            width: '2rem',
            height: '2rem',
            border: '3px solid var(--border)',
            borderTopColor: 'var(--primary)',
            borderRadius: '50%',
            animation: 'spin 0.8s linear infinite'
          }}
        />
        <p style={{ marginTop: '1rem', color: 'var(--muted)' }}>Loading vendor details...</p>
        <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
      </div>
    )
  }

  if (error) {
    return (
      <div className="vendor-detail" style={{ padding: '2rem' }}>
        <button
          onClick={onBack}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '0.5rem',
            background: 'none',
            border: 'none',
            color: 'var(--primary)',
            cursor: 'pointer',
            padding: '0.5rem 0',
            fontSize: '0.875rem',
            marginBottom: '1rem'
          }}
        >
          &larr; Back to vendors
        </button>
        <div
          style={{
            padding: '1.5rem',
            backgroundColor: 'var(--destructive-bg, #fef2f2)',
            borderRadius: '8px',
            border: '1px solid var(--destructive-border, #fecaca)',
            color: 'var(--destructive)'
          }}
        >
          <strong>Error:</strong> {error}
        </div>
      </div>
    )
  }

  if (!vendor) {
    return null
  }

  // The single authoritative risk display: prefer the latest completed
  // AI assessment's RAG; fall back to a mapping from the vendor risk level.
  const ragStatus: VendorRAGStatus | null =
    (latestCompleted?.rag_status as VendorRAGStatus | null | undefined)
    || vendorRiskLevelToRAG(vendor.risk_level)
  const recommendation = (latestCompleted?.recommendation ?? null) as VendorRecommendation | null

  const assessedDate = vendor.risk_provenance?.scored_at || latestCompleted?.completed_at || null
  const reviewDue = vendor.next_review_date || null
  const reviewColour =
    reviewStatus === 'overdue' ? '#ef4444'
    : reviewStatus === 'due_soon' ? '#f59e0b'
    : 'var(--muted)'

  const conditions = Array.isArray((latestCompleted?.report_json as Record<string, unknown> | null | undefined)?.conditions)
    ? ((latestCompleted!.report_json as Record<string, unknown>).conditions as string[])
    : []

  // ── Render ─────────────────────────────────────────────────────────
  return (
    <div className="vendor-detail" style={{ padding: '1.5rem' }}>

      {/* ================================================================
          Persistent Header Strip — visible across all tabs
          ================================================================ */}
      <div
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          justifyContent: 'space-between',
          marginBottom: '0',
          gap: '1rem',
          flexWrap: 'wrap',
          padding: '0 0 1rem 0',
          borderBottom: '1px solid var(--border)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: '1rem', flex: 1, minWidth: 0 }}>
          <button
            onClick={onBack}
            title="Back to vendors"
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
              fontSize: '1.125rem',
              color: 'var(--text)',
              flexShrink: 0
            }}
          >
            &larr;
          </button>

          <div style={{ minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap', minWidth: 0 }}>
              <h2 style={{ margin: 0, fontSize: '1.5rem', fontWeight: 600, color: 'var(--text)' }}>
                {vendor.name}
              </h2>

              {vendor.category && (
                <span style={{ fontSize: '0.8125rem', color: 'var(--muted)' }}>
                  {vendor.category}
                </span>
              )}

              <span style={pillStyle(VENDOR_STATUS_COLORS[vendor.status])}>
                {VENDOR_STATUS_LABELS[vendor.status]}
              </span>

              <span style={pillStyle(VENDOR_CRITICALITY_COLORS[vendor.criticality])}>
                {VENDOR_CRITICALITY_LABELS[vendor.criticality]}
              </span>

              {/* THE risk score — one score, RAG-coloured */}
              {vendor.risk_score != null && (
                <span
                  style={{
                    ...pillStyle(ragStatus ? VENDOR_RAG_COLORS[ragStatus] : 'var(--muted)'),
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: '0.375rem',
                    fontWeight: 600,
                  }}
                >
                  Risk {vendor.risk_score}
                  {ragStatus && <span style={{ fontWeight: 400 }}>· {ragStatus}</span>}
                </span>
              )}

              {recommendation && (
                <span style={{
                  ...pillStyle(VENDOR_RECOMMENDATION_COLORS[recommendation]),
                  fontWeight: 600,
                }}>
                  {VENDOR_RECOMMENDATION_LABELS[recommendation]}
                </span>
              )}
            </div>

            {/* Provenance line */}
            {(assessedDate || reviewDue) && (
              <div style={{ marginTop: '0.375rem', fontSize: '0.75rem', color: 'var(--muted)' }}>
                {assessedDate && <span>Assessed {formatDate(assessedDate)}</span>}
                {assessedDate && reviewDue && <span> · </span>}
                {reviewDue && (
                  <span style={{ color: reviewColour, fontWeight: reviewStatus === 'ok' || !reviewStatus ? 400 : 600 }}>
                    Review due {formatDate(reviewDue)}
                    {reviewStatus === 'due_soon' && ' (due soon)'}
                    {reviewStatus === 'overdue' && ' (overdue)'}
                  </span>
                )}
              </div>
            )}
          </div>
        </div>

        <div style={{ display: 'flex', gap: '0.5rem', flexShrink: 0, alignItems: 'center' }}>
          {/* Primary CTA */}
          <button
            onClick={() => setShowRunDialog(true)}
            disabled={!!inProgressAssessment}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '0.5rem',
              padding: '0.5rem 1rem',
              backgroundColor: 'var(--primary)',
              color: '#ffffff',
              border: 'none',
              borderRadius: '6px',
              cursor: inProgressAssessment ? 'not-allowed' : 'pointer',
              fontSize: '0.875rem',
              fontWeight: 600,
              opacity: inProgressAssessment ? 0.6 : 1,
            }}
          >
            {inProgressAssessment ? 'Assessment running...' : ctaLabel}
          </button>
          <button
            onClick={() => vendor && onEdit(vendor)}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '0.5rem',
              padding: '0.5rem 1rem',
              backgroundColor: 'transparent',
              color: 'var(--text)',
              border: '1px solid var(--border)',
              borderRadius: '6px',
              cursor: 'pointer',
              fontSize: '0.875rem',
              fontWeight: 500,
            }}
          >
            Edit
          </button>
          <button
            onClick={() => vendor && onDelete(vendor)}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '0.5rem',
              padding: '0.5rem 1rem',
              backgroundColor: 'transparent',
              color: '#ef4444',
              border: '1px solid #ef4444',
              borderRadius: '6px',
              cursor: 'pointer',
              fontSize: '0.875rem',
              fontWeight: 500,
            }}
          >
            Delete
          </button>
        </div>
      </div>

      {/* ================================================================
          Tab Bar
          ================================================================ */}
      <div
        style={{
          display: 'flex',
          gap: '0',
          borderBottom: '2px solid var(--border)',
          marginBottom: '1.5rem',
          marginTop: '0',
        }}
      >
        {(Object.keys(TAB_LABELS) as VendorTab[]).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            style={{
              padding: '0.75rem 1.25rem',
              fontSize: '0.875rem',
              fontWeight: activeTab === tab ? 600 : 400,
              color: activeTab === tab ? 'var(--primary)' : 'var(--muted)',
              background: 'none',
              border: 'none',
              borderBottom: activeTab === tab ? '2px solid var(--primary)' : '2px solid transparent',
              marginBottom: '-2px',
              cursor: 'pointer',
              transition: 'color 0.15s, border-color 0.15s',
            }}
          >
            {TAB_LABELS[tab]}
          </button>
        ))}
      </div>

      {/* ================================================================
          Tab: Overview
          ================================================================ */}
      {activeTab === 'overview' && (
        <>
          {/* Vendor metadata */}
          <section style={cardStyle}>
            <h3 style={sectionHeading}>Vendor Details</h3>
            <div style={gridStyle}>
              <div>
                <span style={fieldLabel}>Description</span>
                <p style={fieldValue}>{vendor.description || '-'}</p>
              </div>
              <div>
                <span style={fieldLabel}>Website</span>
                <p style={fieldValue}>
                  {vendor.website ? (
                    <a href={vendor.website} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--primary)', textDecoration: 'none' }}>
                      {vendor.website}
                    </a>
                  ) : '-'}
                </p>
              </div>
              <div>
                <span style={fieldLabel}>Category</span>
                <p style={fieldValue}>{vendor.category || '-'}</p>
              </div>
              <div>
                <span style={fieldLabel}>Data Classification</span>
                <p style={fieldValue}>
                  {vendor.data_classification
                    ? vendor.data_classification.charAt(0).toUpperCase() + vendor.data_classification.slice(1)
                    : '-'}
                </p>
              </div>
            </div>
          </section>

          {/* Contact */}
          <section style={cardStyle}>
            <h3 style={sectionHeading}>Contact</h3>
            <div style={gridStyle}>
              <div>
                <span style={fieldLabel}>Contact Name</span>
                <p style={fieldValue}>{vendor.contact_name || '-'}</p>
              </div>
              <div>
                <span style={fieldLabel}>Email</span>
                <p style={fieldValue}>
                  {vendor.contact_email ? (
                    <a href={`mailto:${vendor.contact_email}`} style={{ color: 'var(--primary)', textDecoration: 'none' }}>
                      {vendor.contact_email}
                    </a>
                  ) : '-'}
                </p>
              </div>
              <div>
                <span style={fieldLabel}>Phone</span>
                <p style={fieldValue}>{vendor.contact_phone || '-'}</p>
              </div>
            </div>
          </section>

          {/* Contract */}
          <section style={cardStyle}>
            <h3 style={sectionHeading}>Contract</h3>
            <div style={gridStyle}>
              <div>
                <span style={fieldLabel}>Start Date</span>
                <p style={fieldValue}>{formatDate(vendor.contract_start_date)}</p>
              </div>
              <div>
                <span style={fieldLabel}>End Date</span>
                <p style={fieldValue}>{formatDate(vendor.contract_end_date)}</p>
              </div>
              <div>
                <span style={fieldLabel}>Contract Value</span>
                <p style={fieldValue}>{formatCurrency(vendor.contract_value)}</p>
              </div>
            </div>
          </section>

          {/* Certifications */}
          <section style={cardStyle}>
            <h3 style={sectionHeading}>Certifications ({certifications.length})</h3>
            {certifications.length === 0 ? (
              <p style={{ color: 'var(--muted)', fontSize: '0.875rem', margin: 0 }}>
                No certifications recorded for this vendor.
              </p>
            ) : (
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.875rem' }}>
                  <thead>
                    <tr style={{ borderBottom: '2px solid var(--border)', textAlign: 'left' }}>
                      <th style={thStyle}>Name</th>
                      <th style={thStyle}>Body</th>
                      <th style={thStyle}>Status</th>
                      <th style={thStyle}>Issue Date</th>
                      <th style={thStyle}>Expiry Date</th>
                      <th style={thStyle}>Certificate Number</th>
                    </tr>
                  </thead>
                  <tbody>
                    {certifications.map((cert) => (
                      <tr key={cert.id} style={{ borderBottom: '1px solid var(--border)' }}>
                        <td style={{ ...tdStyle, fontWeight: 500 }}>{cert.certification_name}</td>
                        <td style={tdStyle}>{cert.certification_body || '-'}</td>
                        <td style={tdStyle}>
                          <span
                            style={{
                              display: 'inline-block',
                              padding: '2px 8px',
                              borderRadius: '4px',
                              fontSize: '0.75rem',
                              fontWeight: 500,
                              backgroundColor:
                                cert.status === 'valid' ? '#dcfce7'
                                : cert.status === 'expired' ? '#fee2e2'
                                : cert.status === 'revoked' ? '#fef3c7'
                                : 'var(--secondary)',
                              color:
                                cert.status === 'valid' ? '#166534'
                                : cert.status === 'expired' ? '#991b1b'
                                : cert.status === 'revoked' ? '#92400e'
                                : 'var(--muted)'
                            }}
                          >
                            {formatLabel(cert.status)}
                          </span>
                        </td>
                        <td style={tdStyle}>{formatDate(cert.issue_date)}</td>
                        <td style={tdStyle}>{formatDate(cert.expiry_date)}</td>
                        <td style={tdStyle}>{cert.certificate_number || '-'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {/* Systems */}
          <section style={cardStyle}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '1rem' }}>
              <h3 style={{ ...sectionHeading, margin: 0 }}>Systems ({systems.length})</h3>
              <button
                onClick={() => setShowAddSystem(true)}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '0.4rem',
                  padding: '0.375rem 0.875rem',
                  backgroundColor: 'var(--primary)',
                  color: '#fff',
                  border: 'none',
                  borderRadius: '6px',
                  cursor: 'pointer',
                  fontSize: '0.8125rem',
                  fontWeight: 600,
                }}
              >
                + Add system
              </button>
            </div>
            {systems.length === 0 ? (
              <p style={{ color: 'var(--muted)', fontSize: '0.875rem', margin: 0 }}>
                No systems linked to this vendor yet.
              </p>
            ) : (
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.875rem' }}>
                  <thead>
                    <tr style={{ borderBottom: '2px solid var(--border)', textAlign: 'left' }}>
                      <th style={thStyle}>Name</th>
                      <th style={thStyle}>Type</th>
                      <th style={thStyle}>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {systems.map((system) => (
                      <tr key={system.id} style={{ borderBottom: '1px solid var(--border)' }}>
                        <td style={{ ...tdStyle, fontWeight: 500 }}>{system.name}</td>
                        <td style={tdStyle}>{formatLabel(system.system_type)}</td>
                        <td style={tdStyle}>{formatLabel(system.status)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}

      {/* ================================================================
          Tab: Assess
          ================================================================ */}
      {activeTab === 'assess' && (
        <>
          {/* Poll failure (network etc.) */}
          {pollError && (
            <section style={{ ...cardStyle, borderColor: 'var(--destructive-border, #fecaca)' }}>
              <p style={{ margin: 0, fontSize: '0.875rem', color: 'var(--destructive, #991b1b)' }}>
                {pollError}
              </p>
              <button
                onClick={() => { setPollError(null); loadVendorData(false) }}
                style={{
                  marginTop: '0.75rem',
                  padding: '0.375rem 0.875rem',
                  backgroundColor: 'var(--primary)',
                  color: '#fff',
                  border: 'none',
                  borderRadius: '6px',
                  cursor: 'pointer',
                  fontSize: '0.8125rem',
                  fontWeight: 600,
                }}
              >
                Check again
              </button>
            </section>
          )}

          {/* In progress */}
          {inProgressAssessment && (
            <section style={{ ...cardStyle, display: 'flex', alignItems: 'center', gap: '0.875rem' }}>
              <div style={{
                width: '1.25rem',
                height: '1.25rem',
                border: '2px solid var(--primary)',
                borderTopColor: 'transparent',
                borderRadius: '50%',
                animation: 'spin 1s linear infinite',
                flexShrink: 0,
              }} />
              <div>
                <div style={{ fontSize: '0.875rem', fontWeight: 600, color: 'var(--text)' }}>
                  {inProgressAssessment.status === 'pending'
                    ? 'Assessment queued...'
                    : 'Researching vendor online...'}
                </div>
                <div style={{ fontSize: '0.8125rem', color: 'var(--muted)' }}>
                  The assessment researches certifications, breach history, CVEs and regulatory
                  actions, then scores the vendor. This can take a couple of minutes.
                </div>
              </div>
              <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
            </section>
          )}

          {/* Latest run failed */}
          {!inProgressAssessment && latestFailed && (
            <section
              style={{
                ...cardStyle,
                backgroundColor: 'var(--destructive-bg, #fef2f2)',
                borderColor: 'var(--destructive-border, #fecaca)',
              }}
            >
              <h3 style={{ margin: '0 0 0.5rem 0', fontSize: '0.9375rem', fontWeight: 600, color: 'var(--destructive, #991b1b)' }}>
                Assessment failed
              </h3>
              <p style={{ margin: 0, fontSize: '0.8125rem', color: 'var(--destructive, #991b1b)' }}>
                {latestFailed.error_message || 'The assessment did not complete. Please try again.'}
              </p>
              <button
                onClick={() => setShowRunDialog(true)}
                style={{
                  marginTop: '0.75rem',
                  padding: '0.375rem 0.875rem',
                  backgroundColor: 'var(--primary)',
                  color: '#fff',
                  border: 'none',
                  borderRadius: '6px',
                  cursor: 'pointer',
                  fontSize: '0.8125rem',
                  fontWeight: 600,
                }}
              >
                Try again
              </button>
            </section>
          )}

          {/* Completed report */}
          {latestCompleted ? (
            <VendorAssessmentReport
              key={refreshKey}
              assessment={latestCompleted}
              vendorName={vendor.name}
            />
          ) : (
            !inProgressAssessment && !latestFailed && (
              <section style={{ ...cardStyle, textAlign: 'center', padding: '2.5rem 1rem' }}>
                <div style={{ fontSize: '0.9375rem', fontWeight: 600, color: 'var(--text)', marginBottom: '0.375rem' }}>
                  No AI assessment yet
                </div>
                <div style={{ fontSize: '0.8125rem', color: 'var(--muted)', marginBottom: '1rem' }}>
                  Run an AI assessment to research this vendor's security posture and
                  produce a full risk report.
                </div>
                <button
                  onClick={() => setShowRunDialog(true)}
                  style={{
                    padding: '0.5rem 1.25rem',
                    backgroundColor: 'var(--primary)',
                    color: '#fff',
                    border: 'none',
                    borderRadius: '6px',
                    cursor: 'pointer',
                    fontSize: '0.875rem',
                    fontWeight: 600,
                  }}
                >
                  {ctaLabel}
                </button>
              </section>
            )
          )}
        </>
      )}

      {/* ================================================================
          Tab: Decide
          ================================================================ */}
      {activeTab === 'decide' && (
        <>
          {/* Recommendation + conditions */}
          <section style={cardStyle}>
            <h3 style={sectionHeading}>Recommendation</h3>
            {latestCompleted && recommendation ? (
              <>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap' }}>
                  <span style={{
                    display: 'inline-block',
                    padding: '0.375rem 1rem',
                    borderRadius: '9999px',
                    fontSize: '0.875rem',
                    fontWeight: 700,
                    color: '#fff',
                    backgroundColor: VENDOR_RECOMMENDATION_COLORS[recommendation],
                  }}>
                    {VENDOR_RECOMMENDATION_LABELS[recommendation]}
                  </span>
                  {latestCompleted.completed_at && (
                    <span style={{ fontSize: '0.75rem', color: 'var(--muted)' }}>
                      From assessment completed {formatDate(latestCompleted.completed_at)}
                    </span>
                  )}
                </div>
                {latestCompleted.executive_summary && (
                  <p style={{ margin: '0.75rem 0 0 0', fontSize: '0.8125rem', color: 'var(--text)', lineHeight: 1.6 }}>
                    {latestCompleted.executive_summary}
                  </p>
                )}
                {conditions.length > 0 && (
                  <div style={{ marginTop: '0.75rem' }}>
                    <div style={{ fontSize: '0.8125rem', fontWeight: 600, color: 'var(--text)', marginBottom: '0.25rem' }}>
                      Conditions for use
                    </div>
                    <ul style={{ margin: '0 0 0 1rem', padding: 0 }}>
                      {conditions.map((c, i) => (
                        <li key={i} style={{ fontSize: '0.8125rem', color: 'var(--text)', lineHeight: 1.6, marginBottom: '0.25rem' }}>
                          {c}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </>
            ) : (
              <p style={{ color: 'var(--muted)', fontSize: '0.875rem', margin: 0 }}>
                No recommendation yet — run an AI assessment first.{' '}
                <button
                  onClick={() => setActiveTab('assess')}
                  style={{ background: 'none', border: 'none', color: 'var(--primary)', cursor: 'pointer', fontSize: '0.875rem', fontWeight: 500, padding: 0 }}
                >
                  Go to Assess
                </button>
              </p>
            )}
          </section>

          {/* Action Items */}
          <section style={cardStyle}>
            <VendorActionItemsPanel
              key={`actions-${refreshKey}`}
              organizationId={organizationId}
              vendorId={vendorId}
            />
          </section>

          {/* Compensating Controls */}
          <section style={cardStyle}>
            <VendorCompensatingControlsPanel
              key={`comp-${refreshKey}`}
              organizationId={organizationId}
              vendorId={vendorId}
            />
          </section>
        </>
      )}

      {/* ================================================================
          Tab: Review
          ================================================================ */}
      {activeTab === 'review' && (
        <>
          {/* Next review */}
          <section style={cardStyle}>
            <h3 style={sectionHeading}>Next Review</h3>
            {reviewDue ? (
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap' }}>
                <span style={{ fontSize: '0.875rem', color: 'var(--text)' }}>
                  Annual review due <strong>{formatDate(reviewDue)}</strong>
                </span>
                {reviewStatus && reviewStatus !== 'ok' && (
                  <span style={pillStyle(reviewStatus === 'overdue' ? '#ef4444' : '#f59e0b')}>
                    {reviewStatus === 'overdue' ? 'Overdue' : 'Due soon'}
                  </span>
                )}
                {(reviewStatus === 'due_soon' || reviewStatus === 'overdue') && !inProgressAssessment && (
                  <button
                    onClick={() => setShowRunDialog(true)}
                    style={{
                      padding: '0.375rem 0.875rem',
                      backgroundColor: 'var(--primary)',
                      color: '#fff',
                      border: 'none',
                      borderRadius: '6px',
                      cursor: 'pointer',
                      fontSize: '0.8125rem',
                      fontWeight: 600,
                    }}
                  >
                    Run annual review
                  </button>
                )}
              </div>
            ) : (
              <p style={{ color: 'var(--muted)', fontSize: '0.875rem', margin: 0 }}>
                No review scheduled — the review date is set automatically when an
                AI assessment completes.
              </p>
            )}
          </section>

          {/* Assessment History */}
          <section style={cardStyle}>
            <h3 style={sectionHeading}>Assessment History ({assessments.length})</h3>
            {assessments.length === 0 ? (
              <p style={{ color: 'var(--muted)', fontSize: '0.875rem', margin: 0 }}>
                No assessments recorded yet.{' '}
                <button
                  onClick={() => setActiveTab('assess')}
                  style={{ background: 'none', border: 'none', color: 'var(--primary)', cursor: 'pointer', fontSize: '0.875rem', fontWeight: 500, padding: 0 }}
                >
                  Run an AI assessment
                </button>
                {' '}to get started.
              </p>
            ) : (
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.875rem' }}>
                  <thead>
                    <tr style={{ borderBottom: '2px solid var(--border)', textAlign: 'left' }}>
                      <th style={thStyle}>Type</th>
                      <th style={thStyle}>Date</th>
                      <th style={thStyle}>Status</th>
                      <th style={thStyle}>Risk Score</th>
                      <th style={thStyle}>RAG</th>
                      <th style={thStyle}>Recommendation</th>
                    </tr>
                  </thead>
                  <tbody>
                    {assessments.map((assessment) => {
                      const rowRag = assessment.rag_status as VendorRAGStatus | null | undefined
                      const rowRec = assessment.recommendation as VendorRecommendation | null | undefined
                      return (
                        <tr key={assessment.id} style={{ borderBottom: '1px solid var(--border)' }}>
                          <td style={tdStyle}>{formatLabel(assessment.assessment_type)}</td>
                          <td style={tdStyle}>{formatDate(assessment.completed_at || assessment.assessment_date)}</td>
                          <td style={tdStyle}>
                            <span
                              style={{
                                display: 'inline-block',
                                padding: '2px 8px',
                                borderRadius: '4px',
                                fontSize: '0.75rem',
                                fontWeight: 500,
                                backgroundColor:
                                  assessment.status === 'completed' ? '#dcfce7'
                                  : assessment.status === 'running' || assessment.status === 'in_progress' || assessment.status === 'pending' ? '#fef3c7'
                                  : assessment.status === 'failed' || assessment.status === 'cancelled' ? '#fee2e2'
                                  : 'var(--secondary)',
                                color:
                                  assessment.status === 'completed' ? '#166534'
                                  : assessment.status === 'running' || assessment.status === 'in_progress' || assessment.status === 'pending' ? '#92400e'
                                  : assessment.status === 'failed' || assessment.status === 'cancelled' ? '#991b1b'
                                  : 'var(--muted)'
                              }}
                            >
                              {formatLabel(assessment.status)}
                            </span>
                          </td>
                          <td style={{ ...tdStyle, fontVariantNumeric: 'tabular-nums', fontWeight: assessment.final_risk_score != null ? 600 : 400 }}>
                            {assessment.final_risk_score ?? '-'}
                          </td>
                          <td style={tdStyle}>
                            {rowRag ? (
                              <span style={pillStyle(VENDOR_RAG_COLORS[rowRag])}>{rowRag}</span>
                            ) : '-'}
                          </td>
                          <td style={tdStyle}>
                            {rowRec ? VENDOR_RECOMMENDATION_LABELS[rowRec] : '-'}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}

      {/* ================================================================
          Run assessment dialog
          ================================================================ */}
      {showRunDialog && (
        <VendorAssessmentRunDialog
          organizationId={organizationId}
          vendor={vendor}
          defaultType={defaultAssessmentType}
          onClose={() => setShowRunDialog(false)}
          onStarted={handleAssessmentStarted}
        />
      )}

      {/* ================================================================
          Add system dialog — pre-linked to this vendor
          ================================================================ */}
      {showAddSystem && (
        <AddSystemModal
          organizationId={organizationId}
          initialVendor={vendorToSimple(vendor)}
          onClose={() => setShowAddSystem(false)}
          onSuccess={() => {
            setShowAddSystem(false)
            loadSystems()
          }}
        />
      )}
    </div>
  )
}

/** Project the full Vendor record down to the lightweight VendorSimple shape. */
function vendorToSimple(vendor: Vendor): VendorSimple {
  return {
    id: vendor.id,
    name: vendor.name,
    website: vendor.website ?? null,
    category: vendor.category ?? null,
    status: vendor.status ?? null,
  }
}
