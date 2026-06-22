/**
 * VendorRiskPanel -- Risk scoring panel for a single vendor (Issue #60).
 *
 * Displays the current risk factor breakdown, allows triggering a
 * risk calculation, and shows the AI analysis when available.
 */
import { useState, useEffect, useCallback } from 'react'
import type { VendorAssessment, VendorRiskCalculationResult } from '../types'
import { getRiskLevelColor } from '../types'
import type { RiskLevel } from '../types'
import { calculateVendorRisk, getVendorAssessments } from '../data/apiClient'

interface VendorRiskPanelProps {
  organizationId: string
  vendorId: string
}

/** Risk factor labels for the breakdown display */
const FACTOR_LABELS: Record<string, string> = {
  breach_score: 'Breach History',
  certification_score: 'Certifications',
  cve_score: 'CVE / Vulnerabilities',
  regulatory_score: 'Regulatory Findings',
  data_handling_score: 'Data Handling',
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

export default function VendorRiskPanel({
  organizationId,
  vendorId,
}: VendorRiskPanelProps) {
  const [latestAssessment, setLatestAssessment] = useState<VendorAssessment | null>(null)
  const [calcResult, setCalcResult] = useState<VendorRiskCalculationResult | null>(null)
  const [calculating, setCalculating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [dpsiaProtected, setDpsiaProtected] = useState(false)
  const [loadingAssessments, setLoadingAssessments] = useState(true)

  // --------------------------------------------------
  // Load the latest completed assessment on mount
  // --------------------------------------------------
  const loadAssessments = useCallback(async () => {
    setLoadingAssessments(true)
    try {
      const assessments = await getVendorAssessments(vendorId, organizationId)
      // Pick the latest completed assessment that has risk scoring data
      const completed = assessments
        .filter((a) => a.status === 'completed')
        .sort((a, b) => new Date(b.assessment_date).getTime() - new Date(a.assessment_date).getTime())
      if (completed.length > 0) {
        setLatestAssessment(completed[0])
      }
    } catch {
      // Not critical -- the panel just shows no data
    } finally {
      setLoadingAssessments(false)
    }
  }, [vendorId, organizationId])

  useEffect(() => {
    loadAssessments()
  }, [loadAssessments])

  // --------------------------------------------------
  // Calculate risk
  // --------------------------------------------------
  const handleCalculate = async () => {
    setCalculating(true)
    setError(null)
    setDpsiaProtected(false)
    try {
      const result = await calculateVendorRisk(
        vendorId,
        organizationId,
        latestAssessment?.id
      )
      setCalcResult(result)
      if (result.dpsia_protected) {
        setDpsiaProtected(true)
      }
      // Refresh assessments to pick up new scores
      await loadAssessments()
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to calculate risk'
      setError(message)
    } finally {
      setCalculating(false)
    }
  }

  // --------------------------------------------------
  // Derive display data from either the calc result or the latest assessment
  // --------------------------------------------------
  const riskData = calcResult || latestAssessment
  const riskLevel = toRiskLevel(
    calcResult?.risk_level ?? latestAssessment?.risk_level
  )
  const riskScore = calcResult?.final_risk_score ?? latestAssessment?.final_risk_score
  const aiAnalysis = calcResult?.ai_analysis ?? latestAssessment?.ai_analysis
  const likelihood = calcResult?.likelihood ?? latestAssessment?.likelihood
  const impact = calcResult?.impact ?? latestAssessment?.impact

  const factors: { key: string; label: string; value: number | null | undefined }[] = [
    { key: 'breach_score', label: FACTOR_LABELS.breach_score, value: (calcResult?.breach_score ?? latestAssessment?.breach_score) },
    { key: 'certification_score', label: FACTOR_LABELS.certification_score, value: (calcResult?.certification_score ?? latestAssessment?.certification_score) },
    { key: 'cve_score', label: FACTOR_LABELS.cve_score, value: (calcResult?.cve_score ?? latestAssessment?.cve_score) },
    { key: 'regulatory_score', label: FACTOR_LABELS.regulatory_score, value: (calcResult?.regulatory_score ?? latestAssessment?.regulatory_score) },
    { key: 'data_handling_score', label: FACTOR_LABELS.data_handling_score, value: (calcResult?.data_handling_score ?? latestAssessment?.data_handling_score) },
  ]

  const hasAnyScores = factors.some((f) => f.value != null)

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
          Risk Scoring
        </h3>
        <button
          onClick={handleCalculate}
          disabled={calculating}
          style={{
            padding: '0.5rem 1rem',
            borderRadius: '6px',
            border: 'none',
            backgroundColor: calculating ? 'var(--muted)' : 'var(--primary)',
            color: '#ffffff',
            cursor: calculating ? 'not-allowed' : 'pointer',
            fontSize: '0.875rem',
            fontWeight: 500,
          }}
        >
          {calculating ? 'Calculating...' : 'Calculate Risk'}
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

      {/* DPSIA-protected info banner */}
      {dpsiaProtected && (
        <div
          style={{
            padding: '0.75rem 1rem',
            backgroundColor: '#eff6ff',
            border: '1px solid #bfdbfe',
            borderRadius: '8px',
            color: '#1e40af',
            marginBottom: '1rem',
            fontSize: '0.8rem',
          }}
        >
          Risk score is based on the DPSIA assessment. To recalculate with factor-level
          breakdown, ensure vendor research data is available first.
        </div>
      )}

      {/* Risk overview cards */}
      {(riskScore != null || riskLevel) && (
        <div
          style={{
            display: 'flex',
            gap: '1rem',
            marginBottom: '1rem',
            flexWrap: 'wrap',
          }}
        >
          {/* Risk Score */}
          {riskScore != null && (
            <div
              style={{
                flex: '1 1 120px',
                padding: '1rem',
                borderRadius: '8px',
                border: '1px solid var(--border)',
                backgroundColor: 'var(--secondary)',
                textAlign: 'center',
                minWidth: '120px',
              }}
            >
              <div style={{ fontSize: '0.75rem', color: 'var(--muted)', fontWeight: 500, marginBottom: '0.25rem' }}>
                Risk Score
              </div>
              <div style={{ fontSize: '1.5rem', fontWeight: 700, color: 'var(--text)' }}>
                {riskScore}
              </div>
            </div>
          )}

          {/* Risk Level Badge */}
          {riskLevel && (
            <div
              style={{
                flex: '1 1 120px',
                padding: '1rem',
                borderRadius: '8px',
                border: '1px solid var(--border)',
                backgroundColor: 'var(--secondary)',
                textAlign: 'center',
                minWidth: '120px',
              }}
            >
              <div style={{ fontSize: '0.75rem', color: 'var(--muted)', fontWeight: 500, marginBottom: '0.25rem' }}>
                Risk Level
              </div>
              <span
                style={{
                  display: 'inline-block',
                  padding: '4px 14px',
                  borderRadius: '9999px',
                  fontSize: '0.875rem',
                  fontWeight: 600,
                  color: '#ffffff',
                  backgroundColor: getRiskLevelColor(riskLevel),
                }}
              >
                {riskLevel.charAt(0).toUpperCase() + riskLevel.slice(1)}
              </span>
            </div>
          )}

          {/* Likelihood */}
          {likelihood != null && (
            <div
              style={{
                flex: '1 1 120px',
                padding: '1rem',
                borderRadius: '8px',
                border: '1px solid var(--border)',
                backgroundColor: 'var(--secondary)',
                textAlign: 'center',
                minWidth: '120px',
              }}
            >
              <div style={{ fontSize: '0.75rem', color: 'var(--muted)', fontWeight: 500, marginBottom: '0.25rem' }}>
                Likelihood
              </div>
              <div style={{ fontSize: '1.5rem', fontWeight: 700, color: 'var(--text)' }}>
                {likelihood}<span style={{ fontSize: '0.875rem', fontWeight: 400, color: 'var(--muted)' }}>/5</span>
              </div>
            </div>
          )}

          {/* Impact */}
          {impact != null && (
            <div
              style={{
                flex: '1 1 120px',
                padding: '1rem',
                borderRadius: '8px',
                border: '1px solid var(--border)',
                backgroundColor: 'var(--secondary)',
                textAlign: 'center',
                minWidth: '120px',
              }}
            >
              <div style={{ fontSize: '0.75rem', color: 'var(--muted)', fontWeight: 500, marginBottom: '0.25rem' }}>
                Impact
              </div>
              <div style={{ fontSize: '1.5rem', fontWeight: 700, color: 'var(--text)' }}>
                {impact}<span style={{ fontSize: '0.875rem', fontWeight: 400, color: 'var(--muted)' }}>/5</span>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Risk waterfall: Inherent Risk -> Control Effectiveness -> Residual Risk */}
      {(riskScore != null || latestAssessment?.inherent_risk_score != null) && (
        <div
          style={{
            border: '1px solid var(--border)',
            borderRadius: '8px',
            overflow: 'hidden',
            marginBottom: '1rem',
          }}
        >
          <div
            style={{
              padding: '0.75rem 1rem',
              backgroundColor: 'var(--secondary)',
              borderBottom: '1px solid var(--border)',
              fontSize: '0.8rem',
              fontWeight: 600,
              color: 'var(--text)',
            }}
          >
            Risk Waterfall
          </div>
          <div
            style={{
              padding: '1rem',
              display: 'flex',
              alignItems: 'center',
              gap: '0.5rem',
              justifyContent: 'center',
              flexWrap: 'wrap',
            }}
          >
            {/* Inherent Risk */}
            <div
              style={{
                textAlign: 'center',
                padding: '0.75rem 1.25rem',
                borderRadius: '8px',
                backgroundColor: '#fef2f2',
                border: '1px solid #fecaca',
                minWidth: '100px',
              }}
            >
              <div style={{ fontSize: '0.7rem', fontWeight: 500, color: '#991b1b', marginBottom: '0.25rem' }}>
                Inherent Risk
              </div>
              <div style={{ fontSize: '1.25rem', fontWeight: 700, color: '#991b1b' }}>
                {latestAssessment?.inherent_risk_score ?? riskScore ?? '-'}
              </div>
              {latestAssessment?.inherent_risk_level && (
                <div style={{ fontSize: '0.7rem', color: '#991b1b', marginTop: '0.125rem' }}>
                  {latestAssessment.inherent_risk_level.charAt(0).toUpperCase() + latestAssessment.inherent_risk_level.slice(1)}
                </div>
              )}
            </div>

            {/* Arrow */}
            <div style={{ fontSize: '1.25rem', color: 'var(--muted)', fontWeight: 700 }}>
              &#8594;
            </div>

            {/* Control Effectiveness */}
            <div
              style={{
                textAlign: 'center',
                padding: '0.75rem 1.25rem',
                borderRadius: '8px',
                backgroundColor: '#eff6ff',
                border: '1px solid #bfdbfe',
                minWidth: '100px',
              }}
            >
              <div style={{ fontSize: '0.7rem', fontWeight: 500, color: '#1e40af', marginBottom: '0.25rem' }}>
                Control Effectiveness
              </div>
              <div style={{ fontSize: '1.25rem', fontWeight: 700, color: '#1e40af' }}>
                {latestAssessment?.control_effectiveness_pct != null
                  ? `${latestAssessment.control_effectiveness_pct}%`
                  : '-'}
              </div>
              <div
                style={{
                  width: '80px',
                  height: '4px',
                  backgroundColor: '#dbeafe',
                  borderRadius: '2px',
                  margin: '0.25rem auto 0',
                  overflow: 'hidden',
                }}
              >
                <div
                  style={{
                    width: `${Math.min(latestAssessment?.control_effectiveness_pct ?? 0, 100)}%`,
                    height: '100%',
                    backgroundColor: '#3b82f6',
                    borderRadius: '2px',
                    transition: 'width 0.3s ease',
                  }}
                />
              </div>
            </div>

            {/* Arrow */}
            <div style={{ fontSize: '1.25rem', color: 'var(--muted)', fontWeight: 700 }}>
              &#8594;
            </div>

            {/* Residual Risk */}
            <div
              style={{
                textAlign: 'center',
                padding: '0.75rem 1.25rem',
                borderRadius: '8px',
                backgroundColor: riskLevel
                  ? getRiskLevelColor(riskLevel) + '15'
                  : '#f0fdf4',
                border: `1px solid ${riskLevel ? getRiskLevelColor(riskLevel) + '40' : '#bbf7d0'}`,
                minWidth: '100px',
              }}
            >
              <div
                style={{
                  fontSize: '0.7rem',
                  fontWeight: 500,
                  color: riskLevel ? getRiskLevelColor(riskLevel) : '#166534',
                  marginBottom: '0.25rem',
                }}
              >
                Residual Risk
              </div>
              <div
                style={{
                  fontSize: '1.25rem',
                  fontWeight: 700,
                  color: riskLevel ? getRiskLevelColor(riskLevel) : '#166534',
                }}
              >
                {riskScore ?? '-'}
              </div>
              {riskLevel && (
                <div style={{ fontSize: '0.7rem', color: getRiskLevelColor(riskLevel), marginTop: '0.125rem' }}>
                  {riskLevel.charAt(0).toUpperCase() + riskLevel.slice(1)}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Factor breakdown */}
      {hasAnyScores && (
        <div
          style={{
            border: '1px solid var(--border)',
            borderRadius: '8px',
            overflow: 'hidden',
            marginBottom: '1rem',
          }}
        >
          <div
            style={{
              padding: '0.75rem 1rem',
              backgroundColor: 'var(--secondary)',
              borderBottom: '1px solid var(--border)',
              fontSize: '0.8rem',
              fontWeight: 600,
              color: 'var(--text)',
            }}
          >
            Factor Breakdown
          </div>
          {factors.map((f) => (
            <div
              key={f.key}
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                padding: '0.625rem 1rem',
                borderBottom: '1px solid var(--border)',
                fontSize: '0.875rem',
              }}
            >
              <span style={{ color: 'var(--text)' }}>{f.label}</span>
              {f.value != null ? (
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                  {/* Score bar */}
                  <div
                    style={{
                      width: '80px',
                      height: '6px',
                      backgroundColor: 'var(--border)',
                      borderRadius: '3px',
                      overflow: 'hidden',
                    }}
                  >
                    <div
                      style={{
                        width: `${Math.min(Math.max(f.value, 0), 5) * 20}%`,
                        height: '100%',
                        backgroundColor:
                          f.value <= 1 ? '#22c55e'
                          : f.value <= 2 ? '#84cc16'
                          : f.value <= 3 ? '#eab308'
                          : f.value <= 4 ? '#f97316'
                          : '#ef4444',
                        borderRadius: '3px',
                        transition: 'width 0.3s ease',
                      }}
                    />
                  </div>
                  <span
                    style={{
                      fontWeight: 600,
                      fontVariantNumeric: 'tabular-nums',
                      minWidth: '2rem',
                      textAlign: 'right',
                      color: 'var(--text)',
                    }}
                  >
                    {f.value.toFixed(1)}
                  </span>
                </div>
              ) : (
                <span style={{ color: 'var(--muted)', fontSize: '0.8rem' }}>-</span>
              )}
            </div>
          ))}
        </div>
      )}

      {/* AI Analysis */}
      {aiAnalysis && (
        <div
          style={{
            border: '1px solid var(--border)',
            borderRadius: '8px',
            overflow: 'hidden',
            marginBottom: '1rem',
          }}
        >
          <div
            style={{
              padding: '0.75rem 1rem',
              backgroundColor: 'var(--secondary)',
              borderBottom: '1px solid var(--border)',
              fontSize: '0.8rem',
              fontWeight: 600,
              color: 'var(--text)',
            }}
          >
            AI Analysis
          </div>
          <div
            style={{
              padding: '1rem',
              fontSize: '0.875rem',
              color: 'var(--text)',
              lineHeight: 1.6,
              whiteSpace: 'pre-wrap',
            }}
          >
            {aiAnalysis}
          </div>
        </div>
      )}

      {/* Research data indicator */}
      {calcResult?.has_research_data === false && (
        <div
          style={{
            padding: '0.75rem 1rem',
            backgroundColor: 'var(--warning-bg, #fffbeb)',
            border: '1px solid var(--warning-border, #fde68a)',
            borderRadius: '8px',
            fontSize: '0.8rem',
            color: '#92400e',
            marginBottom: '1rem',
          }}
        >
          No research data available. Run vendor research first for more accurate risk scoring.
        </div>
      )}

      {/* Empty state */}
      {!hasAnyScores && !calculating && !error && !loadingAssessments && (
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
          No risk scores calculated yet. Click "Calculate Risk" to generate a risk
          assessment based on vendor data, certifications, and research findings.
        </div>
      )}

      {/* Loading state */}
      {loadingAssessments && !hasAnyScores && (
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
            Loading risk data...
          </p>
          <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
        </div>
      )}
    </div>
  )
}
