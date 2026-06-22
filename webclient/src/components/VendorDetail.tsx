/**
 * VendorDetail Component - Detail view for a single vendor record
 *
 * Three-tab layout:
 *   Overview   — vendor metadata, contact, contract, certs, assessment history
 *   Assessment — DPSIA assessment (primary action), CIA detail, claim verification
 *   Results    — action items, compensating controls, reports
 *
 * Persistent header shows vendor name, status, criticality, and risk score
 * across all tabs.
 */
import { useState, useEffect } from 'react'
import type { Vendor, VendorAssessment, VendorCertification } from '../types'
import {
  VENDOR_STATUS_LABELS,
  VENDOR_CRITICALITY_LABELS,
  VENDOR_STATUS_COLORS,
  VENDOR_CRITICALITY_COLORS,
  getRiskLevelColor,
} from '../types'
import type { RiskLevel } from '../types'
import {
  getVendor,
  getVendorAssessments,
  getVendorCertifications
} from '../data/apiClient'
import VendorResearchPanel from './VendorResearchPanel'
import VendorReportsPanel from './VendorReportsPanel'
import VendorCIAPanel from './VendorCIAPanel'
import VendorVerificationPanel from './VendorVerificationPanel'
import VendorActionItemsPanel from './VendorActionItemsPanel'
import VendorCompensatingControlsPanel from './VendorCompensatingControlsPanel'

type VendorTab = 'overview' | 'assessment' | 'results'

const TAB_LABELS: Record<VendorTab, string> = {
  overview: 'Overview',
  assessment: 'Assessment',
  results: 'Results & Actions',
}

interface VendorDetailProps {
  organizationId: string
  vendorId: string
  onBack: () => void
  onEdit: (vendor: Vendor) => void
  onDelete: (vendor: Vendor) => void
}

