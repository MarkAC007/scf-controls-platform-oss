import React, { useState, useEffect } from 'react'
import { createSystem, updateSystem } from '../data/apiClient'
import type { System, SystemInput, SystemType, SystemStatus } from '../types'

interface AddSystemModalProps {
  organizationId?: string
  editSystem?: System | null  // If provided, modal is in edit mode
  onClose: () => void
  onSuccess: () => void
}

const systemTypes: { value: SystemType; label: string; description: string }[] = [
  { value: 'cloud_provider', label: 'Cloud Provider', description: 'AWS, Azure, GCP, etc.' },
  { value: 'identity_provider', label: 'Identity Provider', description: 'Okta, Azure AD, Auth0, etc.' },
  { value: 'ticketing', label: 'Ticketing System', description: 'Jira, ServiceNow, etc.' },
  { value: 'logging', label: 'Logging Platform', description: 'Splunk, Datadog, ELK, etc.' },
  { value: 'security_tool', label: 'Security Tool', description: 'SIEM, vulnerability scanners, etc.' },
  { value: 'code_repository', label: 'Code Repository', description: 'GitHub, GitLab, Bitbucket, etc.' },
  { value: 'document_management', label: 'Document Management', description: 'Confluence, SharePoint, etc.' },
  { value: 'custom', label: 'Custom', description: 'Other systems not listed above' },
]

const systemStatuses: { value: SystemStatus; label: string }[] = [
  { value: 'active', label: 'Active' },
  { value: 'inactive', label: 'Inactive' },
  { value: 'deprecated', label: 'Deprecated' },
]

