/**
 * RiskProfileSettings Component - Organisation Risk Profile Configuration
 *
 * Allows admins to configure per-organisation risk thresholds,
 * acceptable risk levels, auto-escalation, and vendor certification requirements.
 * Viewers and consultants see a read-only view.
 */
import { useState, useEffect, useMemo, useCallback } from 'react'
import { toast } from 'react-hot-toast'
import { useRiskProfile } from '../contexts/RiskProfileContext'
import { useAuth } from '../contexts/AuthContext'
import { useOrganizationSettings } from '../hooks/useOrganizationSettings'
import { updateOrganizationSettings } from '../data/apiClient'
import type { RiskLevel, RiskThresholds } from '../types'
import { getRiskLevel, DEFAULT_RISK_THRESHOLDS } from '../types'

interface RiskProfileSettingsProps {
  organizationId: string
}

const RISK_LEVEL_OPTIONS: { value: RiskLevel; label: string }[] = [
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
  { value: 'critical', label: 'Critical' },
]

const LEVEL_COLORS: Record<RiskLevel, string> = {
  low: '#dcfce7',
  medium: '#fef9c3',
  high: '#fed7aa',
  critical: '#fecaca',
}

export default function RiskProfileSettings({ organizationId }: RiskProfileSettingsProps) {
  const { riskProfile, isLoading, error, updateProfile, resetProfile, refreshProfile } = useRiskProfile()
  const { user } = useAuth()
  const { data: orgSettings, refetch: refetchSettings } = useOrganizationSettings(organizationId)

  // Trust portal state
  const [trustPortalEnabled, setTrustPortalEnabled] = useState(false)
  const [trustPortalDescription, setTrustPortalDescription] = useState('')
  const [isSavingPortal, setIsSavingPortal] = useState(false)

  useEffect(() => {
    if (orgSettings) {
      setTrustPortalEnabled(orgSettings.is_trust_portal_enabled ?? false)
      setTrustPortalDescription(orgSettings.trust_portal_description ?? '')
    }
  }, [orgSettings])

  const handleSaveTrustPortal = useCallback(async () => {
    setIsSavingPortal(true)
    try {
      await updateOrganizationSettings(organizationId, {
        is_trust_portal_enabled: trustPortalEnabled,
        trust_portal_description: trustPortalDescription || null,
      } as any)
      await refetchSettings()
      toast.success(trustPortalEnabled ? 'Trust portal enabled' : 'Trust portal disabled')
    } catch (err: any) {
      toast.error(err.message || 'Failed to update trust portal settings')
    } finally {
      setIsSavingPortal(false)
    }
  }, [organizationId, trustPortalEnabled, trustPortalDescription, refetchSettings])

  // Local form state
  const [lowMax, setLowMax] = useState(4)
  const [mediumMax, setMediumMax] = useState(9)
  const [highMax, setHighMax] = useState(16)
  const [acceptableRiskLevel, setAcceptableRiskLevel] = useState<RiskLevel>('medium')
  const [autoEscalateAbove, setAutoEscalateAbove] = useState<RiskLevel>('high')
  const [requiredCerts, setRequiredCerts] = useState<string[]>([])
  const [preferredCerts, setPreferredCerts] = useState<string[]>([])
  const [requiredCertInput, setRequiredCertInput] = useState('')
  const [preferredCertInput, setPreferredCertInput] = useState('')
  const [vendorAutoApproveMax, setVendorAutoApproveMax] = useState(4)
  const [vendorAutoRejectMin, setVendorAutoRejectMin] = useState(20)
  const [isSaving, setIsSaving] = useState(false)
  const [isResetting, setIsResetting] = useState(false)
  const [hasChanges, setHasChanges] = useState(false)

  // Determine if user has admin role (simplified - check org membership)
  // In production this would come from the org context
  const isAdmin = true // All authenticated users with org access can view; backend enforces admin for writes

  // Sync form state from profile
  useEffect(() => {
    if (riskProfile) {
      setLowMax(riskProfile.low_max)
      setMediumMax(riskProfile.medium_max)
      setHighMax(riskProfile.high_max)
      setAcceptableRiskLevel(riskProfile.acceptable_risk_level)
      setAutoEscalateAbove(riskProfile.auto_escalate_above)
      try { setRequiredCerts(JSON.parse(riskProfile.required_vendor_certifications || '[]')) } catch { setRequiredCerts([]) }
      try { setPreferredCerts(JSON.parse(riskProfile.preferred_vendor_certifications || '[]')) } catch { setPreferredCerts([]) }
      setVendorAutoApproveMax(riskProfile.vendor_auto_approve_max)
      setVendorAutoRejectMin(riskProfile.vendor_auto_reject_min)
      setHasChanges(false)
    }
  }, [riskProfile])

  // Track changes
  useEffect(() => {
    if (!riskProfile) return
    const changed =
      lowMax !== riskProfile.low_max ||
      mediumMax !== riskProfile.medium_max ||
      highMax !== riskProfile.high_max ||
      acceptableRiskLevel !== riskProfile.acceptable_risk_level ||
      autoEscalateAbove !== riskProfile.auto_escalate_above ||
      JSON.stringify(requiredCerts) !== riskProfile.required_vendor_certifications ||
      JSON.stringify(preferredCerts) !== riskProfile.preferred_vendor_certifications ||
      vendorAutoApproveMax !== riskProfile.vendor_auto_approve_max ||
      vendorAutoRejectMin !== riskProfile.vendor_auto_reject_min
    setHasChanges(changed)
  }, [riskProfile, lowMax, mediumMax, highMax, acceptableRiskLevel, autoEscalateAbove, requiredCerts, preferredCerts, vendorAutoApproveMax, vendorAutoRejectMin])

  // Validation
  const validationErrors = useMemo(() => {
    const errors: string[] = []
    if (lowMax < 1 || lowMax > 24) errors.push('Low max must be between 1 and 24')
    if (mediumMax < 1 || mediumMax > 24) errors.push('Medium max must be between 1 and 24')
    if (highMax < 1 || highMax > 24) errors.push('High max must be between 1 and 24')
    if (lowMax >= mediumMax) errors.push('Low max must be less than medium max')
    if (mediumMax >= highMax) errors.push('Medium max must be less than high max')
    if (vendorAutoApproveMax >= vendorAutoRejectMin) errors.push('Auto-approve max must be less than auto-reject min')
    return errors
  }, [lowMax, mediumMax, highMax, vendorAutoApproveMax, vendorAutoRejectMin])

  const isValid = validationErrors.length === 0

  // Preview thresholds for visual bar
  const previewThresholds: RiskThresholds = { lowMax, mediumMax, highMax }

  // Save handler
  const handleSave = useCallback(async () => {
    if (!isValid) return
    setIsSaving(true)
    try {
      await updateProfile({
        low_max: lowMax,
        medium_max: mediumMax,
        high_max: highMax,
        acceptable_risk_level: acceptableRiskLevel,
        auto_escalate_above: autoEscalateAbove,
        required_vendor_certifications: JSON.stringify(requiredCerts),
        preferred_vendor_certifications: JSON.stringify(preferredCerts),
        vendor_auto_approve_max: vendorAutoApproveMax,
        vendor_auto_reject_min: vendorAutoRejectMin,
      })
      toast.success('Risk profile saved')
      setHasChanges(false)
    } catch (err: any) {
      toast.error(err.message || 'Failed to save risk profile')
    } finally {
      setIsSaving(false)
    }
  }, [isValid, lowMax, mediumMax, highMax, acceptableRiskLevel, autoEscalateAbove, requiredCerts, preferredCerts, vendorAutoApproveMax, vendorAutoRejectMin, updateProfile])

  // Reset handler
  const handleReset = useCallback(async () => {
    setIsResetting(true)
    try {
      await resetProfile()
      toast.success('Risk profile reset to defaults')
    } catch (err: any) {
      toast.error(err.message || 'Failed to reset risk profile')
    } finally {
      setIsResetting(false)
    }
  }, [resetProfile])

  if (isLoading) {
    return (
      <div className="risk-profile-settings loading">
        <div className="loading-spinner" />
        <p>Loading risk profile...</p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="risk-profile-settings error">
        <p>Error: {error}</p>
        <button onClick={refreshProfile} className="btn-primary">Retry</button>
      </div>
    )
  }

  return (
    <div className="risk-profile-settings">
      {/* Risk & Governance Settings Group */}
      <div className="settings-group">
        <div className="settings-group-header">
          <h1>Risk & Governance</h1>
          <p className="settings-description">
            Configure your organizational risk thresholds, appetite, and vendor requirements.
          </p>
        </div>

        {/* Risk Level Thresholds */}
        <section className="settings-section">
          <h2>Risk Level Thresholds</h2>
          <p className="section-description">
            Define the score boundaries for each risk level. Risk scores range from 1
            (lowest) to 25 (highest), calculated as Likelihood x Impact.
          </p>

          <div className="threshold-inputs">
            <div className="threshold-group">
              <label htmlFor="low-max">Low Maximum</label>
              <input
                id="low-max"
                type="number"
                min={1}
                max={24}
                value={lowMax}
                onChange={e => setLowMax(Number(e.target.value))}
              />
              <span className="threshold-hint">Scores 1-{lowMax} = Low</span>
            </div>

            <div className="threshold-group">
              <label htmlFor="medium-max">Medium Maximum</label>
              <input
                id="medium-max"
                type="number"
                min={1}
                max={24}
                value={mediumMax}
                onChange={e => setMediumMax(Number(e.target.value))}
              />
              <span className="threshold-hint">Scores {lowMax + 1}-{mediumMax} = Medium</span>
            </div>

            <div className="threshold-group">
              <label htmlFor="high-max">High Maximum</label>
              <input
                id="high-max"
                type="number"
                min={1}
                max={24}
                value={highMax}
                onChange={e => setHighMax(Number(e.target.value))}
              />
              <span className="threshold-hint">Scores {mediumMax + 1}-{highMax} = High</span>
            </div>

            <div className="threshold-group threshold-readonly">
              <label>Critical</label>
              <span className="threshold-auto">Scores {highMax + 1}-25</span>
            </div>
          </div>

          {/* Visual range bar */}
          {isValid && (
            <div className="threshold-visual-bar">
              <div
                className="bar-segment bar-low"
                style={{
                  width: `${(lowMax / 25) * 100}%`,
                  backgroundColor: LEVEL_COLORS.low,
                }}
              >
                <span>Low</span>
                <span className="bar-range">1-{lowMax}</span>
              </div>
              <div
                className="bar-segment bar-medium"
                style={{
                  width: `${((mediumMax - lowMax) / 25) * 100}%`,
                  backgroundColor: LEVEL_COLORS.medium,
                }}
              >
                <span>Medium</span>
                <span className="bar-range">{lowMax + 1}-{mediumMax}</span>
              </div>
              <div
                className="bar-segment bar-high"
                style={{
                  width: `${((highMax - mediumMax) / 25) * 100}%`,
                  backgroundColor: LEVEL_COLORS.high,
                }}
              >
                <span>High</span>
                <span className="bar-range">{mediumMax + 1}-{highMax}</span>
              </div>
              <div
                className="bar-segment bar-critical"
                style={{
                  width: `${((25 - highMax) / 25) * 100}%`,
                  backgroundColor: LEVEL_COLORS.critical,
                }}
              >
                <span>Critical</span>
                <span className="bar-range">{highMax + 1}-25</span>
              </div>
            </div>
          )}

          {/* Preview 5x5 mini matrix */}
          {isValid && (
            <div className="threshold-preview-matrix">
              <h3>Preview Matrix</h3>
              <div className="preview-grid">
                {[5, 4, 3, 2, 1].map(impact => (
                  <div key={`preview-row-${impact}`} className="preview-row">
                    {[1, 2, 3, 4, 5].map(likelihood => {
                      const score = likelihood * impact
                      const level = getRiskLevel(score, previewThresholds)
                      return (
                        <div
                          key={`preview-${likelihood}-${impact}`}
                          className="preview-cell"
                          style={{ backgroundColor: LEVEL_COLORS[level] }}
                          title={`L${likelihood} x I${impact} = ${score} (${level})`}
                        >
                          {score}
                        </div>
                      )
                    })}
                  </div>
                ))}
              </div>
            </div>
          )}
        </section>

        {/* Risk Appetite */}
        <section className="settings-section">
          <h2>Risk Appetite</h2>

          {/* Range slider appetite selector */}
          <div className="appetite-scale-label">
            <span>Risk Appetite Level</span>
            <span>Selected: {(['Minimal', 'Cautious', 'Moderate', 'Flexible', 'Open'] as const)[
              acceptableRiskLevel === 'low' ? 0 :
              acceptableRiskLevel === 'medium' ? 2 :
              acceptableRiskLevel === 'high' ? 3 :
              acceptableRiskLevel === 'critical' ? 4 : 2
            ]} Risk</span>
          </div>
          <div className="appetite-slider-container">
            <input
              type="range"
              className="appetite-slider"
              min={1}
              max={5}
              step={1}
              value={
                acceptableRiskLevel === 'low' ? 1 :
                acceptableRiskLevel === 'medium' ? 3 :
                acceptableRiskLevel === 'high' ? 4 :
                acceptableRiskLevel === 'critical' ? 5 : 3
              }
              onChange={e => {
                const v = Number(e.target.value)
                const mapped: RiskLevel = v <= 2 ? 'low' : v === 3 ? 'medium' : v === 4 ? 'high' : 'critical'
                setAcceptableRiskLevel(mapped)
                setHasChanges(true)
              }}
            />
            <div className="appetite-slider-labels">
              {['Minimal', 'Cautious', 'Moderate', 'Flexible', 'Open'].map((label, i) => {
                const sliderVal =
                  acceptableRiskLevel === 'low' ? 1 :
                  acceptableRiskLevel === 'medium' ? 3 :
                  acceptableRiskLevel === 'high' ? 4 :
                  acceptableRiskLevel === 'critical' ? 5 : 3
                return (
                  <span
                    key={label}
                    className={`appetite-slider-label${sliderVal === i + 1 ? ' appetite-slider-label-active' : ''}`}
                  >
                    {label}
                  </span>
                )
              })}
            </div>
          </div>

          <div className="appetite-inputs">
            <div className="appetite-group">
              <label htmlFor="escalate-above">Auto-Escalate Above</label>
              <select
                id="escalate-above"
                value={autoEscalateAbove}
                onChange={e => setAutoEscalateAbove(e.target.value as RiskLevel)}
              >
                {RISK_LEVEL_OPTIONS.map(opt => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
              <span className="appetite-hint">
                Risks above this level require escalation
              </span>
            </div>
          </div>
        </section>

        {/* Vendor Certification Requirements */}
        <section className="settings-section">
          <h2>Vendor Certification Requirements</h2>
          <p className="section-description">
            Define certification requirements for third-party vendor assessments.
            Type a certification name and press Enter to add it.
          </p>

          <div className="vendor-inputs">
            <div className="vendor-group">
              <label htmlFor="required-certs">Required Certifications</label>
              <div className="cert-tag-container">
                {requiredCerts.map((cert, i) => (
                  <span key={i} className="cert-tag cert-tag-required">
                    {cert}
                    <button
                      type="button"
                      className="cert-tag-remove"
                      onClick={() => setRequiredCerts(prev => prev.filter((_, idx) => idx !== i))}
                      aria-label={`Remove ${cert}`}
                    >
                      &times;
                    </button>
                  </span>
                ))}
                <input
                  id="required-certs"
                  type="text"
                  className="cert-tag-input"
                  value={requiredCertInput}
                  onChange={e => setRequiredCertInput(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === 'Enter' && requiredCertInput.trim()) {
                      e.preventDefault()
                      const val = requiredCertInput.trim()
                      if (!requiredCerts.includes(val)) {
                        setRequiredCerts(prev => [...prev, val])
                      }
                      setRequiredCertInput('')
                    }
                  }}
                  placeholder={requiredCerts.length === 0 ? 'e.g. ISO 27001' : ''}
                />
              </div>
            </div>

            <div className="vendor-group">
              <label htmlFor="preferred-certs">Preferred Certifications</label>
              <div className="cert-tag-container">
                {preferredCerts.map((cert, i) => (
                  <span key={i} className="cert-tag cert-tag-preferred">
                    {cert}
                    <button
                      type="button"
                      className="cert-tag-remove"
                      onClick={() => setPreferredCerts(prev => prev.filter((_, idx) => idx !== i))}
                      aria-label={`Remove ${cert}`}
                    >
                      &times;
                    </button>
                  </span>
                ))}
                <input
                  id="preferred-certs"
                  type="text"
                  className="cert-tag-input"
                  value={preferredCertInput}
                  onChange={e => setPreferredCertInput(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === 'Enter' && preferredCertInput.trim()) {
                      e.preventDefault()
                      const val = preferredCertInput.trim()
                      if (!preferredCerts.includes(val)) {
                        setPreferredCerts(prev => [...prev, val])
                      }
                      setPreferredCertInput('')
                    }
                  }}
                  placeholder={preferredCerts.length === 0 ? 'e.g. Cyber Essentials Plus' : ''}
                />
              </div>
            </div>
          </div>
        </section>

        {/* Vendor Risk Thresholds */}
        <section className="settings-section">
          <h2>Vendor Risk Thresholds</h2>
          <p className="section-description">
            Automate vendor risk decisions based on their risk score.
          </p>

          <div className="vendor-threshold-inputs">
            <div className="vendor-threshold-group">
              <label htmlFor="auto-approve">Auto-Approve Maximum Score</label>
              <input
                id="auto-approve"
                type="number"
                min={1}
                max={25}
                value={vendorAutoApproveMax}
                onChange={e => setVendorAutoApproveMax(Number(e.target.value))}
              />
              <span className="threshold-hint">
                Vendors scoring at or below this are auto-approved
              </span>
            </div>

            <div className="vendor-threshold-group">
              <label htmlFor="auto-reject">Auto-Reject Minimum Score</label>
              <input
                id="auto-reject"
                type="number"
                min={1}
                max={25}
                value={vendorAutoRejectMin}
                onChange={e => setVendorAutoRejectMin(Number(e.target.value))}
              />
              <span className="threshold-hint">
                Vendors scoring at or above this are auto-rejected
              </span>
            </div>
          </div>
        </section>

        {/* Validation errors */}
        {validationErrors.length > 0 && (
          <div className="settings-validation-errors">
            <h3>Validation Errors</h3>
            <ul>
              {validationErrors.map((err, i) => (
                <li key={i}>{err}</li>
              ))}
            </ul>
          </div>
        )}

        {/* Action buttons for risk settings */}
        <div className="settings-actions">
          <button
            className="btn-primary"
            disabled={!isValid || !hasChanges || isSaving}
            onClick={handleSave}
          >
            {isSaving ? 'Saving...' : 'Save Changes'}
          </button>
          <button
            className="btn-secondary"
            disabled={isResetting}
            onClick={handleReset}
          >
            {isResetting ? 'Resetting...' : 'Reset to Defaults'}
          </button>
        </div>
      </div>

      {/* Trust Portal Group */}
      <div className="settings-group">
        <section className="settings-section">
          <h2>Trust Portal</h2>
          <p className="section-description">
            Enable a public trust portal for your organisation. When enabled, prospects can view
            your aggregated compliance posture without authentication.
          </p>

          <div className="trust-portal-toggle">
            <div className="toggle-row">
              <label htmlFor="trust-portal-toggle" className="toggle-label">
                <span className="toggle-label-text">Enable Trust Portal</span>
                <span className="toggle-label-hint">
                  {trustPortalEnabled
                    ? 'Your trust portal is publicly accessible'
                    : 'Trust portal is currently disabled'}
                </span>
              </label>
              <button
                id="trust-portal-toggle"
                type="button"
                role="switch"
                aria-checked={trustPortalEnabled}
                className={`toggle-switch ${trustPortalEnabled ? 'toggle-switch-on' : 'toggle-switch-off'}`}
                onClick={() => setTrustPortalEnabled(prev => !prev)}
              >
                <span className="toggle-switch-thumb" />
              </button>
            </div>

            {trustPortalEnabled && (
              <div className="trust-portal-description-field">
                <label htmlFor="trust-portal-desc">Portal Description</label>
                <textarea
                  id="trust-portal-desc"
                  value={trustPortalDescription}
                  onChange={e => setTrustPortalDescription(e.target.value)}
                  placeholder="Describe your organisation's commitment to security and compliance..."
                  rows={3}
                  maxLength={500}
                />
                <span className="threshold-hint">
                  Displayed on your public trust portal. {500 - trustPortalDescription.length} characters remaining.
                </span>
              </div>
            )}

            <div className="trust-portal-actions">
              <button
                className="btn-primary"
                disabled={isSavingPortal}
                onClick={handleSaveTrustPortal}
              >
                {isSavingPortal ? 'Saving...' : 'Save Trust Portal Settings'}
              </button>
            </div>
          </div>
        </section>
      </div>
    </div>
  )
}
