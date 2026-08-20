/**
 * ActionRadioTable — preview section (b): deprecated entities the org has
 * data on, with a per-row action decision (plan §4.3b).
 *
 * migrate is the default when superseded_by is set (and is disabled without
 * a successor); retire_only requires a justification before apply. Rows are
 * never deleted — retire/migrate demote the scoped control, retain keeps it.
 */
import type { DeprecatedImpactItem, PlannedAction, PlannedActionType } from '../../types/catalogUpgrade'

const ACTION_LABELS: Record<PlannedActionType, string> = {
  migrate: 'Migrate',
  retain: 'Retain',
  retire_only: 'Retire only',
}

const ACTION_HINTS: Record<PlannedActionType, string> = {
  migrate: 'Scope the successor control and demote this one',
  retain: 'Keep untouched (renders badged) — right for orgs mid-engagement',
  retire_only: 'Demote with justification, no successor scoped',
}

/** Compact one-line summary of the org data at stake on a deprecated row. */
function formatDataSummary(summary: Record<string, unknown>): string {
  const parts = Object.entries(summary)
    .filter(([, value]) => typeof value === 'number' || typeof value === 'string')
    .map(([key, value]) => `${key.replace(/_/g, ' ')}: ${value}`)
  return parts.length > 0 ? parts.join(' · ') : '—'
}

interface ActionRadioTableProps {
  impacts: DeprecatedImpactItem[]
  actions: Record<string, PlannedAction>
  disabled?: boolean
  onChange: (key: string, action: PlannedAction) => void
}

export default function ActionRadioTable({ impacts, actions, disabled, onChange }: ActionRadioTableProps) {
  if (impacts.length === 0) {
    return (
      <p style={{ color: 'var(--muted)' }}>
        No deprecated controls with organisation data — nothing to decide.
      </p>
    )
  }

  return (
    <div className="api-keys-table-container">
      <table className="api-key-table">
        <thead>
          <tr>
            <th>Control</th>
            <th>Org data at stake</th>
            <th>Successor</th>
            <th>Action</th>
            <th>Justification</th>
          </tr>
        </thead>
        <tbody>
          {impacts.map(impact => {
            const current = actions[impact.key]
            const selected = current?.action ?? impact.suggested_action
            const justification = current?.justification ?? ''
            const needsJustification = selected === 'retire_only' && !justification.trim()
            return (
              <tr key={impact.key}>
                <td style={{ whiteSpace: 'nowrap' }}>
                  <strong>{impact.key}</strong>
                  {impact.name && (
                    <div style={{ color: 'var(--muted)', fontSize: '0.82rem' }}>{impact.name}</div>
                  )}
                </td>
                <td style={{ fontSize: '0.85rem' }}>{formatDataSummary(impact.data_summary)}</td>
                <td>
                  {impact.superseded_by ? (
                    <span className="badge badge-good">{impact.superseded_by}</span>
                  ) : (
                    <span style={{ color: 'var(--muted)' }}>None</span>
                  )}
                </td>
                <td>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                    {(Object.keys(ACTION_LABELS) as PlannedActionType[]).map(actionType => {
                      const migrateWithoutSuccessor = actionType === 'migrate' && !impact.superseded_by
                      return (
                        <label
                          key={actionType}
                          title={ACTION_HINTS[actionType]}
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: '6px',
                            fontSize: '0.85rem',
                            opacity: migrateWithoutSuccessor ? 0.5 : 1,
                          }}
                        >
                          <input
                            type="radio"
                            name={`action-${impact.key}`}
                            aria-label={`${ACTION_LABELS[actionType]} ${impact.key}`}
                            checked={selected === actionType}
                            disabled={disabled || migrateWithoutSuccessor}
                            onChange={() =>
                              onChange(impact.key, {
                                key: impact.key,
                                entity: impact.entity,
                                action: actionType,
                                justification: justification || null,
                                successor_scf_id:
                                  actionType === 'migrate' ? impact.superseded_by : null,
                              })
                            }
                          />
                          {ACTION_LABELS[actionType]}
                        </label>
                      )
                    })}
                  </div>
                </td>
                <td>
                  <input
                    type="text"
                    aria-label={`Justification for ${impact.key}`}
                    placeholder={selected === 'retire_only' ? 'Required for retire only' : 'Optional'}
                    value={justification}
                    disabled={disabled}
                    onChange={e =>
                      onChange(impact.key, {
                        key: impact.key,
                        entity: impact.entity,
                        action: selected,
                        justification: e.target.value || null,
                        successor_scf_id: selected === 'migrate' ? impact.superseded_by : null,
                      })
                    }
                    style={{
                      width: '100%',
                      borderColor: needsJustification ? 'var(--danger, #ef4444)' : undefined,
                    }}
                  />
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