function toRiskLevel(level: string | null | undefined): RiskLevel | null {
  if (!level) return null
  const normalised = level.toLowerCase()
  if (normalised === 'low' || normalised === 'medium' || normalised === 'high' || normalised === 'critical') {
    return normalised as RiskLevel
  }
  return null
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
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)
  const [activeTab, setActiveTab] = useState<VendorTab>('overview')

  useEffect(() => {
    const fetchVendorData = async () => {
      setLoading(true)
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
        setLoading(false)
      }
    }

    fetchVendorData()
  }, [vendorId, organizationId])

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

  const formatAssessmentType = (type: string): string => {
    return type
      .split('_')
      .map(word => word.charAt(0).toUpperCase() + word.slice(1))
      .join(' ')
  }

  const formatStatus = (status: string): string => {
    return status
      .split('_')
      .map(word => word.charAt(0).toUpperCase() + word.slice(1))
      .join(' ')
  }

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

  const riskLevel = toRiskLevel(vendor.risk_level)

  // ── Render ─────────────────────────────────────────────────────────
  return (
    <div className="vendor-detail" style={{ padding: '1.5rem' }}>

      {/* ================================================================
          Persistent Header — visible across all tabs
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
        <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', flex: 1, minWidth: 0 }}>
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

          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap', minWidth: 0 }}>
            <h2 style={{ margin: 0, fontSize: '1.5rem', fontWeight: 600, color: 'var(--text)' }}>
              {vendor.name}
            </h2>

            <span
              style={{
                display: 'inline-block',
                padding: '2px 10px',
                borderRadius: '9999px',
                fontSize: '0.75rem',
                fontWeight: 500,
                color: '#ffffff',
                backgroundColor: VENDOR_STATUS_COLORS[vendor.status]
              }}
            >
              {VENDOR_STATUS_LABELS[vendor.status]}
            </span>

            <span
              style={{
                display: 'inline-block',
                padding: '2px 10px',
                borderRadius: '9999px',
                fontSize: '0.75rem',
                fontWeight: 500,
                color: '#ffffff',
                backgroundColor: VENDOR_CRITICALITY_COLORS[vendor.criticality]
              }}
            >
              {VENDOR_CRITICALITY_LABELS[vendor.criticality]}
            </span>

            {/* Risk score in header */}
            {vendor.risk_score != null && (
              <span
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '0.375rem',
                  padding: '2px 10px',
                  borderRadius: '9999px',
                  fontSize: '0.75rem',
                  fontWeight: 600,
                  color: '#ffffff',
                  backgroundColor: riskLevel ? getRiskLevelColor(riskLevel) : 'var(--muted)',
                }}
              >
                Risk: {vendor.risk_score}
                {vendor.risk_level && (
                  <span style={{ fontWeight: 400 }}>
                    ({vendor.risk_level.charAt(0).toUpperCase() + vendor.risk_level.slice(1)})
                  </span>
                )}
              </span>
            )}
          </div>
        </div>

        <div style={{ display: 'flex', gap: '0.5rem', flexShrink: 0 }}>
          <button
            onClick={() => vendor && onEdit(vendor)}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '0.5rem',
              padding: '0.5rem 1rem',
              backgroundColor: 'var(--primary)',
              color: '#ffffff',
              border: 'none',
              borderRadius: '6px',
              cursor: 'pointer',
              fontSize: '0.875rem',
              fontWeight: 500,
            }}
          >
            Edit Vendor
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
                            {formatStatus(cert.status)}
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

          {/* Assessment History */}
          <section style={cardStyle}>
            <h3 style={sectionHeading}>Assessment History ({assessments.length})</h3>
            {assessments.length === 0 ? (
              <p style={{ color: 'var(--muted)', fontSize: '0.875rem', margin: 0 }}>
                No assessments recorded yet.{' '}
                <button
                  onClick={() => setActiveTab('assessment')}
                  style={{ background: 'none', border: 'none', color: 'var(--primary)', cursor: 'pointer', fontSize: '0.875rem', fontWeight: 500, padding: 0 }}
                >
                  Run a DPSIA Assessment
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
                      <th style={thStyle}>C / I / A</th>
                      <th style={thStyle}>Risk Rating</th>
                      <th style={thStyle}>Assessor</th>
                    </tr>
                  </thead>
                  <tbody>
                    {assessments.map((assessment) => (
                      <tr key={assessment.id} style={{ borderBottom: '1px solid var(--border)' }}>
                        <td style={tdStyle}>{formatAssessmentType(assessment.assessment_type)}</td>
                        <td style={tdStyle}>{formatDate(assessment.assessment_date)}</td>
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
                                : assessment.status === 'in_progress' ? '#fef3c7'
                                : assessment.status === 'cancelled' ? '#fee2e2'
                                : 'var(--secondary)',
                              color:
                                assessment.status === 'completed' ? '#166534'
                                : assessment.status === 'in_progress' ? '#92400e'
                                : assessment.status === 'cancelled' ? '#991b1b'
                                : 'var(--muted)'
                            }}
                          >
                            {formatStatus(assessment.status)}
                          </span>
                        </td>
                        <td style={{ ...tdStyle, fontVariantNumeric: 'tabular-nums' }}>
                          {assessment.confidentiality_score ?? '-'}
                          {' / '}
                          {assessment.integrity_score ?? '-'}
                          {' / '}
                          {assessment.availability_score ?? '-'}
                        </td>
                        <td style={tdStyle}>{assessment.risk_rating || '-'}</td>
                        <td style={tdStyle}>
                          {assessment.assessor
                            ? assessment.assessor.display_name || assessment.assessor.email
                            : '-'}
                        </td>
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
          Tab: Assessment
          ================================================================ */}
      {activeTab === 'assessment' && (
        <>
          {/* DPSIA Assessment — primary action */}
          <section style={cardStyle}>
            <VendorResearchPanel
              organizationId={organizationId}
              vendorId={vendorId}
              vendorWebsite={vendor.website}
              vendorName={vendor.name}
              vendorDescription={vendor.description}
              onAssessmentComplete={() => {
                setRefreshKey(k => k + 1)
                getVendor(vendorId, organizationId).then(v => setVendor(v)).catch(() => {})
                getVendorAssessments(vendorId, organizationId).then(a => setAssessments(a)).catch(() => {})
              }}
            />
          </section>

          {/* CIA Detail */}
          <section style={cardStyle}>
            <VendorCIAPanel
              key={refreshKey}
              organizationId={organizationId}
              vendorId={vendorId}
            />
          </section>

          {/* Claim Verification */}
          <section style={cardStyle}>
            <VendorVerificationPanel
              key={refreshKey}
              organizationId={organizationId}
              vendorId={vendorId}
            />
          </section>
        </>
      )}

      {/* ================================================================
          Tab: Results & Actions
          ================================================================ */}
      {activeTab === 'results' && (
        <>
          {/* Action Items */}
          <section style={cardStyle}>
            <VendorActionItemsPanel
              key={refreshKey}
              organizationId={organizationId}
              vendorId={vendorId}
            />
          </section>

          {/* Compensating Controls */}
          <section style={cardStyle}>
            <VendorCompensatingControlsPanel
              key={refreshKey}
              organizationId={organizationId}
              vendorId={vendorId}
            />
          </section>

          {/* Reports */}
          <section style={cardStyle}>
            <VendorReportsPanel
              key={refreshKey}
              organizationId={organizationId}
              vendorId={vendorId}
            />
          </section>
        </>
      )}
    </div>
  )
}
