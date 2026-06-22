import React, { useState, useEffect } from 'react'
import type { Vendor, VendorInput, VendorStatus, VendorCriticality, DataClassification } from '../types'
import { VENDOR_STATUS_LABELS, VENDOR_CRITICALITY_LABELS } from '../types'
import { createVendor, updateVendor } from '../data/apiClient'

interface VendorModalProps {
  organizationId: string
  editVendor?: Vendor | null
  onClose: () => void
  onSuccess: () => void
}

const DATA_CLASSIFICATION_LABELS: Record<DataClassification, string> = {
  public: 'Public',
  internal: 'Internal',
  confidential: 'Confidential',
  restricted: 'Restricted',
}

export const VendorModal: React.FC<VendorModalProps> = ({
  organizationId,
  editVendor,
  onClose,
  onSuccess,
}) => {
  const isEditMode = !!editVendor

  const [formData, setFormData] = useState<VendorInput>({
    name: '',
    description: '',
    website: '',
    category: '',
    status: 'prospect',
    criticality: 'low',
    contact_name: '',
    contact_email: '',
    contact_phone: '',
    contract_start_date: '',
    contract_end_date: '',
    contract_value: '',
    data_classification: null,
  })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Populate form when editing
  useEffect(() => {
    if (editVendor) {
      setFormData({
        name: editVendor.name,
        description: editVendor.description || '',
        website: editVendor.website || '',
        category: editVendor.category || '',
        status: editVendor.status,
        criticality: editVendor.criticality,
        contact_name: editVendor.contact_name || '',
        contact_email: editVendor.contact_email || '',
        contact_phone: editVendor.contact_phone || '',
        contract_start_date: editVendor.contract_start_date || '',
        contract_end_date: editVendor.contract_end_date || '',
        contract_value: editVendor.contract_value != null ? String(editVendor.contract_value) : '',
        data_classification: editVendor.data_classification || null,
      })
    }
  }, [editVendor])

  const handleChange = (field: keyof VendorInput, value: string | null) => {
    setFormData(prev => ({ ...prev, [field]: value }))
    setError(null)
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()

    if (!formData.name.trim()) {
      setError('Vendor name is required')
      return
    }

    setLoading(true)
    setError(null)

    // Build the payload, converting empty strings to null for optional fields
    const payload: VendorInput = {
      name: formData.name.trim(),
      description: formData.description?.trim() || null,
      website: formData.website?.trim() || null,
      category: formData.category?.trim() || null,
      status: formData.status,
      criticality: formData.criticality,
      contact_name: formData.contact_name?.trim() || null,
      contact_email: formData.contact_email?.trim() || null,
      contact_phone: formData.contact_phone?.trim() || null,
      contract_start_date: formData.contract_start_date || null,
      contract_end_date: formData.contract_end_date || null,
      contract_value: formData.contract_value?.trim() ? parseFloat(formData.contract_value.trim()) : null,
      data_classification: formData.data_classification || null,
    }

    try {
      if (isEditMode && editVendor) {
        await updateVendor(editVendor.id, payload, organizationId)
      } else {
        await createVendor(payload, organizationId)
      }
      onSuccess()
    } catch (err: any) {
      console.error('Failed to save vendor:', err)
      if (err?.status === 409 || err?.message?.includes('duplicate') || err?.message?.includes('already exists')) {
        setError('A vendor with this name already exists. Please choose a different name.')
      } else {
        setError(err.message || 'Failed to save vendor. Please try again.')
      }
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content vendor-modal" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h2>{isEditMode ? 'Edit Vendor' : 'Add New Vendor'}</h2>
          <button className="modal-close" onClick={onClose} aria-label="Close">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>

        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            <p className="modal-description">
              {isEditMode
                ? 'Update vendor details below.'
                : 'Register a third-party vendor for risk management and compliance tracking.'}
            </p>

            {/* Vendor Name */}
            <div className="form-group">
              <label htmlFor="vendor-name">Vendor Name *</label>
              <input
                id="vendor-name"
                type="text"
                value={formData.name}
                onChange={(e) => handleChange('name', e.target.value)}
                placeholder="e.g., Acme Cloud Services"
                disabled={loading}
                autoFocus
              />
            </div>

            {/* Description */}
            <div className="form-group">
              <label htmlFor="vendor-description">Description</label>
              <textarea
                id="vendor-description"
                value={formData.description || ''}
                onChange={(e) => handleChange('description', e.target.value)}
                placeholder="Brief description of the vendor and services provided..."
                rows={3}
                disabled={loading}
              />
            </div>

            {/* Website */}
            <div className="form-group">
              <label htmlFor="vendor-website">Website</label>
              <input
                id="vendor-website"
                type="url"
                value={formData.website || ''}
                onChange={(e) => handleChange('website', e.target.value)}
                placeholder="https://www.example.com"
                disabled={loading}
              />
            </div>

            {/* Category */}
            <div className="form-group">
              <label htmlFor="vendor-category">Category</label>
              <input
                id="vendor-category"
                type="text"
                value={formData.category || ''}
                onChange={(e) => handleChange('category', e.target.value)}
                placeholder="e.g., Cloud Infrastructure, SaaS, Consulting"
                disabled={loading}
              />
            </div>

            {/* Status and Criticality row */}
            <div className="form-row">
              <div className="form-group">
                <label htmlFor="vendor-status">Status</label>
                <select
                  id="vendor-status"
                  value={formData.status || 'prospect'}
                  onChange={(e) => handleChange('status', e.target.value)}
                  disabled={loading}
                >
                  {(Object.entries(VENDOR_STATUS_LABELS) as [VendorStatus, string][]).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </div>

              <div className="form-group">
                <label htmlFor="vendor-criticality">Criticality</label>
                <select
                  id="vendor-criticality"
                  value={formData.criticality || 'low'}
                  onChange={(e) => handleChange('criticality', e.target.value)}
                  disabled={loading}
                >
                  {(Object.entries(VENDOR_CRITICALITY_LABELS) as [VendorCriticality, string][]).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            {/* Data Classification */}
            <div className="form-group">
              <label htmlFor="vendor-data-classification">Data Classification</label>
              <select
                id="vendor-data-classification"
                value={formData.data_classification || ''}
                onChange={(e) => handleChange('data_classification', e.target.value || null)}
                disabled={loading}
              >
                <option value="">-- Select classification --</option>
                {(Object.entries(DATA_CLASSIFICATION_LABELS) as [DataClassification, string][]).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </div>

            {/* Section divider - Contact Information */}
            <div className="form-section-divider">
              <span>Contact Information</span>
            </div>

            {/* Contact Name */}
            <div className="form-group">
              <label htmlFor="vendor-contact-name">Contact Name</label>
              <input
                id="vendor-contact-name"
                type="text"
                value={formData.contact_name || ''}
                onChange={(e) => handleChange('contact_name', e.target.value)}
                placeholder="e.g., Jane Smith"
                disabled={loading}
              />
            </div>

            {/* Contact Email and Phone row */}
            <div className="form-row">
              <div className="form-group">
                <label htmlFor="vendor-contact-email">Contact Email</label>
                <input
                  id="vendor-contact-email"
                  type="email"
                  value={formData.contact_email || ''}
                  onChange={(e) => handleChange('contact_email', e.target.value)}
                  placeholder="contact@vendor.com"
                  disabled={loading}
                />
              </div>

              <div className="form-group">
                <label htmlFor="vendor-contact-phone">Contact Phone</label>
                <input
                  id="vendor-contact-phone"
                  type="text"
                  value={formData.contact_phone || ''}
                  onChange={(e) => handleChange('contact_phone', e.target.value)}
                  placeholder="+44 20 7946 0958"
                  disabled={loading}
                />
              </div>
            </div>

            {/* Section divider - Contract Details */}
            <div className="form-section-divider">
              <span>Contract Details</span>
            </div>

            {/* Contract Start and End Date row */}
            <div className="form-row">
              <div className="form-group">
                <label htmlFor="vendor-contract-start">Contract Start Date</label>
                <input
                  id="vendor-contract-start"
                  type="date"
                  value={formData.contract_start_date || ''}
                  onChange={(e) => handleChange('contract_start_date', e.target.value)}
                  disabled={loading}
                />
              </div>

              <div className="form-group">
                <label htmlFor="vendor-contract-end">Contract End Date</label>
                <input
                  id="vendor-contract-end"
                  type="date"
                  value={formData.contract_end_date || ''}
                  onChange={(e) => handleChange('contract_end_date', e.target.value)}
                  disabled={loading}
                />
              </div>
            </div>

            {/* Contract Value */}
            <div className="form-group">
              <label htmlFor="vendor-contract-value">Contract Value</label>
              <input
                id="vendor-contract-value"
                type="number"
                step="0.01"
                min="0"
                value={formData.contract_value || ''}
                onChange={(e) => handleChange('contract_value', e.target.value)}
                placeholder="e.g., 50000"
                disabled={loading}
              />
            </div>

            {error && (
              <div className="error-message">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <circle cx="12" cy="12" r="10" />
                  <line x1="12" y1="8" x2="12" y2="12" />
                  <line x1="12" y1="16" x2="12.01" y2="16" />
                </svg>
                {error}
              </div>
            )}
          </div>

          <div className="modal-footer">
            <button
              type="button"
              className="btn-secondary"
              onClick={onClose}
              disabled={loading}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="btn-primary"
              disabled={loading || !formData.name.trim()}
            >
              {loading ? (
                <>
                  <span className="spinner" />
                  Saving...
                </>
              ) : (
                <>
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z" />
                    <polyline points="17 21 17 13 7 13 7 21" />
                    <polyline points="7 3 7 8 15 8" />
                  </svg>
                  {isEditMode ? 'Save Changes' : 'Add Vendor'}
                </>
              )}
            </button>
          </div>
        </form>
      </div>

      <style>{`
        .modal-overlay {
          position: fixed;
          top: 0;
          left: 0;
          right: 0;
          bottom: 0;
          background: rgba(0, 0, 0, 0.5);
          display: flex;
          align-items: center;
          justify-content: center;
          z-index: 1000;
          animation: vendorFadeIn 0.15s ease;
        }

        @keyframes vendorFadeIn {
          from { opacity: 0; }
          to { opacity: 1; }
        }

        .modal-content.vendor-modal {
          background: var(--card);
          border-radius: 16px;
          width: 100%;
          max-width: 620px;
          margin: 20px;
          box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
          animation: vendorSlideUp 0.2s ease;
          max-height: 90vh;
          overflow-y: auto;
          border: 1px solid var(--border);
        }

        @keyframes vendorSlideUp {
          from {
            opacity: 0;
            transform: translateY(20px);
          }
          to {
            opacity: 1;
            transform: translateY(0);
          }
        }

        .modal-content.vendor-modal .modal-header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 20px 24px;
          border-bottom: 1px solid var(--border);
          position: sticky;
          top: 0;
          background: var(--card);
          border-radius: 16px 16px 0 0;
          z-index: 1;
        }

        .modal-content.vendor-modal .modal-header h2 {
          margin: 0;
          font-size: 18px;
          font-weight: 600;
          color: var(--text);
        }

        .modal-content.vendor-modal .modal-close {
          background: transparent;
          border: none;
          padding: 8px;
          cursor: pointer;
          color: var(--muted);
          border-radius: 8px;
          transition: all 0.15s;
        }

        .modal-content.vendor-modal .modal-close:hover {
          background: var(--secondary);
          color: var(--text);
        }

        .modal-content.vendor-modal .modal-body {
          padding: 24px;
        }

        .modal-content.vendor-modal .modal-description {
          margin: 0 0 20px 0;
          color: var(--muted);
          font-size: 14px;
          line-height: 1.5;
        }

        .modal-content.vendor-modal .form-group {
          margin-bottom: 20px;
        }

        .modal-content.vendor-modal .form-group label {
          display: block;
          margin-bottom: 8px;
          font-size: 14px;
          font-weight: 500;
          color: var(--muted);
        }

        .modal-content.vendor-modal .form-group input,
        .modal-content.vendor-modal .form-group select,
        .modal-content.vendor-modal .form-group textarea {
          width: 100%;
          padding: 12px;
          border: 1px solid var(--border);
          border-radius: 8px;
          font-size: 14px;
          transition: border-color 0.15s, box-shadow 0.15s;
          box-sizing: border-box;
          font-family: inherit;
          background: var(--panel);
          color: var(--text);
        }

        .modal-content.vendor-modal .form-group input:focus,
        .modal-content.vendor-modal .form-group select:focus,
        .modal-content.vendor-modal .form-group textarea:focus {
          outline: none;
          border-color: var(--primary);
          box-shadow: 0 0 0 3px color-mix(in srgb, var(--primary) 20%, transparent);
        }

        .modal-content.vendor-modal .form-group input::placeholder,
        .modal-content.vendor-modal .form-group textarea::placeholder {
          color: var(--muted);
        }

        .modal-content.vendor-modal .form-group textarea {
          resize: vertical;
          min-height: 80px;
        }

        .modal-content.vendor-modal .form-group select option {
          background: var(--card);
          color: var(--text);
        }

        .modal-content.vendor-modal .form-row {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 16px;
        }

        .modal-content.vendor-modal .form-section-divider {
          display: flex;
          align-items: center;
          gap: 12px;
          margin: 24px 0 20px 0;
          color: var(--muted);
          font-size: 13px;
          font-weight: 600;
          text-transform: uppercase;
          letter-spacing: 0.5px;
        }

        .modal-content.vendor-modal .form-section-divider::before,
        .modal-content.vendor-modal .form-section-divider::after {
          content: '';
          flex: 1;
          height: 1px;
          background: var(--border);
        }

        .modal-content.vendor-modal .error-message {
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 12px;
          background: color-mix(in srgb, var(--destructive) 10%, transparent);
          border: 1px solid color-mix(in srgb, var(--destructive) 30%, transparent);
          border-radius: 8px;
          color: var(--destructive);
          font-size: 14px;
        }

        .modal-content.vendor-modal .modal-footer {
          display: flex;
          justify-content: flex-end;
          gap: 12px;
          padding: 16px 24px;
          border-top: 1px solid var(--border);
          background: var(--panel);
          border-radius: 0 0 16px 16px;
          position: sticky;
          bottom: 0;
        }

        .modal-content.vendor-modal .btn-secondary,
        .modal-content.vendor-modal .btn-primary {
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 10px 20px;
          border-radius: 8px;
          font-size: 14px;
          font-weight: 500;
          cursor: pointer;
          transition: all 0.15s;
        }

        .modal-content.vendor-modal .btn-secondary {
          background: var(--secondary);
          border: 1px solid var(--border);
          color: var(--text);
        }

        .modal-content.vendor-modal .btn-secondary:hover:not(:disabled) {
          background: var(--panel);
          border-color: var(--muted);
        }

        .modal-content.vendor-modal .btn-primary {
          background: var(--primary);
          border: none;
          color: var(--primary-foreground);
        }

        .modal-content.vendor-modal .btn-primary:hover:not(:disabled) {
          background: var(--primary-hover);
        }

        .modal-content.vendor-modal .btn-primary:disabled,
        .modal-content.vendor-modal .btn-secondary:disabled {
          opacity: 0.6;
          cursor: not-allowed;
        }

        .modal-content.vendor-modal .spinner {
          width: 16px;
          height: 16px;
          border: 2px solid rgba(255, 255, 255, 0.3);
          border-top-color: var(--primary-foreground);
          border-radius: 50%;
          animation: vendorSpin 0.6s linear infinite;
        }

        @keyframes vendorSpin {
          to { transform: rotate(360deg); }
        }

        @media (max-width: 600px) {
          .modal-content.vendor-modal .form-row {
            grid-template-columns: 1fr;
            gap: 0;
          }
        }
      `}</style>
    </div>
  )
}

export default VendorModal
