/**
 * VendorVerificationPanel -- Claim verification display for a vendor (DPSIA Enhancement).
 *
 * Shows a summary bar with verification status counts, a table of all
 * verifications, and a button to trigger automated claim verification.
 */
import { useState, useEffect, useCallback } from 'react'
import type { VendorClaimVerification, VerificationStatus } from '../types'
import {
  VERIFICATION_STATUS_COLORS,
  VERIFICATION_STATUS_LABELS,
} from '../types'
import {
  getVendorClaimVerifications,
  triggerVendorVerification,
} from '../data/apiClient'

interface VendorVerificationPanelProps {
  organizationId: string
  vendorId: string
}

export default function VendorVerificationPanel({
  organizationId,
  vendorId,
}: VendorVerificationPanelProps) {
  const [verifications, setVerifications] = useState<VendorClaimVerification[]>([])
  const [loading, setLoading] = useState(true)
  const [verifying, setVerifying] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // --------------------------------------------------
  // Load verifications
  // --------------------------------------------------
  const loadVerifications = useCallback(async () => {
    setLoading(true)
    try {
      const data = await getVendorClaimVerifications(vendorId, organizationId)
      setVerifications(data)
      setError(null)
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to load verifications'
      // 404 is expected if no verifications exist yet
      if (!message.includes('404')) {
        setError(message)
      }
    } finally {
      setLoading(false)
    }
  }, [vendorId, organizationId])

  useEffect(() => {
    loadVerifications()
  }, [loadVerifications])

  // --------------------------------------------------
  // Trigger verification
  // --------------------------------------------------
  const handleVerify = async () => {
    setVerifying(true)
    setError(null)
    try {
      const results = await triggerVendorVerification(vendorId, organizationId)
      setVerifications(results)
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to run verification'
      setError(message)
    } finally {
      setVerifying(false)
    }
  }

  // --------------------------------------------------
  // Summary counts
  // --------------------------------------------------
  const statusCounts: Record<VerificationStatus, number> = {
    confirmed: 0,
    unverified: 0,
    discrepancy: 0,
    anomaly: 0,
  }
  verifications.forEach((v) => {
    if (statusCounts[v.verification_status] !== undefined) {
      statusCounts[v.verification_status]++
    }
  })

  const formatClaimType = (type: string): string => {
    return type
      .split('_')
      .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
      .join(' ')
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
          Claim Verification
        </h3>
        <button
          onClick={handleVerify}
          disabled={verifying}
          style={{
            padding: '0.5rem 1rem',
            borderRadius: '6px',
            border: 'none',
            backgroundColor: verifying ? 'var(--muted)' : 'var(--primary)',
            color: '#ffffff',
            cursor: verifying ? 'not-allowed' : 'pointer',
            fontSize: '0.875rem',
            fontWeight: 500,
          }}
        >
          {verifying ? 'Verifying...' : 'Run Verification'}
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
        </div>
      )}

      {/* Summary bar */}
      {verifications.length > 0 && (
        <div
          style={{
            display: 'flex',
            gap: '0.75rem',
            marginBottom: '1rem',
            flexWrap: 'wrap',
          }}
        >
          {(Object.keys(statusCounts) as VerificationStatus[]).map((status) => (
            <div
              key={status}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.375rem',
                fontSize: '0.8rem',
              }}
            >
              <span
                style={{
                  display: 'inline-block',
                  padding: '2px 10px',
                  borderRadius: '9999px',
                  fontSize: '0.75rem',
                  fontWeight: 600,
                  color: '#ffffff',
                  backgroundColor: VERIFICATION_STATUS_COLORS[status],
                }}
              >
                {statusCounts[status]}
              </span>
              <span style={{ color: 'var(--text)' }}>
                {VERIFICATION_STATUS_LABELS[status]}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Loading */}
      {loading && verifications.length === 0 && (
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
            Loading verifications...
          </p>
          <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
        </div>
      )}

      {/* Empty state */}
      {!loading && verifications.length === 0 && !error && (
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
          No claim verifications yet. Click "Run Verification" to automatically verify
          vendor claims against external sources.
        </div>
      )}

      {/* Verifications table */}
      {verifications.length > 0 && (
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
                <th style={{ padding: '0.5rem 0.75rem', fontWeight: 600, color: 'var(--text)' }}>Claim</th>
                <th style={{ padding: '0.5rem 0.75rem', fontWeight: 600, color: 'var(--text)' }}>Type</th>
                <th style={{ padding: '0.5rem 0.75rem', fontWeight: 600, color: 'var(--text)' }}>Status</th>
                <th style={{ padding: '0.5rem 0.75rem', fontWeight: 600, color: 'var(--text)' }}>Source</th>
                <th style={{ padding: '0.5rem 0.75rem', fontWeight: 600, color: 'var(--text)' }}>Detail</th>
              </tr>
            </thead>
            <tbody>
              {verifications.map((v) => (
                <tr
                  key={v.id}
                  style={{ borderBottom: '1px solid var(--border)' }}
                >
                  <td
                    style={{
                      padding: '0.5rem 0.75rem',
                      color: 'var(--text)',
                      maxWidth: '250px',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                    title={v.claim_description}
                  >
                    {v.claim_description}
                  </td>
                  <td style={{ padding: '0.5rem 0.75rem', color: 'var(--text)' }}>
                    {formatClaimType(v.claim_type)}
                  </td>
                  <td style={{ padding: '0.5rem 0.75rem' }}>
                    <span
                      style={{
                        display: 'inline-block',
                        padding: '2px 10px',
                        borderRadius: '9999px',
                        fontSize: '0.75rem',
                        fontWeight: 600,
                        color: '#ffffff',
                        backgroundColor: VERIFICATION_STATUS_COLORS[v.verification_status],
                      }}
                    >
                      {VERIFICATION_STATUS_LABELS[v.verification_status]}
                    </span>
                  </td>
                  <td style={{ padding: '0.5rem 0.75rem', color: 'var(--text)' }}>
                    {v.verification_source ? (
                      v.evidence_url ? (
                        <a
                          href={v.evidence_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          style={{ color: 'var(--primary)', textDecoration: 'none' }}
                        >
                          {v.verification_source}
                        </a>
                      ) : (
                        v.verification_source
                      )
                    ) : (
                      <span style={{ color: 'var(--muted)' }}>-</span>
                    )}
                  </td>
                  <td
                    style={{
                      padding: '0.5rem 0.75rem',
                      color: 'var(--text)',
                      maxWidth: '200px',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                    title={v.verification_detail || ''}
                  >
                    {v.verification_detail || <span style={{ color: 'var(--muted)' }}>-</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
