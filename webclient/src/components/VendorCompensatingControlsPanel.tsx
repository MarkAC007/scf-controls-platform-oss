/**
 * VendorCompensatingControlsPanel -- Compensating controls display (DPSIA Enhancement).
 *
 * Shows card-based layout: Gap -> Compensating Control -> Effectiveness Rating.
 */
import { useState, useEffect, useCallback } from 'react'
import type { VendorCompensatingControl, EffectivenessRating } from '../types'
import { EFFECTIVENESS_COLORS } from '../types'
import { getVendorCompensatingControls } from '../data/apiClient'

interface VendorCompensatingControlsPanelProps {
  organizationId: string
  vendorId: string
}

const EFFECTIVENESS_LABELS: Record<EffectivenessRating, string> = {
  full: 'Full',
  partial: 'Partial',
  minimal: 'Minimal',
}

export default function VendorCompensatingControlsPanel({
  organizationId,
  vendorId,
}: VendorCompensatingControlsPanelProps) {
  const [controls, setControls] = useState<VendorCompensatingControl[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // --------------------------------------------------
  // Load compensating controls
  // --------------------------------------------------
  const loadControls = useCallback(async () => {
    setLoading(true)
    try {
      const data = await getVendorCompensatingControls(vendorId, organizationId)
      setControls(data)
      setError(null)
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to load compensating controls'
      if (!message.includes('404')) {
        setError(message)
      }
    } finally {
      setLoading(false)
    }
  }, [vendorId, organizationId])

  useEffect(() => {
    loadControls()
  }, [loadControls])

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
          Compensating Controls ({controls.length})
        </h3>
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

      {/* Loading */}
      {loading && controls.length === 0 && (
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
            Loading compensating controls...
          </p>
          <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
        </div>
      )}

      {/* Empty state */}
      {!loading && controls.length === 0 && !error && (
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
          No compensating controls recorded. Compensating controls document alternative
          measures where gaps have been identified.
        </div>
      )}

      {/* Controls cards */}
      {controls.length > 0 && (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(350px, 1fr))',
            gap: '0.75rem',
          }}
        >
          {controls.map((ctrl) => (
            <div
              key={ctrl.id}
              style={{
                border: '1px solid var(--border)',
                borderRadius: '8px',
                overflow: 'hidden',
                backgroundColor: 'var(--card)',
              }}
            >
              {/* Gap description */}
              <div
                style={{
                  padding: '0.75rem 1rem',
                  borderBottom: '1px solid var(--border)',
                  backgroundColor: '#fef2f2',
                }}
              >
                <div
                  style={{
                    fontSize: '0.7rem',
                    fontWeight: 600,
                    color: '#991b1b',
                    textTransform: 'uppercase',
                    letterSpacing: '0.5px',
                    marginBottom: '0.25rem',
                  }}
                >
                  Gap Identified
                </div>
                <div style={{ fontSize: '0.85rem', color: '#7f1d1d', lineHeight: 1.4 }}>
                  {ctrl.gap_description}
                </div>
              </div>

              {/* Arrow indicator */}
              <div
                style={{
                  textAlign: 'center',
                  padding: '0.25rem 0',
                  color: 'var(--muted)',
                  fontSize: '1rem',
                }}
              >
                &#8595;
              </div>

              {/* Compensating control */}
              <div
                style={{
                  padding: '0.75rem 1rem',
                  borderBottom: '1px solid var(--border)',
                  backgroundColor: '#f0fdf4',
                }}
              >
                <div
                  style={{
                    fontSize: '0.7rem',
                    fontWeight: 600,
                    color: '#166534',
                    textTransform: 'uppercase',
                    letterSpacing: '0.5px',
                    marginBottom: '0.25rem',
                  }}
                >
                  Compensating Control
                </div>
                <div style={{ fontSize: '0.85rem', color: '#14532d', lineHeight: 1.4 }}>
                  {ctrl.compensating_control}
                </div>
              </div>

              {/* Effectiveness and notes */}
              <div
                style={{
                  padding: '0.75rem 1rem',
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  gap: '0.75rem',
                }}
              >
                <div>
                  <div
                    style={{
                      fontSize: '0.7rem',
                      fontWeight: 500,
                      color: 'var(--muted)',
                      marginBottom: '0.25rem',
                    }}
                  >
                    Effectiveness
                  </div>
                  <span
                    style={{
                      display: 'inline-block',
                      padding: '2px 12px',
                      borderRadius: '9999px',
                      fontSize: '0.75rem',
                      fontWeight: 600,
                      color: '#ffffff',
                      backgroundColor: EFFECTIVENESS_COLORS[ctrl.effectiveness_rating],
                    }}
                  >
                    {EFFECTIVENESS_LABELS[ctrl.effectiveness_rating]}
                  </span>
                </div>

                {ctrl.risk_reduction_notes && (
                  <div style={{ flex: 1, textAlign: 'right' }}>
                    <div
                      style={{
                        fontSize: '0.7rem',
                        fontWeight: 500,
                        color: 'var(--muted)',
                        marginBottom: '0.125rem',
                      }}
                    >
                      Risk Reduction
                    </div>
                    <div
                      style={{
                        fontSize: '0.8rem',
                        color: 'var(--text)',
                        lineHeight: 1.3,
                      }}
                    >
                      {ctrl.risk_reduction_notes}
                    </div>
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
