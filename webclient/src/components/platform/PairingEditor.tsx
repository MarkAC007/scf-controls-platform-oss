/**
 * PairingEditor — successor pairing for controls deprecated by a staged run.
 *
 * Suggestions (plan §4.2.3) are display-only chips; nothing is auto-applied.
 * The admin pairs each deprecated control manually — by clicking a suggestion
 * chip, typing an SCF ID, or explicitly recording "no successor" — and the
 * whole list is saved via PUT .../runs/{id}/pairings.
 */
import { useEffect, useState } from 'react'
import { toast } from 'react-hot-toast'
import { getCatalogUpgradeDiff, putCatalogUpgradePairings } from '../../data/catalogUpgradeApi'
import type { DiffItem, SupersededPairing } from '../../types/catalogUpgrade'

interface PairingEditorProps {
  runId: string
  /** Pairings already saved on the run (run detail superseded_pairings). */
  pairings: SupersededPairing[]
  onPairingsSaved: (pairings: SupersededPairing[]) => void
}

/**
 * Draft pairing state per deprecated scf_id:
 * absent → undecided, string → successor scf_id, null → explicit "no successor".
 */
type DraftPairings = Record<string, string | null>

function draftFromSaved(pairings: SupersededPairing[]): DraftPairings {
  const draft: DraftPairings = {}
  for (const pairing of pairings) {
    draft[pairing.deprecated_scf_id] = pairing.superseded_by
  }
  return draft
}

export default function PairingEditor({ runId, pairings, onPairingsSaved }: PairingEditorProps) {
  const [deprecatedItems, setDeprecatedItems] = useState<DiffItem[] | null>(null)
  const [draft, setDraft] = useState<DraftPairings>(() => draftFromSaved(pairings))
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let cancelled = false
    getCatalogUpgradeDiff(runId, { entity: 'controls', change_class: 'deprecated', page_size: 200 })
      .then(response => {
        if (!cancelled) setDeprecatedItems(response.items)
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          toast.error(err instanceof Error ? err.message : 'Failed to load deprecated controls')
          setDeprecatedItems([])
        }
      })
    return () => {
      cancelled = true
    }
  }, [runId])

  const setPairing = (deprecatedId: string, value: string | null | undefined) => {
    setDraft(prev => {
      const next = { ...prev }
      if (value === undefined) delete next[deprecatedId]
      else next[deprecatedId] = value
      return next
    })
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      const list: SupersededPairing[] = Object.entries(draft).map(([deprecated_scf_id, superseded_by]) => ({
        deprecated_scf_id,
        superseded_by,
      }))
      const response = await putCatalogUpgradePairings(runId, list)
      onPairingsSaved(response.pairings)
      toast.success('Pairings saved')
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : 'Failed to save pairings')
    } finally {
      setSaving(false)
    }
  }

  if (deprecatedItems === null) {
    return (
      <div style={{ textAlign: 'center', padding: '1.5rem' }}>
        <div className="loading-spinner" />
      </div>
    )
  }
  if (deprecatedItems.length === 0) {
    return null
  }

  const undecidedCount = deprecatedItems.filter(item => !(item.key in draft)).length

  return (
    <div style={{ marginTop: '1.5rem' }}>
      <h4 style={{ marginBottom: '0.25rem' }}>Deprecated controls — successor pairing</h4>
      <p style={{ color: 'var(--muted)', fontSize: '0.875rem', marginBottom: '0.75rem' }}>
        Pair each deprecated control with its successor, or record it as retired with no
        successor. Suggestions are never applied automatically.
      </p>
      <div className="api-keys-table-container">
        <table className="api-key-table">
          <thead>
            <tr>
              <th>Deprecated control</th>
              <th>Suggestions</th>
              <th>Successor</th>
              <th>Decision</th>
            </tr>
          </thead>
          <tbody>
            {deprecatedItems.map(item => {
              const decision = draft[item.key]
              return (
                <tr key={item.key}>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    <strong>{item.key}</strong>
                    {item.name && (
                      <div style={{ color: 'var(--muted)', fontSize: '0.8rem' }}>{item.name}</div>
                    )}
                  </td>
                  <td>
                    <div style={{ display: 'flex', gap: '4px', flexWrap: 'wrap' }}>
                      {item.suggestions.length === 0 && (
                        <span style={{ color: 'var(--muted)' }}>—</span>
                      )}
                      {item.suggestions.map(suggestion => (
                        <button
                          key={suggestion.scf_id}
                          className="btn btn-secondary btn-sm"
                          title={suggestion.name || suggestion.scf_id}
                          onClick={() => setPairing(item.key, suggestion.scf_id)}
                        >
                          {suggestion.scf_id} · {Math.round(suggestion.score * 100)}%
                        </button>
                      ))}
                    </div>
                  </td>
                  <td>
                    <input
                      type="text"
                      aria-label={`Successor for ${item.key}`}
                      placeholder="SCF ID"
                      value={typeof decision === 'string' ? decision : ''}
                      onChange={e => {
                        const value = e.target.value.trim()
                        setPairing(item.key, value === '' ? undefined : value)
                      }}
                      style={{ width: '9rem' }}
                    />
                  </td>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    {decision === undefined ? (
                      <button
                        className="btn btn-secondary btn-sm"
                        onClick={() => setPairing(item.key, null)}
                      >
                        No successor
                      </button>
                    ) : decision === null ? (
                      <span style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                        <span className="badge badge-warning">Retire without successor</span>
                        <button
                          className="btn btn-secondary btn-sm"
                          onClick={() => setPairing(item.key, undefined)}
                        >
                          Clear
                        </button>
                      </span>
                    ) : (
                      <span style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                        <span className="badge badge-active">→ {decision}</span>
                        <button
                          className="btn btn-secondary btn-sm"
                          onClick={() => setPairing(item.key, undefined)}
                        >
                          Clear
                        </button>
                      </span>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center', marginTop: '0.75rem' }}>
        <button className="btn btn-primary" disabled={saving} onClick={handleSave}>
          {saving ? 'Saving…' : 'Save pairings'}
        </button>
        {undecidedCount > 0 && (
          <span style={{ color: 'var(--muted)', fontSize: '0.85rem' }}>
            {undecidedCount} deprecated control{undecidedCount === 1 ? '' : 's'} still undecided
          </span>
        )}
      </div>
    </div>
  )
}
