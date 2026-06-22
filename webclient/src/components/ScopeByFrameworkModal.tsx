import React, { useState, useEffect, useMemo } from 'react'
import { fetchFrameworks, type FrameworkInfo } from '../data/catalogApi'
import {
  bulkScopeByFramework,
  bulkUnscopeByFramework,
  resetAllScope,
  type BulkScopeFrameworkResponse,
  type BulkUnscopeFrameworkResponse,
  type ResetScopeResponse,
} from '../data/apiClient'

type ModalMode = 'add' | 'remove'

interface ScopeByFrameworkModalProps {
  organizationId?: string
  existingScopedCount: number
  initialMode?: ModalMode
  onClose: () => void
  onSuccess: (result: BulkScopeFrameworkResponse | BulkUnscopeFrameworkResponse | ResetScopeResponse) => void
}

export const ScopeByFrameworkModal: React.FC<ScopeByFrameworkModalProps> = ({
  organizationId,
  existingScopedCount,
  initialMode = 'add',
  onClose,
  onSuccess,
}) => {
  const [mode, setMode] = useState<ModalMode>(initialMode)
  const [frameworks, setFrameworks] = useState<FrameworkInfo[]>([])
  const [selectedFrameworks, setSelectedFrameworks] = useState<Set<string>>(new Set())
  const [reason, setReason] = useState('')
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [showResetConfirm, setShowResetConfirm] = useState(false)
  const [resetConfirmText, setResetConfirmText] = useState('')

  // Load frameworks on mount
  useEffect(() => {
    async function loadFrameworks() {
      try {
        const data = await fetchFrameworks(false)
        setFrameworks(data)
      } catch (err: any) {
        console.error('Failed to load frameworks:', err)
        setError('Failed to load frameworks. Please try again.')
      } finally {
        setLoading(false)
      }
    }
    loadFrameworks()
  }, [])

  // Clear selection when switching modes
  const handleModeSwitch = (newMode: ModalMode) => {
    setMode(newMode)
    setSelectedFrameworks(new Set())
    setReason('')
    setError(null)
  }

  // Filter frameworks by search query
  const filteredFrameworks = useMemo(() => {
    if (!searchQuery) return frameworks
    const q = searchQuery.toLowerCase()
    return frameworks.filter(fw =>
      fw.name.toLowerCase().includes(q) ||
      fw.id.toLowerCase().includes(q)
    )
  }, [frameworks, searchQuery])

  // Calculate preview stats
  const selectedStats = useMemo(() => {
    const selectedList = frameworks.filter(fw => selectedFrameworks.has(fw.id))
    const totalControls = selectedList.reduce((sum, fw) => sum + fw.control_count, 0)
    return {
      frameworkCount: selectedList.length,
      controlCount: totalControls,
      note: selectedList.length > 1 ? '(may include overlapping controls)' : ''
    }
  }, [frameworks, selectedFrameworks])

  // Toggle framework selection
  const toggleFramework = (frameworkId: string) => {
    setSelectedFrameworks(prev => {
      const next = new Set(prev)
      if (next.has(frameworkId)) {
        next.delete(frameworkId)
      } else {
        next.add(frameworkId)
      }
      return next
    })
  }

  // Handle form submission
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()

    if (selectedFrameworks.size === 0) {
      setError('Please select at least one framework')
      return
    }

    setSubmitting(true)
    setError(null)

    try {
      if (mode === 'add') {
        const result = await bulkScopeByFramework({
          frameworks: Array.from(selectedFrameworks),
          selection_reason: reason || undefined
        }, organizationId)
        onSuccess(result)
      } else {
        const result = await bulkUnscopeByFramework({
          frameworks: Array.from(selectedFrameworks),
          removal_reason: reason || undefined
        }, organizationId)
        onSuccess(result)
      }
    } catch (err: any) {
      console.error(`Bulk ${mode} scope failed:`, err)
      setError(err.message || `Failed to ${mode === 'add' ? 'scope' : 'un-scope'} controls. Please try again.`)
    } finally {
      setSubmitting(false)
    }
  }

  // Select/deselect all visible frameworks
  const selectAllVisible = () => {
    setSelectedFrameworks(prev => {
      const next = new Set(prev)
      filteredFrameworks.forEach(fw => next.add(fw.id))
      return next
    })
  }

  const deselectAllVisible = () => {
    setSelectedFrameworks(prev => {
      const next = new Set(prev)
      filteredFrameworks.forEach(fw => next.delete(fw.id))
      return next
    })
  }

  // Handle full scope reset
  const handleResetAllScope = async () => {
    if (resetConfirmText !== 'REMOVE ALL') return

    setSubmitting(true)
    setError(null)

    try {
      const result = await resetAllScope(organizationId)
      setShowResetConfirm(false)
      setResetConfirmText('')
      onSuccess(result)
    } catch (err: any) {
      console.error('Reset scope failed:', err)
      setError(err.message || 'Failed to reset scope. Please try again.')
    } finally {
      setSubmitting(false)
    }
  }

  const isRemoveMode = mode === 'remove'

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content framework-modal" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h2>{isRemoveMode ? 'Remove from Scope' : 'Add to Scope'}</h2>
          <button className="modal-close" onClick={onClose} aria-label="Close">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>

        {/* Mode toggle tabs */}
        <div className="modal-tabs">
          <button
            type="button"
            className={`modal-tab ${mode === 'add' ? 'active' : ''}`}
            onClick={() => handleModeSwitch('add')}
            disabled={submitting}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="12" y1="5" x2="12" y2="19" />
              <line x1="5" y1="12" x2="19" y2="12" />
            </svg>
            Add to Scope
          </button>
          <button
            type="button"
            className={`modal-tab remove ${mode === 'remove' ? 'active' : ''}`}
            onClick={() => handleModeSwitch('remove')}
            disabled={submitting}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="5" y1="12" x2="19" y2="12" />
            </svg>
            Remove from Scope
          </button>
        </div>

        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            <p className="modal-description">
              {isRemoveMode ? (
                <>
                  Select frameworks to remove their controls from scope.
                  Controls shared with other in-scope frameworks will be <strong>protected</strong> and not removed.
                </>
              ) : (
                <>
                  Select one or more frameworks to automatically add all their mapped controls to your scope.
                  Existing in-scope controls will not be modified.
                </>
              )}
            </p>

            {/* Search */}
            <div className="framework-search">
              <input
                type="text"
                value={searchQuery}
                onChange={e => setSearchQuery(e.target.value)}
                placeholder="Search frameworks..."
                disabled={loading}
              />
            </div>

            {/* Quick actions */}
            <div className="framework-actions">
              <button type="button" className="btn-link" onClick={selectAllVisible} disabled={loading}>
                Select All
              </button>
              <button type="button" className="btn-link" onClick={deselectAllVisible} disabled={loading}>
                Deselect All
              </button>
              <span className="framework-count">
                {selectedFrameworks.size} selected
              </span>
            </div>

            {/* Framework list */}
            <div className="framework-list">
              {loading ? (
                <div className="framework-loading">
                  <span className="spinner"></span>
                  Loading frameworks...
                </div>
              ) : filteredFrameworks.length === 0 ? (
                <div className="framework-empty">
                  {searchQuery ? 'No frameworks match your search' : 'No frameworks available'}
                </div>
              ) : (
                filteredFrameworks.map(fw => (
                  <label
                    key={fw.id}
                    className={`framework-item ${selectedFrameworks.has(fw.id) ? 'selected' : ''} ${isRemoveMode && selectedFrameworks.has(fw.id) ? 'selected-remove' : ''}`}
                  >
                    <input
                      type="checkbox"
                      checked={selectedFrameworks.has(fw.id)}
                      onChange={() => toggleFramework(fw.id)}
                      disabled={submitting}
                    />
                    <div className="framework-info">
                      <div className="framework-name">{fw.name}</div>
                      <div className="framework-id">{fw.id}</div>
                    </div>
                    <div className="framework-control-count">
                      {fw.control_count} controls
                    </div>
                  </label>
                ))
              )}
            </div>

            {/* Reason */}
            {selectedFrameworks.size > 0 && (
              <div className="form-group selection-reason">
                <label htmlFor="scope-reason">
                  {isRemoveMode ? 'Removal Reason (optional)' : 'Selection Reason (optional)'}
                </label>
                <input
                  id="scope-reason"
                  type="text"
                  value={reason}
                  onChange={e => setReason(e.target.value)}
                  placeholder={isRemoveMode
                    ? 'e.g., No longer pursuing ISO 27017 certification'
                    : 'e.g., Required by ISO 27001:2022 certification'
                  }
                  disabled={submitting}
                />
              </div>
            )}

            {/* Preview */}
            {selectedFrameworks.size > 0 && (
              <div className={`scope-preview ${isRemoveMode ? 'scope-preview-remove' : ''}`}>
                <div className="preview-icon">
                  {isRemoveMode ? (
                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <path d="M12 9v2m0 4h.01M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" />
                    </svg>
                  ) : (
                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <circle cx="12" cy="12" r="10" />
                      <line x1="12" y1="16" x2="12" y2="12" />
                      <line x1="12" y1="8" x2="12.01" y2="8" />
                    </svg>
                  )}
                </div>
                <div className="preview-text">
                  {isRemoveMode ? (
                    <>
                      Up to <strong>{selectedStats.controlCount}</strong> controls across{' '}
                      <strong>{selectedStats.frameworkCount}</strong> framework{selectedStats.frameworkCount !== 1 ? 's' : ''}{' '}
                      will be evaluated. Controls shared with other in-scope frameworks will be protected.
                      {selectedStats.note && <span className="preview-note"> {selectedStats.note}</span>}
                    </>
                  ) : (
                    <>
                      <strong>{selectedStats.controlCount}</strong> controls across{' '}
                      <strong>{selectedStats.frameworkCount}</strong> framework{selectedStats.frameworkCount !== 1 ? 's' : ''}{' '}
                      will be checked and added to scope if not already present.
                      {selectedStats.note && <span className="preview-note"> {selectedStats.note}</span>}
                    </>
                  )}
                </div>
              </div>
            )}

            {/* Reset All Scope — destructive action */}
            {isRemoveMode && existingScopedCount > 0 && (
              <div className="reset-scope-section">
                <div className="reset-divider">
                  <span>Danger Zone</span>
                </div>
                {!showResetConfirm ? (
                  <button
                    type="button"
                    className="btn-reset-trigger"
                    onClick={() => setShowResetConfirm(true)}
                    disabled={submitting}
                  >
                    Remove All Controls from Scope ({existingScopedCount})
                  </button>
                ) : (
                  <div className="reset-confirm-panel">
                    <p className="reset-warning">
                      This will remove <strong>all {existingScopedCount} controls</strong> from scope.
                      Implementation data will be preserved but no controls will be in scope.
                    </p>
                    <div className="reset-confirm-input">
                      <label htmlFor="reset-confirm">Type <strong>REMOVE ALL</strong> to confirm:</label>
                      <input
                        id="reset-confirm"
                        type="text"
                        value={resetConfirmText}
                        onChange={e => setResetConfirmText(e.target.value)}
                        placeholder="REMOVE ALL"
                        disabled={submitting}
                        autoFocus
                      />
                    </div>
                    <div className="reset-confirm-actions">
                      <button
                        type="button"
                        className="btn-secondary btn-small"
                        onClick={() => { setShowResetConfirm(false); setResetConfirmText('') }}
                        disabled={submitting}
                      >
                        Cancel
                      </button>
                      <button
                        type="button"
                        className="btn-reset-confirm"
                        onClick={handleResetAllScope}
                        disabled={submitting || resetConfirmText !== 'REMOVE ALL'}
                      >
                        {submitting ? 'Removing...' : 'Remove All from Scope'}
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )}

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
              disabled={submitting}
            >
              Cancel
            </button>
            <button
              type="submit"
              className={isRemoveMode ? 'btn-danger' : 'btn-primary'}
              disabled={submitting || selectedFrameworks.size === 0}
            >
              {submitting ? (
                <>
                  <span className="spinner" />
                  {isRemoveMode ? 'Removing...' : 'Adding Controls...'}
                </>
              ) : (
                <>
                  {isRemoveMode ? (
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <line x1="5" y1="12" x2="19" y2="12" />
                    </svg>
                  ) : (
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <polyline points="20 6 9 17 4 12" />
                    </svg>
                  )}
                  {isRemoveMode ? 'Remove from Scope' : 'Add to Scope'}
                </>
              )}
            </button>
          </div>
        </form>

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

          .modal-content.framework-modal {
            background: var(--card);
            border-radius: 16px;
            width: 100%;
            max-width: 640px;
            margin: 20px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
            animation: slideUp 0.2s ease;
            max-height: 90vh;
            display: flex;
            flex-direction: column;
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
            padding: 20px 24px 0 24px;
            flex-shrink: 0;
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

          .modal-tabs {
            display: flex;
            gap: 0;
            padding: 16px 24px 0 24px;
            border-bottom: 1px solid var(--border);
          }

          .modal-tab {
            display: flex;
            align-items: center;
            gap: 6px;
            padding: 10px 16px;
            background: none;
            border: none;
            border-bottom: 2px solid transparent;
            color: var(--muted);
            font-size: 14px;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.15s;
            margin-bottom: -1px;
          }

          .modal-tab:hover:not(:disabled) {
            color: var(--text);
          }

          .modal-tab.active {
            color: #3b82f6;
            border-bottom-color: #3b82f6;
          }

          .modal-tab.remove.active {
            color: #ef4444;
            border-bottom-color: #ef4444;
          }

          .modal-tab:disabled {
            opacity: 0.5;
            cursor: not-allowed;
          }

          .modal-body {
            padding: 24px;
            overflow-y: auto;
            flex: 1;
          }

          .modal-description {
            margin: 0 0 20px 0;
            color: var(--muted);
            font-size: 14px;
            line-height: 1.5;
          }

          .framework-search {
            margin-bottom: 12px;
          }

          .framework-search input {
            width: 100%;
            padding: 10px 12px;
            border: 1px solid var(--border);
            border-radius: 8px;
            font-size: 14px;
            background: var(--panel);
            color: var(--text);
            box-sizing: border-box;
          }

          .framework-search input:focus {
            outline: none;
            border-color: #3b82f6;
            box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.2);
          }

          .framework-search input::placeholder {
            color: var(--muted);
          }

          .framework-actions {
            display: flex;
            align-items: center;
            gap: 12px;
            margin-bottom: 12px;
            padding-bottom: 12px;
            border-bottom: 1px solid var(--border);
          }

          .btn-link {
            background: none;
            border: none;
            color: #3b82f6;
            cursor: pointer;
            font-size: 13px;
            padding: 4px 8px;
            border-radius: 4px;
            transition: background 0.15s;
          }

          .btn-link:hover:not(:disabled) {
            background: rgba(59, 130, 246, 0.1);
          }

          .btn-link:disabled {
            opacity: 0.5;
            cursor: not-allowed;
          }

          .framework-count {
            margin-left: auto;
            font-size: 13px;
            color: var(--muted);
          }

          .framework-list {
            max-height: 300px;
            overflow-y: auto;
            border: 1px solid var(--border);
            border-radius: 8px;
            margin-bottom: 16px;
          }

          .framework-loading,
          .framework-empty {
            padding: 40px 20px;
            text-align: center;
            color: var(--muted);
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 12px;
          }

          .framework-item {
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 12px 16px;
            border-bottom: 1px solid var(--border);
            cursor: pointer;
            transition: background 0.15s;
          }

          .framework-item:last-child {
            border-bottom: none;
          }

          .framework-item:hover {
            background: var(--secondary);
          }

          .framework-item.selected {
            background: rgba(59, 130, 246, 0.1);
          }

          .framework-item.selected-remove {
            background: rgba(239, 68, 68, 0.08);
          }

          .framework-item input[type="checkbox"] {
            width: 18px;
            height: 18px;
            flex-shrink: 0;
            accent-color: #3b82f6;
          }

          .framework-item.selected-remove input[type="checkbox"] {
            accent-color: #ef4444;
          }

          .framework-info {
            flex: 1;
            min-width: 0;
          }

          .framework-name {
            font-size: 14px;
            font-weight: 500;
            color: var(--text);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
          }

          .framework-id {
            font-size: 12px;
            color: var(--muted);
            font-family: monospace;
          }

          .framework-control-count {
            font-size: 13px;
            color: var(--muted);
            white-space: nowrap;
            flex-shrink: 0;
          }

          .selection-reason {
            margin-top: 16px;
          }

          .form-group label {
            display: block;
            margin-bottom: 8px;
            font-size: 14px;
            font-weight: 500;
            color: var(--muted);
          }

          .form-group input {
            width: 100%;
            padding: 10px 12px;
            border: 1px solid var(--border);
            border-radius: 8px;
            font-size: 14px;
            background: var(--panel);
            color: var(--text);
            box-sizing: border-box;
          }

          .form-group input:focus {
            outline: none;
            border-color: #3b82f6;
            box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.2);
          }

          .form-group input::placeholder {
            color: var(--muted);
          }

          .scope-preview {
            display: flex;
            gap: 12px;
            padding: 16px;
            background: rgba(59, 130, 246, 0.08);
            border: 1px solid rgba(59, 130, 246, 0.2);
            border-radius: 8px;
            margin-top: 16px;
          }

          .scope-preview-remove {
            background: rgba(239, 68, 68, 0.08);
            border-color: rgba(239, 68, 68, 0.2);
          }

          .scope-preview-remove .preview-icon {
            color: #ef4444;
          }

          .preview-icon {
            flex-shrink: 0;
            color: #3b82f6;
          }

          .preview-text {
            font-size: 14px;
            color: var(--text);
            line-height: 1.5;
          }

          .preview-note {
            color: var(--muted);
            font-style: italic;
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
            margin-top: 16px;
          }

          .modal-footer {
            display: flex;
            justify-content: flex-end;
            gap: 12px;
            padding: 16px 24px;
            border-top: 1px solid var(--border);
            background: var(--panel);
            border-radius: 0 0 16px 16px;
            flex-shrink: 0;
          }

          .btn-secondary,
          .btn-primary,
          .btn-danger {
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

          .btn-danger {
            background: #dc2626;
            border: none;
            color: white;
          }

          .btn-danger:hover:not(:disabled) {
            background: #b91c1c;
          }

          .btn-primary:disabled,
          .btn-secondary:disabled,
          .btn-danger:disabled {
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

          .framework-loading .spinner {
            width: 24px;
            height: 24px;
            border-color: rgba(59, 130, 246, 0.3);
            border-top-color: #3b82f6;
          }

          @keyframes spin {
            to { transform: rotate(360deg); }
          }

          /* Reset scope — danger zone */
          .reset-scope-section {
            margin-top: 24px;
          }

          .reset-divider {
            display: flex;
            align-items: center;
            gap: 12px;
            margin-bottom: 16px;
          }

          .reset-divider::before,
          .reset-divider::after {
            content: '';
            flex: 1;
            height: 1px;
            background: rgba(239, 68, 68, 0.3);
          }

          .reset-divider span {
            font-size: 12px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: #ef4444;
          }

          .btn-reset-trigger {
            width: 100%;
            padding: 10px 16px;
            background: transparent;
            border: 1px dashed rgba(239, 68, 68, 0.4);
            border-radius: 8px;
            color: #ef4444;
            font-size: 13px;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.15s;
          }

          .btn-reset-trigger:hover:not(:disabled) {
            background: rgba(239, 68, 68, 0.08);
            border-style: solid;
          }

          .btn-reset-trigger:disabled {
            opacity: 0.5;
            cursor: not-allowed;
          }

          .reset-confirm-panel {
            padding: 16px;
            background: rgba(239, 68, 68, 0.06);
            border: 1px solid rgba(239, 68, 68, 0.25);
            border-radius: 8px;
          }

          .reset-warning {
            margin: 0 0 12px 0;
            font-size: 13px;
            color: #ef4444;
            line-height: 1.5;
          }

          .reset-confirm-input {
            margin-bottom: 12px;
          }

          .reset-confirm-input label {
            display: block;
            margin-bottom: 6px;
            font-size: 13px;
            color: var(--muted);
          }

          .reset-confirm-input input {
            width: 100%;
            padding: 8px 12px;
            border: 1px solid rgba(239, 68, 68, 0.3);
            border-radius: 6px;
            font-size: 14px;
            font-family: monospace;
            background: var(--panel);
            color: var(--text);
            box-sizing: border-box;
          }

          .reset-confirm-input input:focus {
            outline: none;
            border-color: #ef4444;
            box-shadow: 0 0 0 3px rgba(239, 68, 68, 0.15);
          }

          .reset-confirm-actions {
            display: flex;
            justify-content: flex-end;
            gap: 8px;
          }

          .btn-small {
            padding: 6px 14px !important;
            font-size: 13px !important;
          }

          .btn-reset-confirm {
            display: flex;
            align-items: center;
            gap: 6px;
            padding: 6px 14px;
            border-radius: 6px;
            font-size: 13px;
            font-weight: 500;
            cursor: pointer;
            background: #dc2626;
            border: none;
            color: white;
            transition: all 0.15s;
          }

          .btn-reset-confirm:hover:not(:disabled) {
            background: #b91c1c;
          }

          .btn-reset-confirm:disabled {
            opacity: 0.4;
            cursor: not-allowed;
          }
        `}</style>
      </div>
    </div>
  )
}

export default ScopeByFrameworkModal
