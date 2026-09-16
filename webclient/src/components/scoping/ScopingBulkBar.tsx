/**
 * ScopingBulkBar — floating bulk-action bar for the Control Scoping list view.
 *
 * Appears when row checkboxes have a non-empty selection. Exposes:
 *   - Selection count + select-all-visible shortcut + Clear (selection
 *     management; mutates nothing on the server)
 *   - Set maturity (maturity_level — the ORG's own level, L0 through L5)
 *   - Set status (implementation_status — the eight API values)
 *   - Assign owner (dropdown of the org's teams — claims the ACCOUNTABLE
 *     team through the team system; the legacy free-text owner list is gone)
 *
 * The former "Set applicable" / "Set N/A" buttons are gone. They wrote
 * `selected` — i.e. bulk SCOPING, not implementation status — and bulk
 * descoping of an arbitrary row selection was withdrawn deliberately. Scope
 * by Framework still offers bulk scope, unscope and reset by framework.
 *
 * "Set status" offers `not_applicable` under its plain API label, "Not
 * applicable". That is a different concept from the removed "Set N/A": it
 * records that a control does not apply, and leaves the control in scope.
 *
 * Mirrors EvidenceBulkActionsBar's interaction idioms (ruling 1):
 *   - Selects reset to placeholder after firing (they are commands, not bound values)
 *   - Busy state disables all controls and shows progress text
 *   - Progress text is supplied by the parent ("Updating 3 of 12…")
 *
 * Affordance (C6a): the bar sits on var(--success-bg). Plain .btn-secondary is
 * near-white on pale green with a near-white border and reads as flat text, so
 * the data-mutating controls are restyled INSIDE .scoping-bulk-bar only —
 * changing .btn-secondary globally would repaint the whole app. Selection
 * management (Select all / Clear) is deliberately given the lighter, quieter
 * treatment so it no longer carries the same weight as a write.
 */
import { useState, type JSX } from 'react'

export interface TeamOption {
  /** Team id from the team system (Users → Teams). */
  value: string
  label: string
}

/** The org's own maturity levels, matching the scoping detail page's select. */
export const MATURITY_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'L0', label: 'L0 - Initial' },
  { value: 'L1', label: 'L1 - Repeatable' },
  { value: 'L2', label: 'L2 - Defined' },
  { value: 'L3', label: 'L3 - Managed' },
  { value: 'L4', label: 'L4 - Measured' },
  { value: 'L5', label: 'L5 - Optimized' },
]

/**
 * implementation_status' eight API values, under their plain API labels.
 *
 * Deliberately NOT relabelled: "Not applicable" here is implementation_status
 * = not_applicable, a statement about whether the control applies. The bar's
 * removed "Set N/A" wrote `selected = false`, which took the control out of
 * scope entirely. Inventing a bespoke label for either would blur two things
 * the API keeps apart.
 */
export const STATUS_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'not_started', label: 'Not started' },
  { value: 'in_progress', label: 'In progress' },
  { value: 'implemented', label: 'Implemented' },
  { value: 'ready_for_review', label: 'Ready for review' },
  { value: 'monitored', label: 'Monitored' },
  { value: 'not_applicable', label: 'Not applicable' },
  { value: 'at_risk', label: 'At risk' },
  { value: 'deferred', label: 'Deferred' },
]

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
  /** Called with the chosen level; the page writes `maturity_level`. */
  onSetMaturity: (maturityLevel: string) => void
  /** Called with the chosen status; the page writes `implementation_status`. */
  onSetStatus: (implementationStatus: string) => void
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
  onSetMaturity,
  onSetStatus,
  onAssignOwner,
  onClear,
}: ScopingBulkBarProps): JSX.Element {
  // Every select here is a command — reset to placeholder after firing
  const [owner, setOwner] = useState('')
  const [maturity, setMaturity] = useState('')
  const [status, setStatus] = useState('')

  return (
    <div className="scoping-bulk-bar" role="group" aria-label="Bulk actions">
      <div className="scoping-bulk-bar-row">
        {/* Count */}
        <span className="scoping-bulk-count">{selectedCount} selected</span>

        {/* ── Selection management — mutates nothing on the server ───────── */}
        <div className="scoping-bulk-selection-group">
          <button
            type="button"
            className="btn btn-sm btn-secondary scoping-bulk-selection-btn"
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
              className="btn btn-sm btn-secondary scoping-bulk-selection-btn scoping-bulk-clear"
              onClick={onClear}
            >
              Clear
            </button>
          )}
        </div>

        {/* ── Data-mutating actions ──────────────────────────────────────── */}
        <div className="scoping-bulk-actions">
          <label className="scoping-bulk-field">
            <span className="scoping-bulk-field-label">Set maturity</span>
            <select
              aria-label="Set maturity level"
              className="form-control form-control-sm scoping-bulk-select"
              value={maturity}
              disabled={busy}
              onChange={(e) => {
                const value = e.target.value
                if (!value) return
                onSetMaturity(value)
                // Command, not bound value — reset to placeholder
                setMaturity('')
              }}
            >
              <option value="">Choose level…</option>
              {MATURITY_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </label>

          <label className="scoping-bulk-field">
            <span className="scoping-bulk-field-label">Set status</span>
            <select
              aria-label="Set implementation status"
              className="form-control form-control-sm scoping-bulk-select"
              value={status}
              disabled={busy}
              onChange={(e) => {
                const value = e.target.value
                if (!value) return
                onSetStatus(value)
                setStatus('')
              }}
            >
              <option value="">Choose status…</option>
              {STATUS_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </label>

          {teamOptions !== null && (
            <label className="scoping-bulk-field">
              <span className="scoping-bulk-field-label">Assign owner</span>
              <select
                aria-label="Assign owner team"
                className="form-control form-control-sm scoping-bulk-select"
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
        </div>

        {/* Progress text while busy */}
        {busy && progressText !== undefined && (
          <span className="scoping-bulk-busy">{progressText}</span>
        )}
      </div>
    </div>
  )
}
