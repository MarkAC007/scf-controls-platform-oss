import { useState } from 'react'
import { FREQUENCY_OPTIONS } from '../../data/frequencyVocabulary'

/**
 * Acting on many evidence items at once (#789).
 *
 * The evidence list is the only place in the product where the same decision is
 * obviously made repeatedly — an organisation scopes a framework, gets back
 * eighty artifacts, and every one of them needs the same three answers: are we
 * tracking it, how often, and which team owns it. Row by row that is 240
 * interactions,
 * each with its own debounced save, and the review's complaint ("no bulk
 * actions") is really a complaint about a first run that cannot be finished in
 * one sitting.
 *
 * Design notes that are decisions, not details:
 *
 *  - Each action sends ONE request carrying every selected id, not one request
 *    per row. The endpoint already reports per-operation failures, so a batch
 *    where three rows are refused lands the other thirty-seven and says which
 *    three. N requests would give N round trips and no coherent answer.
 *  - Only the field being changed is sent. `exclude_unset` on the API means an
 *    omitted key is untouched, so "set the frequency on 40 items" cannot also
 *    blank their assignees.
 *  - The result is stated, including failures. A bulk edit that silently drops
 *    a third of its work is the "product records none of it" failure this epic
 *    keeps finding, one level up.
 *  - The selects reset to their placeholder after firing. They are commands,
 *    not a bound value: leaving "Quarterly" showing after the fact would read
 *    as the current state of a selection whose members may each differ.
 */

export interface BulkActionResult {
  updated: number
  created: number
  failed: number
  errors: string[]
}

export interface EvidenceBulkActionsBarProps {
  selectedCount: number
  /** Items currently visible under the search and domain filters. */
  visibleCount: number
  allVisibleSelected: boolean
  /**
   * The org's teams for the Assign-owner action (the legacy per-user assign
   * list is sunset — ownership is the ACCOUNTABLE team in the team system).
   * `null` hides the control (non-admins cannot write assignments); `[]`
   * renders it disabled with a create-teams hint.
   */
  teamOptions: { value: string; label: string }[] | null
  busy?: boolean
  result?: BulkActionResult | null
  onSelectAllVisible: () => void
  onClear: () => void
  onSetTracked: (tracked: boolean) => void
  onSetFrequency: (frequency: string) => void
  /** Called with the chosen team id; the screen claims it accountable. */
  onAssignTeam: (teamId: string) => void
  onDismissResult: () => void
}

export function EvidenceBulkActionsBar({
  selectedCount,
  visibleCount,
  allVisibleSelected,
  teamOptions,
  busy = false,
  result,
  onSelectAllVisible,
  onClear,
  onSetTracked,
  onSetFrequency,
  onAssignTeam,
  onDismissResult,
}: EvidenceBulkActionsBarProps) {
  const [frequency, setFrequency] = useState('')
  const [assignee, setAssignee] = useState('')

  const nothingSelected = selectedCount === 0

  return (
    <div className="evidence-bulk-bar" role="group" aria-label="Bulk actions">
      <div className="evidence-bulk-bar-row">
        <span className="evidence-bulk-count">
          {nothingSelected
            ? `${visibleCount} item${visibleCount === 1 ? '' : 's'} shown`
            : `${selectedCount} selected`}
        </span>

        <button
          type="button"
          className="btn btn-sm btn-secondary"
          onClick={allVisibleSelected ? onClear : onSelectAllVisible}
          disabled={visibleCount === 0}
        >
          {allVisibleSelected ? 'Clear selection' : `Select all ${visibleCount} shown`}
        </button>

        {!nothingSelected && !allVisibleSelected && (
          <button type="button" className="btn btn-sm btn-secondary" onClick={onClear}>
            Clear
          </button>
        )}
      </div>

      {!nothingSelected && (
        <div className="evidence-bulk-bar-row evidence-bulk-actions">
          <button
            type="button"
            className="btn btn-sm btn-primary"
            disabled={busy}
            onClick={() => onSetTracked(true)}
          >
            Start tracking
          </button>
          <button
            type="button"
            className="btn btn-sm btn-secondary"
            disabled={busy}
            onClick={() => onSetTracked(false)}
          >
            Stop tracking
          </button>

          <label className="evidence-bulk-field">
            <span className="evidence-bulk-field-label">Set frequency</span>
            <select
              className="form-control form-control-sm"
              value={frequency}
              disabled={busy}
              onChange={e => {
                const value = e.target.value
                if (!value) return
                onSetFrequency(value)
                // A command, not a bound value — see the header.
                setFrequency('')
              }}
            >
              <option value="">Choose…</option>
              {FREQUENCY_OPTIONS.map(option => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>

          {teamOptions !== null && (
            <label className="evidence-bulk-field">
              <span className="evidence-bulk-field-label">Assign owner</span>
              <select
                aria-label="Assign owner team"
                className="form-control form-control-sm"
                value={assignee}
                disabled={busy || teamOptions.length === 0}
                title={
                  teamOptions.length === 0
                    ? 'No teams yet — create them under Users → Teams'
                    : undefined
                }
                onChange={e => {
                  const value = e.target.value
                  if (!value) return
                  onAssignTeam(value)
                  setAssignee('')
                }}
              >
                <option value="">
                  {teamOptions.length === 0 ? 'No teams yet' : 'Choose team…'}
                </option>
                {teamOptions.map(option => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
          )}

          {busy && <span className="evidence-bulk-busy">Applying…</span>}
        </div>
      )}

      {result && (
        <div
          className={`evidence-bulk-result ${result.failed > 0 ? 'has-failures' : ''}`}
          role="status"
        >
          <span className="evidence-bulk-result-text">
            {result.updated + result.created} item
            {result.updated + result.created === 1 ? '' : 's'} updated
            {result.failed > 0 && `, ${result.failed} failed`}
          </span>
          {result.errors.length > 0 && (
            <ul className="evidence-bulk-result-errors">
              {result.errors.map((message, i) => (
                <li key={i}>{message}</li>
              ))}
            </ul>
          )}
          <button
            type="button"
            className="btn btn-sm btn-secondary"
            onClick={onDismissResult}
          >
            Dismiss
          </button>
        </div>
      )}
    </div>
  )
}

export default EvidenceBulkActionsBar