export const AddSystemModal: React.FC<AddSystemModalProps> = ({
  organizationId,
  editSystem,
  onClose,
  onSuccess,
}) => {
  const isEditMode = !!editSystem

  const [formData, setFormData] = useState<SystemInput>({
    name: '',
    system_type: 'custom',
    category: '',
    description: '',
    vendor: '',
    status: 'active',
    connection_config: {},
  })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Populate form when editing
  useEffect(() => {
    if (editSystem) {
      setFormData({
        name: editSystem.name,
        system_type: editSystem.system_type,
        category: editSystem.category || '',
        description: editSystem.description || '',
        vendor: editSystem.vendor || '',
        status: editSystem.status,
        connection_config: editSystem.connection_config || {},
      })
    }
  }, [editSystem])

  const handleChange = (field: keyof SystemInput, value: string) => {
    setFormData(prev => ({ ...prev, [field]: value }))
    setError(null)
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()

    if (!formData.name.trim()) {
      setError('System name is required')
      return
    }

    setLoading(true)
    setError(null)

    try {
      if (isEditMode && editSystem) {
        await updateSystem(editSystem.id, formData, organizationId)
      } else {
        await createSystem(formData, organizationId)
      }
      onSuccess()
    } catch (err: any) {
      console.error('Failed to save system:', err)
      setError(err.message || 'Failed to save system')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content system-modal" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h2>{isEditMode ? 'Edit System' : 'Add New System'}</h2>
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
                ? 'Update system information below.'
                : 'Register a system that collects evidence for compliance controls.'}
            </p>

            {/* Name */}
            <div className="form-group">
              <label htmlFor="name">System Name *</label>
              <input
                id="name"
                type="text"
                value={formData.name}
                onChange={(e) => handleChange('name', e.target.value)}
                placeholder="e.g., AWS Production Account"
                disabled={loading}
                autoFocus
              />
            </div>

            {/* System Type */}
            <div className="form-group">
              <label htmlFor="system_type">System Type *</label>
              <select
                id="system_type"
                value={formData.system_type}
                onChange={(e) => handleChange('system_type', e.target.value)}
                disabled={loading}
              >
                {systemTypes.map(type => (
                  <option key={type.value} value={type.value}>
                    {type.label} - {type.description}
                  </option>
                ))}
              </select>
            </div>

            {/* Vendor */}
            <div className="form-group">
              <label htmlFor="vendor">Vendor</label>
              <input
                id="vendor"
                type="text"
                value={formData.vendor || ''}
                onChange={(e) => handleChange('vendor', e.target.value)}
                placeholder="e.g., Amazon Web Services"
                disabled={loading}
              />
            </div>

            {/* Category */}
            <div className="form-group">
              <label htmlFor="category">Category</label>
              <input
                id="category"
                type="text"
                value={formData.category || ''}
                onChange={(e) => handleChange('category', e.target.value)}
                placeholder="e.g., Infrastructure, Security, Monitoring"
                disabled={loading}
              />
            </div>

            {/* Description */}
            <div className="form-group">
              <label htmlFor="description">Description</label>
              <textarea
                id="description"
                value={formData.description || ''}
                onChange={(e) => handleChange('description', e.target.value)}
                placeholder="Brief description of the system and its role..."
                rows={3}
                disabled={loading}
              />
            </div>

            {/* Status */}
            <div className="form-group">
              <label htmlFor="status">Status</label>
              <select
                id="status"
                value={formData.status || 'active'}
                onChange={(e) => handleChange('status', e.target.value)}
                disabled={loading}
              >
                {systemStatuses.map(status => (
                  <option key={status.value} value={status.value}>
                    {status.label}
                  </option>
                ))}
              </select>
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
                  {isEditMode ? 'Save Changes' : 'Add System'}
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
          animation: fadeIn 0.15s ease;
        }

        @keyframes fadeIn {
          from { opacity: 0; }
          to { opacity: 1; }
        }

        .modal-content.system-modal {
          background: var(--card);
          border-radius: 16px;
          width: 100%;
          max-width: 560px;
          margin: 20px;
          box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
          animation: slideUp 0.2s ease;
          max-height: 90vh;
          overflow-y: auto;
          border: 1px solid var(--border);
        }

        @keyframes slideUp {
          from {
            opacity: 0;
            transform: translateY(20px);
          }
          to {
            opacity: 1;
            transform: translateY(0);
          }
        }

        .modal-header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 20px 24px;
          border-bottom: 1px solid var(--border);
          position: sticky;
          top: 0;
          background: var(--card);
          border-radius: 16px 16px 0 0;
        }

        .modal-header h2 {
          margin: 0;
          font-size: 18px;
          font-weight: 600;
          color: var(--text);
        }

        .modal-close {
          background: transparent;
          border: none;
          padding: 8px;
          cursor: pointer;
          color: var(--muted);
          border-radius: 8px;
          transition: all 0.15s;
        }

        .modal-close:hover {
          background: var(--secondary);
          color: var(--text);
        }

        .modal-body {
          padding: 24px;
        }

        .modal-description {
          margin: 0 0 20px 0;
          color: var(--muted);
          font-size: 14px;
          line-height: 1.5;
        }

        .form-group {
          margin-bottom: 20px;
        }

        .form-group label {
          display: block;
          margin-bottom: 8px;
          font-size: 14px;
          font-weight: 500;
          color: var(--muted);
        }

        .form-group input,
        .form-group select,
        .form-group textarea {
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

        .form-group input:focus,
        .form-group select:focus,
        .form-group textarea:focus {
          outline: none;
          border-color: #3b82f6;
          box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.2);
        }

        .form-group input::placeholder,
        .form-group textarea::placeholder {
          color: var(--muted);
        }

        .form-group textarea {
          resize: vertical;
          min-height: 80px;
        }

        .form-group select option {
          background: var(--card);
          color: var(--text);
        }

        .error-message {
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 12px;
          background: rgba(220, 38, 38, 0.1);
          border: 1px solid rgba(220, 38, 38, 0.3);
          border-radius: 8px;
          color: #f87171;
          font-size: 14px;
        }

        .modal-footer {
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

        .btn-secondary,
        .btn-primary {
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

        .btn-secondary {
          background: var(--secondary);
          border: 1px solid var(--border);
          color: var(--text);
        }

        .btn-secondary:hover:not(:disabled) {
          background: var(--panel);
          border-color: var(--muted);
        }

        .btn-primary {
          background: #1976d2;
          border: none;
          color: white;
        }

        .btn-primary:hover:not(:disabled) {
          background: #1565c0;
        }

        .btn-primary:disabled,
        .btn-secondary:disabled {
          opacity: 0.6;
          cursor: not-allowed;
        }

        .spinner {
          width: 16px;
          height: 16px;
          border: 2px solid rgba(255, 255, 255, 0.3);
          border-top-color: white;
          border-radius: 50%;
          animation: spin 0.6s linear infinite;
        }

        @keyframes spin {
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  )
}

export default AddSystemModal
