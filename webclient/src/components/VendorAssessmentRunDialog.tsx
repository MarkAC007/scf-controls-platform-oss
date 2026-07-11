/**
 * VendorAssessmentRunDialog -- small dialog for launching an AI assessment.
 *
 * The assessment type is preselected by the caller based on the vendor's
 * lifecycle position (initial / annual / adhoc), services used is prefilled
 * from the vendor record, and the data role defaults to Processor.
 */
import { useState } from 'react'
import type {
  Vendor,
  VendorAIAssessmentType,
  VendorAIAssessmentTriggerRequest,
  VendorAIAssessmentTriggerResponse,
} from '../types'
import { triggerVendorAIAssessment } from '../data/apiClient'

interface VendorAssessmentRunDialogProps {
  organizationId: string
  vendor: Vendor
  defaultType: VendorAIAssessmentType
  onClose: () => void
  onStarted: (response: VendorAIAssessmentTriggerResponse) => void
}

const TYPE_LABELS: Record<VendorAIAssessmentType, string> = {
  initial: 'Initial assessment',
  annual: 'Annual review',
  adhoc: 'Ad hoc reassessment',
}

export default function VendorAssessmentRunDialog({
  organizationId,
  vendor,
  defaultType,
  onClose,
  onStarted,
}: VendorAssessmentRunDialogProps) {
  const [assessmentType, setAssessmentType] = useState<VendorAIAssessmentType>(defaultType)
  const [servicesUsed, setServicesUsed] = useState(vendor.description || '')
  const [dataRole, setDataRole] = useState<'Processor' | 'Controller' | 'Joint Controller'>('Processor')
  const [additionalContext, setAdditionalContext] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async () => {
    if (!servicesUsed.trim()) {
      setError('Please describe the services this vendor provides.')
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      const body: VendorAIAssessmentTriggerRequest = {
        assessment_type: assessmentType,
        services_used: servicesUsed.trim(),
        data_role: dataRole,
        ...(additionalContext.trim() ? { additional_context: additionalContext.trim() } : {}),
      }
      const resp = await triggerVendorAIAssessment(vendor.id, body, organizationId)
      onStarted(resp)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start the assessment. Please try again.')
      setSubmitting(false)
    }
  }

  const labelStyle: React.CSSProperties = {
    display: 'block',
    fontSize: '0.75rem',
    fontWeight: 600,
    color: 'var(--text)',
    marginBottom: '0.25rem',
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

  return (
    <div
      className="modal-overlay"
      onClick={() => !submitting && onClose()}
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        background: 'rgba(0, 0, 0, 0.5)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1000,
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: 'var(--card)',
          borderRadius: '12px',
          padding: '24px',
          width: '100%',
          maxWidth: '520px',
          margin: '20px',
          border: '1px solid var(--border)',
          boxShadow: '0 20px 60px rgba(0, 0, 0, 0.3)',
          display: 'flex',
          flexDirection: 'column',
          gap: '0.875rem',
        }}
      >
        <div>
          <h3 style={{ margin: 0, fontSize: '1.125rem', fontWeight: 600, color: 'var(--text)' }}>
            Run AI assessment
          </h3>
          <p style={{ margin: '0.375rem 0 0 0', fontSize: '0.8125rem', color: 'var(--muted)' }}>
            The assessment researches {vendor.name} online and produces a full security
            and data protection report. It typically takes a couple of minutes.
          </p>
        </div>

        {error && (
          <div style={{
            padding: '0.625rem 0.75rem',
            backgroundColor: 'var(--destructive-bg, #fef2f2)',
            border: '1px solid var(--destructive-border, #fecaca)',
            borderRadius: '6px',
            color: 'var(--destructive, #991b1b)',
            fontSize: '0.8125rem',
          }}>
            {error}
          </div>
        )}

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem' }}>
          <div>
            <label style={labelStyle} htmlFor="assessment-type">Assessment type</label>
            <select
              id="assessment-type"
              style={{ ...inputStyle, cursor: 'pointer' }}
              value={assessmentType}
              disabled={submitting}
              onChange={e => setAssessmentType(e.target.value as VendorAIAssessmentType)}
            >
              {(Object.keys(TYPE_LABELS) as VendorAIAssessmentType[]).map(t => (
                <option key={t} value={t}>{TYPE_LABELS[t]}</option>
              ))}
            </select>
          </div>
          <div>
            <label style={labelStyle} htmlFor="data-role">Data role</label>
            <select
              id="data-role"
              style={{ ...inputStyle, cursor: 'pointer' }}
              value={dataRole}
              disabled={submitting}
              onChange={e => setDataRole(e.target.value as 'Processor' | 'Controller' | 'Joint Controller')}
            >
              <option value="Processor">Processor</option>
              <option value="Controller">Controller</option>
              <option value="Joint Controller">Joint Controller</option>
            </select>
          </div>
        </div>

        <div>
          <label style={labelStyle} htmlFor="services-used">Services used *</label>
          <textarea
            id="services-used"
            style={{ ...inputStyle, minHeight: '4rem', resize: 'vertical' }}
            placeholder="Describe the services this vendor provides to your organisation..."
            value={servicesUsed}
            disabled={submitting}
            onChange={e => setServicesUsed(e.target.value)}
          />
        </div>

        <div>
          <label style={labelStyle} htmlFor="additional-context">Additional context</label>
          <textarea
            id="additional-context"
            style={{ ...inputStyle, minHeight: '3rem', resize: 'vertical' }}
            placeholder="Optional — anything the assessment should take into account..."
            value={additionalContext}
            disabled={submitting}
            onChange={e => setAdditionalContext(e.target.value)}
          />
        </div>

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.625rem' }}>
          <button
            onClick={onClose}
            disabled={submitting}
            style={{
              padding: '8px 16px',
              background: 'var(--secondary)',
              border: '1px solid var(--border)',
              borderRadius: '6px',
              color: 'var(--text)',
              cursor: submitting ? 'not-allowed' : 'pointer',
              fontSize: '0.875rem',
              opacity: submitting ? 0.6 : 1,
            }}
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={submitting || !servicesUsed.trim()}
            style={{
              padding: '8px 16px',
              background: 'var(--primary)',
              border: 'none',
              borderRadius: '6px',
              color: '#ffffff',
              cursor: submitting || !servicesUsed.trim() ? 'not-allowed' : 'pointer',
              fontSize: '0.875rem',
              fontWeight: 500,
              opacity: submitting || !servicesUsed.trim() ? 0.6 : 1,
            }}
          >
            {submitting ? 'Starting...' : 'Start assessment'}
          </button>
        </div>
      </div>
    </div>
  )
}
