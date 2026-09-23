/**
 * PairingEditor — successor pairing for controls deprecated by a staged run.
 *
 * The workbook is the authority. Every deprecated control the workbook declares
 * a successor for is applied with that successor; this editor exists so an
 * admin can OVERRIDE a declaration, not so they can re-decide all of them.
 * Similarity scoring is deliberately absent — a guessed successor read next to
 * a declared one is noise, and there is nothing to choose between.
 *
 * So: the saved list (PUT .../runs/{id}/pairings) carries overrides ONLY. A row
 * absent from it keeps the workbook's declared successor at apply time, and a
 * row cleared back to its declaration is removed from the list rather than
 * pinned to the same value.
 */
import { useEffect, useState } from 'react'
import { toast } from 'react-hot-toast'
import { getCatalogUpgradeDiff, putCatalogUpgradePairings } from '../../data/catalogUpgradeApi'
import type {
  DiffItem,
  SupersededPairing,
  SupersededSource,
} from '../../types/catalogUpgrade'

interface PairingEditorProps {
  runId: string
  /** Overrides already saved on the run (run detail superseded_pairings). */
  pairings: SupersededPairing[]
  onPairingsSaved: (pairings: SupersededPairing[]) => void
}

/**
 * Draft OVERRIDE state per deprecated scf_id:
 * absent → the workbook's declaration stands, string → override successor,
 * null → override to "no successor" (retire outright).
 */
type DraftOverrides = Record<string, string | null>

/** The diff endpoint caps page_size at 500; the editor must show every row. */
const PAGE_SIZE = 500

function draftFromSaved(pairings: SupersededPairing[]): DraftOverrides {
  const draft: DraftOverrides = {}
  for (const pairing of pairings) {
    draft[pairing.deprecated_scf_id] = pairing.superseded_by
  }
  return draft
}

/** Which workbook declaration produced a row's successor, in the admin's words. */
function sourceLabel(source: SupersededSource | null | undefined): string | null {
  switch (source) {
    case 'workbook_crosswalk':
      return 'Legacy SCF # crosswalk'
    case 'publisher_merged':
      return 'Publisher merge list'
    default:
      // A source name the backend added after this build: show it verbatim
      // rather than silently dropping the provenance.
      return source ? source : null
  }
}

/** The successor the workbook declared for a row, or null if it declared none. */
function declaredSuccessor(item: DiffItem): string | null {
  return item.superseded_by ?? null
}

/**
 * True when a draft entry is a real override — i.e. worth sending. A row whose
 * entry equals its declaration is not an override. A row with no declaration
 * is: any explicit decision there is the only record that it was decided.
 */
function isOverride(item: DiffItem, draft: DraftOverrides): boolean {
  if (!(item.key in draft)) return false
  const declared = declaredSuccessor(item)
  if (declared === null) return true
  return draft[item.key] !== declared
}

/** Page the diff endpoint until every deprecated control is loaded. */
async function loadAllDeprecated(runId: string): Promise<DiffItem[]> {
  const items: DiffItem[] = []
  let page = 1
  for (;;) {
    const response = await getCatalogUpgradeDiff(runId, {
      entity: 'controls',
      change_class: 'deprecated',
      page,
      page_size: PAGE_SIZE,
    })
    items.push(...response.items)
    // An empty page also breaks the loop, so a server that reports a `total`
    // it cannot serve cannot spin this forever.
    if (response.items.length === 0 || items.length >= response.total) return items
    page += 1
  }
}

