/**
 * ScopingBulkBar — floating bulk-action bar for the Control Scoping list view.
 *
 * Appears when row checkboxes have a non-empty selection. Exposes:
 *   - Selection count + select-all-visible shortcut
 *   - Set applicable (selected=true)
 *   - Set N/A (selected=false)
 *   - Assign owner (dropdown of the org's teams — claims the ACCOUNTABLE
 *     team through the team system; the legacy free-text owner list is gone)
 *   - Clear selection
 *
 * Mirrors EvidenceBulkActionsBar's interaction idioms (ruling 1):
 *   - Selects reset to placeholder after firing (they are commands, not bound values)
 *   - Busy state disables all controls and shows progress text
 *   - Progress text is supplied by the parent ("Updating 3 of 12…")
 */
import { useState, type JSX } from 'react'

export interface TeamOption {
  /** Team id from the team system (Users → Teams). */
  value: string
  label: string
}

export interface ScopingBulkBarProps {
  selectedCount: number
  /** Count of currently visible/loaded controls (for select-all label). */
  visibleCount: number
  /** True when every visible control is already checked. */
  allVisibleSelected: boolean
  /**
   * The org's teams for the Assign-owner action. `null` hides the control
   * entirely (non-admins cannot write assignments); `[]` renders it disabled
   * with a create-teams hint.
   */
  teamOptions: TeamOption[] | null
  /** True while a bulk operation is in flight. */
  busy?: boolean
  /** Optional progress message shown while busy ("Updating 3 of 12…"). */
  progressText?: string
  onSelectAllVisible: () => void
  onSetApplicable: () => void
  onSetNA: () => void
  /** Called with the chosen team id; the page claims it accountable. */
  onAssignOwner: (teamId: string) => void
  onClear: () => void
}

export default function ScopingBulkBar({
  selectedCount,
  visibleCount,
  allVisibleSelected,
  teamOptions,
  busy = false,
  progressText,
  onSelectAllVisible,
  onSetApplicable,
  onSetNA,
  onAssignOwner,
  onClear,
}: ScopingBulkBarProps): JSX.Element {
  // Owner select is a command — reset to placeholder after firing
  const [owner, setOwner] = useState('')

  return (
    <div className="scoping-bulk-bar" role="group" aria-label="Bulk actions">
      <div className="scoping-bulk-bar-row">
        {/* Count */}
        <span className="scoping-bulk-count">{selectedCount} selected</span>

        {/* Select-all / clear-selection toggle */}
        <button
          type="button"
          className="btn btn-sm btn-secondary"
          onClick={allVisibleSelected ? onClear : onSelectAllVisible}
          disabled={visibleCount === 0}
        >
          {allVisibleSelected
            ? 'Clear selection'
            : `Select all ${visibleCount} shown`}
        </button>

        {/* Standalone Clear — shown when partial selection (not all-visible) */}
        {!allVisibleSelected && (
          <button
            type="button"
            className="btn btn-sm btn-secondary scoping-bulk-clear"
            onClick={onClear}
          >
            Clear
          </button>
        )}

        {/* Actions */}
        <button
          type="button"
          className="btn btn-sm btn-primary"
          disabled={busy}
          onClick={onSetApplicable}
          aria-label="Set applicable"
        >
          Set applicable
        </button>

        <button
          type="button"
          className="btn btn-sm btn-secondary"
          disabled={busy}
          onClick={onSetNA}
          aria-label="Set N/A"
        >
          Set N/A
        </button>

        {teamOptions !== null && (
          <label className="scoping-bulk-field">
            <span className="scoping-bulk-field-label">Assign owner</span>
            <select
              aria-label="Assign owner team"
              className="form-control form-control-sm"
              value={owner}
              disabled={busy || teamOptions.length === 0}
              title={
                teamOptions.length === 0
                  ? 'No teams yet — create them under Users → Teams'
                  : undefined
              }
              onChange={(e) => {
                const value = e.target.value
                if (!value) return
                onAssignOwner(value)
                // Command, not bound value — reset to placeholder
                setOwner('')
              }}
            >
              <option value="">
                {teamOptions.length === 0 ? 'No teams yet' : 'Choose team…'}
              </option>
              {teamOptions.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </label>
        )}

        {/* Progress text while busy */}
        {busy && progressText !== undefined && (
          <span className="scoping-bulk-busy">{progressText}</span>
        )}
      </div>
    </div>
  )
}
