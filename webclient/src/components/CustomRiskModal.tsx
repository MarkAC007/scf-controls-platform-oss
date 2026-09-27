/**
 * CustomRiskModal — the Add Custom Risk form.
 *
 * RiskDashboard shows it over both the list and the detail view. Mounted only
 * while open, so focus moves into it on open and back to the opener on close
 * (`useDialogFocus`); Escape and scroll lock come from the dashboard's
 * `useModalDismiss`.
 */
import { useId, useRef, type Dispatch, type SetStateAction } from 'react'
import { useDialogFocus } from '../hooks/useDialogFocus'

export interface CustomRiskForm {
  title: string
  description: string
  category_name: string
  category_color: string
}

interface CustomRiskModalProps {
  form: CustomRiskForm
  setForm: Dispatch<SetStateAction<CustomRiskForm>>
  creating: boolean
  onCreate: () => void
  onClose: () => void
}

export default function CustomRiskModal({ form, setForm, creating, onCreate, onClose }: CustomRiskModalProps) {
  const dialogRef = useRef<HTMLDivElement>(null)
  useDialogFocus(dialogRef)
  const id = useId()

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        ref={dialogRef}
        className="modal-content custom-risk-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${id}-heading`}
        onClick={e => e.stopPropagation()}
      >
        <div className="modal-header">
          <h2 id={`${id}-heading`}>Add Custom Risk</h2>
          <button className="modal-close" onClick={onClose} aria-label="Close">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
        <div className="modal-body">
          <div className="form-group">
            <label htmlFor={`${id}-title`}>Title *</label>
            <input
              id={`${id}-title`}
              type="text"
              value={form.title}
              onChange={e => setForm(f => ({ ...f, title: e.target.value }))}
              placeholder="e.g., Physical Security Breach"
              maxLength={100}
              data-autofocus
            />
          </div>
          <div className="form-group">
            <label htmlFor={`${id}-description`}>Description *</label>
            <textarea
              id={`${id}-description`}
              value={form.description}
              onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
              placeholder="Describe the risk scenario..."
              rows={3}
            />
          </div>
          <div className="form-group">
            <label htmlFor={`${id}-category`}>Category Name</label>
            <input
              id={`${id}-category`}
              type="text"
              value={form.category_name}
              onChange={e => setForm(f => ({ ...f, category_name: e.target.value }))}
              placeholder="e.g., Physical Security"
              maxLength={50}
            />
          </div>
          <div className="form-group">
            <label htmlFor={`${id}-color`}>Category Color</label>
            <input
              id={`${id}-color`}
              type="color"
              value={form.category_color}
              onChange={e => setForm(f => ({ ...f, category_color: e.target.value }))}
            />
          </div>
        </div>
        <div className="modal-footer">
          <button className="btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button
            className="btn-primary"
            onClick={onCreate}
            disabled={creating || !form.title.trim() || !form.description.trim()}
          >
            {creating ? 'Creating...' : 'Create Risk'}
          </button>
        </div>
      </div>
    </div>
  )
}