export default function PairingEditor({ runId, pairings, onPairingsSaved }: PairingEditorProps) {
  const [deprecatedItems, setDeprecatedItems] = useState<DiffItem[] | null>(null)
  const [draft, setDraft] = useState<DraftOverrides>(() => draftFromSaved(pairings))
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let cancelled = false
    loadAllDeprecated(runId)
      .then(items => {
        if (!cancelled) setDeprecatedItems(items)
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

  const setOverride = (deprecatedId: string, value: string | null | undefined) => {
    setDraft(prev => {
      const next = { ...prev }
      if (value === undefined) delete next[deprecatedId]
      else next[deprecatedId] = value
      return next
    })
  }

  const overridesToSave = (items: DiffItem[]): SupersededPairing[] =>
    items
      .filter(item => isOverride(item, draft))
      .map(item => ({ deprecated_scf_id: item.key, superseded_by: draft[item.key] }))

  const handleSave = async () => {
    if (!deprecatedItems) return
    setSaving(true)
    try {
      const response = await putCatalogUpgradePairings(runId, overridesToSave(deprecatedItems))
      onPairingsSaved(response.pairings)
      toast.success('Overrides saved')
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

  const overriddenCount = deprecatedItems.filter(item => isOverride(item, draft)).length
  const declaredCount = deprecatedItems.filter(
    item => declaredSuccessor(item) !== null && !isOverride(item, draft)
  ).length
  const undecidedCount = deprecatedItems.length - overriddenCount - declaredCount

  return (
    <div style={{ marginTop: '1.5rem' }}>
      <h4 style={{ marginBottom: '0.25rem' }}>Deprecated controls — successor pairing</h4>
      <p style={{ color: 'var(--muted)', fontSize: '0.875rem', marginBottom: '0.75rem' }}>
        The workbook is the authority for succession. Every successor it declares below is
        applied as declared unless you override it here — you do not need to confirm them.
        Only your overrides are saved.
      </p>
      <div className="api-keys-table-container">
        <table className="api-key-table">
          <thead>
            <tr>
              <th>Deprecated control</th>
              <th>Declared by workbook</th>
              <th>Override</th>
              <th>Decision</th>
            </tr>
          </thead>
          <tbody>
            {deprecatedItems.map(item => {
              const declared = declaredSuccessor(item)
              const label = sourceLabel(item.superseded_source)
              const overridden = isOverride(item, draft)
              const hasEntry = item.key in draft
              const entry = draft[item.key]
              return (
                <tr key={item.key}>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    <strong>{item.key}</strong>
                    {item.name && (
                      <div style={{ color: 'var(--muted)', fontSize: '0.8rem' }}>{item.name}</div>
                    )}
                  </td>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    {declared ? (
                      <>
                        <strong>{declared}</strong>
                        {label && (
                          <div style={{ color: 'var(--muted)', fontSize: '0.8rem' }}>{label}</div>
                        )}
                      </>
                    ) : (
                      <span style={{ color: 'var(--muted)' }}>none declared</span>
                    )}
                  </td>
                  <td>
                    <input
                      type="text"
                      aria-label={`Override successor for ${item.key}`}
                      placeholder={declared ? `overrides ${declared}` : 'SCF ID'}
                      value={typeof entry === 'string' ? entry : ''}
                      onChange={e => {
                        const value = e.target.value.trim()
                        setOverride(item.key, value === '' ? undefined : value)
                      }}
                      style={{ width: '9rem' }}
                    />
                  </td>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                      {overridden ? (
                        <span className="badge badge-active">
                          {entry === null
                            ? 'Overridden — retire with no successor'
                            : `Overridden → ${entry}`}
                        </span>
                      ) : declared !== null ? (
                        <span className="badge badge-good">Workbook successor applies</span>
                      ) : (
                        <span className="badge badge-warning">Undecided</span>
                      )}
                      {entry !== null && (
                        <button
                          type="button"
                          className="btn btn-secondary btn-sm"
                          title={
                            declared
                              ? `Override ${item.key}: retire it with no successor`
                              : `Retire ${item.key} with no successor`
                          }
                          onClick={() => setOverride(item.key, null)}
                        >
                          No successor
                        </button>
                      )}
                      {hasEntry && (
                        <button
                          type="button"
                          className="btn btn-secondary btn-sm"
                          title={
                            declared
                              ? `Return ${item.key} to the declared successor ${declared}`
                              : `Clear the decision for ${item.key}`
                          }
                          onClick={() => setOverride(item.key, undefined)}
                        >
                          Clear
                        </button>
                      )}
                    </span>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center', marginTop: '0.75rem' }}>
        <button type="button" className="btn btn-primary" disabled={saving} onClick={handleSave}>
          {saving ? 'Saving…' : 'Save pairings'}
        </button>
        <span style={{ color: 'var(--muted)', fontSize: '0.85rem' }}>
          {`${declaredCount} declared by the workbook · ${overriddenCount} overridden · ${undecidedCount} undecided`}
        </span>
      </div>
    </div>
  )
}
