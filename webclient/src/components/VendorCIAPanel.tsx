/**
 * VendorCIAPanel -- CIA control breakdown for a vendor assessment (DPSIA Enhancement).
 *
 * Shows three columns for Confidentiality, Integrity, and Availability with
 * controls, scores, and average score per pillar.
 */
import { useState, useEffect, useCallback } from 'react'
import type { VendorCIAControl, CIAPillar, VendorAssessment } from '../types'
import { CIA_PILLAR_LABELS, CIA_PILLAR_COLORS } from '../types'
import { getVendorCIAControls, getVendorAssessments } from '../data/apiClient'

interface VendorCIAPanelProps {
  organizationId: string
  vendorId: string
  assessmentId?: string
}

const PILLARS: CIAPillar[] = ['confidentiality', 'integrity', 'availability']

export default function VendorCIAPanel({
  organizationId,
  vendorId,
  assessmentId: propAssessmentId,
}: VendorCIAPanelProps) {
  const [controls, setControls] = useState<VendorCIAControl[]>([])
  const [assessments, setAssessments] = useState<VendorAssessment[]>([])
  const [selectedAssessmentId, setSelectedAssessmentId] = useState<string | null>(propAssessmentId || null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // --------------------------------------------------
  // Load assessments to allow selection
  // --------------------------------------------------
  useEffect(() => {
    let cancelled = false
    const loadAssessments = async () => {
      try {
        const data = await getVendorAssessments(vendorId, organizationId)
        if (!cancelled) {
          setAssessments(data)
          // Auto-select latest completed assessment if no prop provided
          if (!propAssessmentId && data.length > 0) {
            const completed = data
              .filter((a) => a.status === 'completed')
              .sort((a, b) => new Date(b.assessment_date).getTime() - new Date(a.assessment_date).getTime())
            if (completed.length > 0) {
              setSelectedAssessmentId(completed[0].id)
            } else {
              // Fall back to the most recent assessment of any status
              const sorted = [...data].sort(
                (a, b) => new Date(b.assessment_date).getTime() - new Date(a.assessment_date).getTime()
              )
              setSelectedAssessmentId(sorted[0].id)
            }
          }
        }
      } catch {
        // Not critical
      }
    }
    loadAssessments()
    return () => { cancelled = true }
  }, [vendorId, organizationId, propAssessmentId])

  // --------------------------------------------------
  // Load CIA controls when assessment changes
  // --------------------------------------------------
  const loadControls = useCallback(async () => {
    if (!selectedAssessmentId) {
      setControls([])
      setLoading(false)
      return
    }
    setLoading(true)
    setError(null)
    try {
      const data = await getVendorCIAControls(vendorId, selectedAssessmentId, organizationId)
      setControls(data)
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to load CIA controls'
      // 404 is expected if no controls exist
      if (!message.includes('404')) {
        setError(message)
      }
      setControls([])
    } finally {
      setLoading(false)
    }
  }, [vendorId, selectedAssessmentId, organizationId])

  useEffect(() => {
    loadControls()
  }, [loadControls])

  // --------------------------------------------------
  // Group controls by pillar
  // --------------------------------------------------
  const controlsByPillar: Record<CIAPillar, VendorCIAControl[]> = {
    confidentiality: [],
    integrity: [],
    availability: [],
  }
  controls.forEach((c) => {
    if (controlsByPillar[c.pillar]) {
      controlsByPillar[c.pillar].push(c)
    }
  })

  const averageScore = (pillarControls: VendorCIAControl[]): number | null => {
    const scored = pillarControls.filter((c) => c.score != null)
    if (scored.length === 0) return null
    const sum = scored.reduce((acc, c) => acc + (c.score || 0), 0)
    return Math.round((sum / scored.length) * 10) / 10
  }

  const scoreColour = (score: number): string => {
    if (score >= 4) return '#22c55e'
    if (score >= 3) return '#eab308'
    if (score >= 2) return '#f97316'
    return '#ef4444'
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
          gap: '1rem',
          flexWrap: 'wrap',
        }}
      >
        <h3 style={{ margin: 0, fontSize: '1rem', fontWeight: 600, color: 'var(--text)' }}>
          CIA Control Breakdown
        </h3>

        {/* Assessment selector */}
        {assessments.length > 0 && (
          <select
            value={selectedAssessmentId || ''}
            onChange={(e) => setSelectedAssessmentId(e.target.value || null)}
            style={{
              padding: '0.375rem 0.75rem',
              borderRadius: '6px',
              border: '1px solid var(--border)',
              backgroundColor: 'var(--card)',
              color: 'var(--text)',
              fontSize: '0.8rem',
              cursor: 'pointer',
            }}
          >
            <option value="">Select assessment...</option>
            {assessments.map((a) => (
              <option key={a.id} value={a.id}>
                {a.assessment_type.replace(/_/g, ' ').replace(/\b\w/g, (l) => l.toUpperCase())} -{' '}
                {new Date(a.assessment_date).toLocaleDateString('en-GB', {
                  day: 'numeric',
                  month: 'short',
                  year: 'numeric',
                })}
              </option>
            ))}
          </select>
        )}
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

      {/* No assessment selected */}
      {!selectedAssessmentId && !loading && (
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
          {assessments.length === 0
            ? 'No assessments available. Create an assessment first to view CIA controls.'
            : 'Select an assessment above to view the CIA control breakdown.'}
        </div>
      )}

      {/* Loading */}
      {loading && selectedAssessmentId && (
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
            Loading CIA controls...
          </p>
          <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
        </div>
      )}

      {/* Three-column layout */}
      {!loading && selectedAssessmentId && (
        <>
          {controls.length === 0 ? (
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
              No CIA controls recorded for this assessment.
            </div>
          ) : (
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(3, 1fr)',
                gap: '0.75rem',
              }}
            >
              {PILLARS.map((pillar) => {
                const pillarControls = controlsByPillar[pillar]
                const avg = averageScore(pillarControls)

                return (
                  <div
                    key={pillar}
                    style={{
                      border: '1px solid var(--border)',
                      borderRadius: '8px',
                      overflow: 'hidden',
                    }}
                  >
                    {/* Pillar header */}
                    <div
                      style={{
                        padding: '0.75rem 1rem',
                        backgroundColor: CIA_PILLAR_COLORS[pillar] + '15',
                        borderBottom: '2px solid ' + CIA_PILLAR_COLORS[pillar],
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                      }}
                    >
                      <span
                        style={{
                          fontWeight: 600,
                          fontSize: '0.85rem',
                          color: CIA_PILLAR_COLORS[pillar],
                        }}
                      >
                        {CIA_PILLAR_LABELS[pillar]}
                      </span>
                      {avg != null && (
                        <span
                          style={{
                            display: 'inline-block',
                            padding: '2px 10px',
                            borderRadius: '9999px',
                            fontSize: '0.75rem',
                            fontWeight: 700,
                            color: '#ffffff',
                            backgroundColor: scoreColour(avg),
                          }}
                        >
                          Avg: {avg}/5
                        </span>
                      )}
                    </div>

                    {/* Controls list */}
                    <div style={{ padding: '0.5rem' }}>
                      {pillarControls.length === 0 ? (
                        <div
                          style={{
                            padding: '1rem',
                            textAlign: 'center',
                            color: 'var(--muted)',
                            fontSize: '0.8rem',
                          }}
                        >
                          No controls
                        </div>
                      ) : (
                        pillarControls.map((ctrl) => (
                          <div
                            key={ctrl.id}
                            style={{
                              padding: '0.5rem 0.625rem',
                              borderBottom: '1px solid var(--border)',
                              fontSize: '0.8rem',
                            }}
                          >
                            <div
                              style={{
                                display: 'flex',
                                justifyContent: 'space-between',
                                alignItems: 'center',
                                marginBottom: '0.25rem',
                              }}
                            >
                              <span style={{ fontWeight: 500, color: 'var(--text)' }}>
                                {ctrl.control_name}
                              </span>
                              {ctrl.score != null && (
                                <span
                                  style={{
                                    fontWeight: 700,
                                    fontSize: '0.75rem',
                                    color: scoreColour(ctrl.score),
                                    minWidth: '2rem',
                                    textAlign: 'right',
                                  }}
                                >
                                  {ctrl.score}/5
                                </span>
                              )}
                            </div>
                            {ctrl.control_category && (
                              <div style={{ color: 'var(--muted)', fontSize: '0.7rem', marginBottom: '0.125rem' }}>
                                {ctrl.control_category}
                              </div>
                            )}
                            {ctrl.detail && (
                              <div style={{ color: 'var(--text)', fontSize: '0.75rem', lineHeight: 1.4 }}>
                                {ctrl.detail}
                              </div>
                            )}
                            {ctrl.evidence && (
                              <div style={{ color: 'var(--muted)', fontSize: '0.7rem', marginTop: '0.25rem', fontStyle: 'italic' }}>
                                Evidence: {ctrl.evidence}
                              </div>
                            )}
                          </div>
                        ))
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </>
      )}
    </div>
  )
}
